"""Persistence and domain operations for collaborative notes."""

from __future__ import annotations

import base64
import json
import os
import re
import sqlite3
import subprocess
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone

from appwrite.exception import AppwriteException

from services.database import db_connection, utcnow_iso
from services import note_store
from services.database import BASE_DIR
from services.environment_config import runtime_environment_config


ROLE_LEVELS = {"viewer", "reviewer", "editor"}
REVIEW_ROLES = {"owner", "editor", "reviewer"}
MANAGER_ROLES = {"owner", "editor"}
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
INVITATION_DAYS = 7
VERSION_DAYS = 30


def _collaboration_secret():
    configured = runtime_environment_config()
    return configured.notes_collaboration_internal_secret or configured.notes_collaboration_secret


def _broadcast_review_event(note_id, event_type, resource_id):
    secret = _collaboration_secret()
    if not secret:
        return
    payload = json.dumps({
        "note_id": str(note_id),
        "event": {
            "id": row_id(),
            "type": str(event_type),
            "resource_id": str(resource_id),
        },
    }).encode("utf-8")
    request = urllib.request.Request(
        "http://127.0.0.1:1234/events",
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Nest-Collaboration-Secret": secret,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=0.75):
            pass
    except (OSError, urllib.error.URLError):
        # Database state is authoritative; connected clients can still refresh manually.
        pass


def _reload_collaboration_document(note_id):
    secret = _collaboration_secret()
    if not secret:
        return
    request = urllib.request.Request(
        "http://127.0.0.1:1234/reload",
        data=json.dumps({"note_id": str(note_id)}).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", "X-Nest-Collaboration-Secret": secret},
    )
    try:
        with urllib.request.urlopen(request, timeout=1.5):
            pass
    except (OSError, urllib.error.URLError):
        pass


def row_id():
    return str(uuid.uuid4())


def normalize_role(value):
    role = str(value or "").strip().lower()
    if role not in ROLE_LEVELS:
        raise ValueError("Role must be viewer, reviewer, or editor.")
    return role


def normalize_email(value):
    email = str(value or "").strip()
    if len(email) > 320 or not EMAIL_RE.fullmatch(email):
        raise ValueError("Enter a valid email address.")
    return email.casefold(), email


def _iso_after(days):
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat().replace("+00:00", "Z")


def _dicts(rows):
    return [dict(row) for row in rows]


def record_access_event(
    actor_user_id,
    resource_type,
    resource_id,
    action,
    *,
    target_type=None,
    target_id=None,
    old_access_level=None,
    new_access_level=None,
):
    with db_connection() as conn:
        conn.execute(
            """
            INSERT INTO note_access_events (
                id, actor_user_id, resource_type, resource_id, action,
                target_type, target_id, old_access_level, new_access_level, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                row_id(), str(actor_user_id), str(resource_type), str(resource_id), str(action),
                target_type, str(target_id) if target_id is not None else None,
                old_access_level, new_access_level, utcnow_iso(),
            ],
        )


def list_pending_invitations(resource_type, resource_id):
    now = utcnow_iso()
    with db_connection() as conn:
        conn.execute(
            """
            UPDATE note_share_invitations
            SET status = 'expired', updated_at = ?
            WHERE status = 'pending' AND expires_at <= ?
            """,
            [now, now],
        )
        rows = conn.execute(
            """
            SELECT id, email_display, access_level, expires_at, created_at, updated_at
            FROM note_share_invitations
            WHERE resource_type = ? AND resource_id = ? AND status = 'pending'
            ORDER BY LOWER(email_display)
            """,
            [resource_type, str(resource_id)],
        ).fetchall()
    return [
        {
            "id": row["id"],
            "email": row["email_display"],
            "role": row["access_level"],
            "status": "pending",
            "expires_at": row["expires_at"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
        for row in rows
    ]


def replace_pending_invitations(
    resource_type,
    resource_id,
    owner_user_id,
    invitations,
    actor_user_id,
):
    normalized = []
    seen = set()
    for invitation in invitations:
        if not isinstance(invitation, dict):
            raise ValueError("Every invitation must be an object.")
        email_normalized, email_display = normalize_email(invitation.get("email"))
        if email_normalized in seen:
            continue
        seen.add(email_normalized)
        normalized.append((email_normalized, email_display, normalize_role(invitation.get("role"))))

    now = utcnow_iso()
    expiry = _iso_after(INVITATION_DAYS)
    with db_connection() as conn:
        existing = {
            row["email_normalized"]: dict(row)
            for row in conn.execute(
                """
                SELECT * FROM note_share_invitations
                WHERE resource_type = ? AND resource_id = ? AND status = 'pending'
                """,
                [resource_type, str(resource_id)],
            ).fetchall()
        }
        retained = {email for email, _, _ in normalized}
        for email, row in existing.items():
            if email not in retained:
                conn.execute(
                    "UPDATE note_share_invitations SET status = 'revoked', updated_at = ? WHERE id = ?",
                    [now, row["id"]],
                )
        for email, display, role in normalized:
            row = existing.get(email)
            if row:
                conn.execute(
                    """
                    UPDATE note_share_invitations
                    SET email_display = ?, access_level = ?, invited_by_user_id = ?,
                        expires_at = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    [display, role, str(actor_user_id), expiry, now, row["id"]],
                )
            else:
                conn.execute(
                    """
                    INSERT INTO note_share_invitations (
                        id, owner_user_id, resource_type, resource_id, email_normalized,
                        email_display, access_level, invited_by_user_id, status,
                        expires_at, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)
                    """,
                    [
                        row_id(), str(owner_user_id), resource_type, str(resource_id), email,
                        display, role, str(actor_user_id), expiry, now, now,
                    ],
                )
        table = "notes" if resource_type == "note" else "note_folders"
        conn.execute(
            f"UPDATE {table} SET access_version = access_version + 1, updated_at = ? WHERE id = ?",
            [now, str(resource_id)],
        )
    return list_pending_invitations(resource_type, resource_id)


def claim_pending_invitations(user_id, email):
    normalized_email, _ = normalize_email(email)
    now = utcnow_iso()
    claimed = []
    with db_connection() as conn:
        conn.execute(
            """
            UPDATE note_share_invitations SET status = 'expired', updated_at = ?
            WHERE status = 'pending' AND expires_at <= ?
            """,
            [now, now],
        )
        rows = conn.execute(
            """
            SELECT * FROM note_share_invitations
            WHERE email_normalized = ? AND status = 'pending' AND expires_at > ?
            """,
            [normalized_email, now],
        ).fetchall()
        for row in rows:
            existing = conn.execute(
                """
                SELECT id, access_level FROM note_access_grants
                WHERE resource_type = ? AND resource_id = ?
                  AND principal_type = 'user' AND principal_id = ?
                """,
                [row["resource_type"], row["resource_id"], str(user_id)],
            ).fetchone()
            invited_role = normalize_role(row["access_level"])
            if existing:
                current = note_store._normalized_access_level(existing["access_level"])
                if note_store.ACCESS_RANK[invited_role] > note_store.ACCESS_RANK[current]:
                    conn.execute(
                        "UPDATE note_access_grants SET access_level = ?, updated_at = ? WHERE id = ?",
                        [invited_role, now, existing["id"]],
                    )
            else:
                conn.execute(
                    """
                    INSERT INTO note_access_grants (
                        id, owner_user_id, resource_type, resource_id, principal_type,
                        principal_id, access_level, granted_by_user_id, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 'user', ?, ?, ?, ?, ?)
                    """,
                    [
                        row_id(), row["owner_user_id"], row["resource_type"], row["resource_id"],
                        str(user_id), invited_role, row["invited_by_user_id"], now, now,
                    ],
                )
            table = "notes" if row["resource_type"] == "note" else "note_folders"
            conn.execute(
                f"UPDATE {table} SET access_version = access_version + 1, updated_at = ? WHERE id = ?",
                [now, row["resource_id"]],
            )
            conn.execute(
                """
                UPDATE note_share_invitations
                SET status = 'accepted', accepted_user_id = ?, accepted_at = ?, updated_at = ?
                WHERE id = ?
                """,
                [str(user_id), now, now, row["id"]],
            )
            claimed.append(dict(row))
    return claimed


def create_notification(user_id, notification_type, message, *, actor_user_id=None, note_id=None, thread_id=None, suggestion_id=None):
    if str(user_id) == str(actor_user_id or ""):
        return None
    notification_id = row_id()
    with db_connection() as conn:
        conn.execute(
            """
            INSERT INTO user_notifications (
                id, user_id, actor_user_id, notification_type, note_id, thread_id,
                suggestion_id, message, is_read, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
            """,
            [
                notification_id, str(user_id), str(actor_user_id) if actor_user_id else None,
                notification_type, note_id, thread_id, suggestion_id,
                str(message)[:500], utcnow_iso(),
            ],
        )
    return notification_id


def list_notifications(user_id, limit=50):
    with db_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM user_notifications WHERE user_id = ?
            ORDER BY created_at DESC LIMIT ?
            """,
            [str(user_id), min(max(int(limit), 1), 100)],
        ).fetchall()
        unread = conn.execute(
            "SELECT COUNT(*) AS count FROM user_notifications WHERE user_id = ? AND is_read = 0",
            [str(user_id)],
        ).fetchone()["count"]
    return {"notifications": _dicts(rows), "unread_count": int(unread or 0)}


def mark_notifications_read(user_id, notification_ids=None):
    now = utcnow_iso()
    with db_connection() as conn:
        if notification_ids:
            placeholders = ",".join("?" for _ in notification_ids)
            conn.execute(
                f"UPDATE user_notifications SET is_read = 1, read_at = ? WHERE user_id = ? AND id IN ({placeholders})",
                [now, str(user_id), *[str(value) for value in notification_ids]],
            )
        else:
            conn.execute(
                "UPDATE user_notifications SET is_read = 1, read_at = ? WHERE user_id = ? AND is_read = 0",
                [now, str(user_id)],
            )
    return list_notifications(user_id)


def _note_participant_ids(note_id, include_reviewers=True):
    note = note_store.get_note(note_id)
    if not note:
        return []
    ids = {str(note.get("user_id"))}
    grants = list(note_store.resource_grants("note", note_id))
    folder_id = note.get("folder_id")
    if folder_id:
        grants.extend(note_store.resource_grants("folder", folder_id))
    for grant in grants:
        if grant.get("principal_type") != "user":
            continue
        level = note_store._normalized_access_level(grant.get("access_level"))
        if level == "viewer" or (level == "reviewer" and not include_reviewers):
            continue
        ids.add(str(grant.get("principal_id")))
    return sorted(ids)


def list_suggestions(note_id):
    with db_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM note_suggestions WHERE note_id = ? ORDER BY created_at ASC",
            [str(note_id)],
        ).fetchall()
    payload = []
    for row in rows:
        item = dict(row)
        item["operations"] = json.loads(item.pop("operations_json") or "[]")
        item["author"] = note_store.get_safe_user(item.get("author_user_id"))
        item["resolved_by"] = note_store.get_safe_user(item.get("resolved_by_user_id"))
        payload.append(item)
    return payload


def create_suggestion(note_id, author_user_id, payload):
    operations = payload.get("operations")
    if not isinstance(operations, list) or not operations or len(operations) > 100:
        raise ValueError("A suggestion requires one to 100 operations.")
    encoded = json.dumps(operations, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > 256_000:
        raise ValueError("Suggestion is too large.")
    now = utcnow_iso()
    request_id = str(payload.get("client_request_id") or row_id())[:128]
    target_kind = str(payload.get("target_kind") or "body").strip().lower()
    if target_kind not in {"body", "title", "page_setup"}:
        raise ValueError("Unsupported suggestion target.")
    suggestion_id = row_id()
    with db_connection() as conn:
        existing = conn.execute(
            "SELECT id FROM note_suggestions WHERE note_id = ? AND author_user_id = ? AND client_request_id = ?",
            [str(note_id), str(author_user_id), request_id],
        ).fetchone()
        if existing:
            return next(row for row in list_suggestions(note_id) if row["id"] == existing["id"])
        conn.execute(
            """
            INSERT INTO note_suggestions (
                id, note_id, author_user_id, status, operation_kind, operations_json,
                anchor_start, anchor_end, block_id, base_state_vector, summary,
                created_at, updated_at, client_request_id, target_kind, scope_json
            ) VALUES (?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                suggestion_id, str(note_id), str(author_user_id),
                str(payload.get("operation_kind") or "batch")[:64], encoded,
                payload.get("anchor_start"), payload.get("anchor_end"), payload.get("block_id"),
                payload.get("base_state_vector"), str(payload.get("summary") or "")[:500], now, now,
                request_id, target_kind,
                json.dumps(payload.get("scope") or {}, separators=(",", ":")),
            ],
        )
    for user_id in _note_participant_ids(note_id, include_reviewers=False):
        create_notification(
            user_id, "note_suggestion_created", "A new note suggestion is ready for review.",
            actor_user_id=author_user_id, note_id=note_id, suggestion_id=suggestion_id,
        )
    _broadcast_review_event(note_id, "review.suggestion.created", suggestion_id)
    return next(row for row in list_suggestions(note_id) if row["id"] == suggestion_id)


