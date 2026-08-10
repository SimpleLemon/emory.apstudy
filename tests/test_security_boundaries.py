import os
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import app as app_module
import blueprints.file_share as file_share
import blueprints.notes_api as notes_api
import requests
from extensions import login_manager
from services import feed_fetcher
from services import avatar_storage
from services.outbound_http import is_public_http_url, redacted_url


class OutboundHttpSecurityTests(unittest.TestCase):
    def test_private_and_mixed_resolution_hosts_are_rejected(self):
        private = [(None, None, None, None, ("127.0.0.1", 443))]
        mixed = [
            (None, None, None, None, ("93.184.216.34", 443)),
            (None, None, None, None, ("10.0.0.1", 443)),
        ]
        self.assertFalse(is_public_http_url("https://example.test/feed", resolver=lambda *_args, **_kwargs: private))
        self.assertFalse(is_public_http_url("https://example.test/feed", resolver=lambda *_args, **_kwargs: mixed))

    def test_calendar_fetch_rejects_private_host_before_request(self):
        with patch.object(feed_fetcher.http_requests, "get") as get:
            with self.assertRaisesRegex(ValueError, "public"):
                feed_fetcher.fetch_and_parse_ical("https://127.0.0.1/private.ics")
        get.assert_not_called()

    def test_calendar_fetch_rejects_redirect_to_private_host(self):
        redirect = Mock(status_code=302, headers={"Location": "https://127.0.0.1/private.ics"})
        redirect.close = Mock()
        public_resolution = [(None, None, None, None, ("93.184.216.34", 443))]
        private_resolution = [(None, None, None, None, ("127.0.0.1", 443))]
        def resolve(host, *_args, **_kwargs):
            return private_resolution if host == "127.0.0.1" else public_resolution

        with patch("services.outbound_http.socket.getaddrinfo", side_effect=resolve), \
                patch.object(feed_fetcher.http_requests, "get", return_value=redirect) as get:
            with self.assertRaisesRegex(ValueError, "public"):
                feed_fetcher.fetch_and_parse_ical("https://example.test/feed.ics")
        self.assertEqual(get.call_count, 1)
        redirect.close.assert_called_once()

    def test_log_url_redaction_removes_credentials_query_and_fragment(self):
        rendered = redacted_url("https://user:password@example.test/feed.ics?token=sensitive#fragment")
        self.assertEqual(rendered, "https://example.test/feed.ics?[redacted]")

    def test_avatar_copy_rejects_private_host_before_request(self):
        with patch.object(avatar_storage.http_requests, "get") as get:
            self.assertIsNone(avatar_storage.store_avatar_from_url("user-1", "https://127.0.0.1/avatar.png"))
        get.assert_not_called()

    def test_calendar_response_size_is_bounded(self):
        response = Mock(headers={}, content=b"x" * (feed_fetcher.MAX_ICAL_BYTES + 1))
        with self.assertRaisesRegex(ValueError, "10 MB"):
            feed_fetcher._read_response_bytes(response)

    def test_calendar_request_errors_do_not_log_or_raise_feed_secrets(self):
        secret_url = "https://example.test/feed.ics?token=do-not-log"
        with patch.object(feed_fetcher, "require_public_http_url", side_effect=lambda url: url), \
                patch.object(feed_fetcher.http_requests, "get", side_effect=requests.ConnectionError(secret_url)), \
                self.assertLogs("services.feed_fetcher", level="ERROR") as logs:
            with self.assertRaisesRegex(ValueError, "request failed") as raised:
                feed_fetcher.fetch_and_parse_ical(secret_url)
        combined = "\n".join(logs.output) + str(raised.exception)
        self.assertNotIn("do-not-log", combined)
        self.assertIn("?[redacted]", combined)


class ApplicationSecurityIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.env = patch.dict(os.environ, {
            "DATABASE_PATH": os.path.join(self.temp_dir.name, "security.sqlite3"),
            "FLASK_SECRET_KEY": "test-security-key",
            "FLASK_ENV": "testing",
            "APSTUDY_ALLOW_INSECURE_HTTP": "1",
            "SCHEDULER_ENABLED": "0",
        }, clear=False)
        self.env.start()
        self.addCleanup(self.env.stop)
        schema_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "migrations", "001_initial_schema.sql")
        with sqlite3.connect(os.environ["DATABASE_PATH"]) as connection, open(schema_path, encoding="utf-8") as schema:
            connection.executescript(schema.read())
        with patch("services.scheduler.init_scheduler"), patch("services.discord_audit.init_discord_audit"):
            self.app = app_module.create_app()
        self.app.config.update(TESTING=True)
        self.user = SimpleNamespace(
            id="security-user",
            is_authenticated=True,
            username="security-user",
            name="Security User",
            discord_id=None,
            discord_username=None,
            discord_linked_at=None,
        )
        self.previous_loader = login_manager._user_callback
        login_manager._user_callback = lambda _user_id: self.user

    def tearDown(self):
        login_manager._user_callback = self.previous_loader

    def _authenticated_client(self):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = self.user.id
            session["_fresh"] = True
        return client

    def _csrf_token(self, client):
        client.get("/login")
        cookie = client.get_cookie("csrf_token")
        self.assertIsNotNone(cookie)
        return cookie.value

    def test_authenticated_mutation_requires_and_accepts_csrf_token(self):
        client = self._authenticated_client()
        with patch("blueprints.settings.update_row_safe", return_value={}):
            rejected = client.post("/settings/api/discord/unlink")
            token = self._csrf_token(client)
            accepted = client.post(
                "/settings/api/discord/unlink",
                headers={"X-CSRFToken": token},
            )
        self.assertEqual(rejected.status_code, 400)
        self.assertEqual(accepted.status_code, 200)

    def test_csrf_refresh_endpoint_marks_rejections_and_issues_a_usable_token(self):
        client = self._authenticated_client()
        with patch("blueprints.settings.update_row_safe", return_value={}):
            rejected = client.post("/settings/api/discord/unlink", headers={"X-CSRFToken": "stale"})
            refreshed = client.get("/auth/csrf")
            token = client.get_cookie("csrf_token")
            accepted = client.post(
                "/settings/api/discord/unlink",
                headers={"X-CSRFToken": token.value},
            )

        self.assertEqual(rejected.status_code, 400)
        self.assertEqual(rejected.headers.get("X-APStudy-CSRF-Error"), "1")
        self.assertEqual(refreshed.status_code, 200)
        self.assertEqual(refreshed.headers.get("Cache-Control"), "no-store")
        self.assertIsNotNone(token)
        self.assertEqual(accepted.status_code, 200)

    def test_notes_save_succeeds_after_csrf_refresh_without_replaying_rejection(self):
        client = self._authenticated_client()
        note = {
            "$id": "note-1",
            "user_id": self.user.id,
            "title": "Old title",
            "content": "[]",
            "collaboration_enabled": False,
            "created_at": "2026-07-01T00:00:00Z",
            "updated_at": "2026-07-01T00:00:00Z",
        }
        updated_note = {**note, "content": '[{"type":"paragraph"}]', "updated_at": "2026-07-09T00:00:00Z"}
        access = {"can_edit": True, "role": "owner"}

        with patch.object(notes_api.note_store, "get_note", return_value=note), \
                patch.object(notes_api.note_store, "resolve_note_access", return_value=access), \
                patch.object(notes_api.note_store, "update_note", return_value=updated_note) as update_note, \
                patch.object(notes_api.note_store, "get_safe_user", return_value=None), \
                patch.object(notes_api, "_load_global_notes_page_setup", return_value={}), \
                patch.object(notes_api.note_media, "sync_note_media"):
            rejected = client.patch(
                "/api/notes/note-1",
                json={"content": updated_note["content"]},
                headers={"X-CSRFToken": "stale"},
            )
            client.get("/auth/csrf")
            token = client.get_cookie("csrf_token")
            accepted = client.patch(
                "/api/notes/note-1",
                json={"content": updated_note["content"]},
                headers={"X-CSRFToken": token.value},
            )

        self.assertEqual(rejected.status_code, 400)
        self.assertEqual(rejected.headers.get("X-APStudy-CSRF-Error"), "1")
        self.assertEqual(accepted.status_code, 200)
        update_note.assert_called_once()
        self.assertEqual(update_note.call_args.args[0], "note-1")
        self.assertEqual(update_note.call_args.args[1]["content"], updated_note["content"])

    def test_logout_get_is_safe_redirect_and_post_remains_csrf_protected(self):
        client = self._authenticated_client()
        response = client.get("/logout")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/login")
        with client.session_transaction() as client_session:
            self.assertEqual(client_session["_user_id"], self.user.id)
        self.assertEqual(client.post("/logout").status_code, 400)

    def test_unauthenticated_api_requests_return_json_without_login_redirect(self):
        client = self.app.test_client()

        response = client.post(
            "/api/presence/heartbeat",
            json={"scope_type": "site", "scope_id": "global", "tab_id": "tab-1"},
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json(), {"error": "Authentication required."})
        self.assertEqual(response.headers.get("Cache-Control"), "no-store")
        with client.session_transaction() as client_session:
            self.assertNotIn("login_next_url", client_session)

    def test_logout_clears_persistent_remember_cookie(self):
        client = self._authenticated_client()
        client.set_cookie("remember_token", "remembered-user")
        token = self._csrf_token(client)

        with patch("blueprints.auth.emit_user_event"), \
                patch("blueprints.auth.Users") as users_class:
            response = client.post("/logout", headers={"X-CSRFToken": token})

        self.assertEqual(response.status_code, 302)
        users_class.return_value.delete_sessions.assert_called_once_with(self.user.id)
        set_cookie_headers = response.headers.getlist("Set-Cookie")
        remember_headers = [
            header for header in set_cookie_headers
            if header.startswith("remember_token=")
        ]
        self.assertTrue(remember_headers)
        self.assertIn("Expires=Thu, 01 Jan 1970 00:00:00 GMT", remember_headers[-1])
        self.assertIn("Max-Age=0", remember_headers[-1])
        with client.session_transaction() as client_session:
            self.assertNotIn("_user_id", client_session)
            self.assertNotIn("user_id", client_session)


class SecretAndShareTokenTests(unittest.TestCase):
    def test_production_requires_explicit_flask_secret(self):
        with patch.dict(os.environ, {"FLASK_ENV": "production"}, clear=False):
            os.environ.pop("FLASK_SECRET_KEY", None)
            with self.assertRaisesRegex(RuntimeError, "FLASK_SECRET_KEY"):
                app_module._session_secret_key()

    def test_nonproduction_missing_secret_is_unpredictable(self):
        with patch.dict(os.environ, {"FLASK_ENV": "development"}, clear=False):
            os.environ.pop("FLASK_SECRET_KEY", None)
            first = app_module._session_secret_key()
            second = app_module._session_secret_key()
        self.assertNotEqual(first, "dev-fallback-key")
        self.assertNotEqual(first, second)

    def test_new_file_share_codes_have_at_least_100_bits_of_entropy(self):
        with patch.object(file_share, "first_row", return_value=None):
            code = file_share._generate_share_code()
        self.assertGreaterEqual(len(code), 20)
        self.assertTrue(set(code) <= set(file_share.SHARE_CODE_CHARS))


if __name__ == "__main__":
    unittest.main()
