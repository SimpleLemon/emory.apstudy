import os
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

from appwrite.exception import AppwriteException

from services import course_tracking


class CourseTrackingTests(unittest.TestCase):
    def _track(self, row_id, user_id, section_id="Fall_2026-CS-170-1234-1"):
        return {
            "$id": row_id,
            "user_id": user_id,
            "section_id": section_id,
            "term": "Fall_2026",
            "subject": "CS",
            "catalog": "170",
            "crn": "1234",
            "course_code": "CS 170",
            "course_title": "Intro CS",
            "enabled": True,
            "last_status": "Closed",
            "last_seats_available": 0,
        }

    def _open_section(self):
        return {
            "id": "Fall_2026-CS-170-1234-1",
            "term": "Fall_2026",
            "subject": "CS",
            "catalog_number": "170",
            "crn": "1234",
            "course_code": "CS 170",
            "course_title": "Intro CS",
            "section_number": "1",
            "instructor": "Ada Lovelace",
            "enrollment_status": "Open",
            "seats_available": 2,
        }

    def _closed_section(self):
        return {
            **self._open_section(),
            "enrollment_status": "Closed",
            "seats_available": 0,
        }

    def test_no_enabled_tracks_skips_atlas_ping(self):
        with patch.dict(course_tracking.COLLECTIONS, {"course_seat_tracks": "tracks"}, clear=False), \
                patch.object(course_tracking, "list_rows_all", return_value=[]), \
                patch.object(course_tracking, "fetch_live_section_status") as fetch_status, \
                patch.object(course_tracking, "emit_course_track_event") as emit_event, \
                patch.object(course_tracking, "update_course_tracks_channel_topic") as update_topic:
            notified = course_tracking.check_course_seat_tracks()

        self.assertEqual(notified, 0)
        fetch_status.assert_not_called()
        emit_event.assert_called_once()
        self.assertEqual(emit_event.call_args.args[0], "Automated Course Track Poll Skipped")
        metadata = emit_event.call_args.kwargs["metadata"]
        self.assertEqual(metadata["reason"], "no_enabled_tracks")
        self.assertEqual(metadata["track_count"], 0)
        update_topic.assert_called_once_with(0)

    def test_missing_collection_mapping_emits_skipped_poll_log(self):
        with patch.dict(course_tracking.COLLECTIONS, {"course_seat_tracks": ""}, clear=False), \
                patch.object(course_tracking, "list_rows_all") as list_rows, \
                patch.object(course_tracking, "emit_course_track_event") as emit_event:
            notified = course_tracking.check_course_seat_tracks()

        self.assertEqual(notified, 0)
        list_rows.assert_not_called()
        emit_event.assert_called_once()
        self.assertEqual(emit_event.call_args.args[0], "Automated Course Track Poll Skipped")
        self.assertEqual(emit_event.call_args.kwargs["metadata"]["reason"], "collection_mapping_missing")

    def test_list_tracks_failure_emits_failed_poll_log(self):
        with patch.dict(course_tracking.COLLECTIONS, {"course_seat_tracks": "tracks"}, clear=False), \
                patch.object(course_tracking, "list_rows_all", side_effect=AppwriteException("Appwrite unavailable")), \
                patch.object(course_tracking, "fetch_live_section_status") as fetch_status, \
                patch.object(course_tracking, "emit_course_track_event") as emit_event:
            notified = course_tracking.check_course_seat_tracks()

        self.assertEqual(notified, 0)
        fetch_status.assert_not_called()
        emit_event.assert_called_once()
        self.assertEqual(emit_event.call_args.args[0], "Automated Course Track Poll Failed")
        self.assertIn("Appwrite unavailable", emit_event.call_args.kwargs["metadata"]["error"])

    def test_duplicate_tracks_share_one_atlas_ping_and_one_check_log(self):
        tracks = [self._track("track-1", "user-1"), self._track("track-2", "user-2")]
        section = self._open_section()

        with patch.dict(course_tracking.COLLECTIONS, {"course_seat_tracks": "tracks"}, clear=False), \
                patch.object(course_tracking, "list_rows_all", return_value=tracks), \
                patch.object(course_tracking, "fetch_live_section_status", return_value={"section": section}) as fetch_status, \
                patch.object(course_tracking.notifications, "preferences", return_value={"course_email_enabled": True, "course_push_enabled": False}), \
                patch.object(course_tracking, "_send_open_email") as send_email, \
                patch.object(course_tracking, "update_row_safe", side_effect=lambda table, row_id, data: {"$id": row_id, **data}) as update_row, \
                patch.object(course_tracking, "emit_course_track_event") as emit_event, \
                patch.object(course_tracking, "update_course_tracks_channel_topic") as update_topic, \
                patch.object(course_tracking, "_now_utc", return_value=datetime(2026, 5, 29, tzinfo=timezone.utc)):
            notified = course_tracking.check_course_seat_tracks()

        self.assertEqual(notified, 2)
        fetch_status.assert_called_once_with("Fall_2026", "CS", "170", crn="1234")
        self.assertEqual(send_email.call_count, 2)
        self.assertEqual(update_row.call_count, 2)
        self.assertEqual({call.args[1] for call in update_row.call_args_list}, {"track-1", "track-2"})

        checked_events = [
            call for call in emit_event.call_args_list
            if call.args[0] == "Automated Course Track Checked"
        ]
        self.assertEqual(len(checked_events), 1)
        metadata = checked_events[0].kwargs["metadata"]
        self.assertEqual(metadata["track_count"], 2)
        self.assertEqual(metadata["user_count"], 2)
        self.assertEqual(metadata["request_source"], "automated")

        completed_events = [
            call for call in emit_event.call_args_list
            if call.args[0] == "Automated Course Track Poll Completed"
        ]
        self.assertEqual(len(completed_events), 0)
        summary = course_tracking.get_last_course_tracking_poll()
        self.assertEqual(summary["event_title"], "Automated Course Track Poll Completed")
        self.assertEqual(summary["track_count"], 2)
        self.assertEqual(summary["section_group_count"], 1)
        self.assertEqual(summary["atlas_checks_attempted"], 1)
        self.assertEqual(summary["atlas_checks_succeeded"], 1)
        self.assertEqual(summary["atlas_checks_failed"], 0)
        self.assertEqual(summary["row_updates"], 2)
        self.assertEqual(summary["email_notifications"], 2)
        self.assertEqual(summary["changed_rows_written"], 2)
        self.assertEqual(summary["unchanged_rows_skipped"], 0)
        self.assertEqual(summary["notifications_sent"], 2)
        update_topic.assert_called_once_with(1)

    def test_unchanged_closed_result_schedules_next_check(self):
        tracks = [self._track("track-1", "user-1"), self._track("track-2", "user-2")]
        section = self._closed_section()

        with patch.dict(course_tracking.COLLECTIONS, {"course_seat_tracks": "tracks"}, clear=False), \
                patch.object(course_tracking, "list_rows_all", return_value=tracks), \
                patch.object(course_tracking, "fetch_live_section_status", return_value={"section": section}) as fetch_status, \
                patch.object(course_tracking, "_send_open_email") as send_email, \
                patch.object(course_tracking, "update_row_safe") as update_row, \
                patch.object(course_tracking, "emit_course_track_event") as emit_event, \
                patch.object(course_tracking, "_now_utc", return_value=datetime(2026, 5, 29, tzinfo=timezone.utc)):
            notified = course_tracking.check_course_seat_tracks()

        self.assertEqual(notified, 0)
        fetch_status.assert_called_once_with("Fall_2026", "CS", "170", crn="1234")
        send_email.assert_not_called()
        self.assertEqual(update_row.call_count, 2)
        self.assertTrue(all(call.args[2]["next_check_at"] == "2026-05-29T00:30:00Z" for call in update_row.call_args_list))

        completed_events = [
            call for call in emit_event.call_args_list
            if call.args[0] == "Automated Course Track Poll Completed"
        ]
        self.assertEqual(len(completed_events), 0)
        summary = course_tracking.get_last_course_tracking_poll()
        self.assertEqual(summary["row_updates"], 2)
        self.assertEqual(summary["changed_rows_written"], 2)
        self.assertEqual(summary["unchanged_rows_skipped"], 2)
        self.assertEqual(summary["notifications_sent"], 0)

    def test_unchanged_open_result_sends_no_reminder_and_schedules_next_check(self):
        tracks = [{
            **self._track("track-1", "user-1"),
            "last_status": "Open",
            "last_seats_available": 2,
        }]
        section = self._open_section()

        with patch.dict(course_tracking.COLLECTIONS, {"course_seat_tracks": "tracks"}, clear=False), \
                patch.object(course_tracking, "list_rows_all", return_value=tracks), \
                patch.object(course_tracking, "fetch_live_section_status", return_value={"section": section}), \
                patch.object(course_tracking, "_send_open_email") as send_email, \
                patch.object(course_tracking, "update_row_safe") as update_row, \
                patch.object(course_tracking, "emit_course_track_event") as emit_event, \
                patch.object(course_tracking, "_now_utc", return_value=datetime(2026, 5, 29, tzinfo=timezone.utc)):
            notified = course_tracking.check_course_seat_tracks()

        self.assertEqual(notified, 0)
        send_email.assert_not_called()
        update_row.assert_called_once()
        self.assertEqual(update_row.call_args.args[2]["next_check_at"], "2026-05-29T00:30:00Z")
        completed_events = [
            call for call in emit_event.call_args_list
            if call.args[0] == "Automated Course Track Poll Completed"
        ]
        self.assertEqual(len(completed_events), 0)
        summary = course_tracking.get_last_course_tracking_poll()
        self.assertEqual(summary["unchanged_rows_skipped"], 1)
        self.assertEqual(summary["notifications_sent"], 0)

    def test_open_seat_count_change_updates_without_repeat_notification(self):
        tracks = [{
            **self._track("track-1", "user-1"),
            "last_status": "Open",
            "last_seats_available": 1,
        }]
        section = self._open_section()

        with patch.dict(course_tracking.COLLECTIONS, {"course_seat_tracks": "tracks"}, clear=False), \
                patch.object(course_tracking, "list_rows_all", return_value=tracks), \
                patch.object(course_tracking, "fetch_live_section_status", return_value={"section": section}), \
                patch.object(course_tracking, "_send_open_email") as send_email, \
                patch.object(course_tracking, "update_row_safe", side_effect=lambda table, row_id, data: {"$id": row_id, **data}) as update_row, \
                patch.object(course_tracking, "emit_course_track_event") as emit_event, \
                patch.object(course_tracking, "_now_utc", return_value=datetime(2026, 5, 29, tzinfo=timezone.utc)):
            notified = course_tracking.check_course_seat_tracks()

        self.assertEqual(notified, 0)
        send_email.assert_not_called()
        update_row.assert_called_once()
        self.assertNotIn("last_notified_at", update_row.call_args.args[2])
        completed_events = [
            call for call in emit_event.call_args_list
            if call.args[0] == "Automated Course Track Poll Completed"
        ]
        self.assertEqual(len(completed_events), 0)
        summary = course_tracking.get_last_course_tracking_poll()
        self.assertEqual(summary["changed_rows_written"], 1)
        self.assertEqual(summary["notifications_sent"], 0)

    def test_open_transition_email_failure_does_not_write_open_state(self):
        tracks = [self._track("track-1", "user-1")]
        section = self._open_section()

        with patch.dict(course_tracking.COLLECTIONS, {"course_seat_tracks": "tracks"}, clear=False), \
                patch.object(course_tracking, "list_rows_all", return_value=tracks), \
                patch.object(course_tracking, "fetch_live_section_status", return_value={"section": section}), \
                patch.object(course_tracking.notifications, "preferences", return_value={"course_email_enabled": True, "course_push_enabled": False}), \
                patch.object(course_tracking, "_send_open_email", side_effect=RuntimeError("email failed")) as send_email, \
                patch.object(course_tracking, "update_row_safe") as update_row, \
                patch.object(course_tracking, "emit_course_track_event") as emit_event, \
                patch.object(course_tracking, "_now_utc", return_value=datetime(2026, 5, 29, tzinfo=timezone.utc)):
            notified = course_tracking.check_course_seat_tracks()

        self.assertEqual(notified, 0)
        send_email.assert_called_once()
        update_row.assert_not_called()
        completed_events = [
            call for call in emit_event.call_args_list
            if call.args[0] == "Automated Course Track Poll Completed"
        ]
        self.assertEqual(len(completed_events), 0)
        summary = course_tracking.get_last_course_tracking_poll()
        self.assertEqual(summary["email_failures"], 1)
        self.assertEqual(summary["changed_rows_written"], 0)
        self.assertEqual(summary["notifications_sent"], 0)

    def test_manual_filter_only_pings_matching_course_tracks(self):
        tracks = [
            self._track("track-1", "user-1"),
            {**self._track("track-2", "user-2"), "subject": "CHEM", "catalog": "150", "course_code": "CHEM 150"},
        ]
        section = {
            **self._open_section(),
            "subject": "CHEM",
            "catalog_number": "150",
            "course_code": "CHEM 150",
            "course_title": "Structure and Properties",
        }

        with patch.dict(course_tracking.COLLECTIONS, {"course_seat_tracks": "tracks"}, clear=False), \
                patch.object(course_tracking, "list_rows_all", return_value=tracks), \
                patch.object(course_tracking, "fetch_live_section_status", return_value={"section": section}) as fetch_status, \
                patch.object(course_tracking.notifications, "preferences", return_value={"course_email_enabled": True, "course_push_enabled": False}), \
                patch.object(course_tracking, "_send_open_email"), \
                patch.object(course_tracking, "update_row_safe", side_effect=lambda table, row_id, data: {"$id": row_id, **data}) as update_row, \
                patch.object(course_tracking, "emit_course_track_event") as emit_event, \
                patch.object(course_tracking, "_now_utc", return_value=datetime(2026, 5, 29, tzinfo=timezone.utc)):
            course_tracking.check_course_seat_tracks(
                term="Fall_2026",
                subject="CHEM",
                catalog="150",
                poll_source="manual_admin_test",
            )

        fetch_status.assert_called_once_with("Fall_2026", "CHEM", "150", crn="1234")
        update_row.assert_called_once()
        self.assertEqual(update_row.call_args.args[1], "track-2")
        completed_events = [
            call for call in emit_event.call_args_list
            if call.args[0] == "Automated Course Track Poll Completed"
        ]
        self.assertEqual(len(completed_events), 0)
        summary = course_tracking.get_last_course_tracking_poll()
        self.assertEqual(summary["poll_source"], "manual_admin_test")
        self.assertEqual(summary["track_count"], 1)

    def test_duplicate_track_failure_logs_once_and_updates_each_row(self):
        tracks = [self._track("track-1", "user-1"), self._track("track-2", "user-2")]

        with patch.dict(course_tracking.COLLECTIONS, {"course_seat_tracks": "tracks"}, clear=False), \
                patch.object(course_tracking, "list_rows_all", return_value=tracks), \
                patch.object(course_tracking, "fetch_live_section_status", return_value={"error": "Atlas unavailable"}) as fetch_status, \
                patch.object(course_tracking, "update_row_safe", side_effect=lambda table, row_id, data: {"$id": row_id, **data}) as update_row, \
                patch.object(course_tracking, "emit_course_track_event") as emit_event, \
                patch.object(course_tracking, "_now_utc", return_value=datetime(2026, 5, 29, tzinfo=timezone.utc)):
            notified = course_tracking.check_course_seat_tracks()

        self.assertEqual(notified, 0)
        fetch_status.assert_called_once_with("Fall_2026", "CS", "170", crn="1234")
        update_row.assert_not_called()

        failed_events = [
            call for call in emit_event.call_args_list
            if call.args[0] == "Automated Course Track Check Failed"
        ]
        self.assertEqual(len(failed_events), 1)
        metadata = failed_events[0].kwargs["metadata"]
        self.assertEqual(metadata["track_count"], 2)
        self.assertEqual(metadata["user_count"], 2)
        self.assertEqual(metadata["error"], "Atlas unavailable")

        completed_events = [
            call for call in emit_event.call_args_list
            if call.args[0] == "Automated Course Track Poll Completed"
        ]
        self.assertEqual(len(completed_events), 0)
        summary = course_tracking.get_last_course_tracking_poll()
        self.assertEqual(summary["track_count"], 2)
        self.assertEqual(summary["section_group_count"], 1)
        self.assertEqual(summary["atlas_checks_attempted"], 1)
        self.assertEqual(summary["atlas_checks_succeeded"], 0)
        self.assertEqual(summary["atlas_checks_failed"], 1)
        self.assertEqual(summary["row_updates"], 0)
        self.assertEqual(summary["email_notifications"], 0)
        self.assertEqual(summary["failed_rows_skipped"], 2)
        self.assertEqual(summary["changed_rows_written"], 0)

    def test_live_check_exception_logs_failure_and_completed_poll(self):
        tracks = [self._track("track-1", "user-1")]

        with patch.dict(course_tracking.COLLECTIONS, {"course_seat_tracks": "tracks"}, clear=False), \
                patch.object(course_tracking, "list_rows_all", return_value=tracks), \
                patch.object(course_tracking, "fetch_live_section_status", side_effect=RuntimeError("Atlas exploded token=secret")), \
                patch.object(course_tracking, "update_row_safe") as update_row, \
                patch.object(course_tracking, "emit_course_track_event") as emit_event, \
                patch.object(course_tracking, "_now_utc", return_value=datetime(2026, 5, 29, tzinfo=timezone.utc)):
            notified = course_tracking.check_course_seat_tracks()

        self.assertEqual(notified, 0)
        update_row.assert_not_called()
        failed_events = [
            call for call in emit_event.call_args_list
            if call.args[0] == "Automated Course Track Check Failed"
        ]
        self.assertEqual(len(failed_events), 1)
        self.assertIn("token=[redacted]", failed_events[0].kwargs["metadata"]["error"])

        completed_events = [
            call for call in emit_event.call_args_list
            if call.args[0] == "Automated Course Track Poll Completed"
        ]
        self.assertEqual(len(completed_events), 0)
        summary = course_tracking.get_last_course_tracking_poll()
        self.assertEqual(summary["atlas_checks_failed"], 1)

    def test_malformed_atlas_result_skips_group_and_continues_poll(self):
        tracks = [
            self._track("track-1", "user-1"),
            self._track("track-2", "user-2", section_id="Fall_2026-CS-170-5678-2"),
        ]
        tracks[1]["crn"] = "5678"

        with patch.dict(course_tracking.COLLECTIONS, {"course_seat_tracks": "tracks"}, clear=False), \
                patch.object(course_tracking, "list_rows_all", return_value=tracks), \
                patch.object(
                    course_tracking,
                    "fetch_live_section_status",
                    side_effect=[None, {"section": self._open_section()}],
                ), \
                patch.object(course_tracking.notifications, "preferences", return_value={"course_email_enabled": True, "course_push_enabled": False}), \
                patch.object(course_tracking, "_send_open_email"), \
                patch.object(course_tracking, "update_row_safe") as update_row, \
                patch.object(course_tracking, "emit_course_track_event"), \
                patch.object(course_tracking, "update_course_tracks_channel_topic"), \
                patch.object(course_tracking, "_now_utc", return_value=datetime(2026, 5, 29, tzinfo=timezone.utc)):
            notified = course_tracking.check_course_seat_tracks()

        self.assertEqual(notified, 1)
        update_row.assert_called_once()
        self.assertEqual(update_row.call_args.args[1], "track-2")
        summary = course_tracking.get_last_course_tracking_poll()
        self.assertEqual(summary["atlas_checks_failed"], 1)
        self.assertEqual(summary["atlas_checks_succeeded"], 1)

    def test_open_email_retry_reuses_message_id_after_ambiguous_timeout(self):
        track = self._track("track-1", "user-1")
        track["updated_at"] = "2026-05-29T00:00:00Z"
        sent_ids = []
        messaging = Mock()

        def create_email(**kwargs):
            sent_ids.append(kwargs["message_id"])
            if len(sent_ids) == 1:
                raise AppwriteException("gateway timeout", code=500)
            raise AppwriteException("message already exists", code=409)

        messaging.create_email.side_effect = create_email
        messaging.get_message.return_value = {"$id": "existing"}

        with patch.object(course_tracking, "Messaging", return_value=messaging):
            with self.assertRaises(AppwriteException):
                course_tracking._send_open_email(track, self._open_section())
            course_tracking._send_open_email(track, self._open_section())

        self.assertEqual(len(sent_ids), 2)
        self.assertEqual(sent_ids[0], sent_ids[1])
        messaging.get_message.assert_called_once_with(sent_ids[0])


