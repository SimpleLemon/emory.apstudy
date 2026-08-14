"""Calendar event serialization, source metadata, and share helpers."""

import hashlib
import hmac
import json
import logging
import re
import secrets
import sqlite3
import uuid
from collections import Counter
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse, urlsplit, urlunparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import url_for
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
from services.calendar_store import (
    calendar_connection,
    create_calendar_row,
    delete_calendar_row,
    first_calendar_row,
    list_calendar_rows_all,
    update_calendar_row,
)
from services.calendar_urls import (
    MAX_OTHER_CALENDAR_URLS,
    iter_valid_other_calendar_urls,
    load_other_calendar_urls,
    normalize_calendar_url as _normalize_calendar_url,
)
from services.feed_fetcher import derive_feed_status
from services.row_utils import row_id as _row_id
from services.settings_defaults import settings_defaults as _settings_defaults
from services.task_calendar import (
    task_calendar_events_for_user,
    task_calendar_source,
    user_has_tasks,
)
from services.time_utils import utcnow_iso
from services.extension_contract import (
    CANVAS_LEGACY_SOURCE_KEY,
    EXTENSION_SOURCE_REF_PREFIX,
    ExtensionContractError,
    canonical_canvas_source_key,
    extension_capability_enabled,
    validate_account_key,
    validate_version,
)


logger = logging.getLogger(__name__)

CANVAS_SOURCE_ID = "canvas"
FEED_SOURCE_PREFIX = "feed:"
LOCAL_SOURCE_PREFIX = "local:"
DEFAULT_LOCAL_SOURCE_ID = f"{LOCAL_SOURCE_PREFIX}default"
DEFAULT_LOCAL_SOURCE_NAME = "Personal"
DEFAULT_CALENDAR_COLOR = "#6366f1"
SIMULATED_CALENDAR_NAME = "Simulated Courses"
CANVAS_CALENDAR_HOST_PREFIX = "canvas."
CANVAS_CALENDAR_HOST_SUFFIX = ".edu"
CANVAS_CALENDAR_PATH_PREFIXES = ("/feeds/calendar", "/feeds/calendars")
CALENDAR_SHARE_CODE_LENGTH = 16
CALENDAR_SHARE_CODE_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
CALENDAR_SHARE_DATE_SCOPES = {"all", "fixed", "rolling"}
CALENDAR_SHARE_MIN_ROLLING_DAYS = 1
CALENDAR_SHARE_MAX_ROLLING_DAYS = 366
PREFERENCES_BATCH_LIMIT = 50
TIMED_EVENT_REMINDERS = {-1, 0, 5, 10, 15, 30, 60, 120, 1440, 2880}
ALL_DAY_EVENT_REMINDERS = {-1, -540, 900, 2340, 9540}

