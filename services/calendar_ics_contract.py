"""Frozen contracts for the future single-calendar ICS projector and serializer.

This module contains only data contracts and deterministic helpers. It does
not fetch source events, project them, or serialize an ICS document.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from enum import Enum
import hashlib
import hmac
from typing import Any, Final, Mapping

from services.environment_config import runtime_environment_config


CANVAS_CALENDAR_ID = "canvas"
TASKS_CALENDAR_ID = "tasks"
SIMULATED_COURSES_CALENDAR_ID = "simulated_courses"
ELIGIBLE_CALENDAR_IDS = frozenset({
    CANVAS_CALENDAR_ID,
    TASKS_CALENDAR_ID,
    SIMULATED_COURSES_CALENDAR_ID,
})

ICS_PAST_DAYS = 30
ICS_FUTURE_DAYS = 366
ICS_RANGE_END_DAYS = ICS_FUTURE_DAYS + 1
ICS_TIMEZONE = "UTC"
ICS_UID_POLICY_VERSION = 1
ICS_METHOD = "PUBLISH"
ICS_UID_SECRET_MIN_BYTES = 32

# A dedicated immutable configuration value. It is never derived from Flask,
# session, or any other application secret.
CALENDAR_ICS_UID_SECRET: Final[str | None] = runtime_environment_config().calendar_ics_uid_secret


class CalendarIcsContractError(ValueError):
    """A fail-closed contract or configuration error."""


class CalendarIcsFailureCode(str, Enum):
    """Stable machine-readable outcomes shared by lifecycle and feed phases."""

    DISABLED = "calendar_ics_disabled"
    INELIGIBLE_SELECTION = "calendar_ics_ineligible_selection"
    SELECTION_LOCKED = "calendar_ics_selection_locked"
    NOT_FOUND = "calendar_ics_not_found"
    INVALID_TOKEN = "calendar_ics_invalid_token"
    SUSPENDED = "calendar_ics_suspended"
    PARENT_REVOKED = "calendar_ics_parent_revoked"


class CalendarIcsFailure(ValueError):
    """Typed, stable failure for owner lifecycle and future feed lookups."""

    def __init__(self, code: CalendarIcsFailureCode | str, message: str, *, status: int = 400):
        if isinstance(code, CalendarIcsFailureCode):
            self.code = code
        else:
            try:
                self.code = CalendarIcsFailureCode(code)
            except (TypeError, ValueError):
                self.code = str(code)
        self.message = message
        self.status = status
        super().__init__(message)

    def payload(self) -> dict[str, Any]:
        code = self.code.value if isinstance(self.code, Enum) else self.code
        return {"error": self.message, "code": code}


@dataclass(frozen=True, slots=True)
class CalendarIcsOutcome:
    """Typed lifecycle result; ``share`` is never exposed directly to clients."""

    share: Mapping[str, Any]
    action: str


def _require_text(name: str, value: Any, *, allow_empty: bool = True) -> None:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise CalendarIcsContractError(f"{name} must be a string.")


def _require_optional_text(name: str, value: Any) -> None:
    if value is not None:
        _require_text(name, value)


def _require_utc_datetime(name: str, value: Any) -> None:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise CalendarIcsContractError(f"{name} must be a timezone-aware UTC datetime.")


@dataclass(frozen=True, slots=True)
class NormalizedCalendarEvent:
    """Secret-free normalized event shape emitted by future source projectors."""

    uid: str
    calendar_id: str
    source_type: str
    title: str
    start: datetime | date
    end: datetime | date
    is_all_day: bool
    description: str | None = None
    location: str | None = None
    event_type: str | None = None
    course_name: str | None = None
    course_type: str | None = None
    priority: str | None = None
    completed: bool | None = None
    reminder_minutes: int | None = None
    course_code: str | None = None
    course_title: str | None = None
    section: str | None = None
    instructor: str | None = None
    course_location: str | None = None
    notes: str | None = None
    crn: str | None = None
    last_modified: datetime | None = None
    # Raw source identity is accepted only by ``from_internal`` and is never
    # stored on or emitted from the normalized event.
    _raw_identity: str | bytes | None = field(default=None, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        _require_text("uid", self.uid, allow_empty=False)
        _require_text("calendar_id", self.calendar_id, allow_empty=False)
        _require_text("source_type", self.source_type, allow_empty=False)
        _require_text("title", self.title)
        if self.calendar_id not in ELIGIBLE_CALENDAR_IDS:
            raise CalendarIcsContractError("calendar_id must be an eligible canonical calendar.")
        if type(self.is_all_day) is not bool:
            raise CalendarIcsContractError("is_all_day must be a boolean.")
        if self.is_all_day:
            if type(self.start) is not date or type(self.end) is not date:
                raise CalendarIcsContractError("All-day start and end must be datetime.date values.")
        else:
            _require_utc_datetime("start", self.start)
            _require_utc_datetime("end", self.end)
        if self.end <= self.start:
            raise CalendarIcsContractError("Event end must be after event start.")
        for name in (
            "description", "location", "event_type", "course_name", "course_type", "priority",
            "course_code", "course_title", "section", "instructor", "course_location",
            "notes", "crn",
        ):
            _require_optional_text(name, getattr(self, name))
        if self.completed is not None and type(self.completed) is not bool:
            raise CalendarIcsContractError("completed must be a boolean or None.")
        if self.reminder_minutes is not None and (
            type(self.reminder_minutes) is not int or self.reminder_minutes < -1
        ):
            raise CalendarIcsContractError("reminder_minutes must be an integer of -1 or greater.")
        if self.last_modified is not None:
            _require_utc_datetime("last_modified", self.last_modified)

    @classmethod
    def from_internal(cls, *, raw_identity: str | bytes, **event_fields: Any) -> "NormalizedCalendarEvent":
        """Construct an event from private source identity without retaining it."""

        event_fields["uid"] = build_calendar_ics_uid(event_fields.get("calendar_id"), raw_identity)
        return cls(**event_fields)


class CalendarIcsProjectionStatus(str, Enum):
    SUCCESS = "success"
    VALID_EMPTY = "valid_empty"
    SOURCE_FAILURE = "source_failure"
    RESOURCE_FAILURE = "resource_failure"


ProjectionOutcome = CalendarIcsProjectionStatus


class CalendarIcsDiagnosticCode(str, Enum):
    SOURCE_UNAVAILABLE = "source_unavailable"
    SOURCE_INVALID = "source_invalid"
    RESOURCE_UNAVAILABLE = "resource_unavailable"


def _forward_safe_diagnostic_code(value: Any) -> CalendarIcsDiagnosticCode | str | None:
    if value is None or isinstance(value, CalendarIcsDiagnosticCode):
        return value
    try:
        return CalendarIcsDiagnosticCode(value)
    except (TypeError, ValueError):
        return str(value)


@dataclass(frozen=True, slots=True)
class CalendarIcsProjectionOutcome:
    """Forward-safe projector result contract for future source fan-out."""

    status: CalendarIcsProjectionStatus
    events: tuple[NormalizedCalendarEvent, ...] = ()
    diagnostic_code: CalendarIcsDiagnosticCode | str | None = None
    diagnostic_text: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "diagnostic_code", _forward_safe_diagnostic_code(self.diagnostic_code))

    @classmethod
    def success(cls, events: tuple[NormalizedCalendarEvent, ...] = ()) -> "CalendarIcsProjectionOutcome":
        return cls(CalendarIcsProjectionStatus.SUCCESS, tuple(events))

    @classmethod
    def valid_empty(cls) -> "CalendarIcsProjectionOutcome":
        return cls(CalendarIcsProjectionStatus.VALID_EMPTY)

    @classmethod
    def source_failure(cls, diagnostic_code: Any, diagnostic_text: str) -> "CalendarIcsProjectionOutcome":
        return cls(CalendarIcsProjectionStatus.SOURCE_FAILURE, diagnostic_code=diagnostic_code, diagnostic_text=diagnostic_text)

    @classmethod
    def resource_failure(cls, diagnostic_code: Any, diagnostic_text: str) -> "CalendarIcsProjectionOutcome":
        return cls(CalendarIcsProjectionStatus.RESOURCE_FAILURE, diagnostic_code=diagnostic_code, diagnostic_text=diagnostic_text)


@dataclass(frozen=True, slots=True)
class CalendarIcsUidPolicy:
    """Stable UID policy; changing this requires a contract version bump."""

    version: int = ICS_UID_POLICY_VERSION
    algorithm: str = "HMAC-SHA256(calendar-id, private-source-identity)"
    must_be_stable_across_refresh: bool = True
    must_not_include_secret: bool = True
    must_not_include_raw_identity: bool = True


@dataclass(frozen=True, slots=True)
class CalendarIcsSerializerContract:
    """Explicit later serializer metadata contract; no serializer is implemented."""

    method: str = ICS_METHOD
    method_line: str = "METHOD:PUBLISH"
    omit_sequence: bool = True
    omit_vtimezone: bool = True
    weak_etag: bool = True
    etag_excluded_properties: tuple[str, ...] = ("DTSTAMP",)
    timezone: str = ICS_TIMEZONE
    past_days: int = ICS_PAST_DAYS
    future_days: int = ICS_FUTURE_DAYS
    exclusive_end_days_from_today: int = ICS_RANGE_END_DAYS
    range_end_exclusive: bool = True
    required_event_fields: tuple[str, ...] = (
        "uid", "calendar_id", "source_type", "title", "start", "end", "is_all_day",
    )


UID_POLICY = CalendarIcsUidPolicy()
SERIALIZER_CONTRACT = CalendarIcsSerializerContract()


def _uid_secret_bytes() -> bytes:
    secret = CALENDAR_ICS_UID_SECRET
    if not isinstance(secret, str) or not secret.strip():
        raise CalendarIcsContractError("CALENDAR_ICS_UID_SECRET must be configured.")
    encoded = secret.encode("utf-8")
    if len(encoded) < ICS_UID_SECRET_MIN_BYTES or any(char.isspace() or ord(char) < 32 for char in secret):
        raise CalendarIcsContractError(
            f"CALENDAR_ICS_UID_SECRET must be at least {ICS_UID_SECRET_MIN_BYTES} safe bytes."
        )
    return encoded


def build_calendar_ics_uid(calendar_id: Any, raw_identity: str | bytes) -> str:
    """Build a stable UID from private identity without exposing that identity."""

    canonical_id = canonical_calendar_id(calendar_id)
    if canonical_id is None:
        raise CalendarIcsContractError("calendar_id must be an eligible canonical calendar.")
    if isinstance(raw_identity, str):
        identity = raw_identity.encode("utf-8")
    elif isinstance(raw_identity, bytes):
        identity = raw_identity
    else:
        raise CalendarIcsContractError("raw_identity must be private string or bytes input.")
    if not identity:
        raise CalendarIcsContractError("raw_identity must not be empty.")
    message = (
        b"nest-calendar-ics\0"
        + str(ICS_UID_POLICY_VERSION).encode("ascii")
        + b"\0"
        + canonical_id.encode("utf-8")
        + b"\0"
        + identity
    )
    digest = hmac.new(_uid_secret_bytes(), message, hashlib.sha256).hexdigest()
    return f"nest-ics-v{ICS_UID_POLICY_VERSION}-{digest}"


def subscription_window(today: date | None = None) -> tuple[date, date]:
    """Return the fixed UTC-date, half-open projection window."""

    today = today or datetime.now(timezone.utc).date()
    return today - timedelta(days=ICS_PAST_DAYS), today + timedelta(days=ICS_RANGE_END_DAYS)


def canonical_calendar_id(value: Any) -> str | None:
    """Normalize current browser IDs to the frozen ICS identifier set."""

    candidate = str(value or "").strip()
    aliases = {
        "local:tasks": TASKS_CALENDAR_ID,
        "Simulated Courses": SIMULATED_COURSES_CALENDAR_ID,
        "simulated courses": SIMULATED_COURSES_CALENDAR_ID,
    }
    candidate = aliases.get(candidate, candidate)
    return candidate if candidate in ELIGIBLE_CALENDAR_IDS else None


def normalized_calendar_event_payload(event: NormalizedCalendarEvent) -> dict[str, Any]:
    """Return the explicit user-facing shape, excluding private identity."""

    return {
        "uid": event.uid,
        "calendar_id": event.calendar_id,
        "source_type": event.source_type,
        "title": event.title,
        "start": event.start,
        "end": event.end,
        "is_all_day": event.is_all_day,
        "description": event.description,
        "location": event.location,
        "event_type": event.event_type,
        "course_name": event.course_name,
        "course_type": event.course_type,
        "priority": event.priority,
        "completed": event.completed,
        "reminder_minutes": event.reminder_minutes,
        "course_code": event.course_code,
        "course_title": event.course_title,
        "section": event.section,
        "instructor": event.instructor,
        "course_location": event.course_location,
        "notes": event.notes,
        "crn": event.crn,
        "last_modified": event.last_modified,
    }
