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
    onboarding_complete = db.Column(db.Boolean, default=False, nullable=False)
    onboarding_step = db.Column(db.Integer, default=1, nullable=False)
    class_year = db.Column(db.String(64), nullable=True)
    school_college = db.Column(db.String(255), nullable=True)
    majors_json = db.Column(db.Text, nullable=True)
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
    user_created_events = db.relationship(
        "UserEvent",
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
    other_ical_urls_json = db.Column(db.Text, nullable=True)
    ics_secret_token = db.Column(db.String(255), unique=True, nullable=True, index=True)
    feed_refresh_minutes = db.Column(db.Integer, default=15, nullable=False)
    preferred_calendar_view = db.Column(db.String(16), default="week", nullable=False)
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
    course_name = db.Column(db.String(255), nullable=True)
    section_number = db.Column(db.String(64), nullable=True)
    instructor_name = db.Column(db.String(255), nullable=True)
    source = db.Column(db.String(32), default="settings", nullable=False)
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
    is_all_day = db.Column(db.Boolean, default=False, nullable=False)
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


class UserCalendarPreference(db.Model):
    __tablename__ = "user_calendar_preferences"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )


class UserEvent(db.Model):
    __tablename__ = "user_events"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, nullable=True)
    start = db.Column(db.DateTime, nullable=False, index=True)
    end = db.Column(db.DateTime, nullable=False)
    is_all_day = db.Column(db.Boolean, default=False, nullable=False)
    color = db.Column(db.String(7), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, nullable=True)

    user = db.relationship("User", back_populates="user_created_events")
    calendar_name = db.Column(db.String(255), nullable=False)
    color_hex = db.Column(db.String(7), default="#6366f1", nullable=False)
    visible = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, nullable=True)

    __table_args__ = (
        db.UniqueConstraint(
            "user_id",
            "calendar_name",
            name="uq_user_calendar_pref_user_calendar",
        ),
    )


class SharedFile(db.Model):
    __tablename__ = "shared_files"

    id = db.Column(db.String(36), primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    original_filename = db.Column(db.String(255), nullable=False)
    stored_path = db.Column(db.String(512), nullable=False)
    file_size_bytes = db.Column(db.Integer, nullable=False)
    mime_type = db.Column(db.String(127), nullable=True)
    share_code = db.Column(db.String(10), unique=True, nullable=True, index=True)
    is_public = db.Column(db.Boolean, default=False, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    downloaded_count = db.Column(db.Integer, default=0, nullable=False)


@login_manager.user_loader
def load_user(user_id):
    if not user_id:
        return None
    return User.query.get(int(user_id))
