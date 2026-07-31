"""Shared chat channel and event persistence helpers."""

import logging

from appwrite.exception import AppwriteException
from appwrite.id import ID
from appwrite.permission import Permission
from appwrite.role import Role

from appwrite_client import COLLECTIONS
from appwrite_helpers import create_row_safe, format_datetime, get_row_safe, update_row_safe
from services.time_utils import utcnow
from services.universities import normalize_school_key


logger = logging.getLogger(__name__)


def _default_event_read_permissions(scope_type, *, channel=None, readable_user_ids=None):
    if readable_user_ids is not None:
        ids = [str(user_id) for user_id in readable_user_ids if user_id]
        if ids:
            return [
                Permission.read(Role.user(user_id))
                for user_id in sorted(set(ids))
            ]
    return [Permission.read(Role.users())]


def emit_chat_event(
    scope_type,
    scope_id,
    event_type,
    *,
    message_id=None,
    thread_id=None,
    channel_id=None,
    actor_id=None,
    readable_user_ids=None,
    channel=None,
    now_fn=utcnow,
    id_fn=ID.unique,
    create_row_fn=create_row_safe,
    event_read_permissions_fn=_default_event_read_permissions,
    notify_fn=None,
    error_logger=logger,
):
    if not scope_type or not scope_id or not event_type:
        return None
    now = format_datetime(now_fn())
    event_id = id_fn()
    data = {
        "scope_type": str(scope_type),
        "scope_id": str(scope_id),
        "event_type": str(event_type),
        "message_id": str(message_id) if message_id else None,
        "thread_id": str(thread_id) if thread_id else None,
        "channel_id": str(channel_id) if channel_id else None,
        "actor_id": str(actor_id) if actor_id else None,
        "created_at": now,
    }
    permissions = event_read_permissions_fn(
        scope_type,
        channel=channel,
        readable_user_ids=readable_user_ids,
    )
    try:
        row = create_row_fn(
            COLLECTIONS["chat_events"],
            row_id=event_id,
            data=data,
            permissions=permissions,
        )
    except AppwriteException:
        error_logger.exception("Failed to emit chat event to SQLite")
        return None
    if notify_fn:
        notify_fn()
    return row


def create_university_channel(
    school_key,
    school_name,
    *,
    now_fn=utcnow,
    normalize_school_key_fn=normalize_school_key,
    get_row_fn=get_row_safe,
    create_row_fn=create_row_safe,
    update_row_fn=update_row_safe,
):
    now = format_datetime(now_fn())
    channel_id = f"uni_{normalize_school_key_fn(school_key)[:56]}"
    existing = get_row_fn(
        COLLECTIONS["chat_channels"],
        channel_id,
        allow_missing=True,
    )
    payload = {
        "kind": "university",
        "name": school_name or "University",
        "label": school_name or "University",
        "section": "nest",
        "school_key": school_key,
        "school_name": school_name,
        "read_only": False,
        "approved": True,
        "updated_at": now,
    }
    if existing:
        return update_row_fn(COLLECTIONS["chat_channels"], channel_id, payload)
    return create_row_fn(
        COLLECTIONS["chat_channels"],
        row_id=channel_id,
        data={**payload, "created_at": now},
    )
