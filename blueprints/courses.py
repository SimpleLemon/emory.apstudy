import logging
import json
import random
from datetime import datetime

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required

from appwrite.exception import AppwriteException
from appwrite.id import ID
from appwrite.query import Query

from appwrite_client import COLLECTIONS
from appwrite_helpers import (
    create_row_safe,
    delete_row_safe,
    first_row,
    format_datetime,
    get_row_safe,
    list_rows_all,
    update_row_safe,
)
from services.atlas_client import (
    build_section_id,
    get_sections_by_ids,
    get_sections_index,
    is_section_trackable,
    parse_section_id,
)
from services.app_config import spring_course_tracking_open
from services.course_live_snapshots import merge_snapshots_into_sections, refresh_section_snapshot
from services.course_catalog import get_course_catalog_metadata
from services.discord_audit import (
    emit_course_track_event,
    emit_creation_event,
    format_actor,
)
from services import invites
from services.entitlements import EntitlementError, EntitlementLimitError, TRACK_INTERVALS_KEY, check_limit, request_entitlements
from services.user_profile import is_emory_or_oxford_user


courses_bp = Blueprint("courses", __name__)
logger = logging.getLogger(__name__)

COURSE_COLOR_KEYS = tuple(f"course-color-{index:02d}" for index in range(1, 17))
COURSE_OVERRIDE_STRING_FIELDS = {
    "course_code": 64,
    "course_title": 255,
    "course_name": 255,
    "section_number": 64,
    "instructor": 255,
    "instructor_name": 255,
    "schedule_type": 64,
    "schedule_display": 255,
    "location": 255,
    "campus": 64,
    "campus_description": 255,
    "credit_hours": 64,
    "requirement_designation": 255,
    "course_description": 4000,
    "course_notes": 4000,
}
COURSE_OVERRIDE_FIELDS = set(COURSE_OVERRIDE_STRING_FIELDS) | {"meetings"}
COURSE_DAY_KEYS = {"Mon", "Tue", "Wed", "Thu", "Fri"}
MAX_LIVE_BATCH_SECTIONS = 20


def _current_user_id():
    return str(current_user.id)


def _is_emory_or_oxford_user():
    return is_emory_or_oxford_user(current_user)


def _require_emory_student():
    if not _is_emory_or_oxford_user():
        return jsonify({"error": "Courses are only available to Emory students."}), 403
    return None


def _payload_bool(payload, key, default=False):
    if key not in (payload or {}):
        return default
    return str(payload.get(key)).strip().lower() not in {"0", "false", "no", "off", ""}


def _get_section_by_id(section_id):
    parsed = parse_section_id(section_id)
    if not parsed:
        return None

    result = get_sections_by_ids([section_id], include_cancelled=True)
    sections = result.get("sections") or []
    if sections:
        return merge_snapshots_into_sections(sections)[0]

    fallback = get_sections_index(term=parsed["term"], include_cancelled=True)
    for section in fallback.get("sections", []):
        if section.get("id") == section_id:
            return merge_snapshots_into_sections([section])[0]
    return None


def _course_row_id(course):
    return course.get("$id") or course.get("id")


def _parse_course_overrides(course):
    raw = course.get("course_overrides_json")
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _sanitize_text(value, max_length):
    if value is None:
        return ""
    text = str(value).strip()
    return text[:max_length]


def _normalize_meeting_time(value):
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    if len(digits) == 3:
        digits = f"0{digits}"
    if len(digits) != 4:
        return None
    hour = int(digits[:2])
    minute = int(digits[2:])
    if hour > 24 or minute > 59:
        return None
    if hour == 24 and minute != 0:
        return None
    return digits


def _sanitize_meetings(value):
    if not isinstance(value, list):
        return []
    meetings = []
    for item in value[:20]:
        if not isinstance(item, dict):
            continue
        day = str(item.get("day") or "").strip()
        start = _normalize_meeting_time(item.get("start"))
        end = _normalize_meeting_time(item.get("end"))
        if day not in COURSE_DAY_KEYS or not start or not end:
            continue
        if int(end) <= int(start):
            continue
        meetings.append({"day": day, "start": start, "end": end})
    return meetings


