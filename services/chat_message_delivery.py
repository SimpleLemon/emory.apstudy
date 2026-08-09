"""Non-HTTP orchestration for chat delivery routes.

The chat blueprint owns request parsing, authentication, CSRF, response
construction, and status mapping.  This module owns the ordered persistence
and side-effect workflows behind those adapters.  Every external operation is
passed in so the blueprint's established patch seams remain usable.
"""

import json
import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping


DISCORD_SMALL_ATTACHMENT_LIMIT = 10 * 1024 * 1024


class DiscordDeliveryError(RuntimeError):
    """A Discord webhook operation failed and should map to HTTP 502."""


class MessagePersistenceError(RuntimeError):
    """A channel message could not be persisted or finalized."""


class AttachmentBindingError(RuntimeError):
    """A message attachment could not be bound after message creation."""


class DirectMessagePersistenceError(RuntimeError):
    """A DM message failed after its media payload was accepted."""


class MessageNotFoundError(LookupError):
    pass


class MessageOwnershipError(PermissionError):
    pass


class MessageExpiredError(PermissionError):
    pass


class DirectMessageBlockedError(PermissionError):
    pass


class PendingAttachmentNotFoundError(LookupError):
    pass


class AttachmentOwnershipError(PermissionError):
    pass


class AttachmentUnavailableError(LookupError):
    pass


@dataclass
class ChatMessageDeliveryDependencies:
    """Callbacks used by delivery workflows.

    The fields intentionally mirror the blueprint-level symbols that older
    tests and integrations patch.  The service never imports the blueprint.
    """

    collections: Mapping[str, str]
    appwrite_exception: type
    attachment_error: type
    current_user_fn: Callable[[], Any]
    current_user_id_fn: Callable[[], str]
    message_media_payload_fn: Callable[[], tuple[str, list[str], Any]]
    previews_for_content_fn: Callable[[str], list]
    now_fn: Callable[[], Any]
    format_datetime_fn: Callable[[Any], str]
    render_markdown_fn: Callable[[str], str]
    row_id_fn: Callable[[Mapping[str, Any]], str]
    get_row_fn: Callable[..., Any]
    create_row_fn: Callable[..., Any]
    insert_row_ignore_fn: Callable[..., Any]
    update_row_fn: Callable[..., Any]
    delete_row_fn: Callable[..., Any]
    id_unique_fn: Callable[[], str]
    get_attachment_fn: Callable[[str], Any]
    attachment_bytes_fn: Callable[..., bytes | None]
    bind_pending_fn: Callable[..., Any]
    delete_message_attachments_fn: Callable[[str], Any]
    emit_chat_event_fn: Callable[..., Any]
    serialize_message_fn: Callable[[Mapping[str, Any]], Mapping[str, Any]]
    discord_external_id_fn: Callable[[Mapping[str, Any], str], str]
    discord_row_id_fn: Callable[[Mapping[str, Any], str], str]
    find_discord_message_row_fn: Callable[[str, str], Any]
    prune_discord_fn: Callable[[str], Any]
    execute_chat_webhook_fn: Callable[..., tuple[Mapping[str, Any], Mapping[str, Any]]]
    delete_webhook_message_fn: Callable[[str | None, str], Any]
    notification_fn: Callable[..., Any]
    invite_activation_fn: Callable[[str, str], Any]
    first_row_fn: Callable[..., Any]
    query_cls: Any
    users_collection: str
    thread_participant_ids_fn: Callable[[Mapping[str, Any]], list[str]]
    thread_for_user_fn: Callable[[str], Any]
    other_participant_fn: Callable[[Mapping[str, Any]], Any]
    is_blocked_between_fn: Callable[[str, str], bool]
    threads_for_current_user_fn: Callable[[], list[Mapping[str, Any]]]
    logger: Any
    attachment_download_url_fn: Callable[[str], str]
    delete_window_seconds: int
    message_timestamp_fn: Callable[[Mapping[str, Any]], Any]
    audit_delete_fn: Callable[[Mapping[str, Any], str], Any]


