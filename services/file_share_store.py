"""File-share persistence, folder, storage, and response helpers."""

import io
import logging
import secrets
import zipfile
from datetime import timezone

from flask import abort, render_template, send_file, url_for
from flask_login import current_user
from werkzeug.utils import secure_filename

from appwrite.exception import AppwriteException
from appwrite.query import Query
from appwrite.services.storage import Storage

from appwrite_client import COLLECTIONS, FILE_SHARE_BUCKET_ID, client as appwrite_client
from appwrite_helpers import (
    delete_row_safe,
    first_row,
    format_datetime,
    get_row_safe,
    list_rows_all,
    parse_datetime,
    update_row_safe,
)
from services.appwrite_storage import appwrite_upload_error
from services.row_utils import row_id as _row_id
from services.time_utils import utcnow as _utcnow


logger = logging.getLogger(__name__)

MAX_FILE_SIZE = 50 * 1024 * 1024
MAX_UPLOAD_FILES = 5
DEFAULT_EXPIRY_DAYS = 1
ALLOWED_EXPIRY_OPTIONS = [1, 3, 7, 14, 30]
SHARE_CODE_LENGTH = 24
SHARE_CODE_CHARS = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
APPWRITE_STORAGE_BACKEND = "appwrite"
ROOT_FOLDER_ID = "root"


def _status_code(exc):
    status = getattr(exc, "code", None)
    if status is None:
        status = getattr(exc, "response_code", None)
    try:
        return int(status or 0)
    except (TypeError, ValueError):
        return 0


def _appwrite_upload_error(exc):
    return appwrite_upload_error(exc)


def _folders_collection():
    return COLLECTIONS.get("file_folders", "file_folders")


def _storage():
    return Storage(appwrite_client)


def _normalize_folder_id(value):
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"root", "none", "null", "undefined"}:
        return None
    return text


def _parent_query(column, folder_id):
    normalized = _normalize_folder_id(folder_id)
    if normalized:
        return Query.equal(column, [normalized])
    return Query.is_null(column)


def _isoformat(value):
    if not value:
        return None
    parsed = parse_datetime(value)
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _format_expiry_display(value):
    if not value:
        return ""
    return value.strftime("%B %d, %Y at %I:%M %p UTC").replace(" 0", " ")


def _possessive(name):
    cleaned = (name or "Someone").strip() or "Someone"
    if cleaned.endswith(("s", "S")):
        return f"{cleaned}'"
    return f"{cleaned}'s"


def _human_readable_size(size_bytes):
    value = float(size_bytes or 0)
    units = ["B", "KB", "MB", "GB"]
    for unit in units:
        if value < 1024.0 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{int(size_bytes)} B"


def _share_url(share_code):
    if not share_code:
        return None
    try:
        return url_for("file_share.public_share", share_code=share_code, _external=True)
    except RuntimeError:
        return f"/files/share/{share_code}"


def _folder_share_url(share_code):
    if not share_code:
        return None
    try:
        return url_for("file_share.public_folder_share", share_code=share_code, _external=True)
    except RuntimeError:
        return f"/files/folder/{share_code}"


def _is_expired(shared_file):
    expires_at = parse_datetime(shared_file.get("expires_at"))
    if not expires_at:
        return True
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at <= _utcnow()


def _generate_share_code(
    *,
    first_row_fn=None,
    folders_collection_fn=None,
    log=None,
):
    first_row_fn = first_row_fn or first_row
    folders_collection_fn = folders_collection_fn or _folders_collection
    log = log or logger
    while True:
        code = "".join(secrets.choice(SHARE_CODE_CHARS) for _ in range(SHARE_CODE_LENGTH))
        try:
            existing_file = first_row_fn(
                COLLECTIONS["shared_files"],
                [Query.equal("share_code", [code])],
            )
            existing_folder = first_row_fn(
                folders_collection_fn(),
                [Query.equal("share_code", [code])],
            )
        except AppwriteException:
            log.exception("Failed to check share code")
            raise
        if not existing_file and not existing_folder:
            return code


