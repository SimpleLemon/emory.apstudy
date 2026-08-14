import os
import tempfile
import unittest
from unittest.mock import patch

import app as app_module
from config import load_environment_config
from services.extension_contract import (
    DESTRUCTIVE_EXTENSION_CAPABILITIES,
    EXTENSION_CALENDAR_CAPABILITY,
    EXTENSION_CALENDAR_READ_ONLY_ROLLOUT,
    extension_capabilities_for_rollout,
    extension_read_only_rollout_enabled,
)


READ_ONLY_CAPABILITIES = {
    EXTENSION_CALENDAR_CAPABILITY,
    "calendar_read",
    "calendar_upload",
    "calendar_projection",
    "calendar_shares_ics",
}


class ExtensionRolloutConfigTests(unittest.TestCase):
    def test_missing_rollout_is_fail_closed(self):
        with patch.dict(os.environ, {}, clear=True):
            configured = load_environment_config()

        self.assertIsNone(configured.extension_calendar_rollout_raw)
        capabilities = extension_capabilities_for_rollout(
            configured.extension_calendar_rollout_raw
        )
        self.assertFalse(any(capabilities.values()))

    def test_exact_read_only_rollout_enables_only_safe_cohort(self):
        with patch.dict(
            os.environ,
            {"APSTUDY_EXTENSION_CALENDAR_ROLLOUT": EXTENSION_CALENDAR_READ_ONLY_ROLLOUT},
            clear=True,
        ):
            configured = load_environment_config()

        self.assertEqual(
            configured.extension_calendar_rollout_raw,
            EXTENSION_CALENDAR_READ_ONLY_ROLLOUT,
        )
        capabilities = extension_capabilities_for_rollout(
            configured.extension_calendar_rollout_raw
        )
        self.assertEqual(
            {name for name, enabled in capabilities.items() if enabled},
            READ_ONLY_CAPABILITIES,
        )
        self.assertTrue(extension_read_only_rollout_enabled(EXTENSION_CALENDAR_READ_ONLY_ROLLOUT))
        self.assertFalse(any(capabilities[name] for name in DESTRUCTIVE_EXTENSION_CAPABILITIES))

    def test_ambiguous_and_future_values_remain_disabled(self):
        for value in ("1", "true", "yes", "on", "readonly", "readonly-v2", " READONLY-V1 ", ""):
            with self.subTest(value=value):
                capabilities = extension_capabilities_for_rollout(value)
                self.assertFalse(any(capabilities.values()))
                self.assertFalse(extension_read_only_rollout_enabled(value))

    def test_app_factory_registers_rollout_snapshot_without_enabling_destructive_flags(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ,
            {
                "DATABASE_PATH": os.path.join(temp_dir, "nest.sqlite3"),
                "FLASK_SECRET_KEY": "extension-rollout-test-key",
                "FLASK_ENV": "testing",
                "APSTUDY_ALLOW_INSECURE_HTTP": "1",
                "SCHEDULER_ENABLED": "0",
                "APSTUDY_EXTENSION_CALENDAR_ROLLOUT": EXTENSION_CALENDAR_READ_ONLY_ROLLOUT,
            },
            clear=False,
        ), patch("services.discord_audit.init_discord_audit"), patch(
            "services.scheduler.init_scheduler"
        ):
            app = app_module.create_app()

        self.assertEqual(
            {name for name, enabled in app.config["EXTENSION_CAPABILITIES"].items() if enabled},
            READ_ONLY_CAPABILITIES,
        )
        self.assertTrue(app.config["EXTENSION_CALENDAR_INTEGRATION_ENABLED"])
        self.assertFalse(
            any(
                app.config["EXTENSION_CAPABILITIES"][name]
                for name in DESTRUCTIVE_EXTENSION_CAPABILITIES
            )
        )


if __name__ == "__main__":
    unittest.main()
