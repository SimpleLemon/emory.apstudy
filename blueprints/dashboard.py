"""Main application pages and dashboard summary APIs."""

import hashlib
import json
import logging
import os
from datetime import datetime, timedelta, timezone

from flask import Blueprint, g, jsonify, render_template, redirect, request, url_for
from flask_login import login_required, current_user

from appwrite.exception import AppwriteException
from appwrite.query import Query
from appwrite_client import COLLECTIONS
from appwrite_helpers import (
    create_row_safe,
    first_row,
    format_datetime,
    list_rows_safe,
    list_rows_all,
    parse_datetime,
    update_row_safe,
)
from services.discord_audit import emit_server_log_event
from services.atlas_client import DEFAULT_TERM, get_atlas_term_srcdb, get_general_ed_composite_requirements, get_general_ed_requirement_aliases, get_starred_general_ed_requirements
from services.daily_quote import get_daily_quote_payload
from services.calendar_store import list_calendar_rows_all
from services.toasts import pop_toasts
from services import note_store

dashboard_bp = Blueprint("dashboard", __name__)
logger = logging.getLogger(__name__)

DASHBOARD_TILE_IDS = ("calendar", "tasks", "files", "notes", "messages", "courses")
DEFAULT_DASHBOARD_TILE_ORDER = ("calendar", "tasks", "files", "notes", "messages", "courses")
DASHBOARD_DEFAULT_TILE_SIZES = {
    "calendar": "standard",
    "tasks": "standard",
    "files": "standard",
    "notes": "standard",
    "messages": "standard",
    "courses": "wide",
}
DASHBOARD_ALLOWED_TILE_SIZES = {
    "calendar": ("standard", "tall", "wide"),
    "tasks": ("standard", "tall", "wide"),
    "files": ("standard", "tall", "wide"),
    "notes": ("standard", "tall", "wide"),
    "messages": ("standard", "tall", "wide"),
    "courses": ("standard", "wide"),
}
DASHBOARD_LAYOUT_VERSION = 4
DASHBOARD_CALENDAR_VIEWS = ("month", "week", "upcoming")
DASHBOARD_DEFAULT_CALENDAR_VIEW = "month"
DASHBOARD_CALENDAR_UPCOMING_LIMIT = 80
DASHBOARD_LIST_LIMIT = 8
DASHBOARD_TASK_LIMIT = 8
DASHBOARD_TASK_FILTER_SOURCE_LIMIT = 100
DASHBOARD_TILE_LIMIT = 12
DASHBOARD_DUPLICATE_TILE_LIMIT = 4
DASHBOARD_DUPLICATE_TILE_TYPES = {"calendar", "tasks"}
DASHBOARD_ITEM_LIMITS = (3, 5, 8)
DASHBOARD_DENSITIES = ("compact", "comfortable")
DASHBOARD_CALENDAR_UPCOMING_DAYS = (7, 14, 30)
DASHBOARD_TASK_DEADLINE_DAYS = (7, 30)
DASHBOARD_TASK_PRIORITIES = ("high", "medium", "low", "none")
DASHBOARD_TITLE_MAX_LENGTH = 60
DASHBOARD_TASK_PRIORITY_RANK = {
    "high": 0,
    "medium": 1,
    "low": 2,
}


def _is_emory_or_oxford_user():
    school = str(getattr(current_user, "school", "") or "").strip().lower()
    school_key = str(getattr(current_user, "school_key", "") or "").strip().lower()
    return bool(getattr(current_user, "emory_student", False)) or school in {
        "emory",
        "emory university",
        "emory university-oxford",
        "emory university oxford",
        "oxford college",
        "oxford college of emory university",
    } or school_key in {
        "emory",
        "emory-university",
        "emory-university-oxford",
        "oxford-college",
        "oxford-college-of-emory-university",
    }


def _default_courses_campus():
    school = " ".join([
        str(getattr(current_user, "school", "") or ""),
        str(getattr(current_user, "school_key", "") or ""),
    ]).lower()
    return "oxford" if "oxford" in school else "atlanta"

DASHBOARD_QUOTE_ERROR_REASONS = {
    "fetch_failed",
    "http_error",
    "invalid_payload",
    "cache_read_failed",
    "cache_write_failed",
    "visibility_storage_failed",
    "unknown",
}
DASHBOARD_QUOTE_ERROR_FIELDS = ("status", "dateKey", "quoteUrl", "phase")
DASHBOARD_QUOTE_ERROR_MAX_MESSAGE_LENGTH = 500
DASHBOARD_QUOTE_ERROR_MAX_FIELD_LENGTH = 160


def _trimmed_text(value, max_length):
    text = str(value or "").strip()
    return text[:max_length]


def _daily_quote_error_metadata(payload):
    reason = str(payload.get("reason") or "").strip()
    if reason not in DASHBOARD_QUOTE_ERROR_REASONS:
        reason = "unknown"

    metadata = {
        "reason": reason,
        "message": _trimmed_text(payload.get("message"), DASHBOARD_QUOTE_ERROR_MAX_MESSAGE_LENGTH),
    }
    for field in DASHBOARD_QUOTE_ERROR_FIELDS:
        value = payload.get(field)
        if value is None:
            continue
        metadata[field] = _trimmed_text(value, DASHBOARD_QUOTE_ERROR_MAX_FIELD_LENGTH)
    return metadata


def _user_payload():
    return {
        "id": str(current_user.id),
        "name": current_user.name,
        "username": current_user.username,
        "email": current_user.email,
        "picture": current_user.picture_url,
        "emory_student": _is_emory_or_oxford_user(),
        "school": current_user.school,
        "school_key": getattr(current_user, "school_key", None),
    }


def _load_user_settings():
    if hasattr(g, "_apstudy_user_settings"):
        return g._apstudy_user_settings
    try:
        settings = first_row(
            COLLECTIONS["user_settings"],
            [Query.equal("user_id", [str(current_user.id)])],
        )
        g._apstudy_user_settings = settings
        return settings
    except AppwriteException:
        logger.exception("Failed to load user settings")
        g._apstudy_user_settings = None
        return None


def _settings_row_id(settings):
    return settings.get("$id") or settings.get("id") if settings else None


def _ensure_user_settings(user_id):
    settings = first_row(
        COLLECTIONS["user_settings"],
        [Query.equal("user_id", [str(user_id)])],
    )
    if settings:
        return settings

    from blueprints.settings import _settings_defaults

    return create_row_safe(
        COLLECTIONS["user_settings"],
        row_id=str(user_id),
        data={**_settings_defaults(str(user_id)), "updated_at": format_datetime(datetime.now(timezone.utc))},
    )


def _theme_from_settings(user_settings):
    return user_settings.get("interface_theme") if user_settings else None


def _row_id(row):
    return row.get("$id") or row.get("id") if row else None


