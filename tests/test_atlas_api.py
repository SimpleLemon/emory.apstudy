import unittest
from unittest.mock import patch

from flask import Flask

from blueprints.atlas_api import atlas_bp


class AtlasApiTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.register_blueprint(atlas_bp, url_prefix="/api/atlas")
        self.client = self.app.test_client()

    @patch("blueprints.atlas_api.merge_snapshots_into_sections")
    @patch("blueprints.atlas_api.get_sections_index")
    def test_status_filter_uses_live_snapshot_before_limit(self, get_sections_index, merge_snapshots):
        get_sections_index.return_value = {
            "term": "Fall_2026",
            "sections": [
                {"id": "cached-closed", "course_code": "CHEM 150", "enrollment_status": "Closed"},
                {"id": "still-closed", "course_code": "CHEM 151", "enrollment_status": "Closed"},
            ],
            "count": 2,
            "total": 2,
        }
        merge_snapshots.return_value = [
            {"id": "cached-closed", "course_code": "CHEM 150", "enrollment_status": "Open"},
            {"id": "still-closed", "course_code": "CHEM 151", "enrollment_status": "Closed"},
        ]

        response = self.client.get("/api/atlas/sections?term=Fall_2026&statuses=Open&limit=1")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["sections"], [
            {"id": "cached-closed", "course_code": "CHEM 150", "enrollment_status": "Open"},
        ])
        self.assertIsNone(get_sections_index.call_args.kwargs["limit"])
        self.assertEqual(get_sections_index.call_args.kwargs["offset"], 0)
        self.assertIsNone(get_sections_index.call_args.kwargs["statuses"])
