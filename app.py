import logging
import os
import secrets
from datetime import datetime, timedelta, timezone

from flask import Flask, g, jsonify, redirect, render_template, request, send_from_directory, session, url_for
from dotenv import load_dotenv
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.middleware.proxy_fix import ProxyFix

from config import (
    ENVIRONMENT_CONFIG_EXTENSION_KEY,
    EnvironmentConfig,
    load_environment_config,
)

load_dotenv()
logger = logging.getLogger(__name__)


def _configure_insecure_oauth_transport():
    configured = load_environment_config()
    if configured.allow_insecure_oauth or configured.flask_debug_raw == "1":
        os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")


_configure_insecure_oauth_transport()


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
AUTH_SESSION_DURATION = timedelta(days=400)


def _session_secret_key(environment_config: EnvironmentConfig | None = None):
    if environment_config is None:
        environment_config = load_environment_config()
    configured = environment_config.flask_secret_key
    flask_env = environment_config.flask_env
    if configured:
        return configured
    if flask_env == "production":
        raise RuntimeError("FLASK_SECRET_KEY must be configured in production.")
    logger.warning("FLASK_SECRET_KEY is not configured; using an ephemeral development key.")
    return secrets.token_hex(32)


def create_app():
    app = Flask(__name__)
    environment_config = load_environment_config()
    app.extensions[ENVIRONMENT_CONFIG_EXTENSION_KEY] = environment_config
    app.wsgi_app = ProxyFix(
        app.wsgi_app,
        x_for=1,
        x_proto=1,
        x_host=1,
        x_port=1,
        x_prefix=1,
    )
    from avatar_images import avatar_url_for_size

    app.jinja_env.filters["avatar_url"] = avatar_url_for_size
    app.secret_key = _session_secret_key(environment_config)
    from services.database import database_path, nest_instance_dir

    resolved_database_path = database_path(environment_config=environment_config)
    app.config["DATABASE_PATH"] = resolved_database_path
    app.config["CALENDAR_SQLITE_PATH"] = resolved_database_path
    app.config["MAX_CONTENT_LENGTH"] = 5 * 50 * 1024 * 1024
    app.config["FILE_SHARE_UPLOAD_DIR"] = os.path.join(app.root_path, "uploads", "file_share")
    allow_insecure_http = environment_config.allow_insecure_http
    app.config["SESSION_COOKIE_SECURE"] = not allow_insecure_http
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["AUTH_SESSION_DURATION"] = AUTH_SESSION_DURATION
    app.config["PERMANENT_SESSION_LIFETIME"] = AUTH_SESSION_DURATION
    app.config["REMEMBER_COOKIE_DURATION"] = AUTH_SESSION_DURATION
    app.config["REMEMBER_COOKIE_SECURE"] = app.config["SESSION_COOKIE_SECURE"]
    app.config["REMEMBER_COOKIE_HTTPONLY"] = True
    app.config["REMEMBER_COOKIE_SAMESITE"] = "Lax"
    app.config["PREFERRED_URL_SCHEME"] = "http" if allow_insecure_http else "https"
    app.config["WTF_CSRF_CHECK_DEFAULT"] = False
    app.config["FRONTEND_CONSOLE_DIAGNOSTICS_ENABLED"] = (
        environment_config.frontend_console_diagnostics_enabled
    )
    os.makedirs(app.config["FILE_SHARE_UPLOAD_DIR"], exist_ok=True)
    os.makedirs(app.instance_path, exist_ok=True)
    os.makedirs(nest_instance_dir(environment_config=environment_config), exist_ok=True)

    @app.get("/service-worker.js")
    def service_worker():
        response = send_from_directory(app.static_folder, "service-worker.js")
        response.headers["Cache-Control"] = "no-cache"
        response.headers["Service-Worker-Allowed"] = "/"
        return response

    @app.get("/manifest.json")
    def web_manifest():
        return send_from_directory(app.static_folder, "manifest.json", mimetype="application/manifest+json")

    # Initialize extensions
    from extensions import csrf, login_manager
    csrf.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    from flask_login import current_user
    from flask_wtf.csrf import CSRFError, generate_csrf
    with app.app_context():
        from blueprints.auth import LOGIN_NEXT_SESSION_KEY, _is_safe_login_next_url
    from services.database import close_db, init_db
    init_db(app)
    app.teardown_appcontext(close_db)

    @app.before_request
    def protect_authenticated_mutations():
        if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
            return None
        if request.blueprint == "admin":
            return None
        if current_user.is_authenticated:
            csrf.protect()
        return None

    @app.after_request
    def provide_csrf_cookie(response):
        if request.path.startswith("/static/images/brand/nest-logo-v1-"):
            response.cache_control.no_cache = None
            response.cache_control.no_store = None
            response.cache_control.public = True
            response.cache_control.max_age = 31536000
            response.cache_control.immutable = True
        if request.method in {"GET", "HEAD"} and response.mimetype == "text/html":
            response.set_cookie(
                "csrf_token",
                generate_csrf(),
                secure=app.config["SESSION_COOKIE_SECURE"],
                httponly=False,
                samesite="Lax",
            )
        return response

    @app.errorhandler(CSRFError)
    def handle_csrf_error(error):
        """Let browser clients distinguish token expiry from ordinary 400s."""
        response = app.make_response((error.description, 400))
        response.headers["X-APStudy-CSRF-Error"] = "1"
        response.headers["Cache-Control"] = "no-store"
        return response

    @login_manager.unauthorized_handler
    def handle_unauthorized():
        if "/api/" in request.path or request.path == "/api":
            response = jsonify({"error": "Authentication required."})
            response.headers["Cache-Control"] = "no-store"
            return response, 401

        next_url = request.full_path
        if _is_safe_login_next_url(next_url):
            session[LOGIN_NEXT_SESSION_KEY] = next_url
            return redirect(url_for("auth.login", next=next_url))
        return redirect(url_for("auth.login"))

    @app.before_request
    def track_authenticated_site_open():
        from flask_login import current_user

        if not current_user.is_authenticated:
            return None
        if request.endpoint == "static":
            return None

        now = datetime.now(timezone.utc)
        try:
            from services.admin_analytics import record_authenticated_activity

            record_authenticated_activity(str(current_user.id), at=now)
        except Exception:
            logger.exception("Failed to record authenticated activity for user %s", current_user.id)

        if request.method not in {"GET", "HEAD"}:
            return None
        if request.path.startswith("/api/") or not request.accept_mimetypes.accept_html:
            return None

        tracked_at = session.get("last_site_open_tracked_at")
        if tracked_at:
            try:
                previous = datetime.fromisoformat(str(tracked_at).replace("Z", "+00:00"))
                if previous.tzinfo is None:
                    previous = previous.replace(tzinfo=timezone.utc)
                if now - previous < timedelta(minutes=15):
                    return None
            except ValueError:
                logger.warning("Ignoring malformed last_site_open_tracked_at session value")

        try:
            from appwrite_client import COLLECTIONS
            from appwrite_helpers import format_datetime, update_row_safe

            timestamp = format_datetime(now)
            update_row_safe(COLLECTIONS["users"], str(current_user.id), {"last_login": timestamp})
            current_user.last_login = now
            session["last_site_open_tracked_at"] = timestamp
        except Exception:
            logger.exception(
                "Failed to track authenticated site open for user %s on %s",
                current_user.id,
                request.path,
            )
        return None

    @app.context_processor
    def inject_shell_preferences():
        from flask_login import current_user

        if not current_user.is_authenticated:
            return {
                "sidebar_default": "expanded",
                "avatar_src": avatar_url_for_size,
                "can_access_admin": False,
                "user_tier": None,
                "user_tier_label": None,
                "user_tier_badge": None,
                "frontend_console_diagnostics_enabled": app.config["FRONTEND_CONSOLE_DIAGNOSTICS_ENABLED"],
            }

        try:
            from appwrite.query import Query
            from appwrite_client import COLLECTIONS
            from appwrite_helpers import first_row

            if hasattr(g, "_apstudy_user_settings"):
                settings = g._apstudy_user_settings
            else:
                settings = first_row(
                    COLLECTIONS["user_settings"],
                    [Query.equal("user_id", [str(current_user.id)])],
                )
                g._apstudy_user_settings = settings
            raw_sidebar_default = settings.get("sidebar_default") if settings else ""
        except Exception:
            raw_sidebar_default = ""

        sidebar_default = str(raw_sidebar_default or "").strip().lower()
        if sidebar_default not in {"expanded", "collapsed"}:
            sidebar_default = "expanded"
        user_id = str(getattr(current_user, "id", "") or "")
        from services.admin_access import user_can_access_admin
        from services.entitlements import TIER_BADGES, TIER_LABELS, normalize_tier

        user_tier = normalize_tier(getattr(current_user, "tier", None))

        return {
            "sidebar_default": sidebar_default,
            "avatar_src": avatar_url_for_size,
            "can_access_admin": user_can_access_admin(user_id),
            "user_tier": user_tier,
            "user_tier_label": TIER_LABELS[user_tier],
            "user_tier_badge": TIER_BADGES.get(user_tier),
            "frontend_console_diagnostics_enabled": app.config["FRONTEND_CONSOLE_DIAGNOSTICS_ENABLED"],
        }

    # Register all blueprints
    from blueprints import register_blueprints
    register_blueprints(app)

    @app.route("/apple-touch-icon.png")
    def apple_touch_icon():
        return send_from_directory(
            app.static_folder,
            "images/brand/nest-logo-v1-180.png",
            mimetype="image/png",
            max_age=86400,
        )

    @app.errorhandler(RequestEntityTooLarge)
    def handle_request_entity_too_large(_error):
        if request.path.startswith("/api/"):
            return jsonify({"error": "Upload exceeds the maximum allowed size (50 MB per file)."}), 413
        return jsonify({"error": "Upload exceeds the maximum allowed size."}), 413

    @app.errorhandler(404)
    def page_not_found(error):
        if request.path.startswith("/api/"):
            return jsonify({"error": "not_found"}), 404

        from flask_login import current_user

        home_url = url_for("dashboard.dashboard") if current_user.is_authenticated else url_for("auth.login")
        return render_template("404.html", home_url=home_url), 404

    @app.cli.command("cleanup-files")
    def cleanup_files_command():
        from services.file_cleanup import cleanup_expired_files

        cleanup_expired_files()

    @app.cli.command("cleanup-notes-collaboration")
    def cleanup_notes_collaboration_command():
        from services.notes_collaboration import cleanup_expired_collaboration_rows

        result = cleanup_expired_collaboration_rows()
        print(
            "Expired note invitations: {invitations_expired}; expired note versions deleted: {versions_deleted}".format(
                **result
            )
        )

    @app.cli.command("backup-db")
    def backup_db_command():
        from pathlib import Path

        from scripts.backup_nest_db import run_backup
        from services.database import nest_instance_dir

        configured = load_environment_config()
        instance_dir = Path(nest_instance_dir())
        backup_dir = Path(configured.nest_backup_dir)
        max_backups = int(configured.nest_backup_retention_raw)
        raise SystemExit(
            run_backup(
                instance_dir=instance_dir,
                backup_dir=backup_dir,
                max_backups=max_backups,
                notify_discord=True,
            )
        )

    from services.discord_audit import init_discord_audit
    init_discord_audit(app)
    from services.scheduler import init_scheduler
    init_scheduler(app)

    return app

if __name__ == "__main__":
    app = create_app()
    app.run("localhost", 5000, debug=True)
