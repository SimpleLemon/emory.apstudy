import unittest
from unittest.mock import patch

from flask import Flask
from flask_login import UserMixin

from extensions import login_manager
from blueprints.atlas_api import atlas_bp


VERIFY_URL = "/api/atlas/sections/verify"
SECTION_ID = "2630,Fall_2026,CHEM-150,1"
DETAIL_ID = "2630,Fall_2026,CHEM-150,1"


class AtlasUser(UserMixin):
    def __init__(self, user_id="atlas-user", **profile):
        self.id = user_id
        self.school = profile.get("school")
        self.school_key = profile.get("school_key")
        self.emory_student = profile.get("emory_student", False)


def _service_payload():
    return {
        "verified_by_id": {
            SECTION_ID: {
                "id": SECTION_ID,
                "catalog_number": "CHEM 150",
                "section_number": "1",
                "crn": "2630",
                "term": "Fall_2026",
                "enrollment_status": "Open",
                "seats_available": None,
            },
        },
        "details_by_id": {},
        "detail_errors_by_id": {},
        "errors_by_id": {
            "2630,Fall_2026,MATH-111,1": "Section not found in live Atlas results",
        },
        "groups": [
            {"term": "Fall_2026", "subject": "CHEM", "section_ids": [SECTION_ID]},
        ],
        "limits": {"max_section_ids": 120, "max_groups": 24, "max_details": 12},
    }


