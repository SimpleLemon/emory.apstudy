"""Read-only application environment configuration.

This module owns environment-backed application settings.  Database-backed
feature flags remain in ``services.app_config`` and are intentionally separate.
"""

from dataclasses import dataclass
import os


ENVIRONMENT_CONFIG_EXTENSION_KEY = "apstudy.environment_config"
_DIAGNOSTIC_TRUTH_VALUES = {"1", "true", "yes", "on"}
APPWRITE_FRONTEND_ENDPOINT_DEFAULT = "https://nyc.cloud.appwrite.io/v1"
APPWRITE_FRONTEND_PROJECT_ID_DEFAULT = "69f77663000c16abdff2"
APSTUDY_FORCE_LOCAL_INSTANCE_DB_ENV = "APSTUDY_FORCE_LOCAL_INSTANCE_DB"


@dataclass(frozen=True, slots=True)
class EnvironmentConfig:
    """Immutable snapshot of the environment values used by the app factory."""

    flask_secret_key: str | None
    flask_env: str
    appwrite_database_id: str
    allow_insecure_http: bool
    frontend_console_diagnostics_enabled: bool
    flask_env_raw: str | None = None
    database_path_override: str | None = None
    nest_database_path: str | None = None
    force_local_instance_db: bool = False
    nest_instance_dir_override: str | None = None
    calendar_sqlite_path: str | None = None
    calendar_db_path: str | None = None
    appwrite_endpoint: str | None = None
    appwrite_project_id: str | None = None
    appwrite_api_key: str | None = None
    # The app factory has historically exposed a missing database ID as "",
    # while appwrite_client.py preserves the raw optional value as None.
    appwrite_database_id_raw: str | None = None
    appwrite_frontend_endpoint: str = APPWRITE_FRONTEND_ENDPOINT_DEFAULT
    appwrite_frontend_project_id: str = APPWRITE_FRONTEND_PROJECT_ID_DEFAULT
    appwrite_profile_avatar_bucket_id: str = "profile_avatars"
    appwrite_file_share_bucket_id: str = "file_share_files"
    appwrite_notes_media_bucket_id: str = "notes_media"
    appwrite_chat_attachments_bucket_id: str = "chat_attachments"
    # The bucket keeps its historical default, but capability checks preserve
    # the older behavior where a missing or empty env var disables storage.
    appwrite_chat_attachments_enabled: bool = False


def load_environment_config():
    """Read the app-factory environment contract once for one app instance."""
    flask_env = os.environ.get("FLASK_ENV")
    endpoint = os.environ.get("APPWRITE_ENDPOINT")
    project_id = os.environ.get("APPWRITE_PROJECT_ID")
    database_id = os.environ.get("APPWRITE_DATABASE_ID")
    chat_attachments_bucket_id = os.environ.get("APPWRITE_CHAT_ATTACHMENTS_BUCKET_ID")
    return EnvironmentConfig(
        flask_secret_key=os.environ.get("FLASK_SECRET_KEY"),
        flask_env=(flask_env or "").strip().lower(),
        appwrite_database_id=database_id or "",
        allow_insecure_http=(
            os.environ.get("APSTUDY_ALLOW_INSECURE_HTTP") == "1"
            or os.environ.get("FLASK_DEBUG") == "1"
        ),
        frontend_console_diagnostics_enabled=(
            os.environ.get("FRONTEND_CONSOLE_DIAGNOSTICS_ENABLED", "").strip().lower()
            in _DIAGNOSTIC_TRUTH_VALUES
        ),
        flask_env_raw=flask_env,
        database_path_override=os.environ.get("DATABASE_PATH"),
        nest_database_path=os.environ.get("NEST_DATABASE_PATH"),
        force_local_instance_db=(
            os.environ.get(APSTUDY_FORCE_LOCAL_INSTANCE_DB_ENV) == "1"
        ),
        nest_instance_dir_override=os.environ.get("NEST_INSTANCE_DIR"),
        calendar_sqlite_path=os.environ.get("CALENDAR_SQLITE_PATH"),
        calendar_db_path=os.environ.get("CALENDAR_DB_PATH"),
        appwrite_endpoint=endpoint,
        appwrite_project_id=project_id,
        appwrite_api_key=os.environ.get("APPWRITE_API_KEY"),
        appwrite_database_id_raw=database_id,
        appwrite_frontend_endpoint=(
            APPWRITE_FRONTEND_ENDPOINT_DEFAULT if endpoint is None else endpoint
        ),
        appwrite_frontend_project_id=(
            APPWRITE_FRONTEND_PROJECT_ID_DEFAULT if project_id is None else project_id
        ),
        appwrite_profile_avatar_bucket_id=os.environ.get(
            "APPWRITE_PROFILE_AVATAR_BUCKET_ID", "profile_avatars"
        ),
        appwrite_file_share_bucket_id=os.environ.get(
            "APPWRITE_FILE_SHARE_BUCKET_ID", "file_share_files"
        ),
        appwrite_notes_media_bucket_id=os.environ.get(
            "APPWRITE_NOTES_MEDIA_BUCKET_ID", "notes_media"
        ),
        appwrite_chat_attachments_bucket_id=(
            chat_attachments_bucket_id
            if chat_attachments_bucket_id is not None
            else "chat_attachments"
        ),
        appwrite_chat_attachments_enabled=bool(chat_attachments_bucket_id),
    )


def get_environment_config(app):
    """Return the immutable environment snapshot registered on ``app``."""
    return app.extensions[ENVIRONMENT_CONFIG_EXTENSION_KEY]
