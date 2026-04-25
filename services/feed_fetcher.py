"""
services/feed_fetcher.py
Fetches and parses a user's Canvas iCal feed, then caches
the parsed events in the database.
Canvas iCal feeds are unauthenticated (the URL contains an opaque token)
and return standard RFC 5545 iCalendar data.
Functional now against any valid .ics feed URL.
"""
import logging
import requests as http_requests
from datetime import datetime, date as date_type, timezone
import icalendar
from extensions import db
from models import CalendarCache

logger = logging.getLogger(__name__)


# ── Event type classification ────────────────────────────────────────────────
def _classify_event(summary, description):
    """
    Attempt to classify a calendar event by type based on
    keywords in the summary and description fields.
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
    The bracketed portion, if present, contains the course identifier.
    """
    if not summary:
        return None
    if "[" in summary and "]" in summary:
        start = summary.rfind("[")
        end = summary.rfind("]")
        if start < end:
            return summary[start + 1:end].strip()
    return None


def _stringify_ical(value):
    """Return a trimmed string value for an iCal property."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _first_category_value(categories_prop):
    """Extract the first category value from iCal CATEGORIES."""
    if not categories_prop:
        return None
    cats = getattr(categories_prop, "cats", None)
    if cats:
        for item in cats:
            text = _stringify_ical(item)
            if text:
                return text
        return None
    if isinstance(categories_prop, (list, tuple)):
        for item in categories_prop:
            text = _stringify_ical(item)
            if text:
                return text
        return None
    raw = _stringify_ical(categories_prop)
    if not raw:
        return None
    if "," in raw:
        first = raw.split(",", 1)[0].strip()
        return first or None
    return raw


def _organizer_cn_value(organizer_prop):
    """Extract ORGANIZER CN parameter when available."""
    if not organizer_prop:
        return None
    params = getattr(organizer_prop, "params", None)
    if not params:
        return None
    cn = params.get("CN")
    return _stringify_ical(cn)


def _resolve_course_label(component, calendar_name, is_canvas_feed):
    """Resolve event course/source label using provider metadata priority."""
    if is_canvas_feed:
        return "Canvas"

    event_calname = _stringify_ical(component.get("X-WR-CALNAME"))
    if event_calname:
        return event_calname
    if calendar_name:
        return calendar_name
    category = _first_category_value(component.get("CATEGORIES"))
    if category:
        return category
    organizer_cn = _organizer_cn_value(component.get("ORGANIZER"))
    if organizer_cn:
        return organizer_cn
    return "Other"


def _to_datetime(dt_value):
    """
    Convert an icalendar date/datetime value to a Python datetime.
    Returns a tuple of (datetime, is_all_day).

    The icalendar library returns either datetime.date or datetime.datetime
    objects depending on whether the event is all-day or timed. This function
    normalizes both to datetime for consistent database storage, and signals
    whether the original value was a DATE (all-day) type.
    """
    if dt_value is None:
        return None, False

    # icalendar wraps values in vDate/vDatetime; extract the underlying dt
    raw = dt_value.dt if hasattr(dt_value, "dt") else dt_value

    # DATE type (all-day event): raw is datetime.date, NOT datetime.datetime
    # isinstance(datetime.datetime, datetime.date) is True, so check datetime first
    if isinstance(raw, datetime):
        # Timed event
        if raw.tzinfo is not None:
            return raw.astimezone(timezone.utc).replace(tzinfo=None), False
        return raw, False

    if isinstance(raw, date_type):
        # All-day event: store as midnight UTC, flag as all-day
        return datetime(raw.year, raw.month, raw.day), True

    return None, False


# ── Core fetch and parse ─────────────────────────────────────────────────────
def _normalize_feed_url(feed_url):
    """Normalize feed URLs to a fetchable HTTPS URL."""
    if feed_url is None:
        return ""
    normalized = str(feed_url).strip()
    lower = normalized.lower()
    if lower.startswith("webcal://"):
        return "https://" + normalized[len("webcal://"):]
    if lower.startswith("http://"):
        return "https://" + normalized[len("http://"):]
    return normalized


def fetch_and_parse_ical(feed_url, timeout=20):
    """
    Fetch an iCal feed from a URL and parse it into a list of event dicts.

    Args:
        feed_url: The full calendar iCal feed URL.
        timeout: HTTP request timeout in seconds.

    Returns:
        List of dicts, each containing:
            uid, title, start, end, event_type, course_name,
            description, fetched_at, is_all_day

    Raises:
        requests.RequestException on HTTP errors.
        ValueError if the response is not valid iCalendar data.
    """
    normalized_url = _normalize_feed_url(feed_url)
    if not normalized_url:
        raise ValueError("Feed URL is empty after normalization.")

    headers = {
        "User-Agent": "APStudy-Calendar-Fetcher/1.0",
    }

    logger.info("Fetching calendar feed: url=%s", normalized_url)

    try:
        response = http_requests.get(
            normalized_url,
            headers=headers,
            timeout=timeout,
            allow_redirects=True,
        )
    except http_requests.RequestException as exc:
        logger.error(
            "Calendar feed fetch failed: url=%s error=%s",
            normalized_url,
            str(exc),
            exc_info=True,
        )
        raise

    raw_bytes = response.content
    raw_text = response.text

    if response.status_code != 200:
        logger.error(
            "Calendar feed returned non-200 status: url=%s status_code=%s",
            normalized_url,
            response.status_code,
        )
        raise ValueError(
            f"Feed fetch failed for {normalized_url}: HTTP {response.status_code}"
        )

    if not raw_bytes:
        logger.error("Calendar feed response body is empty: url=%s", normalized_url)
        raise ValueError(f"Feed fetch failed for {normalized_url}: empty response body")

    if "BEGIN:VCALENDAR" not in raw_text.upper():
        content_type = response.headers.get("Content-Type", "")
        logger.error(
            "Calendar feed response is not iCalendar data: url=%s status_code=%s content_type=%s preview=%s",
            normalized_url,
            response.status_code,
            content_type,
            raw_text[:200],
        )
        raise ValueError(
            f"Feed fetch failed for {normalized_url}: response is not iCalendar data"
        )

    try:
        cal = icalendar.Calendar.from_ical(raw_bytes)
    except Exception as exc:
        logger.error(
            "Calendar feed parse failed: url=%s status_code=%s error=%s",
            normalized_url,
            response.status_code,
            str(exc),
            exc_info=True,
        )
        raise ValueError(f"Feed parse failed for {normalized_url}: {exc}") from exc

    calendar_name = _stringify_ical(cal.get("X-WR-CALNAME"))
    prodid = _stringify_ical(cal.get("PRODID")) or ""
    is_canvas_feed = "canvas" in prodid.lower()

    now = datetime.utcnow()
    events = []

    for component in cal.walk():
        if component.name != "VEVENT":
            continue

        summary = str(component.get("SUMMARY", "")) if component.get("SUMMARY") else ""
        description = str(component.get("DESCRIPTION", "")) if component.get("DESCRIPTION") else ""
        uid = str(component.get("UID", "")) if component.get("UID") else None

        dtstart_raw = component.get("DTSTART")
        dtend_raw = component.get("DTEND")

        dtstart, start_is_all_day = _to_datetime(dtstart_raw)
        dtend, end_is_all_day = _to_datetime(dtend_raw)

        # An event is all-day if DTSTART was a DATE type
        is_all_day = start_is_all_day

        events.append({
            "uid": uid,
            "title": summary,
            "start": dtstart,
            "end": dtend,
            "event_type": _classify_event(summary, description),
            "course_name": _resolve_course_label(
                component,
                calendar_name=calendar_name,
                is_canvas_feed=is_canvas_feed,
            ),
            "description": description,
            "fetched_at": now,
            "is_all_day": is_all_day,
        })

    logger.info(
        "Calendar feed parsed successfully: url=%s events_parsed=%s",
        normalized_url,
        len(events),
    )
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
        try:
            aggregated_events.extend(fetch_and_parse_ical(feed_url))
        except Exception as exc:
            normalized_url = _normalize_feed_url(feed_url)
            logger.error(
                "Failed to fetch or parse calendar feed: url=%s error=%s",
                normalized_url,
                str(exc),
                exc_info=True,
            )
            raise

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
            is_all_day=event["is_all_day"],
        )
        db.session.add(cache_entry)

    db.session.commit()
    return len(deduped_events)


def fetch_and_cache_feed(user_id, feed_url):
    """Backward-compatible wrapper for single-feed callers."""
    return fetch_and_cache_feeds(user_id, [feed_url])