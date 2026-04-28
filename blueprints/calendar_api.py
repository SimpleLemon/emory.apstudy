"""
blueprints/calendar_api.py

Per-user calendar data endpoints.
Fetches, caches, and serves Canvas iCal feed data.
Also provides a token-authenticated .ics subscription endpoint.
"""
import json
import logging
from datetime import datetime, timezone
from flask import Blueprint, jsonify, request, Response
from flask_login import login_required, current_user
from extensions import db
from models import User, UserSettings, CalendarCache, UserCalendarPreference, UserEvent

calendar_bp = Blueprint("calendar", __name__)
logger = logging.getLogger(__name__)


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


def _serialize_event(e):
    """Serialize a CalendarCache row for API response."""
    is_all_day = bool(getattr(e, "is_all_day", False))
    is_multi_day, span_days = _span_metadata(e.event_start, e.event_end, is_all_day)

    return {
        "uid": e.event_uid,
        "title": e.event_title,
        "start": _serialize_datetime(e.event_start, is_all_day),
        "end": _serialize_datetime(e.event_end, is_all_day),
        "type": e.event_type,
        "course": e.course_name,
        "description": e.raw_description,
        "fetched_at": e.fetched_at.isoformat() if e.fetched_at else None,
        "is_multi_day": is_multi_day,
        "span_days": span_days,
        "is_all_day": is_all_day,
    }


def _serialize_user_event(ev):
    """Serialize a UserEvent row for API response."""
    return {
        "id": ev.id,
        "title": ev.title,
        "description": ev.description,
        "start": _serialize_datetime(ev.start, ev.is_all_day),
        "end": _serialize_datetime(ev.end, ev.is_all_day),
        "is_all_day": bool(ev.is_all_day),
        "color": ev.color,
        "created_at": ev.created_at.isoformat() if ev.created_at else None,
        "updated_at": ev.updated_at.isoformat() if ev.updated_at else None,
    }


def _configured_feed_urls(settings):
    """Return all configured calendar feed URLs for a user."""
    if not settings:
        return []
    urls = []
    if settings.canvas_ical_url:
        urls.append(settings.canvas_ical_url.strip())
    if settings.other_ical_urls_json:
        try:
            extras = json.loads(settings.other_ical_urls_json)
            if isinstance(extras, list):
                for item in extras:
                    if isinstance(item, str) and item.strip():
                        urls.append(item.strip())
        except json.JSONDecodeError:
            pass
    return urls


@calendar_bp.route("/events")
@login_required
def get_events():
    """
    GET /api/calendar/events
    Returns cached calendar events for the authenticated user.
    """
    cache_events = CalendarCache.query.filter_by(
        user_id=current_user.id
    ).order_by(
        CalendarCache.event_start.asc()
    ).all()

    created_events = UserEvent.query.filter_by(
        user_id=current_user.id
    ).order_by(
        UserEvent.start.asc()
    ).all()

    serialized = [_serialize_event(e) for e in cache_events] + [_serialize_user_event(e) for e in created_events]

    return jsonify({
        "user_id": current_user.id,
        "count": len(serialized),
        "events": serialized,
    })


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
    color = data.get("color")

    if not title:
        return jsonify({"error": "title is required"}), 400

    start_dt = _parse_iso_like(start_raw)
    end_dt = _parse_iso_like(end_raw)

    if not start_dt or not end_dt:
        return jsonify({"error": "start_date and end_date must be valid ISO datetimes"}), 400

    if end_dt <= start_dt:
        return jsonify({"error": "end_date must be after start_date"}), 400

    ev = UserEvent(
        user_id=current_user.id,
        title=title,
        description=description,
        start=start_dt,
        end=end_dt,
        is_all_day=all_day,
        color=color,
    )
    db.session.add(ev)
    db.session.commit()

    return jsonify({"success": True, "event": _serialize_user_event(ev)})


@calendar_bp.route("/events/<int:event_id>", methods=["GET"])
@login_required
def get_single_event(event_id):
    ev = UserEvent.query.filter_by(id=event_id, user_id=current_user.id).first()
    if not ev:
        return jsonify({"error": "not found"}), 404
    return jsonify({"event": _serialize_user_event(ev)})


@calendar_bp.route("/events/<int:event_id>", methods=["PUT"])
@login_required
def update_event(event_id):
    ev = UserEvent.query.filter_by(id=event_id, user_id=current_user.id).first()
    if not ev:
        return jsonify({"error": "not found"}), 404

    data = request.get_json() or {}
    title = data.get("title")
    description = data.get("description")
    start_raw = data.get("start_date") or data.get("start")
    end_raw = data.get("end_date") or data.get("end")
    all_day = data.get("all_day")
    color = data.get("color")

    if title is not None:
        ev.title = title
    if description is not None:
        ev.description = description
    if start_raw is not None:
        parsed = _parse_iso_like(start_raw)
        if parsed:
            ev.start = parsed
    if end_raw is not None:
        parsed = _parse_iso_like(end_raw)
        if parsed:
            ev.end = parsed
    if all_day is not None:
        ev.is_all_day = bool(all_day)
    if color is not None:
        ev.color = color

    ev.updated_at = datetime.utcnow()
    db.session.commit()

    return jsonify({"success": True, "event": _serialize_user_event(ev)})


