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
PERMANENT_FAILURE_QUARANTINE_THRESHOLD = 2
TRANSIENT_FAILURE_QUARANTINE_THRESHOLD = 6
MAX_ERROR_MESSAGE_LENGTH = 500


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
    return normalize_calendar_url(feed_url) or ""


def _feed_url_hash(feed_url):
    return hashlib.sha256(_normalize_feed_url(feed_url).encode("utf-8")).hexdigest()


def feed_url_hash(feed_url):
    """Public helper for callers that need the canonical feed URL hash."""
    return _feed_url_hash(feed_url)


def _is_permanent_feed_failure(exc):
    message = str(exc or "").lower()
    permanent_markers = (
        "response is not icalendar data",
        "invalid icalendar data",
        "empty response body",
        "feed url is empty",
        "redirected too many times",
        "exceeds the 10 mb",
        "http 400",
        "http 401",
        "http 403",
        "http 404",
        "http 410",
        "http 451",
    )
    return any(marker in message for marker in permanent_markers)


def _failure_kind(exc):
    return "permanent" if _is_permanent_feed_failure(exc) else "transient"


def _truncate_error_message(message):
    text = " ".join(str(message or "").split())
    if len(text) <= MAX_ERROR_MESSAGE_LENGTH:
        return text
    return text[: MAX_ERROR_MESSAGE_LENGTH - 1] + "…"


def derive_feed_status(feed_row):
    """Return a derived status label for a calendar_feeds row."""
    if not feed_row:
        return "never fetched"
    if feed_row.get("disabled_at"):
        return "quarantined"
    try:
        failures = int(feed_row.get("consecutive_failures") or 0)
    except (TypeError, ValueError):
        failures = 0
    if failures > 0 or feed_row.get("last_error_type"):
        return "failing"
    if feed_row.get("last_fetched"):
        return "ok"
    return "never fetched"


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


def probe_calendar_feed(feed_url, timeout=15):
    """
    Validate that a URL returns iCalendar data before persisting it.

    Returns:
        Dict with feed_url and calendar_name on success.

    Raises:
        ValueError with a user-safe message when the URL is not a calendar feed.
    """
    normalized_url = _normalize_feed_url(feed_url)
    if not normalized_url:
        raise ValueError("Calendar URL is required.")

    headers = {"User-Agent": "APStudy-Calendar-Fetcher/1.0"}
    safe_log_url = redacted_url(normalized_url)
    logger.info("Probing calendar feed: url=%s", safe_log_url)

    try:
        response, final_url = _request_public_feed(normalized_url, headers=headers, timeout=timeout)
    except http_requests.RequestException:
        logger.error("Calendar feed probe request failed: url=%s", safe_log_url)
        raise ValueError(
            "Unable to reach that calendar URL. Check the link and try again."
        ) from None
    except ValueError as exc:
        logger.warning("Calendar feed probe rejected: url=%s reason=%s", safe_log_url, str(exc))
        raise ValueError(str(exc)) from None

    if response.status_code != 200:
        status_code = response.status_code
        response.close()
        raise ValueError(f"That calendar URL returned HTTP {status_code}.")

    try:
        raw_bytes = _read_response_bytes(response)
    finally:
        response.close()

    encoding = response.encoding if isinstance(getattr(response, "encoding", None), str) else "utf-8"
    raw_text = raw_bytes.decode(encoding or "utf-8", errors="replace")
    if not raw_bytes or "BEGIN:VCALENDAR" not in raw_text.upper():
        raise ValueError(
            "That URL does not look like a calendar feed. Paste an iCal (.ics) "
            "or secret/publish calendar address."
        )

    calendar_name = None
    try:
        cal = icalendar.Calendar.from_ical(raw_bytes)
        calendar_name = _stringify_ical(cal.get("X-WR-CALNAME")) or _stringify_ical(cal.get("NAME"))
    except Exception:
        raise ValueError(
            "That URL does not look like a calendar feed. Paste an iCal (.ics) "
            "or secret/publish calendar address."
        ) from None

    return {
        "feed_url": normalized_url,
        "calendar_name": calendar_name,
    }


def ensure_fetchable_calendar_url(url, timeout=15):
    """Classify then probe a calendar URL before it is persisted."""
    from services.calendar_urls import classify_calendar_url

    verdict, message = classify_calendar_url(url)
    if verdict == "reject":
        raise ValueError(message or "That calendar URL is not supported.")
    return probe_calendar_feed(url, timeout=timeout)


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


def _find_feed_row(user_id, feed_url):
    feed_table = COLLECTIONS.get("calendar_feeds")
    if not feed_table:
        return None
    feed_hash = _feed_url_hash(feed_url)
    existing = first_calendar_row(
        feed_table,
        [
            Query.equal("user_id", [str(user_id)]),
            Query.equal("feed_url_hash", [feed_hash]),
        ],
    )
    if existing:
        return existing
    return first_calendar_row(
        feed_table,
        [
            Query.equal("user_id", [str(user_id)]),
            Query.equal("feed_url", [feed_url]),
        ],
    )


