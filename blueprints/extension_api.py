"""Minimal authenticated browser-extension bootstrap and consent API."""

import logging
import uuid

from flask import Blueprint, current_app, jsonify, make_response, request
from flask_login import current_user
from flask_wtf.csrf import generate_csrf

from services.calendar_events import (
    archive_canvas_import_source,
    begin_canvas_sync_run,
    cancel_canvas_sync_run,
    canvas_consent_status,
    canvas_purge_preflight,
    create_canvas_event_link,
    create_canvas_writeback,
    extension_calendar_destinations,
    finalize_canvas_sync_run,
    get_canvas_event_link,
    get_canvas_import_routing,
    get_canvas_import_source,
    get_canvas_import_source_context,
    get_canvas_sync_run,
    get_canvas_writeback_result,
    ingest_canvas_sync_batch,
    list_canvas_import_sources,
    list_canvas_writebacks,
    record_canvas_event_link_result,
    record_canvas_writeback_result,
    renew_canvas_sync_run,
    register_canvas_import_source,
    resume_canvas_sync_run,
    set_canvas_import_routing,
)
from services.calendar_store import calendar_connection
from services.extension_consent import (
    empty_consent_payload,
    get_consent,
    put_consent,
)
from services.extension_contract import (
    EXTENSION_CAPABILITIES,
    EXTENSION_CONTRACT_VERSION,
    ExtensionContractError,
    extension_capability_enabled,
    validate_account_key,
    validate_https_avatar,
    validate_source_key,
    validate_version,
)


extension_api_bp = Blueprint("extension_api", __name__)
logger = logging.getLogger(__name__)
REQUEST_ID_HEADER = "X-Request-ID"


def _request_id():
    candidate = str(request.headers.get(REQUEST_ID_HEADER) or "").strip()
    if candidate and len(candidate) <= 128 and all(
        character.isalnum() or character in "._:-" for character in candidate
    ):
        return candidate
    return uuid.uuid4().hex


def _json_response(payload, status=200, *, request_id=None):
    response = make_response(jsonify(payload), status)
    response.headers["Cache-Control"] = "no-store"
    response.headers[REQUEST_ID_HEADER] = request_id or _request_id()
    return response


def _phase2_response(name=None, value=None, status=200, *, request_id=None, **extra):
    """Return the stable Phase 2B envelope without exposing request payloads."""
    payload = {
        "contractVersion": EXTENSION_CONTRACT_VERSION,
        "ok": True,
    }
    if name is not None:
        payload[name] = value
        if isinstance(value, dict) and "idempotent" in value:
            payload["idempotent"] = bool(value["idempotent"])
    payload.update(extra)
    return _json_response(payload, status, request_id=request_id)


def _consent_response(consent, *, capabilities, cleanup=None, request_id=None):
    """Expose the v1 consent fields in both nested and legacy top-level form."""
    payload = {
        "consent": consent,
        "capabilities": capabilities,
        "version": consent["version"],
        "current": consent["current"],
        "granted": consent["granted"],
        "scopes": consent["scopes"],
        "sourceKey": consent["sourceKey"],
    }
    if cleanup is not None:
        payload["cleanup"] = cleanup
    return _phase2_response(request_id=request_id, **payload)


def _error_response(code, message, status=400, *, contract_version=None, state=None, request_id=None):
    payload = {
        "ok": False,
        "error": {
            "code": code,
            "message": message,
        },
    }
    if contract_version is not None:
        payload["contractVersion"] = contract_version
    if state is not None:
        payload["state"] = state
    return _json_response(payload, status, request_id=request_id)


def _authenticated_or_error(*, identity=False):
    if current_user.is_authenticated:
        return None
    if identity:
        return _json_response({
            "contractVersion": EXTENSION_CONTRACT_VERSION,
            "state": "signed_out",
        }, 401)
    return _error_response(
        "authentication_required",
        "Authentication required.",
        401,
        contract_version=EXTENSION_CONTRACT_VERSION,
        state="signed_out",
    )


def _user_id():
    return str(getattr(current_user, "id", "") or "").strip()


def _parse_json_object():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise ExtensionContractError("invalid_json", "Request body must be a JSON object.")
    return payload


def _parse_bounded_json_object(max_bytes=512 * 1024):
    """Parse a JSON object after enforcing both declared and actual body size."""
    if request.content_length is not None and request.content_length > max_bytes:
        raise ExtensionContractError("payload_too_large", "Request body exceeds the allowed size.")
    raw = request.get_data(cache=True)
    if len(raw) > max_bytes:
        raise ExtensionContractError("payload_too_large", "Request body exceeds the allowed size.")
    return _parse_json_object()


def _validate_object_schema(payload, *, allowed, required=()):
    if not isinstance(payload, dict):
        raise ExtensionContractError("invalid_json", "Request body must be a JSON object.")
    _reject_credentials(payload)
    unknown = sorted(set(payload) - set(allowed))
    if unknown:
        raise ExtensionContractError("unknown_field", "Request contains an unsupported field.")
    missing = [field for field in required if field not in payload]
    if missing:
        raise ExtensionContractError("missing_field", "A required field is missing.")
    return payload


