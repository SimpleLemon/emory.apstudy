"""Session-backed toast/flash queue.

Lets any backend view (typically before a redirect) queue a toast. HTML
pages consume the queue once via ``consume_request_toasts()`` and embed it
for ``window.APStudyToast``. ``GET /api/toasts`` is only a fallback.
"""

import uuid
from datetime import datetime, timezone

from flask import g, has_request_context, session

_SESSION_KEY = "_toast_queue"
_VALID_TYPES = {"info", "success", "warning", "error"}
_MAX_QUEUE = 8


def push_toast(message, *, type="info", title=None, action=None, duration=None):
    """Queue a toast to be shown on the user's next page load.

    Args:
        message: Body text of the toast (required).
        type: One of "info", "success", "warning", "error".
        title: Optional bold heading.
        action: Optional dict, e.g. {"label": "Undo", "href": "/..."}.
        duration: Optional auto-dismiss duration in milliseconds.
    """
    message = "" if message is None else str(message)
    if not message:
        return

    toast_type = str(type or "info").lower()
    if toast_type not in _VALID_TYPES:
        toast_type = "info"

    entry = {
        "id": str(uuid.uuid4()),
        "message": message,
        "type": toast_type,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if title:
        entry["title"] = str(title)
    if isinstance(action, dict) and (action.get("label") or action.get("text")):
        normalized = {"label": str(action.get("label") or action.get("text"))}
        if action.get("href"):
            normalized["href"] = str(action["href"])
        entry["action"] = normalized
    if isinstance(duration, (int, float)) and duration >= 0:
        entry["duration"] = int(duration)

    queue = session.get(_SESSION_KEY)
    if not isinstance(queue, list):
        queue = []
    queue.append(entry)
    session[_SESSION_KEY] = queue[-_MAX_QUEUE:]
    session.modified = True


def pop_toasts():
    """Return and clear all queued toasts for the current session."""
    queue = session.pop(_SESSION_KEY, None)
    if not isinstance(queue, list):
        return []
    return queue


def consume_request_toasts():
    """Pop the session queue once per request for HTML embedding."""
    if has_request_context() and getattr(g, "_server_toasts_loaded", False):
        cached = getattr(g, "server_toasts", None)
        return list(cached) if isinstance(cached, list) else []
    toasts = pop_toasts()
    if has_request_context():
        g.server_toasts = toasts
        g._server_toasts_loaded = True
    return toasts
