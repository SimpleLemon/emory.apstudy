"""Pure task occurrence expansion shared by calendars and reminders."""

import calendar
import json
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from services.row_utils import row_id as _row_id


MAX_EXPANDED_OCCURRENCES = 1500


def _coerce_utc(value):
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_datetime(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return _coerce_utc(value)
    text = str(value).strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        return _coerce_utc(datetime.fromisoformat(text))
    except ValueError:
        return None


def _parse_date(value):
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _zone(value):
    try:
        return ZoneInfo(str(value or "UTC"))
    except (ZoneInfoNotFoundError, ValueError):
        return timezone.utc


def _deadline_time(value):
    text = str(value or "").strip()
    if len(text) < 5 or text[2] != ":":
        return None
    try:
        hour, minute = int(text[:2]), int(text[3:5])
    except ValueError:
        return None
    return time(hour, minute) if 0 <= hour <= 23 and 0 <= minute <= 59 else None


def _task_recurrence(task):
    raw = task.get("recurrence_json") if task else None
    if not raw:
        return None
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError:
        return None
    if not isinstance(value, dict):
        return None
    unit = str(value.get("unit") or "day").rstrip("s")
    if unit not in {"day", "week", "month", "year"}:
        return None
    try:
        every = int(value.get("every") or 1)
    except (TypeError, ValueError):
        return None
    if every < 1:
        return None
    return {
        "every": every,
        "unit": unit,
        "startDate": value.get("startDate") or value.get("start_date"),
        "endDate": value.get("endDate") or value.get("end_date"),
    }


def _add_months(source_date, months):
    month_index = source_date.month - 1 + months
    year = source_date.year + month_index // 12
    month = month_index % 12 + 1
    return date(year, month, min(source_date.day, calendar.monthrange(year, month)[1]))


def _advance_date(source_date, every, unit):
    if unit == "day":
        return source_date + timedelta(days=every)
    if unit == "week":
        return source_date + timedelta(weeks=every)
    return _add_months(source_date, every * (12 if unit == "year" else 1))


def _local_parts(task):
    deadline = _parse_datetime(task.get("deadline_at"))
    zone = _zone(task.get("timezone"))
    local_date = deadline.astimezone(zone).date() if deadline else None
    return local_date, _deadline_time(task.get("deadline_time")), zone


def _occurrence_bounds(task, occurrence_date):
    _deadline_date, deadline_time, zone = _local_parts(task)
    is_all_day = deadline_time is None
    start_local = datetime.combine(occurrence_date, deadline_time or time.min, tzinfo=zone)
    if is_all_day:
        end_local = datetime.combine(occurrence_date + timedelta(days=1), time.min, tzinfo=zone)
    else:
        end_local = start_local + timedelta(minutes=30)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc), is_all_day


def _overlaps(start, end, range_start, range_end):
    if range_start and end <= range_start:
        return False
    if range_end and start >= range_end:
        return False
    return True


def _completion_keys(completions):
    result = defaultdict(set)
    for row in completions or []:
        result[str(row.get("task_id") or "")].add(str(row.get("occurrence_key") or ""))
    return result


def build_task_occurrences(tasks, completions=None, range_start=None, range_end=None):
    """Return normalized one-off and recurring task occurrences in a UTC range."""
    completed_by_task = _completion_keys(completions)
    occurrences = []
    for task in tasks or []:
        task_id = _row_id(task)
        deadline = _parse_datetime(task.get("deadline_at"))
        if not task_id or not deadline:
            continue
        recurrence = _task_recurrence(task)
        deadline_date, _deadline_clock, zone = _local_parts(task)
        if not recurrence:
            start, end, is_all_day = _occurrence_bounds(task, deadline_date)
            if _overlaps(start, end, range_start, range_end):
                occurrences.append({
                    "task": task,
                    "task_id": task_id,
                    "occurrence_key": "single",
                    "start": start,
                    "end": end,
                    "is_all_day": is_all_day,
                    "completed": bool(task.get("completed", False)),
                })
            continue

        start_date = _parse_date(recurrence.get("startDate")) or deadline_date
        end_date = _parse_date(recurrence.get("endDate"))
        if not start_date:
            continue
        local_range_start = range_start.astimezone(zone).date() if range_start else start_date
        local_range_end = range_end.astimezone(zone).date() if range_end else local_range_start + timedelta(days=365)
        current = start_date
        guard = 0
        while current < local_range_start and guard < MAX_EXPANDED_OCCURRENCES:
            current = _advance_date(current, recurrence["every"], recurrence["unit"])
            guard += 1
        while guard < MAX_EXPANDED_OCCURRENCES and current <= local_range_end:
            if end_date and current > end_date:
                break
            start, end, is_all_day = _occurrence_bounds(task, current)
            occurrence_key = current.isoformat()
            if _overlaps(start, end, range_start, range_end):
                occurrences.append({
                    "task": task,
                    "task_id": task_id,
                    "occurrence_key": occurrence_key,
                    "start": start,
                    "end": end,
                    "is_all_day": is_all_day,
                    "completed": occurrence_key in completed_by_task.get(task_id, set()),
                })
            current = _advance_date(current, recurrence["every"], recurrence["unit"])
            guard += 1
    return sorted(occurrences, key=lambda item: item["start"])


def next_task_occurrence_key(task, now=None):
    recurrence = _task_recurrence(task)
    if not recurrence:
        return "single"
    deadline_date, _deadline_clock, zone = _local_parts(task)
    current = _parse_date(recurrence.get("startDate")) or deadline_date or date.today()
    end_date = _parse_date(recurrence.get("endDate"))
    local_today = (now or datetime.now(timezone.utc)).astimezone(zone).date()
    guard = 0
    while current < local_today and guard < MAX_EXPANDED_OCCURRENCES:
        current = _advance_date(current, recurrence["every"], recurrence["unit"])
        guard += 1
    return None if end_date and current > end_date else current.isoformat()
