"""
services/ics_builder.py

Builds a filtered .ics (iCalendar) file from a user's cached
calendar events and optionally their Atlas course schedules.

Output is RFC 5545 compliant [7] and compatible with Apple Calendar,
Google Calendar, and Outlook subscription feeds.

The generated .ics is served at /api/calendar/feed.ics?token=USER_TOKEN
by the calendar_api blueprint.
"""

import icalendar
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from appwrite.exception import AppwriteException
from appwrite.query import Query
from appwrite_client import COLLECTIONS
from appwrite_helpers import first_row, list_rows_all, parse_datetime
from services.calendar_store import list_calendar_rows_all
from services.calendar_events import (
    _api_event_overlaps_range,
    _apply_event_override,
    _event_ref_for_cache_event,
    _load_active_canvas_sources,
    _load_calendar_preferences,
    _load_event_overrides,
    _load_canvas_import_routing_rows,
    _project_canvas_calendar_events,
    _serialize_event,
)


DEFAULT_ICS_TIMEZONE = "America/New_York"


def _user_settings(user_id):
    try:
        return first_row(
            COLLECTIONS["user_settings"],
            [Query.equal("user_id", [str(user_id)])],
        ) or {}
    except Exception:
        return {}


def _valid_timezone(value, fallback=DEFAULT_ICS_TIMEZONE):
    candidate = str(value or "").strip()
    if not candidate:
        candidate = fallback
    try:
        ZoneInfo(candidate)
    except (KeyError, TypeError):
        candidate = fallback
    return candidate


def _event_timezone(event, source=None, settings=None):
    event = event or {}
    source = source or {}
    settings = settings or {}
    for row in (event, source, settings):
        for key in (
            "timezone",
            "time_zone",
            "tzid",
            "source_timezone",
            "canvas_timezone",
        ):
            if row.get(key):
                return _valid_timezone(row[key])
    return DEFAULT_ICS_TIMEZONE


def _load_projected_events(
    user_id,
    cache_events,
    settings,
    overrides_by_ref=None,
    *,
    require_shares_ics=True,
):
    canvas_events = [
        event for event in cache_events
        if event.get("canvas_source_id") or event.get("canvas_event_ref")
    ]
    if not canvas_events:
        return [], {}
    if overrides_by_ref is None:
        overrides = _load_event_overrides(user_id, list_calendar_rows_all)
        overrides_by_ref = {
            override.get("event_ref"): override
            for override in overrides
            if override.get("event_ref")
        }
    preferences = _load_calendar_preferences(user_id, list_calendar_rows_all)
    source_rows = _load_active_canvas_sources(
        user_id,
        require_shares_ics=require_shares_ics,
    )
    return _project_canvas_calendar_events(
        user_id,
        canvas_events,
        overrides_by_ref,
        preferences=preferences,
        source_rows=source_rows,
        routing_rows=_load_canvas_import_routing_rows(user_id),
        api_event_overlaps_range=_api_event_overlaps_range,
        require_shares_ics=require_shares_ics,
    ), {
        (source.get("source_id"), source.get("account_key")): source
        for source in source_rows
    }


def _add_event_datetime(vevent, field, value, *, all_day, timezone_name=None, canvas=False):
    if not value:
        return
    if all_day:
        try:
            vevent.add(field, date.fromisoformat(str(value)[:10]))
        except ValueError:
            return
        return
    parsed = parse_datetime(value)
    if not parsed:
        return
    if canvas:
        target_zone = ZoneInfo(_valid_timezone(timezone_name))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        local_value = parsed.astimezone(target_zone).replace(tzinfo=None)
        vevent.add(field, local_value, parameters={"TZID": _valid_timezone(timezone_name)})
        return
    vevent.add(field, parsed)


def _build_event_uid(event_uid, user_id, event_id=None):
    """
    Generate a globally unique UID for each event in the output .ics.

    If the original Canvas event has a UID, prefix it with the user ID
    to avoid collisions if multiple users share the same subscription
    infrastructure. If no UID exists, generate one from the event's
    database ID.
    """
    if event_uid:
        return f"u{user_id}-{event_uid}"
    if event_id is None or str(event_id) == "":
        raise ValueError("A cached event ID is required when event_uid is missing.")
    return f"u{user_id}-generated-{event_id}@nest.apstudy.org"


