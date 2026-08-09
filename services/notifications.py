"""Unified notification feed and standards-based Web Push delivery."""

import hashlib
import json
import logging
import os
import uuid
import sqlite3
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from services.database import db_connection
from services.environment_config import runtime_environment_config
from services.task_schedule import build_task_occurrences

logger = logging.getLogger(__name__)
DEFAULT_PREFERENCES = {
    "push_enabled": False, "calendar_enabled": True, "course_push_enabled": True,
    "course_email_enabled": False, "dm_enabled": True, "mention_enabled": True,
    "message_preview_enabled": True, "calendar_lead_minutes": [10, 1440],
    "all_day_previous_time": "18:00", "prompt_dismissed": False,
}
BOOL_FIELDS = {key for key, value in DEFAULT_PREFERENCES.items() if isinstance(value, bool)}
ALLOWED_LEADS = {5, 10, 30, 60, 1440, 10080}
FOREGROUND_PRESENCE_SECONDS = 15
FOREGROUND_FALLBACK_SECONDS = 20
CATEGORY_PREFERENCE_FIELDS = {
    "calendar": "calendar_enabled",
    "courses": "course_push_enabled",
    "chat_dm": "dm_enabled",
    "chat_mention": "mention_enabled",
}
URGENT_FOCUS_CATEGORIES = {"calendar", "courses", "chat_mention"}


def _now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _id():
    return uuid.uuid4().hex


def _safe_url(value, fallback="/dashboard?notifications=open"):
    if value is None or not str(value).strip():
        return fallback
    value = str(value).strip()
    parsed = urlsplit(value)
    return value if value.startswith("/") and not value.startswith("//") and not parsed.netloc else fallback


def push_configuration():
    """Return public push configuration without exposing private key material."""
    configured = runtime_environment_config()
    public_key = str(configured.vapid_public_key or "").strip()
    private_key = str(configured.vapid_private_key or "").strip()
    private_key_available = bool(private_key and ("BEGIN PRIVATE KEY" in private_key or os.path.isfile(private_key)))
    return {
        "configured": bool(public_key and private_key_available),
        "public_key": public_key if public_key and private_key_available else "",
    }


def preferences(user_id):
    try:
        with db_connection() as conn:
            row = conn.execute("SELECT * FROM notification_preferences WHERE user_id = ?", [str(user_id)]).fetchone()
    except sqlite3.OperationalError:
        # Test/upgrade-safe fallback while an older database is awaiting migrations.
        return dict(DEFAULT_PREFERENCES)
    if not row:
        return dict(DEFAULT_PREFERENCES)
    payload = dict(DEFAULT_PREFERENCES)
    for key in BOOL_FIELDS:
        payload[key] = bool(row[key])
    try:
        payload["calendar_lead_minutes"] = json.loads(row["calendar_lead_minutes_json"] or "[]")
    except (TypeError, ValueError):
        pass
    payload["all_day_previous_time"] = row["all_day_previous_time"] or "18:00"
    return payload


