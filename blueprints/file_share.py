import io
import logging
import secrets
import uuid
import zipfile
from datetime import datetime, timedelta, timezone

from flask import (
    Blueprint,
    abort,
    jsonify,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_login import current_user, login_required
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.utils import secure_filename

from appwrite.exception import AppwriteException
from appwrite.input_file import InputFile
from appwrite.query import Query
from appwrite.services.storage import Storage
from appwrite_client import COLLECTIONS, FILE_SHARE_BUCKET_ID, client as appwrite_client
from appwrite_helpers import (
    create_row_safe,
    delete_row_safe,
    first_row,
    format_datetime,
    get_row_safe,
    list_rows_all,
    parse_datetime,
    update_row_safe,
)
from services.discord_audit import emit_creation_event, format_actor
from services.appwrite_storage import appwrite_upload_error
from services.entitlements import (
    EntitlementError,
    EntitlementLimitError,
    check_limit,
    check_storage,
    request_entitlements,
)


file_share_bp = Blueprint("file_share", __name__)
logger = logging.getLogger(__name__)

MAX_FILE_SIZE = 50 * 1024 * 1024
MAX_UPLOAD_FILES = 5
DEFAULT_EXPIRY_DAYS = 1
ALLOWED_EXPIRY_OPTIONS = [1, 3, 7, 14, 30]
SHARE_CODE_LENGTH = 24
SHARE_CODE_CHARS = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
APPWRITE_STORAGE_BACKEND = "appwrite"
ROOT_FOLDER_ID = "root"


def _utcnow():
    return datetime.now(timezone.utc)


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


def _row_id(row):
    return row.get("$id") or row.get("id")


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


def _generate_share_code():
    while True:
        code = "".join(secrets.choice(SHARE_CODE_CHARS) for _ in range(SHARE_CODE_LENGTH))
        try:
            existing_file = first_row(
                COLLECTIONS["shared_files"],
                [Query.equal("share_code", [code])],
            )
            existing_folder = first_row(
                _folders_collection(),
                [Query.equal("share_code", [code])],
            )
        except AppwriteException:
            logger.exception("Failed to check share code")
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


def _zip_response(folder_name, files):
    buffer = io.BytesIO()
    used_names = set()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for shared_file in files:
            if _is_expired(shared_file):
                continue
            try:
                data = _storage_download_bytes(shared_file)
            except FileNotFoundError:
                logger.info("Skipping missing file in folder zip: %s", _row_id(shared_file))
                continue
            archive.writestr(_zip_arcname(shared_file.get("original_filename"), used_names), data)
    buffer.seek(0)
    safe_name = secure_filename(folder_name or "folder") or "folder"
    return send_file(
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


@file_share_bp.errorhandler(RequestEntityTooLarge)
def handle_file_too_large(_error):
    return jsonify({"error": "The upload exceeds the current file-size or request limit.", "code": "file_too_large"}), 413


@file_share_bp.route("/files")
@login_required
def file_share_page():
    try:
        user_settings = first_row(
            COLLECTIONS["user_settings"],
            [Query.equal("user_id", [str(current_user.id)])],
        )
    except AppwriteException:
        logger.exception("Failed to load file share settings")
        user_settings = None
    try:
        entitlements = request_entitlements(current_user)
    except EntitlementError:
        logger.exception("Failed to load file tier limits")
        entitlements = {"limits": {}}
    plan_file_size = entitlements["limits"].get("max_file_size_bytes")
    plan_upload_files = entitlements["limits"].get("max_upload_files")
    effective_file_size = min(MAX_FILE_SIZE, plan_file_size) if plan_file_size is not None else MAX_FILE_SIZE
    effective_upload_files = min(MAX_UPLOAD_FILES, plan_upload_files) if plan_upload_files is not None else MAX_UPLOAD_FILES
    return render_template(
        "files.html",
        user={
            "name": current_user.name,
            "email": current_user.email,
            "picture": current_user.picture_url,
            "emory_student": current_user.emory_student,
        },
        max_file_size=effective_file_size,
        max_file_size_label=_human_readable_size(effective_file_size),
        max_upload_files=effective_upload_files,
        allowed_expiry_options=ALLOWED_EXPIRY_OPTIONS,
        default_expiry_days=DEFAULT_EXPIRY_DAYS,
        theme_preference=user_settings.get("interface_theme") if user_settings else None,
    )


@file_share_bp.route("/api/files/upload", methods=["POST"])
@login_required
def upload_file():
    files = request.files.getlist("file")
    if not files:
        return jsonify({"error": "At least one file is required."}), 400

    user_id = str(current_user.id)
    try:
        entitlements = request_entitlements(current_user)
        plan_upload_files = entitlements["limits"].get("max_upload_files")
        plan_file_size = entitlements["limits"].get("max_file_size_bytes")
        effective_upload_files = min(MAX_UPLOAD_FILES, plan_upload_files) if plan_upload_files is not None else MAX_UPLOAD_FILES
        effective_file_size = min(MAX_FILE_SIZE, plan_file_size) if plan_file_size is not None else MAX_FILE_SIZE
        if plan_upload_files is not None and len(files) > plan_upload_files:
            raise EntitlementLimitError("files per upload", 0, len(files), plan_upload_files)
    except EntitlementLimitError as exc:
        return jsonify(exc.payload()), 403
    except EntitlementError:
        logger.exception("Failed to calculate file upload limits")
        return jsonify({"error": "Unable to verify your storage limits right now.", "code": "tier_check_unavailable"}), 503
    folder_id = _normalize_folder_id(request.form.get("folderId"))
    _assert_folder_target(user_id, folder_id)

    filenames = request.form.getlist("filename")
    visibilities = request.form.getlist("visibility")
    expiries = request.form.getlist("expiryDays")

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
            expiry_days = int(expiries[idx]) if idx < len(expiries) else DEFAULT_EXPIRY_DAYS
        except (TypeError, ValueError):
            expiry_days = DEFAULT_EXPIRY_DAYS

        if visibility not in {"public", "private"}:
            errors.append({"index": idx, "error": "Invalid visibility option."})
            continue
        if expiry_days not in ALLOWED_EXPIRY_OPTIONS:
            errors.append({"index": idx, "error": "Invalid expiry selection."})
            continue

        display_filename = custom_filename.strip() or uploaded_file.filename
        uploaded_data = uploaded_file.read()
        file_size_bytes = len(uploaded_data)
        if plan_file_size is not None and file_size_bytes > plan_file_size:
            quota_error = EntitlementLimitError("file size bytes", 0, file_size_bytes, plan_file_size)
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
            check_storage(entitlements, reserved_storage_bytes + file_size_bytes)
            reserved_storage_bytes += file_size_bytes
        except EntitlementLimitError as exc:
            errors.append({"index": idx, **exc.payload()})
            continue

        file_id = str(uuid.uuid4())
        storage_file_id = file_id
        sanitized_name = secure_filename(display_filename) or "file"

        try:
            _storage().create_file(
                FILE_SHARE_BUCKET_ID,
                storage_file_id,
                InputFile.from_bytes(
                    uploaded_data,
                    filename=sanitized_name,
                    mime_type=uploaded_file.mimetype or "application/octet-stream",
                ),
            )
        except AppwriteException as exc:
            logger.exception("Failed to upload file to Appwrite Storage")
            reserved_storage_bytes -= file_size_bytes
            errors.append({"index": idx, "error": _appwrite_upload_error(exc)})
            continue

        is_public = visibility == "public"
        share_code = _generate_share_code() if is_public else None
        now = _utcnow()
        expires_at = now + timedelta(days=expiry_days)

        try:
            shared_file = create_row_safe(
                COLLECTIONS["shared_files"],
                row_id=file_id,
                data={
                    "user_id": user_id,
                    "folder_id": folder_id,
                    "original_filename": display_filename,
                    "stored_path": _storage_path(storage_file_id),
                    "storage_backend": APPWRITE_STORAGE_BACKEND,
                    "storage_bucket_id": FILE_SHARE_BUCKET_ID,
                    "storage_file_id": storage_file_id,
                    "file_size_bytes": file_size_bytes,
                    "mime_type": uploaded_file.mimetype,
                    "share_code": share_code,
                    "is_public": is_public,
                    "expires_at": format_datetime(expires_at),
                    "created_at": format_datetime(now),
                    "updated_at": format_datetime(now),
                    "downloaded_count": 0,
                },
            )
        except AppwriteException:
            logger.exception("Failed to save shared file row for upload %s", display_filename)
            reserved_storage_bytes -= file_size_bytes
            try:
                _storage().delete_file(FILE_SHARE_BUCKET_ID, storage_file_id)
            except AppwriteException:
                logger.exception(
                    "Failed to clean up uploaded Appwrite file %s after row failure",
                    storage_file_id,
                )
            errors.append({"index": idx, "error": "Unable to save file."})
            continue

        created.append(_shared_file_payload(shared_file))
        emit_creation_event(
            "Shared File Created",
            actor=format_actor(current_user),
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
        response.setdefault("errors", []).append(
            {"error": f"Only {effective_upload_files} files are accepted; {skipped} file(s) were ignored."}
        )
    if errors:
        response.setdefault("errors", []).extend(errors)
        if not created:
            response["error"] = errors[0].get("error") or "Upload failed."

    return jsonify(response), 201 if created else 400


@file_share_bp.route("/api/files/my")
@login_required
def my_files():
    user_id = str(current_user.id)
    folder_id = _normalize_folder_id(request.args.get("folderId"))
    _assert_folder_target(user_id, folder_id)

    try:
        child_folders = _list_child_folders(user_id, folder_id)
        child_files = _list_child_files(user_id, folder_id)
        all_folders = _list_all_user_folders(user_id)
        all_files = _list_all_user_files(user_id, include_expired=False)
    except AppwriteException:
        logger.exception("Failed to load shared files")
        return jsonify({"error": "Unable to load files."}), 500

    folder_counts, file_counts = _folder_counts(all_folders, all_files)
    current_folder = _folder_owner_or_404(folder_id, user_id=user_id) if folder_id else None

    return jsonify(
        {
            "currentFolder": _folder_payload(current_folder) if current_folder else None,
            "breadcrumbs": _folder_breadcrumbs(user_id, folder_id),
            "folders": [
                _folder_payload(
                    folder,
                    folder_count=folder_counts.get(_row_id(folder), 0),
                    file_count=file_counts.get(_row_id(folder), 0),
                )
                for folder in child_folders
            ],
            "files": [_shared_file_payload(shared_file) for shared_file in child_files],
            "allFolders": [_folder_payload(folder) for folder in all_folders],
        }
    )


@file_share_bp.route("/api/files/folders", methods=["POST"])
@login_required
def create_folder():
    payload = request.get_json(silent=True) or {}
    user_id = str(current_user.id)
    parent_folder_id = _normalize_folder_id(payload.get("parentFolderId"))
    _assert_folder_target(user_id, parent_folder_id)
    name = (payload.get("name") or "New Folder").strip() or "New Folder"
    now = format_datetime(_utcnow())

    try:
        created = create_row_safe(
            _folders_collection(),
            row_id=str(uuid.uuid4()),
            data={
                "user_id": user_id,
                "name": name[:255],
                "parent_folder_id": parent_folder_id,
                "is_public": False,
                "share_code": None,
                "order": _sibling_order(user_id, parent_folder_id),
                "created_at": now,
                "updated_at": now,
            },
        )
    except AppwriteException:
        logger.exception("Failed to create file folder")
        return jsonify({"error": "Unable to create folder."}), 500

    emit_creation_event(
        "File Folder Created",
        actor=format_actor(current_user),
        target=name,
        metadata={
            "page_context": "files/folders",
            "resource_type": "file_folder",
            "resource_id": created.get("$id") or created.get("id"),
            "parent_folder_id": parent_folder_id,
        },
        color="green",
    )
    return jsonify(_folder_payload(created)), 201


@file_share_bp.route("/api/files/folders/<folder_id>", methods=["PATCH"])
@login_required
def update_folder(folder_id):
    folder = _folder_owner_or_404(folder_id)
    payload = request.get_json(silent=True) or {}
    updates = {}

    if "name" in payload:
        updates["name"] = ((payload.get("name") or "").strip() or "Untitled Folder")[:255]

    if "parentFolderId" in payload:
        user_id = str(current_user.id)
        parent_folder_id = _normalize_folder_id(payload.get("parentFolderId"))
        _assert_folder_target(user_id, parent_folder_id)
        folders_by_id = {_row_id(item): item for item in _list_all_user_folders(user_id)}
        if parent_folder_id == _row_id(folder) or _is_descendant_folder(folders_by_id, _row_id(folder), parent_folder_id):
            return jsonify({"error": "A folder cannot be moved inside itself."}), 400
        updates["parent_folder_id"] = parent_folder_id

    if "order" in payload:
        updates["order"] = payload.get("order")
    elif "parentFolderId" in payload:
        updates["order"] = _sibling_order(str(current_user.id), updates["parent_folder_id"])

    if not updates:
        return jsonify({"error": "No updatable fields were provided."}), 400

    updates["updated_at"] = format_datetime(_utcnow())
    try:
        updated = update_row_safe(_folders_collection(), _row_id(folder), updates)
    except AppwriteException:
        logger.exception("Failed to update file folder")
        return jsonify({"error": "Unable to update folder."}), 500

    return jsonify(_folder_payload(updated))


@file_share_bp.route("/api/files/folders/<folder_id>/visibility", methods=["POST"])
@login_required
def change_folder_visibility(folder_id):
    folder = _folder_owner_or_404(folder_id)
    payload = request.get_json(silent=True) or request.form
    visibility = (payload.get("visibility") or "").strip().lower()
    if visibility not in {"public", "private"}:
        return jsonify({"error": "Invalid visibility option."}), 400

    updates = {"updated_at": format_datetime(_utcnow())}
    if visibility == "public":
        updates["is_public"] = True
        updates["share_code"] = folder.get("share_code") or _generate_share_code()
    else:
        updates["is_public"] = False
        updates["share_code"] = None

    try:
        updated = update_row_safe(_folders_collection(), _row_id(folder), updates)
    except AppwriteException:
        logger.exception("Failed to update folder visibility")
        return jsonify({"error": "Unable to update visibility."}), 500

    return jsonify(_folder_payload(updated))


@file_share_bp.route("/api/files/folders/<folder_id>/download.zip")
@login_required
def download_folder_zip(folder_id):
    user_id = str(current_user.id)
    normalized = _normalize_folder_id(folder_id)
    folder = _folder_owner_or_404(normalized, user_id=user_id) if normalized else None
    try:
        files = _list_child_files(user_id, normalized)
    except AppwriteException:
        logger.exception("Failed to load folder files for zip")
        abort(500)
    return _zip_response(folder.get("name") if folder else "My Files", files)


@file_share_bp.route("/api/files/folders/<folder_id>", methods=["DELETE"])
@login_required
def delete_folder(folder_id):
    folder = _folder_owner_or_404(folder_id)
    user_id = str(current_user.id)
    try:
        folder_ids = _collect_folder_tree_ids(user_id, _row_id(folder))
        files = [
            shared_file
            for shared_file in _list_all_user_files(user_id, include_expired=True)
            if shared_file.get("folder_id") in folder_ids
        ]
        for shared_file in files:
            _delete_shared_file_row(shared_file)
        for descendant_id in reversed(folder_ids):
            delete_row_safe(_folders_collection(), descendant_id)
    except AppwriteException:
        logger.exception("Failed to delete file folder")
        return jsonify({"error": "Unable to delete folder."}), 500

    return jsonify({"ok": True})


@file_share_bp.route("/api/files/my/<file_id>", methods=["PATCH"])
@login_required
def update_my_file(file_id):
    shared_file = _file_owner_or_404(file_id)
    payload = request.get_json(silent=True) or {}
    updates = {}

    if "filename" in payload:
        filename = ((payload.get("filename") or "").strip() or "Untitled file")[:255]
        updates["original_filename"] = filename

    if "folderId" in payload:
        folder_id = _normalize_folder_id(payload.get("folderId"))
        _assert_folder_target(str(current_user.id), folder_id)
        updates["folder_id"] = folder_id

    if "expiryDays" in payload:
        try:
            expiry_days = int(payload.get("expiryDays"))
        except (TypeError, ValueError):
            return jsonify({"error": "Invalid expiry selection."}), 400
        if expiry_days not in ALLOWED_EXPIRY_OPTIONS:
            return jsonify({"error": "Invalid expiry selection."}), 400
        updates["expires_at"] = format_datetime(_utcnow() + timedelta(days=expiry_days))

    if not updates:
        return jsonify({"error": "No updatable fields were provided."}), 400

    updates["updated_at"] = format_datetime(_utcnow())
    try:
        updated = update_row_safe(COLLECTIONS["shared_files"], _row_id(shared_file), updates)
    except AppwriteException:
        logger.exception("Failed to update shared file")
        return jsonify({"error": "Unable to update file."}), 500

    return jsonify(_shared_file_payload(updated))


@file_share_bp.route("/api/files/my/<file_id>/visibility", methods=["POST"])
@login_required
def change_visibility(file_id):
    shared_file = _file_owner_or_404(file_id)
    data = request.get_json(silent=True) or request.form
    visibility = (data.get("visibility") or "").strip().lower()
    if visibility not in {"public", "private"}:
        return jsonify({"error": "Invalid visibility option."}), 400

    updates = {
        "updated_at": format_datetime(_utcnow()),
        "is_public": visibility == "public",
        "share_code": (
            shared_file.get("share_code") or _generate_share_code()
            if visibility == "public"
            else None
        ),
    }

    try:
        shared_file = update_row_safe(COLLECTIONS["shared_files"], _row_id(shared_file), updates)
    except AppwriteException:
        logger.exception("Failed to update file visibility")
        return jsonify({"error": "Unable to update visibility."}), 500

    return jsonify(_shared_file_payload(shared_file)), 200


@file_share_bp.route("/api/files/my/<file_id>/download")
@login_required
def download_my_file(file_id):
    shared_file = _file_owner_or_404(file_id)
    if _is_expired(shared_file):
        abort(404)

    try:
        return _send_shared_file(shared_file)
    except FileNotFoundError:
        abort(404)
    except AppwriteException:
        logger.exception("Failed to download shared file")
        abort(500)


@file_share_bp.route("/api/files/bulk-download.zip", methods=["POST"])
@login_required
def bulk_download_files():
    payload = request.get_json(silent=True) or {}
    raw_file_ids = payload.get("fileIds") or []
    if not isinstance(raw_file_ids, list):
        return jsonify({"error": "fileIds must be a list."}), 400

    file_ids = []
    seen_ids = set()
    for raw_file_id in raw_file_ids:
        file_id = str(raw_file_id or "").strip()
        if file_id and file_id not in seen_ids:
            seen_ids.add(file_id)
            file_ids.append(file_id)

    if not file_ids:
        return jsonify({"error": "Select at least one file to download."}), 400
    if len(file_ids) > 200:
        return jsonify({"error": "Select 200 files or fewer."}), 400

    user_id = str(current_user.id)
    selected_files = []
    try:
        for file_id in file_ids:
            shared_file = get_row_safe(COLLECTIONS["shared_files"], file_id, allow_missing=True)
            if (
                shared_file
                and shared_file.get("user_id") == user_id
                and not _is_expired(shared_file)
            ):
                selected_files.append(shared_file)
    except AppwriteException:
        logger.exception("Failed to load files for bulk download")
        return jsonify({"error": "Unable to prepare download."}), 500

    if not selected_files:
        return jsonify({"error": "No downloadable files were selected."}), 404

    return _zip_response("file-share-selected", selected_files)


@file_share_bp.route("/api/files/my/<file_id>", methods=["DELETE"])
@login_required
def delete_my_file(file_id):
    shared_file = _file_owner_or_404(file_id)
    try:
        _delete_shared_file_row(shared_file)
    except AppwriteException:
        logger.exception("Failed to delete shared file")
        return jsonify({"error": "Unable to delete file."}), 500
    return jsonify({"message": "File deleted."})


@file_share_bp.route("/files/share/<share_code>")
def public_share(share_code):
    try:
        shared_file = first_row(
            COLLECTIONS["shared_files"],
            [
                Query.equal("share_code", [share_code]),
                Query.equal("is_public", [True]),
            ],
        )
    except AppwriteException:
        logger.exception("Failed to resolve public share")
        return _render_public_share_page(error_message="File not found or expired.")

    if not shared_file or _is_expired(shared_file):
        return _render_public_share_page(error_message="File not found or expired.")

    if request.args.get("download"):
        try:
            return _send_shared_file(shared_file)
        except FileNotFoundError:
            return _render_public_share_page(error_message="File not found or expired.")
        except AppwriteException:
            logger.exception("Failed to download public share")
            return _render_public_share_page(error_message="File not found or expired.")

    return _render_public_share_page(shared_file=shared_file)


@file_share_bp.route("/files/folder/<share_code>")
def public_folder_share(share_code):
    root_folder = _public_folder_by_code(share_code)
    if not root_folder:
        return render_template(
            "file_share_folder.html",
            folder_tree=None,
            error_message="Folder not found or no longer shared.",
        )

    try:
        folder_tree, folder_ids = _build_public_folder_tree(root_folder, share_code)
    except AppwriteException:
        logger.exception("Failed to load public folder tree")
        return render_template(
            "file_share_folder.html",
            folder_tree=None,
            error_message="Unable to load this shared folder right now.",
        )

    if request.args.get("download") == "zip":
        target_folder_id = _normalize_folder_id(request.args.get("folderId")) or _row_id(root_folder)
        if target_folder_id not in folder_ids:
            abort(404)
        try:
            target_folder = get_row_safe(_folders_collection(), target_folder_id)
            files = _list_child_files(root_folder.get("user_id"), target_folder_id)
        except AppwriteException:
            logger.exception("Failed to load public folder zip")
            abort(500)
        return _zip_response(target_folder.get("name"), files)

    return render_template(
        "file_share_folder.html",
        folder_tree=folder_tree,
        error_message=None,
    )


@file_share_bp.route("/files/folder/<share_code>/download/<file_id>")
def public_folder_file_download(share_code, file_id):
    root_folder = _public_folder_by_code(share_code)
    if not root_folder:
        abort(404)
    try:
        folder_ids = set(_collect_folder_tree_ids(root_folder.get("user_id"), _row_id(root_folder)))
        shared_file = get_row_safe(COLLECTIONS["shared_files"], file_id)
    except AppwriteException:
        logger.exception("Failed to resolve public folder file")
        abort(500)
    if (
        not shared_file
        or shared_file.get("user_id") != root_folder.get("user_id")
        or shared_file.get("folder_id") not in folder_ids
        or _is_expired(shared_file)
    ):
        abort(404)
    try:
        return _send_shared_file(shared_file)
    except FileNotFoundError:
        abort(404)
    except AppwriteException:
        logger.exception("Failed to download public folder file")
        abort(500)
