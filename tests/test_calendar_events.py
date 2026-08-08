import unittest
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask

import blueprints.calendar_api as calendar_api


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


if __name__ == "__main__":
    unittest.main()
