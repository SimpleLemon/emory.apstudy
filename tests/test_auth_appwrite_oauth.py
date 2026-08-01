import os
import html
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from flask import Flask, session
from appwrite.exception import AppwriteException
from werkzeug.exceptions import NotFound

import blueprints.auth as auth
from app import create_app
from avatar_images import avatar_url_for_size
from extensions import login_manager
from models import User
from tests.support.harness import reset_flask_login_manager


class AppwriteOauthRouteTestCase(unittest.TestCase):
    def setUp(self):
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        self.app = Flask(
            __name__,
            template_folder=os.path.join(project_root, "templates"),
            static_folder=os.path.join(project_root, "static"),
        )
        self.app.secret_key = "test"
        login_manager.init_app(self.app)
        self.app.register_blueprint(auth.auth_bp)

    def tearDown(self):
        reset_flask_login_manager()

    def assert_login_error_is_rendered_and_consumed(self, error_code):
        with self.app.test_client() as client:
            with client.session_transaction() as client_session:
                client_session[auth.AUTH_ERROR_SESSION_KEY] = error_code

            response = client.get("/login")
            body = html.unescape(response.get_data(as_text=True))

            self.assertEqual(response.status_code, 200)
            self.assertIn(auth.AUTH_ERROR_MESSAGE, body)
            self.assertIn(f"Error code: {error_code}", body)
            with client.session_transaction() as client_session:
                self.assertNotIn(auth.AUTH_ERROR_SESSION_KEY, client_session)

    def complete_new_user_login(self, remote_user, provider, provider_profile=None):
        created_rows = []

        def create_row(_collection, row_id=None, data=None, **_kwargs):
            row = {"$id": row_id, **(data or {})}
            created_rows.append(row)
            return row

        with self.app.test_request_context("/auth/session", method="POST"):
            with patch.object(auth, "get_row_safe", return_value=None), \
                    patch.object(auth, "_find_user_by_email", return_value=None), \
                    patch.object(auth, "_fetch_provider_profile", return_value=provider_profile or {}), \
                    patch.object(auth, "store_avatar_from_url", return_value=None), \
                    patch.object(auth, "create_row_safe", side_effect=create_row), \
                    patch.object(auth, "sync_chat_presence_labels_for_user"), \
                    patch.object(auth, "login_user"), \
                    patch.object(auth, "url_for", side_effect=lambda endpoint, **_kwargs: f"/{endpoint}"), \
                    patch.object(auth, "emit_user_event"):
                result = auth._complete_appwrite_login(
                    remote_user,
                    provider=provider,
                    provider_access_token="provider-token",
                )

        return result, created_rows

    def test_login_renders_and_consumes_session_error_code(self):
        self.assert_login_error_is_rendered_and_consumed(auth.AUTH_ERROR_OAUTH_CALLBACK)

    def test_login_next_rejects_api_endpoints(self):
        self.assertTrue(auth._is_safe_login_next_url("/dashboard"))
        self.assertFalse(auth._is_safe_login_next_url("/api/presence/heartbeat"))
        self.assertFalse(auth._is_safe_login_next_url("/settings/api/profile"))

    def test_redirect_after_login_falls_back_from_api_next_destination(self):
        with self.app.test_request_context("/auth/session", method="POST"):
            session[auth.LOGIN_NEXT_SESSION_KEY] = "/api/presence/heartbeat"
            with patch.object(auth, "url_for", return_value="/dashboard"):
                redirect_target = auth._redirect_after_login({"onboarding_complete": True})

        self.assertEqual(redirect_target, "/dashboard")

    def test_complete_appwrite_login_persists_browser_session(self):
        def create_row(_collection, row_id=None, data=None, **_kwargs):
            return {"$id": row_id, **(data or {})}

        with self.app.test_request_context("/auth/session", method="POST"):
            with patch.object(auth, "get_row_safe", return_value=None), \
                    patch.object(auth, "_find_user_by_email", return_value=None), \
                    patch.object(auth, "_fetch_provider_profile", return_value={}), \
                    patch.object(auth, "store_avatar_from_url", return_value=None), \
                    patch.object(auth, "create_row_safe", side_effect=create_row), \
                    patch.object(auth, "sync_chat_presence_labels_for_user"), \
                    patch.object(auth, "login_user") as login_user, \
                    patch.object(auth, "url_for", side_effect=lambda endpoint, **_kwargs: f"/{endpoint}"), \
                    patch.object(auth, "emit_user_event"):
                auth._complete_appwrite_login(
                    {"$id": "user-1", "email": "student@example.com", "name": "Student"},
                    provider="google",
                    provider_access_token="provider-token",
                )
                session_permanent = session.permanent

        self.assertTrue(session_permanent)
        self.assertEqual(login_user.call_count, 1)
        self.assertTrue(login_user.call_args.kwargs["remember"])
        self.assertEqual(login_user.call_args.kwargs["duration"], auth.AUTH_SESSION_DURATION)

    def test_valid_provider_initiates_appwrite_oauth_token_flow(self):
        calls = []

        def create_o_auth2_token(**kwargs):
            calls.append(kwargs)
            return "https://appwrite.example/oauth"

        fake_account = SimpleNamespace(create_o_auth2_token=create_o_auth2_token)
        with self.app.test_request_context("/auth/appwrite/google"):
            with patch.object(auth, "Account", return_value=fake_account):
                response = auth.appwrite_oauth_start("google")

            self.assertEqual(response.status_code, 302)
            self.assertEqual(response.headers["Location"], "https://appwrite.example/oauth")
            self.assertEqual(session[auth.APPWRITE_OAUTH_PROVIDER_KEY], "google")
            self.assertIn(auth.APPWRITE_OAUTH_STATE_KEY, session)

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["provider"], auth.OAuthProvider.GOOGLE)
        self.assertEqual(calls[0]["scopes"], auth.OAUTH_PROVIDER_SCOPES["google"])
        self.assertIn("/auth/appwrite/callback/", calls[0]["success"])
        self.assertIn("/auth/appwrite/failure/", calls[0]["failure"])
        self.assertNotIn("auth_error", calls[0]["failure"])

    def test_oauth_start_logs_missing_sessions_scope_without_secret(self):
        error = AppwriteException(
            "<html>missing scopes ([\"sessions.write\"]) secret=super-secret-token</html>",
            401,
            "general_unauthorized_scope",
        )
        fake_account = SimpleNamespace(create_o_auth2_token=lambda **_kwargs: (_ for _ in ()).throw(error))

        with self.app.test_request_context("/auth/appwrite/google"):
            with patch.object(auth, "Account", return_value=fake_account), \
                    patch.object(auth, "emit_server_log_event") as emit_server_log, \
                    self.assertLogs("blueprints.auth", level="ERROR") as logs:
                response = auth.appwrite_oauth_start("google")
                state_present = auth.APPWRITE_OAUTH_STATE_KEY in session
                provider_present = auth.APPWRITE_OAUTH_PROVIDER_KEY in session
                error_code = session.get(auth.AUTH_ERROR_SESSION_KEY)

        output = "\n".join(logs.output)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/login")
        self.assertEqual(error_code, auth.AUTH_ERROR_OAUTH_START_SCOPE)
        self.assertFalse(state_present)
        self.assertFalse(provider_present)
        self.assertIn("general_unauthorized_scope", output)
        self.assertIn("sessions.write", output)
        self.assertNotIn("super-secret-token", output)
        emit_server_log.assert_called_once()
        self.assertEqual(emit_server_log.call_args.args[0], "OAuth Login Error: Missing Appwrite Scope")
        self.assertEqual(emit_server_log.call_args.kwargs["metadata"]["error_code"], auth.AUTH_ERROR_OAUTH_START_SCOPE)
        self.assertIn("sessions.write", str(emit_server_log.call_args.kwargs["metadata"]["appwrite_error"]))
        self.assert_login_error_is_rendered_and_consumed(auth.AUTH_ERROR_OAUTH_START_SCOPE)

    def test_oauth_start_uses_generic_code_for_other_start_failures(self):
        fake_account = SimpleNamespace(
            create_o_auth2_token=lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("network down"))
        )

        with self.app.test_request_context("/auth/appwrite/google"):
            with patch.object(auth, "Account", return_value=fake_account), \
                    patch.object(auth, "emit_server_log_event") as emit_server_log, \
                    self.assertLogs("blueprints.auth", level="ERROR"):
                response = auth.appwrite_oauth_start("google")
                error_code = session.get(auth.AUTH_ERROR_SESSION_KEY)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/login")
        self.assertEqual(error_code, auth.AUTH_ERROR_OAUTH_START)
        emit_server_log.assert_called_once()

    def test_appwrite_oauth_preflight_reports_missing_sessions_scope(self):
        error = AppwriteException(
            "app role missing scopes ([\"sessions.write\"])",
            401,
            "general_unauthorized_scope",
        )
        fake_account = SimpleNamespace(create_o_auth2_token=lambda **_kwargs: (_ for _ in ()).throw(error))

        with patch.object(auth, "Account", return_value=fake_account), \
                self.assertLogs("blueprints.auth", level="ERROR"):
            result = self.app.test_cli_runner().invoke(
                args=[
                    "auth",
                    "appwrite-oauth-preflight",
                    "--provider",
                    "google",
                    "--base-url",
                    "https://nest.apstudy.org",
                ],
            )

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("Appwrite OAuth preflight failed.", result.output)
        self.assertIn("general_unauthorized_scope", result.output)
        self.assertIn("required_scope_hint: sessions.write", result.output)

    def test_app_factory_oauth_urls_use_forwarded_https(self):
        calls = []

        def create_o_auth2_token(**kwargs):
            calls.append(kwargs)
            return "https://appwrite.example/oauth"

        fake_account = SimpleNamespace(create_o_auth2_token=create_o_auth2_token)
        with patch("services.scheduler.init_scheduler"), \
                patch("services.discord_audit.init_discord_audit"), \
                patch.object(auth, "Account", return_value=fake_account):
            app = create_app()
            app.config["SERVER_NAME"] = "nest.apstudy.org"
            app.config["TESTING"] = True
            response = app.test_client().get(
                "/auth/appwrite/google",
                headers={
                    "Host": "nest.apstudy.org",
                    "X-Forwarded-Proto": "https",
                    "X-Forwarded-Host": "nest.apstudy.org",
                },
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "https://appwrite.example/oauth")
        self.assertEqual(len(calls), 1)
        self.assertRegex(calls[0]["success"], r"^https://nest\.apstudy\.org/auth/appwrite/callback/")
        self.assertRegex(calls[0]["failure"], r"^https://nest\.apstudy\.org/auth/appwrite/failure/")
        self.assertNotIn("auth_error", calls[0]["failure"])

    def test_app_factory_oauth_urls_allow_local_insecure_http(self):
        calls = []

        def create_o_auth2_token(**kwargs):
            calls.append(kwargs)
            return "https://appwrite.example/oauth"

        fake_account = SimpleNamespace(create_o_auth2_token=create_o_auth2_token)
        with patch.dict(os.environ, {
            "APSTUDY_ALLOW_INSECURE_HTTP": "1",
            "FLASK_DEBUG": "0",
        }, clear=False), \
                patch("services.scheduler.init_scheduler"), \
                patch("services.discord_audit.init_discord_audit"), \
                patch.object(auth, "Account", return_value=fake_account):
            app = create_app()
            app.config["TESTING"] = True
            response = app.test_client().get(
                "/auth/appwrite/google",
                base_url="http://localhost:8000",
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "https://appwrite.example/oauth")
        self.assertEqual(len(calls), 1)
        self.assertRegex(calls[0]["success"], r"^http://localhost:8000/auth/appwrite/callback/")
        self.assertRegex(calls[0]["failure"], r"^http://localhost:8000/auth/appwrite/failure/")
        self.assertNotIn("auth_error", calls[0]["failure"])

    def test_invalid_provider_is_rejected(self):
        with self.app.test_request_context("/auth/appwrite/not-real"):
            with self.assertRaises(NotFound):
                auth.appwrite_oauth_start("not-real")

    def test_callback_rejects_mismatched_state(self):
        with self.app.test_request_context("/auth/appwrite/callback/bad?userId=user-1&secret=secret"):
            session[auth.APPWRITE_OAUTH_STATE_KEY] = "good"
            session[auth.APPWRITE_OAUTH_PROVIDER_KEY] = "google"
            with patch.object(auth, "Account") as account_class, \
                    patch.object(auth, "emit_server_log_event") as emit_server_log:
                response = auth.appwrite_oauth_callback("bad")
                error_code = session.get(auth.AUTH_ERROR_SESSION_KEY)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/login")
        self.assertEqual(error_code, auth.AUTH_ERROR_OAUTH_STATE)
        account_class.assert_not_called()
        emit_server_log.assert_called_once()

    def test_callback_rejects_missing_credentials(self):
        with self.app.test_request_context("/auth/appwrite/callback/state?userId=user-1"):
            session[auth.APPWRITE_OAUTH_STATE_KEY] = "state"
            session[auth.APPWRITE_OAUTH_PROVIDER_KEY] = "google"
            with patch.object(auth, "emit_server_log_event") as emit_server_log:
                response = auth.appwrite_oauth_callback("state")
                error_code = session.get(auth.AUTH_ERROR_SESSION_KEY)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/login")
        self.assertEqual(error_code, auth.AUTH_ERROR_OAUTH_CREDENTIALS)
        emit_server_log.assert_called_once()

    def test_callback_completion_failure_uses_callback_error_code(self):
        fake_account = SimpleNamespace(
            create_session=lambda user_id, secret: {
                "provider": "google",
                "providerAccessToken": "provider-token",
                "userId": user_id,
            },
        )
        with self.app.test_request_context("/auth/appwrite/callback/state?userId=user-1&secret=secret"):
            session[auth.APPWRITE_OAUTH_STATE_KEY] = "state"
            session[auth.APPWRITE_OAUTH_PROVIDER_KEY] = "google"
            with patch.object(auth, "Account", return_value=fake_account), \
                    patch.object(auth, "_account_from_user_id", return_value={"$id": "user-1"}), \
                    patch.object(auth, "_complete_appwrite_login", side_effect=RuntimeError("profile create failed")), \
                    patch.object(auth, "emit_server_log_event") as emit_server_log, \
                    self.assertLogs("blueprints.auth", level="ERROR"):
                response = auth.appwrite_oauth_callback("state")
                error_code = session.get(auth.AUTH_ERROR_SESSION_KEY)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/login")
        self.assertEqual(error_code, auth.AUTH_ERROR_OAUTH_CALLBACK)
        emit_server_log.assert_called_once()

    def test_provider_failure_route_sets_provider_error_and_clears_state(self):
        with self.app.test_request_context("/auth/appwrite/failure/state"):
            session[auth.APPWRITE_OAUTH_STATE_KEY] = "state"
            session[auth.APPWRITE_OAUTH_PROVIDER_KEY] = "discord"
            with patch.object(auth, "emit_server_log_event") as emit_server_log, \
                    self.assertLogs("blueprints.auth", level="WARNING") as logs:
                response = auth.appwrite_oauth_failure("state")
                error_code = session.get(auth.AUTH_ERROR_SESSION_KEY)
                state_present = auth.APPWRITE_OAUTH_STATE_KEY in session
                provider_present = auth.APPWRITE_OAUTH_PROVIDER_KEY in session

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/login")
        self.assertEqual(error_code, auth.AUTH_ERROR_OAUTH_PROVIDER)
        self.assertFalse(state_present)
        self.assertFalse(provider_present)
        self.assertIn("provider=discord", "\n".join(logs.output))
        emit_server_log.assert_called_once()

    def test_provider_failure_route_rejects_invalid_state(self):
        with self.app.test_request_context("/auth/appwrite/failure/bad"):
            session[auth.APPWRITE_OAUTH_STATE_KEY] = "good"
            session[auth.APPWRITE_OAUTH_PROVIDER_KEY] = "github"
            with patch.object(auth, "emit_server_log_event") as emit_server_log, \
                    self.assertLogs("blueprints.auth", level="WARNING"):
                response = auth.appwrite_oauth_failure("bad")
                error_code = session.get(auth.AUTH_ERROR_SESSION_KEY)
                state_present = auth.APPWRITE_OAUTH_STATE_KEY in session
                provider_present = auth.APPWRITE_OAUTH_PROVIDER_KEY in session

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/login")
        self.assertEqual(error_code, auth.AUTH_ERROR_OAUTH_STATE)
        self.assertFalse(state_present)
        self.assertFalse(provider_present)
        emit_server_log.assert_called_once()

    def test_valid_callback_creates_session_and_completes_login(self):
        fake_account = SimpleNamespace(
            create_session=lambda user_id, secret: {
                "provider": "google",
                "providerAccessToken": "provider-token",
                "userId": user_id,
            },
        )
        remote_user = {"$id": "user-1", "email": "student@example.com", "name": "Student"}

        with self.app.test_request_context("/auth/appwrite/callback/state?userId=user-1&secret=secret"):
            session[auth.APPWRITE_OAUTH_STATE_KEY] = "state"
            session[auth.APPWRITE_OAUTH_PROVIDER_KEY] = "google"
            with patch.object(auth, "Account", return_value=fake_account), \
                    patch.object(auth, "_account_from_user_id", return_value=remote_user) as account_from_user_id, \
                    patch.object(auth, "_complete_appwrite_login", return_value={"redirect": "/dashboard", "user_id": "user-1"}) as complete_login:
                response = auth.appwrite_oauth_callback("state")
                state_present = auth.APPWRITE_OAUTH_STATE_KEY in session
                provider_present = auth.APPWRITE_OAUTH_PROVIDER_KEY in session

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/dashboard")
        self.assertFalse(state_present)
        self.assertFalse(provider_present)
        account_from_user_id.assert_called_once_with("user-1")
        complete_login.assert_called_once_with(
            remote_user,
            provider="google",
            provider_access_token="provider-token",
            provider_uid=None,
            page_context="auth/appwrite/callback",
        )

    def test_valid_callback_accepts_session_field_aliases(self):
        fake_account = SimpleNamespace(
            create_session=lambda user_id, secret: {
                "provider": "google",
                "provider_access_token": "provider-token",
                "provider_uid": "google-user-1",
                "userId": user_id,
            },
        )
        remote_user = {"$id": "user-1", "email": "student@example.com", "name": "Student"}

        with self.app.test_request_context("/auth/appwrite/callback/state?userId=user-1&secret=secret"):
            session[auth.APPWRITE_OAUTH_STATE_KEY] = "state"
            session[auth.APPWRITE_OAUTH_PROVIDER_KEY] = "google"
            with patch.object(auth, "Account", return_value=fake_account), \
                    patch.object(auth, "_account_from_user_id", return_value=remote_user), \
                    patch.object(auth, "_complete_appwrite_login", return_value={"redirect": "/dashboard", "user_id": "user-1"}) as complete_login:
                response = auth.appwrite_oauth_callback("state")

        self.assertEqual(response.status_code, 302)
        complete_login.assert_called_once_with(
            remote_user,
            provider="google",
            provider_access_token="provider-token",
            provider_uid="google-user-1",
            page_context="auth/appwrite/callback",
        )

    def test_session_field_reads_camel_and_snake_case_aliases(self):
        payload = {
            "providerAccessToken": "camel-token",
            "provider_uid": "uid-1",
        }
        self.assertEqual(
            auth._session_field(payload, "providerAccessToken", "provider_access_token"),
            "camel-token",
        )
        self.assertEqual(
            auth._session_field(payload, "providerUid", "provider_uid"),
            "uid-1",
        )

    def test_backfill_avatars_dry_run_reports_candidates(self):
        candidates = [
            {
                "$id": "user-1",
                "email": "student@example.com",
                "provider": "google",
                "picture_url": "",
                "avatar_source": None,
            },
        ]

        with patch("appwrite_helpers.list_rows_all", return_value={"rows": candidates}), \
                patch.object(auth, "_account_from_user_id", return_value={
                    "prefs": {"picture_url": "https://lh3.googleusercontent.com/remote=s96"},
                }), \
                patch.object(auth, "_provider_access_token_from_identities", return_value={}), \
                patch.object(auth, "update_row_safe") as update_row:
            result = self.app.test_cli_runner().invoke(
                args=["auth", "backfill-avatars", "--dry-run", "--limit", "10"],
            )

        self.assertEqual(result.exit_code, 0)
        self.assertIn("would_update", result.output)
        self.assertIn("user-1", result.output)
        update_row.assert_not_called()

    def test_resolve_discord_link_identity_uses_appwrite_identities(self):
        with patch.object(
            auth,
            "_identities_for_appwrite_user",
            return_value=[{
                "provider": "discord",
                "providerUid": "123456789012345678",
            }],
        ), patch.object(auth, "_fetch_provider_profile", return_value={}):
            identity = auth._resolve_discord_link_identity(
                appwrite_user_ids=["callback-user"],
            )

        self.assertEqual(identity["id"], "123456789012345678")
        self.assertTrue(identity["has_appwrite_identity"])
        self.assertFalse(identity["has_provider_uid"])
        self.assertFalse(identity["has_access_token"])

    def test_complete_appwrite_login_stores_provider_avatar_for_new_user(self):
        created_rows = []

        def create_row(_collection, row_id=None, data=None, **_kwargs):
            row = {"$id": row_id, **(data or {})}
            created_rows.append(row)
            return row

        bucket_view_url = "https://nyc.cloud.appwrite.io/v1/storage/buckets/profile_avatars/files/file-1/view?project=test"
        with self.app.test_request_context("/auth/session", method="POST"):
            with patch.object(auth, "get_row_safe", return_value=None), \
                    patch.object(auth, "_find_user_by_email", return_value=None), \
                    patch.object(auth, "_fetch_provider_profile", return_value={
                        "name": "Student Name",
                        "avatar_url": "https://lh3.googleusercontent.com/avatar=s96",
                    }), \
                    patch.object(auth, "store_avatar_from_url", return_value={
                        "file_id": "file-1",
                        "view_url": bucket_view_url,
                    }) as store_avatar, \
                    patch.object(auth, "create_row_safe", side_effect=create_row), \
                    patch.object(auth, "sync_chat_presence_labels_for_user"), \
                    patch.object(auth, "login_user"), \
                    patch.object(auth, "url_for", side_effect=lambda endpoint, **_kwargs: f"/{endpoint}"), \
                    patch.object(auth, "emit_user_event"):
                result = auth._complete_appwrite_login(
                    {"$id": "user-1", "email": "student@example.com", "name": "Remote"},
                    provider="google",
                    provider_access_token="provider-token",
                )

        self.assertEqual(result["user_id"], "user-1")
        store_avatar.assert_called_once_with("user-1", "https://lh3.googleusercontent.com/avatar=s96")
        self.assertEqual(created_rows[0]["picture_url"], bucket_view_url)
        self.assertEqual(created_rows[0]["avatar_file_id"], "file-1")
        self.assertEqual(created_rows[0]["avatar_source"], "provider")

    def test_complete_appwrite_login_falls_back_to_provider_url_when_storage_fails(self):
        created_rows = []

        def create_row(_collection, row_id=None, data=None, **_kwargs):
            row = {"$id": row_id, **(data or {})}
            created_rows.append(row)
            return row

        with self.app.test_request_context("/auth/session", method="POST"):
            with patch.object(auth, "get_row_safe", return_value=None), \
                    patch.object(auth, "_find_user_by_email", return_value=None), \
                    patch.object(auth, "_fetch_provider_profile", return_value={
                        "name": "Student Name",
                        "avatar_url": "https://lh3.googleusercontent.com/avatar=s96",
                    }), \
                    patch.object(auth, "store_avatar_from_url", return_value=None), \
                    patch.object(auth, "create_row_safe", side_effect=create_row), \
                    patch.object(auth, "sync_chat_presence_labels_for_user"), \
                    patch.object(auth, "login_user"), \
                    patch.object(auth, "url_for", side_effect=lambda endpoint, **_kwargs: f"/{endpoint}"), \
                    patch.object(auth, "emit_user_event"):
                auth._complete_appwrite_login(
                    {"$id": "user-1", "email": "student@example.com", "name": "Remote"},
                    provider="google",
                    provider_access_token="provider-token",
                )

        self.assertEqual(created_rows[0]["picture_url"], "https://lh3.googleusercontent.com/avatar=s96")
        self.assertIsNone(created_rows[0]["avatar_file_id"])
        self.assertEqual(created_rows[0]["avatar_source"], "provider")

    def test_complete_appwrite_login_uses_remote_user_avatar_when_provider_profile_is_empty(self):
        result, created_rows = self.complete_new_user_login(
            {
                "$id": "user-1",
                "email": "student@example.com",
                "name": "Student",
                "prefs": {
                    "picture_url": "https://lh3.googleusercontent.com/remote-avatar=s96-c",
                },
            },
            provider="google",
        )

        self.assertEqual(result["user_id"], "user-1")
        self.assertEqual(created_rows[0]["picture_url"], "https://lh3.googleusercontent.com/remote-avatar=s96-c")
        self.assertEqual(created_rows[0]["avatar_source"], "provider")

    def test_complete_appwrite_login_accepts_github_avatar_url_from_remote_user(self):
        _result, created_rows = self.complete_new_user_login(
            {
                "$id": "user-1",
                "email": "student@example.com",
                "name": "Student",
                "avatar_url": "https://avatars.githubusercontent.com/u/12345?v=4",
            },
            provider="github",
        )

        self.assertEqual(created_rows[0]["picture_url"], "https://avatars.githubusercontent.com/u/12345?v=4")
        self.assertEqual(created_rows[0]["avatar_source"], "provider")

    def test_complete_appwrite_login_builds_discord_cdn_avatar_from_remote_user(self):
        _result, created_rows = self.complete_new_user_login(
            {
                "$id": "user-1",
                "email": "student@example.com",
                "name": "Student",
                "avatar": "a_discordhash",
            },
            provider="discord",
        )

        self.assertEqual(
            created_rows[0]["picture_url"],
            "https://cdn.discordapp.com/avatars/user-1/a_discordhash.gif?size=256",
        )
        self.assertEqual(created_rows[0]["avatar_source"], "provider")

    def test_complete_appwrite_login_updates_provider_avatar_when_replaceable(self):
        existing = {
            "$id": "user-1",
            "email": "student@example.com",
            "name": "Student",
            "picture_url": "old-provider-avatar",
            "avatar_source": "provider",
            "avatar_file_id": "old-file",
            "onboarding_complete": True,
        }
        bucket_view_url = "https://nyc.cloud.appwrite.io/v1/storage/buckets/profile_avatars/files/file-2/view?project=test"
        with self.app.test_request_context("/auth/session", method="POST"):
            with patch.object(auth, "get_row_safe", return_value=existing), \
                    patch.object(auth, "_fetch_provider_profile", return_value={
                        "name": "Student",
                        "avatar_url": "https://lh3.googleusercontent.com/new=s96",
                    }), \
                    patch.object(auth, "store_avatar_from_url", return_value={
                        "file_id": "file-2",
                        "view_url": bucket_view_url,
                    }), \
                    patch.object(auth, "delete_avatar_file") as delete_avatar, \
                    patch.object(auth, "update_row_safe", return_value={**existing, "picture_url": bucket_view_url}) as update_row, \
                    patch.object(auth, "sync_chat_presence_labels_for_user"), \
                    patch.object(auth, "login_user"), \
                    patch.object(auth, "url_for", side_effect=lambda endpoint, **_kwargs: f"/{endpoint}"), \
                    patch.object(auth, "emit_user_event"):
                auth._complete_appwrite_login(
                    {"$id": "user-1", "email": "student@example.com", "name": "Student"},
                    provider="google",
                    provider_access_token="provider-token",
                )

        updates = update_row.call_args.args[2]
        self.assertEqual(updates["picture_url"], bucket_view_url)
        self.assertEqual(updates["avatar_file_id"], "file-2")
        self.assertEqual(updates["avatar_source"], "provider")
        delete_avatar.assert_called_once_with("old-file")

    def test_complete_appwrite_login_preserves_uploaded_avatar(self):
        existing = {
            "$id": "user-1",
            "email": "student@example.com",
            "name": "Student",
            "picture_url": "uploaded-avatar",
            "avatar_source": "upload",
            "onboarding_complete": True,
        }
        with self.app.test_request_context("/auth/session", method="POST"):
            with patch.object(auth, "get_row_safe", return_value=existing), \
                    patch.object(auth, "_fetch_provider_profile", return_value={
                        "name": "Student",
                        "avatar_url": "provider-avatar",
                    }), \
                    patch.object(auth, "update_row_safe", return_value=existing) as update_row, \
                    patch.object(auth, "sync_chat_presence_labels_for_user"), \
                    patch.object(auth, "login_user"), \
                    patch.object(auth, "url_for", side_effect=lambda endpoint, **_kwargs: f"/{endpoint}"), \
                    patch.object(auth, "emit_user_event"):
                auth._complete_appwrite_login(
                    {"$id": "user-1", "email": "student@example.com", "name": "Student"},
                    provider="google",
                    provider_access_token="provider-token",
                )

        updates = update_row.call_args.args[2]
        self.assertNotIn("picture_url", updates)
        self.assertNotIn("avatar_source", updates)

    def test_user_picture_alias_uses_picture_url(self):
        user = User({"$id": "user-1", "picture_url": "https://example.test/avatar.png"})

        self.assertEqual(user.picture, "https://example.test/avatar.png")

    def test_google_avatar_url_for_size_preserves_crop_suffix(self):
        self.assertEqual(
            avatar_url_for_size("https://lh3.googleusercontent.com/a/avatar=s96-c", 56),
            "https://lh3.googleusercontent.com/a/avatar=s56-c",
        )


