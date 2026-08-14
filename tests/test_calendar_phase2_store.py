import json
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from services import database
from services.extension_consent import put_consent
from services.extension_contract import ExtensionContractError, canonical_canvas_source_key
from services import calendar_events as events


ACCOUNT_1 = "1" * 64
ACCOUNT_2 = "2" * 64
V1_GRANTED_SCOPES = [
    "full_history_upload",
    "ongoing_read",
    "shares_ics_inclusion",
]


class CalendarPhase2StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = os.path.join(self.temp_dir.name, "calendar.sqlite3")
        self.env = patch.dict(os.environ, {
            "DATABASE_PATH": self.db_path,
            "FLASK_ENV": "testing",
            "APSTUDY_ALLOW_INSECURE_HTTP": "1",
        }, clear=False)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.capabilities = patch(
            "services.extension_consent.extension_capability_enabled",
            return_value=True,
        )
        self.capabilities.start()
        self.addCleanup(self.capabilities.stop)
        database.init_db(path=self.db_path)

    def grant(self, user="user-1", account=ACCOUNT_1):
        return put_consent(
            user, canonical_canvas_source_key(account), account,
            action="grant",
            scopes=list(V1_GRANTED_SCOPES),
            version=1,
            path=self.db_path,
        )

    def source(self, user="user-1", source_id="source-1", account=ACCOUNT_1):
        self.grant(user, account)
        return events.register_canvas_import_source(user, {
            "account_key": account,
            "source_id": source_id,
            "origin": "https://canvas.example.edu",
            "provider_user_id": account,
            "label": "Canvas",
        })

    def start_run(self, user="user-1", source_id="source-1", key="run-key", scope=None):
        return events.begin_canvas_sync_run(
            user, source_id,
            scope=scope or {},
            consent_version=1,
            idempotency_key=key,
            run_id=key,
        )

    def item(self, item_id="assignment-1", **overrides):
        value = {
            "context_id": "course-1",
            "calendar_id": "calendar-1",
            "item_type": "assignment",
            "item_id": item_id,
            "title": "Read chapter 1",
            "start": "2026-08-12T10:00:00Z",
            "end": "2026-08-12T11:00:00Z",
            "source_revision": "r1",
            "completion_status": "incomplete",
            "completion_source": "canvas",
        }
        value.update(overrides)
        return value

    def test_migration_018_tables_columns_indexes_and_rerun(self):
        with sqlite3.connect(self.db_path) as connection:
            tables = {
                row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            self.assertTrue({
                "calendar_import_sources", "calendar_sync_runs", "calendar_sync_batches",
                "calendar_import_routing", "calendar_event_links", "calendar_writebacks",
            } <= tables)
            self.assertTrue({
                "canvas_source_id", "canvas_completion_status", "canvas_completion_source",
                "canvas_soft_deleted", "canvas_last_seen_generation",
            } <= {row[1] for row in connection.execute("PRAGMA table_info(calendar_cache)")})
            indexes = {
                row[1] for row in connection.execute(
                    "SELECT type, name FROM sqlite_master WHERE type = 'index'"
                )
            }
            self.assertTrue({
                "idx_calendar_event_links_canvas_identity", "idx_calendar_event_links_active_ref",
                "idx_calendar_writebacks_pending", "idx_calendar_sync_runs_idempotency",
            } <= indexes)
            before = connection.execute(
                "SELECT COUNT(*) FROM schema_migrations WHERE version = '018_canvas_calendar_integration'"
            ).fetchone()[0]
        database.init_db(path=self.db_path)
        with sqlite3.connect(self.db_path) as connection:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM schema_migrations WHERE version = '018_canvas_calendar_integration'"
            ).fetchone()[0], before)

    def test_invalid_placeholder_account_key_is_rejected(self):
        with self.assertRaisesRegex(
            ExtensionContractError,
            "account_key must be exactly 64 lowercase hexadecimal characters",
        ):
            self.grant(account="canvas-1")

    def test_source_account_isolation_and_sync_generation_lease_scope_cancel(self):
        self.source()
        self.source("user-2", "source-2", ACCOUNT_2)
        first = self.start_run()
        replay = self.start_run()
        self.assertTrue(replay["idempotent"])
        self.assertEqual(first["generation"], 1)
        second = self.start_run(key="run-key-2")
        self.assertEqual(second["generation"], 2)
        self.assertEqual(events.get_canvas_sync_run("user-1", "source-1", "run-key")["state"], "superseded")
        with self.assertRaisesRegex(ExtensionContractError, "source was not found"):
            events.get_canvas_sync_run("user-2", "source-1", "run-key")
        with self.assertRaisesRegex(ExtensionContractError, "lease token"):
            events.renew_canvas_sync_run("user-1", "source-1", "run-key-2", lease_token="wrong")
        renewed = events.renew_canvas_sync_run(
            "user-1", "source-1", "run-key-2", lease_token=second["lease_token"]
        )
        self.assertGreater(renewed["lease_expires_at"], second["lease_expires_at"])
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                "UPDATE calendar_sync_runs SET lease_expires_at = '2000-01-01T00:00:00Z' WHERE run_id = 'run-key-2'"
            )
        with self.assertRaisesRegex(ExtensionContractError, "expired"):
            events.ingest_canvas_sync_batch(
                "user-1", "source-1", "run-key-2", [self.item()],
                generation=2, lease_token=renewed["lease_token"], idempotency_key="expired-batch",
            )
        expired = events.get_canvas_sync_run("user-1", "source-1", "run-key-2")
        self.assertEqual(expired["state"], "expired")

    def test_batch_validation_identity_upsert_counters_checkpoint_and_completion(self):
        self.source()
        run = self.start_run(scope={"context_ids": ["course-1"]})
        with self.assertRaisesRegex(ExtensionContractError, "at most 100"):
            events.ingest_canvas_sync_batch(
                "user-1", "source-1", run["run_id"], [self.item(str(i)) for i in range(101)],
                generation=1, lease_token=run["lease_token"], idempotency_key="too-many",
            )
        with self.assertRaisesRegex(ExtensionContractError, "exceeds"):
            events.ingest_canvas_sync_batch(
                "user-1", "source-1", run["run_id"], [{**self.item(), "description": "x" * (512 * 1024)}],
                generation=1, lease_token=run["lease_token"], idempotency_key="too-big",
            )
        quarantined = events.ingest_canvas_sync_batch(
            "user-1", "source-1", run["run_id"], [
                self.item("announcement-1", item_type="announcement"),
                self.item("bad-date", start="not-a-date"),
            ], generation=1, lease_token=run["lease_token"], idempotency_key="quarantined",
        )
        self.assertEqual(quarantined["quarantined"], 2)
        batch = events.ingest_canvas_sync_batch(
            "user-1", "source-1", run["run_id"], [self.item()],
            generation=1, lease_token=run["lease_token"], idempotency_key="batch-1",
            checkpoint={"cursor": "cursor-1"},
        )
        replay = events.ingest_canvas_sync_batch(
            "user-1", "source-1", run["run_id"], [self.item()],
            generation=1, lease_token=run["lease_token"], idempotency_key="batch-1",
        )
        self.assertTrue(replay["idempotent"])
        self.assertEqual(batch["accepted"], 1)
        updated = events.ingest_canvas_sync_batch(
            "user-1", "source-1", run["run_id"], [self.item(title="Changed")],
            generation=1, lease_token=run["lease_token"], idempotency_key="batch-2",
        )
        self.assertEqual(updated["updated"], 1)
        row = events.get_canvas_sync_run("user-1", "source-1", run["run_id"])
        self.assertEqual(row["cursor"], "cursor-1")
        self.assertEqual(row["counters"]["accepted"], 2)
        with sqlite3.connect(self.db_path) as connection:
            connection.row_factory = sqlite3.Row
            cache = connection.execute(
                "SELECT canvas_event_ref, canvas_source_item_key, canvas_completion_status, canvas_completion_source FROM calendar_cache"
            ).fetchone()
        self.assertEqual(cache["canvas_completion_status"], "incomplete")
        self.assertEqual(cache["canvas_completion_source"], "canvas")
        self.assertTrue(cache["canvas_event_ref"])
        self.assertTrue(cache["canvas_source_item_key"])
        complete = events.finalize_canvas_sync_run(
            "user-1", "source-1", run["run_id"], scope={"context_ids": ["course-1"]},
            generation=1, lease_token=run["lease_token"], status="complete",
        )
        self.assertEqual(complete["state"], "complete")

    def test_batch_replay_fences_lease_generation_and_consent_before_receipt(self):
        self.source()
        run = self.start_run(key="run-replay")
        first = events.ingest_canvas_sync_batch(
            "user-1", "source-1", run["run_id"], [self.item()],
            generation=run["generation"], lease_token=run["lease_token"],
            idempotency_key="batch-replay",
        )
        before = events.get_canvas_sync_run("user-1", "source-1", run["run_id"])

        replay = events.ingest_canvas_sync_batch(
            "user-1", "source-1", run["run_id"], [self.item()],
            generation=run["generation"], lease_token=run["lease_token"],
            idempotency_key="batch-replay",
        )
        self.assertTrue(replay["idempotent"])
        self.assertEqual(replay["id"], first["id"])
        self.assertEqual(
            events.get_canvas_sync_run("user-1", "source-1", run["run_id"])["counters"],
            before["counters"],
        )

        with self.assertRaisesRegex(ExtensionContractError, "lease token") as wrong_token:
            events.ingest_canvas_sync_batch(
                "user-1", "source-1", run["run_id"], [self.item()],
                generation=run["generation"], lease_token="wrong-token",
                idempotency_key="batch-replay",
            )
        self.assertEqual(wrong_token.exception.code, "lease_token_mismatch")

        with self.assertRaisesRegex(ExtensionContractError, "idempotency") as payload_conflict:
            events.ingest_canvas_sync_batch(
                "user-1", "source-1", run["run_id"], [self.item(title="changed")],
                generation=run["generation"], lease_token=run["lease_token"],
                idempotency_key="batch-replay",
            )
        self.assertEqual(payload_conflict.exception.code, "idempotency_conflict")

        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                "UPDATE calendar_sync_runs SET lease_expires_at = '2000-01-01T00:00:00Z' WHERE id = ?",
                [run["id"]],
            )
        with self.assertRaisesRegex(ExtensionContractError, "expired") as expired:
            events.ingest_canvas_sync_batch(
                "user-1", "source-1", run["run_id"], [self.item()],
                generation=run["generation"], lease_token=run["lease_token"],
                idempotency_key="batch-replay",
            )
        self.assertEqual(expired.exception.code, "lease_expired")

        newer = self.start_run(key="run-replay-newer")
        with self.assertRaisesRegex(ExtensionContractError, "current Canvas sync generation") as stale:
            events.ingest_canvas_sync_batch(
                "user-1", "source-1", run["run_id"], [self.item()],
                generation=run["generation"], lease_token=run["lease_token"],
                idempotency_key="batch-replay",
            )
        self.assertEqual(stale.exception.code, "stale_run")
        self.assertEqual(newer["generation"], run["generation"] + 1)
        with sqlite3.connect(self.db_path) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT canvas_soft_deleted FROM calendar_cache WHERE user_id = ?",
                    ["user-1"],
                ).fetchone()[0],
                0,
            )

    def test_partial_stale_and_expired_runs_do_not_tombstone(self):
        self.source()
        run = self.start_run(key="run-a")
        events.ingest_canvas_sync_batch(
            "user-1", "source-1", run["run_id"], [self.item()], generation=1,
            lease_token=run["lease_token"], idempotency_key="batch-a",
        )
        partial = events.finalize_canvas_sync_run(
            "user-1", "source-1", run["run_id"], scope={}, generation=1,
            lease_token=run["lease_token"], status="partial",
        )
        self.assertEqual(partial["state"], "partial")
        with sqlite3.connect(self.db_path) as connection:
            self.assertEqual(connection.execute("SELECT canvas_soft_deleted FROM calendar_cache").fetchone()[0], 0)
        tombstone_run = self.start_run(key="run-b")
        complete = events.finalize_canvas_sync_run(
            "user-1", "source-1", tombstone_run["run_id"], scope={}, generation=2,
            lease_token=tombstone_run["lease_token"], status="complete",
        )
        self.assertEqual(complete["tombstoned"], 1)
        with sqlite3.connect(self.db_path) as connection:
            self.assertEqual(connection.execute("SELECT canvas_soft_deleted FROM calendar_cache").fetchone()[0], 1)
        stale = self.start_run(key="run-c")
        with self.assertRaises(ExtensionContractError) as stale_error:
            events.finalize_canvas_sync_run(
                "user-1", "source-1", "run-b", scope={}, generation=2,
                lease_token=run["lease_token"],
            )
        self.assertEqual(stale_error.exception.code, "stale_run")
        with sqlite3.connect(self.db_path) as connection:
            connection.execute("UPDATE calendar_sync_runs SET lease_expires_at = '2000-01-01T00:00:00Z' WHERE run_id = 'run-c'")
        with self.assertRaisesRegex(ExtensionContractError, "expired"):
            events.finalize_canvas_sync_run(
                "user-1", "source-1", stale["run_id"], scope={}, generation=3,
                lease_token=stale["lease_token"],
            )

    def test_completion_source_and_routing(self):
        self.source()
        run = self.start_run()
        events.ingest_canvas_sync_batch(
            "user-1", "source-1", run["run_id"],
            [self.item(completion_status="done", completion_source="nest")],
            generation=1, lease_token=run["lease_token"], idempotency_key="completion",
        )
        with sqlite3.connect(self.db_path) as connection:
            status, source = connection.execute(
                "SELECT canvas_completion_status, canvas_completion_source FROM calendar_cache"
            ).fetchone()
        self.assertEqual((status, source), ("completed", "extension"))
        inventory = [
            {"id": "cal-in", "visible": True, "routing_eligible": True},
            {"id": "cal-fallback", "visible": True, "routing_eligible": True},
            {"id": "cal-done", "visible": True, "routing_eligible": True},
        ]
        with patch.object(events, "extension_calendar_destinations", return_value=inventory):
            self.assertEqual(events.set_canvas_import_routing("user-1", "source-1", "incomplete", "cal-in", "cal-fallback")["state"], "incomplete")
            self.assertEqual(events.set_canvas_import_routing("user-1", "source-1", "completed", "cal-done")["destination_calendar_id"], "cal-done")
        with patch.object(events, "extension_calendar_destinations", return_value=[]):
            with self.assertRaisesRegex(ExtensionContractError, "visible"):
                events.set_canvas_import_routing("user-1", "source-1", "incomplete", "missing-calendar", "cal-fallback")
        self.assertEqual(events.get_canvas_import_routing("user-1", "source-1", "incomplete")["destination_calendar_id"], "cal-in")
        self.assertEqual(len(events.get_canvas_import_routing("user-1", "source-1")), 2)

    def test_revocation_archives_and_cancels_owned_outputs(self):
        self.source()
        run = self.start_run()
        link = events.create_canvas_event_link("user-1", "source-1", {
            "account_key": ACCOUNT_1, "event_ref": "event:one", "projection_event_id": "projection-1",
        })
        # Internal Phase 2 cleanup coverage: public v1 consent intentionally
        # does not grant the future two-way writeback capability.
        with patch.object(events, "_canvas_source_consent", return_value=(None, 1)):
            writeback = events.create_canvas_writeback("user-1", "source-1", {
                "account_key": ACCOUNT_1, "operation": "create", "idempotency_key": "wb-revoke",
                "target_account": ACCOUNT_1, "payload": {"title": "new"},
            })
        revoked = put_consent(
            "user-1", canonical_canvas_source_key(ACCOUNT_1), ACCOUNT_1, action="revoke",
            scopes=list(V1_GRANTED_SCOPES), version=1, path=self.db_path,
        )
        self.assertEqual(revoked.state, "revoked")
        self.assertEqual(events.get_canvas_event_link("user-1", "source-1", link["event_ref"]), None)
        self.assertEqual(events.get_canvas_writeback_result("user-1", "source-1", writeback["id"])["state"], "cancelled")
        self.assertEqual(events.get_canvas_sync_run("user-1", "source-1", run["run_id"])["state"], "cancelled")

    def test_event_link_uniqueness_results_and_isolation(self):
        self.source()
        self.source("user-2", "source-2", ACCOUNT_2)
        payload = {
            "account_key": ACCOUNT_1, "event_ref": "event:one", "canvas_context_id": "course-1",
            "canvas_calendar_id": "calendar-1", "canvas_item_type": "assignment", "canvas_item_id": "a1",
            "source_revision": "r1",
        }
        link = events.create_canvas_event_link("user-1", "source-1", payload)
        self.assertTrue(events.create_canvas_event_link("user-1", "source-1", payload)["idempotent"])
        with self.assertRaisesRegex(ExtensionContractError, "already exists"):
            events.create_canvas_event_link("user-1", "source-1", {**payload, "projection_event_id": "other"})
        with self.assertRaisesRegex(ExtensionContractError, "source was not found"):
            events.get_canvas_event_link("user-2", "source-1", "event:one")
        with self.assertRaisesRegex(ExtensionContractError, "revision"):
            events.record_canvas_event_link_result("user-1", "source-1", "event:one", state="applied", expected_revision="old", source_revision="r2")
        result = events.record_canvas_event_link_result("user-1", "source-1", "event:one", state="applied", expected_revision="r1", source_revision="r2")
        self.assertEqual(result["mirror_state"], "applied")
        self.assertTrue(events.record_canvas_event_link_result("user-1", "source-1", "event:one", state="applied", source_revision="r2")["idempotent"])
        self.assertEqual(events.get_canvas_event_link("user-1", "source-1", link["event_ref"])["source_revision"], "r2")

    def test_writeback_lifecycle_idempotency_conflict_and_result(self):
        self.source()
        events.create_canvas_event_link("user-1", "source-1", {
            "account_key": ACCOUNT_1, "event_ref": "event:one", "source_revision": "r1",
        })
        payload = {
            "account_key": ACCOUNT_1, "operation": "update", "event_ref": "event:one",
            "expected_revision": "r1", "idempotency_key": "wb-1", "target_account": ACCOUNT_1,
            "target_calendar": "calendar-1", "payload": {"title": "Changed"},
        }
        # Future-capability/internal coverage: the public v1 consent contract
        # must continue to reject two_way_writeback grants.
        with patch.object(events, "_canvas_source_consent", return_value=(None, 1)):
            created = events.create_canvas_writeback("user-1", "source-1", payload)
            self.assertTrue(events.create_canvas_writeback("user-1", "source-1", payload)["idempotent"])
            with self.assertRaisesRegex(ExtensionContractError, "idempotency"):
                events.create_canvas_writeback("user-1", "source-1", {**payload, "payload": {"title": "Other"}})
        self.assertEqual(created["state"], "waiting_for_canvas_session")
        self.assertEqual(len(events.list_canvas_writebacks("user-1", "source-1", event_ref="event:one")), 1)
        applied = events.record_canvas_writeback_result(
            "user-1", "source-1", created["id"],
            {"state": "applied", "expected_revision": "r1", "result_revision": "r2"},
        )
        self.assertEqual(applied["state"], "applied")
        self.assertEqual(applied["result_revision"], "r2")
        self.assertTrue(events.record_canvas_writeback_result(
            "user-1", "source-1", created["id"],
            {"state": "applied", "result_revision": "r2"},
        )["idempotent"])
        with self.assertRaisesRegex(ExtensionContractError, "terminal"):
            events.record_canvas_writeback_result("user-1", "source-1", created["id"], state="conflict")


if __name__ == "__main__":
    unittest.main()