def _apply_suggestion(note_id, row):
    with db_connection() as conn:
        document = conn.execute(
            "SELECT * FROM note_collaboration_documents WHERE note_id = ?",
            [str(note_id)],
        ).fetchone()
    if not document:
        raise ValueError("suggestion_conflicted")
    completed = subprocess.run(
        ["node", os.path.join(BASE_DIR, "collaboration", "apply-suggestion.mjs")],
        cwd=BASE_DIR,
        input=json.dumps({
            "ydoc_base64": base64.b64encode(document["ydoc_blob"]).decode("ascii"),
            "base_state_vector": row["base_state_vector"],
            "target_kind": row["target_kind"] or "body",
            "operations": json.loads(row["operations_json"] or "[]"),
        }),
        text=True,
        capture_output=True,
        timeout=30,
    )
    if completed.returncode:
        detail = completed.stderr or "suggestion_apply_failed"
        if "suggestion_conflicted" in detail:
            raise ValueError("suggestion_conflicted")
        raise ValueError("Unable to apply suggestion.")
    applied = json.loads(completed.stdout)
    blob = base64.b64decode(applied["ydoc_base64"], validate=True)
    now = utcnow_iso()
    with db_connection() as conn:
        conn.execute(
            """
            UPDATE note_collaboration_documents
            SET ydoc_blob = ?, durable_revision = durable_revision + 1,
                projection_revision = projection_revision + 1, updated_at = ?
            WHERE note_id = ?
            """,
            [blob, now, str(note_id)],
        )
        updates = {"updated_at": now}
        if applied.get("title") is not None:
            updates["title"] = str(applied["title"]) or "Untitled"
        if applied.get("content_json") is not None:
            updates["content"] = applied["content_json"]
            from services.notes_preview import preview_text_from_content
            updates["preview_text"] = preview_text_from_content(applied["content_json"])
        if applied.get("page_setup") is not None:
            updates["page_setup_json"] = json.dumps(applied["page_setup"], separators=(",", ":"))
        assignments = ", ".join(f"{key} = ?" for key in updates)
        conn.execute(
            f"UPDATE notes SET {assignments} WHERE id = ?",
            [*updates.values(), str(note_id)],
        )
    _reload_collaboration_document(note_id)


