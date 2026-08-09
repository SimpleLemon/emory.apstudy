import os
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from unittest.mock import patch

from config import EnvironmentConfig, get_environment_config, load_environment_config


class EnvironmentConfigTests(unittest.TestCase):
    def test_loads_factory_values_with_existing_defaults_and_normalization(self):
        with patch.dict(
            os.environ,
            {
                "FLASK_SECRET_KEY": "configured-secret",
                "FLASK_ENV": "  PRODUCTION ",
                "APPWRITE_ENDPOINT": "https://appwrite.example/v1",
                "APPWRITE_PROJECT_ID": "project-id",
                "APPWRITE_API_KEY": "api-key",
                "APPWRITE_DATABASE_ID": "database-id",
                "APPWRITE_PROFILE_AVATAR_BUCKET_ID": "avatars",
                "APPWRITE_FILE_SHARE_BUCKET_ID": "files",
                "APPWRITE_NOTES_MEDIA_BUCKET_ID": "note-media",
                "APPWRITE_CHAT_ATTACHMENTS_BUCKET_ID": "chat-files",
                "DATABASE_PATH": "configured.sqlite3",
                "NEST_DATABASE_PATH": "fallback.sqlite3",
                "APSTUDY_FORCE_LOCAL_INSTANCE_DB": "1",
                "NEST_INSTANCE_DIR": "instance",
                "CALENDAR_SQLITE_PATH": "calendar.sqlite3",
                "CALENDAR_DB_PATH": "legacy-calendar.sqlite3",
                "APSTUDY_ALLOW_INSECURE_HTTP": "0",
                "FLASK_DEBUG": "1",
                "FRONTEND_CONSOLE_DIAGNOSTICS_ENABLED": " YeS ",
            },
            clear=True,
        ):
            configured = load_environment_config()

        self.assertEqual(configured.flask_secret_key, "configured-secret")
        self.assertEqual(configured.flask_env, "production")
        self.assertEqual(configured.flask_env_raw, "  PRODUCTION ")
        self.assertEqual(configured.database_path_override, "configured.sqlite3")
        self.assertEqual(configured.nest_database_path, "fallback.sqlite3")
        self.assertTrue(configured.force_local_instance_db)
        self.assertEqual(configured.nest_instance_dir_override, "instance")
        self.assertEqual(configured.calendar_sqlite_path, "calendar.sqlite3")
        self.assertEqual(configured.calendar_db_path, "legacy-calendar.sqlite3")
        self.assertEqual(configured.appwrite_endpoint, "https://appwrite.example/v1")
        self.assertEqual(configured.appwrite_project_id, "project-id")
        self.assertEqual(configured.appwrite_api_key, "api-key")
        self.assertEqual(configured.appwrite_database_id, "database-id")
        self.assertEqual(configured.appwrite_database_id_raw, "database-id")
        self.assertEqual(configured.appwrite_profile_avatar_bucket_id, "avatars")
        self.assertEqual(configured.appwrite_file_share_bucket_id, "files")
        self.assertEqual(configured.appwrite_notes_media_bucket_id, "note-media")
        self.assertEqual(configured.appwrite_chat_attachments_bucket_id, "chat-files")
        self.assertTrue(configured.appwrite_chat_attachments_enabled)
        self.assertTrue(configured.allow_insecure_http)
        self.assertTrue(configured.frontend_console_diagnostics_enabled)

    def test_missing_values_keep_existing_defaults(self):
        with patch.dict(os.environ, {}, clear=True):
            configured = load_environment_config()

        self.assertIsNone(configured.flask_secret_key)
        self.assertEqual(configured.flask_env, "")
        self.assertIsNone(configured.flask_env_raw)
        self.assertIsNone(configured.database_path_override)
        self.assertIsNone(configured.nest_database_path)
        self.assertFalse(configured.force_local_instance_db)
        self.assertIsNone(configured.nest_instance_dir_override)
        self.assertIsNone(configured.calendar_sqlite_path)
        self.assertIsNone(configured.calendar_db_path)
        self.assertIsNone(configured.appwrite_endpoint)
        self.assertIsNone(configured.appwrite_project_id)
        self.assertIsNone(configured.appwrite_api_key)
        self.assertEqual(configured.appwrite_database_id, "")
        self.assertIsNone(configured.appwrite_database_id_raw)
        self.assertEqual(configured.appwrite_profile_avatar_bucket_id, "profile_avatars")
        self.assertEqual(configured.appwrite_file_share_bucket_id, "file_share_files")
        self.assertEqual(configured.appwrite_notes_media_bucket_id, "notes_media")
        self.assertEqual(configured.appwrite_chat_attachments_bucket_id, "chat_attachments")
        self.assertFalse(configured.appwrite_chat_attachments_enabled)
        self.assertFalse(configured.allow_insecure_http)
        self.assertFalse(configured.frontend_console_diagnostics_enabled)

    def test_preserves_explicit_empty_appwrite_values(self):
        with patch.dict(
            os.environ,
            {
                "APPWRITE_ENDPOINT": "",
                "APPWRITE_PROJECT_ID": "",
                "APPWRITE_API_KEY": "",
                "APPWRITE_DATABASE_ID": "",
                "APPWRITE_PROFILE_AVATAR_BUCKET_ID": "",
                "APPWRITE_FILE_SHARE_BUCKET_ID": "",
                "APPWRITE_NOTES_MEDIA_BUCKET_ID": "",
                "APPWRITE_CHAT_ATTACHMENTS_BUCKET_ID": "",
            },
            clear=True,
        ):
            configured = load_environment_config()

        self.assertEqual(configured.appwrite_endpoint, "")
        self.assertEqual(configured.appwrite_project_id, "")
        self.assertEqual(configured.appwrite_api_key, "")
        self.assertEqual(configured.appwrite_database_id, "")
        self.assertEqual(configured.appwrite_database_id_raw, "")
        self.assertEqual(configured.appwrite_profile_avatar_bucket_id, "")
        self.assertEqual(configured.appwrite_file_share_bucket_id, "")
        self.assertEqual(configured.appwrite_notes_media_bucket_id, "")
        self.assertEqual(configured.appwrite_chat_attachments_bucket_id, "")
        self.assertFalse(configured.appwrite_chat_attachments_enabled)

    def test_snapshot_is_read_only(self):
        configured = EnvironmentConfig(
            flask_secret_key="secret",
            flask_env="testing",
            appwrite_database_id="database-id",
            allow_insecure_http=False,
            frontend_console_diagnostics_enabled=False,
        )

        with self.assertRaises(FrozenInstanceError):
            configured.flask_env = "production"

    def test_production_secret_error_is_preserved(self):
        from app import _session_secret_key

        configured = EnvironmentConfig(
            flask_secret_key=None,
            flask_env="production",
            appwrite_database_id="",
            allow_insecure_http=False,
            frontend_console_diagnostics_enabled=False,
        )

        with self.assertRaisesRegex(RuntimeError, "FLASK_SECRET_KEY must be configured in production\\."):
            _session_secret_key(configured)

    def test_create_app_registers_one_snapshot(self):
        from app import create_app

        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ,
            {
                "DATABASE_PATH": os.path.join(temp_dir, "nest.sqlite3"),
                "FLASK_SECRET_KEY": "test-secret",
                "FLASK_ENV": "testing",
                "APSTUDY_ALLOW_INSECURE_HTTP": "1",
                "FRONTEND_CONSOLE_DIAGNOSTICS_ENABLED": "on",
                "SCHEDULER_ENABLED": "0",
            },
            clear=False,
        ), patch("services.discord_audit.init_discord_audit"), patch("services.scheduler.init_scheduler"):
            app = create_app()

        configured = get_environment_config(app)
        self.assertIs(app.extensions["apstudy.environment_config"], configured)
        self.assertEqual(configured.flask_secret_key, "test-secret")
        self.assertTrue(configured.allow_insecure_http)
        self.assertTrue(configured.frontend_console_diagnostics_enabled)
        self.assertFalse(app.config["SESSION_COOKIE_SECURE"])
        self.assertTrue(app.config["FRONTEND_CONSOLE_DIAGNOSTICS_ENABLED"])

    def test_avatar_storage_uses_the_registered_environment_snapshot(self):
        from flask import Flask
        from services import avatar_storage

        with patch.dict(
            os.environ,
            {
                "APPWRITE_ENDPOINT": "https://configured.example/v1",
                "APPWRITE_PROJECT_ID": "configured-project",
            },
            clear=True,
        ):
            configured = load_environment_config()

        app = Flask(__name__)
        app.extensions["apstudy.environment_config"] = configured
        with patch.dict(
            os.environ,
            {
                "APPWRITE_ENDPOINT": "https://changed.example/v1",
                "APPWRITE_PROJECT_ID": "changed-project",
            },
            clear=True,
        ), app.app_context(), patch.object(avatar_storage, "ENDPOINT", None), patch.object(
            avatar_storage, "PROJECT_ID", None
        ):
            url = avatar_storage.build_avatar_view_url("avatar-file")

        self.assertEqual(
            url,
            "https://configured.example/v1/storage/buckets/profile_avatars/files/avatar-file/view?project=configured-project",
        )


if __name__ == "__main__":
    unittest.main()