class AtlasVerifyApiTests(unittest.TestCase):
    def setUp(self):
        previous_loader = login_manager._user_callback
        previous_unauthorized = login_manager.unauthorized_callback
        previous_login_view = login_manager.login_view
        self.addCleanup(setattr, login_manager, "_user_callback", previous_loader)
        self.addCleanup(setattr, login_manager, "unauthorized_callback", previous_unauthorized)
        self.addCleanup(setattr, login_manager, "login_view", previous_login_view)

        # Isolated app: CSRFProtect is never initialized, so POST mutations
        # are not CSRF-blocked, and no unauthorized handler is installed so
        # flask_login deterministically answers unauthenticated requests
        # with 401 instead of a login redirect.
        self.app = Flask(__name__)
        self.app.secret_key = "test"
        self.app.config["SERVER_NAME"] = "example.test"
        login_manager.unauthorized_callback = None
        login_manager.login_view = None
        login_manager.init_app(self.app)
        self.app.register_blueprint(atlas_bp, url_prefix="/api/atlas")

        self.user = AtlasUser()

        @login_manager.user_loader
        def load_user(user_id):
            return self.user if user_id == self.user.id else None

    def _login(self, client):
        with client.session_transaction() as session:
            session["_user_id"] = self.user.id
            session["_fresh"] = True

    def test_unauthenticated_gets_401(self):
        with patch("blueprints.atlas_api.atlas_live_verification.verify_sections_by_ids") as verify:
            response = self.app.test_client().post(
                VERIFY_URL, json={"section_ids": [SECTION_ID]}
            )

        self.assertEqual(response.status_code, 401)
        verify.assert_not_called()

    def test_non_emory_user_gets_403_and_service_not_called(self):
        self.user.school = "Arizona State University"
        with self.app.test_client() as client:
            self._login(client)
            with patch(
                "blueprints.atlas_api.atlas_live_verification.verify_sections_by_ids"
            ) as verify:
                response = client.post(
                    VERIFY_URL, json={"section_ids": [SECTION_ID]}
                )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            response.get_json(),
            {"error": "Courses are only available to Emory students."},
        )
        verify.assert_not_called()

    def test_missing_or_non_list_section_ids_get_400(self):
        self.user.school = "Emory University"
        with self.app.test_client() as client:
            self._login(client)
            with patch(
                "blueprints.atlas_api.atlas_live_verification.verify_sections_by_ids"
            ) as verify:
                missing = client.post(VERIFY_URL, json={})
                non_list = client.post(VERIFY_URL, json={"section_ids": SECTION_ID})

        self.assertEqual(missing.status_code, 400)
        self.assertEqual(missing.get_json(), {"error": "section_ids must be a list"})
        self.assertEqual(non_list.status_code, 400)
        self.assertEqual(non_list.get_json(), {"error": "section_ids must be a list"})
        verify.assert_not_called()

    def test_non_list_detail_ids_get_400(self):
        self.user.school = "Emory University"
        with self.app.test_client() as client:
            self._login(client)
            with patch(
                "blueprints.atlas_api.atlas_live_verification.verify_sections_by_ids"
            ) as verify:
                response = client.post(
                    VERIFY_URL,
                    json={"section_ids": [SECTION_ID], "detail_ids": DETAIL_ID},
                )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json(), {"error": "detail_ids must be a list"})
        verify.assert_not_called()

    def test_valid_request_returns_service_maps_with_status_and_fetched_at(self):
        self.user.school = "Emory University"
        service_payload = _service_payload()
        with self.app.test_client() as client:
            self._login(client)
            with patch(
                "blueprints.atlas_api.atlas_live_verification.verify_sections_by_ids",
                return_value=service_payload,
            ) as verify:
                response = client.post(
                    VERIFY_URL,
                    json={"section_ids": [SECTION_ID], "detail_ids": [DETAIL_ID]},
                )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"], "ok")
        self.assertTrue(payload["fetched_at"].endswith("Z"))
        self.assertEqual(payload["verified_by_id"], service_payload["verified_by_id"])
        self.assertEqual(payload["details_by_id"], service_payload["details_by_id"])
        self.assertEqual(
            payload["detail_errors_by_id"], service_payload["detail_errors_by_id"]
        )
        self.assertEqual(payload["errors_by_id"], service_payload["errors_by_id"])
        self.assertEqual(payload["groups"], service_payload["groups"])
        self.assertEqual(payload["limits"], service_payload["limits"])
        verify.assert_called_once_with([SECTION_ID], [DETAIL_ID])

    def test_oversize_section_ids_get_400_before_service(self):
        self.user.school = "Emory University"
        oversized = [f"2630,Fall_2026,CHEM-150,{index}" for index in range(121)]
        with self.app.test_client() as client:
            self._login(client)
            with patch(
                "blueprints.atlas_api.atlas_live_verification.verify_sections_by_ids"
            ) as verify:
                response = client.post(
                    VERIFY_URL, json={"section_ids": oversized}
                )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.get_json(), {"error": "Too many section IDs requested (max 120)"}
        )
        verify.assert_not_called()

    def test_oversize_detail_ids_get_400_before_service(self):
        self.user.school = "Emory University"
        oversized_details = [f"2630,Fall_2026,CHEM-150,{index}" for index in range(13)]
        with self.app.test_client() as client:
            self._login(client)
            with patch(
                "blueprints.atlas_api.atlas_live_verification.verify_sections_by_ids"
            ) as verify:
                response = client.post(
                    VERIFY_URL,
                    json={"section_ids": [SECTION_ID], "detail_ids": oversized_details},
                )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.get_json(), {"error": "Too many detail IDs requested (max 12)"}
        )
        verify.assert_not_called()

    def test_caps_at_exact_limits_pass_through_to_service(self):
        self.user.school = "Emory University"
        section_ids = [f"2630,Fall_2026,CHEM-150,{index}" for index in range(120)]
        detail_ids = [f"2630,Fall_2026,MATH-111,{index}" for index in range(12)]
        with self.app.test_client() as client:
            self._login(client)
            with patch(
                "blueprints.atlas_api.atlas_live_verification.verify_sections_by_ids",
                return_value=_service_payload(),
            ) as verify:
                response = client.post(
                    VERIFY_URL,
                    json={"section_ids": section_ids, "detail_ids": detail_ids},
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "ok")
        verify.assert_called_once_with(section_ids, detail_ids)

    def test_service_exception_returns_bounded_500(self):
        self.user.school = "Oxford College"
        with self.app.test_client() as client:
            self._login(client)
            with patch(
                "blueprints.atlas_api.atlas_live_verification.verify_sections_by_ids",
                side_effect=RuntimeError("atlas socket exploded"),
            ):
                response = client.post(
                    VERIFY_URL, json={"section_ids": [SECTION_ID]}
                )

        self.assertEqual(response.status_code, 500)
        payload = response.get_json()
        self.assertEqual(payload, {"error": "Live Atlas verification is unavailable."})
        self.assertNotIn("atlas socket exploded", response.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