def _reject_credentials(value):
    credential_keys = {
        "access_token", "api_key", "authorization", "cookie", "cookies", "credential",
        "credentials", "password", "refresh_token", "secret", "session", "session_cookie",
        "token", "tokens",
    }
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).strip().lower() in credential_keys:
                raise ExtensionContractError(
                    "credentials_not_allowed",
                    "Canvas credentials, cookies, and tokens are not accepted by this API.",
                )
            _reject_credentials(child)
    elif isinstance(value, list):
        for child in value:
            _reject_credentials(child)


def _request_version(payload, *, required=False):
    value = payload.get("consent_version", payload.get("version"))
    if value is None and not required:
        return None
    if isinstance(value, str) and value.isdecimal():
        value = int(value)
    return validate_version(value)


def _source_or_error(user_id, source_id, *, include_archived=True):
    source = get_canvas_import_source(user_id, source_id, include_archived=include_archived)
    if source is None:
        raise ExtensionContractError("source_not_found", "Canvas import source was not found.")
    return source


def _source_context_or_error(user_id, source_reference, *, include_archived=True):
    source = get_canvas_import_source_context(
        user_id,
        source_reference,
        include_archived=include_archived,
    )
    if source is None:
        raise ExtensionContractError("source_not_found", "Canvas import source was not found.")
    return source


def _source_account_key(user_id, source):
    if source.get("account_key"):
        return source["account_key"]
    context = _source_context_or_error(
        user_id,
        source.get("source_ref") or source.get("source_id"),
        include_archived=True,
    )
    return context["account_key"]


def _require_source_consent(user_id, source, scopes, version=None):
    account_key = _source_account_key(user_id, source)
    return canvas_consent_status(
        user_id,
        account_key,
        required_scopes=tuple(scopes),
        version=version,
    )


def _query_only(allowed):
    unknown = sorted(set(request.args) - set(allowed))
    if unknown:
        raise ExtensionContractError("unknown_field", "Request contains an unsupported query field.")


def _optional_generation(value):
    if value in (None, ""):
        return None
    if isinstance(value, str) and value.isdecimal():
        value = int(value)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ExtensionContractError("invalid_generation", "generation must be a positive integer.")
    return value


def _body_or_empty():
    if not request.data:
        return {}
    return _parse_json_object()


def _consent_keys(source_key, account_key, version):
    if version is not None:
        if isinstance(version, str):
            if not version.isdecimal():
                raise ExtensionContractError("unsupported_version", "version must be an integer.")
            version = int(version)
        elif isinstance(version, bool) or not isinstance(version, int):
            raise ExtensionContractError("unsupported_version", "version must be an integer.")
    account_key = validate_account_key(account_key)
    return (
        validate_version(version, default=EXTENSION_CONTRACT_VERSION),
        validate_source_key(source_key, account_key=account_key),
        account_key,
    )


def _effective_extension_capabilities():
    configured = current_app.config.get("EXTENSION_CAPABILITIES")
    effective = dict(configured) if isinstance(configured, dict) else dict(EXTENSION_CAPABILITIES)
    for capability in EXTENSION_CAPABILITIES:
        effective[capability] = extension_capability_enabled(capability)
    return effective


def _require_capabilities(*capabilities):
    for capability in capabilities:
        if not extension_capability_enabled(capability):
            raise ExtensionContractError(
                "capability_disabled",
                f"The extension capability {capability} is not enabled.",
            )


@extension_api_bp.route("/api/extension/identity", methods=["GET"])
def extension_identity():
    unauthorized = _authenticated_or_error(identity=True)
    if unauthorized:
        return unauthorized

    user_id = _user_id()
    if not user_id:
        return _error_response("invalid_user", "Authenticated user id is unavailable.", 500)
    display_name = str(
        getattr(current_user, "name", None)
        or getattr(current_user, "username", None)
        or user_id
    ).strip()
    username = getattr(current_user, "username", None)
    username = str(username).strip() if username not in (None, "") else None
    return _json_response({
        "contractVersion": EXTENSION_CONTRACT_VERSION,
        "state": "authenticated",
        "profile": {
            "id": user_id,
            "displayName": display_name,
            "username": username,
            "avatarUrl": validate_https_avatar(getattr(current_user, "picture_url", None)),
        },
    })


@extension_api_bp.route("/api/extension/csrf", methods=["GET"])
def extension_csrf():
    unauthorized = _authenticated_or_error()
    if unauthorized:
        return unauthorized

    token = generate_csrf()
    response = _phase2_response(csrfToken=token)
    response.set_cookie(
        "csrf_token",
        token,
        secure=current_app.config["SESSION_COOKIE_SECURE"],
        httponly=False,
        samesite="Lax",
    )
    return response


