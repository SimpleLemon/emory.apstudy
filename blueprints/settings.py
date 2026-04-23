"""
blueprints/settings.py

Per-user settings and onboarding routes.
Handles Canvas iCal URL configuration, refresh intervals,
.ics token management, and "My Courses" selections.
"""

import json
import secrets
from datetime import datetime
from urllib.parse import urlparse, urlunparse

from flask import Blueprint, render_template, request, jsonify, redirect, url_for
from flask_login import login_required, current_user

from extensions import db
from models import UserSettings, UserCourse
from services.atlas_client import DEFAULT_TERM

settings_bp = Blueprint("settings", __name__)

CANVAS_CALENDAR_HOST = "canvas.emory.edu"
CANVAS_CALENDAR_PATH_PREFIX = "/feeds/calendars"
MAX_OTHER_CALENDAR_URLS = 10


def _normalize_calendar_url(url):
    """Return a normalized URL string for duplicate checks, or None if invalid."""
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
    normalized = urlunparse((
        scheme,
        parsed.netloc.lower(),
        normalized_path,
        "",
        parsed.query,
        "",
    ))
    return normalized


def _is_valid_canvas_calendar_url(url):
    """Validate strict Emory Canvas calendar URLs."""
    if not isinstance(url, str):
        return False

    parsed = urlparse(url.strip())
    if parsed.scheme.lower() != "https":
        return False

    if parsed.netloc.lower() != CANVAS_CALENDAR_HOST:
        return False

    return (parsed.path or "").startswith(CANVAS_CALENDAR_PATH_PREFIX)


def _load_other_calendar_urls(settings):
    """Load and sanitize persisted optional calendar URLs from JSON text."""
    if not settings or not settings.other_ical_urls_json:
        return []

    try:
        parsed = json.loads(settings.other_ical_urls_json)
    except json.JSONDecodeError:
        return []

    if not isinstance(parsed, list):
        return []

    urls = []
    for item in parsed:
        if isinstance(item, str) and item.strip():
            urls.append(item.strip())
    return urls[:MAX_OTHER_CALENDAR_URLS]


def _validate_other_calendar_urls(other_urls, canvas_url):
    """Validate optional external calendar links and prevent duplicates."""
    if other_urls is None:
        return []
    if not isinstance(other_urls, list):
        raise ValueError("other_ical_urls must be a list.")

    cleaned = []
    seen = set()
    normalized_canvas = _normalize_calendar_url(canvas_url)

    for raw in other_urls:
        if not isinstance(raw, str):
            raise ValueError("Each calendar URL must be a string.")

        value = raw.strip()
        if not value:
            continue

        normalized = _normalize_calendar_url(value)
        if not normalized:
            raise ValueError(
                "Each optional calendar link must be a valid http(s) or webcal URL."
            )

        if normalized_canvas and normalized == normalized_canvas:
            raise ValueError("Optional calendar links cannot duplicate the Emory Canvas calendar.")

        if normalized in seen:
            raise ValueError("Duplicate optional calendar links are not allowed.")

        seen.add(normalized)
        cleaned.append(value)

    if len(cleaned) > MAX_OTHER_CALENDAR_URLS:
        raise ValueError(f"You can add up to {MAX_OTHER_CALENDAR_URLS} optional calendar links.")

    return cleaned


MAJOR_OPTIONS = [
    "Undecided",
    "African American Studies",
    "Anthropology",
    "Art History",
    "Biology",
    "Biochemistry",
    "Business Administration",
    "Chemistry",
    "Chinese",
    "Classics",
    "Computer Science",
    "Creative Writing",
    "Dance and Movement Studies",
    "Economics",
    "English",
    "Environmental Science",
    "Ethics",
    "Film and Media Studies",
    "French",
    "German Studies",
    "History",
    "Human Health",
    "International Studies",
    "Latin American Studies",
    "Linguistics",
    "Mathematics",
    "Music",
    "Neuroscience and Behavioral Biology",
    "Philosophy",
    "Physics",
    "Political Science",
    "Psychology",
    "Religion",
    "Sociology",
    "Spanish and Portuguese",
    "Women, Gender, and Sexuality Studies",
    "Other",
]

YEAR_OPTIONS = [
    "Freshman (Class of 2030)",
    "Sophomore (Class of 2029)",
    "Junior (Class of 2028)",
    "Senior (Class of 2027)",
    "Graduate Student",
    "Other",
]

