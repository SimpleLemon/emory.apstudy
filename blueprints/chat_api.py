import io
import json
import logging
import re
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone

from flask import Blueprint, Response, jsonify, request, send_file, stream_with_context
from flask_login import current_user, login_required

from appwrite.exception import AppwriteException
from appwrite.id import ID
from appwrite.permission import Permission
from appwrite.query import Query
from appwrite.role import Role

from appwrite_client import COLLECTIONS
from config import load_environment_config
from appwrite_helpers import (
    create_row_safe,
    delete_row_safe,
    format_datetime,
    get_row_safe,
    insert_row_ignore_safe,
    update_row_safe,
    parse_datetime,
)
from avatar_images import DEFAULT_AVATAR_URL
from services.chat_formatting import extract_links, fetch_link_preview, render_markdown, url_hash
from services.chat_read_state import (
    ChatReadStateDependencies as _ChatReadStateDependencies,
    clear_read_state as _clear_read_state_service,
    latest_unread_target as _latest_unread_target_service,
    latest_visible_message as _latest_visible_message_service,
    initialize_new_user_discord_read_states as _initialize_new_user_discord_read_states_service,
    mark_read as _mark_read_service,
    mark_unread as _mark_unread_service,
    message_can_be_unread_target as _message_can_be_unread_target_service,
    message_for_current_user as _message_for_current_user_service,
    message_in_scope as _message_in_scope_service,
    message_scope_field as _message_scope_field_service,
    message_visible_for_user as _message_visible_for_user_service,
    persist_read_state as _persist_read_state_service,
    previous_visible_message as _previous_visible_message_service,
    read_key as _read_key_service,
    read_state_for_scope as _read_state_for_scope_service,
    unread_count as _unread_count_service,
)
from services.chat_threads import (
    blocked_user_ids as _blocked_user_ids_service,
    create_welcome_dm_for_user as _create_welcome_dm_for_user_service,
    get_or_create_thread as _get_or_create_thread_service,
    get_or_create_thread_between as _get_or_create_thread_between_service,
    is_blocked_between as _is_blocked_between_service,
    other_participant as _other_participant_service,
    thread_accessible_by_user as _thread_accessible_by_user_service,
    thread_for_user as _thread_for_user_service,
    thread_key as _thread_key_service,
    thread_participant_ids as _thread_participant_ids_service,
)
from services.chat_discord_formatting import (
    DISCORD_CUSTOM_EMOJI_RE,
    DISCORD_IMAGE_EXTENSIONS,
    DISCORD_ROLE_MENTION_RE,
    DISCORD_USER_MENTION_RE,
    discord_attachment_is_image as _discord_attachment_is_image_service,
    discord_avatar as _discord_avatar_service,
    discord_images as _discord_images_service,
    discord_media_json as _discord_media_json_service,
    discord_message_external_id as _discord_message_external_id_service,
    discord_message_payload as _discord_message_payload_service,
    discord_message_row_id as _discord_message_row_id_service,
    discord_previews as _discord_previews_service,
    discord_role_mentions as _discord_role_mentions_service,
    discord_user_mention_label as _discord_user_mention_label_service,
    discord_user_mentions as _discord_user_mentions_service,
    discord_mention_name as _discord_mention_name_service,
    emoji_img as _emoji_img_service,
    mention_span as _mention_span_service,
    render_discord_content as _render_discord_content_service,
)
from services.chat_discord_sync import (
    DiscordSyncDependencies as _DiscordSyncDependencies,
    apply_discord_message_changes as _apply_discord_message_changes_service,
    can_sync_discord_channel as _can_sync_discord_channel_service,
    default_channels as _default_channels_service,
    delete_discord_gateway_message as _delete_discord_gateway_message_service,
    delete_discord_gateway_messages as _delete_discord_gateway_messages_service,
    discord_channel_for_discord_id as _discord_channel_for_discord_id_service,
    discord_message_changes as _discord_message_changes_service,
    ensure_discord_channel as _ensure_discord_channel_service,
    find_discord_message_row as _find_discord_message_row_service,
    ingest_discord_gateway_message as _ingest_discord_gateway_message_service,
    log_discord_upsert_failure as _log_discord_upsert_failure_service,
    prune_discord_messages as _prune_discord_messages_service,
    reconcile_discord_deletes as _reconcile_discord_deletes_service,
    soft_delete_discord_message as _soft_delete_discord_message_service,
    sync_discord_channel as _sync_discord_channel_service,
    sync_discord_channels as _sync_discord_channels_service,
    upsert_discord_message as _upsert_discord_message_service,
)
from services.chat_attachments import (
    AttachmentError,
    MAX_ATTACHMENTS_PER_MESSAGE,
    attachment_bytes,
    attachments_for_messages,
    bind_pending,
    create_attachment,
    delete_attachment,
    delete_message_attachments,
    get_attachment,
    serialize_attachment,
)
from services.chat_message_delivery import (
    AttachmentOwnershipError as _AttachmentOwnershipError,
    AttachmentBindingError as _AttachmentBindingError,
    AttachmentUnavailableError as _AttachmentUnavailableError,
    ChatMessageDeliveryDependencies as _ChatMessageDeliveryDependencies,
    DirectMessageBlockedError as _DirectMessageBlockedError,
    DirectMessagePersistenceError as _DirectMessagePersistenceError,
    DiscordDeliveryError as _DiscordDeliveryError,
    MessageExpiredError as _MessageExpiredError,
    MessageNotFoundError as _MessageNotFoundError,
    MessageOwnershipError as _MessageOwnershipError,
    MessagePersistenceError as _MessagePersistenceError,
    PendingAttachmentNotFoundError as _PendingAttachmentNotFoundError,
    attachment_scope_access as _attachment_scope_access_service,
    cancel_pending_attachment as _cancel_pending_attachment_service,
    can_access_attachment as _can_access_attachment_service,
    create_chat_attachment as _create_chat_attachment_service,
    create_direct_thread as _create_direct_thread_service,
    delete_chat_message as _delete_chat_message_service,
    get_message_for_current_user as _get_message_for_current_user_service,
    list_room_messages as _list_room_messages_service,
    list_thread_payloads as _list_thread_payloads_service,
    list_threads_for_current_user as _list_threads_for_current_user_service,
    read_attachment as _read_attachment_service,
    search_direct_message_users as _search_direct_message_users_service,
    send_channel_message as _send_channel_message_service,
    send_direct_message as _send_direct_message_service,
    update_block as _update_block_service,
)
from services.discord_bridge import (
    DiscordBridgeError,
    delete_webhook_message,
    execute_chat_webhook,
    fetch_channel_messages,
    fetch_discord_user,
    fetch_guild_roles,
)
from services.discord_audit import DiscordAuditEvent, emit_audit_event, format_actor
from services.environment_config import runtime_environment_config
from services.chat_presence import sync_chat_presence_labels_for_user, university_presence_label
from services.chat_presence_runtime import (
    fresh_presence_rows as _fresh_presence_rows_service,
    fresh_presence_rows_by_scope as _fresh_presence_rows_by_scope_service,
    presence_cutoff as _presence_cutoff_service,
    presence_fresh_seconds as _presence_fresh_seconds_service,
    presence_status_from_scopes as _presence_status_from_scopes_service,
    presence_statuses_for_users as _presence_statuses_for_users_service,
)
from services.chat_presence_views import (
    fresh_chat_room_presence as _fresh_chat_room_presence_service,
    fresh_typing_room_presence as _fresh_typing_room_presence_service,
    online_users_for_channel as _online_users_for_channel_service,
    presence_online_users as _presence_online_users_service,
    presence_scope_allowed as _presence_scope_allowed_service,
    school_key_for_user_row as _school_key_for_user_row_service,
    upsert_presence as _upsert_presence_service,
    user_can_access_channel_presence as _user_can_access_channel_presence_service,
)
from services.chat_summary_runtime import (
    assemble_bootstrap_payload as _assemble_bootstrap_payload_service,
    assemble_chat_summary_payload as _assemble_chat_summary_payload_service,
    channel_payload as _channel_payload_service,
    ensure_university_request as _ensure_university_request_service,
    existing_visible_channels_for_summary as _existing_visible_channels_for_summary_service,
    thread_payload as _thread_payload_service,
    university_placeholder_channel as _university_placeholder_channel_service,
)
from services.chat_event_runtime import (
    event_visible_for_user as _event_visible_for_user_service,
    serialize_chat_event as _serialize_chat_event_service,
)
from services.chat_events import (
    create_university_channel as _create_university_channel_service,
    emit_chat_event as _emit_chat_event_service,
)
from services.discord_chat import register_discord_chat_handlers
from services import database, invites, notifications
from services.entitlements import EntitlementLimitError, TIER_BADGES, TIER_LABELS, normalize_tier, request_entitlements
from services.giphy import GiphyError, api_key as giphy_api_key, is_available as giphy_available, resolve_gif
from services.row_utils import row_id as _row_id
from services.time_utils import utcnow as _now
from services.universities import normalize_school_key, school_payload, search_universities
from services.user_profile import (
    DEFAULT_BANNER_COLOR,
    is_early_member as _is_early_member,
    is_emory_school as _is_emory_school,
    normalize_banner_color as _normalize_banner_color,
    profile_handle as _profile_handle,
)


chat_api_bp = Blueprint("chat_api", __name__)


def _appwrite_chat_attachments_enabled():
    return runtime_environment_config().appwrite_chat_attachments_enabled


logger = logging.getLogger(__name__)


def list_rows_safe(table_id, queries=None):
    return database.list_rows(table_id, queries, include_total=False)


def list_rows_all(table_id, queries=None, limit=database.DEFAULT_LIMIT):
    return database.list_rows_all(table_id, queries, limit=limit)


def first_row(table_id, queries=None):
    return database.first_row(table_id, queries)

_IMPORT_ENVIRONMENT_CONFIG = load_environment_config()
CHAT_EVENTS_POLL_SECONDS = float(_IMPORT_ENVIRONMENT_CONFIG.chat_events_poll_seconds_raw)
CHAT_EVENTS_KEEPALIVE_SECONDS = float(
    _IMPORT_ENVIRONMENT_CONFIG.chat_events_keepalive_seconds_raw
)
CHAT_EVENTS_STREAM_LIMIT = int(_IMPORT_ENVIRONMENT_CONFIG.chat_events_stream_limit_raw)
CHAT_EVENTS_SCAN_MULTIPLIER = 4
CHAT_EVENTS_MAX_SCAN = 1000
CHAT_EVENTS_RETENTION_DAYS = 7
CHAT_EVENTS_MAX_ROWS = 50000
CHAT_EVENTS_CLEANUP_BATCH = 1000
CHAT_EVENTS_CLEANUP_INTERVAL_SECONDS = 60
PRESENCE_CHAT_FRESH_SECONDS = int(
    _IMPORT_ENVIRONMENT_CONFIG.presence_chat_fresh_seconds_raw
)
PRESENCE_SITE_FRESH_SECONDS = int(
    _IMPORT_ENVIRONMENT_CONFIG.presence_site_fresh_seconds_raw
)
PRESENCE_TYPING_FRESH_SECONDS = int(
    _IMPORT_ENVIRONMENT_CONFIG.presence_typing_fresh_seconds_raw
)
PRESENCE_FRESH_SECONDS = PRESENCE_CHAT_FRESH_SECONDS
PRESENCE_LOOKUP_LIMIT = int(_IMPORT_ENVIRONMENT_CONFIG.presence_lookup_limit_raw)
PRESENCE_ONLINE_LIMIT = int(_IMPORT_ENVIRONMENT_CONFIG.presence_online_limit_raw)
del _IMPORT_ENVIRONMENT_CONFIG

