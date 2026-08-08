import unittest
from unittest.mock import patch

import blueprints.admin as admin


class TestAdminChatHistory(unittest.TestCase):
    def test_section_dispatch_keeps_loader_patch_effective(self):
        expected = {"messages": [{"$id": "message-1"}]}
        with patch.object(admin, "load_chat_section", return_value=expected) as load_chat:
            payload = admin._load_section("chat", "user-1")

        self.assertEqual(payload, expected)
        load_chat.assert_called_once()
        self.assertEqual(load_chat.call_args.args[0], "user-1")

    def test_count_rows_falls_back_to_full_list_when_total_missing(self):
        with patch.object(admin, "list_rows_safe", return_value={"rows": [{"$id": "one"}]}), \
                patch.object(admin, "list_rows_all", return_value=[{"$id": "one"}, {"$id": "two"}, {"$id": "three"}]):
            self.assertEqual(admin._count_rows("any_table", []), 3)

    def test_chat_count_summary_includes_deleted_messages_threads_and_blocks(self):
        messages = [
            {"$id": "visible", "user_id": "user-1"},
            {"$id": "deleted", "user_id": "user-1", "deleted_at": "2026-05-23T22:00:00Z"},
        ]
        with patch.object(admin, "_user_chat_messages", return_value=messages), \
                patch.object(admin, "_user_dm_threads", return_value=[{"$id": "thread-1"}]), \
                patch.object(admin, "_user_chat_blocks", return_value=[{"$id": "block-1"}, {"$id": "block-2"}]):
            summary = admin._chat_count_summary("user-1")

        self.assertEqual(summary["chat_messages"], 2)
        self.assertEqual(summary["deleted_chat_messages"], 1)
        self.assertEqual(summary["dm_threads"], 1)
        self.assertEqual(summary["chat_blocks"], 2)


if __name__ == "__main__":
    unittest.main()