@extension_api_bp.route("/api/extension/consent", methods=["GET"])
def get_extension_consent():
    unauthorized = _authenticated_or_error()
    if unauthorized:
        return unauthorized

    try:
        _query_only({"source_key", "sourceKey", "account_key", "accountKey", "version", "consent_version"})
        source_key = request.args.get("source_key", request.args.get("sourceKey"))
        account_key = request.args.get("account_key", request.args.get("accountKey"))
        if request.args.get("source_key") and request.args.get("sourceKey") is not None and request.args.get("sourceKey") != request.args.get("source_key"):
            raise ExtensionContractError("source_key_conflict", "source_key and sourceKey must match.")
        if request.args.get("account_key") and request.args.get("accountKey") is not None and request.args.get("accountKey") != request.args.get("account_key"):
            raise ExtensionContractError("account_key_conflict", "account_key and accountKey must match.")
        version, source_key, account_key = _consent_keys(
            source_key,
            account_key,
            request.args.get("version"),
        )
        record = get_consent(_user_id(), source_key, account_key)
        consent = record.to_payload() if record else empty_consent_payload(
            source_key, account_key, version
        )
        return _consent_response(
            consent,
            capabilities=_effective_extension_capabilities(),
        )
    except ExtensionContractError as exc:
        return _error_response(exc.code, str(exc), 400, contract_version=EXTENSION_CONTRACT_VERSION)
    except Exception:
        logger.exception("Failed to load extension consent")
        return _error_response("consent_unavailable", "Unable to load consent.", 500)


@extension_api_bp.route("/api/extension/consent", methods=["PUT"])
def put_extension_consent():
    unauthorized = _authenticated_or_error()
    if unauthorized:
        return unauthorized

    try:
        payload = _parse_json_object()
        _reject_credentials(payload)
        _validate_object_schema(
            payload,
            allowed={
                "version", "consent_version", "source_key", "sourceKey",
                "account_key", "accountKey", "action", "scopes",
            },
            required={"action", "scopes"},
        )
        source_key = payload.get("source_key", payload.get("sourceKey"))
        account_key = payload.get("account_key", payload.get("accountKey"))
        if not source_key or not account_key:
            raise ExtensionContractError("missing_field", "source_key and account_key are required.")
        if payload.get("source_key") and payload.get("sourceKey") is not None and payload.get("sourceKey") != payload.get("source_key"):
            raise ExtensionContractError("source_key_conflict", "source_key and sourceKey must match.")
        if payload.get("account_key") and payload.get("accountKey") is not None and payload.get("accountKey") != payload.get("account_key"):
            raise ExtensionContractError("account_key_conflict", "account_key and accountKey must match.")
        version, source_key, account_key = _consent_keys(
            source_key,
            account_key,
            payload.get("version", payload.get("consent_version")),
        )
        cleanup_before = (
            _consent_cleanup_snapshot(_user_id(), account_key)
            if payload.get("action") == "revoke"
            else None
        )
        record = put_consent(
            _user_id(),
            source_key,
            account_key,
            action=payload.get("action"),
            scopes=payload.get("scopes"),
            version=version,
        )
        cleanup = (
            _consent_cleanup_counts(cleanup_before, _user_id())
            if cleanup_before is not None
            else {"sourcesArchived": 0, "eventsArchived": 0, "runsCancelled": 0,
                  "writebacksCancelled": 0, "linksArchived": 0}
        )
        return _consent_response(
            record.to_payload(),
            capabilities=_effective_extension_capabilities(),
            cleanup=cleanup,
        )
    except ExtensionContractError as exc:
        return _error_response(exc.code, str(exc), 400, contract_version=EXTENSION_CONTRACT_VERSION)
    except Exception:
        logger.exception("Failed to update extension consent")
        return _error_response("consent_unavailable", "Unable to update consent.", 500)


def _consent_cleanup_snapshot(user_id, account_key):
    """Capture owned active rows so the existing transactional hook can be reported."""
    with calendar_connection() as connection:
        sources = connection.execute(
            """SELECT source_id FROM calendar_import_sources
               WHERE user_id = ? AND provider = 'canvas' AND account_key = ?
                 AND status != 'archived'""",
            [user_id, account_key],
        ).fetchall()
        source_ids = [row["source_id"] for row in sources]
        result = {"source_ids": source_ids, "events": 0, "runs": 0, "writebacks": 0, "links": 0}
        for source_id in source_ids:
            result["events"] += connection.execute(
                """SELECT COUNT(*) FROM calendar_cache
                   WHERE user_id = ? AND canvas_source_id = ? AND canvas_soft_deleted = 0""",
                [user_id, source_id],
            ).fetchone()[0]
            result["runs"] += connection.execute(
                """SELECT COUNT(*) FROM calendar_sync_runs
                   WHERE user_id = ? AND source_id = ? AND state = 'active'""",
                [user_id, source_id],
            ).fetchone()[0]
            result["writebacks"] += connection.execute(
                """SELECT COUNT(*) FROM calendar_writebacks
                   WHERE user_id = ? AND source_id = ?
                     AND state IN ('waiting_for_canvas_session', 'queued', 'retryable_failed')""",
                [user_id, source_id],
            ).fetchone()[0]
            result["links"] += connection.execute(
                """SELECT COUNT(*) FROM calendar_event_links
                   WHERE user_id = ? AND source_id = ? AND archived_at IS NULL""",
                [user_id, source_id],
            ).fetchone()[0]
    return result