class ProviderIdentityTestCase(unittest.TestCase):
    @staticmethod
    def _response(status_code=200, payload=None):
        return SimpleNamespace(status_code=status_code, json=lambda: payload)

    def test_google_identity_uses_exact_endpoint_headers_and_timeout(self):
        response = self._response(payload={
            "id": "google-user-1",
            "email": "student@example.com",
            "name": "Student Name",
            "picture": "https://example.com/google-avatar.png",
            "verified_email": True,
        })

        with patch("blueprints.auth.http_requests.get", return_value=response) as http_get:
            identity = auth._fetch_provider_identity("google", "google-token")

        self.assertEqual(identity, {
            "id": "google-user-1",
            "email": "student@example.com",
            "name": "Student Name",
            "avatar_url": "https://example.com/google-avatar.png",
        })
        http_get.assert_called_once_with(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": "Bearer google-token"},
            timeout=8,
        )

    def test_google_identity_rejects_an_explicitly_unverified_email(self):
        response = self._response(payload={
            "id": "google-user-1",
            "email": "student@example.com",
            "verified_email": False,
        })

        with patch("blueprints.auth.http_requests.get", return_value=response) as http_get:
            identity = auth._fetch_provider_identity("google", "google-token")

        self.assertEqual(identity, {})
        http_get.assert_called_once_with(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": "Bearer google-token"},
            timeout=8,
        )

    def test_github_identity_uses_profile_email_and_login_name_fallback(self):
        response = self._response(payload={
            "id": 42,
            "email": "octocat@example.com",
            "name": "",
            "login": "octocat",
            "avatar_url": "https://avatars.example.com/octocat.png",
        })

        with patch("blueprints.auth.http_requests.get", return_value=response) as http_get:
            identity = auth._fetch_provider_identity("github", "github-token")

        self.assertEqual(identity, {
            "id": 42,
            "email": "octocat@example.com",
            "name": "octocat",
            "avatar_url": "https://avatars.example.com/octocat.png",
        })
        http_get.assert_called_once_with(
            "https://api.github.com/user",
            headers={
                "Authorization": "Bearer github-token",
                "Accept": "application/vnd.github+json",
            },
            timeout=8,
        )

    def test_github_identity_falls_back_to_the_primary_verified_email(self):
        profile_response = self._response(payload={
            "id": "github-user-1",
            "email": None,
            "name": "GitHub Student",
            "avatar_url": "https://avatars.example.com/student.png",
        })
        emails_response = self._response(payload=[
            {"email": "unverified-primary@example.com", "primary": True, "verified": False},
            {"email": "verified-secondary@example.com", "primary": False, "verified": True},
            {"email": "verified-primary@example.com", "primary": True, "verified": True},
        ])
        headers = {
            "Authorization": "Bearer github-token",
            "Accept": "application/vnd.github+json",
        }

        with patch(
            "blueprints.auth.http_requests.get",
            side_effect=[profile_response, emails_response],
        ) as http_get:
            identity = auth._fetch_provider_identity("github", "github-token")

        self.assertEqual(identity, {
            "id": "github-user-1",
            "email": "verified-primary@example.com",
            "name": "GitHub Student",
            "avatar_url": "https://avatars.example.com/student.png",
        })
        self.assertEqual(http_get.call_args_list, [
            call(
                "https://api.github.com/user",
                headers=headers,
                timeout=8,
            ),
            call(
                "https://api.github.com/user/emails",
                headers=headers,
                timeout=8,
            ),
        ])

    def test_github_identity_keeps_profile_when_email_fallback_is_non_200(self):
        profile_response = self._response(payload={
            "id": "github-user-1",
            "email": None,
            "name": "GitHub Student",
            "avatar_url": "https://avatars.example.com/student.png",
        })
        emails_response = SimpleNamespace(
            status_code=503,
            json=Mock(side_effect=AssertionError("email JSON should not be read")),
        )
        headers = {
            "Authorization": "Bearer github-token",
            "Accept": "application/vnd.github+json",
        }

        with patch(
            "blueprints.auth.http_requests.get",
            side_effect=[profile_response, emails_response],
        ) as http_get:
            identity = auth._fetch_provider_identity("github", "github-token")

        self.assertEqual(identity, {
            "id": "github-user-1",
            "email": None,
            "name": "GitHub Student",
            "avatar_url": "https://avatars.example.com/student.png",
        })
        emails_response.json.assert_not_called()
        self.assertEqual(http_get.call_args_list, [
            call(
                "https://api.github.com/user",
                headers=headers,
                timeout=8,
            ),
            call(
                "https://api.github.com/user/emails",
                headers=headers,
                timeout=8,
            ),
        ])

    def test_github_identity_returns_empty_when_email_fallback_json_raises(self):
        profile_response = self._response(payload={
            "id": "github-user-1",
            "email": None,
            "name": "GitHub Student",
            "avatar_url": "https://avatars.example.com/student.png",
        })
        emails_response = SimpleNamespace(
            status_code=200,
            json=Mock(side_effect=ValueError("invalid email JSON")),
        )
        headers = {
            "Authorization": "Bearer github-token",
            "Accept": "application/vnd.github+json",
        }

        with patch(
            "blueprints.auth.http_requests.get",
            side_effect=[profile_response, emails_response],
        ) as http_get:
            identity = auth._fetch_provider_identity("github", "github-token")

        self.assertEqual(identity, {})
        emails_response.json.assert_called_once_with()
        self.assertEqual(http_get.call_args_list, [
            call(
                "https://api.github.com/user",
                headers=headers,
                timeout=8,
            ),
            call(
                "https://api.github.com/user/emails",
                headers=headers,
                timeout=8,
            ),
        ])

    def test_discord_identity_uses_verified_profile_and_animated_avatar_url(self):
        response = self._response(payload={
            "id": "discord-user-1",
            "email": "student@example.com",
            "global_name": "Student Display",
            "username": "student#1234",
            "avatar": "a_avatar-hash",
            "verified": True,
        })

        with patch("blueprints.auth.http_requests.get", return_value=response) as http_get:
            identity = auth._fetch_provider_identity("discord", "discord-token")

        self.assertEqual(identity, {
            "id": "discord-user-1",
            "email": "student@example.com",
            "name": "Student Display",
            "username": "student#1234",
            "avatar_url": "https://cdn.discordapp.com/avatars/discord-user-1/a_avatar-hash.gif?size=256",
        })
        http_get.assert_called_once_with(
            "https://discord.com/api/users/@me",
            headers={"Authorization": "Bearer discord-token"},
            timeout=8,
        )

    def test_discord_identity_rejects_an_explicitly_unverified_email(self):
        response = self._response(payload={
            "id": "discord-user-1",
            "email": "student@example.com",
            "verified": False,
        })

        with patch("blueprints.auth.http_requests.get", return_value=response) as http_get:
            identity = auth._fetch_provider_identity("discord", "discord-token")

        self.assertEqual(identity, {})
        http_get.assert_called_once_with(
            "https://discord.com/api/users/@me",
            headers={"Authorization": "Bearer discord-token"},
            timeout=8,
        )

    def test_provider_identity_returns_empty_for_non_200_responses(self):
        cases = [
            (
                "google",
                "google-token",
                "https://www.googleapis.com/oauth2/v2/userinfo",
                {"Authorization": "Bearer google-token"},
            ),
            (
                "github",
                "github-token",
                "https://api.github.com/user",
                {
                    "Authorization": "Bearer github-token",
                    "Accept": "application/vnd.github+json",
                },
            ),
            (
                "discord",
                "discord-token",
                "https://discord.com/api/users/@me",
                {"Authorization": "Bearer discord-token"},
            ),
        ]

        for provider, token, url, headers in cases:
            with self.subTest(provider=provider):
                with patch(
                    "blueprints.auth.http_requests.get",
                    return_value=self._response(status_code=401),
                ) as http_get:
                    identity = auth._fetch_provider_identity(provider, token)

                self.assertEqual(identity, {})
                http_get.assert_called_once_with(url, headers=headers, timeout=8)

    def test_provider_identity_returns_empty_when_http_or_json_raises(self):
        with patch(
            "blueprints.auth.http_requests.get",
            side_effect=RuntimeError("network down"),
        ) as http_get:
            self.assertEqual(auth._fetch_provider_identity("google", "google-token"), {})
        http_get.assert_called_once_with(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": "Bearer google-token"},
            timeout=8,
        )

        response = SimpleNamespace(
            status_code=200,
            json=Mock(side_effect=ValueError("invalid JSON")),
        )
        with patch("blueprints.auth.http_requests.get", return_value=response) as http_get:
            self.assertEqual(auth._fetch_provider_identity("discord", "discord-token"), {})
        response.json.assert_called_once_with()
        http_get.assert_called_once_with(
            "https://discord.com/api/users/@me",
            headers={"Authorization": "Bearer discord-token"},
            timeout=8,
        )

    def test_unsupported_or_incomplete_provider_identity_makes_no_request(self):
        cases = [
            (None, "token"),
            ("", "token"),
            ("not-a-provider", "token"),
            ("google", None),
            ("google", ""),
        ]

        for provider, token in cases:
            with self.subTest(provider=provider, token=token):
                with patch("blueprints.auth.http_requests.get") as http_get:
                    identity = auth._fetch_provider_identity(provider, token)

                self.assertEqual(identity, {})
                http_get.assert_not_called()


