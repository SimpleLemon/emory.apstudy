import logging
import base64
import json
import io
import os
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from flask import Blueprint, Response, abort, current_app, jsonify, request, send_file, session, url_for
from flask_login import current_user, login_required
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from appwrite.exception import AppwriteException
from appwrite.query import Query
from appwrite_helpers import (
    create_row_safe,
    first_row,
    format_datetime,
    list_rows_all,
)
from services.discord_audit import emit_creation_event, format_actor
from services.chat_formatting import _is_public_host, fetch_link_preview, safe_url, url_hash
from services.database import db_connection
from services import invites, note_media, note_store, notes_collaboration
from services.appwrite_storage import note_media_upload_failure
from services.entitlements import EntitlementError, EntitlementLimitError, check_limit, check_storage, request_entitlements
from services.note_page_setup import (
    PAGE_SETUP_COLORS,
    PAGE_SETUP_FONT_TYPES,
    PAGE_SETUP_MARGIN_MAX,
    PAGE_SETUP_MARGIN_MIN,
)
from services.time_utils import utcnow_iso as _utcnow_iso


notes_api_bp = Blueprint("notes_api", __name__)
logger = logging.getLogger(__name__)

USER_SETTINGS_TABLE_ID = "user_settings"
LINK_PREVIEWS_TABLE_ID = "chat_link_previews"
@notes_api_bp.errorhandler(404)
def notes_not_found(error):
    return jsonify({"error": "Not found."}), 404


@notes_api_bp.errorhandler(500)
def notes_server_error(error):
    return jsonify({"error": "Unable to complete notes request."}), 500


def _parse_page_setup(value):
    if not value:
        return {}
    if isinstance(value, dict):
        data = value
    elif isinstance(value, str):
        try:
            data = json.loads(value)
        except (TypeError, ValueError):
            return {}
    else:
        return {}
    if not isinstance(data, dict):
        return {}
    return _normalize_page_setup(data)


def _normalize_page_setup(value):
    if not isinstance(value, dict):
        return {}

    normalized = {}
    page_color = value.get("pageColor")
    font_type = value.get("fontType")
    side_margins = value.get("sideMargins")

    if isinstance(page_color, str) and page_color in PAGE_SETUP_COLORS:
        normalized["pageColor"] = page_color
    if isinstance(font_type, str) and font_type in PAGE_SETUP_FONT_TYPES:
        normalized["fontType"] = font_type
    if side_margins is not None:
        try:
            margin_value = round(float(side_margins), 1)
        except (TypeError, ValueError):
            margin_value = None
        if margin_value is not None:
            normalized["sideMargins"] = min(PAGE_SETUP_MARGIN_MAX, max(PAGE_SETUP_MARGIN_MIN, margin_value))

    return normalized


def _load_global_notes_page_setup(user_id):
    settings = first_row(USER_SETTINGS_TABLE_ID, queries=[Query.equal("user_id", [str(user_id)])])
    return _parse_page_setup(settings.get("notes_page_setup_json") if settings else "")


def _note_to_payload(note, global_page_setup=None, *, access=None, owner=None):
    note_id = note.get("$id") or note.get("id")
    payload = {
        "$id": note_id,
        "id": note_id,
        "folder_id": note.get("folder_id"),
        "title": note.get("title") or "Untitled",
        "content": note.get("content") or "",
        "preview_text": note.get("preview_text") or "",
        "order": note.get("order") or 0,
        "collaboration_enabled": bool(note.get("collaboration_enabled")),
        "page_setup": _parse_page_setup(note.get("page_setup_json")),
        "global_page_setup": global_page_setup if global_page_setup is not None else {},
        "created_at": note.get("created_at"),
        "updated_at": note.get("updated_at"),
    }
    if access is not None:
        payload["access"] = access
    if owner is not None:
        payload["owner"] = owner
    return payload


def _viewer_id():
    if not current_user.is_authenticated:
        return None
    return str(current_user.id)


def _access_denied_response():
    if not current_user.is_authenticated:
        return jsonify({"error": "Log in to view this shared note.", "login_required": True}), 401
    return jsonify({"error": "Not found."}), 404


def _note_or_404(note_id):
    note = note_store.get_note(note_id)
    if not note:
        abort(404)
    return note


def _folder_or_404(folder_id):
    folder = note_store.get_folder(folder_id)
    if not folder:
        abort(404)
    return folder


def _require_note_access(note_id, capability="can_view"):
    note = _note_or_404(note_id)
    access = note_store.resolve_note_access(note, _viewer_id())
    if not access.get(capability):
        abort(404)
    return note, access


def _require_folder_access(folder_id, capability="can_view"):
    folder = _folder_or_404(folder_id)
    access = note_store.resolve_folder_access(folder, _viewer_id())
    if not access.get(capability):
        abort(404)
    return folder, access


def _is_owner_access(access):
    return (access or {}).get("role") == "owner"


def _collaboration_serializer():
    secret = os.environ.get("NOTES_COLLABORATION_SECRET") or current_app.secret_key
    return URLSafeTimedSerializer(secret_key=secret, salt="nest-notes-collaboration-token")


def _internal_collaboration_authorized():
    secret = os.environ.get("NOTES_COLLABORATION_INTERNAL_SECRET") or os.environ.get("NOTES_COLLABORATION_SECRET")
    provided = request.headers.get("X-Nest-Collaboration-Secret") or request.args.get("secret")
    if secret:
        return provided == secret
    if (os.environ.get("FLASK_ENV") or "").strip().lower() == "production":
        return False
    return request.remote_addr in {"127.0.0.1", "::1", "localhost", None}


