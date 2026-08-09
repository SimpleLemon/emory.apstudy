import unittest
from unittest.mock import Mock

from flask import Flask

import blueprints.chat_api as chat_api
from services import chat_read_state


class _QueryStub:
    @staticmethod
    def equal(field, values):
        return ("equal", field, values)

    @staticmethod
    def greater_than(field, value):
        return ("greater_than", field, value)

    @staticmethod
    def less_than(field, value):
        return ("less_than", field, value)

    @staticmethod
    def order_desc(field):
        return ("order_desc", field)

    @staticmethod
    def limit(value):
        return ("limit", value)

    @staticmethod
    def offset(value):
        return ("offset", value)


def _dependencies(**overrides):
    values = {
        "collections": {
            "chat_messages": "chat_messages",
            "chat_channels": "chat_channels",
            "chat_read_states": "chat_read_states",
        },
        "appwrite_exception": RuntimeError,
        "query_cls": _QueryStub,
        "id_unique_fn": lambda: "new-read-state",
        "row_id_fn": lambda row: row.get("$id"),
        "now_fn": lambda: "now",
        "format_datetime_fn": lambda value: f"formatted:{value}",
        "current_user_id_fn": lambda: "user-1",
        "get_row_fn": Mock(return_value=None),
        "first_row_fn": Mock(return_value=None),
        "create_row_fn": Mock(return_value={"$id": "created-state"}),
        "update_row_fn": Mock(return_value={"$id": "updated-state"}),
        "delete_row_fn": Mock(),
        "list_rows_fn": Mock(return_value={"rows": []}),
        "read_key_fn": chat_read_state.read_key,
        "message_timestamp_fn": lambda row: row.get("created_at") or "",
        "message_scope_field_fn": chat_read_state.message_scope_field,
        "message_in_scope_fn": chat_read_state.message_in_scope,
        "message_visible_for_user_fn": chat_read_state.message_visible_for_user,
        "message_can_be_unread_target_fn": chat_read_state.message_can_be_unread_target,
        "blocked_user_ids_fn": Mock(return_value=set()),
        "thread_for_user_fn": Mock(return_value={"$id": "thread-1"}),
        "can_access_channel_fn": Mock(return_value=True),
        "latest_visible_message_fn": Mock(return_value=None),
        "persist_read_state_fn": Mock(return_value={"$id": "persisted"}),
        "read_state_for_scope_fn": Mock(return_value={"$id": "existing"}),
        "latest_unread_target_fn": Mock(return_value=None),
        "previous_visible_message_fn": Mock(return_value=None),
        "clear_read_state_fn": Mock(return_value=None),
        "error_logger": Mock(),
        "summary_scan_limit": 2,
        "unread_cap": 99,
    }
    values.update(overrides)
    return chat_read_state.ChatReadStateDependencies(**values)


class TestChatReadState(unittest.TestCase):
    def test_registered_chat_blueprint_keeps_read_routes_and_adapter_symbols(self):
        app = Flask(__name__)
        app.register_blueprint(chat_api.chat_api_bp)
        chat_rules = [
            rule
            for rule in app.url_map.iter_rules()
            if rule.endpoint.startswith("chat_api.")
        ]
        routes = {
            rule.rule: (rule.methods - {"HEAD", "OPTIONS"}, rule.endpoint)
            for rule in chat_rules
        }

        self.assertEqual(len(chat_rules), 25)
        self.assertEqual(
            routes["/api/chat/read"],
            ({"POST"}, "chat_api.mark_chat_read"),
        )
        self.assertEqual(
            routes["/api/chat/unread"],
            ({"POST"}, "chat_api.mark_chat_unread"),
        )
        for symbol in (
            "_read_state_for_scope",
            "_persist_read_state",
            "_mark_read",
            "_mark_unread",
            "_unread_count",
            "_blocked_user_ids",
            "_thread_for_user",
        ):
            self.assertTrue(callable(getattr(chat_api, symbol)))

    def test_unread_count_filters_self_deleted_blocked_and_paginates(self):
        pages = [
            {
                "rows": [
                    {"$id": "own", "user_id": "user-1"},
                    {"$id": "blocked", "user_id": "blocked-user"},
                ]
            },
            {
                "rows": [
                    {"$id": "deleted", "user_id": "user-2", "deleted_at": "deleted"},
                    {"$id": "visible", "user_id": "user-2"},
                ]
            },
            {"rows": [{"$id": "last", "user_id": "user-3"}]},
        ]
        list_rows = Mock(side_effect=pages)
        dependencies = _dependencies(
            list_rows_fn=list_rows,
            blocked_user_ids_fn=Mock(return_value={"blocked-user"}),
        )

        unread, capped = chat_read_state.unread_count(
            "thread",
            "thread-1",
            "user-1",
            "2026-05-26T22:00:00Z",
            dependencies=dependencies,
        )

        self.assertEqual(unread, 2)
        self.assertFalse(capped)
        self.assertEqual(list_rows.call_count, 3)
        first_queries = list_rows.call_args_list[0].args[1]
        self.assertIn(
            ("greater_than", "created_at", "2026-05-26T22:00:00Z"),
            first_queries,
        )
        self.assertIn(("offset", 2), list_rows.call_args_list[1].args[1])

    def test_unread_count_honors_the_99_message_cap(self):
        rows = [{"$id": f"message-{index}", "user_id": "user-2"} for index in range(99)]
        dependencies = _dependencies(
            summary_scan_limit=100,
            unread_cap=99,
            list_rows_fn=Mock(return_value={"rows": rows}),
        )

        unread, capped = chat_read_state.unread_count(
            "channel",
            "nest_chat",
            "user-1",
            None,
            dependencies=dependencies,
        )

        self.assertEqual((unread, capped), (99, True))

    def test_mark_unread_persists_previous_visible_message_boundary(self):
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
        persist = Mock(return_value={"$id": "read-state-1"})
        dependencies = _dependencies(
            get_row_fn=Mock(return_value=target),
            previous_visible_message_fn=Mock(return_value=previous),
            persist_read_state_fn=persist,
        )

        row = chat_read_state.mark_unread(
            "channel",
            "nest_chat",
            message_id="target",
            dependencies=dependencies,
        )

        self.assertEqual(row["$id"], "read-state-1")
        persist.assert_called_once_with(
            "user-1",
            "channel",
            "nest_chat",
            previous,
            fallback_to_now=False,
        )

    def test_persist_read_state_keeps_message_timestamp_and_now_fallback(self):
        update = Mock(return_value={"$id": "updated"})
        dependencies = _dependencies(
            first_row_fn=Mock(return_value={"$id": "state-1"}),
            update_row_fn=update,
        )
        message = {"$id": "message-1", "created_at": "2026-05-26T22:00:00Z"}

        chat_read_state.persist_read_state(
            "user-1",
            "channel",
            "nest_chat",
            message,
            dependencies=dependencies,
        )
        payload = update.call_args.args[2]
        self.assertEqual(payload["last_read_message_id"], "message-1")
        self.assertEqual(payload["last_read_at"], "2026-05-26T22:00:00Z")

        create = Mock(return_value={"$id": "created"})
        dependencies = _dependencies(first_row_fn=Mock(return_value=None), create_row_fn=create)
        chat_read_state.persist_read_state(
            "user-1",
            "channel",
            "nest_chat",
            None,
            dependencies=dependencies,
        )
        self.assertEqual(create.call_args.kwargs["data"]["last_read_at"], "formatted:now")


if __name__ == "__main__":
    unittest.main()
