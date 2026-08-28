import json
import os
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask

import blueprints.calendar_api as ca


class CalendarShareTestCase(unittest.TestCase):
    def setUp(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.app = Flask(
            __name__,
            template_folder=os.path.join(root, "templates"),
            static_folder=os.path.join(root, "static"),
        )
        self.app.secret_key = "test"
        self.app.config["SERVER_NAME"] = "example.test"
        self.app.register_blueprint(ca.calendar_bp, url_prefix="/api/calendar")
        self.user = SimpleNamespace(id="user-1")

    def test_generate_calendar_share_code_checks_uniqueness(self):
        choices = iter(("A" * ca.CALENDAR_SHARE_CODE_LENGTH) + ("B" * ca.CALENDAR_SHARE_CODE_LENGTH))
        with patch.object(ca.secrets, "choice", side_effect=lambda _chars: next(choices)), \
                patch.object(ca, "first_calendar_row", side_effect=[{"$id": "taken"}, None]) as first_row:
            code = ca._generate_calendar_share_code()

        self.assertEqual(code, "B" * ca.CALENDAR_SHARE_CODE_LENGTH)
        self.assertEqual(first_row.call_count, 2)

    def test_share_config_validation_and_date_normalization(self):
        fixed = ca._normalize_calendar_share_payload({
            "includeAllCalendars": False,
            "calendarIds": ["canvas", "canvas", "local:study"],
            "dateScope": "fixed",
            "fixedStart": "2026-05-01",
            "fixedEnd": "2026-05-10",
        })

        self.assertFalse(fixed["include_all_calendars"])
        self.assertEqual(json.loads(fixed["calendar_ids_json"]), ["canvas", "local:study"])
        self.assertEqual(fixed["fixed_start"], "2026-05-01T00:00:00Z")
        self.assertEqual(fixed["fixed_end"], "2026-05-11T00:00:00Z")

        with self.assertRaises(ValueError):
            ca._normalize_calendar_share_payload({
                "includeAllCalendars": False,
                "calendarIds": [],
            })

        with self.assertRaises(ValueError):
            ca._normalize_calendar_share_payload({
                "dateScope": "rolling",
                "rollingDays": 367,
            })

    def test_create_revoke_and_regenerate_share_routes(self):
        created = {
            "$id": "share-1",
            "user_id": "user-1",
            "share_code": "ABCDEFGHIJKLMNOP",
            "is_active": True,
            "include_all_calendars": True,
            "calendar_ids_json": "[]",
            "date_scope": "all",
            "created_at": "2026-05-18T00:00:00Z",
            "updated_at": "2026-05-18T00:00:00Z",
        }
        with self.app.test_request_context("/api/calendar/shares", method="POST", json={"includeAllCalendars": True}):
            with patch.object(ca, "current_user", self.user), \
                    patch.object(ca, "_generate_calendar_share_code", return_value="ABCDEFGHIJKLMNOP"), \
                    patch.object(ca, "create_calendar_row", return_value=created) as create_row:
                response, status = ca.create_calendar_share.__wrapped__()

        self.assertEqual(status, 201)
        self.assertEqual(response.get_json()["share"]["shareCode"], "ABCDEFGHIJKLMNOP")
        self.assertTrue(create_row.call_args.kwargs["data"]["is_active"])

        revoked = {**created, "is_active": False}
        with self.app.test_request_context("/api/calendar/shares/share-1", method="DELETE"):
            with patch.object(ca, "current_user", self.user), \
                    patch.object(ca, "get_calendar_row", return_value=created), \
                    patch.object(ca, "update_calendar_row", return_value=revoked) as update_row:
                response = ca.revoke_calendar_share.__wrapped__("share-1")

        self.assertFalse(response.get_json()["share"]["isActive"])
        self.assertFalse(update_row.call_args.args[2]["is_active"])

        regenerated = {**created, "share_code": "QRSTUVWXYZabcdef"}
        with self.app.test_request_context("/api/calendar/shares/share-1/regenerate", method="POST"):
            with patch.object(ca, "current_user", self.user), \
                    patch.object(ca, "get_calendar_row", return_value=created), \
                    patch.object(ca, "_generate_calendar_share_code", return_value="QRSTUVWXYZabcdef"), \
                    patch.object(ca, "update_calendar_row", return_value=regenerated):
                response = ca.regenerate_calendar_share.__wrapped__("share-1")

        self.assertEqual(response.get_json()["share"]["shareCode"], "QRSTUVWXYZabcdef")

    def test_create_share_rejects_non_object_json_before_mutation(self):
        payloads = [
            ("string", {"json": "a string"}),
            ("list", {"json": ["canvas"]}),
            ("number", {"json": 17}),
            ("boolean", {"json": True}),
            ("null", {"data": "null", "content_type": "application/json"}),
            ("whitespace application/json", {"data": " \t\n", "content_type": "application/json"}),
            ("malformed application/json", {"data": '{"calendarIds":', "content_type": "application/json"}),
            ("malformed text/plain", {"data": "not-json", "content_type": "text/plain"}),
        ]
        for label, request_kwargs in payloads:
            with self.subTest(payload=label):
                with self.app.test_request_context("/api/calendar/shares", method="POST", **request_kwargs):
                    with patch.object(ca, "current_user", self.user), \
                            patch.object(ca, "create_calendar_row") as create_row, \
                            patch.object(ca, "creation_ics_fields") as prepare_ics:
                        response, status = ca.create_calendar_share.__wrapped__()

                self.assertEqual(status, 400)
                self.assertEqual(response.get_json(), {
                    "error": "Calendar share payload must be a JSON object.",
                    "code": "calendar_share_invalid_payload",
                })
                self.assertEqual(create_row.call_count, 0, "invalid input must not change the share count")
                prepare_ics.assert_not_called()

    def test_create_share_preserves_missing_and_empty_payload_defaults(self):
        created = {
            "$id": "share-default",
            "user_id": "user-1",
            "share_code": "ABCDEFGHIJKLMNOP",
            "is_active": True,
            "include_all_calendars": True,
            "calendar_ids_json": "[]",
            "date_scope": "all",
            "created_at": "2026-05-18T00:00:00Z",
            "updated_at": "2026-05-18T00:00:00Z",
        }
        for json_payload in ({}, None):
            with self.subTest(json_payload=json_payload):
                request_context = self.app.test_request_context(
                    "/api/calendar/shares",
                    method="POST",
                    **({"json": json_payload} if json_payload is not None else {}),
                )
                with request_context:
                    with patch.object(ca, "current_user", self.user), \
                            patch.object(ca, "_generate_calendar_share_code", return_value="ABCDEFGHIJKLMNOP"), \
                            patch.object(ca, "create_calendar_row", return_value=created) as create_row, \
                            patch.object(ca, "emit_creation_event"):
                        response, status = ca.create_calendar_share.__wrapped__()

                self.assertEqual(status, 201)
                data = create_row.call_args.kwargs["data"]
                self.assertTrue(data["include_all_calendars"])
                self.assertEqual(data["calendar_ids_json"], "[]")

    def test_public_events_are_filtered_overridden_and_sanitized(self):
        canvas_url = "https://canvas.emory.edu/feeds/calendars/user-token"
        work_url = "https://calendar.example.com/work.ics"
        canvas_event = {
            "$id": "cache-1",
            "user_id": "user-1",
            "feed_url": canvas_url,
            "feed_url_hash": ca._feed_url_hash(canvas_url),
            "event_uid": "canvas-1",
            "event_title": "Original Canvas",
            "event_start": "2026-05-20T14:00:00Z",
            "event_end": "2026-05-20T15:00:00Z",
            "is_all_day": False,
            "event_type": "assignment",
            "course_name": "Canvas",
            "raw_description": "Details",
        }
        work_event = {
            **canvas_event,
            "$id": "cache-2",
            "feed_url": work_url,
            "feed_url_hash": ca._feed_url_hash(work_url),
            "event_uid": "work-1",
            "event_title": "Work Meeting",
            "event_start": "2026-05-20T16:00:00Z",
            "event_end": "2026-05-20T17:00:00Z",
            "course_name": "Work",
        }
        user_event = {
            "$id": "event-1",
            "user_id": "user-1",
            "title": "Private Local",
            "description": "",
            "start": "2026-05-20T18:00:00Z",
            "end": "2026-05-20T19:00:00Z",
            "is_all_day": False,
            "calendar_id": "local:study",
            "created_at": "2026-05-18T00:00:00Z",
        }
        override = {
            "$id": "override-1",
            "event_ref": ca._event_ref_for_cache_event(canvas_event),
            "title": "Overridden Canvas",
            "description": "Changed",
            "start": "2026-05-21T14:00:00Z",
            "end": "2026-05-21T15:00:00Z",
            "is_all_day": False,
            "calendar_id": "canvas",
            "hidden": False,
        }
        settings = {
            "user_id": "user-1",
            "canvas_ical_url": canvas_url,
            "other_ical_urls_json": json.dumps([work_url]),
        }
        share = {
            "$id": "share-1",
            "user_id": "user-1",
            "share_code": "ABCDEFGHIJKLMNOP",
            "is_active": True,
            "include_all_calendars": False,
            "calendar_ids_json": json.dumps(["canvas"]),
            "date_scope": "all",
        }

        def list_rows(table_id, queries=None, limit=None):
            if table_id == ca.COLLECTIONS["calendar_cache"]:
                return [canvas_event, work_event]
            if table_id == ca.COLLECTIONS["user_events"]:
                return [user_event]
            if table_id == ca.COLLECTIONS["user_event_overrides"]:
                return [override]
            if table_id == ca.COLLECTIONS["user_calendar_preferences"]:
                return [{"calendar_name": "canvas", "color_hex": "#0ea5e9", "display_name": "School"}]
            if table_id == ca.COLLECTIONS["calendar_feeds"]:
                return []
            if table_id == ca.COLLECTIONS["user_calendar_sources"]:
                return []
            return []

        with self.app.test_request_context("/api/calendar/share/ABCDEFGHIJKLMNOP/events"):
            with patch.object(ca, "first_row", return_value=settings), \
                    patch.object(ca, "list_calendar_rows_all", side_effect=list_rows):
                payload = ca._public_calendar_events_payload(
                    share,
                    datetime(2026, 5, 20, tzinfo=timezone.utc),
                    datetime(2026, 5, 22, tzinfo=timezone.utc),
                )

        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["events"][0]["title"], "Overridden Canvas")
        self.assertFalse(payload["events"][0]["editable"])
        self.assertNotIn("user_id", payload)
        self.assertNotIn("url", payload["calendar_sources"][0])
        self.assertEqual(payload["calendar_sources"][0]["id"], "canvas")
        self.assertEqual(payload["calendar_sources"][0]["color_hex"], "#0ea5e9")

    def test_public_sources_preserve_local_source_color(self):
        share = {
            "include_all_calendars": True,
            "calendar_ids_json": json.dumps([]),
        }
        sources = [{
            "id": "local:personal",
            "kind": "local",
            "default_name": "Personal",
            "display_name": "",
            "color_hex": "#22c55e",
            "editable": True,
        }]

        payload = ca._sanitize_public_sources(sources, share, preferences=[])

        self.assertEqual(payload[0]["color_hex"], "#22c55e")
        self.assertFalse(payload[0]["editable"])

    def test_public_missing_share_returns_404(self):
        with self.app.test_client() as client:
            with patch.object(ca, "first_calendar_row", return_value=None):
                response = client.get("/api/calendar/share/NOPE/events")

        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
