import io
import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

from flask import Flask
from flask_login import UserMixin

import app as app_module
import blueprints.chat_api as chat_api
from extensions import login_manager
from tests.support.harness import reset_flask_login_manager


class _RouteUser(UserMixin):
    def __init__(self, user_id="user-1"):
        self.id = user_id
        self.name = "Derek C"
        self.username = "derek"
        self.picture_url = "https://example.test/avatar.png"
        self.school = "Emory University"
        self.school_key = "emory-university"
        self.major = "CS"
        self.graduation_year = "2026"
        self.class_year = "2026"
        self.education_level = "Undergraduate"
        self.tier = "free"


class RegisteredChatDeliveryRouteTests(unittest.TestCase):
    """Characterize delivery routes before their orchestration is extracted."""

    def setUp(self):
        previous_loader = login_manager._user_callback
        previous_unauthorized = login_manager.unauthorized_callback
        previous_login_view = login_manager.login_view
        self.addCleanup(setattr, login_manager, "_user_callback", previous_loader)
        self.addCleanup(setattr, login_manager, "unauthorized_callback", previous_unauthorized)
        self.addCleanup(setattr, login_manager, "login_view", previous_login_view)
        self.addCleanup(reset_flask_login_manager)

        self.app = Flask(__name__)
        self.app.secret_key = "test"
        self.app.config.update(SERVER_NAME="example.test", PROPAGATE_EXCEPTIONS=False)
        login_manager.unauthorized_callback = None
        login_manager.login_view = None
        login_manager.init_app(self.app)
        self.app.register_blueprint(chat_api.chat_api_bp)
        self.user = _RouteUser()

        @login_manager.user_loader
        def load_user(user_id):
            return self.user if user_id == self.user.id else None

    def _client(self):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = self.user.id
            session["_fresh"] = True
        return client

    def test_channel_listing_keeps_payload_shape_and_patchable_adapters(self):
        channel = {"$id": "nest_chat", "kind": "discord", "read_only": False}
        rows = [{"$id": "message-1"}]
        with self._client() as client:
            with patch.object(chat_api, "current_user", self.user), \
                    patch.object(chat_api, "get_row_safe", return_value=channel), \
                    patch.object(chat_api, "_can_access_channel", return_value=True), \
                    patch.object(chat_api, "_list_messages", return_value=rows) as list_messages, \
                    patch.object(chat_api, "_serialize_messages", return_value=[{"id": "message-1"}]), \
                    patch.object(chat_api, "_channel_payload", return_value={"id": "nest_chat"}), \
                    patch.object(chat_api, "_room_message_metadata", return_value={"unread_count": 2}):
                response = client.get("/api/chat/channels/nest_chat/messages?after=cursor")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {
            "messages": [{"id": "message-1"}],
            "has_more": False,
            "channel": {"id": "nest_chat"},
            "unread_count": 2,
        })
        list_messages.assert_called_once_with("channel", "nest_chat", None, "cursor", after_message_id=None)

    def test_channel_send_success_preserves_event_serialization_and_invite_side_effects(self):
        channel = {"$id": "nest_chat", "kind": "appwrite", "read_only": False}
        row = {"$id": "message-1", "channel_id": "nest_chat", "user_id": "user-1"}
        with self._client() as client:
            with patch.object(chat_api, "current_user", self.user), \
                    patch.object(chat_api, "_current_user_id", return_value="user-1"), \
                    patch.object(chat_api, "get_row_safe", return_value=channel), \
                    patch.object(chat_api, "_can_access_channel", return_value=True), \
                    patch.object(chat_api, "_message_media_payload", return_value=("hello", [], None)) as media, \
                    patch.object(chat_api, "_previews_for_content", return_value=[]), \
                    patch.object(chat_api, "_now", return_value=datetime(2026, 7, 1, tzinfo=timezone.utc)), \
                    patch.object(chat_api, "format_datetime", return_value="2026-07-01T00:00:00Z"), \
                    patch.object(chat_api, "render_markdown", return_value="<p>hello</p>"), \
                    patch.object(chat_api, "create_row_safe", return_value=row) as create_row, \
                    patch.object(chat_api, "emit_chat_event") as emit_event, \
                    patch.object(chat_api, "_serialize_message", return_value={"id": "message-1"}), \
                    patch.object(chat_api.invites, "record_activation") as activation:
                response = client.post("/api/chat/channels/nest_chat/messages", json={"content": "hello"})

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.get_json(), {"message": {"id": "message-1"}})
        media.assert_called_once_with()
        create_row.assert_called_once()
        emit_event.assert_called_once()
        activation.assert_called_once_with("user-1", "chat_message")

    def test_channel_send_maps_discord_failure_to_502_before_persistence(self):
        channel = {"$id": "nest_chat", "kind": "discord", "read_only": False}
        with self._client() as client:
            with patch.object(chat_api, "current_user", self.user), \
                    patch.object(chat_api, "_current_user_id", return_value="user-1"), \
                    patch.object(chat_api, "get_row_safe", return_value=channel), \
                    patch.object(chat_api, "_can_access_channel", return_value=True), \
                    patch.object(chat_api, "_message_media_payload", return_value=("hello", [], None)), \
                    patch.object(chat_api, "_previews_for_content", return_value=[]), \
                    patch.object(chat_api, "_now", return_value=datetime(2026, 7, 1, tzinfo=timezone.utc)), \
                    patch.object(chat_api, "format_datetime", return_value="now"), \
                    patch.object(chat_api, "render_markdown", return_value="rendered"), \
                    patch.object(chat_api, "execute_chat_webhook", side_effect=RuntimeError("discord down")) as webhook, \
                    patch.object(chat_api, "create_row_safe") as create_row:
                response = client.post("/api/chat/channels/nest_chat/messages", json={"content": "hello"})

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.get_json(), {"error": "Unable to send to Discord right now."})
        webhook.assert_called_once()
        create_row.assert_not_called()

    def test_channel_send_rolls_back_message_when_attachment_binding_fails(self):
        channel = {"$id": "nest_chat", "kind": "appwrite", "read_only": False}
        row = {"$id": "message-1", "channel_id": "nest_chat", "user_id": "user-1"}
        with self._client() as client:
            with patch.object(chat_api, "current_user", self.user), \
                    patch.object(chat_api, "_current_user_id", return_value="user-1"), \
                    patch.object(chat_api, "get_row_safe", return_value=channel), \
                    patch.object(chat_api, "_can_access_channel", return_value=True), \
                    patch.object(chat_api, "_message_media_payload", return_value=("hello", ["attachment-1"], None)), \
                    patch.object(chat_api, "_previews_for_content", return_value=[]), \
                    patch.object(chat_api, "_now", return_value=datetime(2026, 7, 1, tzinfo=timezone.utc)), \
                    patch.object(chat_api, "format_datetime", return_value="now"), \
                    patch.object(chat_api, "render_markdown", return_value="rendered"), \
                    patch.object(chat_api, "create_row_safe", return_value=row), \
                    patch.object(chat_api, "bind_pending", side_effect=chat_api.AttachmentError("bind failed")), \
                    patch.object(chat_api, "delete_row_safe") as delete_row:
                response = client.post("/api/chat/channels/nest_chat/messages", json={"content": "hello"})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json(), {"error": "bind failed"})
        delete_row.assert_called_once_with(chat_api.COLLECTIONS["chat_messages"], "message-1")

    def test_delete_message_success_preserves_webhook_cleanup_event_and_audit(self):
        created_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        row = {
            "$id": "message-1",
            "user_id": "user-1",
            "source": "discord",
            "discord_message_id": "discord-1",
            "discord_webhook_id": "webhook-1",
            "channel_id": "nest_chat",
            "created_at": created_at.isoformat(),
        }
        channel = {"$id": "nest_chat", "kind": "discord"}
        with self._client() as client:
            with patch.object(chat_api, "current_user", self.user), \
                    patch.object(chat_api, "_current_user_id", return_value="user-1"), \
                    patch.object(chat_api, "get_row_safe", side_effect=[row, channel]), \
                    patch.object(chat_api, "delete_webhook_message") as webhook_delete, \
                    patch.object(chat_api, "_now", side_effect=[datetime.now(timezone.utc), datetime.now(timezone.utc)]), \
                    patch.object(chat_api, "format_datetime", return_value="deleted-at"), \
                    patch.object(chat_api, "update_row_safe") as update_row, \
                    patch.object(chat_api, "delete_message_attachments") as delete_attachments, \
                    patch.object(chat_api, "emit_chat_event") as emit_event, \
                    patch.object(chat_api, "_emit_chat_delete_audit") as audit:
                response = client.delete("/api/chat/messages/message-1")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"status": "ok"})
        webhook_delete.assert_called_once_with("webhook-1", "discord-1")
        update_row.assert_called_once()
        delete_attachments.assert_called_once_with("message-1")
        emit_event.assert_called_once()
        audit.assert_called_once_with(row, "deleted-at")

    def test_delete_message_maps_discord_failure_to_502_without_local_delete(self):
        row = {
            "$id": "message-1",
            "user_id": "user-1",
            "source": "discord",
            "discord_message_id": "discord-1",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        with self._client() as client:
            with patch.object(chat_api, "current_user", self.user), \
                    patch.object(chat_api, "_current_user_id", return_value="user-1"), \
                    patch.object(chat_api, "get_row_safe", return_value=row), \
                    patch.object(chat_api, "delete_webhook_message", side_effect=RuntimeError("discord down")), \
                    patch.object(chat_api, "update_row_safe") as update_row:
                response = client.delete("/api/chat/messages/message-1")

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.get_json(), {"error": "Unable to delete the Discord message right now."})
        update_row.assert_not_called()

    def test_dm_search_success_and_backend_failure_keep_empty_result_contract(self):
        users = [{"$id": "user-2", "name": "Pat Student", "username": "pat"}]
        with self._client() as client:
            with patch.object(chat_api, "current_user", self.user), \
                    patch.object(chat_api, "_current_user_id", return_value="user-1"), \
                    patch.object(chat_api, "list_rows_all", return_value=users), \
                    patch.object(chat_api, "_public_user", return_value={"id": "user-2", "name": "Pat Student"}):
                response = client.get("/api/chat/dm/search?q=pat")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"results": [{"id": "user-2", "name": "Pat Student"}]})

        with self._client() as client:
            with patch.object(chat_api, "current_user", self.user), \
                    patch.object(chat_api, "_current_user_id", return_value="user-1"), \
                    patch.object(chat_api, "list_rows_all", side_effect=chat_api.AppwriteException("down")):
                response = client.get("/api/chat/dm/search?q=pat")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"results": []})

    def test_dm_thread_creation_preserves_event_and_payload_contract(self):
        thread = {"$id": "thread-1", "participant_a": "user-1", "participant_b": "user-2"}
        with self._client() as client:
            with patch.object(chat_api, "current_user", self.user), \
                    patch.object(chat_api, "_current_user_id", return_value="user-1"), \
                    patch.object(chat_api, "_get_or_create_thread", return_value=thread) as create_thread, \
                    patch.object(chat_api, "_thread_participant_ids", return_value=["user-1", "user-2"]), \
                    patch.object(chat_api, "emit_chat_event") as emit_event, \
                    patch.object(chat_api, "_thread_payload", return_value={"id": "thread-1"}):
                response = client.post("/api/chat/dm/threads", json={"user_id": "user-2"})

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.get_json(), {"thread": {"id": "thread-1"}})
        create_thread.assert_called_once_with("user-2")
        emit_event.assert_called_once()

    def test_dm_thread_message_get_preserves_read_metadata_shape(self):
        thread = {"$id": "thread-1", "participant_a": "user-1", "participant_b": "user-2"}
        with self._client() as client:
            with patch.object(chat_api, "current_user", self.user), \
                    patch.object(chat_api, "_current_user_id", return_value="user-1"), \
                    patch.object(chat_api, "_thread_for_user", return_value=thread), \
                    patch.object(chat_api, "_other_participant", return_value={"$id": "user-2"}), \
                    patch.object(chat_api, "_public_user", return_value={"id": "user-2"}), \
                    patch.object(chat_api, "_list_messages", return_value=[{"$id": "message-1"}]), \
                    patch.object(chat_api, "_serialize_messages", return_value=[{"id": "message-1"}]), \
                    patch.object(chat_api, "_thread_payload", return_value={"other_user": {"id": "user-2"}, "blocked": False}), \
                    patch.object(chat_api, "_room_message_metadata", return_value={"unread_count": 0}), \
                    patch.object(chat_api, "_presence_scope", return_value="thread:thread-1"), \
                    patch.object(chat_api, "_presence_read_permissions_for_thread", return_value=["read"]):
                response = client.get("/api/chat/dm/threads/thread-1/messages")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["messages"], [{"id": "message-1"}])
        self.assertEqual(payload["thread"]["other_user"], {"id": "user-2"})
        self.assertEqual(payload["unread_count"], 0)

    def test_dm_send_success_preserves_notification_and_invite_ordered_side_effects(self):
        thread = {"$id": "thread-1", "participant_a": "user-1", "participant_b": "user-2"}
        other = {"id": "user-2", "name": "Pat", "username": "pat"}
        row = {"$id": "message-1", "thread_id": "thread-1", "user_id": "user-1"}
        with self._client() as client:
            with patch.object(chat_api, "current_user", self.user), \
                    patch.object(chat_api, "_current_user_id", return_value="user-1"), \
                    patch.object(chat_api, "_thread_for_user", return_value=thread), \
                    patch.object(chat_api, "_other_participant", return_value=other), \
                    patch.object(chat_api, "_public_user", return_value=other), \
                    patch.object(chat_api, "_is_blocked_between", return_value=False), \
                    patch.object(chat_api, "_message_media_payload", return_value=("hello", [], None)), \
                    patch.object(chat_api, "_previews_for_content", return_value=[]), \
                    patch.object(chat_api, "_now", return_value=datetime(2026, 7, 1, tzinfo=timezone.utc)), \
                    patch.object(chat_api, "format_datetime", return_value="now"), \
                    patch.object(chat_api, "render_markdown", return_value="rendered"), \
                    patch.object(chat_api, "create_row_safe", return_value=row), \
                    patch.object(chat_api, "update_row_safe") as update_thread, \
                    patch.object(chat_api, "emit_chat_event") as emit_event, \
                    patch.object(chat_api.notifications, "notify") as notify, \
                    patch.object(chat_api.invites, "record_activation") as activation, \
                    patch.object(chat_api, "_serialize_message", return_value={"id": "message-1"}):
                response = client.post("/api/chat/dm/threads/thread-1/messages", json={"content": "hello"})

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.get_json(), {"message": {"id": "message-1"}})
        update_thread.assert_called_once_with(
            chat_api.COLLECTIONS["chat_dm_threads"], "thread-1", {"last_message_at": "now", "updated_at": "now"}
        )
        emit_event.assert_called_once()
        notify.assert_called_once()
        activation.assert_called_once_with("user-1", "chat_message")

    def test_dm_send_rolls_back_message_and_attachments_when_binding_fails(self):
        thread = {"$id": "thread-1", "participant_a": "user-1", "participant_b": "user-2"}
        row = {"$id": "message-1", "thread_id": "thread-1", "user_id": "user-1"}
        with self._client() as client:
            with patch.object(chat_api, "current_user", self.user), \
                    patch.object(chat_api, "_current_user_id", return_value="user-1"), \
                    patch.object(chat_api, "_thread_for_user", return_value=thread), \
                    patch.object(chat_api, "_other_participant", return_value={"id": "user-2"}), \
                    patch.object(chat_api, "_public_user", return_value={"id": "user-2"}), \
                    patch.object(chat_api, "_is_blocked_between", return_value=False), \
                    patch.object(chat_api, "_message_media_payload", return_value=("hello", ["attachment-1"], None)), \
                    patch.object(chat_api, "_previews_for_content", return_value=[]), \
                    patch.object(chat_api, "_now", return_value=datetime(2026, 7, 1, tzinfo=timezone.utc)), \
                    patch.object(chat_api, "format_datetime", return_value="now"), \
                    patch.object(chat_api, "render_markdown", return_value="rendered"), \
                    patch.object(chat_api, "create_row_safe", return_value=row), \
                    patch.object(chat_api, "bind_pending", side_effect=chat_api.AttachmentError("bind failed")), \
                    patch.object(chat_api, "delete_message_attachments") as delete_attachments, \
                    patch.object(chat_api, "delete_row_safe") as delete_row:
                response = client.post("/api/chat/dm/threads/thread-1/messages", json={"content": "hello"})

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.get_json(), {"error": "Unable to send message."})
        delete_attachments.assert_called_once_with("message-1")
        delete_row.assert_called_once_with(chat_api.COLLECTIONS["chat_messages"], "message-1")

    def test_dm_send_rejects_blocked_conversation_before_message_creation(self):
        thread = {"$id": "thread-1", "participant_a": "user-1", "participant_b": "user-2"}
        with self._client() as client:
            with patch.object(chat_api, "current_user", self.user), \
                    patch.object(chat_api, "_current_user_id", return_value="user-1"), \
                    patch.object(chat_api, "_thread_for_user", return_value=thread), \
                    patch.object(chat_api, "_other_participant", return_value={"id": "user-2"}), \
                    patch.object(chat_api, "_public_user", return_value={"id": "user-2"}), \
                    patch.object(chat_api, "_is_blocked_between", return_value=True), \
                    patch.object(chat_api, "create_row_safe") as create_row:
                response = client.post("/api/chat/dm/threads/thread-1/messages", json={"content": "hello"})

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json(), {"error": "This conversation is blocked."})
        create_row.assert_not_called()

    def test_attachment_upload_success_and_entitlement_error_preserve_statuses(self):
        row = {"$id": "attachment-1"}
        with self._client() as client:
            with patch.object(chat_api, "current_user", self.user), \
                    patch.object(chat_api, "_current_user_id", return_value="user-1"), \
                    patch.object(chat_api, "_attachment_scope_access", return_value=True), \
                    patch.object(chat_api, "request_entitlements", return_value={"limits": {}}), \
                    patch.object(chat_api, "create_attachment", return_value=row) as create_attachment, \
                    patch.object(chat_api, "serialize_attachment", return_value={"id": "attachment-1"}):
                response = client.post(
                    "/api/chat/attachments",
                    data={"scope_type": "channel", "scope_id": "nest_chat", "file": (io.BytesIO(b"body"), "note.txt")},
                    content_type="multipart/form-data",
                )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.get_json(), {"attachment": {"id": "attachment-1"}})
        self.assertEqual(create_attachment.call_args.kwargs["scope_id"], "nest_chat")

        limit_error = chat_api.EntitlementLimitError("chat attachment size", 10, 11, 10)
        with self._client() as client:
            with patch.object(chat_api, "current_user", self.user), \
                    patch.object(chat_api, "_current_user_id", return_value="user-1"), \
                    patch.object(chat_api, "_attachment_scope_access", return_value=True), \
                    patch.object(chat_api, "request_entitlements", return_value={"limits": {}}), \
                    patch.object(chat_api, "create_attachment", side_effect=limit_error):
                response = client.post(
                    "/api/chat/attachments",
                    data={"scope_type": "channel", "scope_id": "nest_chat", "file": (io.BytesIO(b"body"), "note.txt")},
                    content_type="multipart/form-data",
                )
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.get_json(), limit_error.payload())

    def test_attachment_preview_download_headers_and_cancel_authorization(self):
        image = {
            "$id": "attachment-1",
            "status": "active",
            "kind": "image",
            "mime_type": "image/png",
            "original_filename": "image.png",
        }
        with self._client() as client:
            with patch.object(chat_api, "current_user", self.user), \
                    patch.object(chat_api, "get_attachment", return_value=image), \
                    patch.object(chat_api, "_can_access_attachment", return_value=True), \
                    patch.object(chat_api, "attachment_bytes", return_value=b"png"):
                preview = client.get("/api/chat/attachments/attachment-1/preview")
                download = client.get("/api/chat/attachments/attachment-1/download")

        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.mimetype, "image/png")
        self.assertEqual(preview.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(preview.headers["Content-Security-Policy"], "default-src 'none'; sandbox")
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download.headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("attachment", download.headers["Content-Disposition"])
        self.assertIn("image.png", download.headers["Content-Disposition"])

        pending = {"$id": "attachment-2", "status": "pending", "user_id": "user-1"}
        with self._client() as client:
            with patch.object(chat_api, "current_user", self.user), \
                    patch.object(chat_api, "_current_user_id", return_value="user-1"), \
                    patch.object(chat_api, "get_attachment", return_value=pending), \
                    patch.object(chat_api, "delete_attachment") as delete_attachment:
                cancelled = client.delete("/api/chat/attachments/attachment-2")
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(cancelled.get_json(), {"status": "ok"})
        delete_attachment.assert_called_once_with(pending)

    def test_attachment_and_channel_access_errors_keep_404_contract(self):
        with self._client() as client:
            with patch.object(chat_api, "current_user", self.user), \
                    patch.object(chat_api, "_attachment_scope_access", return_value=False):
                upload = client.post(
                    "/api/chat/attachments",
                    data={"scope_type": "channel", "scope_id": "missing", "file": (io.BytesIO(b"body"), "note.txt")},
                    content_type="multipart/form-data",
                )
        self.assertEqual(upload.status_code, 404)
        self.assertEqual(upload.get_json(), {"error": "Conversation unavailable."})

        with self._client() as client:
            with patch.object(chat_api, "current_user", self.user), \
                    patch.object(chat_api, "get_attachment", return_value=None):
                preview = client.get("/api/chat/attachments/missing/preview")
                download = client.get("/api/chat/attachments/missing/download")
        self.assertEqual(preview.status_code, 404)
        self.assertEqual(download.status_code, 404)

    def test_block_create_and_delete_preserve_event_contract(self):
        thread = {"$id": "thread-1", "participant_a": "user-1", "participant_b": "user-2"}
        with self._client() as client:
            with patch.object(chat_api, "current_user", self.user), \
                    patch.object(chat_api, "_current_user_id", return_value="user-1"), \
                    patch.object(chat_api, "first_row", return_value=None), \
                    patch.object(chat_api, "create_row_safe") as create_row, \
                    patch.object(chat_api, "_threads_for_current_user", return_value=[thread]), \
                    patch.object(chat_api, "_thread_participant_ids", return_value=["user-1", "user-2"]), \
                    patch.object(chat_api, "emit_chat_event") as emit_event, \
                    patch.object(chat_api, "_now", return_value=datetime(2026, 7, 1, tzinfo=timezone.utc)), \
                    patch.object(chat_api, "format_datetime", return_value="now"):
                created = client.post("/api/chat/blocks/user-2")
        self.assertEqual(created.status_code, 200)
        self.assertEqual(created.get_json(), {"status": "ok", "blocked": True})
        create_row.assert_called_once()
        emit_event.assert_called_once()

        block = {"$id": "block-1", "block_key": "user-1:user-2"}
        with self._client() as client:
            with patch.object(chat_api, "current_user", self.user), \
                    patch.object(chat_api, "_current_user_id", return_value="user-1"), \
                    patch.object(chat_api, "first_row", return_value=block), \
                    patch.object(chat_api, "delete_row_safe") as delete_row, \
                    patch.object(chat_api, "_threads_for_current_user", return_value=[thread]), \
                    patch.object(chat_api, "_thread_participant_ids", return_value=["user-1", "user-2"]), \
                    patch.object(chat_api, "emit_chat_event") as emit_event:
                deleted = client.delete("/api/chat/blocks/user-2")
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(deleted.get_json(), {"status": "ok", "blocked": False})
        delete_row.assert_called_once_with(chat_api.COLLECTIONS["chat_blocks"], "block-1")
        emit_event.assert_called_once()

    def test_block_self_and_database_failure_preserve_400_and_500_contracts(self):
        with self._client() as client:
            with patch.object(chat_api, "current_user", self.user), \
                    patch.object(chat_api, "_current_user_id", return_value="user-1"):
                response = client.post("/api/chat/blocks/user-1")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json(), {"error": "You cannot block yourself."})

        with self._client() as client:
            with patch.object(chat_api, "current_user", self.user), \
                    patch.object(chat_api, "_current_user_id", return_value="user-1"), \
                    patch.object(chat_api, "first_row", side_effect=chat_api.AppwriteException("down")):
                response = client.delete("/api/chat/blocks/user-2")
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.get_json(), {"error": "Unable to unblock user."})