def _consent_cleanup_counts(before, user_id):
    """Report only transitions caused by the revocation hook."""
    if not before["source_ids"]:
        return {
            "sourcesArchived": 0,
            "eventsArchived": 0,
            "runsCancelled": 0,
            "writebacksCancelled": 0,
            "linksArchived": 0,
        }
    placeholders = ",".join("?" for _ in before["source_ids"])
    with calendar_connection() as connection:
        params = [user_id, *before["source_ids"]]
        sources_archived = connection.execute(
            f"""SELECT COUNT(*) FROM calendar_import_sources
                WHERE user_id = ? AND source_id IN ({placeholders}) AND status = 'archived'""",
            params,
        ).fetchone()[0]
        events_archived = connection.execute(
            f"""SELECT COUNT(*) FROM calendar_cache
                WHERE user_id = ? AND canvas_source_id IN ({placeholders})
                  AND canvas_soft_deleted = 1""",
            params,
        ).fetchone()[0]
        runs_cancelled = connection.execute(
            f"""SELECT COUNT(*) FROM calendar_sync_runs
                WHERE user_id = ? AND source_id IN ({placeholders}) AND state = 'cancelled'
                  AND error_code = 'consent_revoked'""",
            params,
        ).fetchone()[0]
        writebacks_cancelled = connection.execute(
            f"""SELECT COUNT(*) FROM calendar_writebacks
                WHERE user_id = ? AND source_id IN ({placeholders}) AND state = 'cancelled'
                  AND error_code = 'consent_revoked'""",
            params,
        ).fetchone()[0]
        links_archived = connection.execute(
            f"""SELECT COUNT(*) FROM calendar_event_links
                WHERE user_id = ? AND source_id IN ({placeholders})
                  AND archived_at IS NOT NULL AND mirror_error_code = 'consent_revoked'""",
            params,
        ).fetchone()[0]
    return {
        "sourcesArchived": min(sources_archived, len(before["source_ids"])),
        "eventsArchived": min(events_archived, before["events"]),
        "runsCancelled": min(runs_cancelled, before["runs"]),
        "writebacksCancelled": min(writebacks_cancelled, before["writebacks"]),
        "linksArchived": min(links_archived, before["links"]),
    }


def _auth_or_response():
    return _authenticated_or_error()


def _handle_extension_error(exc):
    if isinstance(exc, ExtensionContractError):
        status = 404 if exc.code in {
            "source_not_found", "run_not_found", "event_link_not_found", "writeback_not_found",
        } else 400
        return _error_response(exc.code, str(exc), status, contract_version=EXTENSION_CONTRACT_VERSION)
    if isinstance(exc, (TypeError, ValueError)):
        return _error_response("invalid_request", "The request is invalid.", 400, contract_version=EXTENSION_CONTRACT_VERSION)
    logger.exception("Extension calendar operation failed")
    return _error_response("calendar_unavailable", "Unable to complete the calendar operation.", 500, contract_version=EXTENSION_CONTRACT_VERSION)


@extension_api_bp.after_request
def extension_response_contract(response):
    """Keep even global CSRF failures in the extension's JSON/no-store contract."""
    response.headers["Cache-Control"] = "no-store"
    if response.headers.get("X-APStudy-CSRF-Error") == "1" and response.mimetype != "application/json":
        response = _error_response(
            "csrf_required", "CSRF validation failed.", 400,
            contract_version=EXTENSION_CONTRACT_VERSION,
            request_id=response.headers.get(REQUEST_ID_HEADER),
        )
        response.headers["X-APStudy-CSRF-Error"] = "1"
    if not response.headers.get(REQUEST_ID_HEADER):
        response.headers[REQUEST_ID_HEADER] = _request_id()
    return response


def _idempotency_from_header(payload):
    if "idempotency_key" not in payload and request.headers.get("Idempotency-Key"):
        payload["idempotency_key"] = request.headers["Idempotency-Key"]
    return payload


@extension_api_bp.route("/api/extension/calendars", methods=["GET"])
def list_extension_calendar_destinations():
    unauthorized = _auth_or_response()
    if unauthorized:
        return unauthorized
    try:
        _require_capabilities("calendar_projection")
        return _phase2_response("calendars", extension_calendar_destinations(_user_id()))
    except Exception as exc:
        return _handle_extension_error(exc)


@extension_api_bp.route("/api/extension/calendar/sources", methods=["GET"])
def list_extension_canvas_sources():
    unauthorized = _auth_or_response()
    if unauthorized:
        return unauthorized
    try:
        _require_capabilities("calendar_read")
        _query_only({"include_archived"})
        include_archived = request.args.get("include_archived", "true").lower() not in {"0", "false", "no"}
        return _phase2_response(
            "sources", list_canvas_import_sources(_user_id(), include_archived=include_archived)
        )
    except Exception as exc:
        return _handle_extension_error(exc)


@extension_api_bp.route("/api/extension/calendar/sources", methods=["POST"])
@extension_api_bp.route("/api/extension/calendar/sources/<source_id>", methods=["PUT"])
def register_extension_canvas_source(source_id=None):
    unauthorized = _auth_or_response()
    if unauthorized:
        return unauthorized
    try:
        _require_capabilities("calendar_upload")
        payload = _parse_json_object()
        if source_id is not None:
            if "source_id" in payload and payload["source_id"] != source_id:
                raise ExtensionContractError("source_id_conflict", "The source_id does not match the route.")
            payload["source_id"] = source_id
        _validate_object_schema(
            payload,
            allowed={"account_key", "source_id", "origin", "provider_user_id", "label", "default_mirror_calendar", "consent_version", "version"},
            required={"account_key", "source_id", "origin", "provider_user_id", "label", "consent_version"},
        )
        payload["consent_version"] = _request_version(payload, required=True)
        canvas_consent_status(
            _user_id(), payload["account_key"],
            required_scopes=("full_history_upload", "ongoing_read"),
            version=payload["consent_version"],
        )
        return _phase2_response("source", register_canvas_import_source(_user_id(), payload))
    except Exception as exc:
        return _handle_extension_error(exc)


