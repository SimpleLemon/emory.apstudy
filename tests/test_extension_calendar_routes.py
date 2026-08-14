import sqlite3
import os
import json
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import app as app_module
from extensions import login_manager
from services import calendar_events
from services.extension_contract import EXTENSION_CAPABILITIES, canonical_canvas_source_key


ACCOUNT_1 = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
ACCOUNT_2 = "fedcba9876543210fedcba9876543210fedcba9876543210fedcba9876543210"


class ExtensionCalendarRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = os.path.join(self.temp_dir.name, "calendar-routes.sqlite3")
        self.env = patch.dict(os.environ, {
            "DATABASE_PATH": self.db_path,
            "FLASK_SECRET_KEY": "calendar-route-test-key",
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
            "user-1": SimpleNamespace(id="user-1", is_authenticated=True, name="One", username="one"),
            "user-2": SimpleNamespace(id="user-2", is_authenticated=True, name="Two", username="two"),
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

    def client(self, user_id=None):
        client = self.app.test_client()
        if user_id:
            with client.session_transaction() as session:
                session["_user_id"] = user_id
                session["_fresh"] = True
        return client

    def csrf(self, client):
        return client.get("/api/extension/csrf").get_json()["csrfToken"]

    def grant(self, client, scopes=None, *, account_key=ACCOUNT_1, capabilities=()):
        self.enable_capabilities(*capabilities)
        token = self.csrf(client)
        response = client.put(
            "/api/extension/consent",
            json={
                "version": 1,
                "source_key": canonical_canvas_source_key(account_key),
                "account_key": account_key,
                "action": "grant",
                "scopes": scopes or ["full_history_upload", "ongoing_read", "shares_ics_inclusion"],
            },
            headers={"X-CSRFToken": token},
        )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))

    def register_source(self, client, *, account_key=ACCOUNT_1, source_id="source-1"):
        token = self.csrf(client)
        response = client.post(
            "/api/extension/calendar/sources",
            json={
                "account_key": account_key,
                "source_id": source_id,
                "origin": "https://canvas.example.edu",
                "provider_user_id": "canvas-user-1",
                "label": "Canvas",
                "consent_version": 1,
            },
            headers={"X-CSRFToken": token, "X-Request-ID": "route-test-1"},
        )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.get_json()["source"]

    def item(self):
        return {
            "context_id": "course-1",
            "calendar_id": "calendar-1",
            "item_type": "assignment",
            "item_id": "assignment-1",
            "title": "Read chapter 1",
            "start": "2026-08-12T10:00:00Z",
            "end": "2026-08-12T11:00:00Z",
            "source_revision": "r1",
            "completion_status": "incomplete",
            "completion_source": "canvas",
        }

    def seed_calendar_destinations(self):
        calendar_events._ensure_user_settings("user-1")
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                "UPDATE user_settings SET canvas_ical_url = ?, other_ical_urls_json = ? WHERE user_id = ?",
                [
                    "https://canvas.example.edu/feeds/calendars/private-token",
                    json.dumps(["https://calendar.example.test/work.ics"]),
                    "user-1",
                ],
            )
            for source_id, default_name in (("local:study", "Study"), ("local:hidden", "Hidden")):
                connection.execute(
                    """INSERT INTO user_calendar_sources
                       (id, user_id, source_id, kind, default_name, created_at, updated_at)
                       VALUES (?, ?, ?, 'local', ?, '2026-08-13T00:00:00Z', '2026-08-13T00:00:00Z')""",
                    [f"row-{source_id.split(':', 1)[1]}", "user-1", source_id, default_name],
                )
            connection.execute(
                """INSERT INTO user_calendar_preferences
                   (id, user_id, calendar_name, display_name, color_hex, visible, created_at, updated_at)
                   VALUES ('pref-study', 'user-1', 'local:study', 'Study Calendar', '#123456', 1,
                           '2026-08-13T00:00:00Z', '2026-08-13T00:00:00Z')"""
            )
            connection.execute(
                """INSERT INTO user_calendar_preferences
                   (id, user_id, calendar_name, display_name, color_hex, visible, created_at, updated_at)
                   VALUES ('pref-hidden', 'user-1', 'local:hidden', 'Hidden Calendar', '#123456', 0,
                           '2026-08-13T00:00:00Z', '2026-08-13T00:00:00Z')"""
            )

    def test_signed_out_csrf_and_envelope_contract(self):
        client = self.client()
        response = client.get("/api/extension/calendar/sources")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json()["contractVersion"], 1)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertTrue(response.headers.get("X-Request-ID"))

        authenticated = self.client("user-1")
        rejected = authenticated.post(
            "/api/extension/calendar/sources",
            json={"account_key": ACCOUNT_1},
        )
        self.assertEqual(rejected.status_code, 400)
        self.assertEqual(rejected.headers.get("X-APStudy-CSRF-Error"), "1")
        self.assertEqual(rejected.headers["Cache-Control"], "no-store")
        self.assertEqual(rejected.get_json()["error"]["code"], "csrf_required")
        self.assertTrue(rejected.headers.get("X-Request-ID"))

    def test_source_schema_credentials_and_user_isolation(self):
        client = self.client("user-1")
        self.enable_capabilities("calendar_read", "calendar_upload")
        token = self.csrf(client)
        invalid = client.post(
            "/api/extension/calendar/sources",
            json={
                "account_key": ACCOUNT_1, "source_id": "source-1",
                "origin": "https://canvas.example.edu", "provider_user_id": "canvas-user-1",
                "label": "Canvas", "consent_version": 1, "token": "never",
            },
            headers={"X-CSRFToken": token},
        )
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(invalid.get_json()["error"]["code"], "credentials_not_allowed")

        self.grant(client, capabilities=("calendar_read", "calendar_upload"))
        self.register_source(client)
        other = self.client("user-2")
        response = other.get("/api/extension/calendar/sources/source-1")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json()["error"]["code"], "source_not_found")

        source = client.get("/api/extension/calendar/sources").get_json()["sources"][0]
        self.assertRegex(source["source_ref"], r"^src1:[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
        for field in ("user_id", "nest_user_id", "provider_user_id", "origin", "account_key"):
            self.assertNotIn(field, source)
        by_ref = client.get(f"/api/extension/calendar/sources/{source['source_ref']}")
        self.assertEqual(by_ref.status_code, 200)
        self.assertEqual(by_ref.get_json()["source"]["source_ref"], source["source_ref"])
        self.assertEqual(
            other.get(f"/api/extension/calendar/sources/{source['source_ref']}").status_code,
            404,
        )

    def test_calendars_endpoint_is_authenticated_no_store_and_redacted(self):
        self.enable_capabilities("calendar_projection")
        self.seed_calendar_destinations()
        signed_out = self.client().get("/api/extension/calendars")
        self.assertEqual(signed_out.status_code, 401)
        self.assertEqual(signed_out.headers["Cache-Control"], "no-store")

        response = self.client("user-1").get("/api/extension/calendars")
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        payload = response.get_json()
        self.assertEqual(payload["contractVersion"], 1)
        self.assertTrue(payload["ok"])
        destinations = payload["calendars"]
        self.assertTrue(destinations)
        expected_fields = {
            "id", "label", "visible", "read_only", "imported", "kind",
            "routing_eligible", "routing_degraded",
        }
        for destination in destinations:
            self.assertEqual(set(destination), expected_fields)
            self.assertTrue(destination["visible"])
            self.assertTrue(destination["routing_eligible"])
            for field in (
                "user_id", "nest_user_id", "provider_user_id", "origin", "account_key",
                "url", "feed_url", "description", "events", "token",
            ):
                self.assertNotIn(field, destination)
        self.assertNotIn("local:hidden", {item["id"] for item in destinations})
        local = next(item for item in destinations if item["id"] == "local:study")
        self.assertEqual(local["label"], "Study Calendar")
        self.assertFalse(local["read_only"])
        self.assertFalse(local["imported"])
        self.assertEqual(local["kind"], "local")
        self.assertTrue(any(item["kind"] == "canvas" and item["read_only"] and item["imported"] for item in destinations))
        self.assertTrue(any(item["kind"] == "external" and item["read_only"] and item["imported"] for item in destinations))

    def test_calendars_endpoint_is_user_isolated_and_filters_archived_deleted(self):
        self.enable_capabilities("calendar_projection")
        self.seed_calendar_destinations()
        with patch.object(
            calendar_events,
            "_configured_calendar_sources",
            return_value=[
                {"id": "local:active", "kind": "local", "default_name": "Active", "status": "active"},
                {"id": "local:hidden-status", "kind": "local", "default_name": "Hidden", "status": "hidden"},
                {"id": "local:archived", "kind": "local", "default_name": "Archived", "status": "archived"},
                {"id": "local:deleted", "kind": "local", "default_name": "Deleted", "status": "deleted"},
            ]), patch.object(calendar_events, "_load_calendar_preferences", return_value=[]), \
                patch.object(calendar_events, "_configured_feed_urls", return_value=[]), \
                patch.object(calendar_events, "_load_calendar_feed_metadata", return_value={}), \
                patch.object(calendar_events, "_load_local_calendar_sources", return_value=[]), \
                patch.object(calendar_events, "_task_calendar_payload", return_value=([], None)):
            destinations = calendar_events.extension_calendar_destinations("user-1")
        self.assertEqual([item["id"] for item in destinations], ["local:active"])

        other = self.client("user-2").get("/api/extension/calendars")
        self.assertEqual(other.status_code, 200)
        self.assertEqual(other.get_json()["calendars"], [])

    def test_routing_source_ref_requires_active_consent_and_is_idempotent(self):
        client = self.client("user-1")
        self.seed_calendar_destinations()
        self.grant(client, capabilities=("calendar_read", "calendar_upload", "calendar_projection"))
        source = self.register_source(client)
        token = self.csrf(client)
        path = f"/api/extension/calendar/sources/{source['source_ref']}/routing"
        body = {
            "state": "incomplete",
            "destination_calendar_id": "local:study",
            "fallback_calendar_id": "canvas",
        }
        first = client.put(path, json=body, headers={"X-CSRFToken": token})
        retry = client.put(path, json=body, headers={"X-CSRFToken": token})
        self.assertEqual(first.status_code, 200, first.get_data(as_text=True))
        self.assertEqual(retry.status_code, 200, retry.get_data(as_text=True))
        self.assertFalse(first.get_json()["routing"].get("idempotent", False))
        self.assertTrue(retry.get_json()["routing"]["idempotent"])
        self.assertEqual(first.get_json()["routing"]["id"], retry.get_json()["routing"]["id"])
        with sqlite3.connect(self.db_path) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM calendar_import_routing WHERE user_id = ? AND source_id = ? AND state = ?",
                    ["user-1", "source-1", "incomplete"],
                ).fetchone()[0],
                1,
            )

        revoked = client.put(
            "/api/extension/consent",
            json={
                "version": 1,
                "source_key": canonical_canvas_source_key(ACCOUNT_1),
                "account_key": ACCOUNT_1,
                "action": "revoke",
                "scopes": ["full_history_upload", "ongoing_read", "shares_ics_inclusion"],
            },
            headers={"X-CSRFToken": token},
        )
        self.assertEqual(revoked.status_code, 200)
        denied = client.put(path, json=body, headers={"X-CSRFToken": token})
        self.assertIn(denied.status_code, {400, 404})
        self.assertIn(
            denied.get_json()["error"]["code"],
            {"source_not_found", "source_inactive", "consent_required", "scope_required"},
        )
        self.assertEqual(client.get(path).get_json()["routing"], [])

    def test_routing_requires_current_visible_eligible_inventory_and_preserves_route(self):
        client = self.client("user-1")
        self.grant(client, capabilities=("calendar_read", "calendar_upload", "calendar_projection"))
        source = self.register_source(client)
        token = self.csrf(client)
        path = f"/api/extension/calendar/sources/{source['source_ref']}/routing"
        inventory = [
            {"id": "local:study", "visible": True, "routing_eligible": True},
            {"id": "canvas", "visible": True, "routing_eligible": True, "read_only": True, "imported": True},
            {"id": "local:hidden", "visible": False, "routing_eligible": True},
            {"id": "local:archived", "visible": False, "routing_eligible": False},
            {"id": "local:deleted", "visible": False, "routing_eligible": False},
        ]
        with patch.object(calendar_events, "extension_calendar_destinations", return_value=inventory):
            first = client.put(
                path,
                json={
                    "state": "incomplete",
                    "destination_calendar_id": "local:study",
                    "fallback_calendar_id": "canvas",
                },
                headers={"X-CSRFToken": token},
            )
        self.assertEqual(first.status_code, 200, first.get_data(as_text=True))
        self.assertTrue(first.get_json()["routing"]["destination_calendar_id"].startswith("local:"))

        for invalid_id in ("local:hidden", "local:archived", "local:deleted", "foreign:calendar", "missing:calendar"):
            with patch.object(calendar_events, "extension_calendar_destinations", return_value=inventory):
                rejected = client.put(
                    path,
                    json={
                        "state": "incomplete",
                        "destination_calendar_id": invalid_id,
                        "fallback_calendar_id": "canvas",
                    },
                    headers={"X-CSRFToken": token},
                )
            self.assertEqual(rejected.status_code, 400, rejected.get_data(as_text=True))
            self.assertEqual(rejected.get_json()["error"]["code"], "routing_destination_unavailable")

        with patch.object(calendar_events, "extension_calendar_destinations", return_value=inventory):
            invalid_fallback = client.put(
                path,
                json={
                    "state": "incomplete",
                    "destination_calendar_id": "local:study",
                    "fallback_calendar_id": "foreign:calendar",
                },
                headers={"X-CSRFToken": token},
            )
        self.assertEqual(invalid_fallback.status_code, 400)
        self.assertEqual(invalid_fallback.get_json()["error"]["code"], "routing_destination_unavailable")

        with patch.object(calendar_events, "extension_calendar_destinations", return_value=[]):
            disappeared = client.put(
                path,
                json={
                    "state": "incomplete",
                    "destination_calendar_id": "local:study",
                    "fallback_calendar_id": "canvas",
                },
                headers={"X-CSRFToken": token},
            )
        self.assertEqual(disappeared.status_code, 400)
        self.assertEqual(disappeared.get_json()["error"]["code"], "routing_destination_unavailable")
        preserved = client.get(path).get_json()["routing"][0]
        self.assertEqual(preserved["destination_calendar_id"], "local:study")
        self.assertEqual(preserved["fallback_calendar_id"], "canvas")

        other = self.client("user-2")
        other_rejected = other.put(
            path,
            json={"state": "incomplete", "destination_calendar_id": "local:study"},
            headers={"X-CSRFToken": self.csrf(other)},
        )
        self.assertEqual(other_rejected.status_code, 404)
        self.assertEqual(other_rejected.get_json()["error"]["code"], "source_not_found")

    def test_sync_batch_finalize_status_cancel_and_stale_generation(self):
        client = self.client("user-1")
        self.grant(client, capabilities=("calendar_read", "calendar_upload"))
        source = self.register_source(client)
        token = self.csrf(client)
        start = client.post(
            "/api/extension/calendar/sources/source-1/sync",
            json={"scope": {"context_ids": ["course-1"]}, "consent_version": 1, "idempotency_key": "run-1"},
            headers={"X-CSRFToken": token},
        )
        self.assertEqual(start.status_code, 200, start.get_data(as_text=True))
        run = start.get_json()["run"]
        self.assertEqual(run["source_ref"], source["source_ref"])
        batch = client.post(
            f"/api/extension/calendar/sources/source-1/sync/{run['run_id']}/batch",
            json={"items": [self.item()], "generation": run["generation"], "lease_token": run["lease_token"], "idempotency_key": "batch-1", "checkpoint": {"cursor": "c1"}},
            headers={"X-CSRFToken": token},
        )
        self.assertEqual(batch.status_code, 200, batch.get_data(as_text=True))
        replay = client.post(
            f"/api/extension/calendar/sources/source-1/sync/{run['run_id']}/batch",
            json={"items": [self.item()], "generation": run["generation"], "lease_token": run["lease_token"], "idempotency_key": "batch-1", "checkpoint": {"cursor": "c1"}},
            headers={"X-CSRFToken": token},
        )
        self.assertTrue(replay.get_json()["idempotent"])
        finalized = client.post(
            f"/api/extension/calendar/sources/source-1/sync/{run['run_id']}/finalize",
            json={"scope": {"context_ids": ["course-1"]}, "generation": run["generation"], "lease_token": run["lease_token"], "complete": True},
            headers={"X-CSRFToken": token},
        )
        self.assertEqual(finalized.status_code, 200, finalized.get_data(as_text=True))
        status = client.get(f"/api/extension/calendar/sources/{source['source_ref']}/sync/{run['run_id']}")
        self.assertEqual(status.get_json()["run"]["state"], "complete")
        self.assertEqual(status.get_json()["run"]["source_ref"], source["source_ref"])

        newer = client.post(
            "/api/extension/calendar/sources/source-1/sync",
            json={"scope": {}, "consent_version": 1, "idempotency_key": "run-2"},
            headers={"X-CSRFToken": token},
        ).get_json()["run"]
        stale = client.post(
            f"/api/extension/calendar/sources/source-1/sync/{run['run_id']}/cancel",
            json={"generation": run["generation"], "lease_token": run["lease_token"]},
            headers={"X-CSRFToken": token},
        )
        self.assertEqual(stale.status_code, 400)
        self.assertEqual(stale.get_json()["error"]["code"], "stale_run")
        cancelled = client.post(
            f"/api/extension/calendar/sources/source-1/sync/{newer['run_id']}/cancel",
            json={"generation": newer["generation"], "lease_token": newer["lease_token"]},
            headers={"X-CSRFToken": token},
        )
        self.assertEqual(cancelled.get_json()["run"]["state"], "cancelled")

    def test_finalize_rechecks_partial_read_consent_before_mutation(self):
        client = self.client("user-1")
        self.grant(client, capabilities=("calendar_read", "calendar_upload"))
        self.register_source(client)
        token = self.csrf(client)

        first_run = client.post(
            "/api/extension/calendar/sources/source-1/sync",
            json={"scope": {}, "consent_version": 1, "idempotency_key": "run-before-revoke"},
            headers={"X-CSRFToken": token},
        ).get_json()["run"]
        batch = client.post(
            f"/api/extension/calendar/sources/source-1/sync/{first_run['run_id']}/batch",
            json={
                "items": [self.item()],
                "generation": first_run["generation"],
                "lease_token": first_run["lease_token"],
                "idempotency_key": "batch-before-revoke",
            },
            headers={"X-CSRFToken": token},
        )
        self.assertEqual(batch.status_code, 200, batch.get_data(as_text=True))

        current_run = client.post(
            "/api/extension/calendar/sources/source-1/sync",
            json={"scope": {}, "consent_version": 1, "idempotency_key": "run-after-batch"},
            headers={"X-CSRFToken": token},
        ).get_json()["run"]
        revoke = client.put(
            "/api/extension/consent",
            json={
                "version": 1,
                "source_key": canonical_canvas_source_key(ACCOUNT_1),
                "account_key": ACCOUNT_1,
                "action": "revoke",
                "scopes": ["ongoing_read"],
            },
            headers={"X-CSRFToken": token},
        )
        self.assertEqual(revoke.status_code, 200, revoke.get_data(as_text=True))
        self.assertEqual(revoke.get_json()["consent"]["state"], "active")
        self.assertEqual(set(revoke.get_json()["consent"]["granted_scopes"]), {
            "full_history_upload",
        })

        finalized = client.post(
            f"/api/extension/calendar/sources/source-1/sync/{current_run['run_id']}/finalize",
            json={
                "scope": {},
                "generation": current_run["generation"],
                "lease_token": current_run["lease_token"],
                "complete": True,
            },
            headers={"X-CSRFToken": token},
        )
        self.assertEqual(finalized.status_code, 404)
        self.assertEqual(finalized.get_json()["error"]["code"], "source_not_found")

        unchanged_run = client.get(
            f"/api/extension/calendar/sources/source-1/sync/{current_run['run_id']}"
        ).get_json()["run"]
        self.assertEqual(unchanged_run["state"], "cancelled")
        self.assertIsNotNone(unchanged_run["cancelled_at"])
        source = client.get("/api/extension/calendar/sources/source-1").get_json()["source"]
        self.assertEqual(source["status"], "archived")
        with sqlite3.connect(self.db_path) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT canvas_soft_deleted FROM calendar_cache WHERE canvas_source_id = 'source-1'"
                ).fetchone()[0],
                1,
            )

    def test_limits_routing_event_link_writeback_and_revocation_cleanup(self):
        client = self.client("user-1")
        self.seed_calendar_destinations()
        self.grant(client, capabilities=(
            "calendar_read", "calendar_upload", "calendar_projection",
        ))
        self.register_source(client)
        token = self.csrf(client)
        routing = client.put(
            "/api/extension/calendar/sources/source-1/routing",
            json={"state": "incomplete", "destination_calendar_id": "local:study", "fallback_calendar_id": "canvas"},
            headers={"X-CSRFToken": token},
        )
        self.assertEqual(routing.status_code, 200)
        self.assertEqual(client.get("/api/extension/calendar/sources/source-1/routing?state=bad").get_json()["error"]["code"], "invalid_route_state")

        oversized = client.post(
            "/api/extension/calendar/sources/source-1/sync",
            json={"scope": {}, "consent_version": 1, "idempotency_key": "run-limit"},
            headers={"X-CSRFToken": token},
        )
        run = oversized.get_json()["run"]
        too_many = client.post(
            f"/api/extension/calendar/sources/source-1/sync/{run['run_id']}/batch",
            json={"items": [self.item() for _ in range(101)], "generation": run["generation"], "lease_token": run["lease_token"], "idempotency_key": "too-many"},
            headers={"X-CSRFToken": token},
        )
        self.assertEqual(too_many.get_json()["error"]["code"], "batch_too_large")

        link = client.post(
            "/api/extension/calendar/sources/source-1/event-links",
            json={"account_key": ACCOUNT_1, "event_ref": "event:one", "source_revision": "r1"},
            headers={"X-CSRFToken": token},
        )
        self.assertEqual(link.get_json()["error"]["code"], "capability_disabled")

        writeback = client.post(
            "/api/extension/calendar/sources/source-1/writebacks",
            json={"account_key": ACCOUNT_1, "operation": "create", "idempotency_key": "wb-1", "target_account": ACCOUNT_1, "payload": {"title": "New"}},
            headers={"X-CSRFToken": token},
        )
        self.assertEqual(writeback.get_json()["error"]["code"], "capability_disabled")

        revoke = client.put(
            "/api/extension/consent",
            json={"version": 1, "source_key": canonical_canvas_source_key(ACCOUNT_1), "account_key": ACCOUNT_1, "action": "revoke", "scopes": ["full_history_upload", "ongoing_read", "mirroring", "two_way_writeback"]},
            headers={"X-CSRFToken": token},
        )
        self.assertEqual(revoke.status_code, 200)
        self.assertEqual(revoke.get_json()["cleanup"]["sourcesArchived"], 1)
        denied = client.post(
            "/api/extension/calendar/sources/source-1/writebacks",
            json={"account_key": ACCOUNT_1, "operation": "create", "idempotency_key": "wb-2"},
            headers={"X-CSRFToken": token},
        )
        self.assertEqual(denied.get_json()["error"]["code"], "capability_disabled")

    def test_mirroring_and_writeback_routes_require_capability_and_exact_scope(self):
        client = self.client("user-1")
        self.grant(client, capabilities=("calendar_read", "calendar_upload"))
        self.register_source(client)
        token = self.csrf(client)
        link_body = {
            "account_key": ACCOUNT_1,
            "event_ref": "event:capability-gate",
            "source_revision": "r1",
        }

        disabled_link = client.post(
            "/api/extension/calendar/sources/source-1/event-links",
            json=link_body,
            headers={"X-CSRFToken": token},
        )
        self.assertEqual(disabled_link.get_json()["error"]["code"], "capability_disabled")

        self.enable_capabilities("calendar_read", "calendar_upload", "calendar_mirroring")
        missing_mirroring_scope = client.post(
            "/api/extension/calendar/sources/source-1/event-links",
            json=link_body,
            headers={"X-CSRFToken": token},
        )
        self.assertEqual(missing_mirroring_scope.get_json()["error"]["code"], "scope_required")
        consent = client.put(
            "/api/extension/consent",
            json={
                "version": 1,
                "source_key": canonical_canvas_source_key(ACCOUNT_1),
                "account_key": ACCOUNT_1,
                "action": "grant",
                "scopes": ["mirroring"],
            },
            headers={"X-CSRFToken": token},
        )
        self.assertEqual(consent.status_code, 400)
        self.assertEqual(consent.get_json()["error"]["code"], "exact_scope_set_required")
        self.assertEqual(
            client.post(
                "/api/extension/calendar/sources/source-1/event-links",
                json=link_body,
                headers={"X-CSRFToken": token},
            ).get_json()["error"]["code"],
            "scope_required",
        )

        writeback_body = {
            "account_key": ACCOUNT_1,
            "operation": "create",
            "idempotency_key": "wb-capability-gate",
            "target_account": ACCOUNT_1,
            "payload": {"title": "New"},
        }
        disabled_writeback = client.post(
            "/api/extension/calendar/sources/source-1/writebacks",
            json=writeback_body,
            headers={"X-CSRFToken": token},
        )
        self.assertEqual(disabled_writeback.get_json()["error"]["code"], "capability_disabled")

        self.enable_capabilities(
            "calendar_read", "calendar_upload", "calendar_mirroring", "calendar_two_way_writeback"
        )
        missing_writeback_scope = client.post(
            "/api/extension/calendar/sources/source-1/writebacks",
            json=writeback_body,
            headers={"X-CSRFToken": token},
        )
        self.assertEqual(missing_writeback_scope.get_json()["error"]["code"], "scope_required")
        consent = client.put(
            "/api/extension/consent",
            json={
                "version": 1,
                "source_key": canonical_canvas_source_key(ACCOUNT_1),
                "account_key": ACCOUNT_1,
                "action": "grant",
                "scopes": ["two_way_writeback"],
            },
            headers={"X-CSRFToken": token},
        )
        self.assertEqual(consent.status_code, 400)
        self.assertEqual(consent.get_json()["error"]["code"], "exact_scope_set_required")
        self.assertEqual(
            client.post(
                "/api/extension/calendar/sources/source-1/writebacks",
                json=writeback_body,
                headers={"X-CSRFToken": token},
            ).get_json()["error"]["code"],
            "scope_required",
        )


if __name__ == "__main__":
    unittest.main()
