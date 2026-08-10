import os
import sys
import tempfile
import types
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask
from flask_login import UserMixin

from extensions import csrf, login_manager
from services import database
from services import admin_analytics
import blueprints.admin as admin


class TestUser(UserMixin):
    def __init__(self, user_id):
        self.id = user_id
        self.email = "admin@example.com"
        self.name = "Admin User"


class AdminAnalyticsRouteTestCase(unittest.TestCase):
    def setUp(self):
        previous_loader = login_manager._user_callback
        self.addCleanup(setattr, login_manager, "_user_callback", previous_loader)
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.app = Flask(
            __name__,
            template_folder=os.path.join(root, "templates"),
            static_folder=os.path.join(root, "static"),
        )
        self.app.secret_key = "test"
        self.app.config["SERVER_NAME"] = "example.test"
        self.app.config["WTF_CSRF_CHECK_DEFAULT"] = False
        login_manager.unauthorized_callback = None
        login_manager.login_view = None
        login_manager.init_app(self.app)
        csrf.init_app(self.app)
        self.app.jinja_env.filters["avatar_url"] = lambda url, size=32: url
        self.app.register_blueprint(admin.admin_bp)
        os.environ["ADMIN_USER_IDS"] = "admin-1"

        @login_manager.user_loader
        def load_user(user_id):
            if user_id == "admin-1":
                return TestUser("admin-1")
            return None

    def tearDown(self):
        os.environ.pop("ADMIN_USER_IDS", None)

    def _login(self, client):
        with client.session_transaction() as session:
            session["_user_id"] = "admin-1"
            session["_fresh"] = True

    def _metrics(self):
        return {
            "total_users": 3,
            "emory_users": 2,
            "non_emory_users": 1,
            "pending_requests": 1,
            "saved_courses": 4,
            "active_course_tracks": 5,
            "paused_course_tracks": 6,
            "file_storage": {"formatted": "1.5 MB", "file_count": 7, "avatar_count": 2, "error": None},
        }

    def test_admin_analytics_requires_admin(self):
        with self.app.test_client() as client:
            response = client.get("/admin/analytics")

        self.assertIn(response.status_code, {302, 401})

    def test_admin_analytics_page_renders_moved_metrics_and_sidebar_order(self):
        with self.app.test_client() as client:
            self._login(client)
            with patch.object(admin, "_admin_home_metrics", return_value=self._metrics()), \
                    patch.object(admin, "_theme_preference", return_value=None), \
                    patch.object(admin, "_pending_admin_request_count", return_value=0):
                response = client.get("/admin/analytics")

        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("System Numbers", html)
        self.assertIn("Total Users", html)
        self.assertIn("1.5 MB", html)
        self.assertIn("admin-analytics.js", html)
        self.assertIn("data-analytics-main-source", html)
        self.assertIn('data-analytics-metric="pageViews"', html)
        self.assertIn('data-analytics-delta="activeUsers"', html)
        self.assertIn('data-analytics-delta="pageViews"', html)
        self.assertIn('data-analytics-delta="totalUsers"', html)
        self.assertIn('data-analytics-delta="oauth"', html)
        self.assertIn('data-analytics-delta="uniType"', html)
        self.assertIn('data-analytics-country-map', html)
        self.assertIn('data-analytics-detail-list="countries"', html)
        self.assertIn('data-analytics-detail-list="pages"', html)
        self.assertIn('data-analytics-detail-range-dropdown="countries"', html)
        self.assertIn('data-analytics-detail-range-dropdown="pages"', html)
        self.assertIn("admin-analytics-country-range-menu", html)
        self.assertIn("admin-analytics-page-range-menu", html)
        self.assertIn("admin-analytics-ga-detail-card--country", html)
        self.assertIn("admin-analytics-ga-detail-card--pages", html)
        self.assertEqual(html.count("admin-analytics-ga-detail-card "), 2)
        self.assertNotIn("admin-analytics-ga-details", html)
        self.assertLess(
            html.index('data-analytics-detail-list="countries"'),
            html.index('data-analytics-detail-range-dropdown="countries"'),
        )
        self.assertLess(
            html.index('data-analytics-detail-list="pages"'),
            html.index('data-analytics-detail-range-dropdown="pages"'),
        )
        self.assertNotIn('data-analytics-list="topPages"', html)
        self.assertNotIn('data-analytics-list="featureUsage"', html)
        self.assertNotIn(">Feature Usage<", html)
        self.assertNotIn(">Top Pages<", html)
        self.assertNotIn("data-analytics-engagement-source", html)
        self.assertNotIn("data-analytics-main-title", html)
        self.assertNotIn("data-analytics-main-description", html)
        self.assertNotIn(">Engagement<", html)
        self.assertNotIn("Live GA4 Data API status", html)
        self.assertNotIn("data-analytics-ga4", html)
        self.assertLess(html.index(">Auth<"), html.index(">Analytics<"))
        self.assertLess(html.index(">Analytics<"), html.index(">APSwiftly<"))

    def test_admin_analytics_data_normalizes_range_and_timezone(self):
        payload = {"range": "30d", "timezone": "UTC"}
        with self.app.test_client() as client:
            self._login(client)
            with patch.object(admin, "analytics_payload", return_value=payload) as analytics_payload:
                response = client.get("/admin/analytics/data?range=bogus&tz=America/Phoenix")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), payload)
        analytics_payload.assert_called_once_with(range_key="30d", tz_name="America/Phoenix")


class AdminAnalyticsServiceTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = os.path.join(self.temp_dir.name, "nest.sqlite3")
        database.init_db(path=self.db_path)

    def _insert_fixture_data(self):
        with database.db_connection(self.db_path) as conn:
            conn.executemany(
                """
                INSERT INTO users (
                    id, google_id, email, name, onboarding_complete, onboarding_step,
                    education_level, school, school_key, emory_student, created_at, last_login, provider
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    ("user-1", "google-1", "one@example.com", "One", 1, 1, "Undergraduate", "Emory University", "emory-university", 1, "2026-05-01T12:00:00Z", "2026-05-03T12:00:00Z", "google"),
                    ("user-2", "discord-2", "two@example.com", "Two", 0, 2, "High School", "Example High", "example-high", 0, "2026-05-02T12:00:00Z", "2026-05-03T13:00:00Z", "discord"),
                    ("user-3", "github-3", "three@example.com", "Three", 1, 1, "Graduate", "Example University", "example-university", 0, "2026-05-03T12:00:00Z", "2026-05-03T14:00:00Z", "github"),
                ],
            )
            conn.executemany(
                """
                INSERT INTO analytics_events (id, user_id, event_type, path, endpoint, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    ("event-1", "user-1", "page_view", "/dashboard", "dashboard.dashboard", "2026-05-03T12:05:00Z"),
                    ("event-2", "user-2", "page_view", "/notes", "dashboard.notes", "2026-05-03T13:05:00Z"),
                    ("event-3", "user-1", "page_view", "/notes", "dashboard.notes", "2026-05-03T13:20:00Z"),
                ],
            )
            conn.execute(
                "INSERT INTO daily_active_users (user_id, active_date) VALUES (?, ?)",
                ["user-3", "2026-05-03"],
            )
            conn.execute(
                "INSERT INTO tasks (id, user_id, list_id, title, created_at) VALUES (?, ?, ?, ?, ?)",
                ["task-1", "user-1", "list-1", "Read", "2026-05-03T12:10:00Z"],
            )
            conn.execute(
                "INSERT INTO notes (id, user_id, title, content, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                ["note-1", "user-1", "Note", "[]", "2026-05-03T12:10:00Z", "2026-05-03T12:10:00Z"],
            )
            conn.execute(
                """
                INSERT INTO user_courses (id, user_id, term, subject, catalog, added_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                ["course-1", "user-1", "Fall_2026", "CHEM", "150", "2026-05-03T12:10:00Z"],
            )

    def test_analytics_payload_aggregates_requested_sections(self):
        self._insert_fixture_data()
        now = datetime(2026, 5, 3, 15, 0, tzinfo=timezone.utc)

        payload = admin_analytics.analytics_payload(
            range_key="7d",
            tz_name="America/Phoenix",
            include_ga=False,
            now=now,
            path=self.db_path,
        )

        self.assertEqual(payload["range"], "7d")
        self.assertEqual(payload["timezone"], "America/Phoenix")
        self.assertEqual(payload["cards"]["totalUsers"], 3)
        self.assertEqual(payload["cards"]["activeUsers"], 3)
        self.assertEqual(payload["cards"]["pageViews"], 0)
        self.assertEqual(payload["series"]["activeUsers"][-1]["value"], 3)
        self.assertTrue(all(point["value"] == 0 for point in payload["series"]["pageViews"]))
        self.assertEqual(payload["engagement"]["topPages"], [])
        self.assertTrue(payload["comparison"]["enabled"])
        self.assertEqual(payload["gaDetails"]["countries"], [])
        self.assertEqual(payload["gaDetails"]["pages"], [])
        oauth = {row["key"]: row["value"] for row in payload["breakdowns"]["oauth"]}
        self.assertEqual(oauth["google"], 1)
        self.assertEqual(oauth["discord"], 1)
        self.assertEqual(oauth["github"], 1)
        uni_type = {row["key"]: row["value"] for row in payload["breakdowns"]["uniType"]}
        self.assertEqual(uni_type["emory"], 1)
        self.assertEqual(uni_type["high_school"], 1)
        self.assertEqual(uni_type["graduate"], 1)
        oauth_series = {row["key"]: row for row in payload["series"]["oauth"]}
        self.assertEqual(oauth_series["google"]["points"][-1]["value"], 1)
        self.assertEqual(oauth_series["discord"]["points"][-1]["value"], 1)
        self.assertEqual(oauth_series["github"]["points"][-1]["value"], 1)
        uni_series = {row["key"]: row for row in payload["series"]["uniType"]}
        self.assertEqual(uni_series["emory"]["points"][-1]["value"], 1)
        self.assertEqual(uni_series["high_school"]["points"][-1]["value"], 1)
        self.assertEqual(uni_series["graduate"]["points"][-1]["value"], 1)
        self.assertEqual(payload["engagement"]["onboarding"]["complete"], 2)
        feature_usage = {row["label"]: row["value"] for row in payload["engagement"]["featureUsage"]}
        self.assertEqual(feature_usage["Tasks"], 1)
        self.assertEqual(feature_usage["Courses"], 1)
        self.assertEqual(feature_usage["Notes"], 1)

    def test_missing_ga_credentials_returns_non_failing_state(self):
        with patch.dict(os.environ, {}, clear=True):
            payload = admin_analytics.analytics_payload(
                range_key="30d",
                tz_name="UTC",
                now=datetime(2026, 5, 3, 15, 0, tzinfo=timezone.utc),
                path=self.db_path,
            )

        self.assertFalse(payload["ga4"]["configured"])
        self.assertEqual(payload["ga4"]["status"], "not_configured")
        self.assertEqual(payload["cards"]["activeUsers"], 0)
        self.assertEqual(payload["cards"]["pageViews"], 0)
        self.assertTrue(payload["comparison"]["enabled"])
        self.assertIn("totalUsers", payload["comparison"]["metrics"])
        self.assertIn("activeUsers", payload["comparison"]["metrics"])
        self.assertIn("oauth", payload["comparison"]["metrics"])
        self.assertIn("uniType", payload["comparison"]["metrics"])
        self.assertEqual(payload["gaDetails"]["countries"], [])
        self.assertEqual(payload["gaDetails"]["pages"], [])

    def test_authenticated_activity_uses_hourly_records_and_browser_timezone(self):
        with database.db_connection(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO users (id, google_id, email, name, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                ["signed-in-1", "google-signed-in-1", "signed-in@example.com", "Signed In", "2026-05-01T12:00:00Z"],
            )

        admin_analytics.record_authenticated_activity(
            "signed-in-1",
            at=datetime(2026, 5, 3, 2, 10, tzinfo=timezone.utc),
            path=self.db_path,
        )
        admin_analytics.record_authenticated_activity(
            "signed-in-1",
            at=datetime(2026, 5, 3, 2, 45, tzinfo=timezone.utc),
            path=self.db_path,
        )

        with database.db_connection(self.db_path) as conn:
            bucket_count = conn.execute("SELECT COUNT(*) FROM user_activity_buckets").fetchone()[0]
            latest_seen = conn.execute("SELECT last_seen_at FROM user_activity_buckets").fetchone()[0]

        payload = admin_analytics.analytics_payload(
            range_key="7d",
            tz_name="America/Phoenix",
            include_ga=False,
            now=datetime(2026, 5, 3, 15, 0, tzinfo=timezone.utc),
            path=self.db_path,
        )

        self.assertEqual(bucket_count, 1)
        self.assertEqual(latest_seen, "2026-05-03T02:45:00Z")
        self.assertEqual(payload["cards"]["activeUsers"], 1)
        may_second = next(point for point in payload["series"]["activeUsers"] if point["key"] == "2026-05-02")
        self.assertEqual(may_second["value"], 1)
        self.assertEqual(payload["sources"]["featureUsage"]["label"], "Nest database")

    def test_authenticated_activity_limits_writes_to_once_per_minute(self):
        first = datetime(2026, 5, 3, 2, 10, tzinfo=timezone.utc)
        self.assertTrue(admin_analytics.record_authenticated_activity("user-1", at=first, path=self.db_path))
        self.assertFalse(admin_analytics.record_authenticated_activity(
            "user-1",
            at=datetime(2026, 5, 3, 2, 10, 30, tzinfo=timezone.utc),
            path=self.db_path,
        ))
        self.assertTrue(admin_analytics.record_authenticated_activity(
            "user-1",
            at=datetime(2026, 5, 3, 2, 11, tzinfo=timezone.utc),
            path=self.db_path,
        ))
        self.assertTrue(admin_analytics.record_authenticated_activity(
            "boundary-user",
            at=datetime(2026, 5, 3, 2, 59, 30, tzinfo=timezone.utc),
            path=self.db_path,
        ))
        self.assertFalse(admin_analytics.record_authenticated_activity(
            "boundary-user",
            at=datetime(2026, 5, 3, 3, 0, tzinfo=timezone.utc),
            path=self.db_path,
        ))
        with database.db_connection(self.db_path) as conn:
            rows = conn.execute(
                "SELECT last_seen_at FROM user_activity_buckets WHERE user_id='user-1'"
            ).fetchall()
            boundary_rows = conn.execute(
                "SELECT last_seen_at FROM user_activity_buckets WHERE user_id='boundary-user'"
            ).fetchall()
        self.assertEqual([row["last_seen_at"] for row in rows], ["2026-05-03T02:11:00Z"])
        self.assertEqual(
            [row["last_seen_at"] for row in boundary_rows],
            ["2026-05-03T02:59:30Z"],
        )

    def test_authenticated_activity_excludes_realtime_polling_paths(self):
        app = Flask(__name__)
        with app.test_request_context("/api/notifications/sync", method="POST"), \
                patch.object(admin_analytics.database, "db_connection") as connect:
            recorded = admin_analytics.record_authenticated_activity("user-1")

        self.assertFalse(recorded)
        connect.assert_not_called()

    def test_ga4_defaults_to_nest_property_and_filters_hostname(self):
        def fake_row(dimensions, metrics):
            return SimpleNamespace(
                dimension_values=[SimpleNamespace(value=value) for value in dimensions],
                metric_values=[SimpleNamespace(value=str(value)) for value in metrics],
            )

        class FakeAnalyticsClient:
            report_requests = []

            def run_report(self, request):
                FakeAnalyticsClient.report_requests.append(request)
                dimension_names = [dimension.name for dimension in getattr(request, "dimensions", []) or []]
                date_range = (getattr(request, "date_ranges", []) or [SimpleNamespace(start_date="")])[0]
                previous = getattr(date_range, "start_date", "") == "2026-04-20"
                if not dimension_names:
                    return SimpleNamespace(rows=[fake_row([], [10, 60, 0] if previous else [7, 42, 99])])
                if dimension_names == ["date"]:
                    return SimpleNamespace(rows=[
                        fake_row(["20260501"], [2, 10]),
                        fake_row(["20260503"], [5, 32]),
                    ])
                if dimension_names == ["pageTitle", "pagePath"]:
                    if previous:
                        return SimpleNamespace(rows=[
                            fake_row(["Resources - APStudy Nest", "/resources"], [10]),
                            fake_row(["Dashboard | APStudy Nest", "/dashboard"], [20]),
                            fake_row(["APStudy Nest", "/"], [5]),
                        ])
                    return SimpleNamespace(rows=[
                        fake_row(["Notes | APStudy Nest", "/notes"], [12]),
                        fake_row(["Dashboard - APStudy Nest", "/dashboard"], [30]),
                        fake_row(["APStudy Nest", "/"], [15]),
                    ])
                if dimension_names == ["countryId", "country"]:
                    if previous:
                        return SimpleNamespace(rows=[
                            fake_row(["US", "United States"], [2]),
                            fake_row(["SG", "Singapore"], [6]),
                        ])
                    return SimpleNamespace(rows=[
                        fake_row(["US", "United States"], [4]),
                        fake_row(["SG", "Singapore"], [3]),
                    ])
                return SimpleNamespace(rows=[])

        class FakeType:
            def __init__(self, **kwargs):
                self.__dict__.update(kwargs)

        class FakeStringFilter(FakeType):
            class MatchType:
                EXACT = "EXACT"

        class FakeFilter(FakeType):
            StringFilter = FakeStringFilter

        fake_google = types.ModuleType("google")
        fake_analytics = types.ModuleType("google.analytics")
        fake_data = types.ModuleType("google.analytics.data_v1beta")
        fake_types = types.ModuleType("google.analytics.data_v1beta.types")
        fake_data.BetaAnalyticsDataClient = FakeAnalyticsClient
        fake_types.DateRange = FakeType
        fake_types.Dimension = FakeType
        fake_types.Filter = FakeFilter
        fake_types.FilterExpression = FakeType
        fake_types.Metric = FakeType
        fake_types.RunReportRequest = FakeType

        with patch.dict(os.environ, {"GOOGLE_APPLICATION_CREDENTIALS": "/tmp/fake-ga.json"}, clear=True), \
                patch.dict(sys.modules, {
                    "google": fake_google,
                    "google.analytics": fake_analytics,
                    "google.analytics.data_v1beta": fake_data,
                    "google.analytics.data_v1beta.types": fake_types,
                }):
            payload = admin_analytics.analytics_payload(
                range_key="7d",
                tz_name="UTC",
                now=datetime(2026, 5, 3, 15, 0, tzinfo=timezone.utc),
                path=self.db_path,
            )

        self.assertTrue(payload["ga4"]["configured"])
        self.assertEqual(payload["ga4"]["status"], "ok")
        self.assertEqual(payload["ga4"]["propertyId"], "446578226")
        self.assertEqual(payload["ga4"]["hostName"], "nest.apstudy.org")
        self.assertEqual(payload["cards"]["activeUsers"], 0)
        self.assertEqual(payload["cards"]["pageViews"], 42)
        self.assertEqual(payload["series"]["activeUsers"][-1]["value"], 0)
        self.assertEqual(payload["series"]["pageViews"][-1]["value"], 32)
        self.assertEqual(payload["engagement"]["topPages"][0], {"label": "/dashboard", "value": 30})
        self.assertTrue(payload["comparison"]["enabled"])
        self.assertEqual(payload["comparison"]["metrics"]["activeUsers"]["previous"], 0)
        self.assertEqual(payload["comparison"]["metrics"]["activeUsers"]["label"], "0.0%")
        self.assertEqual(payload["comparison"]["metrics"]["pageViews"]["previous"], 60)
        self.assertEqual(payload["comparison"]["metrics"]["pageViews"]["percentChange"], -30.0)
        self.assertEqual(payload["gaDetails"]["countries"][0]["countryId"], "US")
        self.assertEqual(payload["gaDetails"]["countries"][0]["comparison"]["percentChange"], 100.0)
        self.assertEqual(payload["gaDetails"]["pages"][0]["title"], "Dashboard")
        self.assertEqual(payload["gaDetails"]["pages"][0]["path"], "/dashboard")
        self.assertEqual(payload["gaDetails"]["pages"][0]["comparison"]["percentChange"], 50.0)
        landing_page = next(page for page in payload["gaDetails"]["pages"] if page["path"] == "/")
        self.assertEqual(landing_page["title"], "Landing Page")
        self.assertEqual(landing_page["comparison"]["percentChange"], 200.0)
        self.assertEqual(payload["sources"]["traffic"]["label"], "Google Analytics")
        self.assertEqual(len(FakeAnalyticsClient.report_requests), 7)
        for request in FakeAnalyticsClient.report_requests:
            self.assertEqual(request.property, "properties/446578226")
            self.assertEqual(request.dimension_filter.filter.field_name, "hostName")
            self.assertEqual(request.dimension_filter.filter.string_filter.value, "nest.apstudy.org")


if __name__ == "__main__":
    unittest.main()
