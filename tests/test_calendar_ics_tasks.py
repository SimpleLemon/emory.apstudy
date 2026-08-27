import json
import unittest
from datetime import date, datetime, timezone
from unittest.mock import patch

import services.calendar_ics_contract as ics_contract
from services.calendar_ics_contract import CalendarIcsProjectionStatus, normalized_calendar_event_payload
from services.calendar_ics_tasks import (
    TASK_PRIORITY_VALUES,
    project_tasks,
    project_tasks_for_user,
)


UTC = timezone.utc
RANGE_START = datetime(2026, 8, 24, tzinfo=UTC)
RANGE_END = datetime(2026, 9, 2, tzinfo=UTC)


def task(**overrides):
    value = {
        "$id": "task-1",
        "title": "Read chapter",
        "priority": "high",
        "deadline_at": "2026-08-25T14:00:00Z",
        "deadline_time": "14:00",
        "timezone": "UTC",
        "recurrence_json": None,
        "reminder_minutes": 10,
        "completed": False,
        "updated_at": "2026-08-24T12:00:00Z",
    }
    value.update(overrides)
    return value


class TasksCalendarIcsProjectorTests(unittest.TestCase):
    def project(self, tasks, completions=None, **kwargs):
        with patch.object(ics_contract, "CALENDAR_ICS_UID_SECRET", "s" * 32):
            return project_tasks(tasks, completions or [], RANGE_START, RANGE_END, **kwargs)

    def test_no_tasks_and_no_in_range_occurrences_are_valid_empty(self):
        self.assertEqual(self.project([]).status, CalendarIcsProjectionStatus.VALID_EMPTY)
        self.assertEqual(
            self.project([task(deadline_at="2026-10-01T14:00:00Z")]).status,
            CalendarIcsProjectionStatus.VALID_EMPTY,
        )

    def test_single_timed_task_projects_user_fields_and_omits_raw_identity(self):
        outcome = self.project([task()])
        self.assertEqual(outcome.status, CalendarIcsProjectionStatus.SUCCESS)
        event = outcome.events[0]
        self.assertEqual(event.calendar_id, "tasks")
        self.assertEqual(event.source_type, "task")
        self.assertEqual(event.title, "Read chapter")
        self.assertEqual(event.start, datetime(2026, 8, 25, 14, tzinfo=UTC))
        self.assertEqual(event.end, datetime(2026, 8, 25, 14, 30, tzinfo=UTC))
        self.assertFalse(event.is_all_day)
        self.assertEqual(event.reminder_minutes, 10)
        self.assertNotIn("task-1", json.dumps(normalized_calendar_event_payload(event), default=str))
        self.assertNotIn("recurrence_json", normalized_calendar_event_payload(event))

    def test_forbidden_task_storage_fields_never_cross_normalized_boundary(self):
        outcome = self.project([task(
            list_id="list-secret",
            recurrence_json=json.dumps({"every": 1, "unit": "day"}),
            order=7,
            starred=True,
            completed_at="2026-08-25T15:00:00Z",
            credentials="do-not-export",
            diagnostics="do-not-export",
        )])
        payload = normalized_calendar_event_payload(outcome.events[0])
        serialized = json.dumps(payload, default=str)
        for forbidden in (
            "task-1", "list-secret", "recurrence_json", "starred", "order",
            "completed_at", "credentials", "diagnostics",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_all_day_task_uses_date_semantics_and_omits_alarm_metadata(self):
        outcome = self.project([task(
            title="Exam day",
            deadline_at="2026-08-26T00:00:00Z",
            deadline_time=None,
            reminder_minutes=-540,
        )])
        event = outcome.events[0]
        self.assertTrue(event.is_all_day)
        self.assertEqual(event.start, date(2026, 8, 26))
        self.assertEqual(event.end, date(2026, 8, 27))
        self.assertIsNone(event.reminder_minutes)

    def test_recurring_tasks_expand_to_concrete_occurrences_without_rrule(self):
        recurring = task(
            recurrence_json=json.dumps({
                "every": 1,
                "unit": "week",
                "startDate": "2026-08-25",
                "endDate": "2026-09-30",
            }),
        )
        outcome = self.project([recurring])
        self.assertEqual(len(outcome.events), 2)
        self.assertEqual(
            [event.start for event in outcome.events],
            [datetime(2026, 8, 25, 14, tzinfo=UTC), datetime(2026, 9, 1, 14, tzinfo=UTC)],
        )
        self.assertEqual(len({event.uid for event in outcome.events}), 2)

    def test_utc_interpretation_and_priority_mapping(self):
        outcome = self.project([task(
            deadline_at="2026-08-25T09:00:00-04:00",
            deadline_time="09:00",
            timezone="America/New_York",
            priority="medium",
        )])
        event = outcome.events[0]
        self.assertEqual(event.start, datetime(2026, 8, 25, 13, tzinfo=UTC))
        self.assertEqual(TASK_PRIORITY_VALUES, {"high": 1, "medium": 5, "low": 9})
        self.assertEqual(event.priority, "medium")
        self.assertIn("Priority: Medium (5)", event.description)

    def test_completion_and_truthful_durable_revision(self):
        outcome = self.project([task(completed=True)], [{
            "task_id": "task-1",
            "occurrence_key": "single",
            "completed_at": "2026-08-25T15:00:00Z",
            "updated_at": "2026-08-25T15:01:00Z",
        }])
        event = outcome.events[0]
        self.assertTrue(event.completed)
        self.assertEqual(event.last_modified, datetime(2026, 8, 25, 15, 1, tzinfo=UTC))
        payload = normalized_calendar_event_payload(event)
        self.assertNotIn("completed_at", payload)
        self.assertNotIn("task-1", json.dumps(payload, default=str))

    def test_invalid_or_nonpositive_reminders_do_not_create_alarm_metadata(self):
        for reminder in (-1, 0):
            event = self.project([task(reminder_minutes=reminder)]).events[0]
            self.assertIsNone(event.reminder_minutes)

    def test_uid_is_stable_and_changes_per_private_occurrence(self):
        recurring = task(recurrence_json=json.dumps({
            "every": 1,
            "unit": "day",
            "startDate": "2026-08-25",
            "endDate": "2026-08-27",
        }))
        first = self.project([recurring]).events
        second = self.project([recurring]).events
        self.assertEqual([event.uid for event in first], [event.uid for event in second])
        self.assertNotEqual(first[0].uid, first[1].uid)
        self.assertTrue(all("task-1" not in event.uid for event in first))

    def test_deleted_task_is_omitted_without_tombstone(self):
        self.assertEqual(self.project([]).events, ())

    def test_malformed_recurrence_and_expansion_fail_without_partial_events(self):
        outcome = self.project([task(), task(
            **{"$id": "task-2", "recurrence_json": "not-json"},
        )])
        self.assertEqual(outcome.status, CalendarIcsProjectionStatus.SOURCE_FAILURE)
        self.assertEqual(outcome.events, ())
        self.assertEqual(outcome.diagnostic_code, "task_source_invalid")

        def broken_builder(*_args):
            raise RuntimeError("recurrence storage failed")

        outcome = self.project([task()], occurrence_builder=broken_builder)
        self.assertEqual(outcome.status, CalendarIcsProjectionStatus.SOURCE_FAILURE)
        self.assertEqual(outcome.events, ())

    def test_storage_failure_is_typed(self):
        def broken_rows(*_args):
            raise RuntimeError("task storage unavailable")

        with patch.object(ics_contract, "CALENDAR_ICS_UID_SECRET", "s" * 32):
            outcome = project_tasks_for_user("user-1", RANGE_START, RANGE_END, list_rows_fn=broken_rows)
        self.assertEqual(outcome.status, CalendarIcsProjectionStatus.SOURCE_FAILURE)
        self.assertEqual(outcome.diagnostic_code, "task_source_unavailable")
        self.assertEqual(outcome.events, ())


if __name__ == "__main__":
    unittest.main()
