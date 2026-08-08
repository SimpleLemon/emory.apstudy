"""
blueprints/settings.py

Per-user settings and onboarding routes.
Handles Canvas iCal URL configuration, refresh intervals,
.ics token management, and "My Courses" selections.
"""

import json
import os
import re
import secrets
import logging
import shutil
from datetime import datetime
from urllib.parse import urlparse, urlunparse

from flask import Blueprint, render_template, request, jsonify, redirect, url_for
from flask_login import login_required, current_user

from appwrite.exception import AppwriteException
from appwrite.id import ID
from appwrite.input_file import InputFile
from appwrite.permission import Permission
from appwrite.query import Query
from appwrite.role import Role
from appwrite.services.account import Account
from appwrite.services.storage import Storage
from appwrite.services.users import Users
from appwrite_client import client as appwrite_client
from appwrite_client import COLLECTIONS, PROFILE_AVATAR_BUCKET_ID
from appwrite_helpers import (
    create_row_safe,
    delete_row_safe,
    first_row,
    format_datetime,
    get_row_safe,
    list_rows_all,
    update_row_safe,
)
from services.atlas_client import DEFAULT_TERM
from services.avatar_storage import (
    ALLOWED_AVATAR_MIME_TYPES,
    MAX_AVATAR_BYTES,
    build_avatar_view_url,
    delete_avatar_file,
)
from services.chat_presence import sync_chat_presence_labels_for_user
from services.discord_audit import emit_creation_event, emit_user_event, format_actor
from services import discord_bridge, invites
from services.calendar_store import delete_calendar_rows_by_user
from services.calendar_urls import (
    MAX_OTHER_CALENDAR_URLS,
    iter_valid_other_calendar_urls as _iter_valid_other_calendar_urls,
    load_other_calendar_urls as _load_other_calendar_urls,
    normalize_calendar_url as _normalize_calendar_url,
)
from services.user_cleanup import delete_user_data
from services.user_profile import (
    DEFAULT_BANNER_COLOR,
    USERNAME_MAX_LENGTH,
    USERNAME_MIN_LENGTH,
    USERNAME_PATTERN,
)
from services.universities import school_payload
from services.entitlements import EntitlementError, EntitlementLimitError, check_limit, entitlement_payload, request_entitlements
from services.note_page_setup import (
    PAGE_SETUP_COLORS as NOTES_PAGE_SETUP_COLORS,
    PAGE_SETUP_FONT_TYPES as NOTES_PAGE_SETUP_FONT_TYPES,
    PAGE_SETUP_MARGIN_MAX as NOTES_PAGE_SETUP_MARGIN_MAX,
    PAGE_SETUP_MARGIN_MIN as NOTES_PAGE_SETUP_MARGIN_MIN,
)
from services.onboarding import (
    save_onboarding_step_five,
    save_onboarding_step_four,
    save_onboarding_step_one,
    save_onboarding_step_three,
    save_onboarding_step_two,
)

settings_bp = Blueprint("settings", __name__)
logger = logging.getLogger(__name__)

CANVAS_CALENDAR_HOST_PREFIX = "canvas."
CANVAS_CALENDAR_HOST_SUFFIX = ".edu"
CANVAS_CALENDAR_PATH_PREFIXES = ("/feeds/calendar", "/feeds/calendars")
ALLOWED_AVATAR_EXTENSIONS = {"jpg", "jpeg", "png", "gif", "webp"}
USERNAME_RESERVED = {
    "account",
    "admin",
    "api",
    "auth",
    "calendar",
    "dashboard",
    "data",
    "files",
    "login",
    "logout",
    "notes",
    "onboarding",
    "preferences",
    "profile",
    "settings",
    "signup",
    "u",
    "user",
    "users",
}

THEME_TO_INTERFACE_THEME = {
    "dark": "obsidian-dark",
    "light": "parchment-light",
    "system": "system-match",
}
INTERFACE_THEME_TO_THEME = {
    "obsidian-dark": "dark",
    "nest-dark": "dark",
    "parchment-light": "light",
    "nest-light": "light",
    "system-match": "system",
}


