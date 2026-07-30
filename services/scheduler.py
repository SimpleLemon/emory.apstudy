"""
services/scheduler.py

Background task scheduler using APScheduler.
Handles periodic Canvas iCal feed refresh for all users.

Must be initialized within a Flask application context because
the scheduled jobs access the database via Appwrite.

Warning: When running under Gunicorn with multiple workers, each worker
spawns its own scheduler. Use the SCHEDULER_ENABLED environment variable
or Gunicorn's --preload flag with a worker check to ensure only one
instance runs scheduled jobs [8].
"""

import atexit
import fcntl
import os
import json
import logging
import socket
import time
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

from appwrite.exception import AppwriteException
from appwrite.query import Query
from appwrite_client import COLLECTIONS
from appwrite_helpers import (
    format_datetime,
    list_rows_all,
    parse_datetime,
)
from services.calendar_store import first_calendar_row
from services.calendar_urls import load_other_calendar_urls
from services.app_config import (
    COURSE_TRACKING_REFRESH_INTERVAL_CHOICES,
    get_course_tracking_refresh_minutes,
)
from services.staleness import is_stale

logger = logging.getLogger(__name__)
DAILY_QUOTE_RETRY_LIMIT = 2

# Module-level scheduler instance. Initialized once via init_scheduler().
_scheduler = None
_scheduler_lock_file = None
_scheduler_lock_acquired = False
_scheduler_lock_path = None


def _configured_scheduler_lock_path():
    return os.environ.get("SCHEDULER_LOCK_PATH") or "/tmp/nest_apstudy_scheduler.lock"


def _should_own_scheduler(app):
    """
    Decide whether this process should try to own the scheduler.

    Werkzeug's debug reloader runs create_app() in a parent that only watches
    files, then forks a child (WERKZEUG_RUN_MAIN=true) that serves traffic.
    The parent must not grab the scheduler lock or the child cannot start jobs.
    """
    werkzeug_run_main = os.environ.get("WERKZEUG_RUN_MAIN")
    if werkzeug_run_main == "true":
        return True
    if app.debug and werkzeug_run_main is None:
        return False
    return True


def _read_lock_holder_description(lock_path):
    try:
        with open(lock_path, encoding="utf-8") as lock_file:
            return lock_file.read().strip() or "unknown"
    except OSError:
        return "unknown"


def _release_scheduler_lock():
    global _scheduler_lock_file, _scheduler_lock_acquired, _scheduler_lock_path

    if _scheduler_lock_file is None:
        _scheduler_lock_acquired = False
        _scheduler_lock_path = None
        return
    try:
        fcntl.flock(_scheduler_lock_file.fileno(), fcntl.LOCK_UN)
    except OSError:
        logger.exception("Failed to release scheduler lock")
    try:
        _scheduler_lock_file.close()
    except OSError:
        logger.exception("Failed to close scheduler lock file")
    _scheduler_lock_file = None
    _scheduler_lock_acquired = False
    _scheduler_lock_path = None


def _acquire_scheduler_lock():
    global _scheduler_lock_file, _scheduler_lock_acquired, _scheduler_lock_path

    if _scheduler_lock_acquired:
        return True

    lock_path = _configured_scheduler_lock_path()
    os.makedirs(os.path.dirname(lock_path) or ".", exist_ok=True)
    lock_file = open(lock_path, "a+", encoding="utf-8")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock_file.close()
        _scheduler_lock_file = None
        _scheduler_lock_acquired = False
        _scheduler_lock_path = lock_path
        return False
    except OSError:
        lock_file.close()
        _scheduler_lock_file = None
        _scheduler_lock_acquired = False
        _scheduler_lock_path = lock_path
        logger.exception("Failed to acquire scheduler lock: %s", lock_path)
        return False

    lock_file.seek(0)
    lock_file.truncate()
    lock_file.write(f"pid={os.getpid()} host={socket.gethostname()}\n")
    lock_file.flush()
    _scheduler_lock_file = lock_file
    _scheduler_lock_acquired = True
    _scheduler_lock_path = lock_path
    return True


