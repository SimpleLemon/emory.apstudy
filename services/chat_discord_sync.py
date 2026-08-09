"""Stateful Discord chat persistence, synchronization, and ingest operations.

The service deliberately receives its application callbacks from the blueprint
adapter.  That keeps this boundary independent of Flask while preserving the
blueprint's established patch seams for request-time and adapter tests.
"""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DiscordSyncDependencies:
    collections: dict
    appwrite_exception: type
    query_cls: Any
    id_unique_fn: Any
    row_id_fn: Any
    now_fn: Any
    format_datetime_fn: Any
    parse_datetime_fn: Any
    message_timestamp_fn: Any
    runtime_environment_config_fn: Any
    default_channels_fn: Any
    get_row_fn: Any
    first_row_fn: Any
    create_row_fn: Any
    insert_row_ignore_fn: Any
    update_row_fn: Any
    delete_row_fn: Any
    list_rows_all_fn: Any
    emit_chat_event_fn: Any
    delete_message_attachments_fn: Any
    fetch_channel_messages_fn: Any
    ensure_discord_channel_fn: Any
    discord_message_payload_fn: Any
    discord_message_row_id_fn: Any
    discord_message_external_id_fn: Any
    discord_message_changes_fn: Any
    find_discord_message_row_fn: Any
    apply_discord_message_changes_fn: Any
    upsert_discord_message_fn: Any
    log_discord_upsert_failure_fn: Any
    soft_delete_discord_message_fn: Any
    reconcile_discord_deletes_fn: Any
    sync_discord_channel_fn: Any
    delete_discord_gateway_message_fn: Any
    can_sync_discord_channel_fn: Any
    discord_channel_for_discord_id_fn: Any
    prune_discord_messages_fn: Any
    logger: Any
    discord_message_limit: int
    partial_create_required_fields: tuple[str, ...]


def ensure_discord_channel(
    row_id,
    name,
    label,
    channel_id,
    read_only,
    *,
    dependencies: DiscordSyncDependencies,
):
    now = dependencies.format_datetime_fn(dependencies.now_fn())
    existing = dependencies.get_row_fn(
        dependencies.collections["chat_channels"],
        row_id,
        allow_missing=True,
    )
    stable_payload = {
        "kind": "discord",
        "name": name,
        "label": label,
        "section": "nest",
        "discord_channel_id": channel_id,
        "read_only": read_only,
        "approved": True,
    }
    if existing:
        if all(existing.get(key) == value for key, value in stable_payload.items()):
            return existing
        return dependencies.update_row_fn(
            dependencies.collections["chat_channels"],
            row_id,
            {**stable_payload, "updated_at": now},
        )
    return dependencies.create_row_fn(
        dependencies.collections["chat_channels"],
        row_id=row_id,
        data={**stable_payload, "created_at": now, "updated_at": now},
    )


def default_channels(*, dependencies: DiscordSyncDependencies):
    channels = []
    configured = dependencies.runtime_environment_config_fn()
    announcements_id = (configured.discord_announcements_channel_id or "").strip()
    chat_id = (configured.discord_chat_channel_id or "").strip()
    try:
        if announcements_id:
            channels.append(
                dependencies.ensure_discord_channel_fn(
                    "nest_announcements",
                    "nest-announcements",
                    "Nest Announcements",
                    announcements_id,
                    True,
                )
            )
        if chat_id:
            channels.append(
                dependencies.ensure_discord_channel_fn(
                    "nest_chat",
                    "chat",
                    "Chat",
                    chat_id,
                    False,
                )
            )
    except dependencies.appwrite_exception:
        dependencies.logger.exception("Failed to ensure default chat channels")
    return channels


def discord_message_changes(
    existing,
    payload,
    *,
    compare_fields: tuple[str, ...],
):
    changes = {}
    for key in compare_fields:
        if key not in payload:
            continue
        incoming = payload.get(key)
        if existing.get(key) != incoming:
            changes[key] = incoming
    if changes:
        changes["updated_at"] = payload.get("updated_at")
    return changes