def _format_member_since(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value.strftime("%b %d, %Y")
    parsed = value
    if isinstance(value, str) and value.endswith("Z"):
        parsed = value[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(parsed).strftime("%b %d, %Y")
    except (TypeError, ValueError):
        return str(value)


def _normalize_banner_color(value):
    if not isinstance(value, str):
        return DEFAULT_BANNER_COLOR
    normalized = value.strip()
    if not normalized.startswith("#"):
        normalized = f"#{normalized}"
    if re.fullmatch(r"#[0-9a-fA-F]{6}", normalized):
        return normalized.lower()
    return DEFAULT_BANNER_COLOR


def _normalize_avatar_source(value, picture_url=None):
    if not isinstance(value, str):
        return "url" if picture_url else None
    normalized = value.strip().lower()
    if normalized in {"url", "upload", "provider"}:
        return normalized
    return "url" if picture_url else None


def _avatar_view_url(file_id):
    return build_avatar_view_url(file_id)


def _delete_avatar_file(file_id):
    delete_avatar_file(file_id)


def _normalize_theme_value(value):
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    return normalized if normalized in THEME_TO_INTERFACE_THEME else None


def _interface_theme_from_value(value):
    if not isinstance(value, str):
        return THEME_TO_INTERFACE_THEME["dark"]
    normalized = value.strip().lower()
    if normalized in THEME_TO_INTERFACE_THEME:
        return THEME_TO_INTERFACE_THEME[normalized]
    return normalized if normalized in INTERFACE_THEME_TO_THEME else THEME_TO_INTERFACE_THEME["dark"]


def _theme_from_interface_theme(value):
    if not isinstance(value, str):
        return "dark"
    normalized = value.strip().lower()
    return INTERFACE_THEME_TO_THEME.get(normalized, "dark")


def _normalize_notes_page_setup(value):
    if not isinstance(value, dict):
        return {}

    normalized = {}
    page_color = value.get("pageColor")
    font_type = value.get("fontType")
    side_margins = value.get("sideMargins")

    if isinstance(page_color, str) and page_color in NOTES_PAGE_SETUP_COLORS:
        normalized["pageColor"] = page_color
    if isinstance(font_type, str) and font_type in NOTES_PAGE_SETUP_FONT_TYPES:
        normalized["fontType"] = font_type
    if side_margins is not None:
        try:
            margin_value = round(float(side_margins), 1)
        except (TypeError, ValueError):
            margin_value = None
        if margin_value is not None:
            normalized["sideMargins"] = min(
                NOTES_PAGE_SETUP_MARGIN_MAX,
                max(NOTES_PAGE_SETUP_MARGIN_MIN, margin_value),
            )

    return normalized


def _parse_notes_page_setup(value):
    if not value:
        return {}
    if isinstance(value, dict):
        return _normalize_notes_page_setup(value)
    if not isinstance(value, str):
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return _normalize_notes_page_setup(parsed)


def _normalize_sidebar_default(value):
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    if normalized in {"expanded", "collapsed"}:
        return normalized
    return None


def _normalize_timezone(value):
    if not isinstance(value, str):
        return ""
    return value.strip()


def _normalize_username(value):
    if not isinstance(value, str):
        return ""
    return value.strip().lower()


def _validate_username(value):
    normalized = _normalize_username(value)
    if not normalized:
        raise ValueError("Username is required.")
    if normalized in USERNAME_RESERVED:
        raise ValueError("That username is reserved.")
    if not USERNAME_PATTERN.fullmatch(normalized):
        raise ValueError("Please only use numbers, letters, dashes -, or underscores _." )
    if len(normalized) < USERNAME_MIN_LENGTH or len(normalized) > USERNAME_MAX_LENGTH:
        raise ValueError("Username must be between 3 and 20 characters.")
    return normalized


def _username_is_taken(username, user_id):
    if not username:
        return False
    existing = first_row(
        COLLECTIONS["users"],
        [Query.equal("username", [username])],
    )
    if not existing:
        return False
    existing_id = existing.get("$id") or existing.get("id")
    return str(existing_id) != str(user_id)


def _normalize_bool(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
    return default


def _profile_doc_payload():
    return {
        "id": str(current_user.id),
        "name": current_user.name,
        "username": current_user.username,
        "email": current_user.email,
        "picture_url": current_user.picture_url,
        "banner_color": _normalize_banner_color(current_user.banner_color),
        "avatar_file_id": current_user.avatar_file_id,
        "avatar_source": current_user.avatar_source,
        "school": current_user.school,
        "school_key": getattr(current_user, "school_key", None),
        "school_source": getattr(current_user, "school_source", None),
        "scorecard_id": getattr(current_user, "scorecard_id", None),
        "major": current_user.major,
        "graduation_year": current_user.graduation_year,
        "education_level": current_user.education_level,
        "class_year": current_user.class_year,
        "created_at": format_datetime(current_user.created_at),
        "member_since": _format_member_since(current_user.created_at),
        "tier": getattr(current_user, "tier", "free"),
    }


def _settings_defaults(user_id):
    return {
        "user_id": user_id,
        "ics_secret_token": secrets.token_urlsafe(32),
        "feed_refresh_minutes": 15,
        "preferred_calendar_view": "week",
        "interface_theme": "obsidian-dark",
        "theme": "dark",
        "sidebar_default": "expanded",
        "email_notifications": True,
        "product_updates": True,
        "task_sound_enabled": True,
        "chat_sound_enabled": True,
        "language": "en",
        "timezone": "",
        "dashboard_layout_json": "[]",
        "dashboard_checklist_hidden_signature": "",
        "notes_page_setup_json": "{}",
        "created_at": format_datetime(datetime.utcnow()),
    }


def _settings_payload(settings):
    if not settings:
        return {
            "theme": "dark",
            "sidebar_default": "expanded",
            "email_notifications": True,
            "product_updates": True,
            "task_sound_enabled": True,
            "chat_sound_enabled": True,
            "language": "en",
            "timezone": "",
            "interface_theme": "obsidian-dark",
            "preferred_calendar_view": "week",
            "feed_refresh_minutes": 15,
            "canvas_ical_url": "",
            "other_calendar_urls": [],
            "dashboard_layout_json": "[]",
            "dashboard_checklist_hidden_signature": "",
            "notes_page_setup": {},
        }
    return {
        "theme": _theme_from_interface_theme(settings.get("interface_theme") or settings.get("theme")),
        "sidebar_default": (settings.get("sidebar_default") or "expanded").strip().lower(),
        "email_notifications": bool(settings.get("email_notifications", True)),
        "product_updates": bool(settings.get("product_updates", True)),
        "task_sound_enabled": bool(settings.get("task_sound_enabled", True)),
        "chat_sound_enabled": bool(settings.get("chat_sound_enabled", True)),
        "language": settings.get("language") or "en",
        "timezone": settings.get("timezone") or "",
        "interface_theme": settings.get("interface_theme") or _interface_theme_from_value(settings.get("theme") or "dark"),
        "preferred_calendar_view": settings.get("preferred_calendar_view") or "week",
        "feed_refresh_minutes": settings.get("feed_refresh_minutes") or 15,
        "canvas_ical_url": settings.get("canvas_ical_url") or "",
        "other_calendar_urls": _load_other_calendar_urls(settings),
        "dashboard_layout_json": settings.get("dashboard_layout_json") or "[]",
        "dashboard_checklist_hidden_signature": settings.get("dashboard_checklist_hidden_signature") or "",
        "notes_page_setup": _parse_notes_page_setup(settings.get("notes_page_setup_json")),
    }


def _load_user_settings(user_id):
    try:
        settings = first_row(
            COLLECTIONS["user_settings"],
            [Query.equal("user_id", [user_id])],
        )
    except AppwriteException:
        logger.exception("Failed to load user settings")
        return None
    return settings


def _load_user_courses(user_id):
    try:
        return list_rows_all(
            COLLECTIONS["user_courses"],
            [
                Query.equal("user_id", [user_id]),
                Query.order_asc("term"),
                Query.order_asc("subject"),
                Query.order_asc("catalog"),
            ],
        )
    except AppwriteException:
        logger.exception("Failed to load user courses")
        return []


def _storage_usage_bytes(user_id):
    return _storage_summary(user_id).get("storage_usage_bytes", 0)


def _storage_summary(user_id):
    try:
        files = list_rows_all(
            COLLECTIONS["shared_files"],
            [Query.equal("user_id", [user_id])],
        )
    except AppwriteException:
        logger.exception("Failed to calculate storage usage")
        files = []

    try:
        notes = list_rows_all(
            COLLECTIONS["notes"],
            [Query.equal("user_id", [user_id])],
        )
    except AppwriteException:
        logger.exception("Failed to count user notes")
        notes = []

    try:
        note_media = list_rows_all(
            COLLECTIONS["note_media"],
            [Query.equal("user_id", [user_id])],
        )
    except AppwriteException:
        logger.exception("Failed to count note media storage")
        note_media = []

    try:
        user_doc = get_row_safe(COLLECTIONS["users"], user_id, allow_missing=True) or {}
    except AppwriteException:
        logger.exception("Failed to count avatar storage")
        user_doc = {}

    total = 0
    for file_row in files:
        try:
            total += int(file_row.get("file_size_bytes") or 0)
        except (TypeError, ValueError):
            continue
    for media_row in note_media:
        try:
            total += int(media_row.get("file_size_bytes") or 0)
        except (TypeError, ValueError):
            continue
    try:
        total += int(user_doc.get("avatar_file_size_bytes") or 0)
    except (TypeError, ValueError):
        pass
    return {
        "storage_usage_bytes": total,
        "files_count": len(files),
        "notes_count": len(notes),
        "note_media_count": len(note_media),
    }


@settings_bp.route("/api/account/delete", methods=["POST"])
@login_required
def delete_account():
    user_id = str(current_user.id)

    result = delete_user_data(user_id)
    errors = result if isinstance(result, list) else []
    if errors:
        logger.error("Incomplete account data deletion for %s: %s", user_id, errors)
        return jsonify({"error": "Unable to delete all account data."}), 500

    try:
        Users(appwrite_client).delete(user_id)
    except Exception:
        logger.exception("Failed to delete Appwrite auth account")
        return jsonify({"error": "Unable to delete account."}), 500

    return jsonify({"status": "ok"})


def _normalize_canvas_calendar_url(url):
    """Return a normalized Canvas calendar URL, or None if invalid."""
    if not isinstance(url, str):
        return None

    raw = url.strip()
    if not raw:
        return None

    if "://" not in raw:
        raw = f"https://{raw}"

    parsed = urlparse(raw)
    if parsed.scheme.lower() != "https":
        return None

    host = parsed.netloc.lower()
    if not (host.startswith(CANVAS_CALENDAR_HOST_PREFIX) and host.endswith(CANVAS_CALENDAR_HOST_SUFFIX)):
        return None

    path = parsed.path or ""
    if not path.startswith(CANVAS_CALENDAR_PATH_PREFIXES):
        return None

    normalized_path = path.rstrip("/")
    return urlunparse((
        "https",
        host,
        normalized_path,
        "",
        parsed.query,
        "",
    ))


def _validate_other_calendar_urls(other_urls, canvas_url):
    """Validate optional external calendar links and prevent duplicates."""
    if other_urls is None:
        return []
    if not isinstance(other_urls, list):
        raise ValueError("other_ical_urls must be a list.")

    cleaned = []
    seen = set()
    normalized_canvas = _normalize_calendar_url(canvas_url)

    for raw in other_urls:
        if not isinstance(raw, str):
            raise ValueError("Each calendar URL must be a string.")

        value = raw.strip()
        if not value:
            continue

        normalized = _normalize_calendar_url(value)
        if not normalized:
            raise ValueError(
                "Each optional calendar link must be a valid http(s) or webcal URL."
            )

        if normalized_canvas and normalized == normalized_canvas:
            raise ValueError("Optional calendar links cannot duplicate the Nest Canvas calendar.")

        if normalized in seen:
            raise ValueError("Duplicate optional calendar links are not allowed.")

        seen.add(normalized)
        cleaned.append(normalized)

    if len(cleaned) > MAX_OTHER_CALENDAR_URLS:
        raise ValueError(f"You can add up to {MAX_OTHER_CALENDAR_URLS} optional calendar links.")

    return cleaned


EDUCATION_LEVELS = {
    "High School",
    "Undergraduate",
    "Graduate",
    "Other",
}


def _normalize_education_level(value):
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized if normalized in EDUCATION_LEVELS else None


def _normalize_emory_student(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"yes", "true", "1"}:
            return True
        if normalized in {"no", "false", "0"}:
            return False
    return None


def _normalize_emory_email(value):
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    if not normalized:
        return None
    if not normalized.endswith("@emory.edu"):
        raise ValueError("Please enter a valid @emory.edu email address.")
    return normalized


def _onboarding_courses():
    try:
        return list_rows_all(
            COLLECTIONS["user_courses"],
            [
                Query.equal("user_id", [str(current_user.id)]),
                Query.equal("source", ["onboarding"]),
                Query.order_asc("added_at"),
            ],
        )
    except AppwriteException:
        logger.exception("Failed to load onboarding courses")
        return []


def _onboarding_context():
    user_settings = _load_user_settings(str(current_user.id))
    return {
        "user": {
            "name": current_user.name,
            "username": current_user.username,
            "email": current_user.email,
            "picture": current_user.picture_url,
        },
        "first_name": (current_user.name or current_user.email or "Student").split()[0],
        "step": current_user.onboarding_step or 1,
        "education_level": current_user.education_level,
        "class_year": current_user.class_year,
        "emory_student": current_user.emory_student,
        "emory_email": current_user.emory_email,
        "school": current_user.school,
        "courses": [
            {
                "id": course.get("$id"),
                "course_code": f"{course.get('subject')} {course.get('catalog')}",
                "course_name": course.get("course_name"),
                "section_number": course.get("section_number"),
                "instructor_name": course.get("instructor_name"),
                "term": course.get("term"),
            }
            for course in _onboarding_courses()
        ],
        "default_term": DEFAULT_TERM,
        "settings": user_settings,
        "other_calendar_urls": _load_other_calendar_urls(user_settings),
        "theme_preference": user_settings.get("interface_theme") if user_settings else None,
    }


# ── Page routes ───────────────────────────────────────────────────────────────

@settings_bp.route("/onboarding")
@login_required
def onboarding():
    """
    First-login onboarding page.
    Prompts the user to paste their Canvas iCal feed URL.
    Redirected here from auth.py if no feed URL is configured.
    """
    if current_user.onboarding_complete:
        return redirect(url_for("dashboard.dashboard"))

    return render_template("onboarding.html", **_onboarding_context())


@settings_bp.route("/onboarding", methods=["POST"])
@login_required
def save_onboarding():
    """Persist each onboarding step and advance the user's progress."""
    payload = request.get_json(silent=True) or request.form.to_dict(flat=True)
    step = int(payload.get("step", current_user.onboarding_step or 1))
    action = payload.get("action", "continue")
    user_id = str(current_user.id)
    dependencies = {
        "AppwriteException": AppwriteException,
        "EntitlementError": EntitlementError,
        "EntitlementLimitError": EntitlementLimitError,
        "ID": ID,
        "Query": Query,
        "check_limit": check_limit,
        "collections": COLLECTIONS,
        "create_row_safe": create_row_safe,
        "datetime": datetime,
        "default_term": DEFAULT_TERM,
        "emit_creation_event": emit_creation_event,
        "emit_user_event": emit_user_event,
        "format_actor": format_actor,
        "format_datetime": format_datetime,
        "invites": invites,
        "jsonify": jsonify,
        "list_rows_all": list_rows_all,
        "logger": logger,
        "normalize_education_level": _normalize_education_level,
        "normalize_emory_email": _normalize_emory_email,
        "normalize_emory_student": _normalize_emory_student,
        "request_entitlements": request_entitlements,
        "school_payload": school_payload,
        "sync_chat_presence_labels_for_user": sync_chat_presence_labels_for_user,
        "update_row_safe": update_row_safe,
        "url_for": url_for,
        "username_is_taken": _username_is_taken,
        "validate_username": _validate_username,
    }

    if step == 1:
        return save_onboarding_step_one(payload, current_user, user_id, dependencies)
    if step == 2:
        return save_onboarding_step_two(payload, current_user, user_id, dependencies)
    if step == 3:
        response = save_onboarding_step_three(
            payload,
            action,
            current_user,
            user_id,
            dependencies,
        )
        if response is not None:
            return response
    if step == 4:
        return save_onboarding_step_four(current_user, user_id, dependencies)
    if step == 5:
        return save_onboarding_step_five(current_user, user_id, dependencies)

    return jsonify({"error": "Invalid onboarding step."}), 400


@settings_bp.route("/")
@login_required
def settings_page():
    """Render the settings page with current user configuration."""
    if not current_user.onboarding_complete:
        return redirect(url_for("settings.onboarding"))

    if not current_user.created_at:
        try:
            update_row_safe(
                COLLECTIONS["users"],
                str(current_user.id),
                {"created_at": format_datetime(datetime.utcnow())},
            )
        except AppwriteException:
            logger.exception("Failed to set created_at for user %s", current_user.id)
        current_user.created_at = datetime.utcnow()

    user_settings = _load_user_settings(str(current_user.id))

    return render_template("settings.html", user={
        "name": current_user.name or current_user.email,
        "username": current_user.username,
        "email": current_user.email,
        "picture": current_user.picture_url,
        "banner_color": _normalize_banner_color(current_user.banner_color),
        "avatar_source": current_user.avatar_source,
        "emory_student": current_user.emory_student,
        "school": current_user.school,
        "school_key": current_user.school_key,
        "school_source": current_user.school_source,
        "scorecard_id": current_user.scorecard_id,
        "major": current_user.major,
        "graduation_year": current_user.graduation_year,
        "education_level": current_user.education_level,
        "class_year": current_user.class_year,
        "member_since": _format_member_since(current_user.created_at),
        "tier": getattr(current_user, "tier", "free"),
    }, settings=user_settings, theme_preference=(user_settings.get("interface_theme") if user_settings and user_settings.get("interface_theme") else "obsidian-dark"))


@settings_bp.route("/api/bootstrap", methods=["GET"])
@login_required
def bootstrap_settings():
    user_id = str(current_user.id)
    user_settings = _load_user_settings(user_id)
    user_settings_payload = _settings_payload(user_settings)
    storage_summary = _storage_summary(user_id)
    try:
        tier_payload = entitlement_payload(user_id, getattr(current_user, "_data", None))
    except EntitlementError:
        logger.exception("Failed to load settings tier payload")
        return jsonify({"error": "Unable to load account limits right now.", "code": "tier_check_unavailable"}), 503
    return jsonify({
        "profile": _profile_doc_payload(),
        "settings": user_settings_payload,
        "user_settings_doc": user_settings,
        "storage_usage_bytes": storage_summary.get("storage_usage_bytes", 0),
        "files_count": storage_summary.get("files_count", 0),
        "notes_count": storage_summary.get("notes_count", 0),
        "note_media_count": storage_summary.get("note_media_count", 0),
        "entitlements": tier_payload,
        "connected_services": [],
        "other_calendar_urls": _load_other_calendar_urls(user_settings),
        "member_since": _format_member_since(current_user.created_at),
        "discord": {
            "linked": bool(getattr(current_user, "discord_id", None)),
            "username": getattr(current_user, "discord_username", None),
        },
    })


# ── API routes (called by settings page JavaScript) ──────────────────────────

@settings_bp.route("/api/discord/unlink", methods=["POST"])
@login_required
def unlink_discord():
    """Unlink the current user's Discord account and revoke the guild role."""
    discord_id = getattr(current_user, "discord_id", None)
    if discord_id:
        try:
            discord_bridge.remove_guild_member_role(discord_id)
        except Exception:
            logger.exception("Failed to remove Discord role during unlink for %s", discord_id)

    try:
        update_row_safe(
            COLLECTIONS["users"],
            str(current_user.id),
            {
                "discord_id": None,
                "discord_username": None,
                "discord_linked_at": None,
            },
        )
    except AppwriteException:
        logger.exception("Failed to clear Discord link fields for user %s", current_user.id)
        return jsonify({"error": "Unable to unlink Discord account."}), 500

    current_user.discord_id = None
    current_user.discord_username = None
    current_user.discord_linked_at = None
    return jsonify({"status": "ok"})


@settings_bp.route("/api/profile", methods=["POST"])
@login_required
def update_profile():
    """Update editable profile fields for the authenticated user."""
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    username_value = data.get("username")
    picture_url = (data.get("picture_url") or "").strip() or None
    avatar_source = _normalize_avatar_source(data.get("avatar_source"), picture_url)
    banner_color = _normalize_banner_color(data.get("banner_color"))
    school_updates = school_payload(data.get("school"))
    major = (data.get("major") or "").strip() or None
    graduation_year = (data.get("graduation_year") or "").strip() or None

    if not name:
        return jsonify({"error": "Name is required."}), 400

    if username_value is not None:
        try:
            username = _validate_username(username_value)
        except ValueError as error:
            return jsonify({"error": str(error)}), 400
        if _username_is_taken(username, str(current_user.id)):
            return jsonify({"error": "That username is already taken."}), 409
    else:
        username = current_user.username

    if graduation_year and (len(graduation_year) != 4 or not graduation_year.isdigit()):
        return jsonify({"error": "Graduation year must be a 4-digit year."}), 400

    old_avatar_file_id = current_user.avatar_file_id
    avatar_file_id = old_avatar_file_id if avatar_source == "upload" else None
    should_delete_uploaded_avatar = (
        current_user.avatar_source == "upload"
        and current_user.avatar_file_id
        and (avatar_source != "upload" or picture_url != current_user.picture_url)
    )

    updates = {
        "name": name,
        "username": username,
        "picture_url": picture_url,
        "banner_color": banner_color,
        "avatar_file_id": avatar_file_id,
        "avatar_source": avatar_source,
        "avatar_file_size_bytes": (
            getattr(current_user, "avatar_file_size_bytes", 0)
            if avatar_source == "upload" else 0
        ),
        **school_updates,
        "major": major,
        "graduation_year": graduation_year,
    }
    if not current_user.created_at:
        updates["created_at"] = format_datetime(datetime.utcnow())

    try:
        update_row_safe(
            COLLECTIONS["users"],
            str(current_user.id),
            updates,
        )
    except AppwriteException:
        logger.exception("Failed to update profile")
        return jsonify({"error": "Unable to update profile."}), 500

    current_user.name = name
    current_user.username = username
    current_user.picture_url = picture_url
    current_user.banner_color = banner_color
    current_user.avatar_file_id = avatar_file_id
    current_user.avatar_source = avatar_source
    current_user.avatar_file_size_bytes = updates["avatar_file_size_bytes"]
    current_user.school = school_updates.get("school")
    current_user.school_key = school_updates.get("school_key")
    current_user.school_source = school_updates.get("school_source")
    current_user.scorecard_id = school_updates.get("scorecard_id")
    current_user.major = major
    current_user.graduation_year = graduation_year
    if updates.get("created_at"):
        current_user.created_at = datetime.utcnow()
    if should_delete_uploaded_avatar:
        _delete_avatar_file(old_avatar_file_id)
    sync_chat_presence_labels_for_user(str(current_user.id))

    emit_creation_event(
        "Profile Configuration Updated",
        actor=format_actor(current_user),
        target=str(current_user.id),
        metadata={
            "page_context": "settings/profile",
            "resource_type": "user_profile",
            "resource_id": str(current_user.id),
            "username": current_user.username,
            "school": current_user.school,
            "avatar_source": current_user.avatar_source,
            "banner_color": current_user.banner_color,
        },
        color="gray",
    )
    return jsonify({
        "status": "ok",
        "name": current_user.name,
        "username": current_user.username,
        "picture_url": current_user.picture_url,
        "banner_color": current_user.banner_color,
        "avatar_file_id": current_user.avatar_file_id,
        "avatar_source": current_user.avatar_source,
        "school": current_user.school,
        "school_key": current_user.school_key,
        "school_source": current_user.school_source,
        "scorecard_id": current_user.scorecard_id,
        "major": current_user.major,
        "graduation_year": current_user.graduation_year,
        "education_level": current_user.education_level,
        "class_year": current_user.class_year,
        "created_at": format_datetime(current_user.created_at),
        "member_since": _format_member_since(current_user.created_at),
        "tier": getattr(current_user, "tier", "free"),
    })


@settings_bp.route("/api/avatar-upload", methods=["POST"])
@login_required
def upload_avatar():
    """Upload and persist a profile avatar in Appwrite Storage."""
    uploaded_file = request.files.get("avatar")
    if not uploaded_file or not uploaded_file.filename:
        return jsonify({"error": "Choose an image to upload."}), 400

    original_filename = uploaded_file.filename
    extension = original_filename.rsplit(".", 1)[-1].lower() if "." in original_filename else ""
    if extension not in ALLOWED_AVATAR_EXTENSIONS:
        return jsonify({"error": "Avatar must be a JPG, PNG, GIF, or WebP image."}), 400
    if uploaded_file.mimetype not in ALLOWED_AVATAR_MIME_TYPES:
        return jsonify({"error": "Avatar file type is not supported."}), 400

    file_bytes = uploaded_file.read()
    if not file_bytes:
        return jsonify({"error": "Avatar file is empty."}), 400
    if len(file_bytes) > MAX_AVATAR_BYTES:
        return jsonify({"error": "Avatar must be 10 MB or smaller."}), 400

    try:
        entitlements = request_entitlements(current_user)
        storage_limit = entitlements["limits"].get("storage_bytes")
        old_size = int(getattr(current_user, "avatar_file_size_bytes", 0) or 0) if current_user.avatar_source == "upload" else 0
        if storage_limit is not None:
            current_usage = int(entitlements["usage"].get("storage_bytes") or 0) - old_size
            if current_usage + len(file_bytes) > storage_limit:
                raise EntitlementLimitError("storage bytes", current_usage, current_usage + len(file_bytes), storage_limit)
    except EntitlementLimitError as exc:
        return jsonify(exc.payload()), 403
    except EntitlementError:
        logger.exception("Failed to verify avatar storage limits")
        return jsonify({"error": "Unable to verify your storage limits right now.", "code": "tier_check_unavailable"}), 503

    file_id = ID.unique()
    stored_filename = f"{current_user.id}-{file_id}.{extension}"
    input_file = InputFile.from_bytes(
        file_bytes,
        stored_filename,
        mime_type=uploaded_file.mimetype,
    )

    try:
        Storage(appwrite_client).create_file(
            PROFILE_AVATAR_BUCKET_ID,
            file_id,
            input_file,
            permissions=[Permission.read(Role.any())],
        )
    except AppwriteException:
        logger.exception("Failed to upload avatar")
        return jsonify({"error": "Unable to upload avatar."}), 500

    picture_url = _avatar_view_url(file_id)
    if not picture_url:
        _delete_avatar_file(file_id)
        return jsonify({"error": "Avatar storage is not configured."}), 500

    old_file_id = current_user.avatar_file_id if current_user.avatar_source == "upload" else None
    try:
        update_row_safe(
            COLLECTIONS["users"],
            str(current_user.id),
            {
                "picture_url": picture_url,
                "avatar_file_id": file_id,
                "avatar_source": "upload",
                "avatar_file_size_bytes": len(file_bytes),
            },
        )
    except AppwriteException:
        _delete_avatar_file(file_id)
        logger.exception("Failed to save avatar metadata")
        return jsonify({"error": "Unable to save avatar."}), 500

    if old_file_id and old_file_id != file_id:
        _delete_avatar_file(old_file_id)

    current_user.picture_url = picture_url
    current_user.avatar_file_id = file_id
    current_user.avatar_source = "upload"
    current_user.avatar_file_size_bytes = len(file_bytes)

    emit_creation_event(
        "Profile Avatar Uploaded",
        actor=format_actor(current_user),
        target=str(current_user.id),
        metadata={
            "page_context": "settings/avatar",
            "resource_type": "profile_avatar",
            "resource_id": file_id,
            "mime_type": uploaded_file.mimetype,
            "size_bytes": len(file_bytes),
        },
        color="green",
    )
    return jsonify({
        "status": "ok",
        "picture_url": picture_url,
        "avatar_file_id": file_id,
        "avatar_source": "upload",
    })

@settings_bp.route("/api/feed-url", methods=["POST"])
@login_required
def update_feed_url():
    """
    POST /settings/api/feed-url
        Body: {
            "canvas_ical_url": "https://canvas.nest.edu/feeds/calendars/...",
            "other_ical_urls": ["https://calendar.google.com/...", "webcal://..."]
        }

    Saves or updates the user's Canvas iCal feed URL.
    Used by both the onboarding page and the settings page.
    """
    data = request.get_json(silent=True) or {}
    if "canvas_ical_url" not in data and "other_ical_urls" not in data:
        return jsonify({"error": "Missing calendar payload"}), 400

    raw_url = (data.get("canvas_ical_url") or "").strip()
    url = None
    if raw_url:
        url = _normalize_canvas_calendar_url(raw_url)
        if not url:
            return jsonify({
                "error": "Canvas calendar must use https://canvas.<school>.edu/feeds/calendar..."
            }), 400

    try:
        if "other_ical_urls" in data:
            other_ical_urls = _validate_other_calendar_urls(
                data.get("other_ical_urls"),
                url,
            )
        else:
            try:
                existing = first_row(
                    COLLECTIONS["user_settings"],
                    [Query.equal("user_id", [str(current_user.id)])],
                )
            except AppwriteException:
                logger.exception("Failed to load settings")
                return jsonify({"error": "Unable to save feed URL."}), 500
            other_ical_urls = _load_other_calendar_urls(existing)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    try:
        entitlements = request_entitlements(current_user)
        feed_limit = entitlements["limits"].get("max_calendar_feeds")
        feed_count = (1 if url else 0) + len(other_ical_urls)
        if feed_limit is not None and feed_count > feed_limit:
            raise EntitlementLimitError("calendar feeds", entitlements["usage"]["calendar_feeds"], feed_count, feed_limit)
    except EntitlementLimitError as exc:
        return jsonify(exc.payload()), 403
    except EntitlementError:
        logger.exception("Failed to verify calendar feed limits")
        return jsonify({"error": "Unable to verify your calendar limits right now.", "code": "tier_check_unavailable"}), 503

    user_id = str(current_user.id)
    try:
        settings = first_row(
            COLLECTIONS["user_settings"],
            [Query.equal("user_id", [user_id])],
        )
    except AppwriteException:
        logger.exception("Failed to load settings")
        return jsonify({"error": "Unable to save feed URL."}), 500

    payload = {
        "canvas_ical_url": url,
        "other_ical_urls_json": json.dumps(other_ical_urls),
        "updated_at": format_datetime(datetime.utcnow()),
    }

    try:
        if not settings:
            settings = create_row_safe(COLLECTIONS["user_settings"], row_id=user_id, data={**_settings_defaults(user_id), **payload})
        else:
            settings = update_row_safe(
                COLLECTIONS["user_settings"],
                settings.get("$id"),
                payload,
            )
    except AppwriteException:
        logger.exception("Failed to save feed URL")
        return jsonify({"error": "Unable to save feed URL."}), 500

    if url or other_ical_urls:
        try:
            invites.record_activation(user_id, "calendar")
        except Exception:
            logger.exception("Failed to record invite activation for calendar feed")

    refresh_error = None
    refresh_count = 0
    feed_urls = []
    if url:
        feed_urls.append(url)
    feed_urls.extend(other_ical_urls)
    if feed_urls:
        try:
            from services.feed_fetcher import fetch_and_cache_feeds

            refresh_count = fetch_and_cache_feeds(user_id, feed_urls)
        except Exception as exc:
            logger.exception(
                "Failed to refresh calendar feeds after settings save",
                extra={"user_id": user_id, "feed_count": len(feed_urls)},
            )
            refresh_error = str(exc)

    emit_creation_event(
        "Calendar Feed Configuration Updated",
        actor=format_actor(current_user),
        target=str(current_user.id),
        metadata={
            "page_context": "settings/feed-url",
            "resource_type": "user_settings",
            "resource_id": settings.get("$id") or settings.get("id"),
            "canvas_feed_configured": bool(url),
            "other_calendar_count": len(other_ical_urls),
            "refresh_count": refresh_count,
            "refresh_error": refresh_error,
        },
        color="gray" if not refresh_error else "yellow",
    )
    return jsonify({
        "status": "ok",
        "message": "Feed URL saved.",
        "canvas_ical_url": url or "",
        "other_ical_urls": other_ical_urls,
        "refresh_count": refresh_count,
        "refresh_error": refresh_error,
    })


@settings_bp.route("/api/refresh-interval", methods=["POST"])
@login_required
def update_refresh_interval():
    """
    POST /settings/api/refresh-interval
    Body: { "minutes": 15 }

    Updates how frequently the user's Canvas feed is re-fetched.
    """
    data = request.get_json()
    minutes = data.get("minutes") if data else None

    if not isinstance(minutes, int) or minutes < 5 or minutes > 1440:
        return jsonify({
            "error": "Refresh interval must be between 5 and 1440 minutes."
        }), 400

    try:
        settings = first_row(
            COLLECTIONS["user_settings"],
            [Query.equal("user_id", [str(current_user.id)])],
        )
    except AppwriteException:
        logger.exception("Failed to load settings")
        return jsonify({"error": "Unable to update refresh interval."}), 500
    if not settings:
        return jsonify({"error": "No settings found. Complete onboarding first."}), 404

    try:
        update_row_safe(
            COLLECTIONS["user_settings"],
            settings.get("$id"),
            {
                "feed_refresh_minutes": minutes,
                "updated_at": format_datetime(datetime.utcnow()),
            },
        )
    except AppwriteException:
        logger.exception("Failed to update refresh interval")
        return jsonify({"error": "Unable to update refresh interval."}), 500

    emit_creation_event(
        "Refresh Interval Updated",
        actor=format_actor(current_user),
        target=str(current_user.id),
        metadata={
            "page_context": "settings/refresh-interval",
            "resource_type": "user_settings",
            "resource_id": settings.get("$id") or settings.get("id"),
            "feed_refresh_minutes": minutes,
        },
        color="gray",
    )
    return jsonify({"status": "ok", "refresh_interval_minutes": minutes})


@settings_bp.route("/api/interface-preferences", methods=["POST"])
@login_required
def update_interface_preferences():
    """Persist interface-level preferences such as the default dashboard calendar view."""
    data = request.get_json(silent=True) or {}
    preferred_calendar_view = (data.get("preferred_calendar_view") or "").strip().lower() or None
    theme = _normalize_theme_value(data.get("theme"))
    interface_theme = (data.get("interface_theme") or "").strip() or None
    sidebar_default = _normalize_sidebar_default(data.get("sidebar_default"))
    language = (data.get("language") or "").strip() or None
    timezone = _normalize_timezone(data.get("timezone")) if "timezone" in data else None
    email_notifications = _normalize_bool(data.get("email_notifications"), True) if "email_notifications" in data else None
    product_updates = _normalize_bool(data.get("product_updates"), True) if "product_updates" in data else None
    task_sound_enabled = _normalize_bool(data.get("task_sound_enabled"), True) if "task_sound_enabled" in data else None
    chat_sound_enabled = _normalize_bool(data.get("chat_sound_enabled"), True) if "chat_sound_enabled" in data else None

    valid_interface_themes = {"obsidian-dark", "parchment-light", "system-match", "nest-light", "nest-dark"}
    if preferred_calendar_view and preferred_calendar_view not in {"week", "month"}:
        return jsonify({"error": "Preferred calendar view must be weekly or monthly."}), 400
    if interface_theme and interface_theme not in valid_interface_themes:
        return jsonify({"error": "Interface theme is not valid."}), 400
    if theme is None and interface_theme:
        theme = _theme_from_interface_theme(interface_theme)
    if theme is None:
        theme = None
    if timezone is not None and len(timezone) > 64:
        return jsonify({"error": "Timezone is too long."}), 400

    user_id = str(current_user.id)
    try:
        settings = _load_user_settings(user_id)
    except AppwriteException:
        logger.exception("Failed to load settings")
        return jsonify({"error": "Unable to update preferences."}), 500

    updates = {"updated_at": format_datetime(datetime.utcnow())}
    if preferred_calendar_view:
        updates["preferred_calendar_view"] = preferred_calendar_view
    if interface_theme:
        updates["interface_theme"] = interface_theme
        updates["theme"] = theme or _theme_from_interface_theme(interface_theme)
    elif theme is not None:
        updates["theme"] = theme
        updates["interface_theme"] = _interface_theme_from_value(theme)
    if sidebar_default:
        updates["sidebar_default"] = sidebar_default
    if email_notifications is not None:
        updates["email_notifications"] = email_notifications
    if product_updates is not None:
        updates["product_updates"] = product_updates
    if task_sound_enabled is not None:
        updates["task_sound_enabled"] = task_sound_enabled
    if chat_sound_enabled is not None:
        updates["chat_sound_enabled"] = chat_sound_enabled
    if language:
        updates["language"] = language
    if timezone is not None:
        updates["timezone"] = timezone

    try:
        if not settings:
            settings = create_row_safe(COLLECTIONS["user_settings"], row_id=user_id, data={**_settings_defaults(user_id), **updates})
        else:
            settings = update_row_safe(
                COLLECTIONS["user_settings"],
                settings.get("$id"),
                updates,
            )
    except AppwriteException:
        logger.exception("Failed to update interface preferences")
        return jsonify({"error": "Unable to update preferences."}), 500

    emit_creation_event(
        "Interface Preferences Updated",
        actor=format_actor(current_user),
        target=str(current_user.id),
        metadata={
            "page_context": "settings/interface-preferences",
            "resource_type": "user_settings",
            "resource_id": settings.get("$id") or settings.get("id"),
            "updated_keys": sorted(key for key in updates.keys() if key != "updated_at"),
        },
        color="gray",
    )
    return jsonify({
        "status": "ok",
        "preferred_calendar_view": settings.get("preferred_calendar_view"),
        "interface_theme": settings.get("interface_theme"),
        "theme": settings.get("theme") or _theme_from_interface_theme(settings.get("interface_theme")),
        "sidebar_default": settings.get("sidebar_default") or "expanded",
        "email_notifications": settings.get("email_notifications", True),
        "product_updates": settings.get("product_updates", True),
        "task_sound_enabled": settings.get("task_sound_enabled", True),
        "chat_sound_enabled": settings.get("chat_sound_enabled", True),
        "language": settings.get("language") or "en",
        "timezone": settings.get("timezone") or "",
    })


@settings_bp.route("/api/notes-page-setup", methods=["POST"])
@login_required
def update_notes_page_setup():
    """Persist default page setup values for all note documents."""
    data = request.get_json(silent=True) or {}
    raw_page_setup = data.get("page_setup")
    if isinstance(raw_page_setup, str):
        try:
            raw_page_setup = json.loads(raw_page_setup)
        except (TypeError, ValueError):
            return jsonify({"error": "Page setup must be valid JSON."}), 400

    page_setup = _normalize_notes_page_setup(raw_page_setup)
    user_id = str(current_user.id)

    try:
        settings = _load_user_settings(user_id)
    except AppwriteException:
        logger.exception("Failed to load settings")
        return jsonify({"error": "Unable to update note page setup."}), 500

    updates = {
        "notes_page_setup_json": json.dumps(page_setup, separators=(",", ":")),
        "updated_at": format_datetime(datetime.utcnow()),
    }

    try:
        if not settings:
            settings = create_row_safe(
                COLLECTIONS["user_settings"],
                row_id=user_id,
                data={**_settings_defaults(user_id), **updates},
            )
        else:
            settings = update_row_safe(
                COLLECTIONS["user_settings"],
                settings.get("$id"),
                updates,
            )
    except AppwriteException:
        logger.exception("Failed to update note page setup")
        return jsonify({"error": "Unable to update note page setup."}), 500

    emit_creation_event(
        "Notes Page Setup Updated",
        actor=format_actor(current_user),
        target=str(current_user.id),
        metadata={
            "page_context": "settings/notes-page-setup",
            "resource_type": "user_settings",
            "resource_id": settings.get("$id") or settings.get("id"),
            "updated_keys": ["notes_page_setup_json"],
        },
        color="gray",
    )
    return jsonify({
        "status": "ok",
        "notes_page_setup": _parse_notes_page_setup(settings.get("notes_page_setup_json")),
    })


@settings_bp.route("/api/export", methods=["GET"])
@login_required
def export_user_data():
    user_id = str(current_user.id)
    user_settings = _load_user_settings(user_id)
    storage_summary = _storage_summary(user_id)
    export_payload = {
        "exported_at": format_datetime(datetime.utcnow()),
        "profile": _profile_doc_payload(),
        "settings": _settings_payload(user_settings),
        "courses": _load_user_courses(user_id),
        "other_calendar_urls": _load_other_calendar_urls(user_settings),
        "storage_usage_bytes": storage_summary.get("storage_usage_bytes", 0),
        "files_count": storage_summary.get("files_count", 0),
        "notes_count": storage_summary.get("notes_count", 0),
    }
    return jsonify(export_payload)


@settings_bp.route("/api/account/recovery", methods=["POST"])
@login_required
def request_account_recovery():
    """Send the current user an Appwrite Auth password recovery email."""
    email = (current_user.email or "").strip()
    if not email:
        return jsonify({"error": "Email address is required for password recovery."}), 400

    try:
        Account(appwrite_client).create_recovery(email, url_for("auth.login", _external=True))
    except AppwriteException:
        logger.exception("Failed to start password recovery")
        return jsonify({"error": "Unable to start password recovery."}), 500

    return jsonify({"status": "ok"})


@settings_bp.route("/api/regenerate-token", methods=["POST"])
@login_required
def regenerate_ics_token():
    """
    POST /settings/api/regenerate-token

    Generates a new .ics subscription token. Invalidates the old one,
    so the user must re-subscribe in Apple Calendar with the new URL.
    """
    try:
        settings = first_row(
            COLLECTIONS["user_settings"],
            [Query.equal("user_id", [str(current_user.id)])],
        )
    except AppwriteException:
        logger.exception("Failed to load settings")
        return jsonify({"error": "Unable to regenerate token."}), 500
    if not settings:
        return jsonify({"error": "No settings found."}), 404

    new_token = secrets.token_urlsafe(32)
    try:
        settings = update_row_safe(
            COLLECTIONS["user_settings"],
            settings.get("$id"),
            {
                "ics_secret_token": new_token,
                "updated_at": format_datetime(datetime.utcnow()),
            },
        )
    except AppwriteException:
        logger.exception("Failed to regenerate token")
        return jsonify({"error": "Unable to regenerate token."}), 500

    emit_creation_event(
        "Calendar Token Regenerated",
        actor=format_actor(current_user),
        target=str(current_user.id),
        metadata={
            "page_context": "settings/regenerate-token",
            "resource_type": "user_settings",
            "resource_id": settings.get("$id") or settings.get("id"),
            "token_rotated": True,
        },
        color="yellow",
    )
    return jsonify({
        "status": "ok",
        "message": "Token regenerated. Update your calendar subscription URL.",
        "new_subscription_url": url_for(
            "calendar.ics_feed",
            token=new_token,
            _external=True,
        ),
    })


# ── My Courses ────────────────────────────────────────────────────────────────

@settings_bp.route("/api/courses", methods=["GET"])
@login_required
def list_my_courses():
    """
    GET /settings/api/courses

    Returns the user's saved course selections.
    """
    try:
        courses = list_rows_all(
            COLLECTIONS["user_courses"],
            [
                Query.equal("user_id", [str(current_user.id)]),
                Query.order_asc("term"),
                Query.order_asc("subject"),
                Query.order_asc("catalog"),
            ],
        )
    except AppwriteException:
        logger.exception("Failed to list courses")
        return jsonify({"error": "Unable to load courses."}), 500

    return jsonify({
        "count": len(courses),
        "courses": [
            {
                "id": c.get("$id"),
                "term": c.get("term"),
                "subject": c.get("subject"),
                "catalog": c.get("catalog"),
                "crn": c.get("crn"),
                "course_code": f"{c.get('subject')} {c.get('catalog')}",
            }
            for c in courses
        ],
    })


@settings_bp.route("/api/courses", methods=["POST"])
@login_required
def add_course():
    """
    POST /settings/api/courses
    Body: { "term": "Fall_2026", "subject": "CHEM", "catalog": "150", "crn": "1700" }

    Adds a course to the user's "My Courses" list. CRN is optional.
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "Missing request body"}), 400

    term = data.get("term", "").strip()
    subject = data.get("subject", "").strip().upper()
    catalog = data.get("catalog", "").strip()
    crn = data.get("crn", "").strip() or None

    if not term or not subject or not catalog:
        return jsonify({"error": "term, subject, and catalog are required."}), 400

    # Check for duplicates
    user_id = str(current_user.id)
    try:
        candidates = list_rows_all(
            COLLECTIONS["user_courses"],
            [
                Query.equal("user_id", [user_id]),
                Query.equal("term", [term]),
                Query.equal("subject", [subject]),
                Query.equal("catalog", [catalog]),
            ],
        )
    except AppwriteException:
        logger.exception("Failed to check existing course")
        return jsonify({"error": "Unable to add course."}), 500

    existing = next(
        (doc for doc in candidates if (doc.get("crn") or None) == crn),
        None,
    )
    if existing:
        return jsonify({"error": "Course already in your list."}), 409

    try:
        entitlements = request_entitlements(current_user)
        check_limit(entitlements, "max_saved_courses", entitlements["usage"]["saved_courses"])
    except EntitlementLimitError as exc:
        return jsonify(exc.payload()), 403
    except EntitlementError:
        logger.exception("Failed to verify settings course limits")
        return jsonify({"error": "Unable to verify your course limits right now.", "code": "tier_check_unavailable"}), 503

    try:
        course = create_row_safe(
            COLLECTIONS["user_courses"],
            row_id=ID.unique(),
            data={
                "user_id": user_id,
                "term": term,
                "subject": subject,
                "catalog": catalog,
                "crn": crn,
                "source": "settings",
                "added_at": format_datetime(datetime.utcnow()),
            },
        )
    except AppwriteException:
        logger.exception("Failed to add course")
        return jsonify({"error": "Unable to add course."}), 500

    try:
        invites.record_activation(user_id, "course")
    except Exception:
        logger.exception("Failed to record invite activation for settings course")

    emit_creation_event(
        "Settings Course Added",
        actor=format_actor(current_user),
        target=f"{subject} {catalog}",
        metadata={
            "page_context": "settings/courses",
            "resource_type": "user_course",
            "resource_id": course.get("$id") or course.get("id"),
            "term": term,
            "crn": crn,
        },
        color="green",
    )
    return jsonify({
        "status": "ok",
        "course": {
            "id": course.get("$id"),
            "term": term,
            "subject": subject,
            "catalog": catalog,
            "crn": crn,
            "course_code": f"{subject} {catalog}",
        },
    }), 201


@settings_bp.route("/api/courses/<course_id>", methods=["DELETE"])
@login_required
def remove_course(course_id):
    """
    DELETE /settings/api/courses/42

    Removes a course from the user's "My Courses" list.
    """
    try:
        course = get_row_safe(COLLECTIONS["user_courses"], course_id)
    except AppwriteException as exc:
        if exc.code == 404:
            return jsonify({"error": "Course not found."}), 404
        logger.exception("Failed to load course")
        return jsonify({"error": "Unable to remove course."}), 500

    if course.get("user_id") != str(current_user.id):
        return jsonify({"error": "Course not found."}), 404

    try:
        delete_row_safe(COLLECTIONS["user_courses"], course_id)
    except AppwriteException:
        logger.exception("Failed to delete course")
        return jsonify({"error": "Unable to remove course."}), 500

    return jsonify({"status": "ok", "message": "Course removed."})
