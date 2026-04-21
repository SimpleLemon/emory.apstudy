"""
services/atlas_client.py

Reads scraped Atlas course data from disk and returns structured dicts.
Data lives in atlas-data/{term}/{SUBJECT}/{catalog}.json, written by
the one-time bulk scraper (atlasMainScraper.js) [3] [4].

All functions return plain Python dicts/lists suitable for JSON serialization.
The blueprint layer (atlas_api.py) handles Flask response formatting.
"""

import os
import json
from datetime import datetime, timedelta
from threading import Lock

# Resolve atlas-data/ relative to the project root, not this file's directory.
# This file lives in services/, so go up one level.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ATLAS_DATA_DIR = os.path.join(_PROJECT_ROOT, "atlas-data")

DEFAULT_TERM = "Fall_2026"
VALID_TERMS = {"Fall_2026", "Spring_2026"}

# ── In-memory cache ──────────────────────────────────────────────────────────
# Directory listings change only on re-scrape, so caching them avoids
# repeated os.listdir calls. Cache is invalidated after TTL expires
# or manually via invalidate_cache().

_cache = {}
_cache_lock = Lock()
_CACHE_TTL = timedelta(minutes=30)


def _cache_get(key):
    """Return cached value if it exists and has not expired, else None."""
    with _cache_lock:
        entry = _cache.get(key)
        if entry and datetime.utcnow() - entry["ts"] < _CACHE_TTL:
            return entry["data"]
        return None


def _cache_set(key, data):
    """Store a value in the cache with a current timestamp."""
    with _cache_lock:
        _cache[key] = {"data": data, "ts": datetime.utcnow()}


def invalidate_cache():
    """Clear the entire cache. Call after a re-scrape."""
    with _cache_lock:
        _cache.clear()


# ── Path helpers ─────────────────────────────────────────────────────────────

def _term_path(term):
    """Return the absolute path to a term's data directory."""
    return os.path.join(ATLAS_DATA_DIR, term)


def _validate_term(term):
    """
    Validate and return the term string.
    Returns None if the term is not in the allowed set.
    """
    if term not in VALID_TERMS:
        return None
    return term


def _safe_path(base, *parts):
    """
    Join path components and verify the result stays within the base directory.
    Returns the resolved path, or None if traversal is detected.
    """
    joined = os.path.join(base, *parts)
    resolved = os.path.realpath(joined)
    if not resolved.startswith(os.path.realpath(base)):
        return None
    return resolved


# ── Public API ───────────────────────────────────────────────────────────────

def get_subjects(term=None):
    """
    Return a sorted list of subject codes that have data for the given term.

    Args:
        term: "Fall_2026" or "Spring_2026". Defaults to DEFAULT_TERM.

    Returns:
        dict with keys: term, subjects (list of str), count (int)
        or dict with key: error (str) if term is invalid or missing.
    """
    term = _validate_term(term or DEFAULT_TERM)
    if not term:
        return {"error": "Invalid term"}

    cache_key = f"subjects:{term}"
    cached = _cache_get(cache_key)
    if cached:
        return cached

    term_dir = _term_path(term)
    if not os.path.isdir(term_dir):
        return {"error": f"No data for term {term}"}

    subjects = sorted([
        entry for entry in os.listdir(term_dir)
        if os.path.isdir(os.path.join(term_dir, entry))
        and not entry.startswith(".")
    ])

    result = {"term": term, "subjects": subjects, "count": len(subjects)}
    _cache_set(cache_key, result)
    return result


def get_courses(subject, term=None):
    """
    Return a sorted list of catalog numbers for a subject in a given term.

    Args:
        subject: e.g., "CHEM", "BIOL". Case-insensitive (uppercased internally).
        term: "Fall_2026" or "Spring_2026". Defaults to DEFAULT_TERM.

    Returns:
        dict with keys: term, subject, courses (list of str), count (int)
        or dict with key: error (str).
    """
    term = _validate_term(term or DEFAULT_TERM)
    if not term:
        return {"error": "Invalid term"}

    subject = subject.upper().strip()
    cache_key = f"courses:{term}:{subject}"
    cached = _cache_get(cache_key)
    if cached:
        return cached

    subject_dir = _safe_path(_term_path(term), subject)
    if not subject_dir or not os.path.isdir(subject_dir):
        return {"error": f"Subject {subject} not found in {term}"}

    courses = sorted([
        os.path.splitext(f)[0]
        for f in os.listdir(subject_dir)
        if f.endswith(".json") and not f.startswith(".")
    ])

    result = {
        "term": term,
        "subject": subject,
        "courses": courses,
        "count": len(courses),
    }
    _cache_set(cache_key, result)
    return result


def get_course(subject, catalog, term=None):
    """
    Return the full JSON content of a specific course file.

    Args:
        subject: e.g., "CHEM". Case-insensitive.
        catalog: e.g., "150", "141L". Used as-is for filename lookup.
        term: "Fall_2026" or "Spring_2026". Defaults to DEFAULT_TERM.

    Returns:
        dict with the full course data from the JSON file,
        or dict with key: error (str).
    """
    term = _validate_term(term or DEFAULT_TERM)
    if not term:
        return {"error": "Invalid term"}

    subject = subject.upper().strip()
    catalog = catalog.strip()

    filepath = _safe_path(_term_path(term), subject, f"{catalog}.json")
    if not filepath or not os.path.isfile(filepath):
        return {"error": f"Course {subject} {catalog} not found in {term}"}

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        return {"error": f"Failed to read course data: {str(e)}"}


def search_courses(query, term=None):
    """
    Search for courses matching a query string.

    If the query contains a space (e.g., "CHEM 150"), it attempts a direct
    subject + catalog lookup first. If no space, it searches across all
    subjects for matching subject codes or catalog numbers.

    Args:
        query: search string, e.g., "CHEM 150", "BIOL", "141"
        term: "Fall_2026" or "Spring_2026". Defaults to DEFAULT_TERM.

    Returns:
        dict with keys: term, query, results (list of match dicts), count (int)
        or the full course dict if an exact match is found,
        or dict with key: error (str).
    """
    term = _validate_term(term or DEFAULT_TERM)
    if not term:
        return {"error": "Invalid term"}

    query = query.strip()
    if not query:
        return {"error": "Missing query"}

    parts = query.upper().split()

    # Exact match attempt: "CHEM 150"
    if len(parts) >= 2:
        subject = parts[0]
        catalog = parts[1]
        result = get_course(subject, catalog, term)
        if "error" not in result:
            return result
        return {"error": f"Course {subject} {catalog} not found in {term}"}

    # Single-term search: match against subject codes and catalog numbers
    term_dir = _term_path(term)
    if not os.path.isdir(term_dir):
        return {"error": f"No data for term {term}"}

    search_term = parts[0]
    results = []

    for subject_name in sorted(os.listdir(term_dir)):
        subject_path = os.path.join(term_dir, subject_name)
        if not os.path.isdir(subject_path) or subject_name.startswith("."):
            continue

        # Match subject code itself (e.g., query "CHEM" matches all CHEM courses)
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

    return {
        "term": term,
        "query": query,
        "results": results,
        "count": len(results),
    }


def get_meta():
    """
    Return the scrape metadata from _meta.json.

    Returns:
        dict with scrape timestamps, term statistics, and error lists,
        or dict with key: error (str) if no metadata file exists.
    """
    meta_path = os.path.join(ATLAS_DATA_DIR, "_meta.json")
    if not os.path.isfile(meta_path):
        return {"error": "No scrape metadata found"}

    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        return {"error": f"Failed to read metadata: {str(e)}"}