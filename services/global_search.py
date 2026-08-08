"""Authenticated, user-scoped aggregation for the global command palette."""

from __future__ import annotations

import html
import json
import logging
import re
from datetime import datetime, timezone
from urllib.parse import urlencode

from appwrite.query import Query

from appwrite_client import COLLECTIONS
from appwrite_helpers import first_row, format_datetime, get_row_safe, list_rows_all, parse_datetime
from services.atlas_client import build_section_id
from services.note_store import list_notes_for_user, list_shared_for_user, note_list_payload
from services.notes_preview import preview_text_from_content
from services.row_utils import row_id as _row_id
from services.user_profile import is_emory_or_oxford_user


logger = logging.getLogger(__name__)

GROUP_ORDER = ("files", "notes", "events", "messages", "courses")
RESULTS_PER_GROUP = 5
SNIPPET_LENGTH = 140

def _plain_text(value):
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _normalized(value):
    return _plain_text(value).casefold()


def _query_tokens(query):
    return [token for token in _normalized(query).split(" ") if token]


def _match_score(query, primary, *context_values):
    normalized_query = _normalized(query)
    primary_text = _normalized(primary)
    context_text = " ".join(_normalized(value) for value in context_values if value)
    blob = f"{primary_text} {context_text}".strip()
    tokens = _query_tokens(query)
    if not tokens or not all(token in blob for token in tokens):
        return None

    score = 0
    if primary_text == normalized_query:
        score += 1000
    elif primary_text.startswith(normalized_query):
        score += 700
    elif normalized_query in primary_text:
        score += 500
    elif all(any(word.startswith(token) for word in primary_text.split()) for token in tokens):
        score += 350
    elif all(token in primary_text for token in tokens):
        score += 250
    else:
        score += 100

    if normalized_query and normalized_query in context_text:
        score += 40
    score += sum(5 for token in tokens if token in primary_text)
    return score


