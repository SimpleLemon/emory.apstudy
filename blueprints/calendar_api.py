"""
blueprints/calendar_api.py

Per-user calendar data endpoints.
Fetches, caches, and serves Canvas iCal feed data.
Also provides a token-authenticated .ics subscription endpoint.
"""
import hashlib
import json
import logging
import secrets
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone

from flask import Blueprint, jsonify, request, Response, url_for
from flask_login import login_required, current_user
from werkzeug.routing import BuildError

from appwrite.exception import AppwriteException
from appwrite.id import ID
from appwrite.query import Query
from appwrite_client import COLLECTIONS
from appwrite_helpers import (
    create_row_safe,
    first_row,
    format_datetime,
    get_row_safe,
    parse_datetime,
    update_row_safe,
)
from services.calendar_urls import iter_valid_other_calendar_urls, load_other_calendar_urls
from blueprints.settings import (
    _normalize_calendar_url,
    _normalize_canvas_calendar_url,
    _settings_defaults,
    _validate_other_calendar_urls,
)
from services.discord_audit import emit_creation_event, format_actor
from services.calendar_store import (
    create_calendar_row,
    delete_calendar_row,
    first_calendar_row,
    get_calendar_row,
    list_calendar_rows_all,
    list_calendar_rows_safe,
    update_calendar_row,
)

calendar_bp = Blueprint("calendar", __name__)
logger = logging.getLogger(__name__)

CANVAS_SOURCE_ID = "canvas"
FEED_SOURCE_PREFIX = "feed:"
LOCAL_SOURCE_PREFIX = "local:"
DEFAULT_LOCAL_SOURCE_ID = f"{LOCAL_SOURCE_PREFIX}default"
DEFAULT_LOCAL_SOURCE_NAME = "Personal"
DEFAULT_CALENDAR_COLOR = "#6366f1"
SIMULATED_CALENDAR_NAME = "Simulated Courses"
CALENDAR_SHARE_CODE_LENGTH = 16
CALENDAR_SHARE_CODE_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
CALENDAR_SHARE_DATE_SCOPES = {"all", "fixed", "rolling"}
CALENDAR_SHARE_MIN_ROLLING_DAYS = 1
CALENDAR_SHARE_MAX_ROLLING_DAYS = 366
PREFERENCES_BATCH_LIMIT = 50
TIMED_EVENT_REMINDERS = {-1, 0, 5, 10, 15, 30, 60, 120, 1440, 2880}
ALL_DAY_EVENT_REMINDERS = {-1, -540, 900, 2340, 9540}


def _canonical_feed_url(feed_url):
    return _normalize_calendar_url(feed_url) or (feed_url or "").strip()


def _raw_feed_url_hash(feed_url):
    return hashlib.sha256((feed_url or "").encode("utf-8")).hexdigest()


def _feed_url_hash(feed_url):
    return _raw_feed_url_hash(_canonical_feed_url(feed_url))


def _feed_source_id(feed_url):
    return f"{FEED_SOURCE_PREFIX}{_feed_url_hash(feed_url)}"


def _legacy_feed_source_id(feed_url):
    return f"{FEED_SOURCE_PREFIX}{_raw_feed_url_hash(feed_url)}"


def _normalize_display_name(value):
    if not isinstance(value, str):
        return ""
    return " ".join(value.strip().split())[:120]


def _normalize_source_label(value):
    if not isinstance(value, str):
        return ""
    return " ".join(value.strip().split())[:120]


def _url_fallback_label(feed_url):
    return "Subscribed Calendar"


def _source_id_for_feed_url(feed_url, settings=None):
    canvas_url = (settings or {}).get("canvas_ical_url") or ""
    if canvas_url and _normalize_calendar_url(feed_url) == _normalize_calendar_url(canvas_url):
        return CANVAS_SOURCE_ID
    return _feed_source_id(feed_url)


def _event_ref_for_cache_event(doc):
    feed_hash = doc.get("feed_url_hash") or _feed_url_hash(doc.get("feed_url") or "")
    event_uid = doc.get("event_uid") or ""
    if not feed_hash or not event_uid:
        return None
    uid_hash = hashlib.sha256(str(event_uid).encode("utf-8")).hexdigest()
    return f"feed:{feed_hash}:{uid_hash}"


def _event_ref_for_user_event(doc):
    row_id = doc.get("$id") or doc.get("id")
    return f"user:{row_id}" if row_id else None


def _normalize_color(value):
    if value is None:
        return None
    value = str(value).strip()
    if not value:
        return None
    if len(value) == 7 and value.startswith("#"):
        hex_part = value[1:]
        if all(ch in "0123456789abcdefABCDEF" for ch in hex_part):
            return f"#{hex_part.lower()}"
    raise ValueError("Color must be a valid #RRGGBB value.")


def _default_reminder_minutes(is_all_day):
    return -1 if is_all_day else 10


def _normalize_reminder_minutes(value, is_all_day):
    if value is None or value == "":
        return _default_reminder_minutes(is_all_day)
    try:
        reminder_minutes = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Choose a valid alert time.") from exc
    allowed = ALL_DAY_EVENT_REMINDERS if is_all_day else TIMED_EVENT_REMINDERS
    if reminder_minutes not in allowed:
        raise ValueError("Choose a valid alert time.")
    return reminder_minutes


def _serialized_reminder_minutes(doc, is_all_day):
    value = doc.get("reminder_minutes")
    return _default_reminder_minutes(is_all_day) if value is None else int(value)


def _calendar_preference_updates(payload):
    updates = {}
    if "color_hex" in payload and payload.get("color_hex") is not None:
        updates["color_hex"] = _normalize_color(payload.get("color_hex"))
    if "visible" in payload and payload.get("visible") is not None:
        updates["visible"] = bool(payload.get("visible"))
    if "display_name" in payload:
        updates["display_name"] = _normalize_display_name(payload.get("display_name"))
    return updates


def _calendar_preference_unchanged(pref, updates):
    if not pref:
        return False
    for key, value in updates.items():
        current = pref.get(key)
        if key == "display_name":
            current = current or ""
        if key == "color_hex" and isinstance(current, str):
            current = current.lower()
        if key == "visible" and current is not None:
            current = bool(current)
        if current != value:
            return False
    return True


def _normalize_calendar_id(value):
    calendar_id = str(value or "").strip()
    return calendar_id[:255] if calendar_id else DEFAULT_LOCAL_SOURCE_ID


def _serialize_datetime(dt_value, is_all_day=False):
    """
    Serialize a datetime for the API response.

    All-day events are serialized as date-only strings ("2026-04-24")
    WITHOUT a trailing Z, so the browser parses them as local calendar
    dates with no UTC conversion.

    Timed events are serialized as full ISO-8601 with trailing Z
    ("2026-04-24T20:00:00Z"), so the browser correctly converts from
    UTC to the user's local timezone.
    """
    if dt_value is None:
        return None

    if is_all_day:
        return dt_value.strftime("%Y-%m-%d")

    if dt_value.tzinfo is None:
        dt_value = dt_value.replace(tzinfo=timezone.utc)
    else:
        dt_value = dt_value.astimezone(timezone.utc)
    return dt_value.isoformat().replace("+00:00", "Z")


def _span_metadata(start_dt, end_dt, is_all_day=False):
    """
    Compute multi-day flags for calendar rendering metadata.

    For all-day events, iCal DTEND is exclusive: an event on April 24
    has DTSTART=20260424, DTEND=20260425. The span is the day difference.

    For timed events, span counts distinct calendar dates touched
    (start and end dates inclusive).
    """
    if not start_dt or not end_dt:
        return False, 1

    if end_dt <= start_dt:
        return False, 1

    start_date = start_dt.date() if hasattr(start_dt, "date") else start_dt
    end_date = end_dt.date() if hasattr(end_dt, "date") else end_dt

    if is_all_day:
        span_days = max(1, (end_date - start_date).days)
    else:
        span_days = max(1, (end_date - start_date).days + 1)

    return span_days > 1, span_days


def _serialize_event(doc, settings=None):
    """Serialize a calendar_cache row for API response."""
    is_all_day = bool(doc.get("is_all_day", False))
    event_start = parse_datetime(doc.get("event_start"))
    event_end = parse_datetime(doc.get("event_end"))
    fetched_at = parse_datetime(doc.get("fetched_at"))
    is_multi_day, span_days = _span_metadata(event_start, event_end, is_all_day)
    feed_url = doc.get("feed_url") or ""
    calendar_id = _source_id_for_feed_url(feed_url, settings) if feed_url else None
    event_ref = _event_ref_for_cache_event(doc)

    return {
        "uid": doc.get("event_uid"),
        "event_ref": event_ref,
        "source_type": "feed",
        "editable": True,
        "title": doc.get("event_title"),
        "start": _serialize_datetime(event_start, is_all_day),
        "end": _serialize_datetime(event_end, is_all_day),
        "type": doc.get("event_type"),
        "course": doc.get("course_name"),
        "description": doc.get("raw_description"),
        "fetched_at": fetched_at.isoformat() if fetched_at else None,
        "is_multi_day": is_multi_day,
        "span_days": span_days,
        "is_all_day": is_all_day,
        "reminder_minutes": _default_reminder_minutes(is_all_day),
        "calendar_id": calendar_id,
        "original_calendar_id": calendar_id,
    }


