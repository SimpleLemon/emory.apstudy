"""Chat bootstrap, room projection, and summary payload helpers.

The chat blueprint supplies persistence, authorization, presence, and profile
callbacks.  Keeping those callbacks at the boundary preserves the existing
``blueprints.chat_api`` patch seams while making the payload assembly directly
testable without Flask request machinery.
"""


def university_placeholder_channel(
    school_key,
    school_name,
    status,
    *,
    channel_id_fn,
    now_fn,
    format_datetime_fn,
):
    if not school_key or not school_name:
        return None
    return {
        "$id": channel_id_fn(school_key),
        "kind": "university",
        "name": school_name,
        "label": school_name,
        "section": "nest",
        "school_key": school_key,
        "school_name": school_name,
        "read_only": True,
        "approved": False,
        "university_status": status,
        "created_at": format_datetime_fn(now_fn()),
        "updated_at": format_datetime_fn(now_fn()),
    }


def ensure_university_request(
    current_user,
    *,
    school_payload_fn,
    current_user_id_fn,
    find_university_channel_fn,
    first_row_fn,
    query_cls,
    collections,
    create_university_channel_fn,
    placeholder_channel_fn,
    create_row_fn,
    id_unique_fn,
    now_fn,
    format_datetime_fn,
    appwrite_exception,
    error_logger,
):
    school = school_payload_fn(current_user.school)
    school_key = school.get("school_key") or getattr(current_user, "school_key", None)
    school_name = school.get("school") or current_user.school
    if not school_key or not school_name:
        return {"status": "none", "channel": None, "request": None}

    try:
        channel = find_university_channel_fn(school_key)
        if channel:
            return {"status": "approved", "channel": channel, "request": None}

        request_row = first_row_fn(
            collections["admin_requests"],
            [
                query_cls.equal("request_type", ["uni_channel_approval"]),
                query_cls.equal("school_key", [school_key]),
            ],
        )
        if request_row and request_row.get("status") == "approved":
            channel = create_university_channel_fn(school_key, school_name)
            return {"status": "approved", "channel": channel, "request": request_row}
        if request_row:
            status = request_row.get("status") or "pending"
            return {
                "status": status,
                "channel": placeholder_channel_fn(school_key, school_name, status),
                "request": request_row,
            }

        now = format_datetime_fn(now_fn())
        request_row = create_row_fn(
            collections["admin_requests"],
            row_id=id_unique_fn(),
            data={
                "request_type": "uni_channel_approval",
                "label": "[Uni Channel Approval]",
                "status": "pending",
                "school_key": school_key,
                "school_name": school_name,
                "requested_by": current_user_id_fn(),
                "request_count": 1,
                "last_requested_at": now,
                "created_at": now,
                "updated_at": now,
            },
        )
        return {
            "status": "pending",
            "channel": placeholder_channel_fn(school_key, school_name, "pending"),
            "request": request_row,
        }
    except appwrite_exception:
        error_logger.exception("Failed to ensure university request")
        return {"status": "error", "channel": None, "request": None}


def channel_payload(
    channel,
    university_status=None,
    *,
    row_id_fn,
    online_users_for_channel_fn,
    presence_scope_fn,
    presence_read_permissions_for_channel_fn,
):
    if not channel:
        return None
    channel_id = row_id_fn(channel)
    online_users = online_users_for_channel_fn(channel)
    return {
        "id": channel_id,
        "kind": channel.get("kind"),
        "name": channel.get("name"),
        "label": channel.get("label") or channel.get("name"),
        "school_key": channel.get("school_key"),
        "school_name": channel.get("school_name"),
        "read_only": bool(channel.get("read_only")),
        "approved": bool(channel.get("approved")),
        "active_count": len(online_users),
        "active_users": online_users,
        "online_count": len(online_users),
        "online_users": online_users,
        "history_limited": channel.get("kind") == "discord",
        "university_status": university_status or channel.get("university_status"),
        "presence_scope": presence_scope_fn("channel", channel_id),
        "presence_read_permissions": presence_read_permissions_for_channel_fn(channel),
        "presence_profile_resolve_allowed": bool(
            channel.get("approved") or channel.get("kind") == "discord"
        ),
    }


