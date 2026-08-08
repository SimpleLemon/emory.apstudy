"""Invite codes, signup attribution, activation, and invite history."""

import logging
import secrets
import uuid
from datetime import datetime, timezone

from appwrite.exception import AppwriteException
from appwrite.query import Query

from appwrite_client import COLLECTIONS
from appwrite_helpers import (
    create_row_safe,
    delete_row_safe,
    first_row,
    format_datetime,
    get_row_safe,
    list_rows_all,
    update_row_safe,
)
from services import notifications
from services.discord_audit import emit_creation_event, format_actor
from services.entitlements import normalize_tier
from services.row_utils import row_id as _row_id


logger = logging.getLogger(__name__)

INVITE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
INVITE_CODE_LENGTH = 6
EMPTY_INVITE_LIMIT = 5
MAX_INVITE_LABEL_LENGTH = 80
ACTIVATION_SIGNALS = ("calendar", "note", "course", "chat_message", "task")
INVITE_BASE_URL = "https://nest.apstudy.org/join"


class InviteError(RuntimeError):
    """Base error for invite operations."""


class InviteLimitError(InviteError):
    """Raised when an owner already has the maximum number of empty invites."""

    def __init__(self, limit=EMPTY_INVITE_LIMIT):
        self.limit = int(limit)
        super().__init__(
            f"You can have at most {self.limit} invite links without a signup. "
            "Use an existing link or wait for someone to sign up."
        )


class InviteNotFoundError(InviteError):
    """Raised when an invite is missing or belongs to another user."""

def _now():
    return format_datetime(datetime.now(timezone.utc))


def _label(value):
    normalized = str(value or "").strip()
    if len(normalized) > MAX_INVITE_LABEL_LENGTH:
        raise ValueError(
            f"Invite labels must be {MAX_INVITE_LABEL_LENGTH} characters or fewer."
        )
    return normalized or None


def normalize_code(value):
    """Return a canonical invite code, or None when the input is invalid."""
    normalized = str(value or "").strip().upper()
    if len(normalized) != INVITE_CODE_LENGTH:
        return None
    if any(character not in INVITE_ALPHABET for character in normalized):
        return None
    return normalized


def invite_by_code(code, active_only=True):
    normalized = normalize_code(code)
    if not normalized:
        return None
    queries = [Query.equal("code", [normalized])]
    if active_only:
        queries.append(Query.equal("is_active", [True]))
    return first_row(COLLECTIONS["user_invites"], queries)


def generate_code():
    """Generate an unused, unambiguous six-character invite code."""
    for _ in range(64):
        candidate = "".join(
            secrets.choice(INVITE_ALPHABET) for _ in range(INVITE_CODE_LENGTH)
        )
        if not invite_by_code(candidate, active_only=False):
            return candidate
    raise InviteError("Unable to generate a unique invite code.")


def _owner_invites(user_id):
    return list_rows_all(
        COLLECTIONS["user_invites"],
        [
            Query.equal("owner_user_id", [str(user_id)]),
            Query.order_desc("created_at"),
        ],
    )


def _owner_attributions(user_id):
    return list_rows_all(
        COLLECTIONS["user_invite_attributions"],
        [
            Query.equal("inviter_user_id", [str(user_id)]),
            Query.order_desc("signed_up_at"),
        ],
    )


def list_invites_for_owner(user_id):
    """Return owner-scoped invites with counts and attributed public profiles."""
    user_id = str(user_id)
    invitations = _owner_invites(user_id)
    attributions = _owner_attributions(user_id)
    attributions_by_invite = {}
    for attribution in attributions:
        attributions_by_invite.setdefault(
            str(attribution.get("invite_id") or ""), []
        ).append(attribution)

    people_by_user_id = {}
    for attribution in attributions:
        invited_user_id = str(attribution.get("invited_user_id") or "")
        if not invited_user_id or attribution.get("is_anonymized"):
            continue
        if invited_user_id not in people_by_user_id:
            people_by_user_id[invited_user_id] = get_row_safe(
                COLLECTIONS["users"],
                invited_user_id,
                allow_missing=True,
            )

    payload = []
    for invitation in invitations:
        invite_id = str(_row_id(invitation) or "")
        invite_attributions = attributions_by_invite.get(invite_id, [])
        people = []
        for attribution in invite_attributions:
            invited_user_id = str(attribution.get("invited_user_id") or "")
            user = people_by_user_id.get(invited_user_id)
            if not invited_user_id or not user:
                continue
            people.append(
                {
                    "user_id": invited_user_id,
                    "name": user.get("name"),
                    "username": user.get("username"),
                    "picture_url": user.get("picture_url"),
                    "status": attribution.get("status") or "invited",
                }
            )

        code = invitation.get("code")
        payload.append(
            {
                "id": invite_id,
                "code": code,
                "url": f"{INVITE_BASE_URL}/{code}",
                "label": invitation.get("label"),
                "is_active": bool(invitation.get("is_active")),
                "created_at": invitation.get("created_at"),
                "updated_at": invitation.get("updated_at"),
                "deactivated_at": invitation.get("deactivated_at"),
                "invited_count": len(invite_attributions),
                "joined_count": sum(
                    1
                    for attribution in invite_attributions
                    if attribution.get("status") == "joined"
                ),
                "people": people,
            }
        )
    return payload


