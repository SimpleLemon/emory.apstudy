import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
APPWRITE_KEYS = (
    "APPWRITE_ENDPOINT",
    "APPWRITE_PROJECT_ID",
    "APPWRITE_API_KEY",
    "APPWRITE_DATABASE_ID",
    "APPWRITE_PROFILE_AVATAR_BUCKET_ID",
    "APPWRITE_FILE_SHARE_BUCKET_ID",
    "APPWRITE_NOTES_MEDIA_BUCKET_ID",
    "APPWRITE_CHAT_ATTACHMENTS_BUCKET_ID",
)


class AppwriteClientConfigurationTests(unittest.TestCase):
    def _run_probe(self, source, values=None):
        child_environment = os.environ.copy()
        for key in APPWRITE_KEYS:
            child_environment.pop(key, None)
        child_environment.update(values or {})
        completed = subprocess.run(
            [sys.executable, "-c", source],
            cwd=PROJECT_ROOT,
            env=child_environment,
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(completed.stdout)

    def test_missing_import_time_values_keep_existing_defaults(self):
        values = self._run_probe(
            """
import json
import appwrite_client

print(json.dumps({
    "endpoint": appwrite_client.ENDPOINT,
    "project": appwrite_client.PROJECT_ID,
    "database": appwrite_client.DATABASE_ID,
    "tablesdb": appwrite_client.tablesdb is not None,
    "profile_bucket": appwrite_client.PROFILE_AVATAR_BUCKET_ID,
    "file_bucket": appwrite_client.FILE_SHARE_BUCKET_ID,
    "notes_bucket": appwrite_client.NOTES_MEDIA_BUCKET_ID,
    "chat_bucket": appwrite_client.CHAT_ATTACHMENTS_BUCKET_ID,
}))
"""
        )

        self.assertIsNone(values["endpoint"])
        self.assertIsNone(values["project"])
        self.assertIsNone(values["database"])
        self.assertFalse(values["tablesdb"])
        self.assertEqual(values["profile_bucket"], "profile_avatars")
        self.assertEqual(values["file_bucket"], "file_share_files")
        self.assertEqual(values["notes_bucket"], "notes_media")
        self.assertEqual(values["chat_bucket"], "chat_attachments")

    def test_import_time_values_and_explicit_empty_bucket_are_preserved(self):
        values = self._run_probe(
            """
import json
import appwrite_client

print(json.dumps({
    "endpoint": appwrite_client.ENDPOINT,
    "project": appwrite_client.PROJECT_ID,
    "database": appwrite_client.DATABASE_ID,
    "tablesdb": appwrite_client.tablesdb is not None,
    "project_header": appwrite_client.client._global_headers.get("x-appwrite-project"),
    "key_header": appwrite_client.client._global_headers.get("x-appwrite-key"),
    "profile_bucket": appwrite_client.PROFILE_AVATAR_BUCKET_ID,
    "file_bucket": appwrite_client.FILE_SHARE_BUCKET_ID,
    "notes_bucket": appwrite_client.NOTES_MEDIA_BUCKET_ID,
    "chat_bucket": appwrite_client.CHAT_ATTACHMENTS_BUCKET_ID,
}))
""",
            {
                "APPWRITE_ENDPOINT": "https://appwrite.example/v1",
                "APPWRITE_PROJECT_ID": "project-id",
                "APPWRITE_API_KEY": "api-key",
                "APPWRITE_DATABASE_ID": "database-id",
                "APPWRITE_PROFILE_AVATAR_BUCKET_ID": "",
                "APPWRITE_FILE_SHARE_BUCKET_ID": "files",
                "APPWRITE_NOTES_MEDIA_BUCKET_ID": "note-media",
                "APPWRITE_CHAT_ATTACHMENTS_BUCKET_ID": "chat-files",
            },
        )

        self.assertEqual(values["endpoint"], "https://appwrite.example/v1")
        self.assertEqual(values["project"], "project-id")
        self.assertEqual(values["database"], "database-id")
        self.assertTrue(values["tablesdb"])
        self.assertEqual(values["project_header"], "project-id")
        self.assertEqual(values["key_header"], "api-key")
        self.assertEqual(values["profile_bucket"], "")
        self.assertEqual(values["file_bucket"], "files")
        self.assertEqual(values["notes_bucket"], "note-media")
        self.assertEqual(values["chat_bucket"], "chat-files")

    def test_create_app_imports_appwrite_client_from_one_environment_snapshot(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            values = self._run_probe(
                """
import json
from app import create_app
from config import get_environment_config

app = create_app()
import appwrite_client

configured = get_environment_config(app)
print(json.dumps({
    "same_endpoint": configured.appwrite_endpoint == appwrite_client.ENDPOINT,
    "same_project": configured.appwrite_project_id == appwrite_client.PROJECT_ID,
    "same_database": configured.appwrite_database_id_raw == appwrite_client.DATABASE_ID,
    "same_profile_bucket": (
        configured.appwrite_profile_avatar_bucket_id
        == appwrite_client.PROFILE_AVATAR_BUCKET_ID
    ),
}))
""",
                {
                    "DATABASE_PATH": os.path.join(temp_dir, "nest.sqlite3"),
                    "FLASK_ENV": "testing",
                    "FLASK_SECRET_KEY": "test-secret",
                    "SCHEDULER_ENABLED": "0",
                    "DISCORD_AUDIT_ENABLED": "0",
                    "APPWRITE_ENDPOINT": "https://appwrite.example/v1",
                    "APPWRITE_PROJECT_ID": "project-id",
                    "APPWRITE_API_KEY": "api-key",
                    "APPWRITE_DATABASE_ID": "database-id",
                    "APPWRITE_PROFILE_AVATAR_BUCKET_ID": "avatars",
                },
            )

        self.assertTrue(values["same_endpoint"])
        self.assertTrue(values["same_project"])
        self.assertTrue(values["same_database"])
        self.assertTrue(values["same_profile_bucket"])


if __name__ == "__main__":
    unittest.main()
