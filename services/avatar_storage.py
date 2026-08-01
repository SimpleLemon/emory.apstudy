"""
services/avatar_storage.py

Shared helpers for storing profile avatars in Appwrite Storage.

Used by the OAuth login flow (to copy a provider avatar into the bucket) and
by the settings avatar upload route. Centralizing these helpers avoids a
blueprints/auth -> blueprints/settings import cycle.
"""

import logging
from urllib.parse import urljoin

import requests as http_requests
from flask import current_app, has_app_context
from appwrite.exception import AppwriteException
from appwrite.id import ID
from appwrite.input_file import InputFile
from appwrite.permission import Permission
from appwrite.role import Role
from appwrite.services.storage import Storage

from config import ENVIRONMENT_CONFIG_EXTENSION_KEY, load_environment_config
from appwrite_client import (
    ENDPOINT,
    PROFILE_AVATAR_BUCKET_ID,
    PROJECT_ID,
    client as appwrite_client,
)
from services.outbound_http import redacted_url, require_public_http_url

logger = logging.getLogger(__name__)

MAX_AVATAR_BYTES = 10 * 1024 * 1024
ALLOWED_AVATAR_MIME_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
MAX_AVATAR_REDIRECTS = 4
MIME_TYPE_EXTENSIONS = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
}


def _environment_config_snapshot():
    if has_app_context():
        configured = current_app.extensions.get(ENVIRONMENT_CONFIG_EXTENSION_KEY)
        if configured is not None:
            return configured
    return load_environment_config()


def build_avatar_view_url(file_id):
    """Return the public Appwrite Storage view URL for an avatar file."""
    configured = _environment_config_snapshot()
    endpoint = (ENDPOINT or configured.appwrite_endpoint or "").rstrip("/")
    project_id = PROJECT_ID or configured.appwrite_project_id or ""
    if not endpoint or not project_id or not file_id:
        return None
    return f"{endpoint}/storage/buckets/{PROFILE_AVATAR_BUCKET_ID}/files/{file_id}/view?project={project_id}"


def delete_avatar_file(file_id):
    """Best-effort delete of an avatar file from the profile avatar bucket."""
    if not file_id:
        return
    try:
        Storage(appwrite_client).delete_file(PROFILE_AVATAR_BUCKET_ID, file_id)
    except AppwriteException:
        logger.exception("Failed to delete old avatar file")


def _normalize_mime_type(content_type):
    if not content_type:
        return None
    mime_type = content_type.split(";", 1)[0].strip().lower()
    return mime_type or None


def store_avatar_from_url(user_id, source_url, *, timeout=8, max_bytes=MAX_AVATAR_BYTES):
    """Download an image from ``source_url`` and store it in the avatar bucket.

    Best-effort: returns ``{"file_id", "view_url"}`` on success or ``None`` on any
    failure so callers (e.g. OAuth login) never break when the copy fails.
    """
    clean_url = str(source_url or "").strip()
    if not clean_url:
        return None

    response = None
    current_url = clean_url
    try:
        for _ in range(MAX_AVATAR_REDIRECTS + 1):
            require_public_http_url(current_url)
            response = http_requests.get(
                current_url,
                timeout=timeout,
                stream=True,
                allow_redirects=False,
            )
            if 300 <= response.status_code < 400 and response.headers.get("Location"):
                next_url = urljoin(current_url, response.headers["Location"])
                response.close()
                response = None
                current_url = next_url
                continue
            break
        else:
            logger.warning("Provider avatar redirected too many times")
            return None
    except Exception as exc:
        logger.warning(
            "Failed to download provider avatar: source=%s error_type=%s",
            redacted_url(current_url)[:120],
            type(exc).__name__,
        )
        return None

    try:
        if response.status_code != 200:
            logger.warning(
                "Provider avatar download failed: status=%s source=%s",
                response.status_code,
                redacted_url(current_url)[:120],
            )
            return None

        mime_type = _normalize_mime_type(response.headers.get("Content-Type"))
        if mime_type not in ALLOWED_AVATAR_MIME_TYPES:
            logger.warning(
                "Provider avatar has unsupported content type: %s source=%s",
                mime_type,
                redacted_url(current_url)[:120],
            )
            return None

        content_length = response.headers.get("Content-Length")
        if content_length is not None:
            try:
                if int(content_length) > max_bytes:
                    logger.warning("Provider avatar exceeds size limit: %s", content_length)
                    return None
            except (TypeError, ValueError):
                pass

        file_bytes = bytearray()
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            file_bytes.extend(chunk)
            if len(file_bytes) > max_bytes:
                logger.warning("Provider avatar exceeds size limit while downloading")
                return None
    finally:
        response.close()

    if not file_bytes:
        logger.warning("Provider avatar download was empty")
        return None

    extension = MIME_TYPE_EXTENSIONS.get(mime_type, "png")
    file_id = ID.unique()
    stored_filename = f"{user_id}-{file_id}.{extension}"
    input_file = InputFile.from_bytes(
        bytes(file_bytes),
        stored_filename,
        mime_type=mime_type,
    )

    try:
        Storage(appwrite_client).create_file(
            PROFILE_AVATAR_BUCKET_ID,
            file_id,
            input_file,
            permissions=[Permission.read(Role.any())],
        )
    except AppwriteException:
        logger.exception("Failed to upload provider avatar to storage")
        return None

    view_url = build_avatar_view_url(file_id)
    if not view_url:
        delete_avatar_file(file_id)
        logger.warning("Avatar storage is not configured; cannot build view URL")
        return None

    return {"file_id": file_id, "view_url": view_url, "size_bytes": len(file_bytes)}
