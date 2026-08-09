"""Chat presence projections, access checks, and heartbeat writes."""

import re
import sqlite3


def presence_online_users(
    *,
    fresh_presence_rows_by_scope_fn,
    presence_online_limit,
    get_row_fn,
    users_collection,
    appwrite_exception,
    error_logger,
    public_user_fn,
    presence_status_from_scopes_fn,
    focus_user_ids_fn,
):
    rows = fresh_presence_rows_by_scope_fn(
        ["site", "chat", "typing_channel", "typing_thread"],
        limit=presence_online_limit * 8,
    )
    scopes_by_user = {}
    chat_scopes_by_user = {}
    typing_channels_by_user = {}
    typing_threads_by_user = {}
    latest_by_user = {}
    for row in rows:
        user_id = str(row.get("user_id") or "")
        if not user_id:
            continue
        scope_type = str(row.get("scope_type") or "")
        scope_id = str(row.get("scope_id") or "")
        if scope_type in {"site", "chat"}:
            scopes_by_user.setdefault(user_id, set()).add(scope_type)
        if scope_type == "chat" and scope_id and scope_id != "global":
            chat_scopes_by_user.setdefault(user_id, set()).add(scope_id)
        elif scope_type == "typing_channel" and scope_id:
            typing_channels_by_user.setdefault(user_id, set()).add(scope_id)
        elif scope_type == "typing_thread" and scope_id:
            typing_threads_by_user.setdefault(user_id, set()).add(scope_id)
        latest = row.get("last_seen_at") or ""
        if latest > latest_by_user.get(user_id, ""):
            latest_by_user[user_id] = latest
    try:
        focus_user_ids = focus_user_ids_fn()
        for user_id in focus_user_ids:
            scopes_by_user.setdefault(user_id, {"site"})
    except sqlite3.OperationalError:
        focus_user_ids = set()
    users = []
    for user_id, scopes in scopes_by_user.items():
        try:
            user = get_row_fn(users_collection, user_id, allow_missing=True)
        except appwrite_exception:
            error_logger.exception("Failed to resolve online user %s", user_id)
            continue
        public_user = public_user_fn(user)
        if not public_user:
            continue
        public_user["presence_status"] = (
            "focus"
            if user_id in focus_user_ids
            else presence_status_from_scopes_fn(scopes)
        )
        public_user["online"] = public_user["presence_status"] != "offline"
        public_user["last_seen_at"] = latest_by_user.get(user_id)
        public_user["active_chat_scopes"] = sorted(chat_scopes_by_user.get(user_id, set()))
        public_user["typing_channel_ids"] = sorted(typing_channels_by_user.get(user_id, set()))
        public_user["typing_thread_ids"] = sorted(typing_threads_by_user.get(user_id, set()))
        users.append(public_user)
    users.sort(key=lambda user: (user.get("presence_status") != "active", user.get("name") or ""))
    return users[:presence_online_limit]


def fresh_chat_room_presence(
    scope_type,
    scope_id,
    *,
    fresh_presence_rows_fn,
    presence_fresh_seconds_fn,
    get_row_fn,
    users_collection,
    appwrite_exception,
    error_logger,
    public_user_fn,
    presence_statuses_for_users_fn,
):
    rows = fresh_presence_rows_fn(
        [scope_type],
        seconds=presence_fresh_seconds_fn(scope_type),
        limit=1000,
    )
    users = []
    seen = set()
    for row in rows:
        if str(row.get("scope_id") or "") != str(scope_id or ""):
            continue
        user_id = str(row.get("user_id") or "")
        if not user_id or user_id in seen:
            continue
        seen.add(user_id)
        try:
            user = get_row_fn(users_collection, user_id, allow_missing=True)
        except appwrite_exception:
            error_logger.exception("Failed to resolve room presence user %s", user_id)
            continue
        public_user = public_user_fn(user)
        if public_user:
            public_user["presence_status"] = "active"
            public_user["online"] = True
            users.append(public_user)
    statuses = presence_statuses_for_users_fn([user["id"] for user in users])
    for user in users:
        user["presence_status"] = statuses.get(user["id"], "active")
    users.sort(key=lambda user: user.get("name") or "")
    return users


def fresh_typing_room_presence(
    scope_type,
    scope_id,
    *,
    fresh_presence_rows_fn,
    presence_fresh_seconds_fn,
    current_user_id_fn,
    get_row_fn,
    users_collection,
    appwrite_exception,
    error_logger,
    public_user_fn,
    presence_statuses_for_users_fn,
):
    rows = fresh_presence_rows_fn(
        [scope_type],
        seconds=presence_fresh_seconds_fn(scope_type),
        limit=1000,
    )
    users = []
    seen = set()
    typing_user_ids = []
    for row in rows:
        if str(row.get("scope_id") or "") != str(scope_id or ""):
            continue
        user_id = str(row.get("user_id") or "")
        if not user_id or user_id in seen or user_id == current_user_id_fn():
            continue
        seen.add(user_id)
        typing_user_ids.append(user_id)
        try:
            user = get_row_fn(users_collection, user_id, allow_missing=True)
        except appwrite_exception:
            error_logger.exception("Failed to resolve typing presence user %s", user_id)
            continue
        public_user = public_user_fn(user)
        if public_user:
            if scope_type == "typing_channel":
                public_user["typing_channel_ids"] = [str(scope_id)]
            else:
                public_user["typing_thread_ids"] = [str(scope_id)]
            users.append(public_user)
    statuses = presence_statuses_for_users_fn(typing_user_ids)
    for user in users:
        user["presence_status"] = statuses.get(user["id"], "offline")
        user["online"] = user["presence_status"] != "offline"
    users.sort(key=lambda user: user.get("name") or "")
    return users