_chat_event_listener_lock = threading.Lock()
_chat_event_listeners = []
_chat_event_cleanup_lock = threading.Lock()
_chat_event_last_cleanup = time.monotonic()

DISCORD_MESSAGE_LIMIT = 50
MESSAGE_PAGE_SIZE = 50
DELETE_WINDOW_SECONDS = 5 * 60
DEFAULT_AVATAR = DEFAULT_AVATAR_URL
CHAT_MESSAGE_STRING_LIMITS = {
    "channel_id": 64,
    "thread_id": 64,
    "source": 32,
    "external_id": 255,
    "user_id": 64,
    "author_name": 120,
    "author_username": 64,
    "author_avatar_url": 2048,
    "discord_message_id": 32,
    "discord_webhook_id": 32,
}
CHAT_MESSAGE_TEXT_LIMIT = 60000
DISCORD_SYNC_COMPARE_FIELDS = (
    "channel_id",
    "source",
    "external_id",
    "author_name",
    "author_username",
    "author_avatar_url",
    "content",
    "rendered_html",
    "link_preview_json",
    "discord_message_id",
    "discord_webhook_id",
    "created_at",
)
DISCORD_PARTIAL_CREATE_REQUIRED_FIELDS = ("content", "timestamp")
CHAT_SUMMARY_SCAN_LIMIT = 50
CHAT_UNREAD_CAP = 99
WELCOME_DM_SENDER_ID = "69f922da37638df6557b"
WELCOME_DM_TEXT = (
    "Welcome to your Nest! If you have any questions, feedback, or run into any issues, "
    "please feel free to message me anytime :)"
)


def _chat_delivery_dependencies():
    """Build delivery callbacks from blueprint symbols at request time.

    Resolving these callbacks lazily keeps historical ``blueprints.chat_api``
    patch targets effective for both registered-route and direct route tests.
    """

    return _ChatMessageDeliveryDependencies(
        collections=COLLECTIONS,
        appwrite_exception=AppwriteException,
        attachment_error=AttachmentError,
        current_user_fn=lambda: current_user,
        current_user_id_fn=_current_user_id,
        message_media_payload_fn=_message_media_payload,
        previews_for_content_fn=_previews_for_content,
        now_fn=_now,
        format_datetime_fn=format_datetime,
        render_markdown_fn=render_markdown,
        row_id_fn=_row_id,
        get_row_fn=get_row_safe,
        create_row_fn=create_row_safe,
        insert_row_ignore_fn=insert_row_ignore_safe,
        update_row_fn=update_row_safe,
        delete_row_fn=delete_row_safe,
        id_unique_fn=ID.unique,
        get_attachment_fn=get_attachment,
        attachment_bytes_fn=attachment_bytes,
        bind_pending_fn=bind_pending,
        delete_message_attachments_fn=delete_message_attachments,
        emit_chat_event_fn=emit_chat_event,
        serialize_message_fn=_serialize_message,
        discord_external_id_fn=_discord_message_external_id,
        discord_row_id_fn=_discord_message_row_id,
        find_discord_message_row_fn=_find_discord_message_row,
        prune_discord_fn=_prune_discord_messages,
        execute_chat_webhook_fn=execute_chat_webhook,
        delete_webhook_message_fn=delete_webhook_message,
        notification_fn=notifications.notify,
        invite_activation_fn=invites.record_activation,
        first_row_fn=first_row,
        query_cls=Query,
        users_collection=COLLECTIONS["users"],
        thread_participant_ids_fn=_thread_participant_ids,
        thread_for_user_fn=_thread_for_user,
        other_participant_fn=_other_participant,
        is_blocked_between_fn=_is_blocked_between,
        threads_for_current_user_fn=_threads_for_current_user,
        logger=logger,
        attachment_download_url_fn=lambda attachment_id: (
            f"{request.url_root.rstrip('/')}/api/chat/attachments/{attachment_id}/download"
        ),
        delete_window_seconds=DELETE_WINDOW_SECONDS,
        message_timestamp_fn=_message_timestamp,
        audit_delete_fn=_emit_chat_delete_audit,
    )


def _bounded_string(value, limit, *, empty_as_none=False):
    if value is None:
        return None
    text = str(value)
    if empty_as_none and not text:
        return None
    return text[:limit]


def _bounded_chat_message_value(key, value):
    limit = CHAT_MESSAGE_STRING_LIMITS.get(key)
    if limit:
        return _bounded_string(value, limit, empty_as_none=key in {"discord_webhook_id"})
    if key in {"content", "rendered_html"} and isinstance(value, str):
        return value[:CHAT_MESSAGE_TEXT_LIMIT]
    return value


def _current_user_id():
    return str(current_user.id)


def _readable_by_users(user_ids=None):
    ids = [str(user_id) for user_id in (user_ids or []) if user_id]
    if ids:
        return [Permission.read(Role.user(user_id)) for user_id in sorted(set(ids))]
    return [Permission.read(Role.users())]


def _presence_read_permissions_for_channel(channel):
    if not channel:
        return []
    if channel.get("kind") == "discord":
        return [Permission.read(Role.users())]
    if channel.get("kind") == "university" and channel.get("approved"):
        label = university_presence_label(channel.get("school_key"))
        return [Permission.read(Role.label(label))] if label else []
    return []


def _presence_read_permissions_for_thread(thread):
    return _readable_by_users(_thread_participant_ids(thread or {}))


def _presence_scope(scope_type, scope_id):
    if not scope_type or not scope_id:
        return None
    return {
        "scope_type": str(scope_type),
        "scope_id": str(scope_id),
        "room_key": f"{scope_type}:{scope_id}",
    }


def _presence_cutoff(seconds=PRESENCE_FRESH_SECONDS):
    return _presence_cutoff_service(
        seconds,
        now_fn=_now,
        format_datetime_fn=format_datetime,
    )


def _presence_fresh_seconds(scope_type):
    return _presence_fresh_seconds_service(
        scope_type,
        chat_fresh_seconds=PRESENCE_CHAT_FRESH_SECONDS,
        site_fresh_seconds=PRESENCE_SITE_FRESH_SECONDS,
        typing_fresh_seconds=PRESENCE_TYPING_FRESH_SECONDS,
    )


def _presence_status_from_scopes(scopes):
    return _presence_status_from_scopes_service(scopes)


def _fresh_presence_rows(scope_types=None, *, user_ids=None, seconds=PRESENCE_FRESH_SECONDS, limit=1000):
    return _fresh_presence_rows_service(
        scope_types,
        user_ids=user_ids,
        seconds=seconds,
        limit=limit,
        cutoff_fn=_presence_cutoff,
        query_cls=Query,
        list_rows_fn=list_rows_safe,
        presence_collection=COLLECTIONS["chat_presence"],
        appwrite_exception=AppwriteException,
        error_logger=logger,
    )


def _fresh_presence_rows_by_scope(scope_types, *, user_ids=None, limit=1000):
    return _fresh_presence_rows_by_scope_service(
        scope_types,
        user_ids=user_ids,
        limit=limit,
        fresh_presence_rows_fn=_fresh_presence_rows,
        presence_fresh_seconds_fn=_presence_fresh_seconds,
        row_id_fn=_row_id,
    )


def _presence_statuses_for_users(user_ids):
    return _presence_statuses_for_users_service(
        user_ids,
        lookup_limit=PRESENCE_LOOKUP_LIMIT,
        fresh_presence_rows_by_scope_fn=_fresh_presence_rows_by_scope,
        presence_status_from_scopes_fn=_presence_status_from_scopes,
    )


def _presence_focus_user_ids():
    from services.focus_mode import active_focus_user_ids

    return active_focus_user_ids()


def _presence_user_resolver(rows, extra_user_ids=None):
    user_ids = [row.get("user_id") for row in rows]
    user_ids.extend(extra_user_ids or [])
    users_by_id = _load_users_by_id(user_ids)

    def resolve(_collection, user_id, allow_missing=True):
        return users_by_id.get(str(user_id))

    return resolve


def _presence_online_users():
    rows = _fresh_presence_rows_by_scope(
        ["site", "chat", "typing_channel", "typing_thread"],
        limit=PRESENCE_ONLINE_LIMIT * 8,
    )
    try:
        focus_user_ids = _presence_focus_user_ids()
    except sqlite3.OperationalError:
        focus_user_ids = set()
    return _presence_online_users_service(
        fresh_presence_rows_by_scope_fn=lambda _scope_types, limit: rows,
        presence_online_limit=PRESENCE_ONLINE_LIMIT,
        get_row_fn=_presence_user_resolver(rows, focus_user_ids),
        users_collection=COLLECTIONS["users"],
        appwrite_exception=AppwriteException,
        error_logger=logger,
        public_user_fn=_public_user,
        presence_status_from_scopes_fn=_presence_status_from_scopes,
        focus_user_ids_fn=lambda: focus_user_ids,
    )


def _fresh_chat_room_presence(scope_type, scope_id):
    rows = _fresh_presence_rows(
        [scope_type],
        seconds=_presence_fresh_seconds(scope_type),
        limit=1000,
    )
    return _fresh_chat_room_presence_service(
        scope_type,
        scope_id,
        fresh_presence_rows_fn=lambda _scope_types, seconds, limit: rows,
        presence_fresh_seconds_fn=_presence_fresh_seconds,
        get_row_fn=_presence_user_resolver(rows),
        users_collection=COLLECTIONS["users"],
        appwrite_exception=AppwriteException,
        error_logger=logger,
        public_user_fn=_public_user,
        presence_statuses_for_users_fn=_presence_statuses_for_users,
    )


def _fresh_typing_room_presence(scope_type, scope_id):
    rows = _fresh_presence_rows(
        [scope_type],
        seconds=_presence_fresh_seconds(scope_type),
        limit=1000,
    )
    return _fresh_typing_room_presence_service(
        scope_type,
        scope_id,
        fresh_presence_rows_fn=lambda _scope_types, seconds, limit: rows,
        presence_fresh_seconds_fn=_presence_fresh_seconds,
        current_user_id_fn=_current_user_id,
        get_row_fn=_presence_user_resolver(rows),
        users_collection=COLLECTIONS["users"],
        appwrite_exception=AppwriteException,
        error_logger=logger,
        public_user_fn=_public_user,
        presence_statuses_for_users_fn=_presence_statuses_for_users,
    )


