"""Task calendar event expansion and source metadata."""

import json

from appwrite.query import Query

from appwrite_client import COLLECTIONS
from appwrite_helpers import first_row, format_datetime, list_rows_all
from services.row_utils import row_id
from services.task_schedule import build_task_occurrences


TASK_CALENDAR_ID = "local:tasks"
TASK_CALENDAR_NAME = "Tasks"
TASK_CALENDAR_COLOR = "#0ea5e9"
TASK_PRIORITIES = {"none", "low", "medium", "high"}


def _normalize_priority(value):
    priority = str(value or "none").strip().lower()
    return priority if priority in TASK_PRIORITIES else "none"


def _default_task_reminder(is_date_only):
    return -1 if is_date_only else 10


def _task_recurrence(task):
    raw = task.get("recurrence_json")
    if not raw:
        return None
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(parsed, dict):
            return None
        unit = str(parsed.get("unit") or "day").strip().lower()
        if unit.endswith("s"):
            unit = unit[:-1]
        every = int(parsed.get("every") or 1)
        if unit not in {"day", "week", "month", "year"} or every < 1:
            return None
        return parsed
    except (TypeError, ValueError):
        return None


def _task_event_payload(task, start_dt, end_dt, occurrence_key, completed, is_all_day=False):
    task_id = row_id(task)
    priority = _normalize_priority(task.get("priority"))
    title = task.get("title") or "Untitled Task"
    priority_label = "" if priority == "none" else priority.title()
    description_parts = ["Task"]
    if priority_label:
        description_parts.append(f"Priority: {priority_label}")
    if _task_recurrence(task):
        description_parts.append("Repeating task")
    return {
        "id": f"task:{task_id}:{occurrence_key}",
        "uid": f"task:{task_id}:{occurrence_key}",
        "event_ref": f"task:{task_id}:{occurrence_key}",
        "source_type": "task",
        "editable": False,
        "title": title,
        "description": " | ".join(description_parts),
        "start": format_datetime(start_dt),
        "end": format_datetime(end_dt),
        "type": "task",
        "course": TASK_CALENDAR_NAME,
        "is_multi_day": False,
        "span_days": 1,
        "is_all_day": bool(is_all_day),
        "calendar_id": TASK_CALENDAR_ID,
        "original_calendar_id": TASK_CALENDAR_ID,
        "color": None,
        "task_id": task_id,
        "occurrence_key": occurrence_key,
        "priority": priority,
        "completed": bool(completed),
        "reminder_minutes": int(
            task.get("reminder_minutes")
            if task.get("reminder_minutes") is not None
            else _default_task_reminder(is_all_day)
        ),
    }


def build_task_calendar_events(tasks, completions=None, range_start=None, range_end=None):
    return [
        _task_event_payload(
            occurrence["task"],
            occurrence["start"],
            occurrence["end"],
            occurrence["occurrence_key"],
            occurrence["completed"],
            occurrence["is_all_day"],
        )
        for occurrence in build_task_occurrences(
            tasks,
            completions,
            range_start,
            range_end,
        )
    ]


def task_calendar_events_for_user(
    user_id,
    range_start=None,
    range_end=None,
    *,
    list_rows_fn=list_rows_all,
    build_events_fn=build_task_calendar_events,
):
    tasks = list_rows_fn(
        COLLECTIONS.get("tasks", "tasks"),
        [Query.equal("user_id", [str(user_id)]), Query.order_asc("deadline_at")],
    )
    completions = list_rows_fn(
        COLLECTIONS.get("task_completions", "task_completions"),
        [Query.equal("user_id", [str(user_id)])],
    )
    return build_events_fn(tasks, completions, range_start, range_end)


def user_has_tasks(user_id, *, first_row_fn=first_row):
    row = first_row_fn(
        COLLECTIONS.get("tasks", "tasks"),
        [Query.equal("user_id", [str(user_id)])],
    )
    return bool(row)


def task_calendar_source(preferences=None):
    preferences = preferences or []
    pref = next(
        (row for row in preferences if row.get("calendar_name") == TASK_CALENDAR_ID),
        {},
    )
    return {
        "id": TASK_CALENDAR_ID,
        "kind": "local",
        "default_name": TASK_CALENDAR_NAME,
        "display_name": pref.get("display_name") or "",
        "color_hex": pref.get("color_hex") or TASK_CALENDAR_COLOR,
        "url": "",
        "editable": True,
        "source_id": TASK_CALENDAR_ID,
        "legacy_names": [TASK_CALENDAR_NAME],
    }
