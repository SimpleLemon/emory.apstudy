"""Loopback-only authenticated browser harness for the real Flask app."""

from __future__ import annotations

import os
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from flask import abort, redirect, request
from flask_login import login_user


REPO_ROOT = str(Path(__file__).resolve().parents[2])
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)


AUTH_ROUTE = "/__test__/auth"
TEST_SECRET = "nest-apstudy-browser-test-secret"
TEST_NOTE_ID = "auth-test-note"
ALLOWED_TIERS = ("free", "grade_a", "grade_aa", "developer")
ALLOWED_THEMES = (
    "obsidian-dark",
    "parchment-light",
    "system-match",
    "nest-light",
    "nest-dark",
)


def note_id_for(tier, theme):
    return f"{TEST_NOTE_ID}-{tier}-{theme}"


ALLOWED_NEXT = {
    "/dashboard",
    "/chat",
    "/notes",
    *(f"/notes/{note_id_for(tier, theme)}" for tier in ALLOWED_TIERS for theme in ALLOWED_THEMES),
}


def _now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _upsert(table_id, row_id, data):
    from services import database

    if database.get_row(table_id, row_id, allow_missing=True):
        return database.update_row(table_id, row_id, data=data)
    return database.create_row(table_id, row_id=row_id, data=data)


def _seed_browser_state():
    from services import database

    timestamp = _now()
    for tier in ALLOWED_TIERS:
        for theme in ALLOWED_THEMES:
            user_id = f"browser-{tier}-{theme}"
            _upsert(
                "users",
                user_id,
                {
                    "google_id": f"{user_id}@example.test",
                    "email": f"{user_id}@example.test",
                    "name": f"Browser {tier} {theme}",
                    "username": user_id,
                    "tier": tier,
                    "onboarding_complete": True,
                    "onboarding_step": 5,
                    "created_at": timestamp,
                    "provider": "test",
                },
            )
            _upsert(
                "user_settings",
                user_id,
                {
                    "user_id": user_id,
                    "interface_theme": theme,
                    "theme": "dark" if theme in {"obsidian-dark", "nest-dark"} else "light",
                    "sidebar_default": "expanded",
                    "created_at": timestamp,
                    "updated_at": timestamp,
                },
            )

    for tier in ALLOWED_TIERS:
        for theme in ALLOWED_THEMES:
            note_id = note_id_for(tier, theme)
            if database.get_row("notes", note_id, allow_missing=True):
                continue
            database.create_row(
                "notes",
                row_id=note_id,
                data={
                    "user_id": f"browser-{tier}-{theme}",
                    "title": "Authenticated browser note",
                    "content": "[{\"type\":\"paragraph\",\"content\":[{\"type\":\"text\",\"text\":\"Browser harness note\",\"styles\":{}}]}]",
                    "preview_text": "Browser harness note",
                    "order": 1000,
                    "created_at": timestamp,
                    "updated_at": timestamp,
                },
            )


@contextmanager
def _temporary_test_environment(database_path):
    values = {
        "DATABASE_PATH": database_path,
        "FLASK_SECRET_KEY": TEST_SECRET,
        "FLASK_ENV": "testing",
        "APSTUDY_ALLOW_INSECURE_HTTP": "1",
        "SCHEDULER_ENABLED": "0",
    }
    previous = {
        key: (key in os.environ, os.environ.get(key))
        for key in values
    }
    try:
        os.environ.update(values)
        yield
    finally:
        for key, (was_present, previous_value) in previous.items():
            if was_present:
                os.environ[key] = previous_value
            else:
                os.environ.pop(key, None)


def register_auth_route(app):
    """Register the seam only on an explicitly testing-configured app."""
    if not app.testing:
        raise RuntimeError("The browser auth route is test-only.")

    @app.get(AUTH_ROUTE)
    def browser_auth():
        tier = request.args.get("tier", "free")
        theme = request.args.get("theme", "system-match")
        next_url = request.args.get("next", "/dashboard")
        if tier not in ALLOWED_TIERS or theme not in ALLOWED_THEMES or next_url not in ALLOWED_NEXT:
            abort(400)

        from models import User

        user_id = f"browser-{tier}-{theme}"
        user = User({
            "$id": user_id,
            "email": f"{user_id}@example.test",
            "name": f"Browser {tier} {theme}",
            "username": user_id,
            "tier": tier,
            "onboarding_complete": True,
            "onboarding_step": 5,
            "provider": "test",
        })
        login_user(user, remember=False)
        return redirect(next_url)


def create_test_app(database_path: str | None = None):
    """Build the real app with loopback-safe test settings and seeded rows."""
    if database_path is None:
        database_path = os.path.join(tempfile.mkdtemp(prefix="nest-apstudy-browser-"), "nest.sqlite3")

    from app import create_app

    with _temporary_test_environment(database_path):
        app = create_app()
    app.config.update(
        TESTING=True,
        SECRET_KEY=TEST_SECRET,
        SESSION_COOKIE_SECURE=False,
        REMEMBER_COOKIE_SECURE=False,
    )
    with app.app_context():
        _seed_browser_state()
    register_auth_route(app)
    return app


def _is_loopback(host):
    return host in {"127.0.0.1", "localhost"}


if __name__ == "__main__":
    host = "127.0.0.1"
    port = int(os.environ.get("PORT", "8000"))
    if not _is_loopback(host):
        raise RuntimeError("The browser auth harness must bind to loopback.")
    create_test_app().run(host=host, port=port, debug=False, use_reloader=False)
