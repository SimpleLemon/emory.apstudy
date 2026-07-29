import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask

from blueprints.auth import auth_bp
import blueprints.legal as legal
from extensions import login_manager
from tests.support.harness import register_shell_route_stubs


class LegalPagesTestCase(unittest.TestCase):
    def setUp(self):
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        self.app = Flask(
            __name__,
            template_folder=os.path.join(project_root, "templates"),
            static_folder=os.path.join(project_root, "static"),
        )
        self.app.secret_key = "test"
        self.app.jinja_env.filters["avatar_url"] = lambda value, size=32: value or ""
        login_manager.init_app(self.app)
        self.app.register_blueprint(auth_bp)
        self.app.register_blueprint(legal.legal_bp)
        register_shell_route_stubs(self.app)

    def test_privacy_policy_renders_logged_out(self):
        response = self.app.test_client().get("/privacy-policy")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("<title>Privacy Policy - APStudy Nest</title>", body)
        self.assertIn("Last Updated: July 3, 2026", body)
        self.assertIn('href="/privacy-policy"', body)
        self.assertIn('href="/terms-of-service"', body)
        self.assertIn('href="mailto:derek.chen@emory.edu"', body)
        self.assertIn("legal-public-nav", body)
        self.assertIn("Google Analytics", body)
        self.assertIn("Nest.APStudy Privacy Contact", body)
        self.assertIn("Complaints to a Data Protection Authority", body)
        self.assertIn("European Data Protection Board", body)
        self.assertIn("Information Commissioner's Office", body)
        self.assertIn("machine-readable copy", body)
        self.assertIn("withdrawal does not affect", body.lower())
        self.assertIn("Cookie settings", body)
        self.assertIn("six months", body)
        self.assertIn("analytics remains off until you choose Allow analytics or Reject analytics", body)
        self.assertIn("authenticated application", body)
        self.assertIn('data-analytics-mode="public-choice"', body)
        self.assertNotIn('class="thesidebar"', body)

    def test_terms_of_service_renders_logged_out(self):
        response = self.app.test_client().get("/terms-of-service")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("<title>Terms of Service - APStudy Nest</title>", body)
        self.assertIn("Last Updated: June 2025", body)
        self.assertIn("Academic Data Disclaimer", body)
        self.assertIn("Limitation of Liability", body)
        self.assertIn('href="/privacy-policy"', body)
        self.assertIn('href="/terms-of-service"', body)
        self.assertNotIn('class="thesidebar"', body)

    def test_authenticated_legal_page_uses_app_chrome_placeholders(self):
        user = SimpleNamespace(
            id="user-1",
            email="student@example.test",
            picture_url="https://example.test/avatar.png",
            emory_student=True,
            is_authenticated=True,
        )

        with patch.object(legal, "current_user", user), \
                patch.object(legal, "list_rows_safe", return_value={"rows": [{"interface_theme": "nest-light"}]}):
            response = self.app.test_client().get("/privacy-policy")

        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('global class="thenav"', body)
        self.assertIn('id="sidebar-root"', body)
        self.assertIn('data-user-email="student@example.test"', body)
        self.assertIn('href="/courses"', body)
        self.assertNotIn("legal-public-nav", body)
        self.assertIn('window.APSTUDY_THEME_PREFERENCE = "nest-light";', body)

    def test_legal_css_keeps_responsive_readability_rules(self):
        css_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "static",
            "css",
            "legal.css",
        )
        with open(css_path, encoding="utf-8") as css_file:
            css_source = css_file.read()

        self.assertIn("overflow-x: hidden", css_source)
        self.assertIn(".legal-table-wrap", css_source)
        self.assertIn("overflow-x: auto", css_source)
        self.assertIn("@media (max-width: 980px)", css_source)
        self.assertIn("@media (max-width: 640px)", css_source)
        self.assertIn("grid-template-columns: 1fr", css_source)
        self.assertIn("min-height: 48px", css_source)


if __name__ == "__main__":
    unittest.main()