def find_discord_message_row(row_id, external_id, *, dependencies: DiscordSyncDependencies):
    existing = None
    if row_id:
        existing = dependencies.get_row_fn(
            dependencies.collections["chat_messages"],
            row_id,
            allow_missing=True,
        )
    if not existing and external_id:
        existing = dependencies.first_row_fn(
            dependencies.collections["chat_messages"],
            [dependencies.query_cls.equal("external_id", [external_id])],
        )
    return existing


def apply_discord_message_changes(
    existing,
    payload,
    message,
    *,
    partial=False,
    emit_event=False,
    channel=None,
    dependencies: DiscordSyncDependencies,
):
    channel_id = payload.get("channel_id")
    row_id = dependencies.row_id_fn(existing)
    changes = dependencies.discord_message_changes_fn(existing, payload)
    if existing.get("user_id"):
        for field in (
            "content",
            "rendered_html",
            "link_preview_json",
            "author_name",
            "author_username",
            "author_avatar_url",
        ):
            changes.pop(field, None)
    if partial and not changes and message.get("edited_timestamp"):
        changes = {"updated_at": payload.get("updated_at")}
    if not changes:
        return existing, False
    try:
        row = dependencies.update_row_fn(
            dependencies.collections["chat_messages"],
            row_id,
            changes,
        )
    except dependencies.appwrite_exception:
        return existing, False
    if emit_event:
        dependencies.emit_chat_event_fn(
            "channel",
            channel_id,
            "message_updated",
            message_id=row_id,
            channel_id=channel_id,
            channel=channel,
        )
    return row, False


def log_discord_upsert_failure(row_id, external_id, discord_id, changes, *, logger):
    logger.error(
        "Failed to upsert Discord message row_id=%s external_id=%s discord_message_id=%s changed_fields=%s value_lengths=%s",
        row_id,
        external_id,
        discord_id,
        sorted((changes or {}).keys()),
        {
            key: len(value) if isinstance(value, str) else None
            for key, value in (changes or {}).items()
        },
    )


def upsert_discord_message(
    channel,
    message,
    emit_event=False,
    *,
    partial=False,
    dependencies: DiscordSyncDependencies,
):
    payload = dependencies.discord_message_payload_fn(channel, message, partial=partial)
    if not payload:
        return None, False
    channel_id = payload.get("channel_id")
    external_id = payload.get("external_id")
    discord_id = payload.get("discord_message_id")
    row_id = dependencies.discord_message_row_id_fn(channel, discord_id)
    existing = dependencies.find_discord_message_row_fn(row_id, external_id)
    if existing:
        return dependencies.apply_discord_message_changes_fn(
            existing,
            payload,
            message,
            partial=partial,
            emit_event=emit_event,
            channel=channel,
        )
    if partial and any(
        key not in payload for key in dependencies.partial_create_required_fields
    ):
        dependencies.logger.info(
            "Skipping partial Discord message update for unknown message %s",
            discord_id,
        )
        return None, False

    insert_id = row_id or dependencies.id_unique_fn()
    inserted = dependencies.insert_row_ignore_fn(
        dependencies.collections["chat_messages"],
        row_id=insert_id,
        data=payload,
    )
    if inserted:
        row = dependencies.get_row_fn(
            dependencies.collections["chat_messages"],
            insert_id,
        )
        if emit_event:
            dependencies.emit_chat_event_fn(
                "channel",
                channel_id,
                "message_created",
                message_id=dependencies.row_id_fn(row),
                channel_id=channel_id,
                channel=channel,
            )
        return row, True

    existing = dependencies.find_discord_message_row_fn(insert_id, external_id)
    if existing:
        return dependencies.apply_discord_message_changes_fn(
            existing,
            payload,
            message,
            partial=partial,
            emit_event=emit_event,
            channel=channel,
        )

    dependencies.log_discord_upsert_failure_fn(
        row_id,
        external_id,
        discord_id,
        payload,
    )
    return None, False