def list_room_messages(
    scope_type,
    scope_id,
    before,
    after,
    after_message_id,
    *,
    list_messages_fn,
    page_size,
    history_limited=False,
):
    """Load a channel/DM page and preserve the cursor pagination contract."""

    rows = list_messages_fn(
        scope_type,
        scope_id,
        before,
        after,
        after_message_id=after_message_id,
    )
    has_more = (
        not after
        and not after_message_id
        and not history_limited
        and len(rows) == page_size
    )
    return rows, has_more


def list_threads_for_current_user(
    user_id,
    *,
    list_rows_all_fn,
    query_cls,
    threads_collection,
    appwrite_exception,
    error_logger,
):
    """List both participant directions and de-duplicate thread rows."""

    try:
        rows_a = list_rows_all_fn(
            threads_collection,
            [query_cls.equal("participant_a", [user_id])],
        )
        rows_b = list_rows_all_fn(
            threads_collection,
            [query_cls.equal("participant_b", [user_id])],
        )
    except appwrite_exception:
        error_logger.exception("Failed to list DM threads")
        return []
    return list({str(row.get("$id") or row.get("id")): row for row in rows_a + rows_b}.values())


def list_thread_payloads(threads, *, thread_payload_fn):
    payload = []
    for thread in threads:
        item = thread_payload_fn(thread)
        if item:
            payload.append(item)
    payload.sort(key=lambda item: item.get("last_message_at") or "", reverse=True)
    return payload


def search_direct_message_users(
    query,
    *,
    current_user_id,
    list_rows_all_fn,
    query_cls,
    users_collection,
    row_id_fn,
    public_user_fn,
    appwrite_exception,
    error_logger,
):
    """Search the same user fields and apply the same result cap as before."""

    if len(query) < 2:
        return []
    try:
        users = list_rows_all_fn(
            users_collection,
            [query_cls.order_desc("created_at")],
            limit=100,
        )
    except appwrite_exception:
        error_logger.exception("Failed to search DM users")
        return []
    results = []
    for user in users:
        if row_id_fn(user) == current_user_id:
            continue
        haystack = " ".join([
            user.get("name") or "",
            user.get("username") or "",
            user.get("school") or "",
            user.get("major") or "",
            user.get("graduation_year") or "",
            user.get("class_year") or "",
        ]).lower()
        if query in haystack:
            results.append(public_user_fn(user))
        if len(results) >= 20:
            break
    return results


def create_direct_thread(
    other_user_id,
    *,
    get_or_create_thread_fn,
    current_user_id,
    emit_chat_event_fn=None,
    row_id_fn,
    thread_participant_ids_fn,
):
    thread = get_or_create_thread_fn(other_user_id)
    thread_id = row_id_fn(thread)
    if emit_chat_event_fn is not None:
        emit_chat_event_fn(
            "thread",
            thread_id,
            "thread_updated",
            thread_id=thread_id,
            actor_id=current_user_id,
            readable_user_ids=thread_participant_ids_fn(thread),
        )
    return thread


def attachment_scope_access(
    scope_type,
    scope_id,
    *,
    get_row_fn,
    collections,
    can_access_channel_fn,
    thread_for_user_fn,
):
    if scope_type == "channel":
        channel = get_row_fn(collections["chat_channels"], str(scope_id), allow_missing=True)
        return bool(can_access_channel_fn(channel))
    if scope_type == "thread":
        return bool(thread_for_user_fn(str(scope_id)))
    return False


def can_access_attachment(
    row,
    *,
    current_user_id,
    attachment_scope_access_fn,
):
    if not row:
        return False
    if row.get("status") == "pending":
        return str(row.get("user_id") or "") == current_user_id
    return row.get("status") == "active" and attachment_scope_access_fn(
        row.get("scope_type"),
        row.get("scope_id"),
    )


