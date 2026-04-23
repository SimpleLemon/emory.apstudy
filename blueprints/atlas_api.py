"""
blueprints/atlas_api.py

Atlas course lookup endpoints.
"""

from flask import Blueprint, jsonify, request

from services.atlas_client import get_subjects, search_courses


atlas_bp = Blueprint("atlas", __name__)


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