def _upsert_feed_metadata(user_id, feed_url, result, fetched_at):
    feed_table = COLLECTIONS.get("calendar_feeds")
    if not feed_table:
        return
    feed_hash = _feed_url_hash(feed_url)
    existing = _find_feed_row(user_id, feed_url)
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
        "consecutive_failures": 0,
        "last_error_type": None,
        "last_error_message": None,
        "last_error_at": None,
        "disabled_at": None,
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


def _record_feed_failure(user_id, feed_url, exc, now=None):
    """Upsert failure metadata and quarantine the feed when thresholds are hit."""
    feed_table = COLLECTIONS.get("calendar_feeds")
    if not feed_table or not feed_url:
        return None

    fetched_at = now or datetime.utcnow()
    existing = _find_feed_row(user_id, feed_url)
    try:
        previous_failures = int((existing or {}).get("consecutive_failures") or 0)
    except (TypeError, ValueError):
        previous_failures = 0
    consecutive_failures = previous_failures + 1
    kind = _failure_kind(exc)
    threshold = (
        PERMANENT_FAILURE_QUARANTINE_THRESHOLD
        if kind == "permanent"
        else TRANSIENT_FAILURE_QUARANTINE_THRESHOLD
    )
    disabled_at = (existing or {}).get("disabled_at")
    if consecutive_failures >= threshold:
        disabled_at = format_datetime(fetched_at)

    payload = {
        "user_id": str(user_id),
        "feed_url": feed_url,
        "feed_url_hash": _feed_url_hash(feed_url),
        "calendar_name": (existing or {}).get("calendar_name"),
        "etag_header": (existing or {}).get("etag_header"),
        "last_modified_header": (existing or {}).get("last_modified_header"),
        "last_fetch_http_code": (existing or {}).get("last_fetch_http_code"),
        "last_fetched": (existing or {}).get("last_fetched"),
        "updated_at": format_datetime(fetched_at),
        "consecutive_failures": consecutive_failures,
        "last_error_type": type(exc).__name__ if exc is not None else "Error",
        "last_error_message": _truncate_error_message(exc),
        "last_error_at": format_datetime(fetched_at),
        "disabled_at": disabled_at,
    }

    if existing:
        update_calendar_row(feed_table, existing.get("$id"), payload)
        return {**existing, **payload}

    created = create_calendar_row(
        feed_table,
        row_id=ID.unique(),
        data={
            **payload,
            "created_at": format_datetime(fetched_at),
        },
    )
    return created


def clear_feed_quarantine(user_id, feed_url):
    """Clear quarantine flags so the next refresh can retry the feed."""
    feed_table = COLLECTIONS.get("calendar_feeds")
    if not feed_table or not feed_url:
        return None
    existing = _find_feed_row(user_id, feed_url)
    if not existing:
        return None
    payload = {
        "consecutive_failures": 0,
        "last_error_type": None,
        "last_error_message": None,
        "last_error_at": None,
        "disabled_at": None,
        "updated_at": format_datetime(datetime.utcnow()),
    }
    return update_calendar_row(feed_table, existing.get("$id"), payload)


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


def fetch_and_cache_feeds(user_id, feed_urls, *, force=False):
    """
    Fetch user calendar feeds and cache events using upsert/diffing.

    Quarantined feeds are skipped unless force=True. When every configured URL
    is quarantined, returns 0 instead of raising.
    """
    if feed_urls is None:
        feed_urls = []

    normalized_urls = []
    seen = set()
    for feed_url in feed_urls:
        normalized = _normalize_feed_url(feed_url)
        if not normalized or normalized in seen:
            continue
        normalized_urls.append(normalized)
        seen.add(normalized)

    feed_meta = _load_feed_metadata(user_id)
    if not force:
        active_urls = []
        for feed_url in normalized_urls:
            meta = feed_meta.get(_feed_url_hash(feed_url)) or {}
            if meta.get("disabled_at"):
                logger.info(
                    "Skipping quarantined calendar feed: user_id=%s url=%s",
                    user_id,
                    redacted_url(feed_url),
                )
                continue
            active_urls.append(feed_url)
        normalized_urls = active_urls

    if not normalized_urls:
        return 0

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

    results = []
    errors = []
    failed_at = datetime.utcnow()

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
            normalized_url = futures.get(future)
            try:
                results.append(future.result())
            except Exception as exc:
                logger.error(
                    "Failed to fetch or parse calendar feed: url=%s error_type=%s",
                    redacted_url(normalized_url),
                    type(exc).__name__,
                )
                try:
                    _record_feed_failure(user_id, normalized_url, exc, now=failed_at)
                except Exception:
                    logger.exception(
                        "Failed to record calendar feed failure: url=%s",
                        redacted_url(normalized_url),
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


def fetch_and_cache_feed(user_id, feed_url, *, force=False):
    """Backward-compatible wrapper for single-feed callers."""
    return fetch_and_cache_feeds(user_id, [feed_url], force=force)
