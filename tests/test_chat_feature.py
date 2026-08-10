import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

from flask import Flask

from extensions import login_manager
import blueprints.chat_api as chat_api
from tests.support.harness import reset_flask_login_manager
from avatar_images import APSTUDY_LOGO_URL
import services.chat_presence as chat_presence
from services.chat_formatting import render_markdown
from services.chat_presence import CHAT_UNIVERSITY_LABEL_PREFIX, university_presence_label
from services.universities import school_payload, search_universities
import services.discord_bridge as discord_bridge


class TestChatFeature(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.secret_key = "test"
        self.user = SimpleNamespace(
            id="user-1",
            name="Derek C",
            username="derek",
            picture_url="https://example.test/avatar.png",
            school="Emory University",
            school_key="emory-university",
            major="CS",
            graduation_year="2026",
            class_year="2026",
            education_level="Undergraduate",
        )

    def test_university_search_and_custom_school_payload(self):
        results = search_universities("Emory")
        self.assertTrue(any(row["name"] == "Emory University" for row in results))

        matched = school_payload("Emory University")
        self.assertEqual(matched["school_source"], "scorecard")
        self.assertEqual(matched["school_key"], "emory-university")

        custom = school_payload("A Very Real Custom University")
        self.assertEqual(custom["school_source"], "custom")
        self.assertEqual(custom["school_key"], "a-very-real-custom-university")

    def test_markdown_renderer_sanitizes_script_and_keeps_safe_formatting(self):
        rendered = render_markdown("**hello** <script>alert(1)</script> https://example.com")
        self.assertIn("<strong>hello</strong>", rendered)
        self.assertIn("&lt;script&gt;", rendered)
        self.assertNotIn("<script>", rendered)
        self.assertIn('rel="noopener noreferrer nofollow"', rendered)

    def test_missing_public_user_avatar_uses_neutral_default(self):
        payload = chat_api._public_user({"$id": "user-2", "name": "No Avatar"})

        self.assertTrue(payload["picture_url"].startswith("data:image/svg+xml"))
        self.assertNotEqual(payload["picture_url"], APSTUDY_LOGO_URL)

    def test_public_user_includes_tier_badge_metadata_for_every_chat_profile_surface(self):
        payload = chat_api._public_user({
            "$id": "user-2",
            "name": "Grade A User",
            "tier": "grade_a",
        })

        self.assertEqual(payload["tier_label"], "Grade A")
        self.assertEqual(payload["tier_badge"]["asset"], "/static/images/tiers/grade-a-egg.png")

    def test_missing_message_avatar_serializes_neutral_default(self):
        row = {
            "$id": "message-1",
            "user_id": "user-1",
            "source": "appwrite",
            "author_name": "No Avatar",
            "created_at": "2026-05-25T00:00:00Z",
        }
        with self.app.test_request_context("/api/chat/channels/nest_chat/messages"):
            with patch.object(chat_api, "current_user", self.user):
                payload = chat_api._serialize_message(row)

        self.assertTrue(payload["author_avatar_url"].startswith("data:image/svg+xml"))
        self.assertNotEqual(payload["author_avatar_url"], APSTUDY_LOGO_URL)

    def test_delete_message_allows_owner_within_five_minutes(self):
        created_at = (datetime.now(timezone.utc) - timedelta(minutes=4)).isoformat()
        row = {
            "$id": "message-1",
            "user_id": "user-1",
            "source": "appwrite",
            "channel_id": "nest_chat",
            "created_at": created_at,
        }
        with self.app.test_request_context("/api/chat/messages/message-1", method="DELETE"):
            with patch.object(chat_api, "current_user", self.user), \
                    patch.object(chat_api, "get_row_safe", return_value=row), \
                    patch.object(chat_api, "update_row_safe") as update_row, \
                    patch.object(chat_api, "emit_chat_event") as emit_event, \
                    patch.object(chat_api, "emit_audit_event") as emit_audit:
                response = chat_api.delete_message.__wrapped__("message-1")

        self.assertEqual(response.status_code if hasattr(response, "status_code") else 200, 200)
        update_row.assert_called_once()
        payload = update_row.call_args.args[2]
        self.assertEqual(payload["deleted_by"], "user-1")
        self.assertIn("deleted_at", payload)
        emit_event.assert_called_once()
        self.assertEqual(emit_event.call_args.args[:3], ("channel", "nest_chat", "message_deleted"))
        emit_audit.assert_called_once()
        audit_event = emit_audit.call_args.args[0]
        self.assertEqual(audit_event.channel, "chat_deletes")
        self.assertEqual(audit_event.metadata["message_id"], "message-1")

    def test_delete_message_rejects_after_five_minutes(self):
        created_at = (datetime.now(timezone.utc) - timedelta(minutes=6)).isoformat()
        row = {
            "$id": "message-1",
            "user_id": "user-1",
            "source": "appwrite",
            "created_at": created_at,
        }
        with self.app.test_request_context("/api/chat/messages/message-1", method="DELETE"):
            with patch.object(chat_api, "current_user", self.user), \
                    patch.object(chat_api, "get_row_safe", return_value=row), \
                    patch.object(chat_api, "update_row_safe") as update_row:
                response, status = chat_api.delete_message.__wrapped__("message-1")

        self.assertEqual(status, 403)
        self.assertIn("5 minutes", response.get_json()["error"])
        update_row.assert_not_called()

    def test_get_message_returns_serialized_payload_for_accessible_message(self):
        row = {
            "$id": "message-1",
            "user_id": "user-2",
            "channel_id": "nest_chat",
            "source": "appwrite",
            "content": "hello",
            "created_at": "2026-05-23T20:00:00Z",
        }
        with self.app.test_request_context("/api/chat/messages/message-1", method="GET"):
            with patch.object(chat_api, "current_user", self.user), \
                    patch.object(chat_api, "_message_for_current_user", return_value=row):
                response = chat_api.delete_message.__wrapped__("message-1")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["message"]["id"], "message-1")
        self.assertEqual(response.get_json()["message"]["content"], "hello")

    def test_university_request_created_on_chat_bootstrap_helper(self):
        created = {"$id": "request-1", "status": "pending", "school_key": "emory-university"}
        with self.app.test_request_context("/api/chat/bootstrap"):
            with patch.object(chat_api, "current_user", self.user), \
                    patch.object(chat_api, "_find_university_channel", return_value=None), \
                    patch.object(chat_api, "first_row", return_value=None), \
                    patch.object(chat_api, "create_row_safe", return_value=created) as create_row:
                state = chat_api._ensure_university_request()

        self.assertEqual(state["status"], "pending")
        self.assertIsNotNone(state["channel"])
        self.assertEqual(state["channel"]["name"], "Emory University")
        self.assertTrue(state["channel"]["read_only"])
        self.assertFalse(state["channel"]["approved"])
        self.assertEqual(state["channel"]["university_status"], "pending")
        self.assertEqual(create_row.call_args.kwargs["data"]["label"], "[Uni Channel Approval]")

    def test_list_messages_after_message_id_includes_same_timestamp(self):
        shared_ts = "2026-05-23T20:00:00Z"
        first = {
            "$id": "message-a",
            "channel_id": "nest_chat",
            "user_id": "user-2",
            "created_at": shared_ts,
        }
        second = {
            "$id": "message-b",
            "channel_id": "nest_chat",
            "user_id": "user-2",
            "created_at": shared_ts,
        }
        with patch.object(chat_api, "get_row_safe", return_value=first), \
                patch.object(chat_api, "list_rows_safe", return_value={"rows": [first, second]}):
            rows = chat_api._list_messages(
                "channel",
                "nest_chat",
                after=shared_ts,
                after_message_id="message-a",
            )

        self.assertEqual([chat_api._row_id(row) for row in rows], ["message-b"])

    def test_chat_event_permissions_are_limited_to_dm_participants(self):
        with patch.object(chat_api, "create_row_safe", return_value={"$id": "event-1"}) as create_row:
            chat_api.emit_chat_event(
                "thread",
                "thread-1",
                "message_created",
                message_id="message-1",
                readable_user_ids=["user-b", "user-a", "user-a"],
            )

        create_row.assert_called_once()
        self.assertEqual(create_row.call_args.kwargs["data"]["message_id"], "message-1")
        self.assertEqual(
            create_row.call_args.kwargs["permissions"],
            ['read("user:user-a")', 'read("user:user-b")'],
        )

    def test_emit_chat_event_writes_sqlite_and_notifies_waiters(self):
        with patch.object(chat_api, "create_row_safe", return_value={"$id": "event-1"}) as create_row, \
                patch.object(chat_api, "_notify_chat_event_waiters") as notify:
            row = chat_api.emit_chat_event(
                "channel",
                "nest_chat",
                "message_created",
                message_id="message-1",
                channel_id="nest_chat",
                actor_id="user-1",
            )

        self.assertEqual(row["$id"], "event-1")
        create_row.assert_called_once()
        notify.assert_called_once()
        self.assertEqual(
            create_row.call_args.kwargs["data"]["event_type"],
            "message_created",
        )

    def test_emit_chat_event_uses_university_label_permissions(self):
        school_key = "emory-university"
        label = university_presence_label(school_key)
        channel = {
            "$id": "uni_emory-university",
            "kind": "university",
            "school_key": school_key,
            "approved": True,
        }
        with patch.object(chat_api, "create_row_safe", return_value={"$id": "event-1"}) as create_row:
            chat_api.emit_chat_event(
                "channel",
                channel["$id"],
                "message_created",
                message_id="message-1",
                channel_id=channel["$id"],
                channel=channel,
            )

        expected = [f'read("label:{label}")']
        self.assertEqual(create_row.call_args.kwargs["permissions"], expected)

    def test_emit_chat_event_uses_users_permission_for_discord_channels(self):
        channel = {
            "$id": "nest_chat",
            "kind": "discord",
            "approved": True,
        }
        with patch.object(chat_api, "create_row_safe", return_value={"$id": "event-1"}) as create_row:
            chat_api.emit_chat_event(
                "channel",
                channel["$id"],
                "message_created",
                message_id="message-1",
                channel=channel,
            )

        expected = ['read("users")']
        self.assertEqual(create_row.call_args.kwargs["permissions"], expected)

    def test_event_visible_for_user_filters_channel_and_thread_access(self):
        with patch.object(chat_api, "current_user", self.user), \
                patch.object(chat_api, "get_row_safe") as get_row, \
                patch.object(chat_api, "_can_access_channel", return_value=True) as can_access:
            get_row.return_value = {"$id": "nest_chat"}
            self.assertTrue(chat_api._event_visible_for_user({
                "scope_type": "channel",
                "scope_id": "nest_chat",
            }))
            can_access.assert_called_once()

        with patch.object(chat_api, "current_user", self.user), \
                patch.object(chat_api, "_thread_accessible_by_user", return_value=False) as thread_access:
            self.assertFalse(chat_api._event_visible_for_user({
                "scope_type": "thread",
                "scope_id": "thread-1",
            }))
            thread_access.assert_called_once_with("thread-1", "user-1")

    def test_chat_events_stream_requires_login(self):
        reset_flask_login_manager()
        login_manager.init_app(self.app)
        self.app.register_blueprint(chat_api.chat_api_bp)
        with self.app.test_client() as client:
            response = client.get("/api/chat/events/stream")
        self.assertIn(response.status_code, (302, 401))

    def test_presence_heartbeat_returns_local_presence_row(self):
        row = {
            "$id": "presence-1",
            "scope_type": "site",
            "scope_id": "global",
            "last_seen_at": "2026-06-23T12:00:00Z",
        }
        with self.app.test_request_context(
            "/api/presence/heartbeat",
            method="POST",
            json={"scope_type": "site", "scope_id": "global", "tab_id": "tab-1"},
        ):
            with patch.object(chat_api, "current_user", self.user), \
                    patch.object(chat_api, "_upsert_presence", return_value=row) as upsert:
                response = chat_api.presence_heartbeat.__wrapped__()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["presence"]["scope_type"], "site")
        self.assertEqual(len(response.get_json()["presences"]), 1)
        upsert.assert_called_once_with("site", "global", "tab-1")

    def test_presence_heartbeat_batches_global_and_room_scopes(self):
        scopes = [
            {"scope_type": "chat", "scope_id": "global"},
            {"scope_type": "chat", "scope_id": "room-1"},
        ]
        rows = [
            {"scope_type": scope["scope_type"], "scope_id": scope["scope_id"], "last_seen_at": "now"}
            for scope in scopes
        ]
        with self.app.test_request_context(
            "/api/presence/heartbeat",
            method="POST",
            json={"scopes": scopes, "tab_id": "tab-1"},
        ):
            with patch.object(chat_api, "current_user", self.user), \
                    patch.object(chat_api, "_upsert_presence", side_effect=rows) as upsert:
                response = chat_api.presence_heartbeat.__wrapped__()

        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["scope_id"] for row in response.get_json()["presences"]], ["global", "room-1"])
        self.assertEqual(upsert.call_count, 2)
        upsert.assert_any_call("chat", "global", "tab-1")
        upsert.assert_any_call("chat", "room-1", "tab-1")

    def test_presence_heartbeat_rejects_non_object_json(self):
        with self.app.test_request_context(
            "/api/presence/heartbeat",
            method="POST",
            json=[],
        ):
            with patch.object(chat_api, "current_user", self.user), \
                    patch.object(chat_api, "_upsert_presence") as upsert:
                response, status = chat_api.presence_heartbeat.__wrapped__()

        self.assertEqual(status, 400)
        self.assertIn("JSON object", response.get_json()["error"])
        upsert.assert_not_called()

    def test_presence_status_precedence_uses_active_before_busy(self):
        rows = [
            {"user_id": "user-2", "scope_type": "site", "last_seen_at": "2026-06-23T12:00:00Z"},
            {"user_id": "user-2", "scope_type": "chat", "last_seen_at": "2026-06-23T12:00:01Z"},
            {"user_id": "user-3", "scope_type": "site", "last_seen_at": "2026-06-23T12:00:00Z"},
        ]
        with patch.object(chat_api, "_fresh_presence_rows", return_value=rows):
            statuses = chat_api._presence_statuses_for_users(["user-2", "user-3", "user-4"])

        self.assertEqual(statuses["user-2"], "active")
        self.assertEqual(statuses["user-3"], "busy")
        self.assertEqual(statuses["user-4"], "offline")

    def test_focus_mode_presence_overrides_online_and_busy_states(self):
        rows = [
            {"user_id": "user-2", "scope_type": "chat", "last_seen_at": "2026-06-23T12:00:01Z"},
            {"user_id": "user-3", "scope_type": "site", "last_seen_at": "2026-06-23T12:00:00Z"},
        ]
        with patch.object(chat_api, "_fresh_presence_rows", return_value=rows), \
                patch("services.focus_mode.active_focus_user_ids", return_value={"user-2", "user-3"}):
            statuses = chat_api._presence_statuses_for_users(["user-2", "user-3"])

        self.assertEqual(statuses, {"user-2": "focus", "user-3": "focus"})

    def test_presence_status_lookup_uses_scope_specific_freshness(self):
        chat_rows = [
            {"user_id": "user-2", "scope_type": "chat", "last_seen_at": "2026-06-23T12:00:01Z"},
        ]
        site_rows = [
            {"user_id": "user-3", "scope_type": "site", "last_seen_at": "2026-06-23T12:00:00Z"},
        ]
        with patch.object(chat_api, "_fresh_presence_rows", side_effect=[chat_rows, site_rows]) as fresh_rows:
            statuses = chat_api._presence_statuses_for_users(["user-2", "user-3", "user-4"])

        self.assertEqual(statuses["user-2"], "active")
        self.assertEqual(statuses["user-3"], "busy")
        self.assertEqual(statuses["user-4"], "offline")
        self.assertEqual(fresh_rows.call_args_list[0].kwargs["seconds"], chat_api.PRESENCE_CHAT_FRESH_SECONDS)
        self.assertEqual(fresh_rows.call_args_list[1].kwargs["seconds"], chat_api.PRESENCE_SITE_FRESH_SECONDS)

    def test_presence_scope_freshness_windows_are_specific(self):
        self.assertEqual(chat_api._presence_fresh_seconds("chat"), 30)
        self.assertEqual(chat_api._presence_fresh_seconds("site"), 180)
        self.assertEqual(chat_api._presence_fresh_seconds("typing_channel"), 10)
        self.assertEqual(chat_api._presence_fresh_seconds("typing_thread"), 10)

    def test_presence_status_ignores_stale_rows_from_fresh_query(self):
        with patch.object(chat_api, "_fresh_presence_rows_by_scope", return_value=[]):
            statuses = chat_api._presence_statuses_for_users(["user-2"])

        self.assertEqual(statuses, {"user-2": "offline"})

    def test_presence_online_requires_login(self):
        reset_flask_login_manager()
        login_manager.init_app(self.app)
        self.app.register_blueprint(chat_api.chat_api_bp)
        with self.app.test_client() as client:
            response = client.get("/api/presence/online")
        self.assertIn(response.status_code, (302, 401))

    def test_presence_statuses_requires_login(self):
        reset_flask_login_manager()
        login_manager.init_app(self.app)
        self.app.register_blueprint(chat_api.chat_api_bp)
        with self.app.test_client() as client:
            response = client.post("/api/presence/statuses", json={"user_ids": ["user-1"]})
        self.assertIn(response.status_code, (302, 401))

    def test_presence_room_requires_login(self):
        reset_flask_login_manager()
        login_manager.init_app(self.app)
        self.app.register_blueprint(chat_api.chat_api_bp)
        with self.app.test_client() as client:
            response = client.post("/api/presence/room", json={"scope_type": "channel", "scope_id": "nest_chat"})
        self.assertIn(response.status_code, (302, 401))

    def test_presence_room_returns_targeted_online_and_typing_users(self):
        online_user = {"id": "user-2", "name": "Online User", "presence_status": "busy"}
        typing_user = {"id": "user-3", "name": "Typing User", "typing_channel_ids": ["nest_chat"]}
        with self.app.test_request_context(
            "/api/presence/room",
            method="POST",
            json={"scope_type": "channel", "scope_id": "nest_chat"},
        ):
            with patch.object(chat_api, "current_user", self.user), \
                    patch.object(chat_api, "get_row_safe", return_value={"$id": "nest_chat", "kind": "discord"}), \
                    patch.object(chat_api, "_online_users_for_channel", return_value=[online_user]) as online, \
                    patch.object(chat_api, "_fresh_typing_room_presence", return_value=[typing_user]) as typing:
                response = chat_api.presence_room.__wrapped__()

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["active_users"], [online_user])
        self.assertEqual(payload["online_users"], [online_user])
        self.assertEqual(payload["typing_users"], [typing_user])
        online.assert_called_once_with({"$id": "nest_chat", "kind": "discord"})
        typing.assert_called_once_with("typing_channel", "nest_chat")

    def test_online_users_for_university_channel_filters_by_school_access(self):
        channel = {"$id": "uni_emory", "kind": "university", "approved": True, "school_key": "emory-university"}
        rows = [
            {"user_id": "user-2", "scope_type": "site", "last_seen_at": "2026-06-23T12:00:00Z"},
            {"user_id": "user-3", "scope_type": "site", "last_seen_at": "2026-06-23T12:00:00Z"},
        ]
        users = {
            "user-2": {"$id": "user-2", "name": "Emory User", "school_key": "emory-university"},
            "user-3": {"$id": "user-3", "name": "Other User", "school_key": "other-school"},
        }
        with patch.object(chat_api, "_fresh_presence_rows_by_scope", return_value=rows), \
                patch.object(chat_api.database, "list_rows", return_value={"rows": list(users.values())}) as list_users:
            payload = chat_api._online_users_for_channel(channel)

        self.assertEqual([user["id"] for user in payload], ["user-2"])
        self.assertEqual(list_users.call_count, 1)
        self.assertIs(list_users.call_args.kwargs["include_total"], False)
        self.assertEqual(payload[0]["presence_status"], "busy")

    def test_dm_thread_payload_includes_presence_permissions(self):
        thread = {
            "$id": "thread-1",
            "participant_a": "user-1",
            "participant_b": "user-2",
            "created_at": "2026-05-23T20:00:00Z",
        }
        other_user = {
            "$id": "user-2",
            "name": "Test Account",
            "username": "test",
            "school": "Emory University",
            "major": "Biology",
            "graduation_year": "2027",
            "education_level": "Undergraduate",
            "banner_color": "#aabbcc",
            "created_at": "2026-05-20T20:00:00Z",
        }
        with patch.object(chat_api, "current_user", self.user), \
                patch.object(chat_api, "_other_participant", return_value=other_user), \
                patch.object(chat_api, "_is_blocked_between", return_value=False), \
                patch.object(chat_api, "_presence_statuses_for_users", return_value={"user-2": "offline"}), \
                patch.object(chat_api, "_fresh_chat_room_presence", return_value=[]):
            payload = chat_api._thread_payload(thread)

        self.assertEqual(payload["presence_status"], "offline")
        self.assertFalse(payload["other_user"]["online"])
        self.assertEqual(payload["presence_scope"], {
            "scope_type": "thread",
            "scope_id": "thread-1",
            "room_key": "thread:thread-1",
        })
        self.assertEqual(
            payload["presence_read_permissions"],
            ['read("user:user-1")', 'read("user:user-2")'],
        )
        self.assertTrue(payload["presence_profile_resolve_allowed"])
        self.assertEqual(payload["other_user"]["handle"], "@test")
        self.assertEqual(payload["other_user"]["banner_color"], "#aabbcc")
        self.assertEqual(payload["other_user"]["member_since"], "May 20, 2026")
        self.assertTrue(payload["other_user"]["is_emory_school"])
        self.assertTrue(payload["other_user"]["is_early_member"])
        self.assertEqual(payload["other_user"]["major"], "Biology")

    def test_channel_payload_includes_presence_permissions(self):
        with patch.object(chat_api, "_fresh_chat_room_presence", return_value=[]):
            discord_payload = chat_api._channel_payload({
                "$id": "nest_chat",
                "kind": "discord",
                "name": "chat",
                "label": "Chat",
                "approved": True,
            })
        self.assertEqual(discord_payload["presence_read_permissions"], ['read("users")'])
        self.assertEqual(discord_payload["presence_scope"]["room_key"], "channel:nest_chat")
        self.assertTrue(discord_payload["presence_profile_resolve_allowed"])

        label = university_presence_label("emory-university")
        self.assertTrue(label.startswith(CHAT_UNIVERSITY_LABEL_PREFIX))
        self.assertLessEqual(len(label), 36)
        self.assertRegex(label, r"^[A-Za-z0-9]+$")
        with patch.object(chat_api, "_fresh_chat_room_presence", return_value=[]):
            university_payload = chat_api._channel_payload({
                "$id": "uni_emory-university",
                "kind": "university",
                "name": "Emory University",
                "label": "Emory University",
                "school_key": "emory-university",
                "approved": True,
            })
        self.assertEqual(university_payload["presence_read_permissions"], [f'read("label:{label}")'])

    def test_chat_summary_returns_zero_after_matching_read_state(self):
        channel = {"$id": "nest_chat", "kind": "discord", "label": "Chat"}
        with self.app.test_request_context("/api/chat/summary"):
            with patch.object(chat_api, "current_user", self.user), \
                    patch.object(chat_api, "_existing_visible_channels_for_summary", return_value=[channel]), \
                    patch.object(chat_api, "_threads_for_current_user", return_value=[]), \
                    patch.object(chat_api, "_read_state_for_scope", return_value={"last_read_at": "2026-05-26T22:00:00Z"}), \
                    patch.object(chat_api, "list_rows_safe", return_value={"rows": []}):
                response = chat_api.chat_summary.__wrapped__()

        payload = response.get_json()
        self.assertEqual(payload["total_unread"], 0)
        self.assertFalse(payload["unread_capped"])
        self.assertFalse(payload["has_unread"])
        self.assertEqual(payload["rooms"][0]["unread_count"], 0)

    def test_unread_count_full_scan_page_with_fewer_than_cap_is_not_capped(self):
        first_page = [
            {"$id": f"own-{index}", "user_id": "user-1", "created_at": f"2026-05-26T22:{index:02d}:00Z"}
            for index in range(20)
        ] + [
            {"$id": f"other-{index}", "user_id": "user-2", "created_at": f"2026-05-26T23:{index:02d}:00Z"}
            for index in range(30)
        ]
        second_page = [
            {"$id": f"deleted-{index}", "user_id": "user-2", "deleted_at": "2026-05-27T00:00:00Z"}
            for index in range(10)
        ]
        with patch.object(chat_api, "list_rows_safe", side_effect=[{"rows": first_page}, {"rows": second_page}]):
            unread, capped = chat_api._unread_count("channel", "nest_chat", "user-1", None)

        self.assertEqual(unread, 30)
        self.assertFalse(capped)

    def test_unread_count_ignores_own_and_deleted_messages(self):
        rows = [
            {"$id": "own", "user_id": "user-1"},
            {"$id": "deleted", "user_id": "user-2", "deleted_at": "2026-05-27T00:00:00Z"},
            {"$id": "visible", "user_id": "user-2"},
        ]
        with patch.object(chat_api, "list_rows_safe", return_value={"rows": rows}):
            unread, capped = chat_api._unread_count("channel", "nest_chat", "user-1", None)

        self.assertEqual(unread, 1)
        self.assertFalse(capped)

    def test_unread_count_respects_blocked_dm_senders(self):
        rows = [
            {"$id": "blocked", "user_id": "blocked-user"},
            {"$id": "visible", "user_id": "user-2"},
        ]
        with patch.object(chat_api, "_blocked_user_ids", return_value={"blocked-user"}), \
                patch.object(chat_api, "list_rows_safe", return_value={"rows": rows}):
            unread, capped = chat_api._unread_count("thread", "thread-1", "user-1", None)

        self.assertEqual(unread, 1)
        self.assertFalse(capped)

    def test_dm_thread_endpoint_returns_participant_thread_payload(self):
        thread = {"$id": "thread-1", "participant_a": "user-1", "participant_b": "user-2"}
        payload = {"id": "thread-1", "other_user": {"id": "user-2", "name": "Ada"}}
        with self.app.test_request_context("/api/chat/dm/threads/thread-1"):
            with patch.object(chat_api, "current_user", self.user), \
                    patch.object(chat_api, "_thread_for_user", return_value=thread), \
                    patch.object(chat_api, "_thread_payload", return_value=payload):
                response = chat_api.dm_thread.__wrapped__("thread-1")

        self.assertEqual(response.get_json()["thread"], payload)

    def test_dm_thread_endpoint_rejects_non_participant_thread(self):
        with self.app.test_request_context("/api/chat/dm/threads/thread-1"):
            with patch.object(chat_api, "current_user", self.user), \
                    patch.object(chat_api, "_thread_for_user", return_value=None):
                response, status = chat_api.dm_thread.__wrapped__("thread-1")

        self.assertEqual(status, 404)
        self.assertIn("Thread unavailable", response.get_json()["error"])

    def test_mark_unread_uses_previous_visible_message_boundary(self):
        target = {
            "$id": "target",
            "channel_id": "nest_chat",
            "user_id": "user-2",
            "created_at": "2026-05-26T22:10:00Z",
        }
        previous = {
            "$id": "previous",
            "channel_id": "nest_chat",
            "user_id": "user-1",
            "created_at": "2026-05-26T22:09:00Z",
        }
        existing = {"$id": "read-state-1"}
        with self.app.test_request_context("/api/chat/unread", method="POST"):
            with patch.object(chat_api, "current_user", self.user), \
                    patch.object(chat_api, "list_rows_safe", side_effect=[{"rows": [target]}, {"rows": [previous]}]), \
                    patch.object(chat_api, "first_row", return_value=existing), \
                    patch.object(chat_api, "update_row_safe", return_value={"$id": "read-state-1"}) as update_row:
                row = chat_api._mark_unread("channel", "nest_chat")

        self.assertEqual(row["$id"], "read-state-1")
        payload = update_row.call_args.args[2]
        self.assertEqual(payload["last_read_message_id"], "previous")
        self.assertEqual(payload["last_read_at"], "2026-05-26T22:09:00Z")

    def test_mark_unread_ignores_own_deleted_and_blocked_dm_messages(self):
        rows = [
            {"$id": "own", "thread_id": "thread-1", "user_id": "user-1", "created_at": "2026-05-26T22:12:00Z"},
            {"$id": "deleted", "thread_id": "thread-1", "user_id": "user-2", "deleted_at": "2026-05-26T22:11:00Z", "created_at": "2026-05-26T22:11:00Z"},
            {"$id": "blocked", "thread_id": "thread-1", "user_id": "blocked-user", "created_at": "2026-05-26T22:10:00Z"},
            {"$id": "visible", "thread_id": "thread-1", "user_id": "user-2", "created_at": "2026-05-26T22:09:00Z"},
        ]
        previous = {"$id": "previous", "thread_id": "thread-1", "user_id": "user-1", "created_at": "2026-05-26T22:08:00Z"}
        with self.app.test_request_context("/api/chat/unread", method="POST"):
            with patch.object(chat_api, "current_user", self.user), \
                    patch.object(chat_api, "_blocked_user_ids", return_value={"blocked-user"}), \
                    patch.object(chat_api, "list_rows_safe", side_effect=[{"rows": rows}, {"rows": [previous]}]), \
                    patch.object(chat_api, "first_row", return_value={"$id": "read-state-1"}), \
                    patch.object(chat_api, "update_row_safe", return_value={"$id": "read-state-1"}) as update_row:
                chat_api._mark_unread("thread", "thread-1")

        payload = update_row.call_args.args[2]
        self.assertEqual(payload["last_read_message_id"], "previous")
        self.assertEqual(payload["last_read_at"], "2026-05-26T22:08:00Z")

    def test_current_appwrite_labels_accepts_dict_response(self):
        users_service = Mock()
        users_service.get.return_value = {"labels": ["student", "chat_uni_old"]}
        with patch.object(chat_presence, "Users", return_value=users_service):
            labels = chat_presence._current_appwrite_labels("user-1")

        self.assertEqual(labels, ["student", "chat_uni_old"])

    def test_current_appwrite_labels_accepts_model_dump_response(self):
        account = Mock()
        account.model_dump.return_value = {"labels": ["student"]}
        del account.to_dict
        users_service = Mock()
        users_service.get.return_value = account
        with patch.object(chat_presence, "Users", return_value=users_service):
            labels = chat_presence._current_appwrite_labels("user-1")

        self.assertEqual(labels, ["student"])
        account.model_dump.assert_called_once_with(by_alias=True, mode="json")

    def test_current_appwrite_labels_accepts_to_dict_response(self):
        account = Mock()
        account.to_dict.return_value = {"labels": ["mentor"]}
        users_service = Mock()
        users_service.get.return_value = account
        with patch.object(chat_presence, "Users", return_value=users_service):
            labels = chat_presence._current_appwrite_labels("user-1")

        self.assertEqual(labels, ["mentor"])

    def test_current_appwrite_labels_missing_labels_returns_empty_list(self):
        users_service = Mock()
        users_service.get.return_value = {}
        with patch.object(chat_presence, "Users", return_value=users_service):
            labels = chat_presence._current_appwrite_labels("user-1")

        self.assertEqual(labels, [])

    def test_sync_chat_presence_preserves_unrelated_labels(self):
        label = university_presence_label("emory-university")
        users_service = Mock()
        users_service.get.return_value = {
            "labels": ["student", "chat_uni_old", "bad-label!"],
        }
        with patch.object(chat_presence, "Users", return_value=users_service), \
                patch.object(chat_presence, "_school_has_approved_channel", return_value=True):
            labels = chat_presence.sync_chat_presence_labels_for_user(
                "user-1",
                {"$id": "user-1", "school_key": "emory-university"},
            )

        self.assertEqual(labels, ["student", label])
        users_service.update_labels.assert_called_once_with("user-1", ["student", label])

    def test_discord_webhook_send_suppresses_mentions(self):
        response = Mock()
        response.status_code = 200
        response.json.return_value = {"id": "discord-message-1", "webhook_id": "webhook-1"}
        with patch.object(discord_bridge, "ensure_chat_webhook", return_value={"id": "webhook-1", "token": "token"}), \
                patch.object(discord_bridge.requests, "post", return_value=response) as post:
            result, webhook = discord_bridge.execute_chat_webhook("@everyone hello", "Derek", "https://example.test/a.png")

        self.assertEqual(result["id"], "discord-message-1")
        self.assertEqual(webhook["id"], "webhook-1")
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["allowed_mentions"], {"parse": []})
        self.assertEqual(payload["username"], "Derek")

    def test_delete_webhook_message_raises_when_webhook_missing(self):
        with patch.object(discord_bridge, "_load_webhook", return_value=None):
            with self.assertRaises(discord_bridge.DiscordBridgeError):
                discord_bridge.delete_webhook_message("webhook-1", "message-1")

    def test_delete_webhook_message_raises_on_webhook_id_mismatch(self):
        with patch.object(discord_bridge, "_load_webhook", return_value={"id": "webhook-1", "token": "token"}):
            with self.assertRaises(discord_bridge.DiscordBridgeError):
                discord_bridge.delete_webhook_message("other-webhook", "message-1")

    def test_delete_webhook_message_raises_when_message_id_missing(self):
        with patch.object(discord_bridge, "_load_webhook", return_value={"id": "webhook-1", "token": "token"}):
            with self.assertRaises(discord_bridge.DiscordBridgeError):
                discord_bridge.delete_webhook_message("webhook-1", "")

    def test_delete_message_calls_discord_delete_for_webhook_messages(self):
        created_at = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
        row = {
            "$id": "message-1",
            "user_id": "user-1",
            "source": "discord",
            "channel_id": "nest_chat",
            "discord_message_id": "discord-message-1",
            "discord_webhook_id": "webhook-1",
            "created_at": created_at,
        }
        with self.app.test_request_context("/api/chat/messages/message-1", method="DELETE"):
            with patch.object(chat_api, "current_user", self.user), \
                    patch.object(chat_api, "get_row_safe", return_value=row), \
                    patch.object(chat_api, "delete_webhook_message", return_value=True) as delete_webhook, \
                    patch.object(chat_api, "update_row_safe") as update_row, \
                    patch.object(chat_api, "emit_chat_event"), \
                    patch.object(chat_api, "emit_audit_event"):
                response = chat_api.delete_message.__wrapped__("message-1")

        self.assertEqual(response.status_code if hasattr(response, "status_code") else 200, 200)
        delete_webhook.assert_called_once_with("webhook-1", "discord-message-1")
        update_row.assert_called_once()

    def test_delete_message_aborts_when_discord_delete_fails(self):
        created_at = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
        row = {
            "$id": "message-1",
            "user_id": "user-1",
            "source": "discord",
            "channel_id": "nest_chat",
            "discord_message_id": "discord-message-1",
            "discord_webhook_id": "webhook-1",
            "created_at": created_at,
        }
        with self.app.test_request_context("/api/chat/messages/message-1", method="DELETE"):
            with patch.object(chat_api, "current_user", self.user), \
                    patch.object(chat_api, "get_row_safe", return_value=row), \
                    patch.object(
                        chat_api,
                        "delete_webhook_message",
                        side_effect=discord_bridge.DiscordBridgeError("Discord unavailable"),
                    ), \
                    patch.object(chat_api, "update_row_safe") as update_row:
                response, status = chat_api.delete_message.__wrapped__("message-1")

        self.assertEqual(status, 502)
        self.assertIn("Discord", response.get_json()["error"])
        update_row.assert_not_called()

    def test_reconcile_discord_deletes_soft_deletes_missing_recent_messages(self):
        channel = {
            "$id": "nest_chat",
            "kind": "discord",
            "discord_channel_id": "discord-channel",
        }
        discord_messages = [
            {"id": "discord-message-2", "timestamp": "2026-05-26T22:05:00Z"},
            {"id": "discord-message-3", "timestamp": "2026-05-26T22:00:00Z"},
        ]
        rows = [
            {
                "$id": "message-row-1",
                "channel_id": "nest_chat",
                "source": "discord",
                "discord_message_id": "discord-message-1",
                "created_at": "2026-05-26T22:02:00Z",
            },
            {
                "$id": "message-row-2",
                "channel_id": "nest_chat",
                "source": "discord",
                "discord_message_id": "discord-message-2",
                "created_at": "2026-05-26T22:04:00Z",
            },
        ]
        with patch.object(chat_api, "list_rows_all", return_value=rows), \
                patch.object(chat_api, "_soft_delete_discord_message", return_value=rows[0]) as soft_delete:
            deleted_count = chat_api._reconcile_discord_deletes(
                channel,
                discord_messages,
                emit_events=True,
            )

        self.assertEqual(deleted_count, 1)
        soft_delete.assert_called_once_with(
            channel,
            "discord-message-1",
            emit_event=True,
        )

    def test_reconcile_discord_deletes_skips_messages_older_than_fetch_window(self):
        channel = {
            "$id": "nest_chat",
            "kind": "discord",
            "discord_channel_id": "discord-channel",
        }
        discord_messages = [
            {"id": "discord-message-2", "timestamp": "2026-05-26T22:05:00Z"},
        ]
        rows = [
            {
                "$id": "message-row-1",
                "channel_id": "nest_chat",
                "source": "discord",
                "discord_message_id": "discord-message-1",
                "created_at": "2026-05-26T21:00:00Z",
            },
        ]
        with patch.object(chat_api, "list_rows_all", return_value=rows), \
                patch.object(chat_api, "_soft_delete_discord_message") as soft_delete:
            deleted_count = chat_api._reconcile_discord_deletes(
                channel,
                discord_messages,
                emit_events=True,
            )

        self.assertEqual(deleted_count, 0)
        soft_delete.assert_not_called()

    def test_discord_image_attachments_become_images_not_previews(self):
        message = {
            "attachments": [{
                "filename": "schedule.png",
                "url": "https://cdn.discordapp.com/attachments/channel/message/schedule.png",
                "proxy_url": "https://media.discordapp.net/attachments/channel/message/schedule.png",
                "content_type": "image/png",
                "width": 640,
                "height": 360,
            }],
        }
        previews = chat_api._discord_previews(message)
        images = chat_api._discord_images(message)

        self.assertEqual(previews, [])
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0]["kind"], "discord_image")
        self.assertEqual(images[0]["filename"], "schedule.png")
        self.assertEqual(images[0]["url"], "https://cdn.discordapp.com/attachments/channel/message/schedule.png")
        self.assertEqual(images[0]["proxy_url"], "https://media.discordapp.net/attachments/channel/message/schedule.png")

    def test_serialize_message_splits_discord_images_from_previews(self):
        row = {
            "$id": "message-1",
            "created_at": "2026-05-23T20:00:00Z",
            "link_preview_json": json.dumps([
                {"url": "https://example.test", "title": "Example"},
                {"kind": "discord_image", "url": "https://cdn.discordapp.com/image.png"},
            ]),
        }
        payload = chat_api._serialize_message(row)

        self.assertEqual(payload["previews"], [{"url": "https://example.test", "title": "Example"}])
        self.assertEqual(payload["images"], [{"kind": "discord_image", "url": "https://cdn.discordapp.com/image.png"}])

    def test_discord_mentions_render_as_inert_pills(self):
        message = {
            "content": "hi <@123> and <@&456>",
            "mentions": [{"id": "123", "global_name": "Derek Chen", "username": "derek"}],
        }
        with patch.object(chat_api, "fetch_guild_roles", return_value=[{"id": "456", "name": "Beta Tester"}]):
            rendered = chat_api._render_discord_content(message["content"], message)

        self.assertIn('<span class="chat-mention">@Derek Chen</span>', rendered)
        self.assertIn('<span class="chat-mention chat-mention-role">@Beta Tester</span>', rendered)
        self.assertNotIn("&lt;@123&gt;", rendered)
        self.assertNotIn("&lt;@&456&gt;", rendered)

    def test_discord_mentions_fetch_user_name_when_payload_missing(self):
        message = {"content": "hi <@123>", "mentions": []}
        with patch.object(chat_api, "fetch_discord_user", return_value={"id": "123", "global_name": "Fetched User"}):
            rendered = chat_api._render_discord_content(message["content"], message)

        self.assertIn('<span class="chat-mention">@Fetched User</span>', rendered)

    def test_discord_custom_emojis_render_as_lazy_cdn_images(self):
        message = {
            "content": "look <:bleak:1320062766026854541> <a:party:123456789012345678>",
            "mentions": [],
        }

        rendered = chat_api._render_discord_content(message["content"], message)

        self.assertIn('class="chat-custom-emoji"', rendered)
        self.assertIn("https://cdn.discordapp.com/emojis/1320062766026854541.png?size=48&amp;quality=lossless", rendered)
        self.assertIn("https://cdn.discordapp.com/emojis/123456789012345678.gif?size=48&amp;quality=lossless", rendered)
        self.assertIn('alt=":bleak:"', rendered)
        self.assertIn('loading="lazy"', rendered)
        self.assertNotIn("&lt;:bleak:1320062766026854541&gt;", rendered)

    def test_discord_message_row_id_is_deterministic(self):
        channel = {"discord_channel_id": "discord-channel"}
        row_id = chat_api._discord_message_row_id(channel, "discord-message-1")
        self.assertTrue(row_id.startswith("discord_"))
        self.assertEqual(len(row_id), len("discord_") + 24)
        self.assertEqual(row_id, chat_api._discord_message_row_id(channel, "discord-message-1"))
        self.assertNotEqual(
            row_id,
            chat_api._discord_message_row_id(channel, "discord-message-2"),
        )

    def test_discord_upsert_uses_deterministic_row_id_on_create(self):
        channel = {
            "$id": "nest_chat",
            "kind": "discord",
            "discord_channel_id": "discord-channel",
        }
        message = {
            "id": "discord-message-1",
            "content": "fresh from discord",
            "timestamp": "2026-05-26T22:00:00Z",
            "author": {"id": "author-1", "username": "UrbanPanda"},
        }
        expected_row_id = chat_api._discord_message_row_id(channel, message["id"])

        with patch.object(chat_api, "get_row_safe", side_effect=[None, {"$id": expected_row_id}]), \
                patch.object(chat_api, "first_row", return_value=None), \
                patch.object(chat_api, "insert_row_ignore_safe", return_value=True) as insert_row, \
                patch.object(chat_api, "emit_chat_event"):
            row, created = chat_api._upsert_discord_message(channel, message, emit_event=True)

        self.assertTrue(created)
        self.assertEqual(insert_row.call_args.kwargs["row_id"], expected_row_id)
        self.assertEqual(row["$id"], expected_row_id)

    def test_discord_upsert_recovers_from_duplicate_create_race(self):
        channel = {
            "$id": "nest_chat",
            "kind": "discord",
            "discord_channel_id": "discord-channel",
        }
        message = {
            "id": "discord-message-1",
            "content": "same discord payload",
            "timestamp": "2026-05-26T22:00:00Z",
            "author": {"id": "author-1", "username": "UrbanPanda"},
        }
        existing = {
            "$id": chat_api._discord_message_row_id(channel, message["id"]),
            **chat_api._discord_message_payload(channel, message),
        }

        with patch.object(chat_api, "get_row_safe", side_effect=[None, existing]), \
                patch.object(chat_api, "first_row", return_value=None), \
                patch.object(chat_api, "insert_row_ignore_safe", return_value=False), \
                patch.object(chat_api, "update_row_safe") as update_row, \
                patch.object(chat_api, "emit_chat_event") as emit_event:
            row, created = chat_api._upsert_discord_message(channel, message, emit_event=True)

        self.assertFalse(created)
        self.assertEqual(row["$id"], existing["$id"])
        update_row.assert_not_called()
        emit_event.assert_not_called()

    def test_discord_upsert_is_idempotent_for_duplicate_gateway_delivery(self):
        channel = {
            "$id": "nest_chat",
            "kind": "discord",
            "discord_channel_id": "discord-channel",
        }
        message = {
            "id": "discord-message-1",
            "content": "same discord payload",
            "timestamp": "2026-05-26T22:00:00Z",
            "author": {"id": "author-1", "username": "UrbanPanda"},
        }
        existing = {
            "$id": chat_api._discord_message_row_id(channel, message["id"]),
            **chat_api._discord_message_payload(channel, message),
        }

        with patch.object(chat_api, "get_row_safe", return_value=existing), \
                patch.object(chat_api, "first_row", return_value=None), \
                patch.object(chat_api, "insert_row_ignore_safe") as insert_row, \
                patch.object(chat_api, "update_row_safe") as update_row, \
                patch.object(chat_api, "emit_chat_event") as emit_event:
            row, created = chat_api._upsert_discord_message(channel, message, emit_event=True)

        self.assertFalse(created)
        self.assertEqual(row["$id"], existing["$id"])
        insert_row.assert_not_called()
        update_row.assert_not_called()
        emit_event.assert_not_called()

    def test_serialize_message_includes_author_profile_for_nest_user(self):
        row = {
            "$id": "message-1",
            "user_id": "user-2",
            "source": "appwrite",
            "author_name": "Derek",
            "created_at": "2026-05-25T00:00:00Z",
        }
        users_by_id = {
            "user-2": {
                "$id": "user-2",
                "name": "Derek",
                "username": "derek",
                "picture_url": "https://example.test/avatar.png",
                "created_at": "2026-01-01T00:00:00Z",
            },
        }
        with self.app.test_request_context("/api/chat/channels/nest_chat/messages"):
            with patch.object(chat_api, "current_user", self.user):
                payload = chat_api._serialize_message(row, users_by_id)

        self.assertEqual(payload["author_profile"]["id"], "user-2")
        self.assertEqual(payload["author_profile"]["username"], "derek")

    def test_serialize_message_sets_null_author_profile_for_discord_only_author(self):
        row = {
            "$id": "message-1",
            "source": "discord",
            "author_name": "Discord User",
            "created_at": "2026-05-25T00:00:00Z",
        }
        with self.app.test_request_context("/api/chat/channels/nest_chat/messages"):
            with patch.object(chat_api, "current_user", self.user):
                payload = chat_api._serialize_message(row, {})

        self.assertIsNone(payload["author_profile"])

    def test_initialize_new_user_discord_read_states_marks_accessible_channels(self):
        channels = [
            {"$id": "nest_chat", "kind": "discord"},
            {"$id": "nest_announcements", "kind": "discord"},
        ]
        latest = {"$id": "message-1", "created_at": "2026-05-26T22:00:00Z"}

        with patch.object(chat_api, "_default_channels"), \
                patch.object(chat_api, "list_rows_all", return_value=channels), \
                patch.object(chat_api, "_latest_visible_message", return_value=latest), \
                patch.object(chat_api, "_persist_read_state") as persist_read:
            chat_api.initialize_new_user_discord_read_states("new-user")

        self.assertEqual(persist_read.call_count, 2)
        persist_read.assert_any_call("new-user", "channel", "nest_chat", latest)
        persist_read.assert_any_call("new-user", "channel", "nest_announcements", latest)

    def test_create_welcome_dm_is_idempotent_and_emits_event(self):
        sender = {
            "$id": chat_api.WELCOME_DM_SENDER_ID,
            "name": "Nest Team",
            "username": "nest",
            "picture_url": "https://example.test/nest.png",
        }
        thread = {
            "$id": "thread-1",
            "participant_a": chat_api.WELCOME_DM_SENDER_ID,
            "participant_b": "new-user",
        }
        existing = {"$id": "welcome-message", "external_id": f"welcome:{chat_api.WELCOME_DM_SENDER_ID}:new-user"}

        with patch.object(chat_api, "first_row", return_value=existing):
            row = chat_api.create_welcome_dm_for_user("new-user")

        self.assertEqual(row["$id"], "welcome-message")

        with patch.object(chat_api, "first_row", return_value=None), \
                patch.object(chat_api, "get_row_safe", return_value=sender), \
                patch.object(chat_api, "_get_or_create_thread_between", return_value=thread), \
                patch.object(chat_api, "create_row_safe", return_value={"$id": "welcome-message"}) as create_row, \
                patch.object(chat_api, "update_row_safe"), \
                patch.object(chat_api, "emit_chat_event") as emit_event:
            row = chat_api.create_welcome_dm_for_user("new-user")

        payload = create_row.call_args.kwargs["data"]
        self.assertEqual(payload["thread_id"], "thread-1")
        self.assertEqual(payload["source"], "system")
        self.assertEqual(payload["external_id"], f"welcome:{chat_api.WELCOME_DM_SENDER_ID}:new-user")
        self.assertEqual(payload["user_id"], chat_api.WELCOME_DM_SENDER_ID)
        self.assertEqual(payload["content"], chat_api.WELCOME_DM_TEXT)
        self.assertEqual(payload["author_name"], "Nest Team")
        emit_event.assert_called_once()
        self.assertEqual(emit_event.call_args.args[2], "message_created")

    def test_discord_upsert_emits_realtime_event_for_new_message(self):
        channel = {
            "$id": "nest_chat",
            "kind": "discord",
            "discord_channel_id": "discord-channel",
        }
        message = {
            "id": "discord-message-1",
            "content": "fresh from discord",
            "timestamp": "2026-05-26T22:00:00Z",
            "author": {"id": "author-1", "username": "UrbanPanda"},
        }

        with patch.object(chat_api, "get_row_safe", side_effect=[None, {"$id": "row-1"}]), \
                patch.object(chat_api, "first_row", return_value=None), \
                patch.object(chat_api, "insert_row_ignore_safe", return_value=True) as insert_row, \
                patch.object(chat_api, "emit_chat_event") as emit_event:
            row, created = chat_api._upsert_discord_message(channel, message, emit_event=True)

        self.assertTrue(created)
        self.assertEqual(row["$id"], "row-1")
        insert_row.assert_called_once()
        emit_event.assert_called_once()
        self.assertEqual(emit_event.call_args.args[:3], ("channel", "nest_chat", "message_created"))
        self.assertEqual(emit_event.call_args.kwargs["message_id"], "row-1")
        self.assertEqual(emit_event.call_args.kwargs["channel_id"], "nest_chat")

    def test_discord_upsert_emits_update_event_for_changed_existing_message(self):
        channel = {
            "$id": "nest_chat",
            "kind": "discord",
            "discord_channel_id": "discord-channel",
        }
        message = {
            "id": "discord-message-1",
            "content": "edited discord payload",
            "timestamp": "2026-05-26T22:00:00Z",
            "author": {"id": "author-1", "username": "UrbanPanda"},
        }

        with patch.object(chat_api, "get_row_safe", return_value={"$id": "existing-row"}), \
                patch.object(chat_api, "first_row", return_value={"$id": "existing-row"}), \
                patch.object(chat_api, "update_row_safe", return_value={"$id": "existing-row"}) as update_row, \
                patch.object(chat_api, "emit_chat_event") as emit_event:
            row, created = chat_api._upsert_discord_message(channel, message, emit_event=True)

        self.assertFalse(created)
        self.assertEqual(row["$id"], "existing-row")
        update_row.assert_called_once()
        emit_event.assert_called_once()
        self.assertEqual(emit_event.call_args.args[:3], ("channel", "nest_chat", "message_updated"))

    def test_discord_upsert_skips_update_for_unchanged_existing_message(self):
        channel = {
            "$id": "nest_chat",
            "kind": "discord",
            "discord_channel_id": "discord-channel",
        }
        message = {
            "id": "discord-message-1",
            "content": "same discord payload",
            "timestamp": "2026-05-26T22:00:00Z",
            "author": {"id": "author-1", "username": "UrbanPanda"},
        }
        existing = {
            "$id": "existing-row",
            **chat_api._discord_message_payload(channel, message),
            "updated_at": "2026-05-26T22:01:00Z",
        }

        with patch.object(chat_api, "get_row_safe", return_value=existing), \
                patch.object(chat_api, "first_row", return_value=existing), \
                patch.object(chat_api, "update_row_safe") as update_row, \
                patch.object(chat_api, "emit_chat_event") as emit_event:
            row, created = chat_api._upsert_discord_message(channel, message, emit_event=True)

        self.assertFalse(created)
        self.assertEqual(row["$id"], "existing-row")
        update_row.assert_not_called()
        emit_event.assert_not_called()

    def test_discord_upsert_updates_only_changed_fields_for_existing_message(self):
        channel = {
            "$id": "nest_chat",
            "kind": "discord",
            "discord_channel_id": "discord-channel",
        }
        original = {
            "id": "discord-message-1",
            "content": "old discord payload",
            "timestamp": "2026-05-26T22:00:00Z",
            "author": {"id": "author-1", "username": "UrbanPanda"},
        }
        edited = {
            **original,
            "content": "edited discord payload",
        }
        existing = {
            "$id": "existing-row",
            **chat_api._discord_message_payload(channel, original),
            "updated_at": "2026-05-26T22:01:00Z",
        }

        with patch.object(chat_api, "get_row_safe", return_value=existing), \
                patch.object(chat_api, "first_row", return_value=existing), \
                patch.object(chat_api, "update_row_safe", return_value={"$id": "existing-row"}) as update_row:
            row, created = chat_api._upsert_discord_message(channel, edited)

        self.assertFalse(created)
        self.assertEqual(row["$id"], "existing-row")
        update_row.assert_called_once()
        payload = update_row.call_args.args[2]
        self.assertEqual(set(payload), {"content", "rendered_html", "updated_at"})
        self.assertEqual(payload["content"], "edited discord payload")

    def test_discord_partial_update_does_not_wipe_missing_content(self):
        channel = {
            "$id": "nest_chat",
            "kind": "discord",
            "discord_channel_id": "discord-channel",
        }
        existing = {
            "$id": "existing-row",
            "channel_id": "nest_chat",
            "source": "discord",
            "external_id": "discord:discord-channel:discord-message-1",
            "discord_message_id": "discord-message-1",
            "content": "keep me",
            "rendered_html": "keep me",
            "created_at": "2026-05-26T22:00:00Z",
        }
        partial = {
            "id": "discord-message-1",
            "channel_id": "discord-channel",
            "edited_timestamp": "2026-05-26T22:02:00Z",
        }

        with patch.object(chat_api, "get_row_safe", return_value=existing), \
                patch.object(chat_api, "first_row", return_value=existing), \
                patch.object(chat_api, "update_row_safe", return_value={"$id": "existing-row"}) as update_row, \
                patch.object(chat_api, "emit_chat_event") as emit_event:
            row, created = chat_api._upsert_discord_message(channel, partial, emit_event=True, partial=True)

        self.assertFalse(created)
        self.assertEqual(row["$id"], "existing-row")
        payload = update_row.call_args.args[2]
        self.assertNotIn("content", payload)
        self.assertNotIn("rendered_html", payload)
        emit_event.assert_called_once()
        self.assertEqual(emit_event.call_args.args[:3], ("channel", "nest_chat", "message_updated"))

    def test_discord_delete_soft_deletes_and_emits_event(self):
        channel = {
            "$id": "nest_chat",
            "kind": "discord",
            "discord_channel_id": "discord-channel",
        }
        row = {
            "$id": "message-row",
            "channel_id": "nest_chat",
            "source": "discord",
            "external_id": "discord:discord-channel:discord-message-1",
            "discord_message_id": "discord-message-1",
        }

        with patch.object(chat_api, "first_row", return_value=row), \
                patch.object(chat_api, "update_row_safe", return_value=row) as update_row, \
                patch.object(chat_api, "emit_chat_event") as emit_event:
            result = chat_api._soft_delete_discord_message(channel, "discord-message-1", emit_event=True)

        self.assertEqual(result["$id"], "message-row")
        payload = update_row.call_args.args[2]
        self.assertEqual(payload["deleted_by"], "discord")
        self.assertIn("deleted_at", payload)
        emit_event.assert_called_once()
        self.assertEqual(emit_event.call_args.args[:3], ("channel", "nest_chat", "message_deleted"))

    def test_discord_delete_for_unconfigured_channel_is_silent(self):
        with patch.object(chat_api, "_discord_channel_for_discord_id", return_value=None), \
                patch.object(chat_api.logger, "warning") as warning:
            result = chat_api.delete_discord_gateway_message(
                "unconfigured-channel",
                "discord-message-1",
            )

        self.assertIsNone(result)
        warning.assert_not_called()

    def test_discord_message_payload_normalizes_schema_bounded_values(self):
        channel = {
            "$id": "nest_chat",
            "kind": "discord",
            "discord_channel_id": "discord-channel",
        }
        message = {
            "id": "discord-message-1",
            "content": "payload with media",
            "timestamp": "2026-05-26T22:00:00Z",
            "webhook_id": "w" * 100,
            "author": {
                "id": "author-1",
                "global_name": "D" * 200,
                "username": "u" * 100,
                "avatar": "avatarhash",
            },
            "embeds": [{
                "url": "https://example.test/" + ("x" * 3000),
                "title": "T" * 3000,
                "description": "D" * 3000,
                "provider": {"name": "Example"},
                "type": "link",
            }],
            "attachments": [{
                "filename": "schedule-" + ("x" * 3000) + ".png",
                "url": "https://cdn.discordapp.com/attachments/image.png",
                "content_type": "image/png",
                "width": 640,
                "height": 360,
            }],
        }

        payload = chat_api._discord_message_payload(channel, message)

        self.assertEqual(len(payload["author_name"]), 120)
        self.assertEqual(len(payload["author_username"]), 64)
        self.assertEqual(len(payload["discord_webhook_id"]), 32)
        self.assertLessEqual(len(payload["link_preview_json"]), chat_api.CHAT_MESSAGE_TEXT_LIMIT)
        media = json.loads(payload["link_preview_json"])
        self.assertEqual(media[1]["kind"], "discord_image")
        self.assertLessEqual(len(media[0]["title"]), 2048)

    def test_discord_upsert_returns_existing_when_existing_update_fails(self):
        channel = {
            "$id": "nest_chat",
            "kind": "discord",
            "discord_channel_id": "discord-channel",
        }
        original = {
            "id": "discord-message-1",
            "content": "old discord payload",
            "timestamp": "2026-05-26T22:00:00Z",
            "author": {"id": "author-1", "username": "UrbanPanda"},
        }
        edited = {
            **original,
            "content": "edited discord payload",
        }
        existing = {
            "$id": "existing-row",
            **chat_api._discord_message_payload(channel, original),
        }

        with patch.object(chat_api, "get_row_safe", return_value=existing), \
                patch.object(chat_api, "first_row", return_value=existing), \
                patch.object(chat_api, "update_row_safe", side_effect=chat_api.AppwriteException("Server Error")), \
                patch.object(chat_api.logger, "exception") as log_exception:
            row, created = chat_api._upsert_discord_message(channel, edited)

        self.assertEqual(row["$id"], "existing-row")
        self.assertFalse(created)
        log_exception.assert_not_called()

    def test_discord_ingest_endpoint_upserts_and_emits_for_bot_message(self):
        channel = {
            "$id": "nest_chat",
            "kind": "discord",
            "discord_channel_id": "discord-channel",
        }
        payload = {
            "message": {
                "id": "discord-message-2",
                "channel_id": "discord-channel",
                "content": "bot saw this first",
                "timestamp": "2026-05-26T22:01:00Z",
                "author": {"id": "author-1", "username": "UrbanPanda"},
            },
        }

        with self.app.test_request_context(
            "/api/chat/discord/messages",
            method="POST",
            json=payload,
            headers={"Authorization": "Bearer ingest-secret"},
        ):
            with patch.dict(os.environ, {"DISCORD_CHAT_INGEST_SECRET": "ingest-secret"}, clear=False), \
                    patch.object(chat_api, "first_row", side_effect=[channel, None]), \
                    patch.object(chat_api, "get_row_safe", side_effect=[None, {"$id": "row-2"}]), \
                    patch.object(chat_api, "insert_row_ignore_safe", return_value=True) as insert_row, \
                    patch.object(chat_api, "emit_chat_event") as emit_event:
                response = chat_api.discord_message_ingest()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["message_id"], "row-2")
        insert_row.assert_called_once()
        emit_event.assert_called_once()
        self.assertEqual(emit_event.call_args.args[:3], ("channel", "nest_chat", "message_created"))

    def test_discord_ingest_endpoint_requires_shared_secret(self):
        with self.app.test_request_context(
            "/api/chat/discord/messages",
            method="POST",
            json={"message": {"id": "discord-message-2", "channel_id": "discord-channel"}},
        ):
            with patch.dict(os.environ, {"DISCORD_CHAT_INGEST_SECRET": "ingest-secret"}, clear=False):
                response, status = chat_api.discord_message_ingest()

        self.assertEqual(status, 403)
        self.assertIn("unavailable", response.get_json()["error"])


if __name__ == "__main__":
    unittest.main()
