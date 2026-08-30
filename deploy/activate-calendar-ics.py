#!/usr/bin/env python3
"""Safely activate the Nest calendar ICS subscription service.

This file is deliberately standalone: it is copied/run from the verified
checkout on the VPS and uses only the Python standard library.  It treats the
environment file and Nginx configuration as one transaction.  A durable
journal makes an interrupted transaction recoverable after a process or host
failure; ordinary failures and signals roll back synchronously.

The command does not create calendar shares or touch the application database.
It only enables the already-implemented, token-backed ICS service and installs
the reviewed Nginx route.
"""

from __future__ import annotations

import argparse
import contextlib
import errno
import fcntl
import glob
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable, Iterable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


TOOL_VERSION = 1
PRODUCTION_ROOT = Path("/var/www/nest.apstudy.org")
PRODUCTION_ENV = PRODUCTION_ROOT / ".env"
PRODUCTION_NGINX_SITE = Path("/etc/nginx/sites-available/nest.apstudy.org")
PRODUCTION_NGINX_MAIN = Path("/etc/nginx/nginx.conf")
PRODUCTION_NGINX_CONF_DIR = Path("/etc/nginx/conf.d")
PRODUCTION_NGINX_SNIPPETS_DIR = Path("/etc/nginx/snippets")
PRODUCTION_STATE_DIR = Path("/var/lib/nest-calendar-ics-activation")
PRODUCTION_LOCK = Path("/run/lock/nest-calendar-ics-activation.lock")
PRODUCTION_NGINX_LOG_DIR = Path("/var/log/nginx")
PRODUCTION_HEALTH_ORIGIN = "https://nest.apstudy.org"
PRODUCTION_LOOPBACK_ORIGIN = "http://127.0.0.1:8000"
PRODUCTION_LOOPBACK_HOST = "nest.apstudy.org"
PRODUCTION_APP_SERVICE = "nest"
PRODUCTION_NGINX_SERVICE = "nginx"
READINESS_TOTAL_TIMEOUT_SECONDS = 5.0
READINESS_REQUEST_TIMEOUT_SECONDS = 0.5
READINESS_RETRY_DELAY_SECONDS = 0.1
HEALTH_TOTAL_TIMEOUT_SECONDS = 30.0
HEALTH_REQUEST_TIMEOUT_SECONDS = 3.0
HEALTH_RESPONSE_LIMIT_BYTES = 1024 * 1024
LOG_TAIL_LIMIT_BYTES = 1024 * 1024
RELEVANT_NGINX_LOGS = (
    "nest-calendar-ics-access.log",
    "nest-calendar-ics-error.log",
    "access.log",
    "error.log",
)
NGINX_ICS_MARKERS = (
    "nest-calendar-ics",
    "nest_calendar_ics",
    "/api/calendar/share-feed.ics",
)
ENV_KEYS = (
    "CALENDAR_ICS_SUBSCRIPTIONS_ENABLED",
    "CALENDAR_ICS_SUBSCRIPTIONS_OWNER_ALLOWLIST",
    "CALENDAR_ICS_UID_SECRET",
)
LEGACY_ENV_KEYS = ("CALENDAR_ICS_OWNER_ALLOWLIST", "CALENDAR_ICS_ALLOWLIST")
PROTECTED_ENV_KEYS = frozenset((*ENV_KEYS, *LEGACY_ENV_KEYS))
TERMINAL_STATUSES = frozenset(("committed", "rolled_back"))
HEX_64 = re.compile(r"^[0-9a-f]{64}$")
ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
TRANSACTION_ID = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}$")
GIT_HEAD = re.compile(r"^[0-9a-f]{40}$")


class ActivationError(RuntimeError):
    """A safe, user-facing activation failure."""


class ActivationSignal(ActivationError):
    """Raised by an installed signal handler so the transaction can unwind."""


class NoRedirectHandler(HTTPRedirectHandler):
    """Return redirect responses to the caller instead of following them."""

    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        del request, file_pointer, code, message, headers, new_url
        return None