def _sanitize_course_overrides(value):
    if not isinstance(value, dict):
        return {}
    overrides = {}
    for key, limit in COURSE_OVERRIDE_STRING_FIELDS.items():
        if key in value:
            overrides[key] = _sanitize_text(value.get(key), limit)
    if "meetings" in value:
        overrides["meetings"] = _sanitize_meetings(value.get("meetings"))
    return {key: val for key, val in overrides.items() if val not in (None, "")}


def _used_color_keys(courses, term=None, exclude_course_id=None):
    used = set()
    for course in courses:
        if term and course.get("term") != term:
            continue
        if exclude_course_id and _course_row_id(course) == exclude_course_id:
            continue
        color_key = course.get("color_key")
        if color_key in COURSE_COLOR_KEYS:
            used.add(color_key)
    return used


def _choose_course_color(courses, term, exclude_course_id=None):
    used = _used_color_keys(courses, term=term, exclude_course_id=exclude_course_id)
    available = [key for key in COURSE_COLOR_KEYS if key not in used]
    return random.choice(available or list(COURSE_COLOR_KEYS))


def _ensure_course_colors(user_id, courses):
    changed = []
    now = format_datetime(datetime.utcnow())
    for course in courses:
        if course.get("color_key") in COURSE_COLOR_KEYS:
            continue
        row_id = _course_row_id(course)
        if not row_id:
            continue
        color_key = _choose_course_color(courses, course.get("term"), exclude_course_id=row_id)
        try:
            updated = update_row_safe(
                COLLECTIONS["user_courses"],
                row_id,
                {"color_key": color_key, "updated_at": now},
            )
        except AppwriteException:
            logger.exception("Failed to assign course color")
            continue
        course.update(updated)
        changed.append(row_id)
    return changed


def _merge_course_overrides(serialized, overrides):
    for key, value in overrides.items():
        if key not in COURSE_OVERRIDE_FIELDS:
            continue
        if key == "course_name":
            serialized["course_name"] = value
            serialized["course_title"] = value
            continue
        if key == "course_title":
            serialized["course_title"] = value
            serialized["course_name"] = value
            continue
        if key == "instructor_name":
            serialized["instructor_name"] = value
            serialized["instructor"] = value
            continue
        if key == "instructor":
            serialized["instructor"] = value
            serialized["instructor_name"] = value
            continue
        serialized[key] = value
    return serialized


def _find_section_for_course(course, index_cache):
    term = course.get("term")
    subject = str(course.get("subject") or "").upper()
    catalog = str(course.get("catalog") or "")
    crn = str(course.get("crn") or "")
    section_number = str(course.get("section_number") or "")
    if not term or not subject or not catalog:
        return None

    if crn and section_number:
        section_id = build_section_id(term, subject, catalog, crn, section_number)
        section = _get_section_by_id(section_id)
        if section:
            return section

    if term not in index_cache:
        index_cache[term] = get_sections_index(term=term, include_cancelled=True).get("sections", [])

    best_match = None
    for section in index_cache[term]:
        if section.get("term") != term:
            continue
        if str(section.get("subject") or "").upper() != subject:
            continue
        if str(section.get("catalog_number") or "") != catalog:
            continue
        if crn and str(section.get("crn") or "") == crn:
            return section
        if section_number and str(section.get("section_number") or "") == section_number:
            return section
        if best_match is None:
            best_match = section
    return best_match


