"""Dashboard tile summary loaders."""

import json
from datetime import datetime, timedelta, timezone

from appwrite.exception import AppwriteException
from appwrite.query import Query

from appwrite_client import COLLECTIONS
from appwrite_helpers import get_row_safe, parse_datetime


DASHBOARD_LIST_LIMIT = 8
DASHBOARD_CALENDAR_UPCOMING_LIMIT = 80
DASHBOARD_TASK_LIMIT = 8
DASHBOARD_TASK_FILTER_SOURCE_LIMIT = 100
DASHBOARD_TASK_PRIORITY_RANK = {
    "high": 0,
    "medium": 1,
    "low": 2,
}


def as_utc(value):
    parsed = parse_datetime(value)
    if not parsed:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def date_key(value):
    parsed = as_utc(value)
    if parsed:
        return parsed.date().isoformat()
    text = str(value or "").strip()
    return text[:10] if len(text) >= 10 else ""


def sort_key(value):
    parsed = as_utc(value)
    return parsed or datetime.min.replace(tzinfo=timezone.utc)


def load_calendar_summary(user_id, user_settings, dependencies):
    today = datetime.now(timezone.utc).date()
    today_start = datetime(
        today.year,
        today.month,
        today.day,
        tzinfo=timezone.utc,
    )
    week_start_date = today - timedelta(days=(today.weekday() + 1) % 7)
    week_start = datetime(
        week_start_date.year,
        week_start_date.month,
        week_start_date.day,
        tzinfo=timezone.utc,
    )
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
        feed_urls = dependencies["configured_feed_urls"](user_settings)
        cache_rows = dependencies["list_calendar_rows_all"](
            COLLECTIONS["calendar_cache"],
            [
                Query.equal("user_id", [user_id]),
                Query.order_asc("event_start"),
            ],
        )
        event_rows = dependencies["list_calendar_rows_all"](
            COLLECTIONS["user_events"],
            [
                Query.equal("user_id", [user_id]),
                Query.order_asc("start"),
            ],
        )
        preferences = dependencies["load_calendar_preferences"](
            user_id,
            dependencies["list_calendar_rows_all"],
        )
        local_sources = dependencies["load_local_calendar_sources"](
            user_id,
            dependencies["list_calendar_rows_all"],
        )
        feed_metadata = dependencies["load_calendar_feed_metadata"](
            user_id,
            dependencies["list_calendar_rows_all"],
        )
        sources = dependencies["configured_calendar_sources"](
            user_settings,
            cache_rows,
            preferences,
            feed_metadata,
            local_sources,
            event_rows,
        )
        cache_rows = dependencies["filter_configured_cache_events"](
            cache_rows,
            feed_urls,
        )

        serialized = [
            dependencies["serialize_event"](row, user_settings)
            for row in cache_rows
        ]
        serialized.extend(
            dependencies["serialize_user_event"](row) for row in event_rows
        )

        try:
            serialized.extend(
                dependencies["task_calendar_events_for_user"](
                    user_id,
                    range_start,
                    range_end,
                )
            )
        except (AppwriteException, AttributeError):
            dependencies["logger"].exception(
                "Failed to load task events for dashboard calendar user %s",
                user_id,
            )

        visible = []
        source_by_id = {source.get("id"): source for source in sources}
        for event in serialized:
            start = dependencies["as_utc"](event.get("start"))
            end = dependencies["as_utc"](event.get("end")) or start
            if not start:
                continue
            if end and end < range_start:
                continue
            if start >= range_end:
                continue
            source = source_by_id.get(event.get("calendar_id")) or {}
            color = event.get("color") or source.get("color_hex") or "#6366f1"
            visible.append({
                "id": (
                    event.get("id")
                    or event.get("uid")
                    or event.get("event_ref")
                ),
                "title": event.get("title") or "Untitled event",
                "start": event.get("start"),
                "end": event.get("end"),
                "date": dependencies["date_key"](event.get("start")),
                "color": color,
                "all_day": bool(event.get("is_all_day")),
            })
        visible.sort(
            key=lambda item: dependencies["sort_key"](item.get("start"))
        )
        month_events = [
            event
            for event in visible
            if dependencies["sort_key"](
                event.get("end") or event.get("start")
            ) >= month_start
            and dependencies["sort_key"](event.get("start")) < month_end
        ]
        week_events = [
            event
            for event in visible
            if dependencies["sort_key"](
                event.get("end") or event.get("start")
            ) >= week_start
            and dependencies["sort_key"](event.get("start")) < week_end
        ]
        upcoming_events = [
            event
            for event in visible
            if dependencies["sort_key"](
                event.get("end") or event.get("start")
            ) >= today_start
            and dependencies["sort_key"](event.get("start")) < upcoming_end
        ]
        return {
            "month": today.isoformat()[:7],
            "week_start": week_start.date().isoformat(),
            "upcoming_start": today.isoformat(),
            "upcoming_end": (today + timedelta(days=30)).isoformat(),
            "events": month_events[:80],
            "week_events": week_events[:40],
            "upcoming_events": upcoming_events[
                :DASHBOARD_CALENDAR_UPCOMING_LIMIT
            ],
            "event_count": len(month_events),
            "setup_complete": bool(feed_urls or local_sources or event_rows),
            "error": None,
        }
    except AppwriteException:
        dependencies["logger"].exception(
            "Failed to build dashboard calendar summary"
        )
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


