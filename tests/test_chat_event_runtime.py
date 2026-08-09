import json
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from flask import Flask

from extensions import login_manager
import blueprints.chat_api as chat_api
from services import chat_event_runtime
from tests.support.harness import reset_flask_login_manager


class _QueryStub:
    @staticmethod
    def greater_than_equal(field, value):
        return ("greater_than_equal", field, value)

    @staticmethod
    def order_asc(field):
        return ("order_asc", field)

    @staticmethod
    def limit(value):
        return ("limit", value)


class _StreamUser:
    def __init__(self, user_id="user-1"):
        self.id = user_id
        self.school = "Emory University"
        self.school_key = "emory-university"

    @property
    def is_authenticated(self):
        return True

    @property
    def is_active(self):
        return True

    @property
    def is_anonymous(self):
        return False

    def get_id(self):
        return self.id


class TestChatEventRuntime(unittest.TestCase):
    def test_event_visibility_injects_channel_thread_and_university_access(self):
        user = SimpleNamespace(school="Emory University", school_key="emory-university")
        get_row = Mock(return_value={"$id": "channel-1"})
        can_access_channel = Mock(return_value=True)
        thread_accessible = Mock(return_value=False)

        self.assertTrue(
            chat_event_runtime.event_visible_for_user(
                {"scope_type": "channel", "scope_id": "channel-1"},
                current_user_fn=lambda: user,
                current_user_id_fn=lambda: "user-1",
                get_row_fn=get_row,
                channels_collection="chat_channels",
                can_access_channel_fn=can_access_channel,
                thread_accessible_by_user_fn=thread_accessible,
                school_payload_fn=lambda school: {"school_key": "emory-university"},
            )
        )
        can_access_channel.assert_called_once_with({"$id": "channel-1"})
        get_row.assert_called_once_with("chat_channels", "channel-1", allow_missing=True)

        self.assertFalse(
            chat_event_runtime.event_visible_for_user(
                {"scope_type": "thread", "scope_id": "thread-1"},
                current_user_fn=lambda: user,
                current_user_id_fn=lambda: "user-1",
                get_row_fn=get_row,
                channels_collection="chat_channels",
                can_access_channel_fn=can_access_channel,
                thread_accessible_by_user_fn=thread_accessible,
                school_payload_fn=lambda school: {"school_key": "emory-university"},
            )
        )
        thread_accessible.assert_called_once_with("thread-1", "user-1")

        self.assertTrue(
            chat_event_runtime.event_visible_for_user(
                {"scope_type": "university", "scope_id": "emory-university"},
                current_user_fn=lambda: user,
                current_user_id_fn=lambda: "user-1",
                get_row_fn=get_row,
                channels_collection="chat_channels",
                can_access_channel_fn=can_access_channel,
                thread_accessible_by_user_fn=thread_accessible,
                school_payload_fn=lambda school: {"school_key": "emory-university"},
            )
        )
        self.assertFalse(
            chat_event_runtime.event_visible_for_user(
                {"scope_type": "unknown", "scope_id": "scope-1"},
                current_user_fn=lambda: user,
                current_user_id_fn=lambda: "user-1",
                get_row_fn=get_row,
                channels_collection="chat_channels",
                can_access_channel_fn=can_access_channel,
                thread_accessible_by_user_fn=thread_accessible,
                school_payload_fn=lambda school: {"school_key": "emory-university"},
            )
        )

    def test_serialize_chat_event_preserves_sse_payload_shape(self):
        row = {
            "$id": "event-1",
            "scope_type": "channel",
            "scope_id": "nest_chat",
            "event_type": "message_created",
            "message_id": "message-1",
            "thread_id": None,
            "channel_id": "nest_chat",
            "actor_id": "user-1",
            "created_at": "2026-08-02T10:00:00Z",
        }

        self.assertEqual(
            chat_event_runtime.serialize_chat_event(row, row_id_fn=lambda value: value["$id"]),
            {
                "$id": "event-1",
                "id": "event-1",
                "scope_type": "channel",
                "scope_id": "nest_chat",
                "event_type": "message_created",
                "message_id": "message-1",
                "thread_id": None,
                "channel_id": "nest_chat",
                "actor_id": "user-1",
                "created_at": "2026-08-02T10:00:00Z",
            },
        )

    def test_list_events_filters_invisible_rows_and_resumes_same_timestamp_after_id(self):
        timestamp = "2026-08-02T10:00:00Z"
        rows = [
            {"$id": "event-a", "created_at": timestamp},
            {"$id": "event-hidden", "created_at": timestamp},
            {"$id": "event-b", "created_at": timestamp},
        ]
        list_rows = Mock(return_value={"rows": rows})
        visible = chat_event_runtime.list_chat_events_after(
            timestamp,
            "event-a",
            limit=25,
            query_cls=_QueryStub,
            list_rows_fn=list_rows,
            events_collection="chat_events",
            appwrite_exception=RuntimeError,
            error_logger=Mock(),
            event_visible_for_user_fn=lambda row: row["$id"] != "event-hidden",
            row_id_fn=lambda row: row["$id"],
        )

        self.assertEqual([row["$id"] for row in visible], ["event-b"])
        self.assertEqual(
            list_rows.call_args.args,
            (
                "chat_events",
                [
                    ("greater_than_equal", "created_at", timestamp),
                    ("order_asc", "created_at"),
                    ("order_asc", "$id"),
                    ("limit", 25),
                ],
            ),
        )

    def test_list_events_logs_and_returns_empty_on_persistence_error(self):
        error_logger = Mock()
        list_rows = Mock(side_effect=RuntimeError("database unavailable"))

        result = chat_event_runtime.list_chat_events_after(
            limit=10,
            query_cls=_QueryStub,
            list_rows_fn=list_rows,
            events_collection="chat_events",
            appwrite_exception=RuntimeError,
            error_logger=error_logger,
            event_visible_for_user_fn=Mock(),
            row_id_fn=lambda row: row["$id"],
        )

        self.assertEqual(result, [])
        error_logger.exception.assert_called_once_with("Failed to list chat events")

    def test_blueprint_event_list_adapter_keeps_visibility_patch_target(self):
        row = {"$id": "event-1", "created_at": "2026-08-02T10:00:00Z"}
        with patch.object(chat_api, "list_rows_safe", return_value={"rows": [row]}), \
                patch.object(chat_api, "_event_visible_for_user", return_value=False) as visible:
            result = chat_api._list_chat_events_after(row["created_at"])

        self.assertEqual(result, [])
        visible.assert_called_once_with(row)