def _as_utc(value):
    parsed = parse_datetime(value)
    if not parsed:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _date_key(value):
    parsed = _as_utc(value)
    if parsed:
        return parsed.date().isoformat()
    text = str(value or "").strip()
    return text[:10] if len(text) >= 10 else ""


def _sort_key(value):
    parsed = _as_utc(value)
    return parsed or datetime.min.replace(tzinfo=timezone.utc)


def _default_tile_size(tile_id):
    return DASHBOARD_DEFAULT_TILE_SIZES.get(tile_id, "standard")


def _layout_version(parsed):
    if isinstance(parsed, dict):
        try:
            return int(parsed.get("version") or 2)
        except (TypeError, ValueError):
            return 2
    if isinstance(parsed, list):
        return 1
    return 2


def _normalize_tile_size(tile_id, size):
    normalized = str(size or "").strip().lower()
    if normalized in {"compact", "medium"}:
        normalized = "standard"
    elif normalized == "large":
        normalized = "wide"
    if normalized not in DASHBOARD_ALLOWED_TILE_SIZES.get(tile_id, ()):
        return _default_tile_size(tile_id)
    return normalized


def _normalize_calendar_view(view):
    normalized = str(view or DASHBOARD_DEFAULT_CALENDAR_VIEW).strip().lower()
    return normalized if normalized in DASHBOARD_CALENDAR_VIEWS else DASHBOARD_DEFAULT_CALENDAR_VIEW


def _normalize_task_list_ids(raw_list_ids, available_list_ids=None):
    if not isinstance(raw_list_ids, list):
        return []
    available = set(str(item) for item in available_list_ids) if available_list_ids is not None else None
    normalized = []
    for item in raw_list_ids:
        list_id = str(item or "").strip()
        if not list_id or list_id in normalized:
            continue
        if available is not None and list_id not in available:
            continue
        normalized.append(list_id)
    return normalized


def _legacy_instance_id(tile_id):
    return f"legacy-{tile_id}"


def _normalized_choice(value, allowed, default):
    normalized = str(value or default).strip().lower()
    return normalized if normalized in allowed else default


def _normalized_item_limit(value):
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        normalized = 5
    return normalized if normalized in DASHBOARD_ITEM_LIMITS else 5


def _layout_tile_payload(
    tile_id,
    size=None,
    view=None,
    task_list_ids=None,
    *,
    instance_id=None,
    title=None,
    density=None,
    item_limit=None,
    upcoming_days=None,
    deadline_days=None,
    include_overdue=None,
    include_undated=None,
    priorities=None,
    starred_only=None,
):
    payload = {
        "instance_id": str(instance_id or _legacy_instance_id(tile_id)).strip(),
        "type": tile_id,
        "size": _normalize_tile_size(tile_id, size),
        "density": _normalized_choice(density, DASHBOARD_DENSITIES, "comfortable"),
        "item_limit": _normalized_item_limit(item_limit),
    }
    normalized_title = str(title or "").strip()[:DASHBOARD_TITLE_MAX_LENGTH]
    if normalized_title:
        payload["title"] = normalized_title
    if tile_id == "calendar":
        payload["view"] = _normalize_calendar_view(view)
        payload["upcoming_days"] = int(upcoming_days) if upcoming_days in DASHBOARD_CALENDAR_UPCOMING_DAYS else 7
    if tile_id == "tasks":
        list_ids = _normalize_task_list_ids(task_list_ids)
        if list_ids:
            payload["task_list_ids"] = list_ids
        payload["deadline_days"] = int(deadline_days) if deadline_days in DASHBOARD_TASK_DEADLINE_DAYS else 30
        payload["include_overdue"] = True if include_overdue is None else bool(include_overdue)
        payload["include_undated"] = True if include_undated is None else bool(include_undated)
        normalized_priorities = [
            priority for priority in DASHBOARD_TASK_PRIORITIES
            if priority in {str(item or "").strip().lower() for item in (priorities or DASHBOARD_TASK_PRIORITIES)}
        ]
        payload["priorities"] = normalized_priorities or list(DASHBOARD_TASK_PRIORITIES)
        payload["starred_only"] = bool(starred_only)
    return payload


def _coerce_layout(raw_value):
    if isinstance(raw_value, (dict, list)):
        parsed = raw_value
    else:
        try:
            parsed = json.loads(raw_value or "[]")
        except (TypeError, ValueError):
            parsed = {}

    version = _layout_version(parsed)
    source_tiles = []
    if isinstance(parsed, dict):
        source_tiles = parsed.get("tiles") if isinstance(parsed.get("tiles"), list) else []
    elif isinstance(parsed, list):
        source_tiles = parsed

    tiles = []
    seen_instances = set()
    seen_legacy_types = set()
    for item in source_tiles:
        if isinstance(item, dict):
            tile_id = str(item.get("type") or item.get("id") or "").strip()
            instance_id = str(item.get("instance_id") or (item.get("id") if item.get("type") else "") or _legacy_instance_id(tile_id)).strip()
            size = item.get("size")
            view = item.get("view")
            task_list_ids = item.get("task_list_ids")
        else:
            tile_id = str(item or "").strip()
            instance_id = _legacy_instance_id(tile_id)
            size = None
            view = None
            task_list_ids = None
        if tile_id not in DASHBOARD_TILE_IDS or instance_id in seen_instances:
            continue
        if version < DASHBOARD_LAYOUT_VERSION and tile_id in seen_legacy_types:
            continue
        tiles.append(_layout_tile_payload(
            tile_id,
            size,
            view,
            task_list_ids,
            instance_id=instance_id,
            title=item.get("title") if isinstance(item, dict) else None,
            density=item.get("density") if isinstance(item, dict) else None,
            item_limit=item.get("item_limit") if isinstance(item, dict) else None,
            upcoming_days=item.get("upcoming_days") if isinstance(item, dict) else None,
            deadline_days=item.get("deadline_days") if isinstance(item, dict) else None,
            include_overdue=item.get("include_overdue") if isinstance(item, dict) else None,
            include_undated=item.get("include_undated") if isinstance(item, dict) else None,
            priorities=item.get("priorities") if isinstance(item, dict) else None,
            starred_only=item.get("starred_only") if isinstance(item, dict) else None,
        ))
        seen_instances.add(instance_id)
        seen_legacy_types.add(tile_id)
    quote_visible = parsed.get("daily_quote_visible") if isinstance(parsed, dict) else None
    return {"version": version, "daily_quote_visible": quote_visible if isinstance(quote_visible, bool) else None, "tiles": tiles}


def _coerce_layout_order(raw_value):
    return [tile["type"] for tile in _coerce_layout(raw_value)["tiles"]]


