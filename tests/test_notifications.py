import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from services.database import init_db
from services import notifications
from blueprints import notifications_api


class NotificationPreferenceDefaultTests(unittest.TestCase):
    def test_email_defaults_off_and_normal_channels_default_on(self):
        self.assertFalse(notifications.DEFAULT_PREFERENCES["course_email_enabled"])
        for field in ("calendar_enabled", "course_push_enabled", "dm_enabled", "mention_enabled"):
            self.assertTrue(notifications.DEFAULT_PREFERENCES[field])


def notification_app():
    path = tempfile.mktemp(suffix=".sqlite3")
    app = Flask(__name__)
    app.config["DATABASE_PATH"] = path
    init_db(app, path)
    return app, path


def test_preferences_subscription_feed_and_mutations():
    app, path = notification_app()
    try:
        with app.app_context():
            defaults = notifications.preferences("u1")
            assert defaults["calendar_enabled"] is True
            assert defaults["course_push_enabled"] is True
            assert defaults["dm_enabled"] is True
            assert defaults["mention_enabled"] is True
            assert defaults["course_email_enabled"] is False
            prefs = notifications.update_preferences("u1", {"calendar_lead_minutes": [5, 60], "dm_enabled": False})
            assert prefs["calendar_lead_minutes"] == [5, 60]
            assert prefs["dm_enabled"] is False
            subscription_id = notifications.upsert_subscription("u1", {"endpoint": "https://push.example/sub", "keys": {"p256dh": "key", "auth": "auth"}}, "Laptop")
            assert notifications.list_subscriptions("u1")[0]["id"] == subscription_id
            notification_id = notifications.create_feed_item("u1", "calendar", "Exam", "Starts tomorrow.", "/dashboard", dedupe_key="exam-1")
            assert notifications.create_feed_item("u1", "calendar", "Exam", "Starts tomorrow.", "https://evil.example", dedupe_key="exam-1") == notification_id
            assert notifications.unread_count("u1") == 1
            assert notifications.list_feed("u1")["notifications"][0]["target_url"] == "/dashboard"
            no_link_id = notifications.create_feed_item("u1", "notes", "FYI", "No destination", None)
            no_link = next(item for item in notifications.list_feed("u1")["notifications"] if item["id"] == no_link_id)
            assert no_link["target_url"] is None
            assert [item["id"] for item in notifications.list_feed("u1", search="exam")["notifications"]] == [notification_id]
            notifications.mutate_feed("u1", ids=[], delete=True)
            assert len(notifications.list_feed("u1")["notifications"]) == 2
            notifications.mutate_feed("u1", read=True)
            assert notifications.unread_count("u1") == 0
            assert len(notifications.list_feed("u1", status="read")["notifications"]) == 2
            assert notifications.list_feed("u1", status="unread")["notifications"] == []
            notifications.mutate_feed("u1", delete=True)
            assert notifications.list_feed("u1")["notifications"] == []
    finally:
        Path(path).unlink(missing_ok=True)


def test_course_notification_and_email_channels_are_independent():
    app, path = notification_app()
    try:
        with app.app_context():
            email_only = notifications.update_preferences(
                "u1",
                {"course_push_enabled": False, "course_email_enabled": True},
            )
            assert email_only["course_push_enabled"] is False
            assert email_only["course_email_enabled"] is True

            neither = notifications.update_preferences(
                "u1",
                {"course_push_enabled": False, "course_email_enabled": False},
            )
            assert neither["course_push_enabled"] is False
            assert neither["course_email_enabled"] is False
    finally:
        Path(path).unlink(missing_ok=True)


def test_foreground_sync_combines_reads_and_bounds_query_count():
    app, path = notification_app()
    statements = []
    original_connection = notifications.db_connection

    @contextmanager
    def tracked_connection():
        with original_connection() as conn:
            conn.set_trace_callback(statements.append)
            yield conn

    try:
        with app.app_context():
            notifications.update_preferences("u1", {"dm_enabled": False})
            notifications.create_feed_item("u1", "calendar", "Exam", "Soon", "/dashboard")
            with patch.object(notifications, "db_connection", tracked_connection):
                result = notifications.sync_foreground_state(
                    "u1", "tab-1", active=True, device_class="desktop_tablet",
                )

        data_statements = [
            statement for statement in statements
            if statement.lstrip().upper().startswith(("SELECT", "INSERT", "UPDATE", "DELETE"))
        ]
        assert result["active"] is True
        assert result["unread_count"] == 1
        assert result["preferences"]["dm_enabled"] is False
        assert len(data_statements) <= 4
    finally:
        Path(path).unlink(missing_ok=True)


