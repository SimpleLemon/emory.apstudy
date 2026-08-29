"""Read-only application environment configuration.

This module owns environment-backed application settings.  Database-backed
feature flags remain in ``services.app_config`` and are intentionally separate.
"""

from dataclasses import dataclass, field
import os


ENVIRONMENT_CONFIG_EXTENSION_KEY = "apstudy.environment_config"
_DIAGNOSTIC_TRUTH_VALUES = {"1", "true", "yes", "on"}
APSTUDY_FORCE_LOCAL_INSTANCE_DB_ENV = "APSTUDY_FORCE_LOCAL_INSTANCE_DB"
# An explicit operator-controlled sentinel for granting the ICS owner
# entitlement to every authenticated owner when the feature is enabled.
CALENDAR_ICS_GLOBAL_OWNER_ALLOWLIST_SENTINEL = "*"


@dataclass(frozen=True, slots=True)
class EnvironmentConfig:
    """Immutable snapshot of the environment values used by the app factory."""

    flask_secret_key: str | None = field(repr=False)
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
    appwrite_api_key: str | None = field(default=None, repr=False)
    # The app factory has historically exposed a missing database ID as "",
    # while appwrite_client.py preserves the raw optional value as None.
    appwrite_database_id_raw: str | None = None
    appwrite_profile_avatar_bucket_id: str = "profile_avatars"
    appwrite_file_share_bucket_id: str = "file_share_files"
    appwrite_notes_media_bucket_id: str = "notes_media"
    appwrite_chat_attachments_bucket_id: str = "chat_attachments"
    # The bucket keeps its historical default, but capability checks preserve
    # the older behavior where a missing or empty env var disables storage.
    appwrite_chat_attachments_enabled: bool = False
    flask_debug_raw: str | None = None
    allow_insecure_oauth: bool = False
    github_webhook_secret: str | None = field(default=None, repr=False)
    github_webhook_allow_unsigned: bool = False
    notes_collaboration_secret: str | None = field(default=None, repr=False)
    notes_collaboration_internal_secret: str | None = field(default=None, repr=False)
    calendar_date_buffer_days_raw: str = "7"
    # Calendar import is disabled unless the operator selects the exact,
    # read-only rollout mode.  Keep the raw value in the immutable snapshot so
    # capability resolution cannot accidentally treat arbitrary truthy strings
    # as an authorization.
    extension_calendar_rollout_raw: str | None = None
    discord_invite_url: str = ""
    app_base_url: str = "https://nest.apstudy.org"
    calendar_ics_subscriptions_enabled_raw: str = "0"
    calendar_ics_subscriptions_owner_allowlist_raw: str = ""
    calendar_ics_uid_secret: str | None = field(default=None, repr=False)
    giphy_api_key: str = field(default="", repr=False)
    admin_user_ids_raw: str | None = None
    admin_user_id_raw: str | None = None
    ga4_property_id_raw: str | None = None
    google_application_credentials: str | None = field(default=None, repr=False)
    vapid_public_key: str | None = None
    vapid_private_key: str | None = field(default=None, repr=False)
    vapid_subject: str = "mailto:support@apstudy.org"
    chat_events_poll_seconds_raw: str = "1"
    chat_events_keepalive_seconds_raw: str = "15"
    chat_events_stream_limit_raw: str = "50"
    presence_chat_fresh_seconds_raw: str = "30"
    presence_site_fresh_seconds_raw: str = "180"
    presence_typing_fresh_seconds_raw: str = "10"
    presence_lookup_limit_raw: str = "200"
    presence_online_limit_raw: str = "500"
    discord_announcements_channel_id: str | None = None
    discord_chat_channel_id: str | None = None
    discord_chat_ingest_secret: str | None = field(default=None, repr=False)
    discord_chat_sync_secret: str | None = field(default=None, repr=False)
    discord_bridge_secret: str | None = field(default=None, repr=False)
    discord_link_guild_id: str = "859910344393883710"
    discord_link_role_id: str = "1338596013371555953"
    discord_bot_token: str | None = field(default=None, repr=False)
    discord_guild_id: str | None = None
    discord_gateway_enabled_raw: str = "1"
    discord_audit_admin_channel_id: str | None = None
    discord_audit_course_tracks_channel_id: str | None = None
    discord_audit_creation_channel_id: str | None = None
    discord_audit_chat_deletes_channel_id: str | None = None
    discord_audit_user_logs_channel_id: str | None = None
    discord_audit_server_logs_channel_id: str | None = None
    discord_audit_console_logs_channel_id: str | None = None
    discord_console_log_enabled_raw: str = "1"
    discord_server_console_log_enabled_raw: str = "1"
    discord_audit_enabled_raw: str = "1"
    discord_audit_fallback_path: str | None = None
    scheduler_lock_path: str | None = None
    werkzeug_run_main: str | None = None
    scheduler_enabled_raw: str | None = None
    feed_refresh_interval_minutes_raw: str = "15"
    discord_role_sync_minutes_raw: str = "30"
    discord_chat_reconcile_seconds_raw: str = "300"
    discord_chat_sync_enabled_raw: str = "1"
    discord_chat_sync_seconds_raw: str = "5"
    apswiftly_control_url_raw: str | None = None
    apswiftly_control_token_raw: str | None = field(default=None, repr=False)
    apswiftly_service_name_raw: str | None = None
    apswiftly_control_timeout_seconds_raw: str | None = None
    nest_backup_dir: str = "/var/backups/nest-db"
    nest_backup_retention_raw: str = "7"