def resolve_suggestion(note_id, suggestion_id, resolver_user_id, status):
    if status not in {"accepted", "rejected", "conflicted"}:
        raise ValueError("Unsupported suggestion status.")
    now = utcnow_iso()
    with db_connection() as conn:
        row = conn.execute(
            "SELECT * FROM note_suggestions WHERE id = ? AND note_id = ?",
            [str(suggestion_id), str(note_id)],
        ).fetchone()
        if not row:
            return None
        if row["status"] != "open":
            raise ValueError("Suggestion is already resolved.")
    if status == "accepted":
        # Suggestions created before a note becomes collaboration-enabled do
        # not have a Yjs document to apply against. Preserve the existing
        # review workflow for those legacy notes; collaboration-enabled notes
        # still enforce the conflict-aware apply path.
        with db_connection() as conn:
            has_document = conn.execute(
                "SELECT 1 FROM note_collaboration_documents WHERE note_id = ?",
                [str(note_id)],
            ).fetchone() is not None
        if has_document:
            try:
                _apply_suggestion(note_id, row)
            except ValueError as exc:
                if str(exc) != "suggestion_conflicted":
                    raise
                status = "conflicted"
    with db_connection() as conn:
        conn.execute(
            """
            UPDATE note_suggestions SET status = ?, resolved_by_user_id = ?,
                resolved_at = ?, updated_at = ? WHERE id = ?
            """,
            [status, str(resolver_user_id), now, now, str(suggestion_id)],
        )
    create_notification(
        row["author_user_id"], f"note_suggestion_{status}",
        f"Your note suggestion was {status}.", actor_user_id=resolver_user_id,
        note_id=note_id, suggestion_id=suggestion_id,
    )
    _broadcast_review_event(note_id, "review.suggestion.updated", suggestion_id)
    return next(row for row in list_suggestions(note_id) if row["id"] == str(suggestion_id))


def _comment_anchor_from_row(thread):
    kind = thread.get("anchor_kind") or ("legacy" if thread.get("block_id") else "document")
    return {
        "kind": kind,
        "version": int(thread.get("anchor_version") or 1),
        "state": thread.get("anchor_state") or "detached",
        "relative_start": thread.get("relative_start") or thread.get("anchor_start"),
        "relative_end": thread.get("relative_end") or thread.get("anchor_end"),
        "start_block_id": thread.get("start_block_id") or thread.get("block_id"),
        "start_offset": thread.get("start_offset"),
        "end_block_id": thread.get("end_block_id") or thread.get("block_id"),
        "end_offset": thread.get("end_offset"),
        "quoted_text": thread.get("quoted_text") or "",
        "context_before": thread.get("context_before") or "",
        "context_after": thread.get("context_after") or "",
    }


