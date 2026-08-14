import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import app as app_module
from extensions import login_manager
from services import database
from services.extension_contract import (
    EXTENSION_CAPABILITIES,
    canonical_canvas_source_key,
    extension_capability_enabled,
    validate_source_key,
)


CANVAS_ACCOUNT_1 = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
CANVAS_ACCOUNT_2 = "fedcba9876543210fedcba9876543210fedcba9876543210fedcba9876543210"
CANVAS_SOURCE_KEY_1 = canonical_canvas_source_key(CANVAS_ACCOUNT_1)


class ExtensionApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = os.path.join(self.temp_dir.name, "extension.sqlite3")
        self.env = patch.dict(os.environ, {
            "DATABASE_PATH": self.db_path,
            "FLASK_SECRET_KEY": "extension-test-key",
            "FLASK_ENV": "testing",
            "APSTUDY_ALLOW_INSECURE_HTTP": "1",
            "SCHEDULER_ENABLED": "0",
        }, clear=False)
        self.env.start()
        self.addCleanup(self.env.stop)
        with patch("services.discord_audit.init_discord_audit"), \
                patch("services.scheduler.init_scheduler"):
            self.app = app_module.create_app()
        self.app.config.update(TESTING=True)
        self.users = {
            "user-1": SimpleNamespace(
                id="user-1",
                is_authenticated=True,
                name="One Student",
                username="one",
                picture_url="https://avatars.example.test/one.png",
            ),
            "user-2": SimpleNamespace(
                id="user-2",
                is_authenticated=True,
                name="Two Student",
                username="two",
                picture_url="http://avatars.example.test/two.png",
            ),
        }
        previous_loader = login_manager._user_callback
        self.addCleanup(setattr, login_manager, "_user_callback", previous_loader)
        login_manager._user_callback = lambda user_id: self.users.get(user_id)

    def enable_capabilities(self, *capabilities):
        configured = dict.fromkeys(EXTENSION_CAPABILITIES, False)
        configured["calendar_integration"] = True
        for capability in capabilities:
            configured[capability] = True
        self.app.config.update(
            EXTENSION_CALENDAR_INTEGRATION_ENABLED=True,
            EXTENSION_CAPABILITIES=configured,
        )

    def _client(self, user_id=None):
        client = self.app.test_client()
        if user_id:
            with client.session_transaction() as session:
                session["_user_id"] = user_id
                session["_fresh"] = True
        return client

    def _csrf_token(self, client):
        response = client.get("/api/extension/csrf")
        self.assertEqual(response.status_code, 200)
        token = response.get_json()["csrfToken"]
        self.assertEqual(client.get_cookie("csrf_token").value, token)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        return token

    def test_identity_is_minimal_authenticated_and_no_store(self):
        response = self._client("user-1").get("/api/extension/identity")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(response.get_json(), {
            "contractVersion": 1,
            "state": "authenticated",
            "profile": {
                "id": "user-1",
                "displayName": "One Student",
                "username": "one",
                "avatarUrl": "https://avatars.example.test/one.png",
            },
        })
        self.assertNotIn("email", response.get_json())
        self.assertNotIn("settings", response.get_json())
        self.assertTrue(response.headers.get("X-Request-ID"))

    def test_identity_rejects_non_https_avatar_without_leaking_other_profile_data(self):
        response = self._client("user-2").get("/api/extension/identity")

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.get_json()["profile"]["avatarUrl"])
        self.assertEqual(set(response.get_json()), {"contractVersion", "state", "profile"})

    def test_signed_out_identity_is_json_401_and_no_store(self):
        response = self._client().get("/api/extension/identity")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(response.get_json(), {
            "contractVersion": 1,
            "state": "signed_out",
        })
        self.assertNotIn("<html", response.get_data(as_text=True).lower())

    def test_extension_csrf_is_session_bound_and_mutation_middleware_accepts_it(self):
        client = self._client("user-1")
        rejected = client.put(
            "/api/extension/consent",
            json={
                "source_key": "canvas",
                "account_key": CANVAS_ACCOUNT_1,
                "action": "grant",
                "scopes": ["full_history_upload", "ongoing_read", "shares_ics_inclusion"],
            },
        )
        self.assertEqual(rejected.status_code, 400)
        self.assertEqual(rejected.headers.get("X-APStudy-CSRF-Error"), "1")

        token = self._csrf_token(client)
        accepted = client.put(
            "/api/extension/consent",
            json={
                "version": 1,
                "source_key": CANVAS_SOURCE_KEY_1,
                "account_key": CANVAS_ACCOUNT_1,
                "action": "grant",
                "scopes": ["full_history_upload", "ongoing_read", "shares_ics_inclusion"],
            },
            headers={"X-CSRFToken": token},
        )
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(accepted.get_json()["consent"]["state"], "active")

    def test_csrf_endpoint_requires_authentication(self):
        response = self._client().get("/api/extension/csrf")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(response.get_json()["state"], "signed_out")

    def test_consent_grant_get_revoke_is_idempotent_and_exposes_phase_2_state(self):
        client = self._client("user-1")
        token = self._csrf_token(client)
        grant_payload = {
            "version": 1,
            "source_key": CANVAS_SOURCE_KEY_1,
            "account_key": CANVAS_ACCOUNT_1,
            "action": "grant",
            "scopes": ["full_history_upload", "ongoing_read", "shares_ics_inclusion"],
        }

        first = client.put("/api/extension/consent", json=grant_payload, headers={"X-CSRFToken": token})
        second = client.put("/api/extension/consent", json=grant_payload, headers={"X-CSRFToken": token})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.get_json()["consent"], second.get_json()["consent"])

        fetched = client.get(
            f"/api/extension/consent?source_key={CANVAS_SOURCE_KEY_1}&account_key={CANVAS_ACCOUNT_1}&version=1"
        )
        self.assertEqual(fetched.status_code, 200)
        self.assertEqual(fetched.get_json()["consent"]["granted_scopes"], [
            "full_history_upload", "ongoing_read", "shares_ics_inclusion",
        ])

        revoke = client.put(
            "/api/extension/consent",
            json={**grant_payload, "action": "revoke", "scopes": ["ongoing_read", "full_history_upload", "shares_ics_inclusion"]},
            headers={"X-CSRFToken": token},
        )
        revoke_again = client.put(
            "/api/extension/consent",
            json={**grant_payload, "action": "revoke", "scopes": ["ongoing_read", "full_history_upload", "shares_ics_inclusion"]},
            headers={"X-CSRFToken": token},
        )
        consent = revoke.get_json()["consent"]
        self.assertEqual(revoke.status_code, 200)
        self.assertEqual(revoke_again.status_code, 200)
        self.assertEqual(consent["state"], "revoked")
        self.assertEqual(consent["revocation"], {
            "state": "revoked",
            "cancellation": "deferred_to_phase_2",
            "archive": "deferred_to_phase_2",
        })
        self.assertEqual(revoke.get_json()["consent"], revoke_again.get_json()["consent"])

    def test_canvas_source_key_is_the_received_account_key_without_double_hash(self):
        self.assertEqual(
            canonical_canvas_source_key(CANVAS_ACCOUNT_1),
            f"canvas:{CANVAS_ACCOUNT_1}",
        )
        with self.assertRaisesRegex(ValueError, "derived from account_key"):
            validate_source_key(
                "canvas:" + ("f" * 64),
                account_key=CANVAS_ACCOUNT_1,
            )

    def test_older_incomplete_grant_is_not_current_or_authorizing(self):
        client = self._client("user-1")
        token = self._csrf_token(client)
        granted = client.put(
            "/api/extension/consent",
            json={
                "version": 1,
                "source_key": CANVAS_SOURCE_KEY_1,
                "account_key": CANVAS_ACCOUNT_1,
                "action": "grant",
                "scopes": ["full_history_upload", "ongoing_read", "shares_ics_inclusion"],
            },
            headers={"X-CSRFToken": token},
        )
        self.assertEqual(granted.status_code, 200)
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                "UPDATE calendar_integration_consents SET scopes_json = ?, state = 'active'",
                [json.dumps({
                    "full_history_upload": True,
                    "ongoing_read": True,
                    "two_way_writeback": False,
                    "mirroring": False,
                    "shares_ics_inclusion": False,
                })],
            )
            connection.commit()
        response = client.get(
            f"/api/extension/consent?source_key={CANVAS_SOURCE_KEY_1}&account_key={CANVAS_ACCOUNT_1}"
        )
        self.assertEqual(response.status_code, 200)
        consent = response.get_json()["consent"]
        self.assertFalse(consent["current"])
        self.assertFalse(consent["granted"])


    def test_consent_isolated_by_user_source_and_account(self):
        client = self._client("user-1")
        token = self._csrf_token(client)
        response = client.put(
            "/api/extension/consent",
            json={
                "source_key": CANVAS_SOURCE_KEY_1,
                "account_key": CANVAS_ACCOUNT_1,
                "action": "grant",
                "scopes": ["full_history_upload", "ongoing_read", "shares_ics_inclusion"],
            },
            headers={"X-CSRFToken": token},
        )
        self.assertEqual(response.status_code, 200)

        self.assertEqual(client.get(
            f"/api/extension/consent?source_key={canonical_canvas_source_key(CANVAS_ACCOUNT_2)}&account_key={CANVAS_ACCOUNT_2}"
        ).get_json()["consent"]["state"], "not_granted")

        other_user = self._client("user-2")
        self.assertEqual(other_user.get(
            f"/api/extension/consent?source_key={CANVAS_SOURCE_KEY_1}&account_key={CANVAS_ACCOUNT_1}"
        ).get_json()["consent"]["state"], "not_granted")

    def test_mirroring_and_writeback_consent_require_enabled_capabilities(self):
        client = self._client("user-1")
        token = self._csrf_token(client)
        common = {
            "version": 1,
            "source_key": CANVAS_SOURCE_KEY_1,
            "account_key": CANVAS_ACCOUNT_1,
            "action": "grant",
        }
        mirroring_disabled = client.put(
            "/api/extension/consent",
            json={**common, "scopes": ["mirroring"]},
            headers={"X-CSRFToken": token},
        )
        self.assertEqual(mirroring_disabled.status_code, 400)
        self.assertEqual(mirroring_disabled.get_json()["error"]["code"], "exact_scope_set_required")

        self.enable_capabilities("calendar_mirroring")
        mirroring_enabled = client.put(
            "/api/extension/consent",
            json={**common, "scopes": ["mirroring"]},
            headers={"X-CSRFToken": token},
        )
        self.assertEqual(mirroring_enabled.status_code, 400)
        self.assertEqual(mirroring_enabled.get_json()["error"]["code"], "exact_scope_set_required")

        writeback_disabled = client.put(
            "/api/extension/consent",
            json={**common, "scopes": ["two_way_writeback"]},
            headers={"X-CSRFToken": token},
        )
        self.assertEqual(writeback_disabled.status_code, 400)
        self.assertEqual(writeback_disabled.get_json()["error"]["code"], "exact_scope_set_required")

    def test_consent_rejects_invalid_scope_version_and_source(self):
        client = self._client("user-1")
        token = self._csrf_token(client)
        common = {
            "source_key": CANVAS_SOURCE_KEY_1,
            "account_key": CANVAS_ACCOUNT_1,
            "action": "grant",
            "scopes": ["full_history_upload", "ongoing_read", "shares_ics_inclusion"],
        }
        cases = [
            ({**common, "version": 2}, "unsupported_version"),
            ({**common, "scopes": ["credentials"]}, "invalid_scope"),
            ({**common, "source_key": "Canvas Source"}, "invalid_source_key"),
            ({**common, "source_key": "canvas:" + "A" * 64}, "invalid_source_key"),
            ({**common, "source_key": "canvas:" + "0" * 63}, "invalid_source_key"),
            ({**common, "account_key": "canvas account"}, "invalid_account_key"),
        ]
        for payload, code in cases:
            response = client.put(
                "/api/extension/consent",
                json=payload,
                headers={"X-CSRFToken": token},
            )
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.get_json()["error"]["code"], code)

        get_response = client.get(
            f"/api/extension/consent?source_key={CANVAS_SOURCE_KEY_1}&account_key={CANVAS_ACCOUNT_1}&version=2"
        )
        self.assertEqual(get_response.status_code, 400)
        self.assertEqual(get_response.get_json()["error"]["code"], "unsupported_version")

    def test_consent_migration_is_re_runnable_and_preserves_unique_scope(self):
        with sqlite3.connect(self.db_path) as connection:
            first_schema = connection.execute(
                "SELECT sql FROM sqlite_master WHERE name = 'calendar_integration_consents'"
            ).fetchone()[0]
            first_versions = connection.execute(
                "SELECT COUNT(*) FROM schema_migrations WHERE version = '017_extension_calendar_consents'"
            ).fetchone()[0]
        database.init_db(path=self.db_path)
        with sqlite3.connect(self.db_path) as connection:
            second_schema = connection.execute(
                "SELECT sql FROM sqlite_master WHERE name = 'calendar_integration_consents'"
            ).fetchone()[0]
            second_versions = connection.execute(
                "SELECT COUNT(*) FROM schema_migrations WHERE version = '017_extension_calendar_consents'"
            ).fetchone()[0]
        self.assertEqual(first_schema, second_schema)
        self.assertEqual(first_versions, 1)
        self.assertEqual(second_versions, 1)

    def test_legacy_canvas_source_key_remains_readable_during_transition(self):
        client = self._client("user-1")
        token = self._csrf_token(client)
        response = client.put(
            "/api/extension/consent",
            json={
                "version": 1,
                "source_key": "canvas",
                "account_key": CANVAS_ACCOUNT_1,
                "action": "grant",
                "scopes": ["full_history_upload", "ongoing_read", "shares_ics_inclusion"],
            },
            headers={"X-CSRFToken": token},
        )
        self.assertEqual(response.status_code, 200)
        fetched = client.get(
            f"/api/extension/consent?source_key=canvas&account_key={CANVAS_ACCOUNT_1}"
        )
        self.assertEqual(fetched.status_code, 200)
        self.assertEqual(fetched.get_json()["consent"]["sourceKey"], CANVAS_SOURCE_KEY_1)

    def test_two_accounts_revoke_only_one_account(self):
        client = self._client("user-1")
        token = self._csrf_token(client)
        account_2 = CANVAS_ACCOUNT_2
        for account_key in (CANVAS_ACCOUNT_1, account_2):
            response = client.put(
                "/api/extension/consent",
                json={
                    "version": 1,
                    "source_key": canonical_canvas_source_key(account_key),
                    "account_key": account_key,
                    "action": "grant",
                    "scopes": ["full_history_upload", "ongoing_read", "shares_ics_inclusion"],
                },
                headers={"X-CSRFToken": token},
            )
            self.assertEqual(response.status_code, 200)

        revoked = client.put(
            "/api/extension/consent",
            json={
                "version": 1,
                "source_key": CANVAS_SOURCE_KEY_1,
                "account_key": CANVAS_ACCOUNT_1,
                "action": "revoke",
                "scopes": ["full_history_upload", "ongoing_read", "shares_ics_inclusion"],
            },
            headers={"X-CSRFToken": token},
        )
        self.assertEqual(revoked.status_code, 200)
        self.assertEqual(revoked.get_json()["consent"]["state"], "revoked")

        remaining = client.get(
            f"/api/extension/consent?source_key={canonical_canvas_source_key(account_2)}&account_key={account_2}"
        )
        self.assertEqual(remaining.status_code, 200)
        self.assertTrue(remaining.get_json()["consent"]["granted"])
        self.assertEqual(
            remaining.get_json()["consent"]["scopes"],
            ["full_history_upload", "ongoing_read", "shares_ics_inclusion"],
        )

    def test_cross_contract_v1_request_and_response_fields_match_fixture(self):
        fixture_path = Path(__file__).parent / "fixtures" / "extension_contract_v1.json"
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        client = self._client("user-1")
        token = self._csrf_token(client)
        response = client.put(
            "/api/extension/consent",
            json=fixture["request"],
            headers={"X-CSRFToken": token},
        )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        payload = response.get_json()
        self.assertEqual(set(payload), set(fixture["response_fields"]["envelope"]))
        self.assertEqual(set(payload["consent"]), set(fixture["response_fields"]["consent"]))
        self.assertEqual(payload["consent"]["version"], 1)
        self.assertTrue(payload["consent"]["current"])
        self.assertTrue(payload["consent"]["granted"])
        self.assertEqual(payload["consent"]["scopes"], fixture["request"]["scopes"])
        for field in ("version", "current", "granted", "scopes", "sourceKey"):
            self.assertEqual(payload[field], payload["consent"][field])

    def test_capabilities_are_fail_closed_and_not_cached_between_app_instances(self):
        self.enable_capabilities("calendar_read")
        self.assertTrue(extension_capability_enabled("calendar_read", app=self.app))
        with patch("services.discord_audit.init_discord_audit"), \
                patch("services.scheduler.init_scheduler"):
            other_app = app_module.create_app()
        other_app.config.update(TESTING=True)
        self.assertFalse(extension_capability_enabled("calendar_read", app=other_app))


if __name__ == "__main__":
    unittest.main()