def _serialize_user_event(doc):
    """Serialize a user_events row for API response."""
    start = parse_datetime(doc.get("start"))
    end = parse_datetime(doc.get("end"))
    created_at = parse_datetime(doc.get("created_at"))
    updated_at = parse_datetime(doc.get("updated_at"))
    is_all_day = bool(doc.get("is_all_day", False))
    calendar_id = doc.get("calendar_id") or DEFAULT_LOCAL_SOURCE_ID
    return {
        "id": doc.get("$id"),
        "event_ref": _event_ref_for_user_event(doc),
        "source_type": "user",
        "editable": True,
        "title": doc.get("title"),
        "description": doc.get("description"),
        "start": _serialize_datetime(start, is_all_day),
        "end": _serialize_datetime(end, is_all_day),
        "is_all_day": is_all_day,
        "reminder_minutes": _serialized_reminder_minutes(doc, is_all_day),
        "color": doc.get("color") or None,
        "calendar_id": calendar_id,
        "created_at": created_at.isoformat() if created_at else None,
        "updated_at": updated_at.isoformat() if updated_at else None,
    }


def _coerce_utc(dt_value):
    if dt_value is None:
        return None
    if dt_value.tzinfo is None:
        return dt_value.replace(tzinfo=timezone.utc)
    return dt_value.astimezone(timezone.utc)


def _parse_range_param(value):
    if not value:
        return None
    parsed = parse_datetime(value)
    return _coerce_utc(parsed) if parsed else None


def _event_overlaps_range(start_value, end_value, range_start, range_end):
    if not range_start or not range_end:
        return True
    start_dt = _coerce_utc(parse_datetime(start_value))
    end_dt = _coerce_utc(parse_datetime(end_value)) or start_dt
    if not start_dt or not end_dt:
        return False
    return start_dt < range_end and end_dt > range_start


def _resolve_last_fetched(user_id):
    last_fetched = None
    feed_table = COLLECTIONS.get("calendar_feeds")
    latest_feed = None
    if feed_table:
        try:
            latest_feed = first_calendar_row(
                feed_table,
                [
                    Query.equal("user_id", [user_id]),
                    Query.order_desc("last_fetched"),
                ],
            )
        except AppwriteException:
            latest_feed = None

    if latest_feed and latest_feed.get("last_fetched"):
        parsed = parse_datetime(latest_feed.get("last_fetched"))
        if parsed:
            return parsed.isoformat()

    try:
        latest_event = first_calendar_row(
            COLLECTIONS["calendar_cache"],
            [
                Query.equal("user_id", [user_id]),
                Query.order_desc("fetched_at"),
            ],
        )
    except AppwriteException:
        latest_event = None

    if latest_event and latest_event.get("fetched_at"):
        parsed = parse_datetime(latest_event.get("fetched_at"))
        if parsed:
            last_fetched = parsed.isoformat()
    return last_fetched


def _configured_feed_urls(settings):
    """Return all configured calendar feed URLs for a user."""
    if not settings:
        return []
    urls = []
    canvas_url = settings.get("canvas_ical_url")
    if canvas_url:
        urls.append(canvas_url.strip())
    urls.extend(load_other_calendar_urls(settings))
    return urls


def _load_calendar_feed_metadata(user_id):
    feed_table = COLLECTIONS.get("calendar_feeds")
    if not feed_table:
        return {}
    rows = list_calendar_rows_all(
        feed_table,
        [Query.equal("user_id", [str(user_id)])],
    )
    return {row.get("feed_url_hash"): row for row in rows if row.get("feed_url_hash")}


def _configured_feed_sources(settings, cache_events=None, preferences=None, feed_metadata=None):
    """Return editable feed source metadata for configured URLs."""
    if not settings:
        return []

    cache_events = cache_events or []
    preferences = preferences or []
    feed_metadata = feed_metadata or {}
    prefs_by_name = {
        pref.get("calendar_name"): pref
        for pref in preferences
        if pref.get("calendar_name")
    }
    labels_by_hash = {}
    for row in cache_events:
        feed_hash = row.get("feed_url_hash")
        label = row.get("course_name")
        if feed_hash and label:
            labels_by_hash.setdefault(feed_hash, Counter())[label] += 1

    sources = []
    canvas_url = (settings.get("canvas_ical_url") or "").strip()
    if canvas_url:
        sources.append({
            "id": CANVAS_SOURCE_ID,
            "kind": "canvas",
            "default_name": "Canvas",
            "url": canvas_url,
            "editable": True,
            "legacy_names": ["Canvas"],
        })

    for raw_url, url in iter_valid_other_calendar_urls(settings):
        feed_hash = _feed_url_hash(url)
        raw_feed_hash = _raw_feed_url_hash(url)
        label_counts = labels_by_hash.get(feed_hash)
        if not label_counts and raw_feed_hash != feed_hash:
            label_counts = labels_by_hash.get(raw_feed_hash)
        metadata = feed_metadata.get(feed_hash) or feed_metadata.get(raw_feed_hash) or {}
        metadata_name = _normalize_source_label(metadata.get("calendar_name"))
        default_name = metadata_name
        if label_counts:
            default_name = default_name or label_counts.most_common(1)[0][0]
        default_name = _normalize_source_label(default_name) or _url_fallback_label(url)
        legacy_source_id = _legacy_feed_source_id(raw_url)
        legacy_names = [default_name]
        if legacy_source_id != _feed_source_id(url):
            legacy_names.append(legacy_source_id)
        sources.append({
            "id": _feed_source_id(url),
            "kind": "external",
            "default_name": default_name,
            "url": url,
            "editable": True,
            "legacy_names": legacy_names,
        })

    for source in sources:
        source_pref = prefs_by_name.get(source["id"])
        legacy_pref = next(
            (prefs_by_name.get(name) for name in source.get("legacy_names", []) if prefs_by_name.get(name)),
            None,
        )
        display_name = (
            (source_pref or {}).get("display_name")
            or (legacy_pref or {}).get("display_name")
            or ""
        )
        source["display_name"] = display_name
        source["color_hex"] = (source_pref or {}).get("color_hex") or (legacy_pref or {}).get("color_hex") or None

    return sources


def _load_local_calendar_sources(user_id):
    table_id = COLLECTIONS.get("user_calendar_sources")
    if not table_id:
        return []
    return list_calendar_rows_all(
        table_id,
        [Query.equal("user_id", [str(user_id)])],
    )


def _configured_local_sources(local_sources=None, preferences=None, created_events=None):
    local_sources = local_sources or []
    preferences = preferences or []
    created_events = created_events or []
    prefs_by_name = {
        pref.get("calendar_name"): pref
        for pref in preferences
        if pref.get("calendar_name")
    }
    rows_by_source = {
        row.get("source_id"): row
        for row in local_sources
        if row.get("source_id")
    }
    if any(not event.get("calendar_id") for event in created_events):
        rows_by_source.setdefault(
            DEFAULT_LOCAL_SOURCE_ID,
            {
                "source_id": DEFAULT_LOCAL_SOURCE_ID,
                "default_name": DEFAULT_LOCAL_SOURCE_NAME,
                "kind": "local",
            },
        )

    sources = []
    for source_id, row in rows_by_source.items():
        default_name = _normalize_source_label(row.get("default_name")) or DEFAULT_LOCAL_SOURCE_NAME
        pref = prefs_by_name.get(source_id) or {}
        sources.append({
            "id": source_id,
            "kind": row.get("kind") or "local",
            "default_name": default_name,
            "display_name": pref.get("display_name") or "",
            "color_hex": pref.get("color_hex") or row.get("color_hex") or DEFAULT_CALENDAR_COLOR,
            "url": "",
            "editable": True,
            "source_id": source_id,
            "legacy_names": [],
        })
    return sorted(sources, key=lambda item: (item.get("display_name") or item.get("default_name") or "").lower())


def _configured_calendar_sources(settings, cache_events=None, preferences=None, feed_metadata=None, local_sources=None, created_events=None):
    return _configured_feed_sources(settings, cache_events, preferences, feed_metadata) + _configured_local_sources(
        local_sources,
        preferences,
        created_events,
    )


