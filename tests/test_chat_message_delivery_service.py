import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

from appwrite.exception import AppwriteException

from services.chat_attachments import AttachmentError
from services import chat_message_delivery as delivery


class _QueryStub:
    @staticmethod
    def equal(field, values):
        return ("equal", field, values)

    @staticmethod
    def order_desc(field):
        return ("order_desc", field)


class ChatMessageDeliveryServiceTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 7, 1, tzinfo=timezone.utc)
        self.user = SimpleNamespace(
            name="Derek C",
            username="derek",
            picture_url="https://example.test/avatar.png",
        )
        self.calls = []

        def mark(name, value=None):
            def callback(*args, **kwargs):
                self.calls.append((name, args, kwargs))
                return value
            return callback

        self.deps = delivery.ChatMessageDeliveryDependencies(
            collections={
                "chat_messages": "messages",
                "chat_channels": "channels",
                "chat_dm_threads": "threads",
                "chat_blocks": "blocks",
            },
            appwrite_exception=AppwriteException,
            attachment_error=AttachmentError,
            current_user_fn=lambda: self.user,
            current_user_id_fn=lambda: "user-1",
            message_media_payload_fn=Mock(return_value=("hello", [], None)),
            previews_for_content_fn=lambda _content: [],
            now_fn=lambda: self.now,
            format_datetime_fn=lambda value: value.isoformat() if hasattr(value, "isoformat") else str(value),
            render_markdown_fn=lambda content: f"<p>{content}</p>",
            row_id_fn=lambda row: (row or {}).get("$id") or (row or {}).get("id"),
            get_row_fn=Mock(),
            create_row_fn=Mock(return_value={"$id": "message-1"}),
            insert_row_ignore_fn=Mock(return_value=True),
            update_row_fn=Mock(),
            delete_row_fn=Mock(),
            id_unique_fn=lambda: "new-id",
            get_attachment_fn=Mock(),
            attachment_bytes_fn=Mock(return_value=b"attachment"),
            bind_pending_fn=Mock(),
            delete_message_attachments_fn=Mock(),
            emit_chat_event_fn=Mock(),
            serialize_message_fn=Mock(return_value={"id": "message-1"}),
            discord_external_id_fn=lambda _channel, message_id: f"external:{message_id}",
            discord_row_id_fn=lambda _channel, message_id: f"discord:{message_id}",
            find_discord_message_row_fn=Mock(),
            prune_discord_fn=Mock(),
            execute_chat_webhook_fn=Mock(return_value=(
                {"id": "discord-1", "timestamp": "2026-07-01T00:00:01Z"},
                {"id": "webhook-1"},
            )),
            delete_webhook_message_fn=Mock(),
            notification_fn=Mock(),
            invite_activation_fn=Mock(),
            first_row_fn=Mock(),
            query_cls=_QueryStub,
            users_collection="users",
            thread_participant_ids_fn=lambda thread: [thread.get("participant_a"), thread.get("participant_b")],
            thread_for_user_fn=Mock(),
            other_participant_fn=Mock(),
            is_blocked_between_fn=Mock(return_value=False),
            threads_for_current_user_fn=Mock(return_value=[]),
            logger=Mock(),
            attachment_download_url_fn=lambda attachment_id: f"https://example.test/download/{attachment_id}",
            delete_window_seconds=5 * 60,
            message_timestamp_fn=lambda row: datetime.fromisoformat(row["created_at"]),
            audit_delete_fn=Mock(),
        )

    def test_list_room_messages_preserves_before_cursor_has_more_semantics(self):
        rows = [{"$id": str(index)} for index in range(50)]
        loaded = delivery.list_room_messages(
            "channel",
            "channel-1",
            "before-cursor",
            None,
            None,
            list_messages_fn=Mock(return_value=rows),
            page_size=50,
        )
        self.assertEqual(loaded, (rows, True))

    def test_channel_delivery_orders_discord_webhook_persistence_event_and_invite(self):
        channel = {"$id": "nest_chat", "kind": "discord"}
        row = {"$id": "discord:discord-1"}
        self.deps.create_row_fn = Mock(return_value=row)
        self.deps.get_row_fn = Mock(return_value=row)
        self.deps.insert_row_ignore_fn = Mock(return_value=True)
        self.deps.discord_row_id_fn = lambda _channel, _message_id: "discord:discord-1"
        self.deps.execute_chat_webhook_fn = Mock(side_effect=lambda *args, **kwargs: (
            self.calls.append(("webhook", args, kwargs)) or (
                {"id": "discord-1", "timestamp": "discord-time"},
                {"id": "webhook-1"},
            )
        ))
        self.deps.insert_row_ignore_fn = Mock(side_effect=lambda *args, **kwargs: (
            self.calls.append(("insert", args, kwargs)) or True
        ))
        self.deps.emit_chat_event_fn = Mock(
            side_effect=lambda *args, **kwargs: self.calls.append(("event", args, kwargs))
        )
        self.deps.prune_discord_fn = Mock(
            side_effect=lambda *args, **kwargs: self.calls.append(("prune", args, kwargs))
        )
        self.deps.invite_activation_fn = Mock(
            side_effect=lambda *args, **kwargs: self.calls.append(("invite", args, kwargs))
        )

        result = delivery.send_channel_message(channel["$id"], channel, dependencies=self.deps)

        self.assertEqual(result, (row, True))
        self.assertEqual([name for name, *_ in self.calls], ["webhook", "insert", "prune", "event", "invite"])
        payload = self.deps.insert_row_ignore_fn.call_args.kwargs["data"]
        self.assertEqual(payload["source"], "discord")
        self.assertEqual(payload["external_id"], "external:discord-1")

    def test_channel_delivery_rolls_back_created_message_on_attachment_binding_failure(self):
        row = {"$id": "message-1"}
        self.deps.create_row_fn = Mock(return_value=row)
        self.deps.bind_pending_fn = Mock(side_effect=AttachmentError("binding failed"))
        self.deps.delete_row_fn = Mock()
        self.deps.message_media_payload_fn.return_value = ("hello", ["attachment-1"], None)

        with self.assertRaises(delivery.AttachmentBindingError):
            delivery.send_channel_message(
                "channel-1",
                {"$id": "channel-1", "kind": "appwrite"},
                dependencies=self.deps,
            )

        self.deps.delete_row_fn.assert_called_once_with("messages", "message-1")

    def test_channel_delivery_keeps_mention_notification_before_invite_activation(self):
        self.deps.message_media_payload_fn.return_value = ("hello @pat", [], None)
        self.deps.create_row_fn = Mock(return_value={"$id": "message-1"})
        self.deps.first_row_fn = Mock(return_value={"$id": "user-2"})
        self.deps.emit_chat_event_fn = Mock(
            side_effect=lambda *args, **kwargs: self.calls.append(("event", args, kwargs))
        )
        self.deps.notification_fn = Mock(
            side_effect=lambda *args, **kwargs: self.calls.append(("notification", args, kwargs))
        )
        self.deps.invite_activation_fn = Mock(
            side_effect=lambda *args, **kwargs: self.calls.append(("invite", args, kwargs))
        )

        delivery.send_channel_message(
            "channel-1",
            {"$id": "channel-1", "kind": "appwrite"},
            dependencies=self.deps,
        )

        self.assertEqual([name for name, *_ in self.calls], ["event", "notification", "invite"])
        self.assertEqual(self.deps.notification_fn.call_args.args[:2], ("user-2", "chat_mention"))

    def test_dm_delivery_blocks_before_media_and_rolls_back_after_binding_failure(self):
        self.deps.is_blocked_between_fn.return_value = True
        self.deps.message_media_payload_fn.reset_mock()
        with self.assertRaises(delivery.DirectMessageBlockedError):
            delivery.send_direct_message(
                "thread-1",
                {"$id": "thread-1", "participant_a": "user-1", "participant_b": "user-2"},
                {"id": "user-2"},
                dependencies=self.deps,
            )
        self.deps.message_media_payload_fn.assert_not_called()

        self.deps.is_blocked_between_fn.return_value = False
        self.deps.create_row_fn = Mock(return_value={"$id": "message-1"})
        self.deps.bind_pending_fn = Mock(side_effect=AttachmentError("binding failed"))
        self.deps.delete_message_attachments_fn = Mock()
        self.deps.delete_row_fn = Mock()
        self.deps.message_media_payload_fn.return_value = ("hello", ["attachment-1"], None)
        with self.assertRaises(delivery.DirectMessagePersistenceError):
            delivery.send_direct_message(
                "thread-1",
                {"$id": "thread-1", "participant_a": "user-1", "participant_b": "user-2"},
                {"id": "user-2"},
                dependencies=self.deps,
            )
        self.deps.delete_message_attachments_fn.assert_called_once_with("message-1")
        self.deps.delete_row_fn.assert_called_once_with("messages", "message-1")

    def test_delete_delivery_calls_discord_before_local_update_and_event_audit(self):
        created = self.now - timedelta(minutes=1)
        row = {
            "$id": "message-1",
            "user_id": "user-1",
            "source": "discord",
            "discord_message_id": "discord-1",
            "discord_webhook_id": "webhook-1",
            "channel_id": "nest_chat",
            "created_at": created.isoformat(),
        }
        self.deps.get_row_fn = Mock(side_effect=[row, {"$id": "nest_chat"}])
        self.deps.now_fn = Mock(side_effect=[self.now, self.now])
        self.deps.delete_webhook_message_fn = Mock(side_effect=lambda *args: self.calls.append(("webhook", args, {})))
        self.deps.update_row_fn = Mock(side_effect=lambda *args, **kwargs: self.calls.append(("update", args, kwargs)))
        self.deps.emit_chat_event_fn = Mock(side_effect=lambda *args, **kwargs: self.calls.append(("event", args, kwargs)))
        self.deps.audit_delete_fn = Mock(side_effect=lambda *args: self.calls.append(("audit", args, {})))

        deleted_at = delivery.delete_chat_message("message-1", dependencies=self.deps)

        self.assertEqual(deleted_at, self.now.isoformat())
        self.assertEqual([name for name, *_ in self.calls], ["webhook", "update", "event", "audit"])

    def test_delete_delivery_preserves_owner_and_window_guards(self):
        recent = self.now - timedelta(minutes=1)
        row = {"$id": "message-1", "user_id": "other", "created_at": recent.isoformat()}
        self.deps.get_row_fn = Mock(return_value=row)
        with self.assertRaises(delivery.MessageOwnershipError):
            delivery.delete_chat_message("message-1", dependencies=self.deps)

        row["user_id"] = "user-1"
        row["created_at"] = (self.now - timedelta(minutes=6)).isoformat()
        with self.assertRaises(delivery.MessageExpiredError):
            delivery.delete_chat_message("message-1", dependencies=self.deps)

    def test_attachment_service_preserves_pending_ownership_and_pdf_preview_file(self):
        pending = {"$id": "attachment-1", "status": "pending", "user_id": "user-1"}
        self.deps.get_attachment_fn = Mock(return_value=pending)
        self.deps.delete_attachment_fn = Mock()

        delivery.cancel_pending_attachment(
            "attachment-1",
            get_attachment_fn=self.deps.get_attachment_fn,
            current_user_id="user-1",
            delete_attachment_fn=self.deps.delete_attachment_fn,
        )
        self.deps.delete_attachment_fn.assert_called_once_with(pending)

        with self.assertRaises(delivery.AttachmentOwnershipError):
            delivery.cancel_pending_attachment(
                "attachment-1",
                get_attachment_fn=self.deps.get_attachment_fn,
                current_user_id="other",
                delete_attachment_fn=self.deps.delete_attachment_fn,
            )

        pdf = {"$id": "attachment-2", "status": "active", "kind": "pdf"}
        bytes_fn = Mock(return_value=b"preview")
        row, data = delivery.read_attachment(
            "attachment-2",
            preview=True,
            get_attachment_fn=Mock(return_value=pdf),
            can_access_attachment_fn=Mock(return_value=True),
            attachment_bytes_fn=bytes_fn,
        )
        self.assertEqual((row, data), (pdf, b"preview"))
        bytes_fn.assert_called_once_with(pdf, preview=True)

    def test_attachment_download_uses_the_one_argument_bytes_callback_contract(self):
        attachment = {"$id": "attachment-3", "status": "active", "kind": "file"}
        calls = []

        def attachment_bytes(row):
            calls.append(row)
            return b"download"

        row, data = delivery.read_attachment(
            "attachment-3",
            preview=False,
            get_attachment_fn=lambda _attachment_id: attachment,
            can_access_attachment_fn=lambda candidate: candidate == attachment,
            attachment_bytes_fn=attachment_bytes,
        )

        self.assertEqual((row, data), (attachment, b"download"))
        self.assertEqual(calls, [attachment])

    def test_block_service_is_idempotent_and_emits_for_matching_threads(self):
        thread = {"$id": "thread-1", "participant_a": "user-1", "participant_b": "user-2"}
        self.deps.threads_for_current_user_fn = Mock(return_value=[thread])
        self.deps.thread_participant_ids_fn = Mock(return_value=["user-1", "user-2"])
        self.deps.first_row_fn = Mock(return_value=None)
        self.deps.create_row_fn = Mock()
        self.deps.emit_chat_event_fn = Mock()

        self.assertTrue(delivery.update_block("user-2", method="POST", dependencies=self.deps))
        self.deps.create_row_fn.assert_called_once()
        self.deps.emit_chat_event_fn.assert_called_once()

        self.deps.first_row_fn.return_value = {"$id": "block-1"}
        self.deps.delete_row_fn = Mock()
        self.deps.emit_chat_event_fn.reset_mock()
        self.assertFalse(delivery.update_block("user-2", method="DELETE", dependencies=self.deps))
        self.deps.delete_row_fn.assert_called_once_with("blocks", "block-1")
        self.deps.emit_chat_event_fn.assert_called_once()


if __name__ == "__main__":
    unittest.main()
