"""
services/feed_fetcher.py
Fetches and parses a user's Canvas iCal feed, then caches
the parsed events in the database.
Canvas iCal feeds are unauthenticated (the URL contains an opaque token)
and return standard RFC 5545 iCalendar data.
Functional now against any valid .ics feed URL.
"""
import hashlib
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, date as date_type, timezone
from urllib.parse import urljoin

import icalendar
import requests as http_requests
from appwrite.exception import AppwriteException
from appwrite.id import ID
from appwrite.query import Query
from appwrite_client import COLLECTIONS
from appwrite_helpers import (
    format_datetime,
)
from services.calendar_store import (
    create_calendar_row,
    delete_calendar_row,
    first_calendar_row,
    list_calendar_rows_all,
    update_calendar_row,
)
from services.calendar_urls import normalize_calendar_url
from services.feed_diff import diff_events
from services.outbound_http import redacted_url, require_public_http_url

logger = logging.getLogger(__name__)
MAX_ICAL_BYTES = 10 * 1024 * 1024
MAX_ICAL_REDIRECTS = 5


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
    return normalize_calendar_url(feed_url) or ""


def _feed_url_hash(feed_url):
    return hashlib.sha256(_normalize_feed_url(feed_url).encode("utf-8")).hexdigest()


def _request_public_feed(url, *, headers, timeout):
    current_url = url
    for _ in range(MAX_ICAL_REDIRECTS + 1):
        require_public_http_url(current_url)
        response = http_requests.get(
            current_url,
            headers=headers,
            timeout=timeout,
            allow_redirects=False,
            stream=True,
        )
        if 300 <= response.status_code < 400 and response.headers.get("Location"):
            next_url = urljoin(current_url, response.headers["Location"])
            response.close()
            current_url = next_url
            continue
        return response, current_url
    raise ValueError("Calendar feed redirected too many times.")


def _read_response_bytes(response):
    content_length = response.headers.get("Content-Length")
    if content_length:
        try:
            declared_size = int(content_length)
        except (TypeError, ValueError):
            declared_size = None
        if declared_size is not None and declared_size > MAX_ICAL_BYTES:
            raise ValueError("Calendar feed exceeds the 10 MB response limit.")

    if not isinstance(response, http_requests.Response):
        data = response.content
        if len(data) > MAX_ICAL_BYTES:
            raise ValueError("Calendar feed exceeds the 10 MB response limit.")
        return data

    chunks = []
    total = 0
    for chunk in response.iter_content(chunk_size=64 * 1024):
        if not chunk:
            continue
        total += len(chunk)
        if total > MAX_ICAL_BYTES:
            raise ValueError("Calendar feed exceeds the 10 MB response limit.")
        chunks.append(chunk)
    return b"".join(chunks)


