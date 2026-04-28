import io
import os
import secrets
import uuid
from datetime import datetime, timedelta

from flask import (
    Blueprint,
    abort,
    current_app,
    jsonify,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_login import current_user, login_required
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.utils import secure_filename

from extensions import db
from models import SharedFile, User

file_share_bp = Blueprint("file_share", __name__)

MAX_FILE_SIZE = 50 * 1024 * 1024
DEFAULT_EXPIRY_DAYS = 1
ALLOWED_EXPIRY_OPTIONS = [1, 3, 7, 14, 30]
SHARE_CODE_LENGTH = 7
SHARE_CODE_CHARS = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
PUBLIC_SHARE_BASE_URL = "https://emory.apstudy.org/files/share"


def _utcnow():
    return datetime.utcnow()


def _isoformat(value):
    if not value:
        return None
    return value.replace(microsecond=0).isoformat() + "Z"


def _format_expiry_display(value):
    return value.strftime("%B %d, %Y at %I:%M %p UTC").replace(" 0", " ")


def _possessive(name):
    cleaned = (name or "Someone").strip()
    if not cleaned:
        cleaned = "Someone"
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
    return f"{PUBLIC_SHARE_BASE_URL}/{share_code}"


def _uploads_dir():
    return current_app.config["FILE_SHARE_UPLOAD_DIR"]


def _relative_storage_path(user_id, file_id, filename):
    return os.path.join(str(user_id), file_id, filename)


def _absolute_storage_path(relative_path):
    return os.path.join(_uploads_dir(), relative_path)


def _is_expired(shared_file):
    return shared_file.expires_at <= _utcnow()


def _generate_share_code():
    while True:
        raw_bytes = secrets.token_bytes(SHARE_CODE_LENGTH)
        code = "".join(SHARE_CODE_CHARS[byte % len(SHARE_CODE_CHARS)] for byte in raw_bytes)
        if not SharedFile.query.filter_by(share_code=code).first():
            return code


def _shared_file_payload(shared_file):
    return {
        "id": shared_file.id,
        "filename": shared_file.original_filename,
        "fileSizeBytes": shared_file.file_size_bytes,
        "mimeType": shared_file.mime_type,
        "isPublic": shared_file.is_public,
        "shareUrl": _share_url(shared_file.share_code) if shared_file.is_public and shared_file.share_code else None,
        "expiresAt": _isoformat(shared_file.expires_at),
        "createdAt": _isoformat(shared_file.created_at),
        "downloads": shared_file.downloaded_count,
    }


def _send_shared_file(shared_file):
    absolute_path = _absolute_storage_path(shared_file.stored_path)
    if not os.path.exists(absolute_path):
        raise FileNotFoundError(absolute_path)

    shared_file.downloaded_count += 1
    db.session.commit()

    return send_file(
        absolute_path,
        as_attachment=True,
        download_name=shared_file.original_filename,
        mimetype=shared_file.mime_type or None,
    )


def _render_public_share_page(shared_file=None, error_message=None):
    shared_by_name = None
    if shared_file:
        owner = User.query.get(shared_file.user_id)
        if owner and owner.name:
            shared_by_name = _possessive(owner.name)
        elif owner and owner.email:
            shared_by_name = _possessive(owner.email.split("@")[0])
        else:
            shared_by_name = _possessive("Someone")

    return render_template(
        "file_share_download.html",
        shared_file=shared_file,
        shared_by_name=shared_by_name,
        file_size_display=_human_readable_size(shared_file.file_size_bytes) if shared_file else None,
        expires_at_display=_format_expiry_display(shared_file.expires_at) if shared_file else None,
        download_url=url_for("file_share.public_share", share_code=shared_file.share_code, download=1) if shared_file else None,
        error_message=error_message or "File not found or expired.",
    )


@file_share_bp.errorhandler(RequestEntityTooLarge)
def handle_file_too_large(_error):
    return jsonify({"error": "File exceeds the 50 MB limit."}), 413


@file_share_bp.route("/files")
@login_required
def file_share_page():
    return render_template(
        "file_share.html",
        user={
            "name": current_user.name,
            "email": current_user.email,
            "picture": current_user.picture_url,
        },
        max_file_size=MAX_FILE_SIZE,
        allowed_expiry_options=ALLOWED_EXPIRY_OPTIONS,
        default_expiry_days=DEFAULT_EXPIRY_DAYS,
    )


@file_share_bp.route("/api/files/upload", methods=["POST"])
@login_required
def upload_file():
    # Accept multiple files (up to 5). Client may submit multiple 'file' parts and
    # parallel form values for 'filename', 'visibility', and 'expiryDays'.
    files = request.files.getlist("file")
    if not files:
        return jsonify({"error": "At least one file is required."}), 400

    # Gather per-file metadata lists; if not provided, default values will be used.
    filenames = request.form.getlist("filename")
    visibilities = request.form.getlist("visibility")
    expiries = request.form.getlist("expiryDays")

    max_allowed = 5
    total_provided = len(files)
    to_process = files[:max_allowed]
    skipped = total_provided - len(to_process)

    created = []
    errors = []

    for idx, uploaded_file in enumerate(to_process):
        if not uploaded_file or not uploaded_file.filename:
            errors.append({"index": idx, "error": "Missing file or filename."})
            continue

        # metadata for this file (fall back to sensible defaults)
        custom_filename = (filenames[idx] if idx < len(filenames) else "") or ""
        visibility = (visibilities[idx] if idx < len(visibilities) else "private") or "private"
        try:
            expiry_days = int(expiries[idx]) if idx < len(expiries) else DEFAULT_EXPIRY_DAYS
        except (TypeError, ValueError):
            expiry_days = DEFAULT_EXPIRY_DAYS

        visibility = visibility.strip().lower()
        if visibility not in {"public", "private"}:
            errors.append({"index": idx, "error": "Invalid visibility option."})
            continue
        if expiry_days not in ALLOWED_EXPIRY_OPTIONS:
            errors.append({"index": idx, "error": "Invalid expiry selection."})
            continue

        display_filename = custom_filename.strip() or uploaded_file.filename

        try:
            uploaded_file.stream.seek(0, os.SEEK_END)
            file_size_bytes = uploaded_file.stream.tell()
            uploaded_file.stream.seek(0)
        except (AttributeError, OSError):
            uploaded_data = uploaded_file.stream.read()
            file_size_bytes = len(uploaded_data)
            uploaded_file.stream = io.BytesIO(uploaded_data)

        if file_size_bytes > MAX_FILE_SIZE:
            errors.append({"index": idx, "error": "File exceeds the 50 MB limit."})
            continue

        file_id = str(uuid.uuid4())
        sanitized_name = secure_filename(display_filename) or "file"
        relative_path = _relative_storage_path(current_user.id, file_id, sanitized_name)
        absolute_path = _absolute_storage_path(relative_path)
        os.makedirs(os.path.dirname(absolute_path), exist_ok=True)
        uploaded_file.save(absolute_path)

        is_public = visibility == "public"
        share_code = _generate_share_code() if is_public else None
        expires_at = _utcnow() + timedelta(days=expiry_days)

        shared_file = SharedFile(
            id=file_id,
            user_id=current_user.id,
            original_filename=display_filename,
            stored_path=relative_path,
            file_size_bytes=file_size_bytes,
            mime_type=uploaded_file.mimetype,
            share_code=share_code,
            is_public=is_public,
            expires_at=expires_at,
        )

        db.session.add(shared_file)
        try:
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            if os.path.exists(absolute_path):
                os.remove(absolute_path)
            errors.append({"index": idx, "error": "Unable to save file."})
            continue

        payload = _shared_file_payload(shared_file)
        created.append(payload)

    response = {"files": created}
    if skipped:
        response["skipped"] = skipped
        response.setdefault("errors", []).append({"error": f"Only {max_allowed} files are accepted; {skipped} file(s) were ignored."})
    if errors:
        response.setdefault("errors", []).extend(errors)

    status_code = 201 if created else 400
    return jsonify(response), status_code


@file_share_bp.route("/api/files/my/<file_id>/visibility", methods=["POST"])
@login_required
def change_visibility(file_id):
    shared_file = SharedFile.query.filter_by(id=file_id, user_id=current_user.id).first()
    if not shared_file:
        abort(404)

    data = request.get_json() or request.form
    visibility = (data.get("visibility") or "").strip().lower()
    if visibility not in {"public", "private"}:
        return jsonify({"error": "Invalid visibility option."}), 400

    if visibility == "public":
        if not shared_file.is_public:
            shared_file.is_public = True
            shared_file.share_code = _generate_share_code()
    else:
        shared_file.is_public = False
        shared_file.share_code = None

    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        return jsonify({"error": "Unable to update visibility."}), 500

    return jsonify(_shared_file_payload(shared_file)), 200


@file_share_bp.route("/api/files/my")
@login_required
def my_files():
    now = _utcnow()
    files = (
        SharedFile.query.filter(
            SharedFile.user_id == current_user.id,
            SharedFile.expires_at > now,
        )
        .order_by(SharedFile.created_at.desc())
        .all()
    )
    return jsonify({"files": [_shared_file_payload(shared_file) for shared_file in files]})


@file_share_bp.route("/api/files/my/<file_id>/download")
@login_required
def download_my_file(file_id):
    shared_file = SharedFile.query.filter_by(id=file_id, user_id=current_user.id).first()
    if not shared_file or _is_expired(shared_file):
        abort(404)

    try:
        return _send_shared_file(shared_file)
    except FileNotFoundError:
        abort(404)


@file_share_bp.route("/api/files/my/<file_id>", methods=["DELETE"])
@login_required
def delete_my_file(file_id):
    shared_file = SharedFile.query.filter_by(id=file_id, user_id=current_user.id).first()
    if not shared_file:
        abort(404)

    absolute_path = _absolute_storage_path(shared_file.stored_path)
    try:
        os.remove(absolute_path)
    except FileNotFoundError:
        pass

    db.session.delete(shared_file)
    db.session.commit()
    return jsonify({"message": "File deleted."})


@file_share_bp.route("/files/share/<share_code>")
def public_share(share_code):
    shared_file = SharedFile.query.filter_by(share_code=share_code, is_public=True).first()
    if not shared_file or _is_expired(shared_file):
        return _render_public_share_page(error_message="File not found or expired.")

    if request.args.get("download"):
        try:
            return _send_shared_file(shared_file)
        except FileNotFoundError:
            return _render_public_share_page(error_message="File not found or expired.")

    return _render_public_share_page(shared_file=shared_file)