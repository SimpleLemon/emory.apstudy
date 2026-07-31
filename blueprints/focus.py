"""Focus Mode page and account-synced API routes."""

import logging
import sqlite3

from flask import Blueprint, g, jsonify, make_response, render_template, request
from flask_login import current_user, login_required

from services.dashboard_context import (
    load_user_settings,
    theme_from_settings,
    user_payload,
)
from services import focus_mode
from services.entitlements import EntitlementError, EntitlementLimitError, request_entitlements


focus_bp = Blueprint("focus", __name__)
logger = logging.getLogger(__name__)


def _load_user_settings():
    return load_user_settings(current_user, g, error_logger=logger)


def _theme_from_settings(user_settings):
    return theme_from_settings(user_settings)


def _user_payload():
    return user_payload(current_user)


def _error_response(error):
    if isinstance(error, LookupError):
        return jsonify({"error": str(error)}), 404
    if isinstance(error, RuntimeError):
        return jsonify({"error": str(error)}), 409
    if isinstance(error, (ValueError, TypeError)):
        return jsonify({"error": str(error)}), 400
    logger.exception("Focus Mode request failed")
    return jsonify({"error": "Focus Mode could not save that change. Try again."}), 500


@focus_bp.get("/focus")
@login_required
def focus_page():
    if not current_user.onboarding_complete:
        from flask import redirect, url_for
        return redirect(url_for("settings.onboarding"))
    settings = _load_user_settings()
    response = make_response(render_template(
        "focus.html",
        user=_user_payload(),
        theme_preference=_theme_from_settings(settings),
    ))
    # Cloudflare honors no-transform by leaving authenticated HTML untouched,
    # which prevents its optional analytics beacon from being injected here.
    response.headers["Cache-Control"] = "private, no-store, no-transform"
    return response


@focus_bp.get("/api/focus")
@login_required
def get_focus_state():
    try:
        return jsonify(focus_mode.snapshot(
            current_user.id,
            entitlements=request_entitlements(current_user),
        ))
    except (ValueError, sqlite3.Error, EntitlementError) as error:
        return _error_response(error)


@focus_bp.get("/api/focus/status")
@login_required
def get_focus_status():
    try:
        return jsonify({"active": focus_mode.is_focus_mode_active(current_user.id)})
    except sqlite3.Error as error:
        return _error_response(error)


@focus_bp.patch("/api/focus/player-preferences")
@login_required
def update_focus_player_preferences():
    try:
        preferences = focus_mode.save_player_preferences(
            current_user.id, request.get_json(silent=True) or {}
        )
        return jsonify({"player_preferences": preferences})
    except (ValueError, sqlite3.Error) as error:
        return _error_response(error)


@focus_bp.post("/api/focus/playlists/preview")
@login_required
def preview_focus_playlist():
    try:
        payload = request.get_json(silent=True) or {}
        playlist = focus_mode.playlist(payload.get("playlist_url") or payload.get("spotify_url"))
        return jsonify({"playlist": playlist})
    except ValueError as error:
        return _error_response(error)


@focus_bp.post("/api/focus/playlists")
@login_required
def add_focus_playlist():
    try:
        payload = request.get_json(silent=True) or {}
        entitlements = request_entitlements(current_user)
        playlists = focus_mode.add_user_playlist(
            current_user.id,
            payload.get("spotify_url") or payload.get("playlist_url"),
            entitlements=entitlements,
        )
        active_url = next(
            (item["spotify_url"] for item in playlists if item.get("active")),
            playlists[0]["spotify_url"] if playlists else None,
        )
        return jsonify({
            "spotify_url": active_url,
            "playlists": playlists,
            "playlist_entitlements": focus_mode.playlist_entitlements(
                entitlements, usage=len(playlists),
            ),
        })
    except EntitlementLimitError as exc:
        return jsonify(exc.payload()), 403
    except (ValueError, EntitlementError, sqlite3.Error) as error:
        return _error_response(error)


@focus_bp.delete("/api/focus/playlists")
@login_required
def remove_focus_playlist():
    try:
        payload = request.get_json(silent=True) or {}
        playlists = focus_mode.remove_user_playlist(
            current_user.id,
            payload.get("spotify_url") or payload.get("playlist_url"),
        )
        entitlements = request_entitlements(current_user)
        active_url = next(
            (item["spotify_url"] for item in playlists if item.get("active")),
            playlists[0]["spotify_url"] if playlists else None,
        )
        return jsonify({
            "spotify_url": active_url,
            "playlists": playlists,
            "playlist_entitlements": focus_mode.playlist_entitlements(
                entitlements, usage=len(playlists),
            ),
        })
    except (ValueError, EntitlementError, sqlite3.Error) as error:
        return _error_response(error)


@focus_bp.patch("/api/focus/playlists/active")
@login_required
def set_active_focus_playlist():
    try:
        payload = request.get_json(silent=True) or {}
        source = focus_mode.set_active_playlist(
            current_user.id,
            payload.get("spotify_url") or payload.get("playlist_url"),
        )
        entitlements = request_entitlements(current_user)
        return jsonify({
            **source,
            "playlist_entitlements": focus_mode.playlist_entitlements(
                entitlements, usage=len(source.get("playlists") or []),
            ),
        })
    except (ValueError, EntitlementError, sqlite3.Error) as error:
        return _error_response(error)


@focus_bp.post("/api/focus/routines")
@login_required
def create_focus_routine():
    try:
        routine = focus_mode.save_routine(current_user.id, request.get_json(silent=True) or {})
        return jsonify({"routine": routine}), 201
    except (ValueError, LookupError, sqlite3.Error) as error:
        return _error_response(error)


@focus_bp.patch("/api/focus/routines/<routine_id>")
@login_required
def update_focus_routine(routine_id):
    try:
        routine = focus_mode.save_routine(
            current_user.id,
            request.get_json(silent=True) or {},
            routine_id=routine_id,
        )
        return jsonify({"routine": routine})
    except (ValueError, LookupError, sqlite3.Error) as error:
        return _error_response(error)


@focus_bp.delete("/api/focus/routines/<routine_id>")
@login_required
def delete_focus_routine(routine_id):
    try:
        if not focus_mode.delete_routine(current_user.id, routine_id):
            raise LookupError("Focus routine not found.")
        return jsonify({"deleted": True})
    except (LookupError, sqlite3.Error) as error:
        return _error_response(error)


@focus_bp.post("/api/focus/sessions")
@login_required
def start_focus_session():
    try:
        session = focus_mode.start_session(current_user.id, request.get_json(silent=True) or {})
        return jsonify({"session": session}), 201
    except (ValueError, LookupError, RuntimeError, sqlite3.Error) as error:
        return _error_response(error)


@focus_bp.patch("/api/focus/sessions/<session_id>")
@login_required
def update_focus_session(session_id):
    payload = request.get_json(silent=True) or {}
    try:
        session = focus_mode.update_session(
            current_user.id,
            session_id,
            payload.get("action"),
            payload=payload,
            entitlements=request_entitlements(current_user),
        )
        return jsonify({"session": session, "active": session["state"] in focus_mode.ACTIVE_STATES})
    except EntitlementLimitError as exc:
        return jsonify(exc.payload()), 403
    except (ValueError, LookupError, sqlite3.Error, EntitlementError) as error:
        return _error_response(error)