def soft_delete_discord_message(
    channel,
    discord_message_id,
    *,
    emit_event=False,
    dependencies: DiscordSyncDependencies,
):
    if not channel or not discord_message_id:
        return None
    channel_id = dependencies.row_id_fn(channel)
    external_id = dependencies.discord_message_external_id_fn(channel, discord_message_id)
    row_id = dependencies.discord_message_row_id_fn(channel, discord_message_id)
    try:
        row = None
        if row_id:
            row = dependencies.get_row_fn(
                dependencies.collections["chat_messages"],
                row_id,
                allow_missing=True,
            )
        if not row and external_id:
            row = dependencies.first_row_fn(
                dependencies.collections["chat_messages"],
                [dependencies.query_cls.equal("external_id", [external_id])],
            )
        if not row:
            row = dependencies.first_row_fn(
                dependencies.collections["chat_messages"],
                [
                    dependencies.query_cls.equal("channel_id", [channel_id]),
                    dependencies.query_cls.equal(
                        "discord_message_id",
                        [str(discord_message_id)],
                    ),
                ],
            )
        if not row or row.get("deleted_at"):
            return row
        deleted_at = dependencies.format_datetime_fn(dependencies.now_fn())
        dependencies.update_row_fn(
            dependencies.collections["chat_messages"],
            dependencies.row_id_fn(row),
            {
                "deleted_at": deleted_at,
                "deleted_by": "discord",
                "updated_at": deleted_at,
            },
        )
        dependencies.delete_message_attachments_fn(dependencies.row_id_fn(row))
        if emit_event:
            dependencies.emit_chat_event_fn(
                "channel",
                channel_id,
                "message_deleted",
                message_id=dependencies.row_id_fn(row),
                channel_id=channel_id,
                actor_id="discord",
                channel=channel,
            )
        return row
    except dependencies.appwrite_exception:
        dependencies.logger.exception(
            "Failed to soft-delete Discord message %s",
            discord_message_id,
        )
        return None


def reconcile_discord_deletes(
    channel,
    discord_messages,
    *,
    emit_events=False,
    dependencies: DiscordSyncDependencies,
):
    if not channel or not discord_messages:
        return 0
    channel_id = dependencies.row_id_fn(channel)
    discord_ids = {
        str(message.get("id"))
        for message in discord_messages
        if message.get("id")
    }
    oldest_ts = None
    for message in discord_messages:
        timestamp = message.get("timestamp")
        if not timestamp:
            continue
        parsed = dependencies.parse_datetime_fn(timestamp)
        if parsed and (oldest_ts is None or parsed < oldest_ts):
            oldest_ts = parsed
    if oldest_ts is None:
        return 0
    try:
        rows = dependencies.list_rows_all_fn(
            dependencies.collections["chat_messages"],
            [dependencies.query_cls.equal("channel_id", [channel_id])],
        )
    except dependencies.appwrite_exception:
        dependencies.logger.exception(
            "Failed to list Discord chat messages for delete reconciliation"
        )
        return 0
    deleted_count = 0
    for row in rows:
        if row.get("deleted_at"):
            continue
        if (row.get("source") or "") != "discord":
            continue
        discord_message_id = row.get("discord_message_id")
        if not discord_message_id:
            continue
        if str(discord_message_id) in discord_ids:
            continue
        created = dependencies.message_timestamp_fn(row)
        if created < oldest_ts:
            continue
        result = dependencies.soft_delete_discord_message_fn(
            channel,
            discord_message_id,
            emit_event=emit_events,
        )
        if result is not None and not result.get("deleted_at"):
            deleted_count += 1
    return deleted_count


def sync_discord_channel(
    channel,
    emit_events=False,
    emit_delete_events=None,
    *,
    dependencies: DiscordSyncDependencies,
):
    discord_channel_id = channel.get("discord_channel_id")
    if not discord_channel_id:
        return 0, 0
    if emit_delete_events is None:
        emit_delete_events = emit_events
    messages = dependencies.fetch_channel_messages_fn(
        discord_channel_id,
        dependencies.discord_message_limit,
    )
    created_count = 0
    for message in messages:
        _, created = dependencies.upsert_discord_message_fn(
            channel,
            message,
            emit_event=emit_events,
        )
        if created:
            created_count += 1
    deleted_count = dependencies.reconcile_discord_deletes_fn(
        channel,
        messages,
        emit_events=emit_delete_events,
    )
    dependencies.prune_discord_messages_fn(dependencies.row_id_fn(channel))
    return created_count, deleted_count