def _acquire_scheduler_lock_with_retry(max_attempts=5, delay_seconds=0.2):
    for attempt in range(max_attempts):
        if _acquire_scheduler_lock():
            return True
        if attempt < max_attempts - 1:
            time.sleep(delay_seconds)
    return False


def _emit_scheduler_event(title, metadata=None, color="gray"):
    try:
        from services.discord_audit import emit_server_log_event

        return emit_server_log_event(
            title,
            actor="System",
            target="Background scheduler",
            metadata=metadata or {},
            color=color,
        )
    except Exception:
        logger.exception("Failed to emit scheduler diagnostic event")
        return False


def scheduler_status():
    jobs = []
    if _scheduler is not None:
        try:
            jobs = [
                {
                    "id": getattr(job, "id", ""),
                    "name": getattr(job, "name", ""),
                    "next_run_time": (
                        format_datetime(getattr(job, "next_run_time", None))
                        if getattr(job, "next_run_time", None)
                        else None
                    ),
                }
                for job in _scheduler.get_jobs()
            ]
        except Exception:
            logger.exception("Failed to read scheduler jobs")
    return {
        "scheduler_enabled": os.environ.get("SCHEDULER_ENABLED") == "1",
        "scheduler_initialized": _scheduler is not None,
        "scheduler_running": bool(_scheduler and _scheduler.running),
        "scheduler_lock_acquired": bool(_scheduler_lock_acquired),
        "scheduler_lock_path": _scheduler_lock_path or _configured_scheduler_lock_path(),
        "scheduler_process_id": os.getpid(),
        "scheduler_hostname": socket.gethostname(),
        "jobs": jobs,
    }


def _configured_feed_urls(settings):
    """Return all configured calendar URLs from user settings."""
    urls = []
    canvas_url = settings.get("canvas_ical_url")
    if canvas_url:
        urls.append(canvas_url.strip())
    urls.extend(load_other_calendar_urls(settings))
    return urls


def _all_configured_feeds_quarantined(user_id, feed_urls):
    """Return True when every configured URL has a quarantined calendar_feeds row."""
    if not feed_urls:
        return False
    from services.feed_fetcher import _feed_url_hash, _load_feed_metadata

    feed_meta = _load_feed_metadata(user_id)
    for feed_url in feed_urls:
        meta = feed_meta.get(_feed_url_hash(feed_url)) or {}
        if not meta.get("disabled_at"):
            return False
    return True


