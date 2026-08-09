import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import create_app


REPO_ROOT = Path(__file__).resolve().parents[1]


class EnvironmentOperationsConfigTests(unittest.TestCase):
    def test_apswiftly_values_keep_import_time_normalization(self):
        environment = os.environ.copy()
        environment.update(
            {
                "APSWIFTLY_CONTROL_URL": "https://control.example///",
                "APSWIFTLY_CONTROL_TOKEN": " token ",
                "APSWIFTLY_SERVICE_NAME": "   ",
                "APSWIFTLY_CONTROL_TIMEOUT_SECONDS": "0",
            }
        )
        code = (
            "import json; import services.apswiftly_control as control; "
            "print(json.dumps([control.APSWIFTLY_CONTROL_URL, "
            "control.APSWIFTLY_CONTROL_TOKEN, control.APSWIFTLY_SERVICE_NAME, "
            "control.APSWIFTLY_CONTROL_TIMEOUT_SECONDS]))"
        )

        completed = subprocess.run(
            [sys.executable, "-c", code],
            cwd=REPO_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=True,
        )

        self.assertEqual(
            json.loads(completed.stdout),
            ["https://control.example", "token", "apswiftly", 1],
        )

    def test_apswiftly_timeout_errors_still_happen_during_import(self):
        environment = os.environ.copy()
        environment["APSWIFTLY_CONTROL_TIMEOUT_SECONDS"] = "invalid"

        completed = subprocess.run(
            [sys.executable, "-c", "import services.apswiftly_control"],
            cwd=REPO_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("ValueError", completed.stderr)

    def test_backup_cli_keeps_invocation_time_values(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.dict(
                os.environ,
                {
                    "DATABASE_PATH": os.path.join(temp_dir, "nest.sqlite3"),
                    "FLASK_SECRET_KEY": "test-secret",
                    "FLASK_ENV": "testing",
                    "SCHEDULER_ENABLED": "0",
                    "DISCORD_AUDIT_ENABLED": "0",
                    "NEST_BACKUP_DIR": os.path.join(temp_dir, "initial"),
                    "NEST_BACKUP_RETENTION": "2",
                },
                clear=False,
            ), patch("services.discord_audit.init_discord_audit"), patch(
                "services.scheduler.init_scheduler"
            ):
                app = create_app()

            changed_backup_dir = os.path.join(temp_dir, "changed")
            with patch.dict(
                os.environ,
                {
                    "NEST_BACKUP_DIR": changed_backup_dir,
                    "NEST_BACKUP_RETENTION": "9",
                },
                clear=False,
            ), patch(
                "scripts.backup_nest_db.run_backup", return_value=0
            ) as run_backup, patch(
                "services.database.nest_instance_dir", return_value=temp_dir
            ):
                result = app.test_cli_runner().invoke(args=["backup-db"])

            self.assertEqual(result.exit_code, 0, result.output)
            self.assertEqual(
                run_backup.call_args.kwargs["backup_dir"], Path(changed_backup_dir)
            )
            self.assertEqual(run_backup.call_args.kwargs["max_backups"], 9)


if __name__ == "__main__":
    unittest.main()
