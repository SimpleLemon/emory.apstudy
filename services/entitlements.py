"""Shared tier definitions, usage accounting, and quota enforcement."""

import logging
import sqlite3

from appwrite.exception import AppwriteException
from appwrite.query import Query

from appwrite_client import COLLECTIONS
from appwrite_helpers import first_row, list_rows_all
from services.app_config import get_config, set_config
from services.database import db_connection
from services.environment_config import runtime_environment_config


logger = logging.getLogger(__name__)

TIER_CONFIG_KEY = "tier_entitlements"
TIER_KEYS = ("free", "grade_a", "grade_aa", "developer")
TIER_LABELS = {
    "free": "Free",
    "grade_a": "Grade A",
    "grade_aa": "Grade AA",
    "developer": "Developer",
}
LIMIT_KEYS = (
    "storage_bytes",
    "max_file_size_bytes",
    "max_chat_attachment_size_bytes",
    "max_upload_files",
    "max_saved_courses",
    "max_seat_tracks",
    "max_calendar_feeds",
    "max_notes",
    "max_focus_playlists",
)
TRACK_INTERVALS_KEY = "seat_track_intervals_minutes"
UNLIMITED = None
GIB = 1024 ** 3
MIB = 1024 ** 2

DEFAULT_TIER_DEFINITIONS = {
    "free": {
        "label": TIER_LABELS["free"],
        "storage_bytes": 1 * GIB,
        "max_file_size_bytes": 25 * MIB,
        "max_chat_attachment_size_bytes": 10 * MIB,
        "max_upload_files": 2,
        "max_saved_courses": 5,
        "max_seat_tracks": 1,
        "max_calendar_feeds": 2,
        "max_notes": 10,
        "max_focus_playlists": 2,
        TRACK_INTERVALS_KEY: [30],
    },
    "grade_a": {
        "label": TIER_LABELS["grade_a"],
        "storage_bytes": 10 * GIB,
        "max_file_size_bytes": 50 * MIB,
        "max_chat_attachment_size_bytes": 50 * MIB,
        "max_upload_files": 5,
        "max_saved_courses": 15,
        "max_seat_tracks": 5,
        "max_calendar_feeds": 5,
        "max_notes": 50,
        "max_focus_playlists": 3,
        TRACK_INTERVALS_KEY: [15, 30],
    },
    "grade_aa": {
        "label": TIER_LABELS["grade_aa"],
        "storage_bytes": 15 * GIB,
        "max_file_size_bytes": 100 * MIB,
        "max_chat_attachment_size_bytes": 50 * MIB,
        "max_upload_files": 10,
        "max_saved_courses": 50,
        "max_seat_tracks": 25,
        "max_calendar_feeds": 10,
        "max_notes": 250,
        "max_focus_playlists": 6,
        TRACK_INTERVALS_KEY: [5, 15, 30],
    },
    "developer": {
        "label": TIER_LABELS["developer"],
        **{key: UNLIMITED for key in LIMIT_KEYS},
        "max_chat_attachment_size_bytes": 50 * MIB,
        TRACK_INTERVALS_KEY: [5, 15, 30],
    },
}

TIER_BADGES = {
    "free": None,
    "grade_a": {
        "label": "Grade A",
        "asset": "/static/images/tiers/grade-a-egg.png",
        "class_name": "tier-badge--grade-a",
    },
    "grade_aa": {
        "label": "Grade AA",
        "asset": "/static/images/tiers/grade-aa-egg.png",
        "class_name": "tier-badge--grade-aa",
    },
    "developer": {
        "label": "Developer",
        "asset": "/static/images/tiers/developer-dinosaur-egg.png",
        "class_name": "tier-badge--developer",
    },
}


class EntitlementError(RuntimeError):
    """Base error for entitlement reads and validation."""


class EntitlementLimitError(EntitlementError):
    """Raised when a user would exceed an effective tier limit."""

    def __init__(self, resource, current, requested, limit):
        self.resource = resource
        self.current = current
        self.requested = requested
        self.limit = limit
        label = resource.replace("_", " ")
        super().__init__(
            f"Your {label} limit is {limit:,}; current usage is {current:,} and this action would use {requested:,}."
        )

    def payload(self):
        return {
            "error": str(self),
            "code": "tier_limit",
            "resource": self.resource,
            "limit": self.limit,
            "current": self.current,
            "requested": self.requested,
        }


