import threading
import time
import unittest
from unittest import mock

from services import atlas_client
from services import atlas_live_verification
from services import course_live_snapshots
from services.atlas_client import _normalize_enrollment_status, build_section_id

TERM = "Fall_2026"
SECTION_LIMIT_MESSAGE = (
    "Too many section IDs requested (max 120); excess IDs were ignored"
)
GROUP_LIMIT_MESSAGE = (
    "Too many (term, subject) groups requested (max 24); excess groups were ignored"
)
DETAIL_LIMIT_MESSAGE = (
    "Too many detail IDs requested (max 12); excess IDs were ignored"
)
SECTION_IDS_OVERFLOW_KEY = atlas_live_verification.SECTION_IDS_OVERFLOW_KEY
GROUPS_OVERFLOW_KEY = atlas_live_verification.GROUPS_OVERFLOW_KEY
DETAIL_IDS_OVERFLOW_KEY = atlas_live_verification.DETAIL_IDS_OVERFLOW_KEY
DEADLINE_SECTION_MESSAGE = atlas_live_verification.DEADLINE_SECTION_MESSAGE
DEADLINE_DETAILS_MESSAGE = atlas_live_verification.DEADLINE_DETAILS_MESSAGE
DETAIL_STATUS_MESSAGE = atlas_live_verification.DETAIL_STATUS_MESSAGE
DETAIL_UNVERIFIED_MESSAGE = atlas_live_verification.DETAIL_UNVERIFIED_MESSAGE
DETAIL_INVALID_MESSAGE = atlas_live_verification.DETAIL_INVALID_MESSAGE


def _live_row(subject, catalog, crn, section_number, raw_status, atlas_key=None, seats=None):
    return {
        "id": build_section_id(TERM, subject, catalog, crn, section_number),
        "term": TERM,
        "subject": subject,
        "catalog_number": catalog,
        "crn": crn,
        "section_number": section_number,
        "atlas_key": atlas_key,
        "enrollment_status": _normalize_enrollment_status(raw_status),
        "seats_available": seats,
    }


def _fake_fetch_by_subject(subject_to_result):
    calls = []

    def fake_fetch(term, subject, timeout=15):
        calls.append((term, subject))
        result = subject_to_result[str(subject).upper()]
        if isinstance(result, Exception):
            raise result
        return result

    fake_fetch.calls = calls
    return fake_fetch


def _details_payload(atlas_key, status_text, seats):
    return {
        "key": atlas_key,
        "seats": (
            "<strong>Maximum Enrollment</strong>: 36 / "
            f"<strong>Seats Avail</strong>: {seats}"
        ),
        "enrl_stat_html": status_text,
    }