def _sharing_url(resource_type, resource_id):
    endpoint = "dashboard.note_document" if resource_type == "note" else "dashboard.shared_note_folder"
    key = "note_id" if resource_type == "note" else "folder_id"
    return url_for(endpoint, **{key: resource_id}, _external=True)


def _direct_grants_for_payload(resource_type, resource_id):
    users = []
    public = False
    for grant in note_store.resource_grants(resource_type, resource_id):
        if grant.get("principal_type") == "public":
            public = True
            continue
        if grant.get("principal_type") != "user":
            continue
        user = note_store.get_safe_user(grant.get("principal_id"))
        if user:
            users.append({**user, "role": note_store._normalized_access_level(grant.get("access_level"))})
    users.sort(key=lambda user: (user.get("name") or "").lower())
    return public, users


def _inherited_grants_for_payload(resource_type, resource_id):
    if resource_type != "note":
        return []
    note = note_store.get_note(resource_id)
    folder_id = note.get("folder_id") if note else None
    if not folder_id:
        return []
    folder = note_store.get_folder(folder_id)
    if not folder:
        return []
    inherited = []
    for grant in note_store.resource_grants("folder", folder_id):
        if grant.get("principal_type") == "public":
            inherited.append({
                "id": "*",
                "name": "Anyone with the folder link",
                "role": "viewer",
                "source": "folder",
                "source_id": folder_id,
                "source_label": folder.get("name") or "Folder",
            })
            continue
        if grant.get("principal_type") != "user":
            continue
        user = note_store.get_safe_user(grant.get("principal_id"))
        if user:
            inherited.append({
                **user,
                "role": note_store._normalized_access_level(grant.get("access_level")),
                "source": "folder",
                "source_id": folder_id,
                "source_label": folder.get("name") or "Folder",
            })
    inherited.sort(key=lambda user: (user.get("name") or "").lower())
    return inherited


def _sharing_payload(resource_type, resource_id):
    state = note_store.sharing_state(resource_type, resource_id)
    resource = note_store.get_note(resource_id) if resource_type == "note" else note_store.get_folder(resource_id)
    owner = note_store.get_safe_user(resource.get("user_id")) if resource else None
    actor_access = None
    if current_user.is_authenticated and resource:
        resolver = note_store.resolve_note_access if resource_type == "note" else note_store.resolve_folder_access
        actor_access = resolver(resource, current_user.id)
    return {
        "resource_type": resource_type,
        "resource_id": resource_id,
        "share_url": _sharing_url(resource_type, resource_id),
        "owner": owner,
        "pending_invitations": notes_collaboration.list_pending_invitations(resource_type, resource_id),
        "inherited": _inherited_grants_for_payload(resource_type, resource_id),
        "capabilities": actor_access or {},
        **state,
    }


def _note_owner_or_404(note_id):
    note = note_store.get_note_for_user(note_id, current_user.id)
    if not note:
        abort(404)
    return note


def _folder_owner_or_404(folder_id):
    folder = note_store.get_folder_for_user(folder_id, current_user.id)
    if not folder:
        abort(404)
    return folder


def _normalize_owned_folder_id(folder_id):
    if not folder_id:
        return None
    normalized = str(folder_id).strip()
    if not normalized:
        return None
    _folder_owner_or_404(normalized)
    return normalized


def _fallback_link_preview(url):
    parsed = urlparse(url)
    hostname = parsed.netloc or url
    return {
        "url": url,
        "title": hostname,
        "description": "",
        "image_url": "",
        "site_name": hostname,
        "content_type": "",
        "preview_found": False,
    }


def _link_preview_payload(url):
    normalized_url = safe_url(url)
    if not normalized_url:
        return None, 400
    if not _is_public_host(normalized_url):
        return None, 400

    key = url_hash(normalized_url)
    try:
        cached = first_row(LINK_PREVIEWS_TABLE_ID, queries=[Query.equal("url_hash", [key])])
    except AppwriteException:
        logger.exception("Failed to read cached notes link preview")
        cached = None

    if cached:
        return {
            "url": cached.get("url") or normalized_url,
            "title": cached.get("title") or "",
            "description": cached.get("description") or "",
            "image_url": cached.get("image_url") or "",
            "site_name": cached.get("site_name") or "",
            "content_type": cached.get("content_type") or "",
            "preview_found": True,
        }, 200

    try:
        preview = fetch_link_preview(normalized_url)
    except Exception:
        logger.exception("Failed to fetch notes link preview")
        preview = None

    if not preview:
        return _fallback_link_preview(normalized_url), 200

    now = _utcnow_iso()
    try:
        create_row_safe(
            LINK_PREVIEWS_TABLE_ID,
            row_id=str(uuid.uuid4()),
            data={
                "url_hash": key,
                "url": preview.get("url") or normalized_url,
                "title": preview.get("title") or None,
                "description": preview.get("description") or None,
                "image_url": preview.get("image_url") or None,
                "site_name": preview.get("site_name") or None,
                "content_type": preview.get("content_type") or None,
                "created_at": now,
            },
        )
    except AppwriteException:
        logger.exception("Failed to cache notes link preview")

    return {
        "url": preview.get("url") or normalized_url,
        "title": preview.get("title") or "",
        "description": preview.get("description") or "",
        "image_url": preview.get("image_url") or "",
        "site_name": preview.get("site_name") or "",
        "content_type": preview.get("content_type") or "",
        "preview_found": True,
    }, 200


