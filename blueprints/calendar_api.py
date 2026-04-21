"""
blueprints/calendar_api.py

Per-user calendar data endpoints.
Fetches, caches, and serves Canvas iCal feed data.
Also provides a token-authenticated .ics subscription endpoint.

Phase 5 (blocked until fall enrollment) [1] [3].
Route stubs are functional; feed fetching returns empty until
users configure their Canvas iCal URLs.
"""

from datetime import datetime

from flask import Blueprint, jsonify, request, Response
from flask_login import login_required, current_user

from extensions import db
from models import User, UserSettings, CalendarCache

calendar_bp = Blueprint("calendar", __name__)


@calendar_bp.route("/events")
@login_required
def get_events():
    """
    GET /api/calendar/events

    Returns cached calendar events for the authenticated user.
    Events are sorted by start time, most recent first.
    """
    events = CalendarCache.query.filter_by(
        user_id=current_user.id
    ).order_by(
        CalendarCache.event_start.asc()
    ).all()

    return jsonify({
        "user_id": current_user.id,
        "count": len(events),
        "events": [
            {
                "uid": e.event_uid,
                "title": e.event_title,
                "start": e.event_start.isoformat() if e.event_start else None,
                "end": e.event_end.isoformat() if e.event_end else None,
                "type": e.event_type,
                "course": e.course_name,
                "description": e.raw_description,
                "fetched_at": e.fetched_at.isoformat() if e.fetched_at else None,
            }
            for e in events
        ],
    })


@calendar_bp.route("/refresh", methods=["POST"])
@login_required
def refresh_feed():
    """
    POST /api/calendar/refresh

    Triggers an immediate re-fetch of the user's Canvas iCal feed.
    Returns the updated event count.
    """
    settings = UserSettings.query.filter_by(user_id=current_user.id).first()

    if not settings or not settings.canvas_ical_url:
        return jsonify({
            "error": "No Canvas iCal feed URL configured. Visit Settings to add one."
        }), 400

    # Import here to avoid circular dependency at module load time
    from services.feed_fetcher import fetch_and_cache_feed

    try:
        count = fetch_and_cache_feed(current_user.id, settings.canvas_ical_url)
        settings.updated_at = datetime.utcnow()
        db.session.commit()
        return jsonify({"status": "ok", "events_cached": count})
    except Exception as e:
        return jsonify({"error": f"Feed fetch failed: {str(e)}"}), 500


@calendar_bp.route("/status")
@login_required
def feed_status():
    """
    GET /api/calendar/status

    Returns the current feed configuration and cache freshness
    for the authenticated user.
    """
    settings = UserSettings.query.filter_by(user_id=current_user.id).first()

    latest_event = CalendarCache.query.filter_by(
        user_id=current_user.id
    ).order_by(
        CalendarCache.fetched_at.desc()
    ).first()

    return jsonify({
        "feed_configured": bool(settings and settings.canvas_ical_url),
        "refresh_interval_minutes": settings.feed_refresh_minutes if settings else None,
        "last_fetched": latest_event.fetched_at.isoformat() if latest_event else None,
        "cached_event_count": CalendarCache.query.filter_by(
            user_id=current_user.id
        ).count(),
    })


@calendar_bp.route("/feed.ics")
def ics_feed():
    """
    GET /api/calendar/feed.ics?token=USER_SPECIFIC_TOKEN

    Token-authenticated .ics endpoint for Apple Calendar subscription.
    Does not use session auth because calendar clients cannot send cookies.
    The token is per-user, generated at account creation, and stored in
    user_settings.ics_secret_token.
    """
    token = request.args.get("token")
    if not token:
        return Response("Missing token", status=401, mimetype="text/plain")

    settings = UserSettings.query.filter_by(ics_secret_token=token).first()
    if not settings:
        return Response("Invalid token", status=403, mimetype="text/plain")

    # Import here to avoid circular dependency
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