import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

from blueprints.calendar_api import (
    CANVAS_SOURCE_ID,
    DEFAULT_LOCAL_SOURCE_ID,
    _apply_event_override,
    _configured_feed_sources,
    _configured_local_sources,
    _event_ref_for_cache_event,
    _feed_source_id,
    _feed_url_hash,
    _filter_configured_cache_events,
    _initial_fetch_feed_urls,
    _legacy_feed_source_id,
    _serialize_event,
    _serialize_user_event,
    _settings_payload_for_source_update,
)
from services.feed_fetcher import (
    _feed_url_hash as fetcher_feed_url_hash,
    _normalize_feed_url,
    _upsert_feed_metadata,
    fetch_and_cache_feeds,
    fetch_and_parse_ical,
)


ICLOUD_NOTCHNOOK_URL = (
    "webcal://p48-caldav.icloud.com/published/2/"
    "MTY5Mzg1OTYxNTQxNjkzOIC7gLYBbH9Q2td79N_PNuSOULNKuPulb1YwqjOEy7kG_8nGgkCiDeBuvQWzohuK1K7NgMVuYqEvLYSKBYywBZo"
)


class TestCalendarSources(unittest.TestCase):
    def test_local_sources_include_default_for_legacy_user_events(self):
        sources = _configured_local_sources(
            local_sources=[],
            preferences=[
                {
                    "calendar_name": DEFAULT_LOCAL_SOURCE_ID,
                    "display_name": "My Calendar",
                },
            ],
            created_events=[{"title": "Legacy", "calendar_id": ""}],
        )

        self.assertEqual(sources[0]["id"], DEFAULT_LOCAL_SOURCE_ID)
        self.assertEqual(sources[0]["display_name"], "My Calendar")
        self.assertEqual(sources[0]["kind"], "local")

    def test_serialized_user_event_includes_calendar_identity(self):
        serialized = _serialize_user_event({
            "$id": "event-1",
            "title": "Office Hours",
            "description": "",
            "start": "2026-05-22T14:00:00Z",
            "end": "2026-05-22T15:00:00Z",
            "is_all_day": False,
            "calendar_id": "local:study",
            "color": "",
        })

        self.assertEqual(serialized["event_ref"], "user:event-1")
        self.assertEqual(serialized["calendar_id"], "local:study")
        self.assertIsNone(serialized["color"])

    def test_feed_event_override_can_hide_or_overlay_event(self):
        event = {
            "event_uid": "feed-event",
            "event_title": "Original",
            "event_start": "2026-05-22T14:00:00Z",
            "event_end": "2026-05-22T15:00:00Z",
            "event_type": "event",
            "course_name": "Work",
            "feed_url": "https://example.com/work.ics",
            "feed_url_hash": _feed_url_hash("https://example.com/work.ics"),
            "is_all_day": False,
        }
        serialized = _serialize_event(event, {"canvas_ical_url": "", "other_ical_urls_json": "[]"})
        event_ref = _event_ref_for_cache_event(event)

        self.assertEqual(serialized["event_ref"], event_ref)
        self.assertIsNone(_apply_event_override(serialized, {"hidden": True}))

        overridden = _apply_event_override(serialized, {
            "$id": "override-1",
            "event_ref": event_ref,
            "title": "Renamed",
            "description": "Changed",
            "start": "2026-05-23T15:00:00Z",
            "end": "2026-05-23T16:00:00Z",
            "is_all_day": False,
            "calendar_id": "local:study",
            "color": "#0ea5e9",
        })

        self.assertEqual(overridden["title"], "Renamed")
        self.assertEqual(overridden["calendar_id"], "local:study")
        self.assertEqual(overridden["color"], "#0ea5e9")
        self.assertEqual(overridden["override_id"], "override-1")

    def test_source_metadata_uses_alias_and_feed_labels(self):
        external_url = "https://calendar.google.com/calendar/ical/work/basic.ics"
        settings = {
            "canvas_ical_url": "https://canvas.emory.edu/feeds/calendars/user-token",
            "other_ical_urls_json": f'["{external_url}"]',
        }
        cache_events = [
            {
                "feed_url_hash": _feed_source_id(external_url).replace("feed:", ""),
                "course_name": "Work",
            },
            {
                "feed_url_hash": _feed_source_id(external_url).replace("feed:", ""),
                "course_name": "Work",
            },
        ]
        preferences = [
            {
                "calendar_name": CANVAS_SOURCE_ID,
                "display_name": "School Canvas",
            },
            {
                "calendar_name": _feed_source_id(external_url),
                "display_name": "Internship",
            },
        ]

        sources = _configured_feed_sources(settings, cache_events, preferences)

        self.assertEqual(sources[0]["id"], CANVAS_SOURCE_ID)
        self.assertEqual(sources[0]["display_name"], "School Canvas")
        self.assertEqual(sources[1]["default_name"], "Work")
        self.assertEqual(sources[1]["display_name"], "Internship")

    def test_feed_parser_returns_calendar_name(self):
        ics_payload = "\r\n".join([
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//Example Corp//Calendar//EN",
            "X-WR-CALNAME:US Holidays",
            "BEGIN:VEVENT",
            "UID:event-1",
            "SUMMARY:Memorial Day",
            "DTSTART;VALUE=DATE:20260525",
            "DTEND;VALUE=DATE:20260526",
            "END:VEVENT",
            "END:VCALENDAR",
        ]).encode("utf-8")
        response = Mock()
        response.status_code = 200
        response.content = ics_payload
        response.text = ics_payload.decode("utf-8")
        response.headers = {}

        with patch("services.feed_fetcher.require_public_http_url", side_effect=lambda url: url), \
                patch("services.feed_fetcher.http_requests.get", return_value=response):
            parsed = fetch_and_parse_ical("https://example.com/holidays.ics")

        self.assertEqual(parsed["calendar_name"], "US Holidays")
        self.assertEqual(parsed["events"][0]["course_name"], "US Holidays")

    def test_icloud_webcal_feed_parser_returns_notchnook_name(self):
        ics_payload = "\r\n".join([
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//Apple Inc.//iCloud Calendar//EN",
            "X-WR-CALNAME:NotchNook",
            "BEGIN:VEVENT",
            "UID:notchnook-1",
            "SUMMARY:KEYS Meeting",
            "DTSTART:20260522T140000Z",
            "DTEND:20260522T150000Z",
            "END:VEVENT",
            "END:VCALENDAR",
        ]).encode("utf-8")
        response = Mock()
        response.status_code = 200
        response.content = ics_payload
        response.text = ics_payload.decode("utf-8")
        response.headers = {}

        with patch("services.feed_fetcher.require_public_http_url", side_effect=lambda url: url), \
                patch("services.feed_fetcher.http_requests.get", return_value=response) as get_mock:
            parsed = fetch_and_parse_ical(ICLOUD_NOTCHNOOK_URL)

        normalized_url = _normalize_feed_url(ICLOUD_NOTCHNOOK_URL)
        self.assertTrue(normalized_url.startswith("https://p48-caldav.icloud.com/"))
        get_mock.assert_called_once()
        self.assertEqual(get_mock.call_args.kwargs["url"] if "url" in get_mock.call_args.kwargs else get_mock.call_args.args[0], normalized_url)
        self.assertEqual(parsed["feed_url"], normalized_url)
        self.assertEqual(parsed["calendar_name"], "NotchNook")
        self.assertEqual(parsed["events"][0]["course_name"], "NotchNook")

    def test_source_metadata_uses_persisted_feed_name_without_events(self):
        external_url = "https://calendar.google.com/calendar/ical/holidays/basic.ics"
        settings = {
            "canvas_ical_url": "",
            "other_ical_urls_json": f'["{external_url}"]',
        }
        feed_hash = _feed_source_id(external_url).replace("feed:", "")

        sources = _configured_feed_sources(
            settings,
            cache_events=[],
            preferences=[],
            feed_metadata={
                feed_hash: {"calendar_name": "US Holidays"},
            },
        )

        self.assertEqual(sources[0]["default_name"], "US Holidays")

    def test_webcal_source_metadata_uses_normalized_feed_metadata(self):
        settings = {
            "canvas_ical_url": "",
            "other_ical_urls_json": f'["{ICLOUD_NOTCHNOOK_URL}"]',
        }
        feed_hash = _feed_url_hash(ICLOUD_NOTCHNOOK_URL)

        sources = _configured_feed_sources(
            settings,
            cache_events=[],
            preferences=[],
            feed_metadata={feed_hash: {"calendar_name": "NotchNook"}},
        )

        self.assertEqual(sources[0]["id"], _feed_source_id(ICLOUD_NOTCHNOOK_URL))
        self.assertEqual(sources[0]["default_name"], "NotchNook")

    def test_webcal_source_alias_falls_back_from_legacy_raw_source_id(self):
        settings = {
            "canvas_ical_url": "",
            "other_ical_urls_json": f'["{ICLOUD_NOTCHNOOK_URL}"]',
        }
        feed_hash = _feed_url_hash(ICLOUD_NOTCHNOOK_URL)
        preferences = [
            {
                "calendar_name": _legacy_feed_source_id(ICLOUD_NOTCHNOOK_URL),
                "display_name": "My NotchNook",
            },
        ]

        sources = _configured_feed_sources(
            settings,
            cache_events=[],
            preferences=preferences,
            feed_metadata={feed_hash: {"calendar_name": "NotchNook"}},
        )

        self.assertEqual(sources[0]["display_name"], "My NotchNook")

    def test_webcal_source_alias_overrides_feed_name(self):
        settings = {
            "canvas_ical_url": "",
            "other_ical_urls_json": f'["{ICLOUD_NOTCHNOOK_URL}"]',
        }
        feed_hash = _feed_url_hash(ICLOUD_NOTCHNOOK_URL)
        preferences = [
            {
                "calendar_name": _feed_source_id(ICLOUD_NOTCHNOOK_URL),
                "display_name": "Renamed Calendar",
            },
        ]

        sources = _configured_feed_sources(
            settings,
            cache_events=[],
            preferences=preferences,
            feed_metadata={feed_hash: {"calendar_name": "NotchNook"}},
        )

        self.assertEqual(sources[0]["default_name"], "NotchNook")
        self.assertEqual(sources[0]["display_name"], "Renamed Calendar")

    def test_source_metadata_uses_neutral_fallback_without_feed_name(self):
        external_url = "https://calendar.google.com/calendar/ical/Mty5Mzg1Otyxntqxnjkzoic7Glybbh9Q2Td79N_Pnurbxalqbqj47Q8DWqwti2O6Ta/basic.ics"
        settings = {
            "canvas_ical_url": "",
            "other_ical_urls_json": f'["{external_url}"]',
        }

        sources = _configured_feed_sources(settings, [], [])

        self.assertNotRegex(sources[0]["default_name"], r"^Calendar \d+$")
        self.assertEqual(sources[0]["default_name"], "Subscribed Calendar")

    def test_source_metadata_falls_back_to_legacy_preference_name(self):
        settings = {
            "canvas_ical_url": "https://canvas.emory.edu/feeds/calendars/user-token",
            "other_ical_urls_json": "[]",
        }
        preferences = [
            {
                "calendar_name": "Canvas",
                "display_name": "Legacy Canvas",
            },
        ]

        sources = _configured_feed_sources(settings, [], preferences)

        self.assertEqual(sources[0]["display_name"], "Legacy Canvas")

    def test_external_url_replacement_preserves_order(self):
        first_url = "https://example.com/first.ics"
        old_url = "https://example.com/old.ics"
        next_url = "https://example.com/new.ics"
        settings = {
            "canvas_ical_url": "https://canvas.emory.edu/feeds/calendars/user-token",
            "other_ical_urls_json": f'["{first_url}", "{old_url}"]',
        }

        payload = _settings_payload_for_source_update(
            settings,
            _feed_source_id(old_url),
            next_url,
        )

        self.assertEqual(payload["old_url"], old_url)
        self.assertEqual(payload["new_url"], next_url)
        self.assertEqual(
            payload["settings_updates"]["other_ical_urls_json"],
            f'["{first_url}", "{next_url}"]',
        )

    def test_duplicate_external_url_is_rejected(self):
        first_url = "https://example.com/first.ics"
        old_url = "https://example.com/old.ics"
        settings = {
            "canvas_ical_url": "https://canvas.emory.edu/feeds/calendars/user-token",
            "other_ical_urls_json": f'["{first_url}", "{old_url}"]',
        }

        with self.assertRaises(ValueError):
            _settings_payload_for_source_update(
                settings,
                _feed_source_id(old_url),
                first_url,
            )

    def test_stale_cache_events_are_filtered(self):
        active_url = "https://example.com/active.ics"
        stale_url = "https://example.com/stale.ics"
        cache_events = [
            {"feed_url_hash": _feed_source_id(active_url).replace("feed:", ""), "event_title": "Active"},
            {"feed_url_hash": _feed_source_id(stale_url).replace("feed:", ""), "event_title": "Stale"},
        ]

        filtered = _filter_configured_cache_events(cache_events, [active_url])

        self.assertEqual([event["event_title"] for event in filtered], ["Active"])

    def test_webcal_config_keeps_normalized_cached_events(self):
        normalized_url = _normalize_feed_url(ICLOUD_NOTCHNOOK_URL)
        cache_events = [
            {
                "feed_url_hash": _feed_url_hash(normalized_url),
                "event_title": "KEYS Meeting",
            },
        ]

        filtered = _filter_configured_cache_events(cache_events, [ICLOUD_NOTCHNOOK_URL])

        self.assertEqual([event["event_title"] for event in filtered], ["KEYS Meeting"])

    def test_serialized_webcal_event_uses_canonical_calendar_id(self):
        normalized_url = _normalize_feed_url(ICLOUD_NOTCHNOOK_URL)
        settings = {
            "canvas_ical_url": "",
            "other_ical_urls_json": f'["{ICLOUD_NOTCHNOOK_URL}"]',
        }
        event = {
            "event_uid": "notchnook-1",
            "event_title": "KEYS Meeting",
            "event_start": "2026-05-22T14:00:00Z",
            "event_end": "2026-05-22T15:00:00Z",
            "event_type": "event",
            "course_name": "NotchNook",
            "feed_url": normalized_url,
            "feed_url_hash": _feed_url_hash(normalized_url),
            "is_all_day": False,
        }

        serialized = _serialize_event(event, settings)

        self.assertEqual(serialized["calendar_id"], _feed_source_id(ICLOUD_NOTCHNOOK_URL))

    def test_initial_fetch_detects_uncached_webcal_feed(self):
        missing = _initial_fetch_feed_urls([ICLOUD_NOTCHNOOK_URL], [], {})

        self.assertEqual(missing, [ICLOUD_NOTCHNOOK_URL])

    def test_initial_fetch_skips_webcal_feed_with_normalized_cache(self):
        normalized_url = _normalize_feed_url(ICLOUD_NOTCHNOOK_URL)
        cache_events = [
            {
                "feed_url_hash": _feed_url_hash(normalized_url),
                "event_title": "KEYS Meeting",
            },
        ]

        missing = _initial_fetch_feed_urls([ICLOUD_NOTCHNOOK_URL], cache_events, {})

        self.assertEqual(missing, [])

    def test_initial_fetch_skips_webcal_feed_with_named_metadata(self):
        feed_hash = _feed_url_hash(ICLOUD_NOTCHNOOK_URL)

        missing = _initial_fetch_feed_urls(
            [ICLOUD_NOTCHNOOK_URL],
            [],
            {feed_hash: {"calendar_name": "NotchNook"}},
        )

        self.assertEqual(missing, [])

    def test_fetch_and_cache_forces_full_fetch_when_cache_is_empty(self):
        normalized_url = _normalize_feed_url(ICLOUD_NOTCHNOOK_URL)
        feed_hash = fetcher_feed_url_hash(ICLOUD_NOTCHNOOK_URL)

        with patch("services.feed_fetcher.list_calendar_rows_all", return_value=[]), \
                patch("services.feed_fetcher._load_feed_metadata", return_value={
                    feed_hash: {
                        "etag_header": '"old-etag"',
                        "last_modified_header": "Wed, 13 May 2026 12:00:00 GMT",
                    },
                }), \
                patch("services.feed_fetcher.fetch_and_parse_ical", return_value={
                    "status_code": 200,
                    "events": [],
                    "etag": None,
                    "last_modified": None,
                    "feed_url": normalized_url,
                    "calendar_name": "NotchNook",
                }) as fetch_mock, \
                patch("services.feed_fetcher._upsert_feed_metadata"), \
                patch("services.feed_fetcher._apply_feed_diffs", return_value=0):
            fetch_and_cache_feeds("user-1", [ICLOUD_NOTCHNOOK_URL])

        fetch_mock.assert_called_once()
        self.assertIsNone(fetch_mock.call_args.kwargs.get("etag"))
        self.assertIsNone(fetch_mock.call_args.kwargs.get("last_modified"))

    def test_feed_metadata_keeps_last_fetched_throttle_write(self):
        fetched_at = datetime(2026, 6, 1, 17, 0, tzinfo=timezone.utc)
        existing = {"$id": "feed-1", "feed_url_hash": "hash-1"}
        result = {
            "status_code": 304,
            "etag": '"etag-1"',
            "last_modified": "Mon, 01 Jun 2026 17:00:00 GMT",
            "calendar_name": "Canvas",
        }

        with patch("services.feed_fetcher._feed_url_hash", return_value="hash-1"), \
                patch("services.feed_fetcher.first_calendar_row", return_value=existing), \
                patch("services.feed_fetcher.update_calendar_row", return_value={}) as update_row:
            _upsert_feed_metadata("user-1", "https://example.com/feed.ics", result, fetched_at)

        update_row.assert_called_once()
        payload = update_row.call_args.args[2]
        self.assertEqual(payload["last_fetched"], "2026-06-01T17:00:00Z")
        self.assertEqual(payload["updated_at"], "2026-06-01T17:00:00Z")
        self.assertEqual(payload["consecutive_failures"], 0)
        self.assertIsNone(payload["disabled_at"])


if __name__ == "__main__":
    unittest.main()
