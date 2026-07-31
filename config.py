"""Read-only application environment configuration.

This module owns environment-backed application settings.  Database-backed
feature flags remain in ``services.app_config`` and are intentionally separate.
"""

from dataclasses import dataclass
import os


ENVIRONMENT_CONFIG_EXTENSION_KEY = "apstudy.environment_config"
_DIAGNOSTIC_TRUTH_VALUES = {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class EnvironmentConfig:
    """Immutable snapshot of the environment values used by the app factory."""

    flask_secret_key: str | None
    flask_env: str
    appwrite_database_id: str
    allow_insecure_http: bool
    frontend_console_diagnostics_enabled: bool
    appwrite_endpoint: str | None = None
    appwrite_project_id: str | None = None
    appwrite_api_key: str | None = None
    # The app factory has historically exposed a missing database ID as "",
    # while appwrite_client.py preserves the raw optional value as None.
    appwrite_database_id_raw: str | None = None
    appwrite_profile_avatar_bucket_id: str = "profile_avatars"
    appwrite_file_share_bucket_id: str = "file_share_files"
    appwrite_notes_media_bucket_id: str = "notes_media"
    appwrite_chat_attachments_bucket_id: str = "chat_attachments"


def load_environment_config():
    """Read the app-factory environment contract once for one app instance."""
    database_id = os.environ.get("APPWRITE_DATABASE_ID")
    return EnvironmentConfig(
        flask_secret_key=os.environ.get("FLASK_SECRET_KEY"),
        flask_env=(os.environ.get("FLASK_ENV") or "").strip().lower(),
        appwrite_database_id=database_id or "",
        allow_insecure_http=(
            os.environ.get("APSTUDY_ALLOW_INSECURE_HTTP") == "1"
            or os.environ.get("FLASK_DEBUG") == "1"
        ),
        frontend_console_diagnostics_enabled=(
            os.environ.get("FRONTEND_CONSOLE_DIAGNOSTICS_ENABLED", "").strip().lower()
            in _DIAGNOSTIC_TRUTH_VALUES
        ),
        appwrite_endpoint=os.environ.get("APPWRITE_ENDPOINT"),
        appwrite_project_id=os.environ.get("APPWRITE_PROJECT_ID"),
        appwrite_api_key=os.environ.get("APPWRITE_API_KEY"),
        appwrite_database_id_raw=database_id,
        appwrite_profile_avatar_bucket_id=os.environ.get(
            "APPWRITE_PROFILE_AVATAR_BUCKET_ID", "profile_avatars"
        ),
        appwrite_file_share_bucket_id=os.environ.get(
            "APPWRITE_FILE_SHARE_BUCKET_ID", "file_share_files"
        ),
        appwrite_notes_media_bucket_id=os.environ.get(
            "APPWRITE_NOTES_MEDIA_BUCKET_ID", "notes_media"
        ),
        appwrite_chat_attachments_bucket_id=os.environ.get(
            "APPWRITE_CHAT_ATTACHMENTS_BUCKET_ID", "chat_attachments"
        ),
    )


def get_environment_config(app):
    """Return the immutable environment snapshot registered on ``app``."""
    return app.extensions[ENVIRONMENT_CONFIG_EXTENSION_KEY]