def _empty_invite_count(user_id):
    attributions = _owner_attributions(user_id)
    used_invite_ids = {
        str(attribution.get("invite_id") or "") for attribution in attributions
    }
    return sum(
        1
        for invitation in _owner_invites(user_id)
        if str(_row_id(invitation) or "") not in used_invite_ids
    )


def _emit_invite_created(owner_user_id, invitation):
    try:
        emit_creation_event(
            "Invite Link Created",
            actor=format_actor(user_id=str(owner_user_id)),
            target=str(invitation.get("code") or "Invite"),
            metadata={
                "page_context": "settings/invites",
                "resource_type": "user_invite",
                "resource_id": _row_id(invitation),
                "label": invitation.get("label"),
            },
            color="green",
        )
    except Exception:
        logger.exception("Failed to emit invite creation audit event")


def create_invite(user_id, label=None):
    user_id = str(user_id)
    if _empty_invite_count(user_id) >= EMPTY_INVITE_LIMIT:
        raise InviteLimitError()

    now = _now()
    invitation = create_row_safe(
        COLLECTIONS["user_invites"],
        row_id=uuid.uuid4().hex,
        data={
            "code": generate_code(),
            "owner_user_id": user_id,
            "label": _label(label),
            "is_active": True,
            "created_at": now,
            "updated_at": now,
            "deactivated_at": None,
        },
    )
    _emit_invite_created(user_id, invitation)
    return invitation


def update_invite(user_id, invite_id, label=None, is_active=None):
    user_id = str(user_id)
    invitation = get_row_safe(
        COLLECTIONS["user_invites"],
        str(invite_id),
        allow_missing=True,
    )
    if not invitation or str(invitation.get("owner_user_id") or "") != user_id:
        raise InviteNotFoundError("Invite not found.")

    updates = {}
    if label is not None:
        updates["label"] = _label(label)
    if is_active is not None:
        next_active = bool(is_active)
        updates["is_active"] = next_active
        updates["deactivated_at"] = None if next_active else _now()
    if not updates:
        return invitation
    updates["updated_at"] = _now()
    return update_row_safe(
        COLLECTIONS["user_invites"],
        _row_id(invitation),
        updates,
    )


def attribute_signup(code, new_user_id):
    """Attribute a newly-created account to an active invite when eligible."""
    new_user_id = str(new_user_id)
    invitation = invite_by_code(code)
    if not invitation:
        return None
    if str(invitation.get("owner_user_id") or "") == new_user_id:
        return None
    if first_row(
        COLLECTIONS["user_invite_attributions"],
        [Query.equal("invited_user_id", [new_user_id])],
    ):
        return None

    user = get_row_safe(COLLECTIONS["users"], new_user_id, allow_missing=True)
    tier = normalize_tier((user or {}).get("tier"))
    try:
        return create_row_safe(
            COLLECTIONS["user_invite_attributions"],
            row_id=uuid.uuid4().hex,
            data={
                "invite_id": _row_id(invitation),
                "inviter_user_id": invitation.get("owner_user_id"),
                "invited_user_id": new_user_id,
                "status": "invited",
                "signed_up_at": _now(),
                "activation_signal": None,
                "activation_at": None,
                "joined_at": None,
                "initial_tier": tier,
                "current_tier": tier,
                "is_anonymized": False,
            },
        )
    except AppwriteException:
        if first_row(
            COLLECTIONS["user_invite_attributions"],
            [Query.equal("invited_user_id", [new_user_id])],
        ):
            return None
        raise


def _attribution_for_user(user_id):
    return first_row(
        COLLECTIONS["user_invite_attributions"],
        [Query.equal("invited_user_id", [str(user_id)])],
    )


