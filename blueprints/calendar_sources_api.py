import json
import logging
import uuid
from datetime import datetime

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required

from appwrite.exception import AppwriteException
from appwrite.query import Query
from appwrite_client import COLLECTIONS
from appwrite_helpers import format_datetime, update_row_safe
from services.calendar_store import list_calendar_rows_all
from services.calendar_urls import load_other_calendar_urls
from services import invites
from services.entitlements import EntitlementError, EntitlementLimitError, request_entitlements
from blueprints.calendar_api import (
    DEFAULT_CALENDAR_COLOR,
    DEFAULT_LOCAL_SOURCE_NAME,
    LOCAL_SOURCE_PREFIX,
    _configured_feed_sources,
    _configured_feed_urls,
    _configured_local_sources,
    _ensure_local_calendar_source,
    _ensure_user_settings,
    _feed_source_id,
    _filter_configured_cache_events,
    _load_calendar_feed_metadata,
    _load_calendar_preferences,
    _load_local_calendar_sources,
    _normalize_color,
    _normalize_display_name,
    _update_local_calendar_source_payload,
    _update_url_calendar_source_payload,
    _upsert_calendar_preference,
    _validate_other_calendar_urls,
)


calendar_sources_bp = Blueprint("calendar_sources", __name__)
logger = logging.getLogger(__name__)


@calendar_sources_bp.route("/sources", methods=["POST"])
@login_required
def update_calendar_source():
    data = request.get_json(silent=True) or {}
    source_id = (data.get("source_id") or "").strip()
    next_display_name = _normalize_display_name(data.get("display_name"))
    next_url = (data.get("url") or "").strip()

    if not source_id:
        return jsonify({"error": "source_id is required"}), 400

    user_id = str(current_user.id)
    if source_id.startswith(LOCAL_SOURCE_PREFIX):
        try:
            return jsonify(_update_local_calendar_source_payload(user_id, source_id, next_display_name))
        except AppwriteException:
            logger.exception("Failed to update local calendar source")
            return jsonify({"error": "Unable to update calendar source."}), 500

    if not next_url:
        return jsonify({"error": "Calendar URL is required."}), 400

    try:
        payload = _update_url_calendar_source_payload(user_id, source_id, next_display_name, next_url)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except AppwriteException:
        logger.exception("Failed to update calendar source")
        return jsonify({"error": "Unable to update calendar source."}), 500
    return jsonify(payload)


@calendar_sources_bp.route("/sources/local", methods=["POST"])
@login_required
def create_local_calendar_source():
    data = request.get_json(silent=True) or {}
    display_name = _normalize_display_name(data.get("display_name")) or DEFAULT_LOCAL_SOURCE_NAME
    try:
        color_hex = _normalize_color(data.get("color_hex") or data.get("color")) or DEFAULT_CALENDAR_COLOR
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    user_id = str(current_user.id)
    source_id = f"{LOCAL_SOURCE_PREFIX}{uuid.uuid4().hex}"
    try:
        _ensure_local_calendar_source(user_id, source_id, display_name)
        _upsert_calendar_preference(
            user_id,
            source_id,
            {
                "display_name": display_name,
                "color_hex": color_hex,
                "visible": True,
            },
        )
        preferences = _load_calendar_preferences(user_id)
        local_sources = _load_local_calendar_sources(user_id)
    except AppwriteException:
        logger.exception("Failed to create local calendar source")
        return jsonify({"error": "Unable to create calendar."}), 500

    try:
        invites.record_activation(user_id, "calendar")
    except Exception:
        logger.exception("Failed to record invite activation for local calendar")

    sources = _configured_local_sources(local_sources, preferences)
    return jsonify({
        "status": "ok",
        "source": next((item for item in sources if item.get("id") == source_id), None),
    })


@calendar_sources_bp.route("/sources/url", methods=["POST"])
@login_required
def create_url_calendar_source():
    data = request.get_json(silent=True) or {}
    raw_url = (data.get("url") or "").strip()
    display_name = _normalize_display_name(data.get("display_name"))
    try:
        color_hex = _normalize_color(data.get("color_hex") or data.get("color")) if (data.get("color_hex") or data.get("color")) else None
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    if not raw_url:
        return jsonify({"error": "Calendar URL is required."}), 400

    user_id = str(current_user.id)
    try:
        from services.feed_fetcher import ensure_fetchable_calendar_url, fetch_and_cache_feeds

        settings = _ensure_user_settings(user_id)
        current_canvas_url = (settings.get("canvas_ical_url") or "").strip()
        other_urls = load_other_calendar_urls(settings)
        validated_other_urls = _validate_other_calendar_urls(other_urls + [raw_url], current_canvas_url)
        new_url = validated_other_urls[-1]
        ensure_fetchable_calendar_url(new_url)

        entitlements = request_entitlements(current_user)
        feed_limit = entitlements["limits"].get("max_calendar_feeds")
        feed_count = (1 if current_canvas_url else 0) + len(validated_other_urls)
        if feed_limit is not None and feed_count > feed_limit:
            raise EntitlementLimitError(
                "calendar feeds",
                entitlements["usage"]["calendar_feeds"],
                feed_count,
                feed_limit,
            )

        settings = update_row_safe(
            COLLECTIONS["user_settings"],
            settings.get("$id"),
            {
                "other_ical_urls_json": json.dumps(validated_other_urls),
                "updated_at": format_datetime(datetime.utcnow()),
            },
        )
        source_id = _feed_source_id(new_url)
        pref_updates = {"display_name": display_name, "visible": True}
        if color_hex:
            pref_updates["color_hex"] = color_hex
        _upsert_calendar_preference(user_id, source_id, pref_updates)
    except EntitlementLimitError as exc:
        return jsonify(exc.payload()), 403
    except EntitlementError:
        logger.exception("Failed to verify calendar feed limits")
        return jsonify({"error": "Unable to verify your calendar limits right now.", "code": "tier_check_unavailable"}), 503
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except AppwriteException:
        logger.exception("Failed to create URL calendar source")
        return jsonify({"error": "Unable to add calendar."}), 500

    try:
        invites.record_activation(user_id, "calendar")
    except Exception:
        logger.exception("Failed to record invite activation for URL calendar")

    refresh_error = None
    try:
        fetch_and_cache_feeds(user_id, [new_url], force=True)
    except Exception as exc:
        logger.exception("Failed to fetch new URL calendar source", extra={"user_id": user_id})
        refresh_error = str(exc)

    try:
        cache_events = list_calendar_rows_all(
            COLLECTIONS["calendar_cache"],
            [
                Query.equal("user_id", [user_id]),
                Query.order_asc("event_start"),
            ],
        )
        preferences = _load_calendar_preferences(user_id)
        feed_metadata = _load_calendar_feed_metadata(user_id)
    except AppwriteException:
        logger.exception("Failed to load new URL calendar source")
        return jsonify({"error": "Calendar was added, but source metadata could not be loaded."}), 500

    cache_events = _filter_configured_cache_events(cache_events, _configured_feed_urls(settings))
    sources = _configured_feed_sources(settings, cache_events, preferences, feed_metadata)
    return jsonify({
        "status": "ok",
        "source": next((item for item in sources if item.get("id") == source_id), None),
        "refresh_error": refresh_error,
    })
