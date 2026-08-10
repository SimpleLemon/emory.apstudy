import io
import os
import unittest
import zipfile
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

from flask import Flask, Response

from appwrite_helpers import format_datetime
import blueprints.file_share as fs


class FileShareTestCase(unittest.TestCase):
    def setUp(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.app = Flask(
            __name__,
            template_folder=os.path.join(root, "templates"),
            static_folder=os.path.join(root, "static"),
        )
        self.app.secret_key = "test"
        self.app.config["SERVER_NAME"] = "example.test"
        self.app.register_blueprint(fs.file_share_bp)
        self.user = SimpleNamespace(
            id="user-1",
            name="Test User",
            email="test@example.com",
            picture_url="",
            emory_student=False,
        )

    def future(self, days=1):
        return format_datetime(datetime.now(timezone.utc) + timedelta(days=days))

    def expired(self):
        return format_datetime(datetime.now(timezone.utc) - timedelta(days=1))

    def file_row(self, row_id="file-1", **overrides):
        row = {
            "$id": row_id,
            "user_id": "user-1",
            "folder_id": None,
            "original_filename": f"{row_id}.txt",
            "stored_path": f"appwrite://file_share_files/{row_id}",
            "storage_backend": "appwrite",
            "storage_bucket_id": "file_share_files",
            "storage_file_id": row_id,
            "file_size_bytes": 5,
            "mime_type": "text/plain",
            "share_code": None,
            "is_public": False,
            "expires_at": self.future(),
            "created_at": self.future(),
            "updated_at": self.future(),
            "downloaded_count": 0,
        }
        row.update(overrides)
        return row

    def folder_row(self, row_id="folder-1", **overrides):
        row = {
            "$id": row_id,
            "user_id": "user-1",
            "name": f"Folder {row_id}",
            "parent_folder_id": None,
            "is_public": False,
            "share_code": None,
            "order": 1000,
            "created_at": self.future(),
            "updated_at": self.future(),
        }
        row.update(overrides)
        return row

    def test_my_files_empty_response(self):
        with self.app.test_request_context("/api/files/my"):
            with patch.object(fs, "current_user", self.user), \
                    patch.object(fs, "_assert_folder_target"), \
                    patch.object(fs, "_list_child_folders", return_value=[]), \
                    patch.object(fs, "_list_child_files", return_value=[]), \
                    patch.object(fs, "_list_all_user_folders", return_value=[]), \
                    patch.object(fs, "_list_all_user_files", return_value=[]):
                response = fs.my_files.__wrapped__()

        payload = response.get_json()
        self.assertEqual(payload["folders"], [])
        self.assertEqual(payload["files"], [])
        self.assertEqual(payload["breadcrumbs"][0]["name"], "My Files")

    def test_create_move_and_delete_folder(self):
        created_folder = self.folder_row("folder-1", name="Study Guides")
        with self.app.test_request_context("/api/files/folders", method="POST", json={"name": "Study Guides"}):
            with patch.object(fs, "current_user", self.user), \
                    patch.object(fs, "_assert_folder_target"), \
                    patch.object(fs, "_sibling_order", return_value=1000), \
                    patch.object(fs, "create_row_safe", return_value=created_folder) as create_row:
                response, status = fs.create_folder.__wrapped__()

        self.assertEqual(status, 201)
        self.assertEqual(response.get_json()["name"], "Study Guides")
        self.assertEqual(create_row.call_args.kwargs["data"]["parent_folder_id"], None)

        folder = self.folder_row("folder-1")
        moved_folder = self.folder_row("folder-1", parent_folder_id="folder-2")
        with self.app.test_request_context(
            "/api/files/folders/folder-1",
            method="PATCH",
            json={"parentFolderId": "folder-2"},
        ):
            with patch.object(fs, "current_user", self.user), \
                    patch.object(fs, "_folder_owner_or_404", return_value=folder), \
                    patch.object(fs, "_assert_folder_target"), \
                    patch.object(fs, "_list_all_user_folders", return_value=[folder, self.folder_row("folder-2")]), \
                    patch.object(fs, "_sibling_order", return_value=2000), \
                    patch.object(fs, "update_row_safe", return_value=moved_folder) as update_row:
                response = fs.update_folder.__wrapped__("folder-1")

        self.assertEqual(response.get_json()["parentFolderId"], "folder-2")
        self.assertEqual(update_row.call_args.args[2]["parent_folder_id"], "folder-2")

        file_inside = self.file_row("file-1", folder_id="folder-1")
        with self.app.test_request_context("/api/files/folders/folder-1", method="DELETE"):
            with patch.object(fs, "current_user", self.user), \
                    patch.object(fs, "_folder_owner_or_404", return_value=folder), \
                    patch.object(fs, "_collect_folder_tree_ids", return_value=["folder-1", "child-1"]), \
                    patch.object(fs, "_list_all_user_files", return_value=[file_inside]), \
                    patch.object(fs, "_delete_shared_file_row") as delete_file, \
                    patch.object(fs, "delete_row_safe") as delete_row:
                response = fs.delete_folder.__wrapped__("folder-1")

        self.assertTrue(response.get_json()["ok"])
        delete_file.assert_called_once_with(file_inside)
        self.assertEqual([call.args[1] for call in delete_row.call_args_list], ["child-1", "folder-1"])

    def test_upload_writes_appwrite_storage_metadata(self):
        stored_rows = []

        def create_row(_table, row_id, data, permissions=None):
            stored_rows.append(data)
            return {"$id": row_id, **data}

        storage = Mock()
        data = {
            "folderId": "folder-1",
            "filename": "notes.txt",
            "visibility": "private",
            "expiryDays": "1",
            "file": (io.BytesIO(b"hello"), "notes.txt"),
        }
        with self.app.test_request_context("/api/files/upload", method="POST", data=data):
            with patch.object(fs, "current_user", self.user), \
                    patch.object(fs, "_assert_folder_target"), \
                    patch.object(fs, "_storage", return_value=storage), \
                    patch.object(fs, "_generate_share_code", return_value="ABC1234"), \
                    patch.object(fs, "create_row_safe", side_effect=create_row):
                response, status = fs.upload_file.__wrapped__()

        payload = response.get_json()
        self.assertEqual(status, 201)
        self.assertEqual(payload["files"][0]["folderId"], "folder-1")
        storage.create_file.assert_called_once()
        self.assertEqual(stored_rows[0]["folder_id"], "folder-1")
        self.assertEqual(stored_rows[0]["storage_backend"], "appwrite")
        self.assertEqual(stored_rows[0]["storage_bucket_id"], fs.FILE_SHARE_BUCKET_ID)
        self.assertTrue(stored_rows[0]["stored_path"].startswith("appwrite://"))

    def test_public_upload_cleans_storage_when_share_code_generation_fails(self):
        from appwrite.exception import AppwriteException

        storage = Mock()
        data = {
            "visibility": "public",
            "expiryDays": "1",
            "file": (io.BytesIO(b"hello"), "notes.txt"),
        }
        with self.app.test_request_context("/api/files/upload", method="POST", data=data):
            with patch.object(fs, "current_user", self.user), \
                    patch.object(fs, "_assert_folder_target"), \
                    patch.object(fs, "_storage", return_value=storage), \
                    patch.object(fs, "_generate_share_code", side_effect=AppwriteException("lookup failed")):
                response, status = fs.upload_file.__wrapped__()

        self.assertEqual(status, 400)
        self.assertEqual(response.get_json()["error"], "Unable to save file.")
        storage.create_file.assert_called_once()
        storage.delete_file.assert_called_once()

    def test_upload_rejects_more_than_five_files(self):
        storage = Mock()
        files = [(io.BytesIO(f"file-{idx}".encode("utf-8")), f"f{idx}.txt") for idx in range(6)]
        data = {
            "file": files,
            "filename": [f"f{idx}.txt" for idx in range(6)],
            "visibility": ["private"] * 6,
            "expiryDays": ["1"] * 6,
        }
        with self.app.test_request_context("/api/files/upload", method="POST", data=data):
            with patch.object(fs, "current_user", self.user), \
                    patch.object(fs, "_assert_folder_target"), \
                    patch.object(fs, "_storage", return_value=storage), \
                    patch.object(fs, "create_row_safe", side_effect=lambda _table, row_id, data, permissions=None: {"$id": row_id, **data}):
                response, status = fs.upload_file.__wrapped__()

        payload = response.get_json()
        self.assertEqual(status, 201)
        self.assertEqual(len(payload["files"]), 5)
        self.assertEqual(payload["skipped"], 1)
        self.assertEqual(storage.create_file.call_count, 5)

    def test_single_file_download_uses_send_helper(self):
        row = self.file_row("file-1")
        with self.app.test_request_context("/api/files/my/file-1/download"):
            with patch.object(fs, "current_user", self.user), \
                    patch.object(fs, "_file_owner_or_404", return_value=row), \
                    patch.object(fs, "_send_shared_file", return_value=Response("ok")) as send_file:
                response = fs.download_my_file.__wrapped__("file-1")

        self.assertEqual(response.get_data(as_text=True), "ok")
        send_file.assert_called_once_with(row)

    def test_update_file_expiry_sets_new_expiration_from_now(self):
        row = self.file_row("file-1")
        updated = self.file_row("file-1")
        with self.app.test_request_context("/api/files/my/file-1", method="PATCH", json={"expiryDays": "7"}):
            with patch.object(fs, "current_user", self.user), \
                    patch.object(fs, "_file_owner_or_404", return_value=row), \
                    patch.object(fs, "update_row_safe", return_value=updated) as update_row:
                response = fs.update_my_file.__wrapped__("file-1")

        self.assertEqual(response.get_json()["id"], "file-1")
        updates = update_row.call_args.args[2]
        self.assertIn("expires_at", updates)
        expires_at = datetime.fromisoformat(updates["expires_at"].replace("Z", "+00:00"))
        lower = datetime.now(timezone.utc) + timedelta(days=6, hours=23)
        upper = datetime.now(timezone.utc) + timedelta(days=7, minutes=1)
        self.assertGreaterEqual(expires_at, lower)
        self.assertLessEqual(expires_at, upper)

    def test_update_file_expiry_rejects_invalid_selection(self):
        row = self.file_row("file-1")
        with self.app.test_request_context("/api/files/my/file-1", method="PATCH", json={"expiryDays": "365"}):
            with patch.object(fs, "current_user", self.user), \
                    patch.object(fs, "_file_owner_or_404", return_value=row), \
                    patch.object(fs, "update_row_safe") as update_row:
                response, status = fs.update_my_file.__wrapped__("file-1")

        self.assertEqual(status, 400)
        self.assertEqual(response.get_json()["error"], "Invalid expiry selection.")
        update_row.assert_not_called()

    def test_file_visibility_private_invalidates_share_code(self):
        row = self.file_row("file-1", is_public=True, share_code="ABC1234")
        updated = self.file_row("file-1", is_public=False, share_code=None)
        with self.app.test_request_context("/api/files/my/file-1/visibility", method="POST", json={"visibility": "private"}):
            with patch.object(fs, "current_user", self.user), \
                    patch.object(fs, "_file_owner_or_404", return_value=row), \
                    patch.object(fs, "update_row_safe", return_value=updated) as update_row:
                response, status = fs.change_visibility.__wrapped__("file-1")

        self.assertEqual(status, 200)
        self.assertFalse(response.get_json()["isPublic"])
        updates = update_row.call_args.args[2]
        self.assertFalse(updates["is_public"])
        self.assertIsNone(updates["share_code"])

    def test_folder_payload_has_no_expiry(self):
        payload = fs._folder_payload(self.folder_row("folder-1"))

        self.assertNotIn("expiresAt", payload)
        self.assertFalse(payload["isPublic"])

    def test_folder_visibility_private_invalidates_share_code(self):
        row = self.folder_row("folder-1", is_public=True, share_code="ABC1234")
        updated = self.folder_row("folder-1", is_public=False, share_code=None)
        with self.app.test_request_context("/api/files/folders/folder-1/visibility", method="POST", json={"visibility": "private"}):
            with patch.object(fs, "_folder_owner_or_404", return_value=row), \
                    patch.object(fs, "update_row_safe", return_value=updated) as update_row:
                response = fs.change_folder_visibility.__wrapped__("folder-1")

        self.assertFalse(response.get_json()["isPublic"])
        updates = update_row.call_args.args[2]
        self.assertFalse(updates["is_public"])
        self.assertIsNone(updates["share_code"])

    def test_bulk_download_zip_includes_owned_unexpired_files_only(self):
        rows = {
            "file-1": self.file_row("file-1", original_filename="same.txt"),
            "file-2": self.file_row("file-2", original_filename="same.txt"),
            "expired": self.file_row("expired", expires_at=self.expired()),
            "other": self.file_row("other", user_id="other-user"),
        }

        with self.app.test_request_context(
            "/api/files/bulk-download.zip",
            method="POST",
            json={"fileIds": ["file-1", "file-2", "expired", "other"]},
        ):
            with patch.object(fs, "current_user", self.user), \
                    patch.object(fs, "get_row_safe", side_effect=lambda _table, row_id, allow_missing=False: rows.get(row_id)), \
                    patch.object(fs, "_storage_download_bytes", side_effect=lambda row: row["$id"].encode("utf-8")):
                response = fs.bulk_download_files.__wrapped__()

        response.direct_passthrough = False
        with zipfile.ZipFile(io.BytesIO(response.get_data())) as archive:
            self.assertEqual(sorted(archive.namelist()), ["same-2.txt", "same.txt"])
            self.assertEqual(archive.read("same.txt"), b"file-1")
            self.assertEqual(archive.read("same-2.txt"), b"file-2")

    def test_zip_response_skips_expired_files(self):
        active = self.file_row("active", original_filename="active.txt")
        expired = self.file_row("expired", original_filename="expired.txt", expires_at=self.expired())
        with self.app.test_request_context("/download.zip"):
            with patch.object(fs, "_storage_download_bytes", return_value=b"content") as storage_download:
                response = fs._zip_response("folder", [active, expired])

        response.direct_passthrough = False
        with zipfile.ZipFile(io.BytesIO(response.get_data())) as archive:
            self.assertEqual(archive.namelist(), ["active.txt"])
        storage_download.assert_called_once_with(active)

    def test_public_folder_route_renders_template(self):
        root = self.folder_row("folder-1", name="Shared Notes", is_public=True, share_code="ABC1234")
        tree = {
            "id": "folder-1",
            "name": "Shared Notes",
            "zipUrl": "/files/folder/ABC1234?download=zip&folderId=folder-1",
            "files": [
                {
                    "id": "file-1",
                    "filename": "notes.txt",
                    "fileSizeDisplay": "5 B",
                    "expiresAt": self.future(),
                    "downloadUrl": "/files/folder/ABC1234/download/file-1",
                }
            ],
            "folders": [],
        }
        with self.app.test_request_context("/files/folder/ABC1234"):
            with patch.object(fs, "_public_folder_by_code", return_value=root), \
                    patch.object(fs, "_build_public_folder_tree", return_value=(tree, {"folder-1"})):
                html = fs.public_folder_share("ABC1234")

        self.assertIn("Shared Notes", html)
        self.assertIn("notes.txt", html)

    def test_appwrite_upload_error_maps_size_limit(self):
        from appwrite.exception import AppwriteException

        exc = AppwriteException("File size is larger than maximum allowed size", 400, "general_file_too_large")
        self.assertEqual(
            fs._appwrite_upload_error(exc),
            "File exceeds the storage bucket size limit.",
        )

    def test_upload_surfaces_appwrite_error_and_top_level_error(self):
        from appwrite.exception import AppwriteException

        storage = Mock()
        storage.create_file.side_effect = AppwriteException(
            "File size is larger than maximum allowed size",
            400,
            "general_file_too_large",
        )
        data = {
            "folderId": "folder-1",
            "filename": "large.bin",
            "visibility": "private",
            "expiryDays": "1",
            "file": (io.BytesIO(b"x" * 16), "large.bin"),
        }
        with self.app.test_request_context("/api/files/upload", method="POST", data=data):
            with patch.object(fs, "current_user", self.user), \
                    patch.object(fs, "_assert_folder_target"), \
                    patch.object(fs, "_storage", return_value=storage):
                response, status = fs.upload_file.__wrapped__()

        payload = response.get_json()
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"], "File exceeds the storage bucket size limit.")
        self.assertEqual(payload["errors"][0]["error"], "File exceeds the storage bucket size limit.")


if __name__ == "__main__":
    unittest.main()
