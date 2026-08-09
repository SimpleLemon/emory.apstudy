"""Personal pages for Derek — Echo Show dashboard and related tools."""

import logging
from functools import wraps

from flask import Blueprint, abort, g, make_response, render_template
from flask_login import current_user, login_required

from services.dashboard_context import (
    load_user_settings,
    theme_from_settings,
    user_payload,
)

derek_bp = Blueprint("derek", __name__)
logger = logging.getLogger(__name__)

ALLOWED_EMAIL = "derekchenusa@gmail.com"


def _load_user_settings():
    return load_user_settings(current_user, g, error_logger=logger)


def _theme_from_settings(user_settings):
    return theme_from_settings(user_settings)


def _user_payload():
    return user_payload(current_user)


def derek_email_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        email = str(current_user.email or "").strip().lower()
        if email != ALLOWED_EMAIL:
            abort(403)
        return view(*args, **kwargs)

    return wrapped


@derek_bp.get("/derek/echo")
@derek_email_required
def echo_page():
    settings = _load_user_settings()
    response = make_response(render_template(
        "derek_echo.html",
        user=_user_payload(),
        theme_preference=_theme_from_settings(settings),
    ))
    response.headers["Cache-Control"] = "private, no-store, no-transform"
    return response
