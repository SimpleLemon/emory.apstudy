"""
blueprints/calendar_api.py

Per-user calendar data endpoints.
Fetches, caches, and serves Canvas iCal feed data.
Also provides a token-authenticated .ics subscription endpoint.
"""
import logging
import secrets
import uuid
from datetime import datetime

from flask import Blueprint, jsonify, request, Response
from flask_login import login_required, current_user

from appwrite.exception import AppwriteException
from appwrite.id import ID
from appwrite.query import Query
from appwrite_client import COLLECTIONS
from appwrite_helpers import (
    first_row,
    format_datetime,
    update_row_safe,
)
from blueprints.settings import (
    _normalize_calendar_url,
    _normalize_canvas_calendar_url,
    _settings_defaults,
    _validate_other_calendar_urls,
)
from services.calendar_events import (
    CANVAS_SOURCE_ID,
    FEED_SOURCE_PREFIX,
    LOCAL_SOURCE_PREFIX,
    DEFAULT_LOCAL_SOURCE_ID,
    DEFAULT_LOCAL_SOURCE_NAME,
    DEFAULT_CALENDAR_COLOR,
    SIMULATED_CALENDAR_NAME,
    CALENDAR_SHARE_CODE_LENGTH,
    CALENDAR_SHARE_CODE_CHARS,
    CALENDAR_SHARE_DATE_SCOPES,
    CALENDAR_SHARE_MIN_ROLLING_DAYS,
    CALENDAR_SHARE_MAX_ROLLING_DAYS,
    PREFERENCES_BATCH_LIMIT,
    TIMED_EVENT_REMINDERS,
    ALL_DAY_EVENT_REMINDERS,
    _canonical_feed_url,
    _raw_feed_url_hash,
    _feed_url_hash,
    _feed_source_id,
    _legacy_feed_source_id,
    _normalize_display_name,
    _normalize_source_label,
    _url_fallback_label,
    _source_id_for_feed_url,
    _event_ref_for_cache_event,
    _event_ref_for_user_event,
    _normalize_color,
    _default_reminder_minutes,
    _normalize_reminder_minutes,
    _serialized_reminder_minutes,
    _calendar_preference_updates,
    _calendar_preference_unchanged,
    _normalize_calendar_id,
    _serialize_datetime,
    _span_metadata,
    _serialize_event,
    _serialize_user_event,
    _coerce_utc,
    _parse_range_param,
    _event_overlaps_range,
    _resolve_last_fetched,
    _configured_feed_urls,
    _load_calendar_feed_metadata as _load_calendar_feed_metadata_service,
    _configured_feed_sources,
    _load_local_calendar_sources as _load_local_calendar_sources_service,
    _configured_local_sources,
    _configured_calendar_sources,
    _task_calendar_payload,
    _append_task_calendar_source,
    _ensure_user_settings,
    _ensure_local_calendar_source,
    _load_event_overrides as _load_event_overrides_service,
    _apply_event_override,
    _api_event_overlaps_range,
    _filter_configured_cache_events,
    _feed_needs_initial_fetch,
    _initial_fetch_feed_urls,
    _refresh_initial_feed_cache,
    _delete_cache_rows_for_feed,
    _update_local_calendar_source_payload,
    _update_url_calendar_source_payload,
    _load_calendar_preferences as _load_calendar_preferences_service,
    _upsert_calendar_preference,
    _upsert_event_override,
    _settings_payload_for_source_update,
    _calendar_shares_collection,
    _share_url,
    _generate_calendar_share_code as _generate_calendar_share_code_service,
    _parse_json_list,
    _normalize_share_calendar_ids,
    _parse_date_start,
    _fixed_end_display_date,
    _normalize_calendar_share_payload,
    _calendar_share_scope_label,
    _calendar_share_payload,
    _calendar_share_scope_range,
    _intersect_ranges,
    _range_queries,
    _load_serialized_calendar_events as _load_serialized_calendar_events_service,
    _sanitize_public_event,
    _sanitize_public_sources,
    _resolve_calendar_share_by_code as _resolve_calendar_share_by_code_service,
    _public_calendar_share_context,
    _public_calendar_events_payload as _public_calendar_events_payload_service,
    get_events_response,
)
from services.calendar_urls import (
    iter_valid_other_calendar_urls,
    load_other_calendar_urls,
)
from services.discord_audit import emit_creation_event, format_actor
from services.row_utils import row_id as _row_id
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


