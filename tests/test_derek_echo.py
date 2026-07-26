import os
import unittest
from unittest.mock import patch

from flask import Flask
from flask_login import UserMixin

import blueprints.derek as derek
from extensions import login_manager
from tests.support.harness import register_shell_route_stubs, reset_flask_login_manager


class DerekUser(UserMixin):
    def __init__(self, user_id, email):
        self.id = user_id
        self.email = email
        self.name = "Derek"
        self.onboarding_complete = True


class DerekEchoPageTestCase(unittest.TestCase):
    def setUp(self):
        previous_loader = login_manager._user_callback
        self.addCleanup(setattr, login_manager, "_user_callback", previous_loader)
        self.addCleanup(reset_flask_login_manager)

        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        self.app = Flask(
            __name__,
            template_folder=os.path.join(project_root, "templates"),
            static_folder=os.path.join(project_root, "static"),
        )
        self.app.secret_key = "test"
        self.app.jinja_env.filters["avatar_url"] = lambda value, size=32: value or ""
        login_manager.init_app(self.app)
        self.app.register_blueprint(derek.derek_bp)
        register_shell_route_stubs(self.app)

        self.users = {
            "derek-1": DerekUser("derek-1", "derekchenusa@gmail.com"),
            "other-1": DerekUser("other-1", "other@example.com"),
        }

        @login_manager.user_loader
        def load_user(user_id):
            return self.users.get(user_id)

    def _login(self, client, user_id):
        with client.session_transaction() as session:
            session["_user_id"] = user_id
            session["_fresh"] = True

    def test_echo_page_requires_allowed_email(self):
        client = self.app.test_client()
        allowed = self.users["derek-1"]

        with patch.object(derek, "_load_user_settings", return_value={}), \
                patch.object(derek, "_user_payload", return_value={"email": allowed.email, "name": allowed.name}):
            self._login(client, "derek-1")
            response = client.get("/derek/echo")

        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("data-echo-dash", body)
        self.assertIn("data-echo-clock", body)
        self.assertIn("data-echo-calendar", body)
        self.assertIn("data-echo-digit-slot", body)
        self.assertIn("data-echo-clock-label", body)
        self.assertIn("data-echo-music-open", body)
        self.assertIn("data-echo-playlist-open", body)
        self.assertIn("data-echo-agenda-open", body)
        self.assertIn("data-echo-agenda-modal", body)
        self.assertIn("echo-modal-panel--agenda", body)
        self.assertIn("data-echo-playlist-modal", body)
        self.assertIn("data-echo-event-modal", body)
        self.assertIn("data-echo-event-details", body)
        self.assertNotIn("data-echo-ampm", body)
        self.assertNotIn("Upcoming Calendar", body)
        self.assertNotIn("youtube.com/embed", body)

        self._login(client, "other-1")
        response = client.get("/derek/echo")
        self.assertEqual(response.status_code, 403)