@notes_api_bp.route("/api/notes", methods=["GET"])
@login_required
def list_notes():
    user_id = str(current_user.id)
    try:
        notes = note_store.list_notes_for_user(user_id)
        folders = note_store.list_folders_for_user(user_id)
        shared_note_ids = note_store.shared_resource_ids(user_id, "note")
        shared_folder_ids = note_store.shared_resource_ids(user_id, "folder")
    except AppwriteException:
        logger.exception("Failed to fetch notes/folders")
        return jsonify({"notes": [], "folders": [], "error": "Unable to fetch notes right now."}), 500

    return jsonify(
        {
            "notes": [
                note_store.note_list_payload(note, is_shared=(note.get("$id") or note.get("id")) in shared_note_ids)
                for note in notes
            ],
            "folders": [
                note_store.folder_payload(folder, is_shared=(folder.get("$id") or folder.get("id")) in shared_folder_ids)
                for folder in folders
            ],
        }
    )


@notes_api_bp.route("/api/notes/shared", methods=["GET"])
@login_required
def list_shared_notes():
    try:
        return jsonify(note_store.list_shared_for_user(current_user.id))
    except AppwriteException:
        logger.exception("Failed to load notes shared with user")
        return jsonify({"folders": [], "notes": [], "error": "Unable to load shared notes."}), 500


@notes_api_bp.route("/api/notes/share-users", methods=["GET"])
@login_required
def search_note_share_users():
    query = (request.args.get("q") or "").strip()
    if "@" in query and notes_collaboration.EMAIL_RE.fullmatch(query):
        try:
            match = note_store.find_user_by_email(query, current_user.id)
        except AppwriteException:
            return jsonify({"results": [], "error": "Unable to resolve email recipient."}), 500
        return jsonify({
            "results": [match] if match else [],
            "email": {
                "query": query,
                "status": "matched" if match else "unmatched",
                "warning": None if match else (
                    "This email is not attached to a Nest account yet. The recipient must sign up "
                    "with OAuth using exactly this email before access can activate."
                ),
            },
        })
    try:
        return jsonify({"results": note_store.search_share_users(query, current_user.id)})
    except AppwriteException:
        return jsonify({"results": [], "error": "Unable to search users."}), 500


@notes_api_bp.route("/api/notes", methods=["POST"])
@login_required
def create_note():
    payload = request.get_json(silent=True) or {}
    title = (payload.get("title") or "Untitled").strip() or "Untitled"
    content = payload.get("content") or ""
    folder_id = _normalize_owned_folder_id(payload.get("folder_id"))

    if hasattr(current_user, "_data"):
        try:
            entitlements = request_entitlements(current_user)
            check_limit(entitlements, "max_notes", entitlements["usage"]["notes"])
        except EntitlementLimitError as exc:
            return jsonify(exc.payload()), 403
        except EntitlementError:
            logger.exception("Failed to verify note limits")
            return jsonify({"error": "Unable to verify your note limits right now.", "code": "tier_check_unavailable"}), 503

    try:
        created = note_store.create_note(
            current_user.id,
            title=title,
            content=content,
            folder_id=folder_id,
            now=_utcnow_iso(),
        )
    except AppwriteException:
        logger.exception("Failed to create note")
        return jsonify({"error": "Unable to create note."}), 500

    try:
        invites.record_activation(current_user.id, "note")
    except Exception:
        logger.exception("Failed to record invite activation for note creation")

    emit_creation_event(
        "New Note Created",
        actor=format_actor(current_user),
        target=title,
        metadata={
            "page_context": "notes",
            "resource_type": "note",
            "resource_id": created.get("$id") or created.get("id"),
            "folder_id": folder_id,
        },
        color="green",
    )
    return jsonify(_note_to_payload(created)), 201


@notes_api_bp.route("/api/notes/tools/link-preview", methods=["GET"])
@login_required
def notes_link_preview():
    payload, status = _link_preview_payload(request.args.get("url", ""))
    if status == 400:
        return jsonify({"error": "Enter a valid public http or https URL."}), 400
    return jsonify(payload), status


def _media_payload(note_id, media):
    media_id = media.get("$id") or media.get("id")
    return {
        "id": media_id,
        "url": url_for("notes_api.get_note_media", note_id=note_id, media_id=media_id),
        "name": media.get("original_filename") or "Image",
        "mimeType": media.get("mime_type"),
        "size": media.get("file_size_bytes"),
        "width": media.get("width"),
        "height": media.get("height"),
    }


def _notes_json(payload, status=200):
    response = jsonify(payload)
    response.status_code = status
    return response