def task_is_complete(task):
    if task.get("recurrence_json"):
        return False
    return bool(task.get("completed"))


def task_payload(row, now, dependencies):
    deadline = dependencies["as_utc"](row.get("deadline_at"))
    overdue = bool(
        deadline
        and deadline < now
        and not dependencies["task_is_complete"](row)
    )
    return {
        "id": dependencies["row_id"](row),
        "title": row.get("title") or "Untitled task",
        "list_id": row.get("list_id") or "",
        "priority": row.get("priority") or "none",
        "deadline_at": dependencies["format_datetime"](deadline) if deadline else None,
        "overdue": overdue,
        "starred": bool(row.get("starred")),
    }


def task_list_payload(row, dependencies):
    list_id = dependencies["row_id"](row)
    return {
        "id": list_id,
        "name": row.get("name") or "Untitled List",
        "order": row.get("order") or 0,
        "hidden": bool(row.get("hidden", False)),
    }


def task_priority_rank(row):
    priority = str(row.get("priority") or "").strip().lower()
    return DASHBOARD_TASK_PRIORITY_RANK.get(
        priority,
        len(DASHBOARD_TASK_PRIORITY_RANK),
    )


def dashboard_task_bucket(row, now, seven_day_end, thirty_day_end, as_utc_fn=as_utc):
    deadline = as_utc_fn(row.get("deadline_at"))
    if not deadline:
        return 2
    if deadline <= seven_day_end:
        return 0
    if deadline <= thirty_day_end:
        return 1
    return None


def load_tasks_summary(user_id, selected_list_ids, dependencies):
    now = datetime.now(timezone.utc)
    seven_day_end = now + timedelta(days=7)
    thirty_day_end = now + timedelta(days=30)
    try:
        list_rows = dependencies["list_rows_all"](
            COLLECTIONS.get("task_lists", "task_lists"),
            [
                Query.equal("user_id", [user_id]),
                Query.order_asc("order"),
            ],
        )
        rows = dependencies["list_rows_all"](
            COLLECTIONS["tasks"],
            [
                Query.equal("user_id", [user_id]),
                Query.order_asc("deadline_at"),
            ],
        )
    except AppwriteException:
        dependencies["logger"].exception("Failed to build dashboard task summary")
        return {
            "items": [],
            "lists": [],
            "selected_list_ids": [],
            "total_count": 0,
            "setup_complete": False,
            "error": "Unable to load tasks.",
        }

    lists = [
        dependencies["task_list_payload"](row)
        for row in sorted(list_rows, key=lambda item: item.get("order") or 0)
    ]
    available_list_ids = [item["id"] for item in lists if item.get("id")]
    selected_ids = dependencies["normalize_task_list_ids"](
        selected_list_ids,
        available_list_ids,
    )
    selected_set = set(selected_ids)
    candidates = []
    for row in rows:
        if selected_set and str(row.get("list_id") or "") not in selected_set:
            continue
        if dependencies["task_is_complete"](row):
            continue
        bucket = dependencies["dashboard_task_bucket"](
            row,
            now,
            seven_day_end,
            thirty_day_end,
        )
        if bucket is None:
            continue
        candidates.append((bucket, row))
    candidates.sort(key=lambda item: (
        item[0],
        dependencies["task_priority_rank"](item[1]),
        dependencies["as_utc"](item[1].get("deadline_at"))
        or datetime.max.replace(tzinfo=timezone.utc),
        item[1].get("title") or "",
    ))
    upcoming = [row for _, row in candidates]
    source_items = [
        dependencies["task_payload"](row, now)
        for row in upcoming[:DASHBOARD_TASK_FILTER_SOURCE_LIMIT]
    ]
    return {
        "items": source_items[:DASHBOARD_TASK_LIMIT],
        "all_items": source_items,
        "lists": lists,
        "selected_list_ids": selected_ids,
        "total_count": len(upcoming),
        "setup_complete": bool(rows),
        "error": None,
    }


