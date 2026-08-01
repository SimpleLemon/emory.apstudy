import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from blueprints import chat_api
from config import ENVIRONMENT_CONFIG_EXTENSION_KEY, EnvironmentConfig
from services import discord_bridge, discord_gateway


REPO_ROOT = Path(__file__).resolve().parents[1]


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


def _app(configured):
    app = Flask(__name__)
    app.extensions[ENVIRONMENT_CONFIG_EXTENSION_KEY] = configured
    return app


class EnvironmentChatConsumerTests(unittest.TestCase):
    def test_chat_numeric_errors_still_happen_during_import(self):
        environment = os.environ.copy()
        environment["CHAT_EVENTS_POLL_SECONDS"] = "not-a-number"

        completed = subprocess.run(
            [sys.executable, "-c", "import blueprints.chat_api"],
            cwd=REPO_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("ValueError", completed.stderr)

    def test_discord_link_ids_keep_import_time_defaults_and_explicit_empty_values(self):
        environment = os.environ.copy()
        environment["DISCORD_LINK_GUILD_ID"] = ""
        environment["DISCORD_LINK_ROLE_ID"] = "custom-role"
        code = (
            "import json; import services.discord_bridge as bridge; "
            "print(json.dumps([bridge.LINK_GUILD_ID, bridge.LINK_ROLE_ID]))"
        )

        completed = subprocess.run(
            [sys.executable, "-c", code],
            cwd=REPO_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=True,
        )

        self.assertEqual(json.loads(completed.stdout), ["", "custom-role"])

    def test_ingest_secret_keeps_first_truthy_value_before_trimming(self):
        app = _app(
            _configured(
                discord_chat_ingest_secret=" ",
                discord_chat_sync_secret="sync-secret",
                discord_bridge_secret="bridge-secret",
            )
        )

        with app.app_context():
            self.assertEqual(chat_api._discord_ingest_secret(), "")

    def test_default_channels_trim_snapshot_ids_at_call_time(self):
        app = _app(
            _configured(
                discord_announcements_channel_id=" announcements ",
                discord_chat_channel_id=" chat ",
            )
        )
        with app.app_context(), patch.object(
            chat_api, "_ensure_discord_channel", side_effect=lambda *args: args[0]
        ) as ensure_channel:
            channels = chat_api._default_channels()

        self.assertEqual(channels, ["nest_announcements", "nest_chat"])
        self.assertEqual(ensure_channel.call_args_list[0].args[3], "announcements")
        self.assertEqual(ensure_channel.call_args_list[1].args[3], "chat")

    def test_gateway_keeps_exact_disable_and_token_rules(self):
        disabled_app = _app(
            _configured(discord_gateway_enabled_raw="0", discord_bot_token="token")
        )
        with disabled_app.app_context():
            self.assertFalse(discord_gateway.DiscordGatewayBridge(disabled_app).start())

        enabled_app = _app(
            _configured(discord_gateway_enabled_raw="false", discord_bot_token=" token ")
        )
        bridge = discord_gateway.DiscordGatewayBridge(enabled_app)
        with enabled_app.app_context(), patch.object(discord_gateway.threading, "Thread") as thread:
            self.assertTrue(bridge.start())
        thread.return_value.start.assert_called_once_with()

    def test_bridge_bot_token_is_trimmed_from_the_snapshot(self):
        app = _app(_configured(discord_bot_token=" token "))
        with app.app_context():
            self.assertEqual(discord_bridge._bot_token(), "token")


if __name__ == "__main__":
    unittest.main()