def _ordered_tile_layout(saved_layout, available_tile_ids):
    available = [tile_id for tile_id in available_tile_ids if tile_id in DASHBOARD_TILE_IDS]
    version = int(saved_layout.get("version") or 2) if isinstance(saved_layout, dict) else 2
    saved_tiles = saved_layout.get("tiles") if isinstance(saved_layout, dict) else []
    ordered = []
    seen = set()
    for item in saved_tiles:
        tile_id = str(item.get("type") or item.get("id") or "").strip() if isinstance(item, dict) else ""
        instance_id = str(item.get("instance_id") or _legacy_instance_id(tile_id)).strip() if isinstance(item, dict) else ""
        if tile_id not in available or instance_id in seen:
            continue
        ordered.append(_layout_tile_payload(
            tile_id,
            item.get("size"),
            item.get("view"),
            item.get("task_list_ids"),
            instance_id=instance_id,
            title=item.get("title"),
            density=item.get("density"),
            item_limit=item.get("item_limit"),
            upcoming_days=item.get("upcoming_days"),
            deadline_days=item.get("deadline_days"),
            include_overdue=item.get("include_overdue"),
            include_undated=item.get("include_undated"),
            priorities=item.get("priorities"),
            starred_only=item.get("starred_only"),
        ))
        seen.add(instance_id)
    if version >= 3:
        return ordered
    seen_types = {tile["type"] for tile in ordered}
    for tile_id in DEFAULT_DASHBOARD_TILE_ORDER:
        if tile_id in available and tile_id not in seen_types:
            ordered.append(_layout_tile_payload(tile_id))
            seen_types.add(tile_id)
    for tile_id in available:
        if tile_id not in seen_types:
            ordered.append(_layout_tile_payload(tile_id))
            seen_types.add(tile_id)
    return ordered


def _validated_tile_size(tile_id, raw_size):
    if raw_size is None or str(raw_size).strip() == "":
        return _default_tile_size(tile_id)
    normalized = str(raw_size).strip().lower()
    if normalized in {"compact", "medium"}:
        normalized = "standard"
    elif normalized == "large":
        normalized = "wide"
    if normalized not in DASHBOARD_ALLOWED_TILE_SIZES.get(tile_id, ()):
        return None
    return normalized


def _validated_calendar_view(raw_view):
    if raw_view is None or str(raw_view).strip() == "":
        return DASHBOARD_DEFAULT_CALENDAR_VIEW
    normalized = str(raw_view).strip().lower()
    return normalized if normalized in DASHBOARD_CALENDAR_VIEWS else None


def _checklist_signature(items):
    payload = [
        {"id": item["id"], "complete": bool(item["complete"])}
        for item in items
    ]
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _academic_year_value():
    return current_user.graduation_year or current_user.class_year


def _build_checklist(calendar_complete=False, tasks_complete=False):
    items = [
        {
            "id": "identity",
            "label": "Add your name and username",
            "complete": bool((current_user.name or "").strip() and (current_user.username or "").strip()),
            "href": url_for("settings.settings_page") + "#account",
        },
        {
            "id": "academic",
            "label": "Complete your academic profile",
            "complete": bool(
                (current_user.education_level or "").strip()
                and (current_user.school or "").strip()
                and str(_academic_year_value() or "").strip()
            ),
            "href": url_for("settings.settings_page") + "#account",
        },
        {
            "id": "calendar",
            "label": "Connect or create a calendar",
            "complete": bool(calendar_complete),
            "href": url_for("dashboard.calendar"),
        },
        {
            "id": "tasks",
            "label": "Create your first task",
            "complete": bool(tasks_complete),
            "href": url_for("dashboard.tasks"),
        },
    ]
    completed = sum(1 for item in items if item["complete"])
    signature = _checklist_signature(items)
    return {
        "items": items,
        "completed": completed,
        "total": len(items),
        "complete": completed == len(items),
        "signature": signature,
    }