def load_recent_files(user_id, dependencies):
    now = datetime.now(timezone.utc)
    try:
        rows = dependencies["list_rows_all"](
            COLLECTIONS["shared_files"],
            [Query.equal("user_id", [user_id])],
        )
    except AppwriteException:
        dependencies["logger"].exception("Failed to build dashboard file summary")
        return {
            "items": [],
            "total_count": 0,
            "error": "Unable to load files.",
        }
    rows = [
        row
        for row in rows
        if not (expires_at := dependencies["as_utc"](row.get("expires_at")))
        or expires_at > now
    ]
    rows.sort(
        key=lambda row: dependencies["sort_key"](
            row.get("updated_at") or row.get("created_at")
        ),
        reverse=True,
    )
    return {
        "items": [
            {
                "id": dependencies["row_id"](row),
                "name": row.get("original_filename") or "Untitled file",
                "size_bytes": row.get("file_size_bytes") or 0,
                "updated_at": row.get("updated_at") or row.get("created_at"),
                "href": dependencies["url_for"]("file_share.file_share_page"),
            }
            for row in rows[:DASHBOARD_LIST_LIMIT]
        ],
        "total_count": len(rows),
        "error": None,
    }


def load_recent_notes(user_id, dependencies):
    try:
        rows = dependencies["list_rows_all"](
            COLLECTIONS["notes"],
            [Query.equal("user_id", [user_id])],
        )
    except AppwriteException:
        dependencies["logger"].exception("Failed to build dashboard notes summary")
        return {
            "items": [],
            "total_count": 0,
            "error": "Unable to load notes.",
        }
    rows.sort(
        key=lambda row: dependencies["sort_key"](
            row.get("updated_at") or row.get("created_at")
        ),
        reverse=True,
    )
    return {
        "items": [
            {
                "id": dependencies["row_id"](row),
                "title": row.get("title") or "Untitled note",
                "updated_at": row.get("updated_at") or row.get("created_at"),
                "href": dependencies["url_for"](
                    "dashboard.note_document",
                    note_id=dependencies["row_id"](row),
                ),
            }
            for row in rows[:DASHBOARD_LIST_LIMIT]
        ],
        "total_count": len(rows),
        "error": None,
    }


def can_access_channel(channel, current_user):
    if not channel:
        return False
    kind = channel.get("kind")
    if kind == "discord":
        return True
    if kind == "university":
        return bool(channel.get("approved")) and channel.get(
            "school_key"
        ) == getattr(current_user, "school_key", None)
    return False