def create_chat_attachment(
    *,
    user_id,
    scope_type,
    scope_id,
    uploaded_file,
    entitlements,
    original_size,
    upload_encoding,
    create_attachment_fn,
):
    return create_attachment_fn(
        user_id=user_id,
        scope_type=scope_type,
        scope_id=scope_id,
        uploaded_file=uploaded_file,
        entitlements=entitlements,
        original_size=original_size,
        upload_encoding=upload_encoding,
    )


def cancel_pending_attachment(
    attachment_id,
    *,
    get_attachment_fn,
    current_user_id,
    delete_attachment_fn,
):
    row = get_attachment_fn(attachment_id)
    if not row or row.get("status") != "pending":
        raise PendingAttachmentNotFoundError
    if str(row.get("user_id") or "") != current_user_id:
        raise AttachmentOwnershipError
    delete_attachment_fn(row)


def read_attachment(
    attachment_id,
    *,
    preview,
    get_attachment_fn,
    can_access_attachment_fn,
    attachment_bytes_fn,
):
    row = get_attachment_fn(attachment_id)
    if not can_access_attachment_fn(row):
        raise AttachmentUnavailableError
    if preview and row.get("kind") not in {"image", "pdf"}:
        raise AttachmentUnavailableError
    use_preview_file = preview and row.get("kind") == "pdf"
    if use_preview_file:
        data = attachment_bytes_fn(row, preview=True)
    else:
        data = attachment_bytes_fn(row)
    if (preview and not data) or (not preview and data is None):
        raise AttachmentUnavailableError
    return row, data


def _user_display_name(user):
    return user.name or user.username or "Nest User"


def _channel_mentions(content, *, dependencies, channel_id, message_id):
    mentioned = {
        match.lower()
        for match in re.findall(r"(?<![\w@])@([A-Za-z0-9._-]{2,64})", content)
    }
    for username in mentioned:
        try:
            recipient = dependencies.first_row_fn(
                dependencies.users_collection,
                [dependencies.query_cls.equal("username", [username])],
            )
            recipient_id = dependencies.row_id_fn(recipient)
            if recipient_id and recipient_id != dependencies.current_user_id_fn():
                user = dependencies.current_user_fn()
                dependencies.notification_fn(
                    recipient_id,
                    "chat_mention",
                    f"{user.name or user.username} mentioned you",
                    content,
                    f"/chat?channel={channel_id}&message={message_id}",
                    source_ref=message_id,
                    dedupe_key=f"mention:{message_id}:{recipient_id}",
                    tag=f"mention:{channel_id}",
                    actor_user_id=dependencies.current_user_id_fn(),
                )
        except Exception:
            dependencies.logger.exception("Failed to dispatch channel mention notification")