def _school_key_for_user_row(user):
    return _school_key_for_user_row_service(
        user,
        school_payload_fn=school_payload,
    )


def _user_can_access_channel_presence(channel, user):
    return _user_can_access_channel_presence_service(
        channel,
        user,
        school_key_for_user_row_fn=_school_key_for_user_row,
    )


def _online_users_for_channel(channel):
    rows = _fresh_presence_rows_by_scope(
        ["chat", "site"],
        limit=PRESENCE_ONLINE_LIMIT * 4,
    )
    return _online_users_for_channel_service(
        channel,
        fresh_presence_rows_by_scope_fn=lambda _scope_types, limit: rows,
        presence_online_limit=PRESENCE_ONLINE_LIMIT,
        get_row_fn=_presence_user_resolver(rows),
        users_collection=COLLECTIONS["users"],
        appwrite_exception=AppwriteException,
        error_logger=logger,
        user_can_access_channel_presence_fn=_user_can_access_channel_presence,
        public_user_fn=_public_user,
        presence_status_from_scopes_fn=_presence_status_from_scopes,
    )


def _event_read_permissions(scope_type, *, channel=None, readable_user_ids=None):
    if readable_user_ids is not None:
        return _readable_by_users(readable_user_ids)
    if scope_type == "channel" and channel:
        permissions = _presence_read_permissions_for_channel(channel)
        if permissions:
            return permissions
    return [Permission.read(Role.users())]


def _notify_chat_event_waiters():
    with _chat_event_listener_lock:
        listeners = list(_chat_event_listeners)
    for listener in listeners:
        with listener:
            listener.notify_all()


def _thread_accessible_by_user(thread_id, user_id):
    return _thread_accessible_by_user_service(
        thread_id,
        user_id,
        get_row_fn=get_row_safe,
        threads_collection=COLLECTIONS["chat_dm_threads"],
    )


def _event_visible_for_user(event):
    return _event_visible_for_user_service(
        event,
        current_user_fn=lambda: current_user,
        current_user_id_fn=_current_user_id,
        get_row_fn=get_row_safe,
        channels_collection=COLLECTIONS["chat_channels"],
        can_access_channel_fn=_can_access_channel,
        thread_accessible_by_user_fn=_thread_accessible_by_user,
        school_payload_fn=school_payload,
    )


def _serialize_chat_event(row):
    return _serialize_chat_event_service(row, row_id_fn=_row_id)


class _ChatEventPage(list):
    def __init__(self, rows=(), *, scan_cursor=None):
        super().__init__(rows)
        self.scan_cursor = scan_cursor


def _list_chat_events_after(since=None, after_id=None, *, limit=CHAT_EVENTS_STREAM_LIMIT):
    limit = min(max(int(limit), 1), CHAT_EVENTS_STREAM_LIMIT)
    scan_budget = min(max(limit * CHAT_EVENTS_SCAN_MULTIPLIER, limit), CHAT_EVENTS_MAX_SCAN)
    visible = []
    seen_ids = set()
    visibility_cache = {}
    scanned = 0
    scan_cursor = (since, after_id) if since and after_id else None
    if since and after_id:
        query_stages = [
            [Query.equal("created_at", [since]), Query.greater_than("$id", after_id)],
            [Query.greater_than("created_at", since)],
        ]
    elif since:
        query_stages = [[Query.greater_than_equal("created_at", since)]]
    else:
        query_stages = [[]]

    for constraints in query_stages:
        offset = 0
        while scanned < scan_budget and len(visible) < limit:
            batch_limit = min(limit, scan_budget - scanned)
            queries = [*constraints, Query.order_asc("created_at"), Query.order_asc("$id"), Query.limit(batch_limit)]
            if offset:
                queries.append(Query.offset(offset))
            try:
                rows = database.list_rows(
                    COLLECTIONS["chat_events"],
                    queries,
                    include_total=False,
                ).get("rows", [])
            except AppwriteException:
                logger.exception("Failed to list chat events")
                return _ChatEventPage(visible, scan_cursor=scan_cursor)
            if not rows:
                break
            scanned_before_batch = scanned
            for row in rows:
                row_id = _row_id(row)
                if not row_id or row_id in seen_ids:
                    continue
                seen_ids.add(row_id)
                scanned += 1
                created_at = row.get("created_at") or ""
                candidate_cursor = (created_at, row_id)
                if scan_cursor is None or candidate_cursor > scan_cursor:
                    scan_cursor = candidate_cursor
                if since and created_at == since and after_id and row_id <= after_id:
                    continue
                scope_key = (row.get("scope_type"), row.get("scope_id"))
                if all(scope_key):
                    if scope_key not in visibility_cache:
                        visibility_cache[scope_key] = _event_visible_for_user(row)
                    row_visible = visibility_cache[scope_key]
                else:
                    row_visible = _event_visible_for_user(row)
                if row_visible:
                    visible.append(row)
                    if len(visible) >= limit:
                        break
            if scanned == scanned_before_batch or len(rows) < batch_limit or len(visible) >= limit:
                break
            offset += len(rows)
    return _ChatEventPage(visible, scan_cursor=scan_cursor)


def _cleanup_chat_events(*, now=None, path=None):
    now = now or _now()
    cutoff = format_datetime(now - timedelta(days=CHAT_EVENTS_RETENTION_DAYS))
    with database.db_connection(path) as conn:
        expired = conn.execute(
            """DELETE FROM chat_events WHERE id IN (
                   SELECT id FROM chat_events WHERE created_at < ?
                   ORDER BY created_at, id LIMIT ?
               )""",
            [cutoff, CHAT_EVENTS_CLEANUP_BATCH],
        ).rowcount
        remaining_budget = CHAT_EVENTS_CLEANUP_BATCH - expired
        overflow = 0
        if remaining_budget:
            overflow = conn.execute(
                """DELETE FROM chat_events WHERE id IN (
                       SELECT id FROM chat_events
                       ORDER BY created_at DESC, id DESC
                       LIMIT ? OFFSET ?
                   )""",
                [remaining_budget, CHAT_EVENTS_MAX_ROWS],
            ).rowcount
    return expired + overflow


def _maybe_cleanup_chat_events():
    global _chat_event_last_cleanup
    now_monotonic = time.monotonic()
    if now_monotonic - _chat_event_last_cleanup < CHAT_EVENTS_CLEANUP_INTERVAL_SECONDS:
        return 0
    with _chat_event_cleanup_lock:
        now_monotonic = time.monotonic()
        if now_monotonic - _chat_event_last_cleanup < CHAT_EVENTS_CLEANUP_INTERVAL_SECONDS:
            return 0
        _chat_event_last_cleanup = now_monotonic
    try:
        return _cleanup_chat_events()
    except Exception:
        logger.exception("Failed to clean up chat events")
        return 0


def _presence_scope_allowed(scope_type, scope_id):
    return _presence_scope_allowed_service(
        scope_type,
        scope_id,
        get_row_fn=get_row_safe,
        channels_collection=COLLECTIONS["chat_channels"],
        can_access_channel_fn=_can_access_channel,
        thread_for_user_fn=_thread_for_user,
        other_participant_fn=_other_participant,
        is_blocked_between_fn=_is_blocked_between,
        current_user_id_fn=_current_user_id,
        row_id_fn=_row_id,
    )


def _upsert_presence(scope_type, scope_id, tab_id):
    return _upsert_presence_service(
        scope_type,
        scope_id,
        tab_id,
        current_user_id_fn=_current_user_id,
        presence_scope_allowed_fn=_presence_scope_allowed,
        now_fn=_now,
        format_datetime_fn=format_datetime,
        presence_collection=COLLECTIONS["chat_presence"],
        query_cls=Query,
        first_row_fn=first_row,
        update_row_fn=update_row_safe,
        create_row_fn=create_row_safe,
        id_unique_fn=ID.unique,
        row_id_fn=_row_id,
    )


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
):
    row = _emit_chat_event_service(
        scope_type,
        scope_id,
        event_type,
        message_id=message_id,
        thread_id=thread_id,
        channel_id=channel_id,
        actor_id=actor_id,
        readable_user_ids=readable_user_ids,
        channel=channel,
        now_fn=_now,
        id_fn=ID.unique,
        create_row_fn=create_row_safe,
        event_read_permissions_fn=_event_read_permissions,
        notify_fn=_notify_chat_event_waiters,
        error_logger=logger,
    )
    if row:
        _maybe_cleanup_chat_events()
    return row