@extension_api_bp.route("/api/extension/calendar/sources/<source_id>", methods=["GET"])
def get_extension_canvas_source(source_id):
    unauthorized = _auth_or_response()
    if unauthorized:
        return unauthorized
    try:
        _require_capabilities("calendar_read")
        return _phase2_response("source", _source_or_error(_user_id(), source_id))
    except Exception as exc:
        return _handle_extension_error(exc)


@extension_api_bp.route("/api/extension/calendar/sources/<source_id>/archive", methods=["PUT", "POST"])
def archive_extension_canvas_source(source_id):
    unauthorized = _auth_or_response()
    if unauthorized:
        return unauthorized
    try:
        _require_capabilities("calendar_source_mutation")
        payload = _body_or_empty()
        _validate_object_schema(payload, allowed=set())
        result = archive_canvas_import_source(_user_id(), source_id)
        if result.get("source") is None:
            raise ExtensionContractError("source_not_found", "Canvas import source was not found.")
        return _phase2_response("archive", result)
    except Exception as exc:
        return _handle_extension_error(exc)


@extension_api_bp.route("/api/extension/calendar/sources/<source_id>/purge-preflight", methods=["GET"])
def purge_preflight_extension_canvas_source(source_id):
    unauthorized = _auth_or_response()
    if unauthorized:
        return unauthorized
    try:
        _require_capabilities("calendar_source_mutation")
        result = canvas_purge_preflight(_user_id(), source_id)
        if result is None:
            raise ExtensionContractError("source_not_found", "Canvas import source was not found.")
        return _phase2_response("purgePreflight", result)
    except Exception as exc:
        return _handle_extension_error(exc)


@extension_api_bp.route("/api/extension/calendar/sources/<source_id>/sync", methods=["POST"])
def start_extension_canvas_sync(source_id):
    unauthorized = _auth_or_response()
    if unauthorized:
        return unauthorized
    try:
        _require_capabilities("calendar_read", "calendar_upload")
        payload = _idempotency_from_header(_parse_json_object())
        _validate_object_schema(
            payload,
            allowed={"scope", "consent_version", "version", "idempotency_key", "run_id"},
            required={"scope", "consent_version", "idempotency_key"},
        )
        payload["consent_version"] = _request_version(payload, required=True)
        source = _source_or_error(_user_id(), source_id, include_archived=False)
        _require_source_consent(_user_id(), source, ("full_history_upload", "ongoing_read"), payload["consent_version"])
        result = begin_canvas_sync_run(_user_id(), source["source_id"], payload=payload)
        result["source_ref"] = source["source_ref"]
        return _phase2_response("run", result)
    except Exception as exc:
        return _handle_extension_error(exc)


@extension_api_bp.route("/api/extension/calendar/sources/<source_id>/sync/<run_id>", methods=["GET"])
@extension_api_bp.route("/api/extension/calendar/sources/<source_id>/sync/<run_id>/status", methods=["GET"])
def get_extension_canvas_sync_status(source_id, run_id):
    unauthorized = _auth_or_response()
    if unauthorized:
        return unauthorized
    try:
        _require_capabilities("calendar_read")
        _query_only({"generation"})
        generation = _optional_generation(request.args.get("generation"))
        source = _source_or_error(_user_id(), source_id, include_archived=True)
        result = get_canvas_sync_run(_user_id(), source["source_id"], run_id, generation=generation)
        if result is None:
            raise ExtensionContractError("run_not_found", "Canvas sync run was not found.")
        result["source_ref"] = source["source_ref"]
        return _phase2_response("run", result)
    except Exception as exc:
        return _handle_extension_error(exc)


def _sync_lease_request(source_id, run_id, action):
    payload = _body_or_empty()
    _validate_object_schema(payload, allowed={"generation", "lease_token"}, required={"generation", "lease_token"})
    generation = _optional_generation(payload["generation"])
    if not isinstance(payload["lease_token"], str) or not payload["lease_token"].strip():
        raise ExtensionContractError("invalid_lease_token", "lease_token is required.")
    kwargs = {"generation": generation, "lease_token": payload["lease_token"]}
    if action == "renew":
        result = renew_canvas_sync_run(_user_id(), source_id, run_id, **kwargs)
    else:
        result = resume_canvas_sync_run(_user_id(), source_id, run_id, **kwargs)
    return _phase2_response("run", result)


@extension_api_bp.route("/api/extension/calendar/sources/<source_id>/sync/<run_id>/resume", methods=["PUT", "PATCH"])
def resume_extension_canvas_sync(source_id, run_id):
    unauthorized = _auth_or_response()
    if unauthorized:
        return unauthorized
    try:
        _require_capabilities("calendar_read", "calendar_upload")
        return _sync_lease_request(source_id, run_id, "resume")
    except Exception as exc:
        return _handle_extension_error(exc)


