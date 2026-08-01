import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from flask import Flask

from config import ENVIRONMENT_CONFIG_EXTENSION_KEY, EnvironmentConfig
from services import discord_audit


def _configured(**overrides):
    values = {
        "flask_secret_key": "session-secret",
        "flask_env": "testing",
        "appwrite_database_id": "",
        "allow_insecure_http": False,
        "frontend_console_diagnostics_enabled": False,
    }
    values.update(overrides)
    return EnvironmentConfig(**values)


class EnvironmentDiscordAuditConfigTests(unittest.TestCase):
    def test_channel_override_keeps_truthy_whitespace_from_falling_back(self):
        app = Flask(__name__)
        app.extensions[ENVIRONMENT_CONFIG_EXTENSION_KEY] = _configured(
            discord_audit_chat_deletes_channel_id=" "
        )

        with app.app_context():
            self.assertEqual(discord_audit._env_channel_id("chat_deletes"), "")

    def test_init_uses_the_explicit_app_snapshot_outside_an_app_context(self):
        app = Flask(__name__)
        app.extensions[ENVIRONMENT_CONFIG_EXTENSION_KEY] = _configured(
            discord_audit_enabled_raw="0"
        )

        with patch.dict(os.environ, {"DISCORD_AUDIT_ENABLED": "1"}, clear=False), patch.object(
            discord_audit, "_service", None
        ):
            self.assertIsNone(discord_audit.init_discord_audit(app))

    def test_init_preserves_the_app_instance_fallback_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            app = Flask(__name__, instance_path=temp_dir)
            app.extensions[ENVIRONMENT_CONFIG_EXTENSION_KEY] = _configured(
                discord_audit_enabled_raw="1",
                discord_audit_fallback_path=None,
                discord_bot_token=None,
            )
            service = MagicMock()
            with patch.object(discord_audit, "_service", None), patch.object(
                discord_audit, "DiscordAuditService", return_value=service
            ) as service_class, patch.object(
                discord_audit, "init_discord_error_reporting"
            ), patch.object(discord_audit, "init_server_console_forwarding"):
                discord_audit.init_discord_audit(app)

        service_class.assert_called_once_with(
            fallback_path=os.path.join(temp_dir, "discord_audit_fallback.jsonl")
        )
        service.start.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
