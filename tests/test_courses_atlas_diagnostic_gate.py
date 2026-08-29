import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask

import blueprints.dashboard as dashboard
from config import load_environment_config


def _user(user_id):
    return SimpleNamespace(
        id=user_id,
        name="Derek",
        username="derek",
        email="derek@example.test",
        picture_url="",
        emory_student=True,
        school="Emory College",
        school_key="emory-college",
        education_level="Undergraduate",
        class_year=None,
        graduation_year="2027",
        onboarding_complete=True,
    )


class AtlasDiagnosticGateTestCase(unittest.TestCase):
    def setUp(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.app = Flask(
            __name__,
            template_folder=os.path.join(root, "templates"),
            static_folder=os.path.join(root, "static"),
        )
        self.app.secret_key = "test"
        self.app.config["SERVER_NAME"] = "example.test"
        self.app.config["ATLAS_BROWSER_DIAGNOSTIC_ENABLED"] = True
        self.app.jinja_env.filters["avatar_url"] = lambda value, _size: value
        self.app.register_blueprint(dashboard.dashboard_bp)

        @self.app.route("/settings/", endpoint="settings.settings_page")
        def _settings_page():
            return ""

        @self.app.route("/files", endpoint="file_share.file_share_page")
        def _files_page():
            return ""

        @self.app.route("/admin", endpoint="admin.admin_index")
        def _admin_index():
            return ""


        self._previous_admin_ids = os.environ.get("ADMIN_USER_IDS")
        os.environ["ADMIN_USER_IDS"] = "admin-1"
        self.admin = _user("admin-1")
        self.student = _user("student-1")

    def tearDown(self):
        if self._previous_admin_ids is None:
            os.environ.pop("ADMIN_USER_IDS", None)
        else:
            os.environ["ADMIN_USER_IDS"] = self._previous_admin_ids

    def _render_courses(self, user, query=""):
        with self.app.test_request_context(f"/courses{query}"):
            with patch.object(dashboard, "current_user", user), \
                    patch.object(dashboard, "_is_emory_or_oxford_user", return_value=True), \
                    patch.object(dashboard, "_load_user_settings", return_value={}), \
                    patch.object(dashboard, "get_atlas_term_srcdb", return_value={"Fall_2026": "5269"}), \
                    patch.object(dashboard, "get_starred_general_ed_requirements", return_value=[]), \
                    patch.object(dashboard, "get_general_ed_requirement_aliases", return_value={}), \
                    patch.object(dashboard, "get_general_ed_composite_requirements", return_value={}):
                return dashboard.courses.__wrapped__()

    def _diag_enabled_marker(self, page):
        marker = "window.APSTUDY_ATLAS_DIAGNOSTIC_ENABLED = "
        for line in page.splitlines():
            if marker in line:
                return line.strip().endswith("true;")
        return False

    def test_admin_with_query_param_receives_diagnostic(self):
        page = self._render_courses(self.admin, "?atlas_diag=1")
        self.assertTrue(self._diag_enabled_marker(page))
        self.assertIn("js/courses/atlas-diagnostic.js", page)
        self.assertIn('"subject": "CS"', page)
        self.assertIn('"key": ""', page)
        self.assertIn("Fall_2026", page)

    def test_admin_without_query_param_gets_no_diagnostic(self):
        page = self._render_courses(self.admin)
        self.assertFalse(self._diag_enabled_marker(page))
        self.assertNotIn("atlas-diagnostic.js", page)

    def test_non_admin_never_receives_diagnostic(self):
        page = self._render_courses(self.student, "?atlas_diag=1")
        self.assertFalse(self._diag_enabled_marker(page))
        self.assertNotIn("atlas-diagnostic.js", page)

    def test_disabled_flag_blocks_diagnostic_even_for_admin(self):
        self.app.config["ATLAS_BROWSER_DIAGNOSTIC_ENABLED"] = False
        page = self._render_courses(self.admin, "?atlas_diag=1")
        self.assertFalse(self._diag_enabled_marker(page))
        self.assertNotIn("atlas-diagnostic.js", page)

    def test_query_param_truth_values(self):
        for value, expected in (("1", True), ("true", True), ("yes", True), ("on", True),
                                ("0", False), ("false", False), ("", False)):
            with self.subTest(value=value):
                page = self._render_courses(self.admin, f"?atlas_diag={value}")
                self.assertEqual(self._diag_enabled_marker(page), expected)

    def test_subject_and_key_query_params_are_sanitized(self):
        page = self._render_courses(
            self.admin,
            "?atlas_diag=1"
            "&atlas_diag_subject=chem%3Cscript%3Ealert%281%29%3C%2Fscript%3E"
            "&atlas_diag_key=KEY%26x%3D1",
        )
        self.assertIn('"subject": "CHEMSCRIPTALERT1SCRIPT"', page)
        self.assertIn('"key": "KEYx1"', page)
        self.assertNotIn("<script>alert(1)</script>", page)

    def test_missing_admin_membership_blocks_diagnostic(self):
        os.environ["ADMIN_USER_IDS"] = "someone-else"
        page = self._render_courses(self.admin, "?atlas_diag=1")
        self.assertFalse(self._diag_enabled_marker(page))
        self.assertNotIn("atlas-diagnostic.js", page)

    def test_environment_flag_parsing(self):
        for raw, expected in (("1", True), ("true", True), ("YES", True), ("on", True),
                              ("0", False), ("off", False), ("", False)):
            with self.subTest(raw=raw):
                with patch.dict(os.environ, {"ATLAS_BROWSER_DIAGNOSTIC_ENABLED": raw}):
                    self.assertEqual(
                        load_environment_config().atlas_browser_diagnostic_enabled,
                        expected,
                    )
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(load_environment_config().atlas_browser_diagnostic_enabled)


if __name__ == "__main__":
    unittest.main()
