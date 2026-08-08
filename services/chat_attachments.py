"""Private, room-scoped storage and validation for chat attachments."""

from __future__ import annotations

import gzip
import hashlib
import io
import logging
import os
import posixpath
import re
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError
from appwrite.exception import AppwriteException
from appwrite.input_file import InputFile
from appwrite.query import Query
from appwrite.services.storage import Storage

from appwrite_client import CHAT_ATTACHMENTS_BUCKET_ID, COLLECTIONS, client as appwrite_client
from appwrite_helpers import create_row_safe, delete_row_safe, get_row_safe, list_rows_all, update_row_safe
from services.database import utcnow_iso
from services.entitlements import EntitlementLimitError, check_storage


logger = logging.getLogger(__name__)
TABLE_ID = COLLECTIONS["chat_attachments"]
MAX_ATTACHMENTS_PER_MESSAGE = 5
MAX_IMAGE_PIXELS = 40_000_000
MAX_IMAGE_DIMENSION = 2560
MAX_ARCHIVE_ENTRIES = 2_000
MAX_ARCHIVE_EXPANDED_BYTES = 250 * 1024 * 1024
MAX_ARCHIVE_RATIO = 100

IMAGE_FORMATS = {
    "JPEG": ("image/jpeg", {".jpg", ".jpeg"}),
    "PNG": ("image/png", {".png"}),
    "WEBP": ("image/webp", {".webp"}),
    "GIF": ("image/gif", {".gif"}),
}
DOCUMENT_MIMES = {
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".csv": "text/csv",
    ".json": "application/json",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".odt": "application/vnd.oasis.opendocument.text",
    ".ods": "application/vnd.oasis.opendocument.spreadsheet",
    ".odp": "application/vnd.oasis.opendocument.presentation",
    ".zip": "application/zip",
}
ZIP_CONTAINER_MARKERS = {
    ".docx": "word/",
    ".xlsx": "xl/",
    ".pptx": "ppt/",
}
DENIED_ARCHIVE_EXTENSIONS = {
    ".app", ".bat", ".bin", ".cmd", ".com", ".cpl", ".dll", ".dmg", ".exe",
    ".hta", ".htm", ".html", ".iso", ".jar", ".js", ".jse", ".lnk", ".msi",
    ".msp", ".ps1", ".py", ".rb", ".reg", ".scr", ".sh", ".svg", ".vbs",
    ".xlsm", ".docm", ".pptm", ".xls", ".doc", ".ppt",
}
COMPRESSIBLE_EXTENSIONS = {".txt", ".md", ".markdown", ".csv", ".json"}
SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._()\- ]+")


class AttachmentError(ValueError):
    pass


def storage_service():
    return Storage(appwrite_client)


def _safe_filename(value):
    name = Path(str(value or "attachment").replace("\\", "/")).name
    name = SAFE_NAME_RE.sub("_", name).strip(" .")[:255]
    return name or "attachment"


def _inspect_archive(data, extension):
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
            if not infos or len(infos) > MAX_ARCHIVE_ENTRIES:
                raise AttachmentError("This archive has too many entries or is empty.")
            expanded = 0
            names = []
            for info in infos:
                if info.flag_bits & 0x1:
                    raise AttachmentError("Encrypted archives are not supported.")
                normalized = posixpath.normpath(info.filename.replace("\\", "/"))
                if normalized.startswith("../") or normalized.startswith("/") or normalized == "..":
                    raise AttachmentError("This archive contains an unsafe path.")
                nested_extension = Path(normalized).suffix.lower()
                if nested_extension in DENIED_ARCHIVE_EXTENSIONS:
                    raise AttachmentError("This archive contains an unsafe file type.")
                expanded += int(info.file_size or 0)
                compressed = max(1, int(info.compress_size or 0))
                if info.file_size > 10 * 1024 * 1024 and info.file_size / compressed > MAX_ARCHIVE_RATIO:
                    raise AttachmentError("This archive expands beyond the safe compression ratio.")
                names.append(normalized)
            if expanded > MAX_ARCHIVE_EXPANDED_BYTES:
                raise AttachmentError("This archive expands beyond the safe size limit.")
            marker = ZIP_CONTAINER_MARKERS.get(extension)
            if marker and not any(name.startswith(marker) for name in names):
                raise AttachmentError("The file contents do not match its Office format.")
            if extension in {".odt", ".ods", ".odp"}:
                expected = DOCUMENT_MIMES[extension]
                try:
                    mimetype = archive.read("mimetype").decode("ascii", "strict").strip()
                except (KeyError, UnicodeDecodeError):
                    raise AttachmentError("The file contents do not match its OpenDocument format.")
                if mimetype != expected:
                    raise AttachmentError("The file contents do not match its OpenDocument format.")
    except (zipfile.BadZipFile, RuntimeError) as exc:
        raise AttachmentError("The file is not a valid, readable archive.") from exc