SCHOOL_OPTIONS = [
    "Emory College of Arts and Sciences",
    "Oxford College",
    "Goizueta Business School",
    "Nell Hodgson Woodruff School of Nursing",
    "Rollins School of Public Health",
    "Laney Graduate School",
    "Other",
]


def _onboarding_courses():
    return UserCourse.query.filter_by(
        user_id=current_user.id,
        source="onboarding",
    ).order_by(
        UserCourse.added_at.asc()
    ).all()


def _onboarding_context():
    majors = []
    if current_user.majors_json:
        try:
            majors = json.loads(current_user.majors_json)
        except json.JSONDecodeError:
            majors = []

    return {
        "user": {
            "name": current_user.name,
            "email": current_user.email,
            "picture": current_user.picture_url,
        },
        "first_name": (current_user.name or current_user.email or "Student").split()[0],
        "step": current_user.onboarding_step or 1,
        "class_year": current_user.class_year,
        "school_college": current_user.school_college,
        "selected_majors": majors,
        "courses": [
            {
                "id": course.id,
                "course_code": f"{course.subject} {course.catalog}",
                "course_name": course.course_name,
                "section_number": course.section_number,
                "instructor_name": course.instructor_name,
                "term": course.term,
            }
            for course in _onboarding_courses()
        ],
        "major_options": MAJOR_OPTIONS,
        "year_options": YEAR_OPTIONS,
        "school_options": SCHOOL_OPTIONS,
        "default_term": DEFAULT_TERM,
    }


# ── Page routes ───────────────────────────────────────────────────────────────

@settings_bp.route("/onboarding")
@login_required
def onboarding():
    """
    First-login onboarding page.
    Prompts the user to paste their Canvas iCal feed URL.
    Redirected here from auth.py if no feed URL is configured.
    """
    if current_user.onboarding_complete:
        return redirect(url_for("dashboard.dashboard"))

    return render_template("onboarding.html", **_onboarding_context())


@settings_bp.route("/onboarding", methods=["POST"])
@login_required
def save_onboarding():
    """Persist each onboarding step and advance the user's progress."""
    payload = request.get_json(silent=True) or request.form.to_dict(flat=True)
    step = int(payload.get("step", current_user.onboarding_step or 1))
    action = payload.get("action", "continue")

    if step == 1:
        current_user.onboarding_step = max(current_user.onboarding_step or 1, 2)
        db.session.commit()
        return jsonify({"status": "ok", "next_step": 2})

    if step == 2:
        class_year = (payload.get("class_year") or "").strip() or None
        school_college = (payload.get("school_college") or "").strip() or None
        majors = payload.get("majors") or []

        if isinstance(majors, str):
            majors = [item for item in majors.split("|") if item]

        if len(majors) > 2:
            return jsonify({"error": "Select no more than 2 majors."}), 400

        current_user.class_year = class_year
        current_user.school_college = school_college
        current_user.majors_json = json.dumps(majors)
        current_user.onboarding_step = max(current_user.onboarding_step or 2, 3)
        db.session.commit()
        return jsonify({"status": "ok", "next_step": 3})

    if step == 3:
        if action == "add_course":
            course_code = (payload.get("course_code") or "").strip().upper()
            course_name = (payload.get("course_name") or "").strip() or None
            section_number = (payload.get("section_number") or "").strip() or None
            instructor_name = (payload.get("instructor_name") or "").strip() or None
            term = (payload.get("term") or DEFAULT_TERM).strip() or DEFAULT_TERM

            subject = (payload.get("subject") or "").strip().upper()
            catalog = (payload.get("catalog") or "").strip()

            if course_code and (not subject or not catalog):
                parts = course_code.split()
                if len(parts) >= 2:
                    subject = parts[0].upper()
                    catalog = parts[1]

            if not subject or not catalog:
                return jsonify({"error": "Course code is required."}), 400

            existing = UserCourse.query.filter_by(
                user_id=current_user.id,
                term=term,
                subject=subject,
                catalog=catalog,
                crn=None,
                source="onboarding",
            ).first()
            if existing:
                return jsonify({"error": "Course already added."}), 409

            course = UserCourse(
                user_id=current_user.id,
                term=term,
                subject=subject,
                catalog=catalog,
                course_name=course_name,
                section_number=section_number,
                instructor_name=instructor_name,
                source="onboarding",
            )
            db.session.add(course)
            db.session.commit()

            return jsonify({
                "status": "ok",
                "course": {
                    "id": course.id,
                    "course_code": f"{subject} {catalog}",
                    "course_name": course_name,
                    "section_number": section_number,
                    "instructor_name": instructor_name,
                    "term": term,
                },
            }), 201

        if action in {"advance", "continue", "review"}:
            course_count = UserCourse.query.filter_by(
                user_id=current_user.id,
                source="onboarding",
            ).count()
            if course_count < 1:
                return jsonify({"error": "Add at least one course before continuing."}), 400

            current_user.onboarding_step = 4
            db.session.commit()
            return jsonify({"status": "ok", "next_step": 4})

        if action == "complete":
            current_user.onboarding_step = 4
            current_user.onboarding_complete = True
            db.session.commit()
            return jsonify({"status": "ok", "redirect_url": url_for("dashboard.dashboard")})

    if step == 4:
        current_user.onboarding_complete = True
        current_user.onboarding_step = 4
        db.session.commit()
        return jsonify({"status": "ok", "redirect_url": url_for("dashboard.dashboard")})

    return jsonify({"error": "Invalid onboarding step."}), 400


