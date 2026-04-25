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
)


atlas_bp = Blueprint("atlas", __name__)


def _as_bool(value, default=True):
    if value is None:
        return default
    return str(value).strip().lower() not in {"0", "false", "no", "off"}


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
    include_cancelled = _as_bool(request.args.get("include_cancelled"), default=True)
    result = get_sections_index(term=term, include_cancelled=include_cancelled)
    if "error" in result:
        return jsonify(result), 400 if "Invalid" in result["error"] else 404
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
    return jsonify(result)