def send_channel_message(channel_id, channel, *, dependencies: ChatMessageDeliveryDependencies):
    """Persist a channel message, including Discord and attachment workflows."""

    content, attachment_ids, gif = dependencies.message_media_payload_fn()
    now = dependencies.format_datetime_fn(dependencies.now_fn())
    previews = dependencies.previews_for_content_fn(content)
    if gif:
        previews.append(gif)
    user = dependencies.current_user_fn()
    user_id = dependencies.current_user_id_fn()
    base_payload = {
        "channel_id": channel_id,
        "user_id": user_id,
        "author_name": _user_display_name(user),
        "author_username": user.username or "",
        "author_avatar_url": user.picture_url or "",
        "content": content,
        "rendered_html": dependencies.render_markdown_fn(content),
        "link_preview_json": json.dumps(previews),
        "updated_at": now,
    }

    message_source = "appwrite"
    message_created_at = now
    if channel.get("kind") == "discord":
        bridge_files = []
        bridge_links = []
        for attachment_id in attachment_ids:
            attachment = dependencies.get_attachment_fn(attachment_id)
            if (
                not attachment
                or attachment.get("status") != "pending"
                or str(attachment.get("user_id") or "") != user_id
                or attachment.get("scope_type") != "channel"
                or str(attachment.get("scope_id") or "") != str(channel_id)
            ):
                raise dependencies.attachment_error(
                    "An attachment is unavailable or belongs to a different conversation."
                )
            if int(attachment.get("original_size_bytes") or 0) <= DISCORD_SMALL_ATTACHMENT_LIMIT:
                bridge_files.append({
                    "filename": attachment.get("original_filename") or "attachment",
                    "mime_type": attachment.get("mime_type") or "application/octet-stream",
                    "data": dependencies.attachment_bytes_fn(attachment),
                })
            else:
                bridge_links.append(
                    f"{attachment.get('original_filename') or 'Attachment'}: "
                    f"{dependencies.attachment_download_url_fn(attachment_id)}"
                )
        bridge_content = content
        if gif:
            bridge_content = "\n".join(value for value in (bridge_content, gif.get("url")) if value)
        if bridge_links:
            bridge_content = "\n".join(value for value in (bridge_content, *bridge_links) if value)
        try:
            discord_message, webhook = dependencies.execute_chat_webhook_fn(
                bridge_content,
                _user_display_name(user),
                user.picture_url,
                files=bridge_files,
            )
        except Exception as exc:
            dependencies.logger.exception("Failed to send Discord webhook message")
            raise DiscordDeliveryError from exc
        message_source = "discord"
        message_created_at = discord_message.get("timestamp") or now
        base_payload.update({
            "external_id": dependencies.discord_external_id_fn(channel, discord_message.get("id")),
            "discord_message_id": discord_message.get("id"),
            "discord_webhook_id": discord_message.get("webhook_id") or webhook.get("id"),
        })
    base_payload["source"] = message_source
    base_payload["created_at"] = message_created_at

    row_id = None
    if channel.get("kind") == "discord" and base_payload.get("discord_message_id"):
        row_id = dependencies.discord_row_id_fn(channel, base_payload.get("discord_message_id"))

    created = False
    if row_id:
        inserted = dependencies.insert_row_ignore_fn(
            dependencies.collections["chat_messages"],
            row_id=row_id,
            data=base_payload,
        )
        if inserted:
            row = dependencies.get_row_fn(dependencies.collections["chat_messages"], row_id)
            created = True
        else:
            existing = dependencies.find_discord_message_row_fn(
                row_id,
                base_payload.get("external_id"),
            )
            if not existing:
                dependencies.logger.error(
                    "Failed to persist channel message after duplicate insert race row_id=%s",
                    row_id,
                )
                raise MessagePersistenceError
            row = existing
    else:
        try:
            row = dependencies.create_row_fn(
                dependencies.collections["chat_messages"],
                row_id=dependencies.id_unique_fn(),
                data=base_payload,
            )
            created = True
        except dependencies.appwrite_exception as exc:
            dependencies.logger.exception("Failed to persist channel message")
            raise MessagePersistenceError from exc

    try:
        if attachment_ids:
            dependencies.bind_pending_fn(
                attachment_ids,
                user_id=user_id,
                scope_type="channel",
                scope_id=channel_id,
                message_id=dependencies.row_id_fn(row),
            )
    except (dependencies.attachment_error, dependencies.appwrite_exception) as exc:
        dependencies.logger.exception("Failed to bind channel message attachments")
        try:
            dependencies.delete_row_fn(
                dependencies.collections["chat_messages"],
                dependencies.row_id_fn(row),
            )
        except dependencies.appwrite_exception:
            dependencies.logger.exception("Failed to roll back channel message")
        raise AttachmentBindingError(str(exc) or "Unable to attach files.") from exc

    try:
        if channel.get("kind") == "discord":
            dependencies.prune_discord_fn(channel_id)
        if created:
            message_id = dependencies.row_id_fn(row)
            dependencies.emit_chat_event_fn(
                "channel",
                channel_id,
                "message_created",
                message_id=message_id,
                channel_id=channel_id,
                actor_id=user_id,
                channel=channel,
            )
            _channel_mentions(
                content,
                dependencies=dependencies,
                channel_id=channel_id,
                message_id=message_id,
            )
    except dependencies.appwrite_exception as exc:
        dependencies.logger.exception("Failed to finalize channel message")
        raise MessagePersistenceError from exc

    if created:
        try:
            dependencies.invite_activation_fn(user_id, "chat_message")
        except Exception:
            dependencies.logger.exception("Failed to record invite activation for channel message")
    return row, created