@notes_api_bp.route("/api/notes/<note_id>/media", methods=["POST"])
@login_required
def upload_note_media(note_id):
    note = _note_owner_or_404(note_id)
    access = note_store.resolve_note_access(note, current_user.id)
    if not access["can_edit"]:
        abort(404)
    uploaded_file = request.files.get("file")
    if uploaded_file is None:
        return _notes_json({"error": "Choose an image to upload."}, 400)
    if not uploaded_file.filename:
        uploaded_file.filename = "clipboard-image.png"
    if hasattr(current_user, "_data"):
        try:
            entitlements = request_entitlements(current_user)
            preview = uploaded_file.read(note_media.MAX_NOTE_IMAGE_BYTES + 1)
            uploaded_file.stream.seek(0)
            check_storage(entitlements, len(preview))
        except EntitlementLimitError as exc:
            return _notes_json(exc.payload(), 403)
        except EntitlementError:
            logger.exception("Failed to verify note media limits")
            return _notes_json({"error": "Unable to verify your storage limits right now.", "code": "tier_check_unavailable"}, 503)
    try:
        media = note_media.create_media(note_id, current_user.id, uploaded_file)
    except ValueError as exc:
        return _notes_json({"error": str(exc)}, 400)
    except AppwriteException as exc:
        status_code, error = note_media_upload_failure(exc)
        logger.exception(
            "Failed to upload note media (code=%s): %s",
            getattr(exc, "code", None) or getattr(exc, "response_code", None),
            exc,
        )
        return _notes_json({"error": error}, status_code)
    except Exception:
        logger.exception("Failed to upload note media")
        return _notes_json({"error": "Unable to store this image."}, 500)
    return _notes_json(_media_payload(note_id, media), 201)


@notes_api_bp.route("/api/notes/<note_id>/media/<media_id>", methods=["GET"])
def get_note_media(note_id, media_id):
    note = note_store.get_note(note_id)
    media = note_media.get_media(media_id)
    if not note or not media or str(media.get("note_id")) != str(note_id):
        abort(404)
    access = note_store.resolve_note_access(note, _viewer_id())
    if not access["can_view"]:
        return _access_denied_response()
    try:
        data = note_media.media_bytes(media)
    except AppwriteException:
        logger.exception("Failed to read note media")
        abort(404)
    response = send_file(
        io.BytesIO(data),
        mimetype=media.get("mime_type") or "application/octet-stream",
        as_attachment=False,
        download_name=media.get("original_filename") or "image",
        conditional=True,
    )
    response.headers["Cache-Control"] = "private, no-cache"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.set_etag(str(media.get("storage_file_id") or media_id))
    return response.make_conditional(request)


@notes_api_bp.route("/api/notes/<note_id>/media/<media_id>", methods=["DELETE"])
@login_required
def delete_note_media(note_id, media_id):
    note = _note_owner_or_404(note_id)
    access = note_store.resolve_note_access(note, current_user.id)
    media = note_media.get_media(media_id)
    if not access["can_edit"] or not media or str(media.get("note_id")) != str(note_id):
        abort(404)
    try:
        note_media.delete_media(media)
    except AppwriteException:
        logger.exception("Failed to delete note media")
        return jsonify({"error": "Unable to delete this image."}), 500
    return jsonify({"ok": True})


@notes_api_bp.route("/api/notes/<note_id>", methods=["GET"])
def get_note(note_id):
    note = note_store.get_note(note_id)
    if not note:
        abort(404)
    access = note_store.resolve_note_access(note, _viewer_id())
    if not access["can_view"]:
        return _access_denied_response()
    try:
        global_page_setup = _load_global_notes_page_setup(note.get("user_id"))
    except AppwriteException:
        logger.exception("Failed to load note page setup defaults")
        global_page_setup = {}
    owner = note_store.get_safe_user(note.get("user_id"))
    return jsonify(_note_to_payload(note, global_page_setup=global_page_setup, access=access, owner=owner))


@notes_api_bp.route("/api/notes/<note_id>", methods=["PATCH"])
@login_required
def update_note(note_id):
    note = _note_or_404(note_id)
    access = note_store.resolve_note_access(note, current_user.id)
    if not access["can_edit"]:
        abort(404)
    payload = request.get_json(silent=True) or {}

    allowed = {"title", "content", "folder_id", "order", "page_setup_json"}
    updates = {key: payload[key] for key in allowed if key in payload}
    structure_fields = {"folder_id", "order"} & updates.keys()
    if structure_fields and not _is_owner_access(access):
        return jsonify({"error": "Only the note owner can move or reorder notes."}), 403
    collaborative_fields = {"title", "content", "page_setup_json"} & updates.keys()
    if note.get("collaboration_enabled") and collaborative_fields:
        return jsonify({
            "error": "This note is collaboration-enabled. Use the collaboration provider instead.",
            "code": "collaboration_required",
        }), 409
    if "title" in updates:
        updates["title"] = (updates.get("title") or "").strip() or "Untitled"
    if "folder_id" in updates:
        updates["folder_id"] = _normalize_owned_folder_id(updates.get("folder_id"))
    if "page_setup_json" in updates:
        page_setup = updates.get("page_setup_json")
        if isinstance(page_setup, str):
            try:
                page_setup = json.loads(page_setup)
            except (TypeError, ValueError):
                return jsonify({"error": "Page setup must be valid JSON."}), 400
        updates["page_setup_json"] = json.dumps(_normalize_page_setup(page_setup), separators=(",", ":"))

    if not updates:
        return jsonify({"error": "No updatable fields were provided."}), 400

    updates["updated_at"] = _utcnow_iso()

    try:
        updated = note_store.update_note(note_id, updates)
    except AppwriteException:
        logger.exception("Failed to update note")
        return jsonify({"error": "Unable to update note."}), 500
    if "content" in updates:
        try:
            note_media.sync_note_media(note_id, updates["content"])
        except AppwriteException:
            logger.exception("Failed to synchronize note media references")

    try:
        global_page_setup = _load_global_notes_page_setup(updated.get("user_id") or note.get("user_id"))
    except AppwriteException:
        logger.exception("Failed to load note page setup defaults")
        global_page_setup = {}
    owner = note_store.get_safe_user(updated.get("user_id") or note.get("user_id"))
    return jsonify(_note_to_payload(updated, global_page_setup=global_page_setup, access=access, owner=owner))