def _message_timestamp(row):
    value = parse_datetime(row.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value


def _format_member_since(value):
    parsed = parse_datetime(value)
    if parsed:
        return parsed.strftime("%b %d, %Y")
    return str(value) if value else ""


def _public_user(row):
    if not row:
        return None
    user_id = _row_id(row)
    name = row.get("name") or row.get("username") or "Nest User"
    username = row.get("username") or ""
    tier = normalize_tier(row.get("tier"))
    return {
        "id": user_id,
        "name": name,
        "username": username,
        "handle": _profile_handle(name, user_id, username),
        "picture_url": row.get("picture_url") or DEFAULT_AVATAR,
        "banner_color": _normalize_banner_color(row.get("banner_color")),
        "school": row.get("school") or "",
        "major": row.get("major") or "",
        "graduation_year": row.get("graduation_year") or row.get("class_year") or "",
        "class_year": row.get("class_year") or "",
        "education_level": row.get("education_level") or "",
        "member_since": _format_member_since(row.get("created_at")),
        "is_emory_school": _is_emory_school(row.get("school")),
        "is_early_member": _is_early_member(row.get("created_at")),
        "tier": tier,
        "tier_label": TIER_LABELS[tier],
        "tier_badge": TIER_BADGES.get(tier),
        "profile_url": f"/u/{username}" if username else f"/user/{user_id}",
    }


def _current_user_payload():
    return _public_user({
        "$id": _current_user_id(),
        "name": current_user.name,
        "username": current_user.username,
        "picture_url": current_user.picture_url,
        "banner_color": current_user.banner_color,
        "school": current_user.school,
        "major": current_user.major,
        "graduation_year": current_user.graduation_year,
        "class_year": current_user.class_year,
        "education_level": current_user.education_level,
        "created_at": current_user.created_at,
        "tier": current_user.tier,
    })


def _settings_payload():
    try:
        settings = first_row(
            COLLECTIONS["user_settings"],
            [Query.equal("user_id", [_current_user_id()])],
        )
    except AppwriteException:
        logger.exception("Failed to load chat settings")
        settings = None
    return {
        "chat_sound_enabled": bool((settings or {}).get("chat_sound_enabled", True)),
    }


def _discord_sync_dependencies():
    return _DiscordSyncDependencies(
        collections=COLLECTIONS,
        appwrite_exception=AppwriteException,
        query_cls=Query,
        id_unique_fn=ID.unique,
        row_id_fn=_row_id,
        now_fn=_now,
        format_datetime_fn=format_datetime,
        parse_datetime_fn=parse_datetime,
        message_timestamp_fn=_message_timestamp,
        runtime_environment_config_fn=runtime_environment_config,
        default_channels_fn=_default_channels,
        get_row_fn=get_row_safe,
        first_row_fn=first_row,
        create_row_fn=create_row_safe,
        insert_row_ignore_fn=insert_row_ignore_safe,
        update_row_fn=update_row_safe,
        delete_row_fn=delete_row_safe,
        list_rows_all_fn=list_rows_all,
        emit_chat_event_fn=emit_chat_event,
        delete_message_attachments_fn=delete_message_attachments,
        fetch_channel_messages_fn=fetch_channel_messages,
        ensure_discord_channel_fn=_ensure_discord_channel,
        discord_message_payload_fn=_discord_message_payload,
        discord_message_row_id_fn=_discord_message_row_id,
        discord_message_external_id_fn=_discord_message_external_id,
        discord_message_changes_fn=_discord_message_changes,
        find_discord_message_row_fn=_find_discord_message_row,
        apply_discord_message_changes_fn=_apply_discord_message_changes,
        upsert_discord_message_fn=_upsert_discord_message,
        log_discord_upsert_failure_fn=_log_discord_upsert_failure,
        soft_delete_discord_message_fn=_soft_delete_discord_message,
        reconcile_discord_deletes_fn=_reconcile_discord_deletes,
        sync_discord_channel_fn=_sync_discord_channel,
        delete_discord_gateway_message_fn=delete_discord_gateway_message,
        can_sync_discord_channel_fn=_can_sync_discord_channel,
        discord_channel_for_discord_id_fn=_discord_channel_for_discord_id,
        prune_discord_messages_fn=_prune_discord_messages,
        logger=logger,
        discord_message_limit=DISCORD_MESSAGE_LIMIT,
        partial_create_required_fields=DISCORD_PARTIAL_CREATE_REQUIRED_FIELDS,
    )


def _ensure_discord_channel(row_id, name, label, channel_id, read_only):
    return _ensure_discord_channel_service(
        row_id,
        name,
        label,
        channel_id,
        read_only,
        dependencies=_discord_sync_dependencies(),
    )


def _default_channels():
    return _default_channels_service(dependencies=_discord_sync_dependencies())


def _university_channel_id(school_key):
    return f"uni_{normalize_school_key(school_key)[:56]}"


def _find_university_channel(school_key):
    if not school_key:
        return None
    return first_row(
        COLLECTIONS["chat_channels"],
        [
            Query.equal("kind", ["university"]),
            Query.equal("school_key", [school_key]),
            Query.equal("approved", [True]),
        ],
    )


def create_university_channel(school_key, school_name):
    return _create_university_channel_service(
        school_key,
        school_name,
        now_fn=_now,
        normalize_school_key_fn=normalize_school_key,
        get_row_fn=get_row_safe,
        create_row_fn=create_row_safe,
        update_row_fn=update_row_safe,
    )


def _university_placeholder_channel(school_key, school_name, status):
    return _university_placeholder_channel_service(
        school_key,
        school_name,
        status,
        channel_id_fn=_university_channel_id,
        now_fn=_now,
        format_datetime_fn=format_datetime,
    )


def _ensure_university_request():
    return _ensure_university_request_service(
        current_user,
        school_payload_fn=school_payload,
        current_user_id_fn=_current_user_id,
        find_university_channel_fn=_find_university_channel,
        first_row_fn=first_row,
        query_cls=Query,
        collections=COLLECTIONS,
        create_university_channel_fn=create_university_channel,
        placeholder_channel_fn=_university_placeholder_channel,
        create_row_fn=create_row_safe,
        id_unique_fn=ID.unique,
        now_fn=_now,
        format_datetime_fn=format_datetime,
        appwrite_exception=AppwriteException,
        error_logger=logger,
    )


def _channel_payload(channel, university_status=None):
    return _channel_payload_service(
        channel,
        university_status,
        row_id_fn=_row_id,
        online_users_for_channel_fn=_online_users_for_channel,
        presence_scope_fn=_presence_scope,
        presence_read_permissions_for_channel_fn=_presence_read_permissions_for_channel,
    )


def _can_access_channel(channel):
    if not channel:
        return False
    if channel.get("kind") == "discord":
        return True
    if channel.get("kind") == "university":
        current_school = school_payload(current_user.school)
        return bool(channel.get("approved")) and channel.get("school_key") == current_school.get("school_key")
    return False


def _preview_for_url(url):
    key = url_hash(url)
    try:
        cached = first_row(COLLECTIONS["chat_link_previews"], [Query.equal("url_hash", [key])])
    except AppwriteException:
        cached = None
    if cached:
        return {
            "url": cached.get("url"),
            "title": cached.get("title") or "",
            "description": cached.get("description") or "",
            "image_url": cached.get("image_url") or "",
            "site_name": cached.get("site_name") or "",
            "content_type": cached.get("content_type") or "",
        }

    try:
        preview = fetch_link_preview(url)
    except Exception:
        logger.exception("Failed to fetch link preview")
        return None
    if not preview:
        return None

    now = format_datetime(_now())
    try:
        create_row_safe(
            COLLECTIONS["chat_link_previews"],
            row_id=ID.unique(),
            data={
                "url_hash": key,
                "url": preview.get("url") or url,
                "title": preview.get("title") or None,
                "description": preview.get("description") or None,
                "image_url": preview.get("image_url") or None,
                "site_name": preview.get("site_name") or None,
                "content_type": preview.get("content_type") or None,
                "created_at": now,
                "updated_at": now,
            },
        )
    except AppwriteException:
        logger.exception("Failed to cache link preview")
    return preview


def _previews_for_content(content):
    previews = []
    for link in extract_links(content, limit=2):
        preview = _preview_for_url(link)
        if preview:
            previews.append(preview)
    return previews


def _discord_previews(message):
    return _discord_previews_service(message)


def _discord_images(message):
    return _discord_images_service(
        message,
        attachment_is_image_fn=_discord_attachment_is_image,
    )


def _discord_attachment_is_image(attachment):
    return _discord_attachment_is_image_service(
        attachment,
        image_extensions=DISCORD_IMAGE_EXTENSIONS,
    )


def _discord_media_json(previews, images):
    return _discord_media_json_service(
        previews,
        images,
        bounded_string_fn=_bounded_string,
        text_limit=CHAT_MESSAGE_TEXT_LIMIT,
    )


def _discord_message_row_id(channel, discord_message_id):
    return _discord_message_row_id_service(channel, discord_message_id)


def _discord_message_external_id(channel, discord_message_id):
    return _discord_message_external_id_service(channel, discord_message_id)


def _discord_message_payload(channel, message, *, partial=False):
    return _discord_message_payload_service(
        channel,
        message,
        partial=partial,
        row_id_fn=_row_id,
        external_id_fn=_discord_message_external_id,
        format_datetime_fn=format_datetime,
        now_fn=_now,
        discord_avatar_fn=_discord_avatar,
        render_discord_content_fn=_render_discord_content,
        media_json_fn=_discord_media_json,
        previews_fn=_discord_previews,
        images_fn=_discord_images,
        bounded_chat_message_value_fn=_bounded_chat_message_value,
    )


def _discord_message_changes(existing, payload):
    return _discord_message_changes_service(
        existing,
        payload,
        compare_fields=DISCORD_SYNC_COMPARE_FIELDS,
    )


def _find_discord_message_row(row_id, external_id):
    return _find_discord_message_row_service(
        row_id,
        external_id,
        dependencies=_discord_sync_dependencies(),
    )


def _apply_discord_message_changes(existing, payload, message, *, partial=False, emit_event=False, channel=None):
    return _apply_discord_message_changes_service(
        existing,
        payload,
        message,
        partial=partial,
        emit_event=emit_event,
        channel=channel,
        dependencies=_discord_sync_dependencies(),
    )


def _log_discord_upsert_failure(row_id, external_id, discord_id, changes):
    return _log_discord_upsert_failure_service(
        row_id,
        external_id,
        discord_id,
        changes,
        logger=logger,
    )


def _discord_mention_name(user):
    return _discord_mention_name_service(user)


def _discord_user_mentions(message):
    return _discord_user_mentions_service(
        message,
        mention_name_fn=_discord_mention_name,
    )


def _discord_user_mention_label(user_id, mentions):
    return _discord_user_mention_label_service(
        user_id,
        mentions,
        fetch_user_fn=fetch_discord_user,
        mention_name_fn=_discord_mention_name,
    )


def _discord_role_mentions():
    return _discord_role_mentions_service(fetch_roles_fn=fetch_guild_roles)


def _mention_span(label, class_name="chat-mention"):
    return _mention_span_service(label, class_name)


def _emoji_img(animated, name, emoji_id):
    return _emoji_img_service(animated, name, emoji_id)


def _render_discord_content(content, message):
    return _render_discord_content_service(
        content,
        message,
        render_markdown_fn=render_markdown,
        user_mentions_fn=_discord_user_mentions,
        role_mentions_fn=_discord_role_mentions,
        user_mention_label_fn=_discord_user_mention_label,
        mention_span_fn=_mention_span,
        emoji_img_fn=_emoji_img,
        role_mention_re=DISCORD_ROLE_MENTION_RE,
        user_mention_re=DISCORD_USER_MENTION_RE,
        custom_emoji_re=DISCORD_CUSTOM_EMOJI_RE,
    )


def _load_users_by_id(user_ids):
    users_by_id = {}
    requested_ids = sorted({str(value) for value in (user_ids or []) if value})
    for start in range(0, len(requested_ids), 100):
        batch = requested_ids[start:start + 100]
        try:
            rows = database.list_rows(
                COLLECTIONS["users"],
                [Query.equal("$id", batch), Query.limit(len(batch))],
                include_total=False,
            ).get("rows", [])
        except AppwriteException:
            logger.exception("Failed to resolve chat users in batch")
            continue
        for row in rows:
            users_by_id[_row_id(row)] = row
    return users_by_id


def _serialize_message(row, users_by_id=None, attachments_by_message=None):
    created = _message_timestamp(row)
    user_id = row.get("user_id")
    author_profile = None
    if user_id:
        user_row = (users_by_id or {}).get(str(user_id))
        if user_row is None and users_by_id is None:
            try:
                user_row = get_row_safe(COLLECTIONS["users"], str(user_id), allow_missing=True)
            except AppwriteException:
                user_row = None
        if user_row:
            author_profile = _public_user(user_row)
    can_delete = (
        user_id
        and str(user_id) == _current_user_id()
        and not row.get("deleted_at")
        and (_now() - created).total_seconds() <= DELETE_WINDOW_SECONDS
    )
    previews = []
    images = []
    gif = None
    if row.get("link_preview_json"):
        try:
            media = json.loads(row.get("link_preview_json")) or []
        except (TypeError, json.JSONDecodeError):
            media = []
        gif = next((item for item in media if isinstance(item, dict) and item.get("kind") == "giphy_gif"), None)
        previews = [
            item for item in media
            if not isinstance(item, dict) or item.get("kind") not in {"discord_image", "giphy_gif"}
        ]
        images = [item for item in media if isinstance(item, dict) and item.get("kind") == "discord_image"]
    message_id = _row_id(row)
    if attachments_by_message is None:
        attachment_map = attachments_for_messages([message_id])
    else:
        attachment_map = attachments_by_message
    return {
        "id": message_id,
        "channel_id": row.get("channel_id"),
        "thread_id": row.get("thread_id"),
        "source": row.get("source") or "appwrite",
        "user_id": user_id,
        "author_name": row.get("author_name") or "Nest User",
        "author_username": row.get("author_username") or "",
        "author_avatar_url": row.get("author_avatar_url") or DEFAULT_AVATAR,
        "content": row.get("content") or "",
        "rendered_html": row.get("rendered_html") or render_markdown(row.get("content") or ""),
        "previews": previews,
        "images": images,
        "attachments": attachment_map.get(message_id, []),
        "gif": gif,
        "created_at": format_datetime(created),
        "can_delete": bool(can_delete),
        "delete_expires_at": (
            format_datetime(created + timedelta(seconds=DELETE_WINDOW_SECONDS))
            if user_id and str(user_id) == _current_user_id()
            else None
        ),
        "author_profile": author_profile,
    }


def _serialize_messages(rows):
    users_by_id = _load_users_by_id([row.get("user_id") for row in rows if row.get("user_id")])
    attachment_map = attachments_for_messages([_row_id(row) for row in rows])
    return [_serialize_message(row, users_by_id, attachment_map) for row in rows]


def _message_media_payload():
    payload = request.get_json(silent=True) or {}
    content = str(payload.get("content") or "").strip()
    attachment_ids = payload.get("attachment_ids") or []
    if not isinstance(attachment_ids, list):
        raise AttachmentError("Attachments must be provided as a list.")
    attachment_ids = list(dict.fromkeys(str(value) for value in attachment_ids if value))
    if len(attachment_ids) > MAX_ATTACHMENTS_PER_MESSAGE:
        raise AttachmentError("A message can include at most five attachments.")
    gif = None
    gif_id = str(payload.get("gif_id") or "").strip()
    if gif_id:
        gif = resolve_gif(gif_id, payload.get("gif_query"))
    if not content and not attachment_ids and not gif:
        raise AttachmentError("Add a message, attachment, or GIF before sending.")
    if len(content) > 2000:
        raise AttachmentError("Message is too long.")
    return content, attachment_ids, gif


def _room_message_metadata(scope_type, scope_id):
    user_id = _current_user_id()
    read_state_row = _read_state_for_scope(user_id, scope_type, scope_id)
    last_read_at = (read_state_row or {}).get("last_read_at")
    unread, _ = _unread_count(scope_type, scope_id, user_id, last_read_at)
    return {
        "read_state": {
            "last_read_at": last_read_at,
            "last_read_message_id": (read_state_row or {}).get("last_read_message_id"),
        },
        "unread_count": unread,
    }


def _message_queries(scope_type, scope_id, before=None, after=None):
    field = "channel_id" if scope_type == "channel" else "thread_id"
    queries = [Query.equal(field, [scope_id])]
    if before:
        queries.append(Query.less_than("created_at", before))
        queries.append(Query.order_desc("created_at"))
    elif after:
        queries.append(Query.greater_than("created_at", after))
        queries.append(Query.order_asc("created_at"))
    else:
        queries.append(Query.order_desc("created_at"))
    return queries


def _message_is_after_cursor(row, cursor_row, cursor_id):
    if not row:
        return False
    if not cursor_row and not cursor_id:
        return True
    row_id = str(_row_id(row) or "")
    cursor_id = str(cursor_id or (_row_id(cursor_row) if cursor_row else "") or "")
    if row_id and cursor_id and row_id == cursor_id:
        return False
    if not cursor_row:
        return True
    row_ts = _message_timestamp(row)
    cursor_ts = _message_timestamp(cursor_row)
    if row_ts > cursor_ts:
        return True
    if row_ts < cursor_ts:
        return False
    return row_id > cursor_id


def _list_messages(scope_type, scope_id, before=None, after=None, after_message_id=None):
    if before:
        query_list = _message_queries(scope_type, scope_id, before=before)
        query_list.append(Query.limit(MESSAGE_PAGE_SIZE))
        rows = list_rows_safe(COLLECTIONS["chat_messages"], query_list).get("rows", [])
        visible = [row for row in rows if not row.get("deleted_at")]
        if scope_type == "thread":
            blocked = _blocked_user_ids(_current_user_id())
            visible = [row for row in visible if row.get("user_id") not in blocked]
        visible.sort(key=_message_timestamp)
        return visible

    if after_message_id or after:
        cursor_row = None
        if after_message_id:
            cursor_row = get_row_safe(COLLECTIONS["chat_messages"], after_message_id, allow_missing=True)
        cursor_id = _row_id(cursor_row) if cursor_row else after_message_id
        field = "channel_id" if scope_type == "channel" else "thread_id"
        queries = [Query.equal(field, [scope_id])]
        if after_message_id and cursor_row:
            queries.append(Query.greater_than_equal("created_at", cursor_row.get("created_at")))
        elif after:
            queries.append(Query.greater_than("created_at", after))
        queries.append(Query.order_asc("created_at"))
        queries.append(Query.limit(MESSAGE_PAGE_SIZE + 5))
        rows = list_rows_safe(COLLECTIONS["chat_messages"], queries).get("rows", [])
        visible = [row for row in rows if not row.get("deleted_at")]
        if scope_type == "thread":
            blocked = _blocked_user_ids(_current_user_id())
            visible = [row for row in visible if row.get("user_id") not in blocked]
        if after_message_id and cursor_row:
            visible = [
                row for row in visible
                if _message_is_after_cursor(row, cursor_row, cursor_id)
            ]
        visible.sort(key=_message_timestamp)
        return visible[:MESSAGE_PAGE_SIZE]

    query_list = _message_queries(scope_type, scope_id)
    query_list.append(Query.limit(MESSAGE_PAGE_SIZE))
    rows = list_rows_safe(COLLECTIONS["chat_messages"], query_list).get("rows", [])
    visible = [row for row in rows if not row.get("deleted_at")]
    if scope_type == "thread":
        blocked = _blocked_user_ids(_current_user_id())
        visible = [row for row in visible if row.get("user_id") not in blocked]
    visible.sort(key=_message_timestamp)
    return visible


def _upsert_discord_message(channel, message, emit_event=False, *, partial=False):
    return _upsert_discord_message_service(
        channel,
        message,
        emit_event=emit_event,
        partial=partial,
        dependencies=_discord_sync_dependencies(),
    )


def _soft_delete_discord_message(channel, discord_message_id, *, emit_event=False):
    return _soft_delete_discord_message_service(
        channel,
        discord_message_id,
        emit_event=emit_event,
        dependencies=_discord_sync_dependencies(),
    )


def _discord_avatar(author):
    return _discord_avatar_service(author, default_avatar=DEFAULT_AVATAR)


def _emit_chat_delete_audit(row, deleted_at):
    try:
        emit_audit_event(
            DiscordAuditEvent(
                channel="chat_deletes",
                title="Chat Message Deleted",
                actor=format_actor(current_user),
                target=str(_row_id(row) or ""),
                metadata={
                    "message_id": _row_id(row),
                    "source": row.get("source") or "appwrite",
                    "channel_id": row.get("channel_id"),
                    "thread_id": row.get("thread_id"),
                    "author_user_id": row.get("user_id"),
                    "author_name": row.get("author_name"),
                    "author_username": row.get("author_username"),
                    "created_at": row.get("created_at"),
                    "deleted_at": deleted_at,
                    "discord_message_id": row.get("discord_message_id"),
                    "discord_webhook_id": row.get("discord_webhook_id"),
                    "content": row.get("content") or "",
                },
                color="red",
            )
        )
    except Exception:
        logger.exception("Failed to emit chat delete audit event")


def _reconcile_discord_deletes(channel, discord_messages, *, emit_events=False):
    return _reconcile_discord_deletes_service(
        channel,
        discord_messages,
        emit_events=emit_events,
        dependencies=_discord_sync_dependencies(),
    )


def _sync_discord_channel(channel, emit_events=False, emit_delete_events=None):
    return _sync_discord_channel_service(
        channel,
        emit_events=emit_events,
        emit_delete_events=emit_delete_events,
        dependencies=_discord_sync_dependencies(),
    )


def sync_discord_channels(emit_events=True, emit_delete_events=None):
    return _sync_discord_channels_service(
        emit_events=emit_events,
        emit_delete_events=emit_delete_events,
        dependencies=_discord_sync_dependencies(),
    )


def ingest_discord_gateway_message(message, *, event_type="create"):
    return _ingest_discord_gateway_message_service(
        message,
        event_type=event_type,
        dependencies=_discord_sync_dependencies(),
    )


def delete_discord_gateway_message(discord_channel_id, discord_message_id):
    return _delete_discord_gateway_message_service(
        discord_channel_id,
        discord_message_id,
        dependencies=_discord_sync_dependencies(),
    )


def delete_discord_gateway_messages(discord_channel_id, discord_message_ids):
    return _delete_discord_gateway_messages_service(
        discord_channel_id,
        discord_message_ids,
        dependencies=_discord_sync_dependencies(),
    )


def _can_sync_discord_channel(channel):
    return _can_sync_discord_channel_service(channel)


def _discord_channel_for_discord_id(discord_channel_id):
    return _discord_channel_for_discord_id_service(
        discord_channel_id,
        dependencies=_discord_sync_dependencies(),
    )


def _discord_ingest_secret():
    configured = runtime_environment_config()
    for value in (
        configured.discord_chat_ingest_secret,
        configured.discord_chat_sync_secret,
        configured.discord_bridge_secret,
    ):
        if value:
            return value.strip()
    return ""


def _discord_ingest_token():
    auth_header = request.headers.get("Authorization", "")
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()
    return (request.headers.get("X-Discord-Bridge-Secret") or "").strip()


def _valid_discord_ingest_request():
    expected = _discord_ingest_secret()
    provided = _discord_ingest_token()
    return bool(expected and provided and secrets.compare_digest(provided, expected))


def _prune_discord_messages(channel_id):
    return _prune_discord_messages_service(
        channel_id,
        dependencies=_discord_sync_dependencies(),
    )


def _blocked_user_ids(user_id):
    return _blocked_user_ids_service(
        user_id,
        list_rows_fn=list_rows_all,
        query_cls=Query,
        blocks_collection=COLLECTIONS["chat_blocks"],
        appwrite_exception=AppwriteException,
    )


def _is_blocked_between(user_a, user_b):
    return _is_blocked_between_service(
        user_a,
        user_b,
        first_row_fn=first_row,
        query_cls=Query,
        blocks_collection=COLLECTIONS["chat_blocks"],
        appwrite_exception=AppwriteException,
        error_logger=logger,
    )


def _thread_key(user_a, user_b):
    return _thread_key_service(user_a, user_b)


def _get_or_create_thread_between(user_a, user_b):
    return _get_or_create_thread_between_service(
        user_a,
        user_b,
        thread_key_fn=_thread_key,
        first_row_fn=first_row,
        query_cls=Query,
        threads_collection=COLLECTIONS["chat_dm_threads"],
        format_datetime_fn=format_datetime,
        now_fn=_now,
        create_row_fn=create_row_safe,
        id_unique_fn=ID.unique,
    )


def initialize_new_user_discord_read_states(user_id):
    return _initialize_new_user_discord_read_states_service(
        user_id,
        default_channels_fn=_default_channels,
        list_rows_all_fn=list_rows_all,
        query_cls=Query,
        channels_collection=COLLECTIONS["chat_channels"],
        row_id_fn=_row_id,
        latest_visible_message_fn=_latest_visible_message,
        persist_read_state_fn=_persist_read_state,
        appwrite_exception=AppwriteException,
        error_logger=logger,
    )


def create_welcome_dm_for_user(user_id):
    return _create_welcome_dm_for_user_service(
        user_id,
        welcome_sender_id=WELCOME_DM_SENDER_ID,
        welcome_text=WELCOME_DM_TEXT,
        first_row_fn=first_row,
        query_cls=Query,
        messages_collection=COLLECTIONS["chat_messages"],
        get_row_fn=get_row_safe,
        users_collection=COLLECTIONS["users"],
        get_or_create_thread_between_fn=_get_or_create_thread_between,
        create_row_fn=create_row_safe,
        id_unique_fn=ID.unique,
        update_row_fn=update_row_safe,
        threads_collection=COLLECTIONS["chat_dm_threads"],
        row_id_fn=_row_id,
        now_fn=_now,
        format_datetime_fn=format_datetime,
        render_markdown_fn=render_markdown,
        emit_chat_event_fn=emit_chat_event,
        thread_participant_ids_fn=_thread_participant_ids,
        appwrite_exception=AppwriteException,
        error_logger=logger,
    )


def _get_or_create_thread(other_user_id):
    return _get_or_create_thread_service(
        other_user_id,
        current_user_id_fn=_current_user_id,
        get_row_fn=get_row_safe,
        users_collection=COLLECTIONS["users"],
        get_or_create_thread_between_fn=_get_or_create_thread_between,
    )


def _thread_for_user(thread_id):
    return _thread_for_user_service(
        thread_id,
        get_row_fn=get_row_safe,
        threads_collection=COLLECTIONS["chat_dm_threads"],
        current_user_id_fn=_current_user_id,
    )


def _other_participant(thread):
    return _other_participant_service(
        thread,
        current_user_id_fn=_current_user_id,
        get_row_fn=get_row_safe,
        users_collection=COLLECTIONS["users"],
    )


def _thread_participant_ids(thread):
    return _thread_participant_ids_service(thread)


def _read_state_dependencies():
    return _ChatReadStateDependencies(
        collections=COLLECTIONS,
        appwrite_exception=AppwriteException,
        query_cls=Query,
        id_unique_fn=ID.unique,
        row_id_fn=_row_id,
        now_fn=_now,
        format_datetime_fn=format_datetime,
        current_user_id_fn=_current_user_id,
        get_row_fn=get_row_safe,
        first_row_fn=first_row,
        create_row_fn=create_row_safe,
        update_row_fn=update_row_safe,
        delete_row_fn=delete_row_safe,
        list_rows_fn=list_rows_safe,
        read_key_fn=_read_key,
        message_timestamp_fn=_message_timestamp,
        message_scope_field_fn=_message_scope_field,
        message_in_scope_fn=_message_in_scope,
        message_visible_for_user_fn=_message_visible_for_user,
        message_can_be_unread_target_fn=_message_can_be_unread_target,
        blocked_user_ids_fn=_blocked_user_ids,
        thread_for_user_fn=_thread_for_user,
        can_access_channel_fn=_can_access_channel,
        latest_visible_message_fn=_latest_visible_message,
        persist_read_state_fn=_persist_read_state,
        read_state_for_scope_fn=_read_state_for_scope,
        latest_unread_target_fn=_latest_unread_target,
        previous_visible_message_fn=_previous_visible_message,
        clear_read_state_fn=_clear_read_state,
        error_logger=logger,
        summary_scan_limit=CHAT_SUMMARY_SCAN_LIMIT,
        unread_cap=CHAT_UNREAD_CAP,
    )


def _read_key(user_id, scope_type, scope_id):
    return _read_key_service(user_id, scope_type, scope_id)


def _read_state_for_scope(user_id, scope_type, scope_id):
    return _read_state_for_scope_service(
        user_id,
        scope_type,
        scope_id,
        dependencies=_read_state_dependencies(),
    )


def _latest_visible_message(scope_type, scope_id):
    return _latest_visible_message_service(
        scope_type,
        scope_id,
        dependencies=_read_state_dependencies(),
    )


def _message_scope_field(scope_type):
    return _message_scope_field_service(scope_type)


def _message_in_scope(row, scope_type, scope_id):
    return _message_in_scope_service(
        row,
        scope_type,
        scope_id,
        message_scope_field_fn=_message_scope_field,
    )


def _message_for_current_user(message_id):
    return _message_for_current_user_service(
        message_id,
        dependencies=_read_state_dependencies(),
    )


def _message_visible_for_user(row, scope_type, blocked_user_ids=None):
    return _message_visible_for_user_service(row, scope_type, blocked_user_ids)


def _message_can_be_unread_target(row, scope_type, user_id, blocked_user_ids=None):
    return _message_can_be_unread_target_service(
        row,
        scope_type,
        user_id,
        blocked_user_ids,
        message_visible_for_user_fn=_message_visible_for_user,
    )


def _persist_read_state(user_id, scope_type, scope_id, latest, *, fallback_to_now=True):
    return _persist_read_state_service(
        user_id,
        scope_type,
        scope_id,
        latest,
        fallback_to_now=fallback_to_now,
        dependencies=_read_state_dependencies(),
    )


def _mark_read(scope_type, scope_id, message_id=None):
    return _mark_read_service(
        scope_type,
        scope_id,
        message_id=message_id,
        dependencies=_read_state_dependencies(),
    )


def _latest_unread_target(scope_type, scope_id, user_id, blocked_user_ids):
    return _latest_unread_target_service(
        scope_type,
        scope_id,
        user_id,
        blocked_user_ids,
        dependencies=_read_state_dependencies(),
    )


def _previous_visible_message(scope_type, scope_id, target, blocked_user_ids):
    return _previous_visible_message_service(
        scope_type,
        scope_id,
        target,
        blocked_user_ids,
        dependencies=_read_state_dependencies(),
    )


def _clear_read_state(user_id, scope_type, scope_id):
    return _clear_read_state_service(
        user_id,
        scope_type,
        scope_id,
        dependencies=_read_state_dependencies(),
    )


def _mark_unread(scope_type, scope_id, message_id=None):
    return _mark_unread_service(
        scope_type,
        scope_id,
        message_id=message_id,
        dependencies=_read_state_dependencies(),
    )


def _existing_visible_channels_for_summary():
    return _existing_visible_channels_for_summary_service(
        default_channels_fn=_default_channels,
        list_rows_all_fn=list_rows_all,
        channels_collection=COLLECTIONS["chat_channels"],
        query_cls=Query,
        can_access_channel_fn=_can_access_channel,
        appwrite_exception=AppwriteException,
        error_logger=logger,
    )


def _unread_count(scope_type, scope_id, user_id, last_read_at):
    return _unread_count_service(
        scope_type,
        scope_id,
        user_id,
        last_read_at,
        dependencies=_read_state_dependencies(),
    )


@chat_api_bp.route("/api/universities")
@login_required
def universities():
    query = request.args.get("q") or ""
    return jsonify({"results": search_universities(query)})


@chat_api_bp.route("/api/chat/discord/messages", methods=["POST"])
def discord_message_ingest():
    if not _valid_discord_ingest_request():
        return jsonify({"error": "Discord chat ingest is unavailable."}), 403
    raw_payload = request.get_json(silent=True) or {}
    payload = raw_payload if isinstance(raw_payload, dict) else {}
    message = payload.get("message") if isinstance(payload.get("message"), dict) else payload
    discord_channel_id = (
        payload.get("discord_channel_id")
        or message.get("channel_id")
        or message.get("channel")
    )
    channel = _discord_channel_for_discord_id(discord_channel_id)
    if not _can_sync_discord_channel(channel):
        return jsonify({"error": "Discord channel is not mapped to /chat."}), 404
    row, created = _upsert_discord_message(channel, message, emit_event=True)
    if not row:
        return jsonify({"error": "Unable to ingest Discord message."}), 502
    return jsonify({
        "ok": True,
        "created": bool(created),
        "message_id": _row_id(row),
        "channel_id": _row_id(channel),
    })


@chat_api_bp.route("/api/chat/events/stream")
@login_required
def chat_events_stream():
    since = (request.args.get("since") or "").strip() or None
    after_id = (request.args.get("after_id") or "").strip() or None

    def generate():
        cursor_since = since or format_datetime(_now())
        cursor_after_id = after_id
        listener = threading.Condition()
        with _chat_event_listener_lock:
            _chat_event_listeners.append(listener)
        last_keepalive = time.monotonic()
        try:
            while True:
                events = _list_chat_events_after(cursor_since, cursor_after_id)
                for event in events:
                    payload = json.dumps(_serialize_chat_event(event), separators=(",", ":"))
                    yield f"data: {payload}\n\n"
                    cursor_since = event.get("created_at") or cursor_since
                    cursor_after_id = _row_id(event)
                    last_keepalive = time.monotonic()
                scan_cursor = getattr(events, "scan_cursor", None)
                if scan_cursor:
                    cursor_since, cursor_after_id = scan_cursor
                now = time.monotonic()
                if now - last_keepalive >= CHAT_EVENTS_KEEPALIVE_SECONDS:
                    yield ": keepalive\n\n"
                    last_keepalive = now
                    wait_seconds = CHAT_EVENTS_POLL_SECONDS
                else:
                    wait_seconds = min(
                        CHAT_EVENTS_KEEPALIVE_SECONDS - (now - last_keepalive),
                        CHAT_EVENTS_POLL_SECONDS,
                    )
                with listener:
                    listener.wait(timeout=max(wait_seconds, CHAT_EVENTS_POLL_SECONDS))
        finally:
            with _chat_event_listener_lock:
                try:
                    _chat_event_listeners.remove(listener)
                except ValueError:
                    pass

    headers = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    }
    return Response(stream_with_context(generate()), mimetype="text/event-stream", headers=headers)


