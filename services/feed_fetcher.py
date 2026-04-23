"""
services/feed_fetcher.py

Fetches and parses a user's Canvas iCal feed, then caches
the parsed events in the database.

Canvas iCal feeds are unauthenticated (the URL contains an opaque token)
and return standard RFC 5545 iCalendar data [6] [7].

Phase 5 (blocked until fall enrollment) [1] [3].
Functional now against any valid .ics feed URL.
"""

import requests as http_requests
from datetime import datetime, timezone

import icalendar

from extensions import db
from models import CalendarCache


# ── Event type classification ────────────────────────────────────────────────

def _classify_event(summary, description):
    """
    Attempt to classify a Canvas calendar event by type based on
    keywords in the summary and description fields.

    Canvas iCal feeds embed event type information inconsistently.
    Assignment due dates typically include "due" or the assignment name.
    Quiz events may include "quiz" or "exam". Calendar events from
    the course calendar are more generic.

    Returns one of: "assignment", "quiz", "event", "unknown"
    """
    text = f"{summary} {description}".lower()

    if "quiz" in text or "exam" in text or "test" in text:
        return "quiz"
    if "due" in text or "assignment" in text or "homework" in text or "hw" in text:
        return "assignment"
    if "office hour" in text or "review session" in text:
        return "event"

    return "unknown"


def _extract_course_name(summary):
    """
    Attempt to extract the course name from a Canvas event summary.

    Canvas iCal event summaries typically follow patterns like:
        "Assignment Name [CHEM 150-001]"
        "Quiz 3 [BIOL 141]"
        "Course Event Name"

    The bracketed portion, if present, contains the course identifier.
    """
    if not summary:
        return None

    # Look for bracketed course identifier
    if "[" in summary and "]" in summary:
        start = summary.rfind("[")
        end = summary.rfind("]")
        if start < end:
            return summary[start + 1:end].strip()

    return None


def _to_datetime(dt_value):
    """
    Convert an icalendar date/datetime value to a Python datetime.

    The icalendar library returns either datetime.date or datetime.datetime
    objects depending on whether the event is all-day or timed. This function
    normalizes both to datetime for consistent database storage.
    """
    if dt_value is None:
        return None

    # icalendar wraps values in vDate/vDatetime; extract the underlying dt
    if hasattr(dt_value, "dt"):
        dt_value = dt_value.dt

    # If it's a date (not datetime), treat it as midnight UTC
    if isinstance(dt_value, datetime):
        if dt_value.tzinfo is not None:
            return dt_value.replace(tzinfo=None)  # Store as naive UTC
        return dt_value

    # date object (all-day event)
    return datetime(dt_value.year, dt_value.month, dt_value.day)


# ── Core fetch and parse ─────────────────────────────────────────────────────

def fetch_and_parse_ical(feed_url, timeout=30):
    """
    Fetch an iCal feed from a URL and parse it into a list of event dicts.

    Args:
        feed_url: The full Canvas iCal feed URL.
        timeout: HTTP request timeout in seconds.

    Returns:
        List of dicts, each containing:
            uid, title, start, end, event_type, course_name,
            description, fetched_at

    Raises:
        requests.RequestException on HTTP errors.
        ValueError if the response is not valid iCalendar data.
    """
    response = http_requests.get(feed_url, timeout=timeout)
    response.raise_for_status()

    content_type = response.headers.get("Content-Type", "")
    raw_text = response.text

    # Validate that the response looks like iCalendar data
    if "BEGIN:VCALENDAR" not in raw_text[:500]:
        raise ValueError(
            f"Response does not appear to be iCalendar data. "
            f"Content-Type: {content_type}, "
            f"First 200 chars: {raw_text[:200]}"
        )

    cal = icalendar.Calendar.from_ical(raw_text)
    now = datetime.utcnow()
    events = []

    for component in cal.walk():
        if component.name != "VEVENT":
            continue

        summary = str(component.get("SUMMARY", "")) if component.get("SUMMARY") else ""
        description = str(component.get("DESCRIPTION", "")) if component.get("DESCRIPTION") else ""
        uid = str(component.get("UID", "")) if component.get("UID") else None

        dtstart = _to_datetime(component.get("DTSTART"))
        dtend = _to_datetime(component.get("DTEND"))

        events.append({
            "uid": uid,
            "title": summary,
            "start": dtstart,
            "end": dtend,
            "event_type": _classify_event(summary, description),
            "course_name": _extract_course_name(summary),
            "description": description,
            "fetched_at": now,
        })

    return events


# ── Database caching ─────────────────────────────────────────────────────────

def fetch_and_cache_feeds(user_id, feed_urls):
    """
    Fetch one or more user calendar iCal feeds, parse them, and replace
    cached events in the database.

    Uses a delete-then-insert strategy rather than upsert, because
    Canvas may remove events (e.g., instructor deletes an assignment)
    and we want the cache to reflect that removal.

    Args:
        user_id: Integer user ID from the users table.
        feed_urls: Iterable of calendar feed URLs.

    Returns:
        Integer count of events cached.

    Raises:
        requests.RequestException on HTTP errors.
        ValueError on invalid iCal data.
    """
    if not feed_urls:
        raise ValueError("At least one feed URL is required.")

    aggregated_events = []
    for feed_url in feed_urls:
        aggregated_events.extend(fetch_and_parse_ical(feed_url))

    deduped_events = []
    seen_uids = set()
    seen_fallbacks = set()

    for event in aggregated_events:
        uid = event.get("uid")
        if uid:
            if uid in seen_uids:
                continue
            seen_uids.add(uid)
        else:
            fallback_key = (
                event.get("title"),
                event.get("start"),
                event.get("end"),
            )
            if fallback_key in seen_fallbacks:
                continue
            seen_fallbacks.add(fallback_key)

        deduped_events.append(event)

    # Delete all existing cached events for this user
    CalendarCache.query.filter_by(user_id=user_id).delete()

    # Insert fresh events
    for event in deduped_events:
        cache_entry = CalendarCache(
            user_id=user_id,
            event_uid=event["uid"],
            event_title=event["title"],
            event_start=event["start"],
            event_end=event["end"],
            event_type=event["event_type"],
            course_name=event["course_name"],
            raw_description=event["description"],
            fetched_at=event["fetched_at"],
        )
        db.session.add(cache_entry)

    db.session.commit()
    return len(deduped_events)


def fetch_and_cache_feed(user_id, feed_url):
    """Backward-compatible wrapper for single-feed callers."""
    return fetch_and_cache_feeds(user_id, [feed_url])