@notes_api_bp.route("/api/notes/<note_id>/sharing", methods=["GET", "PATCH"])
@login_required
def note_sharing(note_id):
    note = _note_or_404(note_id)
    access = note_store.resolve_note_access(note, current_user.id)
    if not access["can_share"]:
        abort(404)
    if request.method == "GET":
        return jsonify(_sharing_payload("note", note_id))
    return _replace_sharing("note", note_id, note.get("user_id"))


@notes_api_bp.route("/api/notes/<note_id>", methods=["DELETE"])
@login_required
def delete_note(note_id):
    _note_owner_or_404(note_id)
    try:
        note_media.delete_note_media(note_id)
        note_store.delete_note(note_id)
    except AppwriteException:
        logger.exception("Failed to delete note")
        return jsonify({"error": "Unable to delete note."}), 500

    return jsonify({"ok": True})


@notes_api_bp.route("/api/notes/folders", methods=["POST"])
@login_required
def create_folder():
    payload = request.get_json(silent=True) or {}
    name = (payload.get("name") or "New Folder").strip() or "New Folder"

    try:
        created = note_store.create_folder(
            current_user.id,
            name=name,
            now=_utcnow_iso(),
        )
    except AppwriteException:
        logger.exception("Failed to create note folder")
        return jsonify({"error": "Unable to create folder."}), 500

    emit_creation_event(
        "New Note Folder Created",
        actor=format_actor(current_user),
        target=name,
        metadata={
            "page_context": "notes",
            "resource_type": "note_folder",
            "resource_id": created.get("$id") or created.get("id"),
        },
        color="green",
    )
    return jsonify(note_store.folder_payload(created)), 201


@notes_api_bp.route("/api/notes/folders/<folder_id>", methods=["GET"])
def get_shared_folder(folder_id):
    folder = note_store.get_folder(folder_id)
    if not folder:
        abort(404)
    access = note_store.resolve_folder_access(folder, _viewer_id())
    if not access["can_view"]:
        return _access_denied_response()
    owner = note_store.get_safe_user(folder.get("user_id"))
    notes = [note_store.note_list_payload(note, owner=owner) for note in note_store.list_notes_in_folder(folder_id)]
    return jsonify({
        "folder": note_store.folder_payload(folder, is_shared=True, owner=owner),
        "notes": notes,
        "owner": owner,
        "access": access,
    })


@notes_api_bp.route("/api/notes/folders/<folder_id>", methods=["PATCH"])
@login_required
def update_folder(folder_id):
    _folder_owner_or_404(folder_id)
    payload = request.get_json(silent=True) or {}
    updates = {}

    if "name" in payload:
        updates["name"] = (payload.get("name") or "").strip() or "Untitled Folder"

    if "order" in payload:
        updates["order"] = payload.get("order")

    if not updates:
        return jsonify({"error": "No updatable fields were provided."}), 400

    updates["updated_at"] = _utcnow_iso()

    try:
        updated = note_store.update_folder(folder_id, updates)
    except AppwriteException:
        logger.exception("Failed to update note folder")
        return jsonify({"error": "Unable to update folder."}), 500

    return jsonify(note_store.folder_payload(updated))


@notes_api_bp.route("/api/notes/folders/<folder_id>/sharing", methods=["GET", "PATCH"])
@login_required
def folder_sharing(folder_id):
    folder = _folder_or_404(folder_id)
    access = note_store.resolve_folder_access(folder, current_user.id)
    if not access["can_share"]:
        abort(404)
    if request.method == "GET":
        return jsonify(_sharing_payload("folder", folder_id))
    return _replace_sharing("folder", folder_id, folder.get("user_id"))


@notes_api_bp.route("/api/notes/folders/<folder_id>", methods=["DELETE"])
@login_required
def delete_folder(folder_id):
    _folder_owner_or_404(folder_id)

    try:
        for note in note_store.list_notes_in_folder(folder_id):
            note_media.delete_note_media(note.get("$id") or note.get("id"))
        note_store.delete_folder_and_notes(current_user.id, folder_id)
    except AppwriteException:
        logger.exception("Failed to delete note folder")
        return jsonify({"error": "Unable to delete folder."}), 500

    return jsonify({"ok": True})