def _task_calendar_payload(user_id, preferences, range_start=None, range_end=None):
    try:
        from blueprints.tasks_api import task_calendar_events_for_user, task_calendar_source, user_has_tasks

        task_events = task_calendar_events_for_user(user_id, range_start, range_end)
        source = task_calendar_source(preferences) if task_events or user_has_tasks(user_id) else None
        return task_events, source
    except AppwriteException as exc:
        status_code = getattr(exc, "code", None) or getattr(exc, "response_code", None)
        if int(status_code or 0) == 404:
            logger.warning("Task calendar tables are not available yet; omitting task events.")
            return [], None
        raise
    except AttributeError as exc:
        if "list_rows" in str(exc):
            logger.warning("Task calendar storage is not configured; omitting task events.")
            return [], None
        raise


def _append_task_calendar_source(sources, source):
    if not source:
        return sources
    if any(item.get("id") == source.get("id") for item in sources):
        return sources
    return sources + [source]


def _ensure_user_settings(user_id):
    settings = first_row(
        COLLECTIONS["user_settings"],
        [Query.equal("user_id", [str(user_id)])],
    )
    if settings:
        return settings
    return create_row_safe(
        COLLECTIONS["user_settings"],
        row_id=str(user_id),
        data=_settings_defaults(str(user_id)),
    )


def _ensure_local_calendar_source(user_id, source_id=DEFAULT_LOCAL_SOURCE_ID, display_name=DEFAULT_LOCAL_SOURCE_NAME):
    source_id = _normalize_calendar_id(source_id)
    if not source_id.startswith(LOCAL_SOURCE_PREFIX):
        return None
    table_id = COLLECTIONS.get("user_calendar_sources")
    if not table_id:
        return None
    existing = first_calendar_row(
        table_id,
        [
            Query.equal("user_id", [str(user_id)]),
            Query.equal("source_id", [source_id]),
        ],
    )
    if existing:
        return existing
    now = format_datetime(datetime.utcnow())
    return create_calendar_row(
        table_id,
        row_id=ID.unique(),
        data={
            "user_id": str(user_id),
            "source_id": source_id,
            "kind": "local",
            "default_name": _normalize_source_label(display_name) or DEFAULT_LOCAL_SOURCE_NAME,
            "created_at": now,
            "updated_at": now,
        },
    )


def _load_event_overrides(user_id):
    table_id = COLLECTIONS.get("user_event_overrides")
    if not table_id:
        return []
    return list_calendar_rows_all(
        table_id,
        [Query.equal("user_id", [str(user_id)])],
    )


def _apply_event_override(event, override):
    if not override:
        return event
    if bool(override.get("hidden", False)):
        return None
    result = dict(event)
    is_all_day = bool(override.get("is_all_day")) if override.get("is_all_day") is not None else bool(result.get("is_all_day"))
    if override.get("title") is not None:
        result["title"] = override.get("title")
    if override.get("description") is not None:
        result["description"] = override.get("description")
    if override.get("calendar_id"):
        result["calendar_id"] = override.get("calendar_id")
    if override.get("color") is not None:
        result["color"] = override.get("color") or None
    if override.get("is_all_day") is not None:
        result["is_all_day"] = is_all_day
    override_reminder = override.get("reminder_minutes")
    if override_reminder is not None or override.get("is_all_day") is not None:
        result["reminder_minutes"] = (
            int(override_reminder)
            if override_reminder is not None
            else _default_reminder_minutes(is_all_day)
        )
    if override.get("start"):
        result["start"] = _serialize_datetime(parse_datetime(override.get("start")), is_all_day)
    if override.get("end"):
        result["end"] = _serialize_datetime(parse_datetime(override.get("end")), is_all_day)
    result["override_id"] = override.get("$id")
    result["has_override"] = True
    return result


def _api_event_overlaps_range(event, range_start, range_end):
    if not range_start or not range_end:
        return True
    return _event_overlaps_range(event.get("start"), event.get("end") or event.get("start"), range_start, range_end)


def _filter_configured_cache_events(cache_events, feed_urls):
    configured_hashes = set()
    for url in feed_urls:
        if not url:
            continue
        configured_hashes.add(_feed_url_hash(url))
        configured_hashes.add(_raw_feed_url_hash(url))
    return [
        event
        for event in cache_events
        if event.get("feed_url_hash") in configured_hashes
    ]


def _feed_needs_initial_fetch(feed_url, cache_events, feed_metadata):
    canonical_hash = _feed_url_hash(feed_url)
    raw_hash = _raw_feed_url_hash(feed_url)
    hashes = {canonical_hash, raw_hash}
    has_cache = any(event.get("feed_url_hash") in hashes for event in cache_events)
    metadata = feed_metadata.get(canonical_hash) or feed_metadata.get(raw_hash) or {}
    has_named_metadata = bool(_normalize_source_label(metadata.get("calendar_name")))
    return not has_named_metadata and not has_cache


def _initial_fetch_feed_urls(feed_urls, cache_events, feed_metadata):
    return [
        url
        for url in feed_urls
        if url and _feed_needs_initial_fetch(url, cache_events, feed_metadata)
    ]


def _refresh_initial_feed_cache(user_id, feed_urls, cache_events, feed_metadata):
    missing_urls = _initial_fetch_feed_urls(feed_urls, cache_events, feed_metadata)
    if not missing_urls:
        return False, None

    try:
        from services.feed_fetcher import fetch_and_cache_feeds

        fetch_and_cache_feeds(user_id, missing_urls)
        return True, None
    except Exception as exc:
        logger.exception(
            "Initial calendar feed fetch failed",
            extra={"user_id": user_id, "feed_count": len(missing_urls)},
        )
        return False, str(exc)


def _delete_cache_rows_for_feed(user_id, feed_url):
    feed_hashes = {_feed_url_hash(feed_url), _raw_feed_url_hash(feed_url)}
    seen_row_ids = set()
    for feed_hash in feed_hashes:
        rows = list_calendar_rows_all(
            COLLECTIONS["calendar_cache"],
            [
                Query.equal("user_id", [str(user_id)]),
                Query.equal("feed_url_hash", [feed_hash]),
            ],
        )
        for row in rows:
            row_id = row.get("$id") or row.get("id")
            if row_id and row_id not in seen_row_ids:
                seen_row_ids.add(row_id)
                delete_calendar_row(COLLECTIONS["calendar_cache"], row_id)

    feed_table = COLLECTIONS.get("calendar_feeds")
    if feed_table:
        seen_feed_row_ids = set()
        for feed_hash in feed_hashes:
            feed_rows = list_calendar_rows_all(
                feed_table,
                [
                    Query.equal("user_id", [str(user_id)]),
                    Query.equal("feed_url_hash", [feed_hash]),
                ],
            )
            for row in feed_rows:
                row_id = row.get("$id") or row.get("id")
                if row_id and row_id not in seen_feed_row_ids:
                    seen_feed_row_ids.add(row_id)
                    delete_calendar_row(feed_table, row_id)


def _update_local_calendar_source_payload(user_id, source_id, display_name):
    source = _ensure_local_calendar_source(user_id, source_id, display_name or DEFAULT_LOCAL_SOURCE_NAME)
    if source and display_name:
        source = update_calendar_row(
            COLLECTIONS["user_calendar_sources"],
            source.get("$id"),
            {
                "default_name": display_name,
                "updated_at": format_datetime(datetime.utcnow()),
            },
        )
    _upsert_calendar_preference(user_id, source_id, {"display_name": display_name})
    preferences = _load_calendar_preferences(user_id)
    local_sources = _load_local_calendar_sources(user_id)
    sources = _configured_local_sources(local_sources, preferences)
    return {
        "status": "ok",
        "source": next((item for item in sources if item.get("id") == source_id), None),
        "refresh_required": False,
    }