def _optimize_image(data, extension):
    try:
        with Image.open(io.BytesIO(data)) as image:
            image_format = str(image.format or "").upper()
            if image_format not in IMAGE_FORMATS or extension not in IMAGE_FORMATS[image_format][1]:
                raise AttachmentError("The image contents do not match its filename.")
            width, height = image.size
            if width < 1 or height < 1 or width * height > MAX_IMAGE_PIXELS:
                raise AttachmentError("Image dimensions are too large.")
            image.verify()
        if image_format == "GIF":
            return data, IMAGE_FORMATS[image_format][0], width, height, "identity"
        with Image.open(io.BytesIO(data)) as image:
            has_metadata = bool(image.getexif()) or any(
                key in image.info for key in ("icc_profile", "xmp", "XML:com.adobe.xmp")
            )
            image = ImageOps.exif_transpose(image)
            image.thumbnail((MAX_IMAGE_DIMENSION, MAX_IMAGE_DIMENSION), Image.Resampling.LANCZOS)
            output = io.BytesIO()
            if image_format == "JPEG":
                image = image.convert("RGB")
                save_kwargs = {"optimize": True, "quality": 86, "progressive": True}
            elif image_format == "WEBP":
                save_kwargs = {"optimize": True, "quality": 86, "method": 5}
            else:
                save_kwargs = {"optimize": True}
            image.save(output, format=image_format, **save_kwargs)
            optimized = output.getvalue()
            if len(optimized) >= len(data) and image.size == (width, height) and not has_metadata:
                optimized = data
            return optimized, IMAGE_FORMATS[image_format][0], image.width, image.height, "identity"
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, RuntimeError, Image.DecompressionBombError) as exc:
        raise AttachmentError("The uploaded file is not a valid supported image.") from exc


def _pdf_thumbnail(data):
    try:
        import pypdfium2 as pdfium

        document = pdfium.PdfDocument(data)
        if len(document) < 1:
            return None
        page = document[0]
        bitmap = page.render(scale=1.25)
        image = bitmap.to_pil().convert("RGB")
        image.thumbnail((720, 960), Image.Resampling.LANCZOS)
        output = io.BytesIO()
        image.save(output, format="WEBP", quality=72, method=4)
        return output.getvalue(), image.width, image.height
    except Exception:
        logger.info("PDF preview generation failed; using generic file card", exc_info=True)
        return None


def inspect_and_prepare(data, filename):
    if not data:
        raise AttachmentError("The selected file is empty.")
    safe_name = _safe_filename(filename)
    extension = Path(safe_name).suffix.lower()
    if extension in DENIED_ARCHIVE_EXTENSIONS or extension not in DOCUMENT_MIMES and not any(
        extension in value[1] for value in IMAGE_FORMATS.values()
    ):
        raise AttachmentError("This file type is not allowed in chat.")
    original_sha256 = hashlib.sha256(data).hexdigest()
    preview = None
    width = height = None
    if any(extension in value[1] for value in IMAGE_FORMATS.values()):
        stored, mime_type, width, height, encoding = _optimize_image(data, extension)
        kind = "image"
    else:
        mime_type = DOCUMENT_MIMES[extension]
        kind = "pdf" if extension == ".pdf" else "file"
        if extension == ".pdf":
            if not data.startswith(b"%PDF-"):
                raise AttachmentError("The file is not a valid PDF.")
            preview = _pdf_thumbnail(data)
        elif extension in COMPRESSIBLE_EXTENSIONS:
            try:
                data.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise AttachmentError("Text attachments must use UTF-8 encoding.") from exc
        elif extension in ZIP_CONTAINER_MARKERS or extension in {".odt", ".ods", ".odp", ".zip"}:
            _inspect_archive(data, extension)
        compressed = gzip.compress(data, compresslevel=6, mtime=0) if extension in COMPRESSIBLE_EXTENSIONS else data
        if len(compressed) < len(data):
            stored, encoding = compressed, "gzip"
        else:
            stored, encoding = data, "identity"
    return {
        "filename": safe_name,
        "mime_type": mime_type,
        "kind": kind,
        "original_size_bytes": len(data),
        "stored": stored,
        "stored_size_bytes": len(stored),
        "compression_encoding": encoding,
        "sha256": original_sha256,
        "width": width,
        "height": height,
        "preview": preview,
    }