def send_direct_message(
    thread_id,
    thread,
    other,
    *,
    dependencies: ChatMessageDeliveryDependencies,
):
    """Create a DM after applying blocking, attachment binding, and side effects."""

    user_id = dependencies.current_user_id_fn()
    recipient_id = str(other.get("id") or other.get("$id") or "")
    if dependencies.is_blocked_between_fn(user_id, recipient_id):
        raise DirectMessageBlockedError

    content, attachment_ids, gif = dependencies.message_media_payload_fn()
    now = dependencies.format_datetime_fn(dependencies.now_fn())
    previews = dependencies.previews_for_content_fn(content)
    if gif:
        previews.append(gif)
    user = dependencies.current_user_fn()
    row = None
    try:
        row = dependencies.create_row_fn(
            dependencies.collections["chat_messages"],
            row_id=dependencies.id_unique_fn(),
            data={
                "thread_id": thread_id,
                "source": "appwrite",
                "user_id": user_id,
                "author_name": _user_display_name(user),
                "author_username": user.username or "",
                "author_avatar_url": user.picture_url or "",
                "content": content,
                "rendered_html": dependencies.render_markdown_fn(content),
                "link_preview_json": json.dumps(previews),
                "created_at": now,
                "updated_at": now,
            },
        )
        if attachment_ids:
            dependencies.bind_pending_fn(
                attachment_ids,
                user_id=user_id,
                scope_type="thread",
                scope_id=thread_id,
                message_id=dependencies.row_id_fn(row),
            )
        dependencies.update_row_fn(
            dependencies.collections["chat_dm_threads"],
            thread_id,
            {"last_message_at": now, "updated_at": now},
        )
        message_id = dependencies.row_id_fn(row)
        dependencies.emit_chat_event_fn(
            "thread",
            thread_id,
            "message_created",
            message_id=message_id,
            thread_id=thread_id,
            actor_id=user_id,
            readable_user_ids=dependencies.thread_participant_ids_fn(thread),
        )
        if recipient_id:
            try:
                dependencies.notification_fn(
                    recipient_id,
                    "chat_dm",
                    user.name or user.username or "New direct message",
                    content or ("Sent a GIF" if gif else "Sent an attachment"),
                    f"/chat?thread={thread_id}&message={message_id}",
                    source_ref=message_id,
                    dedupe_key=f"chat:{message_id}",
                    tag=f"dm:{thread_id}",
                    actor_user_id=user_id,
                )
            except Exception:
                dependencies.logger.exception("Failed to dispatch DM notification")
    except (dependencies.appwrite_exception, dependencies.attachment_error) as exc:
        dependencies.logger.exception("Failed to save DM")
        if row:
            try:
                dependencies.delete_message_attachments_fn(dependencies.row_id_fn(row))
                dependencies.delete_row_fn(
                    dependencies.collections["chat_messages"],
                    dependencies.row_id_fn(row),
                )
            except dependencies.appwrite_exception:
                dependencies.logger.exception("Failed to roll back DM message")
        raise DirectMessagePersistenceError from exc
    try:
        dependencies.invite_activation_fn(user_id, "chat_message")
    except Exception:
        dependencies.logger.exception("Failed to record invite activation for direct message")
    return row


def get_message_for_current_user(
    message_id,
    *,
    message_for_current_user_fn,
    serialize_message_fn,
):
    row = message_for_current_user_fn(message_id)
    if not row:
        raise MessageNotFoundError
    return serialize_message_fn(row)


