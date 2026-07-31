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
                "APPWRITE_DATABASE_ID": "database-id",
                "APSTUDY_ALLOW_INSECURE_HTTP": "0",
                "FLASK_DEBUG": "1",
                "FRONTEND_CONSOLE_DIAGNOSTICS_ENABLED": " YeS ",
            },
            clear=True,
        ):
            configured = load_environment_config()

        self.assertEqual(configured.flask_secret_key, "configured-secret")
        self.assertEqual(configured.flask_env, "production")
        self.assertEqual(configured.appwrite_database_id, "database-id")
        self.assertTrue(configured.allow_insecure_http)
        self.assertTrue(configured.frontend_console_diagnostics_enabled)

    def test_missing_values_keep_existing_defaults(self):
        with patch.dict(os.environ, {}, clear=True):
            configured = load_environment_config()

        self.assertIsNone(configured.flask_secret_key)
        self.assertEqual(configured.flask_env, "")
        self.assertEqual(configured.appwrite_database_id, "")
        self.assertFalse(configured.allow_insecure_http)
        self.assertFalse(configured.frontend_console_diagnostics_enabled)

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
        self.assertEqual(app.config["APPWRITE_DATABASE_ID"], configured.appwrite_database_id)
        self.assertFalse(app.config["SESSION_COOKIE_SECURE"])
        self.assertTrue(app.config["FRONTEND_CONSOLE_DIAGNOSTICS_ENABLED"])


if __name__ == "__main__":
    unittest.main()
