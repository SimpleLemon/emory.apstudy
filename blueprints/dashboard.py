"""
blueprints/dashboard.py

Main dashboard view. Renders the authenticated user's dashboard page.
All data fetching happens client-side via the Atlas and Calendar API blueprints.
"""

from flask import Blueprint, render_template, redirect, url_for
from flask_login import login_required, current_user

dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.route("/dashboard")
@login_required
def dashboard():
    """Render the dashboard with user context for the template header."""
    if not current_user.onboarding_complete:
        return redirect(url_for("settings.onboarding"))

    return render_template(
        "dashboard.html",
        user={
            "name": current_user.name,
            "email": current_user.email,
            "picture": current_user.picture_url,
        },
    )