def _serialize_course(course, section=None):
    section = section or {}
    overrides = _parse_course_overrides(course)
    subject = course.get("subject") or section.get("subject")
    catalog = course.get("catalog") or section.get("catalog_number")
    course_code = section.get("course_code") or f"{subject} {catalog}".strip()
    serialized = {
        "id": _course_row_id(course),
        "section_id": section.get("id"),
        "term": course.get("term") or section.get("term"),
        "subject": subject,
        "catalog": catalog,
        "catalog_number": catalog,
        "crn": course.get("crn") or section.get("crn"),
        "section_number": course.get("section_number") or section.get("section_number"),
        "course_code": course_code,
        "course_title": course.get("course_name") or section.get("course_title"),
        "course_name": course.get("course_name") or section.get("course_title"),
        "instructor": course.get("instructor_name") or section.get("instructor"),
        "instructor_name": course.get("instructor_name") or section.get("instructor"),
        "instructors": section.get("instructors") or [],
        "location": section.get("location"),
        "schedule_type": section.get("schedule_type"),
        "schedule_display": section.get("schedule_display"),
        "meetings": section.get("meetings") or [],
        "date_range": section.get("date_range"),
        "credit_hours": section.get("credit_hours"),
        "requirement_designation": section.get("requirement_designation"),
        "requirements": section.get("requirements") or [],
        "course_description": section.get("course_description"),
        "course_notes": section.get("course_notes"),
        "enrollment_status": section.get("enrollment_status"),
        "enrollment_count": section.get("enrollment_count"),
        "seats_available": section.get("seats_available"),
        "enrollment_capacity": section.get("enrollment_capacity"),
        "waitlist_total": section.get("waitlist_total"),
        "waitlist_capacity": section.get("waitlist_capacity"),
        "live_updated_at": section.get("live_updated_at"),
        "live_snapshot_available": section.get("live_snapshot_available", False),
        "live_stale": section.get("live_stale", True),
        "is_cancelled": section.get("is_cancelled", False),
        "color_key": course.get("color_key"),
        "overrides": overrides,
        "updated_at": course.get("updated_at"),
    }
    return _merge_course_overrides(serialized, overrides)


def _track_for_section(user_id, section):
    return first_row(
        COLLECTIONS["course_seat_tracks"],
        [
            Query.equal("user_id", [user_id]),
            Query.equal("term", [section.get("term")]),
            Query.equal("subject", [section.get("subject")]),
            Query.equal("catalog", [section.get("catalog_number")]),
            Query.equal("crn", [section.get("crn") or ""]),
        ],
    )


def _serialize_track(track):
    return {
        "id": track.get("$id") or track.get("id"),
        "section_id": track.get("section_id"),
        "term": track.get("term"),
        "subject": track.get("subject"),
        "catalog": track.get("catalog"),
        "crn": track.get("crn"),
        "course_code": track.get("course_code"),
        "course_title": track.get("course_title"),
        "enabled": bool(track.get("enabled", False)),
        "last_status": track.get("last_status"),
        "last_seats_available": track.get("last_seats_available"),
        "last_checked_at": track.get("last_checked_at"),
        "last_notified_at": track.get("last_notified_at"),
        "interval_minutes": int(track.get("interval_minutes") or 30),
        "next_check_at": track.get("next_check_at"),
        "last_waitlist_total": track.get("last_waitlist_total"),
        "last_waitlist_capacity": track.get("last_waitlist_capacity"),
        "cooldown_until_closed": bool(track.get("cooldown_until_closed", False)),
    }


def _spring_tracking_closed_for_section(section):
    term = str((section or {}).get("term") or "").strip()
    return term.startswith("Spring_") and not spring_course_tracking_open()


def _merge_catalog_section(section):
    timestamp = format_datetime(datetime.utcnow())
    catalog_info = get_course_catalog_metadata(section.get("subject"), section.get("catalog_number"))
    enriched_section = dict(section)
    for key, value in catalog_info.items():
        if value not in (None, "", []) and enriched_section.get(key) in (None, "", []):
            enriched_section[key] = value
    return enriched_section, timestamp