def _shared_file_payload(shared_file):
    share_code = shared_file.get("share_code")
    return {
        "id": _row_id(shared_file),
        "type": "file",
        "filename": shared_file.get("original_filename"),
        "folderId": shared_file.get("folder_id"),
        "fileSizeBytes": shared_file.get("file_size_bytes"),
        "mimeType": shared_file.get("mime_type"),
        "isPublic": bool(shared_file.get("is_public")),
        "shareUrl": _share_url(share_code) if shared_file.get("is_public") and share_code else None,
        "expiresAt": _isoformat(shared_file.get("expires_at")),
        "createdAt": _isoformat(shared_file.get("created_at")),
        "updatedAt": _isoformat(shared_file.get("updated_at")),
        "downloads": shared_file.get("downloaded_count"),
        "storageBackend": shared_file.get("storage_backend") or APPWRITE_STORAGE_BACKEND,
    }


def _folder_payload(folder, *, folder_count=0, file_count=0):
    share_code = folder.get("share_code")
    return {
        "id": _row_id(folder),
        "type": "folder",
        "name": folder.get("name") or "Untitled Folder",
        "parentFolderId": folder.get("parent_folder_id"),
        "isPublic": bool(folder.get("is_public")),
        "shareUrl": _folder_share_url(share_code) if folder.get("is_public") and share_code else None,
        "order": folder.get("order") or 0,
        "createdAt": _isoformat(folder.get("created_at")),
        "updatedAt": _isoformat(folder.get("updated_at")),
        "folderCount": folder_count,
        "fileCount": file_count,
    }


def _owner_display(shared_file):
    owner = None
    try:
        owner = get_row_safe(COLLECTIONS["users"], shared_file.get("user_id"))
    except AppwriteException as exc:
        if _status_code(exc) != 404:
            logger.exception("Failed to load shared file owner")
    if owner and owner.get("name"):
        return _possessive(owner.get("name"))
    if owner and owner.get("email"):
        return _possessive(owner.get("email").split("@")[0])
    return _possessive("Someone")


def _render_public_share_page(shared_file=None, error_message=None):
    return render_template(
        "file_share_download.html",
        shared_file=shared_file,
        shared_by_name=_owner_display(shared_file) if shared_file else None,
        file_size_display=_human_readable_size(shared_file.get("file_size_bytes")) if shared_file else None,
        expires_at_display=_format_expiry_display(parse_datetime(shared_file.get("expires_at"))) if shared_file else None,
        download_url=url_for("file_share.public_share", share_code=shared_file.get("share_code"), download=1) if shared_file else None,
        error_message=error_message or "File not found or expired.",
    )


def _folder_owner_or_404(folder_id, user_id=None):
    normalized = _normalize_folder_id(folder_id)
    if not normalized:
        return None
    try:
        folder = get_row_safe(_folders_collection(), normalized, allow_missing=True)
    except AppwriteException:
        logger.exception("Failed to load file folder")
        abort(500)
    expected_user_id = user_id or str(current_user.id)
    if not folder or folder.get("user_id") != expected_user_id:
        abort(404)
    return folder


def _file_owner_or_404(file_id):
    try:
        shared_file = get_row_safe(COLLECTIONS["shared_files"], file_id)
    except AppwriteException as exc:
        if _status_code(exc) == 404:
            abort(404)
        logger.exception("Failed to load shared file")
        abort(500)
    if shared_file.get("user_id") != str(current_user.id):
        abort(404)
    return shared_file


def _assert_folder_target(user_id, folder_id):
    normalized = _normalize_folder_id(folder_id)
    if not normalized:
        return None
    return _folder_owner_or_404(normalized, user_id=user_id)


def _list_child_folders(user_id, folder_id):
    return list_rows_all(
        _folders_collection(),
        [
            Query.equal("user_id", [user_id]),
            _parent_query("parent_folder_id", folder_id),
            Query.order_asc("order"),
            Query.order_asc("created_at"),
        ],
    )