def fetch_and_parse_ical(feed_url, timeout=20, etag=None, last_modified=None):
    """
    Fetch an iCal feed from a URL and parse it into a list of event dicts.

    Args:
        feed_url: The full calendar iCal feed URL.
        timeout: HTTP request timeout in seconds.

    Returns:
        Dict containing:
            status_code, events, etag, last_modified, feed_url

    Raises:
        requests.RequestException on HTTP errors.
        ValueError if the response is not valid iCalendar data.
    """
    normalized_url = _normalize_feed_url(feed_url)
    if not normalized_url:
        raise ValueError("Feed URL is empty after normalization.")

    headers = {"User-Agent": "APStudy-Calendar-Fetcher/1.0"}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified

    safe_log_url = redacted_url(normalized_url)
    logger.info("Fetching calendar feed: url=%s", safe_log_url)

    try:
        response, final_url = _request_public_feed(normalized_url, headers=headers, timeout=timeout)
    except http_requests.RequestException as exc:
        logger.error(
            "Calendar feed fetch failed: url=%s error=%s",
            safe_log_url,
            type(exc).__name__,
        )
        raise ValueError("Calendar feed request failed.") from None
    except ValueError as exc:
        logger.warning(
            "Calendar feed rejected: url=%s reason=%s",
            safe_log_url,
            str(exc),
        )
        raise

    if response.status_code == 304:
        logger.info(
            "Calendar feed not modified: url=%s",
            redacted_url(final_url),
        )
        result = {
            "status_code": 304,
            "events": [],
            "etag": response.headers.get("ETag") or etag,
            "last_modified": response.headers.get("Last-Modified") or last_modified,
            "feed_url": normalized_url,
            "calendar_name": None,
        }
        response.close()
        return result

    if response.status_code != 200:
        logger.error(
            "Calendar feed returned non-200 status: url=%s status_code=%s",
            redacted_url(final_url),
            response.status_code,
        )
        response.close()
        raise ValueError(
            f"Feed fetch failed: HTTP {response.status_code}"
        )

    try:
        raw_bytes = _read_response_bytes(response)
    finally:
        response.close()
    encoding = response.encoding if isinstance(getattr(response, "encoding", None), str) else "utf-8"
    raw_text = raw_bytes.decode(encoding or "utf-8", errors="replace")

    if not raw_bytes:
        logger.error("Calendar feed response body is empty: url=%s", redacted_url(final_url))
        raise ValueError("Feed fetch failed: empty response body")

    if "BEGIN:VCALENDAR" not in raw_text.upper():
        content_type = response.headers.get("Content-Type", "")
        logger.error(
            "Calendar feed response is not iCalendar data: url=%s status_code=%s content_type=%s",
            redacted_url(final_url),
            response.status_code,
            content_type,
        )
        raise ValueError("Feed fetch failed: response is not iCalendar data")

    try:
        cal = icalendar.Calendar.from_ical(raw_bytes)
    except Exception as exc:
        logger.error(
            "Calendar feed parse failed: url=%s status_code=%s error_type=%s",
            redacted_url(final_url),
            response.status_code,
            type(exc).__name__,
        )
        raise ValueError("Feed parse failed: invalid iCalendar data") from None

    calendar_name = _stringify_ical(cal.get("X-WR-CALNAME")) or _stringify_ical(cal.get("NAME"))
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
        redacted_url(final_url),
        len(events),
    )
    return {
        "status_code": 200,
        "events": events,
        "etag": response.headers.get("ETag"),
        "last_modified": response.headers.get("Last-Modified"),
        "feed_url": normalized_url,
        "calendar_name": calendar_name,
    }


# ── Database caching ─────────────────────────────────────────────────────────
def _load_feed_metadata(user_id):
    feed_table = COLLECTIONS.get("calendar_feeds")
    if not feed_table:
        return {}
    try:
        rows = list_calendar_rows_all(
            feed_table,
            [Query.equal("user_id", [str(user_id)])],
        )
    except AppwriteException:
        logger.exception("Failed to load feed metadata")
        return {}
    return {row.get("feed_url_hash"): row for row in rows if row.get("feed_url_hash")}


def _upsert_feed_metadata(user_id, feed_url, result, fetched_at):
    feed_table = COLLECTIONS.get("calendar_feeds")
    if not feed_table:
        return
    feed_hash = _feed_url_hash(feed_url)
    existing = first_calendar_row(
        feed_table,
        [
            Query.equal("user_id", [str(user_id)]),
            Query.equal("feed_url_hash", [feed_hash]),
        ],
    )
    if not existing:
        existing = first_calendar_row(
            feed_table,
            [
                Query.equal("user_id", [str(user_id)]),
                Query.equal("feed_url", [feed_url]),
            ],
        )
    etag_value = result.get("etag")
    last_modified_value = result.get("last_modified")
    if existing:
        if etag_value is None:
            etag_value = existing.get("etag_header")
        if last_modified_value is None:
            last_modified_value = existing.get("last_modified_header")
    calendar_name = result.get("calendar_name")
    if not calendar_name and existing:
        calendar_name = existing.get("calendar_name")

    payload = {
        "user_id": str(user_id),
        "feed_url": feed_url,
        "feed_url_hash": feed_hash,
        "calendar_name": calendar_name,
        "etag_header": etag_value,
        "last_modified_header": last_modified_value,
        "last_fetch_http_code": result.get("status_code"),
        "last_fetched": format_datetime(fetched_at),
        "updated_at": format_datetime(fetched_at),
    }
    def write_payload(data):
        if existing:
            return update_calendar_row(feed_table, existing.get("$id"), data)
        return create_calendar_row(
            feed_table,
            row_id=ID.unique(),
            data={
                **data,
                "created_at": format_datetime(fetched_at),
            },
        )

    try:
        write_payload(payload)
    except AppwriteException as exc:
        if "calendar_name" not in str(exc):
            raise
        logger.info(
            "calendar_feeds.calendar_name is not available yet; retrying metadata write without it."
        )
        fallback_payload = dict(payload)
        fallback_payload.pop("calendar_name", None)
        write_payload(fallback_payload)


