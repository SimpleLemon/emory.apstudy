import json
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

from services import calendar_ics_courses as courses
import services.calendar_ics_contract as contract
from services.calendar_ics_contract import CalendarIcsProjectionStatus


class SimulatedCoursesProjectorTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.rows = []
        self.course = {
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
                "schedule": {"meetings": [
                    {"day": "Mon", "start": "1000", "end": "1115"},
                    {"day": "Wed", "start": "1000", "end": "1115"},
                ]},
            }],
        }
        self._write_course("Fall_2025", "CS", "170", self.course)
        self.uid_secret = patch.object(contract, "CALENDAR_ICS_UID_SECRET", "s" * 32)
        self.uid_secret.start()
        self.addCleanup(self.uid_secret.stop)
        self.addCleanup(self.temp_dir.cleanup)

    def _write_course(self, term, subject, catalog, value):
        path = self.root / term / subject
        path.mkdir(parents=True, exist_ok=True)
        (path / f"{catalog}.json").write_text(json.dumps(value), encoding="utf-8")

    def _row(self, **overrides):
        row = {
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
        row.update(overrides)
        return row

    def _project(self, rows=None, **kwargs):
        with patch.object(courses, "list_rows_all", return_value=self.rows if rows is None else rows):
            return courses.project_simulated_courses("user-1", data_root=self.root, **kwargs)

    def test_zero_selection_is_valid_empty(self):
        outcome = self._project()
        self.assertEqual(outcome.status, CalendarIcsProjectionStatus.VALID_EMPTY)
        self.assertEqual(outcome.events, ())

    def test_persisted_rows_are_the_only_selection_authority(self):
        self.rows = [self._row()]
        with patch.object(courses, "list_rows_all", return_value=self.rows) as loader:
            outcome = courses.project_simulated_courses(
                "user-1", date(2025, 8, 1), date(2025, 9, 1),
                data_root=self.root,
            )
        loader.assert_called_once()
        self.assertTrue(outcome.events)
        self.assertTrue(all(event.course_code == "CS 170" for event in outcome.events))

    def test_dynamic_window_supports_non_2026_dates(self):
        self.rows = [self._row()]
        outcome = self._project(now=date(2025, 8, 26))
        self.assertEqual(outcome.status, CalendarIcsProjectionStatus.SUCCESS)
        self.assertEqual(outcome.events[0].start, datetime(2025, 8, 25, 10, tzinfo=timezone.utc))

    def test_multiple_patterns_are_concrete_weekly_occurrences(self):
        self.rows = [self._row()]
        outcome = self._project(start=date(2025, 8, 25), end=date(2025, 9, 2))
        self.assertEqual(
            [(event.start.date(), event.start.weekday()) for event in outcome.events],
            [(date(2025, 8, 25), 0), (date(2025, 8, 27), 2), (date(2025, 9, 1), 0)],
        )
        self.assertTrue(all(event.start.tzinfo == timezone.utc for event in outcome.events))

    def test_window_boundaries_are_start_inclusive_and_end_exclusive(self):
        self.rows = [self._row()]
        outcome = self._project(start=date(2025, 8, 27), end=date(2025, 9, 1))
        self.assertEqual([event.start.date() for event in outcome.events], [date(2025, 8, 27)])

    def test_full_approved_details_are_projected_without_forbidden_fields(self):
        self.rows = [self._row()]
        outcome = self._project(start=date(2025, 8, 25), end=date(2025, 8, 26))
        event = outcome.events[0]
        self.assertEqual(event.title, "CS 170 LEC (Sec 1)")
        self.assertEqual(event.description, "An approved course description.")
        self.assertEqual(event.notes, "An approved course note.")
        self.assertEqual(event.location, "White Hall 112")
        self.assertEqual(event.course_location, "White Hall 112")
        self.assertEqual(event.course_name, "Introduction to Computer Science")
        self.assertEqual(event.course_title, "Introduction to Computer Science")
        self.assertEqual(event.section, "1")
        self.assertEqual(event.instructor, "Ada Lovelace")
        self.assertEqual(event.crn, "12345")
        self.assertEqual(event.last_modified, datetime(2025, 8, 20, 12, tzinfo=timezone.utc))
        self.assertNotIn("enrollment", repr(event).lower())
        self.assertNotIn("email", repr(event).lower())

    def test_uids_are_stable_and_hmac_based(self):
        self.rows = [self._row()]
        with patch.object(courses, "list_rows_all", return_value=self.rows):
            first = courses.project_simulated_courses("user-1", date(2025, 8, 25), date(2025, 9, 1), data_root=self.root)
            second = courses.project_simulated_courses("user-1", date(2025, 8, 25), date(2025, 9, 1), data_root=self.root)
        self.assertEqual([event.uid for event in first.events], [event.uid for event in second.events])
        self.assertTrue(all(event.uid.startswith("nest-ics-v1-") for event in first.events))
        self.assertNotIn("saved-course-1", first.events[0].uid)

    def test_unresolved_malformed_and_read_failures_are_typed(self):
        self.rows = [self._row(term="Spring_2025")]
        unresolved = self._project()
        self.assertEqual(unresolved.status, CalendarIcsProjectionStatus.SOURCE_FAILURE)
        self.assertEqual(unresolved.diagnostic_code, courses.SOURCE_UNRESOLVED)

        malformed_path = self.root / "Fall_2025" / "CS" / "170.json"
        malformed_path.write_text("{not json", encoding="utf-8")
        malformed = self._project(rows=[self._row()])
        self.assertEqual(malformed.status, CalendarIcsProjectionStatus.SOURCE_FAILURE)
        self.assertEqual(malformed.diagnostic_code, courses.SOURCE_MALFORMED)

        with patch.object(Path, "open", side_effect=PermissionError("denied")):
            unreadable = self._project(rows=[self._row()])
        self.assertEqual(unreadable.status, CalendarIcsProjectionStatus.SOURCE_FAILURE)
        self.assertEqual(unreadable.diagnostic_code, courses.SOURCE_READ_FAILURE)

    def test_forbidden_source_data_is_not_copied_to_normalized_event(self):
        self.course["sections"][0].update({
            "instructors": [{"name": "Ada Lovelace", "email": "ada@example.test"}],
            "enrollment_status": "Open",
            "seats_available": 10,
            "requirements": ["SECRET REQUIREMENT"],
        })
        self._write_course("Fall_2025", "CS", "170", self.course)
        self.rows = [self._row()]
        outcome = self._project(start=date(2025, 8, 25), end=date(2025, 8, 26))
        event = outcome.events[0]
        self.assertEqual(event.instructor, "Ada Lovelace")
        self.assertNotIn("ada@example.test", repr(event))
        self.assertNotIn("SECRET REQUIREMENT", repr(event))
        self.assertFalse(hasattr(event, "enrollment_status"))


if __name__ == "__main__":
    unittest.main()
