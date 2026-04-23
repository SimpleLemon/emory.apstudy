"""
services/scheduler.py

Background task scheduler using APScheduler.
Handles periodic Canvas iCal feed refresh for all users.

Must be initialized within a Flask application context because
the scheduled jobs access the database via Flask-SQLAlchemy.

Warning: When running under Gunicorn with multiple workers, each worker
spawns its own scheduler. Use the SCHEDULER_ENABLED environment variable
or Gunicorn's --preload flag with a worker check to ensure only one
instance runs scheduled jobs [8].
"""

import os
import json
import logging
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)

# Module-level scheduler instance. Initialized once via init_scheduler().
_scheduler = None


def _configured_feed_urls(settings):
    """Return all configured calendar URLs from user settings."""
    urls = []
    if settings.canvas_ical_url:
        urls.append(settings.canvas_ical_url.strip())

    if settings.other_ical_urls_json:
        try:
            extras = json.loads(settings.other_ical_urls_json)
            if isinstance(extras, list):
                for item in extras:
                    if isinstance(item, str) and item.strip():
                        urls.append(item.strip())
        except json.JSONDecodeError:
            pass

    return urls


def _refresh_all_feeds(app):
    """
    Iterate through all users with configured calendar feed URLs
    and refresh their cached calendar events.

    Runs inside a Flask application context so that database access works.
    """
    with app.app_context():
        from models import User, UserSettings
        from services.feed_fetcher import fetch_and_cache_feeds

        all_settings = UserSettings.query.all()
        settings_with_feeds = [
            settings for settings in all_settings if _configured_feed_urls(settings)
        ]

        if not settings_with_feeds:
            logger.info("Feed refresh: no users with configured feeds.")
            return

        logger.info(
            f"Feed refresh: processing {len(settings_with_feeds)} user(s)."
        )

        for settings in settings_with_feeds:
            try:
                count = fetch_and_cache_feeds(
                    settings.user_id,
                    _configured_feed_urls(settings),
                )
                settings.updated_at = datetime.utcnow()
                logger.info(
                    f"  User {settings.user_id}: {count} events cached."
                )
            except Exception as e:
                logger.error(
                    f"  User {settings.user_id}: feed refresh failed: {e}"
                )

        # Commit all updated_at timestamps
        from extensions import db
        db.session.commit()


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
        return

    if _scheduler is not None:
        logger.warning("Scheduler already initialized. Skipping.")
        return

    default_interval = int(
        os.environ.get("FEED_REFRESH_INTERVAL_MINUTES", "15")
    )

    _scheduler = BackgroundScheduler(daemon=True)

    _scheduler.add_job(
        func=lambda: _refresh_all_feeds(app),
        trigger=IntervalTrigger(minutes=default_interval),
        id="refresh_all_feeds",
        name=f"Refresh Canvas feeds every {default_interval} min",
        replace_existing=True,
        max_instances=1,  # Prevent overlapping runs if a refresh takes longer than the interval
    )

    _scheduler.start()
    logger.info(
        f"Scheduler started. Feed refresh interval: {default_interval} min."
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