class CourseTrackingEmailTests(unittest.TestCase):
    def _track(self, row_id, user_id):
        return {
            "$id": row_id, "user_id": user_id, "section_id": "Fall_2026-CS-170-1234-1",
            "term": "Fall_2026", "subject": "CS", "catalog": "170", "crn": "1234",
            "course_code": "CS 170", "course_title": "Intro CS", "enabled": True,
            "last_status": "Closed", "last_seats_available": 0,
        }

    def _closed_section(self):
        return {
            "id": "Fall_2026-CS-170-1234-1", "term": "Fall_2026", "subject": "CS",
            "catalog_number": "170", "crn": "1234", "course_code": "CS 170",
            "course_title": "Intro CS", "section_number": "1", "instructor": "Ada Lovelace",
            "enrollment_status": "Closed", "seats_available": 0,
        }
    def _sample_section(self):
        return {
            "id": "Spring_2026|JPN|101|1234|1",
            "term": "Spring_2026",
            "subject": "JPN",
            "catalog_number": "101",
            "course_code": "JPN 101",
            "course_title": "Elementary Japanese I",
            "section_number": "1",
            "crn": "1234",
            "instructor": "T. Sensei",
            "schedule_display": "MWF 10-10:50a",
            "location": "White Hall 200",
            "campus": "Atlanta",
            "enrollment_status": "Open",
            "seats_available": 2,
            "credit_hours": "4",
        }

    def test_build_open_seat_subject_pluralizes_seats(self):
        from services.course_tracking_email import build_open_seat_subject

        self.assertEqual(build_open_seat_subject("JPN 101", 1), "🎉 JPN 101 has 1 Open Seat")
        self.assertEqual(build_open_seat_subject("JPN 101", 2), "🎉 JPN 101 has 2 Open Seats")
        self.assertEqual(build_open_seat_subject("JPN 101", None), "🎉 JPN 101 has Open Seats")

    def test_build_nest_courses_detail_url_encodes_section_id(self):
        from services.course_tracking_email import build_nest_courses_detail_url

        section_id = "Spring_2026|JPN|101|1234|1"
        url = build_nest_courses_detail_url("https://nest.apstudy.org", section_id)
        self.assertIn("section=Spring_2026%7CJPN%7C101%7C1234%7C1", url)
        self.assertIn("#section=Spring_2026%7CJPN%7C101%7C1234%7C1", url)

    def test_build_nest_courses_detail_path_encodes_section_id(self):
        from services.course_tracking_email import build_nest_courses_detail_path

        section_id = "Spring_2026|JPN|101|1234|1"
        path = build_nest_courses_detail_path(section_id)
        self.assertEqual(
            path,
            "/courses?section=Spring_2026%7CJPN%7C101%7C1234%7C1#section=Spring_2026%7CJPN%7C101%7C1234%7C1",
        )

    def test_open_transition_push_notify_uses_relative_courses_path(self):
        tracks = [self._track("track-1", "user-1")]
        section = self._sample_section()

        with patch.dict(course_tracking.COLLECTIONS, {"course_seat_tracks": "tracks"}, clear=False), \
                patch.object(course_tracking, "list_rows_all", return_value=tracks), \
                patch.object(course_tracking, "fetch_live_section_status", return_value={"section": section}), \
                patch.object(course_tracking.notifications, "preferences", return_value={"course_email_enabled": False, "course_push_enabled": True}), \
                patch.object(course_tracking.notifications, "notify", return_value=("nid", {"accepted": 1, "failed": 0})) as notify, \
                patch.object(course_tracking, "_send_open_email") as send_email, \
                patch.object(course_tracking, "update_row_safe", side_effect=lambda table, row_id, data: {"$id": row_id, **data}), \
                patch.object(course_tracking, "emit_course_track_event"), \
                patch.object(course_tracking, "update_course_tracks_channel_topic"), \
                patch.object(course_tracking, "_now_utc", return_value=datetime(2026, 5, 29, tzinfo=timezone.utc)):
            notified = course_tracking.check_course_seat_tracks()

        self.assertEqual(notified, 1)
        send_email.assert_not_called()
        notify.assert_called_once()
        self.assertEqual(notify.call_args.args[1], "courses")
        self.assertEqual(
            notify.call_args.args[4],
            "/courses?section=Spring_2026%7CJPN%7C101%7C1234%7C1#section=Spring_2026%7CJPN%7C101%7C1234%7C1",
        )
        self.assertNotIn("https://", notify.call_args.args[4])

    def test_build_open_seat_html_includes_logo_buttons_and_table(self):
        from services.course_tracking_email import build_open_seat_html

        section = self._sample_section()
        section["course_title"] = "Intro <script>alert(1)</script>"
        html = build_open_seat_html(
            section,
            base_url="https://nest.apstudy.org",
            nest_details_url="https://nest.apstudy.org/courses?section=test#section=test",
        )

        self.assertIn("/static/images/apstudy-logo-email.jpg", html)
        self.assertIn('width="100"', html)
        self.assertIn("border-radius:50%", html)
        self.assertIn("https://atlas.emory.edu", html)
        self.assertIn("Nest Course Details", html)
        self.assertIn("Emory Atlas", html)
        self.assertIn("Intro &lt;script&gt;alert(1)&lt;/script&gt;", html)
        self.assertIn("Seats available", html)
        self.assertIn("Spring 2026", html)

    def test_send_open_email_uses_branded_template(self):
        track = {
            "user_id": "user-1",
            "course_code": "JPN 101",
            "section_id": "Spring_2026|JPN|101|1234|1",
        }
        section = self._sample_section()
        mock_messaging = unittest.mock.MagicMock()

        with patch("services.course_tracking.Messaging", return_value=mock_messaging), \
                patch.dict(os.environ, {"APP_BASE_URL": "https://nest.apstudy.org"}, clear=False):
            course_tracking._send_open_email(track, section)

        mock_messaging.create_email.assert_called_once()
        kwargs = mock_messaging.create_email.call_args.kwargs
        self.assertTrue(kwargs["html"])
        self.assertIn("🎉 JPN 101 has 2 Open Seats", kwargs["subject"])
        self.assertIn("apstudy-logo-email.jpg", kwargs["content"])
        self.assertIn("#section=Spring_2026%7CJPN%7C101%7C1234%7C1", kwargs["content"])


    def test_waitlist_opening_notifies_and_enters_three_hour_cooldown(self):
        track = {**self._track("track-1", "user-1"), "last_waitlist_total": 6, "last_waitlist_capacity": 6, "interval_minutes": 15}
        section = {**self._closed_section(), "waitlist_total": 5, "waitlist_capacity": 6}
        with patch.dict(course_tracking.COLLECTIONS, {"course_seat_tracks": "tracks"}, clear=False), \
                patch.object(course_tracking, "list_rows_all", return_value=[track]), \
                patch.object(course_tracking, "fetch_live_section_status", return_value={"section": section}), \
                patch.object(course_tracking.notifications, "preferences", return_value={"course_email_enabled": True, "course_push_enabled": False}), \
                patch.object(course_tracking, "_send_open_email") as send_email, \
                patch.object(course_tracking, "update_row_safe") as update_row, \
                patch.object(course_tracking, "emit_course_track_event"), \
                patch.object(course_tracking, "update_course_tracks_channel_topic"), \
                patch.object(course_tracking, "_now_utc", return_value=datetime(2026, 5, 29, tzinfo=timezone.utc)):
            notified = course_tracking.check_course_seat_tracks()

        self.assertEqual(notified, 1)
        send_email.assert_called_once()
        updates = update_row.call_args.args[2]
        self.assertTrue(updates["cooldown_until_closed"])
        self.assertEqual(updates["next_check_at"], "2026-05-29T03:00:00Z")

    def test_future_tracker_is_not_polled(self):
        track = {**self._track("track-1", "user-1"), "next_check_at": "2026-05-29T00:15:00Z"}
        with patch.dict(course_tracking.COLLECTIONS, {"course_seat_tracks": "tracks"}, clear=False), \
                patch.object(course_tracking, "list_rows_all", return_value=[track]), \
                patch.object(course_tracking, "fetch_live_section_status") as fetch_status, \
                patch.object(course_tracking, "emit_course_track_event"), \
                patch.object(course_tracking, "update_course_tracks_channel_topic"), \
                patch.object(course_tracking, "_now_utc", return_value=datetime(2026, 5, 29, tzinfo=timezone.utc)):
            notified = course_tracking.check_course_seat_tracks()

        self.assertEqual(notified, 0)
        fetch_status.assert_not_called()
        self.assertEqual(course_tracking.get_last_course_tracking_poll()["reason"], "no_due_tracks")

    def test_cooldown_restores_preferred_interval_after_availability_closes(self):
        track = {**self._track("track-1", "user-1"), "cooldown_until_closed": True, "interval_minutes": 15}
        with patch.dict(course_tracking.COLLECTIONS, {"course_seat_tracks": "tracks"}, clear=False), \
                patch.object(course_tracking, "list_rows_all", return_value=[track]), \
                patch.object(course_tracking, "fetch_live_section_status", return_value={"section": self._closed_section()}), \
                patch.object(course_tracking, "update_row_safe") as update_row, \
                patch.object(course_tracking, "emit_course_track_event"), \
                patch.object(course_tracking, "update_course_tracks_channel_topic"), \
                patch.object(course_tracking, "_now_utc", return_value=datetime(2026, 5, 29, tzinfo=timezone.utc)):
            course_tracking.check_course_seat_tracks()

        updates = update_row.call_args.args[2]
        self.assertFalse(updates["cooldown_until_closed"])
        self.assertEqual(updates["next_check_at"], "2026-05-29T00:15:00Z")


if __name__ == "__main__":
    unittest.main()
