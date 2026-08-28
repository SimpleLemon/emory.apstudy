"""Validate and expose the generated calendar module-graph version."""

from __future__ import annotations

import hashlib
import json
import re
import threading
from pathlib import Path, PurePosixPath
from urllib.parse import parse_qsl, urlsplit


STATIC_ROOT = Path(__file__).resolve().parents[1] / "static"
MANIFEST_PATH = STATIC_ROOT / "js/calendar/manifest.json"
ENTRY_PATH = "js/calendar/entry.js"
MANIFEST_SCHEMA = 2
VERSION_PATTERN = re.compile(r"^[0-9a-f]{64}$")
HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")

_cache_lock = threading.RLock()
_cached_signature: tuple[str, int, int, int] | None = None
_cached_version: str | None = None


class CalendarAssetError(RuntimeError):
    """Raised when browser assets are missing or do not match their manifest."""


def _confined_root() -> Path:
    try:
        root = STATIC_ROOT.resolve(strict=True)
    except OSError as error:
        raise CalendarAssetError("Calendar static root is missing or invalid.") from error
    if not root.is_dir():
        raise CalendarAssetError("Calendar static root is not a directory.")
    return root


def _relative_parts(relative_path: str, label: str) -> tuple[str, ...]:
    if not isinstance(relative_path, str) or "\\" in relative_path:
        raise CalendarAssetError(f"Calendar asset {label} path is invalid.")
    parsed = PurePosixPath(relative_path)
    if parsed.is_absolute() or not parsed.parts or any(part in {"", ".", ".."} for part in parsed.parts):
        raise CalendarAssetError(f"Calendar asset {label} path escapes static/.")
    if parsed.as_posix() != relative_path:
        raise CalendarAssetError(f"Calendar asset {label} path is not canonical.")
    return parsed.parts


def _confined_file(root: Path, relative_path: str, label: str) -> Path:
    parts = _relative_parts(relative_path, label)
    lexical = root.joinpath(*parts)
    try:
        resolved = lexical.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as error:
        raise CalendarAssetError(f"Calendar asset {label} path is missing or escapes static/.") from error
    if not resolved.is_file():
        raise CalendarAssetError(f"Calendar asset {label} is not a regular file.")
    return resolved


def _manifest_file(root: Path) -> Path:
    try:
        resolved = MANIFEST_PATH.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as error:
        raise CalendarAssetError("Calendar asset manifest is missing or escapes static/.") from error
    if not resolved.is_file():
        raise CalendarAssetError("Calendar asset manifest is not a regular file.")
    return resolved


def _manifest_signature(root: Path) -> tuple[Path, tuple[str, int, int, int]]:
    manifest_path = _manifest_file(root)
    metadata = manifest_path.stat()
    return manifest_path, (
        str(manifest_path),
        metadata.st_ino,
        metadata.st_mtime_ns,
        metadata.st_size,
    )


def _load_manifest(manifest_path: Path) -> dict:
    try:
        manifest = json.loads(manifest_path.read_bytes())
    except (OSError, UnicodeDecodeError, ValueError) as error:
        raise CalendarAssetError("Calendar asset manifest is missing or malformed.") from error
    if not isinstance(manifest, dict) or set(manifest) != {"schema", "entry", "version", "modules", "edges"}:
        raise CalendarAssetError("Calendar asset manifest has an invalid schema.")
    if (
        manifest["schema"] != MANIFEST_SCHEMA
        or manifest["entry"] != ENTRY_PATH
        or not VERSION_PATTERN.fullmatch(str(manifest["version"]))
        or not isinstance(manifest["modules"], list)
        or not isinstance(manifest["edges"], list)
    ):
        raise CalendarAssetError("Calendar asset manifest has an invalid shape.")
    return manifest


def _validate_modules(root: Path, manifest: dict) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    listed_paths: list[str] = []
    for item in manifest["modules"]:
        if not isinstance(item, dict) or set(item) != {"path", "sha256"}:
            raise CalendarAssetError("Calendar asset manifest has invalid module entries.")
        relative_path = item["path"]
        digest = item["sha256"]
        if not isinstance(relative_path, str) or not HASH_PATTERN.fullmatch(str(digest)):
            raise CalendarAssetError("Calendar asset manifest has invalid module entries.")
        source_path = _confined_file(root, relative_path, "module")
        if source_path.suffix not in {".js", ".mjs"}:
            raise CalendarAssetError("Calendar asset module is not JavaScript.")
        if hashlib.sha256(source_path.read_bytes()).hexdigest() != digest:
            raise CalendarAssetError("Calendar asset final-file hash does not match the manifest.")
        if relative_path in paths:
            raise CalendarAssetError("Calendar asset manifest contains duplicate modules.")
        listed_paths.append(relative_path)
        paths[relative_path] = source_path
    if listed_paths != sorted(listed_paths) or manifest["entry"] not in paths:
        raise CalendarAssetError("Calendar asset manifest has an invalid module file set.")
    return paths