def thread_payload(
    thread,
    *,
    other_participant_fn,
    public_user_fn,
    presence_statuses_for_users_fn,
    current_user_id_fn,
    row_id_fn,
    fresh_chat_room_presence_fn,
    is_blocked_between_fn,
    presence_scope_fn,
    presence_read_permissions_for_thread_fn,
):
    other = public_user_fn(other_participant_fn(thread))
    if not other:
        return None
    status = presence_statuses_for_users_fn([other["id"]]).get(other["id"], "offline")
    other["online"] = status != "offline"
    other["presence_status"] = status
    thread_id = row_id_fn(thread)
    active_users = fresh_chat_room_presence_fn("chat", thread_id)
    return {
        "id": thread_id,
        "other_user": other,
        "last_message_at": thread.get("last_message_at")
        or thread.get("updated_at")
        or thread.get("created_at"),
        "blocked": is_blocked_between_fn(current_user_id_fn(), other["id"]),
        "active_count": len(active_users),
        "presence_status": status,
        "presence_scope": presence_scope_fn("thread", thread_id),
        "presence_read_permissions": presence_read_permissions_for_thread_fn(thread),
        "presence_profile_resolve_allowed": True,
    }


def existing_visible_channels_for_summary(
    *,
    default_channels_fn,
    list_rows_all_fn,
    channels_collection,
    query_cls,
    can_access_channel_fn,
    appwrite_exception,
    error_logger,
):
    default_channels_fn()
    try:
        rows = list_rows_all_fn(
            channels_collection,
            [query_cls.equal("kind", ["discord", "university"])],
        )
    except appwrite_exception:
        error_logger.exception("Failed to list chat summary channels")
        return []
    return [row for row in rows if can_access_channel_fn(row)]


def assemble_bootstrap_payload(
    *,
    current_user,
    channels,
    university,
    dm_threads,
    entitlements,
    current_user_payload_fn,
    settings_payload_fn,
    channel_payload_fn,
    sync_environment_config_fn,
    attachments_enabled_fn,
    max_attachments_per_message,
    giphy_available_fn,
    giphy_api_key_fn,
):
    return {
        "user": current_user_payload_fn(),
        "settings": settings_payload_fn(),
        "sections": {
            "nest": [
                channel_payload_fn(
                    channel,
                    university.get("status")
                    if channel == university.get("channel")
                    else None,
                )
                for channel in channels
            ],
            "direct_messages": dm_threads,
        },
        "university": {
            "status": university.get("status"),
            "school": current_user.school,
            "school_key": getattr(current_user, "school_key", None),
        },
        "discord_invite_url": sync_environment_config_fn().discord_invite_url,
        "capabilities": {
            "attachments": attachments_enabled_fn(),
            "max_attachment_size_bytes": entitlements["limits"].get(
                "max_chat_attachment_size_bytes"
            ),
            "max_attachments_per_message": max_attachments_per_message,
            "giphy": {
                "available": giphy_available_fn(),
                "api_key": giphy_api_key_fn() if giphy_available_fn() else "",
                "rating": "pg",
            },
        },
    }


def assemble_chat_summary_payload(
    user_id,
    channels,
    *,
    threads_fn,
    row_id_fn,
    read_state_for_scope_fn,
    unread_count_fn,
    unread_cap,
):
    rooms = []
    total_unread = 0
    has_capped_room = False

    for channel in channels:
        channel_id = row_id_fn(channel)
        read_state = read_state_for_scope_fn(user_id, "channel", channel_id)
        unread, capped = unread_count_fn(
            "channel",
            channel_id,
            user_id,
            (read_state or {}).get("last_read_at"),
        )
        total_unread += unread
        has_capped_room = has_capped_room or capped
        rooms.append(
            {
                "type": "channel",
                "id": channel_id,
                "label": channel.get("label") or channel.get("name") or "Chat",
                "unread_count": min(unread, unread_cap),
                "has_unread": unread > 0,
            }
        )

    for thread in threads_fn():
        thread_id = row_id_fn(thread)
        read_state = read_state_for_scope_fn(user_id, "thread", thread_id)
        unread, capped = unread_count_fn(
            "thread",
            thread_id,
            user_id,
            (read_state or {}).get("last_read_at"),
        )
        total_unread += unread
        has_capped_room = has_capped_room or capped
        rooms.append(
            {
                "type": "thread",
                "id": thread_id,
                "unread_count": min(unread, unread_cap),
                "has_unread": unread > 0,
            }
        )

    return {
        "total_unread": min(total_unread, unread_cap),
        "unread_capped": total_unread >= unread_cap or has_capped_room,
        "has_unread": total_unread > 0,
        "rooms": rooms,
    }