CANVAS_PROVIDER = "canvas"
CANVAS_READ_SCOPES = frozenset({"full_history_upload", "ongoing_read"})
CANVAS_PROJECTION_SCOPES = frozenset({
    "full_history_upload", "ongoing_read", "shares_ics_inclusion",
})
CANVAS_WRITEBACK_SCOPE = "two_way_writeback"
CANVAS_MIRROR_SCOPE = "mirroring"
CANVAS_SHARES_SCOPE = "shares_ics_inclusion"
CANVAS_BATCH_ITEM_LIMIT = 100
CANVAS_BATCH_BYTES_LIMIT = 512 * 1024
CANVAS_LEASE_MINUTES = 10
CANVAS_SOURCE_STATUSES = frozenset({"active", "paused", "archived"})
CANVAS_ROUTE_STATES = ("incomplete", "completed")
CANVAS_COMPLETION_STATUSES = ("incomplete", "completed")
CANVAS_COMPLETION_SOURCES = frozenset({"canvas", "extension"})
CANVAS_ALLOWED_ITEM_TYPES = frozenset({
    "assignment", "quiz", "discussion_topic", "planner_note", "calendar_event",
})
CANVAS_REJECTED_ITEM_TYPES = frozenset({
    "announcement", "announcements", "unknown",
})
CANVAS_WRITEBACK_STATES = (
    "waiting_for_canvas_session", "queued", "applied", "unsupported", "forbidden",
    "conflict", "retryable_failed", "cancelled",
)
CANVAS_MIRROR_STATES = frozenset({
    "not_requested", "waiting_for_canvas_session", "queued", "applied", "unsupported",
    "forbidden", "conflict", "retryable_failed", "cancelled",
})
CANVAS_SAFE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/~+-]{0,254}$")
CANVAS_RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
CANVAS_IDEMPOTENCY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/=-]{0,254}$")
CANVAS_SOURCE_REF_PATTERN = re.compile(r"^src1:[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
CANVAS_CREDENTIAL_KEYS = frozenset({
    "access_token", "api_key", "authorization", "cookie", "cookies", "credential",
    "credentials", "password", "refresh_token", "secret", "session", "session_cookie",
    "token", "tokens",
})


def _canvas_now():
    return utcnow_iso()


def _canvas_user_id(user_id):
    normalized = str(user_id or "").strip()
    if not normalized:
        raise ExtensionContractError("invalid_user", "Authenticated user id is required.")
    return normalized


def _canvas_json(value, *, field="value", max_bytes=64 * 1024):
    try:
        encoded = json.dumps(value, separators=(",", ":"), sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise ExtensionContractError("invalid_json", f"{field} must be JSON serializable.") from exc
    if len(encoded.encode("utf-8")) > max_bytes:
        raise ExtensionContractError("payload_too_large", f"{field} exceeds the allowed size.")
    return encoded


def _canvas_hash(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canvas_text(value, *, field, max_length, required=False):
    if not isinstance(value, str):
        value = "" if value is None else str(value)
    value = " ".join(value.strip().split()) if field not in {"description", "payload"} else value.strip()
    if required and not value:
        raise ExtensionContractError(f"invalid_{field}", f"{field} is required.")
    if len(value) > max_length:
        raise ExtensionContractError(f"invalid_{field}", f"{field} exceeds the allowed length.")
    return value


def _canvas_id(value, *, field, required=True, pattern=CANVAS_SAFE_ID_PATTERN):
    normalized = _canvas_text(value, field=field, max_length=255, required=required)
    if not normalized:
        return None
    if not pattern.fullmatch(normalized):
        raise ExtensionContractError(f"invalid_{field}", f"{field} contains unsupported characters.")
    return normalized


def _canvas_idempotency_key(value, *, field="idempotency_key"):
    return _canvas_id(value, field=field, pattern=CANVAS_IDEMPOTENCY_PATTERN)


def _canvas_reject_credentials(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).strip().lower() in CANVAS_CREDENTIAL_KEYS:
                raise ExtensionContractError(
                    "credentials_not_allowed",
                    "Canvas credentials, cookies, and tokens are not accepted by this API.",
                )
            _canvas_reject_credentials(child)
    elif isinstance(value, list):
        for child in value:
            _canvas_reject_credentials(child)


def normalize_canvas_origin(value):
    """Normalize a Canvas origin without accepting a feed path or credentials."""
    if not isinstance(value, str) or not value.strip():
        raise ExtensionContractError("invalid_origin", "origin must be an HTTPS Canvas origin.")
    raw = value.strip()
    parsed = urlsplit(raw)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise ExtensionContractError("invalid_origin", "origin must be an HTTPS Canvas origin.")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ExtensionContractError("invalid_origin", "origin has an invalid port.") from exc
    if parsed.username or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ExtensionContractError("invalid_origin", "origin must not include a path, query, fragment, or credentials.")
    hostname = parsed.hostname.lower().rstrip(".")
    if not hostname or ".." in hostname or any(not part for part in hostname.split(".")):
        raise ExtensionContractError("invalid_origin", "origin must contain a valid hostname.")
    normalized_port = "" if port in {None, 443} else f":{port}"
    return f"https://{hostname}{normalized_port}"


def _canvas_completion(value):
    if not isinstance(value, str):
        raise ExtensionContractError("invalid_completion_status", "completion_status is required.")
    normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "complete": "completed",
        "done": "completed",
        "graded": "completed",
        "excused": "completed",
        "submitted": "completed",
        "in_progress": "incomplete",
        "not_started": "incomplete",
        "unsubmitted": "incomplete",
        "missing": "incomplete",
        "late": "incomplete",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in CANVAS_COMPLETION_STATUSES:
        raise ExtensionContractError(
            "invalid_completion_status",
            "completion_status must normalize to incomplete or completed.",
        )
    return normalized


def _canvas_completion_source(value):
    normalized = str(value or "").strip().lower()
    if normalized == "nest":
        normalized = "extension"
    if normalized not in CANVAS_COMPLETION_SOURCES:
        raise ExtensionContractError(
            "invalid_completion_source",
            "completion_source must be canvas or extension.",
        )
    return normalized


def _canvas_item_type(value):
    normalized = str(value or "").strip().lower().replace(" ", "_")
    if normalized in CANVAS_REJECTED_ITEM_TYPES:
        raise ExtensionContractError("item_quarantined", "This Canvas item type is not importable.")
    if normalized not in CANVAS_ALLOWED_ITEM_TYPES:
        raise ExtensionContractError("item_quarantined", "Unknown Canvas item type is not importable.")
    return normalized


def _canvas_source_reference(value):
    normalized = _canvas_text(value, field="source_ref", max_length=160, required=True)
    if normalized.startswith(EXTENSION_SOURCE_REF_PREFIX):
        if not CANVAS_SOURCE_REF_PATTERN.fullmatch(normalized):
            raise ExtensionContractError("invalid_source_ref", "source_ref is invalid.")
        return "row_id", normalized[len(EXTENSION_SOURCE_REF_PREFIX):]
    return "source_id", _canvas_id(normalized, field="source_id", pattern=CANVAS_RUN_ID_PATTERN)


def _canvas_source_ref(row):
    if not row or not row["id"]:
        return None
    row_id = _canvas_text(row["id"], field="source_ref", max_length=128, required=True)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", row_id):
        raise ExtensionContractError("invalid_source_ref", "Stored source reference is invalid.")
    return f"{EXTENSION_SOURCE_REF_PREFIX}{row_id}"


def _canvas_source_row(connection, user_id, source_reference, *, include_archived=False):
    reference_kind, reference_value = _canvas_source_reference(source_reference)
    status_clause = "" if include_archived else " AND status != 'archived'"
    column = "id" if reference_kind == "row_id" else "source_id"
    query = (
        f"SELECT * FROM calendar_import_sources WHERE user_id = ? AND {column} = ?{status_clause}"
    )
    row = connection.execute(query, [_canvas_user_id(user_id), reference_value]).fetchone()
    return dict(row) if row else None


def _canvas_source_internal_payload(row):
    return dict(row) if row else None


def _canvas_source_payload(row):
    if not row:
        return None
    return {
        "id": row["id"],
        "source_ref": _canvas_source_ref(row),
        # Keep the legacy source_id for clients released before source_ref.
        # It is never used as the account binding; all lookups remain user-scoped.
        "source_id": row["source_id"],
        "provider": row["provider"],
        "label": row["label"],
        "status": row["status"],
        "default_mirror_calendar": row["default_mirror_calendar"],
        "sync_state": row["sync_state"],
        "last_sync_started_at": row["last_sync_started_at"],
        "last_sync_completed_at": row["last_sync_completed_at"],
        "last_seen_at": row["last_seen_at"],
        "last_error_code": row["last_error_code"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "archived_at": row["archived_at"],
    }


def _canvas_routing_payload(row, source=None, *, idempotent=False):
    if not row:
        return None
    payload = {
        "id": row["id"],
        "source_id": row["source_id"],
        "source_ref": _canvas_source_ref(source) if source else None,
        "state": row["state"],
        "destination_calendar_id": row["destination_calendar_id"],
        "fallback_calendar_id": row["fallback_calendar_id"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    if idempotent:
        payload["idempotent"] = True
    return payload


def _canvas_consent_from_connection(connection, user_id, account_key, required_scopes=(), version=None):
    user_id = _canvas_user_id(user_id)
    account_key = validate_account_key(account_key)
    if version is not None:
        validate_version(version)
    canonical_key = canonical_canvas_source_key(account_key)
    row = connection.execute(
        "SELECT * FROM calendar_integration_consents "
        "WHERE nest_user_id = ? AND source_key = ? AND account_key = ?",
        [user_id, canonical_key, account_key],
    ).fetchone()
    # One-release compatibility for rows created by the old global-looking
    # source key.  The account predicate remains mandatory, and a canonical
    # row (including a revoked one) always wins so revocation cannot be
    # bypassed through the legacy fallback.
    if row is None:
        row = connection.execute(
            "SELECT * FROM calendar_integration_consents "
            "WHERE nest_user_id = ? AND source_key = ? AND account_key = ?",
            [user_id, CANVAS_LEGACY_SOURCE_KEY, account_key],
        ).fetchone()
    if not row or row["state"] != "active":
        raise ExtensionContractError("consent_required", "Active Canvas consent is required.")
    if version is not None and int(row["version"]) != int(version):
        raise ExtensionContractError("consent_version_mismatch", "Consent version is no longer current.")
    try:
        scopes = json.loads(row["scopes_json"] or "{}")
    except (TypeError, json.JSONDecodeError) as exc:
        raise ExtensionContractError("consent_unavailable", "Stored Canvas consent is invalid.") from exc
    if not isinstance(scopes, dict):
        raise ExtensionContractError("consent_unavailable", "Stored Canvas consent is invalid.")
    missing = [scope for scope in required_scopes if not bool(scopes.get(scope))]
    if missing:
        raise ExtensionContractError(
            "scope_required",
            "Required Canvas consent scope is not granted.",
        )
    return dict(row)


def canvas_consent_status(user_id, account_key, required_scopes=(), version=None):
    with calendar_connection() as connection:
        row = _canvas_consent_from_connection(
            connection,
            user_id,
            account_key,
            required_scopes,
            version,
        )
    return row


def register_canvas_import_source(user_id, payload):
    """Register an extension-owned Canvas account without accepting credentials."""
    _canvas_reject_credentials(payload)
    if not isinstance(payload, dict):
        raise ExtensionContractError("invalid_json", "Request body must be a JSON object.")
    user_id = _canvas_user_id(user_id)
    account_key = validate_account_key(payload.get("account_key"))
    consent_version = payload.get("consent_version", payload.get("version"))
    if consent_version is not None:
        validate_version(consent_version)
    source_id = _canvas_id(payload.get("source_id"), field="source_id", pattern=CANVAS_RUN_ID_PATTERN)
    origin = normalize_canvas_origin(payload.get("origin"))
    provider_user_id = _canvas_text(
        payload.get("provider_user_id", payload.get("canvas_user_id")),
        field="provider_user_id",
        max_length=255,
        required=True,
    )
    label = _canvas_text(payload.get("label") or "Canvas", field="label", max_length=120, required=True)
    default_calendar = payload.get(
        "default_mirror_calendar",
        payload.get("default_calendar_id", payload.get("defaultMirrorCalendar")),
    )
    default_calendar = _canvas_id(
        default_calendar,
        field="default_mirror_calendar",
        required=False,
    )
    now = _canvas_now()

    with calendar_connection() as connection:
        _canvas_consent_from_connection(
            connection,
            user_id,
            account_key,
            CANVAS_READ_SCOPES,
            consent_version,
        )
        by_account = connection.execute(
            "SELECT * FROM calendar_import_sources "
            "WHERE user_id = ? AND provider = 'canvas' AND account_key = ?",
            [user_id, account_key],
        ).fetchone()
        by_source = connection.execute(
            "SELECT * FROM calendar_import_sources WHERE user_id = ? AND source_id = ?",
            [user_id, source_id],
        ).fetchone()
        if by_account and by_account["source_id"] != source_id:
            raise ExtensionContractError(
                "source_account_conflict",
                "This Canvas account is already registered under another source_id.",
            )
        if by_source and by_source["account_key"] != account_key:
            raise ExtensionContractError(
                "source_id_conflict",
                "This source_id belongs to another Canvas account.",
            )

        if by_account:
            connection.execute(
                """UPDATE calendar_import_sources
                   SET nest_user_id = ?, origin = ?, provider_user_id = ?, label = ?,
                       status = 'active', default_mirror_calendar = ?, archived_at = NULL,
                       updated_at = ?, last_error_code = NULL, last_error_message = NULL
                   WHERE id = ?""",
                [user_id, origin, provider_user_id, label, default_calendar, now, by_account["id"]],
            )
            row_id = by_account["id"]
        else:
            row_id = uuid.uuid4().hex
            connection.execute(
                """INSERT INTO calendar_import_sources
                   (id, user_id, nest_user_id, provider, origin, provider_user_id,
                    account_key, source_id, label, status, default_mirror_calendar,
                    sync_state, created_at, updated_at)
                   VALUES (?, ?, ?, 'canvas', ?, ?, ?, ?, ?, 'active', ?, 'idle', ?, ?)""",
                [
                    row_id,
                    user_id,
                    user_id,
                    origin,
                    provider_user_id,
                    account_key,
                    source_id,
                    label,
                    default_calendar,
                    now,
                    now,
                ],
            )
        row = connection.execute(
            "SELECT * FROM calendar_import_sources WHERE id = ?", [row_id]
        ).fetchone()
    return _canvas_source_payload(row)


def list_canvas_import_sources(user_id, *, include_archived=True):
    user_id = _canvas_user_id(user_id)
    query = (
        "SELECT * FROM calendar_import_sources WHERE user_id = ? ORDER BY created_at ASC"
        if include_archived
        else "SELECT * FROM calendar_import_sources WHERE user_id = ? AND status != 'archived' ORDER BY created_at ASC"
    )
    with calendar_connection() as connection:
        rows = connection.execute(query, [user_id]).fetchall()
    return [_canvas_source_payload(dict(row)) for row in rows]


def get_canvas_import_source(user_id, source_id, *, include_archived=True):
    with calendar_connection() as connection:
        row = _canvas_source_row(connection, user_id, source_id, include_archived=include_archived)
    return _canvas_source_payload(row)


def get_canvas_import_source_context(user_id, source_reference, *, include_archived=True):
    """Load a source's private account context for server-side checks only."""
    with calendar_connection() as connection:
        row = _canvas_source_row(
            connection,
            user_id,
            source_reference,
            include_archived=include_archived,
        )
    return _canvas_source_internal_payload(row)


def _archive_canvas_source_in_connection(connection, user_id, source_id, *, now=None, reason="source_archived"):
    now = now or _canvas_now()
    source = _canvas_source_row(connection, user_id, source_id, include_archived=True)
    if not source:
        return {"source": None, "events_archived": 0, "runs_cancelled": 0, "writebacks_cancelled": 0}
    connection.execute(
        """UPDATE calendar_import_sources
           SET status = 'archived', sync_state = 'idle', archived_at = ?, updated_at = ?
           WHERE user_id = ? AND source_id = ?""",
        [now, now, user_id, source_id],
    )
    cache_result = connection.execute(
        """UPDATE calendar_cache
           SET canvas_soft_deleted = 1, canvas_deleted_at = ?, canvas_last_seen_at = ?
           WHERE user_id = ? AND canvas_source_id = ? AND canvas_soft_deleted = 0""",
        [now, now, user_id, source_id],
    )
    run_result = connection.execute(
        """UPDATE calendar_sync_runs
           SET state = 'cancelled', error_code = ?, error_message = ?,
               cancelled_at = ?, updated_at = ?
           WHERE user_id = ? AND source_id = ? AND state = 'active'""",
        [reason, "Canvas source is no longer active.", now, now, user_id, source_id],
    )
    writeback_result = connection.execute(
        """UPDATE calendar_writebacks
           SET state = 'cancelled', error_code = ?, error_message = ?,
               cancelled_at = ?, updated_at = ?
           WHERE user_id = ? AND source_id = ?
             AND state IN ('waiting_for_canvas_session', 'queued', 'retryable_failed')""",
        [reason, "Canvas source is no longer active.", now, now, user_id, source_id],
    )
    connection.execute(
        """UPDATE calendar_event_links
           SET mirror_state = 'cancelled', mirror_error_code = ?,
               mirror_error_message = ?, archived_at = ?, updated_at = ?
           WHERE user_id = ? AND source_id = ? AND archived_at IS NULL""",
        [reason, "Canvas source is no longer active.", now, now, user_id, source_id],
    )
    updated = connection.execute(
        "SELECT * FROM calendar_import_sources WHERE user_id = ? AND source_id = ?",
        [user_id, source_id],
    ).fetchone()
    return {
        "source": _canvas_source_payload(dict(updated)) if updated else None,
        "events_archived": cache_result.rowcount,
        "runs_cancelled": run_result.rowcount,
        "writebacks_cancelled": writeback_result.rowcount,
    }


def revoke_canvas_consent_in_connection(connection, user_id, account_key, *, now=None):
    """Archive all active outputs for one Canvas account in the consent transaction."""
    user_id = _canvas_user_id(user_id)
    account_key = validate_account_key(account_key)
    source_rows = connection.execute(
        """SELECT source_id FROM calendar_import_sources
           WHERE user_id = ? AND provider = 'canvas' AND account_key = ?
             AND status != 'archived'""",
        [user_id, account_key],
    ).fetchall()
    result = {"sources_archived": 0, "events_archived": 0, "runs_cancelled": 0, "writebacks_cancelled": 0}
    for row in source_rows:
        archived = _archive_canvas_source_in_connection(
            connection,
            user_id,
            row["source_id"],
            now=now,
            reason="consent_revoked",
        )
        result["sources_archived"] += 1
        result["events_archived"] += archived["events_archived"]
        result["runs_cancelled"] += archived["runs_cancelled"]
        result["writebacks_cancelled"] += archived["writebacks_cancelled"]
    return result


def archive_canvas_import_source(user_id, source_id):
    source_id = _canvas_id(source_id, field="source_id", pattern=CANVAS_RUN_ID_PATTERN)
    with calendar_connection() as connection:
        result = _archive_canvas_source_in_connection(connection, _canvas_user_id(user_id), source_id)
    return result


def canvas_purge_preflight(user_id, source_id):
    source_id = _canvas_id(source_id, field="source_id", pattern=CANVAS_RUN_ID_PATTERN)
    user_id = _canvas_user_id(user_id)
    with calendar_connection() as connection:
        source = _canvas_source_row(connection, user_id, source_id, include_archived=True)
        if not source:
            return None
        counts = {}
        for table in (
            "calendar_cache", "calendar_sync_runs", "calendar_sync_batches",
            "calendar_import_routing", "calendar_event_links", "calendar_writebacks",
        ):
            column = "canvas_source_id" if table == "calendar_cache" else "source_id"
            counts[table] = connection.execute(
                f"SELECT COUNT(*) FROM {table} WHERE user_id = ? AND {column} = ?",
                [user_id, source_id],
            ).fetchone()[0]
    return {
        "source": _canvas_source_payload(source),
        "purge_supported": False,
        "destructive_purge_requires_phase_5": True,
        "counts": counts,
    }


CANVAS_SCOPE_ARRAY_KEYS = frozenset({
    "contexts", "context_ids", "calendars", "calendar_ids", "item_types",
})
CANVAS_SCOPE_DATE_KEYS = frozenset({"start", "end", "start_at", "end_at"})
CANVAS_SYNC_TERMINAL_STATES = frozenset({
    "complete", "partial", "expired", "error", "cancelled", "superseded",
})
CANVAS_WRITEBACK_CREATE_STATES = frozenset({
    "waiting_for_canvas_session", "queued",
})


def _canvas_timestamp(value, *, field, required=True):
    """Normalize a JSON timestamp to an explicit UTC ISO value."""
    if value is None or value == "":
        if required:
            raise ExtensionContractError(f"invalid_{field}", f"{field} is required.")
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        candidate = value.strip()
        if not candidate:
            if required:
                raise ExtensionContractError(f"invalid_{field}", f"{field} is required.")
            return None
        if candidate.endswith("Z"):
            candidate = candidate[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError as exc:
            raise ExtensionContractError(
                f"invalid_{field}", f"{field} must be an ISO-8601 date or timestamp."
            ) from exc
    else:
        raise ExtensionContractError(f"invalid_{field}", f"{field} must be a date or timestamp.")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    normalized = parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return normalized


def _canvas_generation(value, *, required=True):
    if value is None and not required:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ExtensionContractError("invalid_generation", "generation must be a positive integer.")
    return value


def _canvas_optional_id(value, *, field):
    return _canvas_id(value, field=field, required=False)


def _canvas_normalized_value(value, *, key=None):
    if isinstance(value, Mapping):
        normalized = {}
        for raw_key, child in value.items():
            if not isinstance(raw_key, str) or not raw_key.strip():
                raise ExtensionContractError("invalid_json", "JSON object keys must be non-empty strings.")
            normalized_key = raw_key.strip()
            if normalized_key in normalized:
                raise ExtensionContractError("invalid_json", "JSON object keys must be unique after normalization.")
            normalized[normalized_key] = _canvas_normalized_value(child, key=normalized_key)
        return {name: normalized[name] for name in sorted(normalized)}
    if isinstance(value, list):
        children = [_canvas_normalized_value(child, key=key) for child in value]
        if key in CANVAS_SCOPE_ARRAY_KEYS:
            deduped = []
            for child in children:
                if child not in deduped:
                    deduped.append(child)
            return sorted(deduped, key=lambda child: json.dumps(child, sort_keys=True, ensure_ascii=False))
        return children
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value.strip() if isinstance(value, str) else value
    raise ExtensionContractError("invalid_json", "Payload contains an unsupported JSON value.")


def _normalize_canvas_scope(scope):
    if scope is None:
        scope = {}
    if not isinstance(scope, Mapping):
        raise ExtensionContractError("invalid_scope", "scope must be a JSON object.")
    _canvas_reject_credentials(scope)
    normalized = _canvas_normalized_value(scope)
    aliases = {
        "contextIds": "context_ids",
        "calendarIds": "calendar_ids",
        "itemTypes": "item_types",
        "start_at": "start",
        "end_at": "end",
    }
    canonical = {}
    for key, value in normalized.items():
        canonical_key = aliases.get(key, key)
        if canonical_key in canonical and canonical[canonical_key] != value:
            raise ExtensionContractError("invalid_scope", "scope contains conflicting aliases.")
        canonical[canonical_key] = value

    for key in CANVAS_SCOPE_ARRAY_KEYS:
        if key not in canonical:
            continue
        values = canonical[key]
        if not isinstance(values, list):
            raise ExtensionContractError("invalid_scope", f"{key} must be an array.")
        if key in {"contexts", "context_ids", "calendars", "calendar_ids"}:
            canonical[key] = [
                _canvas_id(value, field=key.rstrip("s") + "_id") for value in values
            ]
        else:
            canonical[key] = [_canvas_item_type(value) for value in values]

    for key in ("start", "end"):
        if key in canonical:
            canonical[key] = _canvas_timestamp(canonical[key], field=key)
    if canonical.get("start") and canonical.get("end"):
        if canonical["start"] > canonical["end"]:
            raise ExtensionContractError("invalid_scope", "scope start must not be after scope end.")

    encoded = _canvas_json(canonical, field="scope", max_bytes=64 * 1024)
    return canonical, encoded, _canvas_hash(encoded)


def _canvas_scope_matches(row, scope):
    context_ids = set(scope.get("context_ids", [])) | set(scope.get("contexts", []))
    calendar_ids = set(scope.get("calendar_ids", [])) | set(scope.get("calendars", []))
    item_types = set(scope.get("item_types", []))
    if context_ids and row["canvas_context_id"] not in context_ids:
        return False
    if calendar_ids and row["canvas_calendar_id"] not in calendar_ids:
        return False
    if item_types and row["canvas_item_type"] not in item_types:
        return False
    event_start = row["event_start"]
    if scope.get("start") and (not event_start or event_start < scope["start"]):
        return False
    if scope.get("end") and (not event_start or event_start > scope["end"]):
        return False
    return True


def _canvas_decode_json(raw_value, default):
    try:
        decoded = json.loads(raw_value) if raw_value else default
    except (TypeError, json.JSONDecodeError):
        return default
    return decoded


def _canvas_sync_run_payload(row, *, idempotent=False, tombstoned=0):
    if not row:
        return None
    payload = dict(row)
    payload["scope"] = _canvas_decode_json(payload.pop("scope_json", None), {})
    payload["checkpoint"] = _canvas_decode_json(payload.pop("checkpoint_json", None), None)
    payload["counters"] = _canvas_decode_json(payload.pop("counters_json", None), {})
    payload["idempotent"] = bool(idempotent)
    if tombstoned:
        payload["tombstoned"] = tombstoned
    return payload


def _canvas_batch_payload(row, *, idempotent=False):
    if not row:
        return None
    result = _canvas_decode_json(row["result_json"], {})
    if not isinstance(result, dict):
        result = {}
    result.update({
        "id": row["id"],
        "run_id": row["run_id"],
        "generation": row["generation"],
        "idempotent": bool(idempotent),
        "checkpoint": _canvas_decode_json(row["checkpoint_json"], None),
    })
    return result


def _canvas_writeback_payload(row, *, idempotent=False):
    if not row:
        return None
    payload = dict(row)
    payload["payload"] = _canvas_decode_json(payload.pop("payload_json", None), {})
    payload["idempotent"] = bool(idempotent)
    return payload


def _canvas_link_payload(row, *, idempotent=False):
    if not row:
        return None
    payload = dict(row)
    payload["idempotent"] = bool(idempotent)
    return payload


def _canvas_result_error(code=None, message=None):
    """Return bounded, credential-free error fields for provider results."""
    if code is None and message is None:
        return None, None
    normalized_code = _canvas_text(
        code or "provider_error",
        field="error_code",
        max_length=80,
        required=True,
    ).lower().replace(" ", "_")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.:-]{0,79}", normalized_code):
        raise ExtensionContractError("invalid_error_code", "error_code contains unsupported characters.")
    if isinstance(message, Mapping):
        raise ExtensionContractError("invalid_error", "error_message must be text.")
    normalized_message = _canvas_text(
        message or "The Canvas operation failed.",
        field="error_message",
        max_length=500,
    )
    _canvas_reject_credentials({"message": normalized_message})
    return normalized_code, normalized_message


def _canvas_source_account(source, account_key):
    account_key = validate_account_key(account_key)
    if source["account_key"] != account_key:
        raise ExtensionContractError(
            "source_account_mismatch",
            "The Canvas account does not belong to this import source.",
        )
    return account_key


def _canvas_event_link_identity(payload):
    """Normalize the optional Canvas identity used by the unique link index."""
    identity = {
        "canvas_context_id": _canvas_optional_id(
            payload.get("canvas_context_id", payload.get("context_id", payload.get("contextId"))),
            field="canvas_context_id",
        ),
        "canvas_calendar_id": _canvas_optional_id(
            payload.get("canvas_calendar_id", payload.get("calendar_id", payload.get("calendarId"))),
            field="canvas_calendar_id",
        ),
        "canvas_item_type": (
            _canvas_item_type(payload.get("canvas_item_type", payload.get("item_type", payload.get("itemType"))))
            if payload.get("canvas_item_type", payload.get("item_type", payload.get("itemType"))) is not None
            else None
        ),
        "canvas_item_id": _canvas_optional_id(
            payload.get("canvas_item_id", payload.get("item_id", payload.get("itemId"))),
            field="canvas_item_id",
        ),
        "canvas_occurrence_id": _canvas_optional_id(
            payload.get("canvas_occurrence_id", payload.get("occurrence_id", payload.get("occurrenceId"))),
            field="canvas_occurrence_id",
        ),
    }
    if identity["canvas_item_id"] and not all(
        identity[key] for key in ("canvas_context_id", "canvas_calendar_id", "canvas_item_type")
    ):
        raise ExtensionContractError(
            "invalid_event_link",
            "Canvas identity requires context, calendar, and item type.",
        )
    return identity


def _canvas_event_link_lookup(connection, user_id, source_id, event_ref=None, *, link_id=None, include_archived=False):
    clauses = ["user_id = ?", "source_id = ?"]
    params = [user_id, source_id]
    if link_id is not None:
        clauses.append("id = ?")
        params.append(link_id)
    elif event_ref is not None:
        clauses.append("event_ref = ?")
        params.append(event_ref)
    if not include_archived:
        clauses.append("archived_at IS NULL")
    return connection.execute(
        f"SELECT * FROM calendar_event_links WHERE {' AND '.join(clauses)} ORDER BY created_at DESC LIMIT 1",
        params,
    ).fetchone()


def create_canvas_event_link(user_id, source_id, payload=None, *, account_key=None, event_kind=None,
                             nest_event_id=None, projection_event_id=None, event_ref=None,
                             source_revision=None, source_hash=None, mirror_state=None):
    """Create or replay an active Canvas-to-Nest event link."""
    if payload is not None:
        if not isinstance(payload, Mapping):
            raise ExtensionContractError("invalid_json", "Event-link payload must be a JSON object.")
        values = dict(payload)
        account_key = values.get("account_key", account_key)
        event_kind = values.get("event_kind", values.get("eventKind", event_kind))
        nest_event_id = values.get("nest_event_id", values.get("nestEventId", nest_event_id))
        projection_event_id = values.get("projection_event_id", values.get("projectionEventId", projection_event_id))
        event_ref = values.get("event_ref", event_ref)
        source_revision = values.get("source_revision", values.get("sourceRevision", source_revision))
        source_hash = values.get("source_hash", values.get("sourceHash", source_hash))
        mirror_state = values.get("mirror_state", values.get("mirrorState", mirror_state))
    else:
        values = {}
    user_id = _canvas_user_id(user_id)
    source_id = _canvas_id(source_id, field="source_id", pattern=CANVAS_RUN_ID_PATTERN)
    event_kind = str(event_kind or "projection").strip().lower()
    if event_kind not in {"native", "projection", "feed"}:
        raise ExtensionContractError("invalid_event_kind", "event_kind must be native, projection, or feed.")
    event_ref = _canvas_optional_id(event_ref, field="event_ref")
    nest_event_id = _canvas_optional_id(nest_event_id, field="nest_event_id")
    projection_event_id = _canvas_optional_id(projection_event_id, field="projection_event_id")
    source_revision = _canvas_optional_id(source_revision, field="source_revision")
    source_hash = _canvas_optional_id(source_hash, field="source_hash")
    mirror_state = str(mirror_state or "not_requested").strip().lower()
    if mirror_state not in CANVAS_MIRROR_STATES:
        raise ExtensionContractError("invalid_mirror_state", "mirror_state is not an approved Canvas mirror state.")
    identity = _canvas_event_link_identity(values)
    if not event_ref and not identity["canvas_item_id"]:
        raise ExtensionContractError("invalid_event_link", "event_ref or a Canvas item identity is required.")
    now = _canvas_now()

    with calendar_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        source = _require_canvas_source(connection, user_id, source_id)
        account_key = _canvas_source_account(source, account_key)
        existing = _canvas_event_link_lookup(connection, user_id, source_id, event_ref)
        if existing is None and identity["canvas_item_id"]:
            existing = connection.execute(
                """SELECT * FROM calendar_event_links
                   WHERE user_id = ? AND source_id = ? AND account_key = ?
                     AND canvas_context_id = ? AND canvas_calendar_id = ?
                     AND canvas_item_type = ? AND canvas_item_id = ?
                     AND IFNULL(canvas_occurrence_id, '') = IFNULL(?, '')
                     AND archived_at IS NULL
                   LIMIT 1""",
                [user_id, source_id, account_key, identity["canvas_context_id"],
                 identity["canvas_calendar_id"], identity["canvas_item_type"],
                 identity["canvas_item_id"], identity["canvas_occurrence_id"]],
            ).fetchone()
        if existing:
            same = all(existing[field] == value for field, value in {
                "account_key": account_key, "event_kind": event_kind,
                "nest_event_id": nest_event_id, "projection_event_id": projection_event_id,
                "event_ref": event_ref, **identity, "source_revision": source_revision,
                "source_hash": source_hash, "mirror_state": mirror_state,
            }.items())
            if not same:
                raise ExtensionContractError("event_link_conflict", "The active Canvas event link already exists.")
            return _canvas_link_payload(existing, idempotent=True)
        link_id = uuid.uuid4().hex
        try:
            connection.execute(
                """INSERT INTO calendar_event_links
                   (id, user_id, source_id, account_key, event_kind, nest_event_id,
                    projection_event_id, event_ref, canvas_context_id, canvas_calendar_id,
                    canvas_item_id, canvas_occurrence_id, canvas_item_type, source_revision,
                    source_hash, mirror_state, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [link_id, user_id, source_id, account_key, event_kind, nest_event_id,
                 projection_event_id, event_ref, identity["canvas_context_id"],
                 identity["canvas_calendar_id"], identity["canvas_item_id"],
                 identity["canvas_occurrence_id"], identity["canvas_item_type"],
                 source_revision, source_hash, mirror_state, now, now],
            )
        except sqlite3.IntegrityError as exc:
            raise ExtensionContractError("event_link_conflict", "The active Canvas event link already exists.") from exc
        created = connection.execute("SELECT * FROM calendar_event_links WHERE id = ?", [link_id]).fetchone()
    return _canvas_link_payload(created)


def get_canvas_event_link(user_id, source_id, event_ref=None, *, link_id=None, include_archived=False):
    user_id = _canvas_user_id(user_id)
    source_id = _canvas_id(source_id, field="source_id", pattern=CANVAS_RUN_ID_PATTERN)
    if event_ref is None and link_id is None:
        raise ExtensionContractError("invalid_event_link", "event_ref or link_id is required.")
    if event_ref is not None:
        event_ref = _canvas_id(event_ref, field="event_ref")
    if link_id is not None:
        link_id = _canvas_id(link_id, field="link_id")
    with calendar_connection() as connection:
        _require_canvas_source(connection, user_id, source_id, include_archived=True)
        row = _canvas_event_link_lookup(
            connection, user_id, source_id, event_ref, link_id=link_id, include_archived=include_archived
        )
    return _canvas_link_payload(row)


def record_canvas_event_link_result(user_id, source_id, event_ref=None, *, link_id=None, payload=None,
                                   mirror_state=None, state=None, expected_revision=None, source_revision=None,
                                   source_hash=None, error_code=None, error_message=None,
                                   mirrored_at=None):
    """Record an approved provider mirror result with optimistic revision control."""
    if payload is not None:
        if not isinstance(payload, Mapping):
            raise ExtensionContractError("invalid_json", "Event-link result must be a JSON object.")
        values = dict(payload)
        mirror_state = values.get("mirror_state", values.get("state", mirror_state))
        expected_revision = values.get("expected_revision", values.get("expectedRevision", expected_revision))
        source_revision = values.get("source_revision", values.get("sourceRevision", source_revision))
        source_hash = values.get("source_hash", values.get("sourceHash", source_hash))
        error_code = values.get("error_code", error_code)
        error_message = values.get("error_message", values.get("errorMessage", error_message))
        mirrored_at = values.get("mirrored_at", values.get("mirroredAt", mirrored_at))
    mirror_state = str(mirror_state or state or "queued").strip().lower()
    if mirror_state not in CANVAS_MIRROR_STATES:
        raise ExtensionContractError("invalid_mirror_state", "mirror_state is not an approved Canvas mirror state.")
    expected_revision = _canvas_optional_id(expected_revision, field="expected_revision")
    source_revision = _canvas_optional_id(source_revision, field="source_revision")
    source_hash = _canvas_optional_id(source_hash, field="source_hash")
    error_code, error_message = _canvas_result_error(error_code, error_message)
    mirrored_at = _canvas_timestamp(mirrored_at, field="mirrored_at", required=False) or _canvas_now()
    user_id = _canvas_user_id(user_id)
    source_id = _canvas_id(source_id, field="source_id", pattern=CANVAS_RUN_ID_PATTERN)
    with calendar_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        _require_canvas_source(connection, user_id, source_id, include_archived=True)
        row = _canvas_event_link_lookup(
            connection, user_id, source_id, event_ref, link_id=link_id, include_archived=True
        )
        if not row:
            raise ExtensionContractError("event_link_not_found", "Canvas event link was not found.")
        if expected_revision is not None and row["source_revision"] != expected_revision:
            raise ExtensionContractError("revision_conflict", "The Canvas event link revision is no longer current.")
        if row["archived_at"] is not None:
            raise ExtensionContractError("event_link_archived", "The Canvas event link is archived.")
        if row["mirror_state"] == mirror_state and row["source_revision"] == source_revision and row["source_hash"] == source_hash:
            return _canvas_link_payload(row, idempotent=True)
        connection.execute(
            """UPDATE calendar_event_links
               SET mirror_state = ?, mirror_error_code = ?, mirror_error_message = ?,
                   source_revision = ?, source_hash = ?, mirrored_at = ?, updated_at = ?
               WHERE id = ? AND user_id = ? AND source_id = ? AND archived_at IS NULL""",
            [mirror_state, error_code, error_message, source_revision, source_hash,
             mirrored_at if mirror_state == "applied" else None, _canvas_now(),
             row["id"], user_id, source_id],
        )
        updated = connection.execute("SELECT * FROM calendar_event_links WHERE id = ?", [row["id"]]).fetchone()
    return _canvas_link_payload(updated)


def _canvas_writeback_request(payload, *, operation, event_ref, expected_revision, idempotency_key,
                              target_account, target_calendar):
    if payload is None:
        payload = {}
    if not isinstance(payload, Mapping):
        raise ExtensionContractError("invalid_json", "Writeback payload must be a JSON object.")
    values = dict(payload)
    operation = values.get("operation", operation)
    event_ref = values.get("event_ref", values.get("eventRef", event_ref))
    expected_revision = values.get("expected_revision", values.get("expectedRevision", expected_revision))
    idempotency_key = values.get("idempotency_key", values.get("idempotencyKey", idempotency_key))
    target_account = values.get("target_account", values.get("targetAccount", target_account))
    target_calendar = values.get("target_calendar", values.get("targetCalendar", target_calendar))
    operation = str(operation or "").strip().lower()
    if operation not in {"create", "update", "delete"}:
        raise ExtensionContractError("invalid_operation", "operation must be create, update, or delete.")
    event_ref = _canvas_optional_id(event_ref, field="event_ref")
    expected_revision = _canvas_optional_id(expected_revision, field="expected_revision")
    idempotency_key = _canvas_idempotency_key(idempotency_key, field="idempotency_key")
    target_account = validate_account_key(target_account)
    target_calendar = _canvas_optional_id(target_calendar, field="target_calendar")
    _canvas_reject_credentials(values)
    payload_json = _canvas_json(values, field="payload", max_bytes=64 * 1024)
    return operation, event_ref, expected_revision, idempotency_key, target_account, target_calendar, payload_json


def create_canvas_writeback(user_id, source_id, payload=None, *, account_key=None, operation=None,
                            event_ref=None, expected_revision=None, idempotency_key=None,
                            target_account=None, target_calendar=None, state="waiting_for_canvas_session"):
    """Queue or replay one consented Canvas writeback operation."""
    if payload is not None and isinstance(payload, Mapping):
        account_key = payload.get("account_key", account_key)
        state = payload.get("state", state)
    state = str(state or "waiting_for_canvas_session").strip().lower()
    if state not in CANVAS_WRITEBACK_CREATE_STATES:
        raise ExtensionContractError("invalid_writeback_state", "A new writeback must be waiting or queued.")
    user_id = _canvas_user_id(user_id)
    source_id = _canvas_id(source_id, field="source_id", pattern=CANVAS_RUN_ID_PATTERN)
    account_key = validate_account_key(account_key)
    (operation, event_ref, expected_revision, idempotency_key, target_account,
     target_calendar, payload_json) = _canvas_writeback_request(
        payload, operation=operation, event_ref=event_ref, expected_revision=expected_revision,
        idempotency_key=idempotency_key, target_account=target_account, target_calendar=target_calendar,
    )
    payload_hash = _canvas_hash(payload_json)
    with calendar_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        source = _require_canvas_source(connection, user_id, source_id)
        _canvas_source_account(source, account_key)
        _canvas_source_consent(connection, source, scopes=(CANVAS_WRITEBACK_SCOPE,))
        if operation in {"update", "delete"} and expected_revision is None:
            raise ExtensionContractError("expected_revision_required", "update and delete require expected_revision.")
        if expected_revision is not None and event_ref:
            link = _canvas_event_link_lookup(connection, user_id, source_id, event_ref)
            cache = connection.execute(
                """SELECT canvas_source_revision FROM calendar_cache
                   WHERE user_id = ? AND canvas_source_id = ? AND canvas_account_key = ?
                     AND canvas_event_ref = ? AND canvas_soft_deleted = 0 LIMIT 1""",
                [user_id, source_id, account_key, event_ref],
            ).fetchone()
            current_revision = link["source_revision"] if link and link["source_revision"] is not None else (cache["canvas_source_revision"] if cache else None)
            if current_revision is not None and current_revision != expected_revision:
                raise ExtensionContractError("revision_conflict", "The Canvas event revision is no longer current.")
        existing = connection.execute(
            """SELECT * FROM calendar_writebacks
               WHERE user_id = ? AND source_id = ? AND idempotency_key = ?""",
            [user_id, source_id, idempotency_key],
        ).fetchone()
        if existing:
            if existing["payload_hash"] != payload_hash or existing["operation"] != operation or existing["expected_revision"] != expected_revision:
                raise ExtensionContractError("idempotency_conflict", "The writeback idempotency key was already used with different parameters.")
            return _canvas_writeback_payload(existing, idempotent=True)
        now = _canvas_now()
        writeback_id = uuid.uuid4().hex
        try:
            connection.execute(
                """INSERT INTO calendar_writebacks
                   (id, user_id, source_id, account_key, operation, event_ref, expected_revision,
                    payload_hash, idempotency_key, target_account, target_calendar, payload_json,
                    state, retry_count, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)""",
                [writeback_id, user_id, source_id, account_key, operation, event_ref, expected_revision,
                 payload_hash, idempotency_key, target_account, target_calendar, payload_json, state, now, now],
            )
        except sqlite3.IntegrityError as exc:
            raise ExtensionContractError("writeback_conflict", "The Canvas writeback could not be created.") from exc
        created = connection.execute("SELECT * FROM calendar_writebacks WHERE id = ?", [writeback_id]).fetchone()
    return _canvas_writeback_payload(created)


def list_canvas_writebacks(user_id, source_id, *, account_key=None, event_ref=None, states=None, limit=100):
    user_id = _canvas_user_id(user_id)
    source_id = _canvas_id(source_id, field="source_id", pattern=CANVAS_RUN_ID_PATTERN)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ExtensionContractError("invalid_limit", "limit must be between 1 and 100.")
    if states is None:
        states = None
    elif isinstance(states, str):
        states = [states]
    elif not isinstance(states, (list, tuple, set)):
        raise ExtensionContractError("invalid_writeback_state", "states must be an array.")
    if states is not None:
        states = [str(value).strip().lower() for value in states]
        if any(value not in CANVAS_WRITEBACK_STATES for value in states):
            raise ExtensionContractError("invalid_writeback_state", "states contains an unapproved state.")
    if account_key is not None:
        account_key = validate_account_key(account_key)
    if event_ref is not None:
        event_ref = _canvas_id(event_ref, field="event_ref")
    with calendar_connection() as connection:
        source = _require_canvas_source(connection, user_id, source_id, include_archived=True)
        if account_key is not None:
            _canvas_source_account(source, account_key)
        clauses = ["user_id = ?", "source_id = ?"]
        params = [user_id, source_id]
        if account_key is not None:
            clauses.append("account_key = ?")
            params.append(account_key)
        if event_ref is not None:
            clauses.append("event_ref = ?")
            params.append(event_ref)
        if states:
            clauses.append("state IN (" + ",".join("?" for _ in states) + ")")
            params.extend(states)
        rows = connection.execute(
            f"SELECT * FROM calendar_writebacks WHERE {' AND '.join(clauses)} ORDER BY created_at ASC LIMIT ?",
            [*params, limit],
        ).fetchall()
    return [_canvas_writeback_payload(row) for row in rows]


def get_canvas_writeback_result(user_id, source_id, writeback_id, *, include_archived=True):
    user_id = _canvas_user_id(user_id)
    source_id = _canvas_id(source_id, field="source_id", pattern=CANVAS_RUN_ID_PATTERN)
    writeback_id = _canvas_id(writeback_id, field="writeback_id")
    with calendar_connection() as connection:
        _require_canvas_source(connection, user_id, source_id, include_archived=True)
        row = connection.execute(
            "SELECT * FROM calendar_writebacks WHERE id = ? AND user_id = ? AND source_id = ?",
            [writeback_id, user_id, source_id],
        ).fetchone()
        if row and not include_archived and row["state"] == "cancelled":
            row = None
    return _canvas_writeback_payload(row)


def record_canvas_writeback_result(user_id, source_id, writeback_id, payload=None, *, state=None,
                                   expected_revision=None, result_revision=None, error_code=None,
                                   error_message=None, retry_count=None, next_retry_at=None):
    """Apply one approved Canvas writeback result, once, to an owned row."""
    if payload is not None:
        if not isinstance(payload, Mapping):
            raise ExtensionContractError("invalid_json", "Writeback result must be a JSON object.")
        values = dict(payload)
        state = values.get("state", values.get("status", state))
        expected_revision = values.get("expected_revision", values.get("expectedRevision", expected_revision))
        result_revision = values.get("result_revision", values.get("resultRevision", result_revision))
        error_code = values.get("error_code", error_code)
        error_message = values.get("error_message", values.get("errorMessage", error_message))
        retry_count = values.get("retry_count", retry_count)
        next_retry_at = values.get("next_retry_at", values.get("nextRetryAt", next_retry_at))
    state = str(state or "retryable_failed").strip().lower()
    if state not in CANVAS_WRITEBACK_STATES:
        raise ExtensionContractError("invalid_writeback_state", "state is not an approved Canvas writeback state.")
    expected_revision = _canvas_optional_id(expected_revision, field="expected_revision")
    result_revision = _canvas_optional_id(result_revision, field="result_revision")
    error_code, error_message = _canvas_result_error(error_code, error_message)
    if retry_count is not None and (isinstance(retry_count, bool) or not isinstance(retry_count, int) or retry_count < 0):
        raise ExtensionContractError("invalid_retry_count", "retry_count must be a non-negative integer.")
    next_retry_at = _canvas_timestamp(next_retry_at, field="next_retry_at", required=False)
    user_id = _canvas_user_id(user_id)
    source_id = _canvas_id(source_id, field="source_id", pattern=CANVAS_RUN_ID_PATTERN)
    with calendar_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        _require_canvas_source(connection, user_id, source_id, include_archived=True)
        row = connection.execute(
            "SELECT * FROM calendar_writebacks WHERE id = ? AND user_id = ? AND source_id = ?",
            [writeback_id, user_id, source_id],
        ).fetchone()
        if not row:
            raise ExtensionContractError("writeback_not_found", "Canvas writeback was not found.")
        if expected_revision is not None and row["expected_revision"] != expected_revision:
            raise ExtensionContractError("revision_conflict", "The Canvas writeback revision is no longer current.")
        if row["state"] in {"applied", "unsupported", "forbidden", "conflict", "cancelled"}:
            if row["state"] == state and row["result_revision"] == result_revision and row["error_code"] == error_code:
                return _canvas_writeback_payload(row, idempotent=True)
            raise ExtensionContractError("writeback_terminal", "The Canvas writeback already has a terminal result.")
        now = _canvas_now()
        applied_at = now if state == "applied" else None
        cancelled_at = now if state == "cancelled" else None
        next_retry_at = next_retry_at if state == "retryable_failed" else None
        connection.execute(
            """UPDATE calendar_writebacks
               SET state = ?, retry_count = COALESCE(?, retry_count), last_attempt_at = ?,
                   next_retry_at = ?, result_revision = ?, error_code = ?, error_message = ?,
                   updated_at = ?, applied_at = ?, cancelled_at = ?
               WHERE id = ? AND user_id = ? AND source_id = ?""",
            [state, retry_count, now, next_retry_at, result_revision, error_code, error_message,
             now, applied_at, cancelled_at, writeback_id, user_id, source_id],
        )
        updated = connection.execute("SELECT * FROM calendar_writebacks WHERE id = ?", [writeback_id]).fetchone()
    return _canvas_writeback_payload(updated)


# Route layers can bind these explicit service hooks without reaching into SQL.
create_canvas_event_link_result = record_canvas_event_link_result
create_canvas_writeback_result = record_canvas_writeback_result


def _require_canvas_source(connection, user_id, source_id, *, include_archived=False):
    user_id = _canvas_user_id(user_id)
    source = _canvas_source_row(
        connection,
        user_id,
        source_id,
        include_archived=include_archived,
    )
    if not source:
        raise ExtensionContractError("source_not_found", "Canvas import source was not found.")
    if source["provider"] != CANVAS_PROVIDER:
        raise ExtensionContractError("source_not_canvas", "The import source is not Canvas.")
    if not include_archived and source["status"] != "active":
        raise ExtensionContractError("source_inactive", "Canvas import source is not active.")
    return source


def _canvas_source_consent(connection, source, *, version=None, scopes=()):
    if version is not None:
        validate_version(version)
    consent = _canvas_consent_from_connection(
        connection,
        source["user_id"],
        source["account_key"],
        scopes,
        version,
    )
    return consent, int(consent["version"])


def _canvas_current_generation(connection, user_id, source_id):
    row = connection.execute(
        "SELECT MAX(generation) AS generation FROM calendar_sync_runs WHERE user_id = ? AND source_id = ?",
        [user_id, source_id],
    ).fetchone()
    return int(row["generation"] or 0)


def _canvas_run_row(connection, user_id, source_id, run_id):
    return connection.execute(
        "SELECT * FROM calendar_sync_runs WHERE user_id = ? AND source_id = ? AND run_id = ?",
        [user_id, source_id, run_id],
    ).fetchone()


def _canvas_mark_run_expired(connection, row, now):
    if row["state"] != "active":
        return row
    connection.execute(
        """UPDATE calendar_sync_runs
           SET state = 'expired', error_code = 'lease_expired',
               error_message = 'The sync lease expired.', updated_at = ?, completed_at = ?
           WHERE id = ? AND state = 'active'""",
        [now, now, row["id"]],
    )
    return connection.execute("SELECT * FROM calendar_sync_runs WHERE id = ?", [row["id"]]).fetchone()


def _require_current_active_run(
    connection,
    user_id,
    source_id,
    run_id,
    *,
    generation=None,
    lease_token=None,
    now=None,
):
    now = now or _canvas_now()
    run_id = _canvas_id(run_id, field="run_id", pattern=CANVAS_RUN_ID_PATTERN)
    row = _canvas_run_row(connection, user_id, source_id, run_id)
    if not row:
        raise ExtensionContractError("run_not_found", "Canvas sync run was not found.")
    expected_generation = _canvas_generation(generation, required=False)
    current_generation = _canvas_current_generation(connection, user_id, source_id)
    if row["generation"] != current_generation or (
        expected_generation is not None and row["generation"] != expected_generation
    ):
        raise ExtensionContractError("stale_run", "Only the current Canvas sync generation may mutate this run.")
    if lease_token is not None and (
        not isinstance(lease_token, str)
        or not isinstance(row["lease_token"], str)
        or not hmac.compare_digest(lease_token, row["lease_token"])
    ):
        raise ExtensionContractError("lease_token_mismatch", "The Canvas sync lease token is invalid.")
    if row["state"] != "active":
        raise ExtensionContractError("run_not_active", "The Canvas sync run is not active.")
    if row["lease_expires_at"] <= now:
        _canvas_mark_run_expired(connection, row, now)
        # Expiration is a state transition, not part of the rejected mutation.
        # Commit it before surfacing the client error so callers cannot keep
        # using a lease that the service has already invalidated.
        connection.commit()
        raise ExtensionContractError("lease_expired", "The Canvas sync lease has expired.")
    return row


def _canvas_update_source_after_run(connection, source, *, state, now, error_code=None, error_message=None):
    connection.execute(
        """UPDATE calendar_import_sources
           SET sync_state = ?, updated_at = ?, last_error_code = ?, last_error_message = ?
           WHERE user_id = ? AND source_id = ?""",
        [state, now, error_code, error_message, source["user_id"], source["source_id"]],
    )


def _canvas_parse_run_request(scope, consent_version, idempotency_key, run_id, payload):
    if payload is not None:
        if not isinstance(payload, Mapping):
            raise ExtensionContractError("invalid_json", "Sync run payload must be a JSON object.")
        if scope is None:
            scope = payload.get("scope")
        if consent_version is None:
            consent_version = payload.get("consent_version", payload.get("version"))
        if idempotency_key is None:
            idempotency_key = payload.get("idempotency_key")
        if run_id is None:
            run_id = payload.get("run_id")
    normalized_scope, scope_json, scope_hash = _normalize_canvas_scope(scope)
    idempotency_key = _canvas_idempotency_key(idempotency_key)
    if run_id is None:
        run_id = uuid.uuid4().hex
    run_id = _canvas_id(run_id, field="run_id", pattern=CANVAS_RUN_ID_PATTERN)
    return normalized_scope, scope_json, scope_hash, consent_version, idempotency_key, run_id


def begin_canvas_sync_run(
    user_id,
    source_id,
    payload=None,
    *,
    scope=None,
    consent_version=None,
    idempotency_key=None,
    run_id=None,
):
    """Begin or return an idempotent, leased Canvas sync generation."""
    user_id = _canvas_user_id(user_id)
    source_id = _canvas_id(source_id, field="source_id", pattern=CANVAS_RUN_ID_PATTERN)
    (
        normalized_scope,
        scope_json,
        scope_hash,
        consent_version,
        idempotency_key,
        run_id,
    ) = _canvas_parse_run_request(scope, consent_version, idempotency_key, run_id, payload)
    with calendar_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        source = _require_canvas_source(connection, user_id, source_id)
        consent, consent_version = _canvas_source_consent(
            connection,
            source,
            version=consent_version,
            scopes=CANVAS_READ_SCOPES,
        )
        existing = connection.execute(
            """SELECT * FROM calendar_sync_runs
               WHERE user_id = ? AND source_id = ? AND idempotency_key = ?""",
            [user_id, source_id, idempotency_key],
        ).fetchone()
        if existing:
            if existing["scope_hash"] != scope_hash or int(existing["consent_version"]) != consent_version:
                raise ExtensionContractError(
                    "idempotency_conflict",
                    "The sync idempotency key was already used with different parameters.",
                )
            return _canvas_sync_run_payload(existing, idempotent=True)

        generation = _canvas_current_generation(connection, user_id, source_id) + 1
        now = _canvas_now()
        lease_expires_at = _canvas_timestamp(
            datetime.fromisoformat(now[:-1] + "+00:00") + timedelta(minutes=CANVAS_LEASE_MINUTES),
            field="lease_expires_at",
        )
        lease_token = secrets.token_urlsafe(32)
        connection.execute(
            """UPDATE calendar_sync_runs
               SET state = 'superseded', error_code = 'new_generation',
                   error_message = 'A newer sync generation was started.', updated_at = ?
               WHERE user_id = ? AND source_id = ? AND state = 'active'""",
            [now, user_id, source_id],
        )
        connection.execute(
            """INSERT INTO calendar_sync_runs
               (id, user_id, source_id, run_id, generation, lease_token,
                lease_expires_at, lease_renewed_at, scope_json, scope_hash,
                consent_version, checkpoint_json, cursor, counters_json, state,
                started_at, updated_at, idempotency_key)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, 'active', ?, ?, ?)""",
            [
                uuid.uuid4().hex,
                user_id,
                source_id,
                run_id,
                generation,
                lease_token,
                lease_expires_at,
                now,
                scope_json,
                scope_hash,
                consent_version,
                _canvas_json({"accepted": 0, "updated": 0, "unchanged": 0, "quarantined": 0}, field="counters"),
                now,
                now,
                idempotency_key,
            ],
        )
        _canvas_update_source_after_run(connection, source, state="active", now=now)
        created = _canvas_run_row(connection, user_id, source_id, run_id)
    return _canvas_sync_run_payload(created)


def get_canvas_sync_run(user_id, source_id, run_id=None, *, generation=None):
    user_id = _canvas_user_id(user_id)
    source_id = _canvas_id(source_id, field="source_id", pattern=CANVAS_RUN_ID_PATTERN)
    generation = _canvas_generation(generation, required=False)
    with calendar_connection() as connection:
        _require_canvas_source(connection, user_id, source_id, include_archived=True)
        if run_id is not None:
            run_id = _canvas_id(run_id, field="run_id", pattern=CANVAS_RUN_ID_PATTERN)
            row = _canvas_run_row(connection, user_id, source_id, run_id)
        elif generation is not None:
            row = connection.execute(
                "SELECT * FROM calendar_sync_runs WHERE user_id = ? AND source_id = ? AND generation = ?",
                [user_id, source_id, generation],
            ).fetchone()
        else:
            row = connection.execute(
                """SELECT * FROM calendar_sync_runs
                   WHERE user_id = ? AND source_id = ?
                   ORDER BY generation DESC LIMIT 1""",
                [user_id, source_id],
            ).fetchone()
    return _canvas_sync_run_payload(row)


def renew_canvas_sync_run(user_id, source_id, run_id, *, generation=None, lease_token=None):
    """Renew only an active lease held by the source's current generation."""
    user_id = _canvas_user_id(user_id)
    source_id = _canvas_id(source_id, field="source_id", pattern=CANVAS_RUN_ID_PATTERN)
    with calendar_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        source = _require_canvas_source(connection, user_id, source_id)
        row = _require_current_active_run(
            connection,
            user_id,
            source_id,
            run_id,
            generation=generation,
            lease_token=lease_token,
        )
        now = _canvas_now()
        lease_expires_at = _canvas_timestamp(
            datetime.fromisoformat(now[:-1] + "+00:00") + timedelta(minutes=CANVAS_LEASE_MINUTES),
            field="lease_expires_at",
        )
        connection.execute(
            """UPDATE calendar_sync_runs
               SET lease_expires_at = ?, lease_renewed_at = ?, updated_at = ?
               WHERE id = ? AND generation = ? AND state = 'active'""",
            [lease_expires_at, now, now, row["id"], row["generation"]],
        )
        renewed = connection.execute("SELECT * FROM calendar_sync_runs WHERE id = ?", [row["id"]]).fetchone()
        _canvas_update_source_after_run(connection, source, state="active", now=now)
    return _canvas_sync_run_payload(renewed)


def resume_canvas_sync_run(user_id, source_id, run_id, *, generation=None, lease_token=None):
    """Resume the current generation after an expired lease with a fresh lease."""
    user_id = _canvas_user_id(user_id)
    source_id = _canvas_id(source_id, field="source_id", pattern=CANVAS_RUN_ID_PATTERN)
    with calendar_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        source = _require_canvas_source(connection, user_id, source_id)
        run_id = _canvas_id(run_id, field="run_id", pattern=CANVAS_RUN_ID_PATTERN)
        row = _canvas_run_row(connection, user_id, source_id, run_id)
        if not row:
            raise ExtensionContractError("run_not_found", "Canvas sync run was not found.")
        expected_generation = _canvas_generation(generation, required=False)
        if row["generation"] != _canvas_current_generation(connection, user_id, source_id) or (
            expected_generation is not None and row["generation"] != expected_generation
        ):
            raise ExtensionContractError("stale_run", "Only the current Canvas sync generation may resume.")
        if lease_token is not None and row["lease_token"] != lease_token:
            raise ExtensionContractError("lease_token_mismatch", "The Canvas sync lease token is invalid.")
        if row["state"] not in {"active", "expired"}:
            raise ExtensionContractError("run_not_resumable", "The Canvas sync run is not resumable.")
        now = _canvas_now()
        next_token = row["lease_token"] if row["state"] == "active" else secrets.token_urlsafe(32)
        lease_expires_at = _canvas_timestamp(
            datetime.fromisoformat(now[:-1] + "+00:00") + timedelta(minutes=CANVAS_LEASE_MINUTES),
            field="lease_expires_at",
        )
        connection.execute(
            """UPDATE calendar_sync_runs
               SET state = 'active', lease_token = ?, lease_expires_at = ?,
                   lease_renewed_at = ?, updated_at = ?, error_code = NULL,
                   error_message = NULL, completed_at = NULL
               WHERE id = ?""",
            [next_token, lease_expires_at, now, now, row["id"]],
        )
        resumed = connection.execute("SELECT * FROM calendar_sync_runs WHERE id = ?", [row["id"]]).fetchone()
        _canvas_update_source_after_run(connection, source, state="active", now=now)
    return _canvas_sync_run_payload(resumed)


def cancel_canvas_sync_run(user_id, source_id, run_id, *, generation=None, lease_token=None, reason=None):
    user_id = _canvas_user_id(user_id)
    source_id = _canvas_id(source_id, field="source_id", pattern=CANVAS_RUN_ID_PATTERN)
    reason = _canvas_text(reason or "Cancelled by the caller.", field="reason", max_length=500, required=True)
    with calendar_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        source = _require_canvas_source(connection, user_id, source_id, include_archived=True)
        row = _canvas_run_row(connection, user_id, source_id, run_id)
        if not row:
            raise ExtensionContractError("run_not_found", "Canvas sync run was not found.")
        expected_generation = _canvas_generation(generation, required=False)
        if row["generation"] != _canvas_current_generation(connection, user_id, source_id) or (
            expected_generation is not None and row["generation"] != expected_generation
        ):
            raise ExtensionContractError("stale_run", "Only the current Canvas sync generation may be cancelled.")
        if lease_token is not None and row["lease_token"] != lease_token:
            raise ExtensionContractError("lease_token_mismatch", "The Canvas sync lease token is invalid.")
        if row["state"] == "cancelled":
            return _canvas_sync_run_payload(row, idempotent=True)
        if row["state"] != "active":
            raise ExtensionContractError("run_not_active", "Only an active Canvas sync run may be cancelled.")
        now = _canvas_now()
        connection.execute(
            """UPDATE calendar_sync_runs
               SET state = 'cancelled', error_code = 'cancelled', error_message = ?,
                   cancelled_at = ?, updated_at = ?, completed_at = ?
               WHERE id = ? AND state = 'active'""",
            [reason, now, now, now, row["id"]],
        )
        cancelled = connection.execute("SELECT * FROM calendar_sync_runs WHERE id = ?", [row["id"]]).fetchone()
        if source["status"] != "archived":
            _canvas_update_source_after_run(connection, source, state="cancelled", now=now)
    return _canvas_sync_run_payload(cancelled)


def _canvas_item_value(item, *keys, default=None):
    for key in keys:
        if key in item:
            return item[key]
    return default


def _canvas_bool(value, *, field, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
    raise ExtensionContractError(f"invalid_{field}", f"{field} must be a boolean.")


def _normalize_canvas_item(item, *, source_id, account_key):
    if not isinstance(item, Mapping):
        raise ExtensionContractError("item_quarantined", "Canvas item must be a JSON object.")
    _canvas_reject_credentials(item)
    context_id = _canvas_id(
        _canvas_item_value(item, "context_id", "contextId", "context"),
        field="context_id",
    )
    calendar_id = _canvas_id(
        _canvas_item_value(item, "calendar_id", "calendarId", "calendar"),
        field="calendar_id",
    )
    item_type = _canvas_item_type(_canvas_item_value(item, "item_type", "itemType", "type"))
    item_id = _canvas_id(
        _canvas_item_value(item, "item_id", "itemId", "id"),
        field="item_id",
    )
    occurrence_id = _canvas_id(
        _canvas_item_value(item, "occurrence_id", "occurrenceId"),
        field="occurrence_id",
        required=False,
    )
    is_all_day = _canvas_bool(
        _canvas_item_value(item, "is_all_day", "all_day", "allDay"),
        field="is_all_day",
        default=False,
    )
    start = _canvas_timestamp(
        _canvas_item_value(item, "start", "start_at", "startAt", "event_start", "due_at", "dueAt"),
        field="start",
    )
    end = _canvas_timestamp(
        _canvas_item_value(item, "end", "end_at", "endAt", "event_end"),
        field="end",
        required=False,
    ) or start
    if end < start:
        raise ExtensionContractError("item_quarantined", "Canvas item end must not be before its start.")

    title = _canvas_text(
        _canvas_item_value(item, "title", "summary", "name", default=item_type),
        field="title",
        max_length=500,
        required=True,
    )
    description = _canvas_text(
        _canvas_item_value(item, "description", "raw_description", default=""),
        field="description",
        max_length=64 * 1024,
    )
    completion_status = _canvas_completion(
        _canvas_item_value(item, "completion_status", "completionStatus", default="incomplete")
    )
    completion_source = _canvas_completion_source(
        _canvas_item_value(item, "completion_source", "completionSource", default="canvas")
    )
    source_revision = _canvas_id(
        _canvas_item_value(item, "source_revision", "sourceRevision", "revision"),
        field="source_revision",
        required=False,
    )
    source_hash = _canvas_id(
        _canvas_item_value(item, "source_hash", "sourceHash", "content_hash", "contentHash"),
        field="source_hash",
        required=False,
    )
    course_name = _canvas_text(
        _canvas_item_value(item, "course_name", "courseName", "context_name", "contextName", default=""),
        field="course_name",
        max_length=255,
    )
    event_ref = canvas_event_ref_for_item(
        source_id,
        account_key,
        context_id,
        calendar_id,
        item_type,
        item_id,
        occurrence_id,
    )
    source_item_key = _canvas_source_item_key(
        source_id,
        account_key,
        context_id,
        calendar_id,
        item_type,
        item_id,
        occurrence_id,
    )
    normalized = {
        "context_id": context_id,
        "calendar_id": calendar_id,
        "item_type": item_type,
        "item_id": item_id,
        "occurrence_id": occurrence_id,
        "event_ref": event_ref,
        "source_item_key": source_item_key,
        "title": title,
        "description": description,
        "start": start,
        "end": end,
        "is_all_day": is_all_day,
        "course_name": course_name,
        "source_revision": source_revision,
        "source_hash": source_hash,
        "completion_status": completion_status,
        "completion_source": completion_source,
    }
    if not source_hash:
        source_hash = _canvas_hash(_canvas_json(normalized, field="item"))
        normalized["source_hash"] = source_hash
    return normalized


_CANVAS_CACHE_OWNED_FIELDS = (
    "event_title", "event_start", "event_end", "is_all_day", "event_type",
    "course_name", "raw_description", "canvas_source_revision", "canvas_source_hash",
    "canvas_completion_status", "canvas_completion_source",
)


def _canvas_cache_row(connection, user_id, source_id, account_key, item):
    occurrence_clause = "canvas_occurrence_id IS NULL" if item["occurrence_id"] is None else "canvas_occurrence_id = ?"
    params = [
        user_id,
        source_id,
        account_key,
        item["context_id"],
        item["calendar_id"],
        item["item_type"],
        item["item_id"],
    ]
    if item["occurrence_id"] is not None:
        params.append(item["occurrence_id"])
    return connection.execute(
        f"""SELECT * FROM calendar_cache
            WHERE user_id = ? AND canvas_source_id = ? AND canvas_account_key = ?
              AND canvas_context_id = ? AND canvas_calendar_id = ?
              AND canvas_item_type = ? AND canvas_item_id = ?
              AND {occurrence_clause}""",
        params,
    ).fetchone()


def _canvas_cache_values(source_id, account_key, item, now, generation, scope_hash):
    return {
        "canvas_source_id": source_id,
        "canvas_account_key": account_key,
        "canvas_source_item_key": item["source_item_key"],
        "canvas_event_ref": item["event_ref"],
        "canvas_context_id": item["context_id"],
        "canvas_calendar_id": item["calendar_id"],
        "canvas_item_id": item["item_id"],
        "canvas_occurrence_id": item["occurrence_id"],
        "canvas_item_type": item["item_type"],
        "canvas_source_revision": item["source_revision"],
        "canvas_source_hash": item["source_hash"],
        "canvas_completion_status": item["completion_status"],
        "canvas_completion_source": item["completion_source"],
        "canvas_last_seen_at": now,
        "canvas_last_seen_generation": generation,
        "canvas_last_seen_scope_hash": scope_hash,
        "event_uid": item["source_item_key"],
        "event_title": item["title"],
        "event_start": item["start"],
        "event_end": item["end"],
        "is_all_day": item["is_all_day"],
        "event_type": item["item_type"],
        "course_name": item["course_name"],
        "raw_description": item["description"],
        "fetched_at": now,
        "canvas_soft_deleted": 0,
        "canvas_deleted_at": None,
    }


def _canvas_cache_is_changed(row, values):
    if row["canvas_soft_deleted"]:
        return True
    for field in _CANVAS_CACHE_OWNED_FIELDS:
        if row[field] != values[field]:
            return True
    return False


def _canvas_insert_cache(connection, user_id, values):
    data = {
        "id": uuid.uuid4().hex,
        "user_id": user_id,
        "feed_url": None,
        "feed_url_hash": None,
        **values,
    }
    columns = list(data)
    placeholders = ", ".join("?" for _ in columns)
    connection.execute(
        f"INSERT INTO calendar_cache ({', '.join(columns)}) VALUES ({placeholders})",
        [data[column] for column in columns],
    )
    return connection.execute("SELECT * FROM calendar_cache WHERE id = ?", [data["id"]]).fetchone()


def _canvas_update_cache(connection, row, values):
    mutable = dict(values)
    assignments = ", ".join(f"{field} = ?" for field in mutable)
    connection.execute(
        f"UPDATE calendar_cache SET {assignments} WHERE id = ?",
        [*mutable.values(), row["id"]],
    )
    return connection.execute("SELECT * FROM calendar_cache WHERE id = ?", [row["id"]]).fetchone()


def _canvas_parse_batch_request(items, *, generation, lease_token, idempotency_key, checkpoint, payload):
    if payload is not None:
        if not isinstance(payload, Mapping):
            raise ExtensionContractError("invalid_json", "Sync batch payload must be a JSON object.")
        if items is None:
            items = payload.get("items")
        if generation is None:
            generation = payload.get("generation")
        if lease_token is None:
            lease_token = payload.get("lease_token", payload.get("leaseToken"))
        if idempotency_key is None:
            idempotency_key = payload.get("idempotency_key", payload.get("idempotencyKey"))
        if checkpoint is None:
            checkpoint = payload.get("checkpoint")
    if not isinstance(items, list):
        raise ExtensionContractError("invalid_items", "items must be an array.")
    if len(items) > CANVAS_BATCH_ITEM_LIMIT:
        raise ExtensionContractError("batch_too_large", "A Canvas batch may contain at most 100 items.")
    _canvas_reject_credentials(items)
    items_json = _canvas_json(items, field="items", max_bytes=CANVAS_BATCH_BYTES_LIMIT)
    idempotency_key = _canvas_idempotency_key(idempotency_key, field="batch_idempotency_key")
    generation = _canvas_generation(generation, required=False)
    checkpoint_json = None
    if checkpoint is not None:
        _canvas_reject_credentials(checkpoint)
        checkpoint_json = _canvas_json(checkpoint, field="checkpoint", max_bytes=64 * 1024)
    return items, items_json, generation, lease_token, idempotency_key, checkpoint_json


def ingest_canvas_sync_batch(
    user_id,
    source_id,
    run_id,
    items=None,
    *,
    generation=None,
    lease_token=None,
    idempotency_key=None,
    checkpoint=None,
    payload=None,
):
    """Validate and atomically ingest one idempotent Canvas batch."""
    user_id = _canvas_user_id(user_id)
    source_id = _canvas_id(source_id, field="source_id", pattern=CANVAS_RUN_ID_PATTERN)
    (
        items,
        items_json,
        generation,
        lease_token,
        idempotency_key,
        checkpoint_json,
    ) = _canvas_parse_batch_request(
        items,
        generation=generation,
        lease_token=lease_token,
        idempotency_key=idempotency_key,
        checkpoint=checkpoint,
        payload=payload,
    )
    payload_hash = _canvas_hash(items_json)
    if not isinstance(lease_token, str) or not lease_token.strip():
        raise ExtensionContractError("invalid_lease_token", "lease_token is required.")
    with calendar_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        source = _require_canvas_source(connection, user_id, source_id)
        run = _require_current_active_run(
            connection,
            user_id,
            source_id,
            run_id,
            generation=generation,
            lease_token=lease_token,
        )
        _canvas_source_consent(
            connection,
            source,
            version=run["consent_version"],
            scopes=CANVAS_READ_SCOPES,
        )
        receipt = connection.execute(
            """SELECT * FROM calendar_sync_batches
               WHERE user_id = ? AND source_id = ? AND idempotency_key = ?""",
            [user_id, source_id, idempotency_key],
        ).fetchone()
        if receipt:
            if receipt["payload_hash"] != payload_hash or receipt["run_id"] != run_id or (
                generation is not None and receipt["generation"] != generation
            ):
                raise ExtensionContractError(
                    "idempotency_conflict",
                    "The batch idempotency key was already used with different parameters.",
                )
            return _canvas_batch_payload(receipt, idempotent=True)

        scope = _canvas_decode_json(run["scope_json"], {})
        counters = _canvas_decode_json(run["counters_json"], {})
        counters = {
            "accepted": int(counters.get("accepted", 0)),
            "updated": int(counters.get("updated", 0)),
            "unchanged": int(counters.get("unchanged", 0)),
            "quarantined": int(counters.get("quarantined", 0)),
        }
        batch_counts = {key: 0 for key in counters}
        now = _canvas_now()
        accepted_items = []
        for raw_item in items:
            try:
                item = _normalize_canvas_item(
                    raw_item,
                    source_id=source_id,
                    account_key=source["account_key"],
                )
            except ExtensionContractError as exc:
                if exc.code == "credentials_not_allowed":
                    raise
                batch_counts["quarantined"] += 1
                continue
            values = _canvas_cache_values(
                source_id,
                source["account_key"],
                item,
                now,
                run["generation"],
                run["scope_hash"],
            )
            existing = _canvas_cache_row(
                connection,
                user_id,
                source_id,
                source["account_key"],
                item,
            )
            if existing is None:
                _canvas_insert_cache(connection, user_id, values)
                batch_counts["accepted"] += 1
            else:
                changed = _canvas_cache_is_changed(existing, values)
                _canvas_update_cache(connection, existing, values)
                batch_counts["updated" if changed else "unchanged"] += 1
                batch_counts["accepted"] += 1
            accepted_items.append(item["event_ref"])

        for key, value in batch_counts.items():
            counters[key] += value
        result = {
            **batch_counts,
            "accepted_event_refs": accepted_items,
            "batch_size": len(items),
            "payload_hash": payload_hash,
        }
        connection.execute(
            """INSERT INTO calendar_sync_batches
               (id, user_id, source_id, run_id, generation, idempotency_key,
                payload_hash, checkpoint_json, result_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                uuid.uuid4().hex,
                user_id,
                source_id,
                run_id,
                run["generation"],
                idempotency_key,
                payload_hash,
                checkpoint_json,
                _canvas_json(result, field="batch_result"),
                now,
            ],
        )
        checkpoint_value = _canvas_decode_json(checkpoint_json, None)
        if checkpoint_json is None:
            checkpoint_json = run["checkpoint_json"]
            checkpoint_value = _canvas_decode_json(checkpoint_json, None)
        cursor = checkpoint_value.get("cursor") if isinstance(checkpoint_value, dict) else None
        connection.execute(
            """UPDATE calendar_sync_runs
               SET counters_json = ?, checkpoint_json = ?, cursor = ?, updated_at = ?
               WHERE id = ? AND state = 'active'""",
            [
                _canvas_json(counters, field="counters"),
                checkpoint_json,
                cursor,
                now,
                run["id"],
            ],
        )
        receipt = connection.execute(
            """SELECT * FROM calendar_sync_batches
               WHERE user_id = ? AND source_id = ? AND idempotency_key = ?""",
            [user_id, source_id, idempotency_key],
        ).fetchone()
    return _canvas_batch_payload(receipt)


def _canvas_finalize_status(value):
    normalized = str(value or "complete").strip().lower().replace("-", "_")
    if normalized in {"completed", "complete", "done"}:
        return "complete"
    if normalized in {"partial", "incomplete"}:
        return "partial"
    raise ExtensionContractError("invalid_run_status", "A sync run may finalize only as complete or partial.")


def finalize_canvas_sync_run(
    user_id,
    source_id,
    run_id,
    *,
    scope=None,
    generation=None,
    lease_token=None,
    status="complete",
    complete=None,
):
    """Close the current run; only an exact complete scope may tombstone."""
    user_id = _canvas_user_id(user_id)
    source_id = _canvas_id(source_id, field="source_id", pattern=CANVAS_RUN_ID_PATTERN)
    run_id = _canvas_id(run_id, field="run_id", pattern=CANVAS_RUN_ID_PATTERN)
    if complete is not None:
        if not isinstance(complete, bool):
            raise ExtensionContractError("invalid_run_status", "complete must be a boolean.")
        status = "complete" if complete else "partial"
    status = _canvas_finalize_status(status)
    normalized_scope, scope_json, scope_hash = _normalize_canvas_scope(scope)
    with calendar_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        source = _require_canvas_source(connection, user_id, source_id)
        run = _require_current_active_run(
            connection,
            user_id,
            source_id,
            run_id,
            generation=generation,
            lease_token=lease_token,
        )
        if run["scope_hash"] != scope_hash or run["scope_json"] != scope_json:
            raise ExtensionContractError(
                "scope_mismatch",
                "Finalization scope must exactly match the normalized begin scope.",
            )
        now = _canvas_now()
        tombstoned = 0
        if status == "complete":
            rows = connection.execute(
                """SELECT * FROM calendar_cache
                   WHERE user_id = ? AND canvas_source_id = ? AND canvas_account_key = ?""",
                [user_id, source_id, source["account_key"]],
            ).fetchall()
            for cache_row in rows:
                if not _canvas_scope_matches(cache_row, normalized_scope):
                    continue
                if (
                    cache_row["canvas_last_seen_generation"] == run["generation"]
                    and cache_row["canvas_last_seen_scope_hash"] == run["scope_hash"]
                ):
                    continue
                connection.execute(
                    """UPDATE calendar_cache
                       SET canvas_soft_deleted = 1, canvas_deleted_at = ?, canvas_last_seen_at = ?
                       WHERE id = ? AND canvas_soft_deleted = 0""",
                    [now, now, cache_row["id"]],
                )
                tombstoned += connection.execute("SELECT changes() AS count").fetchone()["count"]

        error_code = "partial" if status == "partial" else None
        error_message = "The sync completed without a full snapshot." if status == "partial" else None
        connection.execute(
            """UPDATE calendar_sync_runs
               SET state = ?, error_code = ?, error_message = ?, completed_at = ?, updated_at = ?
               WHERE id = ? AND state = 'active'""",
            [status, error_code, error_message, now, now, run["id"]],
        )
        _canvas_update_source_after_run(
            connection,
            source,
            state=status,
            now=now,
            error_code=error_code,
            error_message=error_message,
        )
        completed = connection.execute("SELECT * FROM calendar_sync_runs WHERE id = ?", [run["id"]]).fetchone()
    return _canvas_sync_run_payload(completed, tombstoned=tombstoned)


def get_canvas_import_routing(user_id, source_id, state=None):
    user_id = _canvas_user_id(user_id)
    if state is not None and state not in CANVAS_ROUTE_STATES:
        raise ExtensionContractError("invalid_route_state", "Routing state must be incomplete or completed.")
    with calendar_connection() as connection:
        source = _require_canvas_source(connection, user_id, source_id, include_archived=True)
        if source["status"] != "active":
            return [] if state is None else None
        try:
            _canvas_source_consent(
                connection,
                source,
                scopes=CANVAS_READ_SCOPES,
            )
        except ExtensionContractError as exc:
            # Revocation archives the source, but this also covers a source
            # whose consent was disconnected before cleanup completed. A GET
            # may safely appear empty after revocation.
            if exc.code in {"consent_required", "scope_required", "consent_version_mismatch"}:
                return [] if state is None else None
            raise
        source_id = source["source_id"]
        query = "SELECT * FROM calendar_import_routing WHERE user_id = ? AND source_id = ?"
        params = [user_id, source_id]
        if state is not None:
            query += " AND state = ?"
            params.append(state)
        query += " ORDER BY state ASC"
        rows = connection.execute(query, params).fetchall()
    if state is None:
        return [_canvas_routing_payload(dict(row), source) for row in rows]
    return _canvas_routing_payload(dict(rows[0]), source) if rows else None


def set_canvas_import_routing(
    user_id,
    source_id,
    state,
    destination_calendar_id=None,
    fallback_calendar_id=None,
    *,
    payload=None,
):
    if isinstance(state, Mapping):
        payload = state
        state = payload.get("state")
        destination_calendar_id = payload.get(
            "destination_calendar_id", payload.get("destinationCalendarId")
        )
        fallback_calendar_id = payload.get("fallback_calendar_id", payload.get("fallbackCalendarId"))
    if state not in CANVAS_ROUTE_STATES:
        raise ExtensionContractError("invalid_route_state", "Routing state must be incomplete or completed.")
    destination_calendar_id = _canvas_optional_id(
        destination_calendar_id,
        field="destination_calendar_id",
    )
    fallback_calendar_id = _canvas_optional_id(
        fallback_calendar_id,
        field="fallback_calendar_id",
    )
    user_id = _canvas_user_id(user_id)
    now = _canvas_now()
    with calendar_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        source = _require_canvas_source(connection, user_id, source_id, include_archived=False)
        _canvas_source_consent(
            connection,
            source,
            scopes=CANVAS_READ_SCOPES,
        )
        routing_inventory = {
            destination["id"]
            for destination in extension_calendar_destinations(user_id)
            if destination.get("visible") and destination.get("routing_eligible")
        }
        for field, calendar_id in (
            ("destination_calendar_id", destination_calendar_id),
            ("fallback_calendar_id", fallback_calendar_id),
        ):
            if calendar_id is not None and calendar_id not in routing_inventory:
                raise ExtensionContractError(
                    "routing_destination_unavailable",
                    f"{field} must name a visible, routing-eligible calendar.",
                )
        source_id = source["source_id"]
        existing = connection.execute(
            """SELECT * FROM calendar_import_routing
               WHERE user_id = ? AND source_id = ? AND state = ?""",
            [user_id, source_id, state],
        ).fetchone()
        if existing:
            unchanged = (
                existing["destination_calendar_id"] == destination_calendar_id
                and existing["fallback_calendar_id"] == fallback_calendar_id
            )
            if unchanged:
                return _canvas_routing_payload(dict(existing), source, idempotent=True)
            connection.execute(
                """UPDATE calendar_import_routing
                   SET destination_calendar_id = ?, fallback_calendar_id = ?, updated_at = ?
                   WHERE id = ?""",
                [destination_calendar_id, fallback_calendar_id, now, existing["id"]],
            )
            row_id = existing["id"]
        else:
            row_id = uuid.uuid4().hex
            connection.execute(
                """INSERT INTO calendar_import_routing
                   (id, user_id, source_id, state, destination_calendar_id,
                    fallback_calendar_id, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    row_id,
                    user_id,
                    source_id,
                    state,
                    destination_calendar_id,
                    fallback_calendar_id,
                    now,
                    now,
                ],
            )
        row = connection.execute("SELECT * FROM calendar_import_routing WHERE id = ?", [row_id]).fetchone()
    return _canvas_routing_payload(dict(row), source)


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


def canvas_event_ref_for_item(
    source_id,
    account_key,
    context_id,
    calendar_id,
    item_type,
    item_id,
    occurrence_id=None,
):
    """Return the stable identity used by imported Canvas calendar items.

    Feed references deliberately continue to use their historical URL/UID
    hash.  Canvas references are scoped by user-owned source and account so a
    provider item can never alias another user's or another account's item.
    """
    source_id = _canvas_id(source_id, field="source_id", pattern=CANVAS_RUN_ID_PATTERN)
    account_key = validate_account_key(account_key)
    context_id = _canvas_id(context_id, field="context_id")
    calendar_id = _canvas_id(calendar_id, field="calendar_id")
    item_type = _canvas_item_type(item_type)
    item_id = _canvas_id(item_id, field="item_id")
    occurrence_id = _canvas_id(occurrence_id, field="occurrence_id", required=False)
    identity = {
        "account_key": account_key,
        "calendar_id": calendar_id,
        "context_id": context_id,
        "item_id": item_id,
        "item_type": item_type,
        "occurrence_id": occurrence_id or "",
        "source_id": source_id,
    }
    identity_json = json.dumps(identity, separators=(",", ":"), sort_keys=True)
    return f"canvas:{source_id}:{_canvas_hash(identity_json)}"


def _canvas_source_item_key(
    source_id,
    account_key,
    context_id,
    calendar_id,
    item_type,
    item_id,
    occurrence_id=None,
):
    """Return a compact stable source-item key for cache provenance."""
    return canvas_event_ref_for_item(
        source_id,
        account_key,
        context_id,
        calendar_id,
        item_type,
        item_id,
        occurrence_id,
    ).removeprefix("canvas:")


def _event_ref_for_cache_event(doc):
    if doc.get("canvas_event_ref"):
        return doc["canvas_event_ref"]
    if doc.get("canvas_source_id"):
        try:
            return canvas_event_ref_for_item(
                doc.get("canvas_source_id"),
                doc.get("canvas_account_key"),
                doc.get("canvas_context_id"),
                doc.get("canvas_calendar_id"),
                doc.get("canvas_item_type"),
                doc.get("canvas_item_id"),
                doc.get("canvas_occurrence_id"),
            )
        except (ExtensionContractError, TypeError, ValueError):
            return None
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
    if doc.get("canvas_source_id") or doc.get("canvas_event_ref"):
        return _serialize_canvas_event(doc, settings=settings)
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


def _serialize_canvas_event(doc, settings=None, source=None, *, authenticated=False):
    """Serialize a sanitized extension-owned Canvas cache row."""
    source = source or {}
    is_all_day = bool(doc.get("is_all_day", False))
    event_start = parse_datetime(doc.get("event_start"))
    event_end = parse_datetime(doc.get("event_end"))
    fetched_at = parse_datetime(doc.get("fetched_at"))
    is_multi_day, span_days = _span_metadata(event_start, event_end, is_all_day)
    event_ref = _event_ref_for_cache_event(doc)
    source_id = doc.get("canvas_source_id") or source.get("source_id")
    account_key = doc.get("canvas_account_key") or source.get("account_key")
    source_label = source.get("label") or source.get("source_id") or source_id
    source_url = doc.get("canvas_source_url") or source.get("origin")
    original_calendar_id = doc.get("canvas_calendar_id")
    serialized = {
        "uid": doc.get("event_uid") or doc.get("canvas_source_item_key") or event_ref,
        "event_ref": event_ref,
        "source_type": "canvas",
        "provider": source.get("provider") or CANVAS_PROVIDER,
        "source_id": source_id,
        "source_label": source_label,
        "account_label": source_label,
        "editable": True,
        "title": doc.get("event_title"),
        "start": _serialize_datetime(event_start, is_all_day),
        "end": _serialize_datetime(event_end, is_all_day),
        "type": doc.get("event_type") or doc.get("canvas_item_type"),
        "course": doc.get("course_name"),
        "description": doc.get("raw_description"),
        "fetched_at": fetched_at.isoformat() if fetched_at else None,
        "is_multi_day": is_multi_day,
        "span_days": span_days,
        "is_all_day": is_all_day,
        "reminder_minutes": _default_reminder_minutes(is_all_day),
        "calendar_id": original_calendar_id,
        "original_calendar_id": original_calendar_id,
        "source_item_type": doc.get("canvas_item_type") or doc.get("event_type"),
        "source_item_key": doc.get("canvas_source_item_key"),
        "completion_status": doc.get("canvas_completion_status") or "incomplete",
        "completion_source": doc.get("canvas_completion_source") or "canvas",
        "has_override": False,
        "routing_degraded": False,
        "stale": _canvas_truthy(doc.get("canvas_stale", doc.get("stale", False))),
    }
    if authenticated:
        serialized["source_url"] = source_url
    return serialized


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


def _load_calendar_feed_metadata(user_id, list_rows_fn=None):
    list_rows_fn = list_rows_fn or list_calendar_rows_all
    feed_table = COLLECTIONS.get("calendar_feeds")
    if not feed_table:
        return {}
    rows = list_rows_fn(
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
        canvas_hash = _feed_url_hash(canvas_url)
        canvas_meta = feed_metadata.get(canvas_hash) or {}
        sources.append({
            "id": CANVAS_SOURCE_ID,
            "kind": "canvas",
            "default_name": "Canvas",
            "url": canvas_url,
            "editable": True,
            "legacy_names": ["Canvas"],
            "status": derive_feed_status(canvas_meta),
            "last_error_message": canvas_meta.get("last_error_message") or "",
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
            "status": derive_feed_status(metadata),
            "last_error_message": metadata.get("last_error_message") or "",
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


def _load_local_calendar_sources(user_id, list_rows_fn=None):
    list_rows_fn = list_rows_fn or list_calendar_rows_all
    table_id = COLLECTIONS.get("user_calendar_sources")
    if not table_id:
        return []
    return list_rows_fn(
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


def extension_calendar_destinations(user_id):
    """Return the authenticated extension's safe, visible routing destinations.

    The source/preference inputs intentionally come from the same loader family
    used by the dashboard, calendar share, and ICS projections. Provider URLs,
    event rows, and account identifiers stay inside this service and are never
    copied into the extension response.
    """
    user_id = _canvas_user_id(user_id)
    settings = first_row(
        COLLECTIONS["user_settings"],
        [Query.equal("user_id", [user_id])],
    ) or _settings_defaults(user_id)
    preferences = _load_calendar_preferences(user_id)
    cache_events = list_calendar_rows_all(
        COLLECTIONS["calendar_cache"],
        [Query.equal("user_id", [user_id])],
    )
    feed_urls = _configured_feed_urls(settings)
    cache_events = _filter_configured_cache_events(cache_events, feed_urls)
    feed_metadata = _load_calendar_feed_metadata(user_id)
    local_sources = _load_local_calendar_sources(user_id)
    created_events = list_calendar_rows_all(
        COLLECTIONS["user_events"],
        [Query.equal("user_id", [user_id])],
    )
    sources = _configured_calendar_sources(
        settings,
        cache_events,
        preferences,
        feed_metadata,
        local_sources,
        created_events,
    )
    try:
        _, task_source = _task_calendar_payload(user_id, preferences)
    except (AppwriteException, AttributeError):
        task_source = None
    sources = _append_task_calendar_source(sources, task_source)

    preferences_by_name = {
        preference.get("calendar_name"): preference
        for preference in preferences
        if preference.get("calendar_name")
    }
    destinations = []
    for source in sources:
        calendar_id = source.get("id")
        if not isinstance(calendar_id, str) or not calendar_id.strip():
            continue
        source_status = str(source.get("status") or "active").strip().lower()
        if source_status in {"archived", "deleted", "hidden"}:
            continue
        preference = preferences_by_name.get(calendar_id)
        if preference is None:
            preference = next(
                (
                    preferences_by_name.get(legacy_name)
                    for legacy_name in source.get("legacy_names", [])
                    if preferences_by_name.get(legacy_name) is not None
                ),
                None,
            )
        if preference is not None:
            preference_visible = preference.get("visible", True)
            if preference_visible is not None and not _canvas_truthy(preference_visible):
                continue

        kind = str(source.get("kind") or "local").strip().lower() or "local"
        imported = bool(source.get("imported")) or kind in {"canvas", "external", "imported"}
        label = _normalize_source_label(
            (preference or {}).get("display_name")
            or source.get("display_name")
            or source.get("default_name")
            or calendar_id
        ) or calendar_id
        destinations.append({
            "id": calendar_id,
            "label": label,
            "visible": True,
            "read_only": bool(source.get("read_only")) or imported,
            "imported": imported,
            "kind": kind,
            "routing_eligible": True,
            "routing_degraded": False,
        })

    return sorted(destinations, key=lambda item: (item["label"].lower(), item["id"]))


def _task_calendar_payload(user_id, preferences, range_start=None, range_end=None):
    try:
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


def _load_event_overrides(user_id, list_rows_fn=None):
    list_rows_fn = list_rows_fn or list_calendar_rows_all
    table_id = COLLECTIONS.get("user_event_overrides")
    if not table_id:
        return []
    return list_rows_fn(
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


def _canvas_truthy(value):
    """Interpret SQLite/Appwrite boolean values without treating ``"0"`` as true."""
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _load_active_canvas_sources(user_id, *, require_shares_ics=False):
    """Load active Canvas sources only when the effective projection gates pass."""
    if not extension_capability_enabled("calendar_read"):
        return []
    if not extension_capability_enabled("calendar_projection"):
        return []
    if require_shares_ics and not extension_capability_enabled("calendar_shares_ics"):
        return []
    with calendar_connection() as connection:
        rows = connection.execute(
            """SELECT * FROM calendar_import_sources
               WHERE user_id = ? AND provider = 'canvas' AND status = 'active'
                 AND archived_at IS NULL
               ORDER BY created_at ASC""",
            [str(user_id)],
        ).fetchall()
        sources = []
        for row in rows:
            source = dict(row)
            try:
                _canvas_consent_from_connection(
                    connection,
                    user_id,
                    source.get("account_key"),
                    CANVAS_PROJECTION_SCOPES,
                )
            except ExtensionContractError:
                continue
            sources.append(_canvas_source_internal_payload(source))
    return sources


def _load_canvas_import_routing_rows(user_id):
    """Load display routing without mutating routes or validating destinations."""
    with calendar_connection() as connection:
        rows = connection.execute(
            """SELECT * FROM calendar_import_routing
               WHERE user_id = ?
               ORDER BY source_id ASC, state ASC""",
            [str(user_id)],
        ).fetchall()
    return [dict(row) for row in rows]


def _visible_calendar_fallback(preferences):
    """Choose the first visible configured calendar, or the legacy local calendar."""
    if isinstance(preferences, Mapping):
        preferences = [preferences]
    for preference in preferences or []:
        calendar_id = preference.get("calendar_name")
        visible = preference.get("visible", True)
        if calendar_id and (visible is None or _canvas_truthy(visible)):
            return calendar_id
    return DEFAULT_LOCAL_SOURCE_ID


def _canvas_route_state(doc):
    status = str(doc.get("canvas_completion_status") or "incomplete").strip().lower()
    return "completed" if status in {"completed", "complete", "done"} else "incomplete"


def _canvas_routed_calendar_id(doc, source, routing_by_key, visible_fallback, override):
    """Resolve display routing in precedence order and report degraded routing."""
    explicit_calendar = (override or {}).get("calendar_id")
    if explicit_calendar:
        return explicit_calendar, False

    source_id = doc.get("canvas_source_id") or source.get("source_id")
    route_state = _canvas_route_state(doc)
    route = routing_by_key.get((source_id, route_state))
    if route and route.get("destination_calendar_id"):
        return route["destination_calendar_id"], False
    if route and route.get("fallback_calendar_id"):
        return route["fallback_calendar_id"], True

    source_default = source.get("default_mirror_calendar")
    if source_default:
        return source_default, True
    return visible_fallback, True


def _project_canvas_calendar_events(
    user_id,
    cache_events,
    overrides_by_ref=None,
    *,
    preferences=None,
    range_start=None,
    range_end=None,
    source_rows=None,
    routing_rows=None,
    apply_event_override=_apply_event_override,
    api_event_overlaps_range=None,
    require_shares_ics=False,
):
    """Return sanitized authenticated Canvas events for the unified calendar feed.

    Loader contract: ``cache_events`` is a read-only sequence of cache rows and the
    helper returns a new list of response dictionaries.  It only reads active,
    consented sources/routes; it never updates cache rows, routes, or overrides.
    """
    source_rows = (
        _load_active_canvas_sources(user_id, require_shares_ics=require_shares_ics)
        if source_rows is None
        else source_rows
    )
    routing_rows = (
        _load_canvas_import_routing_rows(user_id)
        if routing_rows is None
        else routing_rows
    )
    source_by_key = {
        (source.get("source_id"), source.get("account_key")): source
        for source in source_rows
        if (
            source.get("source_id")
            and source.get("status", "active") == "active"
            and not source.get("archived_at")
            and source.get("consent_state", "active") == "active"
            and source.get("consented", True) is not False
        )
    }
    routing_by_key = {
        (row.get("source_id"), row.get("state")): row
        for row in routing_rows
        if row.get("source_id") and row.get("state") in CANVAS_ROUTE_STATES
    }
    overrides_by_ref = overrides_by_ref or {}
    api_event_overlaps_range = api_event_overlaps_range or _api_event_overlaps_range
    visible_fallback = _visible_calendar_fallback(preferences)
    projected = []

    for cache_event in cache_events or []:
        if not (cache_event.get("canvas_source_id") or cache_event.get("canvas_event_ref")):
            continue
        if _canvas_truthy(cache_event.get("canvas_soft_deleted")):
            continue
        source_key = (
            cache_event.get("canvas_source_id"),
            cache_event.get("canvas_account_key"),
        )
        source = source_by_key.get(source_key)
        if not source:
            continue
        event_ref = _event_ref_for_cache_event(cache_event)
        if not event_ref:
            continue
        override = overrides_by_ref.get(event_ref)
        serialized = _serialize_canvas_event(
            cache_event,
            source=source,
            authenticated=True,
        )
        routed_calendar_id, routing_degraded = _canvas_routed_calendar_id(
            cache_event,
            source,
            routing_by_key,
            visible_fallback,
            override,
        )
        serialized["calendar_id"] = routed_calendar_id
        serialized["routing_degraded"] = routing_degraded
        serialized = apply_event_override(serialized, override)
        if not serialized:
            continue
        if range_start and range_end and not api_event_overlaps_range(
            serialized,
            range_start,
            range_end,
        ):
            continue
        projected.append(serialized)
    return projected


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


def _load_calendar_preferences(user_id, list_rows_fn=None):
    list_rows_fn = list_rows_fn or list_calendar_rows_all
    return list_rows_fn(
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


def _calendar_shares_collection():
    return COLLECTIONS.get("calendar_shares", "calendar_shares")


def _share_url(share_code):
    if not share_code:
        return None
    try:
        return url_for("dashboard.public_calendar_share", share_code=share_code, _external=True)
    except (BuildError, RuntimeError):
        return f"/calendar/share/{share_code}"


def _generate_calendar_share_code(first_calendar_row_fn=None):
    first_calendar_row_fn = first_calendar_row_fn or first_calendar_row
    table_id = _calendar_shares_collection()
    while True:
        code = "".join(secrets.choice(CALENDAR_SHARE_CODE_CHARS) for _ in range(CALENDAR_SHARE_CODE_LENGTH))
        existing = first_calendar_row_fn(table_id, [Query.equal("share_code", [code])])
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


def _load_serialized_calendar_events(
    user_id,
    settings,
    range_start=None,
    range_end=None,
    dependencies=None,
):
    dependencies = dependencies or {}
    configured_feed_urls = dependencies.get(
        "configured_feed_urls",
        _configured_feed_urls,
    )
    list_calendar_rows = dependencies.get(
        "list_calendar_rows_all",
        list_calendar_rows_all,
    )
    range_queries = dependencies.get("range_queries", _range_queries)
    load_event_overrides = dependencies.get(
        "load_event_overrides",
        _load_event_overrides,
    )
    filter_configured_cache_events = dependencies.get(
        "filter_configured_cache_events",
        _filter_configured_cache_events,
    )
    serialize_event = dependencies.get("serialize_event", _serialize_event)
    apply_event_override = dependencies.get(
        "apply_event_override",
        _apply_event_override,
    )
    serialize_user_event = dependencies.get(
        "serialize_user_event",
        _serialize_user_event,
    )
    api_event_overlaps_range = dependencies.get(
        "api_event_overlaps_range",
        _api_event_overlaps_range,
    )

    feed_urls = configured_feed_urls(settings)
    cache_events = list_calendar_rows(
        COLLECTIONS["calendar_cache"],
        range_queries(
            user_id,
            "event_start",
            "event_end",
            "event_start",
            range_start,
            range_end,
        ),
    )
    created_events = list_calendar_rows(
        COLLECTIONS["user_events"],
        range_queries(
            user_id,
            "start",
            "end",
            "start",
            range_start,
            range_end,
        ),
    )
    event_overrides = load_event_overrides(user_id)
    overrides_by_ref = {
        override.get("event_ref"): override
        for override in event_overrides
        if override.get("event_ref")
    }

    cache_events = filter_configured_cache_events(cache_events, feed_urls)
    serialized_cache_events = []
    for cache_event in cache_events:
        serialized_event = serialize_event(cache_event, settings)
        serialized_event = apply_event_override(
            serialized_event,
            overrides_by_ref.get(serialized_event.get("event_ref")),
        )
        if serialized_event:
            serialized_cache_events.append(serialized_event)

    serialized_created_events = [serialize_user_event(e) for e in created_events]
    events = serialized_cache_events + serialized_created_events
    if range_start and range_end:
        events = [
            event
            for event in events
            if api_event_overlaps_range(event, range_start, range_end)
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


def _resolve_calendar_share_by_code(
    share_code,
    active_only=True,
    first_calendar_row_fn=None,
):
    first_calendar_row_fn = first_calendar_row_fn or first_calendar_row
    queries = [Query.equal("share_code", [share_code])]
    if active_only:
        queries.append(Query.equal("is_active", [True]))
    return first_calendar_row_fn(_calendar_shares_collection(), queries)


def _public_calendar_share_context(share):
    owner = get_row_safe(COLLECTIONS["users"], share.get("user_id"), allow_missing=True)
    owner_name = (owner or {}).get("name") or "APStudy User"
    return {
        "share_code": share.get("share_code"),
        "owner_name": owner_name,
        "scope_label": _calendar_share_scope_label(share),
    }


def _public_calendar_events_payload(
    share,
    requested_start=None,
    requested_end=None,
    dependencies=None,
):
    dependencies = dependencies or {}
    first_row_fn = dependencies.get("first_row", first_row)
    calendar_share_scope_range = dependencies.get(
        "calendar_share_scope_range",
        _calendar_share_scope_range,
    )
    intersect_ranges = dependencies.get("intersect_ranges", _intersect_ranges)
    calendar_share_payload = dependencies.get(
        "calendar_share_payload",
        _calendar_share_payload,
    )
    load_serialized_calendar_events = dependencies.get(
        "load_serialized_calendar_events_for_share",
        dependencies.get("load_serialized_calendar_events", _load_serialized_calendar_events),
    )
    load_calendar_preferences = dependencies.get(
        "load_calendar_preferences",
        _load_calendar_preferences,
    )
    task_calendar_payload = dependencies.get(
        "task_calendar_payload",
        _task_calendar_payload,
    )
    parse_json_list = dependencies.get("parse_json_list", _parse_json_list)
    load_calendar_feed_metadata = dependencies.get(
        "load_calendar_feed_metadata",
        _load_calendar_feed_metadata,
    )
    load_local_calendar_sources = dependencies.get(
        "load_local_calendar_sources",
        _load_local_calendar_sources,
    )
    append_task_calendar_source = dependencies.get(
        "append_task_calendar_source",
        _append_task_calendar_source,
    )
    configured_calendar_sources = dependencies.get(
        "configured_calendar_sources",
        _configured_calendar_sources,
    )
    sanitize_public_event = dependencies.get(
        "sanitize_public_event",
        _sanitize_public_event,
    )
    configured_feed_urls = dependencies.get(
        "configured_feed_urls",
        _configured_feed_urls,
    )
    sanitize_public_sources = dependencies.get(
        "sanitize_public_sources",
        _sanitize_public_sources,
    )

    user_id = str(share.get("user_id"))
    settings = first_row_fn(
        COLLECTIONS["user_settings"],
        [Query.equal("user_id", [user_id])],
    )
    share_start, share_end = calendar_share_scope_range(share)
    range_start, range_end = intersect_ranges(
        (requested_start, requested_end),
        (share_start, share_end),
    )
    if range_start and range_end and range_start >= range_end:
        return {
            "count": 0,
            "events": [],
            "feed_configured": False,
            "calendar_sources": [],
            "share": calendar_share_payload(share),
        }

    events, cache_events, created_events = load_serialized_calendar_events(
        user_id,
        settings,
        range_start,
        range_end,
    )
    preferences = load_calendar_preferences(user_id)
    task_events, task_source = task_calendar_payload(
        user_id,
        preferences,
        range_start,
        range_end,
    )
    events = events + task_events
    include_all = bool(share.get("include_all_calendars", True))
    allowed_calendars = set(parse_json_list(share.get("calendar_ids_json")))
    if not include_all:
        events = [
            event
            for event in events
            if (event.get("calendar_id") or event.get("course") or "Other") in allowed_calendars
        ]
    feed_metadata = load_calendar_feed_metadata(user_id)
    local_sources = load_local_calendar_sources(user_id)
    calendar_sources = append_task_calendar_source(
        configured_calendar_sources(
            settings,
            cache_events,
            preferences,
            feed_metadata,
            local_sources,
            created_events,
        ),
        task_source,
    )

    public_events = [sanitize_public_event(event) for event in events]
    return {
        "count": len(public_events),
        "events": public_events,
        "feed_configured": bool(configured_feed_urls(settings)),
        "calendar_sources": sanitize_public_sources(
            calendar_sources,
            share,
            preferences,
        ),
        "share": calendar_share_payload(share),
    }


def get_events_response(user_id, response_user_id, args, dependencies):
    """Build the authenticated calendar events API response."""
    collections = dependencies["collections"]
    query = dependencies["query"]
    jsonify = dependencies["jsonify"]
    first_row_fn = dependencies["first_row"]
    list_calendar_rows = dependencies["list_calendar_rows_all"]
    logger_instance = dependencies["logger"]
    parse_range_param = dependencies["parse_range_param"]
    configured_feed_urls = dependencies["configured_feed_urls"]
    load_calendar_preferences = dependencies["load_calendar_preferences"]
    load_calendar_feed_metadata = dependencies["load_calendar_feed_metadata"]
    load_local_calendar_sources = dependencies["load_local_calendar_sources"]
    load_event_overrides = dependencies["load_event_overrides"]
    refresh_initial_feed_cache = dependencies["refresh_initial_feed_cache"]
    filter_configured_cache_events = dependencies[
        "filter_configured_cache_events"
    ]
    task_calendar_payload = dependencies["task_calendar_payload"]
    append_task_calendar_source = dependencies["append_task_calendar_source"]
    configured_calendar_sources = dependencies["configured_calendar_sources"]
    serialize_event = dependencies["serialize_event"]
    apply_event_override = dependencies["apply_event_override"]
    serialize_user_event = dependencies["serialize_user_event"]
    api_event_overlaps_range = dependencies["api_event_overlaps_range"]
    resolve_last_fetched = dependencies["resolve_last_fetched"]
    project_canvas_events = dependencies.get(
        "project_canvas_events",
        _project_canvas_calendar_events,
    )

    range_start = parse_range_param(args.get("start"))
    range_end = parse_range_param(args.get("end"))
    if bool(args.get("start")) ^ bool(args.get("end")):
        return jsonify({"error": "start and end are required together"}), 400
    if (args.get("start") and not range_start) or (
        args.get("end") and not range_end
    ):
        return jsonify({"error": "start and end must be valid ISO-8601"}), 400

    try:
        settings = first_row_fn(
            collections["user_settings"],
            [query.equal("user_id", [user_id])],
        )
        feed_urls = configured_feed_urls(settings)
        cache_events = list_calendar_rows(
            collections["calendar_cache"],
            [
                query.equal("user_id", [user_id]),
                query.order_asc("event_start"),
            ],
        )
        created_events = list_calendar_rows(
            collections["user_events"],
            [
                query.equal("user_id", [user_id]),
                query.order_asc("start"),
            ],
        )
        preferences = load_calendar_preferences(user_id)
        feed_metadata = load_calendar_feed_metadata(user_id)
        local_sources = load_local_calendar_sources(user_id)
        event_overrides = load_event_overrides(user_id)
    except AppwriteException:
        logger_instance.exception("Failed to load calendar events")
        return jsonify({"error": "Unable to load calendar events."}), 500

    refresh_error = None
    refreshed = False
    if feed_urls:
        refreshed, refresh_error = refresh_initial_feed_cache(
            user_id,
            feed_urls,
            cache_events,
            feed_metadata,
        )
    if refreshed:
        try:
            cache_events = list_calendar_rows(
                collections["calendar_cache"],
                [
                    query.equal("user_id", [user_id]),
                    query.order_asc("event_start"),
                ],
            )
            feed_metadata = load_calendar_feed_metadata(user_id)
        except AppwriteException:
            logger_instance.exception(
                "Failed to reload calendar events after initial feed fetch"
            )
            return jsonify({"error": "Unable to load calendar events."}), 500

    canvas_cache_events = [
        event
        for event in cache_events
        if event.get("canvas_source_id") or event.get("canvas_event_ref")
    ]
    overrides_by_ref = {
        override.get("event_ref"): override
        for override in event_overrides
        if override.get("event_ref")
    }
    feed_cache_events = filter_configured_cache_events(
        [
            event
            for event in cache_events
            if not (event.get("canvas_source_id") or event.get("canvas_event_ref"))
        ],
        feed_urls,
    )
    serialized_canvas_events = []
    if canvas_cache_events:
        serialized_canvas_events = project_canvas_events(
            user_id,
            canvas_cache_events,
            overrides_by_ref,
            preferences=preferences,
            range_start=range_start,
            range_end=range_end,
            api_event_overlaps_range=api_event_overlaps_range,
        )
    try:
        task_events, task_source = task_calendar_payload(
            user_id,
            preferences,
            range_start,
            range_end,
        )
    except AppwriteException:
        logger_instance.exception("Failed to load task calendar events")
        return jsonify({"error": "Unable to load calendar events."}), 500

    calendar_sources = append_task_calendar_source(
        configured_calendar_sources(
            settings,
            feed_cache_events,
            preferences,
            feed_metadata,
            local_sources,
            created_events,
        ),
        task_source,
    )
    canvas_events_by_ref = {}
    for event in serialized_canvas_events:
        canvas_events_by_ref.setdefault(event.get("event_ref"), []).append(event)
    feed_event_keys = {
        event.get("$id") or event.get("id") or _event_ref_for_cache_event(event)
        for event in feed_cache_events
    }
    serialized_cache_events = []
    for cache_event in cache_events:
        if cache_event.get("canvas_source_id") or cache_event.get("canvas_event_ref"):
            event_ref = _event_ref_for_cache_event(cache_event)
            candidates = canvas_events_by_ref.get(event_ref) or []
            if candidates:
                serialized_cache_events.append(candidates.pop(0))
            continue
        event_key = (
            cache_event.get("$id")
            or cache_event.get("id")
            or _event_ref_for_cache_event(cache_event)
        )
        if event_key not in feed_event_keys:
            continue
        serialized_event = serialize_event(cache_event, settings)
        serialized_event = apply_event_override(
            serialized_event,
            overrides_by_ref.get(serialized_event.get("event_ref")),
        )
        if serialized_event:
            serialized_cache_events.append(serialized_event)
    serialized_created_events = [
        serialize_user_event(event)
        for event in created_events
    ]

    if range_start and range_end:
        serialized_cache_events = [
            event
            for event in serialized_cache_events
            if api_event_overlaps_range(event, range_start, range_end)
        ]
        serialized_created_events = [
            event
            for event in serialized_created_events
            if api_event_overlaps_range(event, range_start, range_end)
        ]

    serialized = (
        serialized_cache_events
        + serialized_created_events
        + task_events
    )

    return jsonify({
        "user_id": response_user_id,
        "count": len(serialized),
        "events": serialized,
        "feed_configured": bool(feed_urls),
        "calendar_sources": calendar_sources,
        "refresh_interval_minutes": (
            settings.get("feed_refresh_minutes")
            if settings
            else None
        ),
        "last_fetched": resolve_last_fetched(user_id),
        "refresh_error": refresh_error,
    })
