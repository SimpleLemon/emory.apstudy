"""Shared calendar feed URL normalization and validation."""

import json
import re
from urllib.parse import parse_qs, urlparse, urlunparse

MAX_OTHER_CALENDAR_URLS = 10
_INVALID_FEED_URL_PART = re.compile(r"[\s;<>]|\.\.")
_FEED_PATH_EXTENSIONS = (".ics", ".ical", ".ifb")
_FEED_PATH_MARKERS = (
    "/calendar/ical/",
    "/feeds/calendar",
    "/feeds/calendars",
    "/published/",
)
_ICAL_QUERY_KEYS = {"ical", "ics", "format"}
_ICAL_QUERY_VALUES = {"ical", "ics", "text/calendar"}

GOOGLE_CALENDAR_UI_MESSAGE = (
    "That's the Google Calendar website, not a feed. In Google Calendar go to "
    "Settings, pick the calendar, and copy the 'Secret address in iCal format'."
)
OUTLOOK_CALENDAR_UI_MESSAGE = (
    "That's the Outlook Calendar website, not a feed. Open calendar settings and "
    "copy the ICS publish or secret address instead."
)
ICLOUD_CALENDAR_UI_MESSAGE = (
    "That's the iCloud Calendar website, not a feed. Use a public calendar "
    "share link or the published iCal address ending in a calendar feed path."
)


def _feed_url_parts_are_safe(path, query):
    for part in (path, query):
        if part and _INVALID_FEED_URL_PART.search(part):
            return False
    return True


def normalize_calendar_url(url):
    """Return a normalized calendar feed URL, or None if invalid."""
    if not isinstance(url, str):
        return None

    raw = url.strip()
    if not raw:
        return None

    parsed = urlparse(raw)
    scheme = parsed.scheme.lower()
    if scheme == "webcal":
        scheme = "https"

    if scheme not in {"http", "https"}:
        return None

    if not parsed.netloc:
        return None

    normalized_path = (parsed.path or "").rstrip("/")
    if not _feed_url_parts_are_safe(normalized_path, parsed.query):
        return None

    return urlunparse((
        scheme,
        parsed.netloc.lower(),
        normalized_path,
        "",
        parsed.query,
        "",
    ))


def _path_looks_like_feed(path):
    lowered = (path or "").lower()
    if any(lowered.endswith(ext) for ext in _FEED_PATH_EXTENSIONS):
        return True
    return any(marker in lowered for marker in _FEED_PATH_MARKERS)


def _query_requests_ical(query):
    if not query:
        return False
    params = parse_qs(query, keep_blank_values=False)
    for key, values in params.items():
        key_lower = key.lower()
        if key_lower in _ICAL_QUERY_KEYS:
            if not values:
                return True
            if any(str(value).lower() in _ICAL_QUERY_VALUES for value in values):
                return True
        for value in values:
            if str(value).lower() in _ICAL_QUERY_VALUES:
                return True
    return False


def classify_calendar_url(url):
    """
    Classify a calendar URL shape before probing.

    Returns:
        ("reject", message) for known web-UI URLs
        ("accept", None) for recognized feed shapes
        ("unknown", None) when the URL should be probed
    """
    normalized = normalize_calendar_url(url)
    if not normalized:
        return (
            "reject",
            "Each optional calendar link must be a valid http(s) or webcal URL.",
        )

    parsed = urlparse(normalized)
    host = (parsed.netloc or "").lower()
    path = parsed.path or ""
    path_lower = path.lower()

    if host == "calendar.google.com" or host.endswith(".calendar.google.com"):
        if "/calendar/ical/" in path_lower or path_lower.endswith(_FEED_PATH_EXTENSIONS):
            return "accept", None
        return "reject", GOOGLE_CALENDAR_UI_MESSAGE

    if host in {"outlook.live.com", "outlook.office.com", "outlook.office365.com"} or host.endswith(".office365.com"):
        if _path_looks_like_feed(path) or _query_requests_ical(parsed.query):
            return "accept", None
        if "/calendar" in path_lower:
            return "reject", OUTLOOK_CALENDAR_UI_MESSAGE

    if host in {"www.icloud.com", "icloud.com"} and "/calendar" in path_lower:
        if not _path_looks_like_feed(path):
            return "reject", ICLOUD_CALENDAR_UI_MESSAGE

    if _path_looks_like_feed(path) or _query_requests_ical(parsed.query):
        return "accept", None

    return "unknown", None


def iter_valid_other_calendar_urls(settings, *, max_urls=MAX_OTHER_CALENDAR_URLS):
    """Yield (raw, normalized) pairs for each valid optional calendar URL."""
    if not settings or not settings.get("other_ical_urls_json"):
        return

    try:
        parsed = json.loads(settings.get("other_ical_urls_json"))
    except json.JSONDecodeError:
        return

    if not isinstance(parsed, list):
        return

    count = 0
    for item in parsed:
        if count >= max_urls:
            break
        if not isinstance(item, str):
            continue
        raw = item.strip()
        if not raw:
            continue
        normalized = normalize_calendar_url(raw)
        if not normalized:
            continue
        count += 1
        yield raw, normalized


def load_other_calendar_urls(settings, *, max_urls=MAX_OTHER_CALENDAR_URLS):
    """Load and sanitize persisted optional calendar URLs from JSON text."""
    return [normalized for _, normalized in iter_valid_other_calendar_urls(settings, max_urls=max_urls)]
