import json
import unittest
from datetime import datetime, timedelta
from unittest.mock import mock_open, patch

import icalendar
from appwrite.exception import AppwriteException

from services import ics_builder


class TestIcsBuilder(unittest.TestCase):
    def _build_calendar(self, events):
        with patch.object(ics_builder, "list_calendar_rows_all", return_value=events), \
                patch.object(ics_builder, "_inject_atlas_schedule"):
            return icalendar.Calendar.from_ical(
                ics_builder.build_ics_for_user("42")
            )

    @staticmethod
    def _vevents(calendar):
        return [component for component in calendar.walk()
                if component.name == "VEVENT"]

    def test_build_event_uid_namespaces_existing_uid(self):
        self.assertEqual(
            ics_builder._build_event_uid("canvas-event", "42"),
            "u42-canvas-event",
        )

    def test_build_event_uid_uses_cached_row_id_when_event_uid_missing(self):
        self.assertEqual(
            ics_builder._build_event_uid(None, "42", "cache-row-7"),
            "u42-generated-cache-row-7@nest.apstudy.org",
        )

    def test_build_ics_uses_cached_row_id_for_missing_event_uid(self):
        events = [
            {
                "$id": "cache-row-1",
                "event_uid": None,
                "event_title": "First",
                "event_start": "2026-08-01T14:00:00Z",
                "event_end": "2026-08-01T15:00:00Z",
            },
            {
                "id": "cache-row-2",
                "event_uid": None,
                "event_title": "Second",
                "event_start": "2026-08-02T14:00:00Z",
                "event_end": "2026-08-02T15:00:00Z",
            },
        ]

        calendar = self._build_calendar(events)

        uids = {str(component["UID"]) for component in self._vevents(calendar)}
        self.assertEqual(
            uids,
            {
                "u42-generated-cache-row-1@nest.apstudy.org",
                "u42-generated-cache-row-2@nest.apstudy.org",
            },
        )

    def test_build_ics_serializes_fields_and_escapes_text_values(self):
        title = "Review,;\\\nATTENDEE:attacker@example.com"
        description = "Line one\nLine two"
        event = {
            "$id": "cache-escape",
            "event_uid": "uid,one;two",
            "event_title": title,
            "raw_description": description,
            "event_start": "2026-08-01T14:00:00-04:00",
            "event_end": None,
            "course_name": "BIO,141",
            "event_type": "assignment",
        }

        with patch.object(ics_builder, "list_calendar_rows_all", return_value=[event]), \
                patch.object(ics_builder, "_inject_atlas_schedule"):
            serialized = ics_builder.build_ics_for_user("42")
        calendar = icalendar.Calendar.from_ical(serialized)
        vevent = self._vevents(calendar)[0]

        self.assertEqual(str(vevent["UID"]), "u42-uid,one;two")
        self.assertEqual(str(vevent["SUMMARY"]), title)
        self.assertEqual(str(vevent["DESCRIPTION"]), description)
        self.assertEqual(
            [str(category) for category in vevent["CATEGORIES"].cats],
            ["BIO,141"],
        )
        self.assertEqual(str(vevent["X-APSTUDY-TYPE"]), "assignment")
        self.assertEqual(
            vevent["DTSTART"].dt.replace(tzinfo=None),
            datetime(2026, 8, 1, 14),
        )
        self.assertEqual(
            vevent["DTEND"].dt - vevent["DTSTART"].dt,
            timedelta(hours=1),
        )
        self.assertNotIn("\r\nATTENDEE:", serialized)
        self.assertIn(r"SUMMARY:Review\,\;", serialized)

    def test_build_ics_omits_unknown_type_and_invalid_timestamps(self):
        calendar = self._build_calendar([
            {
                "$id": "cache-invalid",
                "event_uid": "uid-invalid",
                "event_title": "No date",
                "event_start": "not-a-date",
                "event_end": "also-not-a-date",
                "event_type": "unknown",
            },
        ])

        vevent = self._vevents(calendar)[0]
        self.assertNotIn("DTSTART", vevent)
        self.assertNotIn("DTEND", vevent)
        self.assertNotIn("X-APSTUDY-TYPE", vevent)

    def test_build_ics_rejects_missing_uid_and_row_id(self):
        with patch.object(
            ics_builder,
            "list_calendar_rows_all",
            return_value=[{
                "event_uid": None,
                "event_title": "Malformed cache row",
            }],
        ), patch.object(ics_builder, "_inject_atlas_schedule"):
            with self.assertRaisesRegex(ValueError, "cached event ID"):
                ics_builder.build_ics_for_user("42")

    def test_build_ics_returns_empty_calendar_when_cache_lookup_fails(self):
        with patch.object(
            ics_builder,
            "list_calendar_rows_all",
            side_effect=AppwriteException("calendar cache unavailable"),
        ), patch.object(ics_builder, "_inject_atlas_schedule"):
            calendar = icalendar.Calendar.from_ical(
                ics_builder.build_ics_for_user("42")
            )

        self.assertEqual(self._vevents(calendar), [])

    def test_inject_atlas_schedule_serializes_selected_recurring_course(self):
        user_course = {
            "term": "2026-fall",
            "subject": "BIO",
            "catalog": "141",
            "crn": "12345",
        }
        course_data = {
            "course_code": "BIO 141",
            "course_title": "Biology",
            "date_range": {"end": "2026-12-10"},
            "sections": [
                {
                    "crn": "99999",
                    "schedule": {
                        "meetings": [{"day": "Mon", "start": "0800", "end": "0900"}],
                    },
                    "schedule_type": "Lecture",
                },
                {
                    "crn": "12345",
                    "schedule": {
                        "meetings": [
                            {"day": "Tue", "start": "830", "end": "1015"},
                            {"day": "Thu", "start": "830", "end": "1015"},
                        ],
                    },
                    "schedule_type": "Lecture",
                    "instructor": "Dr. X",
                    "section_number": "001",
                },
            ],
        }
        reader = mock_open(read_data=json.dumps(course_data))
        calendar = icalendar.Calendar()

        with patch.object(ics_builder, "list_rows_all", return_value=[user_course]), \
                patch("os.path.isfile", return_value=True) as isfile, \
                patch("builtins.open", reader):
            ics_builder._inject_atlas_schedule(calendar, "42")

        event = self._vevents(calendar)[0]
        self.assertEqual(str(event["UID"]), "atlas-2026-fall-BIO141-12345@nest.apstudy.org")
        self.assertEqual(str(event["SUMMARY"]), "BIO 141 Lecture (Sec 001)")
        self.assertEqual(
            str(event["DESCRIPTION"]),
            "Biology | Instructor: Dr. X | CRN: 12345",
        )
        self.assertEqual(event["DTSTART"].dt, datetime(2026, 8, 26, 8, 30))
        self.assertEqual(event["DTEND"].dt, datetime(2026, 8, 26, 10, 15))
        self.assertEqual(event["RRULE"]["FREQ"], "weekly")
        self.assertEqual(event["RRULE"]["BYDAY"], ["TU", "TH"])
        self.assertEqual(event["RRULE"]["UNTIL"], datetime(2026, 12, 10))
        self.assertEqual(str(event["X-APSTUDY-TYPE"]), "class-meeting")
        self.assertEqual(str(event["X-APSTUDY-SCHEDULE-TYPE"]), "Lecture")
        self.assertEqual(
            isfile.call_args.args[0].split("atlas-data/")[-1],
            "2026-fall/BIO/141.json",
        )
        reader.assert_called_once_with(isfile.call_args.args[0], "r", encoding="utf-8")

    def test_inject_atlas_schedule_omits_until_for_invalid_semester_end(self):
        course_data = {
            "course_code": "CHEM 150",
            "date_range": {"end": "not-a-date"},
            "sections": [{
                "crn": "15001",
                "schedule": {
                    "meetings": [{"day": "Mon", "start": "0800", "end": "0900"}],
                },
                "schedule_type": "Lab",
            }],
        }
        calendar = icalendar.Calendar()

        with patch.object(
            ics_builder,
            "list_rows_all",
            return_value=[{
                "term": "2026-fall",
                "subject": "CHEM",
                "catalog": "150",
                "crn": "15001",
            }],
        ), patch("os.path.isfile", return_value=True), patch(
            "builtins.open",
            mock_open(read_data=json.dumps(course_data)),
        ):
            ics_builder._inject_atlas_schedule(calendar, "42")

        event = self._vevents(calendar)[0]
        self.assertNotIn("UNTIL", event["RRULE"])

    def test_inject_atlas_schedule_skips_unselected_and_malformed_sections(self):
        course_data = {
            "sections": [
                {
                    "crn": "other",
                    "schedule": {"meetings": [{"day": "Mon", "start": "0800", "end": "0900"}]},
                },
                {
                    "crn": "selected-bad-time",
                    "schedule": {"meetings": [{"day": "Tue", "start": "bad", "end": "0900"}]},
                },
                {
                    "crn": "selected-unknown-day",
                    "schedule": {"meetings": [{"day": "Someday", "start": "0800", "end": "0900"}]},
                },
                {"crn": "selected-empty", "schedule": {"meetings": []}},
            ],
        }
        calendar = icalendar.Calendar()

        with patch.object(
            ics_builder,
            "list_rows_all",
            return_value=[{
                "term": "2026-fall",
                "subject": "HIST",
                "catalog": "101",
                "crn": "selected-bad-time",
            }],
        ), patch("os.path.isfile", return_value=True), patch(
            "builtins.open",
            mock_open(read_data=json.dumps(course_data)),
        ):
            ics_builder._inject_atlas_schedule(calendar, "42")

        self.assertEqual(self._vevents(calendar), [])

    def test_inject_atlas_schedule_skips_missing_and_invalid_course_files(self):
        user_course = {"term": "2026-fall", "subject": "BIO", "catalog": "141"}

        with self.subTest(reason="missing file"):
            calendar = icalendar.Calendar()
            reader = mock_open()
            with patch.object(ics_builder, "list_rows_all", return_value=[user_course]), \
                    patch("os.path.isfile", return_value=False), \
                    patch("builtins.open", reader):
                ics_builder._inject_atlas_schedule(calendar, "42")
            self.assertEqual(self._vevents(calendar), [])
            reader.assert_not_called()

        with self.subTest(reason="invalid JSON"):
            calendar = icalendar.Calendar()
            with patch.object(ics_builder, "list_rows_all", return_value=[user_course]), \
                    patch("os.path.isfile", return_value=True), \
                    patch("builtins.open", mock_open(read_data="not-json")):
                ics_builder._inject_atlas_schedule(calendar, "42")
            self.assertEqual(self._vevents(calendar), [])

    def test_inject_atlas_schedule_returns_when_course_lookup_fails(self):
        calendar = icalendar.Calendar()

        with patch.object(
            ics_builder,
            "list_rows_all",
            side_effect=AppwriteException("course lookup unavailable"),
        ):
            ics_builder._inject_atlas_schedule(calendar, "42")

        self.assertEqual(self._vevents(calendar), [])


if __name__ == "__main__":
    unittest.main()
