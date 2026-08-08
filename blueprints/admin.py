import io
import logging
import os
import platform
import re
import secrets
import shutil
import subprocess
import threading
from functools import wraps
from datetime import datetime, timezone

from flask import Blueprint, abort, jsonify, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required

from appwrite.exception import AppwriteException
from appwrite.query import Query
from appwrite.services.storage import Storage
from appwrite.services.users import Users
from appwrite_client import COLLECTIONS, FILE_SHARE_BUCKET_ID, PROFILE_AVATAR_BUCKET_ID, client as appwrite_client
from appwrite_helpers import (
    create_row_safe,
    delete_row_safe,
    first_row,
    format_datetime,
    get_row_safe,
    list_rows_all,
    list_rows_safe,
    parse_datetime,
    update_row_safe,
)
from extensions import csrf
from blueprints.settings import _settings_defaults
from blueprints.chat_api import create_university_channel, emit_chat_event
from services.chat_presence import sync_chat_presence_labels_for_school
from services.redaction import SECRET_TEXT_RE
from services.row_utils import row_id as _row_id
from services.toasts import push_toast
from services.user_cleanup import delete_user_data
from services.user_profile import (
    is_early_member as _is_early_member,
    is_emory_school as _is_emory_school,
    normalize_banner_color as _normalize_banner_color,
    profile_handle as _profile_handle,
)
from services.app_config import (
    get_course_tracking_refresh_minutes,
    set_course_tracking_refresh_minutes,
    set_spring_course_tracking_open,
    spring_course_tracking_open,
)
from services.admin_access import admin_user_ids
from services.scheduler import update_course_tracking_refresh_interval
from services.discord_audit import discord_audit_status, emit_admin_event, format_actor, format_user_target
import services.apswiftly_control as apswiftly_control_service
from services.apswiftly_control import APSwiftlyControlError, apswiftly_status
from services.calendar_store import (
    count_calendar_rows,
    delete_calendar_rows_by_user,
    list_calendar_rows_all,
)
from services.admin_analytics import RANGE_OPTIONS, analytics_payload, normalize_range
from services.entitlements import (
    TIER_BADGES,
    TIER_KEYS,
    TIER_LABELS,
    EntitlementError,
    entitlement_payload,
    get_tier_definitions,
    normalize_tier,
    save_tier_definitions,
)
from services import invites

try:
    import psutil
except ImportError:  # pragma: no cover - optional dependency for monitoring
    psutil = None


admin_bp = Blueprint("admin", __name__)
logger = logging.getLogger(__name__)
admin_actions_logger = logging.getLogger("admin_actions")
SCHEDULER_ENV_PATH = "/var/www/nest.apstudy.org/.env"
SCHEDULER_SERVICE_NAME = "nest"
SCHEDULER_COMMAND_TIMEOUT_SECONDS = 20
SYSTEM_GIT_REPO_PATH = "/var/www/nest.apstudy.org"
SYSTEM_GIT_COMMAND_TIMEOUT_SECONDS = 60
SYSTEM_RESTART_DELAY_SECONDS = 2
SYSTEM_RESTART_COMMAND_TIMEOUT_SECONDS = 20
SYSTEM_STORAGE_LIMIT_GB = 150
SCHEDULER_EXECUTABLE_FALLBACKS = {
    "git": ("/usr/bin/git", "/bin/git"),
    "sed": ("/usr/bin/sed", "/bin/sed"),
    "sh": ("/bin/sh", "/usr/bin/sh"),
    "ssh": ("/usr/bin/ssh", "/bin/ssh"),
    "sudo": ("/usr/bin/sudo", "/bin/sudo"),
    "systemctl": ("/usr/bin/systemctl", "/bin/systemctl"),
}

ALLOWED_SECTIONS = {
    "overview",
    "settings",
    "files",
    "notes",
    "calendars",
    "courses",
    "seat_tracks",
    "chat",
    "invites",
}


def _format_admin_date(value):
    parsed = parse_datetime(value)
    if parsed:
        return parsed.strftime("%B %-d, %Y")
    return str(value) if value else None


def _format_admin_datetime(value):
    parsed = parse_datetime(value)
    if parsed:
        return parsed.strftime("%B %-d, %Y %-I:%M %p")
    return str(value) if value else None


def _admin_profile_payload(user_doc):
    user_id = _row_id(user_doc)
    name = user_doc.get("name") or "APStudy User"
    username = user_doc.get("username")
    tier = normalize_tier(user_doc.get("tier"))
    return {
        "id": user_id,
        "name": name,
        "username": username,
        "handle": _profile_handle(name, user_id, username),
        "picture_url": user_doc.get("picture_url"),
        "banner_color": _normalize_banner_color(user_doc.get("banner_color")),
        "school": user_doc.get("school"),
        "major": user_doc.get("major"),
        "graduation_year": user_doc.get("graduation_year"),
        "education_level": user_doc.get("education_level"),
        "class_year": user_doc.get("class_year"),
        "member_since": _format_admin_date(user_doc.get("created_at")),
        "is_emory_school": _is_emory_school(user_doc.get("school")),
        "is_early_member": _is_early_member(user_doc.get("created_at")),
        "tier": tier,
        "tier_label": TIER_LABELS[tier],
        "tier_badge": TIER_BADGES.get(tier),
    }


def _admin_viewer_payload():
    if not current_user.is_authenticated:
        return None
    return {
        "id": str(getattr(current_user, "id", "") or ""),
        "name": getattr(current_user, "name", None) or "Admin",
        "email": getattr(current_user, "email", None),
        "picture_url": getattr(current_user, "picture_url", None),
    }


@admin_bp.context_processor
def _admin_template_context():
    return {"admin_viewer": _admin_viewer_payload()}


def _admin_ids():
    return admin_user_ids()


def _read_os_pretty():
    try:
        if hasattr(platform, "freedesktop_os_release"):
            release = platform.freedesktop_os_release()
            pretty = release.get("PRETTY_NAME")
            if pretty:
                return pretty
    except Exception:
        logger.debug("Failed to read freedesktop OS release metadata", exc_info=True)
    try:
        return platform.platform()
    except Exception:
        return "Unknown"


def _system_status():
    status = {
        "os_pretty": _read_os_pretty(),
        "cpu_percent": None,
        "cpu_logical": None,
        "cpu_physical": None,
        "mem_percent": None,
        "mem_used_gb": None,
        "mem_total_gb": None,
        "storage_percent": None,
        "storage_used_gb": None,
        "storage_total_gb": SYSTEM_STORAGE_LIMIT_GB,
    }
    try:
        from services.scheduler import scheduler_status

        status.update(scheduler_status())
    except Exception:
        logger.exception("Failed to read scheduler status for admin system overview")
    if psutil is None:
        return status
    metrics = {}
    try:
        metrics["cpu_percent"] = round(psutil.cpu_percent(interval=0.1), 1)
    except Exception:
        logger.debug("Failed to read CPU utilization", exc_info=True)
    try:
        metrics["cpu_logical"] = psutil.cpu_count(logical=True)
        metrics["cpu_physical"] = psutil.cpu_count(logical=False)
    except Exception:
        logger.debug("Failed to read CPU counts", exc_info=True)
    try:
        memory = psutil.virtual_memory()
        metrics["mem_percent"] = round(memory.percent, 1)
        metrics["mem_used_gb"] = round(memory.used / (1024**3), 1)
        metrics["mem_total_gb"] = round(memory.total / (1024**3), 1)
    except Exception:
        logger.debug("Failed to read memory utilization", exc_info=True)
    try:
        disk = shutil.disk_usage("/")
        storage_used_gb = disk.used / (1024**3)
        metrics["storage_used_gb"] = round(storage_used_gb, 1)
        metrics["storage_percent"] = round((storage_used_gb / SYSTEM_STORAGE_LIMIT_GB) * 100, 1)
    except Exception:
        logger.debug("Failed to read disk utilization", exc_info=True)
    status.update(metrics)
    return status


def _sanitize_admin_error(error):
    text = SECRET_TEXT_RE.sub(r"\1[redacted]", str(error or ""))
    return " ".join(text.split())[:500]


def _resolve_scheduler_executable(name):
    found = shutil.which(name)
    if found:
        return found
    for candidate in SCHEDULER_EXECUTABLE_FALLBACKS.get(name, ()):
        if os.path.exists(candidate) and os.access(candidate, os.X_OK):
            return candidate
    raise FileNotFoundError(f"Required scheduler command not found: {name}")


def _scheduler_command_for_action(action):
    if action == "pause":
        replacement = "s/SCHEDULER_ENABLED=1/SCHEDULER_ENABLED=0/g"
    elif action == "resume":
        replacement = "s/SCHEDULER_ENABLED=0/SCHEDULER_ENABLED=1/g"
    else:
        raise ValueError("Unsupported scheduler action.")
    return [
        [_resolve_scheduler_executable("sed"), "-i", replacement, SCHEDULER_ENV_PATH],
        [_resolve_scheduler_executable("systemctl"), "restart", SCHEDULER_SERVICE_NAME],
    ]


def _run_scheduler_control_action(action):
    commands = _scheduler_command_for_action(action)
    completed = []
    for command in commands:
        subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=SCHEDULER_COMMAND_TIMEOUT_SECONDS,
        )
        completed.append(command[0])
    return completed


def _run_system_git_pull():
    git_env = os.environ.copy()
    git_env["PATH"] = "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
    git_env["GIT_SSH"] = _resolve_scheduler_executable("ssh")
    command = [_resolve_scheduler_executable("git"), "-C", SYSTEM_GIT_REPO_PATH, "pull"]
    return subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        env=git_env,
        timeout=SYSTEM_GIT_COMMAND_TIMEOUT_SECONDS,
    )