def _update_url_calendar_source_payload(user_id, source_id, display_name, next_url):
    settings = first_row(
        COLLECTIONS["user_settings"],
        [Query.equal("user_id", [user_id])],
    )
    update_info = _settings_payload_for_source_update(settings, source_id, next_url)
    old_source_pref = first_calendar_row(
        COLLECTIONS["user_calendar_preferences"],
        [
            Query.equal("user_id", [user_id]),
            Query.equal("calendar_name", [source_id]),
        ],
    )

    old_url = update_info["old_url"]
    new_url = update_info["new_url"]
    new_source_id = update_info["new_source_id"]
    refresh_required = _normalize_calendar_url(old_url) != _normalize_calendar_url(new_url)
    settings_updates = {
        **update_info["settings_updates"],
        "updated_at": format_datetime(datetime.utcnow()),
    }

    settings = update_row_safe(
        COLLECTIONS["user_settings"],
        settings.get("$id"),
        settings_updates,
    )
    pref_updates = {"display_name": display_name}
    if old_source_pref:
        if old_source_pref.get("color_hex"):
            pref_updates["color_hex"] = old_source_pref.get("color_hex")
        if old_source_pref.get("visible") is not None:
            pref_updates["visible"] = bool(old_source_pref.get("visible"))
    _upsert_calendar_preference(user_id, new_source_id, pref_updates)
    if refresh_required and old_url:
        _delete_cache_rows_for_feed(user_id, old_url)

    cache_events = list_calendar_rows_all(
        COLLECTIONS["calendar_cache"],
        [
            Query.equal("user_id", [user_id]),
            Query.order_asc("event_start"),
        ],
    )
    preferences = _load_calendar_preferences(user_id)
    feed_metadata = _load_calendar_feed_metadata(user_id)
    feed_urls = _configured_feed_urls(settings)
    cache_events = _filter_configured_cache_events(cache_events, feed_urls)
    sources = _configured_feed_sources(settings, cache_events, preferences, feed_metadata)
    return {
        "status": "ok",
        "source": next((item for item in sources if item.get("id") == new_source_id), None),
        "refresh_required": refresh_required,
    }


def _load_calendar_preferences(user_id):
    return list_calendar_rows_all(
        COLLECTIONS["user_calendar_preferences"],
        [Query.equal("user_id", [str(user_id)])],
    )


def _upsert_calendar_preference(user_id, calendar_name, updates):
    pref = first_calendar_row(
        COLLECTIONS["user_calendar_preferences"],
        [
            Query.equal("user_id", [str(user_id)]),
            Query.equal("calendar_name", [calendar_name]),
        ],
    )
    now = format_datetime(datetime.utcnow())
    payload = {"updated_at": now, **updates}
    if not pref:
        pref = create_calendar_row(
            COLLECTIONS["user_calendar_preferences"],
            row_id=ID.unique(),
            data={
                "user_id": str(user_id),
                "calendar_name": calendar_name,
                "color_hex": updates.get("color_hex") or "#6366f1",
                "visible": bool(True if updates.get("visible") is None else updates.get("visible")),
                "created_at": now,
                **payload,
            },
        )
    else:
        pref = update_calendar_row(
            COLLECTIONS["user_calendar_preferences"],
            pref.get("$id"),
            payload,
        )
    return pref


def _upsert_event_override(user_id, event_ref, updates):
    table_id = COLLECTIONS["user_event_overrides"]
    existing = first_calendar_row(
        table_id,
        [
            Query.equal("user_id", [str(user_id)]),
            Query.equal("event_ref", [event_ref]),
        ],
    )
    now = format_datetime(datetime.utcnow())
    payload = {"updated_at": now, **updates}
    if not existing:
        return create_calendar_row(
            table_id,
            row_id=ID.unique(),
            data={
                "user_id": str(user_id),
                "event_ref": event_ref,
                "hidden": False,
                "created_at": now,
                **payload,
            },
        )
    return update_calendar_row(
        table_id,
        existing.get("$id"),
        payload,
    )


def _settings_payload_for_source_update(settings, source_id, next_url):
    if not settings:
        raise ValueError("No calendar settings found.")

    current_canvas_url = (settings.get("canvas_ical_url") or "").strip()
    other_urls = load_other_calendar_urls(settings)

    if source_id == CANVAS_SOURCE_ID:
        normalized_canvas = _normalize_canvas_calendar_url(next_url)
        if not normalized_canvas:
            raise ValueError("Canvas calendar must use https://canvas.<school>.edu/feeds/calendar...")
        validated_other_urls = _validate_other_calendar_urls(other_urls, normalized_canvas)
        return {
            "old_url": current_canvas_url,
            "new_url": normalized_canvas,
            "new_source_id": CANVAS_SOURCE_ID,
            "settings_updates": {
                "canvas_ical_url": normalized_canvas,
                "other_ical_urls_json": json.dumps(validated_other_urls),
            },
        }

    if not source_id.startswith(FEED_SOURCE_PREFIX):
        raise ValueError("Only feed calendars can be edited.")

    match_index = None
    for index, url in enumerate(other_urls):
        if _feed_source_id(url) == source_id or _legacy_feed_source_id(url) == source_id:
            match_index = index
            break
    if match_index is None:
        raise ValueError("Calendar source was not found.")
    if not (next_url or "").strip():
        raise ValueError("Calendar URL is required.")

    candidate_urls = list(other_urls)
    candidate_urls[match_index] = (next_url or "").strip()
    validated_other_urls = _validate_other_calendar_urls(candidate_urls, current_canvas_url)
    new_url = validated_other_urls[match_index]
    return {
        "old_url": other_urls[match_index],
        "new_url": new_url,
        "new_source_id": _feed_source_id(new_url),
        "settings_updates": {
            "other_ical_urls_json": json.dumps(validated_other_urls),
        },
    }


def _row_id(row):
    return row.get("$id") or row.get("id") if row else None


def _calendar_shares_collection():
    return COLLECTIONS.get("calendar_shares", "calendar_shares")


def _share_url(share_code):
    if not share_code:
        return None
    try:
        return url_for("dashboard.public_calendar_share", share_code=share_code, _external=True)
    except (BuildError, RuntimeError):
        return f"/calendar/share/{share_code}"


def _generate_calendar_share_code():
    table_id = _calendar_shares_collection()
    while True:
        code = "".join(secrets.choice(CALENDAR_SHARE_CODE_CHARS) for _ in range(CALENDAR_SHARE_CODE_LENGTH))
        existing = first_calendar_row(table_id, [Query.equal("share_code", [code])])
        if not existing:
            return code


def _parse_json_list(value):
    if not value:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item or "").strip()]
    if not isinstance(value, str):
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if str(item or "").strip()]


def _normalize_share_calendar_ids(value):
    ids = []
    seen = set()
    for item in value or []:
        calendar_id = str(item or "").strip()
        if not calendar_id or calendar_id == SIMULATED_CALENDAR_NAME:
            continue
        calendar_id = calendar_id[:255]
        if calendar_id in seen:
            continue
        seen.add(calendar_id)
        ids.append(calendar_id)
    return ids


def _parse_date_start(value):
    parsed = parse_datetime(value)
    if not parsed:
        return None
    parsed = _coerce_utc(parsed)
    return datetime(parsed.year, parsed.month, parsed.day, tzinfo=timezone.utc)


def _fixed_end_display_date(fixed_end):
    parsed = _coerce_utc(parse_datetime(fixed_end))
    if not parsed:
        return None
    display_dt = parsed - timedelta(days=1)
    return display_dt.date().isoformat()


def _normalize_calendar_share_payload(data, existing=None):
    data = data or {}
    existing = existing or {}
    include_all_raw = data.get("includeAllCalendars", data.get("include_all_calendars"))
    include_all = bool(include_all_raw) if include_all_raw is not None else bool(existing.get("include_all_calendars", True))

    calendar_ids_raw = data.get("calendarIds", data.get("calendar_ids"))
    if calendar_ids_raw is None:
        calendar_ids = _parse_json_list(existing.get("calendar_ids_json"))
    else:
        calendar_ids = _normalize_share_calendar_ids(calendar_ids_raw)
    if not include_all and not calendar_ids:
        raise ValueError("Choose at least one calendar to share.")

    date_scope = str(data.get("dateScope", data.get("date_scope", existing.get("date_scope") or "all"))).strip().lower()
    if date_scope not in CALENDAR_SHARE_DATE_SCOPES:
        raise ValueError("Invalid date scope.")

    fixed_start = None
    fixed_end = None
    rolling_days = None
    if date_scope == "fixed":
        fixed_start = _parse_date_start(data.get("fixedStart", data.get("fixed_start", existing.get("fixed_start"))))
        fixed_end_start = _parse_date_start(data.get("fixedEnd", data.get("fixed_end", _fixed_end_display_date(existing.get("fixed_end")))))
        if not fixed_start or not fixed_end_start:
            raise ValueError("Fixed date range requires a start and end date.")
        fixed_end = fixed_end_start + timedelta(days=1)
        if fixed_end <= fixed_start:
            raise ValueError("Fixed date range end must be after the start.")
    elif date_scope == "rolling":
        raw_days = data.get("rollingDays", data.get("rolling_days", existing.get("rolling_days")))
        try:
            rolling_days = int(raw_days)
        except (TypeError, ValueError):
            raise ValueError("Rolling window must be a number of days.")
        if rolling_days < CALENDAR_SHARE_MIN_ROLLING_DAYS or rolling_days > CALENDAR_SHARE_MAX_ROLLING_DAYS:
            raise ValueError(
                f"Rolling window must be between {CALENDAR_SHARE_MIN_ROLLING_DAYS} and {CALENDAR_SHARE_MAX_ROLLING_DAYS} days."
            )

    return {
        "include_all_calendars": include_all,
        "calendar_ids_json": json.dumps([] if include_all else calendar_ids),
        "date_scope": date_scope,
        "fixed_start": format_datetime(fixed_start) if fixed_start else None,
        "fixed_end": format_datetime(fixed_end) if fixed_end else None,
        "rolling_days": rolling_days,
    }


