"""Shared authenticated page context for dashboard-adjacent blueprints."""

import logging

from appwrite.exception import AppwriteException
from appwrite.query import Query

from appwrite_client import COLLECTIONS
from appwrite_helpers import first_row


logger = logging.getLogger(__name__)


def is_emory_or_oxford_user(user):
    school = str(getattr(user, "school", "") or "").strip().lower()
    school_key = str(getattr(user, "school_key", "") or "").strip().lower()
    return bool(getattr(user, "emory_student", False)) or school in {
        "emory",
        "emory university",
        "emory university-oxford",
        "emory university oxford",
        "oxford college",
        "oxford college of emory university",
    } or school_key in {
        "emory",
        "emory-university",
        "emory-university-oxford",
        "oxford-college",
        "oxford-college-of-emory-university",
    }


def user_payload(user, *, emory_predicate=None):
    emory_student = (
        is_emory_or_oxford_user(user)
        if emory_predicate is None
        else emory_predicate()
    )
    return {
        "id": str(user.id),
        "name": user.name,
        "username": user.username,
        "email": user.email,
        "picture": user.picture_url,
        "emory_student": emory_student,
        "school": user.school,
        "school_key": getattr(user, "school_key", None),
    }


def load_user_settings(user, request_context, *, first_row_fn=first_row, error_logger=logger):
    if hasattr(request_context, "_apstudy_user_settings"):
        return request_context._apstudy_user_settings
    try:
        settings = first_row_fn(
            COLLECTIONS["user_settings"],
            [Query.equal("user_id", [str(user.id)])],
        )
        request_context._apstudy_user_settings = settings
        return settings
    except AppwriteException:
        error_logger.exception("Failed to load user settings")
        request_context._apstudy_user_settings = None
        return None


def theme_from_settings(user_settings):
    return user_settings.get("interface_theme") if user_settings else None
