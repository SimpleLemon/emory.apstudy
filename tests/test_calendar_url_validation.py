import unittest

from services.calendar_urls import (
    GOOGLE_CALENDAR_UI_MESSAGE,
    classify_calendar_url,
    load_other_calendar_urls,
    normalize_calendar_url,
)
from blueprints.settings import _validate_other_calendar_urls
from services.feed_fetcher import _normalize_feed_url
from services.scheduler import _configured_feed_urls

ICLOUD_HOLIDAYS_URL = "https://calendars.icloud.com/holidays/us_en-us.ics"
CORRUPTED_ICLOUD_HOLIDAYS_URL = f"{ICLOUD_HOLIDAYS_URL} ; cd ../../"
ICLOUD_NOTCHNOOK_URL = (
    "webcal://p48-caldav.icloud.com/published/2/"
    "MTY5Mzg1OTYxNTQxNjkzOIC7gLYBbH9Q2td79N_PNuSOULNKuPulb1YwqjOEy7kG_8nGgkCiDeBuvQWzohuK1K7NgMVuYqEvLYSKBYywBZo"
)
GOOGLE_UI_URL = "https://calendar.google.com/calendar/u/0/r"
GOOGLE_ICAL_URL = "https://calendar.google.com/calendar/ical/example%40gmail.com/private-token/basic.ics"


class TestCalendarUrlValidation(unittest.TestCase):
    def test_valid_icloud_holidays_url_is_accepted(self):
        self.assertEqual(normalize_calendar_url(ICLOUD_HOLIDAYS_URL), ICLOUD_HOLIDAYS_URL)

    def test_corrupted_icloud_holidays_url_is_rejected(self):
        self.assertIsNone(normalize_calendar_url(CORRUPTED_ICLOUD_HOLIDAYS_URL))

    def test_webcal_url_normalizes_to_https(self):
        normalized = normalize_calendar_url(ICLOUD_NOTCHNOOK_URL)
        self.assertTrue(normalized.startswith("https://p48-caldav.icloud.com/"))

    def test_feed_fetcher_normalization_matches_shared_helper(self):
        self.assertEqual(_normalize_feed_url(ICLOUD_HOLIDAYS_URL), ICLOUD_HOLIDAYS_URL)
        self.assertEqual(_normalize_feed_url(CORRUPTED_ICLOUD_HOLIDAYS_URL), "")

    def test_validate_other_calendar_urls_stores_canonical_urls(self):
        validated = _validate_other_calendar_urls(
            [f"  {ICLOUD_NOTCHNOOK_URL}  "],
            canvas_url="",
        )
        self.assertEqual(len(validated), 1)
        self.assertTrue(validated[0].startswith("https://p48-caldav.icloud.com/"))

    def test_validate_other_calendar_urls_rejects_corrupted_urls(self):
        with self.assertRaises(ValueError):
            _validate_other_calendar_urls([CORRUPTED_ICLOUD_HOLIDAYS_URL], canvas_url="")

    def test_load_other_calendar_urls_drops_invalid_legacy_entries(self):
        settings = {
            "other_ical_urls_json": (
                f'["{ICLOUD_HOLIDAYS_URL}", "{CORRUPTED_ICLOUD_HOLIDAYS_URL}"]'
            ),
        }
        self.assertEqual(load_other_calendar_urls(settings), [ICLOUD_HOLIDAYS_URL])

    def test_configured_feed_urls_uses_sanitized_other_urls(self):
        settings = {
            "canvas_ical_url": "",
            "other_ical_urls_json": f'["{CORRUPTED_ICLOUD_HOLIDAYS_URL}"]',
        }
        self.assertEqual(_configured_feed_urls(settings), [])

    def test_classify_rejects_google_calendar_ui_url(self):
        verdict, message = classify_calendar_url(GOOGLE_UI_URL)
        self.assertEqual(verdict, "reject")
        self.assertEqual(message, GOOGLE_CALENDAR_UI_MESSAGE)

    def test_classify_accepts_google_secret_ical_url(self):
        verdict, message = classify_calendar_url(GOOGLE_ICAL_URL)
        self.assertEqual(verdict, "accept")
        self.assertIsNone(message)

    def test_classify_accepts_icloud_published_feed_without_extension(self):
        verdict, message = classify_calendar_url(ICLOUD_NOTCHNOOK_URL)
        self.assertEqual(verdict, "accept")
        self.assertIsNone(message)

    def test_classify_unknown_for_arbitrary_https_url(self):
        verdict, message = classify_calendar_url("https://example.com/calendar-export")
        self.assertEqual(verdict, "unknown")
        self.assertIsNone(message)


if __name__ == "__main__":
    unittest.main()
