import inspect
import unittest
from unittest.mock import Mock, patch

from flask import Flask

import blueprints.chat_api as chat_api
from services import chat_presence_views


class _QueryStub:
    @staticmethod
    def equal(field, values):
        return ("equal", field, values)


class TestChatPresenceViews(unittest.TestCase):
    def test_online_projection_keeps_focus_and_scope_metadata(self):
        rows = [
            {
                "user_id": "user-1",
                "scope_type": "site",
                "scope_id": "global",
                "last_seen_at": "2026-06-23T12:00:00Z",
            },
            {
                "user_id": "user-1",
                "scope_type": "chat",
                "scope_id": "global",
                "last_seen_at": "2026-06-23T12:00:01Z",
            },
            {
                "user_id": "user-1",
                "scope_type": "chat",
                "scope_id": "channel-2",
                "last_seen_at": "2026-06-23T12:00:02Z",
            },
            {
                "user_id": "user-1",
                "scope_type": "typing_channel",
                "scope_id": "channel-2",
                "last_seen_at": "2026-06-23T12:00:03Z",
            },
            {
                "user_id": "user-1",
                "scope_type": "typing_thread",
                "scope_id": "thread-2",
                "last_seen_at": "2026-06-23T12:00:04Z",
            },
        ]
        users = {
            "user-1": {"$id": "user-1", "name": "Active User"},
            "focus-user": {"$id": "focus-user", "name": "Focus User"},
        }

        payload = chat_presence_views.presence_online_users(
            fresh_presence_rows_by_scope_fn=Mock(return_value=rows),
            presence_online_limit=10,
            get_row_fn=lambda _collection, user_id, allow_missing=True: users[user_id],
            users_collection="users",
            appwrite_exception=RuntimeError,
            error_logger=Mock(),
            public_user_fn=lambda row: {"id": row["$id"], "name": row["name"]},
            presence_status_from_scopes_fn=lambda scopes: "active" if "chat" in scopes else "busy",
            focus_user_ids_fn=lambda: {"user-1", "focus-user"},
        )

        by_id = {user["id"]: user for user in payload}
        self.assertEqual(by_id["user-1"]["presence_status"], "focus")
        self.assertTrue(by_id["user-1"]["online"])
        self.assertEqual(by_id["user-1"]["last_seen_at"], "2026-06-23T12:00:04Z")
        self.assertEqual(by_id["user-1"]["active_chat_scopes"], ["channel-2"])
        self.assertEqual(by_id["user-1"]["typing_channel_ids"], ["channel-2"])
        self.assertEqual(by_id["user-1"]["typing_thread_ids"], ["thread-2"])
        self.assertEqual(by_id["focus-user"]["presence_status"], "focus")
        self.assertIsNone(by_id["focus-user"]["last_seen_at"])

    def test_room_projections_filter_scope_dedupe_and_current_typer(self):
        users = {
            "user-1": {"$id": "user-1", "name": "Current User"},
            "user-2": {"$id": "user-2", "name": "Typing User"},
            "user-3": {"$id": "user-3", "name": "Offline User"},
        }
        resolve_user = lambda _collection, user_id, allow_missing=True: users[user_id]
        public_user = lambda row: {"id": row["$id"], "name": row["name"]}
        presence_rows = [
            {"user_id": "user-2", "scope_id": "room-1"},
            {"user_id": "user-2", "scope_id": "room-1"},
            {"user_id": "user-3", "scope_id": "other-room"},
        ]
        fresh_rows = Mock(return_value=presence_rows)
        statuses = Mock(return_value={"user-2": "busy"})

        room_users = chat_presence_views.fresh_chat_room_presence(
            "chat",
            "room-1",
            fresh_presence_rows_fn=fresh_rows,
            presence_fresh_seconds_fn=lambda scope: {"chat": 30, "typing_channel": 10}[scope],
            get_row_fn=resolve_user,
            users_collection="users",
            appwrite_exception=RuntimeError,
            error_logger=Mock(),
            public_user_fn=public_user,
            presence_statuses_for_users_fn=statuses,
        )

        self.assertEqual(room_users, [{
            "id": "user-2",
            "name": "Typing User",
            "presence_status": "busy",
            "online": True,
        }])
        statuses.assert_called_once_with(["user-2"])
        fresh_rows.assert_called_once_with(["chat"], seconds=30, limit=1000)

        typing_rows = [
            {"user_id": "user-1", "scope_id": "room-1"},
            {"user_id": "user-2", "scope_id": "room-1"},
            {"user_id": "user-2", "scope_id": "room-1"},
            {"user_id": "user-3", "scope_id": "room-1"},
        ]
        typing_statuses = Mock(return_value={"user-2": "active"})
        typing_users = chat_presence_views.fresh_typing_room_presence(
            "typing_channel",
            "room-1",
            fresh_presence_rows_fn=Mock(return_value=typing_rows),
            presence_fresh_seconds_fn=lambda scope: {"chat": 30, "typing_channel": 10}[scope],
            current_user_id_fn=lambda: "user-1",
            get_row_fn=resolve_user,
            users_collection="users",
            appwrite_exception=RuntimeError,
            error_logger=Mock(),
            public_user_fn=public_user,
            presence_statuses_for_users_fn=typing_statuses,
        )

        self.assertEqual(
            typing_users,
            [
                {
                    "id": "user-3",
                    "name": "Offline User",
                    "typing_channel_ids": ["room-1"],
                    "presence_status": "offline",
                    "online": False,
                },
                {
                    "id": "user-2",
                    "name": "Typing User",
                    "typing_channel_ids": ["room-1"],
                    "presence_status": "active",
                    "online": True,
                },
            ],
        )
        typing_statuses.assert_called_once_with(["user-2", "user-3"])

    def test_access_projection_preserves_university_and_read_only_rules(self):
        school_key = lambda user: user.get("school_key") or ""
        university = {"kind": "university", "approved": True, "school_key": "emory"}
        self.assertTrue(
            chat_presence_views.user_can_access_channel_presence(
                university,
                {"school_key": "emory"},
                school_key_for_user_row_fn=school_key,
            )
        )
        self.assertFalse(
            chat_presence_views.user_can_access_channel_presence(
                university,
                {"school_key": "other"},
                school_key_for_user_row_fn=school_key,
            )
        )

        channels = {
            "read-only": {"allowed": True, "read_only": True},
            "writable": {"allowed": True, "read_only": False},
        }
        blocked = {"thread-blocked": {"other": "blocked-user"}}

        def get_row(_collection, scope_id, allow_missing=True):
            return channels.get(scope_id)

        def thread_for_user(scope_id):
            thread = blocked.get(scope_id)
            return {"id": scope_id} if thread else None

        def other_participant(thread):
            return {"$id": blocked[thread["id"]]["other"]}

        self.assertFalse(
            chat_presence_views.presence_scope_allowed(
                "typing_channel",
                "read-only",
                get_row_fn=get_row,
                channels_collection="channels",
                can_access_channel_fn=lambda channel: bool(channel and channel.get("allowed")),
                thread_for_user_fn=thread_for_user,
                other_participant_fn=other_participant,
                is_blocked_between_fn=lambda _user, other: other == "blocked-user",
                current_user_id_fn=lambda: "user-1",
                row_id_fn=lambda row: row["$id"],
            )
        )
        self.assertTrue(
            chat_presence_views.presence_scope_allowed(
                "typing_channel",
                "writable",
                get_row_fn=get_row,
                channels_collection="channels",
                can_access_channel_fn=lambda channel: bool(channel and channel.get("allowed")),
                thread_for_user_fn=thread_for_user,
                other_participant_fn=other_participant,
                is_blocked_between_fn=lambda _user, other: other == "blocked-user",
                current_user_id_fn=lambda: "user-1",
                row_id_fn=lambda row: row["$id"],
            )
        )
        self.assertFalse(
            chat_presence_views.presence_scope_allowed(
                "typing_thread",
                "thread-blocked",
                get_row_fn=get_row,
                channels_collection="channels",
                can_access_channel_fn=lambda channel: bool(channel and channel.get("allowed")),
                thread_for_user_fn=thread_for_user,
                other_participant_fn=other_participant,
                is_blocked_between_fn=lambda _user, other: other == "blocked-user",
                current_user_id_fn=lambda: "user-1",
                row_id_fn=lambda row: row["$id"],
            )
        )

    def test_upsert_normalizes_tab_ids_and_updates_existing_row(self):
        existing = {"$id": "presence-1"}
        first_row = Mock(return_value=existing)
        update_row = Mock(return_value={"$id": "presence-1", "updated": True})
        create_row = Mock()
        allowed = Mock(return_value=True)

        result = chat_presence_views.upsert_presence(
            "chat",
            "room-1",
            " tab/evil id ",
            current_user_id_fn=lambda: "user-1",
            presence_scope_allowed_fn=allowed,
            now_fn=lambda: "now",
            format_datetime_fn=lambda value: f"formatted:{value}",
            presence_collection="chat_presence",
            query_cls=_QueryStub,
            first_row_fn=first_row,
            update_row_fn=update_row,
            create_row_fn=create_row,
            id_unique_fn=lambda: "new-id",
            row_id_fn=lambda row: row["$id"],
        )

        self.assertEqual(result, {"$id": "presence-1", "updated": True})
        allowed.assert_called_once_with("chat", "room-1")
        update_row.assert_called_once_with(
            "chat_presence",
            "presence-1",
            {
                "user_id": "user-1",
                "scope_type": "chat",
                "scope_id": "room-1",
                "presence_key": "user-1:chat:room-1:tabevilid",
                "last_seen_at": "formatted:now",
            },
        )
        create_row.assert_not_called()

    def test_blueprint_online_adapter_uses_patchable_presence_row_callback(self):
        rows = [{
            "user_id": "user-2",
            "scope_type": "site",
            "scope_id": "global",
            "last_seen_at": "2026-06-23T12:00:00Z",
        }]
        with patch.object(chat_api, "_fresh_presence_rows_by_scope", return_value=rows) as fresh_rows, \
                patch.object(chat_api, "_presence_focus_user_ids", return_value=set()), \
                patch.object(chat_api, "get_row_safe", return_value={"$id": "user-2"}), \
                patch.object(chat_api, "_public_user", return_value={"id": "user-2", "name": "Patched User"}), \
                patch.object(chat_api, "_presence_status_from_scopes", return_value="busy"):
            payload = chat_api._presence_online_users()

        self.assertEqual(payload[0]["id"], "user-2")
        self.assertEqual(payload[0]["presence_status"], "busy")
        fresh_rows.assert_called_once_with(
            ["site", "chat", "typing_channel", "typing_thread"],
            limit=chat_api.PRESENCE_ONLINE_LIMIT * 8,
        )

    def test_chat_api_keeps_exact_route_map_and_presence_symbols(self):
        app = Flask(__name__)
        app.register_blueprint(chat_api.chat_api_bp)
        actual = sorted(
            (
                rule.rule,
                tuple(sorted(rule.methods - {"HEAD", "OPTIONS"})),
                rule.endpoint,
            )
            for rule in app.url_map.iter_rules()
            if rule.endpoint.startswith("chat_api.")
        )
        expected = [
            ("/api/chat/attachments", ("POST",), "chat_api.upload_chat_attachment"),
            ("/api/chat/attachments/<attachment_id>", ("DELETE",), "chat_api.cancel_chat_attachment"),
            ("/api/chat/attachments/<attachment_id>/download", ("GET",), "chat_api.download_chat_attachment"),
            ("/api/chat/attachments/<attachment_id>/preview", ("GET",), "chat_api.preview_chat_attachment"),
            ("/api/chat/blocks/<user_id>", ("DELETE", "POST"), "chat_api.blocks"),
            ("/api/chat/bootstrap", ("GET",), "chat_api.bootstrap"),
            ("/api/chat/channels/<channel_id>/messages", ("GET",), "chat_api.channel_messages"),
            ("/api/chat/channels/<channel_id>/messages", ("POST",), "chat_api.send_channel_message"),
            ("/api/chat/discord/messages", ("POST",), "chat_api.discord_message_ingest"),
            ("/api/chat/dm/search", ("GET",), "chat_api.dm_search"),
            ("/api/chat/dm/threads", ("GET", "POST"), "chat_api.dm_threads"),
            ("/api/chat/dm/threads/<thread_id>", ("GET",), "chat_api.dm_thread"),
            ("/api/chat/dm/threads/<thread_id>/messages", ("GET", "POST"), "chat_api.dm_thread_messages"),
            ("/api/chat/events/stream", ("GET",), "chat_api.chat_events_stream"),
            ("/api/chat/messages/<message_id>", ("DELETE", "GET"), "chat_api.delete_message"),
            ("/api/chat/presence", ("POST",), "chat_api.presence"),
            ("/api/chat/presence/users", ("POST",), "chat_api.presence_users"),
            ("/api/chat/read", ("POST",), "chat_api.mark_chat_read"),
            ("/api/chat/summary", ("GET",), "chat_api.chat_summary"),
            ("/api/chat/unread", ("POST",), "chat_api.mark_chat_unread"),
            ("/api/presence/heartbeat", ("POST",), "chat_api.presence_heartbeat"),
            ("/api/presence/online", ("GET",), "chat_api.presence_online"),
            ("/api/presence/room", ("POST",), "chat_api.presence_room"),
            ("/api/presence/statuses", ("POST",), "chat_api.presence_statuses"),
            ("/api/universities", ("GET",), "chat_api.universities"),
        ]

        self.assertEqual(actual, expected)
        self.assertEqual(
            sum(line.startswith("@chat_api_bp.route") for line in inspect.getsource(chat_api).splitlines()),
            25,
        )
        for symbol in (
            "_presence_online_users",
            "_fresh_chat_room_presence",
            "_fresh_typing_room_presence",
            "_school_key_for_user_row",
            "_user_can_access_channel_presence",
            "_online_users_for_channel",
            "_presence_scope_allowed",
            "_upsert_presence",
        ):
            self.assertTrue(callable(getattr(chat_api, symbol)))


if __name__ == "__main__":
    unittest.main()
