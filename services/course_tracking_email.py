"""HTML email builder for course seat tracking notifications."""

import html
from urllib.parse import quote

APSTUDY_EMAIL_LOGO_PATH = "/static/images/apstudy-logo-email.jpg"
APSTUDY_EMAIL_LOGO_SIZE = 100
DEFAULT_ATLAS_URL = "https://atlas.emory.edu"
NAVY = "#0C234B"
PRIMARY_BLUE = "#1E5288"
SECONDARY_BG = "#F5F5F5"
OUTER_BG = "#E1E1E1"


def build_nest_courses_detail_path(section_id):
    """Build a same-origin Nest courses path with query + hash deep link to a section."""
    encoded = quote(str(section_id or ""), safe="")
    return f"/courses?section={encoded}#section={encoded}"


def build_nest_courses_detail_url(base_url, section_id):
    """Build Nest courses URL with query + hash deep link to a section."""
    base = str(base_url or "").rstrip("/")
    return f"{base}{build_nest_courses_detail_path(section_id)}"


def build_open_seat_subject(course_code, seats_available, waitlist_available=None):
    code = str(course_code or "Tracked class").strip() or "Tracked class"
    seats = _coerce_seats(seats_available)
    if seats == 1:
        seat_text = "1 Open Seat"
    elif seats is not None and seats > 1:
        seat_text = f"{seats} Open Seats"
    elif waitlist_available:
        seat_text = "Waitlist Availability"
    else:
        seat_text = "Open Seats"
    return f"🎉 {code} has {seat_text}"


def build_open_seat_html(section, *, base_url, atlas_url=None, nest_details_url=None):
    section = section or {}
    course_code = _text(section.get("course_code"), "N/A")
    course_title = _text(section.get("course_title"), "N/A")
    section_number = _text(section.get("section_number"), "N/A")
    crn = _text(section.get("crn"), "N/A")
    instructor = _text(section.get("instructor"), "TBA")
    schedule = _format_schedule(section)
    location = _text(section.get("location"), "TBA")
    campus = _text(section.get("campus") or section.get("campus_description"), "N/A")
    status = _text(section.get("enrollment_status"), "Unknown")
    seats = _format_seats(section.get("seats_available"))
    waitlist_total = _coerce_seats(section.get("waitlist_total"))
    waitlist_capacity = _coerce_seats(section.get("waitlist_capacity"))
    waitlist = "Unknown" if waitlist_total is None or waitlist_capacity is None else f"{waitlist_total} of {waitlist_capacity} filled"
    term = _format_term(section.get("term"))
    credits = _text(section.get("credit_hours"), "N/A")

    section_id = section.get("id") or section.get("section_id") or ""
    nest_url = nest_details_url or build_nest_courses_detail_url(base_url, section_id)
    atlas_link = str(atlas_url or DEFAULT_ATLAS_URL).strip() or DEFAULT_ATLAS_URL
    logo_size = APSTUDY_EMAIL_LOGO_SIZE
    logo_url = f"{str(base_url or 'https://nest.apstudy.org').rstrip('/')}{APSTUDY_EMAIL_LOGO_PATH}"

    rows = [
        ("Course code", course_code),
        ("Course title", course_title),
        ("Section", section_number),
        ("CRN", crn),
        ("Instructor", instructor),
        ("Schedule", schedule),
        ("Location", location),
        ("Campus", campus),
        ("Status", status),
        ("Seats available", seats),
        ("Waitlist", waitlist),
        ("Term", term),
        ("Credits", credits),
    ]
    table_rows = "".join(
        f"""
        <tr>
          <td style="padding:10px 12px;border-bottom:1px solid #E8E8E8;font-weight:600;color:{NAVY};width:38%;vertical-align:top;">{html.escape(label)}</td>
          <td style="padding:10px 12px;border-bottom:1px solid #E8E8E8;color:{NAVY};vertical-align:top;">{html.escape(value)}</td>
        </tr>"""
        for label, value in rows
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Course seat opened</title>
</head>
<body style="margin:0;padding:0;background-color:{OUTER_BG};">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:{OUTER_BG};margin:0;padding:0;">
    <tr>
      <td align="center" style="padding:32px 16px;">
        <table role="presentation" width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;background-color:#FFFFFF;font-family:Tahoma,Arial,Helvetica,sans-serif;">
          <tr>
            <td align="center" style="padding:24px 40px 12px;">
              <img src="{html.escape(logo_url)}" alt="APStudy Logo" width="{logo_size}" height="{logo_size}" style="display:block;width:{logo_size}px;height:{logo_size}px;max-width:{logo_size}px;border-radius:50%;object-fit:cover;border:0;">
            </td>
          </tr>
          <tr>
            <td style="padding:8px 40px 24px;font-size:16px;line-height:160%;color:{NAVY};">
              <p style="margin:0 0 16px;">A section you&rsquo;re tracking has new seat or waitlist availability. Check Emory Atlas soon before it fills.</p>
            </td>
          </tr>
          <tr>
            <td style="padding:0 40px 24px;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;font-size:15px;line-height:150%;">
                {table_rows}
              </table>
            </td>
          </tr>
          <tr>
            <td align="center" style="padding:0 40px 12px;">
              <a href="{html.escape(atlas_link, quote=True)}" target="_blank" rel="noopener noreferrer" style="display:inline-block;padding:14px 28px;background-color:{PRIMARY_BLUE};color:#FFFFFF;text-decoration:none;border-radius:6px;font-size:16px;font-weight:600;">Emory Atlas</a>
            </td>
          </tr>
          <tr>
            <td align="center" style="padding:0 40px 40px;">
              <a href="{html.escape(nest_url, quote=True)}" target="_blank" rel="noopener noreferrer" style="display:inline-block;padding:14px 28px;background-color:{SECONDARY_BG};color:{PRIMARY_BLUE};text-decoration:none;border-radius:6px;font-size:16px;font-weight:600;border:1px solid {PRIMARY_BLUE};">Nest Course Details</a>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


def _coerce_seats(value):
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _text(value, fallback=""):
    text = str(value if value not in (None, "") else fallback).strip()
    return text or fallback


def _format_seats(value):
    seats = _coerce_seats(value)
    if seats is None:
        return "Unknown"
    return str(seats)


def _format_term(value):
    raw = str(value or "").strip()
    if not raw:
        return "N/A"
    if "_" in raw:
        season, year = raw.split("_", 1)
        return f"{season} {year}".strip()
    return raw


def _format_schedule(section):
    display = str(section.get("schedule_display") or "").strip()
    if display and display.upper() != "TBA":
        return display
    meetings = section.get("meetings") or []
    parts = []
    for meeting in meetings:
        if not isinstance(meeting, dict):
            continue
        day = str(meeting.get("day") or "").strip()
        start = _format_time_token(meeting.get("start"))
        end = _format_time_token(meeting.get("end"))
        if day and start and end:
            parts.append(f"{day} {start}-{end}")
    if parts:
        return ", ".join(parts)
    return display or "TBA"


def _format_time_token(value):
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    if len(digits) == 3:
        digits = f"0{digits}"
    if len(digits) != 4:
        return ""
    hour = int(digits[:2])
    minute = digits[2:]
    suffix = "a"
    if hour >= 12:
        suffix = "p"
        if hour > 12:
            hour -= 12
    if hour == 0:
        hour = 12
    if minute == "00":
        return f"{hour}{suffix}"
    return f"{hour}:{minute}{suffix}"