def build_ics_for_user(user_id):
    """
    Build a complete .ics file string for a user from their cached
    calendar events.

    Args:
        user_id: Integer user ID.

    Returns:
        String containing the full .ics file content, ready to be
        served with Content-Type: text/calendar.
    """
    # Create the top-level VCALENDAR container
    cal = icalendar.Calendar()
    cal.add("prodid", "-//nest.apstudy.org//calendar//EN")
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")
    cal.add("method", "PUBLISH")
    cal.add("x-wr-calname", "Nest APStudy")
    settings = _user_settings(user_id)
    cal.add("x-wr-timezone", _valid_timezone(settings.get("timezone")))

    # Fetch all cached events for this user
    try:
        events = list_calendar_rows_all(
            COLLECTIONS["calendar_cache"],
            [
                Query.equal("user_id", [str(user_id)]),
                Query.order_asc("event_start"),
            ],
        )
    except AppwriteException:
        events = []

    try:
        overrides = _load_event_overrides(user_id, list_calendar_rows_all)
    except Exception:
        overrides = []
    overrides_by_ref = {
        override.get("event_ref"): override
        for override in overrides
        if override.get("event_ref")
    }
    projected_events, canvas_sources = _load_projected_events(
        user_id,
        events,
        settings,
        overrides_by_ref,
    )
    projected_by_ref = {}
    for projected in projected_events:
        projected_by_ref.setdefault(projected.get("event_ref"), []).append(projected)

    now = datetime.now(timezone.utc)

    for event in events:
        if event.get("canvas_source_id") or event.get("canvas_event_ref"):
            event_ref = _event_ref_for_cache_event(event)
            projected = (projected_by_ref.get(event_ref) or [])
            if not projected:
                continue
            event = projected.pop(0)
        else:
            override = overrides_by_ref.get(_event_ref_for_cache_event(event))
            if override:
                event = _apply_event_override(
                    _serialize_event(event, settings),
                    override,
                )
                if not event:
                    continue
        vevent = icalendar.Event()

        # UID is required per RFC 5545 and must be globally unique [7]
        event_id = event.get("$id") or event.get("id")
        vevent.add(
            "uid",
            _build_event_uid(event.get("event_uid") or event.get("uid"), user_id, event_id),
        )

        # DTSTAMP is the timestamp of when this .ics was generated [7]
        vevent.add("dtstamp", now)

        title = event.get("event_title") or event.get("title")
        if title:
            vevent.add("summary", title)

        description = event.get("raw_description") or event.get("description")
        if description:
            vevent.add("description", description)

        is_canvas = event.get("source_type") == "canvas"
        is_all_day = bool(event.get("is_all_day"))
        start_value = event.get("event_start") or event.get("start")
        end_value = event.get("event_end") or event.get("end")
        source = None
        if is_canvas:
            raw_event = next(
                (
                    candidate for candidate in events
                    if _event_ref_for_cache_event(candidate) == event.get("event_ref")
                ),
                {},
            )
            source = canvas_sources.get(
                (
                    raw_event.get("canvas_source_id"),
                    raw_event.get("canvas_account_key"),
                )
            )
        timezone_name = None
        if is_canvas:
            timezone_event = {**raw_event, **event}
            timezone_name = _event_timezone(timezone_event, source, settings)
        _add_event_datetime(
            vevent,
            "dtstart",
            start_value,
            all_day=is_all_day,
            timezone_name=timezone_name,
            canvas=is_canvas,
        )
        if end_value:
            _add_event_datetime(
                vevent,
                "dtend",
                end_value,
                all_day=is_all_day,
                timezone_name=timezone_name,
                canvas=is_canvas,
            )
        elif start_value:
            event_start = parse_datetime(start_value)
            if event_start:
                if is_all_day:
                    try:
                        vevent.add("dtend", date.fromisoformat(str(start_value)[:10]) + timedelta(days=1))
                    except ValueError:
                        pass
                else:
                    vevent.add("dtend", event_start + timedelta(hours=1))

        # Add course name as a category for client-side filtering
        course_name = event.get("course_name") or event.get("course")
        if course_name:
            vevent.add("categories", [course_name])

        # Add event type as a custom property
        event_type = event.get("event_type") or event.get("type")
        if event_type and event_type != "unknown":
            vevent.add("x-apstudy-type", event_type)

        cal.add_component(vevent)

    # Optionally inject Atlas course schedule as recurring events
    _inject_atlas_schedule(cal, user_id)

    return cal.to_ical().decode("utf-8")


