from flask import Blueprint

from extensions import login_manager
from services import database


def reset_flask_login_manager():
    login_manager.unauthorized_callback = None
    login_manager.login_view = None


def bootstrap_calendar_db(db_path):
    database.init_db(path=db_path)


def register_shell_route_stubs(app):
    dashboard_bp = Blueprint("dashboard", __name__)

    @dashboard_bp.route("/dashboard")
    def dashboard():
        return "ok"

    @dashboard_bp.route("/calendar")
    def calendar():
        return "ok"

    @dashboard_bp.route("/courses")
    def courses():
        return "ok"

    @dashboard_bp.route("/notes")
    def notes():
        return "ok"

    @dashboard_bp.route("/tasks")
    def tasks():
        return "ok"

    @dashboard_bp.route("/chat")
    def chat():
        return "ok"

    file_share_bp = Blueprint("file_share", __name__)

    @file_share_bp.route("/files")
    def file_share_page():
        return "ok"

    settings_bp = Blueprint("settings", __name__)

    @settings_bp.route("/settings")
    def settings_page():
        return "ok"

    admin_bp = Blueprint("admin", __name__)

    @admin_bp.route("/admin")
    def admin_index():
        return "ok"

    app.register_blueprint(dashboard_bp)
    app.register_blueprint(file_share_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(admin_bp)

    @app.context_processor
    def inject_shell_context():
        return {
            "can_access_admin": False,
            "sidebar_default": "expanded",
        }