def _refresh_all_feeds(app):
    """
    Iterate through all users with configured calendar feed URLs
    and refresh their cached calendar events.

    Runs inside a Flask application context so that database access works.
    """
    with app.app_context():
        from services.feed_fetcher import fetch_and_cache_feeds
        try:
            all_settings = list_rows_all(COLLECTIONS["user_settings"])
        except AppwriteException:
            logger.exception("Failed to list user settings")
            return

        settings_with_feeds = [
            settings for settings in all_settings if _configured_feed_urls(settings)
        ]

        if not settings_with_feeds:
            logger.info("Feed refresh: no users with configured feeds.")
            return

        logger.info(
            f"Feed refresh: processing {len(settings_with_feeds)} user(s)."
        )

        now = datetime.now(timezone.utc)
        for settings in settings_with_feeds:
            user_id = settings.get("user_id")
            feed_urls = _configured_feed_urls(settings)
            try:
                if _all_configured_feeds_quarantined(user_id, feed_urls):
                    logger.info(
                        "  User %s: skipping refresh; all configured feeds are quarantined.",
                        user_id,
                    )
                    continue

                refresh_minutes = settings.get("feed_refresh_minutes")
                try:
                    refresh_minutes = int(refresh_minutes) if refresh_minutes is not None else None
                except (TypeError, ValueError):
                    refresh_minutes = None

                if refresh_minutes is None:
                    refresh_minutes = int(os.environ.get("FEED_REFRESH_INTERVAL_MINUTES", "15"))

                last_fetched = None
                feed_table = COLLECTIONS.get("calendar_feeds")
                if feed_table:
                    latest_feed = first_calendar_row(
                        feed_table,
                        [
                            Query.equal("user_id", [user_id]),
                            Query.order_desc("last_fetched"),
                        ],
                    )
                    if latest_feed and latest_feed.get("last_fetched"):
                        last_fetched = parse_datetime(latest_feed.get("last_fetched"))

                if not last_fetched:
                    latest_event = first_calendar_row(
                        COLLECTIONS["calendar_cache"],
                        [
                            Query.equal("user_id", [user_id]),
                            Query.order_desc("fetched_at"),
                        ],
                    )
                    if latest_event and latest_event.get("fetched_at"):
                        last_fetched = parse_datetime(latest_event.get("fetched_at"))

                # Refresh only when the user's interval has elapsed.
                if not is_stale(last_fetched, refresh_minutes, now=now):
                    continue
                count = fetch_and_cache_feeds(user_id, feed_urls)
                logger.info(
                    "  User %s: %s events cached.",
                    user_id,
                    count,
                )
            except Exception as exc:
                if _all_configured_feeds_quarantined(user_id, feed_urls):
                    logger.warning(
                        "  User %s: feed refresh skipped after quarantine (%s)",
                        user_id,
                        type(exc).__name__,
                    )
                    continue
                logger.error(
                    "  User %s: feed refresh failed (%s)",
                    user_id,
                    type(exc).__name__,
                )


def _check_course_seat_tracks(app):
    """Run course seat tracking checks inside the Flask app context."""
    with app.app_context():
        try:
            from services.course_tracking import check_course_seat_tracks

            notified_count = check_course_seat_tracks()
            logger.info("Course seat tracking: %s notification(s) sent.", notified_count)
        except Exception:
            logger.error("Course seat tracking failed")
            _emit_scheduler_event(
                "Course Seat Tracking Scheduler Failed",
                metadata={"job_id": "check_course_seat_tracks"},
                color="red",
            )


def _check_notification_reminders(app):
    with app.app_context():
        try:
            from services.notifications import check_calendar_reminders
            check_calendar_reminders()
        except Exception:
            logger.exception("Calendar notification reminder check failed")


def _flush_notification_foreground(app):
    with app.app_context():
        try:
            from services.notifications import flush_foreground_queue

            flushed = flush_foreground_queue()
            if flushed:
                logger.info("Released %s foreground notification fallback(s).", flushed)
        except Exception:
            logger.exception("Foreground notification fallback check failed")


def _schedule_daily_quote_retry(app, quote_date, next_attempt):
    if _scheduler is None or not _scheduler.running:
        logger.warning("Daily quote retry skipped because scheduler is not running.")
        return False
    if quote_date != _current_daily_quote_date():
        logger.warning("Daily quote retry skipped for stale quote date: %s", quote_date)
        return False

    run_at = datetime.now(timezone.utc) + timedelta(hours=1)
    job_id = f"fetch_daily_quote_retry_{quote_date}_{next_attempt}"
    _scheduler.add_job(
        func=lambda: _fetch_daily_quote(app, quote_date=quote_date, attempt=next_attempt),
        trigger=DateTrigger(run_date=run_at),
        id=job_id,
        name=f"Retry daily quote fetch for {quote_date} attempt {next_attempt}",
        replace_existing=True,
        max_instances=1,
    )
    logger.info("Daily quote retry %s scheduled for %s at %s.", next_attempt, quote_date, run_at.isoformat())
    return True


def _current_daily_quote_date():
    from services.daily_quote import utc_quote_date

    return utc_quote_date()


