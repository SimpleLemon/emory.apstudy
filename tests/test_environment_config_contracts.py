import os
import unittest
from unittest.mock import patch

from flask import Flask

from config import ENVIRONMENT_CONFIG_EXTENSION_KEY, load_environment_config
from services.environment_config import runtime_environment_config


class EnvironmentConfigContractTests(unittest.TestCase):
    def test_extended_values_are_captured_without_eager_coercion(self):
        values = {
            "FLASK_DEBUG": " debug ",
            "CALENDAR_DATE_BUFFER_DAYS": " 9 ",
            "APP_BASE_URL": "https://example.test/",
            "CHAT_EVENTS_POLL_SECONDS": "1.5",
            "DISCORD_CHAT_INGEST_SECRET": " ingest ",
            "DISCORD_GATEWAY_ENABLED": "false",
            "SCHEDULER_ENABLED": "yes",
            "FEED_REFRESH_INTERVAL_MINUTES": " 20 ",
            "APSWIFTLY_CONTROL_TIMEOUT_SECONDS": " 21 ",
            "NEST_BACKUP_RETENTION": " 8 ",
        }
        with patch.dict(os.environ, values, clear=True):
            configured = load_environment_config()

        expected = {
            "flask_debug_raw": " debug ",
            "calendar_date_buffer_days_raw": " 9 ",
            "app_base_url": "https://example.test/",
            "chat_events_poll_seconds_raw": "1.5",
            "discord_chat_ingest_secret": " ingest ",
            "discord_gateway_enabled_raw": "false",
            "scheduler_enabled_raw": "yes",
            "feed_refresh_interval_minutes_raw": " 20 ",
            "apswiftly_control_timeout_seconds_raw": " 21 ",
            "nest_backup_retention_raw": " 8 ",
        }
        for field_name, value in expected.items():
            with self.subTest(field_name=field_name):
                self.assertEqual(getattr(configured, field_name), value)

    def test_extended_defaults_match_existing_call_sites(self):
        with patch.dict(os.environ, {}, clear=True):
            configured = load_environment_config()

        expected = {
            "calendar_date_buffer_days_raw": "7",
            "discord_invite_url": "",
            "app_base_url": "https://nest.apstudy.org",
            "giphy_api_key": "",
            "vapid_subject": "mailto:support@apstudy.org",
            "chat_events_poll_seconds_raw": "1",
            "chat_events_keepalive_seconds_raw": "15",
            "chat_events_stream_limit_raw": "50",
            "presence_chat_fresh_seconds_raw": "30",
            "presence_site_fresh_seconds_raw": "180",
            "presence_typing_fresh_seconds_raw": "10",
            "presence_lookup_limit_raw": "200",
            "presence_online_limit_raw": "500",
            "discord_link_guild_id": "859910344393883710",
            "discord_link_role_id": "1338596013371555953",
            "discord_gateway_enabled_raw": "1",
            "discord_console_log_enabled_raw": "1",
            "discord_server_console_log_enabled_raw": "1",
            "discord_audit_enabled_raw": "1",
            "feed_refresh_interval_minutes_raw": "15",
            "discord_role_sync_minutes_raw": "30",
            "discord_chat_reconcile_seconds_raw": "300",
            "discord_chat_sync_enabled_raw": "1",
            "discord_chat_sync_seconds_raw": "5",
            "nest_backup_dir": "/var/backups/nest-db",
            "nest_backup_retention_raw": "7",
        }
        for field_name, value in expected.items():
            with self.subTest(field_name=field_name):
                self.assertEqual(getattr(configured, field_name), value)
        self.assertIsNone(configured.scheduler_enabled_raw)

    def test_secret_values_are_not_exposed_by_repr(self):
        with patch.dict(
            os.environ,
            {
                "FLASK_SECRET_KEY": "session-secret",
                "APPWRITE_API_KEY": "appwrite-secret",
                "DISCORD_BOT_TOKEN": "discord-secret",
                "VAPID_PRIVATE_KEY": "vapid-secret",
            },
            clear=True,
        ):
            rendered = repr(load_environment_config())

        for secret in ("session-secret", "appwrite-secret", "discord-secret", "vapid-secret"):
            self.assertNotIn(secret, rendered)

    def test_runtime_accessor_prefers_the_registered_snapshot(self):
        with patch.dict(os.environ, {"DISCORD_INVITE_URL": "first"}, clear=True):
            configured = load_environment_config()

        app = Flask(__name__)
        app.extensions[ENVIRONMENT_CONFIG_EXTENSION_KEY] = configured
        with patch.dict(os.environ, {"DISCORD_INVITE_URL": "second"}, clear=True):
            self.assertEqual(runtime_environment_config().discord_invite_url, "second")
            with app.app_context():
                self.assertIs(runtime_environment_config(), configured)
                self.assertEqual(runtime_environment_config().discord_invite_url, "first")


if __name__ == "__main__":
    unittest.main()
