import unittest
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask

import blueprints.calendar_api as calendar_api
from services import calendar_events


class CalendarEventsRouteTestCase(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)

    def test_get_events_delegates_through_patchable_blueprint_binding(self):
        user = SimpleNamespace(id="user-1")
        sentinel = object()

        with self.app.test_request_context(
            "/api/calendar/events?start=2026-05-01T00:00:00Z"
        ):
            with patch.object(calendar_api, "current_user", user), patch.object(
                calendar_api,
                "get_events_response",
                return_value=sentinel,
            ) as get_events_response:
                response = calendar_api.get_events.__wrapped__()

        self.assertIs(response, sentinel)
        get_events_response.assert_called_once()
        args = get_events_response.call_args.args
        self.assertEqual(args[0], "user-1")
        self.assertEqual(args[1], "user-1")
        self.assertEqual(args[2].get("start"), "2026-05-01T00:00:00Z")
        self.assertIs(args[3]["first_row"], calendar_api.first_row)

    def test_get_events_response_returns_serialized_events_and_metadata(self):
        settings = {"feed_refresh_minutes": 30}
        cache_event = {"id": "cache-1"}
        created_event = {"id": "created-1"}
        task_event = {"id": "task-1", "source": "tasks"}
        rows_by_collection = {
            "calendar_cache": [cache_event],
            "user_events": [created_event],
        }
        refresh_initial_feed_cache = unittest.mock.Mock(return_value=(False, None))
        dependencies = {
            "collections": {
                "user_settings": "user_settings",
                "calendar_cache": "calendar_cache",
                "user_events": "user_events",
            },
            "query": SimpleNamespace(
                equal=lambda field, values: ("equal", field, values),
                order_asc=lambda field: ("order_asc", field),
            ),
            "jsonify": lambda payload: payload,
            "first_row": lambda _collection, _queries: settings,
            "list_calendar_rows_all": lambda collection, _queries: rows_by_collection[collection],
            "logger": unittest.mock.Mock(),
            "parse_range_param": lambda _value: None,
            "configured_feed_urls": lambda _settings: [],
            "load_calendar_preferences": lambda _user_id: {"timezone": "UTC"},
            "load_calendar_feed_metadata": lambda _user_id: [],
            "load_local_calendar_sources": lambda _user_id: [],
            "load_event_overrides": lambda _user_id: [],
            "refresh_initial_feed_cache": refresh_initial_feed_cache,
            "filter_configured_cache_events": lambda events, _urls: events,
            "task_calendar_payload": lambda _user_id, _preferences, _start, _end: (
                [task_event],
                {"id": "task-source"},
            ),
            "append_task_calendar_source": lambda sources, source: [*sources, source],
            "configured_calendar_sources": lambda *_args: [{"id": "feed-source"}],
            "serialize_event": lambda event, _settings: {
                "id": event["id"],
                "source": "cache",
            },
            "apply_event_override": lambda event, _override: event,
            "serialize_user_event": lambda event: {
                "id": event["id"],
                "source": "created",
            },
            "api_event_overlaps_range": lambda *_args: True,
            "resolve_last_fetched": lambda _user_id: "2026-05-01T12:00:00Z",
        }

        response = calendar_events.get_events_response(
            "owner-1",
            "response-user-1",
            {},
            dependencies,
        )

        self.assertEqual(response["user_id"], "response-user-1")
        self.assertEqual(response["count"], 3)
        self.assertEqual(
            response["events"],
            [
                {"id": "cache-1", "source": "cache"},
                {"id": "created-1", "source": "created"},
                task_event,
            ],
        )
        self.assertEqual(
            response["calendar_sources"],
            [{"id": "feed-source"}, {"id": "task-source"}],
        )
        self.assertEqual(response["refresh_interval_minutes"], 30)
        self.assertEqual(response["last_fetched"], "2026-05-01T12:00:00Z")
        self.assertFalse(response["feed_configured"])
        self.assertIsNone(response["refresh_error"])
        refresh_initial_feed_cache.assert_not_called()


if __name__ == "__main__":
    unittest.main()
