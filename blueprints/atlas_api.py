"""
blueprints/atlas_api.py

REST API for serving scraped Atlas course data.
Reads from atlas-data/{term}/{SUBJECT}/{catalog}.json on disk.

All endpoints require authentication via Flask-Login.
Atlas data is shared across all users (read-only, no per-user state).
"""

import os
import json
from functools import lru_cache

from flask import Blueprint, jsonify, request, current_app
from flask_login import login_required

atlas_bp = Blueprint("atlas", __name__)

# Relative to project root
ATLAS_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "atlas-data")
DEFAULT_TERM = "Fall_2026"
VALID_TERMS = {"Fall_2026", "Spring_2026"}


def _get_term():
    """Extract and validate the term query parameter."""
    term = request.args.get("term", DEFAULT_TERM)
    if term not in VALID_TERMS:
        return None
    return term


def _term_path(term):
    """Return the absolute path to a term's data directory."""
    return os.path.join(ATLAS_DATA_DIR, term)


@atlas_bp.route("/subjects")
@login_required
def list_subjects():
    """
    GET /api/atlas/subjects?term=Fall_2026

    Returns a sorted list of subject codes that have data for the given term.
    Example: ["BIOL", "CHEM", "CS", "MATH", ...]
    """
    term = _get_term()
    if not term:
        return jsonify({"error": "Invalid term"}), 400

    term_dir = _term_path(term)
    if not os.path.isdir(term_dir):
        return jsonify({"error": f"No data for term {term}"}), 404

    subjects = sorted([
        entry for entry in os.listdir(term_dir)
        if os.path.isdir(os.path.join(term_dir, entry))
        and not entry.startswith(".")
    ])

    return jsonify({"term": term, "subjects": subjects, "count": len(subjects)})


@atlas_bp.route("/courses/<subject>")
@login_required
def list_courses(subject):
    """
    GET /api/atlas/courses/CHEM?term=Fall_2026

    Returns a sorted list of catalog numbers for a subject.
    Example: ["150", "150L", "202"]
    """
    term = _get_term()
    if not term:
        return jsonify({"error": "Invalid term"}), 400

    # Sanitize subject input (uppercase, alphanumeric only)
    subject = subject.upper().strip()
    subject_dir = os.path.join(_term_path(term), subject)

    if not os.path.isdir(subject_dir):
        return jsonify({"error": f"Subject {subject} not found in {term}"}), 404

    courses = sorted([
        os.path.splitext(f)[0]
        for f in os.listdir(subject_dir)
        if f.endswith(".json") and not f.startswith(".")
    ])

    return jsonify({
        "term": term,
        "subject": subject,
        "courses": courses,
        "count": len(courses),
    })


@atlas_bp.route("/course/<subject>/<catalog>")
@login_required
def get_course(subject, catalog):
    """
    GET /api/atlas/course/CHEM/150?term=Fall_2026

    Returns the full JSON content of a specific course file.
    """
    term = _get_term()
    if not term:
        return jsonify({"error": "Invalid term"}), 400

    subject = subject.upper().strip()
    catalog = catalog.strip()
    filepath = os.path.join(_term_path(term), subject, f"{catalog}.json")

    if not os.path.isfile(filepath):
        return jsonify({
            "error": f"Course {subject} {catalog} not found in {term}"
        }), 404

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            course_data = json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        return jsonify({"error": f"Failed to read course data: {str(e)}"}), 500

    return jsonify(course_data)


@atlas_bp.route("/search")
@login_required
def search_courses():
    """
    GET /api/atlas/search?q=CHEM+150&term=Fall_2026

    Convenience endpoint that parses a query string like "CHEM 150"
    into subject + catalog and redirects to the specific course.
    Also supports partial matching across all subjects if query
    doesn't contain a space (e.g., "150" matches all courses numbered 150).
    """
    term = _get_term()
    if not term:
        return jsonify({"error": "Invalid term"}), 400

    query = request.args.get("q", "").strip()
    if not query:
        return jsonify({"error": "Missing query parameter 'q'"}), 400

    # Try to split into subject + catalog
    parts = query.upper().split()

    if len(parts) >= 2:
        subject = parts[0]
        catalog = parts[1]
        filepath = os.path.join(_term_path(term), subject, f"{catalog}.json")

        if os.path.isfile(filepath):
            with open(filepath, "r", encoding="utf-8") as f:
                return jsonify(json.load(f))
        else:
            return jsonify({
                "error": f"Course {subject} {catalog} not found in {term}"
            }), 404

    # Single-term query: search across all subjects for matching catalog numbers
    # or subject codes
    term_dir = _term_path(term)
    if not os.path.isdir(term_dir):
        return jsonify({"error": f"No data for term {term}"}), 404

    results = []
    search_term = parts[0]

    for subject_name in sorted(os.listdir(term_dir)):
        subject_path = os.path.join(term_dir, subject_name)
        if not os.path.isdir(subject_path) or subject_name.startswith("."):
            continue

        # Match subject code itself
        if subject_name == search_term:
            for fname in sorted(os.listdir(subject_path)):
                if fname.endswith(".json"):
                    catalog_num = os.path.splitext(fname)[0]
                    results.append({
                        "subject": subject_name,
                        "catalog": catalog_num,
                        "course_code": f"{subject_name} {catalog_num}",
                    })
            continue

        # Match catalog number within each subject
        for fname in sorted(os.listdir(subject_path)):
            if fname.endswith(".json"):
                catalog_num = os.path.splitext(fname)[0]
                if catalog_num == search_term or catalog_num.startswith(search_term):
                    results.append({
                        "subject": subject_name,
                        "catalog": catalog_num,
                        "course_code": f"{subject_name} {catalog_num}",
                    })

    return jsonify({
        "term": term,
        "query": query,
        "results": results,
        "count": len(results),
    })


@atlas_bp.route("/meta")
@login_required
def get_meta():
    """
    GET /api/atlas/meta

    Returns the scrape metadata from _meta.json.
    """
    meta_path = os.path.join(ATLAS_DATA_DIR, "_meta.json")

    if not os.path.isfile(meta_path):
        return jsonify({"error": "No scrape metadata found"}), 404

    with open(meta_path, "r", encoding="utf-8") as f:
        return jsonify(json.load(f))