def _merge_live_section(section, payload=None):
    payload = payload or {}
    force = _payload_bool(payload, "force", default=False)
    enriched_section, _ = _merge_catalog_section(section)
    try:
        return refresh_section_snapshot(enriched_section, force=force)
    except Exception:
        logger.exception("Failed to refresh live Atlas snapshot")
        enriched_section["live_snapshot_available"] = False
        enriched_section["live_stale"] = True
        return enriched_section, "Live Atlas status unavailable.", None, True


@courses_bp.route("/saved", methods=["GET"])
@login_required
def list_saved_courses():
    forbidden = _require_emory_student()
    if forbidden:
        return forbidden

    try:
        courses = list_rows_all(
            COLLECTIONS["user_courses"],
            [
                Query.equal("user_id", [_current_user_id()]),
                Query.order_asc("term"),
                Query.order_asc("subject"),
                Query.order_asc("catalog"),
            ],
        )
    except AppwriteException:
        logger.exception("Failed to list user courses")
        return jsonify({"error": "Unable to load courses."}), 500

    _ensure_course_colors(_current_user_id(), courses)
    index_cache = {}
    serialized = [
        _serialize_course(course, _find_section_for_course(course, index_cache))
        for course in courses
    ]
    return jsonify({"count": len(serialized), "courses": serialized})


@courses_bp.route("/saved", methods=["POST"])
@login_required
def add_saved_course():
    forbidden = _require_emory_student()
    if forbidden:
        return forbidden

    payload = request.get_json(silent=True) or {}
    section_id = payload.get("section_id")
    section = _get_section_by_id(section_id)
    if not section:
        return jsonify({"error": "Course section not found."}), 404
    if section.get("is_cancelled"):
        return jsonify({"error": "Cancelled sections cannot be added."}), 400

    user_id = _current_user_id()
    try:
        term_courses = list_rows_all(
            COLLECTIONS["user_courses"],
            [
                Query.equal("user_id", [user_id]),
                Query.equal("term", [section.get("term")]),
            ],
        )
    except AppwriteException:
        logger.exception("Failed to check existing course")
        return jsonify({"error": "Unable to add course."}), 500

    existing = next(
        (
            doc for doc in term_courses
            if str(doc.get("subject") or "").upper() == str(section.get("subject") or "").upper()
            and str(doc.get("catalog") or "") == str(section.get("catalog_number") or "")
            and str(doc.get("crn") or "") == str(section.get("crn") or "")
        ),
        None,
    )
    if existing:
        if existing.get("color_key") not in COURSE_COLOR_KEYS:
            _ensure_course_colors(user_id, term_courses)
        return jsonify({"status": "ok", "course": _serialize_course(existing, section)})

    try:
        entitlements = request_entitlements(current_user)
        check_limit(entitlements, "max_saved_courses", entitlements["usage"]["saved_courses"])
    except EntitlementLimitError as exc:
        return jsonify(exc.payload()), 403
    except EntitlementError:
        logger.exception("Failed to verify saved-course limits")
        return jsonify({"error": "Unable to verify your course limits right now.", "code": "tier_check_unavailable"}), 503

    now = format_datetime(datetime.utcnow())
    color_key = _choose_course_color(term_courses, section.get("term"))
    try:
        course = create_row_safe(
            COLLECTIONS["user_courses"],
            row_id=ID.unique(),
            data={
                "user_id": user_id,
                "term": section.get("term"),
                "subject": section.get("subject"),
                "catalog": section.get("catalog_number"),
                "crn": section.get("crn") or "",
                "course_name": section.get("course_title") or "",
                "section_number": section.get("section_number") or "",
                "instructor_name": section.get("instructor") or "",
                "source": "courses",
                "added_at": now,
                "color_key": color_key,
                "course_overrides_json": "{}",
                "updated_at": now,
            },
        )
    except AppwriteException:
        logger.exception("Failed to add course")
        return jsonify({"error": "Unable to add course."}), 500

    try:
        invites.record_activation(user_id, "course")
    except Exception:
        logger.exception("Failed to record invite activation for saved course")

    emit_creation_event(
        "Saved Course Added",
        actor=format_actor(current_user),
        target=section.get("course_code") or f"{section.get('subject')} {section.get('catalog_number')}".strip(),
        metadata={
            "page_context": "courses",
            "resource_type": "user_course",
            "resource_id": course.get("$id") or course.get("id"),
            "course_name": section.get("course_title"),
            "section_number": section.get("section_number"),
            "teacher": section.get("instructor"),
            "term": section.get("term"),
        },
        color="green",
    )
    return jsonify({"status": "ok", "course": _serialize_course(course, section)}), 201