def _calendar_share_scope_label(share):
    scope = share.get("date_scope") or "all"
    if scope == "fixed":
        start = _coerce_utc(parse_datetime(share.get("fixed_start")))
        end_label = _fixed_end_display_date(share.get("fixed_end"))
        if start and end_label:
            return f"{start.date().isoformat()} to {end_label}"
        return "Fixed date range"
    if scope == "rolling":
        days = int(share.get("rolling_days") or 0)
        return f"Today through the next {days} day{'s' if days != 1 else ''}"
    return "All shared dates"


def _calendar_share_payload(share):
    fixed_start = _coerce_utc(parse_datetime(share.get("fixed_start")))
    return {
        "id": _row_id(share),
        "shareCode": share.get("share_code"),
        "shareUrl": _share_url(share.get("share_code")),
        "isActive": bool(share.get("is_active", True)),
        "includeAllCalendars": bool(share.get("include_all_calendars", True)),
        "calendarIds": _parse_json_list(share.get("calendar_ids_json")),
        "dateScope": share.get("date_scope") or "all",
        "fixedStart": fixed_start.date().isoformat() if fixed_start else None,
        "fixedEnd": _fixed_end_display_date(share.get("fixed_end")),
        "rollingDays": share.get("rolling_days"),
        "scopeLabel": _calendar_share_scope_label(share),
        "createdAt": share.get("created_at"),
        "updatedAt": share.get("updated_at"),
    }


def _calendar_share_scope_range(share, now=None):
    scope = share.get("date_scope") or "all"
    if scope == "fixed":
        return (
            _coerce_utc(parse_datetime(share.get("fixed_start"))),
            _coerce_utc(parse_datetime(share.get("fixed_end"))),
        )
    if scope == "rolling":
        now = _coerce_utc(now or datetime.now(timezone.utc))
        start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
        days = int(share.get("rolling_days") or 0)
        return start, start + timedelta(days=days)
    return None, None


def _intersect_ranges(*ranges):
    starts = [start for start, _end in ranges if start]
    ends = [end for _start, end in ranges if end]
    start = max(starts) if starts else None
    end = min(ends) if ends else None
    if start and end and start >= end:
        return start, start
    return start, end


def _range_queries(user_id, start_key, end_key, order_key, range_start=None, range_end=None):
    queries = [Query.equal("user_id", [str(user_id)])]
    if range_end:
        queries.append(Query.less_than(start_key, format_datetime(range_end)))
    if range_start:
        queries.append(Query.greater_than(end_key, format_datetime(range_start)))
    queries.append(Query.order_asc(order_key))
    return queries


def _load_serialized_calendar_events(user_id, settings, range_start=None, range_end=None):
    feed_urls = _configured_feed_urls(settings)
    cache_events = list_calendar_rows_all(
        COLLECTIONS["calendar_cache"],
        _range_queries(user_id, "event_start", "event_end", "event_start", range_start, range_end),
    )
    created_events = list_calendar_rows_all(
        COLLECTIONS["user_events"],
        _range_queries(user_id, "start", "end", "start", range_start, range_end),
    )
    event_overrides = _load_event_overrides(user_id)
    overrides_by_ref = {
        override.get("event_ref"): override
        for override in event_overrides
        if override.get("event_ref")
    }

    cache_events = _filter_configured_cache_events(cache_events, feed_urls)
    serialized_cache_events = []
    for cache_event in cache_events:
        serialized_event = _serialize_event(cache_event, settings)
        serialized_event = _apply_event_override(
            serialized_event,
            overrides_by_ref.get(serialized_event.get("event_ref")),
        )
        if serialized_event:
            serialized_cache_events.append(serialized_event)

    serialized_created_events = [_serialize_user_event(e) for e in created_events]
    events = serialized_cache_events + serialized_created_events
    if range_start and range_end:
        events = [
            event
            for event in events
            if _api_event_overlaps_range(event, range_start, range_end)
        ]
    return events, cache_events, created_events


def _sanitize_public_event(event):
    event_ref = event.get("event_ref") or event.get("id") or event.get("uid")
    return {
        "uid": event_ref,
        "event_ref": event_ref,
        "source_type": event.get("source_type"),
        "editable": False,
        "title": event.get("title"),
        "start": event.get("start"),
        "end": event.get("end"),
        "type": event.get("type"),
        "course": event.get("course"),
        "description": event.get("description"),
        "is_multi_day": event.get("is_multi_day"),
        "span_days": event.get("span_days"),
        "is_all_day": event.get("is_all_day"),
        "calendar_id": event.get("calendar_id"),
        "color": event.get("color"),
        "task_id": event.get("task_id"),
        "occurrence_key": event.get("occurrence_key"),
        "priority": event.get("priority"),
        "completed": event.get("completed"),
    }


def _sanitize_public_sources(sources, share, preferences=None):
    allowed = set(_parse_json_list(share.get("calendar_ids_json")))
    include_all = bool(share.get("include_all_calendars", True))
    prefs_by_name = {
        pref.get("calendar_name"): pref
        for pref in (preferences or [])
        if pref.get("calendar_name")
    }
    public_sources = []
    for source in sources:
        source_id = source.get("id")
        if not include_all and source_id not in allowed:
            continue
        source_pref = prefs_by_name.get(source_id) or next(
            (prefs_by_name.get(name) for name in source.get("legacy_names", []) if prefs_by_name.get(name)),
            {},
        )
        public_sources.append({
            "id": source_id,
            "kind": source.get("kind") or "external",
            "default_name": source.get("default_name") or source.get("display_name") or source_id,
            "display_name": source.get("display_name") or "",
            "color_hex": source_pref.get("color_hex") or source.get("color_hex") or DEFAULT_CALENDAR_COLOR,
            "editable": False,
            "legacy_names": source.get("legacy_names") or [],
        })
    return public_sources


def _resolve_calendar_share_by_code(share_code, active_only=True):
    queries = [Query.equal("share_code", [share_code])]
    if active_only:
        queries.append(Query.equal("is_active", [True]))
    return first_calendar_row(_calendar_shares_collection(), queries)


def _public_calendar_share_context(share):
    owner = get_row_safe(COLLECTIONS["users"], share.get("user_id"), allow_missing=True)
    owner_name = (owner or {}).get("name") or "APStudy User"
    return {
        "share_code": share.get("share_code"),
        "owner_name": owner_name,
        "scope_label": _calendar_share_scope_label(share),
    }


def _public_calendar_events_payload(share, requested_start=None, requested_end=None):
    user_id = str(share.get("user_id"))
    settings = first_row(
        COLLECTIONS["user_settings"],
        [Query.equal("user_id", [user_id])],
    )
    share_start, share_end = _calendar_share_scope_range(share)
    range_start, range_end = _intersect_ranges((requested_start, requested_end), (share_start, share_end))
    if range_start and range_end and range_start >= range_end:
        return {
            "count": 0,
            "events": [],
            "feed_configured": False,
            "calendar_sources": [],
            "share": _calendar_share_payload(share),
        }

    events, cache_events, created_events = _load_serialized_calendar_events(user_id, settings, range_start, range_end)
    preferences = _load_calendar_preferences(user_id)
    task_events, task_source = _task_calendar_payload(user_id, preferences, range_start, range_end)
    events = events + task_events
    include_all = bool(share.get("include_all_calendars", True))
    allowed_calendars = set(_parse_json_list(share.get("calendar_ids_json")))
    if not include_all:
        events = [
            event
            for event in events
            if (event.get("calendar_id") or event.get("course") or "Other") in allowed_calendars
        ]
    feed_metadata = _load_calendar_feed_metadata(user_id)
    local_sources = _load_local_calendar_sources(user_id)
    calendar_sources = _append_task_calendar_source(
        _configured_calendar_sources(
            settings,
            cache_events,
            preferences,
            feed_metadata,
            local_sources,
            created_events,
        ),
        task_source,
    )

    public_events = [_sanitize_public_event(event) for event in events]
    return {
        "count": len(public_events),
        "events": public_events,
        "feed_configured": bool(_configured_feed_urls(settings)),
        "calendar_sources": _sanitize_public_sources(calendar_sources, share, preferences),
        "share": _calendar_share_payload(share),
    }