def _validated_comment_anchor(payload):
    anchor = payload.get("anchor") if isinstance(payload.get("anchor"), dict) else {}
    kind = str(anchor.get("kind") or "document").strip().lower()
    if kind not in {"document", "legacy", "yjs"}:
        raise ValueError("Unsupported comment anchor kind.")
    state = str(anchor.get("state") or ("detached" if kind == "document" else "attached")).strip().lower()
    if state not in {"attached", "detached"}:
        raise ValueError("Unsupported comment anchor state.")

    def offset(name):
        value = anchor.get(name)
        if value is None:
            return None
        value = int(value)
        if value < 0 or value > 10_000_000:
            raise ValueError("Comment anchor offset is invalid.")
        return value

    return {
        "kind": kind,
        "version": max(1, min(int(anchor.get("version") or 1), 10)),
        "state": state,
        "relative_start": str(anchor.get("relative_start") or "")[:8000] or None,
        "relative_end": str(anchor.get("relative_end") or "")[:8000] or None,
        "start_block_id": str(anchor.get("start_block_id") or "")[:255] or None,
        "start_offset": offset("start_offset"),
        "end_block_id": str(anchor.get("end_block_id") or "")[:255] or None,
        "end_offset": offset("end_offset"),
        "quoted_text": str(anchor.get("quoted_text") or payload.get("quoted_text") or "")[:1000],
        "context_before": str(anchor.get("context_before") or "")[-250:],
        "context_after": str(anchor.get("context_after") or "")[:250],
    }


def list_comments(note_id, viewer_user_id=None, can_manage=False):
    with db_connection() as conn:
        threads = _dicts(conn.execute(
            "SELECT * FROM note_comment_threads WHERE note_id = ? ORDER BY created_at ASC",
            [str(note_id)],
        ).fetchall())
        replies = _dicts(conn.execute(
            """
            SELECT r.* FROM note_comment_replies r
            JOIN note_comment_threads t ON t.id = r.thread_id
            WHERE t.note_id = ? ORDER BY r.created_at ASC
            """,
            [str(note_id)],
        ).fetchall())
    by_thread = {}
    for reply in replies:
        reply["author"] = note_store.get_safe_user(reply.get("author_user_id"))
        reply["body"] = "" if reply.get("deleted_at") else reply.get("body", "")
        reply["can_edit"] = bool(
            not reply.get("deleted_at") and str(reply.get("author_user_id")) == str(viewer_user_id or "")
        )
        reply["can_delete"] = bool(
            not reply.get("deleted_at")
            and (can_manage or str(reply.get("author_user_id")) == str(viewer_user_id or ""))
        )
        by_thread.setdefault(reply["thread_id"], []).append(reply)
    for thread in threads:
        thread["author"] = note_store.get_safe_user(thread.get("author_user_id"))
        thread["resolved_by"] = note_store.get_safe_user(thread.get("resolved_by_user_id"))
        thread["replies"] = by_thread.get(thread["id"], [])
        thread["anchor"] = _comment_anchor_from_row(thread)
        thread["can_edit"] = bool(
            not thread.get("deleted_at") and str(thread.get("author_user_id")) == str(viewer_user_id or "")
        )
        thread["can_delete"] = bool(
            not thread.get("deleted_at")
            and (can_manage or str(thread.get("author_user_id")) == str(viewer_user_id or ""))
        )
        thread["can_resolve"] = bool(can_manage)
        if thread.get("deleted_at"):
            thread["body"] = ""
    return threads


