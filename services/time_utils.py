"""Shared UTC timestamp helpers."""

from datetime import datetime, timezone


def utcnow():
    return datetime.now(timezone.utc)


def utcnow_iso():
    return utcnow().isoformat().replace("+00:00", "Z")
