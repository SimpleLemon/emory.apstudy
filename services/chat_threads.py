"""DM thread, block, and onboarding helpers.

The chat blueprint keeps adapters for these functions so its established
patch seams remain available to request and onboarding tests.  The service
receives persistence and side-effect callbacks instead of importing the
blueprint, which also keeps background onboarding imports lazy.
"""


def blocked_user_ids(
    user_id,
    *,
    list_rows_fn,
    query_cls,
    blocks_collection,
    appwrite_exception,
):
    try:
        rows = list_rows_fn(
            blocks_collection,
            [query_cls.equal("blocker_id", [user_id])],
        )
    except appwrite_exception:
        return set()
    return {row.get("blocked_id") for row in rows if row.get("blocked_id")}


def is_blocked_between(
    user_a,
    user_b,
    *,
    first_row_fn,
    query_cls,
    blocks_collection,
    appwrite_exception,
    error_logger,
):
    keys = [f"{user_a}:{user_b}", f"{user_b}:{user_a}"]
    try:
        return bool(
            first_row_fn(
                blocks_collection,
                [query_cls.equal("block_key", keys)],
            )
        )
    except appwrite_exception:
        error_logger.exception("Failed to check chat block")
        return True


def thread_key(user_a, user_b):
    return ":".join(sorted([str(user_a), str(user_b)]))


def get_or_create_thread_between(
    user_a,
    user_b,
    *,
    thread_key_fn,
    first_row_fn,
    query_cls,
    threads_collection,
    format_datetime_fn,
    now_fn,
    create_row_fn,
    id_unique_fn,
):
    key = thread_key_fn(user_a, user_b)
    existing = first_row_fn(
        threads_collection,
        [query_cls.equal("participant_key", [key])],
    )
    if existing:
        return existing
    now = format_datetime_fn(now_fn())
    participant_a, participant_b = key.split(":", 1)
    return create_row_fn(
        threads_collection,
        row_id=id_unique_fn(),
        data={
            "participant_a": participant_a,
            "participant_b": participant_b,
            "participant_key": key,
            "created_at": now,
            "updated_at": now,
        },
    )


def get_or_create_thread(
    other_user_id,
    *,
    current_user_id_fn,
    get_row_fn,
    users_collection,
    get_or_create_thread_between_fn,
):
    user_id = current_user_id_fn()
    if other_user_id == user_id:
        raise ValueError("You cannot start a DM with yourself.")
    other = get_row_fn(users_collection, other_user_id, allow_missing=True)
    if not other:
        raise ValueError("User not found.")
    return get_or_create_thread_between_fn(user_id, other_user_id)


def thread_for_user(
    thread_id,
    *,
    get_row_fn,
    threads_collection,
    current_user_id_fn,
):
    thread = get_row_fn(threads_collection, thread_id, allow_missing=True)
    if not thread:
        return None
    user_id = current_user_id_fn()
    if user_id not in {thread.get("participant_a"), thread.get("participant_b")}:
        return None
    return thread


def other_participant(
    thread,
    *,
    current_user_id_fn,
    get_row_fn,
    users_collection,
):
    user_id = current_user_id_fn()
    other_id = (
        thread.get("participant_b")
        if thread.get("participant_a") == user_id
        else thread.get("participant_a")
    )
    return get_row_fn(users_collection, other_id, allow_missing=True)


def thread_participant_ids(thread):
    return [
        str(value)
        for value in (thread.get("participant_a"), thread.get("participant_b"))
        if value
    ]


def thread_accessible_by_user(
    thread_id,
    user_id,
    *,
    get_row_fn,
    threads_collection,
):
    thread = get_row_fn(threads_collection, thread_id, allow_missing=True)
    if not thread:
        return False
    return user_id in {thread.get("participant_a"), thread.get("participant_b")}


def create_welcome_dm_for_user(
    user_id,
    *,
    welcome_sender_id,
    welcome_text,
    first_row_fn,
    query_cls,
    messages_collection,
    get_row_fn,
    users_collection,
    get_or_create_thread_between_fn,
    create_row_fn,
    id_unique_fn,
    update_row_fn,
    threads_collection,
    row_id_fn,
    now_fn,
    format_datetime_fn,
    render_markdown_fn,
    emit_chat_event_fn,
    thread_participant_ids_fn,
    appwrite_exception,
    error_logger,
):
    user_id = str(user_id or "").strip()
    if not user_id or user_id == welcome_sender_id:
        return None
    external_id = f"welcome:{welcome_sender_id}:{user_id}"
    try:
        existing = first_row_fn(
            messages_collection,
            [query_cls.equal("external_id", [external_id])],
        )
    except appwrite_exception:
        error_logger.exception("Failed to check welcome DM for user %s", user_id)
        return None
    if existing:
        return existing

    sender = get_row_fn(users_collection, welcome_sender_id, allow_missing=True)
    try:
        thread = get_or_create_thread_between_fn(welcome_sender_id, user_id)
    except appwrite_exception:
        error_logger.exception("Failed to create welcome DM thread for user %s", user_id)
        return None

    thread_id = row_id_fn(thread)
    now = format_datetime_fn(now_fn())
    content = welcome_text
    try:
        row = create_row_fn(
            messages_collection,
            row_id=id_unique_fn(),
            data={
                "thread_id": thread_id,
                "source": "system",
                "external_id": external_id,
                "user_id": welcome_sender_id,
                "author_name": (sender or {}).get("name") or (sender or {}).get("username") or "Nest User",
                "author_username": (sender or {}).get("username") or "",
                "author_avatar_url": (sender or {}).get("picture_url") or "",
                "content": content,
                "rendered_html": render_markdown_fn(content),
                "link_preview_json": "[]",
                "created_at": now,
                "updated_at": now,
            },
        )
        update_row_fn(
            threads_collection,
            thread_id,
            {"last_message_at": now, "updated_at": now},
        )
        emit_chat_event_fn(
            "thread",
            thread_id,
            "message_created",
            message_id=row_id_fn(row),
            thread_id=thread_id,
            actor_id=welcome_sender_id,
            readable_user_ids=thread_participant_ids_fn(thread),
        )
        return row
    except appwrite_exception:
        error_logger.exception("Failed to create welcome DM for user %s", user_id)
        return None
