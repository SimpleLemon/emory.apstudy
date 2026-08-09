"""Stateless helpers for reading and projecting chat events.

The chat blueprint supplies persistence, authorization, and identity callbacks.
Keeping those dependencies at the boundary makes event queries directly
testable without moving the SSE listener lifecycle out of the blueprint.
"""


def event_visible_for_user(
    event,
    *,
    current_user_fn,
    current_user_id_fn,
    get_row_fn,
    channels_collection,
    can_access_channel_fn,
    thread_accessible_by_user_fn,
    school_payload_fn,
):
    scope_type = (event or {}).get("scope_type")
    scope_id = (event or {}).get("scope_id")
    if not scope_type or not scope_id:
        return False

    user_id = current_user_id_fn()
    if scope_type == "channel":
        channel = get_row_fn(channels_collection, scope_id, allow_missing=True)
        return can_access_channel_fn(channel)
    if scope_type == "thread":
        return thread_accessible_by_user_fn(scope_id, user_id)
    if scope_type == "university":
        current_user = current_user_fn()
        school = school_payload_fn(current_user.school)
        user_school_key = school.get("school_key") or getattr(current_user, "school_key", None)
        return bool(user_school_key) and user_school_key == scope_id
    return False


def serialize_chat_event(row, *, row_id_fn):
    event_id = row_id_fn(row)
    return {
        "$id": event_id,
        "id": event_id,
        "scope_type": row.get("scope_type"),
        "scope_id": row.get("scope_id"),
        "event_type": row.get("event_type"),
        "message_id": row.get("message_id"),
        "thread_id": row.get("thread_id"),
        "channel_id": row.get("channel_id"),
        "actor_id": row.get("actor_id"),
        "created_at": row.get("created_at"),
    }


def list_chat_events_after(
    since=None,
    after_id=None,
    *,
    limit,
    query_cls,
    list_rows_fn,
    events_collection,
    appwrite_exception,
    error_logger,
    event_visible_for_user_fn,
    row_id_fn,
):
    queries = [query_cls.order_asc("created_at"), query_cls.order_asc("$id"), query_cls.limit(limit)]
    if since:
        queries.insert(0, query_cls.greater_than_equal("created_at", since))
    try:
        rows = list_rows_fn(events_collection, queries).get("rows", [])
    except appwrite_exception:
        error_logger.exception("Failed to list chat events")
        return []

    visible = []
    for row in rows:
        row_id = row_id_fn(row)
        if since and after_id and row.get("created_at") == since and row_id == after_id:
            continue
        if not event_visible_for_user_fn(row):
            continue
        visible.append(row)
    return visible