def _edge_target(root: Path, importer: Path, specifier: str) -> Path:
    parsed = urlsplit(specifier)
    if parsed.scheme or parsed.netloc or not (parsed.path.startswith(".") or parsed.path.startswith("/static/")):
        raise CalendarAssetError("Calendar asset manifest contains a non-local graph edge.")
    base = root.joinpath(*_relative_parts(parsed.path.removeprefix("/static/"), "edge")) \
        if parsed.path.startswith("/static/") else importer.parent / parsed.path
    lexical = base.resolve(strict=False)
    try:
        lexical.relative_to(root)
    except ValueError as error:
        raise CalendarAssetError("Calendar asset graph edge traverses outside static/.") from error
    candidates = [lexical] if lexical.suffix else [lexical, Path(f"{lexical}.js"), Path(f"{lexical}.mjs")]
    for candidate in candidates:
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(root)
        except (OSError, ValueError):
            continue
        if resolved.is_file():
            return resolved
    raise CalendarAssetError("Calendar asset graph edge target is missing or escapes static/.")


def _validate_edges(root: Path, manifest: dict, modules: dict[str, Path]) -> None:
    version = manifest["version"]
    adjacency: dict[str, set[str]] = {module: set() for module in modules}
    edge_keys: list[tuple[str, str, str]] = []
    for edge in manifest["edges"]:
        if not isinstance(edge, dict) or list(edge) != ["from", "specifier", "to"]:
            raise CalendarAssetError("Calendar asset manifest has invalid graph edges.")
        source, specifier, target = edge.values()
        if source not in modules or target not in modules or not isinstance(specifier, str):
            raise CalendarAssetError("Calendar asset graph edge references an unknown module.")
        versions = [value for key, value in parse_qsl(urlsplit(specifier).query, keep_blank_values=True) if key == "v"]
        if versions != [version]:
            raise CalendarAssetError("Calendar asset graph edge has a missing or stale version.")
        if _edge_target(root, modules[source], specifier) != modules[target]:
            raise CalendarAssetError("Calendar asset graph edge target does not match its specifier.")
        adjacency[source].add(target)
        edge_keys.append((source, specifier, target))
    if edge_keys != sorted(edge_keys) or len(edge_keys) != len(set(edge_keys)):
        raise CalendarAssetError("Calendar asset manifest graph edges are not canonical.")
    reachable = {manifest["entry"]}
    pending = [manifest["entry"]]
    while pending:
        source = pending.pop()
        for target in adjacency[source] - reachable:
            reachable.add(target)
            pending.append(target)
    if reachable != set(modules):
        raise CalendarAssetError("Calendar asset manifest module file set is not the reachable graph.")


def _validate_manifest(manifest_path: Path, root: Path) -> str:
    manifest = _load_manifest(manifest_path)
    modules = _validate_modules(root, manifest)
    _validate_edges(root, manifest, modules)
    return manifest["version"]


def calendar_asset_version() -> str:
    """Return a validated version, refreshing whenever the manifest identity changes."""

    global _cached_signature, _cached_version
    with _cache_lock:
        root = _confined_root()
        for _attempt in range(2):
            manifest_path, before = _manifest_signature(root)
            if before == _cached_signature and _cached_version is not None:
                return _cached_version
            version = _validate_manifest(manifest_path, root)
            _, after = _manifest_signature(root)
            if before == after:
                _cached_signature = after
                _cached_version = version
                return version
        raise CalendarAssetError("Calendar asset manifest changed while it was being validated.")


def _clear_calendar_asset_cache() -> None:
    global _cached_signature, _cached_version
    with _cache_lock:
        _cached_signature = None
        _cached_version = None


calendar_asset_version.cache_clear = _clear_calendar_asset_cache  # type: ignore[attr-defined]
