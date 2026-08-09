import os
import unittest
from unittest.mock import patch

from flask import Flask

from config import ENVIRONMENT_CONFIG_EXTENSION_KEY, load_environment_config
from services import database


class DatabasePathTestCase(unittest.TestCase):
    def test_database_path_resolves_relative_env_path(self):
        with patch.dict(
            os.environ,
            {"DATABASE_PATH": "instance/nest.sqlite3", "FLASK_ENV": "development"},
            clear=True,
        ):
            resolved = database.database_path()

        self.assertEqual(resolved, os.path.join(database.BASE_DIR, "instance", "nest.sqlite3"))

    def test_nest_instance_dir_prefers_env_override(self):
        with patch.dict(
            os.environ,
            {
                "DATABASE_PATH": "instance/nest.sqlite3",
                "NEST_INSTANCE_DIR": "instance",
                "FLASK_ENV": "development",
            },
            clear=True,
        ):
            resolved = database.nest_instance_dir()

        self.assertEqual(resolved, os.path.join(database.BASE_DIR, "instance"))

    def test_nest_instance_dir_derives_from_database_path(self):
        with patch.dict(
            os.environ,
            {"DATABASE_PATH": "/var/www/nest.apstudy.org/instance/nest.sqlite3", "FLASK_ENV": "production"},
            clear=True,
        ):
            resolved = database.nest_instance_dir()

        self.assertEqual(resolved, "/var/www/nest.apstudy.org/instance")

    def test_database_path_uses_registered_environment_snapshot(self):
        with patch.dict(
            os.environ,
            {"DATABASE_PATH": "first.sqlite3", "FLASK_ENV": "development"},
            clear=True,
        ):
            configured = load_environment_config()

        app = Flask(__name__)
        app.extensions[ENVIRONMENT_CONFIG_EXTENSION_KEY] = configured
        with patch.dict(
            os.environ,
            {"DATABASE_PATH": "changed.sqlite3", "FLASK_ENV": "development"},
            clear=True,
        ), app.app_context():
            resolved = database.database_path()

        self.assertEqual(resolved, os.path.join(database.BASE_DIR, "first.sqlite3"))


if __name__ == "__main__":
    unittest.main()
