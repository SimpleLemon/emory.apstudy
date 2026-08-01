"""Runtime chat presence queries and status aggregation."""

import sqlite3
from datetime import timedelta


def presence_cutoff(seconds, *, now_fn, format_datetime_fn):
    return format_datetime_fn(now_fn() - timedelta(seconds=seconds))


def presence_fresh_seconds(
    scope_type,
    *,
    chat_fresh_seconds,
    site_fresh_seconds,
    typing_fresh_seconds,
):
    scope = str(scope_type or "")
    if scope == "site":
        return site_fresh_seconds
    if scope in {"typing_channel", "typing_thread"}:
        return typing_fresh_seconds
    return chat_fresh_seconds


def presence_status_from_scopes(scopes):
    values = {str(scope or "") for scope in scopes}
    if "chat" in values:
        return "active"
    if "site" in values:
        return "busy"
    return "offline"


def fresh_presence_rows(
    scope_types=None,
    *,
    user_ids=None,
    seconds,
    limit=1000,
    cutoff_fn,
    query_cls,
    list_rows_fn,
    presence_collection,
    appwrite_exception,
    error_logger,
):
    queries = [
        query_cls.greater_than_equal("last_seen_at", cutoff_fn(seconds)),
        query_cls.order_desc("last_seen_at"),
        query_cls.limit(limit),
    ]
    if scope_types:
        queries.insert(0, query_cls.equal("scope_type", [str(value) for value in scope_types if value]))
    if user_ids:
        queries.insert(0, query_cls.equal("user_id", [str(value) for value in user_ids if value]))
    try:
        return list_rows_fn(presence_collection, queries).get("rows", [])
    except appwrite_exception:
        error_logger.exception("Failed to list fresh presence rows")
        return []


def fresh_presence_rows_by_scope(
    scope_types,
    *,
    user_ids=None,
    limit=1000,
    fresh_presence_rows_fn,
    presence_fresh_seconds_fn,
    row_id_fn,
):
    rows = []
    seen = set()
    for scope_type in scope_types or []:
        scope = str(scope_type or "").strip()
        if not scope:
            continue
        scoped_rows = fresh_presence_rows_fn(
            [scope],
            user_ids=user_ids,
            seconds=presence_fresh_seconds_fn(scope),
            limit=limit,
        )
        for row in scoped_rows:
            key = row_id_fn(row) or row.get("presence_key") or (
                row.get("user_id"),
                row.get("scope_type"),
                row.get("scope_id"),
                row.get("last_seen_at"),
            )
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)
    rows.sort(key=lambda row: row.get("last_seen_at") or "", reverse=True)
    return rows[:limit]


def presence_statuses_for_users(
    user_ids,
    *,
    lookup_limit,
    fresh_presence_rows_by_scope_fn,
    presence_status_from_scopes_fn,
):
    ordered_ids = []
    for value in user_ids or []:
        user_id = str(value or "").strip()
        if user_id and user_id not in ordered_ids:
            ordered_ids.append(user_id)
        if len(ordered_ids) >= lookup_limit:
            break
    statuses = {user_id: "offline" for user_id in ordered_ids}
    if not ordered_ids:
        return statuses
    scopes_by_user = {user_id: set() for user_id in ordered_ids}
    rows = fresh_presence_rows_by_scope_fn(
        ["chat", "site"],
        user_ids=ordered_ids,
        limit=max(len(ordered_ids) * 4, 20),
    )
    for row in rows:
        user_id = str(row.get("user_id") or "")
        if user_id in scopes_by_user:
            scopes_by_user[user_id].add(row.get("scope_type"))
    for user_id, scopes in scopes_by_user.items():
        statuses[user_id] = presence_status_from_scopes_fn(scopes)
    try:
        from services.focus_mode import active_focus_user_ids

        for user_id in active_focus_user_ids(ordered_ids):
            statuses[user_id] = "focus"
    except sqlite3.OperationalError:
        pass
    return statuses
