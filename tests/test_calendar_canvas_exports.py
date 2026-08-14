import json
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from services import calendar_events, dashboard_summary, ics_builder
import blueprints.calendar_api as calendar_api


def _canvas_row(ref, *, source_status="active", soft_deleted=0):
    return {
        "$id": f"cache-{ref}",
        "user_id": "user-1",
        "canvas_source_id": "source-1",
        "canvas_account_key": "account-1",
        "canvas_event_ref": ref,
        "canvas_source_item_key": f"item-{ref}",
        "canvas_soft_deleted": soft_deleted,
        "source_status": source_status,
        "event_uid": f"uid-{ref}",
        "event_title": f"Canvas {ref}",
        "event_start": "2026-08-20T14:00:00Z",
        "event_end": "2026-08-20T15:00:00Z",
        "is_all_day": False,
        "event_type": "assignment",
        "course_name": "BIO 141",
        "raw_description": "Canvas details",
    }


def _project(_user_id, rows, overrides, **_kwargs):
    projected = []
    for row in rows:
        if row.get("source_status") != "active" or row.get("canvas_soft_deleted"):
            continue
        if (overrides.get(row["canvas_event_ref"]) or {}).get("hidden"):
            continue
        projected.append({
            "uid": row["event_uid"],
            "event_ref": row["canvas_event_ref"],
            "source_type": "canvas",
            "source_id": "source-1",
            "account_key": "private-account",
            "source_item_key": "private-item",
            "sync_state": "private-sync-state",
            "writeback_id": "private-writeback",
            "sanitized_error": "private-error",
            "source_url": "https://canvas.example.edu/private-feed",
            "title": row["event_title"],
            "start": row["event_start"],
            "end": row["event_end"],
            "type": row["event_type"],
            "course": row["course_name"],
            "description": row["raw_description"],
            "is_multi_day": False,
            "span_days": 1,
            "is_all_day": False,
            "calendar_id": "canvas",
            "color": "#0ea5e9",
        })
    return projected


class CalendarCanvasExportTestCase(unittest.TestCase):
    def test_dashboard_uses_projection_and_excludes_revoked_archived_hidden_and_soft_deleted(self):
        rows = [
            _canvas_row("active"),
            _canvas_row("revoked", source_status="revoked"),
            _canvas_row("archived", source_status="archived"),
            _canvas_row("deleted", soft_deleted=1),
            _canvas_row("hidden"),
        ]
        overrides = [{"event_ref": "hidden", "hidden": True}]
        dependencies = {
            "configured_feed_urls": lambda _settings: [],
            "list_calendar_rows_all": lambda table, _queries: (
                rows if table == "calendar_cache" else []
            ),
            "load_calendar_preferences": lambda *_args: [],
            "load_local_calendar_sources": lambda *_args: [],
            "load_calendar_feed_metadata": lambda *_args: {},
            "configured_calendar_sources": lambda *_args: [],
            "filter_configured_cache_events": lambda events, _urls: events,
            "load_event_overrides": lambda _user_id: overrides,
            "project_canvas_events": _project,
            "api_event_overlaps_range": calendar_events._api_event_overlaps_range,
            "serialize_event": calendar_events._serialize_event,
            "apply_event_override": calendar_events._apply_event_override,
            "serialize_user_event": calendar_events._serialize_user_event,
            "task_calendar_events_for_user": lambda *_args: [],
            "logger": unittest.mock.Mock(),
            "as_utc": dashboard_summary.as_utc,
            "date_key": dashboard_summary.date_key,
            "sort_key": dashboard_summary.sort_key,
        }

        summary = dashboard_summary.load_calendar_summary("user-1", {}, dependencies)

        self.assertEqual([event["id"] for event in summary["events"]], ["uid-active"])
        self.assertTrue(summary["setup_complete"])

    def test_public_share_uses_projection_filters_scope_and_strips_private_fields(self):
        outside = _canvas_row("outside")
        outside.update({
            "event_start": "2026-08-30T14:00:00Z",
            "event_end": "2026-08-30T15:00:00Z",
        })
        rows = [_canvas_row("active"), outside]
        share = {
            "$id": "share-1",
            "user_id": "user-1",
            "share_code": "ABCDEFGHIJKLMNOP",
            "is_active": True,
            "include_all_calendars": False,
            "calendar_ids_json": json.dumps(["canvas"]),
            "date_scope": "all",
        }

        def list_rows(table_id, _queries=None, limit=None):
            if table_id == calendar_api.COLLECTIONS["calendar_cache"]:
                return rows
            if table_id == calendar_api.COLLECTIONS["user_events"]:
                return []
            return []

        settings = {"user_id": "user-1", "canvas_ical_url": ""}
        with patch.object(calendar_api, "first_row", return_value=settings), \
                patch.object(calendar_api, "list_calendar_rows_all", side_effect=list_rows), \
                patch.object(calendar_api, "_project_canvas_calendar_events", side_effect=_project):
            payload = calendar_api._public_calendar_events_payload(
                share,
                datetime(2026, 8, 20, tzinfo=timezone.utc),
                datetime(2026, 8, 21, tzinfo=timezone.utc),
            )

        self.assertEqual(payload["count"], 1)
        public_event = payload["events"][0]
        self.assertEqual(public_event["title"], "Canvas active")
        for field in (
            "account_key", "source_id", "source_item_key", "sync_state",
            "writeback_id", "source_url", "sanitized_error",
        ):
            self.assertNotIn(field, public_event)

    def test_ics_uses_projected_canvas_events_and_preserves_all_day_and_timezone(self):
        timed = _canvas_row("timed")
        timed["canvas_timezone"] = "America/Los_Angeles"
        all_day = _canvas_row("all-day")
        all_day.update({
            "event_start": "2026-08-21",
            "event_end": "2026-08-22",
            "is_all_day": True,
        })
        projected = [
            {
                **_project("user-1", [timed], {})[0],
                "start": "2026-08-20T14:00:00Z",
                "end": "2026-08-20T15:00:00Z",
            },
            {
                **_project("user-1", [all_day], {})[0],
                "start": "2026-08-21",
                "end": "2026-08-22",
                "is_all_day": True,
            },
        ]

        with patch.object(ics_builder, "_user_settings", return_value={"timezone": "America/Phoenix"}), \
                patch.object(ics_builder, "list_calendar_rows_all", return_value=[timed, all_day]), \
                patch.object(ics_builder, "_load_projected_events", return_value=(projected, {})), \
                patch.object(ics_builder, "_inject_atlas_schedule"):
            calendar = __import__("icalendar").Calendar.from_ical(
                ics_builder.build_ics_for_user("user-1")
            )

        vevents = [component for component in calendar.walk() if component.name == "VEVENT"]
        self.assertEqual(len(vevents), 2)
        timed_event = next(event for event in vevents if str(event["UID"]).endswith("uid-timed"))
        all_day_event = next(event for event in vevents if str(event["UID"]).endswith("uid-all-day"))
        self.assertEqual(timed_event["DTSTART"].params["TZID"], "America/Los_Angeles")
        self.assertEqual(timed_event["DTSTART"].dt.hour, 7)
        self.assertEqual(all_day_event["DTSTART"].dt.isoformat(), "2026-08-21")
        self.assertEqual(all_day_event["DTSTART"].params["VALUE"], "DATE")


if __name__ == "__main__":
    unittest.main()
