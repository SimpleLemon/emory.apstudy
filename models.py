from datetime import datetime

from flask_login import UserMixin

from extensions import db, login_manager


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    google_id = db.Column(db.String(255), unique=True, nullable=False, index=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    name = db.Column(db.String(255), nullable=True)
    picture_url = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    last_login = db.Column(db.DateTime, nullable=True)

    settings = db.relationship(
        "UserSettings",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
    )
    courses = db.relationship(
        "UserCourse",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    calendar_events = db.relationship(
        "CalendarCache",
        back_populates="user",
        cascade="all, delete-orphan",
    )


class UserSettings(db.Model):
    __tablename__ = "user_settings"

    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    canvas_ical_url = db.Column(db.Text, nullable=True)
    ics_secret_token = db.Column(db.String(255), unique=True, nullable=True, index=True)
    feed_refresh_minutes = db.Column(db.Integer, default=15, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, nullable=True)

    user = db.relationship("User", back_populates="settings")


class UserCourse(db.Model):
    __tablename__ = "user_courses"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    term = db.Column(db.String(64), nullable=False)
    subject = db.Column(db.String(64), nullable=False)
    catalog = db.Column(db.String(64), nullable=False)
    crn = db.Column(db.String(64), nullable=True)
    added_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    user = db.relationship("User", back_populates="courses")

    __table_args__ = (
        db.UniqueConstraint(
            "user_id",
            "term",
            "subject",
            "catalog",
            "crn",
            name="uq_user_courses_user_term_subject_catalog_crn",
        ),
    )


class CalendarCache(db.Model):
    __tablename__ = "calendar_cache"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_uid = db.Column(db.String(255), nullable=True)
    event_title = db.Column(db.Text, nullable=True)
    event_start = db.Column(db.DateTime, nullable=True, index=True)
    event_end = db.Column(db.DateTime, nullable=True)
    event_type = db.Column(db.String(64), nullable=True)
    course_name = db.Column(db.String(255), nullable=True)
    raw_description = db.Column(db.Text, nullable=True)
    fetched_at = db.Column(db.DateTime, nullable=True, index=True)

    user = db.relationship("User", back_populates="calendar_events")

    __table_args__ = (
        db.UniqueConstraint(
            "user_id",
            "event_uid",
            name="uq_calendar_cache_user_event_uid",
        ),
    )


@login_manager.user_loader
def load_user(user_id):
    if not user_id:
        return None
    return User.query.get(int(user_id))
