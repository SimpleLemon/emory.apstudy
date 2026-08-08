import hashlib
import json
import logging
import os
import random
import sys
import threading
import time
import uuid
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone

import requests

from services.discord_constants import DEFAULT_GUILD_ID, DISCORD_API_BASE
from services.redaction import SECRET_TEXT_RE

logger = logging.getLogger(__name__)

DEFAULT_CHANNEL_IDS = {
    "admin": "1508544226491633834",
    "course_tracks": "1508544241679335555",
    "creation": "1508544277318467685",
    "chat_deletes": "1508949346639675543",
    "user_logs": "1509036183865262100",
    "server_logs": "1509603923433099356",
    "console_logs": "1517608382591139951",
}
COLOR_VALUES = {
    "red": 0xED4245,
    "green": 0x57F287,
    "yellow": 0xFEE75C,
    "gray": 0x95A5A6,
}

MAX_METADATA_CHARS = 950
MAX_FIELD_CHARS = 1024
MAX_DISCORD_CONTENT_CHARS = 2000
MAX_BROWSER_CONSOLE_LINES = 500
BROWSER_CONSOLE_FLUSH_SECONDS = 1.0
MAX_RETRY_ATTEMPTS = 15
MAX_QUEUE_EVENTS = 200
INVALID_WINDOW_SECONDS = 10 * 60
INVALID_PAUSE_SECONDS = 10 * 60
INVALID_PAUSE_THRESHOLD = 50
def _event_nonce(event):
    return hashlib.sha256(event.event_id.encode("utf-8")).hexdigest()[:25]


def _utcnow_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _env_channel_id(channel):
    env_name = {
        "admin": "DISCORD_AUDIT_ADMIN_CHANNEL_ID",
        "course_tracks": "DISCORD_AUDIT_COURSE_TRACKS_CHANNEL_ID",
        "creation": "DISCORD_AUDIT_CREATION_CHANNEL_ID",
        "chat_deletes": "DISCORD_AUDIT_CHAT_DELETES_CHANNEL_ID",
        "user_logs": "DISCORD_AUDIT_USER_LOGS_CHANNEL_ID",
        "server_logs": "DISCORD_AUDIT_SERVER_LOGS_CHANNEL_ID",
        "console_logs": "DISCORD_AUDIT_CONSOLE_LOGS_CHANNEL_ID",
    }.get(channel)
    return (os.environ.get(env_name or "") or DEFAULT_CHANNEL_IDS.get(channel) or "").strip()


def _bot_token():
    return (os.environ.get("DISCORD_BOT_TOKEN") or "").strip()


def _fallback_line_count(path):
    if not path or not os.path.exists(path):
        return 0
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return sum(1 for line in handle if line.strip())
    except OSError:
        logger.exception("Failed to count Discord audit fallback events")
        return None


def _truncate(value, limit=MAX_FIELD_CHARS):
    text = "" if value is None else str(value)
    if len(text) <= limit:
        return text
    return f"{text[: max(0, limit - 1)]}..."


def _sanitize_error_text(value, limit=500):
    text = SECRET_TEXT_RE.sub(r"\1[redacted]", str(value or ""))
    text = " ".join(text.split())
    return _truncate(text, limit)


def _console_log_enabled():
    return os.environ.get("DISCORD_CONSOLE_LOG_ENABLED", "1") != "0"


def _server_console_log_enabled():
    return _console_log_enabled() and os.environ.get("DISCORD_SERVER_CONSOLE_LOG_ENABLED", "1") != "0"


def _format_console_block(header, lines):
    header = _truncate(header, 400)
    body = "\n".join(str(line) for line in lines if str(line).strip())
    body = SECRET_TEXT_RE.sub(r"\1[redacted]", body)
    fence_overhead = len("```text\n\n```")
    max_body_chars = max(0, MAX_DISCORD_CONTENT_CHARS - len(header) - 1 - fence_overhead)
    if len(body) > max_body_chars:
        body = f"{body[: max(0, max_body_chars - 18)]}\n… (truncated)"
    return f"{header}\n```text\n{body}\n```"


def _format_browser_console_content(*, actor, page, lines):
    return _format_console_block(f"**Browser console** · `{page}` · {actor}", lines)


