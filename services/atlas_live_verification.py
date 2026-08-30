"""
services/atlas_live_verification.py

Live Atlas verification for a caller-supplied list of section IDs. Fetches
fresh results from Atlas (never stale snapshots), verifies each requested
section against the live subject search, optionally enriches Open/Waitlist
sections with the FOSE details endpoint, and persists merged rows into the
course_section_live_snapshots table.

Outbound calls are paced process-wide (at least CALL_SPACING_SECONDS between
the starts of all outbound Atlas calls in this process), with no retries, and
a thread-safe singleflight per (term, subject) so overlapping verifications
share only an in-progress fetch. Nothing is cached after completion.

Each request is bounded by an overall monotonic deadline
(DEFAULT_DEADLINE_SECONDS). Before every subject or detail Atlas call the
remaining time is recomputed after process-wide pacing so call spacing counts
against the deadline, and min(per-call timeout, remaining) is passed to the
client. Singleflight waiters honor the same deadline with a timed event wait.
Once the deadline expires, every not-yet-attempted requested ID is marked with
an honest deadline error instead of falling back to stale data.

Detail enrichment failures and ineligible detail requests are reported in a
separate detail_errors_by_id map; errors_by_id stays authoritative for
verification state so a failed detail lookup never un-verifies a section.
Overflow past a request cap is recorded as a single bounded sentinel entry
rather than one error per excess ID.
"""

import logging
import re
import threading
import time

from services import course_live_snapshots
from services.atlas_client import (
    _normalize_enrollment_status,
    fetch_atlas_section_details,
    fetch_live_subject_sections,
    merge_section_with_details,
    parse_section_id,
)

MAX_SECTION_IDS = 120
MAX_GROUPS = 24
MAX_DETAIL_IDS = 12
CALL_SPACING_SECONDS = 0.2
DEFAULT_DEADLINE_SECONDS = 30

OPEN_STATUSES = {"Open", "Waitlist"}
CLOSED_STATUS = "Closed"

# Bounded overflow bookkeeping: one sentinel entry per exceeded cap instead of
# one error per excess ID. Sentinel keys can never collide with real section
# IDs, which always contain "|" separators.
SECTION_IDS_OVERFLOW_KEY = "__section_ids_overflow__"
GROUPS_OVERFLOW_KEY = "__groups_overflow__"
DETAIL_IDS_OVERFLOW_KEY = "__detail_ids_overflow__"
SECTION_IDS_OVERFLOW_MESSAGE = (
    f"Too many section IDs requested (max {MAX_SECTION_IDS}); excess IDs were ignored"
)
GROUPS_OVERFLOW_MESSAGE = (
    f"Too many (term, subject) groups requested (max {MAX_GROUPS}); excess groups were ignored"
)
DETAIL_IDS_OVERFLOW_MESSAGE = (
    f"Too many detail IDs requested (max {MAX_DETAIL_IDS}); excess IDs were ignored"
)

DEADLINE_SECTION_MESSAGE = (
    "Live Atlas verification deadline exceeded before this section was checked"
)
DEADLINE_DETAILS_MESSAGE = (
    "Live Atlas verification deadline exceeded before details lookup"
)

DETAIL_INVALID_MESSAGE = "Invalid section id for details lookup"
DETAIL_UNVERIFIED_MESSAGE = "Section was not verified, so details were not fetched"
DETAIL_STATUS_MESSAGE = "Details are only available for open or waitlisted sections"

logger = logging.getLogger(__name__)

_inflight_lock = threading.Lock()
_inflight = {}

_pacing_lock = threading.Lock()
_last_atlas_call_monotonic = None


def _pace_atlas_call():
    """
    Block until at least CALL_SPACING_SECONDS separate the starts of
    consecutive outbound Atlas calls in this process, then register the start
    of the caller's call. Never hold _inflight_lock here.
    """
    global _last_atlas_call_monotonic
    while True:
        with _pacing_lock:
            now = time.monotonic()
            if (
                _last_atlas_call_monotonic is None
                or now - _last_atlas_call_monotonic >= CALL_SPACING_SECONDS
            ):
                _last_atlas_call_monotonic = now
                return
            wait_seconds = CALL_SPACING_SECONDS - (now - _last_atlas_call_monotonic)
        time.sleep(wait_seconds)