def normalize_tier(value):
    normalized = str(value or "free").strip().lower().replace("-", "_").replace(" ", "_")
    return normalized if normalized in TIER_KEYS else "free"


def normalize_limit(value):
    if value is None or value is False or str(value).strip().lower() in {"", "none", "null", "unlimited", "off"}:
        return UNLIMITED
    try:
        value = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Limits must be non-negative integers or Unlimited.") from exc
    if value < 0:
        raise ValueError("Limits must be non-negative integers or Unlimited.")
    return value


def normalize_definitions(raw):
    source = raw if isinstance(raw, dict) else {}
    normalized = {}
    for tier in TIER_KEYS:
        defaults = DEFAULT_TIER_DEFINITIONS[tier]
        candidate = source.get(tier) if isinstance(source.get(tier), dict) else {}
        values = {"label": defaults["label"]}
        for key in LIMIT_KEYS:
            values[key] = normalize_limit(candidate[key]) if key in candidate else defaults[key]
        raw_intervals = candidate.get(TRACK_INTERVALS_KEY, defaults[TRACK_INTERVALS_KEY])
        if not isinstance(raw_intervals, (list, tuple)):
            raise ValueError("Seat track intervals must be a list of minutes.")
        try:
            intervals = sorted({int(item) for item in raw_intervals})
        except (TypeError, ValueError) as exc:
            raise ValueError("Seat track intervals must contain minutes.") from exc
        if not intervals or any(item not in (5, 15, 30) for item in intervals):
            raise ValueError("Seat track intervals must contain 5, 15, or 30 minutes.")
        values[TRACK_INTERVALS_KEY] = intervals
        normalized[tier] = values
    return normalized


def get_tier_definitions():
    return normalize_definitions(get_config(TIER_CONFIG_KEY, DEFAULT_TIER_DEFINITIONS))


def save_tier_definitions(value):
    if not isinstance(value, dict) or set(value) - set(TIER_KEYS):
        raise ValueError("Tier configuration must contain only supported tier identifiers.")
    normalized = normalize_definitions(value)
    return set_config(TIER_CONFIG_KEY, normalized)


def tier_metadata(tier, definitions=None):
    normalized = normalize_tier(tier)
    definition = (definitions or get_tier_definitions())[normalized]
    badge = TIER_BADGES.get(normalized)
    return {
        "key": normalized,
        "label": definition["label"],
        "badge": badge,
        "limits": definition,
    }


def _rows(table_id, queries):
    try:
        return list_rows_all(table_id, queries)
    except AppwriteException as exc:
        logger.exception("Failed to read entitlement usage from %s", table_id)
        raise EntitlementError("Unable to calculate current account limits.") from exc


def _calendar_feed_count(user_id):
    settings = first_row(
        COLLECTIONS["user_settings"],
        [Query.equal("user_id", [str(user_id)])],
    )
    if not settings:
        return 0
    count = 1 if str(settings.get("canvas_ical_url") or "").strip() else 0
    raw_urls = settings.get("other_ical_urls_json") or "[]"
    try:
        import json

        other_urls = json.loads(raw_urls) if isinstance(raw_urls, str) else raw_urls
    except (TypeError, ValueError):
        other_urls = []
    return count + len([item for item in other_urls if isinstance(item, str) and item.strip()])


def _focus_playlist_count(user_id):
    try:
        with db_connection() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS count FROM focus_playlists WHERE owner_type='user' AND user_id=?",
                (str(user_id),),
            ).fetchone()
    except sqlite3.OperationalError as exc:
        logger.exception("Failed to read focus playlist usage for user %s", user_id)
        raise EntitlementError("Unable to calculate current account limits.") from exc
    return int(row["count"] if row else 0)