@dataclass
class _BrowserConsoleBatch:
    actor: str
    page: str
    lines: list[str] = field(default_factory=list)
    next_flush: float = 0.0
    header: str | None = None


_browser_console_batches: dict[str, _BrowserConsoleBatch] = {}
_browser_console_lock = threading.RLock()

SERVER_CONSOLE_BATCH_KEY = "__server_console__"


def queue_browser_console_lines(*, actor, page, lines):
    if not _console_log_enabled():
        return False
    if not lines:
        return False

    service = get_audit_service()
    if not _bot_token():
        return False

    key = f"{actor}|{page}"
    now = time.monotonic()
    with _browser_console_lock:
        batch = _browser_console_batches.get(key)
        if batch is None:
            batch = _BrowserConsoleBatch(actor=actor, page=page)
            _browser_console_batches[key] = batch
        batch.lines.extend(lines)
        if len(batch.lines) > MAX_BROWSER_CONSOLE_LINES:
            batch.lines = batch.lines[-MAX_BROWSER_CONSOLE_LINES:]
        batch.next_flush = now + BROWSER_CONSOLE_FLUSH_SECONDS

    service.wake_event.set()
    return True


def queue_server_console_lines(lines):
    if not _server_console_log_enabled() or not lines:
        return False
    if not _bot_token():
        return False

    service = get_audit_service()
    now = time.monotonic()
    with _browser_console_lock:
        batch = _browser_console_batches.get(SERVER_CONSOLE_BATCH_KEY)
        if batch is None:
            batch = _BrowserConsoleBatch(actor="System", page="server", header="**Server console**")
            _browser_console_batches[SERVER_CONSOLE_BATCH_KEY] = batch
        batch.lines.extend(lines)
        if len(batch.lines) > MAX_BROWSER_CONSOLE_LINES:
            batch.lines = batch.lines[-MAX_BROWSER_CONSOLE_LINES:]
        batch.next_flush = now + BROWSER_CONSOLE_FLUSH_SECONDS

    service.wake_event.set()
    return True


def _flush_browser_console_batch(batch_key, service=None):
    service = service or get_audit_service()
    with _browser_console_lock:
        batch = _browser_console_batches.pop(batch_key, None)
    if not batch or not batch.lines:
        return False

    if batch.header:
        content = _format_console_block(batch.header, batch.lines)
    else:
        content = _format_browser_console_content(
            actor=batch.actor,
            page=batch.page,
            lines=batch.lines,
        )
    with _suppress_console_capture():
        return service.emit_console_content(content)


def flush_due_browser_console_batches():
    service = get_audit_service()
    now = time.monotonic()
    with _browser_console_lock:
        due_keys = [
            batch_key
            for batch_key, batch in _browser_console_batches.items()
            if batch.next_flush <= now
        ]
    for batch_key in due_keys:
        _flush_browser_console_batch(batch_key, service)


def seconds_until_browser_console_flush(now=None):
    now = time.monotonic() if now is None else now
    with _browser_console_lock:
        if not _browser_console_batches:
            return None
        return max(0.0, min(batch.next_flush - now for batch in _browser_console_batches.values()))


_console_capture_state = threading.local()


def _console_capture_suppressed():
    return getattr(_console_capture_state, "suppressed", False)


@contextmanager
def _suppress_console_capture():
    previous = getattr(_console_capture_state, "suppressed", False)
    _console_capture_state.suppressed = True
    try:
        yield
    finally:
        _console_capture_state.suppressed = previous


class _ConsoleStreamTee:
    """Wraps a real stdout/stderr stream and mirrors complete lines to Discord."""

    def __init__(self, stream):
        self._stream = stream
        self._buffer = ""
        self._buffer_lock = threading.Lock()

    @property
    def wrapped_stream(self):
        return self._stream

    def write(self, data):
        result = self._stream.write(data)
        if not _console_capture_suppressed() and isinstance(data, str) and data:
            self._ingest(data)
        return result

    def _ingest(self, data):
        lines = []
        with self._buffer_lock:
            self._buffer += data
            if "\n" not in self._buffer:
                if len(self._buffer) > 8000:
                    lines.append(self._buffer)
                    self._buffer = ""
                return
            parts = self._buffer.split("\n")
            self._buffer = parts.pop()
            lines.extend(part for part in parts if part.strip())
        if not lines:
            return
        with _suppress_console_capture():
            try:
                queue_server_console_lines(lines)
            except Exception:
                logger.exception("Failed to queue captured server console lines")

    def flush(self):
        return self._stream.flush()

    def isatty(self):
        return getattr(self._stream, "isatty", lambda: False)()

    def fileno(self):
        return self._stream.fileno()

    def writable(self):
        return True

    def __getattr__(self, name):
        return getattr(self._stream, name)