class AuthSessionErrorContractTestCase(unittest.TestCase):
    def setUp(self):
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        self.app = Flask(
            __name__,
            template_folder=os.path.join(project_root, "templates"),
            static_folder=os.path.join(project_root, "static"),
        )
        self.app.secret_key = "test"
        login_manager.init_app(self.app)
        self.app.register_blueprint(auth.auth_bp)

    def tearDown(self):
        reset_flask_login_manager()

    def test_session_rejects_missing_session_proof_with_stable_error(self):
        with self.app.test_client() as client:
            with patch("blueprints.auth.http_requests.get") as http_get, \
                    patch.object(auth, "_account_from_jwt") as account_from_jwt, \
                    patch.object(auth, "_account_from_user_id") as account_from_user_id, \
                    patch.object(auth, "_complete_appwrite_login") as complete_login:
                response = client.post("/auth/session", json={"user_id": "user-1"})

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json(), {"error": "Missing Appwrite session proof."})
        http_get.assert_not_called()
        account_from_jwt.assert_not_called()
        account_from_user_id.assert_not_called()
        complete_login.assert_not_called()

    def test_session_rejects_non_200_provider_identity_before_appwrite_login(self):
        provider_response = SimpleNamespace(status_code=401, json=lambda: {})

        with self.app.test_client() as client:
            with patch(
                "blueprints.auth.http_requests.get",
                return_value=provider_response,
            ) as http_get, \
                    patch.object(auth, "_account_from_user_id") as account_from_user_id, \
                    patch.object(auth, "_complete_appwrite_login") as complete_login:
                response = client.post(
                    "/auth/session",
                    json={
                        "user_id": "user-1",
                        "provider": "google",
                        "provider_access_token": "google-token",
                    },
                )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json(), {"error": "Invalid provider session."})
        http_get.assert_called_once_with(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": "Bearer google-token"},
            timeout=8,
        )
        account_from_user_id.assert_not_called()
        complete_login.assert_not_called()