@settings_bp.route("/")
@login_required
def settings_page():
    """Render the settings page with current user configuration."""
    if not current_user.onboarding_complete:
        return redirect(url_for("settings.onboarding"))

    if not current_user.created_at:
        current_user.created_at = datetime.utcnow()
        db.session.commit()

    user_settings = UserSettings.query.filter_by(
        user_id=current_user.id
    ).first()
    other_calendar_urls = _load_other_calendar_urls(user_settings)

    courses = UserCourse.query.filter_by(
        user_id=current_user.id
    ).order_by(
        UserCourse.term, UserCourse.subject, UserCourse.catalog
    ).all()

    return render_template("settings.html", user={
        "name": current_user.name or current_user.email,
        "email": current_user.email,
        "picture": current_user.picture_url,
        "member_since": current_user.created_at.strftime("%b %d, %Y"),
    }, settings=user_settings, courses=courses, other_calendar_urls=other_calendar_urls)


# ── API routes (called by settings page JavaScript) ──────────────────────────

@settings_bp.route("/api/profile", methods=["POST"])
@login_required
def update_profile():
    """Update editable profile fields for the authenticated user."""
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()

    if not name:
        return jsonify({"error": "Name is required."}), 400

    current_user.name = name
    if not current_user.created_at:
        current_user.created_at = datetime.utcnow()
    db.session.commit()

    return jsonify({
        "status": "ok",
        "name": current_user.name,
        "member_since": current_user.created_at.strftime("%b %d, %Y"),
    })

@settings_bp.route("/api/feed-url", methods=["POST"])
@login_required
def update_feed_url():
    """
    POST /settings/api/feed-url
        Body: {
            "canvas_ical_url": "https://canvas.emory.edu/feeds/calendars/...",
            "other_ical_urls": ["https://calendar.google.com/...", "webcal://..."]
        }

    Saves or updates the user's Canvas iCal feed URL.
    Used by both the onboarding page and the settings page.
    """
    data = request.get_json(silent=True) or {}
    if "canvas_ical_url" not in data:
        return jsonify({"error": "Missing canvas_ical_url"}), 400

    url = (data.get("canvas_ical_url") or "").strip()
    if not url:
        return jsonify({"error": "Missing canvas_ical_url"}), 400

    if not _is_valid_canvas_calendar_url(url):
        return jsonify({
            "error": "Emory Canvas calendar must use https://canvas.emory.edu/feeds/calendars..."
        }), 400

    try:
        if "other_ical_urls" in data:
            other_ical_urls = _validate_other_calendar_urls(
                data.get("other_ical_urls"),
                url,
            )
        else:
            existing = UserSettings.query.filter_by(user_id=current_user.id).first()
            other_ical_urls = _load_other_calendar_urls(existing)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    settings = UserSettings.query.filter_by(user_id=current_user.id).first()
    if not settings:
        settings = UserSettings(
            user_id=current_user.id,
            ics_secret_token=secrets.token_urlsafe(32),
        )
        db.session.add(settings)

    settings.canvas_ical_url = url
    settings.other_ical_urls_json = json.dumps(other_ical_urls)
    settings.updated_at = datetime.utcnow()
    db.session.commit()

    return jsonify({
        "status": "ok",
        "message": "Feed URL saved.",
        "other_ical_urls": other_ical_urls,
    })


