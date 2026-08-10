"""
blueprints/atlas_api.py

Atlas course lookup endpoints.
"""

from flask import Blueprint, jsonify, request

from services.atlas_client import (
    get_subjects,
    search_courses,
    get_terms,
    get_sections_index,
    get_sections_by_ids,
    get_starred_general_ed_requirements,
)
from services.course_live_snapshots import merge_snapshots_into_sections


atlas_bp = Blueprint("atlas", __name__)


def _as_bool(value, default=True):
    if value is None:
        return default
    return str(value).strip().lower() not in {"0", "false", "no", "off"}


def _split_days(value):
    return [
        item.strip()
        for item in str(value or "").split(",")
        if item.strip()
    ]


def _split_statuses(value):
    return [
        item.strip()
        for item in str(value or "").split(",")
        if item.strip()
    ]


def _int_arg(name, default=None):
    value = request.args.get(name)
    if value in (None, ""):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _filter_live_sections_by_status(result, statuses, limit=None, offset=0):
    normalized_statuses = {str(status).strip().lower() for status in statuses if str(status).strip()}
    sections = result.get("sections") or []
    if normalized_statuses:
        sections = [
            section for section in sections
            if str(section.get("enrollment_status") or "").strip().lower() in normalized_statuses
        ]

    total = len(sections)
    safe_offset = max(0, int(offset or 0))
    safe_limit = min(max(1, int(limit)), 500) if limit is not None else total
    page = sections[safe_offset:safe_offset + safe_limit]
    return {
        **result,
        "offset": safe_offset,
        "limit": safe_limit,
        "total": total,
        "count": len(page),
        "sections": page,
    }


@atlas_bp.route("/terms")
def list_terms():
    result = get_terms()
    return jsonify(result)


@atlas_bp.route("/subjects")
def list_subjects():
    term = request.args.get("term", "Fall_2026")
    result = get_subjects(term)
    if "error" in result:
        return jsonify(result), 400 if "Invalid" in result["error"] else 404
    return jsonify(result)


@atlas_bp.route("/general-ed-requirements")
def list_general_ed_requirements():
    return jsonify({
        "requirements": get_starred_general_ed_requirements(),
        "count": len(get_starred_general_ed_requirements()),
    })


@atlas_bp.route("/search")
def search():
    query = request.args.get("query", "")
    term = request.args.get("term", "Fall_2026")
    result = search_courses(query, term)
    if "error" in result:
        return jsonify(result), 400 if "Invalid" in result["error"] or "Missing" in result["error"] else 404
    return jsonify(result)


@atlas_bp.route("/sections")
def list_sections():
    term = request.args.get("term")
    query = request.args.get("q") or request.args.get("query")
    include_cancelled = _as_bool(request.args.get("include_cancelled"), default=True)
    statuses = _split_statuses(request.args.get("statuses") or request.args.get("status"))
    limit = _int_arg("limit")
    offset = _int_arg("offset", 0)
    result = get_sections_index(
        term=term,
        include_cancelled=include_cancelled,
        query=query,
        # Availability is effective live data. Apply its limit only after the
        # cached catalog rows have been merged with current snapshots below.
        limit=None if statuses else limit,
        offset=0 if statuses else offset,
        days=_split_days(request.args.get("days")),
        time_start=request.args.get("time_start"),
        time_end=request.args.get("time_end"),
        campus=request.args.get("campus"),
        requirement=request.args.get("requirement") or request.args.get("ger"),
        statuses=None,
    )
    if "error" in result:
        return jsonify(result), 400 if "Invalid" in result["error"] else 404
    result = {
        **result,
        "sections": merge_snapshots_into_sections(result.get("sections") or []),
    }
    if statuses:
        result = _filter_live_sections_by_status(result, statuses, limit=limit, offset=offset)
    return jsonify(result)


@atlas_bp.route("/sections/by-id", methods=["POST"])
def list_sections_by_id():
    payload = request.get_json(silent=True) or {}
    section_ids = payload.get("section_ids") or []
    include_cancelled = _as_bool(payload.get("include_cancelled"), default=True)

    if not isinstance(section_ids, list):
        return jsonify({"error": "section_ids must be a list"}), 400

    result = get_sections_by_ids(section_ids=section_ids, include_cancelled=include_cancelled)
    if "error" in result:
        return jsonify(result), 400
    result = {
        **result,
        "sections": merge_snapshots_into_sections(result.get("sections") or []),
    }
    return jsonify(result)
