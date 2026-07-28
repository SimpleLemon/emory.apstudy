import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask

import blueprints.auth as auth
import blueprints.invites_api as invites_api
from appwrite_client import COLLECTIONS
from appwrite_helpers import create_row_safe
from blueprints.invites_api import invites_api_bp
from extensions import login_manager
from models import load_user
from services import invites
from services.database import init_db
from tests.support.harness import reset_flask_login_manager


class InviteApiTests(unittest.TestCase):
    def setUp(self):
        self.path = tempfile.mktemp(suffix=".sqlite3")
        self.app = Flask(__name__)
        self.app.secret_key = "test"
        self.app.config.update(
            DATABASE_PATH=self.path,
            SESSION_COOKIE_SECURE=True,
        )
        init_db(self.app, self.path)
        login_manager.init_app(self.app)
        login_manager.user_loader(load_user)
        login_manager.login_view = "auth.login"
        self.app.register_blueprint(invites_api_bp)
        self.app.register_blueprint(auth.auth_bp)
        self.context = self.app.app_context()
        self.context.push()
        self.audit_patch = patch.object(invites, "emit_creation_event")
        self.notify_patch = patch.object(invites.notifications, "notify")
        self.audit_patch.start()
        self.notify_patch.start()
        self._create_user("owner", onboarding_complete=True)
        self._create_user("invitee", onboarding_complete=False)

    def tearDown(self):
        self.notify_patch.stop()
        self.audit_patch.stop()
        self.context.pop()
        reset_flask_login_manager()
        Path(self.path).unlink(missing_ok=True)

    def _create_user(self, user_id, *, onboarding_complete):
        return create_row_safe(
            COLLECTIONS["users"],
            user_id,
            {
                "google_id": user_id,
                "email": f"{user_id}@example.test",
                "name": user_id.title(),
                "username": user_id,
                "tier": "free",
                "onboarding_complete": onboarding_complete,
                "onboarding_step": 5 if onboarding_complete else 1,
                "created_at": "2026-07-24T00:00:00Z",
            },
        )

    @staticmethod
    def _login(client, user_id):
        with client.session_transaction() as session:
            session["_user_id"] = user_id
            session["_fresh"] = True

    def test_routes_require_authentication_and_completed_onboarding(self):
        client = self.app.test_client()
        anonymous = client.get("/settings/api/invites")
        self.assertIn(anonymous.status_code, {302, 401})

        with self.app.test_request_context("/settings/api/invites"), patch.object(
            invites_api,
            "current_user",
            SimpleNamespace(id="invitee", onboarding_complete=False),
        ):
            forbidden, status = invites_api.list_invites.__wrapped__()
        self.assertEqual(status, 403)
        self.assertEqual(forbidden.get_json()["code"], "onboarding_required")

    def test_create_lists_and_enforces_empty_invite_cap(self):
        client = self.app.test_client()
        self._login(client, "owner")
        for index in range(invites.EMPTY_INVITE_LIMIT):
            response = client.post(
                "/settings/api/invites",
                json={"label": f"Group {index + 1}"},
            )
            self.assertEqual(response.status_code, 201)

        capped = client.post(
            "/settings/api/invites",
            json={"label": "Too many"},
        )
        self.assertEqual(capped.status_code, 400)
        self.assertEqual(capped.get_json()["code"], "empty_invite_limit")

        payload = client.get("/settings/api/invites").get_json()
        self.assertEqual(payload["empty_invite_count"], invites.EMPTY_INVITE_LIMIT)
        self.assertFalse(payload["can_create"])

    def test_block_in_either_direction_hides_message_action(self):
        invitation = invites.create_invite("owner")
        invites.attribute_signup(invitation["code"], "invitee")
        create_row_safe(
            COLLECTIONS["chat_blocks"],
            "block-1",
            {
                "blocker_id": "invitee",
                "blocked_id": "owner",
                "block_key": "invitee:owner",
                "created_at": "2026-07-24T00:00:00Z",
            },
        )

        client = self.app.test_client()
        self._login(client, "owner")
        person = client.get("/settings/api/invites").get_json()["invites"][0]["people"][0]
        self.assertFalse(person["can_message"])

    def test_deactivated_invite_stops_capture_and_preserves_counts(self):
        invitation = invites.create_invite("owner")
        invites.attribute_signup(invitation["code"], "invitee")
        invites.update_invite("owner", invitation["$id"], is_active=False)

        client = self.app.test_client()
        response = client.get(f"/join/{invitation['code']}")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/")
        self.assertIsNone(client.get_cookie(auth.INVITE_COOKIE))

        payload = invites.list_invites_for_owner("owner")[0]
        self.assertFalse(payload["is_active"])
        self.assertEqual(payload["invited_count"], 1)

    def test_last_touch_cookie_overwrites_and_invalid_code_leaves_it_untouched(self):
        first = invites.create_invite("owner", "First")
        second = invites.create_invite("owner", "Second")
        client = self.app.test_client()

        first_response = client.get(f"/join/{first['code']}")
        self.assertEqual(first_response.headers["Location"], "/")
        self.assertEqual(client.get_cookie(auth.INVITE_COOKIE).value, first["code"])
        second_response = client.get(f"/join/{second['code'].lower()}")
        self.assertEqual(second_response.headers["Location"], "/")
        self.assertEqual(client.get_cookie(auth.INVITE_COOKIE).value, second["code"])
        invalid = client.get("/join/O0I1LL")
        self.assertEqual(invalid.headers["Location"], "/")
        self.assertNotIn(auth.INVITE_COOKIE, invalid.headers.get("Set-Cookie", ""))
        self.assertEqual(client.get_cookie(auth.INVITE_COOKIE).value, second["code"])

    def test_session_exchange_clears_invite_cookie(self):
        invitation = invites.create_invite("owner")
        client = self.app.test_client()
        client.set_cookie(auth.INVITE_COOKIE, invitation["code"])

        with patch.object(
            auth,
            "_account_from_jwt",
            return_value={"$id": "invitee", "email": "invitee@example.test"},
        ), patch.object(
            auth,
            "_complete_appwrite_login",
            return_value={
                "user_id": "invitee",
                "redirect": "/onboarding",
            },
        ):
            response = client.post(
                "/auth/session",
                json={
                    "jwt": "proof",
                    "user_id": "invitee",
                    "email": "invitee@example.test",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(client.get_cookie(auth.INVITE_COOKIE))
        self.assertIn("Expires=Thu, 01 Jan 1970", response.headers["Set-Cookie"])

    def test_oauth_callback_clears_invite_cookie(self):
        invitation = invites.create_invite("owner")
        client = self.app.test_client()
        client.set_cookie(auth.INVITE_COOKIE, invitation["code"])
        with client.session_transaction() as session:
            session[auth.APPWRITE_OAUTH_STATE_KEY] = "state-token"
            session[auth.APPWRITE_OAUTH_PROVIDER_KEY] = "google"

        appwrite_account = type(
            "AccountStub",
            (),
            {
                "create_session": lambda _self, _user_id, _secret: {
                    "provider": "google",
                    "providerAccessToken": "provider-token",
                    "providerUid": "provider-user",
                }
            },
        )()
        with patch.object(auth, "Account", return_value=appwrite_account), patch.object(
            auth,
            "_account_from_user_id",
            return_value={"$id": "invitee", "email": "invitee@example.test"},
        ), patch.object(
            auth,
            "_complete_appwrite_login",
            return_value={"redirect": "/dashboard"},
        ):
            response = client.get(
                "/auth/appwrite/callback/state-token?userId=invitee&secret=secret"
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/dashboard")
        self.assertIsNone(client.get_cookie(auth.INVITE_COOKIE))

    def test_new_account_is_attributed_but_existing_account_is_not(self):
        invitation = invites.create_invite("owner")
        remote_user = {
            "$id": "new-user",
            "email": "new-user@example.test",
            "name": "New User",
        }

        def create_row(_collection, row_id=None, data=None, **_kwargs):
            return {"$id": row_id, **(data or {})}

        with self.app.test_request_context(
            "/auth/session",
            method="POST",
            headers={"Cookie": f"{auth.INVITE_COOKIE}={invitation['code']}"},
        ), patch.object(auth, "get_row_safe", return_value=None), patch.object(
            auth, "_find_user_by_email", return_value=None
        ), patch.object(
            auth, "_identities_for_appwrite_user", return_value=[]
        ), patch.object(
            auth, "_fetch_provider_profile", return_value={}
        ), patch.object(
            auth, "create_row_safe", side_effect=create_row
        ), patch.object(
            auth, "sync_chat_presence_labels_for_user"
        ), patch.object(
            auth, "login_user"
        ), patch.object(
            auth, "url_for", return_value="/onboarding"
        ), patch.object(
            auth, "emit_user_event"
        ), patch.object(
            auth.invites, "attribute_signup"
        ) as attribute_signup:
            auth._complete_appwrite_login(remote_user, provider="google")

        attribute_signup.assert_called_once_with(invitation["code"], "new-user")

        with self.app.test_request_context(
            "/auth/session",
            method="POST",
            headers={"Cookie": f"{auth.INVITE_COOKIE}={invitation['code']}"},
        ), patch.object(
            auth,
            "get_row_safe",
            return_value={
                "$id": "invitee",
                "email": "invitee@example.test",
                "name": "Invitee",
                "tier": "free",
            },
        ), patch.object(
            auth, "_identities_for_appwrite_user", return_value=[]
        ), patch.object(
            auth, "_fetch_provider_profile", return_value={}
        ), patch.object(
            auth, "update_row_safe", side_effect=lambda _table, _row, data: {
                "$id": "invitee",
                "email": "invitee@example.test",
                "name": "Invitee",
                "tier": "free",
                **data,
            }
        ), patch.object(
            auth, "sync_chat_presence_labels_for_user"
        ), patch.object(
            auth, "login_user"
        ), patch.object(
            auth, "url_for", return_value="/dashboard"
        ), patch.object(
            auth, "emit_user_event"
        ), patch.object(
            auth.invites, "attribute_signup"
        ) as attribute_signup:
            auth._complete_appwrite_login(
                {
                    "$id": "invitee",
                    "email": "invitee@example.test",
                    "name": "Invitee",
                },
                provider="google",
            )

        attribute_signup.assert_not_called()


if __name__ == "__main__":
    unittest.main()