def test_web_presence_skips_redundant_writes_inside_refresh_window():
    app, path = notification_app()
    try:
        with app.app_context(), notifications.db_connection() as conn:
            first = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)
            notifications._touch_web_presence(
                conn, "u1", "tab-1", True, "desktop_tablet", now=first,
            )
            notifications._touch_web_presence(
                conn, "u1", "tab-1", True, "desktop_tablet",
                now=first.replace(microsecond=500000),
            )
            row = conn.execute(
                "SELECT last_seen_at FROM notification_web_presence WHERE user_id='u1' AND tab_id='tab-1'"
            ).fetchone()
        assert row["last_seen_at"] == "2026-07-11T12:00:00Z"
    finally:
        Path(path).unlink(missing_ok=True)


def test_push_configuration_requires_public_and_readable_private_key():
    with patch.dict("os.environ", {"VAPID_PUBLIC_KEY": "public", "VAPID_PRIVATE_KEY": "/missing/key.pem"}, clear=False):
        assert notifications.push_configuration() == {"configured": False, "public_key": ""}
    with tempfile.NamedTemporaryFile() as private_key:
        with patch.dict("os.environ", {"VAPID_PUBLIC_KEY": "public", "VAPID_PRIVATE_KEY": private_key.name}, clear=False):
            assert notifications.push_configuration() == {"configured": True, "public_key": "public"}


def test_delivery_removes_expired_subscription():
    app, path = notification_app()
    try:
        with app.app_context():
            notifications.upsert_subscription("u1", {"endpoint": "https://push.example/gone", "keys": {"p256dh": "key", "auth": "auth"}}, "Phone")
            notification_id = notifications.create_feed_item("u1", "test", "Test", "Body", "/dashboard?notifications=open")
            with patch.object(notifications, "_send", return_value=201):
                result = notifications.deliver("u1", notification_id, "test", "Test", "Body", "/dashboard?notifications=open")
            assert result == {"accepted": 1, "failed": 0}
    finally:
        Path(path).unlink(missing_ok=True)


def test_test_notification_distinguishes_missing_and_failed_subscriptions():
    app, path = notification_app()
    app.register_blueprint(notifications_api.notifications_bp)
    user = type("User", (), {"id": "u1", "is_authenticated": True})()
    try:
        with app.test_request_context("/api/notifications/test", method="POST"), \
                patch.object(notifications_api, "current_user", user):
            response, status = notifications_api.test_notification.__wrapped__()
            assert status == 409
            assert response.get_json()["code"] == "no_push_subscription"

        with app.app_context():
            notifications.upsert_subscription("u1", {"endpoint": "https://push.example/device", "keys": {"p256dh": "key", "auth": "auth"}}, "Laptop")
        with app.test_request_context("/api/notifications/test", method="POST"), \
                patch.object(notifications_api, "current_user", user), \
                patch.object(notifications, "notify", return_value=("n1", {"accepted": 0, "failed": 1})):
            response, status = notifications_api.test_notification.__wrapped__()
            assert status == 502
            assert response.get_json()["code"] == "push_delivery_failed"
    finally:
        Path(path).unlink(missing_ok=True)


def test_active_laptop_session_uses_in_app_delivery_before_push():
    app, path = notification_app()
    try:
        with app.app_context():
            notifications.upsert_subscription("u1", {"endpoint": "https://push.example/device", "keys": {"p256dh": "key", "auth": "auth"}}, "Laptop")
            notifications.touch_web_presence("u1", "tab-1", active=True, device_class="desktop_tablet")
            with patch.object(notifications, "_send") as send:
                result = notifications.deliver("u1", "n1", "calendar", "Exam", "Starts soon", "/dashboard")
            assert result == {"accepted": 1, "failed": 0}
            send.assert_not_called()

            with patch.object(notifications, "_send", return_value=201) as send:
                result = notifications.deliver("u1", "n-test", "test", "Test", "Browser delivery", "/settings#notifications", force_push=True)
            assert result == {"accepted": 1, "failed": 0}
            send.assert_called_once()

            notifications.touch_web_presence("u1", "tab-1", active=False, device_class="desktop_tablet")
            with patch.object(notifications, "_send", return_value=201) as send:
                result = notifications.deliver("u1", "n2", "calendar", "Exam", "Starts soon", "/dashboard")
            assert result == {"accepted": 1, "failed": 0}
            send.assert_called_once()
    finally:
        Path(path).unlink(missing_ok=True)