def _sync_discord_roles(app):
    """Ensure every user with a linked Discord account still holds the role.

    Tracks members who joined (or rejoined) the guild after linking: if a linked
    user is in the guild but missing the membership role, it is granted. Runs
    inside the Flask app context for database access.
    """
    with app.app_context():
        try:
            from services import discord_bridge
        except Exception:
            logger.exception("Failed to import discord_bridge for role sync")
            return

        if not discord_bridge._bot_token():
            return

        try:
            linked_users = list_rows_all(
                COLLECTIONS["users"],
                [Query.is_not_null("discord_id")],
            )
        except AppwriteException:
            logger.exception("Failed to list users for Discord role sync")
            return

        granted = 0
        for user in linked_users:
            discord_id = str(user.get("discord_id") or "").strip()
            if not discord_id:
                continue
            try:
                has_role = discord_bridge.member_has_role(discord_id)
                # None => not a guild member (cannot grant); only act when False.
                if has_role is False:
                    if discord_bridge.add_guild_member_role(discord_id):
                        granted += 1
            except Exception:
                logger.exception("Discord role sync failed for %s", discord_id)
        if granted:
            logger.info("Discord role sync: granted role to %s user(s).", granted)


def _fetch_daily_quote(app, quote_date=None, attempt=0):
    """Fetch and persist the global daily quote inside the Flask app context."""
    target_date = quote_date or _current_daily_quote_date()
    if target_date != _current_daily_quote_date():
        logger.warning("Skipping daily quote fetch for stale quote date: %s", target_date)
        return None

    with app.app_context():
        try:
            from services.daily_quote import fetch_and_store_daily_quote

            row = fetch_and_store_daily_quote(target_date)
            logger.info("Daily quote fetched and stored for %s.", target_date)
            return row
        except Exception as exc:
            logger.error(
                "Daily quote fetch failed for %s on attempt %s (%s)",
                target_date,
                attempt,
                type(exc).__name__,
            )
            next_attempt = attempt + 1
            retry_scheduled = False
            if next_attempt <= DAILY_QUOTE_RETRY_LIMIT:
                retry_scheduled = _schedule_daily_quote_retry(app, target_date, next_attempt)
            _emit_scheduler_event(
                "Daily Quote Scheduler Failed",
                metadata={
                    "job_id": "fetch_daily_quote",
                    "quote_date": target_date,
                    "attempt": attempt,
                    "next_attempt": next_attempt if retry_scheduled else None,
                    "retry_scheduled": retry_scheduled,
                    "gave_up": not retry_scheduled,
                    "error": type(exc).__name__,
                },
                color="red",
            )
            return None


def update_course_tracking_refresh_interval(minutes):
    """Reschedule the course seat tracking job if the scheduler is active."""
    global _scheduler

    try:
        minutes = int(minutes)
    except (TypeError, ValueError):
        return False

    if minutes not in COURSE_TRACKING_REFRESH_INTERVAL_CHOICES:
        return False
    if _scheduler is None or not _scheduler.running:
        return False

    try:
        _scheduler.reschedule_job(
            "check_course_seat_tracks",
            trigger=IntervalTrigger(minutes=minutes),
        )
        try:
            _scheduler.modify_job(
                "check_course_seat_tracks",
                name=f"Check tracked Emory course seats every {minutes} min",
            )
        except Exception:
            logger.exception("Failed to update course tracking scheduler job metadata")
        return True
    except Exception:
        logger.exception("Failed to reschedule course tracking job")
        return False


def _reconcile_discord_chat(app):
    """Slowly reconcile Discord-backed chat channels as a Gateway safety net."""
    with app.app_context():
        try:
            from blueprints.chat_api import sync_discord_channels

            created_count, deleted_count = sync_discord_channels(emit_events=False, emit_delete_events=True)
            if created_count or deleted_count:
                logger.info(
                    "Discord chat reconciliation: %s new message(s), %s deleted message(s).",
                    created_count,
                    deleted_count,
                )
        except Exception:
            logger.exception("Discord chat reconciliation failed")


def _sync_discord_chat(app):
    """Legacy fast polling path used only when Discord Gateway is disabled."""
    _reconcile_discord_chat(app)