def _snippet(value, query, limit=SNIPPET_LENGTH):
    text = _plain_text(value)
    if not text:
        return ""
    normalized_text = text.casefold()
    positions = [normalized_text.find(token) for token in _query_tokens(query)]
    positions = [position for position in positions if position >= 0]
    start = max(0, (min(positions) if positions else 0) - limit // 3)
    end = min(len(text), start + limit)
    if end - start < limit and start > 0:
        start = max(0, end - limit)
    excerpt = text[start:end].strip()
    if start > 0:
        excerpt = f"…{excerpt.lstrip(' ,.;:')}"
    if end < len(text):
        excerpt = f"{excerpt.rstrip(' ,.;:')}…"
    return excerpt


def _timestamp_value(value):
    parsed = parse_datetime(value)
    if not parsed:
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _ranked(results, limit=RESULTS_PER_GROUP, tie_key=None):
    tie_key = tie_key or (lambda item: -_timestamp_value(item.get("timestamp")))
    results.sort(key=lambda item: (-item.pop("_score"), tie_key(item), item.get("title", "").casefold()))
    return results[:limit]


def _format_bytes(value):
    size = float(value or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            precision = 0 if unit == "B" else 1
            return f"{size:.{precision}f} {unit}"
        size /= 1024
    return "0 B"


def _mime_label(value, filename=""):
    mime = str(value or "").lower()
    extension = str(filename or "").rsplit(".", 1)[-1].upper() if "." in str(filename or "") else ""
    if extension and len(extension) <= 8:
        return extension
    if mime.startswith("image/"):
        return "Image"
    if mime.startswith("video/"):
        return "Video"
    if mime.startswith("audio/"):
        return "Audio"
    return "File"


def _folder_paths(folders):
    by_id = {_row_id(folder): folder for folder in folders if _row_id(folder)}
    cache = {}

    def resolve(folder_id):
        folder_id = str(folder_id or "")
        if not folder_id:
            return "My Files"
        if folder_id in cache:
            return cache[folder_id]
        labels = []
        seen = set()
        current_id = folder_id
        while current_id and current_id not in seen:
            seen.add(current_id)
            folder = by_id.get(current_id)
            if not folder:
                break
            labels.append(_plain_text(folder.get("name")) or "Untitled Folder")
            current_id = str(folder.get("parent_folder_id") or "")
        path = " / ".join(reversed(labels)) or "My Files"
        cache[folder_id] = path
        return path

    return resolve


def _search_files(user_id, query):
    now = format_datetime(datetime.now(timezone.utc))
    files = list_rows_all(
        COLLECTIONS["shared_files"],
        [Query.equal("user_id", [user_id]), Query.greater_than("expires_at", now)],
    )
    folders = list_rows_all(COLLECTIONS["file_folders"], [Query.equal("user_id", [user_id])])
    folder_path = _folder_paths(folders)
    results = []
    for row in files:
        title = _plain_text(row.get("original_filename")) or "Untitled file"
        path = folder_path(row.get("folder_id"))
        mime = _mime_label(row.get("mime_type"), title)
        score = _match_score(query, title, path, mime)
        if score is None:
            continue
        params = {"file": _row_id(row)}
        if row.get("folder_id"):
            params["folder"] = str(row.get("folder_id"))
        results.append({
            "id": _row_id(row),
            "category": "files",
            "title": title,
            "secondary": f"{mime} · {_format_bytes(row.get('file_size_bytes'))} · {path}",
            "timestamp": row.get("updated_at") or row.get("created_at"),
            "href": f"/files?{urlencode(params)}",
            "icon": "description",
            "_score": score,
        })
    return _ranked(results)


def _flatten_shared_notes(shared):
    notes = list(shared.get("notes") or [])
    for folder in shared.get("folders") or []:
        notes.extend(folder.get("notes") or [])
    return notes


def _search_notes(user_id, query):
    owned = [note_list_payload(note) for note in list_notes_for_user(user_id)]
    shared = _flatten_shared_notes(list_shared_for_user(user_id))
    notes = {}
    for note in owned + shared:
        note_id = _row_id(note)
        if note_id:
            notes[note_id] = note

    results = []
    for note_id, note in notes.items():
        title = _plain_text(note.get("title")) or "Untitled"
        preview = note.get("preview_text") or preview_text_from_content(note.get("content") or "")
        owner = (note.get("owner") or {}).get("name") or ""
        score = _match_score(query, title, preview, owner)
        if score is None:
            continue
        snippet = _snippet(preview, query)
        secondary = f"Shared by {owner}" if owner else "Your note"
        results.append({
            "id": note_id,
            "category": "notes",
            "title": title,
            "secondary": secondary,
            "snippet": snippet,
            "timestamp": note.get("updated_at") or note.get("created_at"),
            "href": f"/notes/{note_id}",
            "icon": "article",
            "_score": score,
        })
    return _ranked(results)


def _event_tie_key(item):
    parsed = parse_datetime(item.get("timestamp"))
    if not parsed:
        return (2, float("inf"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    delta = (parsed.astimezone(timezone.utc) - now).total_seconds()
    return (0 if delta >= 0 else 1, abs(delta))


def _search_events(user_id, query):
    # Calendar serialization centralizes configured-feed filtering and event overrides.
    from blueprints.calendar_api import _load_serialized_calendar_events

    settings = first_row(COLLECTIONS["user_settings"], [Query.equal("user_id", [user_id])])
    events, _cache_rows, _created_rows = _load_serialized_calendar_events(user_id, settings)
    results = []
    for event in events:
        event_ref = str(event.get("event_ref") or "")
        if not event_ref or not event.get("start"):
            continue
        title = _plain_text(event.get("title")) or "Untitled event"
        description = _plain_text(event.get("description"))
        course = _plain_text(event.get("course"))
        event_type = _plain_text(event.get("type"))
        calendar_id = str(event.get("calendar_id") or "")
        calendar_label = course or ("Personal" if calendar_id.startswith("local:") else "Imported calendar")
        score = _match_score(query, title, description, course, event_type, calendar_label)
        if score is None:
            continue
        event_date = str(event.get("start"))[:10]
        results.append({
            "id": event_ref,
            "category": "events",
            "title": title,
            "secondary": calendar_label,
            "snippet": _snippet(description, query),
            "timestamp": event.get("start"),
            "end": event.get("end"),
            "is_all_day": bool(event.get("is_all_day")),
            "href": f"/calendar?{urlencode({'event': event_ref, 'date': event_date})}",
            "icon": "calendar_today",
            "_score": score,
        })
    return _ranked(results, tie_key=_event_tie_key)


def _search_messages(user_id, query):
    rows_a = list_rows_all(COLLECTIONS["chat_dm_threads"], [Query.equal("participant_a", [user_id])])
    rows_b = list_rows_all(COLLECTIONS["chat_dm_threads"], [Query.equal("participant_b", [user_id])])
    threads = {_row_id(row): row for row in rows_a + rows_b if _row_id(row)}
    results = []
    for thread_id, thread in threads.items():
        if not thread.get("last_message_at"):
            continue
        other_id = thread.get("participant_b") if thread.get("participant_a") == user_id else thread.get("participant_a")
        other = get_row_safe(COLLECTIONS["users"], other_id, allow_missing=True)
        if not other:
            continue
        title = _plain_text(other.get("name") or other.get("username")) or "Nest User"
        username = _plain_text(other.get("username"))
        school = _plain_text(other.get("school"))
        major = _plain_text(other.get("major"))
        score = _match_score(query, title, username, school, major)
        if score is None:
            continue
        details = [f"@{username}" if username else "", school, major]
        results.append({
            "id": thread_id,
            "category": "messages",
            "title": title,
            "secondary": " · ".join(value for value in details if value),
            "timestamp": thread.get("last_message_at"),
            "href": f"/chat?{urlencode({'thread': thread_id})}",
            "icon": "chat_bubble",
            "avatar_url": other.get("picture_url") or "",
            "_score": score,
        })
    return _ranked(results)


def _course_overrides(row):
    raw = row.get("course_overrides_json")
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _search_courses(user_id, query):
    courses = list_rows_all(COLLECTIONS["user_courses"], [Query.equal("user_id", [user_id])])
    results = []
    for row in courses:
        overrides = _course_overrides(row)
        subject = _plain_text(row.get("subject")).upper()
        catalog = _plain_text(row.get("catalog"))
        code = _plain_text(overrides.get("course_code")) or f"{subject} {catalog}".strip()
        title = _plain_text(
            overrides.get("course_title") or overrides.get("course_name") or row.get("course_name")
        ) or code or "Saved course"
        display_title = f"{code} — {title}" if code and title.casefold() != code.casefold() else title
        instructor = _plain_text(
            overrides.get("instructor") or overrides.get("instructor_name") or row.get("instructor_name")
        )
        term = _plain_text(row.get("term")).replace("_", " ")
        score = _match_score(query, display_title, code, title, instructor, term)
        if score is None:
            continue
        section_id = build_section_id(
            row.get("term"), subject, catalog, row.get("crn"), row.get("section_number")
        )
        results.append({
            "id": _row_id(row),
            "category": "courses",
            "title": display_title,
            "secondary": " · ".join(value for value in (term, instructor) if value),
            "timestamp": row.get("updated_at") or row.get("added_at"),
            "href": f"/courses?{urlencode({'section': section_id})}",
            "icon": "school",
            "_score": score,
        })
    return _ranked(results)


def search_global(user, query):
    """Search each category independently so one failure can return partial results."""
    user_id = str(user.id)
    courses_enabled = is_emory_or_oxford_user(user)
    loaders = {
        "files": _search_files,
        "notes": _search_notes,
        "events": _search_events,
        "messages": _search_messages,
    }
    if courses_enabled:
        loaders["courses"] = _search_courses

    groups = {group: [] for group in GROUP_ORDER}
    unavailable = []
    for group, loader in loaders.items():
        try:
            groups[group] = loader(user_id, query)
        except Exception:
            logger.exception("Global search category failed: %s", group)
            unavailable.append(group)

    return {
        "query": query,
        "total": sum(len(items) for items in groups.values()),
        "courses_enabled": courses_enabled,
        "unavailable_categories": unavailable,
        "groups": groups,
    }
