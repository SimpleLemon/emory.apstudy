import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from flask import Flask
from flask_login import UserMixin

import blueprints.chat_api as chat_api
from extensions import login_manager
from services import chat_summary_runtime
from services.entitlements import EntitlementLimitError
from tests.support.harness import reset_flask_login_manager


class _QueryStub:
    @staticmethod
    def equal(field, values):
        return ("equal", field, values)


class _RouteUser(UserMixin):
    def __init__(self):
        self.id = "user-1"
        self.name = "Derek C"
        self.username = "derek"
        self.picture_url = "https://example.test/avatar.png"
        self.banner_color = "#123456"
        self.school = "Emory University"
        self.school_key = "emory-university"
        self.major = "CS"
        self.graduation_year = "2026"
        self.class_year = "2026"
        self.education_level = "Undergraduate"
        self.created_at = "2026-05-20T20:00:00Z"
        self.tier = "free"


class ChatSummaryRuntimeServiceTests(unittest.TestCase):
    def setUp(self):
        self.user = SimpleNamespace(
            id="user-1",
            school="Emory University",
            school_key="emory-university",
        )

    def test_university_request_pending_state_preserves_creation_and_placeholder(self):
        created = {"$id": "request-1", "status": "pending"}
        create_row = Mock(return_value=created)
        placeholder = Mock(return_value={"$id": "uni-emory", "university_status": "pending"})

        result = chat_summary_runtime.ensure_university_request(
            self.user,
            school_payload_fn=lambda value: {"school": value, "school_key": "emory-university"},
            current_user_id_fn=lambda: "user-1",
            find_university_channel_fn=lambda _school_key: None,
            first_row_fn=lambda _collection, _queries: None,
            query_cls=_QueryStub,
            collections={"admin_requests": "admin_requests"},
            create_university_channel_fn=Mock(),
            placeholder_channel_fn=placeholder,
            create_row_fn=create_row,
            id_unique_fn=lambda: "request-id",
            now_fn=lambda: "now",
            format_datetime_fn=lambda value: f"formatted:{value}",
            appwrite_exception=RuntimeError,
            error_logger=Mock(),
        )

        self.assertEqual(result["status"], "pending")
        self.assertEqual(result["request"], created)
        placeholder.assert_called_once_with("emory-university", "Emory University", "pending")
        self.assertEqual(create_row.call_args.kwargs["row_id"], "request-id")
        self.assertEqual(create_row.call_args.kwargs["data"]["requested_by"], "user-1")

    def test_university_request_approved_request_creates_channel_without_new_request(self):
        request_row = {"$id": "request-1", "status": "approved"}
        channel = {"$id": "uni-emory"}
        create_channel = Mock(return_value=channel)

        result = chat_summary_runtime.ensure_university_request(
            self.user,
            school_payload_fn=lambda _value: {"school": "Emory University", "school_key": "emory-university"},
            current_user_id_fn=Mock(),
            find_university_channel_fn=lambda _school_key: None,
            first_row_fn=lambda _collection, _queries: request_row,
            query_cls=_QueryStub,
            collections={"admin_requests": "admin_requests"},
            create_university_channel_fn=create_channel,
            placeholder_channel_fn=Mock(),
            create_row_fn=Mock(),
            id_unique_fn=Mock(),
            now_fn=Mock(),
            format_datetime_fn=Mock(),
            appwrite_exception=RuntimeError,
            error_logger=Mock(),
        )

        self.assertEqual(result, {"status": "approved", "channel": channel, "request": request_row})
        create_channel.assert_called_once_with("emory-university", "Emory University")

    def test_university_request_maps_persistence_error_to_error_state(self):
        logger = Mock()

        result = chat_summary_runtime.ensure_university_request(
            self.user,
            school_payload_fn=lambda _value: {"school": "Emory University", "school_key": "emory-university"},
            current_user_id_fn=Mock(),
            find_university_channel_fn=Mock(side_effect=RuntimeError("database unavailable")),
            first_row_fn=Mock(),
            query_cls=_QueryStub,
            collections={"admin_requests": "admin_requests"},
            create_university_channel_fn=Mock(),
            placeholder_channel_fn=Mock(),
            create_row_fn=Mock(),
            id_unique_fn=Mock(),
            now_fn=Mock(),
            format_datetime_fn=Mock(),
            appwrite_exception=RuntimeError,
            error_logger=logger,
        )

        self.assertEqual(result, {"status": "error", "channel": None, "request": None})
        logger.exception.assert_called_once_with("Failed to ensure university request")

    def test_channel_payload_preserves_presence_and_active_user_aliases(self):
        online_users = [{"id": "user-2", "online": True}]
        payload = chat_summary_runtime.channel_payload(
            {
                "$id": "nest_chat",
                "kind": "discord",
                "name": "chat",
                "label": "Chat",
                "approved": True,
            },
            row_id_fn=lambda row: row["$id"],
            online_users_for_channel_fn=lambda _channel: online_users,
            presence_scope_fn=lambda scope_type, scope_id: {
                "scope_type": scope_type,
                "scope_id": scope_id,
            },
            presence_read_permissions_for_channel_fn=lambda _channel: ['read("users")'],
        )

        self.assertEqual(payload["id"], "nest_chat")
        self.assertIs(payload["active_users"], online_users)
        self.assertIs(payload["online_users"], online_users)
        self.assertEqual(payload["online_count"], 1)
        self.assertTrue(payload["history_limited"])
        self.assertEqual(payload["presence_read_permissions"], ['read("users")'])

    def test_thread_payload_preserves_presence_and_block_state(self):
        other = {"id": "user-2", "name": "Pat"}
        active = [{"id": "user-2"}]
        payload = chat_summary_runtime.thread_payload(
            {"$id": "thread-1", "created_at": "created"},
            other_participant_fn=lambda _thread: other,
            public_user_fn=lambda user: dict(user),
            presence_statuses_for_users_fn=lambda ids: {ids[0]: "busy"},
            current_user_id_fn=lambda: "user-1",
            row_id_fn=lambda row: row["$id"],
            fresh_chat_room_presence_fn=lambda _scope_type, _scope_id: active,
            is_blocked_between_fn=lambda user_a, user_b: (user_a, user_b) == ("user-1", "user-2"),
            presence_scope_fn=lambda scope_type, scope_id: f"{scope_type}:{scope_id}",
            presence_read_permissions_for_thread_fn=lambda _thread: ["dm-permission"],
        )

        self.assertTrue(payload["blocked"])
        self.assertEqual(payload["presence_status"], "busy")
        self.assertTrue(payload["other_user"]["online"])
        self.assertEqual(payload["active_count"], 1)
        self.assertEqual(payload["presence_scope"], "thread:thread-1")

    def test_existing_visible_channels_initializes_defaults_filters_and_maps_errors(self):
        rows = [
            {"$id": "discord", "kind": "discord"},
            {"$id": "university", "kind": "university"},
        ]
        defaults = Mock()
        list_rows = Mock(return_value=rows)
        visible = chat_summary_runtime.existing_visible_channels_for_summary(
            default_channels_fn=defaults,
            list_rows_all_fn=list_rows,
            channels_collection="channels",
            query_cls=_QueryStub,
            can_access_channel_fn=lambda row: row["$id"] == "discord",
            appwrite_exception=RuntimeError,
            error_logger=Mock(),
        )

        self.assertEqual(visible, [rows[0]])
        defaults.assert_called_once_with()
        list_rows.assert_called_once_with("channels", [("equal", "kind", ["discord", "university"])])

        logger = Mock()
        failed = chat_summary_runtime.existing_visible_channels_for_summary(
            default_channels_fn=Mock(),
            list_rows_all_fn=Mock(side_effect=RuntimeError("unavailable")),
            channels_collection="channels",
            query_cls=_QueryStub,
            can_access_channel_fn=Mock(),
            appwrite_exception=RuntimeError,
            error_logger=logger,
        )
        self.assertEqual(failed, [])
        logger.exception.assert_called_once_with("Failed to list chat summary channels")

    def test_bootstrap_assembly_preserves_sections_capabilities_and_giphy_contract(self):
        config = SimpleNamespace(discord_invite_url="https://discord.example/invite")
        giphy_available = Mock(return_value=True)
        payload = chat_summary_runtime.assemble_bootstrap_payload(
            current_user=self.user,
            channels=[{"$id": "nest_chat"}],
            university={"status": "pending", "channel": {"$id": "other"}},
            dm_threads=[{"id": "thread-1"}],
            entitlements={"limits": {"max_chat_attachment_size_bytes": 123}},
            current_user_payload_fn=lambda: {"id": "user-1"},
            settings_payload_fn=lambda: {"chat_sound_enabled": True},
            channel_payload_fn=lambda channel, status: {"id": channel["$id"], "status": status},
            sync_environment_config_fn=lambda: config,
            attachments_enabled_fn=lambda: True,
            max_attachments_per_message=5,
            giphy_available_fn=giphy_available,
            giphy_api_key_fn=lambda: "giphy-key",
        )

        self.assertEqual(payload["sections"]["nest"], [{"id": "nest_chat", "status": None}])
        self.assertEqual(payload["sections"]["direct_messages"], [{"id": "thread-1"}])
        self.assertEqual(payload["university"]["status"], "pending")
        self.assertEqual(payload["discord_invite_url"], config.discord_invite_url)
        self.assertEqual(payload["capabilities"]["max_attachment_size_bytes"], 123)
        self.assertEqual(payload["capabilities"]["giphy"]["api_key"], "giphy-key")
        self.assertEqual(giphy_available.call_count, 2)

    def test_summary_assembly_caps_counts_and_retains_channel_thread_filtering(self):
        calls = []

        def read_state(user_id, scope_type, scope_id):
            calls.append(("read", user_id, scope_type, scope_id))
            return None

        def unread_count(scope_type, scope_id, *_args):
            return (120, True) if scope_type == "channel" else (2, False)

        payload = chat_summary_runtime.assemble_chat_summary_payload(
            "user-1",
            [{"$id": "channel-1", "label": "Chat"}],
            threads_fn=lambda: [{"$id": "thread-1"}],
            row_id_fn=lambda row: row["$id"],
            read_state_for_scope_fn=read_state,
            unread_count_fn=unread_count,
            unread_cap=99,
        )

        self.assertEqual(payload["total_unread"], 99)
        self.assertTrue(payload["unread_capped"])
        self.assertTrue(payload["has_unread"])
        self.assertEqual(
            payload["rooms"],
            [
                {"type": "channel", "id": "channel-1", "label": "Chat", "unread_count": 99, "has_unread": True},
                {"type": "thread", "id": "thread-1", "unread_count": 2, "has_unread": True},
            ],
        )
        self.assertEqual(calls, [
            ("read", "user-1", "channel", "channel-1"),
            ("read", "user-1", "thread", "thread-1"),
        ])


