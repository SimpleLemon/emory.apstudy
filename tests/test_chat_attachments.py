import io
import os
import unittest
import zipfile
from unittest.mock import patch

from PIL import Image
from flask import Flask

from blueprints import chat_api
from config import ENVIRONMENT_CONFIG_EXTENSION_KEY, load_environment_config
from services import chat_attachments
from services.entitlements import DEFAULT_TIER_DEFINITIONS, MIB


class ChatAttachmentValidationTests(unittest.TestCase):
    def test_attachment_capability_uses_the_registered_environment_snapshot(self):
        with patch.dict(
            os.environ,
            {"APPWRITE_CHAT_ATTACHMENTS_BUCKET_ID": ""},
            clear=True,
        ):
            configured = load_environment_config()

        app = Flask(__name__)
        app.extensions[ENVIRONMENT_CONFIG_EXTENSION_KEY] = configured
        with patch.dict(
            os.environ,
            {"APPWRITE_CHAT_ATTACHMENTS_BUCKET_ID": "configured-bucket"},
            clear=True,
        ), app.app_context(), patch.object(chat_attachments, "list_rows_all") as list_rows:
            self.assertFalse(chat_attachments._chat_attachments_enabled())
            self.assertFalse(chat_api._appwrite_chat_attachments_enabled())
            self.assertEqual(chat_attachments.attachments_for_messages(["message-1"]), {})
            list_rows.assert_not_called()

    def test_chat_limits_are_independent_from_regular_file_limits(self):
        self.assertEqual(DEFAULT_TIER_DEFINITIONS["free"]["max_chat_attachment_size_bytes"], 10 * MIB)
        for tier in ("grade_a", "grade_aa", "developer"):
            self.assertEqual(DEFAULT_TIER_DEFINITIONS[tier]["max_chat_attachment_size_bytes"], 50 * MIB)
        self.assertEqual(DEFAULT_TIER_DEFINITIONS["free"]["max_file_size_bytes"], 25 * MIB)

    def test_text_is_gzipped_and_download_restores_original_bytes(self):
        original = ("study notes\n" * 500).encode()
        prepared = chat_attachments.inspect_and_prepare(original, "notes.md")
        self.assertEqual(prepared["compression_encoding"], "gzip")
        self.assertLess(prepared["stored_size_bytes"], len(original))
        row = {"storage_file_id": "file", "compression_encoding": "gzip"}
        with patch.object(chat_attachments, "storage_service") as storage:
            storage.return_value.get_file_download.return_value = prepared["stored"]
            self.assertEqual(chat_attachments.attachment_bytes(row), original)

    def test_animated_gif_is_preserved_without_flattening(self):
        first = Image.new("RGB", (8, 8), "red")
        second = Image.new("RGB", (8, 8), "blue")
        output = io.BytesIO()
        first.save(output, format="GIF", save_all=True, append_images=[second], duration=50, loop=0)
        original = output.getvalue()
        prepared = chat_attachments.inspect_and_prepare(original, "animated.gif")
        self.assertEqual(prepared["stored"], original)
        with Image.open(io.BytesIO(prepared["stored"])) as image:
            self.assertEqual(image.n_frames, 2)

    def test_png_validation_reads_metadata_only_after_integrity_check(self):
        output = io.BytesIO()
        Image.new("RGB", (12, 8), "gold").save(output, format="PNG")

        prepared = chat_attachments.inspect_and_prepare(output.getvalue(), "pasted-image.png")

        self.assertEqual(prepared["mime_type"], "image/png")
        self.assertEqual((prepared["width"], prepared["height"]), (12, 8))
        self.assertTrue(prepared["stored"].startswith(b"\x89PNG"))

    def test_pdf_thumbnail_is_best_effort_and_broken_preview_falls_back(self):
        document = io.BytesIO()
        Image.new("RGB", (120, 160), "white").save(document, format="PDF")
        prepared = chat_attachments.inspect_and_prepare(document.getvalue(), "reading.pdf")
        self.assertIsNotNone(prepared["preview"])
        self.assertGreater(len(prepared["preview"][0]), 0)

        broken = chat_attachments.inspect_and_prepare(b"%PDF-broken", "broken.pdf")
        self.assertIsNone(broken["preview"])
        self.assertEqual(broken["kind"], "pdf")

    def test_zip_rejects_path_traversal_and_executables(self):
        for name in ("../escape.txt", "payload.exe"):
            output = io.BytesIO()
            with zipfile.ZipFile(output, "w") as archive:
                archive.writestr(name, b"unsafe")
            with self.subTest(name=name), self.assertRaises(chat_attachments.AttachmentError):
                chat_attachments.inspect_and_prepare(output.getvalue(), "archive.zip")

    def test_disallowed_active_content_is_rejected(self):
        for filename in ("page.html", "vector.svg", "script.js", "macro.docm"):
            with self.subTest(filename=filename), self.assertRaises(chat_attachments.AttachmentError):
                chat_attachments.inspect_and_prepare(b"not allowed", filename)

    def test_serialized_non_image_requires_warning_and_hash_report(self):
        payload = chat_attachments.serialize_attachment({
            "$id": "attachment-1",
            "original_filename": "reading.pdf",
            "mime_type": "application/pdf",
            "kind": "pdf",
            "original_size_bytes": 2048,
            "sha256": "abc123",
        })
        self.assertTrue(payload["requires_download_warning"])
        self.assertIn("abc123", payload["virus_total_url"])

    def test_message_payload_accepts_attachment_only_and_gif_only(self):
        app = Flask(__name__)
        with app.test_request_context(json={"attachment_ids": ["attachment-1"]}):
            content, attachment_ids, gif = chat_api._message_media_payload()
        self.assertEqual(content, "")
        self.assertEqual(attachment_ids, ["attachment-1"])
        self.assertIsNone(gif)

        resolved = {"kind": "giphy_gif", "id": "gif-1", "url": "https://media.giphy.com/test.webp"}
        with app.test_request_context(json={"gif_id": "gif-1", "gif_query": "study"}), \
                patch.object(chat_api, "resolve_gif", return_value=resolved):
            content, attachment_ids, gif = chat_api._message_media_payload()
        self.assertEqual(content, "")
        self.assertEqual(attachment_ids, [])
        self.assertEqual(gif, resolved)

    def test_message_serialization_batch_loads_attachments_once(self):
        rows = [
            {"$id": "message-1", "content": "one", "created_at": "2026-07-14T10:00:00Z"},
            {"$id": "message-2", "content": "two", "created_at": "2026-07-14T10:01:00Z"},
        ]
        attachment_map = {"message-1": [{"id": "attachment-1"}]}
        app = Flask(__name__)
        with app.test_request_context(), \
                patch.object(chat_api, "_current_user_id", return_value="viewer"), \
                patch.object(chat_api, "_load_users_by_id", return_value={}), \
                patch.object(chat_api, "attachments_for_messages", return_value=attachment_map) as load:
            payload = chat_api._serialize_messages(rows)
        load.assert_called_once_with(["message-1", "message-2"])
        self.assertEqual(payload[0]["attachments"], [{"id": "attachment-1"}])
        self.assertEqual(payload[1]["attachments"], [])


if __name__ == "__main__":
    unittest.main()
