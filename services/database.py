import json
import logging
import os
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

from appwrite.exception import AppwriteException
from flask import current_app, g, has_app_context


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRODUCTION_DATABASE_PATH = "/var/www/nest.apstudy.org/instance/nest.sqlite3"
LOCAL_INSTANCE_ONLY = "APSTUDY_FORCE_LOCAL_INSTANCE_DB"
BASELINE_MIGRATION = "001_initial_schema.sql"
DEFAULT_LIMIT = 100
UNIQUE_SENTINELS = {"unique()"}
IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

BOOLEAN_COLUMNS = {
    "users": {"onboarding_complete", "emory_student"},
    "user_settings": {
        "email_notifications",
        "product_updates",
        "task_sound_enabled",
        "chat_sound_enabled",
    },
    "course_seat_tracks": {"enabled"},
    "course_section_live_snapshots": {"is_cancelled"},
    "calendar_cache": {"is_all_day"},
    "user_calendar_preferences": {"visible"},
    "user_events": {"is_all_day"},
    "user_event_overrides": {"hidden", "is_all_day"},
    "calendar_shares": {"is_active", "include_all_calendars"},
    "task_lists": {"collapsed", "hidden"},
    "tasks": {"completed", "starred"},
    "shared_files": {"is_public"},
    "file_folders": {"is_public"},
    "notes": {"is_pinned", "is_archived", "collaboration_enabled"},
    "chat_channels": {"read_only", "approved"},
    "user_notifications": {"is_read"},
    "notification_preferences": {
        "push_enabled", "calendar_enabled", "course_push_enabled", "course_email_enabled",
        "dm_enabled", "mention_enabled", "message_preview_enabled", "prompt_dismissed",
    },
    "user_invites": {"is_active"},
    "user_invite_attributions": {"is_anonymized"},
}


def utcnow_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def resolve_env_path(path_value):
    if not path_value:
        return None
    expanded = os.path.expanduser(str(path_value).strip())
    if not expanded:
        return None
    if os.path.isabs(expanded):
        return os.path.normpath(expanded)
    return os.path.normpath(os.path.join(BASE_DIR, expanded))


def database_path(path=None):
    if path:
        return resolve_env_path(path) or path

    if has_app_context():
        configured = resolve_env_path(current_app.config.get("DATABASE_PATH"))
        if configured:
            return configured

    configured = resolve_env_path(os.environ.get("DATABASE_PATH") or os.environ.get("NEST_DATABASE_PATH"))
    if configured:
        return configured

    if os.environ.get(LOCAL_INSTANCE_ONLY) == "1" and os.environ.get("FLASK_ENV") != "production":
        return os.path.join(BASE_DIR, "instance", "nest.sqlite3")

    if os.environ.get("FLASK_ENV") == "production":
        return PRODUCTION_DATABASE_PATH
    return os.path.join(BASE_DIR, "instance", "nest.sqlite3")


def nest_instance_dir():
    configured = resolve_env_path(os.environ.get("NEST_INSTANCE_DIR"))
    if configured:
        return configured
    return os.path.dirname(database_path())


def migrations_path():
    return os.path.join(BASE_DIR, "migrations")


def _migration_filenames():
    """Return migrations in upgrade order, with the historical baseline first."""
    filenames = sorted(
        filename
        for filename in os.listdir(migrations_path())
        if filename.endswith(".sql")
    )
    if BASELINE_MIGRATION in filenames:
        filenames.remove(BASELINE_MIGRATION)
        filenames.insert(0, BASELINE_MIGRATION)
    return filenames


def _migration_statements(sql):
    """Yield complete SQL statements without splitting quoted or trigger bodies."""
    start = 0
    for index, character in enumerate(sql):
        if character != ";":
            continue
        candidate = sql[start : index + 1]
        if sqlite3.complete_statement(candidate):
            yield candidate
            start = index + 1

    remainder = sql[start:]
    if remainder.strip():
        yield remainder


def _leading_sql_keyword(statement):
    """Return the first SQL keyword after leading whitespace and comments."""
    remaining = statement
    while True:
        remaining = remaining.lstrip()
        if remaining.startswith("--"):
            newline = remaining.find("\n")
            if newline == -1:
                return None
            remaining = remaining[newline + 1 :]
            continue
        if remaining.startswith("/*"):
            comment_end = remaining.find("*/", 2)
            if comment_end == -1:
                return None
            remaining = remaining[comment_end + 2 :]
            continue
        match = re.match(r"[A-Za-z]+", remaining)
        return match.group(0).upper() if match else None