@settings_bp.route("/api/refresh-interval", methods=["POST"])
@login_required
def update_refresh_interval():
    """
    POST /settings/api/refresh-interval
    Body: { "minutes": 15 }

    Updates how frequently the user's Canvas feed is re-fetched.
    """
    data = request.get_json()
    minutes = data.get("minutes") if data else None

    if not isinstance(minutes, int) or minutes < 5 or minutes > 1440:
        return jsonify({
            "error": "Refresh interval must be between 5 and 1440 minutes."
        }), 400

    settings = UserSettings.query.filter_by(user_id=current_user.id).first()
    if not settings:
        return jsonify({"error": "No settings found. Complete onboarding first."}), 404

    settings.feed_refresh_minutes = minutes
    settings.updated_at = datetime.utcnow()
    db.session.commit()

    return jsonify({"status": "ok", "refresh_interval_minutes": minutes})


@settings_bp.route("/api/regenerate-token", methods=["POST"])
@login_required
def regenerate_ics_token():
    """
    POST /settings/api/regenerate-token

    Generates a new .ics subscription token. Invalidates the old one,
    so the user must re-subscribe in Apple Calendar with the new URL.
    """
    settings = UserSettings.query.filter_by(user_id=current_user.id).first()
    if not settings:
        return jsonify({"error": "No settings found."}), 404

    settings.ics_secret_token = secrets.token_urlsafe(32)
    settings.updated_at = datetime.utcnow()
    db.session.commit()

    return jsonify({
        "status": "ok",
        "message": "Token regenerated. Update your calendar subscription URL.",
        "new_subscription_url": url_for(
            "calendar.ics_feed",
            token=settings.ics_secret_token,
            _external=True,
        ),
    })


# ── My Courses ────────────────────────────────────────────────────────────────

@settings_bp.route("/api/courses", methods=["GET"])
@login_required
def list_my_courses():
    """
    GET /settings/api/courses

    Returns the user's saved course selections.
    """
    courses = UserCourse.query.filter_by(
        user_id=current_user.id
    ).order_by(
        UserCourse.term, UserCourse.subject, UserCourse.catalog
    ).all()

    return jsonify({
        "count": len(courses),
        "courses": [
            {
                "id": c.id,
                "term": c.term,
                "subject": c.subject,
                "catalog": c.catalog,
                "crn": c.crn,
                "course_code": f"{c.subject} {c.catalog}",
            }
            for c in courses
        ],
    })


@settings_bp.route("/api/courses", methods=["POST"])
@login_required
def add_course():
    """
    POST /settings/api/courses
    Body: { "term": "Fall_2026", "subject": "CHEM", "catalog": "150", "crn": "1700" }

    Adds a course to the user's "My Courses" list. CRN is optional.
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "Missing request body"}), 400

    term = data.get("term", "").strip()
    subject = data.get("subject", "").strip().upper()
    catalog = data.get("catalog", "").strip()
    crn = data.get("crn", "").strip() or None

    if not term or not subject or not catalog:
        return jsonify({"error": "term, subject, and catalog are required."}), 400

    # Check for duplicates
    existing = UserCourse.query.filter_by(
        user_id=current_user.id,
        term=term,
        subject=subject,
        catalog=catalog,
        crn=crn,
    ).first()

    if existing:
        return jsonify({"error": "Course already in your list."}), 409

    course = UserCourse(
        user_id=current_user.id,
        term=term,
        subject=subject,
        catalog=catalog,
        crn=crn,
    )
    db.session.add(course)
    db.session.commit()

    return jsonify({
        "status": "ok",
        "course": {
            "id": course.id,
            "term": term,
            "subject": subject,
            "catalog": catalog,
            "crn": crn,
            "course_code": f"{subject} {catalog}",
        },
    }), 201


@settings_bp.route("/api/courses/<int:course_id>", methods=["DELETE"])
@login_required
def remove_course(course_id):
    """
    DELETE /settings/api/courses/42

    Removes a course from the user's "My Courses" list.
    """
    course = UserCourse.query.filter_by(
        id=course_id,
        user_id=current_user.id,
    ).first()

    if not course:
        return jsonify({"error": "Course not found."}), 404

    db.session.delete(course)
    db.session.commit()

    return jsonify({"status": "ok", "message": "Course removed."})