"""Share-scoped aggregation and RFC 5545 serialization for calendar feeds."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import hashlib
from typing import Any, Iterable

from services.calendar_ics_canvas import project_canvas_calendar
from services.calendar_ics_contract import (
    CalendarIcsProjectionStatus,
    NormalizedCalendarEvent,
    TASKS_CALENDAR_ID,
    SIMULATED_COURSES_CALENDAR_ID,
    build_calendar_ics_uid,
    subscription_window,
)
from services.calendar_ics_courses import project_simulated_courses_for_user
from services.calendar_ics_tasks import project_tasks_for_user
from services.calendar_share_service import require_eligible_selection


MAX_ICS_EVENTS = 10_000
MAX_ICS_BYTES = 10 * 1024 * 1024
ICS_PRODID = "-//Nest APStudy//Calendar ICS//EN"
ICS_CALENDAR_NAME = "Nest APStudy"
UTC = timezone.utc


class CalendarIcsFeedError(ValueError):
    """A feed cannot be produced safely or within the transport limits."""


@dataclass(frozen=True, slots=True)
class CalendarIcsDocument:
    content: bytes
    etag: str
    event_count: int


def utc_subscription_window(now: datetime | None = None) -> tuple[datetime, datetime]:
    current = now or datetime.now(UTC)
    if type(current) is not datetime or current.tzinfo is None:
        raise CalendarIcsFeedError("Calendar feed clock is invalid.")
    current = current.astimezone(UTC)
    start, end = subscription_window(current.date())
    return (
        datetime.combine(start, time.min, tzinfo=UTC),
        datetime.combine(end, time.min, tzinfo=UTC),
    )


def _calendar_selection(share: dict[str, Any]) -> str:
    try:
        selection = require_eligible_selection(share)
    except Exception:
        raise CalendarIcsFeedError("Calendar feed selection is invalid.") from None
    if selection not in {"canvas", TASKS_CALENDAR_ID, SIMULATED_COURSES_CALENDAR_ID}:
        raise CalendarIcsFeedError("Calendar feed selection is invalid.")
    return selection


def project_calendar_share(
    share: dict[str, Any],
    range_start: datetime,
    range_end: datetime,
) -> tuple[str, tuple[NormalizedCalendarEvent, ...]]:
    """Dispatch one and only one source projector for a valid share."""
    selection = _calendar_selection(share)
    try:
        # Validate the immutable UID configuration even for valid-empty feeds.
        build_calendar_ics_uid(selection, b"calendar-ics-configuration-check")
        user_id = str(share.get("user_id") or "")
        if not user_id:
            raise CalendarIcsFeedError("Calendar feed owner is invalid.")
        if selection == "canvas":
            outcome = project_canvas_calendar(user_id, range_start, range_end)
        elif selection == TASKS_CALENDAR_ID:
            outcome = project_tasks_for_user(user_id, range_start, range_end)
        else:
            outcome = project_simulated_courses_for_user(
                user_id,
                range_start,
                range_end,
            )
    except CalendarIcsFeedError:
        raise
    except Exception:
        raise CalendarIcsFeedError("Calendar feed source is unavailable.") from None
    if outcome.status == CalendarIcsProjectionStatus.VALID_EMPTY:
        return selection, ()
    if outcome.status == CalendarIcsProjectionStatus.SUCCESS:
        events = tuple(outcome.events)
        if len(events) > MAX_ICS_EVENTS:
            raise CalendarIcsFeedError("Calendar feed exceeds the event limit.")
        return selection, events
    raise CalendarIcsFeedError("Calendar feed source is unavailable.")


def _ical_text(value: str) -> str:
    """Escape RFC 5545 TEXT without allowing header or line injection."""
    normalized = str(value).replace("\r\n", "\n").replace("\r", "\n")
    if not any(
        character in {"\\", ";", ",", "\n"}
        or (ord(character) < 0x20 and character not in {"\n", "\t"})
        for character in normalized
    ):
        return normalized
    normalized = "".join(
        character for character in normalized
        if character in {"\n", "\t"} or ord(character) >= 0x20
    )
    return (
        normalized
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def _fold_line(line: str) -> list[str]:
    encoded = line.encode("utf-8")
    if len(encoded) <= 75:
        return [line]
    chunks: list[str] = []
    offset = 0
    limit = 75
    while offset < len(encoded):
        end = min(offset + limit, len(encoded))
        while end > offset and end < len(encoded) and (encoded[end] & 0xC0) == 0x80:
            end -= 1
        chunk = encoded[offset:end].decode("utf-8")
        chunks.append(chunk if offset == 0 else f" {chunk}")
        offset = end
        limit = 74
    return chunks


def _format_datetime(value: datetime) -> str:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise CalendarIcsFeedError("Calendar event time is not UTC.")
    return value.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def _format_date(value: date) -> str:
    if type(value) is not date:
        raise CalendarIcsFeedError("Calendar all-day date is invalid.")
    return value.strftime("%Y%m%d")


def _event_lines(event: NormalizedCalendarEvent, generated_at: datetime | None) -> list[str]:
    lines = ["BEGIN:VEVENT", f"UID:{_ical_text(event.uid)}"]
    if generated_at is not None:
        lines.append(f"DTSTAMP:{_format_datetime(generated_at)}")
    if event.is_all_day:
        lines.extend((f"DTSTART;VALUE=DATE:{_format_date(event.start)}", f"DTEND;VALUE=DATE:{_format_date(event.end)}"))
    else:
        lines.extend((f"DTSTART:{_format_datetime(event.start)}", f"DTEND:{_format_datetime(event.end)}"))
    lines.append(f"SUMMARY:{_ical_text(event.title)}")
    for name, value in (
        ("DESCRIPTION", event.description),
        ("LOCATION", event.location),
        ("LAST-MODIFIED", _format_datetime(event.last_modified) if event.last_modified else None),
    ):
        if value is not None:
            lines.append(f"{name}:{_ical_text(value)}")
    if event.course_name:
        lines.append(f"CATEGORIES:{_ical_text(event.course_name)}")
    detail_fields = (
        ("X-APSTUDY-CALENDAR-ID", event.calendar_id),
        ("X-APSTUDY-SOURCE-TYPE", event.source_type),
        ("X-APSTUDY-EVENT-TYPE", event.event_type),
        ("X-APSTUDY-COURSE-NAME", event.course_name),
        ("X-APSTUDY-COURSE-TYPE", event.course_type),
        ("X-APSTUDY-PRIORITY", event.priority),
        ("X-APSTUDY-COMPLETED", "TRUE" if event.completed else "FALSE" if event.completed is not None else None),
        ("X-APSTUDY-COURSE-CODE", event.course_code),
        ("X-APSTUDY-COURSE-TITLE", event.course_title),
        ("X-APSTUDY-SECTION", event.section),
        ("X-APSTUDY-INSTRUCTOR", event.instructor),
        ("X-APSTUDY-COURSE-LOCATION", event.course_location),
        ("X-APSTUDY-NOTES", event.notes),
        ("X-APSTUDY-CRN", event.crn),
    )
    for name, value in detail_fields:
        if value is not None:
            lines.append(f"{name}:{_ical_text(value)}")
    if not event.is_all_day and event.calendar_id == TASKS_CALENDAR_ID and event.reminder_minutes and event.reminder_minutes > 0:
        lines.extend((
            "BEGIN:VALARM",
            "ACTION:DISPLAY",
            f"DESCRIPTION:{_ical_text(event.title)}",
            f"TRIGGER:{_ical_duration(-event.reminder_minutes)}",
            "END:VALARM",
        ))
    lines.append("END:VEVENT")
    return lines


def _ical_duration(minutes: int) -> str:
    minutes = abs(int(minutes))
    days, remainder = divmod(minutes, 1440)
    hours, minutes = divmod(remainder, 60)
    parts = "-P"
    if days:
        parts += f"{days}D"
    if hours or minutes or not days:
        parts += "T"
        if hours:
            parts += f"{hours}H"
        if minutes or not hours:
            parts += f"{minutes}M"
    return parts


def _calendar_lines(events: Iterable[NormalizedCalendarEvent], generated_at: datetime | None) -> list[str]:
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{ICS_PRODID}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_ical_text(ICS_CALENDAR_NAME)}",
    ]
    ordered = sorted(
        events,
        key=lambda event: (event.start.isoformat(), event.end.isoformat(), event.uid),
    )
    for event in ordered:
        lines.extend(_event_lines(event, generated_at))
    lines.append("END:VCALENDAR")
    return lines


def _escaped_text_size(value: str) -> int:
    """Return escaped UTF-8 bytes without materializing a large escaped copy."""
    size = 0
    normalized = str(value).replace("\r\n", "\n").replace("\r", "\n")
    for character in normalized:
        if ord(character) < 0x20 and character not in {"\n", "\t"}:
            continue
        encoded_size = len(character.encode("utf-8"))
        size += encoded_size + (1 if character in {"\\", ";", ",", "\n"} else 0)
    return size


def _folded_line_size(unfolded_size: int) -> int:
    if unfolded_size <= 75:
        return unfolded_size
    return unfolded_size + ((unfolded_size - 75 + 73) // 74)


def _calendar_size_upper_bound(
    events: tuple[NormalizedCalendarEvent, ...],
    generated_at: datetime,
) -> int:
    """Preflight serialized size before constructing potentially huge lines."""
    total = 2

    def add_line(prefix: str, value: str | None = None) -> None:
        nonlocal total
        unfolded = len(prefix.encode("utf-8"))
        if value is not None:
            unfolded += _escaped_text_size(value)
        total += _folded_line_size(unfolded) + 2

    for line in (
        "BEGIN:VCALENDAR", "VERSION:2.0", f"PRODID:{ICS_PRODID}",
        "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
    ):
        add_line(line)
    add_line("X-WR-CALNAME:", ICS_CALENDAR_NAME)
    for event in events:
        add_line("BEGIN:VEVENT")
        add_line("UID:", event.uid)
        add_line("DTSTAMP:", _format_datetime(generated_at))
        if event.is_all_day:
            add_line("DTSTART;VALUE=DATE:", _format_date(event.start))
            add_line("DTEND;VALUE=DATE:", _format_date(event.end))
        else:
            add_line("DTSTART:", _format_datetime(event.start))
            add_line("DTEND:", _format_datetime(event.end))
        add_line("SUMMARY:", event.title)
        for prefix, value in (
            ("DESCRIPTION:", event.description), ("LOCATION:", event.location),
            ("LAST-MODIFIED:", _format_datetime(event.last_modified) if event.last_modified else None),
            ("CATEGORIES:", event.course_name),
            ("X-APSTUDY-CALENDAR-ID:", event.calendar_id),
            ("X-APSTUDY-SOURCE-TYPE:", event.source_type),
            ("X-APSTUDY-EVENT-TYPE:", event.event_type),
            ("X-APSTUDY-COURSE-NAME:", event.course_name),
            ("X-APSTUDY-COURSE-TYPE:", event.course_type),
            ("X-APSTUDY-PRIORITY:", event.priority),
            ("X-APSTUDY-COMPLETED:", "TRUE" if event.completed else "FALSE" if event.completed is not None else None),
            ("X-APSTUDY-COURSE-CODE:", event.course_code),
            ("X-APSTUDY-COURSE-TITLE:", event.course_title),
            ("X-APSTUDY-SECTION:", event.section),
            ("X-APSTUDY-INSTRUCTOR:", event.instructor),
            ("X-APSTUDY-COURSE-LOCATION:", event.course_location),
            ("X-APSTUDY-NOTES:", event.notes),
            ("X-APSTUDY-CRN:", event.crn),
        ):
            if value is not None:
                add_line(prefix, value)
        if not event.is_all_day and event.calendar_id == TASKS_CALENDAR_ID and event.reminder_minutes and event.reminder_minutes > 0:
            add_line("BEGIN:VALARM")
            add_line("ACTION:DISPLAY")
            add_line("DESCRIPTION:", event.title)
            add_line("TRIGGER:", _ical_duration(-event.reminder_minutes))
            add_line("END:VALARM")
        add_line("END:VEVENT")
    add_line("END:VCALENDAR")
    return total


def _semantic_etag(
    events: tuple[NormalizedCalendarEvent, ...],
    *,
    calendar_identity: str,
    range_start: datetime,
    range_end: datetime,
) -> str:
    semantic = "\n".join((
        calendar_identity,
        _format_datetime(range_start),
        _format_datetime(range_end),
        *_calendar_lines(events, None),
    )).encode("utf-8")
    return f'W/"{hashlib.sha256(semantic).hexdigest()}"'


def serialize_calendar_ics(
    events: Iterable[NormalizedCalendarEvent],
    *,
    calendar_identity: str,
    range_start: datetime,
    range_end: datetime,
    generated_at: datetime | None = None,
) -> CalendarIcsDocument:
    event_tuple = tuple(events)
    if len(event_tuple) > MAX_ICS_EVENTS:
        raise CalendarIcsFeedError("Calendar feed exceeds the event limit.")
    generated_at = generated_at or datetime.now(UTC)
    if type(generated_at) is not datetime or generated_at.tzinfo is None:
        raise CalendarIcsFeedError("Calendar feed clock is invalid.")
    generated_at = generated_at.astimezone(UTC)
    if _calendar_size_upper_bound(event_tuple, generated_at) > MAX_ICS_BYTES:
        raise CalendarIcsFeedError("Calendar feed exceeds the output limit.")
    lines = _calendar_lines(event_tuple, generated_at)
    folded = [folded_line for line in lines for folded_line in _fold_line(line)]
    content = ("\r\n".join(folded) + "\r\n").encode("utf-8")
    if len(content) > MAX_ICS_BYTES:
        raise CalendarIcsFeedError("Calendar feed exceeds the output limit.")
    return CalendarIcsDocument(
        content=content,
        etag=_semantic_etag(
            event_tuple,
            calendar_identity=calendar_identity,
            range_start=range_start,
            range_end=range_end,
        ),
        event_count=len(event_tuple),
    )


def build_calendar_ics_feed(
    share: dict[str, Any],
    *,
    now: datetime | None = None,
) -> tuple[CalendarIcsDocument, tuple[datetime, datetime]]:
    range_start, range_end = utc_subscription_window(now)
    selection, events = project_calendar_share(share, range_start, range_end)
    identity = f"share:{share.get('id') or share.get('$id')}:{share.get('user_id')}:{selection}"
    document = serialize_calendar_ics(
        events,
        calendar_identity=identity,
        range_start=range_start,
        range_end=range_end,
        generated_at=(now.astimezone(UTC) if now is not None else datetime.now(UTC)),
    )
    return document, (range_start, range_end)


def if_none_match_matches(header: str | None, etag: str) -> bool:
    if not header:
        return False
    def weak_value(value: str) -> str:
        value = value.strip()
        return value[2:].strip() if value[:2].lower() == "w/" else value

    target = weak_value(etag)
    values = {item.strip() for item in header.split(",")}
    return "*" in values or any(weak_value(value) == target for value in values)
