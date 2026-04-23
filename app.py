import os
import sqlite3
from flask import Flask
from dotenv import load_dotenv

load_dotenv()

# Flask CLI imports this module without executing __main__, so set this here
# for local HTTP OAuth callbacks in development environments.
os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")


BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _resolve_database_uri(raw_uri):
    if raw_uri and raw_uri.startswith("sqlite:///") and not raw_uri.startswith("sqlite:////"):
        relative_path = raw_uri.replace("sqlite:///", "", 1)
        return f"sqlite:///{os.path.join(BASE_DIR, relative_path)}"
    return raw_uri


def _database_path_from_uri(database_uri):
    if not database_uri or not database_uri.startswith("sqlite:///"):
        return None
    if database_uri.startswith("sqlite:////"):
        return database_uri.replace("sqlite:////", "/", 1)
    return database_uri.replace("sqlite:///", "", 1)


def _repair_sqlite_database(database_uri):
    database_path = _database_path_from_uri(database_uri)
    if not database_path:
        return

    os.makedirs(os.path.dirname(database_path), exist_ok=True)

    if not os.path.exists(database_path):
        return

    try:
        with sqlite3.connect(database_path) as connection:
            connection.execute("PRAGMA schema_version;")
    except sqlite3.DatabaseError:
        os.remove(database_path)


def _sqlite_table_columns(connection, table_name):
    cursor = connection.execute(f'PRAGMA table_info("{table_name}")')
    return {row[1] for row in cursor.fetchall()}


def _ensure_sqlite_column(connection, table_name, column_name, column_definition):
    columns = _sqlite_table_columns(connection, table_name)
    if column_name in columns:
        return
    connection.execute(
        f'ALTER TABLE "{table_name}" ADD COLUMN {column_definition}'
    )


def _ensure_sqlite_schema(database_uri):
    database_path = _database_path_from_uri(database_uri)
    if not database_path or not os.path.exists(database_path):
        return

    with sqlite3.connect(database_path) as connection:
        _ensure_sqlite_column(
            connection,
            "users",
            "onboarding_complete",
            "onboarding_complete INTEGER NOT NULL DEFAULT 0",
        )
        _ensure_sqlite_column(
            connection,
            "users",
            "onboarding_step",
            "onboarding_step INTEGER NOT NULL DEFAULT 1",
        )
        _ensure_sqlite_column(
            connection,
            "users",
            "class_year",
            "class_year VARCHAR(64)",
        )
        _ensure_sqlite_column(
            connection,
            "users",
            "school_college",
            "school_college VARCHAR(255)",
        )
        _ensure_sqlite_column(
            connection,
            "users",
            "majors_json",
            "majors_json TEXT",
        )
        _ensure_sqlite_column(
            connection,
            "user_courses",
            "course_name",
            "course_name VARCHAR(255)",
        )
        _ensure_sqlite_column(
            connection,
            "user_courses",
            "section_number",
            "section_number VARCHAR(64)",
        )
        _ensure_sqlite_column(
            connection,
            "user_courses",
            "instructor_name",
            "instructor_name VARCHAR(255)",
        )
        _ensure_sqlite_column(
            connection,
            "user_courses",
            "source",
            "source VARCHAR(32) NOT NULL DEFAULT 'settings'",
        )
        _ensure_sqlite_column(
            connection,
            "user_settings",
            "other_ical_urls_json",
            "other_ical_urls_json TEXT",
        )
        connection.commit()

def create_app():
    app = Flask(__name__)
    app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-fallback-key")
    database_uri = _resolve_database_uri(
        os.environ.get(
            "DATABASE_URI",
            f"sqlite:///{os.path.join(BASE_DIR, 'data', 'emory_apstudy.sqlite')}",
        )
    )
    _repair_sqlite_database(database_uri)
    app.config["SQLALCHEMY_DATABASE_URI"] = database_uri
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
        _ensure_sqlite_schema(database_uri)

    return app

if __name__ == "__main__":
    app = create_app()
    app.run("localhost", 5000, debug=True)