def _cleanup_note_media(app):
    with app.app_context():
        try:
            from services.note_media import cleanup_abandoned_media
            deleted = cleanup_abandoned_media()
            if deleted:
                logger.info("Deleted %s abandoned note media upload(s).", deleted)
        except Exception:
            logger.exception("Note media cleanup failed")
        try:
            from services.chat_attachments import cleanup_abandoned_attachments
            deleted = cleanup_abandoned_attachments()
            if deleted:
                logger.info("Deleted %s abandoned chat attachment(s).", deleted)
        except Exception:
            logger.exception("Chat attachment cleanup failed")
        try:
            from services.notifications import cleanup_expired
            cleanup_expired()
        except Exception:
            logger.exception("Notification retention cleanup failed")


def init_scheduler(app):
    """
    Initialize and start the background scheduler.

    Call this once from the application factory (app.py) after all
    extensions and blueprints are registered.

    The scheduler only starts if the SCHEDULER_ENABLED environment
    variable is set to "1". This prevents duplicate job execution
    when running multiple Gunicorn workers.

    Args:
        app: The Flask application instance.
    """
    global _scheduler

    if os.environ.get("SCHEDULER_ENABLED") != "1":
        logger.info(
            "Scheduler disabled (SCHEDULER_ENABLED != '1'). "
            "Feed refresh will only run on manual trigger."
        )
        _emit_scheduler_event(
            "Scheduler Disabled",
            metadata={"scheduler_enabled": False, "SCHEDULER_ENABLED": os.environ.get("SCHEDULER_ENABLED", "")},
            color="yellow",
        )
        return

    if _scheduler is not None:
        logger.warning("Scheduler already initialized. Skipping.")
        return

    if not _should_own_scheduler(app):
        logger.debug(
            "Skipping scheduler startup in werkzeug reloader parent process."
        )
        return

    if not _acquire_scheduler_lock_with_retry():
        lock_path = _scheduler_lock_path or _configured_scheduler_lock_path()
        holder = _read_lock_holder_description(lock_path)
        logger.info(
            "Scheduler lock is held by another process (%s). "
            "Skipping scheduler startup in this worker: %s",
            holder,
            lock_path,
        )
        return

    atexit.register(shutdown_scheduler)

    try:
        default_interval = int(
            os.environ.get("FEED_REFRESH_INTERVAL_MINUTES", "15")
        )
        course_tracking_interval = 5
        _scheduler = BackgroundScheduler(daemon=True)

        _scheduler.add_job(
            func=lambda: _refresh_all_feeds(app),
            trigger=IntervalTrigger(minutes=default_interval),
            id="refresh_all_feeds",
            name=f"Refresh Canvas feeds every {default_interval} min",
            replace_existing=True,
            max_instances=1,  # Prevent overlapping runs if a refresh takes longer than the interval
        )

        _scheduler.add_job(
            func=lambda: _check_course_seat_tracks(app),
            trigger=IntervalTrigger(minutes=course_tracking_interval),
            id="check_course_seat_tracks",
            name=f"Check tracked Emory course seats every {course_tracking_interval} min",
            replace_existing=True,
            max_instances=1,
        )

        _scheduler.add_job(
            func=lambda: _check_notification_reminders(app),
            trigger=IntervalTrigger(minutes=1),
            id="check_notification_reminders",
            name="Check calendar and task notification reminders every minute",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )

        _scheduler.add_job(
            func=lambda: _flush_notification_foreground(app),
            trigger=IntervalTrigger(seconds=10),
            id="flush_notification_foreground",
            name="Release unacknowledged foreground notifications",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )

        _scheduler.add_job(
            func=lambda: _fetch_daily_quote(app),
            trigger=CronTrigger(hour=0, minute=15, timezone=timezone.utc),
            id="fetch_daily_quote",
            name="Fetch ZenQuotes daily quote at 00:15 UTC",
            replace_existing=True,
            max_instances=1,
        )

        _scheduler.add_job(
            func=lambda: _cleanup_note_media(app),
            trigger=CronTrigger(hour=3, minute=20, timezone=timezone.utc),
            id="cleanup_note_media",
            name="Clean abandoned note media daily",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )

        discord_role_sync_minutes = int(os.environ.get("DISCORD_ROLE_SYNC_MINUTES", "30"))
        if discord_role_sync_minutes > 0:
            _scheduler.add_job(
                func=lambda: _sync_discord_roles(app),
                trigger=IntervalTrigger(minutes=discord_role_sync_minutes),
                id="sync_discord_roles",
                name=f"Sync linked Discord member roles every {discord_role_sync_minutes} min",
                replace_existing=True,
                max_instances=1,
                coalesce=True,
            )

        discord_gateway_enabled = os.environ.get("DISCORD_GATEWAY_ENABLED", "1") != "0"
        discord_reconcile_seconds = int(os.environ.get("DISCORD_CHAT_RECONCILE_SECONDS", "300"))
        if os.environ.get("DISCORD_CHAT_SYNC_ENABLED", "1") != "0" and discord_gateway_enabled and discord_reconcile_seconds > 0:
            _scheduler.add_job(
                func=lambda: _reconcile_discord_chat(app),
                trigger=IntervalTrigger(seconds=discord_reconcile_seconds),
                id="reconcile_discord_chat",
                name=f"Reconcile Discord chat every {discord_reconcile_seconds} sec",
                replace_existing=True,
                max_instances=1,
                coalesce=True,
            )
        elif os.environ.get("DISCORD_CHAT_SYNC_ENABLED", "1") != "0":
            discord_sync_seconds = int(os.environ.get("DISCORD_CHAT_SYNC_SECONDS", "5"))
            if discord_sync_seconds > 0:
                _scheduler.add_job(
                    func=lambda: _sync_discord_chat(app),
                    trigger=IntervalTrigger(seconds=discord_sync_seconds),
                    id="sync_discord_chat",
                    name=f"Sync Discord chat every {discord_sync_seconds} sec",
                    replace_existing=True,
                    max_instances=1,
                    coalesce=True,
                )

        if discord_gateway_enabled:
            try:
                from services.discord_gateway import start_discord_gateway

                start_discord_gateway(app)
            except Exception:
                logger.exception("Failed to start Discord Gateway listener")

        _scheduler.start()
    except Exception as exc:
        failed_scheduler = _scheduler
        if failed_scheduler is not None and failed_scheduler.running:
            try:
                failed_scheduler.shutdown(wait=False)
            except Exception:
                logger.exception("Failed to stop partially initialized scheduler")
        _scheduler = None
        _release_scheduler_lock()
        logger.error("Scheduler startup failed (%s)", type(exc).__name__)
        _emit_scheduler_event(
            "Scheduler Startup Failed",
            metadata={
                "scheduler_enabled": True,
                "error_type": type(exc).__name__,
            },
            color="red",
        )
        return None
    logger.info(
        f"Scheduler started. Feed refresh interval: {default_interval} min."
    )
    _emit_scheduler_event(
        "Scheduler Started",
        metadata={
            "scheduler_enabled": True,
            "scheduler_lock_acquired": True,
            "scheduler_lock_path": _scheduler_lock_path,
            "scheduler_process_id": os.getpid(),
            "scheduler_hostname": socket.gethostname(),
            "feed_refresh_interval_minutes": default_interval,
            "course_tracking_refresh_interval_minutes": course_tracking_interval,
            "discord_gateway_enabled": discord_gateway_enabled,
            "discord_chat_reconcile_seconds": discord_reconcile_seconds,
            "job_ids": ", ".join(job["id"] for job in scheduler_status()["jobs"]),
        },
        color="green",
    )


def shutdown_scheduler():
    """
    Gracefully shut down the scheduler.
    Call from a Flask teardown handler or signal handler.
    """
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler shut down.")
    _scheduler = None
    try:
        from services.discord_gateway import shutdown_discord_gateway

        shutdown_discord_gateway()
    except Exception:
        logger.exception("Failed to shut down Discord Gateway listener")
    _release_scheduler_lock()