@courses_bp.route("/saved/<course_id>", methods=["PATCH"])
@login_required
def update_saved_course(course_id):
    forbidden = _require_emory_student()
    if forbidden:
        return forbidden

    try:
        course = get_row_safe(COLLECTIONS["user_courses"], course_id)
    except AppwriteException as exc:
        if getattr(exc, "code", None) == 404:
            return jsonify({"error": "Course not found."}), 404
        logger.exception("Failed to load course")
        return jsonify({"error": "Unable to update course."}), 500

    user_id = _current_user_id()
    if course.get("user_id") != user_id:
        return jsonify({"error": "Course not found."}), 404

    payload = request.get_json(silent=True) or {}
    data = {"updated_at": format_datetime(datetime.utcnow())}

    if "color_key" in payload:
        color_key = str(payload.get("color_key") or "").strip()
        if color_key not in COURSE_COLOR_KEYS:
            return jsonify({"error": "Invalid course color."}), 400
        data["color_key"] = color_key

    if "overrides" in payload:
        overrides = _sanitize_course_overrides(payload.get("overrides") or {})
        data["course_overrides_json"] = json.dumps(overrides, separators=(",", ":"))

    if len(data) == 1:
        return jsonify({"error": "No changes provided."}), 400

    try:
        updated = update_row_safe(COLLECTIONS["user_courses"], course_id, data)
    except AppwriteException:
        logger.exception("Failed to update course")
        return jsonify({"error": "Unable to update course."}), 500

    section = _find_section_for_course(updated, {})
    return jsonify({"status": "ok", "course": _serialize_course(updated, section)})


@courses_bp.route("/saved/<course_id>", methods=["DELETE"])
@login_required
def remove_saved_course(course_id):
    forbidden = _require_emory_student()
    if forbidden:
        return forbidden

    try:
        course = get_row_safe(COLLECTIONS["user_courses"], course_id)
    except AppwriteException as exc:
        if getattr(exc, "code", None) == 404:
            return jsonify({"error": "Course not found."}), 404
        logger.exception("Failed to load course")
        return jsonify({"error": "Unable to remove course."}), 500

    if course.get("user_id") != _current_user_id():
        return jsonify({"error": "Course not found."}), 404

    try:
        delete_row_safe(COLLECTIONS["user_courses"], course_id)
    except AppwriteException:
        logger.exception("Failed to delete course")
        return jsonify({"error": "Unable to remove course."}), 500

    return jsonify({"status": "ok"})


@courses_bp.route("/tracks", methods=["GET"])
@login_required
def list_tracks():
    forbidden = _require_emory_student()
    if forbidden:
        return forbidden

    try:
        tracks = list_rows_all(
            COLLECTIONS["course_seat_tracks"],
            [Query.equal("user_id", [_current_user_id()])],
        )
    except AppwriteException:
        logger.exception("Failed to list course tracks")
        return jsonify({"error": "Unable to load course tracking."}), 500

    serialized = [_serialize_track(track) for track in tracks]
    try:
        entitlements = request_entitlements(current_user)
        allowed_intervals = entitlements["limits"].get(TRACK_INTERVALS_KEY) or [30]
        tier = {"key": entitlements["key"], "label": entitlements["label"]}
        limit = entitlements["limits"].get("max_seat_tracks")
        usage = entitlements["usage"].get("seat_tracks", 0)
    except EntitlementError:
        logger.exception("Failed to load course tracking entitlements")
        allowed_intervals, tier, limit, usage = [30], {"key": "free", "label": "Free"}, None, len([row for row in serialized if row["enabled"]])
    return jsonify({
        "count": len(serialized),
        "tracks": serialized,
        "tracks_by_section": {
            track["section_id"]: track
            for track in serialized
            if track.get("section_id")
        },
        "allowed_intervals_minutes": allowed_intervals,
        "tier": tier,
        "usage": usage,
        "limit": limit,
    })