def create_comment(note_id, author_user_id, payload):
    body = str(payload.get("body") or "").strip()
    if not body or len(body) > 5000:
        raise ValueError("Comment must contain 1 to 5,000 characters.")
    request_id = str(payload.get("client_request_id") or row_id()).strip()
    if len(request_id) > 128:
        raise ValueError("client_request_id is too long.")
    anchor = _validated_comment_anchor(payload)
    now = utcnow_iso()
    thread_id = row_id()
    created = False
    with db_connection() as conn:
        existing = conn.execute(
            "SELECT id FROM note_comment_threads WHERE note_id = ? AND author_user_id = ? AND client_request_id = ?",
            [str(note_id), str(author_user_id), request_id],
        ).fetchone()
        if existing:
            thread_id = existing["id"]
        else:
            try:
                conn.execute(
                    """
                    INSERT INTO note_comment_threads (
                        id, note_id, author_user_id, body, anchor_start, anchor_end,
                        block_id, quoted_text, status, created_at, updated_at,
                        client_request_id, anchor_kind, anchor_version, relative_start,
                        relative_end, start_block_id, start_offset, end_block_id, end_offset,
                        context_before, context_after, anchor_state
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        thread_id, str(note_id), str(author_user_id), body,
                        anchor["relative_start"], anchor["relative_end"], anchor["start_block_id"],
                        anchor["quoted_text"], now, now, request_id, anchor["kind"], anchor["version"],
                        anchor["relative_start"], anchor["relative_end"], anchor["start_block_id"],
                        anchor["start_offset"], anchor["end_block_id"], anchor["end_offset"],
                        anchor["context_before"], anchor["context_after"], anchor["state"],
                    ],
                )
                created = True
            except sqlite3.IntegrityError:
                existing = conn.execute(
                    "SELECT id FROM note_comment_threads WHERE note_id = ? AND author_user_id = ? AND client_request_id = ?",
                    [str(note_id), str(author_user_id), request_id],
                ).fetchone()
                if not existing:
                    raise
                thread_id = existing["id"]
    if created:
        for user_id in _note_participant_ids(note_id):
            create_notification(
                user_id, "note_comment_created", "A new comment was added to a shared note.",
                actor_user_id=author_user_id, note_id=note_id, thread_id=thread_id,
            )
        _broadcast_review_event(note_id, "review.comment.created", thread_id)
    result = next(row for row in list_comments(note_id, author_user_id) if row["id"] == thread_id)
    result["replayed"] = not created
    return result


def reply_to_comment(note_id, thread_id, author_user_id, body, client_request_id=None):
    body = str(body or "").strip()
    if not body or len(body) > 5000:
        raise ValueError("Reply must contain 1 to 5,000 characters.")
    request_id = str(client_request_id or row_id()).strip()
    if len(request_id) > 128:
        raise ValueError("client_request_id is too long.")
    now = utcnow_iso()
    reply_id = row_id()
    created = False
    with db_connection() as conn:
        thread = conn.execute(
            "SELECT * FROM note_comment_threads WHERE id = ? AND note_id = ?",
            [str(thread_id), str(note_id)],
        ).fetchone()
        if not thread:
            return None
        existing = conn.execute(
            "SELECT id FROM note_comment_replies WHERE thread_id = ? AND author_user_id = ? AND client_request_id = ?",
            [str(thread_id), str(author_user_id), request_id],
        ).fetchone()
        if existing:
            reply_id = existing["id"]
        else:
            conn.execute(
                "INSERT INTO note_comment_replies (id, thread_id, author_user_id, body, created_at, updated_at, client_request_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
                [reply_id, str(thread_id), str(author_user_id), body, now, now, request_id],
            )
            created = True
        conn.execute("UPDATE note_comment_threads SET updated_at = ? WHERE id = ?", [now, str(thread_id)])
        participant_rows = conn.execute(
            "SELECT DISTINCT author_user_id FROM note_comment_replies WHERE thread_id = ?",
            [str(thread_id)],
        ).fetchall()
    recipients = {str(thread["author_user_id"]), *[str(row["author_user_id"]) for row in participant_rows]}
    if created:
        for user_id in recipients:
            create_notification(
                user_id, "note_comment_reply", "Someone replied to a note comment.",
                actor_user_id=author_user_id, note_id=note_id, thread_id=thread_id,
            )
        _broadcast_review_event(note_id, "review.comment.replied", thread_id)
    result = next(row for row in list_comments(note_id, author_user_id) if row["id"] == str(thread_id))
    result["replayed"] = not created
    return result


def update_comment(note_id, thread_id, actor_user_id, body, *, can_manage=False):
    body = str(body or "").strip()
    if not body or len(body) > 5000:
        raise ValueError("Comment must contain 1 to 5,000 characters.")
    now = utcnow_iso()
    with db_connection() as conn:
        row = conn.execute(
            "SELECT * FROM note_comment_threads WHERE id = ? AND note_id = ?",
            [str(thread_id), str(note_id)],
        ).fetchone()
        if not row:
            return None
        if row["deleted_at"]:
            raise ValueError("Comment is deleted.")
        if not can_manage and str(row["author_user_id"]) != str(actor_user_id):
            raise PermissionError("You cannot edit this comment.")
        conn.execute(
            "UPDATE note_comment_threads SET body = ?, edited_at = ?, updated_at = ? WHERE id = ?",
            [body, now, now, str(thread_id)],
        )
    _broadcast_review_event(note_id, "review.comment.updated", thread_id)
    return next(item for item in list_comments(note_id, actor_user_id, can_manage) if item["id"] == str(thread_id))


def delete_comment(note_id, thread_id, actor_user_id, *, can_manage=False):
    now = utcnow_iso()
    with db_connection() as conn:
        row = conn.execute(
            "SELECT * FROM note_comment_threads WHERE id = ? AND note_id = ?",
            [str(thread_id), str(note_id)],
        ).fetchone()
        if not row:
            return None
        if not can_manage and str(row["author_user_id"]) != str(actor_user_id):
            raise PermissionError("You cannot delete this comment.")
        conn.execute(
            "UPDATE note_comment_threads SET deleted_at = ?, deleted_by_user_id = ?, anchor_state = 'detached', updated_at = ? WHERE id = ?",
            [now, str(actor_user_id), now, str(thread_id)],
        )
    _broadcast_review_event(note_id, "review.comment.deleted", thread_id)
    return next(item for item in list_comments(note_id, actor_user_id, can_manage) if item["id"] == str(thread_id))


def update_comment_reply(note_id, thread_id, reply_id, actor_user_id, body, *, can_manage=False, delete=False):
    clean_body = str(body or "").strip()
    if not delete and (not clean_body or len(clean_body) > 5000):
        raise ValueError("Reply must contain 1 to 5,000 characters.")
    now = utcnow_iso()
    with db_connection() as conn:
        row = conn.execute(
            """
            SELECT r.* FROM note_comment_replies r
            JOIN note_comment_threads t ON t.id = r.thread_id
            WHERE r.id = ? AND r.thread_id = ? AND t.note_id = ?
            """,
            [str(reply_id), str(thread_id), str(note_id)],
        ).fetchone()
        if not row:
            return None
        if not can_manage and str(row["author_user_id"]) != str(actor_user_id):
            raise PermissionError("You cannot change this reply.")
        if delete:
            conn.execute(
                "UPDATE note_comment_replies SET deleted_at = ?, deleted_by_user_id = ?, updated_at = ? WHERE id = ?",
                [now, str(actor_user_id), now, str(reply_id)],
            )
        else:
            conn.execute(
                "UPDATE note_comment_replies SET body = ?, edited_at = ?, updated_at = ? WHERE id = ?",
                [clean_body, now, now, str(reply_id)],
            )
        conn.execute("UPDATE note_comment_threads SET updated_at = ? WHERE id = ?", [now, str(thread_id)])
    _broadcast_review_event(note_id, "review.comment.replied", thread_id)
    return next(item for item in list_comments(note_id, actor_user_id, can_manage) if item["id"] == str(thread_id))


def set_comment_status(note_id, thread_id, resolver_user_id, status):
    if status not in {"open", "resolved"}:
        raise ValueError("Unsupported comment status.")
    now = utcnow_iso()
    with db_connection() as conn:
        row = conn.execute(
            "SELECT * FROM note_comment_threads WHERE id = ? AND note_id = ?",
            [str(thread_id), str(note_id)],
        ).fetchone()
        if not row:
            return None
        conn.execute(
            """
            UPDATE note_comment_threads SET status = ?, resolved_by_user_id = ?,
                resolved_at = ?, updated_at = ? WHERE id = ?
            """,
            [
                status,
                str(resolver_user_id) if status == "resolved" else None,
                now if status == "resolved" else None,
                now, str(thread_id),
            ],
        )
    create_notification(
        row["author_user_id"], f"note_comment_{status}", f"Your note comment was {status}.",
        actor_user_id=resolver_user_id, note_id=note_id, thread_id=thread_id,
    )
    _broadcast_review_event(note_id, "review.comment.status", thread_id)
    return next(item for item in list_comments(note_id) if item["id"] == str(thread_id))


def create_version(note_id, actor_user_id=None, *, reason="automatic", name=None):
    now = utcnow_iso()
    expires = _iso_after(VERSION_DAYS)
    with db_connection() as conn:
        note = conn.execute("SELECT * FROM notes WHERE id = ?", [str(note_id)]).fetchone()
        if not note:
            return None
        collab = conn.execute(
            "SELECT * FROM note_collaboration_documents WHERE note_id = ?",
            [str(note_id)],
        ).fetchone()
        version_id = row_id()
        conn.execute(
            """
            INSERT INTO note_versions (
                id, note_id, actor_user_id, reason, name, ydoc_blob, title,
                content, page_setup_json, durable_revision, created_at, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                version_id, str(note_id), str(actor_user_id) if actor_user_id else None,
                str(reason)[:64], str(name)[:120] if name else None,
                collab["ydoc_blob"] if collab else None, note["title"] or "Untitled",
                note["content"] or "", note["page_setup_json"],
                int(collab["durable_revision"] or 0) if collab else 0, now, expires,
            ],
        )
    return get_version(note_id, version_id)