def test_foreground_delivery_is_acknowledged_or_released_to_push():
    app, path = notification_app()
    try:
        with app.app_context():
            from services.database import db_connection

            notifications.upsert_subscription("u1", {"endpoint": "https://push.example/device", "keys": {"p256dh": "key", "auth": "auth"}}, "Laptop")
            notifications.touch_web_presence("u1", "tab-1", active=True, device_class="desktop_tablet")
            with patch.object(notifications, "_send") as send:
                notifications.deliver("u1", "n-ack", "calendar", "Exam", "Starts soon", "/dashboard")
            send.assert_not_called()
            assert notifications.pending_foreground_ids("u1") == ["n-ack"]
            assert notifications.acknowledge_foreground("u1", ["n-ack"]) == 1
            assert notifications.pending_foreground_ids("u1") == []

            notifications.deliver("u1", "n-fallback", "calendar", "Lab", "Starts soon", "/dashboard")
            with db_connection() as conn:
                conn.execute("UPDATE notification_foreground_queue SET deliver_after='2020-01-01T00:00:00Z' WHERE notification_id='n-fallback'")
            with patch.object(notifications, "_send", return_value=201) as send:
                assert notifications.flush_foreground_queue(datetime(2026, 7, 11, tzinfo=timezone.utc)) == 1
            send.assert_called_once()
            assert notifications.pending_foreground_ids("u1") == []

            notifications.deliver("u1", "n-read", "calendar", "Review", "Starts soon", "/dashboard")
            assert notifications.pending_foreground_ids("u1") == ["n-read"]
            notifications.mutate_feed("u1", ["n-read"], read=True)
            assert notifications.pending_foreground_ids("u1") == []
    finally:
        Path(path).unlink(missing_ok=True)


def test_calendar_reminder_is_claimed_once():
    app, path = notification_app()
    try:
        with app.app_context():
            from services.database import db_connection
            with db_connection() as conn:
                conn.execute("INSERT INTO user_settings (id,user_id,created_at,timezone) VALUES ('s1','u1','2026-01-01T00:00:00Z','UTC')")
                conn.execute("INSERT INTO user_events (id,user_id,title,start,end,is_all_day,created_at) VALUES ('e1','u1','Exam','2026-07-11T12:10:00Z','2026-07-11T13:00:00Z',0,'2026-01-01T00:00:00Z')")
            with patch.object(notifications, "notify", return_value=("n1", {"accepted": 1, "failed": 0})) as send:
                now = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)
                assert notifications.check_calendar_reminders(now) == 1
                assert notifications.check_calendar_reminders(now) == 0
            assert send.call_count == 1
    finally:
        Path(path).unlink(missing_ok=True)


def test_calendar_reminders_use_each_events_alert_setting():
    app, path = notification_app()
    try:
        with app.app_context():
            from services.database import db_connection
            with db_connection() as conn:
                conn.execute("INSERT INTO user_settings (id,user_id,created_at,timezone) VALUES ('s1','u1','2026-01-01T00:00:00Z','UTC')")
                conn.execute("INSERT INTO user_events (id,user_id,title,start,end,is_all_day,reminder_minutes,created_at) VALUES ('e1','u1','Quiz','2026-07-11T12:05:00Z','2026-07-11T13:00:00Z',0,5,'2026-01-01T00:00:00Z')")
                conn.execute("INSERT INTO user_events (id,user_id,title,start,end,is_all_day,reminder_minutes,created_at) VALUES ('e2','u1','Silent exam','2026-07-11T12:05:00Z','2026-07-11T13:00:00Z',0,-1,'2026-01-01T00:00:00Z')")
                conn.execute("INSERT INTO user_events (id,user_id,title,start,end,is_all_day,reminder_minutes,created_at) VALUES ('e3','u1','Holiday','2026-07-12T00:00:00Z','2026-07-13T00:00:00Z',1,-1,'2026-01-01T00:00:00Z')")
            with patch.object(notifications, "notify", return_value=("n1", {"accepted": 1, "failed": 0})) as send:
                assert notifications.check_calendar_reminders(datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)) == 1
            assert send.call_count == 1
            assert send.call_args.args[2] == "Quiz"
            assert send.call_args.args[3] == "Starts in 5 minutes."
    finally:
        Path(path).unlink(missing_ok=True)


def test_task_reminder_uses_task_copy_and_deep_link():
    app, path = notification_app()
    try:
        with app.app_context():
            from services.database import db_connection
            with db_connection() as conn:
                conn.execute("INSERT INTO user_settings (id,user_id,created_at,timezone) VALUES ('s1','u1','2026-01-01T00:00:00Z','UTC')")
                conn.execute("INSERT INTO tasks (id,user_id,list_id,title,deadline_at,deadline_time,timezone,reminder_minutes,created_at) VALUES ('t1','u1','l1','Submit essay','2026-07-11T12:10:00Z','12:10','UTC',10,'2026-01-01T00:00:00Z')")
            with patch.object(notifications, "notify", return_value=("n1", {"accepted": 1, "failed": 0})) as send:
                assert notifications.check_calendar_reminders(datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)) == 1
            assert send.call_args.args[2] == "Submit essay"
            assert send.call_args.args[3] == "Due in 10 minutes."
            assert send.call_args.args[4] == "/tasks?task=t1"
            assert send.call_args.kwargs["source_ref"] == "task:t1:single"
    finally:
        Path(path).unlink(missing_ok=True)