def init_server_console_forwarding(app):
    if not _server_console_log_enabled():
        return False
    if not _bot_token():
        return False
    if getattr(app, "extensions", None) is not None and app.extensions.get("discord_server_console"):
        return False

    installed = False
    original_stdout = sys.stdout
    original_stderr = sys.stderr
    if not isinstance(sys.stdout, _ConsoleStreamTee):
        sys.stdout = _ConsoleStreamTee(sys.stdout)
        installed = True
    if not isinstance(sys.stderr, _ConsoleStreamTee):
        sys.stderr = _ConsoleStreamTee(sys.stderr)
        installed = True

    _repoint_log_handlers({id(original_stdout): sys.stdout, id(original_stderr): sys.stderr})

    if getattr(app, "extensions", None) is not None:
        app.extensions["discord_server_console"] = True
    return installed


def _repoint_log_handlers(stream_map):
    """Rebind already-installed StreamHandlers so logging flows through the tee."""
    seen = set()
    loggers = [logging.getLogger()] + [
        logging.getLogger(name)
        for name in logging.root.manager.loggerDict
        if isinstance(logging.getLogger(name), logging.Logger)
    ]
    for log in loggers:
        for handler in getattr(log, "handlers", []):
            if id(handler) in seen:
                continue
            seen.add(id(handler))
            stream = getattr(handler, "stream", None)
            replacement = stream_map.get(id(stream))
            if replacement is not None:
                try:
                    handler.setStream(replacement)
                except Exception:
                    handler.stream = replacement


def _compact_metadata(metadata):
    if not metadata:
        return "None"
    if isinstance(metadata, str):
        return _truncate(metadata, MAX_METADATA_CHARS)
    if isinstance(metadata, dict):
        lines = []
        for key, value in metadata.items():
            if value is None or value == "":
                continue
            if isinstance(value, (dict, list, tuple)):
                value = json.dumps(value, default=str, sort_keys=True)
            lines.append(f"{key}: {value}")
        return _truncate("\n".join(lines) or "None", MAX_METADATA_CHARS)
    return _truncate(metadata, MAX_METADATA_CHARS)


def format_actor(user=None, *, user_id=None, username=None, system=False):
    if system:
        return "System"
    user_id = user_id or getattr(user, "id", None)
    username = username or getattr(user, "username", None) or getattr(user, "name", None)
    if user_id and username:
        return f"{user_id} ({username})"
    if user_id:
        return str(user_id)
    if username:
        return str(username)
    return "Unknown"


def format_user_target(user_doc=None, *, user_id=None, username=None, label=None):
    user_id = user_id or (user_doc or {}).get("$id") or (user_doc or {}).get("id")
    username = username or (user_doc or {}).get("username") or (user_doc or {}).get("name")
    if user_id and username:
        return f"{label + ': ' if label else ''}{user_id} ({username})"
    if user_id:
        return f"{label + ': ' if label else ''}{user_id}"
    return label or "Unknown"