class AtlasLiveVerificationTests(unittest.TestCase):
    def setUp(self):
        atlas_live_verification._last_atlas_call_monotonic = None
        never = AssertionError("Stale snapshot read attempted during live verification")
        mock.patch.object(
            course_live_snapshots, "get_snapshot", side_effect=never
        ).start()
        mock.patch.object(
            course_live_snapshots, "list_snapshots", side_effect=never
        ).start()
        self.upsert = mock.patch.object(
            course_live_snapshots, "upsert_snapshot"
        ).start()
        self.details_fetch = mock.patch.object(
            atlas_live_verification,
            "fetch_atlas_section_details",
            side_effect=AssertionError("Details endpoint must not be called in this test"),
        ).start()
        self.pace_patcher = mock.patch.object(
            atlas_live_verification, "_pace_atlas_call", lambda: None
        )
        self.pace_patcher.start()
        self.time = mock.patch.object(atlas_live_verification, "time").start()
        # Deadline arithmetic needs numeric monotonic values by default;
        # deadline tests override this with an explicit deterministic sequence.
        self.time.monotonic.side_effect = time.monotonic
        self.addCleanup(self._reset_pacing_state)
        self.addCleanup(mock.patch.stopall)

    def _reset_pacing_state(self):
        atlas_live_verification._last_atlas_call_monotonic = None

    def tearDown(self):
        atlas_live_verification._last_atlas_call_monotonic = None

    def test_live_closed_overrides_hypothetical_cached_open(self):
        section_id = build_section_id(TERM, "CHEM", "150", "2760", "1")
        row = _live_row("CHEM", "150", "2760", "1", "C", atlas_key="key-chem150")
        fake_fetch = _fake_fetch_by_subject({"CHEM": {"sections": [row]}})
        with mock.patch.object(
            atlas_live_verification, "fetch_live_subject_sections", fake_fetch
        ):
            result = atlas_live_verification.verify_sections_by_ids([section_id])

        self.assertEqual(fake_fetch.calls, [(TERM, "CHEM")])
        self.assertEqual(len(result["groups"]), 1)
        group = result["groups"][0]
        self.assertEqual(group["requested"], 1)
        self.assertEqual(group["matched"], 1)
        self.assertTrue(group["ok"])
        self.assertIsNone(group["error"])
        self.assertEqual(result["errors_by_id"], {})
        verified = result["verified_by_id"][section_id]
        self.assertEqual(verified["enrollment_status"], "Closed")
        self.assertEqual(verified["seats_available"], 0)
        self.assertEqual(result["details_by_id"], {})
        self.upsert.assert_not_called()
        self.details_fetch.assert_not_called()

    def test_live_open_reports_unknown_seats(self):
        section_id = build_section_id(TERM, "CHEM", "150", "2760", "1")
        row = _live_row("CHEM", "150", "2760", "1", "O", atlas_key="key-chem150", seats=3)
        fake_fetch = _fake_fetch_by_subject({"CHEM": {"sections": [row]}})
        with mock.patch.object(
            atlas_live_verification, "fetch_live_subject_sections", fake_fetch
        ):
            result = atlas_live_verification.verify_sections_by_ids([section_id])

        self.assertEqual(result["errors_by_id"], {})
        self.assertEqual(len(result["groups"]), 1)
        group = result["groups"][0]
        self.assertEqual(group["requested"], 1)
        self.assertEqual(group["matched"], 1)
        self.assertTrue(group["ok"])
        self.assertIsNone(group["error"])
        verified = result["verified_by_id"][section_id]
        self.assertEqual(verified["enrollment_status"], "Open")
        self.assertIsNone(verified["seats_available"])
        self.assertEqual(result["details_by_id"], {})
        self.upsert.assert_not_called()
        self.details_fetch.assert_not_called()

    def test_missing_section_reports_error_without_stale_value(self):
        found_id = build_section_id(TERM, "CHEM", "150", "2760", "1")
        missing_id = build_section_id(TERM, "CHEM", "151", "2761", "1")
        row = _live_row("CHEM", "150", "2760", "1", "C", atlas_key="key-chem150")
        fake_fetch = _fake_fetch_by_subject({"CHEM": {"sections": [row]}})
        with mock.patch.object(
            atlas_live_verification, "fetch_live_subject_sections", fake_fetch
        ):
            result = atlas_live_verification.verify_sections_by_ids(
                [found_id, missing_id]
            )

        self.assertIn(missing_id, result["errors_by_id"])
        self.assertEqual(
            result["errors_by_id"][missing_id],
            "Section not found in live Atlas results",
        )
        self.assertNotIn(missing_id, result["verified_by_id"])
        self.assertNotIn("enrollment_status", result["verified_by_id"].get(missing_id, {}))
        self.assertNotIn("seats_available", result["verified_by_id"].get(missing_id, {}))
        self.assertEqual(len(result["groups"]), 1)
        group = result["groups"][0]
        self.assertEqual(group["requested"], 2)
        self.assertEqual(group["matched"], 1)
        self.assertTrue(group["ok"])
        self.assertIsNone(group["error"])
        self.assertIn(found_id, result["verified_by_id"])

    def test_group_fetch_error_isolated_to_ids(self):
        chem_id = build_section_id(TERM, "CHEM", "150", "2760", "1")
        math_id = build_section_id(TERM, "MATH", "140", "3010", "2")
        math_row = _live_row("MATH", "140", "3010", "2", "O", atlas_key="key-math140")
        fake_fetch = _fake_fetch_by_subject(
            {"CHEM": RuntimeError("boom"), "MATH": {"sections": [math_row]}}
        )
        with mock.patch.object(
            atlas_live_verification, "fetch_live_subject_sections", fake_fetch
        ):
            result = atlas_live_verification.verify_sections_by_ids([chem_id, math_id])

        self.assertEqual(fake_fetch.calls, [(TERM, "CHEM"), (TERM, "MATH")])
        self.assertEqual(len(result["groups"]), 2)
        failed_group = result["groups"][0]
        self.assertEqual(failed_group["term"], TERM)
        self.assertEqual(failed_group["subject"], "CHEM")
        self.assertEqual(failed_group["requested"], 1)
        self.assertEqual(failed_group["matched"], 0)
        self.assertFalse(failed_group["ok"])
        self.assertIn("Live Atlas request failed", failed_group["error"])
        self.assertIn("boom", failed_group["error"])
        ok_group = result["groups"][1]
        self.assertEqual(ok_group["requested"], 1)
        self.assertEqual(ok_group["matched"], 1)
        self.assertTrue(ok_group["ok"])
        self.assertIsNone(ok_group["error"])
        self.assertIn(chem_id, result["errors_by_id"])
        self.assertIn("Live Atlas request failed", result["errors_by_id"][chem_id])
        self.assertNotIn(chem_id, result["verified_by_id"])
        verified = result["verified_by_id"][math_id]
        self.assertEqual(verified["enrollment_status"], "Open")
        self.assertIsNone(verified["seats_available"])
        self.upsert.assert_not_called()
        self.details_fetch.assert_not_called()

    def test_two_subjects_cause_exactly_two_searches(self):
        chem_ids = [
            build_section_id(TERM, "CHEM", "150", "2760", "1"),
            build_section_id(TERM, "CHEM", "151", "2761", "2"),
            build_section_id(TERM, "CHEM", "152", "2762", "3"),
        ]
        biol_ids = [
            build_section_id(TERM, "BIOL", "140", "3010", "1"),
            build_section_id(TERM, "BIOL", "141", "3011", "2"),
        ]
        rows = [
            _live_row("CHEM", "150", "2760", "1", "O", atlas_key="k-chem150"),
            _live_row("CHEM", "151", "2761", "2", "C", atlas_key="k-chem151"),
            _live_row("CHEM", "152", "2762", "3", "O", atlas_key="k-chem152"),
            _live_row("BIOL", "140", "3010", "1", "W", atlas_key="k-biol140"),
            _live_row("BIOL", "141", "3011", "2", "O", atlas_key="k-biol141"),
        ]
        fake_fetch = _fake_fetch_by_subject({
            "CHEM": {"term": TERM, "subject": "CHEM", "sections": rows[:3], "count": 3},
            "BIOL": {"term": TERM, "subject": "BIOL", "sections": rows[3:], "count": 2},
        })
        with mock.patch.object(
            atlas_live_verification, "fetch_live_subject_sections", fake_fetch
        ):
            result = atlas_live_verification.verify_sections_by_ids(
                chem_ids + biol_ids
            )

        self.assertEqual(fake_fetch.calls, [(TERM, "CHEM"), (TERM, "BIOL")])
        self.assertEqual(result["errors_by_id"], {})
        self.assertEqual(set(result["verified_by_id"]), set(chem_ids + biol_ids))
        self.assertEqual(len(result["groups"]), 2)
        self.assertEqual(result["groups"][0]["subject"], "CHEM")
        self.assertEqual(result["groups"][1]["subject"], "BIOL")
        for group, expected_count in zip(result["groups"], (3, 2)):
            self.assertEqual(group["requested"], expected_count)
            self.assertEqual(group["matched"], expected_count)
            self.assertTrue(group["ok"])
            self.assertIsNone(group["error"])

    def test_concurrent_same_group_calls_share_one_search(self):
        rows = [
            _live_row("CHEM", "150", "2760", "1", "C", atlas_key="k-chem150"),
            _live_row("CHEM", "151", "2761", "2", "O", atlas_key="k-chem151"),
        ]
        ids = [row["id"] for row in rows]
        search_result = {
            "term": TERM,
            "subject": "CHEM",
            "sections": rows,
            "count": len(rows),
        }
        owner_started = threading.Event()
        release = threading.Event()
        waiter_seen = threading.Event()
        invocations = []
        results = {}
        failures = {}

        def fake_fetch(term, subject, timeout=15):
            invocations.append((term, subject))
            owner_started.set()
            release.wait(timeout=5)
            return search_result

        class TrackedInflight(dict):
            def get(self, key, default=None):
                value = super().get(key, default)
                if value is not None:
                    waiter_seen.set()
                return value

        tracked = TrackedInflight()

        def worker(name):
            try:
                results[name] = atlas_live_verification.verify_sections_by_ids(ids)
            except Exception as exc:
                failures[name] = exc

        with mock.patch.object(
            atlas_live_verification, "fetch_live_subject_sections", fake_fetch
        ), mock.patch.object(atlas_live_verification, "_inflight", tracked):
            threads = [
                threading.Thread(target=worker, args=(name,)) for name in ("a", "b")
            ]
            threads[0].start()
            self.assertTrue(owner_started.wait(timeout=5))
            threads[1].start()
            self.assertTrue(waiter_seen.wait(timeout=5))
            release.set()
            for thread in threads:
                thread.join(timeout=10)

        self.assertEqual(failures, {})
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(invocations, [(TERM, "CHEM")])
        self.assertEqual(results["a"]["errors_by_id"], {})
        self.assertEqual(results["b"]["errors_by_id"], {})
        self.assertEqual(
            results["a"]["verified_by_id"], results["b"]["verified_by_id"]
        )
        self.assertEqual(set(results["a"]["verified_by_id"]), set(ids))
        for name in ("a", "b"):
            self.assertEqual(len(results[name]["groups"]), 1)
            group = results[name]["groups"][0]
            self.assertEqual(group["requested"], 2)
            self.assertEqual(group["matched"], 2)
            self.assertTrue(group["ok"])
            self.assertIsNone(group["error"])
        closed_verified = results["a"]["verified_by_id"][ids[0]]
        self.assertEqual(closed_verified["enrollment_status"], "Closed")
        self.assertEqual(closed_verified["seats_available"], 0)
        self.assertEqual(tracked, {})

    def test_concurrent_same_group_exception_is_shared(self):
        section_id = build_section_id(TERM, "CHEM", "150", "2760", "1")
        ids = [section_id]
        owner_started = threading.Event()
        release = threading.Event()
        waiter_seen = threading.Event()
        invocations = []
        results = {}
        failures = {}

        def fake_fetch(term, subject, timeout=15):
            invocations.append((term, subject))
            owner_started.set()
            release.wait(timeout=5)
            raise RuntimeError("boom")

        class TrackedInflight(dict):
            def get(self, key, default=None):
                value = super().get(key, default)
                if value is not None:
                    waiter_seen.set()
                return value

        tracked = TrackedInflight()

        def worker(name):
            try:
                results[name] = atlas_live_verification.verify_sections_by_ids(ids)
            except Exception as exc:
                failures[name] = exc

        with mock.patch.object(
            atlas_live_verification, "fetch_live_subject_sections", fake_fetch
        ), mock.patch.object(atlas_live_verification, "_inflight", tracked):
            threads = [
                threading.Thread(target=worker, args=(name,)) for name in ("a", "b")
            ]
            threads[0].start()
            self.assertTrue(owner_started.wait(timeout=5))
            threads[1].start()
            self.assertTrue(waiter_seen.wait(timeout=5))
            release.set()
            for thread in threads:
                thread.join(timeout=10)

        self.assertEqual(failures, {})
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(invocations, [(TERM, "CHEM")])
        expected_error = "Live Atlas request failed: boom"
        for name in ("a", "b"):
            self.assertEqual(results[name]["errors_by_id"], {section_id: expected_error})
            self.assertEqual(results[name]["verified_by_id"], {})
            self.assertEqual(len(results[name]["groups"]), 1)
            group = results[name]["groups"][0]
            self.assertEqual(group["requested"], 1)
            self.assertEqual(group["matched"], 0)
            self.assertFalse(group["ok"])
            self.assertEqual(group["error"], expected_error)
        self.assertEqual(tracked, {})

    def test_section_id_overflow_marks_only_extra_ids(self):
        ids = [
            build_section_id(TERM, "CHEM", str(100 + index), str(2000 + index), "1")
            for index in range(121)
        ]
        rows = [
            _live_row(
                "CHEM", str(100 + index), str(2000 + index), "1", "O",
                atlas_key=f"key-{index}",
            )
            for index in range(120)
        ]
        fake_fetch = _fake_fetch_by_subject(
            {"CHEM": {"term": TERM, "subject": "CHEM", "sections": rows, "count": 120}}
        )
        with mock.patch.object(
            atlas_live_verification, "fetch_live_subject_sections", fake_fetch
        ):
            result = atlas_live_verification.verify_sections_by_ids(ids)

        self.assertEqual(len(fake_fetch.calls), 1)
        self.assertEqual(set(result["verified_by_id"]), set(ids[:120]))
        self.assertEqual(
            result["errors_by_id"], {SECTION_IDS_OVERFLOW_KEY: SECTION_LIMIT_MESSAGE}
        )
        self.assertEqual(len(result["groups"]), 1)
        self.assertEqual(len(result["groups"][0]["section_ids"]), 120)
        group = result["groups"][0]
        self.assertEqual(group["requested"], 120)
        self.assertEqual(group["matched"], 120)
        self.assertTrue(group["ok"])
        self.assertIsNone(group["error"])
        self.assertEqual(result["limits"]["max_section_ids"], 120)

    def test_group_overflow_marks_only_extra_groups(self):
        subjects = [f"S{index:02d}" for index in range(1, 26)]
        ids = []
        for index, subject in enumerate(subjects):
            ids.append(build_section_id(TERM, subject, "100", str(3000 + index), "1"))
            ids.append(build_section_id(TERM, subject, "200", str(4000 + index), "1"))
        rows_by_subject = {}
        for index, subject in enumerate(subjects[:24]):
            rows_by_subject[subject] = [
                _live_row(subject, "100", str(3000 + index), "1", "O", atlas_key=f"key-{index}-a"),
                _live_row(subject, "200", str(4000 + index), "2", "C", atlas_key=f"key-{index}-b"),
            ]
        fake_fetch = _fake_fetch_by_subject({
            subject: {"term": TERM, "subject": subject, "sections": rows, "count": len(rows)}
            for subject, rows in rows_by_subject.items()
        })
        with mock.patch.object(
            atlas_live_verification, "fetch_live_subject_sections", fake_fetch
        ):
            result = atlas_live_verification.verify_sections_by_ids(ids)

        overflow_ids = ids[-2:]
        expected_verified = set(ids) - set(overflow_ids)
        self.assertEqual(len(fake_fetch.calls), 24)
        self.assertEqual(
            set(fake_fetch.calls), {(TERM, subject) for subject in subjects[:24]}
        )
        self.assertEqual(set(result["verified_by_id"]), expected_verified)
        self.assertEqual(
            result["errors_by_id"],
            {GROUPS_OVERFLOW_KEY: GROUP_LIMIT_MESSAGE},
        )
        self.assertEqual(len(result["groups"]), 24)
        for group in result["groups"]:
            self.assertEqual(group["requested"], 2)
            self.assertEqual(group["matched"], 2)
            self.assertTrue(group["ok"])
            self.assertIsNone(group["error"])
        self.assertEqual(result["limits"]["max_groups"], 24)

    def test_details_capped_at_twelve_and_never_for_closed(self):
        rows = []
        detail_ids = []
        for index in range(14):
            row = _live_row(
                "CHEM", str(100 + index), str(5000 + index), "1", "O",
                atlas_key=f"key-open-{index:02d}",
            )
            rows.append(row)
            detail_ids.append(row["id"])
        closed = _live_row("CHEM", "900", "5999", "9", "C", atlas_key="key-closed")
        waitlist = _live_row("CHEM", "901", "5998", "8", "W", atlas_key="key-waitlist")
        rows.extend([closed, waitlist])
        detail_ids = [closed["id"], waitlist["id"]] + detail_ids

        seats_by_key = {
            row["atlas_key"]: 12 + index for index, row in enumerate(rows)
        }
        status_text_by_key = {
            row["atlas_key"]: row["enrollment_status"] for row in rows
        }

        def fake_details(term, atlas_key, timeout=15):
            self.assertEqual(term, TERM)
            if atlas_key not in seats_by_key:
                return {"error": f"Unexpected details key {atlas_key}"}
            return _details_payload(
                atlas_key,
                status_text_by_key[atlas_key],
                seats_by_key[atlas_key],
            )

        fake_fetch = _fake_fetch_by_subject(
            {"CHEM": {"term": TERM, "subject": "CHEM", "sections": rows, "count": len(rows)}}
        )
        with mock.patch.object(
            atlas_live_verification, "fetch_live_subject_sections", fake_fetch
        ):
            self.details_fetch.side_effect = fake_details
            result = atlas_live_verification.verify_sections_by_ids(
                [row["id"] for row in rows], detail_ids=detail_ids
            )

        called_keys = [call.args[1] for call in self.details_fetch.call_args_list]
        self.assertEqual(self.details_fetch.call_count, 12)
        self.assertNotIn("key-closed", called_keys)
        self.assertIn("key-waitlist", called_keys)
        remaining_keys = [key for key in called_keys if key != "key-waitlist"]
        self.assertEqual(len(remaining_keys), 11)
        self.assertTrue(
            all(key.startswith("key-open-") for key in remaining_keys),
            f"Non-open key fetched for details: {remaining_keys}",
        )
        self.assertEqual(
            set(remaining_keys), {f"key-open-{index:02d}" for index in range(11)}
        )
        self.assertEqual(result["errors_by_id"], {})
        self.assertEqual(
            result["detail_errors_by_id"],
            {
                closed["id"]: DETAIL_STATUS_MESSAGE,
                DETAIL_IDS_OVERFLOW_KEY: DETAIL_LIMIT_MESSAGE,
            },
        )
        self.assertEqual(len(result["details_by_id"]), 12)
        for section_id, merged in result["details_by_id"].items():
            self.assertGreater(merged["seats_available"], 0)
            self.assertIn(merged["enrollment_status"], {"Open", "Waitlist"})
            expected_seats = seats_by_key[
                next(row["atlas_key"] for row in rows if row["id"] == section_id)
            ]
            self.assertEqual(merged["seats_available"], expected_seats)
        closed_id = closed["id"]
        self.assertNotIn(closed_id, result["details_by_id"])
        self.assertNotIn(closed_id, result["errors_by_id"])
        self.assertEqual(self.upsert.call_count, 12)

    def test_snapshot_persistence_failure_keeps_details(self):
        section_id = build_section_id(TERM, "CHEM", "150", "2760", "1")
        row = _live_row("CHEM", "150", "2760", "1", "O", atlas_key="key-chem150")
        fake_fetch = _fake_fetch_by_subject({"CHEM": {"sections": [row]}})
        self.upsert.side_effect = RuntimeError("snapshot write failed")
        self.details_fetch.side_effect = (
            lambda term, atlas_key, timeout=15: _details_payload(atlas_key, "Open", 5)
        )
        with mock.patch.object(
            atlas_live_verification, "fetch_live_subject_sections", fake_fetch
        ), mock.patch.object(atlas_live_verification.logger, "exception") as log_exc:
            result = atlas_live_verification.verify_sections_by_ids(
                [section_id], detail_ids=[section_id]
            )

        self.assertEqual(result["errors_by_id"], {})
        self.assertEqual(result["detail_errors_by_id"], {})
        merged = result["details_by_id"][section_id]
        self.assertEqual(merged["seats_available"], 5)
        self.assertEqual(merged["enrollment_status"], "Open")
        self.assertEqual(self.upsert.call_count, 1)
        log_exc.assert_called_once()

    def test_raw_status_codes_normalize_to_open_closed_waitlist(self):
        raw_rows = [
            {"code": "CHEM 150", "crn": "2760", "no": "1", "enrl_stat": "O", "key": "k-o"},
            {"code": "CHEM 151", "crn": "2761", "no": "2", "enrl_stat": "C", "key": "k-c"},
            {"code": "CHEM 152", "crn": "2762", "no": "3", "enrl_stat": "W", "key": "k-w"},
        ]
        response = mock.Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"results": raw_rows}

        ids = [
            build_section_id(TERM, "CHEM", "150", "2760", "1"),
            build_section_id(TERM, "CHEM", "151", "2761", "2"),
            build_section_id(TERM, "CHEM", "152", "2762", "3"),
        ]
        with mock.patch.object(atlas_client, "_discover_terms", return_value=["Fall_2026"]), \
                mock.patch.object(atlas_client, "requests") as requests_mock:
            requests_mock.post.return_value = response
            result = atlas_live_verification.verify_sections_by_ids(ids)

        self.assertEqual(result["errors_by_id"], {})
        expected = {
            ids[0]: ("Open", None, "k-o"),
            ids[1]: ("Closed", 0, "k-c"),
            ids[2]: ("Waitlist", None, "k-w"),
        }
        for section_id, (status, seats, atlas_key) in expected.items():
            verified = result["verified_by_id"][section_id]
            self.assertEqual(verified["enrollment_status"], status)
            self.assertEqual(verified["seats_available"], seats)
            self.assertEqual(verified["atlas_key"], atlas_key)
        self.details_fetch.assert_not_called()
        self.upsert.assert_not_called()

    def test_process_wide_pacing(self):
        self.pace_patcher.stop()
        atlas_live_verification._last_atlas_call_monotonic = None
        self.time.monotonic.side_effect = [1000.0, 1000.0, 1000.3]

        atlas_live_verification._pace_atlas_call()

        self.assertEqual(atlas_live_verification._last_atlas_call_monotonic, 1000.0)
        self.time.monotonic.assert_called_once_with()
        self.time.sleep.assert_not_called()

        atlas_live_verification._pace_atlas_call()

        self.assertEqual(self.time.sleep.call_count, 1)
        waited = self.time.sleep.call_args.args[0]
        self.assertAlmostEqual(
            waited, atlas_live_verification.CALL_SPACING_SECONDS, places=12
        )
        self.assertEqual(self.time.monotonic.call_count, 3)
        self.assertEqual(atlas_live_verification._last_atlas_call_monotonic, 1000.3)

    def test_singleflight_waiter_uses_timed_wait_honoring_deadline(self):
        key = (TERM, "CHEM")
        event = mock.Mock()
        event.wait.return_value = False
        entry = {"event": event, "result": None}
        inflight = {key: entry}
        self.time.monotonic.side_effect = [1000.0]
        with mock.patch.object(atlas_live_verification, "_inflight", inflight), \
                mock.patch.object(
                    atlas_live_verification, "fetch_live_subject_sections"
                ) as fetch:
            result, owned = atlas_live_verification._singleflight_subject_fetch(
                TERM, "CHEM", timeout=10, deadline=1000.0
            )
            self.assertEqual(
                inflight, {key: entry},
                "a waiter must not remove the owner's in-flight entry",
            )

        self.assertFalse(owned)
        event.wait.assert_called_once_with(timeout=0)
        self.assertEqual(result, {"error": DEADLINE_SECTION_MESSAGE})
        fetch.assert_not_called()

    def test_singleflight_waiter_waits_only_for_remaining_deadline(self):
        key = (TERM, "CHEM")
        event = mock.Mock()
        event.wait.return_value = True
        entry = {"event": event, "result": {"sections": []}}
        self.time.monotonic.side_effect = [995.0]
        with mock.patch.object(
            atlas_live_verification, "_inflight", {key: entry}
        ):
            result, owned = atlas_live_verification._singleflight_subject_fetch(
                TERM, "CHEM", timeout=10, deadline=1000.0
            )

        self.assertFalse(owned)
        event.wait.assert_called_once_with(timeout=5)
        self.assertEqual(result, {"sections": []})

    def test_singleflight_waiter_timeout_prefers_published_result(self):
        key = (TERM, "CHEM")
        event = mock.Mock()
        event.wait.return_value = False
        entry = {"event": event, "result": {"sections": []}}
        self.time.monotonic.side_effect = [1000.0]
        with mock.patch.object(
            atlas_live_verification, "_inflight", {key: entry}
        ):
            result, owned = atlas_live_verification._singleflight_subject_fetch(
                TERM, "CHEM", timeout=10, deadline=1000.0
            )

        self.assertFalse(owned)
        event.wait.assert_called_once_with(timeout=0)
        self.assertEqual(result, {"sections": []})

    def test_singleflight_owner_deadline_expiry_during_pacing_skips_fetch(self):
        self.time.monotonic.side_effect = [100.0]
        with mock.patch.object(atlas_live_verification, "_inflight", {}), \
                mock.patch.object(
                    atlas_live_verification, "fetch_live_subject_sections"
                ) as fetch:
            result, owned = atlas_live_verification._singleflight_subject_fetch(
                TERM, "CHEM", timeout=10, deadline=100.0
            )
            self.assertEqual(
                atlas_live_verification._inflight, {},
                "an expired owner must still clean up its in-flight entry",
            )

        self.assertTrue(owned)
        self.assertEqual(result, {"error": DEADLINE_SECTION_MESSAGE})
        fetch.assert_not_called()

    def test_singleflight_owner_recomputes_timeout_after_pacing(self):
        self.time.monotonic.side_effect = [95.0]
        fetch = mock.Mock(return_value={"sections": []})
        with mock.patch.object(atlas_live_verification, "_inflight", {}), \
                mock.patch.object(
                    atlas_live_verification, "fetch_live_subject_sections", fetch
                ):
            result, owned = atlas_live_verification._singleflight_subject_fetch(
                TERM, "CHEM", timeout=10, deadline=100.0
            )

        self.assertTrue(owned)
        self.assertEqual(result, {"sections": []})
        fetch.assert_called_once_with(TERM, "CHEM", timeout=5.0)


    def test_deadline_expires_before_first_group_marks_all_ids(self):
        ids = [
            build_section_id(TERM, "CHEM", "150", "2760", "1"),
            build_section_id(TERM, "CHEM", "151", "2761", "2"),
        ]
        fake_fetch = _fake_fetch_by_subject({"CHEM": {"sections": []}})
        self.time.monotonic.side_effect = [100.0, 200.0]
        with mock.patch.object(
            atlas_live_verification, "fetch_live_subject_sections", fake_fetch
        ):
            result = atlas_live_verification.verify_sections_by_ids(
                ids, detail_ids=[ids[0]]
            )

        self.assertEqual(fake_fetch.calls, [])
        self.assertEqual(result["verified_by_id"], {})
        self.assertEqual(
            result["errors_by_id"],
            {section_id: DEADLINE_SECTION_MESSAGE for section_id in ids},
        )
        self.assertEqual(len(result["groups"]), 1)
        group = result["groups"][0]
        self.assertEqual(group["requested"], 2)
        self.assertEqual(group["matched"], 0)
        self.assertFalse(group["ok"])
        self.assertEqual(group["error"], DEADLINE_SECTION_MESSAGE)
        self.assertEqual(result["details_by_id"], {})
        self.assertEqual(
            result["detail_errors_by_id"], {ids[0]: DETAIL_UNVERIFIED_MESSAGE}
        )
        self.details_fetch.assert_not_called()
        self.upsert.assert_not_called()

    def test_deadline_expires_between_groups_uses_remaining_timeout(self):
        chem_id = build_section_id(TERM, "CHEM", "150", "2760", "1")
        math_id = build_section_id(TERM, "MATH", "140", "3010", "2")
        chem_row = _live_row("CHEM", "150", "2760", "1", "O", atlas_key="key-chem150")
        fetch_calls = []

        def fake_fetch(term, subject, timeout=15):
            fetch_calls.append((term, subject, timeout))
            return {"sections": [chem_row]} if subject == "CHEM" else {"sections": []}

        self.time.monotonic.side_effect = [100.0, 115.0, 115.0, 999.0]
        with mock.patch.object(
            atlas_live_verification, "fetch_live_subject_sections", fake_fetch
        ):
            result = atlas_live_verification.verify_sections_by_ids(
                [chem_id, math_id], timeout=25
            )

        self.assertEqual(len(fetch_calls), 1)
        self.assertEqual(fetch_calls[0][0], TERM)
        self.assertEqual(fetch_calls[0][1], "CHEM")
        self.assertAlmostEqual(fetch_calls[0][2], 15.0, places=9)
        self.assertEqual(set(result["verified_by_id"]), {chem_id})
        self.assertEqual(result["errors_by_id"], {math_id: DEADLINE_SECTION_MESSAGE})
        self.assertEqual(len(result["groups"]), 2)
        self.assertTrue(result["groups"][0]["ok"])
        self.assertFalse(result["groups"][1]["ok"])
        self.assertEqual(result["groups"][1]["matched"], 0)
        self.assertEqual(result["groups"][1]["error"], DEADLINE_SECTION_MESSAGE)
        self.assertEqual(result["detail_errors_by_id"], {})

    def test_deadline_expires_during_owner_pacing_marks_group_without_fetch(self):
        section_id = build_section_id(TERM, "CHEM", "150", "2760", "1")
        fake_fetch = _fake_fetch_by_subject({"CHEM": {"sections": []}})
        self.time.monotonic.side_effect = [100.0, 100.0, 130.0]
        with mock.patch.object(
            atlas_live_verification, "fetch_live_subject_sections", fake_fetch
        ):
            result = atlas_live_verification.verify_sections_by_ids([section_id])

        self.assertEqual(fake_fetch.calls, [])
        self.assertEqual(result["verified_by_id"], {})
        self.assertEqual(
            result["errors_by_id"], {section_id: DEADLINE_SECTION_MESSAGE}
        )
        self.assertEqual(len(result["groups"]), 1)
        group = result["groups"][0]
        self.assertEqual(group["requested"], 1)
        self.assertEqual(group["matched"], 0)
        self.assertFalse(group["ok"])
        self.assertEqual(group["error"], DEADLINE_SECTION_MESSAGE)
        self.assertEqual(atlas_live_verification._inflight, {})

    def test_deadline_expires_before_details_keeps_verified_authoritative(self):
        section_id = build_section_id(TERM, "CHEM", "150", "2760", "1")
        row = _live_row("CHEM", "150", "2760", "1", "O", atlas_key="key-chem150")
        fake_fetch = _fake_fetch_by_subject({"CHEM": {"sections": [row]}})
        self.time.monotonic.side_effect = [100.0, 101.0, 101.0, 999.0]
        with mock.patch.object(
            atlas_live_verification, "fetch_live_subject_sections", fake_fetch
        ):
            result = atlas_live_verification.verify_sections_by_ids(
                [section_id], detail_ids=[section_id]
            )

        self.assertEqual(fake_fetch.calls, [(TERM, "CHEM")])
        self.assertEqual(set(result["verified_by_id"]), {section_id})
        self.assertEqual(result["errors_by_id"], {})
        self.assertEqual(result["details_by_id"], {})
        self.assertEqual(
            result["detail_errors_by_id"], {section_id: DEADLINE_DETAILS_MESSAGE}
        )
        self.details_fetch.assert_not_called()
        self.upsert.assert_not_called()

    def test_detail_call_timeout_uses_min_of_timeout_and_remaining(self):
        section_id = build_section_id(TERM, "CHEM", "150", "2760", "1")
        row = _live_row("CHEM", "150", "2760", "1", "O", atlas_key="key-chem150")
        fake_fetch = _fake_fetch_by_subject({"CHEM": {"sections": [row]}})
        self.time.monotonic.side_effect = [100.0, 101.0, 121.0, 126.0, 126.0]
        detail_timeouts = []
        self.details_fetch.side_effect = (
            lambda term, atlas_key, timeout=15: detail_timeouts.append(timeout)
            or _details_payload(atlas_key, "Open", 5)
        )
        with mock.patch.object(
            atlas_live_verification, "fetch_live_subject_sections", fake_fetch
        ):
            result = atlas_live_verification.verify_sections_by_ids(
                [section_id], detail_ids=[section_id], timeout=10
            )

        self.assertEqual(detail_timeouts, [4.0])
        self.assertEqual(result["errors_by_id"], {})
        self.assertEqual(result["detail_errors_by_id"], {})
        self.assertEqual(set(result["verified_by_id"]), {section_id})
        self.assertEqual(result["details_by_id"][section_id]["seats_available"], 5)

    def test_mixed_verified_status_with_failed_details(self):
        closed = _live_row("CHEM", "150", "2760", "1", "C", atlas_key="key-closed")
        open_ok = _live_row("CHEM", "151", "2761", "2", "O", atlas_key="key-ok")
        open_fail = _live_row("CHEM", "152", "2762", "3", "O", atlas_key="key-fail")
        rows = [closed, open_ok, open_fail]
        ids = [row["id"] for row in rows]
        fake_fetch = _fake_fetch_by_subject(
            {"CHEM": {"term": TERM, "subject": "CHEM", "sections": rows, "count": 3}}
        )

        def fake_details(term, atlas_key, timeout=15):
            if atlas_key == "key-ok":
                return _details_payload("key-ok", "Open", 5)
            return {"error": "Atlas details exploded"}

        with mock.patch.object(
            atlas_live_verification, "fetch_live_subject_sections", fake_fetch
        ):
            self.details_fetch.side_effect = fake_details
            result = atlas_live_verification.verify_sections_by_ids(
                ids, detail_ids=[open_ok["id"], open_fail["id"]]
            )

        self.assertEqual(set(result["verified_by_id"]), set(ids))
        self.assertEqual(result["verified_by_id"][closed["id"]]["seats_available"], 0)
        self.assertEqual(result["errors_by_id"], {})
        self.assertEqual(set(result["details_by_id"]), {open_ok["id"]})
        self.assertEqual(result["details_by_id"][open_ok["id"]]["seats_available"], 5)
        self.assertEqual(
            result["detail_errors_by_id"],
            {open_fail["id"]: "Atlas details exploded"},
        )
        self.assertEqual(self.upsert.call_count, 1)

    def test_detail_ineligible_cases_reported_explicitly(self):
        open_ok = _live_row("CHEM", "150", "2760", "1", "O", atlas_key="key-open")
        closed = _live_row("CHEM", "151", "2761", "2", "C", atlas_key="key-closed")
        rows = [open_ok, closed]
        open_id = open_ok["id"]
        closed_id = closed["id"]
        missing_id = build_section_id(TERM, "MATH", "140", "9999", "9")
        fake_fetch = _fake_fetch_by_subject(
            {"CHEM": {"term": TERM, "subject": "CHEM", "sections": rows, "count": 2}}
        )
        self.details_fetch.side_effect = (
            lambda term, atlas_key, timeout=15: _details_payload(atlas_key, "Open", 7)
        )
        with mock.patch.object(
            atlas_live_verification, "fetch_live_subject_sections", fake_fetch
        ):
            result = atlas_live_verification.verify_sections_by_ids(
                [open_id, closed_id],
                detail_ids=["not-a-section", missing_id, closed_id, open_id],
            )

        self.assertEqual(result["errors_by_id"], {})
        self.assertEqual(
            result["detail_errors_by_id"],
            {
                "not-a-section": DETAIL_INVALID_MESSAGE,
                missing_id: DETAIL_UNVERIFIED_MESSAGE,
                closed_id: DETAIL_STATUS_MESSAGE,
            },
        )
        self.assertEqual(set(result["details_by_id"]), {open_id})
        self.assertEqual(set(result["verified_by_id"]), {open_id, closed_id})
        self.assertEqual(self.upsert.call_count, 1)


if __name__ == "__main__":
    unittest.main()
