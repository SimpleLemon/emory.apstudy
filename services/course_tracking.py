import hashlib
import logging
import os
import re
from datetime import datetime, timedelta, timezone

from appwrite.exception import AppwriteException
from appwrite.query import Query
from appwrite.services.messaging import Messaging

from appwrite_client import COLLECTIONS, client as appwrite_client
from appwrite_helpers import format_datetime, list_rows_all, update_row_safe
from services.atlas_client import fetch_live_section_status
from services.course_tracking_email import (
    build_nest_courses_detail_url,
    build_open_seat_html,
    build_open_seat_subject,
)
from services.discord_audit import emit_course_track_event, update_course_tracks_channel_topic
from services.redaction import SECRET_TEXT_RE
from services import notifications


logger = logging.getLogger(__name__)
_last_poll_metadata = None


def _now_utc():
    return datetime.now(timezone.utc)


def _section_open_for_notification(section):
    status = str(section.get("enrollment_status") or "").strip().lower()
    seats_available = section.get("seats_available")
    return _open_from_values(status, seats_available) or _waitlist_available(
        section.get("waitlist_total"), section.get("waitlist_capacity")
    )


def _waitlist_available(total, capacity):
    total = _normalize_seats(total)
    capacity = _normalize_seats(capacity)
    return total is not None and capacity is not None and capacity > total


def _open_from_values(status, seats_available):
    status = str(status or "").strip().lower()
    try:
        seats_available = int(seats_available) if seats_available is not None else None
    except (TypeError, ValueError):
        seats_available = None
    return status == "open" or (seats_available is not None and seats_available > 0)


def _normalize_status(value):
    return str(value or "").strip().lower()


def _normalize_seats(value):
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        match = re.search(r"-?\d+", str(value))
        return int(match.group(0)) if match else None


def _track_result_changed(track, section):
    old_status = _normalize_status(track.get("last_status"))
    new_status = _normalize_status(section.get("enrollment_status"))
    old_seats = _normalize_seats(track.get("last_seats_available"))
    new_seats = _normalize_seats(section.get("seats_available"))
    return (
        old_status != new_status
        or old_seats != new_seats
        or _normalize_seats(track.get("last_waitlist_total")) != _normalize_seats(section.get("waitlist_total"))
        or _normalize_seats(track.get("last_waitlist_capacity")) != _normalize_seats(section.get("waitlist_capacity"))
    )


def _track_was_open(track):
    return _open_from_values(track.get("last_status"), track.get("last_seats_available")) or _waitlist_available(
        track.get("last_waitlist_total"), track.get("last_waitlist_capacity")
    )


