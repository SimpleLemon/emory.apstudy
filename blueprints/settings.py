"""
blueprints/settings.py

Per-user settings and onboarding routes.
Handles Canvas iCal URL configuration, refresh intervals,
.ics token management, and "My Courses" selections.
"""

import secrets
from datetime import datetime

from flask import Blueprint, render_template, request, jsonify, redirect, url_for
from flask_login import login_required, current_user

from extensions import db
from models import UserSettings, UserCourse

settings_bp = Blueprint("settings", __name__)


# ── Page routes ───────────────────────────────────────────────────────────────

@settings_bp.route("/onboarding")
@login_required
def onboarding():
    """
    First-login onboarding page.
    Prompts the user to paste their Canvas iCal feed URL.
    Redirected here from auth.py if no feed URL is configured.
    """
    return render_template("onboarding.html", user={
        "name": current_user.name,
        "email": current_user.email,
        "picture": current_user.picture_url,
    })


@settings_bp.route("/")
@login_required
def settings_page():
    """Render the settings page with current user configuration."""
    user_settings = UserSettings.query.filter_by(
        user_id=current_user.id
    ).first()

    courses = UserCourse.query.filter_by(
        user_id=current_user.id
    ).order_by(
        UserCourse.term, UserCourse.subject, UserCourse.catalog
    ).all()

    return render_template("settings.html", user={
        "name": current_user.name,
        "email": current_user.email,
        "picture": current_user.picture_url,
    }, settings=user_settings, courses=courses)


# ── API routes (called by settings page JavaScript) ──────────────────────────

@settings_bp.route("/api/feed-url", methods=["POST"])
@login_required
def update_feed_url():
    """
    POST /settings/api/feed-url
    Body: { "canvas_ical_url": "https://emory.instructure.com/feeds/..." }

    Saves or updates the user's Canvas iCal feed URL.
    Used by both the onboarding page and the settings page.
    """
    data = request.get_json()
    if not data or not data.get("canvas_ical_url"):
        return jsonify({"error": "Missing canvas_ical_url"}), 400

    url = data["canvas_ical_url"].strip()

    # Basic validation: must look like an Emory Canvas feed URL
    if "emory.instructure.com/feeds/" not in url:
        return jsonify({
            "error": "URL does not appear to be a valid Emory Canvas iCal feed."
        }), 400

    settings = UserSettings.query.filter_by(user_id=current_user.id).first()
    if not settings:
        settings = UserSettings(
            user_id=current_user.id,
            ics_secret_token=secrets.token_urlsafe(32),
        )
        db.session.add(settings)

    settings.canvas_ical_url = url
    settings.updated_at = datetime.utcnow()
    db.session.commit()

    return jsonify({"status": "ok", "message": "Feed URL saved."})


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