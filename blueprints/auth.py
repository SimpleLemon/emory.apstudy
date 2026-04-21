"""
blueprints/auth.py

Google OAuth 2.0 authentication flow.
Handles login, callback, session creation, and logout.

Migrated from the monolithic app.py implementation [2].
Requires: client_secret.json in project root,
          GOOGLE_CLIENT_ID and FLASK_SECRET_KEY in .env.
"""

import os
import secrets
from datetime import datetime

import requests as http_requests
import google_auth_oauthlib.flow
from flask import (
    Blueprint, redirect, url_for, session, render_template, request, current_app
)
from flask_login import login_user, logout_user, current_user

from extensions import db
from models import User, UserSettings

auth_bp = Blueprint("auth", __name__)

CLIENT_SECRETS_FILE = "client_secret.json"
SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
]
ALLOWED_DOMAIN = os.environ.get("ALLOWED_EMAIL_DOMAIN", "emory.edu")


@auth_bp.route("/")
def index():
    """Root redirect: dashboard if authenticated, login if not."""
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.dashboard"))
    return redirect(url_for("auth.login"))


@auth_bp.route("/login")
def login():
    """Render the Google-only sign-in page."""
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.dashboard"))
    return render_template("login.html")


@auth_bp.route("/authorize")
def authorize():
    """Initiate OAuth 2.0 flow by redirecting to Google's consent screen."""
    flow = google_auth_oauthlib.flow.Flow.from_client_secrets_file(
        CLIENT_SECRETS_FILE, scopes=SCOPES
    )
    flow.redirect_uri = url_for("auth.oauth2callback", _external=True)

    authorization_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="select_account",
    )

    session["oauth_state"] = state
    return redirect(authorization_url)


@auth_bp.route("/oauth2callback")
def oauth2callback():
    """Handle Google's redirect after user consent."""
    state = session.get("oauth_state")
    if not state:
        return redirect(url_for("auth.login"))

    flow = google_auth_oauthlib.flow.Flow.from_client_secrets_file(
        CLIENT_SECRETS_FILE, scopes=SCOPES, state=state
    )
    flow.redirect_uri = url_for("auth.oauth2callback", _external=True)

    # Exchange authorization code for tokens
    flow.fetch_token(authorization_response=request.url)
    credentials = flow.credentials

    # Store credentials in session for potential token revocation later
    session["credentials"] = {
        "token": credentials.token,
        "refresh_token": credentials.refresh_token,
        "token_uri": credentials.token_uri,
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
        "scopes": list(credentials.scopes or []),
    }

    # Fetch user profile from Google
    userinfo_response = http_requests.get(
        "https://www.googleapis.com/oauth2/v2/userinfo",
        headers={"Authorization": f"Bearer {credentials.token}"},
    )

    if userinfo_response.status_code != 200:
        session.clear()
        return redirect(url_for("auth.login"))

    user_info = userinfo_response.json()

    # Enforce email domain restriction
    email = user_info.get("email", "")
    if not email.endswith(f"@{ALLOWED_DOMAIN}"):
        session.clear()
        return render_template(
            "login.html",
            error=f"Access restricted to @{ALLOWED_DOMAIN} accounts.",
        )

    # Database lookup-or-create
    user = User.query.filter_by(google_id=user_info["id"]).first()

    if not user:
        user = User(
            google_id=user_info["id"],
            email=email,
            name=user_info.get("name"),
            picture_url=user_info.get("picture"),
        )
        db.session.add(user)
        db.session.flush()

        # Create default settings with a unique .ics subscription token
        user_settings = UserSettings(
            user_id=user.id,
            ics_secret_token=secrets.token_urlsafe(32),
        )
        db.session.add(user_settings)

    user.last_login = datetime.utcnow()
    user.name = user_info.get("name", user.name)
    user.picture_url = user_info.get("picture", user.picture_url)
    db.session.commit()

    login_user(user)
    session.pop("oauth_state", None)

    # Redirect new users (no iCal URL yet) to onboarding
    if not user.settings or not user.settings.canvas_ical_url:
        return redirect(url_for("settings.onboarding"))

    return redirect(url_for("dashboard.dashboard"))


@auth_bp.route("/logout")
def logout():
    """Revoke Google token if possible, then clear session."""
    credentials_data = session.get("credentials")

    if credentials_data and credentials_data.get("token"):
        http_requests.post(
            "https://oauth2.googleapis.com/revoke",
            params={"token": credentials_data["token"]},
            headers={"content-type": "application/x-www-form-urlencoded"},
        )

    logout_user()
    session.clear()
    return redirect(url_for("auth.login"))