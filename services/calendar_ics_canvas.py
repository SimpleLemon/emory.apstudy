"""Strict Canvas projection for the single-calendar ICS feed.

This module is deliberately independent from the browser calendar response
path.  It uses the same cache/source tables and Canvas validation helpers, but
never converts a source/read error into an empty or partial result.
"""

from datetime import date, datetime, time, timedelta, timezone
from html.parser import HTMLParser
import re
import sqlite3
from typing import Any, Iterable, Mapping

from services.calendar_ics_contract import (
    CalendarIcsProjectionOutcome,
    NormalizedCalendarEvent,
)
from services.calendar_events import (
    CANVAS_PROJECTION_SCOPES,
    _canvas_consent_from_connection,
    _canvas_source_internal_payload,
    _canvas_truthy,
    extension_capability_enabled,
    parse_datetime,
)
from services.calendar_store import calendar_connection


class CanvasProjectionError(ValueError):
    """A source or event error that must fail the whole Canvas projection."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


_REQUIRED_CANVAS_ICS_CAPABILITIES = (
    "calendar_read",
    "calendar_projection",
    "calendar_shares_ics",
)


def _require_canvas_ics_capabilities() -> None:
    """Require the same read/projection/share gates as the Canvas calendar path."""
    if any(not extension_capability_enabled(capability) for capability in _REQUIRED_CANVAS_ICS_CAPABILITIES):
        raise _source_failure("capability_unavailable", "Canvas calendar capability is unavailable.")


class _DescriptionText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag.lower() in {"script", "style", "iframe", "object", "embed"}:
            self._ignored_depth += 1

    def handle_endtag(self, tag):
        if tag.lower() in {"script", "style", "iframe", "object", "embed"} and self._ignored_depth:
            self._ignored_depth -= 1

    def handle_data(self, data):
        if not self._ignored_depth:
            self.parts.append(data)


def _sanitize_description(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise CanvasProjectionError("malformed_event", "Canvas description must be text.")
    parser = _DescriptionText()
    try:
        parser.feed(value)
        parser.close()
    except ValueError as exc:
        raise CanvasProjectionError("malformed_event", "Canvas description is malformed.") from exc
    text = " ".join("".join(parser.parts).split())
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    return text or None


def _utc_datetime(value: Any, field: str) -> datetime:
    parsed = value if type(value) is datetime else parse_datetime(value)
    if type(parsed) is not datetime or parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise CanvasProjectionError("malformed_event", f"Canvas {field} must be timezone-aware UTC.")
    return parsed.astimezone(timezone.utc)


def _window_datetime(value: Any, field: str) -> datetime:
    if type(value) is date:
        return datetime.combine(value, time.min, tzinfo=timezone.utc)
    return _utc_datetime(value, field)


def _validate_window(window_start: Any, window_end: Any) -> tuple[datetime, datetime]:
    start = _window_datetime(window_start, "window start")
    end = _window_datetime(window_end, "window end")
    if end <= start:
        raise ValueError("Canvas projection window must be a non-empty [start, end) interval.")
    return start, end


def _source_failure(code: str, message: str) -> CanvasProjectionError:
    return CanvasProjectionError(code, message)


def _strict_sources(user_id: str) -> list[dict[str, Any]]:
    with calendar_connection() as connection:
        rows = connection.execute(
            """SELECT * FROM calendar_import_sources
               WHERE user_id = ? AND provider = 'canvas' AND archived_at IS NULL
               ORDER BY created_at ASC""",
            [str(user_id)],
        ).fetchall()
        sources = [dict(row) for row in rows]
        if not sources:
            return []
        _require_canvas_ics_capabilities()
        validated = []
        for source in sources:
            if source.get("status") != "active":
                raise _source_failure("source_unavailable", "A configured Canvas source is not active.")
            try:
                _canvas_consent_from_connection(
                    connection,
                    str(user_id),
                    source.get("account_key"),
                    CANVAS_PROJECTION_SCOPES,
                )
            except Exception as exc:
                raise _source_failure("source_authentication", "Canvas source authorization is unavailable.") from exc
            internal = _canvas_source_internal_payload(source)
            internal["consent_state"] = "active"
            internal["consented"] = True
            validated.append(internal)
        return validated


def _load_cache_rows(user_id: str, source_rows: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    source_ids = [str(row.get("source_id")) for row in source_rows if row.get("source_id")]
    if not source_ids:
        return []
    placeholders = ", ".join("?" for _ in source_ids)
    with calendar_connection() as connection:
        rows = connection.execute(
            f"""SELECT * FROM calendar_cache
                WHERE user_id = ? AND canvas_source_id IN ({placeholders})""",
            [str(user_id), *source_ids],
        ).fetchall()
    return [dict(row) for row in rows]


def _source_key(row: Mapping[str, Any]) -> tuple[str | None, str | None]:
    if "canvas_source_id" in row:
        return row.get("canvas_source_id"), row.get("canvas_account_key")
    return row.get("source_id"), row.get("account_key")


def _validate_injected_sources(source_rows: list[dict[str, Any]]) -> None:
    if not source_rows:
        return
    _require_canvas_ics_capabilities()
    for source in source_rows:
        if source.get("provider", "canvas") != "canvas":
            raise _source_failure("source_invalid", "A configured Canvas source has the wrong provider.")
        if source.get("status", "active") != "active":
            raise _source_failure("source_unavailable", "A configured Canvas source is not active.")
        if not source.get("source_id") or not source.get("account_key"):
            raise _source_failure("source_invalid", "A configured Canvas source is malformed.")
        if source.get("consent_state", "active") != "active" or source.get("consented", True) is False:
            raise _source_failure("source_authentication", "Active Canvas consent is required.")


def _event_dates(row: Mapping[str, Any]) -> tuple[datetime, datetime, bool]:
    is_all_day = _canvas_truthy(row.get("is_all_day"))
    start = _utc_datetime(row.get("event_start"), "event start")
    end = _utc_datetime(row.get("event_end"), "event end")
    if is_all_day:
        start_date = start.date()
        end_date = end.date()
        if end_date <= start_date:
            raise CanvasProjectionError("malformed_event", "Canvas all-day end must be exclusive and after start.")
        return (
            datetime.combine(start_date, time.min, tzinfo=timezone.utc),
            datetime.combine(end_date, time.min, tzinfo=timezone.utc),
            True,
        )
    if end <= start:
        raise CanvasProjectionError("malformed_event", "Canvas timed end must be after start.")
    return start, end, False


def _last_modified(row: Mapping[str, Any]) -> datetime | None:
    value = row.get("canvas_last_modified")
    if value is None:
        value = row.get("last_modified")
    if value is None:
        value = row.get("source_last_modified")
    if value is None:
        return None
    return _utc_datetime(value, "last modified")


def _normalized_event(row: Mapping[str, Any], source: Mapping[str, Any]) -> NormalizedCalendarEvent:
    start, end, is_all_day = _event_dates(row)
    raw_identity = row.get("canvas_event_ref") or row.get("event_uid")
    if not isinstance(raw_identity, str) or not raw_identity.strip():
        raise CanvasProjectionError("malformed_event", "Canvas event identity is missing.")
    title = row.get("event_title")
    if not isinstance(title, str) or not title.strip():
        raise CanvasProjectionError("malformed_event", "Canvas event title is missing.")
    return NormalizedCalendarEvent.from_internal(
        raw_identity=raw_identity,
        calendar_id="canvas",
        source_type="canvas",
        title=title.strip(),
        start=start.date() if is_all_day else start,
        end=end.date() if is_all_day else end,
        is_all_day=is_all_day,
        description=_sanitize_description(row.get("raw_description")),
        course_name=row.get("course_name") or None,
        event_type=row.get("event_type") or row.get("canvas_item_type") or None,
        last_modified=_last_modified(row),
    )


def project_canvas_calendar(
    user_id: str,
    window_start: Any,
    window_end: Any,
    *,
    cache_events: Iterable[Mapping[str, Any]] | None = None,
    source_rows: Iterable[Mapping[str, Any]] | None = None,
) -> CalendarIcsProjectionOutcome:
    """Project all Canvas events intersecting the caller's fixed UTC window."""

    start, end = _validate_window(window_start, window_end)
    try:
        sources = [dict(row) for row in source_rows] if source_rows is not None else _strict_sources(str(user_id))
        if source_rows is not None:
            _validate_injected_sources(sources)
        if not sources:
            return CalendarIcsProjectionOutcome.valid_empty()
        source_by_key = {_source_key(source): source for source in sources}
        rows = [dict(row) for row in cache_events] if cache_events is not None else _load_cache_rows(str(user_id), sources)
        events = []
        for row in rows:
            source = source_by_key.get(_source_key(row))
            if source is None or _canvas_truthy(row.get("canvas_soft_deleted")):
                continue
            event = _normalized_event(row, source)
            event_start = (
                datetime.combine(event.start, time.min, tzinfo=timezone.utc)
                if type(event.start) is date else event.start
            )
            event_end = (
                datetime.combine(event.end, time.min, tzinfo=timezone.utc)
                if type(event.end) is date else event.end
            )
            if event_start < end and event_end > start:
                events.append(event)
        if not events:
            return CalendarIcsProjectionOutcome.valid_empty()
        return CalendarIcsProjectionOutcome.success(tuple(events))
    except CanvasProjectionError as exc:
        return CalendarIcsProjectionOutcome.source_failure(exc.code, str(exc))
    except (OSError, sqlite3.Error) as exc:
        return CalendarIcsProjectionOutcome.resource_failure("calendar_read", "Canvas calendar storage could not be read.")
    except Exception as exc:
        return CalendarIcsProjectionOutcome.source_failure("source_invalid", "Canvas calendar data is invalid.")
