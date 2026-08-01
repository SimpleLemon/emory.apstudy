import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from flask import Flask

from config import ENVIRONMENT_CONFIG_EXTENSION_KEY, EnvironmentConfig
from services import scheduler


class FakeScheduler:
    def __init__(self, daemon=True):
        self.daemon = daemon
        self.running = False
        self.jobs = []

    def add_job(self, **kwargs):
        job = SimpleNamespace(
            id=kwargs.get("id"),
            name=kwargs.get("name"),
            next_run_time=None,
            kwargs=kwargs,
        )
        self.jobs.append(job)
        return job

    def get_jobs(self):
        return list(self.jobs)

    def get_job(self, job_id):
        for job in self.jobs:
            if job.id == job_id:
                return job
        return None

    def reschedule_job(self, job_id, trigger):
        job = self.get_job(job_id)
        if job is None:
            raise KeyError(job_id)
        job.kwargs["trigger"] = trigger
        return job

    def modify_job(self, job_id, **kwargs):
        job = self.get_job(job_id)
        if job is None:
            raise KeyError(job_id)
        for key, value in kwargs.items():
            setattr(job, key, value)
        return job

    def start(self):
        self.running = True

    def shutdown(self, wait=False):
        self.running = False


class SchedulerDiagnosticsTests(unittest.TestCase):
    def tearDown(self):
        scheduler.shutdown_scheduler()

    def test_scheduler_disabled_emits_diagnostic_without_starting(self):
        app = Flask(__name__)
        with patch.dict(os.environ, {"SCHEDULER_ENABLED": "0"}, clear=False), \
                patch("services.discord_audit.emit_server_log_event", return_value=True) as emit_event:
            scheduler.init_scheduler(app)

        self.assertIsNone(scheduler._scheduler)
        emit_event.assert_called_once()
        self.assertEqual(emit_event.call_args.args[0], "Scheduler Disabled")
        self.assertFalse(emit_event.call_args.kwargs["metadata"]["scheduler_enabled"])

    def test_scheduler_init_prefers_the_registered_app_snapshot(self):
        app = Flask(__name__)
        app.extensions[ENVIRONMENT_CONFIG_EXTENSION_KEY] = EnvironmentConfig(
            flask_secret_key="secret",
            flask_env="testing",
            appwrite_database_id="",
            allow_insecure_http=False,
            frontend_console_diagnostics_enabled=False,
            scheduler_enabled_raw="0",
        )
        with patch.dict(os.environ, {"SCHEDULER_ENABLED": "1"}, clear=False), patch(
            "services.discord_audit.emit_server_log_event", return_value=True
        ) as emit_event:
            scheduler.init_scheduler(app)

        self.assertIsNone(scheduler._scheduler)
        self.assertEqual(emit_event.call_args.args[0], "Scheduler Disabled")

    def test_scheduler_enabled_registers_course_tracking_job_and_emits_started(self):
        app = Flask(__name__)
        with tempfile.TemporaryDirectory() as temp_dir, \
                patch.dict(os.environ, {
                    "SCHEDULER_ENABLED": "1",
                    "DISCORD_GATEWAY_ENABLED": "1",
                    "DISCORD_CHAT_RECONCILE_SECONDS": "300",
                    "SCHEDULER_LOCK_PATH": os.path.join(temp_dir, "scheduler.lock"),
                }, clear=False), \
                patch.object(scheduler, "BackgroundScheduler", FakeScheduler), \
                patch.object(scheduler, "get_course_tracking_refresh_minutes", return_value=30), \
                patch("services.discord_gateway.start_discord_gateway", return_value=True) as start_gateway, \
                patch("services.discord_audit.emit_server_log_event", return_value=True) as emit_event:
            scheduler.init_scheduler(app)

        start_gateway.assert_called_once_with(app)
        status = scheduler.scheduler_status()
        self.assertTrue(status["scheduler_running"])
        self.assertTrue(status["scheduler_lock_acquired"])
        self.assertEqual(
            {job["id"] for job in status["jobs"]},
            {
                "refresh_all_feeds",
                "check_course_seat_tracks",
                "check_notification_reminders",
                "flush_notification_foreground",
                "fetch_daily_quote",
                "cleanup_note_media",
                "reconcile_discord_chat",
                "sync_discord_roles",
            },
        )
        course_tracking_job = next(job for job in status["jobs"] if job["id"] == "check_course_seat_tracks")
        self.assertIn("5 min", course_tracking_job["name"])
        quote_job = scheduler._scheduler.get_job("fetch_daily_quote")
        self.assertEqual(quote_job.kwargs["trigger"].__class__.__name__, "CronTrigger")
        self.assertEqual(quote_job.kwargs["max_instances"], 1)
        self.assertEqual(emit_event.call_args.args[0], "Scheduler Started")
        metadata = emit_event.call_args.kwargs["metadata"]
        self.assertTrue(metadata["scheduler_lock_acquired"])
        self.assertEqual(metadata["scheduler_process_id"], os.getpid())
        self.assertEqual(metadata["course_tracking_refresh_interval_minutes"], 5)
        self.assertTrue(metadata["discord_gateway_enabled"])
        self.assertEqual(metadata["discord_chat_reconcile_seconds"], 300)
        self.assertIn("check_course_seat_tracks", metadata["job_ids"])

    def test_scheduler_gateway_opt_out_registers_legacy_sync_job(self):
        app = Flask(__name__)
        with tempfile.TemporaryDirectory() as temp_dir, \
                patch.dict(os.environ, {
                    "SCHEDULER_ENABLED": "1",
                    "DISCORD_GATEWAY_ENABLED": "0",
                    "DISCORD_CHAT_SYNC_SECONDS": "5",
                    "SCHEDULER_LOCK_PATH": os.path.join(temp_dir, "scheduler.lock"),
                }, clear=False), \
                patch.object(scheduler, "BackgroundScheduler", FakeScheduler), \
                patch.object(scheduler, "get_course_tracking_refresh_minutes", return_value=30), \
                patch("services.discord_gateway.start_discord_gateway", return_value=True) as start_gateway, \
                patch("services.discord_audit.emit_server_log_event", return_value=True):
            scheduler.init_scheduler(app)

        start_gateway.assert_not_called()
        self.assertIn("sync_discord_chat", {job["id"] for job in scheduler.scheduler_status()["jobs"]})
        self.assertNotIn("reconcile_discord_chat", {job["id"] for job in scheduler.scheduler_status()["jobs"]})

    def test_scheduler_lock_contention_skips_startup_and_started_log(self):
        app = Flask(__name__)
        scheduler_factory = Mock(side_effect=FakeScheduler)
        with patch.dict(os.environ, {"SCHEDULER_ENABLED": "1"}, clear=False), \
                patch.object(scheduler, "_acquire_scheduler_lock_with_retry", return_value=False), \
                patch.object(scheduler, "BackgroundScheduler", scheduler_factory), \
                patch("services.discord_audit.emit_server_log_event", return_value=True) as emit_event:
            scheduler.init_scheduler(app)

        self.assertIsNone(scheduler._scheduler)
        scheduler_factory.assert_not_called()
        emit_event.assert_not_called()
        self.assertFalse(scheduler.scheduler_status()["scheduler_lock_acquired"])

    def test_werkzeug_reloader_parent_skips_scheduler_without_lock(self):
        app = Flask(__name__)
        app.debug = True
        scheduler_factory = Mock(side_effect=FakeScheduler)
        with patch.dict(os.environ, {"SCHEDULER_ENABLED": "1"}, clear=False), \
                patch.object(scheduler, "_acquire_scheduler_lock_with_retry") as acquire_lock, \
                patch.object(scheduler, "BackgroundScheduler", scheduler_factory), \
                patch("services.discord_audit.emit_server_log_event", return_value=True) as emit_event:
            scheduler.init_scheduler(app)

        acquire_lock.assert_not_called()
        scheduler_factory.assert_not_called()
        emit_event.assert_not_called()
        self.assertIsNone(scheduler._scheduler)

    def test_werkzeug_reloader_child_starts_scheduler(self):
        app = Flask(__name__)
        app.debug = True
        with tempfile.TemporaryDirectory() as temp_dir, \
                patch.dict(os.environ, {
                    "SCHEDULER_ENABLED": "1",
                    "WERKZEUG_RUN_MAIN": "true",
                    "SCHEDULER_LOCK_PATH": os.path.join(temp_dir, "scheduler.lock"),
                }, clear=False), \
                patch.object(scheduler, "BackgroundScheduler", FakeScheduler), \
                patch.object(scheduler, "get_course_tracking_refresh_minutes", return_value=30), \
                patch("services.discord_gateway.start_discord_gateway", return_value=True), \
                patch("services.discord_audit.emit_server_log_event", return_value=True):
            scheduler.init_scheduler(app)

        self.assertTrue(scheduler.scheduler_status()["scheduler_running"])

    def test_scheduler_double_init_starts_once(self):
        app = Flask(__name__)
        scheduler_factory = Mock(side_effect=FakeScheduler)
        with tempfile.TemporaryDirectory() as temp_dir, \
                patch.dict(os.environ, {
                    "SCHEDULER_ENABLED": "1",
                    "SCHEDULER_LOCK_PATH": os.path.join(temp_dir, "scheduler.lock"),
                }, clear=False), \
                patch.object(scheduler, "BackgroundScheduler", scheduler_factory), \
                patch("services.discord_audit.emit_server_log_event", return_value=True) as emit_event:
            scheduler.init_scheduler(app)
            scheduler.init_scheduler(app)

        scheduler_factory.assert_called_once()
        emit_event.assert_called_once()
        self.assertEqual(emit_event.call_args.args[0], "Scheduler Started")

    def test_shutdown_releases_scheduler_lock(self):
        app = Flask(__name__)
        with tempfile.TemporaryDirectory() as temp_dir, \
                patch.dict(os.environ, {
                    "SCHEDULER_ENABLED": "1",
                    "SCHEDULER_LOCK_PATH": os.path.join(temp_dir, "scheduler.lock"),
                }, clear=False), \
                patch.object(scheduler, "BackgroundScheduler", FakeScheduler), \
                patch("services.discord_audit.emit_server_log_event", return_value=True):
            scheduler.init_scheduler(app)
            self.assertTrue(scheduler.scheduler_status()["scheduler_lock_acquired"])
            scheduler.shutdown_scheduler()

        status = scheduler.scheduler_status()
        self.assertFalse(status["scheduler_running"])
        self.assertFalse(status["scheduler_lock_acquired"])

    def test_course_tracking_wrapper_emits_server_log_on_exception(self):
        app = Flask(__name__)
        with patch("services.course_tracking.check_course_seat_tracks", side_effect=RuntimeError("boom")), \
                patch("services.discord_audit.emit_server_log_event", return_value=True) as emit_event:
            scheduler._check_course_seat_tracks(app)

        emit_event.assert_called_once()
        self.assertEqual(emit_event.call_args.args[0], "Course Seat Tracking Scheduler Failed")
        self.assertEqual(emit_event.call_args.kwargs["metadata"]["job_id"], "check_course_seat_tracks")

    def test_daily_quote_wrapper_stores_quote_successfully(self):
        app = Flask(__name__)
        with patch.object(scheduler, "_current_daily_quote_date", return_value="2026-06-03"), \
                patch("services.daily_quote.fetch_and_store_daily_quote", return_value={"$id": "quote-1"}) as fetch_quote:
            row = scheduler._fetch_daily_quote(app, quote_date="2026-06-03")

        self.assertEqual(row, {"$id": "quote-1"})
        fetch_quote.assert_called_once_with("2026-06-03")

    def test_daily_quote_wrapper_retries_twice_then_gives_up(self):
        app = Flask(__name__)
        fake_scheduler = FakeScheduler()
        fake_scheduler.running = True

        with patch.object(scheduler, "_scheduler", fake_scheduler), \
                patch.object(scheduler, "_current_daily_quote_date", return_value="2026-06-03"), \
                patch("services.daily_quote.fetch_and_store_daily_quote", side_effect=RuntimeError("boom")), \
                patch.object(scheduler, "_emit_scheduler_event", return_value=True) as emit_event:
            scheduler._fetch_daily_quote(app, quote_date="2026-06-03", attempt=0)
            self.assertEqual(len(fake_scheduler.jobs), 1)
            self.assertEqual(fake_scheduler.jobs[-1].id, "fetch_daily_quote_retry_2026-06-03_1")

            fake_scheduler.jobs[-1].kwargs["func"]()
            self.assertEqual(len(fake_scheduler.jobs), 2)
            self.assertEqual(fake_scheduler.jobs[-1].id, "fetch_daily_quote_retry_2026-06-03_2")

            fake_scheduler.jobs[-1].kwargs["func"]()

        self.assertEqual(len(fake_scheduler.jobs), 2)
        self.assertEqual(emit_event.call_count, 3)
        self.assertTrue(emit_event.call_args.kwargs["metadata"]["gave_up"])
        self.assertFalse(emit_event.call_args.kwargs["metadata"]["retry_scheduled"])

    def test_daily_quote_wrapper_skips_stale_retry_date(self):
        app = Flask(__name__)
        with patch.object(scheduler, "_current_daily_quote_date", return_value="2026-06-04"), \
                patch("services.daily_quote.fetch_and_store_daily_quote") as fetch_quote:
            result = scheduler._fetch_daily_quote(app, quote_date="2026-06-03", attempt=1)

        self.assertIsNone(result)
        fetch_quote.assert_not_called()

    def test_daily_quote_failure_diagnostic_does_not_expose_exception_secret(self):
        app = Flask(__name__)
        with patch.object(scheduler, "_current_daily_quote_date", return_value="2026-06-03"), \
                patch("services.daily_quote.fetch_and_store_daily_quote", side_effect=RuntimeError("token=quote-secret")), \
                patch.object(scheduler, "_schedule_daily_quote_retry", return_value=False), \
                patch.object(scheduler, "_emit_scheduler_event", return_value=True) as emit_event, \
                self.assertLogs(scheduler.logger, level="ERROR") as captured:
            scheduler._fetch_daily_quote(app, quote_date="2026-06-03")

        self.assertNotIn("quote-secret", "\n".join(captured.output))
        self.assertEqual(emit_event.call_args.kwargs["metadata"]["error"], "RuntimeError")

    def test_update_course_tracking_refresh_interval_reschedules_job(self):
        fake_scheduler = FakeScheduler()
        fake_scheduler.running = True
        fake_scheduler.add_job(
            id="check_course_seat_tracks",
            name="Check tracked Emory course seats every 5 min",
            trigger=None,
        )

        with patch.object(scheduler, "_scheduler", fake_scheduler):
            updated = scheduler.update_course_tracking_refresh_interval(5)

        self.assertTrue(updated)
        job = fake_scheduler.get_job("check_course_seat_tracks")
        self.assertEqual(job.name, "Check tracked Emory course seats every 5 min")
        self.assertIsNotNone(job.kwargs["trigger"])

    def test_feed_refresh_does_not_write_user_settings_heartbeat(self):
        app = Flask(__name__)
        settings = {
            "$id": "settings-1",
            "user_id": "user-1",
            "canvas_ical_url": "https://example.com/feed.ics",
            "feed_refresh_minutes": "5",
        }
        with patch.object(scheduler, "list_rows_all", return_value=[settings]), \
                patch.object(scheduler, "first_calendar_row", return_value=None), \
                patch.object(scheduler, "is_stale", return_value=True), \
                patch("services.feed_fetcher.fetch_and_cache_feeds", return_value=0) as fetch_feeds, \
                patch("services.scheduler.update_row_safe", create=True) as update_row:
            scheduler._refresh_all_feeds(app)

        fetch_feeds.assert_called_once_with("user-1", ["https://example.com/feed.ics"])
        update_row.assert_not_called()

    def test_feed_refresh_isolates_metadata_failure_to_one_user(self):
        app = Flask(__name__)
        settings = [
            {"user_id": "user-1", "canvas_ical_url": "https://one.example/feed.ics"},
            {"user_id": "user-2", "canvas_ical_url": "https://two.example/feed.ics"},
        ]

        with patch.object(scheduler, "list_rows_all", return_value=settings), \
                patch.object(
                    scheduler,
                    "first_calendar_row",
                    side_effect=[RuntimeError("metadata unavailable"), None, None],
                ), \
                patch.object(scheduler, "is_stale", return_value=True), \
                patch("services.feed_fetcher.fetch_and_cache_feeds", return_value=0) as fetch_feeds:
            scheduler._refresh_all_feeds(app)

        fetch_feeds.assert_called_once_with("user-2", ["https://two.example/feed.ics"])

    def test_role_sync_does_not_repeat_grant_after_ambiguous_failure(self):
        app = Flask(__name__)
        linked_user = {"discord_id": "discord-1"}

        with patch.object(scheduler, "list_rows_all", return_value=[linked_user]), \
                patch("services.discord_bridge._bot_token", return_value="token"), \
                patch(
                    "services.discord_bridge.member_has_role",
                    side_effect=[False, True],
                ) as has_role, \
                patch(
                    "services.discord_bridge.add_guild_member_role",
                    return_value=False,
                ) as add_role:
            scheduler._sync_discord_roles(app)
            scheduler._sync_discord_roles(app)

        self.assertEqual(has_role.call_count, 2)
        add_role.assert_called_once_with("discord-1")

    def test_scheduler_init_failure_releases_lock_without_crashing_app(self):
        app = Flask(__name__)
        with tempfile.TemporaryDirectory() as temp_dir, \
                patch.dict(os.environ, {
                    "SCHEDULER_ENABLED": "1",
                    "FEED_REFRESH_INTERVAL_MINUTES": "not-an-integer",
                    "SCHEDULER_LOCK_PATH": os.path.join(temp_dir, "scheduler.lock"),
                }, clear=False), \
                patch.object(scheduler, "BackgroundScheduler", FakeScheduler), \
                patch.object(scheduler, "_emit_scheduler_event", return_value=True) as emit_event:
            scheduler.init_scheduler(app)

        self.assertIsNone(scheduler._scheduler)
        self.assertFalse(scheduler.scheduler_status()["scheduler_lock_acquired"])
        self.assertEqual(emit_event.call_args.args[0], "Scheduler Startup Failed")


if __name__ == "__main__":
    unittest.main()
