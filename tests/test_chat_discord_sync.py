import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

from flask import Flask

import blueprints.chat_api as chat_api
from services import chat_discord_sync, discord_chat


class QueryStub:
    @staticmethod
    def equal(field, values):
        return ("equal", field, tuple(values))

    @staticmethod
    def order_desc(field):
        return ("order_desc", field)


def _dependencies(**overrides):
    values = {
        "collections": {
            "chat_channels": "chat_channels",
            "chat_messages": "chat_messages",
        },
        "appwrite_exception": RuntimeError,
        "query_cls": QueryStub,
        "id_unique_fn": lambda: "generated-row",
        "row_id_fn": lambda row: row.get("$id") or row.get("id"),
        "now_fn": lambda: datetime(2026, 5, 26, 22, 10, tzinfo=timezone.utc),
        "format_datetime_fn": lambda value: value.isoformat().replace("+00:00", "Z"),
        "parse_datetime_fn": lambda value: datetime.fromisoformat(value.replace("Z", "+00:00")),
        "message_timestamp_fn": lambda row: datetime.fromisoformat(
            row["created_at"].replace("Z", "+00:00")
        ),
        "runtime_environment_config_fn": lambda: SimpleNamespace(
            discord_announcements_channel_id="",
            discord_chat_channel_id="",
        ),
        "default_channels_fn": Mock(),
        "get_row_fn": Mock(return_value=None),
        "first_row_fn": Mock(return_value=None),
        "create_row_fn": Mock(return_value={"$id": "created-channel"}),
        "insert_row_ignore_fn": Mock(return_value=False),
        "update_row_fn": Mock(return_value={"$id": "updated-row"}),
        "delete_row_fn": Mock(),
        "list_rows_all_fn": Mock(return_value=[]),
        "emit_chat_event_fn": Mock(),
        "delete_message_attachments_fn": Mock(),
        "fetch_channel_messages_fn": Mock(return_value=[]),
        "ensure_discord_channel_fn": Mock(),
        "discord_message_payload_fn": Mock(return_value=None),
        "discord_message_row_id_fn": Mock(return_value="discord-row"),
        "discord_message_external_id_fn": Mock(return_value="discord:channel:message"),
        "discord_message_changes_fn": Mock(return_value={}),
        "find_discord_message_row_fn": Mock(return_value=None),
        "apply_discord_message_changes_fn": Mock(return_value=(None, False)),
        "upsert_discord_message_fn": Mock(return_value=(None, False)),
        "log_discord_upsert_failure_fn": Mock(),
        "soft_delete_discord_message_fn": Mock(return_value=None),
        "reconcile_discord_deletes_fn": Mock(return_value=0),
        "sync_discord_channel_fn": Mock(return_value=(0, 0)),
        "delete_discord_gateway_message_fn": Mock(return_value=None),
        "can_sync_discord_channel_fn": lambda channel: bool(channel),
        "discord_channel_for_discord_id_fn": Mock(return_value=None),
        "prune_discord_messages_fn": Mock(),
        "logger": Mock(),
        "discord_message_limit": 50,
        "partial_create_required_fields": ("content", "timestamp"),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class DiscordSyncServiceTests(unittest.TestCase):
    def test_upsert_injects_payload_and_persistence_callbacks_for_create(self):
        channel = {"$id": "nest_chat", "discord_channel_id": "discord-channel"}
        message = {"id": "discord-message", "content": "hello"}
        payload = {
            "channel_id": "nest_chat",
            "source": "discord",
            "external_id": "discord:discord-channel:discord-message",
            "discord_message_id": "discord-message",
            "content": "hello",
        }
        inserted_row = {"$id": "discord-row"}
        payload_fn = Mock(return_value=payload)
        find_fn = Mock(return_value=None)
        insert_fn = Mock(return_value=True)
        get_fn = Mock(return_value=inserted_row)
        emit_fn = Mock()
        dependencies = _dependencies(
            discord_message_payload_fn=payload_fn,
            find_discord_message_row_fn=find_fn,
            insert_row_ignore_fn=insert_fn,
            get_row_fn=get_fn,
            emit_chat_event_fn=emit_fn,
        )

        row, created = chat_discord_sync.upsert_discord_message(
            channel,
            message,
            emit_event=True,
            dependencies=dependencies,
        )

        self.assertTrue(created)
        self.assertEqual(row, inserted_row)
        payload_fn.assert_called_once_with(channel, message, partial=False)
        insert_fn.assert_called_once_with(
            "chat_messages",
            row_id="discord-row",
            data=payload,
        )
        emit_fn.assert_called_once_with(
            "channel",
            "nest_chat",
            "message_created",
            message_id="discord-row",
            channel_id="nest_chat",
            channel=channel,
        )

    def test_partial_update_does_not_wipe_missing_message_fields(self):
        existing = {
            "$id": "message-row",
            "content": "keep me",
            "rendered_html": "keep me",
            "updated_at": "2026-05-26T22:00:00Z",
        }
        payload = {
            "channel_id": "nest_chat",
            "updated_at": "2026-05-26T22:02:00Z",
        }
        update_fn = Mock(return_value=existing)
        emit_fn = Mock()
        dependencies = _dependencies(
            discord_message_changes_fn=lambda _existing, _payload: {
                "updated_at": payload["updated_at"]
            },
            update_row_fn=update_fn,
            emit_chat_event_fn=emit_fn,
        )

        row, created = chat_discord_sync.apply_discord_message_changes(
            existing,
            payload,
            {"edited_timestamp": "2026-05-26T22:02:00Z"},
            partial=True,
            emit_event=True,
            channel={"$id": "nest_chat"},
            dependencies=dependencies,
        )

        self.assertEqual(row, existing)
        self.assertFalse(created)
        self.assertEqual(update_fn.call_args.args[2], {"updated_at": payload["updated_at"]})
        self.assertEqual(emit_fn.call_args.args[:3], ("channel", "nest_chat", "message_updated"))

    def test_sync_fetches_fifty_messages_then_reconciles_and_prunes(self):
        channel = {"$id": "nest_chat", "discord_channel_id": "discord-channel"}
        messages = [{"id": "message-1"}, {"id": "message-2"}]
        steps = []

        def fetch(channel_id, limit):
            steps.append(("fetch", channel_id, limit))
            return messages

        def upsert(channel_value, message, *, emit_event):
            steps.append(("upsert", message["id"], emit_event))
            return None, message["id"] == "message-1"

        def reconcile(channel_value, messages_value, *, emit_events):
            steps.append(("reconcile", emit_events))
            return 1

        def prune(channel_id):
            steps.append(("prune", channel_id))

        dependencies = _dependencies(
            fetch_channel_messages_fn=fetch,
            upsert_discord_message_fn=upsert,
            reconcile_discord_deletes_fn=reconcile,
            prune_discord_messages_fn=prune,
        )

        result = chat_discord_sync.sync_discord_channel(
            channel,
            emit_events=False,
            emit_delete_events=True,
            dependencies=dependencies,
        )

        self.assertEqual(result, (1, 1))
        self.assertEqual(
            steps,
            [
                ("fetch", "discord-channel", 50),
                ("upsert", "message-1", False),
                ("upsert", "message-2", False),
                ("reconcile", True),
                ("prune", "nest_chat"),
            ],
        )

    def test_sync_channels_initializes_defaults_and_skips_unmapped_rows(self):
        valid_channel = {"$id": "nest_chat", "kind": "discord", "discord_channel_id": "channel"}
        steps = []
        sync_channel = Mock(return_value=(1, 2))
        dependencies = _dependencies(
            default_channels_fn=lambda: steps.append("defaults"),
            list_rows_all_fn=Mock(return_value=[valid_channel, {"$id": "unmapped"}]),
            can_sync_discord_channel_fn=lambda channel: bool(channel.get("discord_channel_id")),
            sync_discord_channel_fn=sync_channel,
        )

        result = chat_discord_sync.sync_discord_channels(
            emit_events=False,
            emit_delete_events=True,
            dependencies=dependencies,
        )

        self.assertEqual(result, (1, 2))
        self.assertEqual(steps, ["defaults"])
        sync_channel.assert_called_once_with(
            valid_channel,
            emit_events=False,
            emit_delete_events=True,
        )

    def test_gateway_update_is_partial_and_prunes_after_ingest(self):
        channel = {"$id": "nest_chat", "kind": "discord", "discord_channel_id": "channel"}
        row = {"$id": "message-row"}
        resolve_channel = Mock(return_value=channel)
        upsert = Mock(return_value=(row, False))
        prune = Mock()
        dependencies = _dependencies(
            discord_channel_for_discord_id_fn=resolve_channel,
            can_sync_discord_channel_fn=lambda value: bool(value),
            upsert_discord_message_fn=upsert,
            prune_discord_messages_fn=prune,
        )

        result = chat_discord_sync.ingest_discord_gateway_message(
            {"id": "message", "channel_id": "channel"},
            event_type="update",
            dependencies=dependencies,
        )

        self.assertEqual(result, (row, False))
        resolve_channel.assert_called_once_with("channel")
        upsert.assert_called_once_with(
            channel,
            {"id": "message", "channel_id": "channel"},
            emit_event=True,
            partial=True,
        )
        prune.assert_called_once_with("nest_chat")

    def test_reconcile_only_soft_deletes_recent_missing_discord_rows(self):
        channel = {"$id": "nest_chat", "discord_channel_id": "discord-channel"}
        messages = [
            {"id": "present", "timestamp": "2026-05-26T22:05:00Z"},
            {"id": "window-start", "timestamp": "2026-05-26T22:00:00Z"},
        ]
        rows = [
            {
                "$id": "recent-missing",
                "source": "discord",
                "discord_message_id": "missing",
                "created_at": "2026-05-26T22:02:00Z",
            },
            {
                "$id": "old-missing",
                "source": "discord",
                "discord_message_id": "old",
                "created_at": "2026-05-26T21:00:00Z",
            },
            {
                "$id": "appwrite-row",
                "source": "appwrite",
                "discord_message_id": "not-discord",
                "created_at": "2026-05-26T22:03:00Z",
            },
        ]
        soft_delete = Mock(return_value=rows[0])
        dependencies = _dependencies(
            list_rows_all_fn=Mock(return_value=rows),
            soft_delete_discord_message_fn=soft_delete,
        )

        deleted = chat_discord_sync.reconcile_discord_deletes(
            channel,
            messages,
            emit_events=True,
            dependencies=dependencies,
        )

        self.assertEqual(deleted, 1)
        soft_delete.assert_called_once_with(channel, "missing", emit_event=True)

    def test_prune_keeps_the_fifty_newest_rows(self):
        rows = [{"$id": f"row-{index}"} for index in range(52)]
        delete_row = Mock()
        dependencies = _dependencies(
            list_rows_all_fn=Mock(return_value=rows),
            delete_row_fn=delete_row,
        )

        chat_discord_sync.prune_discord_messages("nest_chat", dependencies=dependencies)

        self.assertEqual(
            delete_row.call_args_list,
            [
                unittest.mock.call("chat_messages", "row-50"),
                unittest.mock.call("chat_messages", "row-51"),
            ],
        )

    def test_background_registry_contract_calls_registered_handlers(self):
        previous = discord_chat._handlers.copy()
        try:
            sync = Mock(return_value=(2, 1))
            ingest = Mock(return_value=({"$id": "row"}, True))
            delete = Mock(return_value={"$id": "row"})
            bulk_delete = Mock(return_value=2)
            discord_chat.register_discord_chat_handlers(
                sync_discord_channels=sync,
                ingest_discord_gateway_message=ingest,
                delete_discord_gateway_message=delete,
                delete_discord_gateway_messages=bulk_delete,
            )

            self.assertEqual(
                discord_chat.sync_discord_channels(False, True),
                (2, 1),
            )
            self.assertEqual(
                discord_chat.ingest_discord_gateway_message(
                    {"id": "message"},
                    event_type="update",
                ),
                ({"$id": "row"}, True),
            )
            self.assertEqual(
                discord_chat.delete_discord_gateway_message("channel", "message"),
                {"$id": "row"},
            )
            self.assertEqual(
                discord_chat.delete_discord_gateway_messages("channel", ["a", "b"]),
                2,
            )
            sync.assert_called_once_with(False, True)
            ingest.assert_called_once_with({"id": "message"}, event_type="update")
            delete.assert_called_once_with("channel", "message")
            bulk_delete.assert_called_once_with("channel", ["a", "b"])
        finally:
            discord_chat._handlers.clear()
            discord_chat._handlers.update(previous)


class DiscordSyncAdapterTests(unittest.TestCase):
    def test_blueprint_sync_uses_patchable_default_channels_adapter(self):
        with patch.object(chat_api, "_default_channels", return_value=[]) as defaults, \
                patch.object(chat_api, "list_rows_all", return_value=[]):
            result = chat_api.sync_discord_channels(
                emit_events=False,
                emit_delete_events=True,
            )

        self.assertEqual(result, (0, 0))
        defaults.assert_called_once_with()

    def test_discord_registry_sync_uses_blueprint_default_channels_adapter(self):
        with patch.object(chat_api, "_default_channels", return_value=[]) as defaults, \
                patch.object(chat_api, "list_rows_all", return_value=[]):
            result = discord_chat.sync_discord_channels(False, True)

        self.assertEqual(result, (0, 0))
        defaults.assert_called_once_with()

    def test_gateway_ingest_resolves_unmapped_channel_after_default_fallback(self):
        channel = {
            "$id": "nest_chat",
            "kind": "discord",
            "discord_channel_id": "discord-channel",
        }
        message = {
            "id": "discord-message",
            "channel_id": "discord-channel",
            "content": "hello",
        }
        row = {"$id": "message-row"}

        with patch.object(chat_api, "_default_channels", return_value=[channel]) as defaults, \
                patch.object(chat_api, "first_row", side_effect=[None, channel]), \
                patch.object(chat_api, "_upsert_discord_message", return_value=(row, True)) as upsert, \
                patch.object(chat_api, "_prune_discord_messages") as prune:
            result = chat_api.ingest_discord_gateway_message(message)

        self.assertEqual(result, (row, True))
        defaults.assert_called_once_with()
        upsert.assert_called_once_with(
            channel,
            message,
            emit_event=True,
            partial=False,
        )
        prune.assert_called_once_with("nest_chat")

    def test_http_ingest_resolves_unmapped_channel_after_default_fallback(self):
        app = Flask(__name__)
        channel = {
            "$id": "nest_chat",
            "kind": "discord",
            "discord_channel_id": "discord-channel",
        }
        message = {
            "id": "discord-message",
            "channel_id": "discord-channel",
            "content": "hello",
        }
        row = {"$id": "message-row"}

        with app.test_request_context(
            "/api/chat/discord/messages",
            method="POST",
            json={"message": message},
            headers={"Authorization": "Bearer ingest-secret"},
        ), patch.object(
            chat_api,
            "_discord_ingest_secret",
            return_value="ingest-secret",
        ), patch.object(
            chat_api,
            "_default_channels",
            return_value=[channel],
        ) as defaults, patch.object(
            chat_api,
            "first_row",
            side_effect=[None, channel],
        ), patch.object(
            chat_api,
            "_upsert_discord_message",
            return_value=(row, True),
        ) as upsert:
            response = chat_api.discord_message_ingest()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {
            "ok": True,
            "created": True,
            "message_id": "message-row",
            "channel_id": "nest_chat",
        })
        defaults.assert_called_once_with()
        upsert.assert_called_once_with(channel, message, emit_event=True)

    def test_blueprint_adapter_keeps_payload_patch_interception(self):
        channel = {"$id": "nest_chat", "discord_channel_id": "discord-channel"}
        message = {"id": "discord-message", "content": "hello"}
        with patch.object(
            chat_api,
            "_discord_message_payload",
            side_effect=RuntimeError("payload patch reached"),
        ):
            with self.assertRaisesRegex(RuntimeError, "payload patch reached"):
                chat_api._upsert_discord_message(channel, message)

    def test_http_secret_validation_keeps_precedence_and_compare_digest(self):
        app = Flask(__name__)
        configured = SimpleNamespace(
            discord_chat_ingest_secret=" ingest-secret ",
            discord_chat_sync_secret="sync-secret",
            discord_bridge_secret="bridge-secret",
        )
        with app.test_request_context(
            "/api/chat/discord/messages",
            headers={"Authorization": "Bearer ingest-secret"},
        ), patch.object(
            chat_api,
            "runtime_environment_config",
            return_value=configured,
        ), patch.object(
            chat_api.secrets,
            "compare_digest",
            wraps=chat_api.secrets.compare_digest,
        ) as compare_digest:
            self.assertTrue(chat_api._valid_discord_ingest_request())

        compare_digest.assert_called_once_with("ingest-secret", "ingest-secret")


if __name__ == "__main__":
    unittest.main()