def get_version(note_id, version_id):
    with db_connection() as conn:
        row = conn.execute(
            "SELECT * FROM note_versions WHERE id = ? AND note_id = ?",
            [str(version_id), str(note_id)],
        ).fetchone()
    if not row:
        return None
    payload = dict(row)
    payload.pop("ydoc_blob", None)
    payload["actor"] = note_store.get_safe_user(payload.get("actor_user_id"))
    return payload


def list_versions(note_id):
    with db_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM note_versions WHERE note_id = ? AND expires_at > ? ORDER BY created_at DESC",
            [str(note_id), utcnow_iso()],
        ).fetchall()
    payload = []
    for row in rows:
        item = dict(row)
        item.pop("ydoc_blob", None)
        item["actor"] = note_store.get_safe_user(item.get("actor_user_id"))
        payload.append(item)
    return payload


def restore_version(note_id, version_id, actor_user_id):
    create_version(note_id, actor_user_id, reason="before_restore")
    now = utcnow_iso()
    with db_connection() as conn:
        version = conn.execute(
            "SELECT * FROM note_versions WHERE id = ? AND note_id = ?",
            [str(version_id), str(note_id)],
        ).fetchone()
        if not version:
            return None
        conn.execute(
            """
            UPDATE notes SET title = ?, content = ?, page_setup_json = ?,
                updated_at = ?, access_version = access_version + 1 WHERE id = ?
            """,
            [version["title"], version["content"], version["page_setup_json"], now, str(note_id)],
        )
        if version["ydoc_blob"] is not None:
            conn.execute(
                """
                INSERT INTO note_collaboration_documents (
                    note_id, ydoc_blob, schema_version, durable_revision,
                    projection_revision, initialized_at, updated_at
                ) VALUES (?, ?, 1, 1, 1, ?, ?)
                ON CONFLICT(note_id) DO UPDATE SET
                    ydoc_blob = excluded.ydoc_blob,
                    durable_revision = note_collaboration_documents.durable_revision + 1,
                    projection_revision = note_collaboration_documents.projection_revision + 1,
                    updated_at = excluded.updated_at
                """,
                [str(note_id), version["ydoc_blob"], now, now],
            )
    return note_store.get_note(note_id)


def get_collaboration_document(note_id):
    with db_connection() as conn:
        row = conn.execute(
            "SELECT * FROM note_collaboration_documents WHERE note_id = ?",
            [str(note_id)],
        ).fetchone()
    return dict(row) if row else None


def store_collaboration_document(note_id, blob, *, title=None, content=None, page_setup_json=None, schema_version=1):
    if not isinstance(blob, (bytes, bytearray)) or len(blob) > 10 * 1024 * 1024:
        raise ValueError("Collaboration document is invalid or too large.")
    now = utcnow_iso()
    with db_connection() as conn:
        note = conn.execute("SELECT * FROM notes WHERE id = ?", [str(note_id)]).fetchone()
        if not note:
            return None
        current = conn.execute(
            "SELECT durable_revision FROM note_collaboration_documents WHERE note_id = ?",
            [str(note_id)],
        ).fetchone()
        revision = int(current["durable_revision"] or 0) + 1 if current else 1
        conn.execute(
            """
            INSERT INTO note_collaboration_documents (
                note_id, ydoc_blob, schema_version, durable_revision,
                projection_revision, initialized_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(note_id) DO UPDATE SET
                ydoc_blob = excluded.ydoc_blob,
                schema_version = excluded.schema_version,
                durable_revision = excluded.durable_revision,
                projection_revision = excluded.projection_revision,
                updated_at = excluded.updated_at
            """,
            [str(note_id), bytes(blob), int(schema_version), revision, revision, now, now],
        )
        updates = {"updated_at": now, "collaboration_enabled": 1}
        if title is not None:
            updates["title"] = str(title).strip() or "Untitled"
        if content is not None:
            updates["content"] = str(content)
            from services.notes_preview import preview_text_from_content
            updates["preview_text"] = preview_text_from_content(str(content))
        if page_setup_json is not None:
            updates["page_setup_json"] = str(page_setup_json)
        assignments = ", ".join(f"{key} = ?" for key in updates)
        conn.execute(
            f"UPDATE notes SET {assignments} WHERE id = ?",
            [*updates.values(), str(note_id)],
        )
    return {"note_id": str(note_id), "durable_revision": revision, "updated_at": now}


