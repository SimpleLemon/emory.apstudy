import unittest
from datetime import date, datetime, timezone
from unittest.mock import patch

from services.calendar_ics_canvas import project_canvas_calendar
from services.calendar_ics_contract import (
    CalendarIcsProjectionStatus,
    CalendarIcsContractError,
    normalized_calendar_event_payload,
)
import services.calendar_ics_contract as ics_contract


UTC = timezone.utc


def source_row(*, source_id="source-1", account_key="account-1", status="active", **overrides):
    row = {
        "source_id": source_id,
        "account_key": account_key,
        "provider": "canvas",
        "status": status,
        "consented": True,
        "consent_state": "active",
    }
    row.update(overrides)
    return row


def event_row(**overrides):
    row = {
        "canvas_source_id": "source-1",
        "canvas_account_key": "account-1",
        "canvas_event_ref": "private:canvas:event-1",
        "event_uid": "private-event-1",
        "event_title": "Read chapter 1",
        "event_start": "2026-08-10T10:00:00Z",
        "event_end": "2026-08-10T11:00:00Z",
        "is_all_day": 0,
        "event_type": "assignment",
        "course_name": "BIO 101",
        "raw_description": "<p>Read <strong>chapter 1</strong>.</p><script>secret()</script>",
        "canvas_soft_deleted": 0,
    }
    row.update(overrides)
    return row


class CanvasIcsProjectionTests(unittest.TestCase):
    def setUp(self):
        self.secret = patch.object(ics_contract, "CALENDAR_ICS_UID_SECRET", "x" * 32)
        self.secret.start()
        self.addCleanup(self.secret.stop)
        self.capabilities = patch(
            "services.calendar_ics_canvas.extension_capability_enabled",
            return_value=True,
        )
        self.capabilities.start()
        self.addCleanup(self.capabilities.stop)

    def project(self, rows, *, start="2026-08-01T00:00:00Z", end="2026-09-01T00:00:00Z", sources=None):
        return project_canvas_calendar(
            "user-1",
            start,
            end,
            cache_events=rows,
            source_rows=[source_row()] if sources is None else sources,
        )

    def test_no_source_and_no_events_are_valid_empty(self):
        self.assertEqual(
            project_canvas_calendar(
                "user-1", "2026-08-01T00:00:00Z", "2026-09-01T00:00:00Z",
                cache_events=[], source_rows=[],
            ).status,
            CalendarIcsProjectionStatus.VALID_EMPTY,
        )
        self.assertEqual(
            self.project([event_row(event_start="2027-01-01T00:00:00Z", event_end="2027-01-01T01:00:00Z")]).status,
            CalendarIcsProjectionStatus.VALID_EMPTY,
        )

    def test_range_intersection_and_utc_timed_projection(self):
        outcome = self.project([event_row()])
        self.assertEqual(outcome.status, CalendarIcsProjectionStatus.SUCCESS)
        event = outcome.events[0]
        self.assertEqual(event.start, datetime(2026, 8, 10, 10, tzinfo=UTC))
        self.assertEqual(event.end, datetime(2026, 8, 10, 11, tzinfo=UTC))
        self.assertEqual(event.calendar_id, "canvas")
        self.assertEqual(event.course_name, "BIO 101")
        self.assertEqual(event.event_type, "assignment")

    def test_all_day_uses_date_values_and_exclusive_end(self):
        outcome = self.project([event_row(
            event_start="2026-08-10T00:00:00Z",
            event_end="2026-08-12T00:00:00Z",
            is_all_day=1,
        )])
        event = outcome.events[0]
        self.assertTrue(event.is_all_day)
        self.assertIs(type(event.start), date)
        self.assertIs(type(event.end), date)
        self.assertEqual(event.start, date(2026, 8, 10))
        self.assertEqual(event.end, date(2026, 8, 12))

    def test_missing_shares_ics_capability_fails_closed_for_configured_source(self):
        def enabled(capability):
            return capability != "calendar_shares_ics"

        with patch("services.calendar_ics_canvas.extension_capability_enabled", side_effect=enabled):
            outcome = self.project([event_row()])
        self.assertEqual(outcome.status, CalendarIcsProjectionStatus.SOURCE_FAILURE)
        self.assertEqual(outcome.diagnostic_code, "capability_unavailable")
        self.assertEqual(outcome.events, ())

    def test_missing_canvas_consent_fails_closed_for_configured_source(self):
        outcome = self.project([], sources=[source_row(consented=False)])
        self.assertEqual(outcome.status, CalendarIcsProjectionStatus.SOURCE_FAILURE)
        self.assertEqual(outcome.diagnostic_code, "source_authentication")
        self.assertEqual(outcome.events, ())

    def test_description_is_sanitized_and_private_fields_are_not_serialized(self):
        outcome = self.project([event_row()])
        event = outcome.events[0]
        payload = normalized_calendar_event_payload(event)
        self.assertEqual(event.description, "Read chapter 1.")
        self.assertNotIn("private:canvas:event-1", repr(event))
        self.assertNotIn("canvas_source_id", payload)
        self.assertNotIn("account-1", repr(payload))
        self.assertNotIn("source_url", payload)

    def test_uid_is_stable_and_last_modified_is_only_source_revision_timestamp(self):
        row = event_row(canvas_last_modified="2026-08-09T12:00:00Z")
        first = self.project([row]).events[0]
        second = self.project([dict(row)]).events[0]
        self.assertEqual(first.uid, second.uid)
        self.assertEqual(first.last_modified, datetime(2026, 8, 9, 12, tzinfo=UTC))
        self.assertIsNone(self.project([event_row(canvas_source_revision="rev-1")]).events[0].last_modified)

    def test_malformed_input_and_configured_source_failure_are_typed_failures(self):
        malformed = self.project([event_row(event_end="2026-08-10T09:00:00Z")])
        self.assertEqual(malformed.status, CalendarIcsProjectionStatus.SOURCE_FAILURE)
        self.assertEqual(malformed.diagnostic_code, "malformed_event")

        failed = self.project([], sources=[source_row(status="paused")])
        self.assertEqual(failed.status, CalendarIcsProjectionStatus.SOURCE_FAILURE)
        self.assertEqual(failed.diagnostic_code, "source_unavailable")

    def test_missing_uid_secret_fails_closed_without_partial_output(self):
        with patch.object(ics_contract, "CALENDAR_ICS_UID_SECRET", None):
            outcome = self.project([event_row(), event_row(canvas_event_ref="private:canvas:event-2")])
        self.assertEqual(outcome.status, CalendarIcsProjectionStatus.SOURCE_FAILURE)
        self.assertEqual(outcome.events, ())


if __name__ == "__main__":
    unittest.main()
