from contextlib import contextmanager

from flask import current_app, has_app_context

from config import ENVIRONMENT_CONFIG_EXTENSION_KEY, load_environment_config
from services import database as _nest_database


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

def _environment_config_snapshot():
    if has_app_context():
        configured = current_app.extensions.get(ENVIRONMENT_CONFIG_EXTENSION_KEY)
        if configured is not None:
            return configured
    return load_environment_config()


def _validate_table(table_id):
    if table_id not in TABLE_COLUMNS:
        raise ValueError(f"Unsupported calendar table: {table_id}")


# Calendar storage and schema management are shared with the main database.
def calendar_db_path(path=None):
    if path:
        return _nest_database.resolve_env_path(path) or path
    configured_environment = _environment_config_snapshot()
    legacy = configured_environment.calendar_sqlite_path or configured_environment.calendar_db_path
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
