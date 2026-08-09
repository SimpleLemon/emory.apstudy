import os
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

from appwrite.query import Query
from flask import Flask
from flask_login import UserMixin

from appwrite_client import COLLECTIONS
from blueprints import admin
from blueprints.calendar_sources_api import calendar_sources_bp
from extensions import csrf, login_manager
from services import calendar_store as store
from services.feed_fetcher import (
    PERMANENT_FAILURE_QUARANTINE_THRESHOLD,
    TRANSIENT_FAILURE_QUARANTINE_THRESHOLD,
    _record_feed_failure,
    clear_feed_quarantine,
    derive_feed_status,
    fetch_and_cache_feeds,
)
from tests.support.harness import bootstrap_calendar_db, reset_flask_login_manager


class TestUser(UserMixin):
    def __init__(self, user_id):
        self.id = user_id
        self.email = "user@example.com"
        self.name = "Test User"


class CalendarFeedHealthTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "nest.sqlite3")
        self.previous_database_path = os.environ.get("DATABASE_PATH")
        self.previous_calendar_path = os.environ.get("CALENDAR_SQLITE_PATH")
        os.environ["DATABASE_PATH"] = self.db_path
        os.environ.pop("CALENDAR_SQLITE_PATH", None)
        bootstrap_calendar_db(self.db_path)
        self.user_id = "user-1"
        self.feed_url = "https://example.com/calendar.ics"

    def tearDown(self):
        if self.previous_database_path is None:
            os.environ.pop("DATABASE_PATH", None)
        else:
            os.environ["DATABASE_PATH"] = self.previous_database_path
        if self.previous_calendar_path is None:
            os.environ.pop("CALENDAR_SQLITE_PATH", None)
        else:
            os.environ["CALENDAR_SQLITE_PATH"] = self.previous_calendar_path
        self.tmpdir.cleanup()

    def test_permanent_failure_quarantines_at_threshold(self):
        now = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)
        for _ in range(PERMANENT_FAILURE_QUARANTINE_THRESHOLD):
            row = _record_feed_failure(
                self.user_id,
                self.feed_url,
                ValueError("Feed fetch failed: response is not iCalendar data"),
                now=now,
            )

        self.assertEqual(row["consecutive_failures"], PERMANENT_FAILURE_QUARANTINE_THRESHOLD)
        self.assertTrue(row.get("disabled_at"))
        self.assertEqual(derive_feed_status(row), "quarantined")

    def test_transient_failure_quarantines_later(self):
        now = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)
        for index in range(TRANSIENT_FAILURE_QUARANTINE_THRESHOLD - 1):
            row = _record_feed_failure(
                self.user_id,
                self.feed_url,
                ValueError("Calendar feed request failed."),
                now=now,
            )
            self.assertIsNone(row.get("disabled_at"))
            self.assertEqual(row["consecutive_failures"], index + 1)
            self.assertEqual(derive_feed_status(row), "failing")

        row = _record_feed_failure(
            self.user_id,
            self.feed_url,
            ValueError("Calendar feed request failed."),
            now=now,
        )
        self.assertTrue(row.get("disabled_at"))
        self.assertEqual(derive_feed_status(row), "quarantined")

    def test_success_resets_failure_counters(self):
        now = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)
        _record_feed_failure(
            self.user_id,
            self.feed_url,
            ValueError("Feed fetch failed: response is not iCalendar data"),
            now=now,
        )
        with patch("services.feed_fetcher.list_calendar_rows_all", return_value=[]), \
                patch("services.feed_fetcher.fetch_and_parse_ical", return_value={
                    "status_code": 200,
                    "events": [],
                    "etag": None,
                    "last_modified": None,
                    "feed_url": self.feed_url,
                    "calendar_name": "Example",
                }), \
                patch("services.feed_fetcher._apply_feed_diffs", return_value=0):
            fetch_and_cache_feeds(self.user_id, [self.feed_url], force=True)

        rows = store.list_calendar_rows_all(
            COLLECTIONS["calendar_feeds"],
            [Query.equal("user_id", [self.user_id])],
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["consecutive_failures"], 0)
        self.assertIsNone(rows[0].get("disabled_at"))
        self.assertEqual(derive_feed_status(rows[0]), "ok")

    def test_quarantined_feed_is_skipped_unless_forced(self):
        now = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)
        for _ in range(PERMANENT_FAILURE_QUARANTINE_THRESHOLD):
            _record_feed_failure(
                self.user_id,
                self.feed_url,
                ValueError("Feed fetch failed: response is not iCalendar data"),
                now=now,
            )

        with patch("services.feed_fetcher.fetch_and_parse_ical") as fetch_mock:
            count = fetch_and_cache_feeds(self.user_id, [self.feed_url])
        self.assertEqual(count, 0)
        fetch_mock.assert_not_called()

        with patch("services.feed_fetcher.list_calendar_rows_all", return_value=[]), \
                patch("services.feed_fetcher.fetch_and_parse_ical", return_value={
                    "status_code": 200,
                    "events": [],
                    "etag": None,
                    "last_modified": None,
                    "feed_url": self.feed_url,
                    "calendar_name": "Example",
                }) as fetch_mock, \
                patch("services.feed_fetcher._apply_feed_diffs", return_value=0):
            clear_feed_quarantine(self.user_id, self.feed_url)
            fetch_and_cache_feeds(self.user_id, [self.feed_url], force=True)
        fetch_mock.assert_called_once()

    def test_all_quarantined_returns_zero(self):
        now = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)
        for _ in range(PERMANENT_FAILURE_QUARANTINE_THRESHOLD):
            _record_feed_failure(
                self.user_id,
                self.feed_url,
                ValueError("Feed fetch failed: response is not iCalendar data"),
                now=now,
            )
        self.assertEqual(fetch_and_cache_feeds(self.user_id, [self.feed_url]), 0)
        self.assertEqual(fetch_and_cache_feeds(self.user_id, []), 0)


