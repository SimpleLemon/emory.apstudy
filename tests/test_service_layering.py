import ast
import logging
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from appwrite.exception import AppwriteException
from flask import Flask, jsonify

import blueprints.chat_api as chat_api
import blueprints.settings as settings_bp
from services import onboarding


REPO_ROOT = Path(__file__).resolve().parents[1]
SERVICE_PATHS = (
    REPO_ROOT / "services" / "calendar_events.py",
    REPO_ROOT / "services" / "onboarding.py",
)
FORBIDDEN_SERVICE_IMPORTS = {"blueprints.settings", "blueprints.chat_api"}


class ServiceLayeringTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.secret_key = "test"

    def test_calendar_and_onboarding_services_do_not_import_blueprints(self):
        violations = []
        for path in SERVICE_PATHS:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                module = None
                if isinstance(node, ast.ImportFrom):
                    module = node.module
                    if module == "blueprints":
                        imported_names = {alias.name for alias in node.names}
                        if imported_names & {"settings", "chat_api"}:
                            module = "blueprints." + next(
                                name for name in ("settings", "chat_api") if name in imported_names
                            )
                    if module in FORBIDDEN_SERVICE_IMPORTS:
                        violations.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}: {module}")
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name in FORBIDDEN_SERVICE_IMPORTS:
                            violations.append(
                                f"{path.relative_to(REPO_ROOT)}:{node.lineno}: {alias.name}"
                            )

        self.assertEqual(violations, [])

    def test_settings_validation_binding_remains_patchable(self):
        user = SimpleNamespace(id="user-1")
        with self.app.test_request_context(
            "/settings/api/feed-url",
            method="POST",
            json={"canvas_ical_url": "not-used"},
        ):
            with patch.object(settings_bp, "current_user", user), patch.object(
                settings_bp,
                "_normalize_canvas_calendar_url",
                return_value=None,
            ) as normalize_canvas:
                response = settings_bp.update_feed_url.__wrapped__()

        self.assertEqual(response[1], 400)
        normalize_canvas.assert_called_once_with("not-used")

    def test_settings_route_uses_patchable_onboarding_handler(self):
        user = SimpleNamespace(id="user-1", onboarding_step=5)
        sentinel = object()
        with self.app.test_request_context(
            "/onboarding",
            method="POST",
            json={"step": 5},
        ):
            with patch.object(settings_bp, "current_user", user), patch.object(
                settings_bp,
                "save_onboarding_step_five",
                return_value=sentinel,
            ) as handler:
                response = settings_bp.save_onboarding.__wrapped__()

        self.assertIs(response, sentinel)
        handler.assert_called_once()
        self.assertIs(handler.call_args.args[0], user)

    def test_settings_route_resolves_live_chat_blueprint_patches(self):
        user = SimpleNamespace(
            id="user-1",
            onboarding_step=5,
            onboarding_complete=False,
            education_level="Undergraduate",
            class_year="2028",
            emory_student=False,
            emory_email=None,
            school=None,
            school_key=None,
            school_source=None,
            scorecard_id=None,
            major=None,
            graduation_year=None,
        )
        with self.app.test_request_context(
            "/onboarding",
            method="POST",
            json={"step": 5},
        ):
            with patch.object(settings_bp, "current_user", user), patch.object(
                settings_bp, "update_row_safe", return_value={}
            ), patch.object(settings_bp, "emit_user_event"), patch.object(
                settings_bp, "url_for", return_value="/"
            ), patch.object(
                chat_api, "initialize_new_user_discord_read_states"
            ) as initialize_reads, patch.object(
                chat_api, "create_welcome_dm_for_user"
            ) as create_welcome:
                response = settings_bp.save_onboarding.__wrapped__()

        self.assertEqual(response.get_json()["redirect_url"], "/")
        initialize_reads.assert_called_once_with("user-1")
        create_welcome.assert_called_once_with("user-1")

    def test_onboarding_service_uses_injected_chat_callbacks(self):
        user = SimpleNamespace(
            id="user-1",
            onboarding_complete=False,
            onboarding_step=5,
            education_level="Undergraduate",
            class_year="2028",
            emory_student=False,
            emory_email=None,
            school=None,
            school_key=None,
            school_source=None,
            scorecard_id=None,
            major=None,
            graduation_year=None,
        )
        update_row = Mock(return_value={})
        initialize_reads = Mock()
        create_welcome = Mock()
        dependencies = {
            "AppwriteException": AppwriteException,
            "collections": {"users": "users"},
            "emit_user_event": Mock(),
            "format_actor": Mock(return_value="actor"),
            "invites": SimpleNamespace(promote_if_activated=Mock()),
            "jsonify": jsonify,
            "logger": logging.getLogger(__name__),
            "update_row_safe": update_row,
            "url_for": Mock(return_value="/dashboard"),
            "initialize_new_user_discord_read_states": initialize_reads,
            "create_welcome_dm_for_user": create_welcome,
        }

        with patch.object(
            chat_api,
            "initialize_new_user_discord_read_states",
            side_effect=AssertionError("service reached chat blueprint directly"),
        ), patch.object(
            chat_api,
            "create_welcome_dm_for_user",
            side_effect=AssertionError("service reached chat blueprint directly"),
        ), self.app.app_context():
            response = onboarding.save_onboarding_step_five(user, "user-1", dependencies)

        initialize_reads.assert_called_once_with("user-1")
        create_welcome.assert_called_once_with("user-1")
        self.assertEqual(response.get_json(), {"status": "ok", "redirect_url": "/dashboard"})
        update_row.assert_called_once_with(
            "users",
            "user-1",
            {"onboarding_complete": True, "onboarding_step": 5},
        )


if __name__ == "__main__":
    unittest.main()
