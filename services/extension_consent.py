"""Persistence and Phase 2 seams for extension calendar consent."""

from dataclasses import dataclass
import json
import uuid
from typing import Protocol

from services.database import db_connection
from services.extension_contract import (
    CANVAS_LEGACY_SOURCE_KEY,
    CONSENT_SCOPES,
    CURRENT_CONSENT_SCOPES,
    ExtensionContractError,
    canonical_canvas_source_key,
    extension_capability_enabled,
    validate_account_key,
    validate_grant_scopes,
    validate_scopes,
    validate_source_key,
    validate_version,
)
from services.time_utils import utcnow_iso


CONSENT_TABLE = "calendar_integration_consents"
ACTIVE_STATE = "active"
NOT_GRANTED_STATE = "not_granted"
REVOKED_STATE = "revoked"
DEFERRED_STATE = "deferred_to_phase_2"
NOT_APPLICABLE_STATE = "not_applicable"
READ_CONSENT_SCOPES = frozenset({"full_history_upload", "ongoing_read"})
CAPABILITY_SCOPE_MAP = {
    "mirroring": "calendar_mirroring",
    "two_way_writeback": "calendar_two_way_writeback",
}


@dataclass(frozen=True)
class ConsentRecord:
    id: str
    nest_user_id: str
    source_key: str
    account_key: str
    version: int
    scopes: dict
    state: str
    created_at: str
    updated_at: str
    granted_at: str | None
    revoked_at: str | None
    cancellation_state: str
    archive_state: str

    @property
    def granted_scopes(self):
        return tuple(scope for scope in CONSENT_SCOPES if self.scopes.get(scope, False))

    @property
    def current(self):
        return self.version == 1 and set(self.granted_scopes) == CURRENT_CONSENT_SCOPES

    def to_payload(self):
        granted_scopes = list(self.granted_scopes)
        active = self.state == ACTIVE_STATE
        current = self.current
        payload = {
            "version": self.version,
            "sourceKey": self.source_key,
            "source_key": self.source_key,
            "accountKey": self.account_key,
            "account_key": self.account_key,
            # An older incomplete grant remains visible for migration, but it
            # is not an authorizing v1 grant.
            "granted": active and current,
            "current": current,
            "state": self.state,
            # Contract v1 uses a bounded array.  ``scope_flags`` is retained as
            # an explicitly named compatibility field for older Nest callers.
            "scopes": granted_scopes,
            "granted_scopes": granted_scopes,
            "scope_flags": {scope: bool(self.scopes.get(scope, False)) for scope in CONSENT_SCOPES},
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "granted_at": self.granted_at,
            "revoked_at": self.revoked_at,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
            "grantedAt": self.granted_at,
            "revokedAt": self.revoked_at,
        }
        if self.state == REVOKED_STATE:
            payload["revocation"] = {
                "state": REVOKED_STATE,
                "cancellation": self.cancellation_state,
                "archive": self.archive_state,
            }
        else:
            payload["revocation"] = None
        return payload


class ConsentRevocationHooks(Protocol):
    """Phase 2 interface; implementations may cancel work and archive data."""

    def request_cancellation(self, consent: ConsentRecord) -> str:
        """Request cancellation of in-flight source work for this consent."""

    def request_archive(self, consent: ConsentRecord) -> str:
        """Request archival/deletion handling for source data owned by this consent."""


def deferred_revocation_state():
    """Return Phase 1's explicit no-op state for future cancellation/archive work."""
    return {
        "cancellation": DEFERRED_STATE,
        "archive": DEFERRED_STATE,
    }


def _scopes_json(scopes):
    return json.dumps(
        {
            scope: bool(scopes.get(scope, False)) if isinstance(scopes, dict) else scope in scopes
            for scope in CONSENT_SCOPES
        },
        separators=(",", ":"),
        sort_keys=True,
    )