def _replace_sharing(resource_type, resource_id, owner_user_id):
    payload = request.get_json(silent=True) or {}
    public = payload.get("public", payload.get("public_view_link_enabled"))
    if not isinstance(public, bool):
        return jsonify({"error": "public must be a boolean."}), 400
    expected_revision = payload.get("expected_revision", payload.get("revision"))

    if "grants" in payload:
        raw_grants = payload.get("grants")
        if not isinstance(raw_grants, list):
            return jsonify({"error": "grants must be a list."}), 400
    elif "users" in payload:
        raw_users = payload.get("users")
        if not isinstance(raw_users, list):
            return jsonify({"error": "users must be a list."}), 400
        raw_grants = [{"user_id": user.get("id"), "role": user.get("role", "viewer")} for user in raw_users if isinstance(user, dict)]
    else:
        raw_user_ids = payload.get("user_ids", [])
        if not isinstance(raw_user_ids, list):
            return jsonify({"error": "user_ids must be a list."}), 400
        raw_grants = [{"user_id": user_id, "role": "viewer"} for user_id in raw_user_ids]

    raw_invitations = payload.get("invitations", payload.get("pending_invitations", []))
    if not isinstance(raw_invitations, list):
        return jsonify({"error": "invitations must be a list."}), 400
    if len(raw_grants) + len(raw_invitations) > 100:
        return jsonify({"error": "A resource can be shared with at most 100 users."}), 400

    grants = []
    seen_user_ids = set()
    matched_from_email = []
    for entry in raw_grants:
        if not isinstance(entry, dict):
            return jsonify({"error": "Every grant must be an object."}), 400
        user_id = str(entry.get("user_id") or entry.get("id") or "").strip()
        if not user_id or user_id == str(owner_user_id):
            return jsonify({"error": "Every user_id must be a valid recipient."}), 400
        if user_id in seen_user_ids:
            continue
        try:
            role = notes_collaboration.normalize_role(entry.get("role", "viewer"))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        if not note_store.get_safe_user(user_id):
            return jsonify({"error": "One or more selected users no longer exist."}), 400
        seen_user_ids.add(user_id)
        grants.append({"user_id": user_id, "role": role})

    invitations = []
    seen_emails = set()
    for invitation in raw_invitations:
        if not isinstance(invitation, dict):
            return jsonify({"error": "Every invitation must be an object."}), 400
        try:
            email_normalized, email_display = notes_collaboration.normalize_email(invitation.get("email"))
            role = notes_collaboration.normalize_role(invitation.get("role", "viewer"))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        if email_normalized in seen_emails:
            continue
        seen_emails.add(email_normalized)
        try:
            matched_user = note_store.find_user_by_email(email_display, owner_user_id)
        except AppwriteException:
            logger.exception("Failed to resolve invitation email")
            return jsonify({"error": "Unable to resolve email recipient."}), 500
        if matched_user:
            matched_user_id = matched_user["id"]
            if matched_user_id not in seen_user_ids:
                grants.append({"user_id": matched_user_id, "role": role})
                seen_user_ids.add(matched_user_id)
                matched_from_email.append(matched_user)
            continue
        invitations.append({"email": email_display, "role": role})

    previous_public, previous_users = _direct_grants_for_payload(resource_type, resource_id)
    previous_roles = {user["id"]: user.get("role") for user in previous_users}

    try:
        note_store.replace_resource_grants(
            resource_type,
            resource_id,
            owner_user_id,
            public=public,
            grants=grants,
            granted_by_user_id=current_user.id,
            expected_revision=expected_revision,
        )
        notes_collaboration.replace_pending_invitations(
            resource_type,
            resource_id,
            owner_user_id,
            invitations,
            current_user.id,
        )
    except ValueError as exc:
        if str(exc) == "sharing_revision_conflict":
            return jsonify({
                "error": "Sharing changed in another tab.",
                "code": "sharing_revision_conflict",
                "sharing": _sharing_payload(resource_type, resource_id),
            }), 409
        return jsonify({"error": str(exc)}), 400
    except AppwriteException:
        return jsonify({"error": "Unable to update sharing."}), 500

    notes_collaboration.record_access_event(
        current_user.id,
        resource_type,
        resource_id,
        "sharing_updated",
        old_access_level="public" if previous_public else "private",
        new_access_level="public" if public else "private",
    )
    if payload.get("notify_users") or payload.get("notify"):
        for grant in grants:
            if previous_roles.get(grant["user_id"]) != grant["role"]:
                notes_collaboration.create_notification(
                    grant["user_id"],
                    "note_sharing_grant",
                    "A note or notes folder was shared with you.",
                    actor_user_id=current_user.id,
                    note_id=resource_id if resource_type == "note" else None,
                )

    response = _sharing_payload(resource_type, resource_id)
    if matched_from_email:
        response["matched_email_users"] = matched_from_email
    return jsonify(response)


@notes_api_bp.route("/api/notes/<note_id>/collaboration-token", methods=["POST"])
def create_note_collaboration_token(note_id):
    note = _note_or_404(note_id)
    access = note_store.resolve_note_access(note, _viewer_id())
    if not access["can_view"]:
        return _access_denied_response()
    is_anonymous = not current_user.is_authenticated
    is_public = "public" in str(access.get("source") or "")
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
    token_payload = {
        "token_id": str(uuid.uuid4()),
        "user_id": str(current_user.id) if current_user.is_authenticated else None,
        "session_id": session.get("_id"),
        "note_id": str(note_id),
        "role": access.get("role") or "viewer",
        "access_revision": int(note.get("access_version") or 1),
        "public": bool(is_public),
        "anonymous": bool(is_anonymous),
        "expires_at": expires_at.isoformat().replace("+00:00", "Z"),
    }
    token = _collaboration_serializer().dumps(token_payload)
    return jsonify({
        "token": token,
        "expires_in": 300,
        "ws_url": f"/ws/notes/{note_id}?ticket={token}",
        "provider_url": "/ws/notes",
        "access": access,
        "awareness_allowed": not is_anonymous and not is_public,
        "user": note_store.get_safe_user(current_user.id) if current_user.is_authenticated else None,
    })


