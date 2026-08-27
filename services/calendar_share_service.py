"""Owner lifecycle and lookup gates for single-calendar ICS subscriptions."""

import json
import secrets
from urllib.parse import urlsplit, urlunsplit

from flask import current_app, has_app_context

from config import ENVIRONMENT_CONFIG_EXTENSION_KEY, load_environment_config
from services.calendar_ics_contract import (
    CalendarIcsFailure,
    CalendarIcsFailureCode,
    CalendarIcsOutcome,
    ELIGIBLE_CALENDAR_IDS,
    canonical_calendar_id,
)
from services.calendar_store import calendar_connection
from services.time_utils import utcnow_iso


CALENDAR_ICS_TOKEN_BYTES = 32
CALENDAR_ICS_FEED_PATH = "/api/calendar/share-feed.ics"


class CalendarIcsResourceError(RuntimeError):
    """A storage/resource failure that must not be disguised as token absence."""


def _environment_config():
    if has_app_context():
        configured = current_app.extensions.get(ENVIRONMENT_CONFIG_EXTENSION_KEY)
        if configured is not None:
            return configured
    return load_environment_config()


def _setting(name, default=None):
    if has_app_context() and name in current_app.config:
        return current_app.config[name]
    field_names = {
        "CALENDAR_ICS_SUBSCRIPTIONS_ENABLED": "calendar_ics_subscriptions_enabled_raw",
        "CALENDAR_ICS_SUBSCRIPTIONS_OWNER_ALLOWLIST": "calendar_ics_subscriptions_owner_allowlist_raw",
    }
    return getattr(_environment_config(), field_names.get(name, name.lower()), default)


def _truthy(value):
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _owner_allowlist():
    configured = _setting("CALENDAR_ICS_SUBSCRIPTIONS_OWNER_ALLOWLIST", "")
    if isinstance(configured, str):
        return frozenset(item.strip() for item in configured.split(",") if item.strip())
    if configured:
        return frozenset(str(item).strip() for item in configured if str(item).strip())
    return frozenset()


def calendar_ics_enabled_for_owner(user_id):
    enabled = _setting("CALENDAR_ICS_SUBSCRIPTIONS_ENABLED", None)
    if enabled is None:
        enabled = _setting("calendar_ics_subscriptions_enabled", False)
    return _truthy(enabled) and str(user_id) in _owner_allowlist()


def require_calendar_ics_enabled(user_id):
    if not calendar_ics_enabled_for_owner(user_id):
        raise CalendarIcsFailure(
            CalendarIcsFailureCode.DISABLED,
            "Calendar ICS subscriptions are not enabled for this account.",
            status=403,
        )


def _row_id(row):
    return row.get("$id") or row.get("id")


def _parse_ids(value):
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item or "").strip()]
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return []
    return _parse_ids(parsed)


def normalized_ics_selection(share_or_values, calendar_ids=None):
    """Return one eligible canonical ID, or ``None`` for any other selection."""

    if calendar_ids is None and isinstance(share_or_values, dict):
        include_all = bool(share_or_values.get("include_all_calendars", True))
        calendar_ids = _parse_ids(share_or_values.get("calendar_ids_json"))
    else:
        include_all = bool(share_or_values)
        calendar_ids = _parse_ids(calendar_ids)
    if include_all or len(calendar_ids) != 1:
        return None
    return canonical_calendar_id(calendar_ids[0])


def require_eligible_selection(share_or_values, calendar_ids=None):
    selection = normalized_ics_selection(share_or_values, calendar_ids)
    if selection not in ELIGIBLE_CALENDAR_IDS:
        raise CalendarIcsFailure(
            CalendarIcsFailureCode.INELIGIBLE_SELECTION,
            "ICS subscriptions require exactly one eligible calendar: canvas, tasks, or simulated_courses.",
            status=422,
        )
    return selection


def assert_selection_change_allowed(existing, updates):
    """Fail closed when a retained secret would be pointed at another calendar."""

    if not existing.get("ics_token"):
        return
    current_target = normalized_ics_selection(existing)
    proposed = dict(existing)
    proposed.update(updates or {})
    proposed_target = normalized_ics_selection(proposed)
    if current_target is None or proposed_target != current_target:
        raise CalendarIcsFailure(
            CalendarIcsFailureCode.SELECTION_LOCKED,
            "Calendar selection is locked while an ICS subscription secret exists.",
            status=409,
        )