def _singleflight_subject_fetch(term, subject, timeout, deadline):
    """
    Fetch live subject sections, sharing only in-progress calls per group.

    Returns (result, owned_call). Fetch exceptions are captured as an error
    dict so both owner and waiters get a published result and no exception
    escapes to abort other groups. Pacing and the outbound call are performed
    only by the singleflight owner, which recomputes the remaining overall
    deadline after pacing so call spacing counts against it. Waiters honor
    the caller's remaining deadline with a timed event wait and get an honest
    deadline error instead of blocking indefinitely or using stale data. Only
    the owner removes the in-flight entry and publishes the shared event.
    """
    key = (str(term), str(subject or "").upper())
    with _inflight_lock:
        entry = _inflight.get(key)
        if entry is None:
            entry = {"event": threading.Event(), "result": None}
            _inflight[key] = entry
            is_owner = True
        else:
            is_owner = False

    if not is_owner:
        remaining = deadline - time.monotonic()
        if not entry["event"].wait(timeout=max(remaining, 0)):
            # The caller's overall deadline expired while waiting. Use a
            # result published in the interim when present; otherwise report
            # the deadline instead of blocking forever.
            result = entry["result"]
            if result is None:
                result = {"error": DEADLINE_SECTION_MESSAGE}
            return result, False
        return entry["result"], False

    try:
        _pace_atlas_call()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            result = {"error": DEADLINE_SECTION_MESSAGE}
        else:
            try:
                result = fetch_live_subject_sections(
                    term, subject, timeout=min(timeout, remaining)
                )
            except Exception as exc:
                result = {"error": f"Live Atlas request failed: {exc}"}
        entry["result"] = result
        return result, True
    finally:
        with _inflight_lock:
            _inflight.pop(key, None)
        entry["event"].set()


def _normalize_input_ids(section_ids):
    if isinstance(section_ids, str):
        raw_items = [part for part in re.split(r"[,\s]+", section_ids) if part]
    elif isinstance(section_ids, (list, tuple)):
        raw_items = [str(item or "").strip() for item in section_ids]
    else:
        raise ValueError("section_ids must be a list or a delimited string")

    seen = set()
    ordered = []
    for item in raw_items:
        if item and item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered


def _verify_seats_for_status(row):
    status = str(row.get("enrollment_status") or "")
    if status == CLOSED_STATUS:
        row["seats_available"] = 0
    elif status in OPEN_STATUSES:
        row["seats_available"] = None
    return row


def _build_candidate_index(sections):
    by_crn = {}
    by_code = {}
    for row in sections:
        crn = str(row.get("crn") or "").strip()
        if crn:
            by_crn.setdefault(crn, row)
        code_key = (
            str(row.get("catalog_number") or "").strip().upper(),
            str(row.get("section_number") or "").strip(),
        )
        if code_key[0] or code_key[1]:
            by_code.setdefault(code_key, row)
    return by_crn, by_code


def _match_candidate(parsed, by_crn, by_code):
    crn = str(parsed.get("crn") or "").strip()
    if crn:
        return by_crn.get(crn)
    return by_code.get(
        (
            str(parsed.get("catalog") or "").strip().upper(),
            str(parsed.get("section_number") or "").strip(),
        )
    )