class ChatDeliveryRoutePatchSeamTests(unittest.TestCase):
    def test_route_adapters_are_registered_and_keep_historical_symbols(self):
        app = Flask(__name__)
        app.register_blueprint(chat_api.chat_api_bp)
        routes = {
            (rule.rule, tuple(sorted(rule.methods - {"HEAD", "OPTIONS"})), rule.endpoint)
            for rule in app.url_map.iter_rules()
            if rule.endpoint.startswith("chat_api.")
        }
        self.assertEqual(len(routes), 25)
        for symbol in (
            "_message_media_payload",
            "_thread_payload",
            "_upsert_discord_message",
            "_attachment_scope_access",
            "_can_access_attachment",
            "upload_chat_attachment",
            "download_chat_attachment",
            "preview_chat_attachment",
            "cancel_chat_attachment",
            "send_channel_message",
            "dm_thread_messages",
            "delete_message",
            "blocks",
        ):
            self.assertTrue(callable(getattr(chat_api, symbol)))


class ChatAttachmentCsrfBoundaryTests(unittest.TestCase):
    """Exercise the real app CSRF boundary; stub storage after route entry."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.env = patch.dict(os.environ, {
            "DATABASE_PATH": os.path.join(self.temp_dir.name, "chat-csrf.sqlite3"),
            "FLASK_SECRET_KEY": "test-chat-csrf-key",
            "FLASK_ENV": "testing",
            "APSTUDY_ALLOW_INSECURE_HTTP": "1",
            "SCHEDULER_ENABLED": "0",
        }, clear=False)
        self.env.start()
        self.addCleanup(self.env.stop)
        schema_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "migrations",
            "001_initial_schema.sql",
        )
        with sqlite3.connect(os.environ["DATABASE_PATH"]) as connection, \
                open(schema_path, encoding="utf-8") as schema:
            connection.executescript(schema.read())
        with patch("services.scheduler.init_scheduler"), \
                patch("services.discord_audit.init_discord_audit"):
            self.app = app_module.create_app()
        self.app.config.update(TESTING=True)
        self.user = _RouteUser()

        previous_loader = login_manager._user_callback
        previous_unauthorized = login_manager.unauthorized_callback
        previous_login_view = login_manager.login_view
        self.addCleanup(setattr, login_manager, "_user_callback", previous_loader)
        self.addCleanup(setattr, login_manager, "unauthorized_callback", previous_unauthorized)
        self.addCleanup(setattr, login_manager, "login_view", previous_login_view)
        self.addCleanup(reset_flask_login_manager)
        login_manager._user_callback = lambda user_id: (
            self.user if user_id == self.user.id else None
        )

    def _authenticated_client(self):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = self.user.id
            session["_fresh"] = True
        return client

    def _csrf_token(self, client):
        response = client.get("/auth/csrf")
        self.assertEqual(response.status_code, 200)
        cookie = client.get_cookie("csrf_token")
        self.assertIsNotNone(cookie)
        return cookie.value

    def _upload(self, client, headers=None):
        return client.post(
            "/api/chat/attachments",
            data={
                "scope_type": "channel",
                "scope_id": "nest_chat",
                "file": (io.BytesIO(b"test attachment"), "notes.txt"),
            },
            content_type="multipart/form-data",
            headers=headers or {},
        )

    def test_chat_attachment_raw_xhr_requires_csrf_and_accepts_current_token(self):
        row = {"$id": "attachment-1"}
        client = self._authenticated_client()
        with patch.object(chat_api, "_attachment_scope_access", return_value=True), \
                patch.object(chat_api, "create_attachment", return_value=row) as create_attachment, \
                patch.object(chat_api, "serialize_attachment", return_value={"id": "attachment-1"}) as serialize:
            missing = self._upload(client)
            invalid = self._upload(client, headers={"X-CSRFToken": "stale-token"})
            token = self._csrf_token(client)
            accepted = self._upload(client, headers={"X-CSRFToken": token})

        self.assertEqual(missing.status_code, 400)
        self.assertEqual(missing.headers.get("X-APStudy-CSRF-Error"), "1")
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(invalid.headers.get("X-APStudy-CSRF-Error"), "1")
        self.assertEqual(accepted.status_code, 201)
        self.assertEqual(accepted.get_json(), {"attachment": {"id": "attachment-1"}})
        create_attachment.assert_called_once()
        serialize.assert_called_once_with(row)


if __name__ == "__main__":
    unittest.main()