def transfer_note(note_id, current_owner_id, new_owner_id):
    now = utcnow_iso()
    create_version(note_id, current_owner_id, reason="before_transfer")
    with db_connection() as conn:
        note = conn.execute(
            "SELECT * FROM notes WHERE id = ? AND user_id = ?",
            [str(note_id), str(current_owner_id)],
        ).fetchone()
        if not note:
            return None
        target = conn.execute("SELECT id FROM users WHERE id = ?", [str(new_owner_id)]).fetchone()
        grant = conn.execute(
            """
            SELECT access_level FROM note_access_grants
            WHERE resource_type = 'note' AND resource_id = ?
              AND principal_type = 'user' AND principal_id = ?
            """,
            [str(note_id), str(new_owner_id)],
        ).fetchone()
        inherited = None
        if note["folder_id"]:
            inherited = conn.execute(
                """
                SELECT access_level FROM note_access_grants
                WHERE resource_type = 'folder' AND resource_id = ?
                  AND principal_type = 'user' AND principal_id = ?
                """,
                [note["folder_id"], str(new_owner_id)],
            ).fetchone()
            folder_grants = conn.execute(
                """
                SELECT * FROM note_access_grants
                WHERE resource_type = 'folder' AND resource_id = ? AND principal_type = 'user'
                """,
                [note["folder_id"]],
            ).fetchall()
            for inherited_grant in folder_grants:
                conn.execute(
                    """
                    INSERT INTO note_access_grants (
                        id, owner_user_id, resource_type, resource_id, principal_type,
                        principal_id, access_level, granted_by_user_id, created_at, updated_at
                    ) VALUES (?, ?, 'note', ?, 'user', ?, ?, ?, ?, ?)
                    ON CONFLICT(resource_type, resource_id, principal_type, principal_id)
                    DO UPDATE SET access_level = CASE
                        WHEN excluded.access_level = 'editor' THEN 'editor'
                        WHEN excluded.access_level = 'reviewer' AND note_access_grants.access_level = 'viewer' THEN 'reviewer'
                        ELSE note_access_grants.access_level END,
                        updated_at = excluded.updated_at
                    """,
                    [
                        row_id(), str(new_owner_id), str(note_id), inherited_grant["principal_id"],
                        inherited_grant["access_level"], str(current_owner_id), now, now,
                    ],
                )
        effective_target = None
        for candidate in (grant, inherited):
            if not candidate:
                continue
            level = note_store._normalized_access_level(candidate["access_level"])
            if effective_target is None or note_store.ACCESS_RANK[level] > note_store.ACCESS_RANK[effective_target]:
                effective_target = level
        if not target or effective_target != "editor":
            raise ValueError("Ownership can only be transferred to an existing Editor.")
        conn.execute(
            "DELETE FROM note_access_grants WHERE resource_type = 'note' AND resource_id = ? AND principal_type = 'user' AND principal_id = ?",
            [str(note_id), str(new_owner_id)],
        )
        conn.execute(
            "UPDATE note_access_grants SET owner_user_id = ? WHERE resource_type = 'note' AND resource_id = ?",
            [str(new_owner_id), str(note_id)],
        )
        conn.execute(
            """
            INSERT INTO note_access_grants (
                id, owner_user_id, resource_type, resource_id, principal_type,
                principal_id, access_level, granted_by_user_id, created_at, updated_at
            ) VALUES (?, ?, 'note', ?, 'user', ?, 'editor', ?, ?, ?)
            """,
            [row_id(), str(new_owner_id), str(note_id), str(current_owner_id), str(current_owner_id), now, now],
        )
        conn.execute(
            "UPDATE notes SET user_id = ?, folder_id = NULL, access_version = access_version + 1, updated_at = ? WHERE id = ?",
            [str(new_owner_id), now, str(note_id)],
        )
        conn.execute(
            "UPDATE note_share_invitations SET owner_user_id = ?, updated_at = ? WHERE resource_type = 'note' AND resource_id = ? AND status = 'pending'",
            [str(new_owner_id), now, str(note_id)],
        )
    create_notification(
        new_owner_id, "note_ownership_transferred", "You are now the owner of a shared note.",
        actor_user_id=current_owner_id, note_id=note_id,
    )
    return note_store.get_note(note_id)