def new_ics_token():
    return secrets.token_urlsafe(CALENDAR_ICS_TOKEN_BYTES)


def _load_owned_share(connection, share_id, user_id):
    row = connection.execute(
        "SELECT * FROM calendar_shares WHERE id = ? AND user_id = ?",
        [str(share_id), str(user_id)],
    ).fetchone()
    return dict(row) if row else None


def _row_from_connection(row):
    if row is None:
        return None
    result = dict(row)
    result["$id"] = result["id"]
    for key in ("is_active", "include_all_calendars", "ics_enabled"):
        if key in result and result[key] is not None:
            result[key] = bool(result[key])
    return result


def _lifecycle(user_id, share_id, action):
    require_calendar_ics_enabled(user_id)
    now = utcnow_iso()
    with calendar_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = _load_owned_share(connection, share_id, user_id)
        if not row:
            raise CalendarIcsFailure(
                CalendarIcsFailureCode.NOT_FOUND,
                "Calendar share not found.",
                status=404,
            )
        if not row.get("is_active") and action != "remove":
            raise CalendarIcsFailure(
                CalendarIcsFailureCode.PARENT_REVOKED,
                "The parent calendar share is revoked.",
                status=409,
            )
        if action in {"enable", "rotate"}:
            require_eligible_selection(row)
        if action == "enable":
            token = row.get("ics_token") or new_ics_token()
            issued_at = row.get("ics_issued_at") or now
            connection.execute(
                """UPDATE calendar_shares
                   SET ics_token = ?, ics_enabled = 1, ics_issued_at = ?, updated_at = ?
                   WHERE id = ? AND user_id = ?""",
                [token, issued_at, now, str(share_id), str(user_id)],
            )
        elif action == "disable":
            if not row.get("ics_token"):
                raise CalendarIcsFailure(
                    CalendarIcsFailureCode.NOT_FOUND,
                    "Calendar ICS subscription is not configured.",
                    status=404,
                )
            connection.execute(
                "UPDATE calendar_shares SET ics_enabled = 0, updated_at = ? WHERE id = ? AND user_id = ?",
                [now, str(share_id), str(user_id)],
            )
        elif action == "rotate":
            if not row.get("ics_token"):
                raise CalendarIcsFailure(
                    CalendarIcsFailureCode.NOT_FOUND,
                    "Calendar ICS subscription is not configured.",
                    status=404,
                )
            connection.execute(
                """UPDATE calendar_shares
                   SET ics_token = ?, ics_rotated_at = ?, updated_at = ?
                   WHERE id = ? AND user_id = ?""",
                [new_ics_token(), now, now, str(share_id), str(user_id)],
            )
        elif action == "remove":
            connection.execute(
                """UPDATE calendar_shares
                   SET ics_token = NULL, ics_enabled = 0, ics_issued_at = NULL,
                       ics_rotated_at = NULL, updated_at = ?
                   WHERE id = ? AND user_id = ?""",
                [now, str(share_id), str(user_id)],
            )
        else:
            raise ValueError(f"Unsupported ICS lifecycle action: {action}")
        updated = connection.execute(
            "SELECT * FROM calendar_shares WHERE id = ? AND user_id = ?",
            [str(share_id), str(user_id)],
        ).fetchone()
    return CalendarIcsOutcome(_row_from_connection(updated), action)


def enable_calendar_ics(user_id, share_id):
    return _lifecycle(user_id, share_id, "enable")


def disable_calendar_ics(user_id, share_id):
    return _lifecycle(user_id, share_id, "disable")


def rotate_calendar_ics(user_id, share_id):
    return _lifecycle(user_id, share_id, "rotate")


def remove_calendar_ics(user_id, share_id):
    return _lifecycle(user_id, share_id, "remove")