@courses_bp.route("/tracks", methods=["POST"])
@login_required
def upsert_track():
    forbidden = _require_emory_student()
    if forbidden:
        return forbidden

    payload = request.get_json(silent=True) or {}
    section_id = payload.get("section_id")
    enabled = bool(payload.get("enabled", True))
    section = _get_section_by_id(section_id)
    if not section:
        return jsonify({"error": "Course section not found."}), 404

    if enabled and _spring_tracking_closed_for_section(section):
        return jsonify({
            "error": "Spring course tracking is not open yet.",
            "section": section,
            "spring_course_tracking_open": False,
        }), 403

    live_error = None
    last_updated_at = None
    live_stale = False
    if enabled:
        section, live_error, last_updated_at, live_stale = _merge_live_section(section, payload)
        if not is_section_trackable(section):
            return jsonify({
                "error": "This section is not closed/full right now.",
                "section": section,
                "live_error": live_error,
                "last_updated_at": last_updated_at,
                "live_stale": live_stale,
            }), 400

    user_id = _current_user_id()
    now = format_datetime(datetime.utcnow())
    data = {
        "user_id": user_id,
        "term": section.get("term"),
        "subject": section.get("subject"),
        "catalog": section.get("catalog_number"),
        "crn": section.get("crn") or "",
        "section_id": section.get("id") or section_id,
        "course_code": section.get("course_code") or "",
        "course_title": section.get("course_title") or "",
        "last_status": section.get("enrollment_status"),
        "last_seats_available": section.get("seats_available"),
        "enabled": enabled,
        "updated_at": now,
    }

    try:
        existing = _track_for_section(user_id, section)
    except AppwriteException:
        logger.exception("Failed to load existing course track")
        return jsonify({"error": "Unable to update course tracking."}), 500
    entitlements = None
    if enabled or "interval_minutes" in payload:
        try:
            entitlements = request_entitlements(current_user)
            allowed_intervals = entitlements["limits"].get(TRACK_INTERVALS_KEY) or [30]
            has_explicit_interval = payload.get("interval_minutes") not in (None, "")
            requested_interval = int(payload.get("interval_minutes") or (existing or {}).get("interval_minutes") or min(allowed_intervals))
            if not has_explicit_interval and requested_interval not in allowed_intervals:
                requested_interval = min(allowed_intervals)
            if requested_interval not in allowed_intervals:
                return jsonify({
                    "error": f"Your {entitlements['label']} tier supports {', '.join(str(value) for value in allowed_intervals)} minute checks.",
                    "code": "tier_interval",
                    "allowed_intervals_minutes": allowed_intervals,
                }), 403
            data["interval_minutes"] = requested_interval
            if enabled:
                data["next_check_at"] = now
            if enabled and (not existing or not existing.get("enabled", True)):
                check_limit(entitlements, "max_seat_tracks", entitlements["usage"]["seat_tracks"])
        except EntitlementLimitError as exc:
            return jsonify(exc.payload()), 403
        except EntitlementError:
            logger.exception("Failed to verify seat-track limits")
            return jsonify({"error": "Unable to verify your seat-track limits right now.", "code": "tier_check_unavailable"}), 503

    try:
        if existing:
            track = update_row_safe(
                COLLECTIONS["course_seat_tracks"],
                existing.get("$id") or existing.get("id"),
                data,
            )
        else:
            data["created_at"] = now
            track = create_row_safe(
                COLLECTIONS["course_seat_tracks"],
                row_id=ID.unique(),
                data=data,
            )
    except AppwriteException:
        logger.exception("Failed to update course track")
        return jsonify({"error": "Unable to update course tracking."}), 500

    emit_course_track_event(
        "Course Track Requested" if enabled else "Course Track Updated",
        actor=format_actor(current_user),
        target=section.get("course_code") or data["course_code"] or data["section_id"],
        metadata={
            "course_name": section.get("course_title") or data["course_title"],
            "teacher": section.get("instructor"),
            "section_number": section.get("section_number"),
            "seats_open": section.get("seats_available"),
            "enrollment_type": section.get("enrollment_status"),
            "request_source": "manual",
            "track_id": track.get("$id") or track.get("id"),
            "enabled": enabled,
            "was_existing": bool(existing),
        },
        color="green" if enabled and not existing else "gray",
    )
    return jsonify({
        "status": "ok",
        "track": _serialize_track(track),
        "section": section,
        "live_error": live_error,
        "last_updated_at": last_updated_at,
        "live_stale": live_stale,
    })