@extension_api_bp.route("/api/extension/calendar/sources/<source_id>/sync/<run_id>/renew", methods=["PUT", "PATCH"])
def renew_extension_canvas_sync(source_id, run_id):
    unauthorized = _auth_or_response()
    if unauthorized:
        return unauthorized
    try:
        _require_capabilities("calendar_read", "calendar_upload")
        return _sync_lease_request(source_id, run_id, "renew")
    except Exception as exc:
        return _handle_extension_error(exc)


@extension_api_bp.route("/api/extension/calendar/sources/<source_id>/sync/<run_id>/cancel", methods=["POST"])
def cancel_extension_canvas_sync(source_id, run_id):
    unauthorized = _auth_or_response()
    if unauthorized:
        return unauthorized
    try:
        _require_capabilities("calendar_read", "calendar_upload")
        payload = _body_or_empty()
        _validate_object_schema(payload, allowed={"generation", "lease_token", "reason"}, required={"generation", "lease_token"})
        generation = _optional_generation(payload["generation"])
        if not isinstance(payload["lease_token"], str) or not payload["lease_token"].strip():
            raise ExtensionContractError("invalid_lease_token", "lease_token is required.")
        result = cancel_canvas_sync_run(
            _user_id(), source_id, run_id,
            generation=generation,
            lease_token=payload["lease_token"],
            reason=payload.get("reason"),
        )
        return _phase2_response("run", result)
    except Exception as exc:
        return _handle_extension_error(exc)


CANVAS_ITEM_FIELDS = {
    "context_id", "contextId", "context", "calendar_id", "calendarId", "calendar",
    "item_type", "itemType", "type", "item_id", "itemId", "id", "occurrence_id", "occurrenceId",
    "is_all_day", "all_day", "allDay", "start", "start_at", "startAt", "event_start", "due_at", "dueAt",
    "end", "end_at", "endAt", "event_end", "title", "summary", "name", "description", "raw_description",
    "completion_status", "completionStatus", "completion_source", "completionSource", "source_revision",
    "sourceRevision", "revision", "source_hash", "sourceHash", "content_hash", "contentHash", "course_name",
    "courseName", "context_name", "contextName",
}


def _validate_batch_items(items):
    if not isinstance(items, list):
        raise ExtensionContractError("invalid_items", "items must be an array.")
    if len(items) > 100:
        raise ExtensionContractError("batch_too_large", "A Canvas batch may contain at most 100 items.")
    for item in items:
        if not isinstance(item, dict):
            raise ExtensionContractError("invalid_item", "Each Canvas item must be a JSON object.")
        _validate_object_schema(item, allowed=CANVAS_ITEM_FIELDS)


@extension_api_bp.route("/api/extension/calendar/sources/<source_id>/sync/<run_id>/batch", methods=["POST"])
@extension_api_bp.route("/api/extension/calendar/sources/<source_id>/sync/<run_id>/batches", methods=["POST"])
def ingest_extension_canvas_sync_batch(source_id, run_id):
    unauthorized = _auth_or_response()
    if unauthorized:
        return unauthorized
    try:
        _require_capabilities("calendar_read", "calendar_upload")
        payload = _idempotency_from_header(_parse_bounded_json_object())
        _validate_object_schema(
            payload,
            allowed={"items", "generation", "lease_token", "idempotency_key", "checkpoint"},
            required={"items", "generation", "lease_token", "idempotency_key"},
        )
        _validate_batch_items(payload["items"])
        generation = _optional_generation(payload["generation"])
        if not isinstance(payload["lease_token"], str) or not payload["lease_token"].strip():
            raise ExtensionContractError("invalid_lease_token", "lease_token is required.")
        source = _source_or_error(_user_id(), source_id, include_archived=False)
        _require_source_consent(_user_id(), source, ("full_history_upload", "ongoing_read"))
        result = ingest_canvas_sync_batch(_user_id(), source_id, run_id, payload=payload)
        return _phase2_response("batch", result)
    except Exception as exc:
        return _handle_extension_error(exc)


@extension_api_bp.route("/api/extension/calendar/sources/<source_id>/sync/<run_id>/finalize", methods=["POST"])
def finalize_extension_canvas_sync(source_id, run_id):
    unauthorized = _auth_or_response()
    if unauthorized:
        return unauthorized
    try:
        _require_capabilities("calendar_read", "calendar_upload")
        payload = _body_or_empty()
        _validate_object_schema(
            payload,
            allowed={"scope", "generation", "lease_token", "status", "complete"},
            required={"scope", "generation", "lease_token"},
        )
        if "status" not in payload and "complete" not in payload:
            raise ExtensionContractError("missing_field", "status or complete is required.")
        generation = _optional_generation(payload["generation"])
        if not isinstance(payload["lease_token"], str) or not payload["lease_token"].strip():
            raise ExtensionContractError("invalid_lease_token", "lease_token is required.")
        source = _source_or_error(_user_id(), source_id, include_archived=False)
        run = get_canvas_sync_run(_user_id(), source_id, run_id, generation=generation)
        if run is None:
            raise ExtensionContractError("run_not_found", "Canvas sync run was not found.")
        _require_source_consent(
            _user_id(),
            source,
            ("full_history_upload", "ongoing_read"),
            version=run["consent_version"],
        )
        result = finalize_canvas_sync_run(
            _user_id(), source_id, run_id, scope=payload["scope"], generation=generation,
            lease_token=payload["lease_token"], status=payload.get("status", "complete"),
            complete=payload.get("complete"),
        )
        return _phase2_response("run", result)
    except Exception as exc:
        return _handle_extension_error(exc)


