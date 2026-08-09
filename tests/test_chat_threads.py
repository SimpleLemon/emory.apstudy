import unittest
from unittest.mock import Mock

from services import chat_read_state, chat_threads


class _QueryStub:
    @staticmethod
    def equal(field, values):
        return ("equal", field, values)


class TestChatThreads(unittest.TestCase):
    def test_block_queries_preserve_directionality_and_fail_closed_check(self):
        rows = [{"blocked_id": "user-2"}, {"blocked_id": None}]
        list_rows = Mock(return_value=rows)
        blocked = chat_threads.blocked_user_ids(
            "user-1",
            list_rows_fn=list_rows,
            query_cls=_QueryStub,
            blocks_collection="chat_blocks",
            appwrite_exception=RuntimeError,
        )

        self.assertEqual(blocked, {"user-2"})
        list_rows.assert_called_once_with(
            "chat_blocks",
            [("equal", "blocker_id", ["user-1"])],
        )

        first_row = Mock(return_value={"$id": "block-1"})
        self.assertTrue(
            chat_threads.is_blocked_between(
                "user-1",
                "user-2",
                first_row_fn=first_row,
                query_cls=_QueryStub,
                blocks_collection="chat_blocks",
                appwrite_exception=RuntimeError,
                error_logger=Mock(),
            )
        )
        first_row.assert_called_once_with(
            "chat_blocks",
            [("equal", "block_key", ["user-1:user-2", "user-2:user-1"])],
        )

    def test_thread_identity_is_deterministic_and_existing_rows_are_idempotent(self):
        self.assertEqual(chat_threads.thread_key("user-2", "user-1"), "user-1:user-2")

        existing = {"$id": "thread-1"}
        first_row = Mock(return_value=existing)
        create_row = Mock()
        result = chat_threads.get_or_create_thread_between(
            "user-2",
            "user-1",
            thread_key_fn=chat_threads.thread_key,
            first_row_fn=first_row,
            query_cls=_QueryStub,
            threads_collection="chat_dm_threads",
            format_datetime_fn=lambda value: value,
            now_fn=lambda: "now",
            create_row_fn=create_row,
            id_unique_fn=lambda: "new-thread",
        )

        self.assertIs(result, existing)
        create_row.assert_not_called()

        first_row.return_value = None
        create_row.return_value = {"$id": "thread-2"}
        result = chat_threads.get_or_create_thread_between(
            "user-2",
            "user-1",
            thread_key_fn=chat_threads.thread_key,
            first_row_fn=first_row,
            query_cls=_QueryStub,
            threads_collection="chat_dm_threads",
            format_datetime_fn=lambda value: f"formatted:{value}",
            now_fn=lambda: "now",
            create_row_fn=create_row,
            id_unique_fn=lambda: "new-thread",
        )

        self.assertEqual(result["$id"], "thread-2")
        payload = create_row.call_args.kwargs["data"]
        self.assertEqual(payload["participant_a"], "user-1")
        self.assertEqual(payload["participant_b"], "user-2")
        self.assertEqual(payload["participant_key"], "user-1:user-2")
        self.assertEqual(payload["created_at"], "formatted:now")

    def test_thread_participant_authorization_and_self_dm_guard(self):
        thread = {"participant_a": "user-1", "participant_b": "user-2"}
        get_row = Mock(return_value=thread)
        self.assertEqual(
            chat_threads.thread_for_user(
                "thread-1",
                get_row_fn=get_row,
                threads_collection="chat_dm_threads",
                current_user_id_fn=lambda: "user-2",
            ),
            thread,
        )
        self.assertIsNone(
            chat_threads.thread_for_user(
                "thread-1",
                get_row_fn=get_row,
                threads_collection="chat_dm_threads",
                current_user_id_fn=lambda: "outsider",
            )
        )
        self.assertEqual(
            chat_threads.thread_participant_ids(thread),
            ["user-1", "user-2"],
        )

        with self.assertRaisesRegex(ValueError, "cannot start a DM"):
            chat_threads.get_or_create_thread(
                "user-1",
                current_user_id_fn=lambda: "user-1",
                get_row_fn=Mock(),
                users_collection="users",
                get_or_create_thread_between_fn=Mock(),
            )

    def test_onboarding_read_initialization_and_welcome_dm_use_callbacks(self):
        channels = [{"$id": "nest_chat", "kind": "discord"}]
        latest = {"$id": "message-1", "created_at": "2026-05-26T22:00:00Z"}
        persist = Mock()
        chat_read_state.initialize_new_user_discord_read_states(
            "new-user",
            default_channels_fn=Mock(),
            list_rows_all_fn=Mock(return_value=channels),
            query_cls=_QueryStub,
            channels_collection="chat_channels",
            row_id_fn=lambda row: row["$id"],
            latest_visible_message_fn=Mock(return_value=latest),
            persist_read_state_fn=persist,
            appwrite_exception=RuntimeError,
            error_logger=Mock(),
        )
        persist.assert_called_once_with("new-user", "channel", "nest_chat", latest)

        existing = {"$id": "welcome-1"}
        first_row = Mock(return_value=existing)
        self.assertIs(
            chat_threads.create_welcome_dm_for_user(
                "new-user",
                welcome_sender_id="system",
                welcome_text="Welcome",
                first_row_fn=first_row,
                query_cls=_QueryStub,
                messages_collection="chat_messages",
                get_row_fn=Mock(),
                users_collection="users",
                get_or_create_thread_between_fn=Mock(),
                create_row_fn=Mock(),
                id_unique_fn=Mock(),
                update_row_fn=Mock(),
                threads_collection="chat_dm_threads",
                row_id_fn=lambda row: row["$id"],
                now_fn=lambda: "now",
                format_datetime_fn=lambda value: value,
                render_markdown_fn=lambda value: f"<p>{value}</p>",
                emit_chat_event_fn=Mock(),
                thread_participant_ids_fn=Mock(),
                appwrite_exception=RuntimeError,
                error_logger=Mock(),
            ),
            existing,
        )


if __name__ == "__main__":
    unittest.main()