@notes_api_bp.route("/api/notes/<note_id>/suggestions", methods=["GET", "POST"])
@login_required
def note_suggestions(note_id):
    _note, access = _require_note_access(note_id, "can_review")
    if request.method == "GET":
        return jsonify({"suggestions": notes_collaboration.list_suggestions(note_id)})
    try:
        suggestion = notes_collaboration.create_suggestion(note_id, current_user.id, request.get_json(silent=True) or {})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(suggestion), 201


@notes_api_bp.route("/api/notes/<note_id>/suggestions/<suggestion_id>/<action>", methods=["POST"])
@login_required
def resolve_note_suggestion(note_id, suggestion_id, action):
    _note, access = _require_note_access(note_id, "can_manage_reviews")
    status_map = {"accept": "accepted", "reject": "rejected", "conflict": "conflicted"}
    status = status_map.get(action)
    if not status:
        return jsonify({"error": "Unsupported suggestion action."}), 400
    try:
        suggestion = notes_collaboration.resolve_suggestion(note_id, suggestion_id, current_user.id, status)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if not suggestion:
        abort(404)
    return jsonify(suggestion)


@notes_api_bp.route("/api/notes/<note_id>/comments", methods=["GET", "POST"])
@login_required
def note_comments(note_id):
    _note, access = _require_note_access(note_id, "can_review")
    if request.method == "GET":
        return jsonify({"threads": notes_collaboration.list_comments(note_id)})
    try:
        thread = notes_collaboration.create_comment(note_id, current_user.id, request.get_json(silent=True) or {})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(thread), 201


@notes_api_bp.route("/api/notes/<note_id>/comments/<thread_id>/replies", methods=["POST"])
@login_required
def reply_to_note_comment(note_id, thread_id):
    _note, access = _require_note_access(note_id, "can_review")
    payload = request.get_json(silent=True) or {}
    try:
        thread = notes_collaboration.reply_to_comment(note_id, thread_id, current_user.id, payload.get("body"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if not thread:
        abort(404)
    return jsonify(thread), 201


@notes_api_bp.route("/api/notes/<note_id>/comments/<thread_id>/<action>", methods=["POST"])
@login_required
def update_note_comment_status(note_id, thread_id, action):
    _note, access = _require_note_access(note_id, "can_manage_reviews")
    status_map = {"resolve": "resolved", "reopen": "open"}
    status = status_map.get(action)
    if not status:
        return jsonify({"error": "Unsupported comment action."}), 400
    try:
        thread = notes_collaboration.set_comment_status(note_id, thread_id, current_user.id, status)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if not thread:
        abort(404)
    return jsonify(thread)


@notes_api_bp.route("/api/notes/<note_id>/versions", methods=["GET", "POST"])
@login_required
def note_versions(note_id):
    _note, access = _require_note_access(note_id, "can_edit")
    if request.method == "GET":
        return jsonify({"versions": notes_collaboration.list_versions(note_id)})
    payload = request.get_json(silent=True) or {}
    version = notes_collaboration.create_version(
        note_id,
        current_user.id,
        reason="named",
        name=(payload.get("name") or "Manual snapshot"),
    )
    if not version:
        abort(404)
    return jsonify(version), 201


@notes_api_bp.route("/api/notes/<note_id>/versions/<version_id>/restore", methods=["POST"])
@login_required
def restore_note_version(note_id, version_id):
    _note, access = _require_note_access(note_id, "can_edit")
    restored = notes_collaboration.restore_version(note_id, version_id, current_user.id)
    if not restored:
        abort(404)
    try:
        global_page_setup = _load_global_notes_page_setup(restored.get("user_id"))
    except AppwriteException:
        global_page_setup = {}
    return jsonify(_note_to_payload(
        restored,
        global_page_setup=global_page_setup,
        access=note_store.resolve_note_access(restored, current_user.id),
        owner=note_store.get_safe_user(restored.get("user_id")),
    ))


@notes_api_bp.route("/api/notes/<note_id>/transfer", methods=["POST"])
@login_required
def transfer_note_ownership(note_id):
    note, access = _require_note_access(note_id, "can_transfer")
    payload = request.get_json(silent=True) or {}
    if not payload.get("confirm"):
        return jsonify({"error": "Ownership transfer requires explicit confirmation."}), 400
    new_owner_id = str(payload.get("new_owner_user_id") or payload.get("user_id") or "").strip()
    if not new_owner_id:
        return jsonify({"error": "Choose a target owner."}), 400
    try:
        transferred = notes_collaboration.transfer_note(note_id, note.get("user_id"), new_owner_id)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if not transferred:
        abort(404)
    notes_collaboration.record_access_event(
        current_user.id,
        "note",
        note_id,
        "ownership_transferred",
        target_type="user",
        target_id=new_owner_id,
    )
    return jsonify(_note_to_payload(
        transferred,
        access=note_store.resolve_note_access(transferred, current_user.id),
        owner=note_store.get_safe_user(transferred.get("user_id")),
    ))


@notes_api_bp.route("/api/notes/folders/<folder_id>/transfer", methods=["POST"])
@login_required
def transfer_folder_ownership(folder_id):
    folder, access = _require_folder_access(folder_id, "can_transfer")
    payload = request.get_json(silent=True) or {}
    if not payload.get("confirm"):
        return jsonify({"error": "Ownership transfer requires explicit confirmation."}), 400
    new_owner_id = str(payload.get("new_owner_user_id") or payload.get("user_id") or "").strip()
    if not new_owner_id:
        return jsonify({"error": "Choose a target owner."}), 400
    try:
        transferred = notes_collaboration.transfer_folder(folder_id, folder.get("user_id"), new_owner_id)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if not transferred:
        abort(404)
    notes_collaboration.record_access_event(
        current_user.id,
        "folder",
        folder_id,
        "ownership_transferred",
        target_type="user",
        target_id=new_owner_id,
    )
    return jsonify({
        "folder": note_store.folder_payload(transferred),
        "access": note_store.resolve_folder_access(transferred, current_user.id),
        "owner": note_store.get_safe_user(transferred.get("user_id")),
    })


@notes_api_bp.route("/api/internal/notes/<note_id>/collaboration-document", methods=["GET"])
def internal_get_collaboration_document(note_id):
    if not _internal_collaboration_authorized():
        abort(403)
    doc = notes_collaboration.get_collaboration_document(note_id)
    if not doc:
        return jsonify({"error": "collaboration_document_not_found"}), 404
    response = Response(doc["ydoc_blob"], mimetype="application/octet-stream")
    response.headers["X-Nest-Durable-Revision"] = str(doc.get("durable_revision") or 0)
    response.headers["X-Nest-Schema-Version"] = str(doc.get("schema_version") or 1)
    return response


@notes_api_bp.route("/api/internal/notes/<note_id>/collaboration-document", methods=["PUT", "POST"])
def internal_store_collaboration_document(note_id):
    if not _internal_collaboration_authorized():
        abort(403)
    payload = request.get_json(silent=True) or {}
    encoded = payload.get("ydoc_base64")
    if not isinstance(encoded, str):
        return jsonify({"error": "ydoc_base64 is required."}), 400
    try:
        blob = base64.b64decode(encoded.encode("ascii"), validate=True)
        result = notes_collaboration.store_collaboration_document(
            note_id,
            blob,
            title=payload.get("title"),
            content=payload.get("content"),
            page_setup_json=payload.get("page_setup_json"),
            schema_version=int(payload.get("schema_version") or 1),
        )
    except (ValueError, TypeError) as exc:
        return jsonify({"error": str(exc)}), 400
    if not result:
        abort(404)
    return jsonify(result)


@notes_api_bp.route("/api/internal/notes/access-invalidation", methods=["POST"])
def internal_note_access_invalidation():
    if not _internal_collaboration_authorized():
        abort(403)
    return jsonify({"ok": True, "message": "Sidecar should close or downgrade sessions for the submitted resources."})


@notes_api_bp.route("/api/internal/notes/collaboration-health", methods=["GET"])
def internal_note_collaboration_health():
    if not _internal_collaboration_authorized():
        abort(403)
    try:
        with db_connection() as conn:
            collab_table = conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'note_collaboration_documents'"
            ).fetchone()
            notes_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(notes)").fetchall()
            }
    except Exception:
        logger.exception("Collaboration health check failed")
        return jsonify({"ok": False, "schema_version": 0}), 503
    schema_ready = bool(collab_table) and {"collaboration_enabled", "access_version"}.issubset(notes_columns)
    return jsonify({
        "ok": schema_ready,
        "schema_version": 1 if schema_ready else 0,
        "persistence": "sqlite",
    }), 200 if schema_ready else 503