def _create_storage_file(data, filename, mime_type):
    file_id = str(uuid.uuid4())
    storage_service().create_file(
        CHAT_ATTACHMENTS_BUCKET_ID,
        file_id,
        InputFile.from_bytes(data, filename=filename, mime_type=mime_type),
        permissions=[],
    )
    return file_id


def create_attachment(*, user_id, scope_type, scope_id, uploaded_file, entitlements, original_size=None, upload_encoding="identity"):
    limit = entitlements["limits"].get("max_chat_attachment_size_bytes")
    declared_size = int(original_size or 0)
    read_limit = (limit if limit is not None else 50 * 1024 * 1024) + 1
    data = uploaded_file.read(read_limit)
    if upload_encoding == "gzip":
        try:
            data = gzip.decompress(data)
        except (gzip.BadGzipFile, OSError) as exc:
            raise AttachmentError("The compressed upload could not be read.") from exc
        if len(data) > read_limit - 1:
            raise AttachmentError("The decompressed upload exceeds your chat limit.")
    actual_size = len(data)
    original_size = max(declared_size, actual_size)
    if limit is not None and original_size > limit:
        raise EntitlementLimitError("chat attachment size", 0, original_size, limit)
    prepared = inspect_and_prepare(data, uploaded_file.filename)
    check_storage(entitlements, prepared["stored_size_bytes"] + (len(prepared["preview"][0]) if prepared["preview"] else 0))
    attachment_id = str(uuid.uuid4())
    storage_file_id = preview_file_id = None
    try:
        storage_file_id = _create_storage_file(
            prepared["stored"], f"{attachment_id}.bin", "application/octet-stream"
        )
        if prepared["preview"]:
            preview_file_id = _create_storage_file(
                prepared["preview"][0], f"{attachment_id}-preview.webp", "image/webp"
            )
        now = utcnow_iso()
        return create_row_safe(TABLE_ID, row_id=attachment_id, data={
            "user_id": str(user_id),
            "scope_type": scope_type,
            "scope_id": str(scope_id),
            "message_id": "",
            "status": "pending",
            "original_filename": prepared["filename"],
            "mime_type": prepared["mime_type"],
            "kind": prepared["kind"],
            "original_size_bytes": int(original_size),
            "stored_size_bytes": prepared["stored_size_bytes"],
            "compression_encoding": prepared["compression_encoding"],
            "sha256": prepared["sha256"],
            "width": prepared["width"],
            "height": prepared["height"],
            "storage_bucket_id": CHAT_ATTACHMENTS_BUCKET_ID,
            "storage_file_id": storage_file_id,
            "preview_file_id": preview_file_id or "",
            "preview_size_bytes": len(prepared["preview"][0]) if prepared["preview"] else 0,
            "provider": "nest",
            "provider_metadata_json": "{}",
            "created_at": now,
            "updated_at": now,
        })
    except Exception:
        for file_id in (storage_file_id, preview_file_id):
            if file_id:
                try:
                    storage_service().delete_file(CHAT_ATTACHMENTS_BUCKET_ID, file_id)
                except AppwriteException:
                    logger.exception("Failed to roll back chat attachment storage")
        raise


def get_attachment(attachment_id):
    return get_row_safe(TABLE_ID, str(attachment_id), allow_missing=True)


def serialize_attachment(row):
    attachment_id = str(row.get("$id") or row.get("id") or "")
    return {
        "id": attachment_id,
        "filename": row.get("original_filename") or "attachment",
        "mime_type": row.get("mime_type") or "application/octet-stream",
        "kind": row.get("kind") or "file",
        "size_bytes": int(row.get("original_size_bytes") or 0),
        "stored_size_bytes": int(row.get("stored_size_bytes") or 0),
        "sha256": row.get("sha256") or "",
        "width": row.get("width"),
        "height": row.get("height"),
        "preview_url": f"/api/chat/attachments/{attachment_id}/preview" if row.get("kind") in {"image", "pdf"} else None,
        "download_url": f"/api/chat/attachments/{attachment_id}/download",
        "requires_download_warning": row.get("kind") != "image",
        "virus_total_url": f"https://www.virustotal.com/gui/file/{row.get('sha256')}" if row.get("sha256") else None,
    }