def _inject_atlas_schedule(cal, user_id):
    """
    If the user has selected courses in "My Courses", inject their
    Atlas meeting times as recurring weekly events.

    This merges class schedules (from the Atlas scrape) with Canvas
    assignment due dates (from the iCal feed) into a single .ics file.

    Reads from user_courses table and atlas-data/ JSON files.
    """
    import json
    import os

    _PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ATLAS_DATA_DIR = os.path.join(_PROJECT_ROOT, "atlas-data")

    try:
        user_courses = list_rows_all(
            COLLECTIONS["user_courses"],
            [Query.equal("user_id", [str(user_id)])],
        )
    except AppwriteException:
        user_courses = []

    if not user_courses:
        return

    now = datetime.now(timezone.utc)

    for uc in user_courses:
        # Read the course JSON file
        filepath = os.path.join(
            ATLAS_DATA_DIR,
            uc.get("term"),
            uc.get("subject"),
            f"{uc.get('catalog')}.json",
        )
        if not os.path.isfile(filepath):
            continue

        try:
            with open(filepath, "r", encoding="utf-8") as f:
                course_data = json.load(f)
        except (json.JSONDecodeError, IOError):
            continue

        course_code = course_data.get(
            "course_code",
            f"{uc.get('subject')} {uc.get('catalog')}",
        )
        course_title = course_data.get("course_title", "")
        date_range = course_data.get("date_range", {})

        # Determine semester date boundaries for RRULE UNTIL
        end_date_str = date_range.get("end")
        if end_date_str:
            try:
                semester_end = datetime.strptime(end_date_str, "%Y-%m-%d")
            except ValueError:
                semester_end = None
        else:
            semester_end = None

        sections = course_data.get("sections", [])

        for section in sections:
            # If user selected a specific CRN, only include that section
            if uc.get("crn") and section.get("crn") != uc.get("crn"):
                continue

            schedule = section.get("schedule", {})
            meetings = schedule.get("meetings", [])
            schedule_type = section.get("schedule_type", "")
            instructor = section.get("instructor", "")
            section_num = section.get("section_number", "")

            if not meetings:
                continue

            # Map day names to iCalendar RRULE day codes [7]
            day_to_rrule = {
                "Mon": "MO", "Tue": "TU", "Wed": "WE",
                "Thu": "TH", "Fri": "FR", "Sat": "SA", "Sun": "SU",
            }

            rrule_days = []
            for meeting in meetings:
                day_code = day_to_rrule.get(meeting.get("day"))
                if day_code:
                    rrule_days.append(day_code)

            if not rrule_days or not meetings[0].get("start"):
                continue

            # Use the first meeting's times for the event
            start_time_str = meetings[0].get("start", "0800")
            end_time_str = meetings[0].get("end", "0900")

            # Parse time strings (e.g., "0830" -> hour=8, minute=30)
            try:
                start_hour = int(start_time_str[:2]) if len(start_time_str) >= 4 else int(start_time_str[0])
                start_min = int(start_time_str[-2:])
                end_hour = int(end_time_str[:2]) if len(end_time_str) >= 4 else int(end_time_str[0])
                end_min = int(end_time_str[-2:])
            except (ValueError, IndexError):
                continue

            # Build the VEVENT with RRULE for weekly recurrence
            vevent = icalendar.Event()

            summary = f"{course_code} {schedule_type}"
            if section_num:
                summary += f" (Sec {section_num})"

            vevent.add(
                "uid",
                f"atlas-{uc.get('term')}-{uc.get('subject')}{uc.get('catalog')}-{section.get('crn', 'x')}@nest.apstudy.org",
            )
            vevent.add("dtstamp", now)
            vevent.add("summary", summary)

            desc_parts = []
            if course_title:
                desc_parts.append(course_title)
            if instructor:
                desc_parts.append(f"Instructor: {instructor}")
            desc_parts.append(f"CRN: {section.get('crn', 'N/A')}")
            vevent.add("description", " | ".join(desc_parts))

            # DTSTART for the first occurrence
            # Use a reference Monday to calculate the correct first day
            # This is a simplification; a production version would
            # calculate the actual first class date from the semester start
            start_dt = datetime(2026, 8, 26, start_hour, start_min)
            end_dt = datetime(2026, 8, 26, end_hour, end_min)

            vevent.add("dtstart", start_dt)
            vevent.add("dtend", end_dt)

            # Weekly recurrence rule
            rrule = {"freq": "weekly", "byday": rrule_days}
            if semester_end:
                rrule["until"] = semester_end
            vevent.add("rrule", rrule)

            vevent.add("categories", [course_code])
            vevent.add("x-apstudy-type", "class-meeting")
            vevent.add("x-apstudy-schedule-type", schedule_type)

            cal.add_component(vevent)
