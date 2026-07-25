"""Settings API for creating and managing reusable invite links."""

import logging

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required

from appwrite.exception import AppwriteException
from appwrite.query import Query

from appwrite_client import COLLECTIONS
from appwrite_helpers import list_rows_all
from services import invites


invites_api_bp = Blueprint("invites_api", __name__)
logger = logging.getLogger(__name__)


def _onboarding_gate():
    if getattr(current_user, "onboarding_complete", False):
        return None
    return jsonify(
        {
            "error": "Complete onboarding before managing invite links.",
            "code": "onboarding_required",
        }
    ), 403


def _blocked_user_ids(user_id):
    user_id = str(user_id)
    blocked = set()
    outgoing = list_rows_all(
        COLLECTIONS["chat_blocks"],
        [Query.equal("blocker_id", [user_id])],
    )
    incoming = list_rows_all(
        COLLECTIONS["chat_blocks"],
        [Query.equal("blocked_id", [user_id])],
    )
    blocked.update(
        str(row.get("blocked_id") or "")
        for row in outgoing
        if row.get("blocked_id")
    )
    blocked.update(
        str(row.get("blocker_id") or "")
        for row in incoming
        if row.get("blocker_id")
    )
    return blocked


def _summary(user_id):
    user_id = str(user_id)
    invitation_rows = invites.list_invites_for_owner(user_id)
    blocked_user_ids = _blocked_user_ids(user_id)
    for invitation in invitation_rows:
        for person in invitation["people"]:
            person["can_message"] = person["user_id"] not in blocked_user_ids

    empty_count = sum(
        1 for invitation in invitation_rows if invitation["invited_count"] == 0
    )
    return {
        "invites": invitation_rows,
        "empty_invite_count": empty_count,
        "empty_invite_limit": invites.EMPTY_INVITE_LIMIT,
        "can_create": empty_count < invites.EMPTY_INVITE_LIMIT,
    }


@invites_api_bp.route("/settings/api/invites", methods=["GET"])
@login_required
def list_invites():
    forbidden = _onboarding_gate()
    if forbidden:
        return forbidden
    try:
        return jsonify(_summary(current_user.id))
    except AppwriteException:
        logger.exception("Failed to load invite settings")
        return jsonify({"error": "Unable to load invite links."}), 500


@invites_api_bp.route("/settings/api/invites", methods=["POST"])
@login_required
def create_invite():
    forbidden = _onboarding_gate()
    if forbidden:
        return forbidden
    payload = request.get_json(silent=True) or {}
    try:
        invites.create_invite(current_user.id, payload.get("label"))
        return jsonify(_summary(current_user.id)), 201
    except invites.InviteLimitError as exc:
        return jsonify(
            {
                "error": str(exc),
                "code": "empty_invite_limit",
                "limit": exc.limit,
            }
        ), 400
    except ValueError as exc:
        return jsonify({"error": str(exc), "code": "invalid_invite"}), 400
    except invites.InviteError:
        logger.exception("Invite service failed to create a link")
        return jsonify({"error": "Unable to create invite link."}), 500
    except AppwriteException:
        logger.exception("Failed to create invite link")
        return jsonify({"error": "Unable to create invite link."}), 500


@invites_api_bp.route("/settings/api/invites/<invite_id>", methods=["PATCH"])
@login_required
def update_invite(invite_id):
    forbidden = _onboarding_gate()
    if forbidden:
        return forbidden
    payload = request.get_json(silent=True) or {}
    if not any(key in payload for key in ("label", "is_active")):
        return jsonify({"error": "Choose a label or status to update."}), 400
    if "is_active" in payload and not isinstance(payload.get("is_active"), bool):
        return jsonify({"error": "is_active must be true or false."}), 400

    try:
        invites.update_invite(
            current_user.id,
            invite_id,
            label=payload.get("label") if "label" in payload else None,
            is_active=payload.get("is_active") if "is_active" in payload else None,
        )
        return jsonify(_summary(current_user.id))
    except invites.InviteNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc), "code": "invalid_invite"}), 400
    except AppwriteException:
        logger.exception("Failed to update invite link")
        return jsonify({"error": "Unable to update invite link."}), 500