@calendar_bp.route("/events")
@login_required
def get_events():
    """
    GET /api/calendar/events
    Returns cached calendar events for the authenticated user.
    """
    user_id = str(current_user.id)
    range_start = _parse_range_param(request.args.get("start"))
    range_end = _parse_range_param(request.args.get("end"))
    if bool(request.args.get("start")) ^ bool(request.args.get("end")):
        return jsonify({"error": "start and end are required together"}), 400
    if (request.args.get("start") and not range_start) or (
        request.args.get("end") and not range_end
    ):
        return jsonify({"error": "start and end must be valid ISO-8601"}), 400

    try:
        settings = first_row(
            COLLECTIONS["user_settings"],
            [Query.equal("user_id", [user_id])],
        )
        feed_urls = _configured_feed_urls(settings)
        cache_events = list_calendar_rows_all(
            COLLECTIONS["calendar_cache"],
            [
                Query.equal("user_id", [user_id]),
                Query.order_asc("event_start"),
            ],
        )
        created_events = list_calendar_rows_all(
            COLLECTIONS["user_events"],
            [
                Query.equal("user_id", [user_id]),
                Query.order_asc("start"),
            ],
        )
        preferences = _load_calendar_preferences(user_id)
        feed_metadata = _load_calendar_feed_metadata(user_id)
        local_sources = _load_local_calendar_sources(user_id)
        event_overrides = _load_event_overrides(user_id)
    except AppwriteException:
        logger.exception("Failed to load calendar events")
        return jsonify({"error": "Unable to load calendar events."}), 500

    refresh_error = None
    refreshed = False
    if feed_urls:
        refreshed, refresh_error = _refresh_initial_feed_cache(user_id, feed_urls, cache_events, feed_metadata)
    if refreshed:
        try:
            cache_events = list_calendar_rows_all(
                COLLECTIONS["calendar_cache"],
                [
                    Query.equal("user_id", [user_id]),
                    Query.order_asc("event_start"),
                ],
            )
            feed_metadata = _load_calendar_feed_metadata(user_id)
        except AppwriteException:
            logger.exception("Failed to reload calendar events after initial feed fetch")
            return jsonify({"error": "Unable to load calendar events."}), 500

    cache_events = _filter_configured_cache_events(cache_events, feed_urls)
    try:
        task_events, task_source = _task_calendar_payload(user_id, preferences, range_start, range_end)
    except AppwriteException:
        logger.exception("Failed to load task calendar events")
        return jsonify({"error": "Unable to load calendar events."}), 500

    calendar_sources = _append_task_calendar_source(
        _configured_calendar_sources(
            settings,
            cache_events,
            preferences,
            feed_metadata,
            local_sources,
            created_events,
        ),
        task_source,
    )
    overrides_by_ref = {
        override.get("event_ref"): override
        for override in event_overrides
        if override.get("event_ref")
    }

    serialized_cache_events = []
    for cache_event in cache_events:
        serialized_event = _serialize_event(cache_event, settings)
        serialized_event = _apply_event_override(
            serialized_event,
            overrides_by_ref.get(serialized_event.get("event_ref")),
        )
        if serialized_event:
            serialized_cache_events.append(serialized_event)
    serialized_created_events = [_serialize_user_event(e) for e in created_events]

    if range_start and range_end:
        serialized_cache_events = [
            e
            for e in serialized_cache_events
            if _api_event_overlaps_range(e, range_start, range_end)
        ]
        serialized_created_events = [
            e
            for e in serialized_created_events
            if _api_event_overlaps_range(e, range_start, range_end)
        ]

    serialized = serialized_cache_events + serialized_created_events + task_events

    return jsonify({
        "user_id": current_user.id,
        "count": len(serialized),
        "events": serialized,
        "feed_configured": bool(feed_urls),
        "calendar_sources": calendar_sources,
        "refresh_interval_minutes": settings.get("feed_refresh_minutes") if settings else None,
        "last_fetched": _resolve_last_fetched(user_id),
        "refresh_error": refresh_error,
    })


@calendar_bp.route("/shares", methods=["GET"])
@login_required
def list_calendar_shares():
    user_id = str(current_user.id)
    try:
        shares = list_calendar_rows_all(
            _calendar_shares_collection(),
            [
                Query.equal("user_id", [user_id]),
                Query.order_desc("created_at"),
            ],
        )
    except AppwriteException:
        logger.exception("Failed to load calendar shares")
        return jsonify({"error": "Unable to load calendar shares."}), 500

    return jsonify({"shares": [_calendar_share_payload(share) for share in shares]})


@calendar_bp.route("/shares", methods=["POST"])
@login_required
def create_calendar_share():
    user_id = str(current_user.id)
    try:
        config = _normalize_calendar_share_payload(request.get_json(silent=True) or {})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    now = format_datetime(datetime.utcnow())
    try:
        share = create_calendar_row(
            _calendar_shares_collection(),
            row_id=ID.unique(),
            data={
                "user_id": user_id,
                "share_code": _generate_calendar_share_code(),
                "is_active": True,
                "created_at": now,
                "updated_at": now,
                **config,
            },
        )
    except AppwriteException:
        logger.exception("Failed to create calendar share")
        return jsonify({"error": "Unable to create calendar share."}), 500

    emit_creation_event(
        "Calendar Share Created",
        actor=format_actor(current_user),
        target=share.get("$id") or share.get("id"),
        metadata={
            "page_context": "calendar/shares",
            "resource_type": "calendar_share",
            "resource_id": share.get("$id") or share.get("id"),
            "date_scope": config.get("date_scope"),
            "include_all_calendars": config.get("include_all_calendars"),
        },
        color="green",
    )
    return jsonify({"share": _calendar_share_payload(share)}), 201


def _owned_calendar_share_or_none(share_id, user_id):
    share = get_calendar_row(_calendar_shares_collection(), share_id, allow_missing=True)
    if not share or share.get("user_id") != str(user_id):
        return None
    return share


@calendar_bp.route("/shares/<share_id>", methods=["PATCH"])
@login_required
def update_calendar_share(share_id):
    user_id = str(current_user.id)
    try:
        share = _owned_calendar_share_or_none(share_id, user_id)
    except AppwriteException:
        logger.exception("Failed to load calendar share")
        return jsonify({"error": "Unable to load calendar share."}), 500
    if not share:
        return jsonify({"error": "Calendar share not found."}), 404

    payload = request.get_json(silent=True) or {}
    try:
        updates = _normalize_calendar_share_payload(payload, existing=share)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if "isActive" in payload or "is_active" in payload:
        updates["is_active"] = bool(payload.get("isActive", payload.get("is_active")))
    updates["updated_at"] = format_datetime(datetime.utcnow())

    try:
        updated = update_calendar_row(_calendar_shares_collection(), _row_id(share), updates)
    except AppwriteException:
        logger.exception("Failed to update calendar share")
        return jsonify({"error": "Unable to update calendar share."}), 500

    return jsonify({"share": _calendar_share_payload(updated)})


@calendar_bp.route("/shares/<share_id>/regenerate", methods=["POST"])
@login_required
def regenerate_calendar_share(share_id):
    user_id = str(current_user.id)
    try:
        share = _owned_calendar_share_or_none(share_id, user_id)
    except AppwriteException:
        logger.exception("Failed to load calendar share")
        return jsonify({"error": "Unable to load calendar share."}), 500
    if not share:
        return jsonify({"error": "Calendar share not found."}), 404

    try:
        updated = update_calendar_row(
            _calendar_shares_collection(),
            _row_id(share),
            {
                "share_code": _generate_calendar_share_code(),
                "is_active": True,
                "updated_at": format_datetime(datetime.utcnow()),
            },
        )
    except AppwriteException:
        logger.exception("Failed to regenerate calendar share")
        return jsonify({"error": "Unable to regenerate calendar share."}), 500

    return jsonify({"share": _calendar_share_payload(updated)})


@calendar_bp.route("/shares/<share_id>", methods=["DELETE"])
@login_required
def revoke_calendar_share(share_id):
    user_id = str(current_user.id)
    try:
        share = _owned_calendar_share_or_none(share_id, user_id)
    except AppwriteException:
        logger.exception("Failed to load calendar share")
        return jsonify({"error": "Unable to load calendar share."}), 500
    if not share:
        return jsonify({"error": "Calendar share not found."}), 404

    try:
        updated = update_calendar_row(
            _calendar_shares_collection(),
            _row_id(share),
            {
                "is_active": False,
                "updated_at": format_datetime(datetime.utcnow()),
            },
        )
    except AppwriteException:
        logger.exception("Failed to revoke calendar share")
        return jsonify({"error": "Unable to revoke calendar share."}), 500

    return jsonify({"share": _calendar_share_payload(updated)})


