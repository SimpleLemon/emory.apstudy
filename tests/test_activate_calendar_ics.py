import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch


MODULE_PATH = Path(__file__).resolve().parents[1] / "deploy" / "activate-calendar-ics.py"
SPEC = importlib.util.spec_from_file_location("activate_calendar_ics", MODULE_PATH)
ACTIVATE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(ACTIVATE)

EXPECTED_HEAD = "a" * 40


class FakeRunner:
    def __init__(self, root, *, fail=None):
        self.root = Path(root)
        self.calls = []
        self.fail = fail
        self.head = EXPECTED_HEAD
        self.tracked_status = ""
        self.nginx_topology = (
            "# configuration file /etc/nginx/nginx.conf:\n"
            "events {}\nhttp { server { listen 443 ssl; location / { return 204; } } }\n"
        )

    def run(self, argv, *, check=True):
        del check
        command = tuple(str(part) for part in argv)
        self.calls.append(command)
        if self.fail and self.fail(command, len(self.calls)):
            raise ACTIVATE.ActivationError("injected command failure")
        stdout = ""
        if command[:3] == ("git", "-C", str(self.root)):
            if command[3:] == ("rev-parse", "HEAD"):
                stdout = self.head + "\n"
            elif command[3:] == ("status", "--porcelain=v1", "-z", "--untracked-files=all"):
                stdout = self.tracked_status
        elif command == ("nginx", "-T"):
            stdout = self.nginx_topology
        return subprocess.CompletedProcess(command, 0, stdout, "")


class FakeHTTPResponse:
    def __init__(self, status, headers=None, body=b""):
        self.status = status
        self.headers = dict(headers or {})
        self._body = body
        self.read_calls = 0
        self.close_calls = 0
        self.closed = False

    def read(self, limit=-1):
        self.read_calls += 1
        return self._body if limit < 0 else self._body[:limit]

    def close(self):
        self.close_calls += 1
        self.closed = True
        return None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()
        return False


class FakeHTTP:
    def __init__(self, *, root_status=200, readiness=None, readiness_default=200, advance=None):
        self.requests = []
        self.request_headers = []
        self.root_status = root_status
        self.readiness = list(readiness or [])
        self.readiness_default = readiness_default
        self.readiness_calls = 0
        self.head_calls = 0
        self.advance = advance
        self.responses = []

    def __call__(self, request, timeout):
        self.requests.append((request.method, request.full_url, timeout))
        self.request_headers.append(dict(request.headers))
        if self.advance:
            self.advance()
        headers = {
            "Cache-Control": "private, no-store, no-transform",
            "X-Content-Type-Options": "nosniff",
        }
        if request.full_url == ACTIVATE.PRODUCTION_LOOPBACK_ORIGIN + "/":
            self.readiness_calls += 1
            outcome = (
                self.readiness.pop(0)
                if self.readiness
                else self.readiness_default
            )
            if isinstance(outcome, BaseException):
                raise outcome
            response = FakeHTTPResponse(outcome, headers, b"ok")
            self.responses.append(response)
            return response
        if request.full_url == ACTIVATE.PRODUCTION_HEALTH_ORIGIN + "/":
            response = FakeHTTPResponse(self.root_status, headers, b"ok")
            self.responses.append(response)
            return response
        if request.method == "HEAD":
            self.head_calls += 1
            if self.head_calls >= 32:
                response = FakeHTTPResponse(429, {**headers, "Retry-After": "60"})
                self.responses.append(response)
                return response
        response = FakeHTTPResponse(404, headers, b"" if request.method == "HEAD" else b"Not Found")
        self.responses.append(response)
        return response


class ManualClock:
    def __init__(self):
        self.value = 100.0

    def __call__(self):
        return self.value

    def advance(self, seconds=31.0):
        self.value += seconds


class CalendarIcsActivationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        # macOS exposes /var as a symlink to /private/var. Resolve the
        # TemporaryDirectory root so the production-grade component checks
        # exercise only paths created by this test rather than that OS alias.
        base = Path(self.temp.name).resolve()
        self.root = base / "app"
        self.root.mkdir()
        self.env = self.root / ".env"
        self.env.write_bytes(
            b"# preserve exactly\nAPP_BASE_URL=https://example.test\n"
            b"APSTUDY_EXTENSION_CALENDAR_ROLLOUT=readonly-v1\nUNRELATED=value\n"
        )
        self.site_dir = base / "sites-available"
        self.site_dir.mkdir()
        self.site = self.site_dir / "nest.apstudy.org"
        self.site.write_text(
            "server { listen 80; server_name nest.apstudy.org; }\n"
            "server {\n  listen 443 ssl;\n  server_name nest.apstudy.org;\n"
            "  location / { proxy_pass http://127.0.0.1:8000; }\n}\n",
            encoding="utf-8",
        )
        self.conf_dir = base / "conf.d"
        self.conf_dir.mkdir()
        self.snippets_dir = base / "snippets"
        self.snippets_dir.mkdir()
        self.main_conf = base / "nginx.conf"
        self.main_conf.write_text("events {}\nhttp { include conf.d/*.conf; }\n", encoding="utf-8")
        self.state = base / "state"
        self.lock = base / "activation.lock"
        self.logs = base / "logs"
        self.logs.mkdir()
        for name in ACTIVATE.RELEVANT_NGINX_LOGS:
            (self.logs / name).write_bytes(b"")
        self.runner = FakeRunner(self.root)
        self.http = FakeHTTP()
        self.tool = self.make_tool()

    def tearDown(self):
        self.temp.cleanup()

    def make_tool(self, *, runner=None, hook=None, http=None, clock=None, sleeper=None):
        return ACTIVATE.ActivationTool(
            root=self.root,
            env_path=self.env,
            nginx_main_conf=self.main_conf,
            nginx_site=self.site,
            nginx_conf_dir=self.conf_dir,
            nginx_snippets_dir=self.snippets_dir,
            state_dir=self.state,
            lock_path=self.lock,
            runner=runner or self.runner,
            nginx_log_dir=self.logs,
            http_open=http or self.http,
            clock=clock or time.monotonic,
            sleeper=sleeper or time.sleep,
            hook=hook,
        )

    def pins(self):
        return {
            "expected_head": EXPECTED_HEAD,
            "expected_env": ACTIVATE.sha256_file(self.env),
            "expected_site": ACTIVATE.sha256_file(self.site),
        }

    def baseline(self):
        env_stat = self.env.stat()
        site_stat = self.site.stat()
        return {
            "env": self.env.read_bytes(),
            "site": self.site.read_bytes(),
            "env_meta": (stat.S_IMODE(env_stat.st_mode), env_stat.st_uid, env_stat.st_gid),
            "site_meta": (stat.S_IMODE(site_stat.st_mode), site_stat.st_uid, site_stat.st_gid),
            "targets": {name: path.exists() for name, path in self.tool.nginx_targets.items()},
        }

    def assert_baseline(self, baseline):
        self.assertEqual(self.env.read_bytes(), baseline["env"])
        self.assertEqual(self.site.read_bytes(), baseline["site"])
        env_stat = self.env.stat()
        site_stat = self.site.stat()
        self.assertEqual(
            (stat.S_IMODE(env_stat.st_mode), env_stat.st_uid, env_stat.st_gid),
            baseline["env_meta"],
        )
        self.assertEqual(
            (stat.S_IMODE(site_stat.st_mode), site_stat.st_uid, site_stat.st_gid),
            baseline["site_meta"],
        )
        self.assertEqual(
            {name: path.exists() for name, path in self.tool.nginx_targets.items()},
            baseline["targets"],
        )
        self.assertNotIn(b"nest-calendar-ics-feed", self.site.read_bytes())

    def apply(self, tool=None):
        (tool or self.tool).apply(owner_allowlist="*", **self.pins())

    def prepare_interrupted_activation(self):
        pins = self.pins()
        baseline = self.tool._preflight(**pins)
        candidate_env, _ = ACTIVATE._candidate_env(self.env.read_bytes(), "*")
        candidate_site = ACTIVATE._site_with_feed_include(
            self.site.read_bytes(), str(self.snippets_dir / "nest-calendar-ics-feed.conf")
        )
        txid = "20260827T120000Z-abcdefabcdef"
        targets = {"env": self.env, "site": self.site, **self.tool.nginx_targets}
        backup, manifest = self.tool._make_backup(txid, targets)
        stage, staged = self.tool._stage(txid, self.env.read_bytes(), candidate_env, candidate_site)
        self.tool._create_journal(
            txid,
            backup,
            manifest,
            stage,
            staged,
            {name: ACTIVATE.sha256_file(Path(path)) for name, path in staged.items()},
            baseline,
        )
        for name in ("real_ip", "http", "feed"):
            self.tool._install_file(Path(staged[name]), targets[name], mode=0o644)
        self.tool._install_file(
            Path(staged["site"]), self.site, mode=manifest["site"]["mode"], metadata=manifest["site"]
        )
        self.tool._install_file(
            Path(staged["env"]), self.env, mode=manifest["env"]["mode"], metadata=manifest["env"]
        )
        self.tool._update_journal(phase="env_installed")
        return txid

    def fresh_case(self):
        case = type(self)(self._testMethodName)
        case.setUp()
        return case

    def test_env_validation_and_no_trailing_newline_preservation(self):
        for value in (
            b"BAD LINE\n",
            b"VALID=x\nINVALID=\xff\n",
            b"CALENDAR_ICS_UID_SECRET=x\n",
            b"CALENDAR_ICS_UID_SECRET=x\nCALENDAR_ICS_UID_SECRET=y\n",
            b"CALENDAR_ICS_OWNER_ALLOWLIST=owner\n",
            b"CALENDAR_ICS_ALLOWLIST=owner\n",
        ):
            with self.subTest(value=value), self.assertRaises(ACTIVATE.ActivationError):
                ACTIVATE._validate_env_base(value)
        base = b"APP_BASE_URL=https://example.test\nAPSTUDY_EXTENSION_CALENDAR_ROLLOUT=x\nOTHER=y"
        candidate, secret = ACTIVATE._candidate_env(base, "*")
        self.assertEqual(candidate[: len(base)], base)
        self.assertRegex(secret, r"^[0-9a-f]{64}$")
        self.assertEqual(candidate.count(b"CALENDAR_ICS_SUBSCRIPTIONS_ENABLED="), 1)
        self.assertEqual(candidate.count(b"CALENDAR_ICS_SUBSCRIPTIONS_OWNER_ALLOWLIST="), 1)
        self.assertEqual(candidate.count(b"CALENDAR_ICS_UID_SECRET="), 1)

    def test_site_requires_one_https_server_one_catchall_and_no_existing_reference(self):
        candidate = ACTIVATE._site_with_feed_include(
            self.site.read_bytes(), "/etc/nginx/snippets/nest-calendar-ics-feed.conf"
        ).decode()
        self.assertLess(candidate.index("include /etc/nginx"), candidate.index("location / {"))
        ambiguous = self.site.read_text() + "server { listen 443; location / { return 204; } }\n"
        with self.assertRaises(ACTIVATE.ActivationError):
            ACTIVATE._site_with_feed_include(ambiguous.encode(), "/tmp/feed.conf")
        existing = self.site.read_bytes() + b"\n# nest-calendar-ics-feed.conf\n"
        with self.assertRaises(ACTIVATE.ActivationError):
            ACTIVATE._site_with_feed_include(existing, "/tmp/feed.conf")

    def test_check_requires_pins_rejects_tracked_dirt_and_is_read_only(self):
        pins = self.pins()
        with self.assertRaises(ACTIVATE.ActivationError):
            self.tool._preflight(None, pins["expected_env"], pins["expected_site"])
        self.runner.tracked_status = " M config.py\0"
        with self.assertRaises(ACTIVATE.ActivationError):
            self.tool.check(**pins)
        self.runner.tracked_status = (
            "?? actions-runner/_diag/Runner_1.log\0"
            "?? actions-runner/_work/nest/job/output.txt\0"
        )
        self.tool.check(**pins)
        for dirty in (
            "?? arbitrary.py\0",
            "?? actions-runner-evil/config.sh\0",
            "?? actions-runner\0",
            "?? actions-runner/../config.py\0",
            "?? nested/actions-runner/file\0",
        ):
            with self.subTest(dirty=dirty):
                self.runner.tracked_status = dirty
                with self.assertRaises(ACTIVATE.ActivationError):
                    self.tool.check(**pins)
        self.runner.tracked_status = ""
        before = self.baseline()
        self.tool.check(**pins)
        self.assert_baseline(before)
        self.assertFalse(self.state.exists())
        self.assertFalse(self.lock.exists())

    def test_pins_are_rechecked_immediately_before_first_live_replacement(self):
        baseline = self.baseline()

        def hook(phase):
            if phase == "shadow:validate":
                self.runner.tracked_status = " M config.py\0"

        tool = self.make_tool(hook=hook)
        with self.assertRaises(ACTIVATE.ActivationError):
            self.apply(tool)
        self.assert_baseline(baseline)
        status_calls = [call for call in self.runner.calls if "--porcelain=v1" in call]
        self.assertEqual(len(status_calls), 2)

    def test_effective_nginx_topology_rejects_nested_stale_markers_and_accepts_clean_dump(self):
        self.runner.fail = lambda command, _number: command == ("nginx", "-T")
        with self.assertRaises(ACTIVATE.ActivationError):
            self.tool.check(**self.pins())
        self.runner.fail = None
        self.runner.nginx_topology = (
            "# configuration file /etc/nginx/sites-enabled/sibling:\n"
            "include /etc/nginx/snippets/nested-calendar.conf;\n"
            "# configuration file /etc/nginx/snippets/nested-calendar.conf:\n"
            "limit_req_zone $binary_remote_addr zone=nest_calendar_ics_per_ip:10m rate=300r/m;\n"
        )
        with self.assertRaisesRegex(ACTIVATE.ActivationError, "effective Nginx topology"):
            self.tool.check(**self.pins())
        self.runner.nginx_topology = (
            "# configuration file /etc/nginx/sites-enabled/sibling:\n"
            "server { listen 443 ssl; location /health { return 204; } }\n"
        )
        self.tool.check(**self.pins())
        self.assertIn(("nginx", "-T"), self.runner.calls)

    def test_production_cli_rejects_path_service_and_health_overrides(self):
        for option in ("--root", "--env-path", "--nginx-site", "--state-dir", "--lock-path", "--service", "--health-url"):
            with self.subTest(option=option), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                ACTIVATE.build_parser().parse_args(["--check", option, "/tmp/unsafe"])
        with patch.object(ACTIVATE, "ActivationTool"), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(ACTIVATE.main(["--check"]), 1)

    def test_lock_contention_and_symlinked_security_paths_fail_closed(self):
        first = self.make_tool()
        second = self.make_tool()
        first._lock()
        try:
            with self.assertRaises(ACTIVATE.ActivationError):
                second.check(**self.pins())
        finally:
            first._unlock()
        self.lock.unlink()
        victim = Path(self.temp.name) / "victim"
        victim.write_text("safe")
        self.lock.symlink_to(victim)
        with self.assertRaises(ACTIVATE.ActivationError):
            self.tool.check(**self.pins())
        self.assertEqual(victim.read_text(), "safe")

    def test_shadow_uses_complete_candidate_site_and_redirected_absolute_includes(self):
        extra = Path(self.temp.name) / "existing-server-snippet.conf"
        extra.write_text("add_header X-Shadow-Test yes;\n", encoding="utf-8")
        self.site.write_text(self.site.read_text().replace("  location /", f"  include {extra};\n  location /"))
        candidate_env, _ = ACTIVATE._candidate_env(self.env.read_bytes(), "*")
        candidate_site = ACTIVATE._site_with_feed_include(
            self.site.read_bytes(), str(self.snippets_dir / "nest-calendar-ics-feed.conf")
        )
        stage, _ = self.tool._stage("20260827T120000Z-111111111111", self.env.read_bytes(), candidate_env, candidate_site)
        shadow = self.tool._build_shadow_nginx(stage)
        shadow_config = shadow.read_text()
        shadow_site = (stage / "shadow-site.conf").read_text()
        self.assertIn(f"include {stage / 'real_ip'};", shadow_config)
        self.assertIn(f"include {stage / 'http'};", shadow_config)
        self.assertIn(f"include {stage / 'shadow-site.conf'};", shadow_config)
        self.assertIn("server { listen 80", shadow_site)
        self.assertIn("listen 443 ssl", shadow_site)
        self.assertIn("X-Shadow-Test", next((stage / "shadow-includes").iterdir()).read_text())
        self.assertNotIn(str(extra), shadow_site)
        self.assertNotIn(str(self.tool.nginx_targets["feed"]), shadow_site)
        self.tool._shadow_nginx(stage)
        self.assertEqual(self.runner.calls[-1], ("nginx", "-t", "-c", str(shadow), "-p", str(stage)))
        with self.assertRaisesRegex(ACTIVATE.ActivationError, "not redirected"):
            self.tool._rewrite_shadow_includes(
                f"server {{ include {extra}; }}", stage, {}, stage / "shadow-feed.conf"
            )

    @unittest.skipUnless(shutil.which("nginx"), "real nginx is not installed")
    def test_shadow_topology_passes_real_nginx_without_root_when_available(self):
        self.site.write_text(
            "server { listen 80; location /health { return 204; } }\n"
            "server { listen 443; location / { return 204; } }\n",
            encoding="utf-8",
        )
        candidate_env, _ = ACTIVATE._candidate_env(self.env.read_bytes(), "*")
        candidate_site = ACTIVATE._site_with_feed_include(
            self.site.read_bytes(), str(self.snippets_dir / "nest-calendar-ics-feed.conf")
        )
        stage, _ = self.tool._stage("20260827T120000Z-222222222222", self.env.read_bytes(), candidate_env, candidate_site)
        self.make_tool(runner=ACTIVATE.CommandRunner(timeout=10))._shadow_nginx(stage)

    def test_apply_commits_with_exact_manifest_and_no_secret_exposure(self):
        baseline = self.baseline()
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            self.apply()
        self.assertNotEqual(self.env.read_bytes(), baseline["env"])
        self.assertTrue(all(path.exists() for path in self.tool.nginx_targets.values()))
        journal_path = next(self.state.glob("transaction-*.json"))
        journal = json.loads(journal_path.read_text())
        secret = self.env.read_text().split("CALENDAR_ICS_UID_SECRET=", 1)[1].splitlines()[0]
        self.assertEqual(journal["status"], "committed")
        self.assertEqual(journal["baseline"]["head"], EXPECTED_HEAD)
        self.assertNotIn(secret, journal_path.read_text())
        self.assertNotIn(secret, stdout.getvalue() + stderr.getvalue())
        self.assertNotIn(secret, " ".join(" ".join(call) for call in self.runner.calls))
        manifest = json.loads(next(self.state.rglob("manifest.json")).read_text())["targets"]
        for record in manifest.values():
            self.assertIn("mode", record)
            self.assertIn("uid", record)
            self.assertIn("gid", record)
        self.assertEqual(len([call for call in self.runner.calls if "--porcelain=v1" in call]), 2)
        self.assertLessEqual(len(self.http.requests), 44)
        self.assertTrue(all(timeout <= ACTIVATE.HEALTH_REQUEST_TIMEOUT_SECONDS for _, _, timeout in self.http.requests))

    def test_readiness_retries_connection_refused_then_public_probe_runs(self):
        http = FakeHTTP(readiness=[ConnectionRefusedError("not listening"), 200])
        sleeps = []
        self.apply(self.make_tool(http=http, sleeper=sleeps.append))

        loopback_url = ACTIVATE.PRODUCTION_LOOPBACK_ORIGIN + "/"
        loopback_requests = [request for request in http.requests if request[1] == loopback_url]
        self.assertEqual(len(loopback_requests), 2)
        self.assertEqual(sleeps, [ACTIVATE.READINESS_RETRY_DELAY_SECONDS])
        self.assertEqual(http.request_headers[0]["Host"], ACTIVATE.PRODUCTION_LOOPBACK_HOST)
        self.assertEqual(http.request_headers[1]["Host"], ACTIVATE.PRODUCTION_LOOPBACK_HOST)
        self.assertEqual(loopback_requests[0][1], "http://127.0.0.1:8000/")
        self.assertEqual(http.requests[2][1], ACTIVATE.PRODUCTION_HEALTH_ORIGIN + "/")
        self.assertTrue(all(timeout <= ACTIVATE.READINESS_REQUEST_TIMEOUT_SECONDS for _, _, timeout in loopback_requests))
        self.assertTrue(http.responses[0].closed)
        self.assertEqual(http.responses[0].read_calls, 0)

    def test_readiness_retries_5xx_then_returns_200_without_reading_body(self):
        http = FakeHTTP(readiness=[503, 200])
        sleeps = []
        self.make_tool(http=http, sleeper=sleeps.append)._wait_for_app_readiness()

        self.assertEqual(http.readiness_calls, 2)
        self.assertEqual(sleeps, [ACTIVATE.READINESS_RETRY_DELAY_SECONDS])
        self.assertEqual(len(http.responses), 2)
        self.assertTrue(all(response.closed for response in http.responses))
        self.assertTrue(all(response.read_calls == 0 for response in http.responses))

    def test_readiness_rejects_3xx_and_4xx_immediately_without_sleeping(self):
        for status in (302, 404):
            with self.subTest(status=status):
                http = FakeHTTP(readiness=[status, 200])
                sleeps = []
                with self.assertRaisesRegex(ACTIVATE.ActivationError, "non-success HTTP status"):
                    self.make_tool(http=http, sleeper=sleeps.append)._wait_for_app_readiness()
                self.assertEqual(http.readiness_calls, 1)
                self.assertEqual(sleeps, [])
                self.assertEqual(len(http.responses), 1)
                self.assertTrue(http.responses[0].closed)
                self.assertEqual(http.responses[0].close_calls, 1)
                self.assertEqual(http.responses[0].read_calls, 0)

    def test_readiness_closes_http_error_without_reading_error_body(self):
        class TrackingBody(io.BytesIO):
            def __init__(self, content):
                super().__init__(content)
                self.read_calls = 0

            def read(self, *args, **kwargs):
                self.read_calls += 1
                return super().read(*args, **kwargs)

        body = TrackingBody(b"private error body")
        error = ACTIVATE.HTTPError(
            ACTIVATE.PRODUCTION_LOOPBACK_ORIGIN + "/",
            404,
            "not found",
            {},
            body,
        )
        sleeps = []

        def http_open(_request, timeout):
            del timeout
            raise error

        with self.assertRaisesRegex(ACTIVATE.ActivationError, "non-success HTTP status"):
            self.make_tool(http=http_open, sleeper=sleeps.append)._wait_for_app_readiness()

        self.assertTrue(body.closed)
        self.assertEqual(body.read_calls, 0)
        self.assertEqual(sleeps, [])

    def test_readiness_status_failure_triggers_transaction_rollback(self):
        http = FakeHTTP(readiness=[302, 200])
        sleeps = []
        baseline = self.baseline()

        with self.assertRaisesRegex(ACTIVATE.ActivationError, "non-success HTTP status"):
            self.apply(self.make_tool(http=http, sleeper=sleeps.append))

        self.assertEqual(http.responses[0].status, 302)
        self.assertEqual(http.readiness_calls, 2)
        self.assertEqual(sleeps, [])
        self.assertTrue(all(response.closed for response in http.responses[:2]))
        self.assertTrue(all(response.read_calls == 0 for response in http.responses[:2]))
        self.assert_baseline(baseline)
        journal = json.loads(next(self.state.glob("transaction-*.json")).read_text())
        self.assertEqual(journal["status"], "rolled_back")

    def test_readiness_exhaustion_rolls_back_exactly_without_public_probe(self):
        clock = ManualClock()
        http = FakeHTTP(readiness=[TimeoutError("booting")] + [503] * 49 + [200])
        baseline = self.baseline()

        def sleeper(seconds):
            clock.value += seconds

        with self.assertRaisesRegex(ACTIVATE.ActivationError, "readiness check timed out"):
            self.apply(self.make_tool(http=http, clock=clock, sleeper=sleeper))

        self.assertEqual(http.readiness_calls, 51)
        self.assertFalse(any(url.startswith("https://") for _, url, _ in http.requests))
        self.assert_baseline(baseline)
        journal = json.loads(next(self.state.glob("transaction-*.json")).read_text())
        self.assertEqual(journal["status"], "rolled_back")

    def test_readiness_deadline_is_bounded_with_fake_clock_and_sleeper(self):
        clock = ManualClock()
        sleeps = []
        http = FakeHTTP(readiness_default=503)

        def sleeper(seconds):
            sleeps.append(seconds)
            clock.value += seconds

        with self.assertRaisesRegex(ACTIVATE.ActivationError, "readiness check timed out"):
            self.make_tool(http=http, clock=clock, sleeper=sleeper)._wait_for_app_readiness()

        self.assertAlmostEqual(clock.value, 105.0)
        self.assertEqual(len(sleeps), 50)
        self.assertTrue(all(0 < delay <= ACTIVATE.READINESS_RETRY_DELAY_SECONDS for delay in sleeps))
        self.assertTrue(
            all(timeout <= ACTIVATE.READINESS_REQUEST_TIMEOUT_SECONDS for _, _, timeout in http.requests)
        )

    def test_public_502_after_readiness_still_rolls_back(self):
        http = FakeHTTP(root_status=502)
        baseline = self.baseline()

        with self.assertRaisesRegex(ACTIVATE.ActivationError, "ordinary root health check failed"):
            self.apply(self.make_tool(http=http))

        loopback_url = ACTIVATE.PRODUCTION_LOOPBACK_ORIGIN + "/"
        loopback_index = next(index for index, request in enumerate(http.requests) if request[1] == loopback_url)
        public_index = next(
            index
            for index, request in enumerate(http.requests)
            if request[1] == ACTIVATE.PRODUCTION_HEALTH_ORIGIN + "/"
        )
        self.assertLess(loopback_index, public_index)
        self.assert_baseline(baseline)
        journal = json.loads(next(self.state.glob("transaction-*.json")).read_text())
        self.assertEqual(journal["status"], "rolled_back")

    def test_apply_faults_restore_exact_baseline_and_required_rollback_order(self):
        phases = (
            "shadow:validate",
            "pins:rechecked",
            "replace:nest-calendar-ics-cloudflare-real-ip.conf",
            "crash-after-replace:nest-calendar-ics-cloudflare-real-ip.conf",
            "replace:nest-calendar-ics-http.conf",
            "crash-after-replace:nest-calendar-ics-http.conf",
            "replace:nest-calendar-ics-feed.conf",
            "crash-after-replace:nest-calendar-ics-feed.conf",
            "replace:nest.apstudy.org",
            "crash-after-replace:nest.apstudy.org",
            "nginx:reload",
            "replace:.env",
            "crash-after-replace:.env",
            "service:restart",
            "health:root",
            "health:invalid",
            "health:rate",
        )
        for fail_phase in phases:
            with self.subTest(fail_phase=fail_phase):
                case = self.fresh_case()
                baseline = case.baseline()
                calls = {}
                order = []

                def hook(phase):
                    order.append(phase)
                    calls[phase] = calls.get(phase, 0) + 1
                    if phase == fail_phase and calls[phase] == 1:
                        raise ACTIVATE.ActivationError("injected phase failure")

                case.tool = case.make_tool(hook=hook)
                with self.assertRaises(ACTIVATE.ActivationError):
                    case.apply()
                case.assert_baseline(baseline)
                journal = json.loads(next(case.state.glob("transaction-*.json")).read_text())
                self.assertEqual(journal["status"], "rolled_back")
                if "crash-after:rollback-site" in order:
                    site_index = order.index("crash-after:rollback-site")
                    for dependency in ("feed", "http", "real_ip"):
                        self.assertLess(site_index, order.index(f"crash-after:rollback-{dependency}"))
                case.tearDown()

    def test_recovery_resumes_after_every_post_replacement_crash(self):
        crash_points = (
            "crash-after:rollback-env",
            "crash-after-replace:.env",
            "crash-after:rollback-app-restart",
            "crash-after:rollback-site",
            "crash-after-replace:nest.apstudy.org",
            "crash-after:rollback-feed",
            "crash-after:rollback-http",
            "crash-after:rollback-real_ip",
            "crash-after:rollback-nginx-test",
            "crash-after:rollback-nginx-reload",
            "crash-after:rollback-stage",
        )
        for crash_point in crash_points:
            with self.subTest(crash_point=crash_point):
                case = self.fresh_case()
                baseline = case.baseline()
                txid = case.prepare_interrupted_activation()
                crashed = {"done": False}

                def hook(phase):
                    if phase == crash_point and not crashed["done"]:
                        crashed["done"] = True
                        raise ACTIVATE.ActivationError("simulated process crash")

                with self.assertRaises(ACTIVATE.ActivationError):
                    case.make_tool(hook=hook).recover(transaction_id=txid)
                case.make_tool().recover(transaction_id=txid)
                case.assert_baseline(baseline)
                journal = json.loads(next(case.state.glob("transaction-*.json")).read_text())
                self.assertEqual(journal["status"], "rolled_back")
                case.make_tool().recover()
                case.tearDown()

    def test_recovery_readiness_failure_is_bounded_and_resumes_idempotently(self):
        case = self.fresh_case()
        baseline = case.baseline()
        txid = case.prepare_interrupted_activation()
        clock = ManualClock()
        http = FakeHTTP(readiness_default=503)

        def sleeper(seconds):
            clock.value += seconds

        with self.assertRaisesRegex(ACTIVATE.ActivationError, "readiness check timed out"):
            case.make_tool(http=http, clock=clock, sleeper=sleeper).recover(transaction_id=txid)

        journal_path = next(case.state.glob("transaction-*.json"))
        journal = json.loads(journal_path.read_text())
        self.assertEqual(journal["status"], "rolling_back")
        self.assertFalse(journal["rollback"].get("app_restarted", False))
        self.assertEqual(http.readiness_calls, 50)

        http.readiness_default = 200
        case.make_tool(http=http).recover(transaction_id=txid)
        case.assert_baseline(baseline)
        journal = json.loads(journal_path.read_text())
        self.assertEqual(journal["status"], "rolled_back")
        case.make_tool(http=http).recover()
        case.tearDown()

    def test_recovery_rejects_malicious_journal_and_symlink_without_touching_victim(self):
        txid = self.prepare_interrupted_activation()
        journal_path = next(self.state.glob("transaction-*.json"))
        journal = json.loads(journal_path.read_text())
        victim = Path(self.temp.name) / "victim"
        victim.write_text("do not touch")
        journal["targets"]["feed"]["path"] = str(victim)
        ACTIVATE.atomic_write(journal_path, ACTIVATE._journal_json(journal))
        with self.assertRaises(ACTIVATE.ActivationError):
            self.tool.recover(transaction_id=txid)
        self.assertEqual(victim.read_text(), "do not touch")
        journal["targets"]["feed"]["path"] = str(self.tool.nginx_targets["feed"])
        ACTIVATE.atomic_write(journal_path, ACTIVATE._journal_json(journal))
        link = self.state / "transaction-20260827T120001Z-111111111111.json"
        link.symlink_to(journal_path)
        with self.assertRaises(ACTIVATE.ActivationError):
            self.tool.recover()

        case = self.fresh_case()
        txid = case.prepare_interrupted_activation()
        feed = case.tool.nginx_targets["feed"]
        feed.unlink()
        feed.symlink_to(victim)
        with self.assertRaises(ACTIVATE.ActivationError):
            case.tool.recover(transaction_id=txid)
        self.assertEqual(victim.read_text(), "do not touch")
        case.tearDown()

    def test_recover_works_after_git_drift_and_second_recover_is_noop(self):
        baseline = self.baseline()
        txid = self.prepare_interrupted_activation()
        self.runner.head = "b" * 40
        self.runner.tracked_status = " M unrelated.py\0"
        before = len(self.runner.calls)
        self.tool.recover(transaction_id=txid)
        self.assert_baseline(baseline)
        self.assertFalse(any(call and call[0] == "git" for call in self.runner.calls[before:]))
        self.tool.recover()

    def test_signal_handler_triggers_rollback_and_restores_previous_handler(self):
        baseline = self.baseline()
        previous = signal.getsignal(signal.SIGTERM)

        def hook(phase):
            if phase == "health:root":
                handler = signal.getsignal(signal.SIGTERM)
                self.assertTrue(callable(handler))
                handler(signal.SIGTERM, None)

        with self.assertRaises(ACTIVATE.ActivationSignal):
            self.apply(self.make_tool(hook=hook))
        self.assert_baseline(baseline)
        self.assertIs(signal.getsignal(signal.SIGTERM), previous)

    def test_redirect_deadline_and_bounded_relevant_log_reads(self):
        with self.assertRaises(ACTIVATE.ActivationError):
            self.make_tool(http=FakeHTTP(root_status=302))._health_checks()
        clock = ManualClock()
        deadline_http = FakeHTTP(advance=clock.advance)
        with self.assertRaisesRegex(ACTIVATE.ActivationError, "deadline"):
            self.make_tool(http=deadline_http, clock=clock)._health_checks()
        self.assertEqual(len(deadline_http.requests), 1)

        sentinel = "sentinel-token"
        (self.logs / "access.log").write_bytes(
            sentinel.encode() + b"x" * (ACTIVATE.LOG_TAIL_LIMIT_BYTES + 10)
        )
        (self.logs / "unrelated.log").write_text(sentinel)
        self.tool._check_logs(sentinel)
        with (self.logs / "access.log").open("ab") as handle:
            handle.write(sentinel.encode())
        with self.assertRaises(ACTIVATE.ActivationError):
            self.tool._check_logs(sentinel)

    def test_command_failures_after_preflight_restore_exact_baseline(self):
        for label in ("shadow-nginx-test", "reload", "restart"):
            with self.subTest(label=label):
                case = self.fresh_case()
                baseline = case.baseline()
                seen = {"match": 0, "nginx": 0}

                def fail(command, _number, label=label):
                    if command[:2] == ("nginx", "-t"):
                        seen["nginx"] += 1
                    match = (
                        (label == "shadow-nginx-test" and command[:2] == ("nginx", "-t") and seen["nginx"] == 2)
                        or (label == "reload" and command[:3] == ("systemctl", "reload", "nginx"))
                        or (label == "restart" and command[:3] == ("systemctl", "restart", "nest"))
                    )
                    if match:
                        seen["match"] += 1
                        return seen["match"] == 1
                    return False

                runner = FakeRunner(case.root, fail=fail)
                case.runner = runner
                case.tool = case.make_tool(runner=runner)
                with self.assertRaises(ACTIVATE.ActivationError):
                    case.apply()
                case.assert_baseline(baseline)
                case.tearDown()

    def test_source_never_uses_shell_or_mutates_protected_configuration_contracts(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("shell=True", source)
        self.assertNotIn("DATABASE_PATH", source)
        self.assertNotIn("sqlite", source.lower())
        candidate, _ = ACTIVATE._candidate_env(self.env.read_bytes(), "*")
        for key in (
            b"APP_BASE_URL=https://example.test",
            b"APSTUDY_EXTENSION_CALENDAR_ROLLOUT=readonly-v1",
        ):
            self.assertEqual(candidate.count(key), 1)


if __name__ == "__main__":
    unittest.main()