def _apply_feed_diffs(user_id, feed_url, events, fetched_at, existing_rows=None):
    if existing_rows is None:
        feed_hash = _feed_url_hash(feed_url)
        existing_rows = list_calendar_rows_all(
            COLLECTIONS["calendar_cache"],
            [
                Query.equal("user_id", [str(user_id)]),
                Query.equal("feed_url_hash", [feed_hash]),
            ],
        )

    # Diffing uses feed_url + event_uid for stable upserts/deletes.
    diff = diff_events(existing_rows, events, user_id, feed_url, fetched_at)

    for payload in diff.to_create:
        create_calendar_row(
            COLLECTIONS["calendar_cache"],
            row_id=ID.unique(),
            data=payload,
        )

    for row_id, payload in diff.to_update:
        if not row_id:
            continue
        update_calendar_row(
            COLLECTIONS["calendar_cache"],
            row_id,
            payload,
        )

    for row in diff.to_delete:
        row_id = row.get("$id") or row.get("id")
        if row_id:
            delete_calendar_row(COLLECTIONS["calendar_cache"], row_id)

    return len(diff.to_create) + len(diff.to_update)


def fetch_and_cache_feeds(user_id, feed_urls):
    """
    Fetch user calendar feeds and cache events using upsert/diffing.
    """
    if not feed_urls:
        raise ValueError("At least one feed URL is required.")

    normalized_urls = []
    seen = set()
    for feed_url in feed_urls:
        normalized = _normalize_feed_url(feed_url)
        if not normalized or normalized in seen:
            continue
        normalized_urls.append(normalized)
        seen.add(normalized)

    try:
        existing_rows = list_calendar_rows_all(
            COLLECTIONS["calendar_cache"],
            [Query.equal("user_id", [str(user_id)])],
        )
    except AppwriteException:
        logger.exception("Failed to load cached events")
        raise

    rows_to_update = []
    orphaned_rows = []
    for row in existing_rows:
        if not row.get("feed_url"):
            orphaned_rows.append(row)
            continue
        if not row.get("feed_url_hash"):
            rows_to_update.append(row)
    if orphaned_rows:
        # Legacy rows without feed_url cannot be associated to a feed.
        for row in orphaned_rows:
            row_id = row.get("$id") or row.get("id")
            if row_id:
                delete_calendar_row(COLLECTIONS["calendar_cache"], row_id)
    for row in rows_to_update:
        row_id = row.get("$id") or row.get("id")
        if not row_id:
            continue
        update_calendar_row(
            COLLECTIONS["calendar_cache"],
            row_id,
            {"feed_url_hash": _feed_url_hash(row.get("feed_url"))},
        )
    existing_rows = [row for row in existing_rows if row.get("feed_url")]

    existing_by_feed = {}
    for row in existing_rows:
        existing_by_feed.setdefault(_normalize_feed_url(row.get("feed_url")), []).append(row)

    feed_meta = _load_feed_metadata(user_id)
    results = []
    errors = []

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {}
        for feed_url in normalized_urls:
            feed_hash = _feed_url_hash(feed_url)
            meta = feed_meta.get(feed_hash) or {}
            has_cached_events = bool(existing_by_feed.get(feed_url))
            futures[executor.submit(
                fetch_and_parse_ical,
                feed_url,
                etag=meta.get("etag_header") if has_cached_events else None,
                last_modified=meta.get("last_modified_header") if has_cached_events else None,
            )] = feed_url
        for future in as_completed(futures):
            try:
                results.append(future.result())
            except Exception as exc:
                normalized_url = futures.get(future)
                logger.error(
                    "Failed to fetch or parse calendar feed: url=%s error_type=%s",
                    redacted_url(normalized_url),
                    type(exc).__name__,
                )
                errors.append(exc)

    if errors:
        raise errors[0]

    total_changes = 0
    fetched_at = datetime.utcnow()
    for result in results:
        feed_url = result.get("feed_url")
        _upsert_feed_metadata(user_id, feed_url, result, fetched_at)
        if result.get("status_code") == 304:
            # No changes; skip parsing and cache writes.
            continue
        total_changes += _apply_feed_diffs(
            user_id,
            feed_url,
            result.get("events", []),
            fetched_at,
            existing_rows=existing_by_feed.get(feed_url, []),
        )

    return total_changes


def fetch_and_cache_feed(user_id, feed_url):
    """Backward-compatible wrapper for single-feed callers."""
    return fetch_and_cache_feeds(user_id, [feed_url])
