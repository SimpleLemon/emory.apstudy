import hashlib
import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask, render_template

import blueprints.dashboard as dashboard_bp
from services import calendar_assets


class CalendarAssetRuntimeTests(unittest.TestCase):
    def setUp(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.app = Flask(
            __name__,
            template_folder=os.path.join(root, "templates"),
            static_folder=os.path.join(root, "static"),
        )
        self.app.secret_key = "test"
        self.app.config["SERVER_NAME"] = "example.test"
        self.app.config["CALENDAR_ASSET_VERSION"] = calendar_assets.calendar_asset_version()
        self.app.jinja_env.filters["avatar_url"] = lambda value, _size: value
        self.app.add_url_rule("/files", endpoint="file_share.file_share_page", view_func=lambda: "")
        self.app.add_url_rule("/settings", endpoint="settings.settings_page", view_func=lambda: "")
        self.app.add_url_rule("/admin", endpoint="admin.admin_index", view_func=lambda: "")
        self.app.register_blueprint(dashboard_bp.dashboard_bp)
        self.user = SimpleNamespace(
            id="user-1",
            name="Derek",
            username="derek",
            email="derek@example.test",
            picture_url="",
            emory_student=False,
            school="Nest University",
            onboarding_complete=True,
            is_authenticated=True,
        )

    def test_calendar_route_uses_the_validated_manifest_version(self):
        with self.app.test_request_context("/calendar"):
            with patch.object(dashboard_bp, "current_user", self.user), \
                    patch.object(dashboard_bp, "_load_user_settings", return_value={}), \
                    patch.object(
                        dashboard_bp,
                        "runtime_environment_config",
                        return_value=SimpleNamespace(calendar_date_buffer_days_raw="7"),
                    ):
                    html = dashboard_bp.calendar.__wrapped__()

        self.assertIn(
            f"/static/js/calendar/entry.js?v={self.app.config['CALENDAR_ASSET_VERSION']}",
            html,
        )

    def test_public_calendar_share_template_uses_the_same_version(self):
        with self.app.test_request_context("/calendar/share/not-found"):
            with patch.object(dashboard_bp, "current_user", self.user), \
                    patch.object(dashboard_bp, "_resolve_calendar_share_by_code", return_value=None), \
                    patch.object(
                        dashboard_bp,
                        "runtime_environment_config",
                        return_value=SimpleNamespace(calendar_date_buffer_days_raw="7"),
                    ), patch.object(dashboard_bp, "render_template", return_value="share-html") as render:
                response, status = dashboard_bp.public_calendar_share("not-found")

        self.assertEqual(status, 404)
        self.assertEqual(response, "share-html")
        self.assertEqual(
            render.call_args.kwargs["calendar_asset_version"],
            self.app.config["CALENDAR_ASSET_VERSION"],
        )

    def test_shared_calendar_template_emits_versioned_entry_when_found(self):
        with self.app.test_request_context("/calendar/share/example"):
            html = render_template(
                "calendar_share.html",
                share_found=True,
                share_code="example",
                owner_name="Derek",
                scope_label="All calendars",
                theme_preference=None,
                preferred_calendar_view="month",
                calendar_buffer_days=7,
                calendar_asset_version=self.app.config["CALENDAR_ASSET_VERSION"],
            )

        self.assertIn(
            f"/static/js/calendar/entry.js?v={self.app.config['CALENDAR_ASSET_VERSION']}",
            html,
        )

    def test_missing_manifest_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = os.path.join(directory, "manifest.json")
            with patch.object(calendar_assets, "MANIFEST_PATH", calendar_assets.Path(missing)):
                calendar_assets.calendar_asset_version.cache_clear()
                with self.assertRaises(calendar_assets.CalendarAssetError):
                    calendar_assets.calendar_asset_version()
        calendar_assets.calendar_asset_version.cache_clear()

    def test_manifest_version_is_content_derived(self):
        manifest = json.loads(calendar_assets.MANIFEST_PATH.read_text(encoding="utf-8"))
        self.assertRegex(manifest["version"], r"^[0-9a-f]{64}$")
        self.assertEqual(manifest["entry"], "js/calendar/entry.js")
        self.assertTrue(any(item["path"].endswith("events/ui-actions.js") for item in manifest["modules"]))

    def test_dashboard_reads_the_manifest_aware_version_each_render(self):
        with self.app.app_context(), patch.object(
            dashboard_bp,
            "calendar_asset_version",
            side_effect=["a" * 64, "b" * 64],
        ):
            self.assertEqual(dashboard_bp._calendar_asset_version(), "a" * 64)
            self.assertEqual(dashboard_bp._calendar_asset_version(), "b" * 64)


class CalendarAssetManifestCacheTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.static_root = calendar_assets.Path(self.temporary.name) / "static"
        self.module_path = self.static_root / calendar_assets.ENTRY_PATH
        self.manifest_path = self.static_root / "js/calendar/manifest.json"
        self.module_path.parent.mkdir(parents=True)
        self.patch_root = patch.object(calendar_assets, "STATIC_ROOT", self.static_root)
        self.patch_manifest = patch.object(calendar_assets, "MANIFEST_PATH", self.manifest_path)
        self.patch_root.start()
        self.patch_manifest.start()
        calendar_assets.calendar_asset_version.cache_clear()

    def tearDown(self):
        calendar_assets.calendar_asset_version.cache_clear()
        self.patch_manifest.stop()
        self.patch_root.stop()
        self.temporary.cleanup()

    def _publish(self, content: bytes, version: str):
        self.module_path.write_bytes(content)
        manifest = {
            "schema": 2,
            "entry": calendar_assets.ENTRY_PATH,
            "version": version,
            "modules": [{
                "path": calendar_assets.ENTRY_PATH,
                "sha256": hashlib.sha256(content).hexdigest(),
            }],
            "edges": [],
        }
        replacement = self.manifest_path.with_suffix(".next")
        replacement.write_text(f"{json.dumps(manifest)}\n", encoding="utf-8")
        os.replace(replacement, self.manifest_path)

    def test_manifest_identity_change_refreshes_cached_version(self):
        self._publish(b"export const value = 1;\n", "1" * 64)
        self.assertEqual(calendar_assets.calendar_asset_version(), "1" * 64)
        self._publish(b"export const value = 2;\n", "2" * 64)
        self.assertEqual(calendar_assets.calendar_asset_version(), "2" * 64)

    def test_malformed_manifest_and_raw_hash_mismatch_fail_closed(self):
        self._publish(b"export const value = 1;\n", "1" * 64)
        self.manifest_path.write_text("not-json", encoding="utf-8")
        with self.assertRaises(calendar_assets.CalendarAssetError):
            calendar_assets.calendar_asset_version()
        self._publish(b"export const value = 1;\n", "1" * 64)
        self.module_path.write_bytes(b"tampered\n")
        calendar_assets.calendar_asset_version.cache_clear()
        with self.assertRaises(calendar_assets.CalendarAssetError):
            calendar_assets.calendar_asset_version()

    def test_manifest_and_module_symlink_escapes_fail_closed(self):
        outside = calendar_assets.Path(self.temporary.name) / "outside"
        outside.write_text("{}", encoding="utf-8")
        self.manifest_path.symlink_to(outside)
        with self.assertRaises(calendar_assets.CalendarAssetError):
            calendar_assets.calendar_asset_version()
        self.manifest_path.unlink()
        self._publish(b"export const value = 1;\n", "1" * 64)
        self.module_path.unlink()
        self.module_path.symlink_to(outside)
        calendar_assets.calendar_asset_version.cache_clear()
        with self.assertRaises(calendar_assets.CalendarAssetError):
            calendar_assets.calendar_asset_version()

    def test_manifest_traversal_path_fails_closed(self):
        self._publish(b"export const value = 1;\n", "1" * 64)
        manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        manifest["modules"][0]["path"] = "../outside.js"
        self.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaises(calendar_assets.CalendarAssetError):
            calendar_assets.calendar_asset_version()


if __name__ == "__main__":
    unittest.main()