class AdminConfiguredFeedsTestCase(unittest.TestCase):
    def test_configured_feeds_include_never_fetched_urls(self):
        settings = {
            "canvas_ical_url": "",
            "other_ical_urls_json": '["https://calendar.google.com/calendar/u/0/r"]',
        }
        configured = admin._build_configured_feeds(settings, [])
        self.assertEqual(len(configured), 1)
        self.assertEqual(configured[0]["status"], "never fetched")
        self.assertEqual(configured[0]["origin"], "other")
        self.assertEqual(configured[0]["url"], "https://calendar.google.com/calendar/u/0/r")


class CreateUrlCalendarSourceValidationTestCase(unittest.TestCase):
    def setUp(self):
        previous_loader = login_manager._user_callback
        self.addCleanup(setattr, login_manager, "_user_callback", previous_loader)
        self.app = Flask(__name__)
        self.app.secret_key = "test"
        self.app.config["WTF_CSRF_CHECK_DEFAULT"] = False
        login_manager.init_app(self.app)
        csrf.init_app(self.app)
        self.app.register_blueprint(calendar_sources_bp, url_prefix="/api/calendar")

        @login_manager.user_loader
        def load_user(user_id):
            if user_id == "user-1":
                return TestUser("user-1")
            return None

        self.settings = {
            "$id": "settings-1",
            "user_id": "user-1",
            "canvas_ical_url": "",
            "other_ical_urls_json": "[]",
        }

    def tearDown(self):
        reset_flask_login_manager()

    def test_non_ical_url_is_rejected_before_persist(self):
        update_mock = Mock()
        with self.app.test_client() as client:
            with client.session_transaction() as session:
                session["_user_id"] = "user-1"
                session["_fresh"] = True
            with patch("blueprints.calendar_sources_api._ensure_user_settings", return_value=self.settings), \
                    patch("blueprints.calendar_sources_api.update_row_safe", update_mock), \
                    patch("blueprints.calendar_sources_api.request_entitlements", return_value={
                        "limits": {"max_calendar_feeds": 5},
                        "usage": {"calendar_feeds": 0},
                    }):
                response = client.post(
                    "/api/calendar/sources/url",
                    json={"url": "https://calendar.google.com/calendar/u/0/r"},
                )

        self.assertEqual(response.status_code, 400)
        payload = response.get_json()
        self.assertIn("Google Calendar website", payload["error"])
        update_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