def update_owned_calendar_share_with_invariants(user_id, share_id, updates):
    """Update a retained-secret share while rechecking its selection in-transaction."""

    with calendar_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = _load_owned_share(connection, share_id, user_id)
        if not row:
            raise CalendarIcsFailure(
                CalendarIcsFailureCode.NOT_FOUND,
                "Calendar share not found.",
                status=404,
            )
        assert_selection_change_allowed(row, updates)
        allowed = {
            "include_all_calendars", "calendar_ids_json", "date_scope", "fixed_start",
            "fixed_end", "rolling_days", "is_active", "ics_token", "ics_enabled",
            "ics_issued_at", "ics_rotated_at", "updated_at",
        }
        payload = {key: value for key, value in (updates or {}).items() if key in allowed}
        if not bool(payload.get("is_active", row.get("is_active", True))):
            payload.update({
                "ics_token": None,
                "ics_enabled": 0,
                "ics_issued_at": None,
                "ics_rotated_at": None,
            })
        if payload:
            assignments = ", ".join(f'"{key}" = ?' for key in payload)
            connection.execute(
                f"UPDATE calendar_shares SET {assignments} WHERE id = ? AND user_id = ?",
                [*payload.values(), str(share_id), str(user_id)],
            )
        updated = connection.execute(
            "SELECT * FROM calendar_shares WHERE id = ? AND user_id = ?",
            [str(share_id), str(user_id)],
        ).fetchone()
    return _row_from_connection(updated)


def creation_ics_fields(user_id, payload, normalized_share):
    """Return insert fields for an opted-in share, or an empty mapping."""

    if not _truthy(payload.get("icsEnabled", payload.get("ics_enabled", False))):
        return {}
    require_calendar_ics_enabled(user_id)
    require_eligible_selection(normalized_share)
    now = utcnow_iso()
    return {
        "ics_token": new_ics_token(),
        "ics_enabled": True,
        "ics_issued_at": now,
        "ics_rotated_at": None,
    }


def invalidate_calendar_ics_for_parent(connection, share_id):
    """Invalidate a browser share's ICS secret inside its parent transaction."""

    connection.execute(
        """UPDATE calendar_shares
           SET ics_token = NULL, ics_enabled = 0, ics_issued_at = NULL,
               ics_rotated_at = NULL
           WHERE id = ?""",
        [str(share_id)],
    )


def resolve_calendar_ics_token(token):
    """Fail-closed lookup seam for the later public feed projector."""

    if not token:
        raise CalendarIcsFailure(CalendarIcsFailureCode.INVALID_TOKEN, "Invalid calendar ICS token.", status=403)
    try:
        with calendar_connection() as connection:
            row = connection.execute(
                """SELECT * FROM calendar_shares
                   WHERE ics_token = ? AND ics_enabled = 1 AND is_active = 1""",
                [str(token)],
            ).fetchone()
    except Exception as exc:
        raise CalendarIcsResourceError("Calendar ICS token lookup unavailable.") from exc
    if not row:
        raise CalendarIcsFailure(CalendarIcsFailureCode.INVALID_TOKEN, "Invalid calendar ICS token.", status=403)
    share = _row_from_connection(row)
    if not calendar_ics_enabled_for_owner(share.get("user_id")):
        raise CalendarIcsFailure(CalendarIcsFailureCode.DISABLED, "Calendar ICS subscriptions are not enabled for this account.", status=403)
    require_eligible_selection(share)
    return share


def _app_base_url():
    configured = current_app.config.get("APP_BASE_URL") if has_app_context() else None
    configured = configured or _environment_config().app_base_url
    parsed = None
    try:
        parsed = urlsplit(str(configured or "").strip())
        hostname = parsed.hostname
        _ = parsed.port
    except ValueError:
        hostname = None
    if (
        parsed is None
        or parsed.scheme.lower() != "https"
        or not parsed.netloc
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise CalendarIcsFailure(CalendarIcsFailureCode.DISABLED, "Calendar ICS base URL is not configured securely.", status=503)
    return urlunsplit(("https", parsed.netloc, parsed.path.rstrip("/"), "", ""))


def owner_ics_metadata(share):
    token = share.get("ics_token")
    if not token:
        return {
            "configured": False,
            "enabled": False,
            "token": None,
            "httpsUrl": None,
            "webcalUrl": None,
        }
    base = _app_base_url()
    https_url = f"{base}{CALENDAR_ICS_FEED_PATH}?token={token}"
    parsed = urlsplit(https_url)
    webcal_url = urlunsplit(("webcal", parsed.netloc, parsed.path, parsed.query, ""))
    return {
        "configured": True,
        "enabled": bool(share.get("ics_enabled")),
        "token": token,
        "httpsUrl": https_url,
        "webcalUrl": webcal_url,
    }