def _list_child_files(user_id, folder_id, *, include_expired=False):
    queries = [
        Query.equal("user_id", [user_id]),
        _parent_query("folder_id", folder_id),
    ]
    if not include_expired:
        queries.append(Query.greater_than("expires_at", format_datetime(_utcnow())))
    queries.append(Query.order_desc("created_at"))
    return list_rows_all(COLLECTIONS["shared_files"], queries)


def _list_all_user_folders(user_id):
    return list_rows_all(
        _folders_collection(),
        [
            Query.equal("user_id", [user_id]),
            Query.order_asc("order"),
            Query.order_asc("created_at"),
        ],
    )


def _list_all_user_files(user_id, *, include_expired=True):
    queries = [Query.equal("user_id", [user_id])]
    if not include_expired:
        queries.append(Query.greater_than("expires_at", format_datetime(_utcnow())))
    return list_rows_all(COLLECTIONS["shared_files"], queries)


def _sibling_order(user_id, parent_folder_id):
    siblings = _list_child_folders(user_id, parent_folder_id)
    return max((int(folder.get("order") or 0) for folder in siblings), default=0) + 1000


def _folder_breadcrumbs(user_id, folder_id):
    breadcrumbs = [{"id": None, "name": "My Files"}]
    normalized = _normalize_folder_id(folder_id)
    if not normalized:
        return breadcrumbs

    seen = set()
    current_id = normalized
    chain = []
    while current_id and current_id not in seen:
        seen.add(current_id)
        folder = _folder_owner_or_404(current_id, user_id=user_id)
        if not folder:
            break
        chain.append({"id": _row_id(folder), "name": folder.get("name") or "Untitled Folder"})
        current_id = folder.get("parent_folder_id")

    breadcrumbs.extend(reversed(chain))
    return breadcrumbs


def _folder_counts(folders, files):
    folder_counts = {}
    file_counts = {}
    for folder in folders:
        parent = folder.get("parent_folder_id")
        if parent:
            folder_counts[parent] = folder_counts.get(parent, 0) + 1
    for shared_file in files:
        parent = shared_file.get("folder_id")
        if parent:
            file_counts[parent] = file_counts.get(parent, 0) + 1
    return folder_counts, file_counts


def _is_descendant_folder(folders_by_id, folder_id, possible_descendant_id):
    current_id = _normalize_folder_id(possible_descendant_id)
    target_id = _normalize_folder_id(folder_id)
    seen = set()
    while current_id and current_id not in seen:
        if current_id == target_id:
            return True
        seen.add(current_id)
        current = folders_by_id.get(current_id)
        current_id = current.get("parent_folder_id") if current else None
    return False


def _collect_folder_tree_ids(user_id, root_folder_id):
    normalized_root = _normalize_folder_id(root_folder_id)
    if not normalized_root:
        return []
    folders = _list_all_user_folders(user_id)
    children_by_parent = {}
    for folder in folders:
        parent_id = folder.get("parent_folder_id")
        children_by_parent.setdefault(parent_id, []).append(_row_id(folder))

    collected = []
    stack = [normalized_root]
    seen = set()
    while stack:
        folder_id = stack.pop()
        if not folder_id or folder_id in seen:
            continue
        seen.add(folder_id)
        collected.append(folder_id)
        stack.extend(children_by_parent.get(folder_id, []))
    return collected


def _storage_path(storage_file_id):
    return f"appwrite://{FILE_SHARE_BUCKET_ID}/{storage_file_id}"


def _delete_storage_file(shared_file):
    storage_file_id = shared_file.get("storage_file_id")
    if not storage_file_id:
        return
    bucket_id = shared_file.get("storage_bucket_id") or FILE_SHARE_BUCKET_ID
    try:
        _storage().delete_file(bucket_id, storage_file_id)
    except AppwriteException as exc:
        if _status_code(exc) == 404:
            return
        raise


def _delete_shared_file_row(shared_file):
    _delete_storage_file(shared_file)
    delete_row_safe(COLLECTIONS["shared_files"], _row_id(shared_file))


