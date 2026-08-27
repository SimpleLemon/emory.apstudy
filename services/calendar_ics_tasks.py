"""Strict Tasks source projector for single-calendar ICS feeds.

The public surface of this module is deliberately small.  It accepts already
loaded task rows (or loads them through the existing Appwrite helpers), uses
the shared task occurrence expander, and emits only the frozen normalized ICS
event shape.  Source identifiers are used only as private input to the
central HMAC UID builder.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from appwrite.query import Query

from appwrite_client import COLLECTIONS
from appwrite_helpers import list_rows_all
from services.calendar_ics_contract import (
    CalendarIcsProjectionOutcome,
    NormalizedCalendarEvent,
)
from services.row_utils import row_id
from services.task_schedule import build_task_occurrences


TASKS_CALENDAR_ID = "tasks"
TASK_SOURCE_TYPE = "task"
TASK_PRIORITY_VALUES = {"high": 1, "medium": 5, "low": 9}
TASK_PRIORITIES = {"none", *TASK_PRIORITY_VALUES}
RECURRENCE_UNITS = {"day", "week", "month", "year"}
TIMED_REMINDER_VALUES = {-1, 0, 5, 10, 15, 30, 60, 120, 1440, 2880}
DATE_ONLY_REMINDER_VALUES = {-1, -540, 900, 2340, 9540}


class TasksProjectorError(ValueError):
    """A source row or occurrence cannot be projected accurately."""


def _source_failure(code: str, message: str) -> CalendarIcsProjectionOutcome:
    return CalendarIcsProjectionOutcome.source_failure(code, message)


def _require_utc_range(range_start: datetime, range_end: datetime) -> None:
    for name, value in (("range_start", range_start), ("range_end", range_end)):
        if type(value) is not datetime or value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError(f"{name} must be a timezone-aware UTC datetime.")
    if range_end <= range_start:
        raise ValueError("range_end must be after range_start.")


def _parse_utc_datetime(value: Any, *, field: str) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise TasksProjectorError(f"{field} must be a valid ISO datetime.") from exc
    else:
        raise TasksProjectorError(f"{field} must be a datetime or ISO datetime.")
    if parsed.tzinfo is None:
        # Existing task storage treats naive ISO values as UTC.
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_date(value: Any, *, field: str) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        raise TasksProjectorError(f"{field} must be a calendar date.")
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        raise TasksProjectorError(f"{field} must be a calendar date.")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise TasksProjectorError(f"{field} must be a valid calendar date.") from exc


def _task_timezone(task: Mapping[str, Any]) -> timezone | ZoneInfo:
    value = str(task.get("timezone") or "UTC").strip() or "UTC"
    try:
        return ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise TasksProjectorError("Task timezone is invalid.") from exc


def _validate_deadline_time(value: Any) -> None:
    if value in (None, ""):
        return
    if not isinstance(value, str) or len(value) != 5 or value[2] != ":":
        raise TasksProjectorError("Task deadline_time is invalid.")
    try:
        hour, minute = int(value[:2]), int(value[3:])
    except ValueError as exc:
        raise TasksProjectorError("Task deadline_time is invalid.") from exc
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise TasksProjectorError("Task deadline_time is invalid.")


def _recurrence_payload(task: Mapping[str, Any]) -> dict[str, Any] | None:
    raw = task.get("recurrence_json")
    if raw in (None, "", False):
        return None
    if isinstance(raw, str):
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise TasksProjectorError("Task recurrence_json is malformed.") from exc
    else:
        value = raw
    if not isinstance(value, dict):
        raise TasksProjectorError("Task recurrence_json must be an object.")
    try:
        every = int(value.get("every") or 1)
    except (TypeError, ValueError) as exc:
        raise TasksProjectorError("Task recurrence frequency is invalid.") from exc
    unit = str(value.get("unit") or "day").strip().lower().rstrip("s")
    if every < 1 or every > 365 or unit not in RECURRENCE_UNITS:
        raise TasksProjectorError("Task recurrence rule is invalid.")
    start = _parse_date(value.get("startDate", value.get("start_date")), field="recurrence startDate")
    end = _parse_date(value.get("endDate", value.get("end_date")), field="recurrence endDate")
    if end is not None and start is not None and end < start:
        raise TasksProjectorError("Task recurrence endDate precedes startDate.")
    return value


def _validate_task(task: Mapping[str, Any]) -> tuple[str, timezone | ZoneInfo]:
    if not isinstance(task, Mapping):
        raise TasksProjectorError("Task row is malformed.")
    task_id = row_id(task)
    if not isinstance(task_id, str) or not task_id.strip():
        raise TasksProjectorError("Task row has no stable identity.")
    if not isinstance(task.get("title"), str) or not task["title"].strip():
        raise TasksProjectorError("Task title is missing.")
    _parse_utc_datetime(task.get("deadline_at"), field="Task deadline_at")
    _validate_deadline_time(task.get("deadline_time"))
    zone = _task_timezone(task)
    priority = str(task.get("priority") or "none").strip().lower()
    if priority not in TASK_PRIORITIES:
        raise TasksProjectorError("Task priority is invalid.")
    reminder = task.get("reminder_minutes", -1)
    if isinstance(reminder, bool):
        raise TasksProjectorError("Task reminder_minutes is invalid.")
    try:
        reminder_value = int(reminder if reminder is not None else -1)
    except (TypeError, ValueError) as exc:
        raise TasksProjectorError("Task reminder_minutes is invalid.") from exc
    reminder_values = TIMED_REMINDER_VALUES if task.get("deadline_time") not in (None, "") else DATE_ONLY_REMINDER_VALUES
    if reminder_value not in reminder_values:
        raise TasksProjectorError("Task reminder_minutes is invalid.")
    _recurrence_payload(task)
    _parse_utc_datetime(task.get("updated_at"), field="Task updated_at")
    return task_id, zone


def _validate_completions(completions: Iterable[Mapping[str, Any]]) -> dict[tuple[str, str], datetime | None]:
    revisions: dict[tuple[str, str], datetime | None] = {}
    for completion in completions:
        if not isinstance(completion, Mapping):
            raise TasksProjectorError("Task completion row is malformed.")
        task_id = completion.get("task_id")
        occurrence_key = completion.get("occurrence_key")
        if not isinstance(task_id, str) or not task_id.strip():
            raise TasksProjectorError("Task completion has no task identity.")
        if not isinstance(occurrence_key, str) or not occurrence_key.strip():
            raise TasksProjectorError("Task completion has no occurrence identity.")
        revision = _parse_utc_datetime(completion.get("updated_at"), field="Task completion updated_at")
        revisions[(task_id, occurrence_key)] = revision
    return revisions


def _event_last_modified(task: Mapping[str, Any], revision: datetime | None) -> datetime | None:
    task_revision = _parse_utc_datetime(task.get("updated_at"), field="Task updated_at")
    if task_revision is None:
        return revision
    if revision is None:
        return task_revision
    return max(task_revision, revision)


def _all_day_dates(
    start: datetime,
    end: datetime,
    zone: timezone | ZoneInfo,
) -> tuple[date, date]:
    return start.astimezone(zone).date(), end.astimezone(zone).date()


def _project_occurrence(
    occurrence: Mapping[str, Any],
    completion_revisions: Mapping[tuple[str, str], datetime | None],
) -> NormalizedCalendarEvent:
    task = occurrence.get("task")
    if not isinstance(task, Mapping):
        raise TasksProjectorError("Expanded task occurrence has no task row.")
    task_id, zone = _validate_task(task)
    occurrence_key = occurrence.get("occurrence_key")
    if not isinstance(occurrence_key, str) or not occurrence_key.strip():
        raise TasksProjectorError("Expanded task occurrence has no occurrence identity.")
    start = occurrence.get("start")
    end = occurrence.get("end")
    if type(start) is not datetime or type(end) is not datetime:
        raise TasksProjectorError("Expanded task occurrence has invalid bounds.")
    if start.tzinfo is None or start.utcoffset() != timedelta(0) or end.tzinfo is None or end.utcoffset() != timedelta(0):
        raise TasksProjectorError("Expanded task occurrence bounds must be UTC.")
    is_all_day = occurrence.get("is_all_day")
    if type(is_all_day) is not bool:
        raise TasksProjectorError("Expanded task occurrence has invalid all-day state.")
    priority = str(task.get("priority") or "none").strip().lower()
    priority_text = "" if priority == "none" else f"Priority: {priority.title()} ({TASK_PRIORITY_VALUES[priority]})"
    description = "Task" if not priority_text else f"Task | {priority_text}"
    reminder = task.get("reminder_minutes", -1)
    reminder_minutes = int(reminder) if not is_all_day and int(reminder) > 0 else None
    revision = completion_revisions.get((task_id, occurrence_key))
    event_fields: dict[str, Any] = {
        "calendar_id": TASKS_CALENDAR_ID,
        "source_type": TASK_SOURCE_TYPE,
        "title": task["title"].strip(),
        "start": start,
        "end": end,
        "is_all_day": is_all_day,
        "description": description,
        "event_type": TASK_SOURCE_TYPE,
        "course_name": "Tasks",
        "priority": priority,
        "completed": bool(occurrence.get("completed")),
        "reminder_minutes": reminder_minutes,
        "last_modified": _event_last_modified(task, revision),
    }
    if is_all_day:
        event_fields["start"], event_fields["end"] = _all_day_dates(start, end, zone)
    return NormalizedCalendarEvent.from_internal(
        raw_identity=f"task:{task_id}:{occurrence_key}",
        **event_fields,
    )


def project_tasks(
    tasks: Iterable[Mapping[str, Any]],
    completions: Iterable[Mapping[str, Any]] | None,
    range_start: datetime,
    range_end: datetime,
    *,
    occurrence_builder: Callable[..., Iterable[Mapping[str, Any]]] = build_task_occurrences,
) -> CalendarIcsProjectionOutcome:
    """Project task rows into a complete, fail-closed normalized result."""

    _require_utc_range(range_start, range_end)
    try:
        task_rows = list(tasks)
        completion_rows = list(completions or [])
        for task in task_rows:
            _validate_task(task)
        completion_revisions = _validate_completions(completion_rows)
        occurrences = occurrence_builder(task_rows, completion_rows, range_start, range_end)
        if occurrences is None:
            raise TasksProjectorError("Task occurrence expansion returned no result.")
        events = tuple(_project_occurrence(item, completion_revisions) for item in occurrences)
        if len({event.uid for event in events}) != len(events):
            raise TasksProjectorError("Task occurrence expansion returned duplicate identities.")
    except Exception as exc:  # source errors must never become partial or empty feeds
        return _source_failure("task_source_invalid", str(exc))
    return CalendarIcsProjectionOutcome.valid_empty() if not events else CalendarIcsProjectionOutcome.success(events)


def project_tasks_for_user(
    user_id: str,
    range_start: datetime,
    range_end: datetime,
    *,
    list_rows_fn: Callable[..., Iterable[Mapping[str, Any]]] = list_rows_all,
    occurrence_builder: Callable[..., Iterable[Mapping[str, Any]]] = build_task_occurrences,
) -> CalendarIcsProjectionOutcome:
    """Load both task tables and project them atomically for one owner."""

    _require_utc_range(range_start, range_end)
    try:
        tasks = list_rows_fn(
            COLLECTIONS.get("tasks", "tasks"),
            [Query.equal("user_id", [str(user_id)])],
        )
        completions = list_rows_fn(
            COLLECTIONS.get("task_completions", "task_completions"),
            [Query.equal("user_id", [str(user_id)])],
        )
        return project_tasks(
            tasks,
            completions,
            range_start,
            range_end,
            occurrence_builder=occurrence_builder,
        )
    except Exception as exc:
        return _source_failure("task_source_unavailable", str(exc))
