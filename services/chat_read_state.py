"""Chat read-state, message visibility, and unread-count domain helpers.

These functions deliberately receive persistence and authorization callbacks.
The blueprint adapters rebuild the dependency object for each call, keeping
the historical ``blueprints.chat_api`` patch points live while the domain
logic remains independent of Flask request state.
"""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ChatReadStateDependencies:
    collections: dict
    appwrite_exception: type
    query_cls: Any
    id_unique_fn: Any
    row_id_fn: Any
    now_fn: Any
    format_datetime_fn: Any
    current_user_id_fn: Any
    get_row_fn: Any
    first_row_fn: Any
    create_row_fn: Any
    update_row_fn: Any
    delete_row_fn: Any
    list_rows_fn: Any
    read_key_fn: Any
    message_timestamp_fn: Any
    message_scope_field_fn: Any
    message_in_scope_fn: Any
    message_visible_for_user_fn: Any
    message_can_be_unread_target_fn: Any
    blocked_user_ids_fn: Any
    thread_for_user_fn: Any
    can_access_channel_fn: Any
    latest_visible_message_fn: Any
    persist_read_state_fn: Any
    read_state_for_scope_fn: Any
    latest_unread_target_fn: Any
    previous_visible_message_fn: Any
    clear_read_state_fn: Any
    error_logger: Any
    summary_scan_limit: int
    unread_cap: int


def read_key(user_id, scope_type, scope_id):
    return f"{user_id}:{scope_type}:{scope_id}"


def initialize_new_user_discord_read_states(
    user_id,
    *,
    default_channels_fn,
    list_rows_all_fn,
    query_cls,
    channels_collection,
    row_id_fn,
    latest_visible_message_fn,
    persist_read_state_fn,
    appwrite_exception,
    error_logger,
):
    user_id = str(user_id or "").strip()
    if not user_id:
        return
    default_channels_fn()
    try:
        channels = list_rows_all_fn(
            channels_collection,
            [query_cls.equal("kind", ["discord"])],
        )
    except appwrite_exception:
        error_logger.exception("Failed to list Discord channels for onboarding read init")
        return
    for channel in channels:
        channel_id = row_id_fn(channel)
        if not channel_id:
            continue
        latest = latest_visible_message_fn("channel", channel_id)
        if latest:
            persist_read_state_fn(user_id, "channel", channel_id, latest)


def read_state_for_scope(user_id, scope_type, scope_id, *, dependencies):
    try:
        return dependencies.first_row_fn(
            dependencies.collections["chat_read_states"],
            [
                dependencies.query_cls.equal(
                    "read_key",
                    [dependencies.read_key_fn(user_id, scope_type, scope_id)],
                )
            ],
        )
    except dependencies.appwrite_exception:
        dependencies.error_logger.exception("Failed to load chat read state")
        return None


def latest_visible_message(scope_type, scope_id, *, dependencies):
    field = dependencies.message_scope_field_fn(scope_type)
    try:
        rows = dependencies.list_rows_fn(
            dependencies.collections["chat_messages"],
            [
                dependencies.query_cls.equal(field, [scope_id]),
                dependencies.query_cls.order_desc("created_at"),
                dependencies.query_cls.limit(10),
            ],
        ).get("rows", [])
    except dependencies.appwrite_exception:
        dependencies.error_logger.exception("Failed to load latest chat message")
        return None
    for row in rows:
        if not row.get("deleted_at"):
            return row
    return None


def message_scope_field(scope_type):
    return "channel_id" if scope_type == "channel" else "thread_id"


def message_in_scope(
    row,
    scope_type,
    scope_id,
    *,
    message_scope_field_fn=message_scope_field,
):
    return bool(row and row.get(message_scope_field_fn(scope_type)) == scope_id)


def message_for_current_user(message_id, *, dependencies):
    row = dependencies.get_row_fn(
        dependencies.collections["chat_messages"],
        message_id,
        allow_missing=True,
    )
    if not row or row.get("deleted_at"):
        return None
    channel_id = row.get("channel_id")
    thread_id = row.get("thread_id")
    if channel_id:
        channel = dependencies.get_row_fn(
            dependencies.collections["chat_channels"],
            channel_id,
            allow_missing=True,
        )
        if not dependencies.can_access_channel_fn(channel):
            return None
    elif thread_id:
        if not dependencies.thread_for_user_fn(thread_id):
            return None
        if str(row.get("user_id") or "") in dependencies.blocked_user_ids_fn(
            dependencies.current_user_id_fn()
        ):
            return None
    else:
        return None
    return row