def load_environment_config():
    """Read the app-factory environment contract once for one app instance."""
    get = os.environ.get
    flask_env = get("FLASK_ENV")
    flask_debug = get("FLASK_DEBUG")
    endpoint = get("APPWRITE_ENDPOINT")
    project_id = get("APPWRITE_PROJECT_ID")
    database_id = get("APPWRITE_DATABASE_ID")
    chat_attachments_bucket_id = get("APPWRITE_CHAT_ATTACHMENTS_BUCKET_ID")
    return EnvironmentConfig(
        flask_secret_key=get("FLASK_SECRET_KEY"),
        flask_env=(flask_env or "").strip().lower(),
        appwrite_database_id=database_id or "",
        allow_insecure_http=(
            get("APSTUDY_ALLOW_INSECURE_HTTP") == "1" or flask_debug == "1"
        ),
        frontend_console_diagnostics_enabled=(
            get("FRONTEND_CONSOLE_DIAGNOSTICS_ENABLED", "").strip().lower()
            in _DIAGNOSTIC_TRUTH_VALUES
        ),
        flask_env_raw=flask_env,
        database_path_override=get("DATABASE_PATH"),
        nest_database_path=get("NEST_DATABASE_PATH"),
        force_local_instance_db=(get(APSTUDY_FORCE_LOCAL_INSTANCE_DB_ENV) == "1"),
        nest_instance_dir_override=get("NEST_INSTANCE_DIR"),
        calendar_sqlite_path=get("CALENDAR_SQLITE_PATH"),
        calendar_db_path=get("CALENDAR_DB_PATH"),
        appwrite_endpoint=endpoint,
        appwrite_project_id=project_id,
        appwrite_api_key=get("APPWRITE_API_KEY"),
        appwrite_database_id_raw=database_id,
        appwrite_profile_avatar_bucket_id=get(
            "APPWRITE_PROFILE_AVATAR_BUCKET_ID", "profile_avatars"
        ),
        appwrite_file_share_bucket_id=get(
            "APPWRITE_FILE_SHARE_BUCKET_ID", "file_share_files"
        ),
        appwrite_notes_media_bucket_id=get(
            "APPWRITE_NOTES_MEDIA_BUCKET_ID", "notes_media"
        ),
        appwrite_chat_attachments_bucket_id=(
            chat_attachments_bucket_id
            if chat_attachments_bucket_id is not None
            else "chat_attachments"
        ),
        appwrite_chat_attachments_enabled=bool(chat_attachments_bucket_id),
        flask_debug_raw=flask_debug,
        allow_insecure_oauth=get("APSTUDY_ALLOW_INSECURE_OAUTH") == "1",
        github_webhook_secret=get("GITHUB_WEBHOOK_SECRET"),
        github_webhook_allow_unsigned=get("GITHUB_WEBHOOK_ALLOW_UNSIGNED") == "1",
        notes_collaboration_secret=get("NOTES_COLLABORATION_SECRET"),
        notes_collaboration_internal_secret=get("NOTES_COLLABORATION_INTERNAL_SECRET"),
        calendar_date_buffer_days_raw=get("CALENDAR_DATE_BUFFER_DAYS", "7"),
        extension_calendar_rollout_raw=get("APSTUDY_EXTENSION_CALENDAR_ROLLOUT"),
        discord_invite_url=get("DISCORD_INVITE_URL", ""),
        app_base_url=get("APP_BASE_URL", "https://nest.apstudy.org"),
        calendar_ics_subscriptions_enabled_raw=get("CALENDAR_ICS_SUBSCRIPTIONS_ENABLED", "0"),
        calendar_ics_subscriptions_owner_allowlist_raw=get(
            "CALENDAR_ICS_SUBSCRIPTIONS_OWNER_ALLOWLIST",
            get("CALENDAR_ICS_OWNER_ALLOWLIST", get("CALENDAR_ICS_ALLOWLIST", "")),
        ),
        calendar_ics_uid_secret=get("CALENDAR_ICS_UID_SECRET"),
        giphy_api_key=get("GIPHY_API_KEY", ""),
        admin_user_ids_raw=get("ADMIN_USER_IDS"),
        admin_user_id_raw=get("ADMIN_USER_ID"),
        ga4_property_id_raw=get("GA4_PROPERTY_ID"),
        google_application_credentials=get("GOOGLE_APPLICATION_CREDENTIALS"),
        vapid_public_key=get("VAPID_PUBLIC_KEY"),
        vapid_private_key=get("VAPID_PRIVATE_KEY"),
        vapid_subject=get("VAPID_SUBJECT", "mailto:support@apstudy.org"),
        chat_events_poll_seconds_raw=get("CHAT_EVENTS_POLL_SECONDS", "1"),
        chat_events_keepalive_seconds_raw=get("CHAT_EVENTS_KEEPALIVE_SECONDS", "15"),
        chat_events_stream_limit_raw=get("CHAT_EVENTS_STREAM_LIMIT", "50"),
        presence_chat_fresh_seconds_raw=get("PRESENCE_CHAT_FRESH_SECONDS", "30"),
        presence_site_fresh_seconds_raw=get("PRESENCE_SITE_FRESH_SECONDS", "180"),
        presence_typing_fresh_seconds_raw=get("PRESENCE_TYPING_FRESH_SECONDS", "10"),
        presence_lookup_limit_raw=get("PRESENCE_LOOKUP_LIMIT", "200"),
        presence_online_limit_raw=get("PRESENCE_ONLINE_LIMIT", "500"),
        discord_announcements_channel_id=get("DISCORD_ANNOUNCEMENTS_CHANNEL_ID"),
        discord_chat_channel_id=get("DISCORD_CHAT_CHANNEL_ID"),
        discord_chat_ingest_secret=get("DISCORD_CHAT_INGEST_SECRET"),
        discord_chat_sync_secret=get("DISCORD_CHAT_SYNC_SECRET"),
        discord_bridge_secret=get("DISCORD_BRIDGE_SECRET"),
        discord_link_guild_id=get("DISCORD_LINK_GUILD_ID", "859910344393883710"),
        discord_link_role_id=get("DISCORD_LINK_ROLE_ID", "1338596013371555953"),
        discord_bot_token=get("DISCORD_BOT_TOKEN"),
        discord_guild_id=get("DISCORD_GUILD_ID"),
        discord_gateway_enabled_raw=get("DISCORD_GATEWAY_ENABLED", "1"),
        discord_audit_admin_channel_id=get("DISCORD_AUDIT_ADMIN_CHANNEL_ID"),
        discord_audit_course_tracks_channel_id=get(
            "DISCORD_AUDIT_COURSE_TRACKS_CHANNEL_ID"
        ),
        discord_audit_creation_channel_id=get("DISCORD_AUDIT_CREATION_CHANNEL_ID"),
        discord_audit_chat_deletes_channel_id=get(
            "DISCORD_AUDIT_CHAT_DELETES_CHANNEL_ID"
        ),
        discord_audit_user_logs_channel_id=get("DISCORD_AUDIT_USER_LOGS_CHANNEL_ID"),
        discord_audit_server_logs_channel_id=get(
            "DISCORD_AUDIT_SERVER_LOGS_CHANNEL_ID"
        ),
        discord_audit_console_logs_channel_id=get(
            "DISCORD_AUDIT_CONSOLE_LOGS_CHANNEL_ID"
        ),
        discord_console_log_enabled_raw=get("DISCORD_CONSOLE_LOG_ENABLED", "1"),
        discord_server_console_log_enabled_raw=get(
            "DISCORD_SERVER_CONSOLE_LOG_ENABLED", "1"
        ),
        discord_audit_enabled_raw=get("DISCORD_AUDIT_ENABLED", "1"),
        discord_audit_fallback_path=get("DISCORD_AUDIT_FALLBACK_PATH"),
        scheduler_lock_path=get("SCHEDULER_LOCK_PATH"),
        werkzeug_run_main=get("WERKZEUG_RUN_MAIN"),
        scheduler_enabled_raw=get("SCHEDULER_ENABLED"),
        feed_refresh_interval_minutes_raw=get("FEED_REFRESH_INTERVAL_MINUTES", "15"),
        discord_role_sync_minutes_raw=get("DISCORD_ROLE_SYNC_MINUTES", "30"),
        discord_chat_reconcile_seconds_raw=get("DISCORD_CHAT_RECONCILE_SECONDS", "300"),
        discord_chat_sync_enabled_raw=get("DISCORD_CHAT_SYNC_ENABLED", "1"),
        discord_chat_sync_seconds_raw=get("DISCORD_CHAT_SYNC_SECONDS", "5"),
        apswiftly_control_url_raw=get("APSWIFTLY_CONTROL_URL"),
        apswiftly_control_token_raw=get("APSWIFTLY_CONTROL_TOKEN"),
        apswiftly_service_name_raw=get("APSWIFTLY_SERVICE_NAME"),
        apswiftly_control_timeout_seconds_raw=get("APSWIFTLY_CONTROL_TIMEOUT_SECONDS"),
        nest_backup_dir=get("NEST_BACKUP_DIR", "/var/backups/nest-db"),
        nest_backup_retention_raw=get("NEST_BACKUP_RETENTION", "7"),
    )


def get_environment_config(app):
    """Return the immutable environment snapshot registered on ``app``."""
    return app.extensions[ENVIRONMENT_CONFIG_EXTENSION_KEY]
