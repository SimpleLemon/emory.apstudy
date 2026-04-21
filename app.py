import os
from flask import Flask
from dotenv import load_dotenv

load_dotenv()

def create_app():
    app = Flask(__name__)
    app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-fallback-key")
    app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
        "DATABASE_URI", "sqlite:///data/emory_apstudy.sqlite"
    )
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    # Initialize extensions
    from extensions import db, login_manager
    db.init_app(app)
    login_manager.init_app(app)

    # Register all blueprints
    from blueprints import register_blueprints
    register_blueprints(app)

    from services.scheduler import init_scheduler
    init_scheduler(app)

    # Create database tables on first run
    with app.app_context():
        from models import User, UserSettings, UserCourse, CalendarCache
        db.create_all()

    return app

if __name__ == "__main__":
    os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
    app = create_app()
    app.run("localhost", 5000, debug=True)