@chat_api_bp.route("/api/presence/heartbeat", methods=["POST"])
@login_required
def presence_heartbeat():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Presence heartbeat body must be a JSON object."}), 400
    scopes = data.get("scopes")
    if scopes is None:
        scopes = [{
            "scope_type": data.get("scope_type"),
            "scope_id": data.get("scope_id") or "global",
        }]
    if not isinstance(scopes, list) or not scopes or len(scopes) > 8:
        return jsonify({"error": "Presence scopes must contain between 1 and 8 entries."}), 400
    if not all(isinstance(scope, dict) for scope in scopes):
        return jsonify({"error": "Each presence scope must be an object."}), 400
    try:
        rows = [
            _upsert_presence(
                scope.get("scope_type"),
                scope.get("scope_id") or "global",
                data.get("tab_id"),
            )
            for scope in scopes
        ]
    except PermissionError:
        return jsonify({"error": "Presence scope unavailable."}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except AppwriteException:
        logger.exception("Failed to update presence heartbeat")
        return jsonify({"error": "Unable to update presence."}), 500
    presences = [{
        "scope_type": row.get("scope_type"),
        "scope_id": row.get("scope_id"),
        "last_seen_at": row.get("last_seen_at"),
    } for row in rows]
    return jsonify({
        "status": "ok",
        "presence": presences[0],
        "presences": presences,
    })


@chat_api_bp.route("/api/presence/online")
@login_required
def presence_online():
    return jsonify({"users": _presence_online_users()})


@chat_api_bp.route("/api/presence/statuses", methods=["POST"])
@login_required
def presence_statuses():
    data = request.get_json(silent=True) or {}
    user_ids = data.get("user_ids") if isinstance(data.get("user_ids"), list) else []
    return jsonify({"statuses": _presence_statuses_for_users(user_ids)})


@chat_api_bp.route("/api/presence/room", methods=["POST"])
@login_required
def presence_room():
    data = request.get_json(silent=True) or {}
    scope_type = str(data.get("scope_type") or "").strip()
    scope_id = str(data.get("scope_id") or "").strip()
    if not scope_id:
        return jsonify({"error": "Missing presence scope."}), 400

    if scope_type == "channel":
        channel = get_row_safe(COLLECTIONS["chat_channels"], scope_id, allow_missing=True)
        if not _can_access_channel(channel):
            return jsonify({"error": "Presence scope unavailable."}), 404
        typing_scope = "typing_channel"
    elif scope_type == "thread":
        thread = _thread_for_user(scope_id)
        if not thread:
            return jsonify({"error": "Presence scope unavailable."}), 404
        typing_scope = "typing_thread"
    else:
        return jsonify({"error": "Unsupported presence scope."}), 400

    online_users = _online_users_for_channel(channel) if scope_type == "channel" else _fresh_chat_room_presence("chat", scope_id)
    return jsonify({
        "active_users": online_users,
        "online_users": online_users,
        "typing_users": _fresh_typing_room_presence(typing_scope, scope_id),
    })


@chat_api_bp.route("/api/chat/bootstrap")
@login_required
def bootstrap():
    channels = _default_channels()
    university = _ensure_university_request()
    if university.get("channel"):
        channels.append(university["channel"])
    sync_chat_presence_labels_for_user(_current_user_id())
    dm_threads = _list_threads()
    entitlements = request_entitlements(current_user)
    return jsonify(
        _assemble_bootstrap_payload_service(
            current_user=current_user,
            channels=channels,
            university=university,
            dm_threads=dm_threads,
            entitlements=entitlements,
            current_user_payload_fn=_current_user_payload,
            settings_payload_fn=_settings_payload,
            channel_payload_fn=_channel_payload,
            sync_environment_config_fn=runtime_environment_config,
            attachments_enabled_fn=_appwrite_chat_attachments_enabled,
            max_attachments_per_message=MAX_ATTACHMENTS_PER_MESSAGE,
            giphy_available_fn=giphy_available,
            giphy_api_key_fn=giphy_api_key,
        )
    )


@chat_api_bp.route("/api/chat/summary")
@login_required
def chat_summary():
    user_id = _current_user_id()
    return jsonify(
        _assemble_chat_summary_payload_service(
            user_id,
            _existing_visible_channels_for_summary(),
            threads_fn=_threads_for_current_user,
            row_id_fn=_row_id,
            read_state_for_scope_fn=_read_state_for_scope,
            unread_count_fn=_unread_count,
            unread_cap=CHAT_UNREAD_CAP,
        )
    )


@chat_api_bp.route("/api/chat/read", methods=["POST"])
@login_required
def mark_chat_read():
    data = request.get_json(silent=True) or {}
    scope_type = str(data.get("scope_type") or "").strip()
    scope_id = str(data.get("scope_id") or "").strip()
    message_id = str(data.get("message_id") or "").strip() or None
    if scope_type == "channel":
        channel = get_row_safe(COLLECTIONS["chat_channels"], scope_id, allow_missing=True)
        if not _can_access_channel(channel):
            return jsonify({"error": "Channel unavailable."}), 404
    elif scope_type == "thread":
        if not _thread_for_user(scope_id):
            return jsonify({"error": "Thread unavailable."}), 404
    else:
        return jsonify({"error": "Unsupported read scope."}), 400
    row = _mark_read(scope_type, scope_id, message_id=message_id)
    return jsonify({"status": "ok", "read_state": row or {}})


@chat_api_bp.route("/api/chat/unread", methods=["POST"])
@login_required
def mark_chat_unread():
    data = request.get_json(silent=True) or {}
    scope_type = str(data.get("scope_type") or "").strip()
    scope_id = str(data.get("scope_id") or "").strip()
    message_id = str(data.get("message_id") or "").strip() or None
    if scope_type == "channel":
        channel = get_row_safe(COLLECTIONS["chat_channels"], scope_id, allow_missing=True)
        if not _can_access_channel(channel):
            return jsonify({"error": "Channel unavailable."}), 404
    elif scope_type == "thread":
        if not _thread_for_user(scope_id):
            return jsonify({"error": "Thread unavailable."}), 404
    else:
        return jsonify({"error": "Unsupported unread scope."}), 400
    row = _mark_unread(scope_type, scope_id, message_id=message_id)
    return jsonify({"status": "ok", "read_state": row or {}})


def _threads_for_current_user():
    return _list_threads_for_current_user_service(
        _current_user_id(),
        list_rows_all_fn=list_rows_all,
        query_cls=Query,
        threads_collection=COLLECTIONS["chat_dm_threads"],
        appwrite_exception=AppwriteException,
        error_logger=logger,
    )


def _thread_payload(thread):
    return _thread_payload_service(
        thread,
        other_participant_fn=_other_participant,
        public_user_fn=_public_user,
        presence_statuses_for_users_fn=_presence_statuses_for_users,
        current_user_id_fn=_current_user_id,
        row_id_fn=_row_id,
        fresh_chat_room_presence_fn=_fresh_chat_room_presence,
        is_blocked_between_fn=_is_blocked_between,
        presence_scope_fn=_presence_scope,
        presence_read_permissions_for_thread_fn=_presence_read_permissions_for_thread,
    )


def _list_threads():
    return _list_thread_payloads_service(
        _threads_for_current_user(),
        thread_payload_fn=_thread_payload,
    )


def _attachment_scope_access(scope_type, scope_id):
    return _attachment_scope_access_service(
        scope_type,
        scope_id,
        get_row_fn=get_row_safe,
        collections=COLLECTIONS,
        can_access_channel_fn=_can_access_channel,
        thread_for_user_fn=_thread_for_user,
    )


def _can_access_attachment(row):
    return _can_access_attachment_service(
        row,
        current_user_id=_current_user_id(),
        attachment_scope_access_fn=_attachment_scope_access,
    )


@chat_api_bp.route("/api/chat/attachments", methods=["POST"])
@login_required
def upload_chat_attachment():
    uploaded_file = request.files.get("file")
    scope_type = str(request.form.get("scope_type") or "").strip()
    scope_id = str(request.form.get("scope_id") or "").strip()
    if not uploaded_file or not uploaded_file.filename:
        return jsonify({"error": "Choose a file to attach."}), 400
    if not _attachment_scope_access(scope_type, scope_id):
        return jsonify({"error": "Conversation unavailable."}), 404
    try:
        row = _create_chat_attachment_service(
            user_id=_current_user_id(),
            scope_type=scope_type,
            scope_id=scope_id,
            uploaded_file=uploaded_file,
            entitlements=request_entitlements(current_user),
            original_size=request.form.get("original_size_bytes"),
            upload_encoding=request.form.get("content_encoding") or "identity",
            create_attachment_fn=create_attachment,
        )
    except EntitlementLimitError as exc:
        return jsonify(exc.payload()), 413
    except (AttachmentError, ValueError) as exc:
        return jsonify({"error": str(exc), "code": "invalid_attachment"}), 400
    except AppwriteException:
        logger.exception("Failed to upload chat attachment")
        return jsonify({"error": "Unable to upload this attachment right now."}), 503
    return jsonify({"attachment": serialize_attachment(row)}), 201


@chat_api_bp.route("/api/chat/attachments/<attachment_id>", methods=["DELETE"])
@login_required
def cancel_chat_attachment(attachment_id):
    try:
        _cancel_pending_attachment_service(
            attachment_id,
            get_attachment_fn=get_attachment,
            current_user_id=_current_user_id(),
            delete_attachment_fn=delete_attachment,
        )
    except _PendingAttachmentNotFoundError:
        return jsonify({"error": "Pending attachment not found."}), 404
    except _AttachmentOwnershipError:
        return jsonify({"error": "You cannot cancel this attachment."}), 403
    return jsonify({"status": "ok"})


@chat_api_bp.route("/api/chat/attachments/<attachment_id>/preview")
@login_required
def preview_chat_attachment(attachment_id):
    try:
        row, data = _read_attachment_service(
            attachment_id,
            preview=True,
            get_attachment_fn=get_attachment,
            can_access_attachment_fn=_can_access_attachment,
            attachment_bytes_fn=attachment_bytes,
        )
    except _AttachmentUnavailableError:
        return jsonify({"error": "Preview unavailable."}), 404
    use_preview = row.get("kind") == "pdf"
    response = send_file(
        io.BytesIO(data),
        mimetype="image/webp" if use_preview else row.get("mime_type"),
        as_attachment=False,
        max_age=3600,
        conditional=True,
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
    return response


@chat_api_bp.route("/api/chat/attachments/<attachment_id>/download")
@login_required
def download_chat_attachment(attachment_id):
    try:
        row, data = _read_attachment_service(
            attachment_id,
            preview=False,
            get_attachment_fn=get_attachment,
            can_access_attachment_fn=_can_access_attachment,
            attachment_bytes_fn=attachment_bytes,
        )
    except _AttachmentUnavailableError:
        return jsonify({"error": "Attachment unavailable."}), 404
    response = send_file(
        io.BytesIO(data),
        mimetype=row.get("mime_type") or "application/octet-stream",
        as_attachment=True,
        download_name=row.get("original_filename") or "attachment",
        max_age=0,
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
    return response


@chat_api_bp.route("/api/chat/channels/<channel_id>/messages")
@login_required
def channel_messages(channel_id):
    channel = get_row_safe(COLLECTIONS["chat_channels"], channel_id, allow_missing=True)
    if not _can_access_channel(channel):
        return jsonify({"error": "Channel unavailable."}), 404
    after = request.args.get("after")
    after_message_id = request.args.get("after_message_id")
    rows, has_more = _list_room_messages_service(
        "channel",
        channel_id,
        request.args.get("before"),
        after,
        after_message_id,
        list_messages_fn=_list_messages,
        page_size=MESSAGE_PAGE_SIZE,
        history_limited=channel.get("kind") == "discord",
    )
    return jsonify({
        "messages": _serialize_messages(rows),
        "has_more": has_more,
        "channel": _channel_payload(channel),
        **_room_message_metadata("channel", channel_id),
    })


@chat_api_bp.route("/api/chat/channels/<channel_id>/messages", methods=["POST"])
@login_required
def send_channel_message(channel_id):
    channel = get_row_safe(COLLECTIONS["chat_channels"], channel_id, allow_missing=True)
    if not _can_access_channel(channel):
        return jsonify({"error": "Channel unavailable."}), 404
    if channel.get("read_only"):
        return jsonify({"error": "This channel is read-only."}), 403
    try:
        row, _created = _send_channel_message_service(
            channel_id,
            channel,
            dependencies=_chat_delivery_dependencies(),
        )
    except (AttachmentError, GiphyError) as exc:
        return jsonify({"error": str(exc)}), 400
    except _DiscordDeliveryError:
        return jsonify({"error": "Unable to send to Discord right now."}), 502
    except _AttachmentBindingError as exc:
        return jsonify({"error": str(exc) or "Unable to attach files."}), 400
    except _MessagePersistenceError:
        return jsonify({"error": "Unable to save message."}), 500
    return jsonify({"message": _serialize_message(row)}), 201


@chat_api_bp.route("/api/chat/messages/<message_id>", methods=["GET", "DELETE"])
@login_required
def delete_message(message_id):
    if request.method == "GET":
        try:
            message = _get_message_for_current_user_service(
                message_id,
                message_for_current_user_fn=_message_for_current_user,
                serialize_message_fn=_serialize_message,
            )
        except _MessageNotFoundError:
            return jsonify({"error": "Message not found."}), 404
        return jsonify({"message": message})
    try:
        _delete_chat_message_service(
            message_id,
            dependencies=_chat_delivery_dependencies(),
        )
    except _MessageNotFoundError:
        return jsonify({"error": "Message not found."}), 404
    except _MessageOwnershipError:
        return jsonify({"error": "You can only delete your own messages."}), 403
    except _MessageExpiredError:
        return jsonify({"error": "Messages can only be deleted within 5 minutes of sending."}), 403
    except _DiscordDeliveryError:
        return jsonify({"error": "Unable to delete the Discord message right now."}), 502
    except _MessagePersistenceError:
        return jsonify({"error": "Unable to delete message."}), 500
    return jsonify({"status": "ok"})


@chat_api_bp.route("/api/chat/dm/search")
@login_required
def dm_search():
    query = (request.args.get("q") or "").strip().lower()
    return jsonify({
        "results": _search_direct_message_users_service(
            query,
            current_user_id=_current_user_id(),
            list_rows_all_fn=list_rows_all,
            query_cls=Query,
            users_collection=COLLECTIONS["users"],
            row_id_fn=_row_id,
            public_user_fn=_public_user,
            appwrite_exception=AppwriteException,
            error_logger=logger,
        )
    })


@chat_api_bp.route("/api/chat/dm/threads", methods=["GET", "POST"])
@login_required
def dm_threads():
    if request.method == "GET":
        return jsonify({"threads": _list_threads()})
    other_user_id = str((request.get_json(silent=True) or {}).get("user_id") or "").strip()
    try:
        thread = _create_direct_thread_service(
            other_user_id,
            get_or_create_thread_fn=_get_or_create_thread,
            current_user_id=_current_user_id(),
            row_id_fn=_row_id,
            thread_participant_ids_fn=_thread_participant_ids,
        )
    except (ValueError, AppwriteException) as exc:
        return jsonify({"error": str(exc) or "Unable to create thread."}), 400
    emit_chat_event(
        "thread",
        _row_id(thread),
        "thread_updated",
        thread_id=_row_id(thread),
        actor_id=_current_user_id(),
        readable_user_ids=_thread_participant_ids(thread),
    )
    return jsonify({"thread": _thread_payload(thread)}), 201


@chat_api_bp.route("/api/chat/dm/threads/<thread_id>")
@login_required
def dm_thread(thread_id):
    thread = _thread_for_user(thread_id)
    if not thread:
        return jsonify({"error": "Thread unavailable."}), 404
    payload = _thread_payload(thread)
    if not payload:
        return jsonify({"error": "Thread unavailable."}), 404
    return jsonify({"thread": payload})


@chat_api_bp.route("/api/chat/dm/threads/<thread_id>/messages", methods=["GET", "POST"])
@login_required
def dm_thread_messages(thread_id):
    thread = _thread_for_user(thread_id)
    if not thread:
        return jsonify({"error": "Thread unavailable."}), 404
    other = _public_user(_other_participant(thread))
    if request.method == "GET":
        after = request.args.get("after")
        after_message_id = request.args.get("after_message_id")
        rows, has_more = _list_room_messages_service(
            "thread",
            thread_id,
            request.args.get("before"),
            after,
            after_message_id,
            list_messages_fn=_list_messages,
            page_size=MESSAGE_PAGE_SIZE,
        )
        thread_payload = _thread_payload(thread) or {}
        return jsonify({
            "messages": _serialize_messages(rows),
            "has_more": has_more,
            "thread": {
                "id": thread_id,
                "other_user": thread_payload.get("other_user", other),
                "blocked": thread_payload.get("blocked", _is_blocked_between(_current_user_id(), other["id"]) if other else False),
                "active_count": thread_payload.get("active_count", 0),
                "presence_status": thread_payload.get("presence_status", "offline"),
                "presence_scope": thread_payload.get("presence_scope") or _presence_scope("thread", thread_id),
                "presence_read_permissions": thread_payload.get("presence_read_permissions") or _presence_read_permissions_for_thread(thread),
                "presence_profile_resolve_allowed": True,
            },
            **_room_message_metadata("thread", thread_id),
        })

    if not other:
        return jsonify({"error": "Recipient unavailable."}), 404
    try:
        row = _send_direct_message_service(
            thread_id,
            thread,
            other,
            dependencies=_chat_delivery_dependencies(),
        )
    except (AttachmentError, GiphyError) as exc:
        return jsonify({"error": str(exc)}), 400
    except _DirectMessageBlockedError:
        return jsonify({"error": "This conversation is blocked."}), 403
    except _DirectMessagePersistenceError:
        return jsonify({"error": "Unable to send message."}), 500
    return jsonify({"message": _serialize_message(row)}), 201


@chat_api_bp.route("/api/chat/blocks/<user_id>", methods=["POST", "DELETE"])
@login_required
def blocks(user_id):
    target_id = str(user_id or "").strip()
    if target_id == _current_user_id():
        return jsonify({"error": "You cannot block yourself."}), 400
    try:
        blocked = _update_block_service(
            target_id,
            method=request.method,
            dependencies=_chat_delivery_dependencies(),
        )
    except AppwriteException:
        if request.method == "DELETE":
            return jsonify({"error": "Unable to unblock user."}), 500
        return jsonify({"error": "Unable to block user."}), 500
    return jsonify({"status": "ok", "blocked": blocked})


@chat_api_bp.route("/api/chat/presence/users", methods=["POST"])
@login_required
def presence_users():
    data = request.get_json(silent=True) or {}
    scope_type = str(data.get("scope_type") or "").strip()
    scope_id = str(data.get("scope_id") or "").strip()
    requested_ids = []
    for value in data.get("user_ids") or []:
        user_id = str(value or "").strip()
        if user_id and user_id not in requested_ids:
            requested_ids.append(user_id)
        if len(requested_ids) >= 80:
            break

    allowed_ids = None
    if scope_type == "channel":
        channel = get_row_safe(COLLECTIONS["chat_channels"], scope_id, allow_missing=True)
        if not _can_access_channel(channel):
            return jsonify({"error": "Presence scope unavailable."}), 404
    elif scope_type == "thread":
        thread = _thread_for_user(scope_id)
        if not thread:
            return jsonify({"error": "Presence scope unavailable."}), 404
        allowed_ids = set(_thread_participant_ids(thread))
    else:
        return jsonify({"error": "Unsupported presence scope."}), 400

    visible_ids = [
        user_id for user_id in requested_ids
        if allowed_ids is None or user_id in allowed_ids
    ]
    users_by_id = _load_users_by_id(visible_ids)
    users = []
    for user_id in visible_ids:
        user = users_by_id.get(user_id)
        public_user = _public_user(user)
        if public_user:
            users.append(public_user)
    return jsonify({"users": users})


@chat_api_bp.route("/api/chat/presence", methods=["POST"])
@login_required
def presence():
    # Compatibility endpoint for older clients. Live chat presence now uses
    # the local /api/presence/* endpoints.
    return jsonify({
        "status": "ok",
        "users": _presence_online_users(),
        "dm_statuses": {},
    })


def _sync_discord_channels_for_background(*args, **kwargs):
    return sync_discord_channels(*args, **kwargs)


def _ingest_discord_gateway_message_for_background(*args, **kwargs):
    return ingest_discord_gateway_message(*args, **kwargs)


def _delete_discord_gateway_message_for_background(*args, **kwargs):
    return delete_discord_gateway_message(*args, **kwargs)


def _delete_discord_gateway_messages_for_background(*args, **kwargs):
    return delete_discord_gateway_messages(*args, **kwargs)


register_discord_chat_handlers(
    sync_discord_channels=_sync_discord_channels_for_background,
    ingest_discord_gateway_message=_ingest_discord_gateway_message_for_background,
    delete_discord_gateway_message=_delete_discord_gateway_message_for_background,
    delete_discord_gateway_messages=_delete_discord_gateway_messages_for_background,
)