def attachments_for_messages(message_ids):
    if not os.environ.get("APPWRITE_CHAT_ATTACHMENTS_BUCKET_ID"):
        return {}
    ids = [str(value) for value in message_ids if value]
    if not ids:
        return {}
    try:
        rows = list_rows_all(TABLE_ID, [Query.equal("message_id", ids), Query.equal("status", ["active"])])
    except AppwriteException:
        logger.warning("Chat attachment metadata is unavailable; returning messages without attachments")
        return {}
    result = {}
    for row in rows:
        result.setdefault(str(row.get("message_id") or ""), []).append(serialize_attachment(row))
    return result


def bind_pending(attachment_ids, *, user_id, scope_type, scope_id, message_id):
    ids = list(dict.fromkeys(str(value) for value in attachment_ids if value))
    if len(ids) > MAX_ATTACHMENTS_PER_MESSAGE:
        raise AttachmentError("A message can include at most five attachments.")
    rows = []
    for attachment_id in ids:
        row = get_attachment(attachment_id)
        if not row or row.get("status") != "pending" or str(row.get("user_id")) != str(user_id):
            raise AttachmentError("An attachment is unavailable or no longer pending.")
        if row.get("scope_type") != scope_type or str(row.get("scope_id")) != str(scope_id):
            raise AttachmentError("An attachment belongs to a different conversation.")
        rows.append(row)
    now = utcnow_iso()
    activated = []
    try:
        for row in rows:
            row_id = str(row.get("$id") or row.get("id"))
            update_row_safe(TABLE_ID, row_id, {
                "message_id": str(message_id), "status": "active", "updated_at": now,
            })
            activated.append(row_id)
    except Exception:
        for row_id in activated:
            try:
                update_row_safe(TABLE_ID, row_id, {"message_id": "", "status": "pending", "updated_at": utcnow_iso()})
            except AppwriteException:
                logger.exception("Failed to roll back partially bound chat attachment")
        raise
    return rows


def attachment_bytes(row, *, preview=False):
    file_id = row.get("preview_file_id") if preview else row.get("storage_file_id")
    if not file_id:
        return None
    data = storage_service().get_file_download(row.get("storage_bucket_id") or CHAT_ATTACHMENTS_BUCKET_ID, file_id)
    if not preview and row.get("compression_encoding") == "gzip":
        return gzip.decompress(data)
    return data


def delete_attachment(row):
    if not row:
        return
    bucket_id = row.get("storage_bucket_id") or CHAT_ATTACHMENTS_BUCKET_ID
    for file_id in (row.get("storage_file_id"), row.get("preview_file_id")):
        if not file_id:
            continue
        try:
            storage_service().delete_file(bucket_id, file_id)
        except AppwriteException as exc:
            status = int(getattr(exc, "code", 0) or getattr(exc, "response_code", 0) or 0)
            if status != 404:
                logger.exception("Failed to delete chat attachment storage object")
    delete_row_safe(TABLE_ID, str(row.get("$id") or row.get("id")))


def delete_message_attachments(message_id):
    if not os.environ.get("APPWRITE_CHAT_ATTACHMENTS_BUCKET_ID"):
        return
    try:
        rows = list_rows_all(TABLE_ID, [Query.equal("message_id", [str(message_id)])])
    except AppwriteException:
        logger.warning("Chat attachment metadata is unavailable during message cleanup")
        return
    for row in rows:
        delete_attachment(row)


def delete_user_attachments(user_id):
    if not os.environ.get("APPWRITE_CHAT_ATTACHMENTS_BUCKET_ID"):
        return
    for row in list_rows_all(TABLE_ID, [Query.equal("user_id", [str(user_id)])]):
        delete_attachment(row)


def cleanup_abandoned_attachments(max_age_hours=24):
    if not os.environ.get("APPWRITE_CHAT_ATTACHMENTS_BUCKET_ID"):
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    rows = list_rows_all(TABLE_ID, [Query.equal("status", ["pending"])])
    deleted = 0
    for row in rows:
        raw_created = str(row.get("created_at") or "").replace("Z", "+00:00")
        try:
            created = datetime.fromisoformat(raw_created)
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
        except ValueError:
            created = datetime.min.replace(tzinfo=timezone.utc)
        if created <= cutoff:
            delete_attachment(row)
            deleted += 1
    return deleted