class CommandRunner:
    """Small subprocess boundary; never interprets a command through a shell."""

    def __init__(self, timeout: float = 20.0):
        self.timeout = timeout

    def run(self, argv: Iterable[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
        command = tuple(str(part) for part in argv)
        try:
            result = subprocess.run(
                command,
                check=False,
                shell=False,
                timeout=self.timeout,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ActivationError(f"command failed safely: {command[0] if command else 'empty command'}") from exc
        if check and result.returncode != 0:
            raise ActivationError(f"command returned {result.returncode}: {command[0] if command else 'empty command'}")
        return result


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fsync_directory(path: Path) -> None:
    """Persist directory entries when the platform permits directory fsync."""

    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError as exc:
        if exc.errno in (errno.EINVAL, errno.ENOTSUP, errno.EPERM):
            return
        raise
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def fsync_file(path: Path) -> None:
    with path.open("rb") as handle:
        os.fsync(handle.fileno())


def write_bytes_fsync(path: Path, content: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        os.fchmod(descriptor, mode)
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    fsync_directory(path.parent)


def atomic_write(path: Path, content: bytes, *, mode: int = 0o600) -> None:
    """Atomically write a file using a temporary file in the same directory."""

    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, raw_temp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp = Path(raw_temp)
    try:
        os.fchmod(descriptor, mode)
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temp, path)
        fsync_directory(path.parent)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        with contextlib.suppress(FileNotFoundError):
            temp.unlink()


def assert_no_symlink_components(path: Path, *, allow_missing_leaf: bool = True) -> None:
    """Reject symlinks in every existing component of a security-sensitive path."""

    absolute = path if path.is_absolute() else path.absolute()
    parts = absolute.parts
    current = Path(parts[0])
    for index, part in enumerate(parts[1:], start=1):
        current = current / part
        is_leaf = index == len(parts) - 1
        try:
            info = current.lstat()
        except FileNotFoundError:
            if is_leaf and allow_missing_leaf:
                return
            # A missing parent is allowed only when later code will create it;
            # all existing ancestors have already been verified.
            return
        if stat.S_ISLNK(info.st_mode):
            raise ActivationError(f"symlinked path component is not allowed: {current}")


def read_bounded_tail(path: Path, limit: int = LOG_TAIL_LIMIT_BYTES) -> bytes:
    with path.open("rb") as handle:
        size = path.stat().st_size
        if size > limit:
            handle.seek(size - limit)
        return handle.read(limit)


def _safe_rel(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _metadata(path: Path) -> dict[str, Any]:
    info = path.stat()
    return {
        "path": str(path),
        "existed": True,
        "sha256": sha256_file(path),
        "mode": stat.S_IMODE(info.st_mode),
        "uid": info.st_uid,
        "gid": info.st_gid,
        "size": info.st_size,
    }


def _missing_metadata(path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "existed": False,
        "sha256": None,
        "mode": None,
        "uid": None,
        "gid": None,
        "size": None,
    }


def _strip_nginx_comments(text: str) -> str:
    output: list[str] = []
    quoted = False
    escaped = False
    comment = False
    for char in text:
        if comment:
            if char == "\n":
                comment = False
                output.append(char)
            else:
                output.append(" ")
            continue
        if quoted:
            output.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
            continue
        if char == '"':
            quoted = True
            output.append(char)
        elif char == "#":
            comment = True
            output.append(" ")
        else:
            output.append(char)
    return "".join(output)


def _brace_blocks(text: str, keyword: str) -> list[tuple[int, int, int]]:
    """Return (keyword start, opening brace, closing brace) for top-level blocks."""

    clean = _strip_nginx_comments(text)
    blocks: list[tuple[int, int, int]] = []
    for match in re.finditer(rf"\b{re.escape(keyword)}\s*\{{", clean):
        brace = clean.find("{", match.start(), match.end())
        depth = 0
        quoted = False
        escaped = False
        close = None
        for index in range(brace, len(clean)):
            char = clean[index]
            if quoted:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    quoted = False
            elif char == '"':
                quoted = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    close = index
                    break
        if close is None:
            raise ActivationError(f"unterminated nginx {keyword} block")
        blocks.append((match.start(), brace, close))
    return blocks


def _site_with_feed_include(site: bytes, include_path: str) -> bytes:
    try:
        text = site.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ActivationError("Nginx site is not valid UTF-8") from exc
    clean = _strip_nginx_comments(text)
    if "nest-calendar-ics-feed" in text or "share-feed.ics" in text:
        raise ActivationError("Nginx site already contains an ICS reference")
    servers = _brace_blocks(text, "server")
    https = []
    for start, brace, close in servers:
        block = clean[brace + 1 : close]
        if re.search(r"\blisten\s+(?:\[[^]]+\]:)?443\b", block):
            https.append((start, brace, close, block))
    if len(https) != 1:
        raise ActivationError(f"expected exactly one HTTPS server block, found {len(https)}")
    start, brace, close, block = https[0]
    locations = list(re.finditer(r"\blocation\s+/\s*\{", clean[brace + 1 : close]))
    if len(locations) != 1:
        raise ActivationError(f"expected exactly one HTTPS catch-all location, found {len(locations)}")
    location = locations[0]
    absolute = brace + 1 + location.start()
    line_start = text.rfind("\n", 0, absolute) + 1
    prefix = text[line_start:absolute]
    indentation = re.match(r"[ \t]*", prefix).group(0)
    if prefix.strip():
        # Content (for example "server { listen 443;") precedes the location
        # on the same line.  Keep that prefix and put the include on its own
        # line so it stays inside the selected server block.
        include = f"{prefix}\n{indentation}include {include_path};\n"
        return (text[:line_start] + include + text[absolute:]).encode("utf-8")
    include = f"{indentation}include {include_path};\n"
    return (text[:line_start] + include + text[line_start:]).encode("utf-8")


def _validate_env_base(content: bytes) -> dict[str, str]:
    """Parse without rewriting the file and reject ambiguous/malformed states."""

    values: dict[str, str] = {}
    for raw_line in content.splitlines():
        try:
            line = raw_line.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ActivationError(".env is not valid UTF-8") from exc
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in line:
            raise ActivationError(".env contains a malformed non-comment line")
        key, value = line.split("=", 1)
        if key.startswith("export "):
            key = key[7:]
        if not ENV_NAME.fullmatch(key) or key in values:
            raise ActivationError(".env contains an invalid or duplicate key")
        values[key] = value
    collisions = PROTECTED_ENV_KEYS.intersection(values)
    if collisions:
        raise ActivationError(".env already contains an ICS setting or legacy alias")
    return values


def _validate_allowlist(value: str) -> str:
    if not value or value != value.strip() or "\n" in value or "\r" in value:
        raise ActivationError("owner allowlist must be a non-empty, newline-free value")
    entries = [item.strip() for item in value.split(",")]
    if not all(entries) or any(any(char.isspace() for char in item) for item in entries):
        raise ActivationError("owner allowlist contains an empty or whitespace-bearing entry")
    if "*" in entries and len(entries) != 1:
        raise ActivationError("wildcard owner allowlist cannot be mixed with owner IDs")
    if any("=" in item or "#" in item for item in entries):
        raise ActivationError("owner allowlist contains unsafe .env syntax")
    return ",".join(entries)


def _candidate_env(base: bytes, owner_allowlist: str) -> tuple[bytes, str]:
    _validate_env_base(base)
    normalized_allowlist = _validate_allowlist(owner_allowlist)
    uid_secret = secrets.token_bytes(32).hex()
    newline = b"\r\n" if base.count(b"\r\n") > base.count(b"\n") // 2 else b"\n"
    separator = b"" if not base or base.endswith((b"\n", b"\r")) else newline
    additions = newline.join(
        (
            b"CALENDAR_ICS_SUBSCRIPTIONS_ENABLED=1",
            f"CALENDAR_ICS_SUBSCRIPTIONS_OWNER_ALLOWLIST={normalized_allowlist}".encode(),
            f"CALENDAR_ICS_UID_SECRET={uid_secret}".encode(),
        )
    )
    return base + separator + additions + newline, uid_secret


def _journal_json(journal: Mapping[str, Any]) -> bytes:
    return (json.dumps(journal, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


class ActivationTool:
    """Transactional implementation with injectable command runner for tests."""

    def __init__(
        self,
        *,
        root: Path = PRODUCTION_ROOT,
        env_path: Path | None = None,
        nginx_site: Path = PRODUCTION_NGINX_SITE,
        nginx_main_conf: Path = PRODUCTION_NGINX_MAIN,
        nginx_conf_dir: Path = PRODUCTION_NGINX_CONF_DIR,
        nginx_snippets_dir: Path = PRODUCTION_NGINX_SNIPPETS_DIR,
        state_dir: Path = PRODUCTION_STATE_DIR,
        lock_path: Path = PRODUCTION_LOCK,
        runner: CommandRunner | None = None,
        service: str = PRODUCTION_APP_SERVICE,
        nginx_service: str = PRODUCTION_NGINX_SERVICE,
        nginx_log_dir: Path = PRODUCTION_NGINX_LOG_DIR,
        http_open: Callable[..., Any] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        hook: Callable[[str], None] | None = None,
    ):
        self.root = root
        self.env_path = env_path or root / ".env"
        self.nginx_site = nginx_site
        self.nginx_main_conf = nginx_main_conf
        self.nginx_conf_dir = nginx_conf_dir
        self.nginx_snippets_dir = nginx_snippets_dir
        self.state_dir = state_dir
        self.lock_path = lock_path
        self.runner = runner or CommandRunner()
        self.service = service
        self.nginx_service = nginx_service
        self.nginx_log_dir = nginx_log_dir
        self.http_open = http_open or build_opener(NoRedirectHandler()).open
        self.clock = clock
        self.sleeper = sleeper
        self.hook = hook
        self._lock_handle = None
        self._journal: dict[str, Any] | None = None
        self._journal_path: Path | None = None
        self._rollback_running = False
        self._committed = False

    @property
    def nginx_targets(self) -> dict[str, Path]:
        return {
            "real_ip": self.nginx_conf_dir / "nest-calendar-ics-cloudflare-real-ip.conf",
            "http": self.nginx_conf_dir / "nest-calendar-ics-http.conf",
            "feed": self.nginx_snippets_dir / "nest-calendar-ics-feed.conf",
        }

    def _lock(self, *, create: bool = True) -> None:
        assert_no_symlink_components(self.lock_path, allow_missing_leaf=True)
        if create:
            self.lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
            assert_no_symlink_components(self.lock_path.parent, allow_missing_leaf=False)
            flags = os.O_RDWR | os.O_CREAT
        elif not self.lock_path.exists():
            # ``--check`` is strictly read-only.  It cannot create a lock
            # inode; the preflight still remains safe because it makes no
            # writes or service changes.
            self._lock_handle = None
            return
        else:
            flags = os.O_RDONLY
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(self.lock_path, flags, 0o600)
        self._lock_handle = os.fdopen(descriptor, "r+" if create else "r")
        try:
            fcntl.flock(self._lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self._lock_handle.close()
            self._lock_handle = None
            if exc.errno in (errno.EACCES, errno.EAGAIN):
                raise ActivationError("another calendar ICS activation is running") from exc
            raise

    def _unlock(self) -> None:
        if self._lock_handle is not None:
            fcntl.flock(self._lock_handle.fileno(), fcntl.LOCK_UN)
            self._lock_handle.close()
            self._lock_handle = None

    def _call_hook(self, phase: str) -> None:
        if self.hook:
            self.hook(phase)

    def _journals(self) -> list[tuple[Path, dict[str, Any]]]:
        if not self.state_dir.exists():
            return []
        assert_no_symlink_components(self.state_dir, allow_missing_leaf=False)
        result = []
        for path in sorted(self.state_dir.glob("transaction-*.json")):
            assert_no_symlink_components(path, allow_missing_leaf=False)
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise ActivationError("activation state contains an unreadable journal") from exc
            if not isinstance(data, dict) or data.get("version") != TOOL_VERSION:
                raise ActivationError("activation state contains an unsupported journal")
            result.append((path, data))
        return result

    def _incomplete(self) -> list[tuple[Path, dict[str, Any]]]:
        return [(path, data) for path, data in self._journals() if data.get("status") not in TERMINAL_STATUSES]

    def _assert_no_incomplete(self) -> None:
        incomplete = self._incomplete()
        if incomplete:
            names = ", ".join(data.get("transaction_id", path.stem) for path, data in incomplete)
            raise ActivationError(f"incomplete transaction exists; run --recover first ({names})")

    def _validate_recovery_journal(self, journal_path: Path, journal: Mapping[str, Any]) -> None:
        txid = journal.get("transaction_id")
        if not isinstance(txid, str) or not TRANSACTION_ID.fullmatch(txid):
            raise ActivationError("transaction journal has an invalid transaction ID")
        if journal_path.name != f"transaction-{txid}.json":
            raise ActivationError("transaction journal filename does not match its ID")
        if (
            journal.get("root") != str(self.root)
            or journal.get("env_path") != str(self.env_path)
            or journal.get("nginx_site") != str(self.nginx_site)
        ):
            raise ActivationError("transaction journal application paths do not match this invocation")
        tx_root = self.state_dir / txid
        if journal.get("stage") != str(tx_root / "stage") or journal.get("backup") != str(tx_root / "backup"):
            raise ActivationError("transaction journal state paths are outside the expected transaction directory")
        expected = {"env": self.env_path, "site": self.nginx_site, **self.nginx_targets}
        targets = journal.get("targets")
        if not isinstance(targets, dict) or set(targets) != set(expected):
            raise ActivationError("transaction journal target set is invalid")
        for name, target in expected.items():
            record = targets.get(name)
            if not isinstance(record, dict) or record.get("path") != str(target):
                raise ActivationError("transaction journal target path is invalid")
            assert_no_symlink_components(target, allow_missing_leaf=True)
            existed = record.get("existed")
            if type(existed) is not bool:
                raise ActivationError("transaction journal target existence state is invalid")
            backup = record.get("backup")
            expected_backup = tx_root / "backup" / name
            if existed:
                if (
                    backup != str(expected_backup)
                    or not HEX_64.fullmatch(str(record.get("sha256", "")))
                    or type(record.get("mode")) is not int
                    or type(record.get("uid")) is not int
                    or type(record.get("gid")) is not int
                ):
                    raise ActivationError("transaction journal backup metadata is invalid")
                assert_no_symlink_components(expected_backup, allow_missing_leaf=False)
                if not expected_backup.is_file() or sha256_file(expected_backup) != record["sha256"]:
                    raise ActivationError("transaction journal backup content is invalid")
            elif backup is not None:
                raise ActivationError("missing target has an unexpected backup")
        stage = tx_root / "stage"
        if stage.exists():
            assert_no_symlink_components(stage, allow_missing_leaf=False)

    def _run(self, *argv: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return self.runner.run(argv, check=check)

    def _active(self, service: str) -> None:
        self._run("systemctl", "is-active", "--quiet", service)

    def _nginx_test(self, config: str | None = None) -> None:
        args = ["nginx", "-t"]
        if config:
            args.extend(("-c", config))
        self._run(*args)

    @staticmethod
    def _contains_ics_nginx_marker(text: str) -> bool:
        lowered = text.lower()
        return any(marker in lowered for marker in NGINX_ICS_MARKERS)

    def _assert_effective_nginx_has_no_ics(self) -> None:
        # nginx -T traverses the effective include graph, including enabled
        # sibling sites and nested includes that a directory scan can miss.
        # CommandRunner captures both streams with a timeout; do not include
        # either stream in errors because configuration may contain secrets.
        result = self._run("nginx", "-T")
        if self._contains_ics_nginx_marker(result.stdout) or self._contains_ics_nginx_marker(result.stderr):
            raise ActivationError("effective Nginx topology already contains a calendar ICS marker")

    def _scan_ics_references(self) -> list[Path]:
        roots = [self.nginx_main_conf, self.nginx_site, self.nginx_conf_dir, self.nginx_snippets_dir]
        references: list[Path] = []
        for root in roots:
            paths = [root] if root.is_file() else sorted(root.glob("*.conf")) if root.exists() else []
            for path in paths:
                if path in self.nginx_targets.values():
                    continue
                try:
                    text = path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError) as exc:
                    raise ActivationError(f"cannot inspect Nginx file {path}") from exc
                if self._contains_ics_nginx_marker(text):
                    references.append(path)
        return references

    @staticmethod
    def _allowed_untracked_path(path: str) -> bool:
        if not path or path.startswith("/") or any(ord(char) < 32 for char in path):
            return False
        parts = path.split("/")
        return len(parts) > 1 and parts[0] == "actions-runner" and all(
            part not in ("", ".", "..") for part in parts[1:]
        )

    @classmethod
    def _assert_clean_git_status(cls, raw_status: str) -> None:
        if not raw_status:
            return
        records = raw_status.split("\0")
        if records[-1] != "":
            raise ActivationError("application checkout returned malformed Git status")
        for record in records[:-1]:
            if len(record) < 4 or record[2] != " ":
                raise ActivationError("application checkout returned malformed Git status")
            status, path = record[:2], record[3:]
            if status != "??":
                raise ActivationError("application checkout has tracked Git changes")
            if not cls._allowed_untracked_path(path):
                raise ActivationError("application checkout has an unexpected untracked path")

    @staticmethod
    def _require_pins(expected_head: str | None, expected_env: str | None, expected_site: str | None) -> tuple[str, str, str]:
        if not expected_head or not GIT_HEAD.fullmatch(expected_head):
            raise ActivationError("expected Git HEAD must be an exact 40-character lowercase hexadecimal commit")
        if not expected_env or not HEX_64.fullmatch(expected_env):
            raise ActivationError("expected .env SHA-256 must be an exact lowercase hexadecimal digest")
        if not expected_site or not HEX_64.fullmatch(expected_site):
            raise ActivationError("expected Nginx site SHA-256 must be an exact lowercase hexadecimal digest")
        return expected_head, expected_env, expected_site

    def _assert_secure_paths(self) -> None:
        for path, allow_missing in (
            (self.root, False),
            (self.env_path, False),
            (self.nginx_main_conf, False),
            (self.nginx_site, False),
            (self.nginx_conf_dir, False),
            (self.nginx_snippets_dir, False),
            (self.state_dir, True),
            (self.lock_path, True),
            (self.nginx_log_dir, False),
            *((path, True) for path in self.nginx_targets.values()),
        ):
            assert_no_symlink_components(path, allow_missing_leaf=allow_missing)

    def _validate_pins(self, expected_head: str, expected_env: str, expected_site: str) -> dict[str, str]:
        actual_head = self._run("git", "-C", str(self.root), "rev-parse", "HEAD").stdout.strip()
        if actual_head != expected_head:
            raise ActivationError("application checkout HEAD does not match expected HEAD")
        git_status = self._run(
            "git", "-C", str(self.root), "status", "--porcelain=v1", "-z", "--untracked-files=all"
        ).stdout
        self._assert_clean_git_status(git_status)
        env_hash = sha256_file(self.env_path)
        site_hash = sha256_file(self.nginx_site)
        if env_hash != expected_env:
            raise ActivationError(".env SHA-256 does not match expected baseline")
        if site_hash != expected_site:
            raise ActivationError("Nginx site SHA-256 does not match expected baseline")
        return {"head": actual_head, "env_sha256": env_hash, "site_sha256": site_hash}

    def _preflight(self, expected_head: str, expected_env: str, expected_site: str) -> dict[str, Any]:
        expected_head, expected_env, expected_site = self._require_pins(expected_head, expected_env, expected_site)
        if not self.root.is_dir() or not self.env_path.is_file() or not self.nginx_site.is_file():
            raise ActivationError("required application, .env, or Nginx site path is missing")
        self._assert_secure_paths()
        origin = urlsplit(PRODUCTION_HEALTH_ORIGIN)
        if origin.scheme != "https" or origin.hostname != "nest.apstudy.org" or origin.username or origin.password:
            raise ActivationError("audited health origin is invalid")
        validated = self._validate_pins(expected_head, expected_env, expected_site)
        env_values = _validate_env_base(self.env_path.read_bytes())
        if "APSTUDY_EXTENSION_CALENDAR_ROLLOUT" in env_values:
            # It is allowed to exist, but activation must not alter it.  The
            # candidate is built from the exact original bytes below.
            pass
        self._active(self.nginx_service)
        self._active(self.service)
        self._nginx_test()
        self._assert_effective_nginx_has_no_ics()
        references = self._scan_ics_references()
        if references:
            raise ActivationError("existing Nginx ICS references make installation ambiguous")
        existing = [path for path in self.nginx_targets.values() if path.exists()]
        if existing:
            raise ActivationError("an ICS Nginx target already exists")
        return {
            **validated,
            "rollout": env_values.get("APSTUDY_EXTENSION_CALENDAR_ROLLOUT"),
        }

    def check(self, *, expected_head: str, expected_env: str, expected_site: str) -> None:
        self._lock(create=False)
        try:
            self._assert_no_incomplete()
            self._preflight(expected_head, expected_env, expected_site)
        finally:
            self._unlock()

    def _make_backup(self, txid: str, targets: Mapping[str, Path]) -> tuple[Path, dict[str, Any]]:
        assert_no_symlink_components(self.state_dir, allow_missing_leaf=True)
        backup = self.state_dir / txid / "backup"
        backup.mkdir(parents=True, exist_ok=False, mode=0o700)
        assert_no_symlink_components(backup, allow_missing_leaf=False)
        manifest: dict[str, Any] = {}
        for name, path in targets.items():
            assert_no_symlink_components(path, allow_missing_leaf=True)
            record = _metadata(path) if path.exists() else _missing_metadata(path)
            record["backup"] = None
            if record["existed"]:
                backup_file = backup / name
                shutil.copyfile(path, backup_file)
                os.chmod(backup_file, 0o600)
                fsync_file(backup_file)
                record["backup"] = str(backup_file)
            manifest[name] = record
        manifest_path = backup / "manifest.json"
        atomic_write(manifest_path, _journal_json({"version": TOOL_VERSION, "targets": manifest}), mode=0o600)
        os.chmod(backup, 0o700)
        return backup, manifest

    def _stage_dir(self, txid: str) -> Path:
        stage = self.state_dir / txid / "stage"
        stage.mkdir(parents=True, exist_ok=False, mode=0o700)
        assert_no_symlink_components(stage, allow_missing_leaf=False)
        return stage

    def _stage_contents(self, stage: Path, candidate_env: bytes, candidate_site: bytes) -> dict[str, str]:
        contents = {
            "env": candidate_env,
            "site": candidate_site,
            "real_ip": Path(__file__).with_name("nginx-calendar-ics-cloudflare-real-ip.conf").read_bytes(),
            "http": Path(__file__).with_name("nginx-calendar-ics-http.conf").read_bytes(),
            "feed": Path(__file__).with_name("nginx-calendar-ics-feed.snippet.conf").read_bytes(),
        }
        paths: dict[str, str] = {}
        for name, content in contents.items():
            path = stage / name
            write_bytes_fsync(path, content, 0o600 if name == "env" else 0o644)
            paths[name] = str(path)
        return paths

    def _stage(self, txid: str, base_env: bytes, candidate_env: bytes, candidate_site: bytes) -> tuple[Path, dict[str, str]]:
        # Kept as a compact helper for callers/tests that need a fully staged
        # candidate.  ``apply`` journals the stage directory before writing
        # its secret-bearing env candidate so a host crash remains recoverable.
        del base_env
        stage = self._stage_dir(txid)
        return stage, self._stage_contents(stage, candidate_env, candidate_site)

    def _rewrite_shadow_includes(
        self,
        text: str,
        stage: Path,
        copied: dict[str, Path],
        shadow_feed: Path,
    ) -> str:
        include_pattern = re.compile(
            r"^(?P<indent>[ \t]*)include[ \t]+(?P<path>/[^;]+);[ \t]*(?:#.*)?$",
            re.MULTILINE,
        )
        live_feed = str(self.nginx_targets["feed"])

        def replace(match: re.Match[str]) -> str:
            raw_path = match.group("path").strip().strip('"\'')
            indent = match.group("indent")
            if raw_path == live_feed:
                return f"{indent}include {shadow_feed};"
            matches = [Path(value) for value in sorted(glob.glob(raw_path))]
            if not matches:
                raise ActivationError(f"absolute Nginx include cannot be staged: {raw_path}")
            redirected = []
            include_dir = stage / "shadow-includes"
            include_dir.mkdir(exist_ok=True, mode=0o700)
            for source in matches:
                if not source.is_file():
                    raise ActivationError(f"Nginx include is not a regular file: {source}")
                key = str(source.resolve())
                destination = copied.get(key)
                if destination is None:
                    suffix = hashlib.sha256(key.encode()).hexdigest()[:16]
                    destination = include_dir / f"{suffix}-{source.name}"
                    copied[key] = destination
                    try:
                        nested = source.read_text(encoding="utf-8")
                    except (OSError, UnicodeDecodeError) as exc:
                        raise ActivationError(f"cannot stage Nginx include: {source}") from exc
                    rewritten = self._rewrite_shadow_includes(nested, stage, copied, shadow_feed)
                    write_bytes_fsync(destination, rewritten.encode("utf-8"), 0o644)
                redirected.append(f"{indent}include {destination};")
            return "\n".join(redirected)

        rewritten = include_pattern.sub(replace, text)
        # Fail closed if unusual formatting kept an absolute include pointed
        # at the live filesystem. Every absolute include in the shadow tree
        # must resolve beneath the transaction stage directory.
        for match in re.finditer(r"\binclude[ \t]+(?P<path>[^;]+);", rewritten):
            raw_path = match.group("path").strip().strip('"\'')
            if raw_path.startswith("/") and not _safe_rel(Path(raw_path), stage):
                raise ActivationError(f"absolute Nginx include was not redirected to stage: {raw_path}")
        return rewritten

    @staticmethod
    def _redirect_shadow_logs(text: str, stage: Path) -> str:
        pattern = re.compile(r"\b(?P<directive>access_log|error_log)[ \t]+(?P<path>/[^; \t]+)")

        def replace(match: re.Match[str]) -> str:
            suffix = "access.log" if match.group("directive") == "access_log" else "error.log"
            return f"{match.group('directive')} {stage / ('shadow-' + suffix)}"

        return pattern.sub(replace, text)

    def _build_shadow_nginx(self, stage: Path) -> Path:
        candidate_site_path = stage / "site"
        try:
            candidate_site = candidate_site_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise ActivationError("staged candidate Nginx site is unreadable") from exc
        try:
            feed = (stage / "feed").read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise ActivationError("staged ICS Nginx snippet is unreadable") from exc
        shadow_feed = stage / "shadow-feed.conf"
        write_bytes_fsync(
            shadow_feed,
            self._redirect_shadow_logs(feed, stage).encode("utf-8"),
            0o600,
        )
        shadow_site = stage / "shadow-site.conf"
        redirected_site = self._rewrite_shadow_includes(candidate_site, stage, {}, shadow_feed)
        redirected_site = self._redirect_shadow_logs(redirected_site, stage)
        if str(self.nginx_targets["feed"]) in redirected_site:
            raise ActivationError("shadow Nginx site still references the live ICS snippet")
        write_bytes_fsync(shadow_site, redirected_site.encode("utf-8"), 0o600)

        shadow = stage / "nginx.conf"
        mime = Path("/etc/nginx/mime.types")
        include_mime = f'include {mime};\n' if mime.exists() else ""
        content = (
            "pid " + str(stage / "nginx.pid") + ";\n"
            "error_log stderr;\n"
            "events {}\n"
            "http {\n"
            + include_mime
            + f"include {stage / 'real_ip'};\n"
            + f"include {stage / 'http'};\n"
            + f"include {shadow_site};\n"
            + "}\n"
        ).encode()
        write_bytes_fsync(shadow, content, 0o600)
        return shadow

    def _shadow_nginx(self, stage: Path) -> None:
        shadow = self._build_shadow_nginx(stage)
        self._run("nginx", "-t", "-c", str(shadow), "-p", str(stage))

    def _install_file(self, source: Path, target: Path, *, mode: int = 0o644, metadata: Mapping[str, Any] | None = None) -> None:
        assert_no_symlink_components(source, allow_missing_leaf=False)
        assert_no_symlink_components(target, allow_missing_leaf=True)
        self._call_hook(f"replace:{target.name}")
        descriptor, raw_temp = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
        temp = Path(raw_temp)
        try:
            with source.open("rb") as source_handle:
                while True:
                    chunk = source_handle.read(1024 * 1024)
                    if not chunk:
                        break
                    os.write(descriptor, chunk)
            os.fchmod(descriptor, mode)
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = -1
            os.replace(temp, target)
            # Fault injection at the exact irreversible namespace boundary.
            # A crash here must be recoverable even before metadata/fsync and
            # before the caller advances its durable phase marker.
            self._call_hook(f"crash-after-replace:{target.name}")
            if metadata and metadata.get("existed"):
                self._restore_metadata(target, metadata)
            fsync_directory(target.parent)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            with contextlib.suppress(FileNotFoundError):
                temp.unlink()

    def _restore_metadata(self, path: Path, metadata: Mapping[str, Any]) -> None:
        os.chmod(path, int(metadata["mode"]))
        try:
            os.chown(path, int(metadata["uid"]), int(metadata["gid"]))
        except PermissionError:
            if os.geteuid() == 0:
                raise

    def _replace_env(self, source: Path, metadata: Mapping[str, Any]) -> None:
        self._install_file(source, self.env_path, mode=int(metadata.get("mode") or 0o640), metadata=metadata)

    def _reload_nginx(self) -> None:
        self._call_hook("nginx:reload")
        self._run("systemctl", "reload", self.nginx_service)

    def _restart_app(self) -> None:
        self._call_hook("service:restart")
        self._run("systemctl", "restart", self.service)

    def _wait_for_app_readiness(self) -> None:
        """Wait for the restarted app, without sending traffic through Nginx."""

        deadline = self.clock() + READINESS_TOTAL_TIMEOUT_SECONDS
        url = PRODUCTION_LOOPBACK_ORIGIN + "/"
        while True:
            remaining = deadline - self.clock()
            if remaining <= 0:
                raise ActivationError("Nest application readiness check timed out")
            request = Request(
                url,
                method="GET",
                headers={
                    "Host": PRODUCTION_LOOPBACK_HOST,
                    "User-Agent": "Nest-ICS-activation/1",
                },
            )
            status: int | None = None
            try:
                response = self.http_open(
                    request,
                    timeout=min(READINESS_REQUEST_TIMEOUT_SECONDS, remaining),
                )
                try:
                    status = response.status
                finally:
                    response.close()
            except HTTPError as exc:
                try:
                    status = exc.code
                finally:
                    exc.close()
            except (URLError, TimeoutError, OSError):
                pass
            if self.clock() >= deadline:
                raise ActivationError("Nest application readiness check timed out")
            if status == 200:
                return
            if status is not None and not 500 <= status <= 599:
                raise ActivationError("Nest application readiness check returned a non-success HTTP status")
            remaining = deadline - self.clock()
            if remaining <= 0:
                raise ActivationError("Nest application readiness check timed out")
            if remaining <= READINESS_RETRY_DELAY_SECONDS + 1e-9:
                self.sleeper(min(READINESS_RETRY_DELAY_SECONDS, remaining))
                raise ActivationError("Nest application readiness check timed out")
            self.sleeper(READINESS_RETRY_DELAY_SECONDS)

    def _restart_and_verify_app(self) -> None:
        self._restart_app()
        self._active(self.service)
        self._wait_for_app_readiness()

    def _health_request(self, method: str, url: str, deadline: float) -> tuple[int, Mapping[str, str], bytes]:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname != "nest.apstudy.org" or parsed.username or parsed.password:
            raise ActivationError("health request escaped the audited HTTPS origin")
        remaining = deadline - self.clock()
        if remaining <= 0:
            raise ActivationError("calendar ICS health deadline exceeded")
        timeout = min(HEALTH_REQUEST_TIMEOUT_SECONDS, remaining)
        request = Request(url, method=method, headers={"User-Agent": "Nest-ICS-activation/1"})
        try:
            with self.http_open(request, timeout=timeout) as response:
                result = response.status, response.headers, response.read(HEALTH_RESPONSE_LIMIT_BYTES)
        except HTTPError as exc:
            result = exc.code, exc.headers, exc.read(HEALTH_RESPONSE_LIMIT_BYTES)
        except (URLError, TimeoutError, OSError) as exc:
            raise ActivationError("calendar ICS HTTP health check failed") from exc
        if self.clock() > deadline:
            raise ActivationError("calendar ICS health deadline exceeded")
        return result

    def _assert_headers(self, headers: Mapping[str, str], *, rate_limited: bool = False) -> None:
        cache = headers.get("Cache-Control", "").lower()
        if "no-store" not in cache or "private" not in cache or "no-transform" not in cache:
            raise ActivationError("ICS response is missing no-store security cache headers")
        if headers.get("X-Content-Type-Options", "").lower() != "nosniff":
            raise ActivationError("ICS response is missing nosniff")
        if "Content-Disposition" in headers:
            raise ActivationError("ICS response exposes an attachment disposition")
        if rate_limited and headers.get("Retry-After") != "60":
            raise ActivationError("ICS rate-limit response is missing Retry-After: 60")

    def _check_logs(self, sentinel: str) -> None:
        if not self.nginx_log_dir.exists():
            raise ActivationError("Nginx log directory is missing")
        encoded = sentinel.encode("utf-8")
        for name in RELEVANT_NGINX_LOGS:
            path = self.nginx_log_dir / name
            if not path.exists():
                continue
            assert_no_symlink_components(path, allow_missing_leaf=False)
            try:
                content = read_bounded_tail(path)
            except OSError as exc:
                raise ActivationError("cannot inspect Nginx logs") from exc
            if encoded in content:
                raise ActivationError("invalid ICS token appeared in an Nginx log")

    def _health_checks(self) -> None:
        deadline = self.clock() + HEALTH_TOTAL_TIMEOUT_SECONDS
        self._call_hook("health:root")
        status, _, _ = self._health_request("GET", PRODUCTION_HEALTH_ORIGIN + "/", deadline)
        if status < 200 or status >= 300:
            raise ActivationError("ordinary root health check failed")
        sentinel = secrets.token_urlsafe(48)
        feed_url = PRODUCTION_HEALTH_ORIGIN + "/api/calendar/share-feed.ics?token=" + sentinel
        self._call_hook("health:invalid")
        for method in ("GET", "HEAD"):
            status, headers, body = self._health_request(method, feed_url, deadline)
            if status != 404:
                raise ActivationError("invalid ICS token did not return 404")
            self._assert_headers(headers)
            if method == "HEAD" and body:
                raise ActivationError("ICS HEAD response contained a body")
        self._call_hook("health:rate")
        rate_status = None
        rate_headers: Mapping[str, str] = {}
        for _ in range(40):
            rate_status, rate_headers, _ = self._health_request("HEAD", feed_url, deadline)
            if rate_status == 429:
                break
            if rate_status != 404:
                raise ActivationError("ICS rate-limit probe returned an unexpected status")
        if rate_status != 429:
            raise ActivationError("bounded ICS rate-limit probe did not receive 429")
        self._assert_headers(rate_headers, rate_limited=True)
        self._check_logs(sentinel)

    def _install_signal_handlers(self) -> dict[int, Any]:
        previous = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}

        def interrupt(signum: int, _frame: Any) -> None:
            raise ActivationSignal(f"activation interrupted by signal {signum}")

        signal.signal(signal.SIGINT, interrupt)
        signal.signal(signal.SIGTERM, interrupt)
        return previous

    @staticmethod
    def _restore_signal_handlers(previous: Mapping[int, Any]) -> None:
        for sig, handler in previous.items():
            signal.signal(sig, handler)

    def _update_journal(self, **fields: Any) -> None:
        assert self._journal is not None and self._journal_path is not None
        self._journal.update(fields)
        atomic_write(self._journal_path, _journal_json(self._journal), mode=0o600)

    def _commit_journal(self) -> None:
        # Once the commit marker is durably replaced, rollback must not be
        # triggered by a signal delivered in the tiny interval before the
        # in-memory flag is updated.
        previous = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        try:
            self._update_journal(status="committed", phase="committed", committed_at=int(time.time()))
            self._committed = True
        finally:
            self._restore_signal_handlers(previous)

    def _create_journal(
        self,
        txid: str,
        backup: Path,
        manifest: Mapping[str, Any],
        stage: Path,
        staged: Mapping[str, str],
        candidate_hashes: Mapping[str, str],
        baseline: Mapping[str, Any],
    ) -> None:
        assert_no_symlink_components(self.state_dir, allow_missing_leaf=True)
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        assert_no_symlink_components(self.state_dir, allow_missing_leaf=False)
        os.chmod(self.state_dir, 0o700)
        self._journal_path = self.state_dir / f"transaction-{txid}.json"
        self._journal = {
            "version": TOOL_VERSION,
            "transaction_id": txid,
            "status": "prepared",
            "phase": "prepared",
            "created_at": int(time.time()),
            "root": str(self.root),
            "env_path": str(self.env_path),
            "nginx_site": str(self.nginx_site),
            "backup": str(backup),
            "stage": str(stage),
            "staged": dict(staged),
            "targets": dict(manifest),
            "candidate_sha256": dict(candidate_hashes),
            "baseline": dict(baseline),
        }
        atomic_write(self._journal_path, _journal_json(self._journal), mode=0o600)

    def _target_matches(self, record: Mapping[str, Any]) -> bool:
        target = Path(str(record["path"]))
        if record.get("existed"):
            if not target.is_file() or target.is_symlink():
                return False
            info = target.stat()
            return (
                sha256_file(target) == record.get("sha256")
                and stat.S_IMODE(info.st_mode) == int(record["mode"])
                and info.st_uid == int(record["uid"])
                and info.st_gid == int(record["gid"])
            )
        return not target.exists() and not target.is_symlink()

    def _restore_target(self, record: Mapping[str, Any]) -> bool:
        target = Path(str(record["path"]))
        assert_no_symlink_components(target.parent, allow_missing_leaf=False)
        if self._target_matches(record):
            return False
        backup = record.get("backup")
        if record.get("existed"):
            if not backup or not Path(str(backup)).is_file():
                raise ActivationError("transaction backup is missing")
            assert_no_symlink_components(Path(str(backup)), allow_missing_leaf=False)
            self._call_hook(f"rollback:{target.name}")
            self._install_file(Path(str(backup)), target, mode=int(record["mode"]), metadata=record)
            if not self._target_matches(record):
                raise ActivationError("restored file hash does not match transaction manifest")
        else:
            self._call_hook(f"rollback:{target.name}")
            with contextlib.suppress(FileNotFoundError):
                target.unlink()
            fsync_directory(target.parent)
            if not self._target_matches(record):
                raise ActivationError("transaction-created file could not be removed")
        return True

    def _persist_rollback_state(
        self,
        journal: dict[str, Any],
        journal_path: Path,
        rollback: Mapping[str, Any],
        phase: str,
    ) -> None:
        journal["status"] = "rolling_back"
        journal["phase"] = phase
        journal["rollback"] = dict(rollback)
        atomic_write(journal_path, _journal_json(journal), mode=0o600)

    def _rollback_journal(self, journal: Mapping[str, Any], journal_path: Path) -> None:
        if journal.get("status") in TERMINAL_STATUSES:
            return
        if self._rollback_running:
            return
        self._rollback_running = True
        old_handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
        try:
            signal.signal(signal.SIGINT, signal.SIG_IGN)
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            working = dict(journal)
            targets = working.get("targets", {})
            if not isinstance(targets, dict):
                raise ActivationError("transaction target manifest is invalid")
            rollback = working.get("rollback")
            if not isinstance(rollback, dict):
                rollback = {
                    "app_restart_required": not self._target_matches(targets["env"]),
                }
                self._persist_rollback_state(working, journal_path, rollback, "rollback_prepared")

            # Restore the disabled baseline environment first.  Persisting the
            # restart requirement before this write guarantees that a crash
            # after replacement still causes Nest to restart on recovery.
            if not rollback.get("env_restored"):
                self._restore_target(targets["env"])
                self._call_hook("crash-after:rollback-env")
                rollback["env_restored"] = True
                self._persist_rollback_state(working, journal_path, rollback, "rollback_env_restored")
            if rollback.get("app_restart_required") and not rollback.get("app_restarted"):
                self._restart_and_verify_app()
                self._call_hook("crash-after:rollback-app-restart")
                rollback["app_restarted"] = True
                self._persist_rollback_state(working, journal_path, rollback, "rollback_app_restarted")

            # The clean site must stop referencing ICS dependencies before
            # those dependency files are removed/restored.
            if not rollback.get("site_restored"):
                self._restore_target(targets["site"])
                self._call_hook("crash-after:rollback-site")
                rollback["site_restored"] = True
                self._persist_rollback_state(working, journal_path, rollback, "rollback_site_restored")
            for name in ("feed", "http", "real_ip"):
                marker = f"{name}_restored"
                if not rollback.get(marker):
                    self._restore_target(targets[name])
                    self._call_hook(f"crash-after:rollback-{name}")
                    rollback[marker] = True
                    self._persist_rollback_state(working, journal_path, rollback, f"rollback_{marker}")
            if not rollback.get("nginx_validated"):
                self._nginx_test()
                self._call_hook("crash-after:rollback-nginx-test")
                rollback["nginx_validated"] = True
                self._persist_rollback_state(working, journal_path, rollback, "rollback_nginx_validated")
            if not rollback.get("nginx_reloaded"):
                self._run("systemctl", "reload", self.nginx_service)
                self._active(self.nginx_service)
                self._call_hook("crash-after:rollback-nginx-reload")
                rollback["nginx_reloaded"] = True
                self._persist_rollback_state(working, journal_path, rollback, "rollback_nginx_reloaded")
            stage = Path(str(journal.get("stage", "")))
            if not rollback.get("stage_removed"):
                if stage and stage.exists() and _safe_rel(stage, self.state_dir):
                    shutil.rmtree(stage, ignore_errors=False)
                    fsync_directory(stage.parent)
                self._call_hook("crash-after:rollback-stage")
                rollback["stage_removed"] = True
                self._persist_rollback_state(working, journal_path, rollback, "rollback_stage_removed")
            working.update({
                "status": "rolled_back",
                "phase": "rolled_back",
                "rollback": rollback,
                "rolled_back_at": int(time.time()),
            })
            atomic_write(journal_path, _journal_json(working), mode=0o600)
        finally:
            for sig, handler in old_handlers.items():
                signal.signal(sig, handler)
            self._rollback_running = False

    def recover(self, transaction_id: str | None = None) -> None:
        self._lock()
        try:
            incomplete = self._incomplete()
            if transaction_id:
                incomplete = [
                    item for item in incomplete if item[1].get("transaction_id") == transaction_id
                ]
            if not incomplete:
                if transaction_id:
                    raise ActivationError("requested incomplete transaction does not exist")
                return
            if len(incomplete) > 1:
                raise ActivationError("multiple incomplete transactions exist; specify --transaction-id")
            journal_path, journal = incomplete[0]
            self._validate_recovery_journal(journal_path, journal)
            self._rollback_journal(journal, journal_path)
        finally:
            self._unlock()

    def apply(
        self,
        *,
        owner_allowlist: str,
        expected_head: str,
        expected_env: str,
        expected_site: str,
    ) -> None:
        self._lock()
        txid = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + secrets.token_hex(6)
        journal_path: Path | None = None
        stage: Path | None = None
        backup: Path | None = None
        signal_handlers: dict[int, Any] | None = None
        try:
            self._assert_no_incomplete()
            baseline = self._preflight(expected_head, expected_env, expected_site)
            base_env = self.env_path.read_bytes()
            candidate_env, _uid_secret = _candidate_env(base_env, owner_allowlist)
            candidate_site = _site_with_feed_include(
                self.nginx_site.read_bytes(),
                str(self.nginx_snippets_dir / "nest-calendar-ics-feed.conf"),
            )
            targets = {
                "env": self.env_path,
                "site": self.nginx_site,
                **self.nginx_targets,
            }
            backup, manifest = self._make_backup(txid, targets)
            stage = self._stage_dir(txid)
            staged = {name: str(stage / name) for name in ("env", "site", "real_ip", "http", "feed")}
            self._create_journal(txid, backup, manifest, stage, staged, {}, baseline)
            journal_path = self._journal_path
            signal_handlers = self._install_signal_handlers()
            self._stage_contents(stage, candidate_env, candidate_site)
            candidate_hashes = {name: sha256_file(Path(path)) for name, path in staged.items()}
            self._update_journal(phase="staged", candidate_sha256=candidate_hashes)
            self._call_hook("shadow:validate")
            self._shadow_nginx(stage)
            self._update_journal(phase="shadow_validated")

            # Install dependencies before the site include can refer to them.
            self._validate_pins(expected_head, expected_env, expected_site)
            self._call_hook("pins:rechecked")
            self._update_journal(phase="pins_rechecked")
            for name in ("real_ip", "http", "feed"):
                self._install_file(Path(staged[name]), targets[name], mode=0o644)
            self._install_file(Path(staged["site"]), self.nginx_site, mode=int(manifest["site"]["mode"]), metadata=manifest["site"])
            self._update_journal(phase="nginx_installed")
            self._nginx_test()
            self._update_journal(phase="nginx_validated")
            self._reload_nginx()
            self._active(self.nginx_service)
            self._update_journal(phase="nginx_reloaded")

            self._replace_env(Path(staged["env"]), manifest["env"])
            self._update_journal(phase="env_installed")
            self._restart_and_verify_app()
            self._update_journal(phase="app_restarted")
            self._health_checks()
            self._update_journal(phase="health_checked")
            shutil.rmtree(stage, ignore_errors=False)
            self._commit_journal()
        except BaseException as exc:
            if journal_path and journal_path.exists() and self._journal and not self._committed:
                try:
                    self._rollback_journal(self._journal, journal_path)
                except BaseException as rollback_exc:
                    raise ActivationError("activation failed and automatic rollback also failed; run --recover") from rollback_exc
            elif stage is not None and _safe_rel(stage, self.state_dir):
                with contextlib.suppress(FileNotFoundError):
                    shutil.rmtree(stage.parent, ignore_errors=False)
            if isinstance(exc, ActivationError):
                raise
            if isinstance(exc, KeyboardInterrupt):
                raise ActivationError("activation interrupted; rollback completed") from exc
            raise ActivationError("activation failed; rollback completed") from exc
        finally:
            if signal_handlers is not None:
                self._restore_signal_handlers(signal_handlers)
            self._unlock()


def _hex_arg(value: str | None, label: str) -> str | None:
    if value is None:
        return None
    if not re.fullmatch(r"[0-9a-fA-F]{64}", value):
        raise ActivationError(f"{label} must be a SHA-256 hexadecimal digest")
    return value.lower()


def _head_arg(value: str | None) -> str | None:
    if value is None:
        return None
    if not GIT_HEAD.fullmatch(value):
        raise ActivationError("--expected-head must be a 40-character lowercase hexadecimal commit")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--check", action="store_true")
    modes.add_argument("--apply", action="store_true")
    modes.add_argument("--recover", action="store_true")
    modes.add_argument("--rollback", metavar="TRANSACTION_ID")
    parser.add_argument("--owner-allowlist")
    parser.add_argument("--expected-head")
    parser.add_argument("--expected-env-sha256")
    parser.add_argument("--expected-site-sha256")
    parser.add_argument("--transaction-id")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        expected_head = _head_arg(args.expected_head)
        expected_env = _hex_arg(args.expected_env_sha256, "--expected-env-sha256")
        expected_site = _hex_arg(args.expected_site_sha256, "--expected-site-sha256")
        if args.rollback and args.transaction_id:
            raise ActivationError("use only one rollback transaction selector")
        tool = ActivationTool()
        if args.check:
            if not expected_head or not expected_env or not expected_site:
                raise ActivationError("--check requires expected HEAD, .env SHA-256, and site SHA-256 pins")
            tool.check(expected_head=expected_head, expected_env=expected_env, expected_site=expected_site)
        elif args.apply:
            if not args.owner_allowlist:
                raise ActivationError("--owner-allowlist is required for --apply")
            if not expected_head or not expected_env or not expected_site:
                raise ActivationError("--apply requires expected HEAD, .env SHA-256, and site SHA-256 pins")
            tool.apply(
                owner_allowlist=args.owner_allowlist,
                expected_head=expected_head,
                expected_env=expected_env,
                expected_site=expected_site,
            )
        else:
            tool.recover(transaction_id=args.transaction_id or args.rollback)
        print("PASS")
        return 0
    except ActivationError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