def transfer_folder(folder_id, current_owner_id, new_owner_id):
    now = utcnow_iso()
    with db_connection() as conn:
        folder = conn.execute(
            "SELECT * FROM note_folders WHERE id = ? AND user_id = ?",
            [str(folder_id), str(current_owner_id)],
        ).fetchone()
        if not folder:
            return None
        target = conn.execute("SELECT id FROM users WHERE id = ?", [str(new_owner_id)]).fetchone()
        grant = conn.execute(
            """
            SELECT access_level FROM note_access_grants
            WHERE resource_type = 'folder' AND resource_id = ?
              AND principal_type = 'user' AND principal_id = ?
            """,
            [str(folder_id), str(new_owner_id)],
        ).fetchone()
        if not target or not grant or grant["access_level"] != "editor":
            raise ValueError("Ownership can only be transferred to an existing folder Editor.")
        note_ids = [row["id"] for row in conn.execute("SELECT id FROM notes WHERE folder_id = ?", [str(folder_id)]).fetchall()]
        for note_id in note_ids:
            # Record a projection snapshot inside the same transaction.
            note = conn.execute("SELECT * FROM notes WHERE id = ?", [note_id]).fetchone()
            collab = conn.execute("SELECT * FROM note_collaboration_documents WHERE note_id = ?", [note_id]).fetchone()
            conn.execute(
                """
                INSERT INTO note_versions (
                    id, note_id, actor_user_id, reason, ydoc_blob, title, content,
                    page_setup_json, durable_revision, created_at, expires_at
                ) VALUES (?, ?, ?, 'before_transfer', ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    row_id(), note_id, str(current_owner_id), collab["ydoc_blob"] if collab else None,
                    note["title"] or "Untitled", note["content"] or "", note["page_setup_json"],
                    int(collab["durable_revision"] or 0) if collab else 0, now, _iso_after(VERSION_DAYS),
                ],
            )
        conn.execute(
            "DELETE FROM note_access_grants WHERE resource_type = 'folder' AND resource_id = ? AND principal_type = 'user' AND principal_id = ?",
            [str(folder_id), str(new_owner_id)],
        )
        conn.execute(
            "UPDATE note_access_grants SET owner_user_id = ? WHERE resource_type = 'folder' AND resource_id = ?",
            [str(new_owner_id), str(folder_id)],
        )
        conn.execute(
            """
            INSERT INTO note_access_grants (
                id, owner_user_id, resource_type, resource_id, principal_type,
                principal_id, access_level, granted_by_user_id, created_at, updated_at
            ) VALUES (?, ?, 'folder', ?, 'user', ?, 'editor', ?, ?, ?)
            """,
            [row_id(), str(new_owner_id), str(folder_id), str(current_owner_id), str(current_owner_id), now, now],
        )
        conn.execute(
            "UPDATE note_folders SET user_id = ?, access_version = access_version + 1, updated_at = ? WHERE id = ?",
            [str(new_owner_id), now, str(folder_id)],
        )
        conn.execute(
            "UPDATE notes SET user_id = ?, access_version = access_version + 1, updated_at = ? WHERE folder_id = ?",
            [str(new_owner_id), now, str(folder_id)],
        )
        if note_ids:
            placeholders = ",".join("?" for _ in note_ids)
            conn.execute(
                f"UPDATE note_access_grants SET owner_user_id = ? WHERE resource_type = 'note' AND resource_id IN ({placeholders})",
                [str(new_owner_id), *note_ids],
            )
            conn.execute(
                f"UPDATE note_share_invitations SET owner_user_id = ?, updated_at = ? WHERE resource_type = 'note' AND resource_id IN ({placeholders}) AND status = 'pending'",
                [str(new_owner_id), now, *note_ids],
            )
        conn.execute(
            "UPDATE note_share_invitations SET owner_user_id = ?, updated_at = ? WHERE resource_type = 'folder' AND resource_id = ? AND status = 'pending'",
            [str(new_owner_id), now, str(folder_id)],
        )
    create_notification(
        new_owner_id, "note_folder_ownership_transferred", "You are now the owner of a shared notes folder.",
        actor_user_id=current_owner_id,
    )
    return note_store.get_folder(folder_id)


def migrate_notes_to_collaboration(*, report_only=True, note_ids=None):
    """Convert legacy BlockNote JSON with the production schema before enabling Yjs."""
    selected = {str(value) for value in (note_ids or []) if value}
    with db_connection() as conn:
        rows = _dicts(conn.execute(
            "SELECT id, title, content, page_setup_json, collaboration_enabled FROM notes ORDER BY created_at"
        ).fetchall())
    if selected:
        rows = [row for row in rows if str(row["id"]) in selected]

    result = {"checked": 0, "ready": 0, "migrated": 0, "skipped": 0, "failed": []}
    converter = os.path.join(BASE_DIR, "collaboration", "convert-note.mjs")
    for note in rows:
        result["checked"] += 1
        if note.get("collaboration_enabled"):
            result["skipped"] += 1
            continue
        try:
            blocks = json.loads(note.get("content") or "[]")
            if not isinstance(blocks, list):
                raise ValueError("content is not a BlockNote array")
            if not blocks:
                blocks = [{"type": "paragraph", "content": ""}]
            page_setup = json.loads(note.get("page_setup_json") or "{}")
            completed = subprocess.run(
                ["node", converter],
                cwd=BASE_DIR,
                input=json.dumps({
                    "title": note.get("title") or "Untitled",
                    "blocks": blocks,
                    "page_setup": page_setup if isinstance(page_setup, dict) else {},
                }),
                text=True,
                capture_output=True,
                timeout=30,
                check=True,
            )
            converted = json.loads(completed.stdout)
            blob = base64.b64decode(converted["ydoc_base64"], validate=True)
            result["ready"] += 1
            if report_only:
                continue
            create_version(note["id"], reason="before_migration")
            now = utcnow_iso()
            with db_connection() as conn:
                existing = conn.execute(
                    "SELECT 1 FROM note_collaboration_documents WHERE note_id = ?",
                    [str(note["id"])],
                ).fetchone()
                if existing:
                    result["skipped"] += 1
                    continue
                conn.execute(
                    """
                    INSERT INTO note_collaboration_documents (
                        note_id, ydoc_blob, schema_version, durable_revision,
                        projection_revision, initialized_at, updated_at
                    ) VALUES (?, ?, 1, 1, 1, ?, ?)
                    """,
                    [str(note["id"]), blob, now, now],
                )
                conn.execute(
                    "UPDATE notes SET collaboration_enabled = 1, updated_at = ? WHERE id = ?",
                    [now, str(note["id"])],
                )
            result["migrated"] += 1
        except (ValueError, KeyError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
            detail = str(exc)
            if isinstance(exc, subprocess.CalledProcessError) and exc.stderr:
                detail = " | ".join(exc.stderr.strip().splitlines()[-8:]) or detail
            result["failed"].append({"note_id": str(note["id"]), "error": detail[:500]})
    return result


def cleanup_expired_collaboration_rows():
    now = utcnow_iso()
    with db_connection() as conn:
        invitations = conn.execute(
            "UPDATE note_share_invitations SET status = 'expired', updated_at = ? WHERE status = 'pending' AND expires_at <= ?",
            [now, now],
        ).rowcount
        versions = conn.execute("DELETE FROM note_versions WHERE expires_at <= ?", [now]).rowcount
    return {"invitations_expired": invitations, "versions_deleted": versions}