def sync_discord_channels(
    emit_events=True,
    emit_delete_events=None,
    *,
    dependencies: DiscordSyncDependencies,
):
    dependencies.default_channels_fn()
    try:
        channels = dependencies.list_rows_all_fn(
            dependencies.collections["chat_channels"],
            [dependencies.query_cls.equal("kind", ["discord"])],
        )
    except dependencies.appwrite_exception:
        dependencies.logger.exception("Failed to list Discord chat channels for sync")
        return 0, 0
    created_count = 0
    deleted_count = 0
    for channel in channels:
        if not dependencies.can_sync_discord_channel_fn(channel):
            continue
        created, deleted = dependencies.sync_discord_channel_fn(
            channel,
            emit_events=emit_events,
            emit_delete_events=emit_delete_events,
        )
        created_count += created
        deleted_count += deleted
    return created_count, deleted_count


def ingest_discord_gateway_message(
    message,
    *,
    event_type="create",
    dependencies: DiscordSyncDependencies,
):
    channel = dependencies.discord_channel_for_discord_id_fn(
        (message or {}).get("channel_id")
    )
    if not dependencies.can_sync_discord_channel_fn(channel):
        return None, False
    partial = event_type == "update"
    row, created = dependencies.upsert_discord_message_fn(
        channel,
        message,
        emit_event=True,
        partial=partial,
    )
    if row:
        dependencies.prune_discord_messages_fn(dependencies.row_id_fn(channel))
    return row, created


def delete_discord_gateway_message(
    discord_channel_id,
    discord_message_id,
    *,
    dependencies: DiscordSyncDependencies,
):
    channel = dependencies.discord_channel_for_discord_id_fn(discord_channel_id)
    if not dependencies.can_sync_discord_channel_fn(channel):
        return None
    row = dependencies.soft_delete_discord_message_fn(
        channel,
        discord_message_id,
        emit_event=True,
    )
    if row is None:
        dependencies.logger.warning(
            "Discord delete received for channel %s message %s but no matching chat row was found.",
            discord_channel_id,
            discord_message_id,
        )
    return row


def delete_discord_gateway_messages(
    discord_channel_id,
    discord_message_ids,
    *,
    dependencies: DiscordSyncDependencies,
):
    deleted = 0
    for message_id in discord_message_ids or []:
        row = dependencies.delete_discord_gateway_message_fn(
            discord_channel_id,
            message_id,
        )
        if row:
            deleted += 1
    return deleted


def can_sync_discord_channel(channel):
    return bool(
        channel
        and channel.get("kind") == "discord"
        and channel.get("discord_channel_id")
    )


def discord_channel_for_discord_id(
    discord_channel_id,
    *,
    dependencies: DiscordSyncDependencies,
):
    if not discord_channel_id:
        return None
    try:
        channel = dependencies.first_row_fn(
            dependencies.collections["chat_channels"],
            [
                dependencies.query_cls.equal(
                    "discord_channel_id",
                    [str(discord_channel_id)],
                )
            ],
        )
        if channel:
            return channel
        dependencies.default_channels_fn()
        return dependencies.first_row_fn(
            dependencies.collections["chat_channels"],
            [
                dependencies.query_cls.equal(
                    "discord_channel_id",
                    [str(discord_channel_id)],
                )
            ],
        )
    except dependencies.appwrite_exception:
        dependencies.logger.exception(
            "Failed to resolve Discord chat channel %s",
            discord_channel_id,
        )
        return None


def prune_discord_messages(channel_id, *, dependencies: DiscordSyncDependencies):
    try:
        rows = dependencies.list_rows_all_fn(
            dependencies.collections["chat_messages"],
            [
                dependencies.query_cls.equal("channel_id", [channel_id]),
                dependencies.query_cls.order_desc("created_at"),
            ],
        )
    except dependencies.appwrite_exception:
        return
    for row in rows[dependencies.discord_message_limit :]:
        try:
            dependencies.delete_row_fn(
                dependencies.collections["chat_messages"],
                dependencies.row_id_fn(row),
            )
        except dependencies.appwrite_exception:
            dependencies.logger.exception("Failed to prune old Discord message")