class ChatBootstrapSummaryRouteTests(unittest.TestCase):
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
        self.app.config["SERVER_NAME"] = "example.test"
        self.app.config["PROPAGATE_EXCEPTIONS"] = False
        login_manager.unauthorized_callback = None
        login_manager.login_view = None
        login_manager.init_app(self.app)
        self.app.register_blueprint(chat_api.chat_api_bp)
        self.user = _RouteUser()

        @login_manager.user_loader
        def load_user(user_id):
            return self.user if user_id == self.user.id else None

    def _login(self, client):
        with client.session_transaction() as session:
            session["_user_id"] = self.user.id
            session["_fresh"] = True

    def test_registered_bootstrap_preserves_payload_and_invokes_patchable_adapters(self):
        channel = {"$id": "nest_chat", "kind": "discord"}
        university = {"status": "error", "channel": None}
        config = SimpleNamespace(discord_invite_url="https://discord.example/invite")
        with self.app.test_client() as client:
            self._login(client)
            with patch.object(chat_api, "current_user", self.user), \
                    patch.object(chat_api, "_default_channels", return_value=[channel]), \
                    patch.object(chat_api, "_ensure_university_request", return_value=university), \
                    patch.object(chat_api, "sync_chat_presence_labels_for_user") as sync_labels, \
                    patch.object(chat_api, "_list_threads", return_value=[]), \
                    patch.object(chat_api, "request_entitlements", return_value={"limits": {"max_chat_attachment_size_bytes": 123}}), \
                    patch.object(chat_api, "_current_user_payload", return_value={"id": "user-1"}), \
                    patch.object(chat_api, "_settings_payload", return_value={"chat_sound_enabled": True}), \
                    patch.object(chat_api, "_channel_payload", return_value={"id": "nest_chat"}) as channel_payload, \
                    patch.object(chat_api, "runtime_environment_config", return_value=config), \
                    patch.object(chat_api, "_appwrite_chat_attachments_enabled", return_value=True), \
                    patch.object(chat_api, "giphy_available", return_value=False), \
                    patch.object(chat_api, "giphy_api_key") as giphy_key:
                response = client.get("/api/chat/bootstrap")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["university"]["status"], "error")
        self.assertEqual(payload["sections"]["nest"], [{"id": "nest_chat"}])
        self.assertEqual(payload["capabilities"]["max_attachment_size_bytes"], 123)
        self.assertEqual(payload["capabilities"]["giphy"], {"available": False, "api_key": "", "rating": "pg"})
        sync_labels.assert_called_once_with("user-1")
        channel_payload.assert_called_once_with(channel, None)
        giphy_key.assert_not_called()

    def test_registered_summary_preserves_channel_thread_aggregation(self):
        channel = {"$id": "channel-1", "label": "Chat"}
        thread = {"$id": "thread-1"}
        with self.app.test_client() as client:
            self._login(client)
            with patch.object(chat_api, "current_user", self.user), \
                    patch.object(chat_api, "_current_user_id", return_value="user-1"), \
                    patch.object(chat_api, "_existing_visible_channels_for_summary", return_value=[channel]), \
                    patch.object(chat_api, "_threads_for_current_user", return_value=[thread]), \
                    patch.object(chat_api, "_read_state_for_scope", return_value=None), \
                    patch.object(chat_api, "_unread_count", side_effect=[(4, False), (7, True)]):
                response = client.get("/api/chat/summary")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {
            "total_unread": 11,
            "unread_capped": True,
            "has_unread": True,
            "rooms": [
                {"type": "channel", "id": "channel-1", "label": "Chat", "unread_count": 4, "has_unread": True},
                {"type": "thread", "id": "thread-1", "unread_count": 7, "has_unread": True},
            ],
        })

    def test_registered_summary_preserves_empty_error_fallback(self):
        with self.app.test_client() as client:
            self._login(client)
            with patch.object(chat_api, "current_user", self.user), \
                    patch.object(chat_api, "_current_user_id", return_value="user-1"), \
                    patch.object(chat_api, "_default_channels", return_value=[]), \
                    patch.object(chat_api, "list_rows_all", side_effect=chat_api.AppwriteException("unavailable")), \
                    patch.object(chat_api, "_threads_for_current_user", return_value=[]):
                response = client.get("/api/chat/summary")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {
            "total_unread": 0,
            "unread_capped": False,
            "has_unread": False,
            "rooms": [],
        })

    def test_registered_bootstrap_preserves_entitlement_error_status(self):
        error = EntitlementLimitError("chat attachment size", 10, 11, 10)
        with self.app.test_client() as client:
            self._login(client)
            with patch.object(chat_api, "current_user", self.user), \
                    patch.object(chat_api, "_default_channels", return_value=[]), \
                    patch.object(chat_api, "_ensure_university_request", return_value={"status": "none", "channel": None}), \
                    patch.object(chat_api, "sync_chat_presence_labels_for_user"), \
                    patch.object(chat_api, "_list_threads", return_value=[]), \
                    patch.object(chat_api, "request_entitlements", side_effect=error) as request_entitlements:
                response = client.get("/api/chat/bootstrap")

        self.assertEqual(response.status_code, 500)
        request_entitlements.assert_called_once_with(self.user)


if __name__ == "__main__":
    unittest.main()