def _validate_migration_sql(sql, filename):
    """Reject transaction control that could escape the runner-managed transaction."""
    transaction_keywords = {"BEGIN", "COMMIT", "END", "ROLLBACK", "SAVEPOINT", "RELEASE"}
    for statement in _migration_statements(sql):
        keyword = _leading_sql_keyword(statement)
        if keyword in transaction_keywords:
            raise sqlite3.OperationalError(
                f"Migration {filename} contains forbidden transaction statement {keyword}"
            )


def _apply_migration(conn, filename):
    """Apply and record one migration in the same SQLite transaction."""
    version = filename[:-4]
    with open(os.path.join(migrations_path(), filename), "r", encoding="utf-8") as handle:
        sql = handle.read()

    _validate_migration_sql(sql, filename)

    try:
        # Acquire the write lock before checking the marker again. Multiple
        # Gunicorn workers can initialize concurrently during a deploy; a
        # worker that waited for another worker's migration must not replay it.
        conn.execute("BEGIN IMMEDIATE")
        applied = conn.execute(
            "SELECT 1 FROM schema_migrations WHERE version = ?",
            [version],
        ).fetchone()
        if applied:
            conn.commit()
            return

        # executescript commits pending transactions, so execute the already
        # validated complete statements individually inside our lock.
        for statement in _migration_statements(sql):
            conn.execute(statement)
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, ?)",
            [version, utcnow_iso()],
        )
        conn.commit()
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise


def _quote_identifier(identifier):
    if not isinstance(identifier, str) or not IDENTIFIER_RE.match(identifier):
        raise ValueError(f"Invalid SQL identifier: {identifier!r}")
    return f'"{identifier}"'


def connect(path=None):
    db_path = database_path(path)
    if db_path != ":memory:":
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def get_db():
    if not has_app_context():
        return connect()
    db = g.get("nest_db")
    if db is None:
        db = g.nest_db = connect(current_app.config.get("DATABASE_PATH"))
    return db


def close_db(error=None):
    if not has_app_context():
        return
    db = g.pop("nest_db", None)
    if db is not None:
        db.close()