def usage_for_user(user_id, user_doc=None):
    normalized_id = str(user_id)
    files = _rows(COLLECTIONS["shared_files"], [Query.equal("user_id", [normalized_id])])
    media = _rows(COLLECTIONS["note_media"], [Query.equal("user_id", [normalized_id])])
    chat_attachments = (
        _rows(COLLECTIONS["chat_attachments"], [Query.equal("user_id", [normalized_id])])
        if runtime_environment_config().appwrite_chat_attachments_enabled
        else []
    )
    notes = _rows(COLLECTIONS["notes"], [Query.equal("user_id", [normalized_id])])
    courses = _rows(COLLECTIONS["user_courses"], [Query.equal("user_id", [normalized_id])])
    tracks = _rows(COLLECTIONS["course_seat_tracks"], [Query.equal("user_id", [normalized_id])])
    user = user_doc or first_row(COLLECTIONS["users"], [Query.equal("id", [normalized_id])]) or {}
    storage_bytes = sum(int(row.get("file_size_bytes") or 0) for row in files)
    storage_bytes += sum(int(row.get("file_size_bytes") or 0) for row in media)
    storage_bytes += sum(
        int(row.get("stored_size_bytes") or 0) + int(row.get("preview_size_bytes") or 0)
        for row in chat_attachments
        if row.get("status") in {"pending", "active"}
    )
    storage_bytes += int(user.get("avatar_file_size_bytes") or 0)
    return {
        "storage_bytes": storage_bytes,
        "files": len(files),
        "notes": len(notes),
        "saved_courses": len(courses),
        "seat_tracks": len([row for row in tracks if row.get("enabled", True)]),
        "calendar_feeds": _calendar_feed_count(normalized_id),
        "focus_playlists": _focus_playlist_count(normalized_id),
    }


def entitlements_for_user(user_id, user_doc=None, *, include_usage=True):
    user = user_doc
    if user is None:
        user = first_row(COLLECTIONS["users"], [Query.equal("id", [str(user_id)])]) or {}
    definitions = get_tier_definitions()
    tier = normalize_tier(user.get("tier"))
    limits = definitions[tier]
    usage = usage_for_user(user_id, user) if include_usage else None
    usage_limit_pairs = {
        "notes": "max_notes",
        "saved_courses": "max_saved_courses",
        "seat_tracks": "max_seat_tracks",
        "calendar_feeds": "max_calendar_feeds",
        "focus_playlists": "max_focus_playlists",
    }
    return {
        **tier_metadata(tier, definitions),
        "usage": usage,
        "over_limit": {
            key: usage[key] > limits[limit_key]
            for key, limit_key in usage_limit_pairs.items()
            if limits.get(limit_key) is not None
        } if usage else {},
    }


def request_entitlements(user):
    """Load entitlements for a Flask user; keep direct unit-call stubs permissive."""
    if not hasattr(user, "_data"):
        return {
            **tier_metadata("developer"),
            "usage": {
                "storage_bytes": 0,
                "files": 0,
                "notes": 0,
                "saved_courses": 0,
                "seat_tracks": 0,
                "calendar_feeds": 0,
                "focus_playlists": 0,
            },
            "over_limit": {},
        }
    return entitlements_for_user(str(user.id), user._data)


def check_limit(entitlements, limit_key, current, additional=1):
    limit = entitlements["limits"].get(limit_key)
    if limit is None:
        return
    requested = int(current) + int(additional)
    if requested > limit:
        resource = limit_key.removeprefix("max_")
        raise EntitlementLimitError(resource, int(current), requested, limit)


def check_storage(entitlements, additional_bytes):
    limit = entitlements["limits"].get("storage_bytes")
    if limit is None:
        return
    current = int((entitlements.get("usage") or {}).get("storage_bytes") or 0)
    requested = current + int(additional_bytes or 0)
    if requested > limit:
        raise EntitlementLimitError("storage bytes", current, requested, limit)


def entitlement_payload(user_id, user_doc=None):
    data = entitlements_for_user(user_id, user_doc)
    limits = data["limits"]
    usage = data["usage"] or {}
    return {
        "tier": data["key"],
        "label": data["label"],
        "badge": data["badge"],
        "limits": limits,
        "usage": usage,
        "over_limit": data["over_limit"],
        "storage_usage_bytes": usage.get("storage_bytes", 0),
        "storage_limit_bytes": limits.get("storage_bytes"),
        "storage_remaining_bytes": (
            None if limits.get("storage_bytes") is None
            else max(0, limits["storage_bytes"] - usage.get("storage_bytes", 0))
        ),
    }