def load_message_rooms(user_id, dependencies):
    url_for = dependencies["url_for"]
    try:
        channels = dependencies["list_rows_all"](
            COLLECTIONS["chat_channels"],
            [Query.order_asc("created_at")],
        )
        thread_rows_a = dependencies["list_rows_all"](
            COLLECTIONS["chat_dm_threads"],
            [Query.equal("participant_a", [user_id])],
        )
        thread_rows_b = dependencies["list_rows_all"](
            COLLECTIONS["chat_dm_threads"],
            [Query.equal("participant_b", [user_id])],
        )
        message_rows = dependencies["list_rows_safe"](
            COLLECTIONS["chat_messages"],
            [Query.order_desc("created_at"), Query.limit(250)],
        ).get("rows", [])
    except AppwriteException:
        dependencies["logger"].exception("Failed to build dashboard message summary")
        return {
            "items": [],
            "total_count": 0,
            "error": "Unable to load messages.",
        }

    channel_by_id = {
        dependencies["row_id"](row): row
        for row in channels
        if dependencies["row_id"](row)
    }
    thread_by_id = {
        dependencies["row_id"](row): row
        for row in thread_rows_a + thread_rows_b
        if dependencies["row_id"](row)
    }
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
        if (
            key not in room_latest
            or dependencies["sort_key"](created_at)
            > dependencies["sort_key"](room_latest[key])
        ):
            room_latest[key] = created_at

    rooms = []
    for (room_type, room_id), last_at in room_latest.items():
        if room_type == "channel":
            row = channel_by_id.get(room_id) or {}
            if not dependencies["can_access_channel"](row):
                continue
            label = row.get("label") or row.get("name") or "Channel"
            href = url_for("dashboard.chat", channel=room_id)
        else:
            row = thread_by_id.get(room_id) or {}
            other_id = (
                row.get("participant_b")
                if row.get("participant_a") == user_id
                else row.get("participant_a")
            )
            label = "Direct message"
            try:
                user = (
                    get_row_safe(
                        COLLECTIONS["users"],
                        other_id,
                        allow_missing=True,
                    )
                    if other_id
                    else None
                )
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
    rooms.sort(
        key=lambda item: dependencies["sort_key"](item.get("last_activity_at")),
        reverse=True,
    )
    return {
        "items": rooms[:DASHBOARD_LIST_LIMIT],
        "total_count": len(rooms),
        "error": None,
    }