def verify_sections_by_ids(
    section_ids,
    detail_ids=None,
    *,
    timeout=10,
    deadline_seconds=DEFAULT_DEADLINE_SECONDS,
):
    """
    Verify live Atlas enrollment state for the given section IDs.

    Returns a dict with verified_by_id, details_by_id, detail_errors_by_id,
    errors_by_id, groups, and limits. Verification failures are recorded
    per-ID in errors_by_id; detail enrichment failures and ineligible detail
    requests are recorded in detail_errors_by_id so verified_by_id remains the
    sole authority on verified state. Stale snapshots are never used as a
    fallback.

    The whole request is bounded by deadline_seconds on a monotonic clock.
    Before each subject or detail Atlas call the remaining time is recomputed
    after pacing and min(timeout, remaining) is passed to the client, so call
    spacing counts against the deadline; once the deadline expires, every
    not-yet-attempted requested ID is marked with an explicit deadline error
    instead of a stale value. Request caps are enforced with a single bounded
    sentinel error per exceeded cap, never one error per excess ID.
    """
    ordered_ids = _normalize_input_ids(section_ids)
    ordered_detail_ids = _normalize_input_ids(detail_ids or [])

    verified_by_id = {}
    details_by_id = {}
    errors_by_id = {}
    detail_errors_by_id = {}
    groups = []

    if len(ordered_ids) > MAX_SECTION_IDS:
        ordered_ids = ordered_ids[:MAX_SECTION_IDS]
        errors_by_id[SECTION_IDS_OVERFLOW_KEY] = SECTION_IDS_OVERFLOW_MESSAGE

    deadline = time.monotonic() + deadline_seconds
    deadline_exceeded = False

    parsed_by_id = {}
    group_order = []
    groups_by_key = {}
    for section_id in ordered_ids:
        parsed = parse_section_id(section_id)
        if not parsed or not parsed.get("term") or not parsed.get("subject"):
            errors_by_id[section_id] = "Invalid section id"
            continue
        parsed_by_id[section_id] = parsed
        key = (parsed["term"], parsed["subject"])
        if key not in groups_by_key:
            groups_by_key[key] = []
            group_order.append(key)
        groups_by_key[key].append(section_id)

    if len(group_order) > MAX_GROUPS:
        errors_by_id[GROUPS_OVERFLOW_KEY] = GROUPS_OVERFLOW_MESSAGE

    for term, subject in group_order[:MAX_GROUPS]:
        group_ids = groups_by_key[(term, subject)]
        group_entry = {
            "term": term,
            "subject": subject,
            "section_ids": list(group_ids),
            "requested": len(group_ids),
            "matched": 0,
            "ok": False,
            "error": None,
        }
        groups.append(group_entry)

        if not deadline_exceeded:
            remaining = deadline - time.monotonic()
            deadline_exceeded = remaining <= 0
        if deadline_exceeded:
            group_entry["error"] = DEADLINE_SECTION_MESSAGE
            for section_id in group_ids:
                errors_by_id[section_id] = DEADLINE_SECTION_MESSAGE
            continue

        result, owned_call = _singleflight_subject_fetch(
            term, subject, timeout, deadline
        )
        if not isinstance(result, dict) or result.get("error"):
            error = (result or {}).get("error") if isinstance(result, dict) else "Live Atlas request failed"
            group_entry["error"] = error
            for section_id in group_ids:
                errors_by_id[section_id] = error
            continue

        by_crn, by_code = _build_candidate_index(result.get("sections") or [])
        matched = 0
        for section_id in group_ids:
            parsed = parsed_by_id[section_id]
            candidate = _match_candidate(parsed, by_crn, by_code)
            if candidate is None:
                errors_by_id[section_id] = "Section not found in live Atlas results"
                continue
            verified_by_id[section_id] = _verify_seats_for_status(dict(candidate))
            matched += 1
        group_entry["matched"] = matched
        group_entry["ok"] = True

    detail_candidates = []
    detail_overflow = False
    for section_id in ordered_detail_ids:
        if len(detail_candidates) >= MAX_DETAIL_IDS:
            detail_overflow = True
            break
        if not parse_section_id(section_id):
            detail_errors_by_id[section_id] = DETAIL_INVALID_MESSAGE
            continue
        verified = verified_by_id.get(section_id)
        if verified is None:
            detail_errors_by_id[section_id] = DETAIL_UNVERIFIED_MESSAGE
            continue
        if verified.get("enrollment_status") not in OPEN_STATUSES:
            detail_errors_by_id[section_id] = DETAIL_STATUS_MESSAGE
            continue
        detail_candidates.append(section_id)
    if detail_overflow:
        detail_errors_by_id[DETAIL_IDS_OVERFLOW_KEY] = DETAIL_IDS_OVERFLOW_MESSAGE

    detail_deadline_exceeded = False
    for section_id in detail_candidates:
        verified = verified_by_id[section_id]
        atlas_key = str(verified.get("atlas_key") or "").strip()
        if not atlas_key:
            detail_errors_by_id[section_id] = "Missing atlas key for details lookup"
            continue
        if not detail_deadline_exceeded:
            remaining = deadline - time.monotonic()
            detail_deadline_exceeded = remaining <= 0
        if detail_deadline_exceeded:
            detail_errors_by_id[section_id] = DEADLINE_DETAILS_MESSAGE
            continue
        _pace_atlas_call()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            detail_deadline_exceeded = True
            detail_errors_by_id[section_id] = DEADLINE_DETAILS_MESSAGE
            continue
        try:
            payload = fetch_atlas_section_details(
                verified.get("term"), atlas_key, timeout=min(timeout, remaining)
            )
        except Exception as exc:  # per-ID failure only
            detail_errors_by_id[section_id] = f"Live Atlas details request failed: {exc}"
            continue
        if not isinstance(payload, dict):
            detail_errors_by_id[section_id] = "Live Atlas details returned invalid data"
            continue
        error = payload.get("error") or payload.get("fatal")
        if error:
            detail_errors_by_id[section_id] = error
            continue
        try:
            merged = merge_section_with_details(verified, payload)
            merged["enrollment_status"] = _normalize_enrollment_status(merged.get("enrollment_status"))
            if merged["enrollment_status"] == CLOSED_STATUS:
                merged["seats_available"] = 0
        except Exception as exc:  # per-ID failure only
            detail_errors_by_id[section_id] = f"Failed to merge details: {exc}"
            continue
        try:
            course_live_snapshots.upsert_snapshot(merged)
        except Exception:
            # Persistence is best effort: the merged row is still valid live
            # data for the caller even if the snapshot write fails.
            logger.exception("Failed to persist live Atlas snapshot for %s", section_id)
        details_by_id[section_id] = merged

    return {
        "verified_by_id": verified_by_id,
        "details_by_id": details_by_id,
        "detail_errors_by_id": detail_errors_by_id,
        "errors_by_id": errors_by_id,
        "groups": groups,
        "limits": {
            "max_section_ids": MAX_SECTION_IDS,
            "max_groups": MAX_GROUPS,
            "max_details": MAX_DETAIL_IDS,
        },
    }