@calendar_bp.route("/share/<share_code>/events")
def get_public_calendar_share_events(share_code):
    range_start = _parse_range_param(request.args.get("start"))
    range_end = _parse_range_param(request.args.get("end"))
    if bool(request.args.get("start")) ^ bool(request.args.get("end")):
        return jsonify({"error": "start and end are required together"}), 400
    if (request.args.get("start") and not range_start) or (
        request.args.get("end") and not range_end
    ):
        return jsonify({"error": "start and end must be valid ISO-8601"}), 400

    try:
        share = _resolve_calendar_share_by_code(share_code, active_only=True)
    except AppwriteException:
        logger.exception("Failed to resolve public calendar share")
        return jsonify({"error": "Unable to load shared calendar."}), 500
    if not share:
        return jsonify({"error": "Shared calendar not found."}), 404

    try:
        payload = _public_calendar_events_payload(share, range_start, range_end)
    except AppwriteException:
        logger.exception("Failed to load public calendar events")
        return jsonify({"error": "Unable to load shared calendar."}), 500

    return jsonify(payload)


def _parse_iso_like(s):
    """Parse ISO-ish datetime or date strings into a naive UTC datetime for storage.

    Accepts date-only strings (YYYY-MM-DD) and full ISO strings that may end with Z.
    Returns a naive datetime in UTC for timed events, and local-midnight datetime for all-day.
    """
    if not s:
        return None

    s = str(s)
    # date-only -> treat as local midnight (all-day semantics)
    import re
    from datetime import datetime, timezone

    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        parts = s.split("-")
        return datetime(int(parts[0]), int(parts[1]), int(parts[2]), 0, 0, 0)

    # replace trailing Z with +00:00 for fromisoformat
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"

    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


@calendar_bp.route("/events", methods=["POST"])
@login_required
def create_event():
    """POST /api/calendar/events - create a user event"""
    data = request.get_json() or {}
    title = (data.get("title") or "").strip()
    description = data.get("description")
    start_raw = data.get("start_date") or data.get("start")
    end_raw = data.get("end_date") or data.get("end")
    all_day = bool(data.get("all_day", False))
    calendar_id = _normalize_calendar_id(data.get("calendar_id"))
    try:
        color = _normalize_color(data.get("color"))
        reminder_minutes = _normalize_reminder_minutes(data.get("reminder_minutes"), all_day)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    if not title:
        return jsonify({"error": "title is required"}), 400

    start_dt = _parse_iso_like(start_raw)
    end_dt = _parse_iso_like(end_raw)

    if not start_dt or not end_dt:
        return jsonify({"error": "start_date and end_date must be valid ISO datetimes"}), 400

    if end_dt <= start_dt:
        return jsonify({"error": "end_date must be after start_date"}), 400

    try:
        _ensure_local_calendar_source(user_id=current_user.id, source_id=calendar_id)
        ev = create_calendar_row(
            COLLECTIONS["user_events"],
            row_id=ID.unique(),
            data={
                "user_id": str(current_user.id),
                "title": title,
                "description": description,
                "start": format_datetime(start_dt),
                "end": format_datetime(end_dt),
                "is_all_day": all_day,
                "color": color,
                "calendar_id": calendar_id,
                "reminder_minutes": reminder_minutes,
                "created_at": format_datetime(datetime.utcnow()),
            },
        )
    except AppwriteException:
        logger.exception("Failed to create user event")
        return jsonify({"error": "Unable to create event."}), 500

    emit_creation_event(
        "Calendar Event Created",
        actor=format_actor(current_user),
        target=title,
        metadata={
            "page_context": "calendar/events",
            "resource_type": "user_event",
            "resource_id": ev.get("$id") or ev.get("id"),
            "calendar_id": calendar_id,
            "is_all_day": all_day,
            "start": format_datetime(start_dt),
            "end": format_datetime(end_dt),
        },
        color="green",
    )
    return jsonify({"success": True, "event": _serialize_user_event(ev)})


@calendar_bp.route("/events/<event_id>", methods=["GET"])
@login_required
def get_single_event(event_id):
    try:
        ev = get_calendar_row(COLLECTIONS["user_events"], event_id)
    except AppwriteException as exc:
        if exc.code == 404:
            return jsonify({"error": "not found"}), 404
        logger.exception("Failed to load user event")
        return jsonify({"error": "Unable to load event."}), 500

    if ev.get("user_id") != str(current_user.id):
        return jsonify({"error": "not found"}), 404
    return jsonify({"event": _serialize_user_event(ev)})


@calendar_bp.route("/events/<event_id>", methods=["PUT"])
@login_required
def update_event(event_id):
    try:
        ev = get_calendar_row(COLLECTIONS["user_events"], event_id)
    except AppwriteException as exc:
        if exc.code == 404:
            return jsonify({"error": "not found"}), 404
        logger.exception("Failed to load user event")
        return jsonify({"error": "Unable to load event."}), 500

    if ev.get("user_id") != str(current_user.id):
        return jsonify({"error": "not found"}), 404

    data = request.get_json() or {}
    title = data.get("title")
    description = data.get("description")
    start_raw = data.get("start_date") or data.get("start")
    end_raw = data.get("end_date") or data.get("end")
    all_day = data.get("all_day")
    calendar_id = data.get("calendar_id")

    updates = {"updated_at": format_datetime(datetime.utcnow())}
    if title is not None:
        updates["title"] = title
    if description is not None:
        updates["description"] = description
    if start_raw is not None:
        parsed = _parse_iso_like(start_raw)
        if parsed:
            updates["start"] = format_datetime(parsed)
    if end_raw is not None:
        parsed = _parse_iso_like(end_raw)
        if parsed:
            updates["end"] = format_datetime(parsed)
    if all_day is not None:
        updates["is_all_day"] = bool(all_day)
    if "reminder_minutes" in data or all_day is not None:
        reminder_all_day = bool(all_day) if all_day is not None else bool(ev.get("is_all_day"))
        try:
            updates["reminder_minutes"] = _normalize_reminder_minutes(data.get("reminder_minutes"), reminder_all_day)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
    if "color" in data:
        try:
            updates["color"] = _normalize_color(data.get("color"))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
    if calendar_id is not None:
        normalized_calendar_id = _normalize_calendar_id(calendar_id)
        _ensure_local_calendar_source(user_id=current_user.id, source_id=normalized_calendar_id)
        updates["calendar_id"] = normalized_calendar_id

    try:
        ev = update_calendar_row(
            COLLECTIONS["user_events"],
            event_id,
            updates,
        )
    except AppwriteException:
        logger.exception("Failed to update user event")
        return jsonify({"error": "Unable to update event."}), 500

    return jsonify({"success": True, "event": _serialize_user_event(ev)})


@calendar_bp.route("/events/<event_id>", methods=["DELETE"])
@login_required
def delete_event(event_id):
    try:
        ev = get_calendar_row(COLLECTIONS["user_events"], event_id)
    except AppwriteException as exc:
        if exc.code == 404:
            return jsonify({"error": "not found"}), 404
        logger.exception("Failed to load user event")
        return jsonify({"error": "Unable to delete event."}), 500

    if ev.get("user_id") != str(current_user.id):
        return jsonify({"error": "not found"}), 404

    try:
        delete_calendar_row(COLLECTIONS["user_events"], event_id)
    except AppwriteException:
        logger.exception("Failed to delete user event")
        return jsonify({"error": "Unable to delete event."}), 500

    return jsonify({"success": True})


@calendar_bp.route("/event-overrides", methods=["POST"])
@login_required
def upsert_event_override():
    """Create or update the authenticated user's override for an imported event."""
    data = request.get_json(silent=True) or {}
    event_ref = (data.get("event_ref") or "").strip()
    if not event_ref.startswith("feed:"):
        return jsonify({"error": "event_ref is required for an imported event."}), 400

    title = (data.get("title") or "").strip()
    start_raw = data.get("start_date") or data.get("start")
    end_raw = data.get("end_date") or data.get("end")
    all_day = bool(data.get("all_day", data.get("is_all_day", False)))
    calendar_id = _normalize_calendar_id(data.get("calendar_id"))

    if not title:
        return jsonify({"error": "title is required"}), 400

    start_dt = _parse_iso_like(start_raw)
    end_dt = _parse_iso_like(end_raw)
    if not start_dt or not end_dt:
        return jsonify({"error": "start_date and end_date must be valid ISO datetimes"}), 400
    if end_dt <= start_dt:
        return jsonify({"error": "end_date must be after start_date"}), 400

    try:
        color = _normalize_color(data.get("color"))
        reminder_minutes = _normalize_reminder_minutes(data.get("reminder_minutes"), all_day)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    try:
        _ensure_local_calendar_source(current_user.id, calendar_id)
        override = _upsert_event_override(
            current_user.id,
            event_ref,
            {
                "title": title,
                "description": data.get("description") or "",
                "start": format_datetime(start_dt),
                "end": format_datetime(end_dt),
                "is_all_day": all_day,
                "calendar_id": calendar_id,
                "color": color,
                "reminder_minutes": reminder_minutes,
                "hidden": False,
            },
        )
    except AppwriteException:
        logger.exception("Failed to save event override")
        return jsonify({"error": "Unable to save event override."}), 500

    return jsonify({"success": True, "override": override})