@dataclass
class DiscordAuditEvent:
    channel: str
    title: str
    actor: str
    target: str
    metadata: dict | str | None = field(default_factory=dict)
    color: str = "gray"
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    event_timestamp: str = field(default_factory=_utcnow_iso)

    def to_dict(self):
        return {
            "channel": self.channel,
            "title": self.title,
            "actor": self.actor,
            "target": self.target,
            "metadata": self.metadata,
            "color": self.color,
            "event_id": self.event_id,
            "event_timestamp": self.event_timestamp,
        }

    @classmethod
    def from_dict(cls, value):
        return cls(
            channel=value.get("channel") or "admin",
            title=value.get("title") or "Audit Event",
            actor=value.get("actor") or "Unknown",
            target=value.get("target") or "Unknown",
            metadata=value.get("metadata") or {},
            color=value.get("color") or "gray",
            event_id=value.get("event_id") or str(uuid.uuid4()),
            event_timestamp=value.get("event_timestamp") or _utcnow_iso(),
        )

    def channel_id(self):
        return _env_channel_id(self.channel)

    def _course_track_checked_embed(self):
        metadata = self.metadata if isinstance(self.metadata, dict) else {}
        color_name = self.color if self.color in COLOR_VALUES else "gray"
        course_name = metadata.get("course_name") or self.target
        course_label = self.target
        if course_name and course_name not in {self.target, "N/A"}:
            course_label = f"{self.target}: {course_name}"
        tracking_count = metadata.get("user_count") or metadata.get("track_count") or 0
        section_number = metadata.get("section_number")
        description_parts = []
        if section_number:
            description_parts.append(f"Sec # {section_number}")
        description_parts.append(f"{tracking_count} Tracking")
        return {
            "title": _truncate(course_label, 256),
            "description": _truncate(" | ".join(description_parts), 4096),
            "color": COLOR_VALUES[color_name],
            "fields": [
                {"name": "Enrollment", "value": _truncate(metadata.get("enrollment_type") or "N/A"), "inline": True},
                {"name": "Seats Open", "value": _truncate(metadata.get("seats_open") if metadata.get("seats_open") is not None else "N/A"), "inline": True},
            ],
            "timestamp": self.event_timestamp,
        }

    def embed(self):
        if self.channel == "course_tracks" and self.title == "Automated Course Track Checked":
            return self._course_track_checked_embed()
        color_name = self.color if self.color in COLOR_VALUES else "gray"
        return {
            "title": _truncate(self.title, 256),
            "color": COLOR_VALUES[color_name],
            "fields": [
                {"name": "Actor", "value": _truncate(self.actor), "inline": True},
                {"name": "Target", "value": _truncate(self.target), "inline": True},
                {"name": "Metadata", "value": _compact_metadata(self.metadata), "inline": True},
            ],
            "timestamp": self.event_timestamp,
        }


@dataclass
class _QueuedAuditEvent:
    event: DiscordAuditEvent
    attempt: int = 0
    next_attempt: float = 0.0

    def to_record(self):
        return {"event": self.event.to_dict(), "attempt": self.attempt}

    @classmethod
    def from_record(cls, value):
        if "event" in value:
            event_data = value.get("event") or {}
            attempt = int(value.get("attempt") or 0)
        else:
            event_data = value
            attempt = int(value.get("attempt") or 0)
        return cls(event=DiscordAuditEvent.from_dict(event_data), attempt=attempt, next_attempt=0.0)


class TokenBucket:
    def __init__(self, capacity=4, refill_amount=4, refill_seconds=5):
        self.capacity = float(capacity)
        self.refill_amount = float(refill_amount)
        self.refill_seconds = float(refill_seconds)
        self.tokens = float(capacity)
        self.updated_at = time.monotonic()

    def _refill(self, now):
        elapsed = max(0.0, now - self.updated_at)
        if elapsed:
            rate = self.refill_amount / self.refill_seconds
            self.tokens = min(self.capacity, self.tokens + elapsed * rate)
            self.updated_at = now

    def consume(self, now=None):
        now = time.monotonic() if now is None else now
        self._refill(now)
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False

    def seconds_until_available(self, now=None):
        now = time.monotonic() if now is None else now
        self._refill(now)
        if self.tokens >= 1.0:
            return 0.0
        rate = self.refill_amount / self.refill_seconds
        return max(0.0, (1.0 - self.tokens) / rate)