def _load_calendar_summary(user_id, user_settings):
    today = datetime.now(timezone.utc).date()
    today_start = datetime(today.year, today.month, today.day, tzinfo=timezone.utc)
    week_start_date = today - timedelta(days=(today.weekday() + 1) % 7)
    week_start = datetime(week_start_date.year, week_start_date.month, week_start_date.day, tzinfo=timezone.utc)
    week_end = week_start + timedelta(days=7)
    upcoming_end = today_start + timedelta(days=31)
    month_start = datetime(today.year, today.month, 1, tzinfo=timezone.utc)
    if today.month == 12:
        month_end = datetime(today.year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        month_end = datetime(today.year, today.month + 1, 1, tzinfo=timezone.utc)
    range_start = min(month_start, week_start, today_start)
    range_end = max(month_end, week_end, upcoming_end)

    try:
        from blueprints.calendar_api import (
            _configured_calendar_sources,
            _configured_feed_urls,
            _filter_configured_cache_events,
            _load_calendar_feed_metadata,
            _load_calendar_preferences,
            _load_local_calendar_sources,
            _serialize_event,
            _serialize_user_event,
        )

        feed_urls = _configured_feed_urls(user_settings)
        cache_rows = list_calendar_rows_all(
            COLLECTIONS["calendar_cache"],
            [
                Query.equal("user_id", [user_id]),
                Query.order_asc("event_start"),
            ],
        )
        event_rows = list_calendar_rows_all(
            COLLECTIONS["user_events"],
            [
                Query.equal("user_id", [user_id]),
                Query.order_asc("start"),
            ],
        )
        preferences = _load_calendar_preferences(user_id)
        local_sources = _load_local_calendar_sources(user_id)
        feed_metadata = _load_calendar_feed_metadata(user_id)
        sources = _configured_calendar_sources(
            user_settings,
            cache_rows,
            preferences,
            feed_metadata,
            local_sources,
            event_rows,
        )
        cache_rows = _filter_configured_cache_events(cache_rows, feed_urls)

        serialized = [_serialize_event(row, user_settings) for row in cache_rows]
        serialized.extend(_serialize_user_event(row) for row in event_rows)

        try:
            from blueprints.tasks_api import task_calendar_events_for_user

            serialized.extend(task_calendar_events_for_user(user_id, range_start, range_end))
        except (AppwriteException, AttributeError):
            logger.exception("Failed to load task events for dashboard calendar user %s", user_id)

        visible = []
        source_by_id = {source.get("id"): source for source in sources}
        for event in serialized:
            start = _as_utc(event.get("start"))
            end = _as_utc(event.get("end")) or start
            if not start:
                continue
            if end and end < range_start:
                continue
            if start >= range_end:
                continue
            source = source_by_id.get(event.get("calendar_id")) or {}
            color = event.get("color") or source.get("color_hex") or "#6366f1"
            visible.append({
                "id": event.get("id") or event.get("uid") or event.get("event_ref"),
                "title": event.get("title") or "Untitled event",
                "start": event.get("start"),
                "end": event.get("end"),
                "date": _date_key(event.get("start")),
                "color": color,
                "all_day": bool(event.get("is_all_day")),
            })
        visible.sort(key=lambda item: _sort_key(item.get("start")))
        month_events = [
            event for event in visible
            if _sort_key(event.get("end") or event.get("start")) >= month_start and _sort_key(event.get("start")) < month_end
        ]
        week_events = [
            event for event in visible
            if _sort_key(event.get("end") or event.get("start")) >= week_start and _sort_key(event.get("start")) < week_end
        ]
        upcoming_events = [
            event for event in visible
            if _sort_key(event.get("end") or event.get("start")) >= today_start and _sort_key(event.get("start")) < upcoming_end
        ]
        return {
            "month": today.isoformat()[:7],
            "week_start": week_start.date().isoformat(),
            "upcoming_start": today.isoformat(),
            "upcoming_end": (today + timedelta(days=30)).isoformat(),
            "events": month_events[:80],
            "week_events": week_events[:40],
            "upcoming_events": upcoming_events[:DASHBOARD_CALENDAR_UPCOMING_LIMIT],
            "event_count": len(month_events),
            "setup_complete": bool(feed_urls or local_sources or event_rows),
            "error": None,
        }
    except AppwriteException:
        logger.exception("Failed to build dashboard calendar summary")
        return {
            "month": today.isoformat()[:7],
            "week_start": week_start.date().isoformat(),
            "upcoming_start": today.isoformat(),
            "upcoming_end": (today + timedelta(days=30)).isoformat(),
            "events": [],
            "week_events": [],
            "upcoming_events": [],
            "event_count": 0,
            "setup_complete": False,
            "error": "Unable to load calendar.",
        }


def _task_is_complete(task):
    if task.get("recurrence_json"):
        return False
    return bool(task.get("completed"))


def _task_payload(row, now):
    deadline = _as_utc(row.get("deadline_at"))
    overdue = bool(deadline and deadline < now and not _task_is_complete(row))
    return {
        "id": _row_id(row),
        "title": row.get("title") or "Untitled task",
        "list_id": row.get("list_id") or "",
        "priority": row.get("priority") or "none",
        "deadline_at": format_datetime(deadline) if deadline else None,
        "overdue": overdue,
        "starred": bool(row.get("starred")),
    }


def _task_list_payload(row):
    list_id = _row_id(row)
    return {
        "id": list_id,
        "name": row.get("name") or "Untitled List",
        "order": row.get("order") or 0,
        "hidden": bool(row.get("hidden", False)),
    }


def _task_priority_rank(row):
    priority = str(row.get("priority") or "").strip().lower()
    return DASHBOARD_TASK_PRIORITY_RANK.get(priority, len(DASHBOARD_TASK_PRIORITY_RANK))


def _dashboard_task_bucket(row, now, seven_day_end, thirty_day_end):
    deadline = _as_utc(row.get("deadline_at"))
    if not deadline:
        return 2
    if deadline <= seven_day_end:
        return 0
    if deadline <= thirty_day_end:
        return 1
    return None


def _load_tasks_summary(user_id, selected_list_ids=None):
    now = datetime.now(timezone.utc)
    seven_day_end = now + timedelta(days=7)
    thirty_day_end = now + timedelta(days=30)
    try:
        list_rows = list_rows_all(
            COLLECTIONS.get("task_lists", "task_lists"),
            [
                Query.equal("user_id", [user_id]),
                Query.order_asc("order"),
            ],
        )
        rows = list_rows_all(
            COLLECTIONS["tasks"],
            [
                Query.equal("user_id", [user_id]),
                Query.order_asc("deadline_at"),
            ],
        )
    except AppwriteException:
        logger.exception("Failed to build dashboard task summary")
        return {"items": [], "lists": [], "selected_list_ids": [], "total_count": 0, "setup_complete": False, "error": "Unable to load tasks."}

    lists = [_task_list_payload(row) for row in sorted(list_rows, key=lambda item: item.get("order") or 0)]
    available_list_ids = [item["id"] for item in lists if item.get("id")]
    selected_ids = _normalize_task_list_ids(selected_list_ids, available_list_ids)
    selected_set = set(selected_ids)
    candidates = []
    for row in rows:
        if selected_set and str(row.get("list_id") or "") not in selected_set:
            continue
        if _task_is_complete(row):
            continue
        bucket = _dashboard_task_bucket(row, now, seven_day_end, thirty_day_end)
        if bucket is None:
            continue
        candidates.append((bucket, row))
    candidates.sort(key=lambda item: (
        item[0],
        _task_priority_rank(item[1]),
        _as_utc(item[1].get("deadline_at")) or datetime.max.replace(tzinfo=timezone.utc),
        item[1].get("title") or "",
    ))
    upcoming = [row for _, row in candidates]
    source_items = [_task_payload(row, now) for row in upcoming[:DASHBOARD_TASK_FILTER_SOURCE_LIMIT]]
    return {
        "items": source_items[:DASHBOARD_TASK_LIMIT],
        "all_items": source_items,
        "lists": lists,
        "selected_list_ids": selected_ids,
        "total_count": len(upcoming),
        "setup_complete": bool(rows),
        "error": None,
    }


def _load_recent_files(user_id):
    now = datetime.now(timezone.utc)
    try:
        rows = list_rows_all(
            COLLECTIONS["shared_files"],
            [Query.equal("user_id", [user_id])],
        )
    except AppwriteException:
        logger.exception("Failed to build dashboard file summary")
        return {"items": [], "total_count": 0, "error": "Unable to load files."}
    rows = [
        row for row in rows
        if not (expires_at := _as_utc(row.get("expires_at"))) or expires_at > now
    ]
    rows.sort(key=lambda row: _sort_key(row.get("updated_at") or row.get("created_at")), reverse=True)
    return {
        "items": [
            {
                "id": _row_id(row),
                "name": row.get("original_filename") or "Untitled file",
                "size_bytes": row.get("file_size_bytes") or 0,
                "updated_at": row.get("updated_at") or row.get("created_at"),
                "href": url_for("file_share.file_share_page"),
            }
            for row in rows[:DASHBOARD_LIST_LIMIT]
        ],
        "total_count": len(rows),
        "error": None,
    }


def _load_recent_notes(user_id):
    try:
        rows = list_rows_all(
            COLLECTIONS["notes"],
            [Query.equal("user_id", [user_id])],
        )
    except AppwriteException:
        logger.exception("Failed to build dashboard notes summary")
        return {"items": [], "total_count": 0, "error": "Unable to load notes."}
    rows.sort(key=lambda row: _sort_key(row.get("updated_at") or row.get("created_at")), reverse=True)
    return {
        "items": [
            {
                "id": _row_id(row),
                "title": row.get("title") or "Untitled note",
                "updated_at": row.get("updated_at") or row.get("created_at"),
                "href": url_for("dashboard.note_document", note_id=_row_id(row)),
            }
            for row in rows[:DASHBOARD_LIST_LIMIT]
        ],
        "total_count": len(rows),
        "error": None,
    }


def _load_message_rooms(user_id):
    try:
        channels = list_rows_all(COLLECTIONS["chat_channels"], [Query.order_asc("created_at")])
        thread_rows_a = list_rows_all(COLLECTIONS["chat_dm_threads"], [Query.equal("participant_a", [user_id])])
        thread_rows_b = list_rows_all(COLLECTIONS["chat_dm_threads"], [Query.equal("participant_b", [user_id])])
        message_rows = list_rows_safe(
            COLLECTIONS["chat_messages"],
            [Query.order_desc("created_at"), Query.limit(250)],
        ).get("rows", [])
    except AppwriteException:
        logger.exception("Failed to build dashboard message summary")
        return {"items": [], "total_count": 0, "error": "Unable to load messages."}

    channel_by_id = {_row_id(row): row for row in channels if _row_id(row)}
    thread_by_id = {_row_id(row): row for row in thread_rows_a + thread_rows_b if _row_id(row)}
    room_latest = {}
    for row in message_rows:
        if row.get("deleted_at"):
            continue
        channel_id = row.get("channel_id")
        thread_id = row.get("thread_id")
        if channel_id and channel_id in channel_by_id:
            key = ("channel", channel_id)
        elif thread_id and thread_id in thread_by_id:
            key = ("thread", thread_id)
        else:
            continue
        created_at = row.get("created_at")
        if key not in room_latest or _sort_key(created_at) > _sort_key(room_latest[key]):
            room_latest[key] = created_at

    rooms = []
    for (room_type, room_id), last_at in room_latest.items():
        if room_type == "channel":
            row = channel_by_id.get(room_id) or {}
            if not _dashboard_can_access_channel(row):
                continue
            label = row.get("label") or row.get("name") or "Channel"
            href = url_for("dashboard.chat", channel=room_id)
        else:
            row = thread_by_id.get(room_id) or {}
            other_id = row.get("participant_b") if row.get("participant_a") == user_id else row.get("participant_a")
            label = "Direct message"
            try:
                from appwrite_helpers import get_row_safe

                user = get_row_safe(COLLECTIONS["users"], other_id, allow_missing=True) if other_id else None
            except AppwriteException:
                user = None
            if user:
                label = user.get("name") or user.get("username") or label
            href = url_for("dashboard.chat", thread=room_id)
        rooms.append({
            "id": room_id,
            "type": room_type,
            "label": label,
            "last_activity_at": last_at,
            "href": href,
        })
    rooms.sort(key=lambda item: _sort_key(item.get("last_activity_at")), reverse=True)
    return {"items": rooms[:DASHBOARD_LIST_LIMIT], "total_count": len(rooms), "error": None}


def _dashboard_can_access_channel(channel):
    if not channel:
        return False
    kind = channel.get("kind")
    if kind == "discord":
        return True
    if kind == "university":
        return bool(channel.get("approved")) and channel.get("school_key") == getattr(current_user, "school_key", None)
    return False


def _load_courses_summary(user_id):
    if not _is_emory_or_oxford_user():
        return {"items": [], "total_count": 0, "available": False, "error": None}
    try:
        rows = list_rows_all(
            COLLECTIONS["user_courses"],
            [
                Query.equal("user_id", [user_id]),
                Query.order_asc("term"),
                Query.order_asc("subject"),
                Query.order_asc("catalog"),
            ],
        )
    except AppwriteException:
        logger.exception("Failed to build dashboard courses summary")
        return {"items": [], "total_count": 0, "available": False, "error": "Unable to load courses."}
    rows.sort(key=lambda row: _sort_key(row.get("updated_at") or row.get("added_at")), reverse=True)
    return {
        "items": [
            {
                "id": _row_id(row),
                "code": f"{row.get('subject') or ''} {row.get('catalog') or ''}".strip() or "Course",
                "name": row.get("course_name") or "",
                "term": row.get("term") or "",
                "section": row.get("section_number") or row.get("crn") or "",
                "updated_at": row.get("updated_at") or row.get("added_at"),
            }
            for row in rows[:DASHBOARD_LIST_LIMIT]
        ],
        "total_count": len(rows),
        "available": bool(rows),
        "error": None,
    }


def _dashboard_summary_payload():
    user_id = str(current_user.id)
    user_settings = _load_user_settings()
    saved_layout = _coerce_layout(user_settings.get("dashboard_layout_json") if user_settings else "[]")
    calendar_summary = _load_calendar_summary(user_id, user_settings)
    tasks_summary = _load_tasks_summary(user_id)
    files_summary = _load_recent_files(user_id)
    notes_summary = _load_recent_notes(user_id)
    messages_summary = _load_message_rooms(user_id)
    courses_summary = _load_courses_summary(user_id)

    available_tiles = ["calendar", "tasks", "files", "notes", "messages"]
    if courses_summary.get("available"):
        available_tiles.append("courses")

    checklist = _build_checklist(
        calendar_complete=calendar_summary.get("setup_complete"),
        tasks_complete=tasks_summary.get("setup_complete"),
    )
    hidden_signature = user_settings.get("dashboard_checklist_hidden_signature") if user_settings else ""
    checklist["hidden"] = bool(hidden_signature and hidden_signature == checklist["signature"])

    tile_layout = _ordered_tile_layout(saved_layout, available_tiles)

    return {
        "user": _user_payload(),
        "generated_at": format_datetime(datetime.now(timezone.utc)),
        "tile_layout_version": DASHBOARD_LAYOUT_VERSION,
        "tile_layout": tile_layout,
        "tile_order": [tile["type"] for tile in tile_layout],
        "dashboard_layout": {
            "version": DASHBOARD_LAYOUT_VERSION,
            "daily_quote_visible": saved_layout.get("daily_quote_visible"),
            "tiles": tile_layout,
        },
        "available_tiles": available_tiles,
        "checklist": checklist,
        "tiles": {
            "calendar": calendar_summary,
            "tasks": tasks_summary,
            "files": files_summary,
            "notes": notes_summary,
            "messages": messages_summary,
            "courses": courses_summary,
        },
    }


@dashboard_bp.route("/dashboard")
@login_required
def dashboard():
    """Render the authenticated user's dashboard."""
    if not current_user.onboarding_complete:
        return redirect(url_for("settings.onboarding"))

    user_settings = _load_user_settings()
    preferred_calendar_view = (
        user_settings.get("preferred_calendar_view")
        if user_settings and user_settings.get("preferred_calendar_view")
        else "week"
    )
    if preferred_calendar_view not in {"week", "month"}:
        preferred_calendar_view = "week"

    return render_template(
        "dashboard.html",
        user=_user_payload(),
        preferred_calendar_view=preferred_calendar_view,
        theme_preference=_theme_from_settings(user_settings),
    )


@dashboard_bp.route("/api/toasts")
def drain_toasts():
    """Return and clear any server-queued toasts for the current session.

    Drained once per page load by window.APStudyToast in global.js. Available to
    anonymous sessions too (e.g. post-logout messages).
    """
    return jsonify(pop_toasts())


@dashboard_bp.route("/api/dashboard/summary")
@login_required
def dashboard_summary():
    """Return bounded morning-brief data for the dashboard."""
    if not current_user.onboarding_complete:
        return jsonify({"error": "Onboarding is required."}), 403
    return jsonify(_dashboard_summary_payload())


@dashboard_bp.route("/api/dashboard/quote/today")
@login_required
def dashboard_quote_today():
    """Return today's UTC daily quote from Appwrite, falling back server-side."""
    if not current_user.onboarding_complete:
        return jsonify({"error": "Onboarding is required."}), 403
    quote = get_daily_quote_payload()
    return jsonify({"quote": quote, "dateKey": quote.get("date")})


@dashboard_bp.route("/api/dashboard/quote/error", methods=["POST"])
@login_required
def report_dashboard_quote_error():
    """Record client-side daily quote failures without proxying the quote API."""
    if not current_user.onboarding_complete:
        return jsonify({"error": "Onboarding is required."}), 403

    payload = request.get_json(silent=True) or {}
    metadata = _daily_quote_error_metadata(payload)
    user_id = str(current_user.id)
    actor = getattr(current_user, "email", None) or user_id
    log_metadata = {**metadata, "user_id": user_id}

    logger.warning("Daily quote error reported", extra={"daily_quote_error": log_metadata})
    try:
        emit_server_log_event(
            "Daily Quote Error",
            actor=actor,
            target="Dashboard Daily Quote",
            metadata=log_metadata,
            color="yellow",
        )
    except Exception:
        logger.exception(
            "Failed to emit daily quote error to Discord server log for user %s",
            current_user.id,
        )

    return jsonify({"status": "ok", "reason": metadata["reason"]})


@dashboard_bp.route("/api/dashboard/layout", methods=["PATCH"])
@login_required
def update_dashboard_layout():
    """Persist a validated v4 dashboard layout draft."""
    if not current_user.onboarding_complete:
        return jsonify({"error": "Onboarding is required."}), 403

    payload = request.get_json(silent=True) or {}
    raw_layout = payload.get("dashboard_layout", payload.get("tile_layout", payload.get("layout")))
    if raw_layout is None:
        raw_layout = payload.get("tiles")
    if raw_layout is None:
        raw_layout = payload.get("tile_order", payload.get("order"))
    if not isinstance(raw_layout, (dict, list)):
        return jsonify({"error": "tile_layout must be an object or list."}), 400

    raw_tiles = raw_layout.get("tiles") if isinstance(raw_layout, dict) else raw_layout
    if not isinstance(raw_tiles, list):
        return jsonify({"error": "tile_layout tiles must be a list."}), 400
    if len(raw_tiles) > DASHBOARD_TILE_LIMIT:
        return jsonify({"error": f"Dashboard layouts support at most {DASHBOARD_TILE_LIMIT} tiles."}), 400

    layout_version = _layout_version(raw_layout)
    raw_quote_visible = raw_layout.get("daily_quote_visible") if isinstance(raw_layout, dict) else True
    if raw_quote_visible is not None and not isinstance(raw_quote_visible, bool):
        return jsonify({"error": "daily_quote_visible must be true or false."}), 400
    daily_quote_visible = True if raw_quote_visible is None else raw_quote_visible

    user_id = str(current_user.id)
    owned_task_list_ids = None
    normalized_tiles = []
    seen_instances = set()
    type_counts = {}
    for item in raw_tiles:
        if isinstance(item, dict):
            tile_id = str(item.get("type") or item.get("id") or "").strip()
            instance_id = str(item.get("instance_id") or (item.get("id") if item.get("type") else "") or _legacy_instance_id(tile_id)).strip()
            raw_size = item.get("size")
            raw_view = item.get("view")
            raw_task_list_ids = item.get("task_list_ids")
        else:
            tile_id = str(item or "").strip()
            instance_id = _legacy_instance_id(tile_id)
            raw_size = None
            raw_view = None
            raw_task_list_ids = None
            item = {}
        if tile_id not in DASHBOARD_TILE_IDS:
            return jsonify({"error": f"Unknown dashboard tile: {tile_id or 'blank'}."}), 400
        if not instance_id or len(instance_id) > 80 or not all(character.isalnum() or character in "-_:." for character in instance_id):
            return jsonify({"error": f"Invalid dashboard tile instance: {instance_id or 'blank'}."}), 400
        if instance_id in seen_instances:
            return jsonify({"error": f"Duplicate dashboard tile instance: {instance_id}."}), 400
        type_counts[tile_id] = type_counts.get(tile_id, 0) + 1
        if type_counts[tile_id] > 1 and (layout_version < DASHBOARD_LAYOUT_VERSION or tile_id not in DASHBOARD_DUPLICATE_TILE_TYPES):
            return jsonify({"error": f"Duplicate dashboard tile: {tile_id}."}), 400
        if type_counts[tile_id] > DASHBOARD_DUPLICATE_TILE_LIMIT:
            return jsonify({"error": f"Dashboard supports at most {DASHBOARD_DUPLICATE_TILE_LIMIT} {tile_id} tiles."}), 400
        size = _validated_tile_size(tile_id, raw_size)
        if size is None:
            return jsonify({"error": f"Invalid size '{raw_size or 'blank'}' for dashboard tile: {tile_id}."}), 400
        raw_title = item.get("title")
        if raw_title is not None and not isinstance(raw_title, str):
            return jsonify({"error": "Dashboard tile titles must be text."}), 400
        title = str(raw_title or "").strip()
        if len(title) > DASHBOARD_TITLE_MAX_LENGTH:
            return jsonify({"error": f"Dashboard tile titles must be {DASHBOARD_TITLE_MAX_LENGTH} characters or fewer."}), 400
        try:
            item_limit = int(item.get("item_limit", 5))
        except (TypeError, ValueError):
            item_limit = None
        if item_limit not in DASHBOARD_ITEM_LIMITS:
            return jsonify({"error": "Dashboard tile item_limit must be 3, 5, or 8."}), 400
        density = str(item.get("density", "comfortable")).strip().lower()
        if density not in DASHBOARD_DENSITIES:
            return jsonify({"error": "Dashboard tile density must be compact or comfortable."}), 400
        tile_payload = {
            "instance_id": instance_id,
            "type": tile_id,
            "size": size,
            "density": density,
            "item_limit": item_limit,
        }
        if title:
            tile_payload["title"] = title
        if tile_id == "calendar":
            view = _validated_calendar_view(raw_view)
            if view is None:
                return jsonify({"error": f"Invalid calendar view '{raw_view or 'blank'}'."}), 400
            upcoming_days = item.get("upcoming_days", 7)
            if upcoming_days not in DASHBOARD_CALENDAR_UPCOMING_DAYS:
                return jsonify({"error": "Calendar upcoming_days must be 7, 14, or 30."}), 400
            tile_payload.update({"view": view, "upcoming_days": upcoming_days})
        if tile_id == "tasks" and raw_task_list_ids is not None:
            if not isinstance(raw_task_list_ids, list):
                return jsonify({"error": "Task list filters must be a list."}), 400
            if raw_task_list_ids:
                if owned_task_list_ids is None:
                    try:
                        owned_task_list_ids = {
                            _row_id(row)
                            for row in list_rows_all(
                                COLLECTIONS.get("task_lists", "task_lists"),
                                [Query.equal("user_id", [user_id])],
                            )
                        }
                    except AppwriteException:
                        logger.exception("Failed to validate dashboard task list filters")
                        return jsonify({"error": "Unable to validate task list filters."}), 500
                task_list_ids = _normalize_task_list_ids(raw_task_list_ids, owned_task_list_ids)
                if len(task_list_ids) != len({str(item or "").strip() for item in raw_task_list_ids if str(item or "").strip()}):
                    return jsonify({"error": "Task list filters must belong to your account."}), 400
                if not task_list_ids:
                    return jsonify({"error": "Select at least one task list or choose All."}), 400
                tile_payload["task_list_ids"] = task_list_ids
        if tile_id == "tasks":
            deadline_days = item.get("deadline_days", 30)
            if deadline_days not in DASHBOARD_TASK_DEADLINE_DAYS:
                return jsonify({"error": "Task deadline_days must be 7 or 30."}), 400
            raw_priorities = item.get("priorities", list(DASHBOARD_TASK_PRIORITIES))
            if not isinstance(raw_priorities, list):
                return jsonify({"error": "Task priorities must be a list."}), 400
            priorities = []
            for raw_priority in raw_priorities:
                priority = str(raw_priority or "").strip().lower()
                if priority not in DASHBOARD_TASK_PRIORITIES:
                    return jsonify({"error": f"Unknown task priority: {priority or 'blank'}."}), 400
                if priority not in priorities:
                    priorities.append(priority)
            if not priorities:
                return jsonify({"error": "Select at least one task priority."}), 400
            for boolean_field in ("include_overdue", "include_undated", "starred_only"):
                if boolean_field in item and not isinstance(item[boolean_field], bool):
                    return jsonify({"error": f"{boolean_field} must be true or false."}), 400
            tile_payload.update({
                "deadline_days": deadline_days,
                "include_overdue": item.get("include_overdue", True),
                "include_undated": item.get("include_undated", True),
                "priorities": priorities,
                "starred_only": item.get("starred_only", False),
            })
        normalized_tiles.append(tile_payload)
        seen_instances.add(instance_id)

    normalized = {
        "version": DASHBOARD_LAYOUT_VERSION,
        "daily_quote_visible": daily_quote_visible,
        "tiles": normalized_tiles,
    }

    try:
        settings = _ensure_user_settings(user_id)
        settings = update_row_safe(
            COLLECTIONS["user_settings"],
            _settings_row_id(settings),
            {
                "dashboard_layout_json": json.dumps(normalized, separators=(",", ":")),
                "updated_at": format_datetime(datetime.now(timezone.utc)),
            },
        )
    except AppwriteException:
        logger.exception("Failed to save dashboard layout")
        return jsonify({"error": "Unable to save dashboard layout."}), 500

    saved_layout = _coerce_layout(settings.get("dashboard_layout_json"))
    return jsonify({
        "status": "ok",
        "dashboard_layout": saved_layout,
        "tile_layout": saved_layout["tiles"],
        "tile_order": [tile["type"] for tile in saved_layout["tiles"]],
    })


@dashboard_bp.route("/api/dashboard/checklist/hidden", methods=["POST"])
@login_required
def update_dashboard_checklist_hidden():
    """Persist whether the current checklist state is hidden."""
    if not current_user.onboarding_complete:
        return jsonify({"error": "Onboarding is required."}), 403

    payload = request.get_json(silent=True) or {}
    hidden = bool(payload.get("hidden"))
    summary = _dashboard_summary_payload()
    signature = summary.get("checklist", {}).get("signature") or ""

    user_id = str(current_user.id)
    try:
        settings = _ensure_user_settings(user_id)
        update_row_safe(
            COLLECTIONS["user_settings"],
            _settings_row_id(settings),
            {
                "dashboard_checklist_hidden_signature": signature if hidden else "",
                "updated_at": format_datetime(datetime.now(timezone.utc)),
            },
        )
    except AppwriteException:
        logger.exception("Failed to save dashboard checklist visibility")
        return jsonify({"error": "Unable to save checklist preference."}), 500

    return jsonify({"status": "ok", "hidden": hidden, "signature": signature})


@dashboard_bp.route("/calendar")
@login_required
def calendar():
    """Render the calendar page with user and preference context."""
    if not current_user.onboarding_complete:
        return redirect(url_for("settings.onboarding"))

    user_settings = _load_user_settings()
    preferred_calendar_view = (
        user_settings.get("preferred_calendar_view")
        if user_settings and user_settings.get("preferred_calendar_view")
        else "week"
    )
    if preferred_calendar_view not in {"week", "month"}:
        preferred_calendar_view = "week"
    interface_theme = _theme_from_settings(user_settings)
    try:
        calendar_buffer_days = int(os.environ.get("CALENDAR_DATE_BUFFER_DAYS", "7"))
    except (TypeError, ValueError):
        calendar_buffer_days = 7
    
    return render_template(
        "calendar.html",
        user=_user_payload(),
        preferred_calendar_view=preferred_calendar_view,
        theme_preference=interface_theme,
        calendar_buffer_days=calendar_buffer_days,
    )


@dashboard_bp.route("/calendar/share/<share_code>")
def public_calendar_share(share_code):
    """Render a public read-only shared calendar page."""
    from blueprints.calendar_api import _public_calendar_share_context, _resolve_calendar_share_by_code

    try:
        share = _resolve_calendar_share_by_code(share_code, active_only=True)
    except AppwriteException:
        logger.exception("Failed to resolve public calendar share")
        share = None

    theme_preference = None
    if current_user.is_authenticated:
        theme_preference = _theme_from_settings(_load_user_settings())

    try:
        calendar_buffer_days = int(os.environ.get("CALENDAR_DATE_BUFFER_DAYS", "7"))
    except (TypeError, ValueError):
        calendar_buffer_days = 7

    if not share:
        return render_template(
            "calendar_share.html",
            share_found=False,
            share_code=share_code,
            owner_name="",
            scope_label="",
            theme_preference=theme_preference,
            preferred_calendar_view="month",
            calendar_buffer_days=calendar_buffer_days,
        ), 404

    context = _public_calendar_share_context(share)
    return render_template(
        "calendar_share.html",
        share_found=True,
        preferred_calendar_view="month",
        theme_preference=theme_preference,
        calendar_buffer_days=calendar_buffer_days,
        **context,
    )


@dashboard_bp.route("/courses")
@login_required
def courses():
    """Render the Emory-only course planning page."""
    if not current_user.onboarding_complete:
        return redirect(url_for("settings.onboarding"))
    if not _is_emory_or_oxford_user():
        return redirect(url_for("dashboard.dashboard"))

    user_settings = _load_user_settings()
    return render_template(
        "courses.html",
        user=_user_payload(),
        theme_preference=_theme_from_settings(user_settings),
        default_term=DEFAULT_TERM,
        default_campus=_default_courses_campus(),
        atlas_srcdb=get_atlas_term_srcdb(),
        general_ed_requirements=get_starred_general_ed_requirements(),
        general_ed_requirement_aliases=get_general_ed_requirement_aliases(),
        general_ed_composite_requirements=get_general_ed_composite_requirements(),
    )


@dashboard_bp.route("/notes")
@login_required
def notes():
    """Render the notes page."""
    if not current_user.onboarding_complete:
        return redirect(url_for("settings.onboarding"))

    user_settings = _load_user_settings()
    
    return render_template(
        "notes.html",
        user=_user_payload(),
        theme_preference=_theme_from_settings(user_settings),
    )


@dashboard_bp.route("/task")
@login_required
def task_redirect():
    """Redirect the legacy task URL to the canonical tasks page."""
    return redirect(url_for("dashboard.tasks", **request.args))


@dashboard_bp.route("/tasks")
@login_required
def tasks():
    """Render the task management page."""
    if not current_user.onboarding_complete:
        return redirect(url_for("settings.onboarding"))

    user_settings = _load_user_settings()
    return render_template(
        "task.html",
        user=_user_payload(),
        theme_preference=_theme_from_settings(user_settings),
    )


def _shared_notes_page_context(resource, access, *, page_state="ready"):
    viewer_authenticated = current_user.is_authenticated
    user = _user_payload() if viewer_authenticated else None
    theme_preference = "system-match"
    if viewer_authenticated:
        theme_preference = _theme_from_settings(_load_user_settings()) or "system-match"
    owner = note_store.get_safe_user(resource.get("user_id")) if resource else None
    if access.get("source") in {"folder_user", "folder_public"} and access.get("source_id"):
        back_url = url_for("dashboard.shared_note_folder", folder_id=access["source_id"])
        back_label = "Shared folder"
    elif viewer_authenticated and access.get("role") == "viewer":
        back_url = url_for("dashboard.notes", view="shared")
        back_label = "Shared with me"
    elif viewer_authenticated:
        back_url = url_for("dashboard.notes")
        back_label = "Notes"
    else:
        back_url = url_for("auth.index")
        back_label = "Nest.APStudy"
    return {
        "user": user,
        "viewer_authenticated": viewer_authenticated,
        "access": access,
        "owner": owner,
        "page_state": page_state,
        "theme_preference": theme_preference,
        "login_url": url_for("auth.login", next=request.path),
        "back_url": back_url,
        "back_label": back_label,
    }


@dashboard_bp.route("/notes/editor")
def legacy_notes_editor_root():
    return redirect(url_for("dashboard.notes"), code=308)


@dashboard_bp.route("/notes/editor/<note_id>")
def legacy_notes_editor(note_id):
    return redirect(url_for("dashboard.note_document", note_id=note_id, **request.args), code=308)


@dashboard_bp.route("/notes/<note_id>")
def note_document(note_id):
    """Render an owned or shared note at its canonical URL."""
    note = note_store.get_note(note_id)
    if not note:
        context = _shared_notes_page_context(None, note_store.resolve_note_access(None), page_state="unavailable")
        return render_template("notes_editor.html", note_id=note_id, **context), 404

    viewer_id = str(current_user.id) if current_user.is_authenticated else None
    access = note_store.resolve_note_access(note, viewer_id)
    if not access["can_view"]:
        page_state = "login_required" if not current_user.is_authenticated else "unavailable"
        context = _shared_notes_page_context(None, access, page_state=page_state)
        status = 401 if page_state == "login_required" else 404
        return render_template("notes_editor.html", note_id=note_id, **context), status

    if current_user.is_authenticated and not current_user.onboarding_complete:
        return redirect(url_for("settings.onboarding"))
    context = _shared_notes_page_context(note, access)
    return render_template("notes_editor.html", note_id=note_id, **context)


@dashboard_bp.route("/notes/folders/<folder_id>")
def shared_note_folder(folder_id):
    """Render an owned or shared note folder at its stable URL."""
    folder = note_store.get_folder(folder_id)
    if not folder:
        context = _shared_notes_page_context(None, note_store.resolve_folder_access(None), page_state="unavailable")
        return render_template("notes_shared_folder.html", folder=None, notes=[], **context), 404

    viewer_id = str(current_user.id) if current_user.is_authenticated else None
    access = note_store.resolve_folder_access(folder, viewer_id)
    if not access["can_view"]:
        page_state = "login_required" if not current_user.is_authenticated else "unavailable"
        context = _shared_notes_page_context(None, access, page_state=page_state)
        status = 401 if page_state == "login_required" else 404
        return render_template("notes_shared_folder.html", folder=None, notes=[], **context), status

    if current_user.is_authenticated and not current_user.onboarding_complete:
        return redirect(url_for("settings.onboarding"))
    context = _shared_notes_page_context(folder, access)
    if access.get("role") == "owner":
        context["back_url"] = url_for("dashboard.notes", folder=folder_id)
        context["back_label"] = "My Notes"
    elif current_user.is_authenticated:
        context["back_url"] = url_for("dashboard.notes", view="shared")
        context["back_label"] = "Shared with me"
    else:
        context["back_url"] = url_for("auth.index")
        context["back_label"] = "Nest.APStudy"
    notes = note_store.list_notes_in_folder(folder_id)
    return render_template("notes_shared_folder.html", folder=folder, notes=notes, **context)


@dashboard_bp.route("/chat")
@login_required
def chat():
    """Render the chat page."""
    if not current_user.onboarding_complete:
        return redirect(url_for("settings.onboarding"))

    user_settings = _load_user_settings()
    return render_template(
        "chat.html",
        user=_user_payload(),
        theme_preference=_theme_from_settings(user_settings),
        discord_invite_url=os.environ.get("DISCORD_INVITE_URL", ""),
    )
