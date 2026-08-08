import logging
import uuid
from datetime import timedelta

from flask import (
    Blueprint,
    abort,
    jsonify,
    render_template,
    request,
    send_file,
)
from flask_login import current_user, login_required
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.utils import secure_filename

from appwrite.exception import AppwriteException
from appwrite.input_file import InputFile
from appwrite.query import Query
from appwrite_client import COLLECTIONS, FILE_SHARE_BUCKET_ID
from appwrite_helpers import (
    create_row_safe,
    delete_row_safe,
    first_row,
    format_datetime,
    get_row_safe,
    list_rows_all,
    update_row_safe,
)
from services.discord_audit import emit_creation_event, format_actor
from services.entitlements import (
    EntitlementError,
    EntitlementLimitError,
    check_storage,
    request_entitlements,
)
from services.file_share_store import (
    ALLOWED_EXPIRY_OPTIONS,
    APPWRITE_STORAGE_BACKEND,
    DEFAULT_EXPIRY_DAYS,
    MAX_FILE_SIZE,
    MAX_UPLOAD_FILES,
    ROOT_FOLDER_ID,
    SHARE_CODE_CHARS,
    SHARE_CODE_LENGTH,
    _appwrite_upload_error,
    _assert_folder_target,
    _build_public_folder_tree,
    _collect_folder_tree_ids,
    _delete_shared_file_row,
    _delete_storage_file,
    _file_owner_or_404,
    _folder_breadcrumbs,
    _folder_counts,
    _folder_owner_or_404,
    _folder_payload,
    _folder_share_url,
    _folders_collection,
    _format_expiry_display,
    _generate_share_code as _generate_share_code_service,
    _human_readable_size,
    _is_descendant_folder,
    _is_expired,
    _isoformat,
    _list_all_user_files,
    _list_all_user_folders,
    _list_child_files,
    _list_child_folders,
    _normalize_folder_id,
    _owner_display,
    _parent_query,
    _possessive,
    _public_folder_by_code,
    _render_public_share_page,
    _send_shared_file,
    _share_url,
    _shared_file_payload,
    _sibling_order,
    _status_code,
    _storage,
    _storage_download_bytes,
    _storage_path,
    _zip_arcname,
    _zip_response as _zip_response_service,
    upload_file_response,
)
from services.row_utils import row_id as _row_id
from services.time_utils import utcnow as _utcnow


file_share_bp = Blueprint("file_share", __name__)
logger = logging.getLogger(__name__)

def _generate_share_code():
    return _generate_share_code_service(
        first_row_fn=first_row,
        folders_collection_fn=_folders_collection,
        log=logger,
    )


def _zip_response(folder_name, files):
    return _zip_response_service(
        folder_name,
        files,
        is_expired_fn=_is_expired,
        storage_download_bytes_fn=_storage_download_bytes,
        row_id_fn=_row_id,
        log=logger,
        send_file_fn=send_file,
        secure_filename_fn=secure_filename,
    )


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


def _upload_file_dependencies():
    return {
        "allowed_expiry_options": ALLOWED_EXPIRY_OPTIONS,
        "appwrite_upload_error": _appwrite_upload_error,
        "assert_folder_target": _assert_folder_target,
        "bucket_id": FILE_SHARE_BUCKET_ID,
        "check_storage": check_storage,
        "create_row_safe": create_row_safe,
        "default_expiry_days": DEFAULT_EXPIRY_DAYS,
        "emit_creation_event": emit_creation_event,
        "entitlement_error": EntitlementError,
        "entitlement_limit_error": EntitlementLimitError,
        "format_actor": format_actor,
        "format_datetime": format_datetime,
        "generate_share_code": _generate_share_code,
        "input_file_from_bytes": InputFile.from_bytes,
        "jsonify": jsonify,
        "logger": logger,
        "max_file_size": MAX_FILE_SIZE,
        "max_upload_files": MAX_UPLOAD_FILES,
        "normalize_folder_id": _normalize_folder_id,
        "request_entitlements": request_entitlements,
        "secure_filename": secure_filename,
        "shared_file_payload": _shared_file_payload,
        "storage": _storage,
        "storage_backend": APPWRITE_STORAGE_BACKEND,
        "storage_path": _storage_path,
        "timedelta": timedelta,
        "utcnow": _utcnow,
        "uuid4": uuid.uuid4,
    }


@file_share_bp.route("/api/files/upload", methods=["POST"])
@login_required
def upload_file():
    files = request.files.getlist("file")
    return upload_file_response(
        current_user,
        files,
        request.form,
        _upload_file_dependencies(),
    )


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