def _emit_joined_side_effects(attribution):
    attribution_id = str(_row_id(attribution) or "")
    inviter_user_id = str(attribution.get("inviter_user_id") or "")
    invited_user_id = str(attribution.get("invited_user_id") or "")
    try:
        notifications.notify(
            inviter_user_id,
            "system",
            "Invite joined",
            "Someone you invited is now active on Nest",
            "/settings#tier",
            source_ref=attribution_id,
            dedupe_key=f"invite-joined:{attribution_id}",
            tag=f"invite-joined:{attribution_id}",
        )
    except Exception:
        logger.exception("Failed to notify inviter about joined user")

    try:
        emit_creation_event(
            "Invited User Joined",
            actor=format_actor(user_id=invited_user_id),
            target=f"Inviter {inviter_user_id}",
            metadata={
                "page_context": "settings/invites",
                "resource_type": "user_invite_attribution",
                "resource_id": attribution_id,
                "invite_id": attribution.get("invite_id"),
                "inviter_user_id": inviter_user_id,
                "invited_user_id": invited_user_id,
            },
            color="green",
        )
    except Exception:
        logger.exception("Failed to emit invite joined audit event")


def _promote(attribution):
    if not attribution or attribution.get("status") == "joined":
        return attribution
    updated = update_row_safe(
        COLLECTIONS["user_invite_attributions"],
        _row_id(attribution),
        {
            "status": "joined",
            "joined_at": _now(),
        },
    )
    _emit_joined_side_effects(updated)
    return updated


def record_activation(user_id, signal):
    signal = str(signal or "").strip().lower()
    if signal not in ACTIVATION_SIGNALS:
        raise ValueError("Unsupported invite activation signal.")

    attribution = _attribution_for_user(user_id)
    if not attribution:
        return None
    if not attribution.get("activation_signal"):
        attribution = update_row_safe(
            COLLECTIONS["user_invite_attributions"],
            _row_id(attribution),
            {
                "activation_signal": signal,
                "activation_at": _now(),
            },
        )

    user = get_row_safe(COLLECTIONS["users"], str(user_id), allow_missing=True)
    if user and user.get("onboarding_complete"):
        return _promote(attribution)
    return attribution


def promote_if_activated(user_id):
    attribution = _attribution_for_user(user_id)
    if not attribution or not attribution.get("activation_signal"):
        return attribution
    user = get_row_safe(COLLECTIONS["users"], str(user_id), allow_missing=True)
    if user and user.get("onboarding_complete"):
        return _promote(attribution)
    return attribution


def record_tier_change(user_id, from_tier, to_tier):
    from_tier = normalize_tier(from_tier)
    to_tier = normalize_tier(to_tier)
    if from_tier == to_tier:
        return None
    attribution = _attribution_for_user(user_id)
    if not attribution:
        return None

    event = create_row_safe(
        COLLECTIONS["user_invite_tier_events"],
        row_id=uuid.uuid4().hex,
        data={
            "attribution_id": _row_id(attribution),
            "invited_user_id": str(user_id),
            "from_tier": from_tier,
            "to_tier": to_tier,
            "changed_at": _now(),
        },
    )
    update_row_safe(
        COLLECTIONS["user_invite_attributions"],
        _row_id(attribution),
        {"current_tier": to_tier},
    )
    return event


def delete_tier_events_for_user(user_id):
    """Remove tier history that would identify a deleted invitee or become orphaned."""
    user_id = str(user_id)
    owned_attribution_ids = {
        str(_row_id(attribution) or "")
        for attribution in _owner_attributions(user_id)
        if _row_id(attribution)
    }
    events_by_id = {}
    for event in list_rows_all(
        COLLECTIONS["user_invite_tier_events"],
        [Query.equal("invited_user_id", [user_id])],
    ):
        events_by_id[str(_row_id(event) or "")] = event

    attribution_ids = sorted(owned_attribution_ids)
    for offset in range(0, len(attribution_ids), 50):
        for event in list_rows_all(
            COLLECTIONS["user_invite_tier_events"],
            [Query.equal("attribution_id", attribution_ids[offset:offset + 50])],
        ):
            events_by_id[str(_row_id(event) or "")] = event

    for event_id in events_by_id:
        if event_id:
            delete_row_safe(COLLECTIONS["user_invite_tier_events"], event_id)
    return len(events_by_id)


def anonymize_invitee(user_id):
    rows = list_rows_all(
        COLLECTIONS["user_invite_attributions"],
        [Query.equal("invited_user_id", [str(user_id)])],
    )
    for attribution in rows:
        update_row_safe(
            COLLECTIONS["user_invite_attributions"],
            _row_id(attribution),
            {
                "invited_user_id": None,
                "is_anonymized": True,
            },
        )
    return len(rows)