@notes_api_bp.route("/api/internal/notes/collaboration-token/verify", methods=["POST"])
def internal_verify_collaboration_token():
    if not _internal_collaboration_authorized():
        abort(403)
    payload = request.get_json(silent=True) or {}
    ticket = payload.get("ticket")
    requested_note_id = str(payload.get("note_id") or "").strip()
    if not isinstance(ticket, str) or not ticket:
        return jsonify({"error": "ticket is required."}), 400
    try:
        claims = _collaboration_serializer().loads(ticket, max_age=300)
    except SignatureExpired:
        return jsonify({"error": "collaboration_token_expired"}), 401
    except BadSignature:
        return jsonify({"error": "collaboration_token_invalid"}), 401
    note_id = str(claims.get("note_id") or "")
    if requested_note_id and requested_note_id != note_id:
        return jsonify({"error": "collaboration_token_note_mismatch"}), 403
    note = note_store.get_note(note_id)
    if not note:
        return jsonify({"error": "note_not_found"}), 404
    token_revision = int(claims.get("access_revision") or 0)
    current_revision = int(note.get("access_version") or 1)
    if token_revision != current_revision:
        return jsonify({"error": "collaboration_token_stale", "access_revision": current_revision}), 401
    user_id = claims.get("user_id")
    access = note_store.resolve_note_access(note, user_id)
    if not access["can_view"] and not claims.get("public"):
        return jsonify({"error": "collaboration_access_revoked"}), 403
    role = access.get("role") if access["can_view"] else "viewer"
    return jsonify({
        "ok": True,
        "note_id": note_id,
        "user_id": user_id,
        "role": role,
        "can_write": role in {"owner", "editor"},
        "can_review": role in {"owner", "editor", "reviewer"},
        "awareness_allowed": bool(user_id and not claims.get("public") and not claims.get("anonymous")),
        "public": bool(claims.get("public")),
        "anonymous": bool(claims.get("anonymous")),
        "access_revision": current_revision,
        "user": note_store.get_safe_user(user_id) if user_id else None,
    })