def school_key_for_user_row(user, *, school_payload_fn):
    if not user:
        return ""
    return user.get("school_key") or school_payload_fn(user.get("school")).get("school_key") or ""


def user_can_access_channel_presence(
    channel,
    user,
    *,
    school_key_for_user_row_fn,
):
    if not channel or not user:
        return False
    if channel.get("kind") == "discord":
        return True
    if channel.get("kind") == "university":
        return bool(channel.get("approved")) and school_key_for_user_row_fn(user) == channel.get("school_key")
    return False


def online_users_for_channel(
    channel,
    *,
    fresh_presence_rows_by_scope_fn,
    presence_online_limit,
    get_row_fn,
    users_collection,
    appwrite_exception,
    error_logger,
    user_can_access_channel_presence_fn,
    public_user_fn,
    presence_status_from_scopes_fn,
):
    rows = fresh_presence_rows_by_scope_fn(
        ["chat", "site"],
        limit=presence_online_limit * 4,
    )
    scopes_by_user = {}
    latest_by_user = {}
    for row in rows:
        user_id = str(row.get("user_id") or "")
        if not user_id:
            continue
        scopes_by_user.setdefault(user_id, set()).add(row.get("scope_type"))
        latest = row.get("last_seen_at") or ""
        if latest > latest_by_user.get(user_id, ""):
            latest_by_user[user_id] = latest

    users = []
    for user_id, scopes in scopes_by_user.items():
        try:
            user = get_row_fn(users_collection, user_id, allow_missing=True)
        except appwrite_exception:
            error_logger.exception("Failed to resolve channel online user %s", user_id)
            continue
        if not user_can_access_channel_presence_fn(channel, user):
            continue
        public_user = public_user_fn(user)
        if not public_user:
            continue
        public_user["presence_status"] = presence_status_from_scopes_fn(scopes)
        public_user["online"] = public_user["presence_status"] != "offline"
        public_user["last_seen_at"] = latest_by_user.get(user_id)
        users.append(public_user)
    users.sort(key=lambda user: (user.get("presence_status") != "active", user.get("name") or ""))
    return users[:presence_online_limit]


def presence_scope_allowed(
    scope_type,
    scope_id,
    *,
    get_row_fn,
    channels_collection,
    can_access_channel_fn,
    thread_for_user_fn,
    other_participant_fn,
    is_blocked_between_fn,
    current_user_id_fn,
    row_id_fn,
):
    if scope_type == "site":
        return scope_id == "global"
    if scope_type == "chat":
        if scope_id == "global":
            return True
        channel = get_row_fn(channels_collection, scope_id, allow_missing=True)
        if can_access_channel_fn(channel):
            return True
        return bool(thread_for_user_fn(scope_id))
    if scope_type == "typing_channel":
        channel = get_row_fn(channels_collection, scope_id, allow_missing=True)
        return bool(can_access_channel_fn(channel) and not channel.get("read_only"))
    if scope_type == "typing_thread":
        thread = thread_for_user_fn(scope_id)
        if not thread:
            return False
        other = other_participant_fn(thread)
        return bool(other and not is_blocked_between_fn(current_user_id_fn(), row_id_fn(other)))
    return False


def upsert_presence(
    scope_type,
    scope_id,
    tab_id,
    *,
    current_user_id_fn,
    presence_scope_allowed_fn,
    now_fn,
    format_datetime_fn,
    presence_collection,
    query_cls,
    first_row_fn,
    update_row_fn,
    create_row_fn,
    id_unique_fn,
    row_id_fn,
):
    user_id = current_user_id_fn()
    scope_type = str(scope_type or "").strip()
    scope_id = str(scope_id or "").strip() or "global"
    tab_id = re.sub(r"[^A-Za-z0-9_-]", "", str(tab_id or "").strip())[:64] or "default"
    if scope_type not in {"site", "chat", "typing_channel", "typing_thread"}:
        raise ValueError("Unsupported presence scope.")
    if not presence_scope_allowed_fn(scope_type, scope_id):
        raise PermissionError("Presence scope unavailable.")
    now = format_datetime_fn(now_fn())
    presence_key = f"{user_id}:{scope_type}:{scope_id}:{tab_id}"
    payload = {
        "user_id": user_id,
        "scope_type": scope_type,
        "scope_id": scope_id,
        "presence_key": presence_key,
        "last_seen_at": now,
    }
    existing = first_row_fn(
        presence_collection,
        [query_cls.equal("presence_key", [presence_key])],
    )
    if existing:
        return update_row_fn(presence_collection, row_id_fn(existing), payload)
    return create_row_fn(
        presence_collection,
        row_id=id_unique_fn(),
        data=payload,
    )