def _parse_datetime(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _track_due(track, now):
    next_check = _parse_datetime(track.get("next_check_at"))
    if next_check:
        return next_check <= now
    last_checked = _parse_datetime(track.get("last_checked_at"))
    if not last_checked:
        return True
    minutes = 180 if track.get("cooldown_until_closed") else int(track.get("interval_minutes") or 30)
    return last_checked + timedelta(minutes=minutes) <= now


def _send_open_email(track, section):
    messaging = Messaging(appwrite_client)
    base_url = os.environ.get("APP_BASE_URL", "https://nest.apstudy.org").rstrip("/")
    course_code = section.get("course_code") or track.get("course_code") or "Tracked class"
    section_id = section.get("id") or track.get("section_id") or ""
    subject = build_open_seat_subject(
        course_code,
        section.get("seats_available"),
        _waitlist_available(section.get("waitlist_total"), section.get("waitlist_capacity")),
    )
    content = build_open_seat_html(
        section,
        base_url=base_url,
        nest_details_url=build_nest_courses_detail_url(base_url, section_id),
    )
    message_id = _open_notification_id(track)
    try:
        messaging.create_email(
            message_id=message_id,
            subject=subject,
            content=content,
            users=[track.get("user_id")],
            html=True,
        )
    except AppwriteException as exc:
        if exc.code != 409:
            raise
        # A retry after an ambiguous timeout may find that Appwrite accepted the
        # first request. Verify that the deterministic message exists before
        # treating the conflict as a successful, already-enqueued delivery.
        messaging.get_message(message_id)


def _open_notification_id(track):
    transition_key = "|".join([
        str(track.get("$id") or track.get("id") or ""),
        str(track.get("user_id") or ""),
        str(track.get("section_id") or ""),
        str(track.get("last_status") or ""),
        str(track.get("last_seats_available") or ""),
        str(track.get("updated_at") or track.get("last_checked_at") or ""),
    ])
    return f"seat-{hashlib.sha256(transition_key.encode('utf-8')).hexdigest()[:31]}"


def _track_group_key(track):
    section_id = str(track.get("section_id") or "").strip()
    if section_id:
        return f"section:{section_id}"
    return "|".join([
        "course",
        str(track.get("term") or "").strip(),
        str(track.get("subject") or "").strip().upper(),
        str(track.get("catalog") or "").strip(),
        str(track.get("crn") or "").strip(),
    ])


def _group_metadata(tracks, section=None, error=None):
    representative = tracks[0] if tracks else {}
    section = section or {}
    user_ids = {
        str(track.get("user_id"))
        for track in tracks
        if track.get("user_id")
    }
    metadata = {
        "course_name": section.get("course_title") or representative.get("course_title"),
        "term": section.get("term") or representative.get("term"),
        "crn": section.get("crn") or representative.get("crn"),
        "section_number": section.get("section_number") or representative.get("section_id"),
        "seats_open": section.get("seats_available") if section else representative.get("last_seats_available"),
        "enrollment_type": section.get("enrollment_status") if section else representative.get("last_status"),
        "track_count": len(tracks),
        "user_count": len(user_ids),
        "request_source": "automated",
    }
    if error:
        metadata["error"] = _sanitize_track_error(error)
    return metadata


def _group_target(tracks, section=None):
    representative = tracks[0] if tracks else {}
    section = section or {}
    return section.get("course_code") or representative.get("course_code") or representative.get("section_id") or "Tracked course"


def _sanitize_track_error(error):
    text = SECRET_TEXT_RE.sub(r"\1[redacted]", str(error or ""))
    return " ".join(text.split())[:500]


def _emit_poll_event(title, *, metadata, color="gray"):
    return emit_course_track_event(
        title,
        actor="System",
        target="Course seat tracking poll",
        metadata={
            "request_source": "automated",
            **metadata,
        },
        color=color,
    )


def _record_last_poll(title, metadata, *, discord_emit_returned=None):
    global _last_poll_metadata
    snapshot = dict(metadata or {})
    if discord_emit_returned is not None:
        snapshot["discord_emit_returned"] = bool(discord_emit_returned)
    snapshot["event_title"] = title
    snapshot["recorded_at"] = format_datetime(_now_utc())
    _last_poll_metadata = snapshot
    return snapshot


def get_last_course_tracking_poll():
    return dict(_last_poll_metadata or {})


def _track_matches_filter(track, *, term=None, subject=None, catalog=None):
    if term and str(track.get("term") or "") != str(term):
        return False
    if subject and str(track.get("subject") or "").upper() != str(subject).upper():
        return False
    if catalog and str(track.get("catalog") or "").upper() != str(catalog).upper():
        return False
    return True


def check_course_seat_tracks(*, term=None, subject=None, catalog=None, poll_source="automated"):
    """Poll enabled course seat trackers and notify users when seats open."""
    table_id = COLLECTIONS.get("course_seat_tracks")
    filter_metadata = {
        key: value
        for key, value in {
            "term": term,
            "subject": str(subject or "").upper() if subject else None,
            "catalog": str(catalog or "").upper() if catalog else None,
            "poll_source": poll_source,
        }.items()
        if value
    }
    if not table_id:
        logger.info("Course tracking skipped: collection mapping missing.")
        metadata = {"reason": "collection_mapping_missing", **filter_metadata}
        emitted = _emit_poll_event(
            "Automated Course Track Poll Skipped",
            metadata=metadata,
            color="yellow",
        )
        _record_last_poll("Automated Course Track Poll Skipped", metadata, discord_emit_returned=emitted)
        return 0

    try:
        tracks = list_rows_all(table_id, [Query.equal("enabled", [True])])
    except AppwriteException as exc:
        logger.exception("Failed to list course seat tracks")
        metadata = {"error": _sanitize_track_error(exc), **filter_metadata}
        emitted = _emit_poll_event(
            "Automated Course Track Poll Failed",
            metadata=metadata,
            color="red",
        )
        _record_last_poll("Automated Course Track Poll Failed", metadata, discord_emit_returned=emitted)
        return 0

    if term or subject or catalog:
        tracks = [
            track for track in tracks
            if _track_matches_filter(track, term=term, subject=subject, catalog=catalog)
        ]

    now = _now_utc()
    enabled_count = len(tracks)
    if poll_source == "automated":
        tracks = [track for track in tracks if _track_due(track, now)]
    notified_count = 0
    if not tracks:
        logger.info("Course tracking skipped: no enabled course seat tracks.")
        metadata = {"reason": "no_due_tracks" if enabled_count else "no_enabled_tracks", "enabled_track_count": enabled_count, "track_count": 0, **filter_metadata}
        emitted = _emit_poll_event(
            "Automated Course Track Poll Skipped",
            metadata=metadata,
            color="gray",
        )
        update_course_tracks_channel_topic(0)
        _record_last_poll("Automated Course Track Poll Skipped", metadata, discord_emit_returned=emitted)
        return 0

    grouped_tracks = {}
    for track in tracks:
        grouped_tracks.setdefault(_track_group_key(track), []).append(track)

    poll_metadata = {
        **filter_metadata,
        "enabled_track_count": enabled_count,
        "track_count": len(tracks),
        "section_group_count": len(grouped_tracks),
        "atlas_checks_attempted": 0,
        "atlas_checks_succeeded": 0,
        "atlas_checks_failed": 0,
        "row_updates": 0,
        "row_update_failures": 0,
        "email_notifications": 0,
        "email_failures": 0,
        "changed_rows_written": 0,
        "unchanged_rows_skipped": 0,
        "failed_rows_skipped": 0,
        "notifications_sent": 0,
        "due_track_count": len(tracks),
        "cooldown_track_count": len([track for track in tracks if track.get("cooldown_until_closed")]),
    }

    for grouped in grouped_tracks.values():
        representative = grouped[0]

        poll_metadata["atlas_checks_attempted"] += 1
        try:
            result = fetch_live_section_status(
                representative.get("term"),
                representative.get("subject"),
                representative.get("catalog"),
                crn=representative.get("crn"),
            )
        except Exception as exc:
            error = _sanitize_track_error(exc)
            logger.error(
                "Course track group %s live check raised an exception: %s",
                _track_group_key(representative),
                error,
            )
            result = {"error": error}
        if not isinstance(result, dict):
            result = {"error": "Live Atlas returned invalid data"}
        elif "error" not in result and not isinstance(result.get("section"), dict):
            result = {"error": "Live Atlas returned an invalid section"}
        updates = {
            "last_checked_at": format_datetime(now),
            "updated_at": format_datetime(now),
        }

        if "error" in result:
            poll_metadata["atlas_checks_failed"] += 1
            logger.warning(
                "Course track group %s live check failed: %s",
                _track_group_key(representative),
                result["error"],
            )
            emit_course_track_event(
                "Automated Course Track Check Failed",
                actor="System",
                target=_group_target(grouped),
                metadata=_group_metadata(grouped, error=result["error"]),
                color="yellow",
            )
            poll_metadata["failed_rows_skipped"] += len(grouped)
            continue

        poll_metadata["atlas_checks_succeeded"] += 1
        section = result.get("section") or {}
        updates["last_status"] = section.get("enrollment_status")
        updates["last_seats_available"] = section.get("seats_available")
        updates["last_waitlist_total"] = section.get("waitlist_total")
        updates["last_waitlist_capacity"] = section.get("waitlist_capacity")

        emit_course_track_event(
            "Automated Course Track Checked",
            actor="System",
            target=_group_target(grouped, section),
            metadata=_group_metadata(grouped, section),
            color="gray",
        )

        for track in grouped:
            row_id = track.get("$id") or track.get("id")
            if not row_id:
                continue
            track_updates = dict(updates)
            should_notify = _section_open_for_notification(section) and not _track_was_open(track)
            if should_notify:
                prefs = notifications.preferences(track.get("user_id"))
                delivered = False
                if prefs["course_email_enabled"]:
                    try:
                        _send_open_email(track, section)
                        poll_metadata["email_notifications"] += 1
                        delivered = True
                    except Exception:
                        poll_metadata["email_failures"] += 1
                        logger.exception("Failed to send course opening email for track %s", row_id)
                if prefs["course_push_enabled"]:
                    code = section.get("course_code") or track.get("course_code") or "Tracked course"
                    seats = section.get("seats_available")
                    body = f"{seats} seat{'s' if seats != 1 else ''} available." if seats is not None else "Enrollment is now available."
                    try:
                        _, push_result = notifications.notify(
                            track.get("user_id"), "courses", f"{code} has an opening", body,
                            build_nest_courses_detail_url(os.environ.get("APP_BASE_URL", "https://nest.apstudy.org"), section.get("id") or track.get("section_id")), source_ref=row_id,
                            dedupe_key=f"course-open:{row_id}:{section.get('enrollment_status')}:{seats}", tag=f"course:{row_id}",
                        )
                        delivered = delivered or push_result["accepted"] > 0
                    except Exception:
                        logger.exception("Failed to send course opening push for track %s", row_id)
                if delivered:
                    track_updates["last_notified_at"] = format_datetime(now)
                    track_updates["cooldown_until_closed"] = True
                    notified_count += 1
                    poll_metadata["notifications_sent"] += 1
                    emit_course_track_event(
                        "Tracked Course Availability Opened",
                        actor="System",
                        target=section.get("course_code") or track.get("course_code") or row_id,
                        metadata={
                            "course_name": section.get("course_title") or track.get("course_title"),
                            "teacher": section.get("instructor") or track.get("instructor_name"),
                            "section_number": section.get("section_number") or track.get("section_id"),
                            "seats_open": section.get("seats_available"),
                            "enrollment_type": section.get("enrollment_status"),
                            "request_source": "automated",
                            "track_id": row_id,
                            "user_id": track.get("user_id"),
                        },
                        color="green",
                    )
                else:
                    # Preserve the previous transition so a later poll can retry delivery.
                    continue

            available = _section_open_for_notification(section)
            cooldown = bool(track_updates.get("cooldown_until_closed", track.get("cooldown_until_closed")))
            if cooldown and not available:
                cooldown = False
                track_updates["cooldown_until_closed"] = False
            effective_minutes = 180 if cooldown else int(track.get("interval_minutes") or 30)
            track_updates["next_check_at"] = format_datetime(now + timedelta(minutes=effective_minutes))
            if not _track_result_changed(track, section):
                poll_metadata["unchanged_rows_skipped"] += 1

            try:
                update_row_safe(table_id, row_id, track_updates)
                poll_metadata["row_updates"] += 1
                poll_metadata["changed_rows_written"] += 1
            except AppwriteException:
                poll_metadata["row_update_failures"] += 1
                logger.exception("Failed to update course track: %s", row_id)
                continue

    if poll_metadata["enabled_track_count"] and not poll_metadata["atlas_checks_attempted"]:
        logger.warning("Course tracking found enabled tracks but made no Atlas checks.")
        emit_course_track_event(
            "Automated Course Track Poll Diagnostic",
            actor="System",
            target="Course seat tracking poll",
            metadata={**poll_metadata, "reason": "enabled_tracks_without_atlas_checks"},
            color="yellow",
        )

    title = "Automated Course Track Poll Completed"
    update_course_tracks_channel_topic(poll_metadata["section_group_count"])
    _record_last_poll(title, poll_metadata)
    return notified_count
