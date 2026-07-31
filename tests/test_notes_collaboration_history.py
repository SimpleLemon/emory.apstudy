import unittest
from unittest.mock import patch

from services import database, notes_collaboration
from tests.test_notes_collaboration import (
    CollaborationDatabaseTestCase,
    NEXT_MONTH,
    NEXT_WEEK,
    NOW,
)


class NotesCollaborationHistoryTests(CollaborationDatabaseTestCase):
    def test_comments_include_safe_authors_replies_and_resolution_notifications(self):
        with database.db_connection(self.path) as conn:
            conn.executemany(
                """
                INSERT INTO note_access_grants (
                    id, owner_user_id, resource_type, resource_id, principal_type,
                    principal_id, access_level, granted_by_user_id, created_at, updated_at
                ) VALUES (?, 'owner', 'note', 'note-1', 'user', ?, ?, 'owner', ?, ?)
                """,
                [
                    ("editor-grant", "editor", "editor", NOW, NOW),
                    ("reviewer-grant", "reviewer", "reviewer", NOW, NOW),
                    ("viewer-grant", "viewer", "viewer", NOW, NOW),
                ],
            )

        with patch.object(notes_collaboration, "utcnow_iso", return_value=NOW):
            thread = notes_collaboration.create_comment(
                "note-1",
                "owner",
                {"body": "  Please review  ", "quoted_text": "quoted"},
            )
            reply = notes_collaboration.reply_to_comment(
                "note-1", thread["id"], "editor", "  I reviewed it.  "
            )
            resolved = notes_collaboration.set_comment_status(
                "note-1", thread["id"], "editor", "resolved"
            )

        self.assertEqual(thread["body"], "Please review")
        self.assertEqual(thread["author"]["id"], "owner")
        self.assertEqual(reply["replies"][0]["body"], "I reviewed it.")
        self.assertEqual(resolved["status"], "resolved")
        self.assertEqual(resolved["resolved_by"]["id"], "editor")
        notification_types = [
            row["notification_type"]
            for row in self.rows("SELECT notification_type FROM user_notifications ORDER BY rowid")
        ]
        self.assertEqual(
            notification_types,
            ["note_comment_created", "note_comment_created", "note_comment_reply", "note_comment_resolved"],
        )
        self.assertIsNone(notes_collaboration.reply_to_comment("note-1", "missing", "editor", "body"))
        self.assertIsNone(notes_collaboration.set_comment_status("note-1", "missing", "editor", "resolved"))

    def test_comment_validation_rejects_empty_oversized_and_unknown_status(self):
        for payload in ({"body": ""}, {"body": "x" * 5001}):
            with self.subTest(length=len(payload["body"])):
                with self.assertRaises(ValueError):
                    notes_collaboration.create_comment("note-1", "owner", payload)
        with self.assertRaises(ValueError):
            notes_collaboration.reply_to_comment("note-1", "missing", "owner", "")
        with self.assertRaises(ValueError):
            notes_collaboration.set_comment_status("note-1", "missing", "owner", "closed")

    def test_store_document_validates_binary_boundary_upserts_revision_and_updates_projection(self):
        with self.assertRaises(ValueError):
            notes_collaboration.store_collaboration_document("note-1", "text")
        with self.assertRaises(ValueError):
            notes_collaboration.store_collaboration_document("note-1", b"x" * (10 * 1024 * 1024 + 1))
        self.assertIsNone(notes_collaboration.store_collaboration_document("missing", b"bytes"))

        with patch.object(notes_collaboration, "utcnow_iso", return_value=NOW):
            first = notes_collaboration.store_collaboration_document(
                "note-1",
                bytearray(b"\x00\xffbinary"),
                title="  ",
                content='[{"type":"paragraph"}]',
                page_setup_json='{"zoom": 1.25}',
                schema_version=2,
            )
            second = notes_collaboration.store_collaboration_document("note-1", b"next")

        self.assertEqual(first["durable_revision"], 1)
        self.assertEqual(second["durable_revision"], 2)
        document = notes_collaboration.get_collaboration_document("note-1")
        self.assertEqual(document["ydoc_blob"], b"next")
        self.assertEqual(document["schema_version"], 1)
        note = self.row("SELECT title, content, page_setup_json, collaboration_enabled, preview_text FROM notes WHERE id = 'note-1'")
        self.assertEqual(note["title"], "Untitled")
        self.assertEqual(note["content"], '[{"type":"paragraph"}]')
        self.assertEqual(note["page_setup_json"], '{"zoom": 1.25}')
        self.assertEqual(note["collaboration_enabled"], 1)
        self.assertTrue(note["preview_text"])
        self.assertIsNone(notes_collaboration.get_collaboration_document("missing"))

    def test_versions_hide_binary_payloads_and_restore_note_projection(self):
        with patch.object(notes_collaboration, "utcnow_iso", return_value=NOW), patch.object(
            notes_collaboration, "_iso_after", return_value=NEXT_MONTH
        ):
            notes_collaboration.store_collaboration_document(
                "note-1", b"version-bytes", title="Current", content="current", page_setup_json="current-setup"
            )
            version = notes_collaboration.create_version("note-1", "editor", reason="manual", name="Checkpoint")

        self.assertEqual(version["name"], "Checkpoint")
        self.assertEqual(version["actor"]["id"], "editor")
        self.assertNotIn("ydoc_blob", version)
        self.assertNotIn("ydoc_blob", notes_collaboration.get_version("note-1", version["id"]))
        self.assertEqual(notes_collaboration.list_versions("note-1")[0]["id"], version["id"])
        self.assertIsNone(notes_collaboration.get_version("note-1", "missing"))

        with database.db_connection(self.path) as conn:
            conn.execute("UPDATE notes SET title = 'Changed', content = 'changed' WHERE id = 'note-1'")

        with patch.object(notes_collaboration, "utcnow_iso", return_value=NEXT_WEEK), patch.object(
            notes_collaboration, "_iso_after", return_value=NEXT_MONTH
        ):
            restored = notes_collaboration.restore_version("note-1", version["id"], "editor")

        self.assertEqual(restored["title"], "Current")
        self.assertEqual(restored["content"], "current")
        document = notes_collaboration.get_collaboration_document("note-1")
        self.assertEqual(document["ydoc_blob"], b"version-bytes")
        self.assertEqual(document["durable_revision"], 2)
        self.assertEqual(self.row("SELECT COUNT(*) AS count FROM note_versions")["count"], 2)
        self.assertIsNone(notes_collaboration.create_version("missing", "editor"))

    def test_transfer_note_requires_editor_and_moves_pending_ownership(self):
        with database.db_connection(self.path) as conn:
            conn.execute(
                """
                INSERT INTO note_access_grants (
                    id, owner_user_id, resource_type, resource_id, principal_type,
                    principal_id, access_level, granted_by_user_id, created_at, updated_at
                ) VALUES ('editor-grant', 'owner', 'note', 'note-1', 'user', 'editor', 'editor', 'owner', ?, ?)
                """,
                [NOW, NOW],
            )

        with patch.object(notes_collaboration, "utcnow_iso", return_value=NOW), patch.object(
            notes_collaboration, "_iso_after", return_value=NEXT_MONTH
        ):
            notes_collaboration.replace_pending_invitations(
                "note", "note-1", "owner", [{"email": "pending@example.test", "role": "viewer"}], "owner"
            )
            transferred = notes_collaboration.transfer_note("note-1", "owner", "editor")

        self.assertEqual(transferred["user_id"], "editor")
        self.assertIsNone(transferred["folder_id"])
        self.assertEqual(
            self.row("SELECT owner_user_id FROM note_share_invitations WHERE resource_id = 'note-1'")["owner_user_id"],
            "editor",
        )
        self.assertEqual(
            self.row("SELECT COUNT(*) AS count FROM note_versions WHERE reason = 'before_transfer'")["count"],
            1,
        )
        self.assertEqual(
            self.row("SELECT notification_type FROM user_notifications WHERE user_id = 'editor'")["notification_type"],
            "note_ownership_transferred",
        )
        with self.assertRaises(ValueError):
            notes_collaboration.transfer_note("note-1", "editor", "viewer")
        self.assertIsNone(notes_collaboration.transfer_note("missing", "owner", "editor"))

    def test_transfer_folder_updates_child_notes_and_snapshots_each_child(self):
        with database.db_connection(self.path) as conn:
            conn.execute(
                """
                INSERT INTO note_access_grants (
                    id, owner_user_id, resource_type, resource_id, principal_type,
                    principal_id, access_level, granted_by_user_id, created_at, updated_at
                ) VALUES ('folder-editor', 'owner', 'folder', 'folder-1', 'user', 'editor', 'editor', 'owner', ?, ?)
                """,
                [NOW, NOW],
            )
            conn.execute(
                """
                INSERT INTO notes (
                    id, user_id, folder_id, title, content, page_setup_json,
                    "order", created_at, updated_at
                ) VALUES ('note-3', 'owner', 'folder-1', 'Second Child', 'second', '{}', 2, ?, ?)
                """,
                [NOW, NOW],
            )

        with patch.object(notes_collaboration, "utcnow_iso", return_value=NOW), patch.object(
            notes_collaboration, "_iso_after", return_value=NEXT_MONTH
        ):
            transferred = notes_collaboration.transfer_folder("folder-1", "owner", "editor")

        self.assertEqual(transferred["user_id"], "editor")
        self.assertEqual(
            {
                row["user_id"]
                for row in self.rows("SELECT user_id FROM notes WHERE folder_id = 'folder-1'")
            },
            {"editor"},
        )
        self.assertEqual(
            self.row("SELECT COUNT(*) AS count FROM note_versions WHERE reason = 'before_transfer'")["count"],
            2,
        )
        self.assertEqual(
            self.row("SELECT notification_type FROM user_notifications WHERE user_id = 'editor'")["notification_type"],
            "note_folder_ownership_transferred",
        )
        with self.assertRaises(ValueError):
            notes_collaboration.transfer_folder("folder-1", "editor", "viewer")
        self.assertIsNone(notes_collaboration.transfer_folder("missing", "owner", "editor"))

    def test_cleanup_expires_invitations_and_deletes_only_expired_versions(self):
        with database.db_connection(self.path) as conn:
            conn.execute(
                """
                INSERT INTO note_share_invitations (
                    id, owner_user_id, resource_type, resource_id, email_normalized,
                    email_display, access_level, invited_by_user_id, status,
                    expires_at, created_at, updated_at
                ) VALUES ('expired', 'owner', 'note', 'note-1', 'expired@example.test',
                          'expired@example.test', 'viewer', 'owner', 'pending', ?, ?, ?)
                """,
                ["2026-07-30T00:00:00Z", NOW, NOW],
            )
            for version_id, title, content, expires_at in (
                ("expired-version", "Old", "old", "2026-07-30T00:00:00Z"),
                ("active-version", "New", "new", NEXT_MONTH),
            ):
                conn.execute(
                    """
                    INSERT INTO note_versions (
                        id, note_id, actor_user_id, reason, title, content,
                        page_setup_json, durable_revision, created_at, expires_at
                    ) VALUES (?, 'note-1', 'owner', 'automatic', ?, ?, '{}', 0, ?, ?)
                    """,
                    [version_id, title, content, NOW, expires_at],
                )

        with patch.object(notes_collaboration, "utcnow_iso", return_value=NOW):
            result = notes_collaboration.cleanup_expired_collaboration_rows()

        self.assertEqual(result, {"invitations_expired": 1, "versions_deleted": 1})
        self.assertEqual(self.row("SELECT status FROM note_share_invitations WHERE id = 'expired'")["status"], "expired")
        self.assertEqual(
            [row["id"] for row in self.rows("SELECT id FROM note_versions")],
            ["active-version"],
        )


if __name__ == "__main__":
    unittest.main()