class TestRegisteredChatEventStream(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.secret_key = "test"
        self.app.config.update(TESTING=True, WTF_CSRF_CHECK_DEFAULT=False)
        self.user = _StreamUser()

        previous_loader = login_manager._user_callback
        previous_unauthorized = login_manager.unauthorized_callback
        previous_login_view = login_manager.login_view
        self.addCleanup(setattr, login_manager, "_user_callback", previous_loader)
        self.addCleanup(setattr, login_manager, "unauthorized_callback", previous_unauthorized)
        self.addCleanup(setattr, login_manager, "login_view", previous_login_view)
        self.addCleanup(reset_flask_login_manager)
        reset_flask_login_manager()
        login_manager.init_app(self.app)
        login_manager._user_callback = lambda user_id: self.user if user_id == self.user.id else None
        self.app.register_blueprint(chat_api.chat_api_bp)
        self.addCleanup(self._clear_listeners)

    def _authenticated_client(self):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = self.user.id
            session["_fresh"] = True
        return client

    @staticmethod
    def _event(event_id, created_at="2026-08-02T10:00:00Z"):
        return {
            "$id": event_id,
            "scope_type": "channel",
            "scope_id": "nest_chat",
            "event_type": "message_created",
            "message_id": f"message-{event_id}",
            "thread_id": None,
            "channel_id": "nest_chat",
            "actor_id": "user-1",
            "created_at": created_at,
        }

    @staticmethod
    def _chunk_payload(chunk):
        text = chunk.decode() if isinstance(chunk, bytes) else chunk
        assert text.startswith("data: ")
        return json.loads(text[len("data: "):-2])

    def _clear_listeners(self):
        with chat_api._chat_event_listener_lock:
            chat_api._chat_event_listeners.clear()

    def _listener_count(self):
        with chat_api._chat_event_listener_lock:
            return len(chat_api._chat_event_listeners)

    def _wait_for_listener(self):
        deadline = time.time() + 2
        while self._listener_count() == 0 and time.time() < deadline:
            time.sleep(0.005)
        self.assertEqual(self._listener_count(), 1)

    def test_registered_stream_emits_first_event_with_exact_headers_and_payload(self):
        event = self._event("event-1")
        client = self._authenticated_client()
        with patch.object(chat_api, "_list_chat_events_after", return_value=[event]):
            response = client.get("/api/chat/events/stream", buffered=False)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["Content-Type"], "text/event-stream; charset=utf-8")
            self.assertEqual(response.headers["Cache-Control"], "no-cache")
            self.assertEqual(response.headers["X-Accel-Buffering"], "no")
            self.assertEqual(response.headers["Connection"], "keep-alive")

            payload = self._chunk_payload(next(response.response))
            self.assertEqual(payload["$id"], "event-1")
            self.assertEqual(payload["id"], "event-1")
            self.assertEqual(payload["event_type"], "message_created")
            self.assertEqual(payload["message_id"], "message-event-1")
            self.assertEqual(self._listener_count(), 1)
            response.close()

        self.assertEqual(self._listener_count(), 0)

    def test_registered_stream_passes_reconnect_cursor_to_event_query(self):
        timestamp = "2026-08-02T10:00:00Z"
        event = self._event("event-b", timestamp)
        client = self._authenticated_client()
        with patch.object(chat_api, "_list_chat_events_after", return_value=[event]) as list_events:
            response = client.get(
                "/api/chat/events/stream?since=2026-08-02T10:00:00Z&after_id=event-a",
                buffered=False,
            )
            self.assertEqual(self._chunk_payload(next(response.response))["id"], "event-b")
            list_events.assert_called_once_with(timestamp, "event-a")
            response.close()

    def test_registered_stream_filters_invisible_channel_thread_and_university_events(self):
        rows = [
            {**self._event("hidden-channel"), "scope_id": "private-channel"},
            {**self._event("hidden-thread"), "scope_type": "thread", "scope_id": "thread-1"},
            {**self._event("hidden-university"), "scope_type": "university", "scope_id": "other-school"},
            self._event("visible-channel"),
        ]

        def get_row(_collection, scope_id, allow_missing=True):
            if scope_id == "nest_chat":
                return {"$id": scope_id, "kind": "discord"}
            return {"$id": scope_id, "kind": "unsupported"}

        client = self._authenticated_client()
        with patch.object(chat_api, "list_rows_safe", return_value={"rows": rows}), \
                patch.object(chat_api, "get_row_safe", side_effect=get_row), \
                patch.object(chat_api, "_thread_accessible_by_user", return_value=False):
            response = client.get("/api/chat/events/stream", buffered=False)
            payload = self._chunk_payload(next(response.response))
            self.assertEqual(payload["id"], "visible-channel")
            response.close()

    def test_registered_stream_emits_keepalive_after_configured_interval(self):
        client = self._authenticated_client()
        with patch.object(chat_api, "_list_chat_events_after", return_value=[]), \
                patch.object(chat_api.time, "monotonic", side_effect=[100.0, 116.0]), \
                patch.object(chat_api, "CHAT_EVENTS_KEEPALIVE_SECONDS", 15), \
                patch.object(chat_api, "CHAT_EVENTS_POLL_SECONDS", 1):
            response = client.get("/api/chat/events/stream", buffered=False)
            chunk = next(response.response)
            self.assertEqual(chunk.decode() if isinstance(chunk, bytes) else chunk, ": keepalive\n\n")
            response.close()

    def test_condition_notification_wakes_registered_stream(self):
        initial_event = self._event("event-initial")
        event = self._event("event-after-wakeup")
        calls = []

        def list_events(*args):
            calls.append(args)
            if len(calls) == 1:
                return [initial_event]
            if len(calls) == 2:
                return []
            return [event]

        waiting = threading.Event()
        wake_event = threading.Event()

        class SignalingCondition:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_value, traceback):
                return False

            def wait(self, timeout=None):
                waiting.set()
                return wake_event.wait(timeout)

            def notify_all(self):
                wake_event.set()

        client = self._authenticated_client()
        with patch.object(chat_api, "_list_chat_events_after", side_effect=list_events), \
                patch.object(chat_api, "CHAT_EVENTS_KEEPALIVE_SECONDS", 300), \
                patch.object(chat_api, "CHAT_EVENTS_POLL_SECONDS", 60):
            with patch.object(chat_api.threading, "Condition", SignalingCondition):
                response = client.get("/api/chat/events/stream", buffered=False)
            result = {}
            self.assertEqual(self._chunk_payload(next(response.response))["id"], "event-initial")

            def consume_one_chunk():
                try:
                    result["chunk"] = next(response.response)
                except BaseException as exc:  # pragma: no cover - reports cross-thread failures below
                    result["error"] = exc

            worker = threading.Thread(target=consume_one_chunk)
            worker.start()
            self.assertTrue(waiting.wait(2), result)
            chat_api._notify_chat_event_waiters()
            worker.join(2)
            self.assertFalse(worker.is_alive())
            self.assertNotIn("error", result)
            self.assertEqual(self._chunk_payload(result["chunk"])["id"], "event-after-wakeup")
            self.assertEqual(len(calls), 3)
            self.assertEqual(calls[1][1], "event-initial")
            self.assertEqual(calls[2][1], "event-initial")
            response.close()

    def test_generator_close_removes_listener_after_disconnect(self):
        client = self._authenticated_client()
        with patch.object(chat_api, "_list_chat_events_after", return_value=[self._event("event-1")]):
            response = client.get("/api/chat/events/stream", buffered=False)
            next(response.response)
            self.assertEqual(self._listener_count(), 1)
            response.close()

        self.assertEqual(self._listener_count(), 0)


if __name__ == "__main__":
    unittest.main()
