import logging

from appwrite.exception import AppwriteException
from appwrite.query import Query


logger = logging.getLogger(__name__)


def load_invites_section(user_id, dependencies):
    list_rows_all = dependencies["list_rows_all"]
    first_row = dependencies["first_row"]
    collections = dependencies["collections"]
    row_id = dependencies["row_id"]

    try:
        owned_invites = list_rows_all(
            collections["user_invites"],
            [
                Query.equal("owner_user_id", [user_id]),
                Query.order_desc("created_at"),
            ],
        )
        owned_attributions = list_rows_all(
            collections["user_invite_attributions"],
            [
                Query.equal("inviter_user_id", [user_id]),
                Query.order_desc("signed_up_at"),
            ],
        )
        received_attribution = first_row(
            collections["user_invite_attributions"],
            [Query.equal("invited_user_id", [user_id])],
        )
    except AppwriteException:
        logger.exception("Failed to load invite records for admin")
        return {
            "invites": [],
            "received_attribution": None,
        }

    attributions_by_invite = {}
    for attribution in owned_attributions:
        attributions_by_invite.setdefault(
            str(attribution.get("invite_id") or ""),
            [],
        ).append(attribution)
    return {
        "invites": [
            {
                "invite": invitation,
                "attributions": attributions_by_invite.get(
                    str(row_id(invitation) or ""),
                    [],
                ),
            }
            for invitation in owned_invites
        ],
        "received_attribution": received_attribution,
    }


def load_settings_section(user_id, dependencies):
    try:
        return {
            "settings": dependencies["first_row"](
                dependencies["collections"]["user_settings"],
                [Query.equal("user_id", [user_id])],
            )
        }
    except AppwriteException:
        logger.exception("Failed to load user settings")
        return {"settings": None}


def load_files_section(user_id, dependencies):
    list_rows_all = dependencies["list_rows_all"]
    collections = dependencies["collections"]
    try:
        folders = list_rows_all(
            collections["file_folders"],
            [Query.equal("user_id", [user_id]), Query.order_asc("created_at")],
        )
        files = list_rows_all(
            collections["shared_files"],
            [Query.equal("user_id", [user_id]), Query.order_desc("created_at")],
        )
    except AppwriteException:
        logger.exception("Failed to load files for admin")
        return {"folders": [], "files": []}
    return {
        "folders": folders,
        "files": files,
    }


def load_notes_section(user_id, dependencies):
    list_rows_all = dependencies["list_rows_all"]
    collections = dependencies["collections"]
    try:
        notes = list_rows_all(
            collections["notes"],
            [Query.equal("user_id", [user_id]), Query.order_desc("updated_at")],
        )
        folders = list_rows_all(
            collections["note_folders"],
            [Query.equal("user_id", [user_id]), Query.order_asc("created_at")],
        )
    except AppwriteException:
        logger.exception("Failed to load notes for admin")
        return {"notes": [], "note_folders": []}
    return {
        "notes": notes,
        "note_folders": folders,
    }


def load_calendars_section(user_id, dependencies):
    list_calendar_rows_all = dependencies["list_calendar_rows_all"]
    collections = dependencies["collections"]
    try:
        cache_rows = list_calendar_rows_all(
            collections["calendar_cache"],
            [Query.equal("user_id", [user_id]), Query.order_desc("event_start")],
        )
        feeds = list_calendar_rows_all(
            collections["calendar_feeds"],
            [Query.equal("user_id", [user_id]), Query.order_desc("updated_at")],
        )
        preferences = list_calendar_rows_all(
            collections["user_calendar_preferences"],
            [Query.equal("user_id", [user_id]), Query.order_asc("calendar_name")],
        )
        sources = list_calendar_rows_all(
            collections["user_calendar_sources"],
            [Query.equal("user_id", [user_id]), Query.order_desc("updated_at")],
        )
        events = list_calendar_rows_all(
            collections["user_events"],
            [Query.equal("user_id", [user_id]), Query.order_desc("start")],
        )
        overrides = list_calendar_rows_all(
            collections["user_event_overrides"],
            [Query.equal("user_id", [user_id]), Query.order_desc("updated_at")],
        )
    except AppwriteException:
        logger.exception("Failed to load calendar data for admin")
        return {
            "calendar_cache": [],
            "calendar_feeds": [],
            "calendar_preferences": [],
            "calendar_sources": [],
            "calendar_events": [],
            "calendar_overrides": [],
        }
    return {
        "calendar_cache": cache_rows,
        "calendar_feeds": feeds,
        "calendar_preferences": preferences,
        "calendar_sources": sources,
        "calendar_events": events,
        "calendar_overrides": overrides,
    }


def load_courses_section(user_id, dependencies):
    try:
        courses = dependencies["list_rows_all"](
            dependencies["collections"]["user_courses"],
            [Query.equal("user_id", [user_id]), Query.order_asc("term")],
        )
    except AppwriteException:
        logger.exception("Failed to load courses for admin")
        courses = []
    return {"courses": courses}


def load_seat_tracks_section(user_id, dependencies):
    try:
        tracks = dependencies["list_rows_all"](
            dependencies["collections"]["course_seat_tracks"],
            [Query.equal("user_id", [user_id]), Query.order_desc("updated_at")],
        )
    except AppwriteException:
        logger.exception("Failed to load seat tracks for admin")
        tracks = []
    return {"seat_tracks": tracks}


def load_chat_section(user_id, dependencies):
    return {
        "messages": dependencies["user_chat_messages"](user_id),
        "dm_threads": dependencies["user_dm_threads"](user_id),
        "blocks": dependencies["user_chat_blocks"](user_id),
    }
