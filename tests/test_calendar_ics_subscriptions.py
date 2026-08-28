import json
import os
import re
import sqlite3
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

from flask import Flask
from types import SimpleNamespace

from extensions import login_manager
from services import database
import services.calendar_ics_contract as ics_contract
from services.calendar_ics_contract import (
    CalendarIcsContractError,
    CalendarIcsFailure,
    CalendarIcsFailureCode,
    CalendarIcsProjectionOutcome,
    CalendarIcsProjectionStatus,
    NormalizedCalendarEvent,
    SERIALIZER_CONTRACT,
    build_calendar_ics_uid,
    normalized_calendar_event_payload,
    subscription_window,
)
from services.calendar_events import _calendar_share_payload
from services.calendar_share_service import (
    _owner_allowlist,
    assert_selection_change_allowed,
    calendar_ics_enabled_for_owner,
    creation_ics_fields,
    disable_calendar_ics,
    enable_calendar_ics,
    new_ics_token,
    normalized_ics_selection,
    owner_ics_metadata,
    remove_calendar_ics,
    resolve_calendar_ics_token,
    rotate_calendar_ics,
    update_owned_calendar_share_with_invariants,
)
from services.calendar_store import calendar_connection
import blueprints.calendar_api as calendar_api


class CalendarIcsSubscriptionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = os.path.join(self.temp_dir.name, "calendar.sqlite3")
        self.env = patch.dict(os.environ, {
            "DATABASE_PATH": self.db_path,
            "FLASK_ENV": "testing",
        }, clear=False)
        self.env.start()
        self.addCleanup(self.env.stop)
        database.init_db(path=self.db_path)
        self.app = Flask(__name__)
        self.app.config.update(
            DATABASE_PATH=self.db_path,
            APP_BASE_URL="https://calendar.example.test",
            CALENDAR_ICS_SUBSCRIPTIONS_ENABLED=True,
            CALENDAR_ICS_SUBSCRIPTIONS_OWNER_ALLOWLIST="owner-1",
            TESTING=True,
        )
        self.app.secret_key = "test-secret"
        self.authenticated_users = {}
        previous_loader = login_manager._user_callback
        previous_unauthorized_callback = login_manager.unauthorized_callback
        previous_login_view = login_manager.login_view
        self.addCleanup(setattr, login_manager, "_user_callback", previous_loader)
        self.addCleanup(
            setattr,
            login_manager,
            "unauthorized_callback",
            previous_unauthorized_callback,
        )
        self.addCleanup(setattr, login_manager, "login_view", previous_login_view)
        login_manager.unauthorized_callback = None
        login_manager.login_view = None
        login_manager.init_app(self.app)
        login_manager._user_callback = lambda user_id: self.authenticated_users.get(user_id)
        self.app.register_blueprint(calendar_api.calendar_bp, url_prefix="/api/calendar")

    def insert_share(self, *, share_id="share-1", user_id="owner-1", calendar_id="canvas", token=None):
        with calendar_connection(self.db_path) as connection:
            connection.execute(
                """INSERT INTO calendar_shares
                   (id, user_id, share_code, is_active, include_all_calendars,
                    calendar_ids_json, date_scope, created_at, updated_at,
                    ics_token, ics_enabled, ics_issued_at, ics_rotated_at)
                   VALUES (?, ?, ?, 1, 0, ?, 'all', '2026-08-24T00:00:00Z',
                           '2026-08-24T00:00:00Z', ?, ?, ?, NULL)""",
                [
                    share_id, user_id, f"code-{share_id}", json.dumps([calendar_id]),
                    token, 1 if token else 0, "2026-08-24T00:00:00Z" if token else None,
                ],
            )

    def authenticated_client(self, user_id):
        self.authenticated_users[user_id] = SimpleNamespace(
            id=user_id,
            is_authenticated=True,
        )
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = user_id
            session["_fresh"] = True
        return client

    def test_migration_adds_defaults_and_partial_unique_index(self):
        with sqlite3.connect(self.db_path) as connection:
            columns = {row[1]: row[4] for row in connection.execute("PRAGMA table_info(calendar_shares)")}
            self.assertEqual(columns["ics_enabled"], "0")
            indexes = {
                row[1]: row[0]
                for row in connection.execute("PRAGMA index_list(calendar_shares)")
            }
            self.assertIn("idx_calendar_shares_ics_token", indexes)
            index_sql = connection.execute(
                "SELECT sql FROM sqlite_master WHERE name = 'idx_calendar_shares_ics_token'"
            ).fetchone()[0]
            self.assertIn("WHERE ics_token IS NOT NULL", index_sql)

    def test_eligibility_normalizes_legacy_ids_but_requires_one_calendar(self):
        self.assertEqual(normalized_ics_selection({
            "include_all_calendars": False,
            "calendar_ids_json": '["local:tasks"]',
        }), "tasks")
        self.assertEqual(normalized_ics_selection({
            "include_all_calendars": False,
            "calendar_ids_json": '["simulated_courses"]',
        }), "simulated_courses")
        self.assertIsNone(normalized_ics_selection({
            "include_all_calendars": True,
            "calendar_ids_json": '["canvas"]',
        }))
        self.assertIsNone(normalized_ics_selection({
            "include_all_calendars": False,
            "calendar_ids_json": '["canvas", "tasks"]',
        }))

    def test_lifecycle_retains_disable_reenable_rotates_and_removes_secret(self):
        self.insert_share()
        with self.app.app_context():
            enabled = enable_calendar_ics("owner-1", "share-1").share
            first_token = enabled["ics_token"]
            self.assertRegex(first_token, re.compile(r"^[A-Za-z0-9_-]{43}$"))
            self.assertEqual(len(first_token), 43)
            self.assertEqual(resolve_calendar_ics_token(first_token)["id"], "share-1")

            disabled = disable_calendar_ics("owner-1", "share-1").share
            self.assertFalse(disabled["ics_enabled"])
            self.assertEqual(disabled["ics_token"], first_token)
            with self.assertRaisesRegex(CalendarIcsFailure, "Invalid calendar ICS token"):
                resolve_calendar_ics_token(first_token)

            reenabled = enable_calendar_ics("owner-1", "share-1").share
            self.assertEqual(reenabled["ics_token"], first_token)
            rotated = rotate_calendar_ics("owner-1", "share-1").share
            second_token = rotated["ics_token"]
            self.assertNotEqual(second_token, first_token)
            with self.assertRaisesRegex(CalendarIcsFailure, "Invalid calendar ICS token"):
                resolve_calendar_ics_token(first_token)
            self.assertEqual(resolve_calendar_ics_token(second_token)["id"], "share-1")

            removed = remove_calendar_ics("owner-1", "share-1").share
            self.assertIsNone(removed["ics_token"])
            self.assertFalse(removed["ics_enabled"])
            with self.assertRaises(CalendarIcsFailure):
                resolve_calendar_ics_token(second_token)

    def test_owner_get_returns_secret_only_through_owner_route_and_parent_revoke_invalidates(self):
        self.insert_share(token="route-secret")
        with self.app.app_context():
            with self.app.test_request_context("/api/calendar/shares/share-1/ics"):
                with patch.object(calendar_api, "current_user", SimpleNamespace(id="owner-1")):
                    response = calendar_api.get_calendar_share_ics.__wrapped__("share-1")
            body = response.get_json()
            self.assertEqual(body["ics"]["token"], "route-secret")
            self.assertTrue(body["ics"]["httpsUrl"].startswith("https://calendar.example.test/"))
            self.assertTrue(body["ics"]["webcalUrl"].startswith("webcal://calendar.example.test/"))

            with self.app.test_request_context("/api/calendar/shares/share-1", method="DELETE"):
                with patch.object(calendar_api, "current_user", SimpleNamespace(id="owner-1")):
                    response = calendar_api.revoke_calendar_share.__wrapped__("share-1")
            self.assertFalse(response.get_json()["share"]["isActive"])
            with self.assertRaises(CalendarIcsFailure):
                resolve_calendar_ics_token("route-secret")

    def test_patch_parent_deactivation_clears_ics_before_regeneration(self):
        self.insert_share(token="patch-secret")
        with self.app.app_context():
            with self.app.test_request_context("/api/calendar/shares/share-1", method="PATCH", json={"isActive": False}):
                with patch.object(calendar_api, "current_user", SimpleNamespace(id="owner-1")):
                    response = calendar_api.update_calendar_share.__wrapped__("share-1")
            self.assertFalse(response.get_json()["share"]["isActive"])
            with self.assertRaises(CalendarIcsFailure):
                resolve_calendar_ics_token("patch-secret")
            with calendar_connection(self.db_path) as connection:
                row = connection.execute(
                    "SELECT is_active, ics_token, ics_enabled FROM calendar_shares WHERE id = ?",
                    ["share-1"],
                ).fetchone()
            self.assertEqual(tuple(row), (0, None, 0))

    def test_transactional_deactivation_overrides_stale_ics_fields(self):
        self.insert_share(token="current-secret")
        with self.app.app_context():
            updated = update_owned_calendar_share_with_invariants(
                "owner-1",
                "share-1",
                {
                    "is_active": False,
                    "ics_token": "racing-secret",
                    "ics_enabled": True,
                    "ics_issued_at": "2026-08-25T00:00:00Z",
                    "ics_rotated_at": "2026-08-25T00:00:00Z",
                },
            )
            self.assertFalse(updated["is_active"])
            self.assertIsNone(updated["ics_token"])
            self.assertFalse(updated["ics_enabled"])
            self.assertIsNone(updated["ics_issued_at"])
            self.assertIsNone(updated["ics_rotated_at"])
            with self.assertRaises(CalendarIcsFailure):
                resolve_calendar_ics_token("current-secret")
                resolve_calendar_ics_token("racing-secret")

        with calendar_connection(self.db_path) as connection:
            row = connection.execute(
                "SELECT is_active, ics_token, ics_enabled, ics_issued_at, ics_rotated_at "
                "FROM calendar_shares WHERE id = ?",
                ["share-1"],
            ).fetchone()
        self.assertEqual(tuple(row), (0, None, 0, None, None))

    def test_creation_opt_in_is_feature_and_allowlist_gated(self):
        normalized = {
            "include_all_calendars": False,
            "calendar_ids_json": '["canvas"]',
        }
        with self.app.app_context():
            fields = creation_ics_fields("owner-1", {"icsEnabled": True}, normalized)
            self.assertRegex(fields["ics_token"], r"^[A-Za-z0-9_-]{43}$")
            self.app.config["CALENDAR_ICS_SUBSCRIPTIONS_OWNER_ALLOWLIST"] = "other-owner"
            with self.assertRaisesRegex(CalendarIcsFailure, "not enabled") as error:
                creation_ics_fields("owner-1", {"icsEnabled": True}, normalized)
            self.assertEqual(error.exception.code, CalendarIcsFailureCode.DISABLED)

    def test_global_owner_entitlement_is_explicit_and_fail_closed(self):
        with self.app.app_context():
            self.app.config["CALENDAR_ICS_SUBSCRIPTIONS_ENABLED"] = False
            self.app.config["CALENDAR_ICS_SUBSCRIPTIONS_OWNER_ALLOWLIST"] = "*"
            self.assertFalse(calendar_ics_enabled_for_owner("owner-1"))
            self.assertFalse(calendar_ics_enabled_for_owner("owner-2"))

            self.app.config["CALENDAR_ICS_SUBSCRIPTIONS_ENABLED"] = True
            self.app.config["CALENDAR_ICS_SUBSCRIPTIONS_OWNER_ALLOWLIST"] = ""
            self.assertFalse(calendar_ics_enabled_for_owner("owner-1"))

            self.assertEqual(_owner_allowlist(), frozenset())

    def test_enabled_allowlist_supports_multiple_exact_owners_and_normalizes_config(self):
        with self.app.app_context():
            self.app.config["CALENDAR_ICS_SUBSCRIPTIONS_OWNER_ALLOWLIST"] = (
                " owner-1, owner-2, owner-1, , owner-2 "
            )
            self.assertEqual(_owner_allowlist(), frozenset({"owner-1", "owner-2"}))
            self.assertTrue(calendar_ics_enabled_for_owner("owner-1"))
            self.assertTrue(calendar_ics_enabled_for_owner("owner-2"))
            self.assertFalse(calendar_ics_enabled_for_owner("owner-3"))
            self.assertFalse(calendar_ics_enabled_for_owner(" owner-1 "))
            self.assertFalse(calendar_ics_enabled_for_owner(None))

    def test_enabled_wildcard_alone_grants_multiple_authenticated_owners(self):
        with self.app.app_context():
            self.app.config["CALENDAR_ICS_SUBSCRIPTIONS_OWNER_ALLOWLIST"] = " * "
            self.assertEqual(_owner_allowlist(), frozenset({"*"}))
            for owner_id in ("owner-1", "owner-2", "owner-3"):
                with self.subTest(owner_id=owner_id):
                    self.assertTrue(calendar_ics_enabled_for_owner(owner_id))

    def test_mixed_wildcard_ids_and_duplicate_whitespace_entries_are_deterministic(self):
        with self.app.app_context():
            self.app.config["CALENDAR_ICS_SUBSCRIPTIONS_OWNER_ALLOWLIST"] = (
                " owner-1, *, owner-1, , owner-2, * "
            )
            self.assertEqual(_owner_allowlist(), frozenset({"*"}))
            for owner_id in ("owner-1", "owner-2", "owner-3"):
                with self.subTest(owner_id=owner_id):
                    self.assertTrue(calendar_ics_enabled_for_owner(owner_id))

    def test_missing_environment_defaults_disable_ics_and_empty_allowlist(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(calendar_ics_enabled_for_owner("owner-1"))
            self.assertEqual(_owner_allowlist(), frozenset())

    def test_owner_ics_routes_enforce_authentication_and_ownership(self):
        self.insert_share(token="route-secret")
        self.app.config["CALENDAR_ICS_SUBSCRIPTIONS_OWNER_ALLOWLIST"] = "owner-1, owner-2"

        for method, path, kwargs in (
            ("GET", "/api/calendar/shares/share-1/ics", {}),
            ("POST", "/api/calendar/shares/share-1/ics", {"json": {"action": "enable"}}),
            ("DELETE", "/api/calendar/shares/share-1/ics", {}),
        ):
            with self.subTest(method=method, path=path):
                response = self.app.test_client().open(path, method=method, **kwargs)
                self.assertEqual(response.status_code, 401)

        client = self.authenticated_client("owner-2")
        for method, path, kwargs in (
            ("GET", "/api/calendar/shares/share-1/ics", {}),
            ("POST", "/api/calendar/shares/share-1/ics", {"json": {"action": "enable"}}),
            ("DELETE", "/api/calendar/shares/share-1/ics", {}),
        ):
            with self.subTest(method=method, path=path):
                response = client.open(path, method=method, **kwargs)
                self.assertEqual(response.status_code, 404)

    def test_unauthenticated_share_creation_is_rejected_by_the_http_route(self):
        response = self.app.test_client().post(
            "/api/calendar/shares",
            json={
                "includeAllCalendars": False,
                "calendarIds": ["simulated_courses"],
                "icsEnabled": True,
            },
        )
        self.assertEqual(response.status_code, 401)
        with calendar_connection(self.db_path) as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM calendar_shares").fetchone()[0],
                0,
            )

    def test_dedicated_lifecycle_routes_conceal_wrong_owner_and_preserve_state_transitions(self):
        self.insert_share(user_id="owner-1")
        self.app.config["CALENDAR_ICS_SUBSCRIPTIONS_OWNER_ALLOWLIST"] = "owner-1, owner-2"

        wrong_owner = self.authenticated_client("owner-2")
        for action in ("enable", "disable", "rotate"):
            with self.subTest(owner="owner-2", action=action):
                response = wrong_owner.post(f"/api/calendar/shares/share-1/ics/{action}")
                self.assertEqual(response.status_code, 404)

        with calendar_connection(self.db_path) as connection:
            untouched = connection.execute(
                "SELECT ics_token, ics_enabled FROM calendar_shares WHERE id = ?",
                ["share-1"],
            ).fetchone()
        self.assertEqual(tuple(untouched), (None, 0))

        owner = self.authenticated_client("owner-1")
        enabled = owner.post("/api/calendar/shares/share-1/ics/enable")
        self.assertEqual(enabled.status_code, 200)
        self.assertTrue(enabled.get_json()["share"]["icsConfigured"])
        self.assertTrue(enabled.get_json()["share"]["icsEnabled"])
        with calendar_connection(self.db_path) as connection:
            first_token = connection.execute(
                "SELECT ics_token FROM calendar_shares WHERE id = ?", ["share-1"]
            ).fetchone()[0]
        self.assertTrue(first_token)

        disabled = owner.post("/api/calendar/shares/share-1/ics/disable")
        self.assertEqual(disabled.status_code, 200)
        self.assertFalse(disabled.get_json()["share"]["icsEnabled"])
        with calendar_connection(self.db_path) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT ics_token FROM calendar_shares WHERE id = ?", ["share-1"]
                ).fetchone()[0],
                first_token,
            )

        reenabled = owner.post("/api/calendar/shares/share-1/ics/enable")
        self.assertEqual(reenabled.status_code, 200)
        self.assertTrue(reenabled.get_json()["share"]["icsEnabled"])

        rotated = owner.post("/api/calendar/shares/share-1/ics/rotate")
        self.assertEqual(rotated.status_code, 200)
        self.assertTrue(rotated.get_json()["share"]["icsConfigured"])
        self.assertTrue(rotated.get_json()["share"]["icsEnabled"])
        with calendar_connection(self.db_path) as connection:
            second_token = connection.execute(
                "SELECT ics_token FROM calendar_shares WHERE id = ?", ["share-1"]
            ).fetchone()[0]
        self.assertTrue(second_token)
        self.assertNotEqual(second_token, first_token)

    def test_existing_public_feed_rechecks_current_entitlement_without_rotating_token(self):
        self.app.config["CALENDAR_ICS_SUBSCRIPTIONS_OWNER_ALLOWLIST"] = "*"
        self.insert_share(token="public-secret")
        document = SimpleNamespace(content=b"BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n", etag='"etag"')
        with patch.object(calendar_api, "build_calendar_ics_feed", return_value=(document, None)):
            client = self.app.test_client()
            self.assertEqual(
                client.get("/api/calendar/share-feed.ics?token=public-secret").status_code,
                200,
            )

            self.app.config["CALENDAR_ICS_SUBSCRIPTIONS_ENABLED"] = False
            self.assertEqual(
                client.get("/api/calendar/share-feed.ics?token=public-secret").status_code,
                404,
            )

            self.app.config["CALENDAR_ICS_SUBSCRIPTIONS_ENABLED"] = True
            self.app.config["CALENDAR_ICS_SUBSCRIPTIONS_OWNER_ALLOWLIST"] = ""
            self.assertEqual(
                client.get("/api/calendar/share-feed.ics?token=public-secret").status_code,
                404,
            )

            self.app.config["CALENDAR_ICS_SUBSCRIPTIONS_OWNER_ALLOWLIST"] = "*"
            self.assertEqual(
                client.get("/api/calendar/share-feed.ics?token=public-secret").status_code,
                200,
            )

        with calendar_connection(self.db_path) as connection:
            row = connection.execute(
                "SELECT ics_token, ics_enabled FROM calendar_shares WHERE id = ?",
                ["share-1"],
            ).fetchone()
        self.assertEqual(tuple(row), ("public-secret", 1))

    def test_wildcard_does_not_change_public_token_or_payload_redaction(self):
        self.app.config["CALENDAR_ICS_SUBSCRIPTIONS_OWNER_ALLOWLIST"] = "*"
        self.insert_share(token="public-secret")
        with self.app.app_context():
            resolved = resolve_calendar_ics_token("public-secret")
            self.assertEqual(resolved["id"], "share-1")
            with self.assertRaises(CalendarIcsFailure):
                resolve_calendar_ics_token("not-the-token")

            payload = _calendar_share_payload(resolved)
            self.assertNotIn("public-secret", json.dumps(payload))
            self.assertNotIn("httpsUrl", payload)
            self.assertNotIn("webcalUrl", payload)

    def test_selection_lock_and_browser_regeneration_independence(self):
        self.insert_share(token=new_ics_token())
        with self.app.app_context():
            share = resolve_calendar_ics_token(
                self._token_for("share-1")
            )
            with self.assertRaises(CalendarIcsFailure) as locked:
                assert_selection_change_allowed(
                    share,
                    {"calendar_ids_json": json.dumps(["tasks"])},
                )
            self.assertEqual(locked.exception.code, CalendarIcsFailureCode.SELECTION_LOCKED)
            assert_selection_change_allowed(
                share,
                {"calendar_ids_json": json.dumps(["canvas"])},
            )
            token_before = share["ics_token"]
            with calendar_connection(self.db_path) as connection:
                connection.execute(
                    "UPDATE calendar_shares SET share_code = ? WHERE id = ?",
                    ["regenerated-code", "share-1"],
                )
            self.assertEqual(resolve_calendar_ics_token(token_before)["share_code"], "regenerated-code")

    def test_feature_allowlist_and_owner_urls_are_fail_closed_and_secret_free_elsewhere(self):
        with self.app.app_context():
            self.assertTrue(calendar_ics_enabled_for_owner("owner-1"))
            self.assertFalse(calendar_ics_enabled_for_owner("owner-2"))
        self.insert_share(token="secret-token")
        with self.app.app_context():
            share = resolve_calendar_ics_token("secret-token")
            metadata = owner_ics_metadata(share)
            self.assertEqual(metadata["token"], "secret-token")
            self.assertEqual(
                metadata["httpsUrl"],
                "https://calendar.example.test/api/calendar/share-feed.ics?token=secret-token",
            )
            self.assertEqual(
                metadata["webcalUrl"],
                "webcal://calendar.example.test/api/calendar/share-feed.ics?token=secret-token",
            )
            ordinary_payload = _calendar_share_payload(share)
            self.assertNotIn("ics_token", ordinary_payload)
            self.assertNotIn("httpsUrl", ordinary_payload)
            self.assertNotIn("webcalUrl", ordinary_payload)
            self.assertTrue(ordinary_payload["icsConfigured"])

    def test_simulated_courses_normalization_persists_for_create_edit_and_enable(self):
        canonical = calendar_api._normalize_calendar_share_payload({
            "includeAllCalendars": False,
            "calendarIds": ["simulated_courses"],
        })
        display = calendar_api._normalize_calendar_share_payload({
            "includeAllCalendars": False,
            "calendarIds": ["Simulated Courses"],
        })
        self.assertEqual(canonical["calendar_ids_json"], '["simulated_courses"]')
        self.assertEqual(display["calendar_ids_json"], '["simulated_courses"]')
        self.assertEqual(
            normalized_ics_selection({
                "include_all_calendars": False,
                "calendar_ids_json": display["calendar_ids_json"],
            }),
            "simulated_courses",
        )
        all_calendars = calendar_api._normalize_calendar_share_payload({
            "includeAllCalendars": True,
            "calendarIds": ["simulated_courses"],
        })
        self.assertTrue(all_calendars["include_all_calendars"])
        self.assertEqual(all_calendars["calendar_ids_json"], "[]")

        with self.app.app_context():
            with self.app.test_request_context("/api/calendar/shares", method="POST", json={
                "includeAllCalendars": False,
                "calendarIds": ["Simulated Courses"],
                "icsEnabled": True,
            }):
                with patch.object(calendar_api, "current_user", SimpleNamespace(id="owner-1")), \
                        patch.object(calendar_api, "_generate_calendar_share_code", return_value="simulated-code"), \
                        patch.object(calendar_api, "emit_creation_event"):
                    response, status = calendar_api.create_calendar_share.__wrapped__()
            self.assertEqual(status, 201)
            share_id = response.get_json()["share"]["id"]
            with calendar_connection(self.db_path) as connection:
                row = connection.execute(
                    "SELECT include_all_calendars, calendar_ids_json, ics_token FROM calendar_shares WHERE id = ?",
                    [share_id],
                ).fetchone()
            self.assertEqual(tuple(row[:2]), (0, '["simulated_courses"]'))
            self.assertTrue(row[2])

            with self.app.test_request_context(f"/api/calendar/shares/{share_id}", method="PATCH", json={
                "includeAllCalendars": False,
                "calendarIds": ["simulated_courses"],
            }):
                with patch.object(calendar_api, "current_user", SimpleNamespace(id="owner-1")):
                    response = calendar_api.update_calendar_share.__wrapped__(share_id)
            self.assertEqual(response.get_json()["share"]["calendarIds"], ["simulated_courses"])

    def test_simulated_courses_ics_post_route_returns_created_metadata(self):
        client = self.authenticated_client("owner-1")
        with patch.object(calendar_api, "_generate_calendar_share_code", return_value="simulated-route-code"), \
                patch.object(calendar_api, "emit_creation_event"):
            response = client.post("/api/calendar/shares", json={
                "includeAllCalendars": False,
                "calendarIds": ["Simulated Courses"],
                "icsEnabled": True,
            })

        self.assertEqual(response.status_code, 201)
        share = response.get_json()["share"]
        self.assertEqual(share["calendarIds"], ["simulated_courses"])
        self.assertTrue(share["icsConfigured"])
        self.assertTrue(share["icsEnabled"])
        with calendar_connection(self.db_path) as connection:
            token = connection.execute(
                "SELECT ics_token FROM calendar_shares WHERE id = ?",
                [share["id"]],
            ).fetchone()[0]
        self.assertTrue(token)

    def test_invalid_create_bodies_leave_the_calendar_share_count_unchanged(self):
        client = self.authenticated_client("owner-1")
        invalid_requests = [
            {"data": " \t\n", "content_type": "application/json"},
            {"data": '{"calendarIds":', "content_type": "application/json"},
            {"data": "not-json", "content_type": "text/plain"},
            {"data": "null", "content_type": "application/json"},
            {"data": '"text"', "content_type": "application/json"},
            {"data": "[\"simulated_courses\"]", "content_type": "application/json"},
            {"data": "17", "content_type": "application/json"},
            {"data": "true", "content_type": "application/json"},
        ]
        expected = {
            "error": "Calendar share payload must be a JSON object.",
            "code": "calendar_share_invalid_payload",
        }
        for request_kwargs in invalid_requests:
            with self.subTest(request=request_kwargs):
                with calendar_connection(self.db_path) as connection:
                    before = connection.execute("SELECT COUNT(*) FROM calendar_shares").fetchone()[0]
                response = client.post("/api/calendar/shares", **request_kwargs)
                with calendar_connection(self.db_path) as connection:
                    after = connection.execute("SELECT COUNT(*) FROM calendar_shares").fetchone()[0]
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.get_json(), expected)
                self.assertEqual(after, before)

    def test_contract_uid_invariants_outcomes_diagnostics_and_serializer_metadata(self):
        secret = "s" * 32
        with patch.object(ics_contract, "CALENDAR_ICS_UID_SECRET", secret):
            first = build_calendar_ics_uid("canvas", "private-event-1")
            self.assertEqual(first, build_calendar_ics_uid("canvas", "private-event-1"))
            self.assertNotEqual(first, build_calendar_ics_uid("canvas", "private-event-2"))
            self.assertNotEqual(first, build_calendar_ics_uid("tasks", "private-event-1"))
            event = NormalizedCalendarEvent.from_internal(
                raw_identity="private-event-1",
                calendar_id="canvas",
                source_type="canvas",
                title="Exam",
                start=datetime(2026, 8, 24, 14, tzinfo=timezone.utc),
                end=datetime(2026, 8, 24, 15, tzinfo=timezone.utc),
                is_all_day=False,
                description="Visible description",
                course_name="Course",
                course_code="BIO 101",
                section="001",
                instructor="Instructor",
                course_location="Room 1",
                notes="Visible notes",
                crn="12345",
                last_modified=datetime(2026, 8, 23, 12, tzinfo=timezone.utc),
            )
            payload = normalized_calendar_event_payload(event)
            self.assertNotIn("private-event-1", json.dumps(payload, default=str))
            self.assertNotIn("raw_identity", payload)

        for unsafe in (None, "", "  ", "short", "s" * 31, "s" * 31 + " "):
            with patch.object(ics_contract, "CALENDAR_ICS_UID_SECRET", unsafe):
                with self.assertRaises(CalendarIcsContractError):
                    build_calendar_ics_uid("canvas", "private-event")

        with self.assertRaises(CalendarIcsContractError):
            NormalizedCalendarEvent(
                uid="uid", calendar_id="canvas", source_type="canvas", title="Event",
                start=datetime(2026, 8, 24, 14), end=datetime(2026, 8, 24, 15), is_all_day=False,
            )
        with self.assertRaises(CalendarIcsContractError):
            NormalizedCalendarEvent(
                uid="uid", calendar_id="canvas", source_type="canvas", title="Event",
                start=date(2026, 8, 24), end=date(2026, 8, 25), is_all_day=False,
            )
        with self.assertRaises(CalendarIcsContractError):
            NormalizedCalendarEvent(
                uid="uid", calendar_id="canvas", source_type="canvas", title="Event",
                start=datetime(2026, 8, 24, 14, tzinfo=timezone.utc),
                end=datetime(2026, 8, 24, 15, tzinfo=timezone.utc), is_all_day=True,
            )
        with self.assertRaises(CalendarIcsContractError):
            NormalizedCalendarEvent(
                uid="uid", calendar_id="canvas", source_type="canvas", title="Event",
                start=date(2026, 8, 24), end=date(2026, 8, 24), is_all_day=True,
            )

        outcome = CalendarIcsProjectionOutcome.source_failure(
            "future_source_code", "Future diagnostic text must survive unchanged."
        )
        self.assertEqual(outcome.status, CalendarIcsProjectionStatus.SOURCE_FAILURE)
        self.assertEqual(outcome.diagnostic_code, "future_source_code")
        self.assertEqual(outcome.diagnostic_text, "Future diagnostic text must survive unchanged.")
        self.assertEqual(
            CalendarIcsFailure("future_failure_code", "diagnostic").payload()["code"],
            "future_failure_code",
        )
        self.assertEqual(CalendarIcsProjectionOutcome.valid_empty().status, CalendarIcsProjectionStatus.VALID_EMPTY)
        self.assertEqual(CalendarIcsProjectionOutcome.resource_failure("resource", "text").status, CalendarIcsProjectionStatus.RESOURCE_FAILURE)
        self.assertEqual(SERIALIZER_CONTRACT.method, "PUBLISH")
        self.assertEqual(SERIALIZER_CONTRACT.method_line, "METHOD:PUBLISH")
        self.assertTrue(SERIALIZER_CONTRACT.omit_sequence)
        self.assertTrue(SERIALIZER_CONTRACT.omit_vtimezone)
        self.assertEqual(SERIALIZER_CONTRACT.etag_excluded_properties, ("DTSTAMP",))
        self.assertEqual(subscription_window(date(2026, 8, 24)), (date(2026, 7, 25), date(2027, 8, 26)))

    def _token_for(self, share_id):
        with calendar_connection(self.db_path) as connection:
            return connection.execute(
                "SELECT ics_token FROM calendar_shares WHERE id = ?", [share_id]
            ).fetchone()[0]


if __name__ == "__main__":
    unittest.main()
