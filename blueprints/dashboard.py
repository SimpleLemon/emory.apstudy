"""
blueprints/dashboard.py

Main dashboard view. Renders the authenticated user's dashboard page.
All data fetching happens client-side via the Atlas and Calendar API blueprints.
"""

from flask import Blueprint, render_template, redirect, url_for
from flask_login import login_required, current_user

from models import UserSettings

dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.route("/dashboard")
@login_required
def dashboard():
    """Render the dashboard with user context for the template header."""
    if not current_user.onboarding_complete:
        return redirect(url_for("settings.onboarding"))

    user_settings = UserSettings.query.filter_by(user_id=current_user.id).first()
    preferred_calendar_view = (user_settings.preferred_calendar_view if user_settings and user_settings.preferred_calendar_view else "week")
    if preferred_calendar_view not in {"week", "month"}:
        preferred_calendar_view = "week"

    return render_template(
        "dashboard.html",
        user={
            "name": current_user.name,
            "email": current_user.email,
            "picture": current_user.picture_url,
        },
        preferred_calendar_view=preferred_calendar_view,
    )