def update_preferences(user_id, updates):
    current = preferences(user_id)
    for key in BOOL_FIELDS:
        if key in updates:
            current[key] = bool(updates[key])
    if "calendar_lead_minutes" in updates:
        leads = sorted({int(value) for value in updates["calendar_lead_minutes"] if int(value) in ALLOWED_LEADS})
        if not leads:
            raise ValueError("Choose at least one calendar reminder time.")
        current["calendar_lead_minutes"] = leads
    if "all_day_previous_time" in updates:
        value = str(updates["all_day_previous_time"])
        try:
            datetime.strptime(value, "%H:%M")
        except ValueError as exc:
            raise ValueError("Use a valid reminder time.") from exc
        current["all_day_previous_time"] = value
    now = _now()
    with db_connection() as conn:
        existing = conn.execute("SELECT id FROM notification_preferences WHERE user_id = ?", [str(user_id)]).fetchone()
        values = [int(current[key]) for key in ["push_enabled", "calendar_enabled", "course_push_enabled", "course_email_enabled", "dm_enabled", "mention_enabled", "message_preview_enabled"]]
        if existing:
            conn.execute("""UPDATE notification_preferences SET push_enabled=?, calendar_enabled=?, course_push_enabled=?, course_email_enabled=?, dm_enabled=?, mention_enabled=?, message_preview_enabled=?, calendar_lead_minutes_json=?, all_day_previous_time=?, prompt_dismissed=?, updated_at=? WHERE user_id=?""",
                         [*values, json.dumps(current["calendar_lead_minutes"]), current["all_day_previous_time"], int(current["prompt_dismissed"]), now, str(user_id)])
        else:
            conn.execute("""INSERT INTO notification_preferences (id,user_id,push_enabled,calendar_enabled,course_push_enabled,course_email_enabled,dm_enabled,mention_enabled,message_preview_enabled,calendar_lead_minutes_json,all_day_previous_time,prompt_dismissed,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                         [_id(), str(user_id), *values, json.dumps(current["calendar_lead_minutes"]), current["all_day_previous_time"], int(current["prompt_dismissed"]), now, now])
    return current


def upsert_subscription(user_id, subscription, device_name, user_agent=""):
    endpoint = str(subscription.get("endpoint") or "")
    keys = subscription.get("keys") or {}
    if not endpoint.startswith("https://") or not keys.get("p256dh") or not keys.get("auth"):
        raise ValueError("Invalid push subscription.")
    now = _now()
    with db_connection() as conn:
        row = conn.execute("SELECT id,user_id FROM push_subscriptions WHERE endpoint=?", [endpoint]).fetchone()
        if row and str(row["user_id"]) != str(user_id):
            raise PermissionError("Subscription belongs to another account.")
        row_id = row["id"] if row else _id()
        if row:
            conn.execute("UPDATE push_subscriptions SET p256dh=?,auth=?,device_name=?,user_agent=?,updated_at=?,last_seen_at=?,failed_at=NULL,failure_reason=NULL WHERE id=?", [keys["p256dh"], keys["auth"], str(device_name or "This browser")[:80], str(user_agent)[:300], now, now, row_id])
        else:
            conn.execute("INSERT INTO push_subscriptions (id,user_id,endpoint,p256dh,auth,device_name,user_agent,created_at,last_seen_at) VALUES (?,?,?,?,?,?,?,?,?)", [row_id, str(user_id), endpoint, keys["p256dh"], keys["auth"], str(device_name or "This browser")[:80], str(user_agent)[:300], now, now])
    update_preferences(user_id, {"push_enabled": True})
    return row_id


def list_subscriptions(user_id):
    with db_connection() as conn:
        rows = conn.execute("SELECT id,device_name,user_agent,created_at,last_seen_at,failed_at,failure_reason FROM push_subscriptions WHERE user_id=? ORDER BY created_at DESC", [str(user_id)]).fetchall()
    return [dict(row) for row in rows]


def delete_subscription(user_id, subscription_id=None, endpoint=None):
    with db_connection() as conn:
        if subscription_id:
            result = conn.execute("DELETE FROM push_subscriptions WHERE id=? AND user_id=?", [str(subscription_id), str(user_id)])
        else:
            result = conn.execute("DELETE FROM push_subscriptions WHERE endpoint=? AND user_id=?", [str(endpoint), str(user_id)])
    return result.rowcount > 0


def rename_subscription(user_id, subscription_id, name):
    name = str(name or "").strip()[:80]
    if not name: raise ValueError("Enter a device name.")
    with db_connection() as conn:
        result = conn.execute("UPDATE push_subscriptions SET device_name=?,updated_at=? WHERE id=? AND user_id=?", [name, _now(), str(subscription_id), str(user_id)])
    if not result.rowcount: raise ValueError("Device not found.")
    return list_subscriptions(user_id)


def create_feed_item(user_id, category, title, body, target_url, *, source_ref=None, dedupe_key=None, actor_user_id=None):
    if actor_user_id is not None and str(actor_user_id) == str(user_id):
        return None
    notification_id = _id()
    now = _now()
    with db_connection() as conn:
        if dedupe_key:
            existing = conn.execute("SELECT id FROM user_notifications WHERE user_id=? AND dedupe_key=?", [str(user_id), str(dedupe_key)]).fetchone()
            if existing:
                return existing["id"]
        conn.execute("""INSERT INTO user_notifications (id,user_id,actor_user_id,notification_type,message,is_read,created_at,category,title,body,target_url,source_ref,dedupe_key,expires_at) VALUES (?,?,?,?,?,0,?,?,?,?,?,?,?,?)""",
                     [notification_id, str(user_id), str(actor_user_id) if actor_user_id else None, str(category), str(body)[:500], now, str(category), str(title)[:160], str(body)[:500], _safe_url(target_url, fallback=None), str(source_ref) if source_ref else None, str(dedupe_key) if dedupe_key else None, (datetime.now(timezone.utc)+timedelta(days=90)).isoformat().replace("+00:00","Z")])
    return notification_id


def unread_count(user_id):
    with db_connection() as conn:
        row = conn.execute("SELECT COUNT(*) count FROM user_notifications WHERE user_id=? AND is_read=0 AND deleted_at IS NULL", [str(user_id)]).fetchone()
    return int(row["count"] or 0)


def _apply_feed_filters(clauses, args, *, category=None, status=None, search=None):
    if category == "chat":
        clauses.append("category IN ('chat_dm','chat_mention')")
    elif category and category != "all":
        clauses.append("category=?")
        args.append(str(category))
    if status == "unread":
        clauses.append("is_read=0")
    elif status == "read":
        clauses.append("is_read=1")
    term = str(search or "").strip().lower()[:100]
    if term:
        escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        clauses.append("(LOWER(COALESCE(title, notification_type, '')) LIKE ? ESCAPE '\\' OR LOWER(COALESCE(body, message, '')) LIKE ? ESCAPE '\\')")
        args.extend([f"%{escaped}%", f"%{escaped}%"])


def list_feed(user_id, *, category=None, unread=False, status=None, search=None, limit=50, before=None):
    clauses, args = ["user_id=?", "deleted_at IS NULL"], [str(user_id)]
    _apply_feed_filters(
        clauses,
        args,
        category=category,
        status="unread" if unread else status,
        search=search,
    )
    if before:
        clauses.append("created_at<?")
        args.append(before)
    limit = min(max(int(limit), 1), 100)
    with db_connection() as conn:
        rows = conn.execute(f"SELECT * FROM user_notifications WHERE {' AND '.join(clauses)} ORDER BY created_at DESC LIMIT ?", [*args, limit]).fetchall()
    return {"notifications": [dict(row) for row in rows], "unread_count": unread_count(user_id), "next_cursor": rows[-1]["created_at"] if len(rows) == limit else None}


def mutate_feed(user_id, ids=None, *, read=None, delete=False, category=None):
    if ids is not None and not ids:
        return unread_count(user_id)
    clauses, args = ["user_id=?"], [str(user_id)]
    if ids:
        clauses.append(f"id IN ({','.join('?' for _ in ids)})"); args.extend(str(value) for value in ids)
    if category == "chat": clauses.append("category IN ('chat_dm','chat_mention')")
    elif category: clauses.append("category=?"); args.append(category)
    now = _now()
    with db_connection() as conn:
        if delete:
            conn.execute(f"UPDATE user_notifications SET deleted_at=? WHERE {' AND '.join(clauses)}", [now, *args])
        elif read is not None:
            conn.execute(f"UPDATE user_notifications SET is_read=?,read_at=? WHERE {' AND '.join(clauses)}", [int(read), now if read else None, *args])
        if delete or read is True:
            queue_clauses, queue_args = ["user_id=?", "acknowledged_at IS NULL"], [str(user_id)]
            if ids:
                queue_clauses.append(f"notification_id IN ({','.join('?' for _ in ids)})")
                queue_args.extend(str(value) for value in ids)
            if category == "chat":
                queue_clauses.append("category IN ('chat_dm','chat_mention')")
            elif category:
                queue_clauses.append("category=?")
                queue_args.append(category)
            conn.execute(
                f"UPDATE notification_foreground_queue SET acknowledged_at=? WHERE {' AND '.join(queue_clauses)}",
                [now, *queue_args],
            )
    return unread_count(user_id)


def category_delivery_enabled(category, prefs):
    field = CATEGORY_PREFERENCE_FIELDS.get(str(category or ""))
    return not field or bool(prefs.get(field, True))


def is_urgent_during_focus(category):
    """Return whether a category should interrupt an active focus session."""
    return str(category or "") in URGENT_FOCUS_CATEGORIES


def focus_delivery_enabled(user_id, category):
    if is_urgent_during_focus(category):
        return True
    try:
        from services.focus_mode import is_focus_mode_active
        return not is_focus_mode_active(user_id)
    except sqlite3.OperationalError:
        # Upgrade-safe while the Focus Mode migration is still being applied.
        return True


def touch_web_presence(user_id, tab_id, *, active, device_class):
    tab_id = "".join(character for character in str(tab_id or "") if character.isalnum() or character in "_-")[:64]
    if not tab_id:
        raise ValueError("Missing notification tab identifier.")
    device_class = str(device_class or "")[:32]
    is_active = bool(active and device_class == "desktop_tablet")
    now = _now()
    with db_connection() as conn:
        conn.execute(
            """INSERT INTO notification_web_presence (id,user_id,tab_id,device_class,is_active,last_seen_at)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(user_id,tab_id) DO UPDATE SET
               device_class=excluded.device_class,is_active=excluded.is_active,last_seen_at=excluded.last_seen_at""",
            [_id(), str(user_id), tab_id, device_class, int(is_active), now],
        )
    return is_active


def has_active_web_session(user_id, *, now=None):
    now = now or datetime.now(timezone.utc)
    cutoff = (now - timedelta(seconds=FOREGROUND_PRESENCE_SECONDS)).isoformat().replace("+00:00", "Z")
    try:
        with db_connection() as conn:
            row = conn.execute(
                """SELECT 1 FROM notification_web_presence
                   WHERE user_id=? AND device_class='desktop_tablet' AND is_active=1 AND last_seen_at>=?
                   LIMIT 1""",
                [str(user_id), cutoff],
            ).fetchone()
    except sqlite3.OperationalError:
        return False
    return bool(row)


def _queue_foreground_delivery(user_id, notification_id, category, title, body, target_url, tag):
    now = datetime.now(timezone.utc)
    deliver_after = (now + timedelta(seconds=FOREGROUND_FALLBACK_SECONDS)).isoformat().replace("+00:00", "Z")
    created_at = now.isoformat().replace("+00:00", "Z")
    with db_connection() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO notification_foreground_queue
               (id,notification_id,user_id,category,title,body,target_url,tag,deliver_after,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            [
                _id(), str(notification_id), str(user_id), str(category), str(title)[:160],
                str(body)[:500], _safe_url(target_url, fallback=None), str(tag or notification_id),
                deliver_after, created_at,
            ],
        )


def pending_foreground_ids(user_id):
    with db_connection() as conn:
        rows = conn.execute(
            """SELECT notification_id FROM notification_foreground_queue
               WHERE user_id=? AND acknowledged_at IS NULL AND fallback_at IS NULL
               ORDER BY created_at DESC LIMIT 100""",
            [str(user_id)],
        ).fetchall()
    return [str(row["notification_id"]) for row in rows]


def acknowledge_foreground(user_id, notification_ids):
    ids = [str(value) for value in (notification_ids or []) if value][:100]
    if not ids:
        return 0
    with db_connection() as conn:
        result = conn.execute(
            f"""UPDATE notification_foreground_queue SET acknowledged_at=?
                WHERE user_id=? AND notification_id IN ({','.join('?' for _ in ids)})
                AND acknowledged_at IS NULL""",
            [_now(), str(user_id), *ids],
        )
    return result.rowcount


def flush_foreground_queue(now=None):
    now = now or datetime.now(timezone.utc)
    now_iso = now.isoformat().replace("+00:00", "Z")
    with db_connection() as conn:
        rows = [dict(row) for row in conn.execute(
            """SELECT * FROM notification_foreground_queue
               WHERE acknowledged_at IS NULL AND fallback_at IS NULL AND deliver_after<=?
               ORDER BY deliver_after LIMIT 100""",
            [now_iso],
        ).fetchall()]
    flushed = 0
    for row in rows:
        with db_connection() as conn:
            claimed = conn.execute(
                """UPDATE notification_foreground_queue SET fallback_at=?
                   WHERE id=? AND acknowledged_at IS NULL AND fallback_at IS NULL""",
                [now_iso, row["id"]],
            ).rowcount
        if not claimed:
            continue
        try:
            deliver(
                row["user_id"], row["notification_id"], row["category"], row["title"],
                row["body"], row["target_url"], tag=row["tag"], force_push=True,
            )
            flushed += 1
        except Exception:
            logger.exception("Foreground notification fallback failed")
            with db_connection() as conn:
                conn.execute("UPDATE notification_foreground_queue SET fallback_at=NULL WHERE id=?", [row["id"]])
    return flushed


def _send(subscription, payload):
    from pywebpush import WebPushException, webpush
    configured = runtime_environment_config()
    private_key = str(configured.vapid_private_key or "").strip()
    subject = configured.vapid_subject
    if not push_configuration()["configured"]:
        raise RuntimeError("VAPID_PRIVATE_KEY is not configured.")
    try:
        response = webpush(subscription_info={"endpoint": subscription["endpoint"], "keys": {"p256dh": subscription["p256dh"], "auth": subscription["auth"]}}, data=json.dumps(payload), vapid_private_key=private_key, vapid_claims={"sub": subject}, ttl=86400)
        return getattr(response, "status_code", 201)
    except WebPushException as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status in {404, 410}:
            with db_connection() as conn: conn.execute("DELETE FROM push_subscriptions WHERE id=?", [subscription["id"]])
        raise


def deliver(user_id, notification_id, category, title, body, target_url, *, tag=None, force_push=False):
    prefs = preferences(user_id)
    if not category_delivery_enabled(category, prefs):
        return {"accepted": 0, "failed": 0}
    if not focus_delivery_enabled(user_id, category):
        return {"accepted": 0, "failed": 0}
    if not force_push and has_active_web_session(user_id):
        _queue_foreground_delivery(user_id, notification_id, category, title, body, target_url, tag)
        return {"accepted": 1, "failed": 0}
    if not prefs["push_enabled"]:
        return {"accepted": 0, "failed": 0}
    with db_connection() as conn:
        subscriptions = [dict(row) for row in conn.execute("SELECT * FROM push_subscriptions WHERE user_id=?", [str(user_id)]).fetchall()]
    result = {"accepted": 0, "failed": 0}
    payload = {"version": 1, "id": notification_id, "category": category, "title": title, "body": body, "url": _safe_url(target_url), "tag": tag or notification_id, "badgeCount": unread_count(user_id), "timestamp": _now()}
    for subscription in subscriptions:
        delivery_id = _id()
        try:
            status = _send(subscription, payload); outcome, error = "accepted", None; result["accepted"] += 1
        except Exception as exc:
            logger.warning("Push delivery failed: %s", exc); status, outcome, error = getattr(getattr(exc, "response", None), "status_code", None), "failed", str(exc)[:500]; result["failed"] += 1
        with db_connection() as conn:
            conn.execute("INSERT OR IGNORE INTO notification_deliveries (id,notification_id,subscription_id,status,provider_status,failure_reason,attempted_at) VALUES (?,?,?,?,?,?,?)", [delivery_id, notification_id, subscription["id"], outcome, status, error, _now()])
    return result


def notify(user_id, category, title, body, target_url, **kwargs):
    notification_id = create_feed_item(user_id, category, title, body, target_url, source_ref=kwargs.get("source_ref"), dedupe_key=kwargs.get("dedupe_key"), actor_user_id=kwargs.get("actor_user_id"))
    return notification_id, deliver(user_id, notification_id, category, title, body, target_url, tag=kwargs.get("tag"), force_push=bool(kwargs.get("force_push"))) if notification_id else {"accepted": 0, "failed": 0}


def _default_event_reminder(is_all_day):
    return -1 if is_all_day else 10


def _feed_event_ref(row):
    feed_hash = str(row.get("feed_url_hash") or "")
    event_uid = str(row.get("event_uid") or "")
    if not feed_hash or not event_uid:
        return None
    uid_hash = hashlib.sha256(event_uid.encode("utf-8")).hexdigest()
    return f"feed:{feed_hash}:{uid_hash}"


def _reminder_label(start, now, zone, lead):
    if lead < 0 or lead in {900, 2340, 9540}:
        days = (start.astimezone(zone).date() - now.astimezone(zone).date()).days
        if days == 0:
            return "today"
        if days == 1:
            return "tomorrow"
        return f"in {days} days"
    return {
        0: "now", 5: "in 5 minutes", 10: "in 10 minutes", 15: "in 15 minutes",
        30: "in 30 minutes", 60: "in 1 hour", 120: "in 2 hours",
        1440: "tomorrow", 2880: "in 2 days",
    }.get(lead, "soon")


def check_calendar_reminders(now=None):
    """Claim and deliver due reminders from local, imported, and task calendars."""
    now = now or datetime.now(timezone.utc)
    range_start = now - timedelta(days=1)
    range_end = now + timedelta(days=8)
    window_end = range_end.isoformat().replace("+00:00", "Z")
    sent = 0
    with db_connection() as conn:
        users = conn.execute("SELECT user_id,timezone FROM user_settings").fetchall()
        for user in users:
            user_id = str(user["user_id"])
            prefs = preferences(user_id)
            if not prefs["calendar_enabled"]:
                continue
            try: zone = ZoneInfo(user["timezone"] or "UTC")
            except Exception: zone = timezone.utc
            overrides = {
                row["event_ref"]: dict(row)
                for row in conn.execute("SELECT event_ref,hidden,title,start,is_all_day,reminder_minutes FROM user_event_overrides WHERE user_id=?", [user_id])
            }
            rows = []
            rows.extend(dict(row, kind="event", ref=f"local:{row['id']}") for row in conn.execute("SELECT id,title,start,is_all_day,reminder_minutes FROM user_events WHERE user_id=? AND start<=?", [user_id, window_end]))
            for row in conn.execute("SELECT id,event_title AS title,event_start AS start,is_all_day,feed_url_hash,event_uid FROM calendar_cache WHERE user_id=? AND event_start<=?", [user_id, window_end]):
                item = dict(row, kind="event", ref=f"feed:{row['id']}")
                override = overrides.get(_feed_event_ref(item))
                if override and bool(override["hidden"]):
                    continue
                if override:
                    if override["title"] is not None:
                        item["title"] = override["title"]
                    if override["start"] is not None:
                        item["start"] = override["start"]
                    if override["is_all_day"] is not None:
                        item["is_all_day"] = bool(override["is_all_day"])
                reminder = override["reminder_minutes"] if override else None
                item["reminder_minutes"] = _default_event_reminder(bool(item["is_all_day"])) if reminder is None else reminder
                rows.append(item)
            task_rows = [dict(row) for row in conn.execute("SELECT * FROM tasks WHERE user_id=? AND deadline_at IS NOT NULL", [user_id])]
            task_completions = [dict(row) for row in conn.execute("SELECT * FROM task_completions WHERE user_id=?", [user_id])]
            for occurrence in build_task_occurrences(task_rows, task_completions, range_start, range_end):
                if occurrence["completed"]:
                    continue
                task = occurrence["task"]
                rows.append({
                    "kind": "task",
                    "ref": f"task:{occurrence['task_id']}:{occurrence['occurrence_key']}",
                    "task_id": occurrence["task_id"],
                    "title": task.get("title"),
                    "start": occurrence["start"].isoformat(),
                    "is_all_day": occurrence["is_all_day"],
                    "reminder_minutes": task.get("reminder_minutes"),
                })
            for item in rows:
                try:
                    raw = str(item["start"])
                    start = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                    if start.tzinfo is None: start = start.replace(tzinfo=zone).astimezone(timezone.utc)
                except (TypeError, ValueError):
                    continue
                lead = item.get("reminder_minutes")
                lead = _default_event_reminder(bool(item["is_all_day"])) if lead is None else int(lead)
                if lead == -1:
                    continue
                target = start - timedelta(minutes=lead)
                if not (target <= now < target + timedelta(minutes=2)):
                    continue
                claim_id = _id()
                try:
                    conn.execute("INSERT INTO calendar_reminder_claims (id,user_id,event_ref,occurrence_start,lead_minutes,claimed_at) VALUES (?,?,?,?,?,?)", [claim_id, user_id, item["ref"], start.isoformat(), lead, _now()])
                except Exception:
                    continue
                label = _reminder_label(start, now, zone, lead)
                is_task = item.get("kind") == "task"
                target_url = f"/tasks?task={item['task_id']}" if is_task else "/dashboard#calendar"
                body = f"Due {label}." if is_task else f"Starts {label}."
                notification_id, result = notify(
                    user_id,
                    "calendar",
                    str(item["title"] or ("Upcoming task" if is_task else "Upcoming item")),
                    body,
                    target_url,
                    source_ref=item["ref"],
                    dedupe_key=f"calendar:{item['ref']}:{start.isoformat()}:{lead}",
                    tag=f"calendar:{item['ref']}",
                )
                conn.execute("UPDATE calendar_reminder_claims SET notification_id=? WHERE id=?", [notification_id, claim_id])
                sent += result["accepted"]
    return sent


def cleanup_expired():
    cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat().replace("+00:00", "Z")
    presence_cutoff = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat().replace("+00:00", "Z")
    with db_connection() as conn:
        feed = conn.execute("DELETE FROM user_notifications WHERE expires_at IS NOT NULL AND expires_at<?", [_now()]).rowcount
        deliveries = conn.execute("DELETE FROM notification_deliveries WHERE attempted_at<?", [cutoff]).rowcount
        claims = conn.execute("DELETE FROM calendar_reminder_claims WHERE claimed_at<?", [cutoff]).rowcount
        presence = conn.execute("DELETE FROM notification_web_presence WHERE last_seen_at<?", [presence_cutoff]).rowcount
        foreground_queue = conn.execute("DELETE FROM notification_foreground_queue WHERE created_at<?", [cutoff]).rowcount
    return {"feed": feed, "deliveries": deliveries, "claims": claims, "presence": presence, "foreground_queue": foreground_queue}
