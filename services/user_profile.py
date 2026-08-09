"""Shared profile formatting helpers for auth and admin blueprints."""

import re
from datetime import datetime

from appwrite_helpers import parse_datetime


DEFAULT_BANNER_COLOR = "#fecae1"
USERNAME_MIN_LENGTH = 3
USERNAME_MAX_LENGTH = 20
USERNAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")


def normalize_banner_color(value):
    if not isinstance(value, str):
        return DEFAULT_BANNER_COLOR
    normalized = value.strip()
    if not normalized.startswith("#"):
        normalized = f"#{normalized}"
    if len(normalized) == 7:
        try:
            int(normalized[1:], 16)
            return normalized.lower()
        except ValueError:
            return DEFAULT_BANNER_COLOR
    return DEFAULT_BANNER_COLOR


def profile_handle(name, user_id, username=None):
    if username:
        return f"@{username}"
    base = "".join(
        char.lower() if char.isalnum() else "-"
        for char in (name or "")
    ).strip("-")
    base = "-".join(part for part in base.split("-") if part)
    return f"@{base or user_id or 'apstudy-user'}"


def is_emory_school(value):
    normalized = str(value or "").strip().lower()
    return normalized in {"emory", "emory university"}


def is_emory_or_oxford_user(user):
    """Return whether a user qualifies for Emory-only product surfaces."""
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


def is_early_member(value):
    if not value:
        return False
    parsed = parse_datetime(value)
    if not parsed:
        if isinstance(value, str) and value.endswith("Z"):
            try:
                parsed = datetime.fromisoformat(value[:-1] + "+00:00")
            except (TypeError, ValueError):
                return False
        else:
            try:
                parsed = datetime.fromisoformat(value)
            except (TypeError, ValueError):
                return False
    if parsed.tzinfo is not None:
        parsed = parsed.replace(tzinfo=None)
    return parsed < datetime(2026, 8, 20)