def delete_chat_message(message_id, *, dependencies: ChatMessageDeliveryDependencies):
    """Delete a message while preserving ownership, time-window, and side effects."""

    row = dependencies.get_row_fn(
        dependencies.collections["chat_messages"],
        message_id,
        allow_missing=True,
    )
    if not row or row.get("deleted_at"):
        raise MessageNotFoundError
    if str(row.get("user_id") or "") != dependencies.current_user_id_fn():
        raise MessageOwnershipError
    created = dependencies.message_timestamp_fn(row)
    if (
        dependencies.now_fn() - created
    ).total_seconds() > dependencies.delete_window_seconds:
        raise MessageExpiredError

    if row.get("source") == "discord" and row.get("discord_message_id"):
        try:
            dependencies.delete_webhook_message_fn(
                row.get("discord_webhook_id"),
                row.get("discord_message_id"),
            )
        except Exception as exc:
            dependencies.logger.exception("Failed to delete Discord webhook message")
            raise DiscordDeliveryError from exc
    try:
        deleted_at = dependencies.format_datetime_fn(dependencies.now_fn())
        dependencies.update_row_fn(
            dependencies.collections["chat_messages"],
            message_id,
            {
                "deleted_at": deleted_at,
                "deleted_by": dependencies.current_user_id_fn(),
                "updated_at": deleted_at,
            },
        )
        dependencies.delete_message_attachments_fn(message_id)
        if row.get("channel_id"):
            channel = dependencies.get_row_fn(
                dependencies.collections["chat_channels"],
                row.get("channel_id"),
                allow_missing=True,
            )
            dependencies.emit_chat_event_fn(
                "channel",
                row.get("channel_id"),
                "message_deleted",
                message_id=message_id,
                channel_id=row.get("channel_id"),
                actor_id=dependencies.current_user_id_fn(),
                channel=channel,
            )
        elif row.get("thread_id"):
            thread = dependencies.get_row_fn(
                dependencies.collections["chat_dm_threads"],
                row.get("thread_id"),
                allow_missing=True,
            )
            dependencies.emit_chat_event_fn(
                "thread",
                row.get("thread_id"),
                "message_deleted",
                message_id=message_id,
                thread_id=row.get("thread_id"),
                actor_id=dependencies.current_user_id_fn(),
                readable_user_ids=dependencies.thread_participant_ids_fn(thread or {}),
            )
        dependencies.audit_delete_fn(row, deleted_at)
    except dependencies.appwrite_exception as exc:
        dependencies.logger.exception("Failed to delete chat message")
        raise MessagePersistenceError from exc
    return deleted_at


def update_block(
    target_id,
    *,
    method,
    dependencies: ChatMessageDeliveryDependencies,
):
    """Create/delete one directional block and notify affected DM threads."""

    user_id = dependencies.current_user_id_fn()
    key = f"{user_id}:{target_id}"
    try:
        if method == "DELETE":
            row = dependencies.first_row_fn(
                dependencies.collections["chat_blocks"],
                [dependencies.query_cls.equal("block_key", [key])],
            )
            if row:
                dependencies.delete_row_fn(
                    dependencies.collections["chat_blocks"],
                    dependencies.row_id_fn(row),
                )
            blocked = False
        else:
            existing = dependencies.first_row_fn(
                dependencies.collections["chat_blocks"],
                [dependencies.query_cls.equal("block_key", [key])],
            )
            if not existing:
                dependencies.create_row_fn(
                    dependencies.collections["chat_blocks"],
                    row_id=dependencies.id_unique_fn(),
                    data={
                        "blocker_id": user_id,
                        "blocked_id": target_id,
                        "block_key": key,
                        "created_at": dependencies.format_datetime_fn(dependencies.now_fn()),
                    },
                )
            blocked = True

        for thread in dependencies.threads_for_current_user_fn():
            if target_id in dependencies.thread_participant_ids_fn(thread):
                thread_id = dependencies.row_id_fn(thread)
                dependencies.emit_chat_event_fn(
                    "thread",
                    thread_id,
                    "block_updated",
                    thread_id=thread_id,
                    actor_id=user_id,
                    readable_user_ids=dependencies.thread_participant_ids_fn(thread),
                )
    except dependencies.appwrite_exception:
        if method == "DELETE":
            dependencies.logger.exception("Failed to unblock user")
        else:
            dependencies.logger.exception("Failed to block user")
        raise
    return blocked