def _storage_download_bytes(shared_file):
    storage_file_id = shared_file.get("storage_file_id")
    if not storage_file_id:
        raise FileNotFoundError("Missing Appwrite storage file id")
    bucket_id = shared_file.get("storage_bucket_id") or FILE_SHARE_BUCKET_ID
    try:
        return _storage().get_file_download(bucket_id, storage_file_id)
    except AppwriteException as exc:
        if _status_code(exc) == 404:
            raise FileNotFoundError(storage_file_id) from exc
        raise


def _send_shared_file(shared_file):
    data = _storage_download_bytes(shared_file)
    try:
        update_row_safe(
            COLLECTIONS["shared_files"],
            _row_id(shared_file),
            {
                "downloaded_count": int(shared_file.get("downloaded_count") or 0) + 1,
                "updated_at": format_datetime(_utcnow()),
            },
        )
    except AppwriteException:
        logger.exception("Failed to update download count for shared file %s", _row_id(shared_file))

    return send_file(
        io.BytesIO(data),
        as_attachment=True,
        download_name=shared_file.get("original_filename"),
        mimetype=shared_file.get("mime_type") or "application/octet-stream",
    )


def _zip_arcname(filename, used_names):
    base = secure_filename(filename or "file") or "file"
    if base not in used_names:
        used_names.add(base)
        return base
    stem, dot, suffix = base.partition(".")
    counter = 2
    while True:
        candidate = f"{stem}-{counter}{dot}{suffix}" if dot else f"{stem}-{counter}"
        if candidate not in used_names:
            used_names.add(candidate)
            return candidate
        counter += 1


def _zip_response(
    folder_name,
    files,
    *,
    is_expired_fn=None,
    storage_download_bytes_fn=None,
    row_id_fn=None,
    log=None,
    send_file_fn=None,
    secure_filename_fn=None,
):
    is_expired_fn = is_expired_fn or _is_expired
    storage_download_bytes_fn = storage_download_bytes_fn or _storage_download_bytes
    row_id_fn = row_id_fn or _row_id
    log = log or logger
    send_file_fn = send_file_fn or send_file
    secure_filename_fn = secure_filename_fn or secure_filename

    buffer = io.BytesIO()
    used_names = set()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for shared_file in files:
            if is_expired_fn(shared_file):
                continue
            try:
                data = storage_download_bytes_fn(shared_file)
            except FileNotFoundError:
                log.info("Skipping missing file in folder zip: %s", row_id_fn(shared_file))
                continue
            archive.writestr(_zip_arcname(shared_file.get("original_filename"), used_names), data)
    buffer.seek(0)
    safe_name = secure_filename_fn(folder_name or "folder") or "folder"
    return send_file_fn(
        buffer,
        as_attachment=True,
        download_name=f"{safe_name}.zip",
        mimetype="application/zip",
    )


def _public_folder_by_code(share_code):
    try:
        folder = first_row(
            _folders_collection(),
            [
                Query.equal("share_code", [share_code]),
                Query.equal("is_public", [True]),
            ],
        )
    except AppwriteException:
        logger.exception("Failed to resolve public folder")
        return None
    return folder


