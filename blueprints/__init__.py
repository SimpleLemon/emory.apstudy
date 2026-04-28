"""
blueprints/__init__.py

Central registration point for all Flask blueprints.
Called once from the application factory in app.py.
"""

from blueprints.auth import auth_bp
from blueprints.dashboard import dashboard_bp
from blueprints.atlas_api import atlas_bp
from blueprints.calendar_api import calendar_bp
from blueprints.file_share import file_share_bp
from blueprints.settings import settings_bp


def register_blueprints(app):
    """Register all blueprints on the Flask application instance."""
    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(atlas_bp, url_prefix="/api/atlas")
    app.register_blueprint(calendar_bp, url_prefix="/api/calendar")
    app.register_blueprint(file_share_bp)
    app.register_blueprint(settings_bp, url_prefix="/settings")