def test_repeating_task_reminders_skip_completed_occurrences():
    app, path = notification_app()
    try:
        with app.app_context():
            from services.database import db_connection
            with db_connection() as conn:
                conn.execute("INSERT INTO user_settings (id,user_id,created_at,timezone) VALUES ('s1','u1','2026-01-01T00:00:00Z','UTC')")
                conn.execute("INSERT INTO tasks (id,user_id,list_id,title,deadline_at,deadline_time,timezone,recurrence_json,reminder_minutes,created_at) VALUES ('t1','u1','l1','Weekly review','2026-07-11T12:10:00Z','12:10','UTC',?,10,'2026-01-01T00:00:00Z')", ['{"every":1,"unit":"week","startDate":"2026-07-11","endDate":null}'])
                conn.execute("INSERT INTO task_completions (id,user_id,task_id,occurrence_key,completed_at) VALUES ('c1','u1','t1','2026-07-18','2026-07-18T11:00:00Z')")
            with patch.object(notifications, "notify", return_value=("n1", {"accepted": 1, "failed": 0})) as send:
                assert notifications.check_calendar_reminders(datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc)) == 0
            send.assert_not_called()
    finally:
        Path(path).unlink(missing_ok=True)


def test_date_only_task_can_alert_at_nine_on_due_date():
    app, path = notification_app()
    try:
        with app.app_context():
            from services.database import db_connection
            with db_connection() as conn:
                conn.execute("INSERT INTO user_settings (id,user_id,created_at,timezone) VALUES ('s1','u1','2026-01-01T00:00:00Z','UTC')")
                conn.execute("INSERT INTO tasks (id,user_id,list_id,title,deadline_at,deadline_time,timezone,reminder_minutes,created_at) VALUES ('t1','u1','l1','Reading day','2026-07-12T00:00:00Z',NULL,'UTC',-540,'2026-01-01T00:00:00Z')")
            with patch.object(notifications, "notify", return_value=("n1", {"accepted": 1, "failed": 0})) as send:
                assert notifications.check_calendar_reminders(datetime(2026, 7, 12, 9, 0, tzinfo=timezone.utc)) == 1
            assert send.call_args.args[3] == "Due today."
    finally:
        Path(path).unlink(missing_ok=True)


def test_all_day_event_can_alert_at_nine_on_the_event_day():
    app, path = notification_app()
    try:
        with app.app_context():
            from services.database import db_connection
            with db_connection() as conn:
                conn.execute("INSERT INTO user_settings (id,user_id,created_at,timezone) VALUES ('s1','u1','2026-01-01T00:00:00Z','UTC')")
                conn.execute("INSERT INTO user_events (id,user_id,title,start,end,is_all_day,reminder_minutes,created_at) VALUES ('e1','u1','Move-in day','2026-07-12T00:00:00Z','2026-07-13T00:00:00Z',1,-540,'2026-01-01T00:00:00Z')")
            with patch.object(notifications, "notify", return_value=("n1", {"accepted": 1, "failed": 0})) as send:
                assert notifications.check_calendar_reminders(datetime(2026, 7, 12, 9, 0, tzinfo=timezone.utc)) == 1
            assert send.call_args.args[3] == "Starts today."
    finally:
        Path(path).unlink(missing_ok=True)


def test_imported_event_uses_its_per_user_alert_override():
    app, path = notification_app()
    try:
        with app.app_context():
            from services.database import db_connection
            event_ref = notifications._feed_event_ref({"feed_url_hash": "feed-hash", "event_uid": "uid-1"})
            with db_connection() as conn:
                conn.execute("INSERT INTO user_settings (id,user_id,created_at,timezone) VALUES ('s1','u1','2026-01-01T00:00:00Z','UTC')")
                conn.execute("INSERT INTO calendar_cache (id,user_id,feed_url_hash,event_uid,event_title,event_start,event_end,is_all_day) VALUES ('c1','u1','feed-hash','uid-1','Seminar','2026-07-11T12:30:00Z','2026-07-11T13:30:00Z',0)")
                conn.execute("INSERT INTO user_event_overrides (id,user_id,event_ref,hidden,reminder_minutes,created_at) VALUES ('o1','u1',?,0,30,'2026-01-01T00:00:00Z')", [event_ref])
            with patch.object(notifications, "notify", return_value=("n1", {"accepted": 1, "failed": 0})) as send:
                assert notifications.check_calendar_reminders(datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)) == 1
            assert send.call_args.args[2] == "Seminar"
            assert send.call_args.args[3] == "Starts in 30 minutes."
    finally:
        Path(path).unlink(missing_ok=True)