@courses_bp.route("/tracks/<track_id>", methods=["DELETE"])
@login_required
def delete_track(track_id):
    forbidden = _require_emory_student()
    if forbidden:
        return forbidden
    try:
        track = get_row_safe(COLLECTIONS["course_seat_tracks"], track_id)
    except AppwriteException:
        track = None
    if not track or str(track.get("user_id")) != _current_user_id():
        return jsonify({"error": "Tracked course not found."}), 404
    try:
        delete_row_safe(COLLECTIONS["course_seat_tracks"], track_id)
    except AppwriteException:
        logger.exception("Failed to remove course track")
        return jsonify({"error": "Unable to remove course tracking."}), 500
    return jsonify({"status": "ok"})


@courses_bp.route("/section-status", methods=["POST"])
@login_required
def section_status():
    forbidden = _require_emory_student()
    if forbidden:
        return forbidden

    payload = request.get_json(silent=True) or {}
    section_id = payload.get("section_id")
    section = _get_section_by_id(section_id)
    if not section:
        return jsonify({"error": "Course section not found."}), 404

    section, live_error, last_updated_at, live_stale = _merge_live_section(section, payload)
    return jsonify({
        "section": section,
        "trackable": is_section_trackable(section),
        "live_error": live_error,
        "last_updated_at": last_updated_at,
        "live_stale": live_stale,
    })


@courses_bp.route("/section-status/batch", methods=["POST"])
@login_required
def section_status_batch():
    forbidden = _require_emory_student()
    if forbidden:
        return forbidden

    payload = request.get_json(silent=True) or {}
    raw_section_ids = payload.get("section_ids") or []
    if not isinstance(raw_section_ids, list):
        return jsonify({"error": "section_ids must be a list"}), 400

    force = _payload_bool(payload, "force", default=False)
    section_ids = []
    seen = set()
    for raw_id in raw_section_ids:
        section_id = str(raw_id or "").strip()
        if not section_id or section_id in seen:
            continue
        seen.add(section_id)
        section_ids.append(section_id)
        if len(section_ids) >= MAX_LIVE_BATCH_SECTIONS:
            break

    sections_by_id = {}
    errors_by_id = {}
    stale_by_id = {}
    updated_by_id = {}
    for section_id in section_ids:
        section = _get_section_by_id(section_id)
        if not section:
            errors_by_id[section_id] = "Course section not found."
            continue
        section, live_error, last_updated_at, live_stale = _merge_live_section(section, {"force": force})
        sections_by_id[section_id] = section
        stale_by_id[section_id] = live_stale
        if last_updated_at:
            updated_by_id[section_id] = last_updated_at
        if live_error:
            errors_by_id[section_id] = live_error

    return jsonify({
        "status": "ok",
        "count": len(sections_by_id),
        "sections_by_id": sections_by_id,
        "errors_by_id": errors_by_id,
        "stale_by_id": stale_by_id,
        "updated_by_id": updated_by_id,
    })