class DiscordAuditService:
    def __init__(
        self,
        *,
        fallback_path=None,
        token_getter=_bot_token,
        request_func=requests.request,
        sleep_event_factory=threading.Event,
        max_queue_events=MAX_QUEUE_EVENTS,
    ):
        self.fallback_path = fallback_path
        self.token_getter = token_getter
        self.request_func = request_func
        self.max_queue_events = max_queue_events
        self.queue = deque()
        self.lock = threading.RLock()
        self.wake_event = sleep_event_factory()
        self.stop_event = sleep_event_factory()
        self.thread = None
        self.buckets = {name: TokenBucket() for name in DEFAULT_CHANNEL_IDS}
        self.invalid_response_times = deque()
        self.pause_until = 0.0
        self.pause_warning_pending = False

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        if self.token_getter():
            self.replay_fallback()
        self.thread = threading.Thread(target=self._run, name="discord-audit-sender", daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.wake_event.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2)

    def emit(self, event):
        try:
            queued = event if isinstance(event, _QueuedAuditEvent) else _QueuedAuditEvent(event=event)
            if not self.token_getter():
                logger.warning(
                    "Discord audit token missing; persisting %s event to fallback",
                    queued.event.channel,
                )
                self.persist_event(queued)
                return False
            self._enqueue(queued)
            return True
        except Exception:
            logger.exception("Failed to enqueue Discord audit event")
            return False

    def emit_console_content(self, content):
        if not _console_log_enabled():
            return False
        channel_id = _env_channel_id("console_logs")
        if not channel_id:
            logger.warning("Discord console_logs channel is not configured for console output")
            return False
        if not self.token_getter():
            logger.warning("Discord bot token missing; skipping console output")
            return False
        try:
            self._send_console_content(channel_id, str(content or "")[:MAX_DISCORD_CONTENT_CHARS])
            return True
        except Exception:
            logger.exception("Failed to send browser console output to Discord")
            return False

    def _send_console_content(self, channel_id, content):
        response = self._request(
            "POST",
            f"/channels/{channel_id}/messages",
            json={"content": content, "allowed_mentions": {"parse": []}},
        )
        if 200 <= response.status_code < 300:
            return
        logger.warning(
            "Discord browser console send returned HTTP %s for channel %s",
            response.status_code,
            channel_id,
        )
        if response.status_code in {401, 403, 429}:
            self._record_invalid_response()

    def _enqueue(self, queued):
        with self.lock:
            while len(self.queue) >= self.max_queue_events:
                oldest = self.queue.popleft()
                self.persist_event(oldest)
            self.queue.append(queued)
            self.wake_event.set()

    def replay_fallback(self):
        if not self.fallback_path or not os.path.exists(self.fallback_path):
            return 0
        queued = []
        try:
            with open(self.fallback_path, "r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        queued.append(_QueuedAuditEvent.from_record(json.loads(line)))
                    except (TypeError, ValueError, json.JSONDecodeError):
                        logger.warning("Skipping invalid Discord audit fallback line")
            open(self.fallback_path, "w", encoding="utf-8").close()
        except OSError:
            logger.exception("Failed to replay Discord audit fallback file")
            return 0
        for item in queued:
            self._enqueue(item)
        return len(queued)

    def persist_event(self, queued):
        if not self.fallback_path:
            logger.warning("Discord audit fallback path is not configured")
            return
        try:
            os.makedirs(os.path.dirname(self.fallback_path), exist_ok=True)
            with open(self.fallback_path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(queued.to_record(), default=str, sort_keys=True))
                handle.write("\n")
        except OSError:
            logger.exception("Failed to persist Discord audit fallback event")

    def _run(self):
        while not self.stop_event.is_set():
            now = time.monotonic()
            if self.pause_until > now:
                self.wake_event.wait(min(1.0, self.pause_until - now))
                self.wake_event.clear()
                continue
            if self.pause_warning_pending:
                self.pause_warning_pending = False
                self.emit_warning_pause_resumed()

            flush_due_browser_console_batches()
            queued, wait_seconds = self._pop_ready(now)
            console_wait = seconds_until_browser_console_flush(now)
            if console_wait is not None:
                wait_seconds = min(wait_seconds, console_wait)
            if queued is None:
                self.wake_event.wait(max(0.1, min(wait_seconds, 1.0)))
                self.wake_event.clear()
                continue
            self._send_queued(queued)

    def _pop_ready(self, now):
        with self.lock:
            wait_seconds = 1.0
            for index, queued in enumerate(self.queue):
                if queued.next_attempt > now:
                    wait_seconds = min(wait_seconds, queued.next_attempt - now)
                    continue
                bucket = self.buckets.setdefault(queued.event.channel, TokenBucket())
                if bucket.consume(now):
                    del self.queue[index]
                    return queued, 0.0
                wait_seconds = min(wait_seconds, bucket.seconds_until_available(now))
            return None, wait_seconds

    def _headers(self):
        token = self.token_getter()
        if not token:
            raise RuntimeError("Discord bot token is not configured.")
        return {
            "Authorization": f"Bot {token}",
            "Content-Type": "application/json",
            "User-Agent": "Nest.APStudy Discord audit logger",
        }

    def _request(self, method, path, **kwargs):
        return self.request_func(
            method,
            f"{DISCORD_API_BASE}{path}",
            headers=kwargs.pop("headers", self._headers()),
            timeout=8,
            **kwargs,
        )

    def _send_queued(self, queued):
        channel_id = queued.event.channel_id()
        if not channel_id:
            logger.warning(
                "Discord audit channel ID missing for channel %s; persisting event to fallback",
                queued.event.channel,
            )
            self.persist_event(queued)
            return
        try:
            response = self._request(
                "POST",
                f"/channels/{channel_id}/messages",
                json={
                    "embeds": [queued.event.embed()],
                    "allowed_mentions": {"parse": []},
                    "nonce": _event_nonce(queued.event),
                    "enforce_nonce": True,
                },
            )
            status_code = int(response.status_code)
        except Exception:
            logger.exception(
                "Discord audit send failed or returned an invalid response for channel %s; scheduling retry",
                queued.event.channel,
            )
            self._retry_or_persist(queued, self._backoff_seconds(queued.attempt + 1))
            return

        if 200 <= status_code < 300:
            return

        logger.warning(
            "Discord audit send returned HTTP %s for channel %s; scheduling retry",
            status_code,
            queued.event.channel,
        )
        if status_code in {401, 403, 429}:
            self._record_invalid_response()

        if status_code == 429:
            self._retry_or_persist(queued, self._retry_after_seconds(response))
            return

        delay = self._backoff_seconds(queued.attempt + 1)
        self._retry_or_persist(queued, delay)

    def _retry_after_seconds(self, response):
        headers = getattr(response, "headers", None)
        retry_after = None
        if hasattr(headers, "get"):
            retry_after = headers.get("Retry-After") or headers.get("retry-after")
        if retry_after is None:
            try:
                retry_after = response.json().get("retry_after")
            except Exception:
                retry_after = None
        try:
            return max(0.0, float(retry_after))
        except (TypeError, ValueError):
            return 5.0

    def _backoff_seconds(self, attempt):
        base = min(300.0, 5.0 * (2 ** max(0, attempt - 1)))
        return base + random.uniform(0.0, 2.0)

    def _retry_or_persist(self, queued, delay_seconds):
        if self._already_posted(queued.event):
            return
        queued.attempt += 1
        if queued.attempt >= MAX_RETRY_ATTEMPTS:
            self.persist_event(queued)
            return
        queued.next_attempt = time.monotonic() + max(0.0, delay_seconds)
        self._enqueue(queued)

    def _already_posted(self, event):
        channel_id = event.channel_id()
        if not channel_id or not self.token_getter():
            return False
        try:
            response = self._request("GET", f"/channels/{channel_id}/messages", params={"limit": 10})
            if response.status_code >= 400:
                return False
            messages = response.json() or []
            if not isinstance(messages, list):
                logger.warning("Discord audit history returned invalid data")
                return False
        except Exception:
            logger.exception("Failed to check Discord audit message history")
            return False
        event_id = event.event_id
        event_nonce = _event_nonce(event)
        for message in messages:
            if not isinstance(message, dict):
                continue
            if str(message.get("nonce") or "") == event_nonce:
                return True
            if event_id in (message.get("content") or ""):
                return True
            for embed in message.get("embeds") or []:
                if not isinstance(embed, dict):
                    continue
                if event_id in (embed.get("footer") or {}).get("text", ""):
                    return True
                for field in embed.get("fields") or []:
                    if not isinstance(field, dict):
                        continue
                    if field.get("name") == "Event ID" and field.get("value") == event_id:
                        return True
                    if event_id in str(field.get("value") or ""):
                        return True
        return False

    def _record_invalid_response(self):
        now = time.monotonic()
        self.invalid_response_times.append(now)
        while self.invalid_response_times and now - self.invalid_response_times[0] > INVALID_WINDOW_SECONDS:
            self.invalid_response_times.popleft()
        if len(self.invalid_response_times) > INVALID_PAUSE_THRESHOLD and self.pause_until <= now:
            self.pause_until = now + INVALID_PAUSE_SECONDS
            self.pause_warning_pending = True
            logger.warning("Discord audit sends paused after repeated invalid responses")

    def emit_warning_pause_resumed(self):
        warning = DiscordAuditEvent(
            channel="admin",
            title="Discord Audit Sends Resumed After Pause",
            actor="System",
            target="Discord audit sender",
            metadata={
                "action_type": "warning",
                "reason": "More than 50 invalid Discord responses in a rolling 10-minute window.",
                "pause_seconds": INVALID_PAUSE_SECONDS,
            },
            color="yellow",
        )
        self._enqueue(_QueuedAuditEvent(event=warning))


_service = None


def init_discord_audit(app):
    global _service
    if os.environ.get("DISCORD_AUDIT_ENABLED", "1") == "0":
        logger.info("Discord audit logger disabled (DISCORD_AUDIT_ENABLED=0).")
        return None
    fallback_path = os.environ.get("DISCORD_AUDIT_FALLBACK_PATH")
    if not fallback_path:
        fallback_path = os.path.join(app.instance_path, "discord_audit_fallback.jsonl")
    _service = _service or DiscordAuditService(fallback_path=fallback_path)
    _service.start()
    init_discord_error_reporting(app)
    init_server_console_forwarding(app)
    return _service


def init_discord_error_reporting(app):
    if app.extensions.get("discord_audit_error_reporting"):
        return
    from flask import got_request_exception

    got_request_exception.connect(_emit_unhandled_request_exception, app, weak=False)
    app.extensions["discord_audit_error_reporting"] = True


def _emit_unhandled_request_exception(sender, exception, **extra):
    try:
        from flask import has_request_context, request

        metadata = {
            "exception_class": exception.__class__.__name__,
            "message": _sanitize_error_text(exception),
        }
        if has_request_context():
            metadata.update({
                "path": request.path,
                "method": request.method,
                "remote_addr": request.headers.get("X-Forwarded-For", request.remote_addr or ""),
                "user_agent": _sanitize_error_text(request.headers.get("User-Agent", ""), limit=180),
            })
        emit_server_log_event(
            "Unhandled Server Error",
            actor="System",
            target="Flask request",
            metadata=metadata,
            color="red",
        )
    except Exception:
        logger.exception("Failed to emit unhandled server error to Discord server log")


def shutdown_discord_audit():
    if _service:
        _service.stop()


def get_audit_service():
    global _service
    if _service is None:
        fallback_path = os.environ.get("DISCORD_AUDIT_FALLBACK_PATH")
        if not fallback_path:
            fallback_path = os.path.join(os.getcwd(), "instance", "discord_audit_fallback.jsonl")
        _service = DiscordAuditService(fallback_path=fallback_path)
    return _service


def discord_audit_status():
    fallback_path = os.environ.get("DISCORD_AUDIT_FALLBACK_PATH")
    if not fallback_path:
        fallback_path = (
            _service.fallback_path
            if _service and _service.fallback_path
            else os.path.join(os.getcwd(), "instance", "discord_audit_fallback.jsonl")
        )
    thread_alive = bool(_service and _service.thread and _service.thread.is_alive())
    queue_length = len(_service.queue) if _service else 0
    return {
        "audit_enabled": os.environ.get("DISCORD_AUDIT_ENABLED", "1") != "0",
        "bot_token_present": bool(_bot_token()),
        "course_tracks_channel_present": bool(_env_channel_id("course_tracks")),
        "server_logs_channel_present": bool(_env_channel_id("server_logs")),
        "console_logs_channel_present": bool(_env_channel_id("console_logs")),
        "service_initialized": _service is not None,
        "sender_thread_alive": thread_alive,
        "queue_length": queue_length,
        "fallback_path": fallback_path,
        "fallback_line_count": _fallback_line_count(fallback_path),
    }


def emit_audit_event(event):
    return get_audit_service().emit(event)


def emit_admin_event(title, *, actor, target, metadata=None, color="gray"):
    return emit_audit_event(
        DiscordAuditEvent(
            channel="admin",
            title=title,
            actor=actor,
            target=target,
            metadata=metadata or {},
            color=color,
        )
    )


def emit_course_track_event(title, *, actor, target, metadata=None, color="gray"):
    return emit_audit_event(
        DiscordAuditEvent(
            channel="course_tracks",
            title=title,
            actor=actor,
            target=target,
            metadata=metadata or {},
            color=color,
        )
    )


def format_course_tracks_channel_topic(course_count):
    return f"Tracking {int(course_count)} courses"


def update_course_tracks_channel_topic(course_count):
    channel_id = _env_channel_id("course_tracks")
    if not channel_id:
        logger.warning("Discord course_tracks channel is not configured for topic updates")
        return False
    if not _bot_token():
        logger.warning("Discord bot token missing; skipping course tracks channel topic update")
        return False

    topic = format_course_tracks_channel_topic(course_count)
    try:
        response = get_audit_service()._request(
            "PATCH",
            f"/channels/{channel_id}",
            json={"topic": topic},
        )
    except Exception:
        logger.exception("Failed to update course tracks Discord channel topic")
        return False

    if 200 <= response.status_code < 300:
        return True

    logger.warning(
        "Discord course tracks topic update returned HTTP %s for channel %s",
        response.status_code,
        channel_id,
    )
    return False


def emit_creation_event(title, *, actor, target, metadata=None, color="green"):
    return emit_audit_event(
        DiscordAuditEvent(
            channel="creation",
            title=title,
            actor=actor,
            target=target,
            metadata=metadata or {},
            color=color,
        )
    )


def emit_user_event(title, *, actor, target, metadata=None, color="green"):
    return emit_audit_event(
        DiscordAuditEvent(
            channel="user_logs",
            title=title,
            actor=actor,
            target=target,
            metadata=metadata or {},
            color=color,
        )
    )


def emit_server_log_event(title, *, actor="System", target="Server", metadata=None, color="gray"):
    return emit_audit_event(
        DiscordAuditEvent(
            channel="server_logs",
            title=title,
            actor=actor,
            target=target,
            metadata=metadata or {},
            color=color,
        )
    )


def emit_backup_event(title, *, actor="System", target="SQLite backup", metadata=None, color="green"):
    return emit_audit_event(
        DiscordAuditEvent(
            channel="server_logs",
            title=title,
            actor=actor,
            target=target,
            metadata=metadata or {},
            color=color,
        )
    )


def send_audit_event_sync(event):
    """Send an audit embed immediately without the background sender queue."""
    service = get_audit_service()
    if not _bot_token():
        logger.warning("Discord bot token missing; skipping synchronous audit event")
        return False
    channel_id = event.channel_id()
    if not channel_id:
        logger.warning("Discord audit channel ID missing for channel %s", event.channel)
        return False
    try:
        response = service._request(
            "POST",
            f"/channels/{channel_id}/messages",
            json={"embeds": [event.embed()], "allowed_mentions": {"parse": []}},
        )
    except Exception:
        logger.exception("Failed to send synchronous Discord audit event to %s", event.channel)
        return False
    if 200 <= response.status_code < 300:
        return True
    logger.warning(
        "Synchronous Discord audit send returned HTTP %s for channel %s",
        response.status_code,
        event.channel,
    )
    return False


def send_console_content_sync(content):
    """Send a plain console log message immediately without the background sender queue."""
    if not _console_log_enabled():
        return False
    channel_id = _env_channel_id("console_logs")
    if not channel_id or not _bot_token():
        return False
    service = get_audit_service()
    try:
        service._send_console_content(channel_id, str(content or "")[:MAX_DISCORD_CONTENT_CHARS])
        return True
    except Exception:
        logger.exception("Failed to send synchronous console output to Discord")
        return False