def load_courses_summary(user_id, available, dependencies):
    if not available:
        return {
            "items": [],
            "total_count": 0,
            "available": False,
            "error": None,
        }
    try:
        rows = dependencies["list_rows_all"](
            COLLECTIONS["user_courses"],
            [
                Query.equal("user_id", [user_id]),
                Query.order_asc("term"),
                Query.order_asc("subject"),
                Query.order_asc("catalog"),
            ],
        )
    except AppwriteException:
        dependencies["logger"].exception("Failed to build dashboard courses summary")
        return {
            "items": [],
            "total_count": 0,
            "available": False,
            "error": "Unable to load courses.",
        }
    rows.sort(
        key=lambda row: dependencies["sort_key"](
            row.get("updated_at") or row.get("added_at")
        ),
        reverse=True,
    )
    return {
        "items": [
            {
                "id": dependencies["row_id"](row),
                "code": (
                    f"{row.get('subject') or ''} {row.get('catalog') or ''}".strip()
                    or "Course"
                ),
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


def save_dashboard_layout(current_user, payload, dependencies):
    """Validate and persist the current user's dashboard layout."""
    jsonify = dependencies["jsonify"]
    list_rows_all = dependencies["list_rows_all"]
    logger = dependencies["logger"]
    update_row_safe = dependencies["update_row_safe"]
    _coerce_layout = dependencies["coerce_layout"]
    _ensure_user_settings = dependencies["ensure_user_settings"]
    _layout_version = dependencies["layout_version"]
    _legacy_instance_id = dependencies["legacy_instance_id"]
    _normalize_task_list_ids = dependencies["normalize_task_list_ids"]
    _row_id = dependencies["row_id"]
    _settings_row_id = dependencies["settings_row_id"]
    _validated_calendar_view = dependencies["validated_calendar_view"]
    _validated_tile_size = dependencies["validated_tile_size"]

    if not current_user.onboarding_complete:
        return jsonify({"error": "Onboarding is required."}), 403

    raw_layout = payload.get(
        "dashboard_layout",
        payload.get("tile_layout", payload.get("layout")),
    )
    if raw_layout is None:
        raw_layout = payload.get("tiles")
    if raw_layout is None:
        raw_layout = payload.get("tile_order", payload.get("order"))
    if not isinstance(raw_layout, (dict, list)):
        return jsonify({"error": "tile_layout must be an object or list."}), 400

    raw_tiles = (
        raw_layout.get("tiles")
        if isinstance(raw_layout, dict)
        else raw_layout
    )
    if not isinstance(raw_tiles, list):
        return jsonify({"error": "tile_layout tiles must be a list."}), 400
    if len(raw_tiles) > dependencies["tile_limit"]:
        return jsonify({
            "error": (
                "Dashboard layouts support at most "
                f"{dependencies['tile_limit']} tiles."
            )
        }), 400

    layout_version = _layout_version(raw_layout)
    raw_quote_visible = (
        raw_layout.get("daily_quote_visible")
        if isinstance(raw_layout, dict)
        else True
    )
    if raw_quote_visible is not None and not isinstance(
        raw_quote_visible,
        bool,
    ):
        return jsonify({
            "error": "daily_quote_visible must be true or false."
        }), 400
    daily_quote_visible = (
        True if raw_quote_visible is None else raw_quote_visible
    )

    user_id = str(current_user.id)
    owned_task_list_ids = None
    normalized_tiles = []
    seen_instances = set()
    type_counts = {}
    for item in raw_tiles:
        if isinstance(item, dict):
            tile_id = str(item.get("type") or item.get("id") or "").strip()
            instance_id = str(
                item.get("instance_id")
                or (item.get("id") if item.get("type") else "")
                or _legacy_instance_id(tile_id)
            ).strip()
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
        if tile_id not in dependencies["tile_ids"]:
            return jsonify({
                "error": f"Unknown dashboard tile: {tile_id or 'blank'}."
            }), 400
        if (
            not instance_id
            or len(instance_id) > 80
            or not all(
                character.isalnum() or character in "-_:."
                for character in instance_id
            )
        ):
            return jsonify({
                "error": (
                    "Invalid dashboard tile instance: "
                    f"{instance_id or 'blank'}."
                )
            }), 400
        if instance_id in seen_instances:
            return jsonify({
                "error": f"Duplicate dashboard tile instance: {instance_id}."
            }), 400
        type_counts[tile_id] = type_counts.get(tile_id, 0) + 1
        if type_counts[tile_id] > 1 and (
            layout_version < dependencies["layout_version_number"]
            or tile_id not in dependencies["duplicate_tile_types"]
        ):
            return jsonify({
                "error": f"Duplicate dashboard tile: {tile_id}."
            }), 400
        if type_counts[tile_id] > dependencies["duplicate_tile_limit"]:
            return jsonify({
                "error": (
                    "Dashboard supports at most "
                    f"{dependencies['duplicate_tile_limit']} "
                    f"{tile_id} tiles."
                )
            }), 400
        size = _validated_tile_size(tile_id, raw_size)
        if size is None:
            return jsonify({
                "error": (
                    f"Invalid size '{raw_size or 'blank'}' "
                    f"for dashboard tile: {tile_id}."
                )
            }), 400
        raw_title = item.get("title")
        if raw_title is not None and not isinstance(raw_title, str):
            return jsonify({
                "error": "Dashboard tile titles must be text."
            }), 400
        title = str(raw_title or "").strip()
        if len(title) > dependencies["title_max_length"]:
            return jsonify({
                "error": (
                    "Dashboard tile titles must be "
                    f"{dependencies['title_max_length']} characters or fewer."
                )
            }), 400
        try:
            item_limit = int(item.get("item_limit", 5))
        except (TypeError, ValueError):
            item_limit = None
        if item_limit not in dependencies["item_limits"]:
            return jsonify({
                "error": "Dashboard tile item_limit must be 3, 5, or 8."
            }), 400
        density = str(item.get("density", "comfortable")).strip().lower()
        if density not in dependencies["densities"]:
            return jsonify({
                "error": "Dashboard tile density must be compact or comfortable."
            }), 400
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
                return jsonify({
                    "error": (
                        f"Invalid calendar view '{raw_view or 'blank'}'."
                    )
                }), 400
            upcoming_days = item.get("upcoming_days", 7)
            if upcoming_days not in dependencies["calendar_upcoming_days"]:
                return jsonify({
                    "error": "Calendar upcoming_days must be 7, 14, or 30."
                }), 400
            tile_payload.update({
                "view": view,
                "upcoming_days": upcoming_days,
            })
        if tile_id == "tasks" and raw_task_list_ids is not None:
            if not isinstance(raw_task_list_ids, list):
                return jsonify({
                    "error": "Task list filters must be a list."
                }), 400
            if raw_task_list_ids:
                if owned_task_list_ids is None:
                    try:
                        owned_task_list_ids = {
                            _row_id(row)
                            for row in list_rows_all(
                                COLLECTIONS.get(
                                    "task_lists",
                                    "task_lists",
                                ),
                                [Query.equal("user_id", [user_id])],
                            )
                        }
                    except AppwriteException:
                        logger.exception(
                            "Failed to validate dashboard task list filters"
                        )
                        return jsonify({
                            "error": "Unable to validate task list filters."
                        }), 500
                task_list_ids = _normalize_task_list_ids(
                    raw_task_list_ids,
                    owned_task_list_ids,
                )
                normalized_input_ids = {
                    str(item or "").strip()
                    for item in raw_task_list_ids
                    if str(item or "").strip()
                }
                if len(task_list_ids) != len(normalized_input_ids):
                    return jsonify({
                        "error": (
                            "Task list filters must belong to your account."
                        )
                    }), 400
                if not task_list_ids:
                    return jsonify({
                        "error": (
                            "Select at least one task list or choose All."
                        )
                    }), 400
                tile_payload["task_list_ids"] = task_list_ids
        if tile_id == "tasks":
            deadline_days = item.get("deadline_days", 30)
            if deadline_days not in dependencies["task_deadline_days"]:
                return jsonify({
                    "error": "Task deadline_days must be 7 or 30."
                }), 400
            raw_priorities = item.get(
                "priorities",
                list(dependencies["task_priorities"]),
            )
            if not isinstance(raw_priorities, list):
                return jsonify({
                    "error": "Task priorities must be a list."
                }), 400
            priorities = []
            for raw_priority in raw_priorities:
                priority = str(raw_priority or "").strip().lower()
                if priority not in dependencies["task_priorities"]:
                    return jsonify({
                        "error": (
                            f"Unknown task priority: {priority or 'blank'}."
                        )
                    }), 400
                if priority not in priorities:
                    priorities.append(priority)
            if not priorities:
                return jsonify({
                    "error": "Select at least one task priority."
                }), 400
            for boolean_field in (
                "include_overdue",
                "include_undated",
                "starred_only",
            ):
                if (
                    boolean_field in item
                    and not isinstance(item[boolean_field], bool)
                ):
                    return jsonify({
                        "error": f"{boolean_field} must be true or false."
                    }), 400
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
        "version": dependencies["layout_version_number"],
        "daily_quote_visible": daily_quote_visible,
        "tiles": normalized_tiles,
    }

    try:
        settings = _ensure_user_settings(user_id)
        settings = update_row_safe(
            COLLECTIONS["user_settings"],
            _settings_row_id(settings),
            {
                "dashboard_layout_json": json.dumps(
                    normalized,
                    separators=(",", ":"),
                ),
                "updated_at": dependencies["format_datetime"](
                    datetime.now(timezone.utc)
                ),
            },
        )
    except AppwriteException:
        logger.exception("Failed to save dashboard layout")
        return jsonify({
            "error": "Unable to save dashboard layout."
        }), 500

    saved_layout = _coerce_layout(settings.get("dashboard_layout_json"))
    return jsonify({
        "status": "ok",
        "dashboard_layout": saved_layout,
        "tile_layout": saved_layout["tiles"],
        "tile_order": [tile["type"] for tile in saved_layout["tiles"]],
    })
