import json
import os
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import icalendar
from flask import Flask

from blueprints import calendar_api
from services import calendar_ics_feed as feed
from services import database
import services.calendar_ics_courses as courses
import services.calendar_ics_contract as contract
from services.calendar_ics_contract import (
    CalendarIcsFailure,
    CalendarIcsFailureCode,
    CalendarIcsProjectionOutcome,
    NormalizedCalendarEvent,
    SIMULATED_COURSES_CALENDAR_ID,
    TASKS_CALENDAR_ID,
)
from services.calendar_share_service import CalendarIcsResourceError
from services.calendar_store import calendar_connection


UTC = timezone.utc


class CalendarIcsFeedTests(unittest.TestCase):
    def setUp(self):
        self.secret = patch.object(contract, "CALENDAR_ICS_UID_SECRET", "s" * 32)
        self.secret.start()
        self.addCleanup(self.secret.stop)
        self.app = Flask(__name__)
        self.app.register_blueprint(calendar_api.calendar_bp, url_prefix="/api/calendar")

    def _event(self, calendar_id="tasks", *, all_day=False, reminder=None, **overrides):
        if all_day:
            start, end = date(2026, 8, 10), date(2026, 8, 11)
        else:
            start = datetime(2026, 8, 10, 14, tzinfo=UTC)
            end = datetime(2026, 8, 10, 15, tzinfo=UTC)
        fields = {
            "calendar_id": calendar_id,
            "source_type": "task" if calendar_id == TASKS_CALENDAR_ID else "course",
            "title": "Review,;\\ notes",
            "start": start,
            "end": end,
            "is_all_day": all_day,
            "description": "Description\nsecond line",
            "location": "Room 1",
            "event_type": "assignment",
            "course_name": "BIO 141",
            "course_type": "Lecture",
            "priority": "high",
            "completed": False,
            "reminder_minutes": reminder,
            "course_code": "BIO 141",
            "course_title": "Biology",
            "section": "001",
            "instructor": "Dr. X",
            "course_location": "Room 1",
            "notes": "Bring lab coat",
            "crn": "12345",
            "last_modified": datetime(2026, 8, 1, 12, tzinfo=UTC),
        }
        fields.update(overrides)
        return NormalizedCalendarEvent.from_internal(
            raw_identity="private-row-1",
            **fields,
        )

    @staticmethod
    def _share(calendar_id="tasks"):
        return {
            "id": "share-1",
            "user_id": "user-1",
            "include_all_calendars": False,
            "calendar_ids_json": json.dumps([calendar_id]),
        }

    def _request(self, path, method="GET", headers=None):
        return self.app.test_client().open(path, method=method, headers=headers or {})

    def _assert_security_headers(self, response):
        for name, value in {
            "Cache-Control": "no-store, private, no-transform",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
            "X-Robots-Tag": "noindex, nofollow",
        }.items():
            self.assertEqual(response.headers[name], value)
        self.assertNotIn("secret", response.get_data(as_text=True))
        for value in response.headers.values():
            self.assertNotIn("secret", value)

    def test_empty_selection_serializes_as_valid_empty_calendar(self):
        with patch.object(feed, "project_tasks_for_user", return_value=CalendarIcsProjectionOutcome.valid_empty()):
            document, window = feed.build_calendar_ics_feed(
                self._share(), now=datetime(2025, 3, 4, 9, tzinfo=UTC)
            )
        self.assertEqual(window[0], datetime(2025, 2, 2, tzinfo=UTC))
        self.assertEqual(window[1], datetime(2026, 3, 6, tzinfo=UTC))
        calendar = icalendar.Calendar.from_ical(document.content)
        self.assertEqual([item for item in calendar.walk() if item.name == "VEVENT"], [])

    def test_dynamic_window_and_exactly_one_projector_dispatch(self):
        now = datetime(2025, 3, 4, 9, tzinfo=UTC)
        outcomes = {
            "canvas": patch.object(feed, "project_canvas_calendar", return_value=CalendarIcsProjectionOutcome.valid_empty()),
            "tasks": patch.object(feed, "project_tasks_for_user", return_value=CalendarIcsProjectionOutcome.valid_empty()),
            "simulated_courses": patch.object(feed, "project_simulated_courses_for_user", return_value=CalendarIcsProjectionOutcome.valid_empty()),
        }
        with outcomes["canvas"] as canvas, outcomes["tasks"] as tasks, outcomes["simulated_courses"] as courses:
            for calendar_id, selected in (("canvas", canvas), ("tasks", tasks), ("simulated_courses", courses)):
                feed.build_calendar_ics_feed(self._share(calendar_id), now=now)
                self.assertEqual(selected.call_count, 1)
                self.assertEqual(selected.call_args.args[1:], (datetime(2025, 2, 2, tzinfo=UTC), datetime(2026, 3, 6, tzinfo=UTC)))
            self.assertEqual(canvas.call_count, 1)
            self.assertEqual(tasks.call_count, 1)
            self.assertEqual(courses.call_count, 1)

    def test_projection_failures_are_typed_as_feed_failures(self):
        for outcome in (
            CalendarIcsProjectionOutcome.source_failure("source_invalid", "bad"),
            CalendarIcsProjectionOutcome.resource_failure("resource_unavailable", "down"),
        ):
            with self.subTest(outcome=outcome.status), patch.object(feed, "project_tasks_for_user", return_value=outcome):
                with self.assertRaises(feed.CalendarIcsFeedError):
                    feed.build_calendar_ics_feed(self._share())

    def test_serializer_is_parseable_deterministic_and_uses_utc(self):
        event = self._event(reminder=10)
        start = datetime(2026, 7, 11, tzinfo=UTC)
        end = datetime(2027, 8, 12, tzinfo=UTC)
        first = feed.serialize_calendar_ics(
            [event], calendar_identity="share:one", range_start=start, range_end=end,
            generated_at=datetime(2026, 8, 24, 1, tzinfo=UTC),
        )
        second = feed.serialize_calendar_ics(
            [event], calendar_identity="share:one", range_start=start, range_end=end,
            generated_at=datetime(2026, 8, 24, 2, tzinfo=UTC),
        )
        parsed = icalendar.Calendar.from_ical(first.content)
        vevent = next(item for item in parsed.walk() if item.name == "VEVENT")
        self.assertEqual(first.etag, second.etag)
        self.assertNotEqual(first.content, second.content)
        self.assertIn(b"METHOD:PUBLISH", first.content)
        self.assertIn(b"DTSTART:20260810T140000Z", first.content)
        self.assertEqual(vevent["UID"].to_ical(), event.uid.encode())
        self.assertIn(b"\r\n", first.content)
        self.assertNotIn(b"TZID=", first.content)
        self.assertNotIn(b"VTIMEZONE", first.content)
        self.assertNotIn(b"SEQUENCE:", first.content)
        self.assertNotIn(b"REFRESH-INTERVAL", first.content)

    def test_details_text_sanitization_alarm_and_all_day_rules(self):
        timed = self._event(reminder=15)
        all_day = self._event(all_day=True, reminder=15, title="All day")
        document = feed.serialize_calendar_ics(
            [timed, all_day], calendar_identity="share:details",
            range_start=datetime(2026, 7, 1, tzinfo=UTC),
            range_end=datetime(2027, 9, 1, tzinfo=UTC),
        )
        parsed = icalendar.Calendar.from_ical(document.content)
        alarms = [item for item in parsed.walk() if item.name == "VALARM"]
        self.assertEqual(len(alarms), 1)
        self.assertEqual(str(alarms[0]["ACTION"]), "DISPLAY")
        self.assertEqual(alarms[0]["TRIGGER"].to_ical(), b"-PT15M")
        self.assertIn(b"DTSTART;VALUE=DATE:20260810", document.content)
        self.assertIn(b"DTEND;VALUE=DATE:20260811", document.content)
        self.assertIn(b"X-APSTUDY-INSTRUCTOR:Dr. X", document.content)
        self.assertIn(b"SUMMARY:Review\\,\\;\\\\ notes", document.content)
        self.assertNotIn(b"ATTENDEE", document.content)

    def test_event_and_output_guards_fail_closed(self):
        event = self._event()
        with self.assertRaises(feed.CalendarIcsFeedError):
            feed.serialize_calendar_ics(
                [event] * (feed.MAX_ICS_EVENTS + 1), calendar_identity="share:guard",
                range_start=datetime(2026, 1, 1, tzinfo=UTC),
                range_end=datetime(2027, 1, 1, tzinfo=UTC),
            )
        oversized = self._event(description="x" * feed.MAX_ICS_BYTES)
        with self.assertRaises(feed.CalendarIcsFeedError):
            feed.serialize_calendar_ics(
                [oversized], calendar_identity="share:guard",
                range_start=datetime(2026, 1, 1, tzinfo=UTC),
                range_end=datetime(2027, 1, 1, tzinfo=UTC),
            )

    def test_route_headers_get_head_etag_and_if_modified_since_ignored(self):
        document = feed.serialize_calendar_ics(
            [], calendar_identity="share:route", range_start=datetime(2026, 1, 1, tzinfo=UTC),
            range_end=datetime(2027, 1, 1, tzinfo=UTC), generated_at=datetime(2026, 8, 24, tzinfo=UTC),
        )
        with patch.object(calendar_api, "resolve_calendar_ics_token", return_value=self._share()), \
                patch.object(calendar_api, "build_calendar_ics_feed", return_value=(document, ())):
            response = self._request("/api/calendar/share-feed.ics?token=secret", headers={"If-Modified-Since": "yesterday"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get_data(), document.content)
            self.assertEqual(response.headers["Content-Type"], "text/calendar; charset=utf-8; method=PUBLISH")
            self.assertEqual(response.headers["Content-Length"], str(len(document.content)))
            self._assert_security_headers(response)
            self.assertNotIn("Content-Disposition", response.headers)
            self.assertNotIn("Last-Modified", response.headers)
            head = self._request("/api/calendar/share-feed.ics?token=secret", method="HEAD")
            self.assertEqual(head.status_code, 200)
            self.assertEqual(head.headers["Content-Length"], response.headers["Content-Length"])
            self.assertEqual(head.get_data(), b"")
            self._assert_security_headers(head)
            not_modified = self._request(
                "/api/calendar/share-feed.ics?token=secret",
                headers={"If-None-Match": document.etag},
            )
            self.assertEqual(not_modified.status_code, 304)
            self.assertEqual(not_modified.headers["ETag"], document.etag)
            self.assertEqual(not_modified.get_data(), b"")
            self._assert_security_headers(not_modified)

    def test_saved_course_share_feed_projects_atlanta_wall_clock_to_utc(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        root = Path(temp_dir.name)
        db_path = str(root / "calendar.sqlite3")
        environment = patch.dict(os.environ, {"DATABASE_PATH": db_path, "FLASK_ENV": "testing"}, clear=False)
        environment.start()
        self.addCleanup(environment.stop)
        database.init_db(path=db_path)
        self.app.config.update(
            DATABASE_PATH=db_path,
            CALENDAR_ICS_SUBSCRIPTIONS_ENABLED=True,
            CALENDAR_ICS_SUBSCRIPTIONS_OWNER_ALLOWLIST="user-1",
        )
        for key in ("DATABASE_PATH", "CALENDAR_ICS_SUBSCRIPTIONS_ENABLED", "CALENDAR_ICS_SUBSCRIPTIONS_OWNER_ALLOWLIST"):
            self.addCleanup(self.app.config.pop, key, None)
        with calendar_connection(db_path) as connection:
            connection.execute(
                """INSERT INTO calendar_shares
                   (id, user_id, share_code, is_active, include_all_calendars,
                    calendar_ids_json, date_scope, created_at, updated_at,
                    ics_token, ics_enabled, ics_issued_at, ics_rotated_at)
                   VALUES (?, ?, ?, 1, 0, ?, 'all', '2025-08-01T00:00:00Z',
                           '2025-08-01T00:00:00Z', 'secret', 1, '2025-08-01T00:00:00Z', NULL)""",
                ["share-1", "user-1", "code-share-1", json.dumps([SIMULATED_COURSES_CALENDAR_ID])],
            )
        course = {
            "course_code": "CS 170",
            "course_title": "Introduction to Computer Science",
            "course_description": "An approved course description.",
            "course_notes": "An approved course note.",
            "date_range": {"start": "2025-08-25", "end": "2025-12-12"},
            "sections": [{
                "crn": "12345",
                "section_number": "1",
                "schedule_type": "LEC",
                "instructor": "Ada Lovelace",
                "location": "White Hall 112",
                "schedule": {"meetings": [{"day": "Mon", "start": "1000", "end": "1115"}]},
            }],
        }
        course_dir = root / "Fall_2025" / "CS"
        course_dir.mkdir(parents=True)
        (course_dir / "170.json").write_text(json.dumps(course), encoding="utf-8")
        saved_row = {
            "$id": "saved-course-1",
            "user_id": "user-1",
            "term": "Fall_2025",
            "subject": "CS",
            "catalog": "170",
            "crn": "12345",
            "section_number": "1",
            "course_name": "Introduction to Computer Science",
            "instructor_name": "Ada Lovelace",
            "added_at": "2025-08-01T12:00:00Z",
            "updated_at": "2025-08-20T12:00:00Z",
        }
        fixed_window = (datetime(2025, 8, 1, tzinfo=UTC), datetime(2025, 9, 1, tzinfo=UTC))
        with patch.object(courses, "list_rows_all", return_value=[saved_row]), \
                patch.object(courses, "COURSE_DATA_ROOT", root), \
                patch.object(feed, "utc_subscription_window", return_value=fixed_window):
            response = self._request("/api/calendar/share-feed.ics?token=secret")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Content-Type"], "text/calendar; charset=utf-8; method=PUBLISH")
        self._assert_security_headers(response)
        body = response.get_data()
        parsed = icalendar.Calendar.from_ical(body)
        vevents = [item for item in parsed.walk() if item.name == "VEVENT"]
        self.assertEqual(len(vevents), 1)
        self.assertEqual(str(vevents[0]["SUMMARY"]), "CS 170 LEC (Sec 1)")
        # 10:00–11:15 Atlanta (EDT, UTC-4) on Mon 2025-08-25 -> 14:00Z–15:15Z.
        self.assertIn(b"DTSTART:20250825T140000Z", body)
        self.assertIn(b"DTEND:20250825T151500Z", body)
        self.assertNotIn(b"DTSTART:20250825T100000Z", body)
        self.assertNotIn(b"TZID", body)

    def test_route_rejects_noncanonical_queries_and_source_failures(self):
        document = feed.serialize_calendar_ics(
            [], calendar_identity="share:route", range_start=datetime(2026, 1, 1, tzinfo=UTC),
            range_end=datetime(2027, 1, 1, tzinfo=UTC),
        )
        with patch.object(calendar_api, "resolve_calendar_ics_token", return_value=None), \
                patch.object(calendar_api, "build_calendar_ics_feed", return_value=(document, ())):
            for method, path in (
                ("GET", "/api/calendar/share-feed.ics"),
                ("HEAD", "/api/calendar/share-feed.ics"),
                ("GET", "/api/calendar/share-feed.ics?token=bad&token=other"),
            ):
                response = self._request(path, method=method)
                with self.subTest(method=method, path=path):
                    self.assertEqual(response.status_code, 404)
                    self.assertEqual(response.get_data(), b"" if method == "HEAD" else b"Not Found")
                    self._assert_security_headers(response)

        typed_failures = {
            "invalid": CalendarIcsFailure(CalendarIcsFailureCode.INVALID_TOKEN, "invalid", status=403),
            "disabled": CalendarIcsFailure(CalendarIcsFailureCode.DISABLED, "disabled", status=403),
            "rotated": CalendarIcsFailure(CalendarIcsFailureCode.INVALID_TOKEN, "rotated", status=403),
            "removed": CalendarIcsFailure(CalendarIcsFailureCode.INVALID_TOKEN, "removed", status=403),
            "inactive": CalendarIcsFailure(CalendarIcsFailureCode.PARENT_REVOKED, "inactive", status=409),
            "mismatch": CalendarIcsFailure(CalendarIcsFailureCode.INELIGIBLE_SELECTION, "mismatch", status=422),
            "feature-or-allowlist": CalendarIcsFailure(CalendarIcsFailureCode.DISABLED, "allowlist", status=403),
        }
        for label, failure in typed_failures.items():
            with patch.object(calendar_api, "resolve_calendar_ics_token", side_effect=failure):
                for method in ("GET", "HEAD"):
                    response = self._request("/api/calendar/share-feed.ics?token=secret", method=method)
                    with self.subTest(kind=label, method=method):
                        self.assertEqual(response.status_code, 404)
                        self.assertEqual(response.get_data(), b"" if method == "HEAD" else b"Not Found")
                        self._assert_security_headers(response)

        with patch.object(
            calendar_api,
            "resolve_calendar_ics_token",
            side_effect=CalendarIcsResourceError("storage down"),
        ):
            for method in ("GET", "HEAD"):
                unavailable = self._request("/api/calendar/share-feed.ics?token=secret", method=method)
                with self.subTest(kind="storage", method=method):
                    self.assertEqual(unavailable.status_code, 503)
                    self.assertEqual(
                        unavailable.get_data(),
                        b"" if method == "HEAD" else b"Calendar temporarily unavailable.",
                    )
                    self._assert_security_headers(unavailable)

        with patch.object(calendar_api, "resolve_calendar_ics_token", return_value=self._share()), \
                patch.object(calendar_api, "build_calendar_ics_feed", side_effect=feed.CalendarIcsFeedError("private")):
            unavailable = self._request("/api/calendar/share-feed.ics?token=secret")
            self.assertEqual(unavailable.status_code, 503)
            self.assertEqual(unavailable.get_data(), b"Calendar temporarily unavailable.")
            self._assert_security_headers(unavailable)

    def test_legacy_feed_route_remains_unchanged(self):
        with patch.object(calendar_api, "first_row", return_value={"user_id": "legacy-user"}), \
                patch("services.ics_builder.build_ics_for_user", return_value="BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n"):
            response = self.app.test_client().get("/api/calendar/feed.ics?token=legacy-secret")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "text/calendar")
        self.assertIn("attachment; filename=nest_apstudy.ics", response.headers["Content-Disposition"])
        self.assertEqual(response.get_data(), b"BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n")

    def test_uid_and_forbidden_data_do_not_leak(self):
        event = self._event(description="safe", location="safe")
        content = feed.serialize_calendar_ics(
            [event], calendar_identity="share:secret-token", range_start=datetime(2026, 1, 1, tzinfo=UTC),
            range_end=datetime(2027, 1, 1, tzinfo=UTC),
        ).content.decode()
        self.assertNotIn("private-row-1", content)
        self.assertNotIn("secret-token", content)
        self.assertNotIn("user-1", content)
        self.assertNotIn("email", content.lower())
        self.assertNotIn("enrollment", content.lower())

    def test_edge_snippet_documents_required_controls(self):
        snippet = Path("deploy/nginx-calendar-ics-feed.snippet.conf").read_text(encoding="utf-8")
        for required in ("rate=300r/m", "burst=30", "Retry-After 60", "proxy_cache off", "$uri"):
            self.assertIn(required, snippet)


if __name__ == "__main__":
    unittest.main()