@contextmanager
def db_connection(path=None):
    conn = connect(path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(app=None, path=None):
    db_path = path
    if app is not None:
        db_path = path or app.config.get("DATABASE_PATH")

    should_backfill_preview_text = False

    with db_connection(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL
            )
            """
        )

        if not os.path.isdir(migrations_path()):
            return

        for filename in _migration_filenames():
            version = filename[:-4]
            applied = conn.execute(
                "SELECT 1 FROM schema_migrations WHERE version = ?",
                [version],
            ).fetchone()
            if applied:
                continue

            _apply_migration(conn, filename)
            if version == "001_notes_preview_text":
                should_backfill_preview_text = True

    if should_backfill_preview_text:
        try:
            from services.note_store import backfill_preview_texts

            backfill_preview_texts(path=db_path)
        except Exception:
            logging.getLogger(__name__).exception(
                "Failed to backfill notes preview_text after migration"
            )


def _table_exists(conn, table_id):
    return bool(
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            [table_id],
        ).fetchone()
    )


def table_columns(conn, table_id):
    if not isinstance(table_id, str) or not IDENTIFIER_RE.match(table_id):
        raise ValueError(f"Unsupported table: {table_id!r}")
    if not _table_exists(conn, table_id):
        raise ValueError(f"Unsupported table: {table_id}")
    rows = conn.execute(f"PRAGMA table_info({_quote_identifier(table_id)})").fetchall()
    return {row["name"] for row in rows}


def _validate_column(conn, table_id, column):
    column = "id" if column == "$id" else column
    columns = table_columns(conn, table_id)
    if column not in columns:
        raise ValueError(f"Unsupported column for {table_id}: {column}")
    return column


def _query_dict(query):
    if isinstance(query, dict):
        return query
    if isinstance(query, str):
        return json.loads(query)
    raise ValueError(f"Unsupported query format: {query!r}")


def _normalize_bool(value):
    if value is None:
        return None
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return 1
        if normalized in {"0", "false", "no", "off"}:
            return 0
    return 1 if bool(value) else 0


def _normalize_value(table_id, column, value):
    if column in BOOLEAN_COLUMNS.get(table_id, set()):
        return _normalize_bool(value)
    return value


def _row_to_dict(table_id, row):
    row_id = row["id"]
    data = dict(row)
    for column in BOOLEAN_COLUMNS.get(table_id, set()):
        if column in data and data[column] is not None:
            data[column] = bool(data[column])
    data["$id"] = row_id
    return data


def _parse_queries(conn, table_id, queries):
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
            column = _validate_column(conn, table_id, attribute)
            if not values:
                where.append("0 = 1")
                continue
            placeholders = ", ".join("?" for _ in values)
            where.append(f"{_quote_identifier(column)} IN ({placeholders})")
            params.extend(_normalize_value(table_id, column, value) for value in values)
        elif method == "notEqual":
            column = _validate_column(conn, table_id, attribute)
            if not values:
                continue
            placeholders = ", ".join("?" for _ in values)
            where.append(f"{_quote_identifier(column)} NOT IN ({placeholders})")
            params.extend(_normalize_value(table_id, column, value) for value in values)
        elif method == "isNull":
            column = _validate_column(conn, table_id, attribute)
            where.append(f"{_quote_identifier(column)} IS NULL")
        elif method == "isNotNull":
            column = _validate_column(conn, table_id, attribute)
            where.append(f"{_quote_identifier(column)} IS NOT NULL")
        elif method == "lessThan":
            column = _validate_column(conn, table_id, attribute)
            where.append(f"{_quote_identifier(column)} < ?")
            params.append(_normalize_value(table_id, column, values[0] if values else None))
        elif method == "lessThanEqual":
            column = _validate_column(conn, table_id, attribute)
            where.append(f"{_quote_identifier(column)} <= ?")
            params.append(_normalize_value(table_id, column, values[0] if values else None))
        elif method == "greaterThan":
            column = _validate_column(conn, table_id, attribute)
            where.append(f"{_quote_identifier(column)} > ?")
            params.append(_normalize_value(table_id, column, values[0] if values else None))
        elif method == "greaterThanEqual":
            column = _validate_column(conn, table_id, attribute)
            where.append(f"{_quote_identifier(column)} >= ?")
            params.append(_normalize_value(table_id, column, values[0] if values else None))
        elif method == "orderAsc":
            column = _validate_column(conn, table_id, attribute)
            order.append(f"{_quote_identifier(column)} ASC")
        elif method == "orderDesc":
            column = _validate_column(conn, table_id, attribute)
            order.append(f"{_quote_identifier(column)} DESC")
        elif method == "limit":
            limit = int(values[0])
        elif method == "offset":
            offset = int(values[0])
        else:
            raise ValueError(f"Unsupported query method: {method}")

    return where, params, order, limit, offset


def list_rows(table_id, queries=None, path=None):
    try:
        with db_connection(path) as conn:
            table_columns(conn, table_id)
            quoted_table = _quote_identifier(table_id)
            where, params, order, limit, offset = _parse_queries(conn, table_id, queries)
            sql = f"SELECT * FROM {quoted_table}"
            count_sql = f"SELECT COUNT(*) AS total FROM {quoted_table}"
            if where:
                clause = " WHERE " + " AND ".join(where)
                sql += clause
                count_sql += clause
            if order:
                sql += " ORDER BY " + ", ".join(order)
            params_for_rows = list(params)
            if limit is not None:
                sql += " LIMIT ?"
                params_for_rows.append(limit)
            if offset is not None:
                if limit is None:
                    sql += " LIMIT -1"
                sql += " OFFSET ?"
                params_for_rows.append(offset)

            total = conn.execute(count_sql, params).fetchone()["total"]
            rows = conn.execute(sql, params_for_rows).fetchall()
    except (sqlite3.Error, ValueError, json.JSONDecodeError) as exc:
        raise AppwriteException(str(exc)) from exc

    converted = [_row_to_dict(table_id, row) for row in rows]
    return {"total": total, "rows": converted, "documents": converted}


def list_rows_all(table_id, queries=None, limit=DEFAULT_LIMIT, path=None):
    rows = []
    offset = 0
    while True:
        query_list = list(queries or [])
        query_list.append(json.dumps({"method": "limit", "values": [limit]}))
        query_list.append(json.dumps({"method": "offset", "values": [offset]}))
        response = list_rows(table_id, query_list, path=path)
        batch = response.get("rows", [])
        rows.extend(batch)
        if len(batch) < limit:
            break
        offset += limit
    return rows


def first_row(table_id, queries=None, path=None):
    query_list = list(queries or [])
    query_list.append(json.dumps({"method": "limit", "values": [1]}))
    rows = list_rows(table_id, query_list, path=path).get("rows", [])
    return rows[0] if rows else None


def get_row(table_id, row_id, *, allow_missing=False, path=None):
    try:
        with db_connection(path) as conn:
            table_columns(conn, table_id)
            row = conn.execute(
                f"SELECT * FROM {_quote_identifier(table_id)} WHERE id = ?",
                [str(row_id)],
            ).fetchone()
    except (sqlite3.Error, ValueError) as exc:
        raise AppwriteException(str(exc)) from exc

    if row:
        return _row_to_dict(table_id, row)
    if allow_missing:
        return None
    raise AppwriteException("Row not found", 404)


def _row_id(row_id=None):
    if row_id is None or str(row_id) in UNIQUE_SENTINELS:
        return uuid.uuid4().hex
    return str(row_id)


def _clean_payload(conn, table_id, data):
    cleaned = {}
    for key, value in (data or {}).items():
        if not isinstance(key, str):
            continue
        column = "id" if key == "$id" else key
        if column == "id" or key.startswith("$"):
            continue
        column = _validate_column(conn, table_id, column)
        cleaned[column] = _normalize_value(table_id, column, value)
    return cleaned


def create_row(table_id, row_id=None, data=None, path=None):
    row_id = _row_id(row_id)
    try:
        with db_connection(path) as conn:
            table_columns(conn, table_id)
            payload = {"id": row_id, **_clean_payload(conn, table_id, data)}
            columns = list(payload.keys())
            placeholders = ", ".join("?" for _ in columns)
            column_sql = ", ".join(_quote_identifier(column) for column in columns)
            conn.execute(
                f"INSERT INTO {_quote_identifier(table_id)} ({column_sql}) VALUES ({placeholders})",
                [payload[column] for column in columns],
            )
    except (sqlite3.Error, ValueError) as exc:
        raise AppwriteException(str(exc)) from exc
    return get_row(table_id, row_id, path=path)


def insert_row_ignore(table_id, row_id=None, data=None, path=None):
    row_id = _row_id(row_id)
    try:
        with db_connection(path) as conn:
            table_columns(conn, table_id)
            payload = {"id": row_id, **_clean_payload(conn, table_id, data)}
            columns = list(payload.keys())
            placeholders = ", ".join("?" for _ in columns)
            column_sql = ", ".join(_quote_identifier(column) for column in columns)
            cursor = conn.execute(
                f"INSERT OR IGNORE INTO {_quote_identifier(table_id)} ({column_sql}) VALUES ({placeholders})",
                [payload[column] for column in columns],
            )
            return cursor.rowcount > 0
    except (sqlite3.Error, ValueError) as exc:
        raise AppwriteException(str(exc)) from exc


def upsert_row(table_id, row_id=None, data=None, path=None):
    row_id = _row_id(row_id)
    try:
        with db_connection(path) as conn:
            table_columns(conn, table_id)
            payload = {"id": row_id, **_clean_payload(conn, table_id, data)}
            columns = list(payload.keys())
            placeholders = ", ".join("?" for _ in columns)
            column_sql = ", ".join(_quote_identifier(column) for column in columns)
            updates = ", ".join(
                f"{_quote_identifier(column)} = excluded.{_quote_identifier(column)}"
                for column in columns
                if column != "id"
            )
            conflict = f"DO UPDATE SET {updates}" if updates else "DO NOTHING"
            conn.execute(
                (
                    f"INSERT INTO {_quote_identifier(table_id)} ({column_sql}) VALUES ({placeholders}) "
                    f"ON CONFLICT(id) {conflict}"
                ),
                [payload[column] for column in columns],
            )
    except (sqlite3.Error, ValueError) as exc:
        raise AppwriteException(str(exc)) from exc
    return get_row(table_id, row_id, path=path)


def update_row(table_id, row_id, data=None, path=None):
    try:
        with db_connection(path) as conn:
            table_columns(conn, table_id)
            payload = _clean_payload(conn, table_id, data)
            if not payload:
                return get_row(table_id, row_id, path=path)
            assignments = ", ".join(f"{_quote_identifier(column)} = ?" for column in payload)
            params = list(payload.values()) + [str(row_id)]
            cursor = conn.execute(
                f"UPDATE {_quote_identifier(table_id)} SET {assignments} WHERE id = ?",
                params,
            )
            if cursor.rowcount == 0:
                raise AppwriteException("Row not found", 404)
    except AppwriteException:
        raise
    except (sqlite3.Error, ValueError) as exc:
        raise AppwriteException(str(exc)) from exc
    return get_row(table_id, row_id, path=path)


def delete_row(table_id, row_id, path=None):
    try:
        with db_connection(path) as conn:
            table_columns(conn, table_id)
            conn.execute(
                f"DELETE FROM {_quote_identifier(table_id)} WHERE id = ?",
                [str(row_id)],
            )
    except (sqlite3.Error, ValueError) as exc:
        raise AppwriteException(str(exc)) from exc


def count_rows(table_id, queries=None, path=None):
    return list_rows(table_id, queries, path=path).get("total", 0)


def delete_rows_by_user(table_ids, user_id, path=None):
    counts = {}
    try:
        with db_connection(path) as conn:
            for table_id in table_ids:
                columns = table_columns(conn, table_id)
                if "user_id" not in columns:
                    continue
                cursor = conn.execute(
                    f"DELETE FROM {_quote_identifier(table_id)} WHERE user_id = ?",
                    [str(user_id)],
                )
                counts[table_id] = cursor.rowcount
    except (sqlite3.Error, ValueError) as exc:
        raise AppwriteException(str(exc)) from exc
    return counts