def _build_public_folder_tree(root_folder, share_code):
    user_id = root_folder.get("user_id")
    folder_ids = set(_collect_folder_tree_ids(user_id, _row_id(root_folder)))
    all_folders = [folder for folder in _list_all_user_folders(user_id) if _row_id(folder) in folder_ids]
    all_files = [
        shared_file
        for shared_file in _list_all_user_files(user_id, include_expired=False)
        if shared_file.get("folder_id") in folder_ids
    ]

    children_by_parent = {}
    files_by_parent = {}
    for folder in all_folders:
        children_by_parent.setdefault(folder.get("parent_folder_id"), []).append(folder)
    for shared_file in all_files:
        files_by_parent.setdefault(shared_file.get("folder_id"), []).append(shared_file)

    def build_node(folder):
        folder_id = _row_id(folder)
        child_folders = children_by_parent.get(folder_id, [])
        direct_files = files_by_parent.get(folder_id, [])
        return {
            "id": folder_id,
            "name": folder.get("name") or "Untitled Folder",
            "zipUrl": url_for(
                "file_share.public_folder_share",
                share_code=share_code,
                download="zip",
                folderId=folder_id,
            ),
            "files": [
                {
                    **_shared_file_payload(shared_file),
                    "downloadUrl": url_for(
                        "file_share.public_folder_file_download",
                        share_code=share_code,
                        file_id=_row_id(shared_file),
                    ),
                    "fileSizeDisplay": _human_readable_size(shared_file.get("file_size_bytes")),
                }
                for shared_file in direct_files
            ],
            "folders": [build_node(child) for child in child_folders],
        }

    return build_node(root_folder), folder_ids