def _load_calendar_feed_metadata(user_id):
    return _load_calendar_feed_metadata_service(user_id, list_calendar_rows_all)


def _load_local_calendar_sources(user_id):
    return _load_local_calendar_sources_service(user_id, list_calendar_rows_all)


def _load_event_overrides(user_id):
    return _load_event_overrides_service(user_id, list_calendar_rows_all)


def _load_calendar_preferences(user_id):
    return _load_calendar_preferences_service(user_id, list_calendar_rows_all)


def _calendar_serialization_dependencies():
    return {
        "api_event_overlaps_range": _api_event_overlaps_range,
        "apply_event_override": _apply_event_override,
        "configured_feed_urls": _configured_feed_urls,
        "filter_configured_cache_events": _filter_configured_cache_events,
        "list_calendar_rows_all": list_calendar_rows_all,
        "load_event_overrides": _load_event_overrides,
        "range_queries": _range_queries,
        "serialize_event": _serialize_event,
        "serialize_user_event": _serialize_user_event,
    }


def _load_serialized_calendar_events(
    user_id,
    settings,
    range_start=None,
    range_end=None,
):
    return _load_serialized_calendar_events_service(
        user_id,
        settings,
        range_start,
        range_end,
        _calendar_serialization_dependencies(),
    )


def _generate_calendar_share_code():
    return _generate_calendar_share_code_service(first_calendar_row)


def _resolve_calendar_share_by_code(share_code, active_only=True):
    return _resolve_calendar_share_by_code_service(
        share_code,
        active_only,
        first_calendar_row,
    )


def _calendar_share_dependencies():
    return {
        "append_task_calendar_source": _append_task_calendar_source,
        "calendar_share_payload": _calendar_share_payload,
        "calendar_share_scope_range": _calendar_share_scope_range,
        "configured_calendar_sources": _configured_calendar_sources,
        "configured_feed_urls": _configured_feed_urls,
        "first_row": first_row,
        "intersect_ranges": _intersect_ranges,
        "load_calendar_feed_metadata": _load_calendar_feed_metadata,
        "load_calendar_preferences": _load_calendar_preferences,
        "load_local_calendar_sources": _load_local_calendar_sources,
        "load_serialized_calendar_events": _load_serialized_calendar_events,
        "parse_json_list": _parse_json_list,
        "sanitize_public_event": _sanitize_public_event,
        "sanitize_public_sources": _sanitize_public_sources,
        "task_calendar_payload": _task_calendar_payload,
    }


def _public_calendar_events_payload(
    share,
    requested_start=None,
    requested_end=None,
):
    return _public_calendar_events_payload_service(
        share,
        requested_start,
        requested_end,
        _calendar_share_dependencies(),
    )


def _get_events_dependencies():
    return {
        "api_event_overlaps_range": _api_event_overlaps_range,
        "append_task_calendar_source": _append_task_calendar_source,
        "apply_event_override": _apply_event_override,
        "collections": COLLECTIONS,
        "configured_calendar_sources": _configured_calendar_sources,
        "configured_feed_urls": _configured_feed_urls,
        "filter_configured_cache_events": _filter_configured_cache_events,
        "first_row": first_row,
        "jsonify": jsonify,
        "list_calendar_rows_all": list_calendar_rows_all,
        "load_calendar_feed_metadata": _load_calendar_feed_metadata,
        "load_calendar_preferences": _load_calendar_preferences,
        "load_event_overrides": _load_event_overrides,
        "load_local_calendar_sources": _load_local_calendar_sources,
        "logger": logger,
        "parse_range_param": _parse_range_param,
        "query": Query,
        "refresh_initial_feed_cache": _refresh_initial_feed_cache,
        "resolve_last_fetched": _resolve_last_fetched,
        "serialize_event": _serialize_event,
        "serialize_user_event": _serialize_user_event,
        "task_calendar_payload": _task_calendar_payload,
    }


@calendar_bp.route("/events")
@login_required
def get_events():
    """Return cached calendar events for the authenticated user."""
    return get_events_response(
        str(current_user.id),
        current_user.id,
        request.args,
        _get_events_dependencies(),
    )


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
        count = fetch_and_cache_feeds(user_id, feed_urls, force=True)
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