def _schedule_system_restart():
    restart_env = os.environ.copy()
    restart_env["PATH"] = "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
    sudo_path = _resolve_scheduler_executable("sudo")
    systemctl_path = _resolve_scheduler_executable("systemctl")
    command = [
        _resolve_scheduler_executable("sh"),
        "-c",
        f"sleep {SYSTEM_RESTART_DELAY_SECONDS}; exec {sudo_path} {systemctl_path} restart {SCHEDULER_SERVICE_NAME}",
    ]
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=restart_env,
        start_new_session=True,
    )
    if callable(getattr(process, "wait", None)):
        def stop_hung_restart():
            try:
                process.wait(timeout=SYSTEM_RESTART_COMMAND_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                process.kill()

        threading.Thread(target=stop_hung_restart, daemon=True).start()
    return process


def _git_pull_already_up_to_date(completed):
    output = f"{completed.stdout or ''}\n{completed.stderr or ''}".lower()
    return "already up to date" in output or "already up-to-date" in output


def _scheduler_command_label(command):
    if isinstance(command, (list, tuple)):
        return " ".join(str(part) for part in command)
    return str(command or "")


def _require_admin():
    if not current_user.is_authenticated:
        return redirect(url_for("dashboard.dashboard"))
    user_id = str(getattr(current_user, "id", "") or "")
    if not user_id or user_id not in _admin_ids():
        return redirect(url_for("dashboard.dashboard"))
    return None


@admin_bp.before_request
def _protect_admin_csrf():
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        csrf.protect()


def _admin_client_ip():
    return request.remote_addr or ""


def _admin_request_metadata(action, extra=None):
    metadata = {
        "action_type": action,
        "ip": _admin_client_ip(),
    }
    session_hint = (request.cookies.get("session") or "")[:16]
    if session_hint:
        metadata["session_identifier"] = session_hint
    if extra:
        metadata.update(extra)
    return metadata


def _admin_event_title(action):
    labels = {
        "view_admin_index": "Admin Viewed Dashboard",
        "view_admin_users": "Admin Viewed User Directory",
        "view_admin_requests": "Admin Viewed Requests",
        "view_admin_auth": "Admin Viewed Auth",
        "view_admin_analytics": "Admin Viewed Analytics",
        "view_admin_detail": "Admin Viewed Profile",
        "export_admin_detail": "Admin Exported User Data",
        "update_onboarding": "Admin Updated Onboarding",
        "reset_ics_token": "Admin Reset ICS Token",
        "disable_seat_tracks": "Admin Disabled Seat Tracks",
        "delete_shared_file": "Admin Deleted Shared File",
        "delete_shared_folder": "Admin Deleted Shared Folder",
        "delete_user": "Admin Deleted User",
        "view_admin_tiers": "Admin Viewed Tiers",
        "update_tier_definitions": "Admin Updated Tier Definitions",
        "update_user_tier": "Admin Updated User Tier",
        "approve_admin_request": "Admin Approved Request",
        "deny_admin_request": "Admin Denied Request",
        "manual_course_tracking_run": "Admin Ran Course Tracking Diagnostics",
        "toggle_course_tracking": "Admin Updated Course Tracking",
        "test_chem_150_tracking": "Admin Tested CHEM 150 Tracking",
        "course_tracking_refresh_interval": "Admin Updated Course Tracking Refresh",
        "spring_course_tracking_toggle": "Admin Updated Spring Course Tracking",
        "scheduler_pause": "Admin Paused Scheduler",
        "scheduler_resume": "Admin Resumed Scheduler",
        "system_git_pull": "Admin Ran Git Pull",
        "view_admin_apswiftly": "Admin Viewed APSwiftly",
        "apswiftly_reload": "Admin Reloaded APSwiftly Commands",
        "apswiftly_refresh_slash": "Admin Refreshed APSwiftly Slash Commands",
        "apswiftly_shutdown": "Admin Shut Down APSwiftly",
        "apswiftly_restart": "Admin Restarted APSwiftly Service",
    }
    return labels.get(action, "Admin Action")


def _log_admin_action(action, target, *, target_user=None, metadata=None, color="gray"):
    admin_actions_logger.info(
        "admin_id=%s action=%s target=%s ip=%s",
        str(getattr(current_user, "id", "") or ""),
        action,
        target,
        _admin_client_ip(),
    )
    event_target = format_user_target(target_user) if target_user else target
    emit_admin_event(
        _admin_event_title(action),
        actor=format_actor(current_user),
        target=event_target,
        metadata=_admin_request_metadata(action, metadata),
        color=color,
    )


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        gate = _require_admin()
        if gate:
            return gate
        return view(*args, **kwargs)

    return wrapped


def _theme_preference():
    try:
        settings = first_row(
            COLLECTIONS["user_settings"],
            [Query.equal("user_id", [str(current_user.id)])],
        )
    except AppwriteException:
        logger.exception("Failed to load admin theme preference")
        return None
    return settings.get("interface_theme") if settings else None


def _account_to_dict(value):
    if isinstance(value, dict):
        return value
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if hasattr(value, "model_dump"):
        return value.model_dump(by_alias=True, mode="json")
    return {}


def _fetch_account(user_id):
    try:
        account = Users(appwrite_client).get(user_id)
    except Exception:
        logger.exception("Failed to fetch Appwrite account data")
        return {}
    return _account_to_dict(account)


def _humanize_admin_key(key):
    text = str(key or "").replace("_", " ").replace("-", " ")
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    return text.strip().title()


def _format_admin_value(value):
    if value in (None, ""):
        return None
    if value is True:
        return "Yes"
    if value is False:
        return "No"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        if not value:
            return "None"
        if all(isinstance(item, (str, int, float, bool)) for item in value):
            preview = ", ".join(str(item) for item in value[:6])
            if len(value) > 6:
                preview = f"{preview}…"
            return preview
        return f"{len(value)} items"
    if isinstance(value, dict):
        parts = []
        for key in ("$id", "id", "name", "label", "status", "email", "provider", "type"):
            item = value.get(key)
            if item not in (None, ""):
                parts.append(f"{_humanize_admin_key(key)}: {item}")
        if parts:
            return "; ".join(parts)
        return f"{len(value)} fields"
    return str(value)


def _account_summary_rows(account_data):
    if not account_data:
        return []

    fields = [
        ("$id", "Account ID"),
        ("name", "Name"),
        ("email", "Email"),
        ("status", "Status"),
        ("emailVerification", "Email verified"),
        ("phone", "Phone"),
        ("phoneVerification", "Phone verified"),
        ("labels", "Labels"),
        ("prefs", "Preferences"),
        ("passwordUpdate", "Password updated"),
        ("registration", "Registered"),
        ("updatedAt", "Updated"),
    ]

    rows = []
    for key, label in fields:
        value = account_data.get(key)
        formatted = _format_admin_value(value)
        if formatted in (None, ""):
            continue
        rows.append({"label": label, "value": formatted})

    return rows


def _normalize_oauth_provider(user_doc):
    provider = str((user_doc or {}).get("provider") or "").strip().lower()
    if provider in {"google", "discord", "github"}:
        return provider
    if (user_doc or {}).get("google_id"):
        return "google"
    return "other"


def _user_summary(user_doc):
    tier = normalize_tier(user_doc.get("tier"))
    return {
        "id": _row_id(user_doc),
        "username": user_doc.get("username"),
        "name": user_doc.get("name"),
        "email": user_doc.get("email"),
        "created_at_raw": user_doc.get("created_at"),
        "created_at": _format_admin_date(user_doc.get("created_at")),
        "last_login_raw": user_doc.get("last_login"),
        "last_login": _format_admin_datetime(user_doc.get("last_login")),
        "onboarding_complete": bool(user_doc.get("onboarding_complete")),
        "onboarding_step": user_doc.get("onboarding_step") or 1,
        "discord_linked": bool(user_doc.get("discord_id")),
        "oauth_provider": _normalize_oauth_provider(user_doc),
        "emory_student": bool(user_doc.get("emory_student")),
        "school": user_doc.get("school"),
        "major": user_doc.get("major"),
        "graduation_year": user_doc.get("graduation_year"),
        "education_level": user_doc.get("education_level"),
        "class_year": user_doc.get("class_year"),
        "picture_url": user_doc.get("picture_url"),
        "banner_color": _normalize_banner_color(user_doc.get("banner_color")),
        "tier": tier,
        "tier_label": TIER_LABELS[tier],
        "tier_badge": TIER_BADGES.get(tier),
    }


def _search_users(users, query, field):
    """Return case-insensitive partial matches from the admin user directory."""
    needle = (query or "").strip().casefold()
    if not needle:
        return list(users)

    searchable_fields = {
        "name": ("name",),
        "email": ("email",),
        "username": ("username",),
        "id": ("$id", "id"),
    }
    keys = searchable_fields.get(field, ("name", "username", "email", "$id", "id"))
    return [
        user for user in users
        if any(needle in str(user.get(key) or "").casefold() for key in keys)
    ]


AUTH_USER_SORTS = {"identity", "tier", "profile", "activity", "created"}


def _sort_auth_users(users, sort_key, sort_order):
    reverse = sort_order == "desc"

    def normalized(value):
        return str(value or "").strip().casefold()

    def sort_value(user):
        if sort_key == "identity":
            return (normalized(user.get("name") or user.get("username")), normalized(user.get("email")))
        if sort_key == "tier":
            tier = normalize_tier(user.get("tier"))
            return (normalized(TIER_LABELS[tier]), normalized(user.get("name") or user.get("username")))
        if sort_key == "profile":
            return (normalized(user.get("school")), normalized(user.get("major") or user.get("education_level")))
        if sort_key == "activity":
            return normalized(user.get("last_login"))
        return normalized(user.get("created_at"))

    return sorted(users, key=sort_value, reverse=reverse)


def _count_rows(table_id, queries):
    try:
        response = list_rows_safe(
            table_id,
            queries + [Query.limit(1)],
        )
    except Exception:
        logger.exception("Failed to count rows for %s", table_id)
        return None

    total = response.get("total")
    if isinstance(total, int):
        return total
    try:
        return len(list_rows_all(table_id, queries))
    except Exception:
        logger.exception("Failed to fully count rows for %s", table_id)
        return None


def _safe_count_rows(table_id, queries):
    value = _count_rows(table_id, queries)
    return value if isinstance(value, int) else 0


def _user_chat_messages(user_id):
    try:
        return list_rows_all(
            COLLECTIONS["chat_messages"],
            [Query.equal("user_id", [user_id]), Query.order_desc("created_at")],
        )
    except AppwriteException:
        logger.exception("Failed to load chat history for admin")
        return []


def _user_dm_threads(user_id):
    threads = {}
    for field in ("participant_a", "participant_b"):
        try:
            rows = list_rows_all(
                COLLECTIONS["chat_dm_threads"],
                [Query.equal(field, [user_id]), Query.order_desc("updated_at")],
            )
        except AppwriteException:
            logger.exception("Failed to load chat threads for admin")
            rows = []
        for row in rows:
            row_id = _row_id(row)
            if row_id:
                threads[row_id] = row
    return sorted(
        threads.values(),
        key=lambda row: row.get("last_message_at") or row.get("updated_at") or row.get("created_at") or "",
        reverse=True,
    )


def _user_chat_blocks(user_id):
    blocks = {}
    for field in ("blocker_id", "blocked_id"):
        try:
            rows = list_rows_all(
                COLLECTIONS["chat_blocks"],
                [Query.equal(field, [user_id])],
            )
        except AppwriteException:
            logger.exception("Failed to load chat blocks for admin")
            rows = []
        for row in rows:
            row_id = _row_id(row)
            if row_id:
                blocks[row_id] = row
    return sorted(blocks.values(), key=lambda row: row.get("created_at") or "", reverse=True)


def _chat_count_summary(user_id):
    messages = _user_chat_messages(user_id)
    return {
        "chat_messages": len(messages),
        "deleted_chat_messages": sum(1 for row in messages if row.get("deleted_at")),
        "dm_threads": len(_user_dm_threads(user_id)),
        "chat_blocks": len(_user_chat_blocks(user_id)),
    }


def _pending_admin_request_count():
    return _count_rows(
        COLLECTIONS["admin_requests"],
        [Query.equal("status", ["pending"])],
    ) or 0


def _storage_service():
    return Storage(appwrite_client)


def _status_code(exc):
    status = getattr(exc, "code", None)
    if status is None:
        status = getattr(exc, "response_code", None)
    try:
        return int(status or 0)
    except (TypeError, ValueError):
        return 0


def _delete_storage_file(shared_file):
    storage_file_id = shared_file.get("storage_file_id")
    if not storage_file_id:
        return
    bucket_id = shared_file.get("storage_bucket_id") or FILE_SHARE_BUCKET_ID
    try:
        _storage_service().delete_file(bucket_id, storage_file_id)
    except AppwriteException as exc:
        if _status_code(exc) != 404:
            logger.exception("Failed to delete storage file %s", storage_file_id)


def _delete_shared_file_row(shared_file):
    _delete_storage_file(shared_file)
    delete_row_safe(COLLECTIONS["shared_files"], _row_id(shared_file))


def _collect_folder_tree_ids(user_id, root_folder_id):
    if not root_folder_id:
        return []

    folders = list_rows_all(
        COLLECTIONS["file_folders"],
        [Query.equal("user_id", [user_id])],
    )
    children_by_parent = {}
    for folder in folders:
        parent_id = folder.get("parent_folder_id")
        children_by_parent.setdefault(parent_id, []).append(_row_id(folder))

    collected = []
    stack = [root_folder_id]
    seen = set()
    while stack:
        folder_id = stack.pop()
        if not folder_id or folder_id in seen:
            continue
        seen.add(folder_id)
        collected.append(folder_id)
        stack.extend(children_by_parent.get(folder_id, []))
    return collected


def _delete_user_rows(user_id):
    return delete_user_data(user_id)


def _load_section(section, user_id):
    if section == "invites":
        try:
            owned_invites = list_rows_all(
                COLLECTIONS["user_invites"],
                [
                    Query.equal("owner_user_id", [user_id]),
                    Query.order_desc("created_at"),
                ],
            )
            owned_attributions = list_rows_all(
                COLLECTIONS["user_invite_attributions"],
                [
                    Query.equal("inviter_user_id", [user_id]),
                    Query.order_desc("signed_up_at"),
                ],
            )
            received_attribution = first_row(
                COLLECTIONS["user_invite_attributions"],
                [Query.equal("invited_user_id", [user_id])],
            )
        except AppwriteException:
            logger.exception("Failed to load invite records for admin")
            return {
                "invites": [],
                "received_attribution": None,
            }

        attributions_by_invite = {}
        for attribution in owned_attributions:
            attributions_by_invite.setdefault(
                str(attribution.get("invite_id") or ""),
                [],
            ).append(attribution)
        return {
            "invites": [
                {
                    "invite": invitation,
                    "attributions": attributions_by_invite.get(
                        str(_row_id(invitation) or ""),
                        [],
                    ),
                }
                for invitation in owned_invites
            ],
            "received_attribution": received_attribution,
        }

    if section == "settings":
        try:
            return {
                "settings": first_row(
                    COLLECTIONS["user_settings"],
                    [Query.equal("user_id", [user_id])],
                )
            }
        except AppwriteException:
            logger.exception("Failed to load user settings")
            return {"settings": None}

    if section == "files":
        try:
            folders = list_rows_all(
                COLLECTIONS["file_folders"],
                [Query.equal("user_id", [user_id]), Query.order_asc("created_at")],
            )
            files = list_rows_all(
                COLLECTIONS["shared_files"],
                [Query.equal("user_id", [user_id]), Query.order_desc("created_at")],
            )
        except AppwriteException:
            logger.exception("Failed to load files for admin")
            return {"folders": [], "files": []}
        return {
            "folders": folders,
            "files": files,
        }

    if section == "notes":
        try:
            notes = list_rows_all(
                COLLECTIONS["notes"],
                [Query.equal("user_id", [user_id]), Query.order_desc("updated_at")],
            )
            folders = list_rows_all(
                COLLECTIONS["note_folders"],
                [Query.equal("user_id", [user_id]), Query.order_asc("created_at")],
            )
        except AppwriteException:
            logger.exception("Failed to load notes for admin")
            return {"notes": [], "note_folders": []}
        return {
            "notes": notes,
            "note_folders": folders,
        }

    if section == "calendars":
        try:
            cache_rows = list_calendar_rows_all(
                COLLECTIONS["calendar_cache"],
                [Query.equal("user_id", [user_id]), Query.order_desc("event_start")],
            )
            feeds = list_calendar_rows_all(
                COLLECTIONS["calendar_feeds"],
                [Query.equal("user_id", [user_id]), Query.order_desc("updated_at")],
            )
            preferences = list_calendar_rows_all(
                COLLECTIONS["user_calendar_preferences"],
                [Query.equal("user_id", [user_id]), Query.order_asc("calendar_name")],
            )
            sources = list_calendar_rows_all(
                COLLECTIONS["user_calendar_sources"],
                [Query.equal("user_id", [user_id]), Query.order_desc("updated_at")],
            )
            events = list_calendar_rows_all(
                COLLECTIONS["user_events"],
                [Query.equal("user_id", [user_id]), Query.order_desc("start")],
            )
            overrides = list_calendar_rows_all(
                COLLECTIONS["user_event_overrides"],
                [Query.equal("user_id", [user_id]), Query.order_desc("updated_at")],
            )
        except AppwriteException:
            logger.exception("Failed to load calendar data for admin")
            return {
                "calendar_cache": [],
                "calendar_feeds": [],
                "calendar_preferences": [],
                "calendar_sources": [],
                "calendar_events": [],
                "calendar_overrides": [],
            }
        return {
            "calendar_cache": cache_rows,
            "calendar_feeds": feeds,
            "calendar_preferences": preferences,
            "calendar_sources": sources,
            "calendar_events": events,
            "calendar_overrides": overrides,
        }

    if section == "courses":
        try:
            courses = list_rows_all(
                COLLECTIONS["user_courses"],
                [Query.equal("user_id", [user_id]), Query.order_asc("term")],
            )
        except AppwriteException:
            logger.exception("Failed to load courses for admin")
            courses = []
        return {"courses": courses}

    if section == "seat_tracks":
        try:
            tracks = list_rows_all(
                COLLECTIONS["course_seat_tracks"],
                [Query.equal("user_id", [user_id]), Query.order_desc("updated_at")],
            )
        except AppwriteException:
            logger.exception("Failed to load seat tracks for admin")
            tracks = []
        return {"seat_tracks": tracks}

    if section == "chat":
        return {
            "messages": _user_chat_messages(user_id),
            "dm_threads": _user_dm_threads(user_id),
            "blocks": _user_chat_blocks(user_id),
        }

    return {}


def _export_payload(user_id):
    user_doc = get_row_safe(COLLECTIONS["users"], user_id, allow_missing=True)
    if not user_doc:
        return None

    return {
        "user": user_doc,
        "account": _fetch_account(user_id),
        "settings": first_row(
            COLLECTIONS["user_settings"],
            [Query.equal("user_id", [user_id])],
        ),
        "files": list_rows_all(
            COLLECTIONS["shared_files"],
            [Query.equal("user_id", [user_id])],
        ),
        "file_folders": list_rows_all(
            COLLECTIONS["file_folders"],
            [Query.equal("user_id", [user_id])],
        ),
        "notes": list_rows_all(
            COLLECTIONS["notes"],
            [Query.equal("user_id", [user_id])],
        ),
        "note_folders": list_rows_all(
            COLLECTIONS["note_folders"],
            [Query.equal("user_id", [user_id])],
        ),
        "calendar_cache": list_calendar_rows_all(
            COLLECTIONS["calendar_cache"],
            [Query.equal("user_id", [user_id])],
        ),
        "calendar_feeds": list_calendar_rows_all(
            COLLECTIONS["calendar_feeds"],
            [Query.equal("user_id", [user_id])],
        ),
        "calendar_preferences": list_calendar_rows_all(
            COLLECTIONS["user_calendar_preferences"],
            [Query.equal("user_id", [user_id])],
        ),
        "calendar_sources": list_calendar_rows_all(
            COLLECTIONS["user_calendar_sources"],
            [Query.equal("user_id", [user_id])],
        ),
        "calendar_events": list_calendar_rows_all(
            COLLECTIONS["user_events"],
            [Query.equal("user_id", [user_id])],
        ),
        "calendar_overrides": list_calendar_rows_all(
            COLLECTIONS["user_event_overrides"],
            [Query.equal("user_id", [user_id])],
        ),
        "courses": list_rows_all(
            COLLECTIONS["user_courses"],
            [Query.equal("user_id", [user_id])],
        ),
        "seat_tracks": list_rows_all(
            COLLECTIONS["course_seat_tracks"],
            [Query.equal("user_id", [user_id])],
        ),
        "chat_messages": _user_chat_messages(user_id),
        "chat_dm_threads": _user_dm_threads(user_id),
        "chat_blocks": _user_chat_blocks(user_id),
    }


ADMIN_STATUS_MESSAGES = {
    "onboarding-updated": "Onboarding updated.",
    "token-reset": "ICS token reset.",
    "file-deleted": "File deleted.",
    "folder-deleted": "Folder deleted.",
    "request-approved": "Request approved.",
    "request-denied": "Request denied.",
    "user-deleted": "User deleted.",
}


def _status_message(status):
    if not status:
        return None
    if status in ADMIN_STATUS_MESSAGES:
        return ADMIN_STATUS_MESSAGES[status]
    if status.startswith("disabled-"):
        return f"Disabled {status.split('-', 1)[1]} seat track(s)."
    return status.replace("-", " ").strip()


def _flash_admin_result(status=None, error=None):
    """Queue a toast for the post-redirect page load."""
    if error:
        push_toast(error, type="error")
    elif status:
        message = _status_message(status)
        if message:
            push_toast(message, type="success")


def _redirect_detail(user_id, section, status=None, error=None, return_to=None):
    _flash_admin_result(status=status, error=error)
    destination = {
        "user_id": user_id,
        "section": section,
    }
    if return_to:
        destination["return_to"] = _admin_detail_return_url(return_to)
    return redirect(url_for("admin.admin_detail", **destination))


def _admin_detail_return_url(value):
    fallback = url_for("admin.admin_auth", tab="users")
    candidate = (value or "").strip()
    if candidate == "/admin/auth" or candidate.startswith("/admin/auth?"):
        return candidate
    return fallback


AUTH_TABS = ("users", "course-tracking", "channel-requests")
ALLOWED_USERS_PER_PAGE = {5, 10, 25, 50, 100}
DEFAULT_USERS_PER_PAGE = 10


def _normalize_auth_tab(tab):
    value = (tab or "users").strip().lower()
    if value not in AUTH_TABS:
        return "users"
    return value


def _auth_page_context(**extra):
    context = {
        "theme_preference": _theme_preference(),
        "pending_request_count": _pending_admin_request_count(),
        "active_admin_page": "auth",
        "breadcrumbs": [("Admin", url_for("admin.admin_index")), ("Auth", None)],
    }
    context.update(extra)
    return context


def _load_auth_users_section():
    query = (request.args.get("q") or "").strip()
    field = (request.args.get("field") or "").strip()
    sort_key = (request.args.get("sort") or "created").strip().lower()
    if sort_key not in AUTH_USER_SORTS:
        sort_key = "created"
    sort_order = (request.args.get("order") or "desc").strip().lower()
    if sort_order not in {"asc", "desc"}:
        sort_order = "desc"
    try:
        page = max(1, int(request.args.get("page") or 1))
    except (TypeError, ValueError):
        page = 1
    try:
        per_page = int(request.args.get("per_page") or DEFAULT_USERS_PER_PAGE)
    except (TypeError, ValueError):
        per_page = DEFAULT_USERS_PER_PAGE
    if per_page not in ALLOWED_USERS_PER_PAGE:
        per_page = DEFAULT_USERS_PER_PAGE

    error = None
    total_users = 0
    users = []
    total_pages = 1
    try:
        if not query and sort_key == "created" and sort_order == "desc":
            offset = (page - 1) * per_page
            response = list_rows_safe(
                COLLECTIONS["users"],
                [Query.order_desc("created_at"), Query.limit(per_page), Query.offset(offset)],
            )
            users = response.get("rows", [])
            total_users = int(response.get("total") or 0)
            total_pages = max(1, (total_users + per_page - 1) // per_page) if total_users else 1
            if page > total_pages and total_users:
                page = total_pages
        else:
            all_users = list_rows_all(COLLECTIONS["users"], [Query.order_desc("created_at")])
            matched = _search_users(all_users, query, field)
            matched = _sort_auth_users(matched, sort_key, sort_order)
            total_users = len(matched)
            total_pages = max(1, (total_users + per_page - 1) // per_page) if total_users else 1
            if page > total_pages:
                page = total_pages
            start = (page - 1) * per_page
            users = matched[start:start + per_page]
    except AppwriteException:
        logger.exception("Failed to load admin user list")
        users = []
        total_users = 0
        total_pages = 1
        error = "Unable to load users right now."

    return {
        "users": [_user_summary(user) for user in users],
        "q": query,
        "field": field,
        "sort": sort_key,
        "order": sort_order,
        "page": page,
        "per_page": per_page,
        "total_users": total_users,
        "total_pages": total_pages,
        "allowed_users_per_page": sorted(ALLOWED_USERS_PER_PAGE),
        "error": error,
    }


def _load_auth_channel_requests_section():
    status_filter = (request.args.get("status") or "pending").strip().lower()
    if status_filter not in {"pending", "approved", "denied", "all"}:
        status_filter = "pending"
    queries = [Query.order_desc("created_at")]
    if status_filter != "all":
        queries.insert(0, Query.equal("status", [status_filter]))
    try:
        requests_rows = list_rows_all(COLLECTIONS["admin_requests"], queries)
    except AppwriteException:
        logger.exception("Failed to load admin requests")
        requests_rows = []
    return {
        "requests": requests_rows,
        "status_filter": status_filter,
    }


def _redirect_requests(notice=None, error=None):
    _flash_admin_result(status=notice, error=error)
    return redirect(url_for("admin.admin_auth", tab="channel-requests"))


@admin_bp.route("/admin")
@admin_required
def admin_index():
    _log_admin_action("view_admin_index", "admin home")
    return render_template(
        "admin.html",
        error=request.args.get("error"),
        status=request.args.get("status"),
        system_status=_system_status(),
        theme_preference=_theme_preference(),
        pending_request_count=_pending_admin_request_count(),
        active_admin_page="home",
        breadcrumbs=[("Admin", url_for("admin.admin_index")), ("Home", None)],
    )


@admin_bp.route("/admin/analytics")
@admin_required
def admin_analytics():
    error = None
    metrics = {}
    try:
        metrics = _admin_home_metrics()
    except AppwriteException:
        logger.exception("Failed to load admin analytics metrics")
        error = "Unable to load all admin metrics right now."

    _log_admin_action("view_admin_analytics", "admin analytics")
    return render_template(
        "admin_analytics.html",
        metrics=metrics,
        error=error,
        status=request.args.get("status"),
        range_options=RANGE_OPTIONS,
        default_range="30d",
        theme_preference=_theme_preference(),
        pending_request_count=_pending_admin_request_count(),
        active_admin_page="analytics",
        breadcrumbs=[("Admin", url_for("admin.admin_index")), ("Analytics", None)],
    )


@admin_bp.route("/admin/analytics/data")
@admin_required
def admin_analytics_data():
    range_key = normalize_range(request.args.get("range"))
    tz_name = (request.args.get("tz") or "UTC").strip() or "UTC"
    payload = analytics_payload(range_key=range_key, tz_name=tz_name)
    return jsonify(payload)


@admin_bp.route("/admin/auth")
@admin_required
def admin_auth():
    tab = _normalize_auth_tab(request.args.get("tab"))
    _log_admin_action("view_admin_auth", "admin auth", metadata={"tab": tab, "section": None})
    return render_template(
        "admin_auth.html",
        initial_tab=tab,
        status=request.args.get("status"),
        error=request.args.get("error"),
        **_auth_page_context(),
    )


@admin_bp.route("/admin/auth/sections/users")
@admin_required
def admin_auth_section_users():
    payload = _load_auth_users_section()
    _log_admin_action(
        "view_admin_auth",
        "admin auth users",
        metadata={"section": "users", "query": payload["q"], "field": payload["field"]},
    )
    return render_template("partials/admin_auth_users.html", **payload)


@admin_bp.route("/admin/tiers")
@admin_required
def admin_tiers():
    _log_admin_action("view_admin_tiers", "admin tiers")
    return render_template(
        "admin_tiers.html",
        tier_definitions=get_tier_definitions(),
        tier_keys=TIER_KEYS,
        tier_labels=TIER_LABELS,
        tier_badges=TIER_BADGES,
        status=request.args.get("status"),
        error=request.args.get("error"),
        **_auth_page_context(active_admin_page="tiers", breadcrumbs=[
            ("Admin", url_for("admin.admin_index")),
            ("Tiers", None),
        ]),
    )


@admin_bp.route("/admin/tiers", methods=["POST"])
@admin_required
def save_admin_tiers():
    payload = request.get_json(silent=True)
    if payload is None:
        try:
            payload = {}
            for tier in TIER_KEYS:
                payload[tier] = {}
                form_keys = {
                    "storage_bytes": (f"{tier}__storage_gb", 1024 ** 3),
                    "max_file_size_bytes": (f"{tier}__max_file_size_mb", 1024 ** 2),
                    "max_chat_attachment_size_bytes": (f"{tier}__max_chat_attachment_size_mb", 1024 ** 2),
                    "max_upload_files": (f"{tier}__max_upload_files", 1),
                    "max_saved_courses": (f"{tier}__max_saved_courses", 1),
                    "max_seat_tracks": (f"{tier}__max_seat_tracks", 1),
                    "max_calendar_feeds": (f"{tier}__max_calendar_feeds", 1),
                    "max_notes": (f"{tier}__max_notes", 1),
                    "max_focus_playlists": (f"{tier}__max_focus_playlists", 1),
                }
                for key, (field, multiplier) in form_keys.items():
                    unlimited = request.form.get(f"{field}__unlimited")
                    raw_value = request.form.get(field)
                    payload[tier][key] = None if unlimited else (
                        int(float(raw_value) * multiplier) if raw_value not in (None, "") else raw_value
                    )
                payload[tier]["seat_track_intervals_minutes"] = [
                    int(value.strip())
                    for value in request.form.get(f"{tier}__seat_track_intervals_minutes", "").split(",")
                    if value.strip()
                ]
        except (TypeError, ValueError):
            return redirect(url_for("admin.admin_tiers", error="invalid-tier-config"))
    try:
        definitions = save_tier_definitions(payload)
    except (TypeError, ValueError) as exc:
        if request.is_json:
            return jsonify({"error": str(exc), "code": "invalid_tier_config"}), 400
        return redirect(url_for("admin.admin_tiers", error="invalid-tier-config"))

    _log_admin_action(
        "update_tier_definitions",
        "admin tiers",
        metadata={"tiers": list(definitions.keys())},
        color="green",
    )
    if request.is_json:
        return jsonify({"status": "ok", "tier_definitions": definitions})
    return redirect(url_for("admin.admin_tiers", status="tiers-updated"))


@admin_bp.route("/admin/auth/sections/course-tracking")
@admin_required
def admin_auth_section_course_tracking():
    tracking_groups, tracking_error = _course_tracking_groups()
    _log_admin_action("view_admin_auth", "admin auth course tracking", metadata={"section": "course-tracking"})
    return render_template(
        "partials/admin_auth_course_tracking.html",
        tracking_groups=tracking_groups,
        tracking_error=tracking_error,
        course_tracking_refresh_minutes=get_course_tracking_refresh_minutes(),
        spring_tracking_open=spring_course_tracking_open(),
    )


@admin_bp.route("/admin/auth/sections/channel-requests")
@admin_required
def admin_auth_section_channel_requests():
    payload = _load_auth_channel_requests_section()
    _log_admin_action(
        "view_admin_auth",
        "admin auth channel requests",
        metadata={"section": "channel-requests", "status_filter": payload["status_filter"]},
    )
    return render_template("partials/admin_auth_channel_requests.html", **payload)


@admin_bp.route("/admin/users")
@admin_required
def admin_users():
    return redirect(
        url_for(
            "admin.admin_auth",
            tab="users",
            q=request.args.get("q"),
            field=request.args.get("field"),
        )
    )


@admin_bp.route("/admin/system-status")
@admin_required
def admin_system_status():
    return jsonify(_system_status())


@admin_bp.route("/admin/system-scheduler/<action>", methods=["POST"])
@admin_required
def admin_system_scheduler_control(action):
    if action not in {"pause", "resume"}:
        abort(404)

    metadata = {
        "scheduler_action": action,
        "command_mode": "fixed",
        "env_path": SCHEDULER_ENV_PATH,
        "service": SCHEDULER_SERVICE_NAME,
    }
    try:
        completed = _run_scheduler_control_action(action)
    except subprocess.CalledProcessError as exc:
        message = _sanitize_admin_error(exc.stderr or exc.stdout or exc)
        _log_admin_action(
            f"scheduler_{action}",
            "Background scheduler",
            metadata={**metadata, "result": "failed", "failed_command": _scheduler_command_label(exc.cmd)},
            color="red",
        )
        return jsonify({"error": "Scheduler command failed.", "message": message}), 500
    except subprocess.TimeoutExpired as exc:
        message = _sanitize_admin_error(exc)
        _log_admin_action(
            f"scheduler_{action}",
            "Background scheduler",
            metadata={**metadata, "result": "timeout", "failed_command": _scheduler_command_label(exc.cmd)},
            color="red",
        )
        return jsonify({"error": "Scheduler command timed out.", "message": message}), 500
    except Exception as exc:
        logger.exception("Failed to %s scheduler", action)
        message = _sanitize_admin_error(exc)
        _log_admin_action(
            f"scheduler_{action}",
            "Background scheduler",
            metadata={**metadata, "result": "failed"},
            color="red",
        )
        return jsonify({"error": "Unable to update scheduler.", "message": message}), 500

    _log_admin_action(
        f"scheduler_{action}",
        "Background scheduler",
        metadata={**metadata, "result": "success", "completed_steps": ", ".join(completed)},
        color="yellow" if action == "pause" else "green",
    )
    return jsonify({"status": "ok", "action": action, "completed_steps": completed})


@admin_bp.route("/admin/system-git-pull", methods=["POST"])
@admin_required
def admin_system_git_pull():
    metadata = {
        "command_mode": "fixed",
        "repo_path": SYSTEM_GIT_REPO_PATH,
    }
    try:
        completed = _run_system_git_pull()
        restart_process = None if _git_pull_already_up_to_date(completed) else _schedule_system_restart()
    except subprocess.CalledProcessError as exc:
        message = _sanitize_admin_error(exc.stderr or exc.stdout or exc)
        _log_admin_action(
            "system_git_pull",
            "Nest repository",
            metadata={**metadata, "result": "failed", "failed_command": _scheduler_command_label(exc.cmd)},
            color="red",
        )
        return jsonify({"error": "Git pull failed.", "message": message}), 500
    except subprocess.TimeoutExpired as exc:
        message = _sanitize_admin_error(exc)
        _log_admin_action(
            "system_git_pull",
            "Nest repository",
            metadata={**metadata, "result": "timeout", "failed_command": _scheduler_command_label(exc.cmd)},
            color="red",
        )
        return jsonify({"error": "Git pull timed out.", "message": message}), 500
    except Exception as exc:
        logger.exception("Failed to run git pull or schedule restart")
        message = _sanitize_admin_error(exc)
        _log_admin_action(
            "system_git_pull",
            "Nest repository",
            metadata={**metadata, "result": "failed"},
            color="red",
        )
        return jsonify({"error": "Unable to run git pull or schedule restart.", "message": message}), 500

    stdout = _sanitize_admin_error(completed.stdout)
    stderr = _sanitize_admin_error(completed.stderr)
    _log_admin_action(
        "system_git_pull",
        "Nest repository",
        metadata={
            **metadata,
            "result": "success",
            "restart_scheduled": restart_process is not None,
            "restart_delay_seconds": SYSTEM_RESTART_DELAY_SECONDS if restart_process else 0,
        },
        color="green",
    )
    return jsonify({
        "status": "ok",
        "command": f"cd {SYSTEM_GIT_REPO_PATH} && git pull && sudo systemctl restart {SCHEDULER_SERVICE_NAME}",
        "stdout": stdout,
        "stderr": stderr,
        "returncode": completed.returncode,
        "restart_scheduled": restart_process is not None,
        "restart_delay_seconds": SYSTEM_RESTART_DELAY_SECONDS if restart_process else 0,
        "restart_process_id": restart_process.pid if restart_process else None,
    })


def _enabled_course_track_count():
    try:
        return len(list_rows_all(
            COLLECTIONS["course_seat_tracks"],
            [Query.equal("enabled", [True])],
        )), None
    except AppwriteException as exc:
        logger.exception("Failed to count enabled course seat tracks")
        return None, _sanitize_admin_error(exc)


def _format_bytes(value):
    try:
        size = int(value or 0)
    except (TypeError, ValueError):
        size = 0
    units = ("B", "KB", "MB", "GB", "TB")
    amount = float(size)
    unit = units[0]
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            break
        amount /= 1024
    if unit == "B":
        return f"{int(amount)} {unit}"
    return f"{amount:.1f} {unit}"


def _storage_usage_summary():
    try:
        files = list_rows_all(COLLECTIONS["shared_files"])
    except Exception as exc:
        logger.exception("Failed to load file storage summary")
        return {
            "bytes": 0,
            "formatted": "--",
            "file_count": 0,
            "avatar_count": 0,
            "error": _sanitize_admin_error(exc),
        }

    total_bytes = 0
    for file_row in files:
        try:
            total_bytes += int(file_row.get("file_size_bytes") or 0)
        except (TypeError, ValueError):
            continue

    try:
        users = list_rows_all(COLLECTIONS["users"])
        avatar_count = sum(1 for user in users if user.get("avatar_file_id"))
    except Exception:
        logger.exception("Failed to count avatar storage rows")
        avatar_count = 0
    return {
        "bytes": total_bytes,
        "formatted": _format_bytes(total_bytes),
        "file_count": len(files),
        "avatar_count": avatar_count,
        "error": None,
    }


def _admin_home_metrics():
    active_tracks = _safe_count_rows(COLLECTIONS["course_seat_tracks"], [Query.equal("enabled", [True])])
    paused_tracks = _safe_count_rows(COLLECTIONS["course_seat_tracks"], [Query.equal("enabled", [False])])
    return {
        "total_users": _safe_count_rows(COLLECTIONS["users"], []),
        "emory_users": _safe_count_rows(COLLECTIONS["users"], [Query.equal("emory_student", [True])]),
        "non_emory_users": _safe_count_rows(COLLECTIONS["users"], [Query.equal("emory_student", [False])]),
        "pending_requests": _pending_admin_request_count(),
        "saved_courses": _safe_count_rows(COLLECTIONS["user_courses"], []),
        "active_course_tracks": active_tracks,
        "paused_course_tracks": paused_tracks,
        "file_storage": _storage_usage_summary(),
    }


def _course_tracking_diagnostics():
    from services.course_tracking import get_last_course_tracking_poll
    from services.scheduler import scheduler_status

    enabled_count, enabled_error = _enabled_course_track_count()
    scheduler_payload = scheduler_status()
    payload = {
        **scheduler_payload,
        **discord_audit_status(),
        "enabled_track_count": enabled_count,
        "enabled_track_count_error": enabled_error,
        "last_course_tracking_poll": get_last_course_tracking_poll(),
    }
    payload["course_tracking_job_registered"] = any(
        job.get("id") == "check_course_seat_tracks"
        for job in scheduler_payload.get("jobs", [])
    )
    return payload


def _track_group_key(track):
    return {
        "term": track.get("term") or "",
        "subject": str(track.get("subject") or "").upper(),
        "catalog": str(track.get("catalog") or ""),
        "crn": str(track.get("crn") or ""),
    }


def _track_group_id(key):
    return "|".join([key["term"], key["subject"], key["catalog"], key["crn"]])


def _serialize_admin_track(track):
    return {
        "id": _row_id(track),
        "user_id": track.get("user_id"),
        "term": track.get("term"),
        "subject": track.get("subject"),
        "catalog": track.get("catalog"),
        "crn": track.get("crn"),
        "section_id": track.get("section_id"),
        "course_code": track.get("course_code"),
        "course_title": track.get("course_title"),
        "enabled": bool(track.get("enabled")),
        "last_status": track.get("last_status"),
        "last_seats_available": track.get("last_seats_available"),
        "last_checked_at": track.get("last_checked_at"),
        "last_notified_at": track.get("last_notified_at"),
        "created_at": track.get("created_at"),
        "updated_at": track.get("updated_at"),
    }


def _course_tracking_groups():
    try:
        tracks = list_rows_all(
            COLLECTIONS["course_seat_tracks"],
            [Query.order_desc("updated_at")],
        )
    except Exception:
        logger.exception("Failed to load course tracking rows")
        return [], "Unable to load course tracking."

    grouped = {}
    for track in tracks:
        key = _track_group_key(track)
        if not key["term"] or not key["subject"] or not key["catalog"]:
            continue
        group_id = _track_group_id(key)
        group = grouped.setdefault(group_id, {
            "id": group_id,
            **key,
            "course_code": track.get("course_code") or f"{key['subject']} {key['catalog']}".strip(),
            "course_title": track.get("course_title") or "",
            "tracks": [],
        })
        if not group.get("course_code") and track.get("course_code"):
            group["course_code"] = track.get("course_code")
        if not group.get("course_title") and track.get("course_title"):
            group["course_title"] = track.get("course_title")
        group["tracks"].append(_serialize_admin_track(track))

    groups = []
    for group in grouped.values():
        tracks = group["tracks"]
        enabled_tracks = [track for track in tracks if track.get("enabled")]
        paused_tracks = [track for track in tracks if not track.get("enabled")]
        users = sorted({track.get("user_id") for track in tracks if track.get("user_id")})
        last_checked_values = [track.get("last_checked_at") for track in tracks if track.get("last_checked_at")]
        last_updated_values = [track.get("updated_at") for track in tracks if track.get("updated_at")]
        representative = tracks[0] if tracks else {}
        group.update({
            "track_count": len(tracks),
            "active_count": len(enabled_tracks),
            "paused_count": len(paused_tracks),
            "user_count": len(users),
            "users": users,
            "last_checked_at": max(last_checked_values) if last_checked_values else None,
            "last_updated_at": max(last_updated_values) if last_updated_values else None,
            "last_status": representative.get("last_status"),
            "last_seats_available": representative.get("last_seats_available"),
            "enabled": bool(enabled_tracks),
        })
        groups.append(group)

    groups.sort(key=lambda item: (item.get("term") or "", item.get("subject") or "", item.get("catalog") or "", item.get("crn") or ""))
    return groups, None


def _toggle_track(track, enabled):
    row_id = _row_id(track)
    if not row_id:
        raise AppwriteException("Track row is missing an id.")
    return update_row_safe(
        COLLECTIONS["course_seat_tracks"],
        row_id,
        {
            "enabled": bool(enabled),
            "updated_at": format_datetime(datetime.utcnow()),
        },
    )


@admin_bp.route("/admin/course-tracking-status")
@admin_required
def admin_course_tracking_status():
    return jsonify(_course_tracking_diagnostics())


@admin_bp.route("/admin/course-tracking/tracks")
@admin_required
def admin_course_tracking_tracks():
    groups, error = _course_tracking_groups()
    status_code = 500 if error else 200
    return jsonify({
        "groups": groups,
        "count": sum(group.get("track_count", 0) for group in groups),
        "group_count": len(groups),
        "error": error,
    }), status_code


@admin_bp.route("/admin/course-tracking/groups/toggle", methods=["POST"])
@admin_required
def admin_course_tracking_group_toggle():
    payload = request.get_json(silent=True) or request.form or {}
    enabled = str(payload.get("enabled", "")).strip().lower() in {"1", "true", "yes", "on"}
    term = str(payload.get("term") or "").strip()
    subject = str(payload.get("subject") or "").strip().upper()
    catalog = str(payload.get("catalog") or "").strip()
    crn = str(payload.get("crn") or "").strip()
    if not term or not subject or not catalog:
        return jsonify({"error": "Missing course tracking group identifiers."}), 400

    try:
        tracks = list_rows_all(
            COLLECTIONS["course_seat_tracks"],
            [
                Query.equal("term", [term]),
                Query.equal("subject", [subject]),
                Query.equal("catalog", [catalog]),
                Query.equal("crn", [crn]),
            ],
        )
        updated = [_toggle_track(track, enabled) for track in tracks]
    except AppwriteException as exc:
        logger.exception("Failed to update course tracking group")
        return jsonify({"error": "Unable to update course tracking.", "message": _sanitize_admin_error(exc)}), 500

    _log_admin_action(
        "toggle_course_tracking",
        f"group:{term}:{subject}:{catalog}:{crn}",
        metadata={"enabled": enabled, "updated_count": len(updated), "scope": "group"},
        color="green" if enabled else "yellow",
    )
    groups, _ = _course_tracking_groups()
    return jsonify({"status": "ok", "updated_count": len(updated), "groups": groups})


@admin_bp.route("/admin/course-tracking/tracks/<track_id>/toggle", methods=["POST"])
@admin_required
def admin_course_tracking_track_toggle(track_id):
    payload = request.get_json(silent=True) or request.form or {}
    enabled = str(payload.get("enabled", "")).strip().lower() in {"1", "true", "yes", "on"}
    track = get_row_safe(COLLECTIONS["course_seat_tracks"], track_id, allow_missing=True)
    if not track:
        abort(404)

    try:
        updated = _toggle_track(track, enabled)
    except AppwriteException as exc:
        logger.exception("Failed to update course tracking row")
        return jsonify({"error": "Unable to update course tracking.", "message": _sanitize_admin_error(exc)}), 500

    _log_admin_action(
        "toggle_course_tracking",
        f"track:{track_id}",
        metadata={"enabled": enabled, "scope": "track", "track_id": track_id},
        color="green" if enabled else "yellow",
    )
    return jsonify({"status": "ok", "track": _serialize_admin_track(updated)})


@admin_bp.route("/admin/course-tracking/refresh-interval", methods=["POST"])
@admin_required
def admin_course_tracking_refresh_interval():
    payload = request.get_json(silent=True) or request.form or {}
    try:
        minutes = int(payload.get("minutes"))
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid refresh interval."}), 400
    if minutes != 5:
        return jsonify({"error": "Invalid refresh interval."}), 400

    try:
        set_course_tracking_refresh_minutes(minutes)
    except Exception as exc:
        logger.exception("Failed to update course tracking refresh interval")
        return jsonify({"error": "Unable to update course tracking refresh interval.", "message": _sanitize_admin_error(exc)}), 500

    scheduler_updated = update_course_tracking_refresh_interval(minutes)

    _log_admin_action(
        "course_tracking_refresh_interval",
        "course_tracking",
        metadata={"refresh_interval_minutes": minutes, "scheduler_updated": scheduler_updated},
        color="gray",
    )
    return jsonify({
        "status": "ok",
        "refresh_interval_minutes": minutes,
        "scheduler_updated": scheduler_updated,
    })


@admin_bp.route("/admin/course-tracking/test-chem-150", methods=["POST"])
@admin_required
def admin_course_tracking_test_chem_150():
    from services.course_tracking import check_course_seat_tracks, get_last_course_tracking_poll

    try:
        notified_count = check_course_seat_tracks(
            term="Fall_2026",
            subject="CHEM",
            catalog="150",
            poll_source="manual_admin_test",
        )
    except Exception as exc:
        logger.exception("CHEM 150 tracking diagnostic failed")
        return jsonify({
            "status": "error",
            "request": {"term": "Fall_2026", "subject": "CHEM", "catalog": "150"},
            "error": _sanitize_admin_error(exc),
        }), 500

    poll = get_last_course_tracking_poll()
    return jsonify({
        "status": "ok",
        "request": {"term": "Fall_2026", "subject": "CHEM", "catalog": "150"},
        "notifications_sent": notified_count,
        "poll": poll,
    })


@admin_bp.route("/admin/course-tracking-run-now", methods=["POST"])
@admin_required
def admin_course_tracking_run_now():
    from services.course_tracking import check_course_seat_tracks

    try:
        notified_count = check_course_seat_tracks()
    except Exception as exc:
        logger.exception("Manual course tracking run failed")
        return jsonify({
            "error": "manual_course_tracking_run_failed",
            "message": _sanitize_admin_error(exc),
            "diagnostics": _course_tracking_diagnostics(),
        }), 500

    _log_admin_action(
        "manual_course_tracking_run",
        "course_tracking",
        metadata={"notifications_sent": notified_count},
        color="yellow",
    )
    return jsonify({
        "status": "ok",
        "notifications_sent": notified_count,
        "diagnostics": _course_tracking_diagnostics(),
    })


@admin_bp.route("/admin/requests")
@admin_required
def admin_requests():
    status = request.args.get("status")
    tab = "channel-requests" if status else "course-tracking"
    return redirect(url_for("admin.admin_auth", tab=tab, status=status, error=request.args.get("error")))


APSWIFTLY_CONTROL_ACTIONS = {
    "reload": "apswiftly_reload",
    "refresh-slash": "apswiftly_refresh_slash",
    "shutdown": "apswiftly_shutdown",
    "restart": "apswiftly_service_restart",
}

APSWIFTLY_CONFIRM_ACTIONS = {
    "shutdown": "SHUTDOWN",
    "restart": "RESTART",
}


@admin_bp.route("/admin/apswiftly")
@admin_required
def admin_apswiftly():
    status_payload = apswiftly_status()
    _log_admin_action("view_admin_apswiftly", "APSwiftly")
    return render_template(
        "admin_apswiftly.html",
        apswiftly_status=status_payload,
        theme_preference=_theme_preference(),
        pending_request_count=_pending_admin_request_count(),
        active_admin_page="apswiftly",
        breadcrumbs=[("Admin", url_for("admin.admin_index")), ("APSwiftly", None)],
    )


@admin_bp.route("/admin/apswiftly/status")
@admin_required
def admin_apswiftly_status():
    return jsonify(apswiftly_status())


@admin_bp.route("/admin/apswiftly/<action>", methods=["POST"])
@admin_required
def admin_apswiftly_control(action):
    if action not in APSWIFTLY_CONTROL_ACTIONS:
        abort(404)

    payload = request.get_json(silent=True) or {}
    required_confirm = APSWIFTLY_CONFIRM_ACTIONS.get(action)
    if required_confirm:
        confirm = str(payload.get("confirm") or "").strip().upper()
        if confirm != required_confirm:
            return jsonify(
                {
                    "error": "Confirmation required.",
                    "message": f'Type "{required_confirm}" to confirm this action.',
                }
            ), 400

    metadata = {
        "apswiftly_action": action,
        "command_mode": "fixed",
    }
    handler = getattr(apswiftly_control_service, APSWIFTLY_CONTROL_ACTIONS[action])
    try:
        result = handler()
    except APSwiftlyControlError as exc:
        message = _sanitize_admin_error(exc)
        _log_admin_action(
            f"apswiftly_{action.replace('-', '_')}",
            "APSwiftly",
            metadata={**metadata, "result": "failed"},
            color="red",
        )
        return jsonify({"error": "APSwiftly command failed.", "message": message}), exc.status_code
    except Exception as exc:
        logger.exception("Failed APSwiftly action: %s", action)
        message = _sanitize_admin_error(exc)
        _log_admin_action(
            f"apswiftly_{action.replace('-', '_')}",
            "APSwiftly",
            metadata={**metadata, "result": "failed"},
            color="red",
        )
        return jsonify({"error": "Unable to run APSwiftly command.", "message": message}), 500

    action_color = "green"
    if action == "shutdown":
        action_color = "red"
    elif action in {"restart", "reload", "refresh-slash"}:
        action_color = "yellow"

    _log_admin_action(
        f"apswiftly_{action.replace('-', '_')}",
        "APSwiftly",
        metadata={**metadata, "result": "success", "response": result if isinstance(result, dict) else {}},
        color=action_color,
    )
    return jsonify({"status": "ok", "action": action, **(result if isinstance(result, dict) else {})})


@admin_bp.route("/admin/course-tracking/spring-toggle", methods=["POST"])
@admin_required
def admin_course_tracking_spring_toggle():
    payload = request.get_json(silent=True) or request.form or {}
    enabled = str(payload.get("enabled", "")).strip().lower() in {"1", "true", "yes", "on"}
    try:
        set_spring_course_tracking_open(enabled)
    except Exception as exc:
        logger.exception("Failed to update Spring course tracking gate")
        return jsonify({"error": "Unable to update Spring course tracking.", "message": _sanitize_admin_error(exc)}), 500

    _log_admin_action(
        "spring_course_tracking_toggle",
        "spring_course_tracking",
        metadata={"enabled": enabled},
        color="green" if enabled else "yellow",
    )
    return jsonify({"status": "ok", "enabled": enabled})


@admin_bp.route("/admin/requests/<request_id>/approve", methods=["POST"])
@admin_required
def approve_admin_request(request_id):
    request_row = get_row_safe(COLLECTIONS["admin_requests"], request_id, allow_missing=True)
    if not request_row:
        abort(404)
    if request_row.get("request_type") != "uni_channel_approval":
        return _redirect_requests(error="Unsupported request type.")
    school_key = request_row.get("school_key")
    school_name = request_row.get("school_name")
    if not school_key or not school_name:
        return _redirect_requests(error="Request is missing school data.")
    now = format_datetime(datetime.now(timezone.utc))
    try:
        channel = create_university_channel(school_key, school_name)
        update_row_safe(
            COLLECTIONS["admin_requests"],
            request_id,
            {
                "status": "approved",
                "resolved_by": str(current_user.id),
                "resolved_at": now,
                "updated_at": now,
            },
        )
        emit_chat_event(
            "university",
            school_key,
            "university_approved",
            channel_id=_row_id(channel),
            actor_id=str(current_user.id),
        )
        sync_chat_presence_labels_for_school(school_key)
    except AppwriteException:
        logger.exception("Failed to approve admin request")
        return _redirect_requests(error="Unable to approve request.")
    _log_admin_action(
        "approve_admin_request",
        f"request:{request_id}",
        metadata={"request_type": request_row.get("request_type"), "school_name": school_name},
        color="green",
    )
    return _redirect_requests(notice="request-approved")


@admin_bp.route("/admin/requests/<request_id>/deny", methods=["POST"])
@admin_required
def deny_admin_request(request_id):
    request_row = get_row_safe(COLLECTIONS["admin_requests"], request_id, allow_missing=True)
    if not request_row:
        abort(404)
    now = format_datetime(datetime.now(timezone.utc))
    try:
        update_row_safe(
            COLLECTIONS["admin_requests"],
            request_id,
            {
                "status": "denied",
                "resolved_by": str(current_user.id),
                "resolved_at": now,
                "updated_at": now,
            },
        )
        if request_row.get("school_key"):
            emit_chat_event(
                "university",
                request_row.get("school_key"),
                "university_denied",
                actor_id=str(current_user.id),
            )
            sync_chat_presence_labels_for_school(request_row.get("school_key"))
    except AppwriteException:
        logger.exception("Failed to deny admin request")
        return _redirect_requests(error="Unable to deny request.")
    _log_admin_action(
        "deny_admin_request",
        f"request:{request_id}",
        metadata={"request_type": request_row.get("request_type"), "school_name": request_row.get("school_name")},
        color="yellow",
    )
    return _redirect_requests(notice="request-denied")


@admin_bp.route("/admin/<user_id>/tier", methods=["POST"])
@admin_required
def update_user_tier(user_id):
    payload = request.get_json(silent=True) or request.form
    tier = normalize_tier(payload.get("tier"))
    raw_tier = str(payload.get("tier") or "").strip().lower().replace("-", "_").replace(" ", "_")
    if raw_tier not in TIER_KEYS:
        if request.is_json:
            return jsonify({"error": "Choose a valid tier.", "code": "invalid_tier"}), 400
        return redirect(url_for("admin.admin_detail", user_id=user_id, error="invalid-tier"))

    user_doc = get_row_safe(COLLECTIONS["users"], user_id, allow_missing=True)
    if not user_doc:
        abort(404)
    previous_tier = normalize_tier(user_doc.get("tier"))
    try:
        updated = update_row_safe(COLLECTIONS["users"], user_id, {"tier": tier})
    except AppwriteException:
        logger.exception("Failed to update tier for %s", user_id)
        if request.is_json:
            return jsonify({"error": "Unable to update user tier."}), 500
        return redirect(url_for("admin.admin_detail", user_id=user_id, error="tier-update-failed"))

    try:
        invites.record_tier_change(user_id, previous_tier, tier)
    except Exception:
        logger.exception("Failed to record invited user tier change")

    _log_admin_action(
        "update_user_tier",
        f"user:{user_id}",
        target_user=user_doc,
        metadata={"previous_tier": previous_tier, "tier": tier},
        color="green",
    )
    if request.is_json:
        return jsonify({"status": "ok", "tier": tier, "user": _user_summary(updated)})
    section = request.form.get("section") or "overview"
    return redirect(url_for("admin.admin_detail", user_id=user_id, section=section, status="tier-updated"))


@admin_bp.route("/admin/<user_id>")
@admin_required
def admin_detail(user_id):
    section = (request.args.get("section") or "overview").strip().lower()
    if section not in ALLOWED_SECTIONS:
        section = "overview"

    user_doc = get_row_safe(COLLECTIONS["users"], user_id, allow_missing=True)
    if not user_doc:
        abort(404)

    overview_counts = None
    account_data = None
    if section == "overview":
        account_data = _fetch_account(user_id)
        overview_counts = {
            "files": _safe_count_rows(COLLECTIONS["shared_files"], [Query.equal("user_id", [user_id])]),
            "folders": _safe_count_rows(COLLECTIONS["file_folders"], [Query.equal("user_id", [user_id])]),
            "notes": _safe_count_rows(COLLECTIONS["notes"], [Query.equal("user_id", [user_id])]),
            "calendar_cache": count_calendar_rows(COLLECTIONS["calendar_cache"], [Query.equal("user_id", [user_id])]),
            "calendar_feeds": count_calendar_rows(COLLECTIONS["calendar_feeds"], [Query.equal("user_id", [user_id])]),
            "courses": _safe_count_rows(COLLECTIONS["user_courses"], [Query.equal("user_id", [user_id])]),
            "seat_tracks": _safe_count_rows(COLLECTIONS["course_seat_tracks"], [Query.equal("user_id", [user_id])]),
            **_chat_count_summary(user_id),
        }

    section_data = _load_section(section, user_id)
    try:
        tier_entitlements = entitlement_payload(user_id, user_doc)
    except EntitlementError:
        logger.exception("Failed to load tier entitlements for %s", user_id)
        tier_entitlements = None

    _log_admin_action(
        "view_admin_detail",
        f"user:{user_id}",
        target_user=user_doc,
        metadata={"section": section},
    )

    return render_template(
        "admin_detail.html",
        user=_user_summary(user_doc),
        profile=_admin_profile_payload(user_doc),
        user_doc=user_doc,
        account_data=account_data,
        account_summary_rows=_account_summary_rows(account_data),
        overview_counts=overview_counts,
        section=section,
        section_data=section_data,
        tier_entitlements=tier_entitlements,
        status=request.args.get("status"),
        error=request.args.get("error"),
        theme_preference=_theme_preference(),
        pending_request_count=_pending_admin_request_count(),
        active_admin_page="auth",
        return_to=_admin_detail_return_url(request.args.get("return_to")),
        breadcrumbs=[
            ("Admin", url_for("admin.admin_index")),
            ("Auth", url_for("admin.admin_auth")),
            ((_user_summary(user_doc).get("name") or _row_id(user_doc) or "User"), None),
        ],
    )


@admin_bp.route("/admin/<user_id>.json", methods=["POST"])
@admin_required
def admin_detail_export(user_id):
    payload = _export_payload(user_id)
    if not payload:
        abort(404)
    _log_admin_action(
        "export_admin_detail",
        f"user:{user_id}",
        target_user=payload.get("user"),
        metadata={"exported_sections": sorted(payload.keys())},
        color="yellow",
    )
    return jsonify(payload)


@admin_bp.route("/admin/<user_id>/onboarding", methods=["POST"])
@admin_required
def update_onboarding(user_id):
    section = (request.form.get("section") or "overview").strip() or "overview"
    return_to = request.form.get("return_to")
    onboarding_complete = bool(request.form.get("onboarding_complete"))
    step_raw = (request.form.get("onboarding_step") or "").strip()
    try:
        onboarding_step = int(step_raw) if step_raw else 1
    except (TypeError, ValueError):
        onboarding_step = 1

    try:
        update_row_safe(
            COLLECTIONS["users"],
            user_id,
            {
                "onboarding_complete": onboarding_complete,
                "onboarding_step": onboarding_step,
            },
        )
    except AppwriteException:
        logger.exception("Failed to update onboarding for %s", user_id)
        return _redirect_detail(user_id, section, error="Unable to update onboarding.", return_to=return_to)

    _log_admin_action(
        "update_onboarding",
        f"user:{user_id}",
        target_user={"$id": user_id},
        metadata={
            "onboarding_complete": onboarding_complete,
            "onboarding_step": onboarding_step,
        },
        color="green" if onboarding_complete else "gray",
    )
    return _redirect_detail(user_id, section, status="onboarding-updated", return_to=return_to)


@admin_bp.route("/admin/<user_id>/reset-ics-token", methods=["POST"])
@admin_required
def reset_ics_token(user_id):
    section = (request.form.get("section") or "settings").strip() or "settings"
    return_to = request.form.get("return_to")
    token = secrets.token_urlsafe(32)
    now = format_datetime(datetime.utcnow())

    try:
        settings = first_row(
            COLLECTIONS["user_settings"],
            [Query.equal("user_id", [user_id])],
        )
        if settings:
            update_row_safe(
                COLLECTIONS["user_settings"],
                _row_id(settings),
                {"ics_secret_token": token, "updated_at": now},
            )
        else:
            create_row_safe(
                COLLECTIONS["user_settings"],
                row_id=str(user_id),
                data={**_settings_defaults(str(user_id)), "ics_secret_token": token, "updated_at": now},
            )
    except AppwriteException:
        logger.exception("Failed to reset ICS token for %s", user_id)
        return _redirect_detail(user_id, section, error="Unable to reset token.", return_to=return_to)

    _log_admin_action("reset_ics_token", f"user:{user_id}", target_user={"$id": user_id}, color="yellow")
    return _redirect_detail(user_id, section, status="token-reset", return_to=return_to)


@admin_bp.route("/admin/<user_id>/seat-tracks/disable", methods=["POST"])
@admin_required
def disable_seat_tracks(user_id):
    section = (request.form.get("section") or "seat_tracks").strip() or "seat_tracks"
    return_to = request.form.get("return_to")
    now = format_datetime(datetime.utcnow())
    updated = 0

    try:
        tracks = list_rows_all(
            COLLECTIONS["course_seat_tracks"],
            [Query.equal("user_id", [user_id])],
        )
        for track in tracks:
            update_row_safe(
                COLLECTIONS["course_seat_tracks"],
                _row_id(track),
                {"enabled": False, "updated_at": now},
            )
            updated += 1
    except AppwriteException:
        logger.exception("Failed to disable seat tracks for %s", user_id)
        return _redirect_detail(user_id, section, error="Unable to disable seat tracks.", return_to=return_to)

    _log_admin_action(
        "disable_seat_tracks",
        f"user:{user_id}",
        target_user={"$id": user_id},
        metadata={"tracks_disabled": updated},
        color="yellow",
    )
    return _redirect_detail(user_id, section, status=f"disabled-{updated}", return_to=return_to)


@admin_bp.route("/admin/<user_id>/files/<file_id>/delete", methods=["POST"])
@admin_required
def delete_shared_file(user_id, file_id):
    section = (request.form.get("section") or "files").strip() or "files"
    return_to = request.form.get("return_to")
    shared_file = get_row_safe(COLLECTIONS["shared_files"], file_id, allow_missing=True)
    if not shared_file or shared_file.get("user_id") != user_id:
        abort(404)

    try:
        _delete_shared_file_row(shared_file)
    except AppwriteException:
        logger.exception("Failed to delete shared file %s", file_id)
        return _redirect_detail(user_id, section, error="Unable to delete file.", return_to=return_to)

    _log_admin_action(
        "delete_shared_file",
        f"user:{user_id} file:{file_id}",
        target_user={"$id": user_id},
        metadata={"resource_type": "shared_file", "resource_id": file_id},
        color="yellow",
    )
    return _redirect_detail(user_id, section, status="file-deleted", return_to=return_to)


@admin_bp.route("/admin/<user_id>/folders/<folder_id>/delete", methods=["POST"])
@admin_required
def delete_shared_folder(user_id, folder_id):
    section = (request.form.get("section") or "files").strip() or "files"
    return_to = request.form.get("return_to")
    folder = get_row_safe(COLLECTIONS["file_folders"], folder_id, allow_missing=True)
    if not folder or folder.get("user_id") != user_id:
        abort(404)

    try:
        folder_ids = _collect_folder_tree_ids(user_id, folder_id)
        files = list_rows_all(
            COLLECTIONS["shared_files"],
            [Query.equal("user_id", [user_id])],
        )
        for shared_file in files:
            if shared_file.get("folder_id") in folder_ids:
                _delete_shared_file_row(shared_file)
        for target_id in reversed(folder_ids):
            delete_row_safe(COLLECTIONS["file_folders"], target_id)
    except AppwriteException:
        logger.exception("Failed to delete shared folder %s", folder_id)
        return _redirect_detail(user_id, section, error="Unable to delete folder.", return_to=return_to)

    _log_admin_action(
        "delete_shared_folder",
        f"user:{user_id} folder:{folder_id}",
        target_user={"$id": user_id},
        metadata={"resource_type": "file_folder", "resource_id": folder_id},
        color="yellow",
    )
    return _redirect_detail(user_id, section, status="folder-deleted", return_to=return_to)


@admin_bp.route("/admin/users/<user_id>/files/<file_id>/download")
@admin_required
def download_shared_file(user_id, file_id):
    shared_file = get_row_safe(COLLECTIONS["shared_files"], file_id, allow_missing=True)
    if not shared_file or shared_file.get("user_id") != user_id:
        abort(404)

    storage_file_id = shared_file.get("storage_file_id")
    if not storage_file_id:
        abort(404)

    bucket_id = shared_file.get("storage_bucket_id") or FILE_SHARE_BUCKET_ID
    try:
        data = _storage_service().get_file_download(bucket_id, storage_file_id)
    except AppwriteException as exc:
        if _status_code(exc) == 404:
            abort(404)
        logger.exception("Failed to download shared file %s", file_id)
        abort(500)

    try:
        update_row_safe(
            COLLECTIONS["shared_files"],
            _row_id(shared_file),
            {
                "downloaded_count": int(shared_file.get("downloaded_count") or 0) + 1,
                "updated_at": format_datetime(datetime.utcnow()),
            },
        )
    except AppwriteException:
        logger.exception("Failed to update download count for %s", file_id)

    return send_file(
        io.BytesIO(data),
        as_attachment=True,
        download_name=shared_file.get("original_filename") or "download",
        mimetype=shared_file.get("mime_type") or "application/octet-stream",
    )


@admin_bp.route("/admin/<user_id>/delete", methods=["POST"])
@admin_required
def delete_user(user_id):
    return_to = request.form.get("return_to")
    confirm = (request.form.get("confirm") or "").strip()
    if confirm != "DELETE":
        return _redirect_detail(user_id, "overview", error="Type DELETE to confirm removal.", return_to=return_to)

    user_doc = get_row_safe(COLLECTIONS["users"], user_id, allow_missing=True)
    if not user_doc:
        abort(404)

    avatar_file_id = user_doc.get("avatar_file_id")

    result = _delete_user_rows(user_id)
    errors = result if isinstance(result, list) else []
    if errors:
        logger.error("Incomplete user data deletion for %s: %s", user_id, errors)
        return _redirect_detail(user_id, "overview", error="Unable to delete all user data.", return_to=return_to)

    try:
        Users(appwrite_client).delete(user_id)
    except Exception:
        logger.exception("Failed to delete Appwrite account for %s", user_id)
        return _redirect_detail(user_id, "overview", error="Unable to delete Appwrite user.", return_to=return_to)

    _log_admin_action("delete_user", f"user:{user_id}", target_user=user_doc, color="red")

    if avatar_file_id:
        try:
            _storage_service().delete_file(PROFILE_AVATAR_BUCKET_ID, avatar_file_id)
        except AppwriteException as exc:
            if _status_code(exc) != 404:
                logger.exception("Failed to delete avatar file %s", avatar_file_id)

    push_toast("User deleted.", type="success")
    return redirect(_admin_detail_return_url(return_to))