def upload_file_response(user, files, form, dependencies):
    jsonify_fn = dependencies["jsonify"]
    if not files:
        return jsonify_fn({"error": "At least one file is required."}), 400

    user_id = str(user.id)
    entitlement_limit_error = dependencies["entitlement_limit_error"]
    entitlement_error = dependencies["entitlement_error"]
    try:
        entitlements = dependencies["request_entitlements"](user)
        plan_upload_files = entitlements["limits"].get("max_upload_files")
        plan_file_size = entitlements["limits"].get("max_file_size_bytes")
        effective_upload_files = (
            min(dependencies["max_upload_files"], plan_upload_files)
            if plan_upload_files is not None
            else dependencies["max_upload_files"]
        )
        effective_file_size = (
            min(dependencies["max_file_size"], plan_file_size)
            if plan_file_size is not None
            else dependencies["max_file_size"]
        )
        if plan_upload_files is not None and len(files) > plan_upload_files:
            raise entitlement_limit_error(
                "files per upload",
                0,
                len(files),
                plan_upload_files,
            )
    except entitlement_limit_error as exc:
        return jsonify_fn(exc.payload()), 403
    except entitlement_error:
        dependencies["logger"].exception("Failed to calculate file upload limits")
        return jsonify_fn({
            "error": "Unable to verify your storage limits right now.",
            "code": "tier_check_unavailable",
        }), 503

    folder_id = dependencies["normalize_folder_id"](form.get("folderId"))
    dependencies["assert_folder_target"](user_id, folder_id)

    filenames = form.getlist("filename")
    visibilities = form.getlist("visibility")
    expiries = form.getlist("expiryDays")

    total_provided = len(files)
    to_process = files[:effective_upload_files]
    skipped = total_provided - len(to_process)

    created = []
    errors = []
    reserved_storage_bytes = 0

    for idx, uploaded_file in enumerate(to_process):
        if not uploaded_file or not uploaded_file.filename:
            errors.append({"index": idx, "error": "Missing file or filename."})
            continue

        custom_filename = (filenames[idx] if idx < len(filenames) else "") or ""
        visibility = ((visibilities[idx] if idx < len(visibilities) else "private") or "private").strip().lower()
        try:
            expiry_days = (
                int(expiries[idx])
                if idx < len(expiries)
                else dependencies["default_expiry_days"]
            )
        except (TypeError, ValueError):
            expiry_days = dependencies["default_expiry_days"]

        if visibility not in {"public", "private"}:
            errors.append({"index": idx, "error": "Invalid visibility option."})
            continue
        if expiry_days not in dependencies["allowed_expiry_options"]:
            errors.append({"index": idx, "error": "Invalid expiry selection."})
            continue

        display_filename = custom_filename.strip() or uploaded_file.filename
        uploaded_data = uploaded_file.read()
        file_size_bytes = len(uploaded_data)
        if plan_file_size is not None and file_size_bytes > plan_file_size:
            quota_error = entitlement_limit_error(
                "file size bytes",
                0,
                file_size_bytes,
                plan_file_size,
            )
            errors.append({"index": idx, **quota_error.payload()})
            continue
        if file_size_bytes > effective_file_size:
            errors.append({
                "index": idx,
                "error": f"{uploaded_file.filename} exceeds the current file-size limit.",
                "code": "file_too_large",
            })
            continue
        if file_size_bytes == 0:
            errors.append({"index": idx, "error": f"{uploaded_file.filename} is empty."})
            continue

        try:
            dependencies["check_storage"](
                entitlements,
                reserved_storage_bytes + file_size_bytes,
            )
            reserved_storage_bytes += file_size_bytes
        except entitlement_limit_error as exc:
            errors.append({"index": idx, **exc.payload()})
            continue

        file_id = str(dependencies["uuid4"]())
        storage_file_id = file_id
        sanitized_name = dependencies["secure_filename"](display_filename) or "file"

        try:
            dependencies["storage"]().create_file(
                dependencies["bucket_id"],
                storage_file_id,
                dependencies["input_file_from_bytes"](
                    uploaded_data,
                    filename=sanitized_name,
                    mime_type=uploaded_file.mimetype or "application/octet-stream",
                ),
            )
        except AppwriteException as exc:
            dependencies["logger"].exception("Failed to upload file to Appwrite Storage")
            reserved_storage_bytes -= file_size_bytes
            errors.append({
                "index": idx,
                "error": dependencies["appwrite_upload_error"](exc),
            })
            continue

        try:
            is_public = visibility == "public"
            share_code = dependencies["generate_share_code"]() if is_public else None
            now = dependencies["utcnow"]()
            expires_at = now + dependencies["timedelta"](days=expiry_days)
            shared_file = dependencies["create_row_safe"](
                COLLECTIONS["shared_files"],
                row_id=file_id,
                data={
                    "user_id": user_id,
                    "folder_id": folder_id,
                    "original_filename": display_filename,
                    "stored_path": dependencies["storage_path"](storage_file_id),
                    "storage_backend": dependencies["storage_backend"],
                    "storage_bucket_id": dependencies["bucket_id"],
                    "storage_file_id": storage_file_id,
                    "file_size_bytes": file_size_bytes,
                    "mime_type": uploaded_file.mimetype,
                    "share_code": share_code,
                    "is_public": is_public,
                    "expires_at": dependencies["format_datetime"](expires_at),
                    "created_at": dependencies["format_datetime"](now),
                    "updated_at": dependencies["format_datetime"](now),
                    "downloaded_count": 0,
                },
            )
        except AppwriteException:
            dependencies["logger"].exception(
                "Failed to save shared file row for upload %s",
                display_filename,
            )
            reserved_storage_bytes -= file_size_bytes
            try:
                dependencies["storage"]().delete_file(
                    dependencies["bucket_id"],
                    storage_file_id,
                )
            except AppwriteException:
                dependencies["logger"].exception(
                    "Failed to clean up uploaded Appwrite file %s after row failure",
                    storage_file_id,
                )
            errors.append({"index": idx, "error": "Unable to save file."})
            continue

        created.append(dependencies["shared_file_payload"](shared_file))
        dependencies["emit_creation_event"](
            "Shared File Created",
            actor=dependencies["format_actor"](user),
            target=display_filename,
            metadata={
                "page_context": "files/upload",
                "resource_type": "shared_file",
                "resource_id": shared_file.get("$id") or shared_file.get("id"),
                "folder_id": folder_id,
                "is_public": is_public,
                "file_size_bytes": file_size_bytes,
                "mime_type": uploaded_file.mimetype,
                "expiry_days": expiry_days,
            },
            color="green",
        )

    response = {"files": created}
    if skipped:
        response["skipped"] = skipped
        response.setdefault("errors", []).append({
            "error": (
                f"Only {effective_upload_files} files are accepted; "
                f"{skipped} file(s) were ignored."
            ),
        })
    if errors:
        response.setdefault("errors", []).extend(errors)
        if not created:
            response["error"] = errors[0].get("error") or "Upload failed."

    return jsonify_fn(response), 201 if created else 400
