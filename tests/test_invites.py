import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from appwrite.query import Query
from appwrite_client import COLLECTIONS
from appwrite_helpers import create_row_safe, first_row, list_rows_all, update_row_safe
from services import invites
from services.database import init_db
from services.user_cleanup import delete_user_data


class InviteServiceTests(unittest.TestCase):
    def setUp(self):
        self.path = tempfile.mktemp(suffix=".sqlite3")
        self.app = Flask(__name__)
        self.app.config["DATABASE_PATH"] = self.path
        init_db(self.app, self.path)
        self.context = self.app.app_context()
        self.context.push()
        self.audit_patch = patch.object(invites, "emit_creation_event")
        self.notify_patch = patch.object(invites.notifications, "notify")
        self.audit = self.audit_patch.start()
        self.notify = self.notify_patch.start()
        self._create_user("owner")
        self._create_user("invitee")

    def tearDown(self):
        self.notify_patch.stop()
        self.audit_patch.stop()
        self.context.pop()
        Path(self.path).unlink(missing_ok=True)

    def _create_user(self, user_id, **overrides):
        payload = {
            "google_id": user_id,
            "email": f"{user_id}@example.test",
            "name": user_id.title(),
            "username": user_id,
            "tier": "free",
            "onboarding_complete": False,
            "onboarding_step": 1,
            "created_at": "2026-07-24T00:00:00Z",
        }
        payload.update(overrides)
        return create_row_safe(COLLECTIONS["users"], user_id, payload)

    def _invite_and_attribute(self, invitee_id="invitee"):
        invitation = invites.create_invite("owner", "Study group")
        attribution = invites.attribute_signup(invitation["code"], invitee_id)
        return invitation, attribution

    def test_code_generation_and_normalization(self):
        code = invites.generate_code()
        self.assertEqual(len(code), invites.INVITE_CODE_LENGTH)
        self.assertTrue(set(code).issubset(set(invites.INVITE_ALPHABET)))
        self.assertEqual(invites.normalize_code(f" {code.lower()} "), code)
        self.assertIsNone(invites.normalize_code("O0I1LL"))
        self.assertIsNone(invites.normalize_code("ABC"))

    def test_empty_invite_cap(self):
        for index in range(invites.EMPTY_INVITE_LIMIT):
            invites.create_invite("owner", f"Invite {index + 1}")

        with self.assertRaises(invites.InviteLimitError):
            invites.create_invite("owner", "One too many")

        first_invite = invites.list_invites_for_owner("owner")[-1]
        invites.attribute_signup(first_invite["code"], "invitee")
        replacement = invites.create_invite("owner", "Replacement")
        self.assertTrue(replacement["is_active"])

    def test_self_invite_is_blocked(self):
        invitation = invites.create_invite("owner")
        self.assertIsNone(invites.attribute_signup(invitation["code"], "owner"))
        self.assertEqual(
            list_rows_all(COLLECTIONS["user_invite_attributions"]),
            [],
        )

    def test_user_is_attributed_only_once(self):
        first = invites.create_invite("owner", "First")
        invites.attribute_signup(first["code"], "invitee")

        self._create_user("owner-2")
        second = invites.create_invite("owner-2", "Second")
        self.assertIsNone(invites.attribute_signup(second["code"], "invitee"))
        rows = list_rows_all(COLLECTIONS["user_invite_attributions"])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["invite_id"], first["$id"])

    def test_activation_before_onboarding_promotes_at_completion(self):
        _, attribution = self._invite_and_attribute()
        activated = invites.record_activation("invitee", "course")
        self.assertEqual(activated["status"], "invited")
        self.assertEqual(activated["activation_signal"], "course")
        self.notify.assert_not_called()

        update_row_safe(
            COLLECTIONS["users"],
            "invitee",
            {"onboarding_complete": True},
        )
        joined = invites.promote_if_activated("invitee")
        self.assertEqual(joined["status"], "joined")
        self.assertIsNotNone(joined["joined_at"])
        self.notify.assert_called_once()

        invites.promote_if_activated("invitee")
        self.notify.assert_called_once()
        self.assertEqual(joined["$id"], attribution["$id"])

    def test_activation_after_onboarding_promotes_immediately(self):
        self._invite_and_attribute()
        update_row_safe(
            COLLECTIONS["users"],
            "invitee",
            {"onboarding_complete": True},
        )
        joined = invites.record_activation("invitee", "note")
        self.assertEqual(joined["status"], "joined")
        self.assertEqual(joined["activation_signal"], "note")
        self.notify.assert_called_once()

    def test_tier_change_appends_history_and_updates_attribution(self):
        _, attribution = self._invite_and_attribute()
        event = invites.record_tier_change("invitee", "free", "grade_a")
        self.assertEqual(event["from_tier"], "free")
        self.assertEqual(event["to_tier"], "grade_a")
        stored = first_row(
            COLLECTIONS["user_invite_attributions"],
            [Query.equal("$id", [attribution["$id"]])],
        )
        self.assertEqual(stored["current_tier"], "grade_a")

    def test_anonymization_preserves_counts(self):
        invitation, _ = self._invite_and_attribute()
        self.assertEqual(invites.anonymize_invitee("invitee"), 1)
        attribution = first_row(
            COLLECTIONS["user_invite_attributions"],
            [Query.equal("invite_id", [invitation["$id"]])],
        )
        self.assertIsNone(attribution["invited_user_id"])
        self.assertTrue(attribution["is_anonymized"])
        payload = invites.list_invites_for_owner("owner")[0]
        self.assertEqual(payload["invited_count"], 1)
        self.assertEqual(payload["people"], [])

    def test_account_deletion_anonymizes_invitee_attribution(self):
        invitation, _ = self._invite_and_attribute()
        invites.record_tier_change("invitee", "free", "grade_a")
        self.assertEqual(delete_user_data("invitee"), [])
        attribution = first_row(
            COLLECTIONS["user_invite_attributions"],
            [Query.equal("invite_id", [invitation["$id"]])],
        )
        self.assertIsNone(attribution["invited_user_id"])
        self.assertTrue(attribution["is_anonymized"])
        self.assertEqual(
            invites.list_invites_for_owner("owner")[0]["invited_count"],
            1,
        )
        self.assertEqual(
            list_rows_all(COLLECTIONS["user_invite_tier_events"]),
            [],
        )

    def test_owner_deletion_removes_orphaned_invitee_tier_events(self):
        self._invite_and_attribute()
        invites.record_tier_change("invitee", "free", "grade_a")

        self.assertEqual(delete_user_data("owner"), [])
        self.assertEqual(
            list_rows_all(COLLECTIONS["user_invite_tier_events"]),
            [],
        )


if __name__ == "__main__":
    unittest.main()