def message_visible_for_user(row, scope_type, blocked_user_ids=None):
    if not row or row.get("deleted_at"):
        return False
    if scope_type == "thread" and str(row.get("user_id") or "") in (blocked_user_ids or set()):
        return False
    return True


def message_can_be_unread_target(
    row,
    scope_type,
    user_id,
    blocked_user_ids=None,
    *,
    message_visible_for_user_fn=message_visible_for_user,
):
    if not message_visible_for_user_fn(row, scope_type, blocked_user_ids):
        return False
    return str(row.get("user_id") or "") != str(user_id)


def persist_read_state(
    user_id,
    scope_type,
    scope_id,
    latest,
    *,
    fallback_to_now=True,
    dependencies,
):
    read_key_value = dependencies.read_key_fn(user_id, scope_type, scope_id)
    last_read_at = (
        latest.get("created_at")
        if latest
        else (
            dependencies.format_datetime_fn(dependencies.now_fn())
            if fallback_to_now
            else None
        )
    )
    payload = {
        "user_id": user_id,
        "scope_type": scope_type,
        "scope_id": scope_id,
        "read_key": read_key_value,
        "last_read_message_id": dependencies.row_id_fn(latest) if latest else None,
        "last_read_at": last_read_at,
    }
    try:
        existing = dependencies.first_row_fn(
            dependencies.collections["chat_read_states"],
            [dependencies.query_cls.equal("read_key", [read_key_value])],
        )
        if existing:
            return dependencies.update_row_fn(
                dependencies.collections["chat_read_states"],
                dependencies.row_id_fn(existing),
                payload,
            )
        return dependencies.create_row_fn(
            dependencies.collections["chat_read_states"],
            row_id=dependencies.id_unique_fn(),
            data=payload,
        )
    except dependencies.appwrite_exception:
        dependencies.error_logger.exception("Failed to persist chat read state")
        return None


def mark_read(scope_type, scope_id, message_id=None, *, dependencies):
    user_id = dependencies.current_user_id_fn()
    latest = None
    if message_id:
        try:
            latest = dependencies.get_row_fn(
                dependencies.collections["chat_messages"],
                message_id,
                allow_missing=True,
            )
        except dependencies.appwrite_exception:
            latest = None
        if latest:
            if (
                not dependencies.message_in_scope_fn(latest, scope_type, scope_id)
                or latest.get("deleted_at")
            ):
                latest = None
    server_latest = dependencies.latest_visible_message_fn(scope_type, scope_id)
    if server_latest:
        if (
            not latest
            or dependencies.message_timestamp_fn(server_latest)
            > dependencies.message_timestamp_fn(latest)
        ):
            latest = server_latest
    elif not latest:
        latest = None
    return dependencies.persist_read_state_fn(user_id, scope_type, scope_id, latest)


def latest_unread_target(scope_type, scope_id, user_id, blocked_user_ids, *, dependencies):
    field = dependencies.message_scope_field_fn(scope_type)
    offset = 0
    while True:
        try:
            rows = dependencies.list_rows_fn(
                dependencies.collections["chat_messages"],
                [
                    dependencies.query_cls.equal(field, [scope_id]),
                    dependencies.query_cls.order_desc("created_at"),
                    dependencies.query_cls.limit(dependencies.summary_scan_limit),
                    dependencies.query_cls.offset(offset),
                ],
            ).get("rows", [])
        except dependencies.appwrite_exception:
            dependencies.error_logger.exception("Failed to load latest unread chat target")
            return None
        for row in rows:
            if dependencies.message_can_be_unread_target_fn(
                row,
                scope_type,
                user_id,
                blocked_user_ids,
            ):
                return row
        if len(rows) < dependencies.summary_scan_limit:
            return None
        offset += dependencies.summary_scan_limit


