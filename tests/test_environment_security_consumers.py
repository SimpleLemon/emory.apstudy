import os
import unittest
from unittest.mock import patch

from flask import Flask

import app as app_module
from blueprints import notes_api, webhooks
from config import ENVIRONMENT_CONFIG_EXTENSION_KEY, EnvironmentConfig


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


class EnvironmentSecurityConsumerTests(unittest.TestCase):
    def test_oauth_bootstrap_keeps_exact_truth_rules_and_setdefault(self):
        configured = _configured(allow_insecure_oauth=True, flask_debug_raw="0")
        with patch.dict(os.environ, {}, clear=True), patch.object(
            app_module, "load_environment_config", return_value=configured
        ):
            app_module._configure_insecure_oauth_transport()
            self.assertEqual(os.environ["OAUTHLIB_INSECURE_TRANSPORT"], "1")

    def test_webhook_helpers_use_the_registered_raw_snapshot(self):
        app = Flask(__name__)
        app.extensions[ENVIRONMENT_CONFIG_EXTENSION_KEY] = _configured(
            github_webhook_secret="  webhook-secret  ",
            github_webhook_allow_unsigned=False,
            flask_debug_raw="1",
        )

        with app.app_context():
            self.assertEqual(webhooks._github_webhook_secret(), "webhook-secret")
            self.assertTrue(webhooks._github_unsigned_allowed())

    def test_internal_notes_secret_keeps_first_truthy_value_before_matching(self):
        app = Flask(__name__)
        app.secret_key = "fallback-secret"
        app.extensions[ENVIRONMENT_CONFIG_EXTENSION_KEY] = _configured(
            notes_collaboration_internal_secret=" ",
            notes_collaboration_secret="shared-secret",
        )

        with app.test_request_context(
            "/", headers={"X-Nest-Collaboration-Secret": "shared-secret"}
        ):
            self.assertFalse(notes_api._internal_collaboration_authorized())
        with app.test_request_context(
            "/", headers={"X-Nest-Collaboration-Secret": " "}
        ):
            self.assertTrue(notes_api._internal_collaboration_authorized())

    def test_internal_notes_access_stays_closed_in_normalized_production(self):
        app = Flask(__name__)
        app.extensions[ENVIRONMENT_CONFIG_EXTENSION_KEY] = _configured(
            flask_env="production",
            notes_collaboration_internal_secret=None,
            notes_collaboration_secret=None,
        )

        with app.test_request_context("/", environ_base={"REMOTE_ADDR": "127.0.0.1"}):
            self.assertFalse(notes_api._internal_collaboration_authorized())


if __name__ == "__main__":
    unittest.main()