@extension_api_bp.route("/api/extension/calendar/sources/<source_id>/routing", methods=["GET"])
def get_extension_canvas_routing(source_id):
    unauthorized = _auth_or_response()
    if unauthorized:
        return unauthorized
    try:
        _require_capabilities("calendar_projection")
        _query_only({"state"})
        state = request.args.get("state")
        return _phase2_response("routing", get_canvas_import_routing(_user_id(), source_id, state))
    except Exception as exc:
        return _handle_extension_error(exc)


@extension_api_bp.route("/api/extension/calendar/sources/<source_id>/routing", methods=["PUT", "PATCH"])
def set_extension_canvas_routing(source_id):
    unauthorized = _auth_or_response()
    if unauthorized:
        return unauthorized
    try:
        _require_capabilities("calendar_projection")
        payload = _parse_json_object()
        _validate_object_schema(
            payload,
            allowed={"state", "destination_calendar_id", "fallback_calendar_id"},
            required={"state"},
        )
        if payload["state"] not in {"incomplete", "completed"}:
            raise ExtensionContractError("invalid_route_state", "Routing state must be incomplete or completed.")
        result = set_canvas_import_routing(_user_id(), source_id, payload)
        return _phase2_response("routing", result)
    except Exception as exc:
        return _handle_extension_error(exc)


EVENT_LINK_FIELDS = {
    "account_key", "event_kind", "nest_event_id", "projection_event_id", "event_ref", "source_revision",
    "source_hash", "mirror_state", "canvas_context_id", "context_id", "contextId", "canvas_calendar_id",
    "calendar_id", "calendarId", "canvas_item_type", "item_type", "itemType", "canvas_item_id", "item_id",
    "itemId", "canvas_occurrence_id", "occurrence_id", "occurrenceId",
}
EVENT_LINK_RESULT_FIELDS = {
    "mirror_state", "state", "expected_revision", "expectedRevision", "source_revision", "sourceRevision",
    "source_hash", "sourceHash", "error_code", "error_message", "errorMessage", "mirrored_at", "mirroredAt",
}


def _require_mirroring_consent(source, payload=None):
    _require_capabilities("calendar_mirroring")
    source_account_key = _source_account_key(_user_id(), source)
    account_key = source_account_key if payload is None else payload.get("account_key", source_account_key)
    if account_key != source_account_key:
        raise ExtensionContractError("source_account_mismatch", "The Canvas account does not belong to this import source.")
    return canvas_consent_status(_user_id(), account_key, required_scopes=("mirroring",))


@extension_api_bp.route("/api/extension/calendar/sources/<source_id>/event-links", methods=["GET"])
def get_extension_canvas_event_link_route(source_id):
    unauthorized = _auth_or_response()
    if unauthorized:
        return unauthorized
    try:
        _query_only({"event_ref", "link_id"})
        if not request.args.get("event_ref") and not request.args.get("link_id"):
            raise ExtensionContractError("invalid_event_link", "event_ref or link_id is required.")
        source = _source_or_error(_user_id(), source_id)
        _require_mirroring_consent(source)
        result = get_canvas_event_link(
            _user_id(), source_id, request.args.get("event_ref"), link_id=request.args.get("link_id")
        )
        if result is None:
            raise ExtensionContractError("event_link_not_found", "Canvas event link was not found.")
        return _phase2_response("eventLink", result)
    except Exception as exc:
        return _handle_extension_error(exc)


@extension_api_bp.route("/api/extension/calendar/sources/<source_id>/event-links", methods=["POST"])
def create_extension_canvas_event_link_route(source_id):
    unauthorized = _auth_or_response()
    if unauthorized:
        return unauthorized
    try:
        payload = _parse_json_object()
        _validate_object_schema(payload, allowed=EVENT_LINK_FIELDS, required={"account_key"})
        source = _source_or_error(_user_id(), source_id, include_archived=False)
        _require_mirroring_consent(source, payload)
        return _phase2_response(
            "eventLink", create_canvas_event_link(_user_id(), source_id, payload=payload)
        )
    except Exception as exc:
        return _handle_extension_error(exc)


@extension_api_bp.route("/api/extension/calendar/sources/<source_id>/event-links/<link_id>/result", methods=["GET"])
@extension_api_bp.route("/api/extension/calendar/sources/<source_id>/event-link-results/<link_id>", methods=["GET"])
def get_extension_canvas_event_link_result(source_id, link_id):
    unauthorized = _auth_or_response()
    if unauthorized:
        return unauthorized
    try:
        source = _source_or_error(_user_id(), source_id)
        _require_mirroring_consent(source)
        result = get_canvas_event_link(_user_id(), source_id, link_id=link_id)
        if result is None:
            raise ExtensionContractError("event_link_not_found", "Canvas event link was not found.")
        return _phase2_response("eventLinkResult", result)
    except Exception as exc:
        return _handle_extension_error(exc)


