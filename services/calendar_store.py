import json
import os
import sqlite3
import uuid
from contextlib import contextmanager

from flask import current_app

from appwrite.exception import AppwriteException


CALENDAR_TABLES = (
    "calendar_cache",
    "calendar_feeds",
    "user_calendar_preferences",
    "user_events",
    "user_calendar_sources",
    "user_event_overrides",
    "calendar_shares",
)

TABLE_COLUMNS = {
    "calendar_cache": {
        "id", "user_id", "feed_url", "feed_url_hash", "event_uid", "event_title",
        "event_start", "event_end", "is_all_day", "event_type", "course_name",
        "raw_description", "fetched_at",
    },
    "calendar_feeds": {
        "id", "user_id", "feed_url", "feed_url_hash", "calendar_name", "etag_header",
        "last_modified_header", "last_fetch_http_code", "last_fetched", "created_at",
        "updated_at",
    },
    "user_calendar_preferences": {
        "id", "user_id", "calendar_name", "display_name", "color_hex", "visible",
        "created_at", "updated_at",
    },
    "user_events": {
        "id", "user_id", "title", "description", "start", "end", "is_all_day",
        "color", "calendar_id", "reminder_minutes", "created_at", "updated_at",
    },
    "user_calendar_sources": {
        "id", "user_id", "source_id", "kind", "default_name", "created_at",
        "updated_at",
    },
    "user_event_overrides": {
        "id", "user_id", "event_ref", "hidden", "title", "description", "start",
        "end", "is_all_day", "calendar_id", "color", "reminder_minutes", "created_at", "updated_at",
    },
    "calendar_shares": {
        "id", "user_id", "share_code", "is_active", "include_all_calendars",
        "calendar_ids_json", "date_scope", "fixed_start", "fixed_end", "rolling_days",
        "created_at", "updated_at",
    },
}

BOOLEAN_COLUMNS = {
    "calendar_cache": {"is_all_day"},
    "user_calendar_preferences": {"visible"},
    "user_events": {"is_all_day"},
    "user_event_overrides": {"hidden", "is_all_day"},
    "calendar_shares": {"is_active", "include_all_calendars"},
}

INTEGER_COLUMNS = {
    "calendar_feeds": {"last_fetch_http_code"},
    "user_events": {"reminder_minutes"},
    "user_event_overrides": {"reminder_minutes"},
    "calendar_shares": {"rolling_days"},
}

SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS calendar_cache (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        feed_url TEXT,
        feed_url_hash TEXT,
        event_uid TEXT,
        event_title TEXT,
        event_start TEXT,
        event_end TEXT,
        is_all_day INTEGER NOT NULL DEFAULT 0,
        event_type TEXT,
        course_name TEXT,
        raw_description TEXT,
        fetched_at TEXT,
        UNIQUE(user_id, feed_url_hash, event_uid)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS calendar_feeds (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        feed_url TEXT NOT NULL,
        feed_url_hash TEXT NOT NULL,
        calendar_name TEXT,
        etag_header TEXT,
        last_modified_header TEXT,
        last_fetch_http_code INTEGER,
        last_fetched TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT,
        UNIQUE(user_id, feed_url_hash)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS user_calendar_preferences (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        calendar_name TEXT NOT NULL,
        display_name TEXT,
        color_hex TEXT DEFAULT '#6366f1',
        visible INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        updated_at TEXT,
        UNIQUE(user_id, calendar_name)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS user_events (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        title TEXT NOT NULL,
        description TEXT,
        start TEXT NOT NULL,
        end TEXT NOT NULL,
        is_all_day INTEGER NOT NULL DEFAULT 0,
        color TEXT,
        calendar_id TEXT,
        reminder_minutes INTEGER,
        created_at TEXT NOT NULL,
        updated_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS user_calendar_sources (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        source_id TEXT NOT NULL,
        kind TEXT DEFAULT 'local',
        default_name TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT,
        UNIQUE(user_id, source_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS user_event_overrides (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        event_ref TEXT NOT NULL,
        hidden INTEGER NOT NULL DEFAULT 0,
        title TEXT,
        description TEXT,
        start TEXT,
        end TEXT,
        is_all_day INTEGER,
        calendar_id TEXT,
        color TEXT,
        reminder_minutes INTEGER,
        created_at TEXT NOT NULL,
        updated_at TEXT,
        UNIQUE(user_id, event_ref)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS calendar_shares (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        share_code TEXT NOT NULL UNIQUE,
        is_active INTEGER NOT NULL DEFAULT 1,
        include_all_calendars INTEGER NOT NULL DEFAULT 1,
        calendar_ids_json TEXT,
        date_scope TEXT NOT NULL DEFAULT 'all',
        fixed_start TEXT,
        fixed_end TEXT,
        rolling_days INTEGER,
        created_at TEXT NOT NULL,
        updated_at TEXT
    )
    """,
)

INDEX_STATEMENTS = (
    "CREATE INDEX IF NOT EXISTS idx_calendar_cache_user_id ON calendar_cache(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_calendar_cache_feed_hash ON calendar_cache(feed_url_hash)",
    "CREATE INDEX IF NOT EXISTS idx_calendar_cache_user_feed ON calendar_cache(user_id, feed_url_hash)",
    "CREATE INDEX IF NOT EXISTS idx_calendar_cache_event_start ON calendar_cache(event_start)",
    "CREATE INDEX IF NOT EXISTS idx_calendar_cache_fetched_at ON calendar_cache(fetched_at)",
    "CREATE INDEX IF NOT EXISTS idx_calendar_feeds_user_id ON calendar_feeds(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_calendar_feeds_feed_hash ON calendar_feeds(feed_url_hash)",
    "CREATE INDEX IF NOT EXISTS idx_calendar_feeds_last_fetched ON calendar_feeds(last_fetched)",
    "CREATE INDEX IF NOT EXISTS idx_user_calendar_prefs_user_id ON user_calendar_preferences(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_user_calendar_prefs_name ON user_calendar_preferences(calendar_name)",
    "CREATE INDEX IF NOT EXISTS idx_user_events_user_id ON user_events(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_user_events_start ON user_events(start)",
    "CREATE INDEX IF NOT EXISTS idx_user_events_calendar ON user_events(calendar_id)",
    "CREATE INDEX IF NOT EXISTS idx_user_calendar_sources_user ON user_calendar_sources(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_user_calendar_sources_source ON user_calendar_sources(source_id)",
    "CREATE INDEX IF NOT EXISTS idx_user_event_overrides_user ON user_event_overrides(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_user_event_overrides_ref ON user_event_overrides(event_ref)",
    "CREATE INDEX IF NOT EXISTS idx_user_event_overrides_calendar ON user_event_overrides(calendar_id)",
    "CREATE INDEX IF NOT EXISTS idx_calendar_shares_user ON calendar_shares(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_calendar_shares_code ON calendar_shares(share_code)",
    "CREATE INDEX IF NOT EXISTS idx_calendar_shares_active ON calendar_shares(is_active)",
)


def calendar_db_path(path=None):
    if path:
        return path
    configured = os.environ.get("CALENDAR_SQLITE_PATH")
    if configured:
        return configured
    try:
        instance_path = current_app.instance_path
    except RuntimeError:
        instance_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "instance")
    return os.path.join(instance_path, "calendar.sqlite3")


def _validate_table(table_id):
    if table_id not in TABLE_COLUMNS:
        raise ValueError(f"Unsupported calendar table: {table_id}")


def _validate_column(table_id, column):
    if column not in TABLE_COLUMNS[table_id]:
        raise ValueError(f"Unsupported column for {table_id}: {column}")


def _connect(path=None):
    db_path = calendar_db_path(path)
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


@contextmanager
def calendar_connection(path=None):
    conn = _connect(path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_calendar_store(path=None):
    with calendar_connection(path) as conn:
        for statement in SCHEMA_STATEMENTS:
            conn.execute(statement)
        for statement in INDEX_STATEMENTS:
            conn.execute(statement)


def _query_dict(query):
    if isinstance(query, dict):
        return query
    if isinstance(query, str):
        return json.loads(query)
    raise ValueError(f"Unsupported query format: {query!r}")


def _normalize_value(table_id, column, value):
    if column in BOOLEAN_COLUMNS.get(table_id, set()):
        if value is None and table_id == "user_event_overrides" and column == "is_all_day":
            return None
        return 1 if bool(value) else 0
    if column in INTEGER_COLUMNS.get(table_id, set()):
        return int(value) if value is not None else None
    if value is None:
        return None
    return str(value)


def _row_to_dict(table_id, row):
    row_id = row["id"]
    data = dict(row)
    for column in BOOLEAN_COLUMNS.get(table_id, set()):
        if column in data and data[column] is not None:
            data[column] = bool(data[column])
    data["$id"] = row_id
    return data


def _parse_queries(table_id, queries):
    where = []
    params = []
    order = []
    limit = None
    offset = None

    for raw_query in queries or []:
        query = _query_dict(raw_query)
        method = query.get("method")
        attribute = query.get("attribute")
        values = query.get("values") or []

        if method == "equal":
            _validate_column(table_id, attribute)
            placeholders = ", ".join("?" for _ in values)
            where.append(f"{attribute} IN ({placeholders})")
            params.extend(_normalize_value(table_id, attribute, value) for value in values)
        elif method == "lessThan":
            _validate_column(table_id, attribute)
            where.append(f"{attribute} < ?")
            params.append(_normalize_value(table_id, attribute, values[0] if values else None))
        elif method == "greaterThan":
            _validate_column(table_id, attribute)
            where.append(f"{attribute} > ?")
            params.append(_normalize_value(table_id, attribute, values[0] if values else None))
        elif method == "orderAsc":
            _validate_column(table_id, attribute)
            order.append(f"{attribute} ASC")
        elif method == "orderDesc":
            _validate_column(table_id, attribute)
            order.append(f"{attribute} DESC")
        elif method == "limit":
            limit = int(values[0])
        elif method == "offset":
            offset = int(values[0])
        else:
            raise ValueError(f"Unsupported calendar query method: {method}")

    return where, params, order, limit, offset


def list_calendar_rows_safe(table_id, queries=None):
    _validate_table(table_id)
    where, params, order, limit, offset = _parse_queries(table_id, queries)
    sql = f"SELECT * FROM {table_id}"
    count_sql = f"SELECT COUNT(*) AS total FROM {table_id}"
    if where:
        clause = " WHERE " + " AND ".join(where)
        sql += clause
        count_sql += clause
    if order:
        sql += " ORDER BY " + ", ".join(order)
    if limit is not None:
        sql += " LIMIT ?"
        params_for_rows = params + [limit]
    else:
        params_for_rows = list(params)
    if offset is not None:
        if limit is None:
            sql += " LIMIT -1"
        sql += " OFFSET ?"
        params_for_rows.append(offset)

    try:
        with calendar_connection() as conn:
            total = conn.execute(count_sql, params).fetchone()["total"]
            rows = conn.execute(sql, params_for_rows).fetchall()
    except sqlite3.Error as exc:
        raise AppwriteException(str(exc)) from exc

    return {
        "total": total,
        "rows": [_row_to_dict(table_id, row) for row in rows],
    }


def list_calendar_rows_all(table_id, queries=None, limit=100):
    response = list_calendar_rows_safe(table_id, queries)
    return response.get("rows", [])


def first_calendar_row(table_id, queries=None):
    query_list = list(queries or [])
    query_list.append(json.dumps({"method": "limit", "values": [1]}))
    rows = list_calendar_rows_all(table_id, query_list)
    return rows[0] if rows else None


def get_calendar_row(table_id, row_id, *, allow_missing=False):
    _validate_table(table_id)
    try:
        with calendar_connection() as conn:
            row = conn.execute(f"SELECT * FROM {table_id} WHERE id = ?", [str(row_id)]).fetchone()
    except sqlite3.Error as exc:
        raise AppwriteException(str(exc)) from exc
    if row:
        return _row_to_dict(table_id, row)
    if allow_missing:
        return None
    raise AppwriteException("Calendar row not found", 404)


def _clean_payload(table_id, data):
    cleaned = {}
    for key, value in (data or {}).items():
        column = "id" if key == "$id" else key
        if column == "id":
            continue
        if isinstance(key, str) and key.startswith("$"):
            continue
        _validate_column(table_id, column)
        cleaned[column] = _normalize_value(table_id, column, value)
    return cleaned


def create_calendar_row(table_id, row_id=None, data=None):
    _validate_table(table_id)
    row_id = str(row_id or uuid.uuid4())
    payload = {"id": row_id, **_clean_payload(table_id, data)}
    columns = list(payload.keys())
    placeholders = ", ".join("?" for _ in columns)
    sql = f"INSERT INTO {table_id} ({', '.join(columns)}) VALUES ({placeholders})"
    try:
        with calendar_connection() as conn:
            conn.execute(sql, [payload[column] for column in columns])
    except sqlite3.Error as exc:
        raise AppwriteException(str(exc)) from exc
    return get_calendar_row(table_id, row_id)


def upsert_calendar_row(table_id, row_id=None, data=None):
    _validate_table(table_id)
    row_id = str(row_id or uuid.uuid4())
    payload = {"id": row_id, **_clean_payload(table_id, data)}
    columns = list(payload.keys())
    placeholders = ", ".join("?" for _ in columns)
    updates = ", ".join(f"{column} = excluded.{column}" for column in columns if column != "id")
    sql = (
        f"INSERT INTO {table_id} ({', '.join(columns)}) VALUES ({placeholders}) "
        f"ON CONFLICT(id) DO UPDATE SET {updates}"
    )
    try:
        with calendar_connection() as conn:
            conn.execute(sql, [payload[column] for column in columns])
    except sqlite3.Error as exc:
        raise AppwriteException(str(exc)) from exc
    return get_calendar_row(table_id, row_id)


def update_calendar_row(table_id, row_id, data=None):
    _validate_table(table_id)
    payload = _clean_payload(table_id, data)
    if not payload:
        return get_calendar_row(table_id, row_id)
    assignments = ", ".join(f"{column} = ?" for column in payload)
    params = list(payload.values()) + [str(row_id)]
    try:
        with calendar_connection() as conn:
            cursor = conn.execute(f"UPDATE {table_id} SET {assignments} WHERE id = ?", params)
            if cursor.rowcount == 0:
                raise AppwriteException("Calendar row not found", 404)
    except sqlite3.Error as exc:
        raise AppwriteException(str(exc)) from exc
    return get_calendar_row(table_id, row_id)


def delete_calendar_row(table_id, row_id):
    _validate_table(table_id)
    try:
        with calendar_connection() as conn:
            conn.execute(f"DELETE FROM {table_id} WHERE id = ?", [str(row_id)])
    except sqlite3.Error as exc:
        raise AppwriteException(str(exc)) from exc


def count_calendar_rows(table_id, queries=None):
    return list_calendar_rows_safe(table_id, queries).get("total", 0)


def delete_calendar_rows_by_user(user_id):
    counts = {}
    try:
        with calendar_connection() as conn:
            for table_id in CALENDAR_TABLES:
                cursor = conn.execute(f"DELETE FROM {table_id} WHERE user_id = ?", [str(user_id)])
                counts[table_id] = cursor.rowcount
    except sqlite3.Error as exc:
        raise AppwriteException(str(exc)) from exc
    return counts


# Shared nest.sqlite3-backed implementations. These override the legacy
# calendar.sqlite3 helpers above while preserving the public calendar_store API.
from services import database as _nest_database


def calendar_db_path(path=None):
    if path:
        return _nest_database.resolve_env_path(path) or path
    legacy = os.environ.get("CALENDAR_SQLITE_PATH") or os.environ.get("CALENDAR_DB_PATH")
    if legacy:
        resolved = _nest_database.resolve_env_path(legacy)
        if resolved:
            return resolved
    return _nest_database.database_path()


@contextmanager
def calendar_connection(path=None):
    with _nest_database.db_connection(calendar_db_path(path)) as conn:
        yield conn


def init_calendar_store(path=None):
    _nest_database.init_db(path=calendar_db_path(path))


def list_calendar_rows_safe(table_id, queries=None):
    _validate_table(table_id)
    return _nest_database.list_rows(table_id, queries, path=calendar_db_path())


def list_calendar_rows_all(table_id, queries=None, limit=100):
    _validate_table(table_id)
    return _nest_database.list_rows_all(table_id, queries, limit=limit, path=calendar_db_path())


def first_calendar_row(table_id, queries=None):
    _validate_table(table_id)
    return _nest_database.first_row(table_id, queries, path=calendar_db_path())


def get_calendar_row(table_id, row_id, *, allow_missing=False):
    _validate_table(table_id)
    return _nest_database.get_row(table_id, row_id, allow_missing=allow_missing, path=calendar_db_path())


def create_calendar_row(table_id, row_id=None, data=None):
    _validate_table(table_id)
    return _nest_database.create_row(table_id, row_id=row_id, data=data, path=calendar_db_path())


def upsert_calendar_row(table_id, row_id=None, data=None):
    _validate_table(table_id)
    return _nest_database.upsert_row(table_id, row_id=row_id, data=data, path=calendar_db_path())


def update_calendar_row(table_id, row_id, data=None):
    _validate_table(table_id)
    return _nest_database.update_row(table_id, row_id, data=data, path=calendar_db_path())


def delete_calendar_row(table_id, row_id):
    _validate_table(table_id)
    return _nest_database.delete_row(table_id, row_id, path=calendar_db_path())


def count_calendar_rows(table_id, queries=None):
    _validate_table(table_id)
    return _nest_database.count_rows(table_id, queries, path=calendar_db_path())


def delete_calendar_rows_by_user(user_id):
    return _nest_database.delete_rows_by_user(CALENDAR_TABLES, user_id, path=calendar_db_path())
