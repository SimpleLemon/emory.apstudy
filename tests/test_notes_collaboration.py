import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from services import database, notes_collaboration


NOW = "2026-07-31T12:00:00Z"
NEXT_WEEK = "2026-08-07T12:00:00Z"
NEXT_MONTH = "2026-08-30T12:00:00Z"


class CollaborationDatabaseTestCase(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(handle)
        self.addCleanup(lambda: Path(self.path).unlink(missing_ok=True))
        self.database_env = patch.dict(os.environ, {"DATABASE_PATH": self.path}, clear=False)
        self.database_env.start()
        self.addCleanup(self.database_env.stop)

        database.init_db(path=self.path)
        with database.db_connection(self.path) as conn:
            conn.executemany(
                """
                INSERT INTO users (
                    id, google_id, email, name, username, picture_url, created_at
                ) VALUES (?, ?, ?, ?, ?, '', ?)
                """,
                [
                    ("owner", "google-owner", "owner@example.test", "Owner Name", "owner", NOW),
                    ("editor", "google-editor", "editor@example.test", "Editor Name", "editor", NOW),
                    ("reviewer", "google-reviewer", "reviewer@example.test", "Reviewer Name", "reviewer", NOW),
                    ("viewer", "google-viewer", "viewer@example.test", "Viewer Name", "viewer", NOW),
                ],
            )
            conn.execute(
                """
                INSERT INTO note_folders (id, user_id, name, "order", created_at, updated_at)
                VALUES ('folder-1', 'owner', 'Shared Folder', 1, ?, ?)
                """,
                [NOW, NOW],
            )
            conn.executemany(
                """
                INSERT INTO notes (
                    id, user_id, folder_id, title, content, page_setup_json,
                    "order", created_at, updated_at
                ) VALUES (?, 'owner', ?, ?, ?, ?, 1, ?, ?)
                """,
                [
                    ("note-1", "folder-1", "Collaborative Note", "original", "{}", NOW, NOW),
                    ("note-2", None, "Standalone Note", "standalone", "{}", NOW, NOW),
                ],
            )

    def rows(self, statement, parameters=()):
        with database.db_connection(self.path) as conn:
            return conn.execute(statement, parameters).fetchall()

    def row(self, statement, parameters=()):
        with database.db_connection(self.path) as conn:
            return conn.execute(statement, parameters).fetchone()


class NotesCollaborationDatabaseTests(CollaborationDatabaseTestCase):
    def test_replace_invitations_canonicalizes_duplicates_and_revokes_removed_rows(self):
        with patch.object(notes_collaboration, "utcnow_iso", return_value=NOW), patch.object(
            notes_collaboration, "_iso_after", return_value=NEXT_WEEK
        ):
            notes_collaboration.replace_pending_invitations(
                "note",
                "note-1",
                "owner",
                [
                    {"email": "old@example.test", "role": "viewer"},
                    {"email": "Alice@Example.test", "role": "reviewer"},
                ],
                "owner",
            )
            invitations = notes_collaboration.replace_pending_invitations(
                "note",
                "note-1",
                "owner",
                [
                    {"email": "alice@example.test", "role": "editor"},
                    {"email": "ALICE@example.test", "role": "viewer"},
                    {"email": "new@example.test", "role": "editor"},
                ],
                "editor",
            )

        self.assertEqual(
            [(item["email"], item["role"], item["status"]) for item in invitations],
            [("alice@example.test", "editor", "pending"), ("new@example.test", "editor", "pending")],
        )
        old = self.row(
            "SELECT status FROM note_share_invitations WHERE email_normalized = 'old@example.test'"
        )
        self.assertEqual(old["status"], "revoked")
        version = self.row("SELECT access_version FROM notes WHERE id = 'note-1'")
        self.assertEqual(version["access_version"], 3)

    def test_pending_invitations_expire_before_listing_and_are_sorted_case_insensitively(self):
        with database.db_connection(self.path) as conn:
            conn.executemany(
                """
                INSERT INTO note_share_invitations (
                    id, owner_user_id, resource_type, resource_id, email_normalized,
                    email_display, access_level, invited_by_user_id, status,
                    expires_at, created_at, updated_at
                ) VALUES (?, 'owner', 'note', 'note-1', ?, ?, 'viewer', 'owner', 'pending', ?, ?, ?)
                """,
                [
                    ("expired", "expired@example.test", "expired@example.test", "2026-07-30T00:00:00Z", NOW, NOW),
                    ("zeta", "zeta@example.test", "Zeta@example.test", NEXT_WEEK, NOW, NOW),
                    ("alpha", "alpha@example.test", "alpha@example.test", NEXT_WEEK, NOW, NOW),
                ],
            )

        with patch.object(notes_collaboration, "utcnow_iso", return_value=NOW):
            result = notes_collaboration.list_pending_invitations("note", "note-1")

        self.assertEqual([item["id"] for item in result], ["alpha", "zeta"])
        self.assertEqual(self.row("SELECT status FROM note_share_invitations WHERE id = 'expired'")["status"], "expired")

    def test_claim_invitations_accepts_new_users_and_never_downgrades_existing_access(self):
        with database.db_connection(self.path) as conn:
            conn.execute(
                """
                INSERT INTO note_access_grants (
                    id, owner_user_id, resource_type, resource_id, principal_type,
                    principal_id, access_level, granted_by_user_id, created_at, updated_at
                ) VALUES ('existing-grant', 'owner', 'note', 'note-1', 'user', 'other', 'reviewer', 'owner', ?, ?)
                """,
                [NOW, NOW],
            )
            conn.execute(
                """
                INSERT INTO users (id, google_id, email, name, username, picture_url, created_at)
                VALUES ('other', 'google-other', 'other@example.test', 'Other', 'other', '', ?)
                """,
                [NOW],
            )

        with patch.object(notes_collaboration, "utcnow_iso", return_value=NOW):
            notes_collaboration.replace_pending_invitations(
                "note",
                "note-1",
                "owner",
                [
                    {"email": "viewer@example.test", "role": "EDITOR"},
                    {"email": "other@example.test", "role": "viewer"},
                ],
                "owner",
            )
            claimed = notes_collaboration.claim_pending_invitations("viewer", " VIEWER@EXAMPLE.TEST ")
            claimed_existing = notes_collaboration.claim_pending_invitations("other", "other@example.test")

        self.assertEqual(len(claimed), 1)
        self.assertEqual(claimed[0]["resource_id"], "note-1")
        self.assertEqual(len(claimed_existing), 1)
        viewer_grant = self.row(
            "SELECT access_level FROM note_access_grants WHERE resource_id = 'note-1' AND principal_id = 'viewer'"
        )
        other_grant = self.row(
            "SELECT access_level FROM note_access_grants WHERE resource_id = 'note-1' AND principal_id = 'other'"
        )
        self.assertEqual(viewer_grant["access_level"], "editor")
        self.assertEqual(other_grant["access_level"], "reviewer")
        statuses = {
            row["email_normalized"]: row["status"]
            for row in self.rows("SELECT email_normalized, status FROM note_share_invitations")
        }
        self.assertEqual(statuses["viewer@example.test"], "accepted")
        self.assertEqual(statuses["other@example.test"], "accepted")

    def test_claim_invitations_upgrades_existing_viewer_access(self):
        with database.db_connection(self.path) as conn:
            conn.execute(
                """
                INSERT INTO note_access_grants (
                    id, owner_user_id, resource_type, resource_id, principal_type,
                    principal_id, access_level, granted_by_user_id, created_at, updated_at
                ) VALUES ('viewer-grant', 'owner', 'note', 'note-1', 'user', 'viewer', 'viewer', 'owner', ?, ?)
                """,
                [NOW, NOW],
            )

        with patch.object(notes_collaboration, "utcnow_iso", return_value=NOW):
            notes_collaboration.replace_pending_invitations(
                "note",
                "note-1",
                "owner",
                [{"email": "viewer@example.test", "role": "editor"}],
                "owner",
            )
            claimed = notes_collaboration.claim_pending_invitations(
                "viewer",
                "viewer@example.test",
            )

        self.assertEqual(len(claimed), 1)
        viewer_grant = self.row(
            "SELECT access_level FROM note_access_grants WHERE resource_id = 'note-1' AND principal_id = 'viewer'"
        )
        self.assertEqual(viewer_grant["access_level"], "editor")

    def test_access_events_stringify_identifiers_and_preserve_optional_fields(self):
        with patch.object(notes_collaboration, "utcnow_iso", return_value=NOW):
            notes_collaboration.record_access_event(
                42,
                "note",
                7,
                "grant",
                target_type="user",
                target_id=99,
                old_access_level="viewer",
                new_access_level="editor",
            )

        event = self.row("SELECT * FROM note_access_events")
        self.assertEqual(event["actor_user_id"], "42")
        self.assertEqual(event["resource_id"], "7")
        self.assertEqual(event["target_id"], "99")
        self.assertEqual(event["new_access_level"], "editor")
        self.assertEqual(event["created_at"], NOW)

    def test_notification_lifecycle_skips_actor_truncates_messages_and_uses_bound_ids(self):
        self.assertIsNone(
            notes_collaboration.create_notification(
                "owner", "note_comment_created", "self", actor_user_id="owner"
            )
        )
        with patch.object(notes_collaboration, "utcnow_iso", return_value=NOW):
            first = notes_collaboration.create_notification(
                "owner",
                "note_comment_created",
                "x" * 600,
                actor_user_id="editor",
                note_id="note-1",
            )
            second = notes_collaboration.create_notification(
                "owner", "note_suggestion_created", "Short", actor_user_id="reviewer"
            )

        payload = notes_collaboration.list_notifications("owner", limit=0)
        self.assertEqual(len(payload["notifications"]), 1)
        self.assertEqual(payload["unread_count"], 2)
        self.assertEqual(len(payload["notifications"][0]["message"]), 500)

        with patch.object(notes_collaboration, "utcnow_iso", return_value=NEXT_WEEK):
            selected = notes_collaboration.mark_notifications_read(
                "owner", [first, "missing' OR 1=1 --"]
            )
        self.assertEqual(selected["unread_count"], 1)
        self.assertEqual(
            self.row("SELECT is_read FROM user_notifications WHERE id = ?", [first])["is_read"],
            1,
        )
        self.assertEqual(
            self.row("SELECT is_read FROM user_notifications WHERE id = ?", [second])["is_read"],
            0,
        )

        with patch.object(notes_collaboration, "utcnow_iso", return_value=NEXT_WEEK):
            all_read = notes_collaboration.mark_notifications_read("owner")
        self.assertEqual(all_read["unread_count"], 0)

    def test_suggestions_validate_operations_serialize_payloads_and_notify_editors_only(self):
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

        operations = [{"type": "replace", "text": "café", "attributes": {"bold": True}}]
        with patch.object(notes_collaboration, "utcnow_iso", return_value=NOW):
            suggestion = notes_collaboration.create_suggestion(
                "note-1",
                "owner",
                {
                    "operations": operations,
                    "operation_kind": "replace",
                    "summary": "Review this",
                    "base_state_vector": "AAEC",
                },
            )

        self.assertEqual(suggestion["operations"], operations)
        self.assertEqual(suggestion["author"], {
            "id": "owner",
            "name": "Owner Name",
            "username": "owner",
            "picture_url": "",
            "profile_url": "/u/owner",
        })
        raw = self.row("SELECT operations_json FROM note_suggestions WHERE id = ?", [suggestion["id"]])
        self.assertEqual(raw["operations_json"], '[{"type":"replace","text":"caf\\u00e9","attributes":{"bold":true}}]')
        recipients = {
            row["user_id"]
            for row in self.rows(
                "SELECT user_id FROM user_notifications WHERE suggestion_id = ?", [suggestion["id"]]
            )
        }
        self.assertEqual(recipients, {"editor"})

    def test_suggestion_validation_rejects_empty_excessive_and_oversized_operations(self):
        invalid_payloads = [
            {},
            {"operations": "not-a-list"},
            {"operations": []},
            {"operations": [{}] * 101},
            {"operations": [{"text": "x" * 256_001}]},
        ]
        for payload in invalid_payloads:
            with self.subTest(payload_keys=tuple(payload)):
                with self.assertRaises(ValueError):
                    notes_collaboration.create_suggestion("note-1", "owner", payload)
        self.assertEqual(self.row("SELECT COUNT(*) AS count FROM note_suggestions")["count"], 0)

    def test_suggestion_resolution_is_single_use_and_notifies_original_author(self):
        with patch.object(notes_collaboration, "utcnow_iso", return_value=NOW):
            suggestion = notes_collaboration.create_suggestion(
                "note-1", "owner", {"operations": [{"type": "insert"}]}
            )
            resolved = notes_collaboration.resolve_suggestion(
                "note-1", suggestion["id"], "editor", "accepted"
            )

        self.assertEqual(resolved["status"], "accepted")
        self.assertEqual(resolved["resolved_by"]["id"], "editor")
        self.assertEqual(
            self.row(
                "SELECT notification_type FROM user_notifications WHERE suggestion_id = ?",
                [suggestion["id"]],
            )["notification_type"],
            "note_suggestion_accepted",
        )
        with self.assertRaises(ValueError):
            notes_collaboration.resolve_suggestion("note-1", suggestion["id"], "editor", "rejected")
        with self.assertRaises(ValueError):
            notes_collaboration.resolve_suggestion("note-1", suggestion["id"], "editor", "unknown")
        self.assertIsNone(notes_collaboration.resolve_suggestion("note-1", "missing", "editor", "accepted"))


class NotesCollaborationValidationTests(unittest.TestCase):
    def test_roles_and_emails_are_normalized_with_strict_validation(self):
        self.assertEqual(notes_collaboration.normalize_role(" EDITOR "), "editor")
        self.assertEqual(
            notes_collaboration.normalize_email(" Alice@Example.TEST "),
            ("alice@example.test", "Alice@Example.TEST"),
        )
        for invalid_role in (None, "owner", "", "admin"):
            with self.subTest(role=invalid_role):
                with self.assertRaisesRegex(ValueError, "Role must be"):
                    notes_collaboration.normalize_role(invalid_role)
        for invalid_email in (None, "", "missing-at.example", "user@example", "x" * 321 + "@example.test"):
            with self.subTest(email=invalid_email):
                with self.assertRaisesRegex(ValueError, "valid email"):
                    notes_collaboration.normalize_email(invalid_email)


if __name__ == "__main__":
    unittest.main()
