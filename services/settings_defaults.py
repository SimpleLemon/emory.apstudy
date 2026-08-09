"""Default persisted settings for newly created user rows."""

import secrets
from datetime import datetime

from appwrite_helpers import format_datetime


def settings_defaults(user_id):
    return {
        "user_id": user_id,
        "ics_secret_token": secrets.token_urlsafe(32),
        "feed_refresh_minutes": 15,
        "preferred_calendar_view": "week",
        "interface_theme": "obsidian-dark",
        "theme": "dark",
        "sidebar_default": "expanded",
        "email_notifications": True,
        "product_updates": True,
        "task_sound_enabled": True,
        "chat_sound_enabled": True,
        "language": "en",
        "timezone": "",
        "dashboard_layout_json": "[]",
        "dashboard_checklist_hidden_signature": "",
        "notes_page_setup_json": "{}",
        "created_at": format_datetime(datetime.utcnow()),
    }