@extension_api_bp.route("/api/extension/calendar/sources/<source_id>/event-links/<link_id>/result", methods=["POST"])
@extension_api_bp.route("/api/extension/calendar/sources/<source_id>/event-link-results/<link_id>", methods=["POST"])
def record_extension_canvas_event_link_result(source_id, link_id):
    unauthorized = _auth_or_response()
    if unauthorized:
        return unauthorized
    try:
        payload = _parse_json_object()
        _validate_object_schema(payload, allowed=EVENT_LINK_RESULT_FIELDS)
        source = _source_or_error(_user_id(), source_id, include_archived=False)
        _require_mirroring_consent(source)
        result = record_canvas_event_link_result(
            _user_id(), source_id, link_id=link_id, payload=payload
        )
        return _phase2_response("eventLinkResult", result)
    except Exception as exc:
        return _handle_extension_error(exc)


WRITEBACK_FIELDS = {
    "account_key", "operation", "event_ref", "expected_revision", "idempotency_key", "target_account",
    "target_calendar", "payload", "state",
}
WRITEBACK_RESULT_FIELDS = {
    "state", "status", "expected_revision", "expectedRevision", "result_revision", "resultRevision",
    "error_code", "error_message", "errorMessage", "retry_count", "next_retry_at", "nextRetryAt",
}


def _require_writeback_consent(source, payload=None):
    _require_capabilities("calendar_two_way_writeback")
    source_account_key = _source_account_key(_user_id(), source)
    account_key = source_account_key if payload is None else payload.get("account_key", source_account_key)
    if account_key != source_account_key:
        raise ExtensionContractError("source_account_mismatch", "The Canvas account does not belong to this import source.")
    return canvas_consent_status(_user_id(), account_key, required_scopes=("two_way_writeback",))


@extension_api_bp.route("/api/extension/calendar/sources/<source_id>/writebacks", methods=["GET"])
@extension_api_bp.route("/api/extension/calendar/sources/<source_id>/writeback-intents", methods=["GET"])
def list_extension_canvas_writebacks(source_id):
    unauthorized = _auth_or_response()
    if unauthorized:
        return unauthorized
    try:
        _query_only({"account_key", "event_ref", "states", "limit"})
        source = _source_or_error(_user_id(), source_id)
        _require_writeback_consent(source, {"account_key": request.args.get("account_key", _source_account_key(_user_id(), source))})
        raw_limit = request.args.get("limit", "100")
        if not raw_limit.isdecimal():
            raise ExtensionContractError("invalid_limit", "limit must be between 1 and 100.")
        states = request.args.getlist("states") or None
        if states and len(states) == 1 and "," in states[0]:
            states = [value for value in states[0].split(",") if value]
        result = list_canvas_writebacks(
            _user_id(), source_id, account_key=request.args.get("account_key"),
            event_ref=request.args.get("event_ref"), states=states, limit=int(raw_limit),
        )
        return _phase2_response("writebacks", result)
    except Exception as exc:
        return _handle_extension_error(exc)


@extension_api_bp.route("/api/extension/calendar/sources/<source_id>/writebacks", methods=["POST"])
@extension_api_bp.route("/api/extension/calendar/sources/<source_id>/writeback-intents", methods=["POST"])
def create_extension_canvas_writeback(source_id):
    unauthorized = _auth_or_response()
    if unauthorized:
        return unauthorized
    try:
        payload = _idempotency_from_header(_parse_json_object())
        _validate_object_schema(payload, allowed=WRITEBACK_FIELDS, required={"account_key", "operation", "idempotency_key"})
        source = _source_or_error(_user_id(), source_id, include_archived=True)
        _require_writeback_consent(source, payload)
        return _phase2_response(
            "writeback", create_canvas_writeback(_user_id(), source_id, payload=payload)
        )
    except Exception as exc:
        return _handle_extension_error(exc)


@extension_api_bp.route("/api/extension/calendar/sources/<source_id>/writebacks/<writeback_id>", methods=["GET"])
@extension_api_bp.route("/api/extension/calendar/sources/<source_id>/writebacks/<writeback_id>/result", methods=["GET"])
def get_extension_canvas_writeback_result(source_id, writeback_id):
    unauthorized = _auth_or_response()
    if unauthorized:
        return unauthorized
    try:
        source = _source_or_error(_user_id(), source_id)
        _require_writeback_consent(source)
        result = get_canvas_writeback_result(_user_id(), source_id, writeback_id)
        if result is None:
            raise ExtensionContractError("writeback_not_found", "Canvas writeback was not found.")
        return _phase2_response("writebackResult", result)
    except Exception as exc:
        return _handle_extension_error(exc)


@extension_api_bp.route("/api/extension/calendar/sources/<source_id>/writebacks/<writeback_id>/result", methods=["POST"])
@extension_api_bp.route("/api/extension/calendar/sources/<source_id>/writeback-results/<writeback_id>", methods=["POST"])
def record_extension_canvas_writeback_result(source_id, writeback_id):
    unauthorized = _auth_or_response()
    if unauthorized:
        return unauthorized
    try:
        payload = _parse_json_object()
        _validate_object_schema(payload, allowed=WRITEBACK_RESULT_FIELDS)
        source = _source_or_error(_user_id(), source_id, include_archived=False)
        _require_writeback_consent(source)
        result = record_canvas_writeback_result(
            _user_id(), source_id, writeback_id, payload=payload
        )
        return _phase2_response("writebackResult", result)
    except Exception as exc:
        return _handle_extension_error(exc)