class AvatarStorageServiceTestCase(unittest.TestCase):
    def _fake_response(self, *, status_code=200, content_type="image/png", body=b"imgbytes", content_length=None):
        headers = {"Content-Type": content_type}
        if content_length is not None:
            headers["Content-Length"] = str(content_length)

        class _Resp:
            def __init__(self):
                self.status_code = status_code
                self.headers = headers
                self.closed = False

            def iter_content(self, chunk_size=0):
                yield body

            def close(self):
                self.closed = True

        return _Resp()

    def test_store_avatar_from_url_uploads_and_returns_view_url(self):
        from services import avatar_storage

        created = {}

        class _Storage:
            def __init__(self, _client):
                pass

            def create_file(self, bucket_id, file_id, input_file, permissions=None):
                created["bucket_id"] = bucket_id
                created["file_id"] = file_id
                created["permissions"] = permissions
                return {"$id": file_id}

        with patch.object(avatar_storage, "require_public_http_url", side_effect=lambda url: url), \
                patch.object(avatar_storage.http_requests, "get", return_value=self._fake_response()), \
                patch.object(avatar_storage, "Storage", _Storage), \
                patch.object(avatar_storage, "build_avatar_view_url", side_effect=lambda fid: f"https://appwrite.test/view/{fid}"):
            result = avatar_storage.store_avatar_from_url("user-1", "https://provider.test/avatar.png")

        self.assertIsNotNone(result)
        self.assertEqual(result["file_id"], created["file_id"])
        self.assertEqual(result["view_url"], f"https://appwrite.test/view/{created['file_id']}")

    def test_store_avatar_from_url_rejects_unsupported_content_type(self):
        from services import avatar_storage

        with patch.object(avatar_storage, "require_public_http_url", side_effect=lambda url: url), \
                patch.object(avatar_storage.http_requests, "get", return_value=self._fake_response(content_type="text/html")), \
                patch.object(avatar_storage, "Storage") as storage_class:
            result = avatar_storage.store_avatar_from_url("user-1", "https://provider.test/avatar.png")

        self.assertIsNone(result)
        storage_class.assert_not_called()

    def test_store_avatar_from_url_returns_none_on_download_error(self):
        from services import avatar_storage

        with patch.object(avatar_storage, "require_public_http_url", side_effect=lambda url: url), \
                patch.object(avatar_storage.http_requests, "get", side_effect=RuntimeError("network down")), \
                patch.object(avatar_storage, "Storage") as storage_class:
            result = avatar_storage.store_avatar_from_url("user-1", "https://provider.test/avatar.png")

        self.assertIsNone(result)
        storage_class.assert_not_called()

    def test_store_avatar_from_url_enforces_size_limit(self):
        from services import avatar_storage

        with patch.object(avatar_storage, "require_public_http_url", side_effect=lambda url: url), patch.object(
            avatar_storage.http_requests,
            "get",
            return_value=self._fake_response(content_length=avatar_storage.MAX_AVATAR_BYTES + 1),
        ), patch.object(avatar_storage, "Storage") as storage_class:
            result = avatar_storage.store_avatar_from_url("user-1", "https://provider.test/avatar.png")

        self.assertIsNone(result)
        storage_class.assert_not_called()

    def test_store_avatar_from_url_ignores_empty_source(self):
        from services import avatar_storage

        with patch.object(avatar_storage.http_requests, "get") as http_get:
            self.assertIsNone(avatar_storage.store_avatar_from_url("user-1", ""))
        http_get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