@calendar_bp.route("/events/<int:event_id>", methods=["DELETE"])
@login_required
def delete_event(event_id):
    ev = UserEvent.query.filter_by(id=event_id, user_id=current_user.id).first()
    if not ev:
        return jsonify({"error": "not found"}), 404
    db.session.delete(ev)
    db.session.commit()
    return jsonify({"success": True})


@calendar_bp.route("/refresh", methods=["POST"])
@login_required
def refresh_feed():
    """
    POST /api/calendar/refresh
    Triggers an immediate re-fetch of all configured user calendar feeds.
    """
    settings = UserSettings.query.filter_by(user_id=current_user.id).first()
    feed_urls = _configured_feed_urls(settings)

    if not feed_urls:
        return jsonify({
            "error": "No calendar feed URLs configured. Visit Settings to add one."
        }), 400

    from services.feed_fetcher import fetch_and_cache_feeds

    try:
        count = fetch_and_cache_feeds(current_user.id, feed_urls)
        settings.updated_at = datetime.utcnow()
        db.session.commit()
        return jsonify({"status": "ok", "events_cached": count})
    except Exception as e:
        logger.exception(
            "Calendar refresh failed",
            extra={"user_id": current_user.id, "feed_count": len(feed_urls)},
        )
        return jsonify({"error": f"Feed fetch failed: {str(e)}"}), 500


@calendar_bp.route("/status")
@login_required
def feed_status():
    """
    GET /api/calendar/status
    """
    settings = UserSettings.query.filter_by(user_id=current_user.id).first()
    feed_urls = _configured_feed_urls(settings)

    latest_event = CalendarCache.query.filter_by(
        user_id=current_user.id
    ).order_by(
        CalendarCache.fetched_at.desc()
    ).first()

    return jsonify({
        "feed_configured": bool(feed_urls),
        "configured_feed_count": len(feed_urls),
        "refresh_interval_minutes": settings.feed_refresh_minutes if settings else None,
        "last_fetched": latest_event.fetched_at.isoformat() if latest_event else None,
        "cached_event_count": CalendarCache.query.filter_by(
            user_id=current_user.id
        ).count(),
    })


@calendar_bp.route("/preferences", methods=["GET"])
@login_required
def get_calendar_preferences():
    """
    GET /api/calendar/preferences
    """
    prefs = UserCalendarPreference.query.filter_by(user_id=current_user.id).all()

    return jsonify({
        "preferences": [
            {
                "calendar_name": p.calendar_name,
                "color_hex": p.color_hex,
                "visible": p.visible,
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
    color_hex = data.get("color_hex")
    visible = data.get("visible")

    if not calendar_name:
        return jsonify({"error": "calendar_name is required"}), 400

    pref = UserCalendarPreference.query.filter_by(
        user_id=current_user.id,
        calendar_name=calendar_name
    ).first()

    if not pref:
        pref = UserCalendarPreference(
            user_id=current_user.id,
            calendar_name=calendar_name,
        )
        db.session.add(pref)

    if color_hex:
        pref.color_hex = color_hex
    if visible is not None:
        pref.visible = visible

    pref.updated_at = datetime.utcnow()
    db.session.commit()

    return jsonify({
        "status": "ok",
        "calendar_name": calendar_name,
        "color_hex": pref.color_hex,
        "visible": pref.visible,
    })


@calendar_bp.route("/feed.ics")
def ics_feed():
    """
    GET /api/calendar/feed.ics?token=USER_SPECIFIC_TOKEN
    """
    token = request.args.get("token")
    if not token:
        return Response("Missing token", status=401, mimetype="text/plain")

    settings = UserSettings.query.filter_by(ics_secret_token=token).first()
    if not settings:
        return Response("Invalid token", status=403, mimetype="text/plain")

    from services.ics_builder import build_ics_for_user

    try:
        ics_content = build_ics_for_user(settings.user_id)
        return Response(
            ics_content,
            status=200,
            mimetype="text/calendar",
            headers={
                "Content-Disposition": "attachment; filename=emory_apstudy.ics",
            },
        )
    except Exception as e:
        return Response(
            f"Feed generation failed: {str(e)}",
            status=500,
            mimetype="text/plain",
        )