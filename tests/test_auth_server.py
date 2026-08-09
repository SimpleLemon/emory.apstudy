import os
import tempfile
import unittest
from unittest.mock import patch


class AuthServerContractTests(unittest.TestCase):
    def test_create_test_app_restores_environment_exactly(self):
        from tests.browser.auth_server import create_test_app

        environment_keys = (
            "DATABASE_PATH",
            "FLASK_SECRET_KEY",
            "FLASK_ENV",
            "APSTUDY_ALLOW_INSECURE_HTTP",
            "SCHEDULER_ENABLED",
        )
        with patch.dict(os.environ, clear=False), tempfile.TemporaryDirectory() as temp_dir:
            os.environ["DATABASE_PATH"] = "caller-database.sqlite3"
            os.environ.pop("FLASK_SECRET_KEY", None)
            os.environ["FLASK_ENV"] = "development"
            os.environ.pop("APSTUDY_ALLOW_INSECURE_HTTP", None)
            os.environ["SCHEDULER_ENABLED"] = "1"
            before = {
                key: (key in os.environ, os.environ.get(key))
                for key in environment_keys
            }

            app = create_test_app(os.path.join(temp_dir, "test.sqlite3"))

            after = {
                key: (key in os.environ, os.environ.get(key))
                for key in environment_keys
            }

        self.assertEqual(after, before)
        self.assertTrue(app.testing)
        self.assertEqual(app.secret_key, "nest-apstudy-browser-test-secret")
        self.assertFalse(app.config["SESSION_COOKIE_SECURE"])
        self.assertEqual(app.config["DATABASE_PATH"], os.path.join(temp_dir, "test.sqlite3"))

    def test_create_test_app_restores_environment_when_app_construction_fails(self):
        from tests.browser.auth_server import create_test_app

        with patch.dict(os.environ, clear=False):
            os.environ["DATABASE_PATH"] = "caller-database.sqlite3"
            os.environ.pop("FLASK_SECRET_KEY", None)
            os.environ["FLASK_ENV"] = "development"
            os.environ.pop("APSTUDY_ALLOW_INSECURE_HTTP", None)
            os.environ["SCHEDULER_ENABLED"] = "1"
            before = dict(os.environ)

            with patch("app.create_app", side_effect=RuntimeError("construction failed")):
                with self.assertRaisesRegex(RuntimeError, "construction failed"):
                    create_test_app("test.sqlite3")

            self.assertEqual(dict(os.environ), before)

    def test_subsequent_non_test_app_does_not_inherit_test_configuration(self):
        from app import create_app
        from tests.browser.auth_server import AUTH_ROUTE, create_test_app

        with tempfile.TemporaryDirectory() as temp_dir:
            test_database_path = os.path.join(temp_dir, "test.sqlite3")
            normal_database_path = os.path.join(temp_dir, "normal.sqlite3")
            create_test_app(test_database_path)

            with patch.dict(
                os.environ,
                {
                    "DATABASE_PATH": normal_database_path,
                    "FLASK_SECRET_KEY": "normal-development-secret",
                    "FLASK_ENV": "development",
                    "APSTUDY_ALLOW_INSECURE_HTTP": "0",
                    "SCHEDULER_ENABLED": "1",
                },
                clear=False,
            ), patch("services.discord_audit.init_discord_audit"), patch(
                "services.scheduler.init_scheduler"
            ):
                app = create_app()

        self.assertFalse(app.testing)
        self.assertEqual(app.secret_key, "normal-development-secret")
        self.assertTrue(app.config["SESSION_COOKIE_SECURE"])
        self.assertEqual(app.config["DATABASE_PATH"], normal_database_path)
        self.assertEqual(
            app.extensions["apstudy.environment_config"].scheduler_enabled_raw,
            "1",
        )
        self.assertNotIn(AUTH_ROUTE, {rule.rule for rule in app.url_map.iter_rules()})

    def test_auth_route_is_absent_from_a_non_testing_app(self):
        from app import create_app
        from tests.browser.auth_server import AUTH_ROUTE

        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ,
            {
                "DATABASE_PATH": os.path.join(temp_dir, "nest.sqlite3"),
                "FLASK_SECRET_KEY": "contract-test-secret",
                "FLASK_ENV": "testing",
                "APSTUDY_ALLOW_INSECURE_HTTP": "1",
                "SCHEDULER_ENABLED": "0",
            },
            clear=False,
        ), patch("services.discord_audit.init_discord_audit"), patch("services.scheduler.init_scheduler"):
            app = create_app()

        self.assertFalse(app.testing)
        self.assertNotIn(AUTH_ROUTE, {rule.rule for rule in app.url_map.iter_rules()})


if __name__ == "__main__":
    unittest.main()
