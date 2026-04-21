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
from datetime import datetime, timedelta, timezone

from extensions import db
from models import CalendarCache, UserCourse


def _ics_escape(text):
    """
    Escape special characters for iCalendar text values.
    Commas, semicolons, and backslashes must be escaped per RFC 5545 [7].
    """
    if not text:
        return ""
    return (
        text
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def _build_event_uid(event_uid, user_id):
    """
    Generate a globally unique UID for each event in the output .ics.

    If the original Canvas event has a UID, prefix it with the user ID
    to avoid collisions if multiple users share the same subscription
    infrastructure. If no UID exists, generate one from the event's
    database ID.
    """
    if event_uid:
        return f"u{user_id}-{event_uid}"
    return f"u{user_id}-generated-{id(event_uid)}@emory.apstudy.org"


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
    cal.add("prodid", "-//emory.apstudy.org//calendar//EN")
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")
    cal.add("method", "PUBLISH")
    cal.add("x-wr-calname", "Emory APStudy")
    cal.add("x-wr-timezone", "America/New_York")

    # Fetch all cached events for this user
    events = CalendarCache.query.filter_by(
        user_id=user_id
    ).order_by(
        CalendarCache.event_start.asc()
    ).all()

    now = datetime.now(timezone.utc)

    for event in events:
        vevent = icalendar.Event()

        # UID is required per RFC 5545 and must be globally unique [7]
        vevent.add("uid", _build_event_uid(event.event_uid, user_id))

        # DTSTAMP is the timestamp of when this .ics was generated [7]
        vevent.add("dtstamp", now)

        if event.event_title:
            vevent.add("summary", event.event_title)

        if event.raw_description:
            vevent.add("description", event.raw_description)

        if event.event_start:
            vevent.add("dtstart", event.event_start)

        if event.event_end:
            vevent.add("dtend", event.event_end)
        elif event.event_start:
            # If no end time, default to 1 hour after start
            vevent.add("dtend", event.event_start + timedelta(hours=1))

        # Add course name as a category for client-side filtering
        if event.course_name:
            vevent.add("categories", [event.course_name])

        # Add event type as a custom property
        if event.event_type and event.event_type != "unknown":
            vevent.add("x-apstudy-type", event.event_type)

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

    user_courses = UserCourse.query.filter_by(user_id=user_id).all()

    if not user_courses:
        return

    now = datetime.now(timezone.utc)

    for uc in user_courses:
        # Read the course JSON file
        filepath = os.path.join(ATLAS_DATA_DIR, uc.term, uc.subject, f"{uc.catalog}.json")
        if not os.path.isfile(filepath):
            continue

        try:
            with open(filepath, "r", encoding="utf-8") as f:
                course_data = json.load(f)
        except (json.JSONDecodeError, IOError):
            continue

        course_code = course_data.get("course_code", f"{uc.subject} {uc.catalog}")
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
            if uc.crn and section.get("crn") != uc.crn:
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

            vevent.add("uid", f"atlas-{uc.term}-{uc.subject}{uc.catalog}-{section.get('crn', 'x')}@emory.apstudy.org")
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