def previous_visible_message(scope_type, scope_id, target, blocked_user_ids, *, dependencies):
    created_at = target.get("created_at") if target else None
    if not created_at:
        return None
    field = dependencies.message_scope_field_fn(scope_type)
    offset = 0
    while True:
        try:
            rows = dependencies.list_rows_fn(
                dependencies.collections["chat_messages"],
                [
                    dependencies.query_cls.equal(field, [scope_id]),
                    dependencies.query_cls.less_than("created_at", created_at),
                    dependencies.query_cls.order_desc("created_at"),
                    dependencies.query_cls.limit(dependencies.summary_scan_limit),
                    dependencies.query_cls.offset(offset),
                ],
            ).get("rows", [])
        except dependencies.appwrite_exception:
            dependencies.error_logger.exception("Failed to load previous chat read boundary")
            return None
        for row in rows:
            if dependencies.message_visible_for_user_fn(
                row,
                scope_type,
                blocked_user_ids,
            ):
                return row
        if len(rows) < dependencies.summary_scan_limit:
            return None
        offset += dependencies.summary_scan_limit


def clear_read_state(user_id, scope_type, scope_id, *, dependencies):
    read_key_value = dependencies.read_key_fn(user_id, scope_type, scope_id)
    try:
        existing = dependencies.first_row_fn(
            dependencies.collections["chat_read_states"],
            [dependencies.query_cls.equal("read_key", [read_key_value])],
        )
        if existing:
            dependencies.delete_row_fn(
                dependencies.collections["chat_read_states"],
                dependencies.row_id_fn(existing),
            )
    except dependencies.appwrite_exception:
        dependencies.error_logger.exception("Failed to clear chat read state")
    return None


def mark_unread(scope_type, scope_id, message_id=None, *, dependencies):
    user_id = dependencies.current_user_id_fn()
    blocked_user_ids = (
        dependencies.blocked_user_ids_fn(user_id)
        if scope_type == "thread"
        else set()
    )
    target = None
    if message_id:
        try:
            candidate = dependencies.get_row_fn(
                dependencies.collections["chat_messages"],
                message_id,
                allow_missing=True,
            )
        except dependencies.appwrite_exception:
            candidate = None
        if (
            dependencies.message_in_scope_fn(candidate, scope_type, scope_id)
            and dependencies.message_can_be_unread_target_fn(
                candidate,
                scope_type,
                user_id,
                blocked_user_ids,
            )
        ):
            target = candidate
    if not target:
        target = dependencies.latest_unread_target_fn(
            scope_type,
            scope_id,
            user_id,
            blocked_user_ids,
        )
    if not target:
        return dependencies.read_state_for_scope_fn(user_id, scope_type, scope_id)

    previous = dependencies.previous_visible_message_fn(
        scope_type,
        scope_id,
        target,
        blocked_user_ids,
    )
    if previous:
        return dependencies.persist_read_state_fn(
            user_id,
            scope_type,
            scope_id,
            previous,
            fallback_to_now=False,
        )
    dependencies.clear_read_state_fn(user_id, scope_type, scope_id)
    return {}


def unread_count(scope_type, scope_id, user_id, last_read_at, *, dependencies):
    field = dependencies.message_scope_field_fn(scope_type)
    offset = 0
    blocked_user_ids = (
        dependencies.blocked_user_ids_fn(user_id)
        if scope_type == "thread"
        else set()
    )
    count = 0

    while True:
        queries = [
            dependencies.query_cls.equal(field, [scope_id]),
            dependencies.query_cls.order_desc("created_at"),
            dependencies.query_cls.limit(dependencies.summary_scan_limit),
            dependencies.query_cls.offset(offset),
        ]
        if last_read_at:
            queries.insert(
                1,
                dependencies.query_cls.greater_than("created_at", last_read_at),
            )
        try:
            rows = dependencies.list_rows_fn(
                dependencies.collections["chat_messages"],
                queries,
            ).get("rows", [])
        except dependencies.appwrite_exception:
            dependencies.error_logger.exception("Failed to count unread chat messages")
            return 0, False

        for row in rows:
            if row.get("deleted_at"):
                continue
            message_user_id = str(row.get("user_id") or "")
            if message_user_id == str(user_id):
                continue
            if message_user_id in blocked_user_ids:
                continue
            count += 1
            if count >= dependencies.unread_cap:
                return dependencies.unread_cap, True

        if len(rows) < dependencies.summary_scan_limit:
            return count, False
        offset += dependencies.summary_scan_limit
