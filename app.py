import os
import json
import flask
import requests
import google.oauth2.credentials
import google_auth_oauthlib.flow
from dotenv import load_dotenv
from functools import wraps

load_dotenv()

app = flask.Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-fallback-key")

CLIENT_SECRETS_FILE = "client_secret.json"

# Only request identity scopes. No API access needed, just sign-in.
SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
]


def login_required(f):
    """Decorator that redirects unauthenticated users to the login page."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "user" not in flask.session:
            return flask.redirect(flask.url_for("login"))
        return f(*args, **kwargs)
    return decorated_function


@app.route("/")
def index():
    """Root redirects to dashboard if logged in, login if not."""
    if "user" in flask.session:
        return flask.redirect(flask.url_for("dashboard"))
    return flask.redirect(flask.url_for("login"))


@app.route("/login")
def login():
    """Render the Google-only login page."""
    # If already authenticated, skip straight to dashboard.
    if "user" in flask.session:
        return flask.redirect(flask.url_for("dashboard"))
    return flask.render_template("login.html")


@app.route("/authorize")
def authorize():
    """Initiate the OAuth 2.0 flow by redirecting to Google's consent screen."""
    flow = google_auth_oauthlib.flow.Flow.from_client_secrets_file(
        CLIENT_SECRETS_FILE, scopes=SCOPES
    )

    # This URI must exactly match one registered in the Google Cloud Console.
    flow.redirect_uri = flask.url_for("oauth2callback", _external=True)

    authorization_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="select_account",  # Always show account chooser
    )

    # Store state in session to verify callback authenticity.
    flask.session["state"] = state

    return flask.redirect(authorization_url)


@app.route("/oauth2callback")
def oauth2callback():
    """Handle Google's redirect after user consent."""
    # Verify state to prevent CSRF attacks.
    state = flask.session.get("state")
    if not state:
        return flask.redirect(flask.url_for("login"))

    flow = google_auth_oauthlib.flow.Flow.from_client_secrets_file(
        CLIENT_SECRETS_FILE, scopes=SCOPES, state=state
    )
    flow.redirect_uri = flask.url_for("oauth2callback", _external=True)

    # Exchange the authorization code for tokens.
    authorization_response = flask.request.url
    flow.fetch_token(authorization_response=authorization_response)

    credentials = flow.credentials

    # Store credentials in session.
    flask.session["credentials"] = {
        "token": credentials.token,
        "refresh_token": credentials.refresh_token,
        "token_uri": credentials.token_uri,
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
        "scopes": list(credentials.scopes or []),
    }

    # Fetch user profile info from Google's userinfo endpoint.
    userinfo_response = requests.get(
        "https://www.googleapis.com/oauth2/v2/userinfo",
        headers={"Authorization": f"Bearer {credentials.token}"},
    )

    if userinfo_response.status_code == 200:
        user_info = userinfo_response.json()
        flask.session["user"] = {
            "id": user_info.get("id"),
            "email": user_info.get("email"),
            "name": user_info.get("name"),
            "picture": user_info.get("picture"),
            "verified_email": user_info.get("verified_email"),
        }
        email = user_info.get("email", "")
    else:
        flask.session.clear()
        return flask.redirect(flask.url_for("login"))

    return flask.redirect(flask.url_for("dashboard"))


@app.route("/dashboard")
@login_required
def dashboard():
    """Render the main dashboard for authenticated users."""
    user = flask.session.get("user", {})
    return flask.render_template("dashboard.html", user=user)


@app.route("/logout")
def logout():
    """Revoke token if possible, then clear session."""
    credentials_data = flask.session.get("credentials")

    if credentials_data and credentials_data.get("token"):
        # Attempt to revoke the access token with Google.
        requests.post(
            "https://oauth2.googleapis.com/revoke",
            params={"token": credentials_data["token"]},
            headers={"content-type": "application/x-www-form-urlencoded"},
        )

    flask.session.clear()
    return flask.redirect(flask.url_for("login"))


if __name__ == "__main__":
    # For local development only. In production, use gunicorn or similar.
    os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"  # Allow HTTP for localhost
    app.run("localhost", 5000, debug=True)