def _scopes_from_row(raw_value):
    try:
        decoded = json.loads(raw_value or "{}")
    except (TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Stored consent scopes are invalid.") from exc
    if not isinstance(decoded, dict):
        raise RuntimeError("Stored consent scopes are invalid.")
    return {scope: bool(decoded.get(scope, False)) for scope in CONSENT_SCOPES}


def _record_from_row(row):
    if row is None:
        return None
    return ConsentRecord(
        id=row["id"],
        nest_user_id=row["nest_user_id"],
        source_key=row["source_key"],
        account_key=row["account_key"],
        version=int(row["version"]),
        scopes=_scopes_from_row(row["scopes_json"]),
        state=row["state"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        granted_at=row["granted_at"],
        revoked_at=row["revoked_at"],
        cancellation_state=row["cancellation_state"],
        archive_state=row["archive_state"],
    )


def _validate_identity(user_id, source_key, account_key):
    normalized_user_id = str(user_id or "").strip()
    if not normalized_user_id:
        raise ExtensionContractError("invalid_user", "Authenticated user id is required.")
    account_key = validate_account_key(account_key)
    return normalized_user_id, validate_source_key(source_key, account_key=account_key), account_key


def get_consent(user_id, source_key, account_key, path=None):
    normalized_user_id, source_key, account_key = _validate_identity(
        user_id, source_key, account_key
    )
    with db_connection(path) as connection:
        row = connection.execute(
            f"SELECT * FROM {CONSENT_TABLE} WHERE nest_user_id = ? AND source_key = ? AND account_key = ?",
            [normalized_user_id, source_key, account_key],
        ).fetchone()
        if row is None and source_key == CANVAS_LEGACY_SOURCE_KEY:
            row = connection.execute(
                f"SELECT * FROM {CONSENT_TABLE} WHERE nest_user_id = ? AND source_key = ? AND account_key = ?",
                [normalized_user_id, canonical_canvas_source_key(account_key), account_key],
            ).fetchone()
    return _record_from_row(row)


def put_consent(user_id, source_key, account_key, *, action, scopes, version=1, path=None):
    normalized_user_id, source_key, account_key = _validate_identity(
        user_id, source_key, account_key
    )
    validate_version(version)
    if action not in {"grant", "revoke"}:
        raise ExtensionContractError("invalid_action", "action must be 'grant' or 'revoke'.")
    normalized_scopes = validate_grant_scopes(scopes) if action == "grant" else validate_scopes(scopes)
    now = utcnow_iso()

    with db_connection(path) as connection:
        requested_source_key = source_key
        stored_source_key = (
            canonical_canvas_source_key(account_key)
            if action == "grant" and requested_source_key == CANVAS_LEGACY_SOURCE_KEY
            else source_key
        )
        source_key = stored_source_key
        row = connection.execute(
            f"SELECT * FROM {CONSENT_TABLE} WHERE nest_user_id = ? AND source_key = ? AND account_key = ?",
            [normalized_user_id, stored_source_key, account_key],
        ).fetchone()
        if row is None and action == "revoke" and requested_source_key == CANVAS_LEGACY_SOURCE_KEY:
            row = connection.execute(
                f"SELECT * FROM {CONSENT_TABLE} WHERE nest_user_id = ? AND source_key = ? AND account_key = ?",
                [normalized_user_id, canonical_canvas_source_key(account_key), account_key],
            ).fetchone()
        if row is not None:
            source_key = row["source_key"]
        current = _record_from_row(row)
        current_scopes = dict(current.scopes) if current else {scope: False for scope in CONSENT_SCOPES}
        next_scopes = dict(current_scopes)
        desired_value = action == "grant"
        for scope in normalized_scopes:
            next_scopes[scope] = desired_value
        # Read-only disclosure covers both upload/read paths and automatic
        # share/ICS inclusion.  Couple the disclosure scope to the complete
        # read grant so a partial read grant can never project implicitly.
        if next_scopes["full_history_upload"] and next_scopes["ongoing_read"]:
            next_scopes["shares_ics_inclusion"] = True
        else:
            next_scopes["shares_ics_inclusion"] = False
        next_state = ACTIVE_STATE if any(next_scopes.values()) else REVOKED_STATE

        if current and next_scopes == current_scopes and current.state == next_state:
            return current

        if next_state == ACTIVE_STATE:
            granted_at = current.granted_at if current and current.state == ACTIVE_STATE else now
            revoked_at = None
            cancellation_state = NOT_APPLICABLE_STATE
            archive_state = NOT_APPLICABLE_STATE
        else:
            granted_at = current.granted_at if current else None
            revoked_at = now
            cancellation_state = DEFERRED_STATE
            archive_state = DEFERRED_STATE

        if current:
            connection.execute(
                f"""UPDATE {CONSENT_TABLE}
                    SET version = ?, scopes_json = ?, state = ?, updated_at = ?,
                        granted_at = ?, revoked_at = ?, cancellation_state = ?, archive_state = ?
                    WHERE id = ?""",
                [
                    version,
                    _scopes_json(next_scopes),
                    next_state,
                    now,
                    granted_at,
                    revoked_at,
                    cancellation_state,
                    archive_state,
                    current.id,
                ],
            )
            row_id = current.id
        else:
            row_id = uuid.uuid4().hex
            created_at = now
            connection.execute(
                f"""INSERT INTO {CONSENT_TABLE}
                    (id, nest_user_id, source_key, account_key, version, scopes_json, state,
                     created_at, updated_at, granted_at, revoked_at, cancellation_state, archive_state)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    row_id,
                    normalized_user_id,
                    source_key,
                    account_key,
                    version,
                    _scopes_json(next_scopes),
                    next_state,
                    created_at,
                    now,
                    granted_at,
                    revoked_at,
                    cancellation_state,
                    archive_state,
                ],
            )

        current_read_active = bool(
            current
            and current.state == ACTIVE_STATE
            and current.scopes.get("full_history_upload")
            and current.scopes.get("ongoing_read")
        )
        next_read_active = bool(
            next_scopes.get("full_history_upload")
            and next_scopes.get("ongoing_read")
        )
        if source_key in {CANVAS_LEGACY_SOURCE_KEY, canonical_canvas_source_key(account_key)} and (
            (current_read_active and not next_read_active)
            or (next_state == REVOKED_STATE and (current is None or current.state != REVOKED_STATE))
        ):
            # Keep cleanup in the same transaction as consent revocation. The
            # import service owns archival/cancellation details; this module
            # only invokes its Phase 2 hook.  Canonical keys are account
            # isolated; legacy ``canvas`` retains its one-release all-Canvas
            # cleanup behavior for the same authenticated account.
            from services.calendar_events import revoke_canvas_consent_in_connection

            revoke_canvas_consent_in_connection(
                connection,
                normalized_user_id,
                account_key,
                now=now,
            )

        return _record_from_row(
            connection.execute(
                f"SELECT * FROM {CONSENT_TABLE} WHERE id = ?", [row_id]
            ).fetchone()
        )


def empty_consent_payload(source_key, account_key, version=1):
    validate_version(version)
    account_key = validate_account_key(account_key)
    source_key = validate_source_key(source_key, account_key=account_key)
    return {
        "version": version,
        "sourceKey": source_key,
            "source_key": source_key,
        "accountKey": account_key,
        "account_key": account_key,
        "granted": False,
        "current": version == 1,
        "state": NOT_GRANTED_STATE,
        "scopes": [],
        "granted_scopes": [],
        "scope_flags": {scope: False for scope in CONSENT_SCOPES},
        "created_at": None,
        "updated_at": None,
        "granted_at": None,
        "revoked_at": None,
        "createdAt": None,
        "updatedAt": None,
        "grantedAt": None,
        "revokedAt": None,
        "revocation": None,
    }
