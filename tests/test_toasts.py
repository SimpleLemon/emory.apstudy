import json
import re
import unittest
from pathlib import Path

from flask import Flask, jsonify, render_template, request, session

from services.toasts import consume_request_toasts, pop_toasts, push_toast


ROOT = Path(__file__).resolve().parents[1]
SESSION_KEY = "_toast_queue"
EMBEDDED_TOASTS_RE = re.compile(
    r'<script type="application/json" id="apstudy-server-toasts">(.*?)</script>',
    re.DOTALL,
)


class ToastQueueTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(
            __name__,
            template_folder=str(ROOT / "templates"),
            static_folder=str(ROOT / "static"),
        )
        self.app.secret_key = "test"

        @self.app.context_processor
        def inject_server_toasts():
            if request.path.startswith("/api/"):
                return {"server_toasts": []}
            return {"server_toasts": consume_request_toasts()}

        @self.app.get("/page")
        def page():
            return render_template("_shared_runtime_assets.html")

        @self.app.get("/api/toasts")
        def drain_toasts():
            # Fallback path for pages that omit the embedded payload.
            # A cached GET would re-show consumed toasts, so this must be no-store.
            response = jsonify(pop_toasts())
            response.headers["Cache-Control"] = "no-store"
            return response

        self.client = self.app.test_client()

    def _queue(self, message="Request approved.", **kwargs):
        with self.app.test_request_context("/"):
            push_toast(message, **kwargs)
            queued = list(session.get(SESSION_KEY) or [])
        with self.client.session_transaction() as client_session:
            client_session[SESSION_KEY] = queued

    def _embedded(self, html):
        match = EMBEDDED_TOASTS_RE.search(html)
        self.assertIsNotNone(match, html)
        return json.loads(match.group(1))

    def _queued(self):
        with self.client.session_transaction() as session:
            queue = session.get(SESSION_KEY)
            return list(queue) if isinstance(queue, list) else []

    def test_push_toast_assigns_id_and_created_at(self):
        with self.app.test_request_context("/"):
            push_toast("Request approved.", type="success")
            queued = pop_toasts()
        self.assertEqual(len(queued), 1)
        self.assertEqual(queued[0]["message"], "Request approved.")
        self.assertEqual(queued[0]["type"], "success")
        self.assertTrue(queued[0]["id"])
        self.assertTrue(queued[0]["created_at"])

    def test_html_page_consumes_queue_once(self):
        self._queue("Request approved.", type="success")
        first = self.client.get("/page")
        payload = self._embedded(first.get_data(as_text=True))

        self.assertEqual(first.status_code, 200)
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["message"], "Request approved.")
        self.assertEqual(payload[0]["type"], "success")
        self.assertEqual(self._queued(), [])

        second = self.client.get("/page")
        self.assertEqual(self._embedded(second.get_data(as_text=True)), [])
        self.assertEqual(self._queued(), [])

    def test_api_toasts_does_not_restore_queue_after_html_consume(self):
        self._queue("Request approved.", type="success")
        self.client.get("/page")

        leftover = self.client.get("/api/toasts")
        self.assertEqual(leftover.status_code, 200)
        self.assertEqual(leftover.get_json(), [])
        self.assertEqual(leftover.headers.get("Cache-Control"), "no-store")
        self.assertEqual(self._queued(), [])

    def test_api_toasts_fallback_pops_when_html_did_not_consume(self):
        self._queue("Request approved.", type="success")
        response = self.client.get("/api/toasts")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("Cache-Control"), "no-store")
        payload = response.get_json()
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["message"], "Request approved.")
        self.assertEqual(self._queued(), [])

    def test_consume_request_toasts_is_idempotent_for_one_request(self):
        with self.app.test_request_context("/page"):
            push_toast("Request approved.", type="success")
            first = consume_request_toasts()
            second = consume_request_toasts()
            self.assertEqual(first, second)
            self.assertEqual(len(first), 1)
            self.assertEqual(pop_toasts(), [])

    def test_stale_cookie_replay_is_the_old_lost_update_not_the_live_path(self):
        # Previous live path: GET /api/toasts popped the queue while a parallel
        # session-refreshing request (presence heartbeat, admin JSON) still held
        # the original cookie and wrote the toast back. HTML consume happens on
        # the navigation response itself, before any page JS runs, so that
        # race is no longer the live path.
        self._queue("Request approved.", type="success")
        snapshot = self._queued()
        self.assertTrue(snapshot)

        first = self.client.get("/page")
        self.assertEqual(self._embedded(first.get_data(as_text=True))[0]["message"], "Request approved.")
        self.assertEqual(self._embedded(self.client.get("/page").get_data(as_text=True)), [])

        with self.client.session_transaction() as session:
            session[SESSION_KEY] = snapshot
        restored = self._embedded(self.client.get("/page").get_data(as_text=True))
        self.assertEqual(restored[0]["message"], "Request approved.")
        self.assertEqual(self._embedded(self.client.get("/page").get_data(as_text=True)), [])


class ToastClientContractTests(unittest.TestCase):
    def test_embedded_payload_id_matches_drain_selector(self):
        template = (ROOT / "templates/_shared_runtime_assets.html").read_text()
        global_js = (ROOT / "static/js/core/global.js").read_text()
        self.assertIn('id="apstudy-server-toasts"', template)
        self.assertIn('getElementById("apstudy-server-toasts")', global_js)
        self.assertIn('cache: "no-store"', global_js)
        self.assertIn("if (embedded !== null)", global_js)

    def test_toast_client_max_lifetime_is_unpauseable_and_hover_pauses_immediately(self):
        primitives = (ROOT / "static/js/core/ui-primitives.js").read_text()
        self.assertIn("TOAST_MAX_LIFETIME_MS", primitives)
        self.assertIn("maxTimer", primitives)
        self.assertIn("dismiss('max-lifetime')", primitives)
        self.assertNotIn("allowPointerPause", primitives)
        self.assertIn("mouseenter', () => setPaused('pointer', true)", primitives)
        self.assertIn("hasRecentlyShownToast", primitives)
        self.assertIn("apstudy.seen-toasts", primitives)

    def test_dashboard_toasts_endpoint_uses_no_store(self):
        dashboard = (ROOT / "blueprints/dashboard.py").read_text()
        self.assertIn('response.headers["Cache-Control"] = "no-store"', dashboard)
        self.assertIn("pop_toasts()", dashboard)


class DiagnosticsToastEmbedTests(unittest.TestCase):
    def test_diagnostics_include_embeds_server_toasts(self):
        app = Flask(
            __name__,
            template_folder=str(ROOT / "templates"),
            static_folder=str(ROOT / "static"),
        )
        with app.test_request_context("/"):
            html = render_template(
                "_diagnostics_assets.html",
                frontend_console_diagnostics_enabled=False,
            )
        self.assertIn('id="apstudy-server-toasts"', html)
        self.assertIn("[]", html)


if __name__ == "__main__":
    unittest.main()