@calendar_bp.route("/event-overrides/hide", methods=["POST"])
@login_required
def hide_event_override():
    """Hide an imported event for the authenticated user without deleting the source feed event."""
    data = request.get_json(silent=True) or {}
    event_ref = (data.get("event_ref") or "").strip()
    if not event_ref.startswith("feed:"):
        return jsonify({"error": "event_ref is required for an imported event."}), 400

    try:
        override = _upsert_event_override(
            current_user.id,
            event_ref,
            {"hidden": True},
        )
    except AppwriteException:
        logger.exception("Failed to hide imported event")
        return jsonify({"error": "Unable to delete event."}), 500

    return jsonify({"success": True, "override": override})


@calendar_bp.route("/refresh", methods=["POST"])
@login_required
def refresh_feed():
    """
    POST /api/calendar/refresh
    Triggers an immediate re-fetch of all configured user calendar feeds.
    """
    user_id = str(current_user.id)
    try:
        settings = first_row(
            COLLECTIONS["user_settings"],
            [Query.equal("user_id", [user_id])],
        )
    except AppwriteException:
        logger.exception("Failed to load user settings")
        return jsonify({"error": "Unable to refresh calendar feeds."}), 500

    feed_urls = _configured_feed_urls(settings)

    if not feed_urls:
        return jsonify({
            "error": "No calendar feed URLs configured. Visit Settings to add one."
        }), 400

    from services.feed_fetcher import fetch_and_cache_feeds

    try:
        count = fetch_and_cache_feeds(user_id, feed_urls)
        update_row_safe(
            COLLECTIONS["user_settings"],
            settings.get("$id"),
            {"updated_at": format_datetime(datetime.utcnow())},
        )
        return jsonify({"status": "ok", "events_cached": count})
    except AppwriteException:
        logger.exception("Failed to update settings after refresh")
        return jsonify({"error": "Unable to refresh calendar feeds."}), 500
    except Exception as e:
        logger.exception(
            "Calendar refresh failed",
            extra={"user_id": user_id, "feed_count": len(feed_urls)},
        )
        return jsonify({"error": f"Feed fetch failed: {str(e)}"}), 500


@calendar_bp.route("/status")
@login_required
def feed_status():
    """
    GET /api/calendar/status
    """
    user_id = str(current_user.id)
    try:
        settings = first_row(
            COLLECTIONS["user_settings"],
            [Query.equal("user_id", [user_id])],
        )
        feed_urls = _configured_feed_urls(settings)

        count_response = list_calendar_rows_safe(
            COLLECTIONS["calendar_cache"],
            [Query.equal("user_id", [user_id]), Query.limit(1)],
        )
    except AppwriteException:
        logger.exception("Failed to load calendar status")
        return jsonify({"error": "Unable to load calendar status."}), 500

    return jsonify({
        "feed_configured": bool(feed_urls),
        "configured_feed_count": len(feed_urls),
        "refresh_interval_minutes": settings.get("feed_refresh_minutes") if settings else None,
        "last_fetched": _resolve_last_fetched(user_id),
        "cached_event_count": count_response.get("total", 0),
    })


@calendar_bp.route("/preferences", methods=["GET"])
@login_required
def get_calendar_preferences():
    """
    GET /api/calendar/preferences
    """
    user_id = str(current_user.id)
    try:
        prefs = list_calendar_rows_all(
            COLLECTIONS["user_calendar_preferences"],
            [Query.equal("user_id", [user_id])],
        )
    except AppwriteException:
        logger.exception("Failed to load calendar preferences")
        return jsonify({"error": "Unable to load calendar preferences."}), 500

    return jsonify({
        "preferences": [
            {
                "calendar_name": p.get("calendar_name"),
                "color_hex": p.get("color_hex"),
                "visible": p.get("visible"),
                "display_name": p.get("display_name") or "",
            }
            for p in prefs
        ]
    })


@calendar_bp.route("/preferences", methods=["POST"])
@login_required
def update_calendar_preferences():
    """
    POST /api/calendar/preferences
    """
    data = request.get_json() or {}
    calendar_name = data.get("calendar_name")

    if not calendar_name:
        return jsonify({"error": "calendar_name is required"}), 400

    user_id = str(current_user.id)
    try:
        updates = _calendar_preference_updates(data)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    try:
        pref = first_calendar_row(
            COLLECTIONS["user_calendar_preferences"],
            [
                Query.equal("user_id", [user_id]),
                Query.equal("calendar_name", [calendar_name]),
            ],
        )
        if pref and updates and _calendar_preference_unchanged(pref, updates):
            return jsonify({
                "status": "ok",
                "calendar_name": calendar_name,
                "color_hex": pref.get("color_hex"),
                "visible": pref.get("visible"),
                "display_name": pref.get("display_name") or "",
            })
        if updates or not pref:
            pref = _upsert_calendar_preference(user_id, calendar_name, updates)
    except AppwriteException:
        logger.exception("Failed to update calendar preference")
        return jsonify({"error": "Unable to update preferences."}), 500

    return jsonify({
        "status": "ok",
        "calendar_name": calendar_name,
        "color_hex": pref.get("color_hex"),
        "visible": pref.get("visible"),
        "display_name": pref.get("display_name") or "",
    })


@calendar_bp.route("/preferences/batch", methods=["POST"])
@login_required
def update_calendar_preferences_batch():
    """
    POST /api/calendar/preferences/batch
    """
    data = request.get_json() or {}
    entries = data.get("preferences")
    if not isinstance(entries, list):
        return jsonify({"error": "preferences must be a list"}), 400
    if len(entries) > PREFERENCES_BATCH_LIMIT:
        return jsonify({"error": f"preferences batch must be <= {PREFERENCES_BATCH_LIMIT}"}), 400

    user_id = str(current_user.id)
    updated = []
    skipped = []
    errors = []

    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            logger.warning("Invalid calendar preference entry", extra={"index": index})
            errors.append({"index": index, "error": "Invalid preference entry."})
            continue
        calendar_name = entry.get("calendar_name")
        if not calendar_name:
            logger.warning("Missing calendar_name in preference batch", extra={"index": index})
            errors.append({"index": index, "error": "calendar_name is required."})
            continue

        try:
            updates = _calendar_preference_updates(entry)
        except ValueError as exc:
            logger.warning("Invalid calendar preference update", extra={"calendar_name": calendar_name, "error": str(exc)})
            errors.append({"calendar_name": calendar_name, "error": str(exc)})
            continue

        try:
            pref = first_calendar_row(
                COLLECTIONS["user_calendar_preferences"],
                [
                    Query.equal("user_id", [user_id]),
                    Query.equal("calendar_name", [calendar_name]),
                ],
            )
            if pref and updates and _calendar_preference_unchanged(pref, updates):
                skipped.append(calendar_name)
                continue
            if updates or not pref:
                _upsert_calendar_preference(user_id, calendar_name, updates)
                updated.append(calendar_name)
            else:
                skipped.append(calendar_name)
        except AppwriteException:
            logger.exception("Failed to update calendar preference", extra={"calendar_name": calendar_name})
            errors.append({"calendar_name": calendar_name, "error": "Unable to update preferences."})

    return jsonify({
        "status": "ok",
        "updated": updated,
        "skipped": skipped,
        "errors": errors,
    })


@calendar_bp.route("/feed.ics")
def ics_feed():
    """
    GET /api/calendar/feed.ics?token=USER_SPECIFIC_TOKEN
    """
    token = request.args.get("token")
    if not token:
        return Response("Missing token", status=401, mimetype="text/plain")

    try:
        settings = first_row(
            COLLECTIONS["user_settings"],
            [Query.equal("ics_secret_token", [token])],
        )
    except AppwriteException:
        logger.exception("Failed to resolve calendar token")
        return Response("Feed lookup failed", status=500, mimetype="text/plain")
    if not settings:
        return Response("Invalid token", status=403, mimetype="text/plain")

    from services.ics_builder import build_ics_for_user

    try:
        ics_content = build_ics_for_user(settings.get("user_id"))
        return Response(
            ics_content,
            status=200,
            mimetype="text/calendar",
            headers={
                "Content-Disposition": "attachment; filename=nest_apstudy.ics",
            },
        )
    except Exception as e:
        return Response(
            f"Feed generation failed: {str(e)}",
            status=500,
            mimetype="text/plain",
        )
