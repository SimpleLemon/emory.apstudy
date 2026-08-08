"""
blueprints/auth.py

OAuth 2.0 authentication flow.
Handles login, callback, session creation, and logout via Appwrite OAuth.
"""

import os
import secrets
import logging
import re
import html
from datetime import datetime

import click

import requests as http_requests
from flask import (
    Blueprint, Response, abort, redirect, url_for, session, render_template, request, jsonify, make_response,
    current_app
)
from flask_login import login_user, logout_user, current_user, login_required
from flask_wtf.csrf import generate_csrf

from appwrite.client import Client
from appwrite.exception import AppwriteException
from appwrite.enums.o_auth_provider import OAuthProvider
from appwrite.query import Query
from appwrite.services.account import Account
from appwrite.services.users import Users
from appwrite_client import COLLECTIONS
from appwrite_client import ENDPOINT as APPWRITE_ENDPOINT, PROJECT_ID as APPWRITE_PROJECT_ID
from appwrite_client import client as appwrite_client
from appwrite_helpers import (
    create_row_safe,
    format_datetime,
    get_row_safe,
    first_row,
    list_rows_safe,
    update_row_safe,
)
from models import User, user_from_doc
from avatar_images import DEFAULT_AVATAR_URL
from services.avatar_storage import delete_avatar_file, store_avatar_from_url
from services.chat_presence import sync_chat_presence_labels_for_user
from services.discord_audit import emit_server_log_event, emit_user_event, format_actor, format_user_target
from services import discord_bridge, invites, notes_collaboration
from services.entitlements import TIER_BADGES, TIER_LABELS, normalize_tier
from services.user_profile import (
    is_early_member as _is_early_member,
    is_emory_school as _is_emory_school,
    normalize_banner_color as _normalize_banner_color,
    profile_handle as _profile_handle,
)
from app import AUTH_SESSION_DURATION

auth_bp = Blueprint("auth", __name__)
logger = logging.getLogger(__name__)

INVITE_COOKIE = "nest_invite"
INVITE_COOKIE_MAX_AGE = 30 * 24 * 60 * 60

LOGIN_CSP = "; ".join([
    "default-src 'self'",
    "base-uri 'self'",
    "object-src 'none'",
    "frame-ancestors 'none'",
    "script-src 'self' https://cdn.jsdelivr.net",
    "style-src 'self'",
    "font-src 'self'",
    "img-src 'self' https://resources.apstudy.org data:",
    "connect-src 'self' https://nyc.cloud.appwrite.io",
    "form-action 'self'",
])

LANDING_CSP = "; ".join([
    "default-src 'self'",
    "base-uri 'self'",
    "object-src 'none'",
    "frame-ancestors 'none'",
    "script-src 'self' https://www.googletagmanager.com",
    "style-src 'self'",
    "font-src 'self'",
    "img-src 'self' https://resources.apstudy.org https://www.google-analytics.com data:",
    "connect-src 'self' https://www.googletagmanager.com https://www.google-analytics.com https://region1.google-analytics.com https://analytics.google.com",
    "form-action 'self'",
])

PUBLIC_PROFILE_CSP = "; ".join([
    "default-src 'self'",
    "base-uri 'self'",
    "object-src 'none'",
    "frame-ancestors 'none'",
    "script-src 'self' https://cdn.jsdelivr.net https://www.googletagmanager.com",
    "style-src 'self' https://fonts.googleapis.com",
    "font-src 'self' https://fonts.gstatic.com",
    "img-src 'self' https: data:",
    "connect-src 'self' https://nyc.cloud.appwrite.io https://www.googletagmanager.com https://www.google-analytics.com https://region1.google-analytics.com https://analytics.google.com",
    "form-action 'self'",
])

USERNAME_MIN_LENGTH = 3
USERNAME_MAX_LENGTH = 20
USERNAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")
APPWRITE_OAUTH_PROVIDERS = {
    "discord": OAuthProvider.DISCORD,
    "github": OAuthProvider.GITHUB,
    "google": OAuthProvider.GOOGLE,
}
OAUTH_PROVIDER_SCOPES = {
    "google": ["openid", "email", "profile"],
    "github": ["read:user", "user:email"],
    "discord": ["identify", "email"],
}
APPWRITE_OAUTH_STATE_KEY = "appwrite_oauth_state"
APPWRITE_OAUTH_PROVIDER_KEY = "appwrite_oauth_provider"
APPWRITE_OAUTH_LINK_MODE_KEY = "appwrite_oauth_link_mode"
APPWRITE_OAUTH_REQUIRED_SCOPES = ("sessions.write",)
AUTH_ERROR_SESSION_KEY = "auth_error_code"
LOGIN_NEXT_SESSION_KEY = "login_next_url"
AUTH_ERROR_OAUTH_START_SCOPE = "AUTH-OAUTH-START-SCOPE"
AUTH_ERROR_OAUTH_START = "AUTH-OAUTH-START"
AUTH_ERROR_OAUTH_STATE = "AUTH-OAUTH-STATE"
AUTH_ERROR_OAUTH_CREDENTIALS = "AUTH-OAUTH-CREDENTIALS"
AUTH_ERROR_OAUTH_CALLBACK = "AUTH-OAUTH-CALLBACK"
AUTH_ERROR_OAUTH_PROVIDER = "AUTH-OAUTH-PROVIDER"
AUTH_ERROR_MESSAGE = "We couldn't complete sign-in. Please try again."


def _sanitize_log_text(value, limit=500):
    text = str(value or "")
    text = re.sub(r"((?:[?&]|\b)(?:secret|key|token)=)[^&\s]+", r"\1[redacted]", text, flags=re.IGNORECASE)
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = " ".join(text.split())
    if len(text) > limit:
        return f"{text[:limit]}..."
    return text


def _appwrite_exception_details(exc):
    message = getattr(exc, "message", None) or str(exc)
    details = {
        "class": exc.__class__.__name__,
        "message": _sanitize_log_text(message),
    }
    for attr in ("code", "response_code", "type"):
        value = getattr(exc, attr, None)
        if value not in (None, ""):
            details[attr] = value

    text = str(message)
    missing_scopes = [
        scope
        for scope in APPWRITE_OAUTH_REQUIRED_SCOPES
        if scope in text
    ]
    if missing_scopes:
        details["missing_scopes"] = ",".join(missing_scopes)
    return details


def _appwrite_oauth_failure_url(state):
    return url_for("auth.appwrite_oauth_failure", state=state, _external=True)


def _normalize_session_key(key):
    return re.sub(r"[_\s-]", "", str(key or "").lower())


def _session_field(payload, *aliases):
    """Return the first non-empty value for any Appwrite session field alias."""
    if not payload or not isinstance(payload, dict) or not aliases:
        return None
    wanted = {_normalize_session_key(alias) for alias in aliases}
    for key, value in payload.items():
        if _normalize_session_key(key) in wanted and value not in (None, ""):
            return value
    return None


def _oauth_provider_scopes(provider_key):
    scopes = OAUTH_PROVIDER_SCOPES.get(str(provider_key or "").strip().lower())
    return scopes or None


def _appwrite_oauth_redirect_url(provider_key, success_url, failure_url):
    kwargs = {
        "provider": APPWRITE_OAUTH_PROVIDERS[provider_key],
        "success": success_url,
        "failure": failure_url,
    }
    scopes = _oauth_provider_scopes(provider_key)
    if scopes:
        kwargs["scopes"] = scopes
    return Account(appwrite_client).create_o_auth2_token(**kwargs)


def _log_oauth_exception(message, exc, *args):
    details = _appwrite_exception_details(exc)
    if isinstance(exc, AppwriteException):
        logger.error(f"{message}: appwrite_error=%s", *args, details)
        return details
    logger.exception(f"{message}: appwrite_error=%s", *args, details)
    return details


def _oauth_start_error_code(details):
    if "sessions.write" in details.get("missing_scopes", ""):
        return AUTH_ERROR_OAUTH_START_SCOPE
    return AUTH_ERROR_OAUTH_START


@auth_bp.cli.command("appwrite-oauth-preflight")
@click.option(
    "--provider",
    default="google",
    type=click.Choice(sorted(APPWRITE_OAUTH_PROVIDERS.keys())),
    show_default=True,
    help="OAuth provider to use for the Appwrite token preflight.",
)
@click.option(
    "--base-url",
    default="https://nest.apstudy.org",
    show_default=True,
    help="Public app origin used to build OAuth callback URLs.",
)
def appwrite_oauth_preflight(provider, base_url):
    """Verify the configured Appwrite API key can start SSR OAuth."""
    provider_key = str(provider or "").strip().lower()
    normalized_base_url = str(base_url or "").rstrip("/") or "https://nest.apstudy.org"
    with current_app.test_request_context(base_url=normalized_base_url):
        success_url = url_for("auth.appwrite_oauth_callback", state="preflight", _external=True)
        failure_url = _appwrite_oauth_failure_url("preflight")

    try:
        _appwrite_oauth_redirect_url(provider_key, success_url, failure_url)
    except Exception as exc:
        details = _appwrite_exception_details(exc)
        logger.error(
            "Appwrite OAuth preflight failed: provider=%s appwrite_error=%s",
            provider_key,
            details,
        )
        click.echo("Appwrite OAuth preflight failed.")
        click.echo(f"provider: {provider_key}")
        click.echo(f"success_url: {success_url}")
        click.echo(f"failure_url: {failure_url}")
        click.echo(f"appwrite_error: {details}")
        if "sessions.write" in details.get("missing_scopes", ""):
            click.echo("required_scope_hint: sessions.write")
        raise click.ClickException("Appwrite API key cannot start OAuth token flow.")

    click.echo("Appwrite OAuth preflight passed.")
    click.echo(f"provider: {provider_key}")
    click.echo(f"success_url: {success_url}")
    click.echo(f"failure_url: {failure_url}")


def _user_needs_avatar_backfill(user_doc):
    if not user_doc:
        return False
    avatar_source = str(user_doc.get("avatar_source") or "").strip().lower()
    if avatar_source == "upload":
        return False
    return _clean_avatar_url(user_doc.get("picture_url")) is None


def _backfill_user_avatar(user_doc, *, dry_run=False):
    user_id = str(user_doc.get("$id") or user_doc.get("id") or "").strip()
    if not user_id:
        return {"user_id": None, "status": "skipped", "reason": "missing_user_id"}

    if not _user_needs_avatar_backfill(user_doc):
        return {"user_id": user_id, "status": "skipped", "reason": "avatar_present"}

    provider = str(user_doc.get("provider") or "google").strip().lower()
    remote_user = _account_from_user_id(user_id)
    identity_token = _provider_access_token_from_identities(user_id, provider=provider)
    provider_access_token = identity_token.get("provider_access_token")
    if identity_token.get("provider"):
        provider = identity_token["provider"]

    provider_profile = _fetch_provider_profile(provider, provider_access_token)
    avatar_url = _provider_avatar_url(provider_profile, remote_user, provider=provider)
    if not avatar_url:
        return {"user_id": user_id, "status": "unresolved", "reason": "no_avatar_source"}

    if dry_run:
        return {
            "user_id": user_id,
            "status": "would_update",
            "provider": provider,
            "avatar_url": avatar_url,
        }

    picture_url, avatar_file_id, storage_result, avatar_file_size_bytes = _store_provider_avatar(
        user_id,
        avatar_url,
        page_context="auth/backfill-avatars",
    )
    update_row_safe(
        COLLECTIONS["users"],
        user_id,
        {
            "picture_url": picture_url,
            "avatar_file_id": avatar_file_id,
            "avatar_source": "provider",
            "avatar_file_size_bytes": avatar_file_size_bytes,
            "provider": provider,
        },
    )
    return {
        "user_id": user_id,
        "status": "updated",
        "provider": provider,
        "storage_result": storage_result,
        "picture_url": picture_url,
    }


@auth_bp.cli.command("backfill-avatars")
@click.option("--dry-run", is_flag=True, help="Report candidates without writing changes.")
@click.option("--limit", default=100, show_default=True, help="Maximum users to inspect.")
def backfill_avatars(dry_run, limit):
    """Repair users missing provider avatars after OAuth signup."""
    from appwrite_helpers import list_rows_all

    response = list_rows_all(
        COLLECTIONS["users"],
        [Query.is_null("avatar_source"), Query.limit(limit)],
        limit=limit,
    )
    candidates = [
        row for row in response.get("rows", [])
        if _user_needs_avatar_backfill(row)
    ]

    click.echo(f"candidates: {len(candidates)} (limit={limit}, dry_run={dry_run})")
    updated = 0
    unresolved = 0
    skipped = 0
    for user_doc in candidates:
        result = _backfill_user_avatar(user_doc, dry_run=dry_run)
        status = result.get("status")
        if status in {"updated", "would_update"}:
            updated += 1
        elif status == "unresolved":
            unresolved += 1
        else:
            skipped += 1
        click.echo(result)

    click.echo(
        f"summary: updated={updated} unresolved={unresolved} skipped={skipped} dry_run={dry_run}"
    )


def _set_oauth_session(provider, user_id, email, name=None, picture_url=None):
    session["oauth_provider"] = provider
    session["oauth_user_id"] = user_id
    session["oauth_email"] = email
    if name:
        session["oauth_name"] = name
    if picture_url:
        session["oauth_picture_url"] = picture_url


def _account_to_dict(value):
    if isinstance(value, dict):
        return value

    if hasattr(value, "to_dict"):
        return value.to_dict()
    if hasattr(value, "model_dump"):
        return value.model_dump(by_alias=True, mode="json")
    return {}


def _account_from_jwt(jwt):
    if not jwt:
        return {}
    if not APPWRITE_ENDPOINT or not APPWRITE_PROJECT_ID:
        raise RuntimeError("Appwrite endpoint/project is not configured.")
    jwt_client = Client()
    jwt_client.set_endpoint(APPWRITE_ENDPOINT)
    jwt_client.set_project(APPWRITE_PROJECT_ID)
    jwt_client.set_jwt(jwt)
    return _account_to_dict(Account(jwt_client).get())


def _account_from_user_id(user_id):
    if not user_id:
        return {}
    return _account_to_dict(Users(appwrite_client).get(str(user_id)))


def _identities_for_appwrite_user(appwrite_user_id):
    """Return OAuth identities linked to an Appwrite auth user."""
    appwrite_user_id = str(appwrite_user_id or "").strip()
    if not appwrite_user_id:
        return []
    try:
        response = Users(appwrite_client).list_identities(
            [Query.equal("userId", [appwrite_user_id])],
        )
        payload = _account_to_dict(response)
        identities = payload.get("identities") or []
        return [_account_to_dict(identity) for identity in identities]
    except Exception:
        logger.exception("Failed to list Appwrite identities for user %s", appwrite_user_id)
        return []


def _discord_identity_from_appwrite(*appwrite_user_ids):
    """Resolve Discord providerUid/access token from Appwrite user identities."""
    seen = set()
    for raw_user_id in appwrite_user_ids:
        appwrite_user_id = str(raw_user_id or "").strip()
        if not appwrite_user_id or appwrite_user_id in seen:
            continue
        seen.add(appwrite_user_id)
        for identity in _identities_for_appwrite_user(appwrite_user_id):
            provider = str(identity.get("provider") or "").strip().lower()
            if provider != "discord":
                continue
            provider_uid = str(
                _session_field(
                    identity,
                    "providerUid",
                    "provideruid",
                    "provider_uid",
                )
                or ""
            ).strip()
            if not provider_uid:
                continue
            return {
                "id": provider_uid,
                "username": identity.get("providerEmail") or identity.get("provideremail"),
                "access_token": _session_field(
                    identity,
                    "providerAccessToken",
                    "provideraccesstoken",
                    "provider_access_token",
                ),
            }
    return {}


def _resolve_discord_link_identity(
    *,
    provider_uid=None,
    provider_access_token=None,
    appwrite_user_ids=(),
):
    """Best-effort Discord identity resolution for link/login flows."""
    provider_uid = str(provider_uid or "").strip()
    profile = (
        _fetch_provider_profile("discord", provider_access_token)
        if provider_access_token
        else {}
    )
    appwrite_identity = _discord_identity_from_appwrite(*appwrite_user_ids)

    access_token = provider_access_token or appwrite_identity.get("access_token")
    if access_token and not profile.get("id"):
        profile = _fetch_provider_profile("discord", access_token) or profile

    discord_id = (
        provider_uid
        or str(profile.get("id") or "").strip()
        or str(appwrite_identity.get("id") or "").strip()
    )
    username = (
        profile.get("username")
        or profile.get("name")
        or appwrite_identity.get("username")
    )
    return {
        "id": discord_id or None,
        "username": username,
        "has_provider_uid": bool(provider_uid),
        "has_access_token": bool(provider_access_token),
        "has_appwrite_identity": bool(appwrite_identity.get("id")),
    }


def _discord_avatar_url(profile):
    user_id = profile.get("id") or profile.get("$id")
    avatar_hash = profile.get("avatar")
    if not user_id or not avatar_hash:
        return None
    extension = "gif" if avatar_hash.startswith("a_") else "png"
    return f"https://cdn.discordapp.com/avatars/{user_id}/{avatar_hash}.{extension}?size=256"


def _fetch_provider_identity(provider, access_token):
    if not provider or not access_token:
        return {}

    provider_key = provider.lower()
    try:
        if provider_key == "google":
            response = http_requests.get(
                "https://www.googleapis.com/oauth2/v2/userinfo",
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=8,
            )
            if response.status_code == 200:
                data = response.json()
                if data.get("verified_email") is False:
                    logger.warning("Google token email is not verified")
                    return {}
                return {
                    "id": data.get("id"),
                    "email": data.get("email"),
                    "name": data.get("name"),
                    "avatar_url": data.get("picture"),
                }
            logger.warning("Google identity fetch failed: %s", response.status_code)
            return {}

        if provider_key == "github":
            response = http_requests.get(
                "https://api.github.com/user",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/vnd.github+json",
                },
                timeout=8,
            )
            if response.status_code != 200:
                logger.warning("GitHub identity fetch failed: %s", response.status_code)
                return {}

            data = response.json()
            email = data.get("email")
            if not email:
                emails_response = http_requests.get(
                    "https://api.github.com/user/emails",
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Accept": "application/vnd.github+json",
                    },
                    timeout=8,
                )
                if emails_response.status_code == 200:
                    emails = emails_response.json()
                    primary_email = next(
                        (
                            item.get("email")
                            for item in emails
                            if item.get("primary") and item.get("verified")
                        ),
                        None,
                    )
                    email = primary_email

            return {
                "id": data.get("id"),
                "email": email,
                "name": data.get("name") or data.get("login"),
                "avatar_url": data.get("avatar_url"),
            }

        if provider_key == "discord":
            response = http_requests.get(
                "https://discord.com/api/users/@me",
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=8,
            )
            if response.status_code == 200:
                data = response.json()
                if data.get("verified") is False:
                    logger.warning("Discord token email is not verified")
                    return {}
                return {
                    "id": data.get("id"),
                    "email": data.get("email"),
                    "name": data.get("global_name") or data.get("username"),
                    "username": data.get("username") or data.get("global_name"),
                    "avatar_url": _discord_avatar_url(data),
                }
            logger.warning("Discord identity fetch failed: %s", response.status_code)
            return {}
    except Exception:
        logger.exception("Failed to fetch provider identity: %s", provider)

    return {}


def _format_member_since(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value.strftime("%b %d, %Y")
    text = value[:-1] + "+00:00" if isinstance(value, str) and value.endswith("Z") else value
    try:
        return datetime.fromisoformat(text).strftime("%b %d, %Y")
    except (TypeError, ValueError):
        return str(value)


def _normalize_username(value):
    if not value:
        return ""
    normalized = str(value).strip().lower()
    if not USERNAME_PATTERN.fullmatch(normalized):
        return ""
    if len(normalized) < USERNAME_MIN_LENGTH or len(normalized) > USERNAME_MAX_LENGTH:
        return ""
    return normalized


def _public_profile_payload(user_doc):
    user_id = user_doc.get("$id") or user_doc.get("id")
    name = user_doc.get("name") or "APStudy User"
    username = user_doc.get("username")
    tier = normalize_tier(user_doc.get("tier"))
    return {
        "id": user_id,
        "name": name,
        "username": username,
        "handle": _profile_handle(name, user_id, username),
        "picture_url": user_doc.get("picture_url"),
        "banner_color": _normalize_banner_color(user_doc.get("banner_color")),
        "school": user_doc.get("school"),
        "major": user_doc.get("major"),
        "graduation_year": user_doc.get("graduation_year"),
        "education_level": user_doc.get("education_level"),
        "class_year": user_doc.get("class_year"),
        "member_since": _format_member_since(user_doc.get("created_at")),
        "is_emory_school": _is_emory_school(user_doc.get("school")),
        "is_early_member": _is_early_member(user_doc.get("created_at")),
        "tier": tier,
        "tier_label": TIER_LABELS[tier],
        "tier_badge": TIER_BADGES.get(tier),
    }


def _fetch_provider_profile(provider, access_token):
    identity = _fetch_provider_identity(provider, access_token)
    return {
        "id": identity.get("id"),
        "name": identity.get("name"),
        "username": identity.get("username"),
        "avatar_url": identity.get("avatar_url"),
    } if identity else {}


def _clean_avatar_url(value):
    text = str(value or "").strip()
    if not text or text == DEFAULT_AVATAR_URL:
        return None
    return text


def _provider_access_token_from_identities(appwrite_user_id, provider=None):
    """Best-effort provider token lookup from linked Appwrite identities."""
    provider_key = str(provider or "").strip().lower()
    for identity in _identities_for_appwrite_user(appwrite_user_id):
        identity_provider = str(identity.get("provider") or "").strip().lower()
        if provider_key and identity_provider and identity_provider != provider_key:
            continue
        token = _session_field(
            identity,
            "providerAccessToken",
            "provideraccesstoken",
            "provider_access_token",
        )
        if token:
            return {
                "provider_access_token": token,
                "provider_uid": _session_field(
                    identity,
                    "providerUid",
                    "provideruid",
                    "provider_uid",
                ),
                "provider": identity_provider or provider_key or None,
            }
    return {}


def _sanitize_avatar_log_url(url):
    return _sanitize_log_text(url, limit=120)


def _log_avatar_collection(
    *,
    user_id,
    provider,
    page_context,
    created_user,
    has_provider_token,
    provider_profile_avatar,
    remote_avatar_candidate,
    resolved_avatar_url,
    storage_result,
):
    logger.info(
        "Avatar collection: user_id=%s provider=%s page_context=%s created_user=%s "
        "has_provider_token=%s profile_avatar=%s remote_avatar=%s resolved=%s storage=%s",
        user_id,
        provider,
        page_context,
        created_user,
        has_provider_token,
        bool(provider_profile_avatar),
        bool(remote_avatar_candidate),
        bool(resolved_avatar_url),
        storage_result,
    )


def _store_provider_avatar(user_id, source_url, *, page_context="auth"):
    """Copy a provider avatar into storage; keep the source URL when copy fails."""
    clean_url = _clean_avatar_url(source_url)
    if not clean_url:
        return None, None, "missing_source_url", 0

    stored_avatar = store_avatar_from_url(user_id, clean_url)
    if stored_avatar:
        return stored_avatar["view_url"], stored_avatar["file_id"], "stored", stored_avatar.get("size_bytes", 0)

    logger.warning(
        "Provider avatar storage copy failed; keeping provider URL: user_id=%s page_context=%s source=%s",
        user_id,
        page_context,
        _sanitize_avatar_log_url(clean_url),
    )
    return clean_url, None, "provider_url_fallback", 0


def _provider_avatar_url(provider_profile, remote_user, provider=None):
    remote_user = remote_user or {}
    provider_key = str(provider or "").strip().lower()
    prefs = remote_user.get("prefs") if isinstance(remote_user.get("prefs"), dict) else {}
    if provider_key == "discord":
        url = _clean_avatar_url(_discord_avatar_url(remote_user))
        if url:
            return url

    candidates = (
        (provider_profile or {}).get("avatar_url"),
        remote_user.get("avatar_url"),
        remote_user.get("photoUrl"),
        remote_user.get("photo_url"),
        remote_user.get("picture"),
        remote_user.get("picture_url"),
        None if provider_key == "discord" else remote_user.get("avatar"),
        prefs.get("avatar_url"),
        prefs.get("photoUrl"),
        prefs.get("photo_url"),
        prefs.get("picture"),
        prefs.get("picture_url"),
        None if provider_key == "discord" else prefs.get("avatar"),
    )
    for candidate in candidates:
        url = _clean_avatar_url(candidate)
        if url:
            return url
    return None


def _avatar_can_use_provider(user_doc):
    if not user_doc:
        return True
    avatar_source = str(user_doc.get("avatar_source") or "").strip().lower()
    if avatar_source == "provider":
        return True
    return _clean_avatar_url(user_doc.get("picture_url")) is None



def _find_user_by_email(email):
    if not email:
        return None
    response = list_rows_safe(
        COLLECTIONS["users"],
        [Query.equal("email", [email]), Query.limit(1)],
    )
    rows = response.get("rows", [])
    return rows[0] if rows else None


def _auth_error_title(error_code):
    return {
        AUTH_ERROR_OAUTH_START_SCOPE: "OAuth Login Error: Missing Appwrite Scope",
        AUTH_ERROR_OAUTH_START: "OAuth Login Error: Start Failed",
        AUTH_ERROR_OAUTH_STATE: "OAuth Login Error: Invalid State",
        AUTH_ERROR_OAUTH_CREDENTIALS: "OAuth Login Error: Missing Credentials",
        AUTH_ERROR_OAUTH_CALLBACK: "OAuth Login Error: Callback Failed",
        AUTH_ERROR_OAUTH_PROVIDER: "OAuth Login Error: Provider Failed",
    }.get(error_code, "OAuth Login Error")


def _login_error_metadata(error_code, metadata=None):
    details = {
        "error_code": error_code,
        "path": request.path,
        "method": request.method,
        "remote_addr": request.headers.get("X-Forwarded-For", request.remote_addr or ""),
        "user_agent": _sanitize_log_text(request.headers.get("User-Agent", ""), limit=180),
    }
    details.update(metadata or {})
    return {key: value for key, value in details.items() if value not in (None, "")}


def _emit_login_error(error_code, metadata=None):
    try:
        emit_server_log_event(
            _auth_error_title(error_code),
            actor="System",
            target="OAuth sign-in",
            metadata=_login_error_metadata(error_code, metadata),
            color="red",
        )
    except Exception:
        logger.exception("Failed to emit OAuth login error to Discord server log")


def _login_error_redirect(error_code, metadata=None):
    session[AUTH_ERROR_SESSION_KEY] = error_code
    _emit_login_error(error_code, metadata)
    return redirect(url_for("auth.login"))


def _login_error_text(error_code):
    if not error_code:
        return None
    return f"{AUTH_ERROR_MESSAGE} Error code: {error_code}"


def _clear_appwrite_oauth_state():
    session.pop(APPWRITE_OAUTH_STATE_KEY, None)
    session.pop(APPWRITE_OAUTH_PROVIDER_KEY, None)
    session.pop(APPWRITE_OAUTH_LINK_MODE_KEY, None)


def _is_safe_login_next_url(next_url):
    if not next_url or not isinstance(next_url, str):
        return False
    if not next_url.startswith("/"):
        return False
    if next_url.startswith("//"):
        return False
    path = next_url.split("?", 1)[0]
    if path == "/api" or "/api/" in path:
        return False
    return True


def _store_login_next_url(next_url):
    if _is_safe_login_next_url(next_url):
        session[LOGIN_NEXT_SESSION_KEY] = next_url


def _redirect_after_login(user_doc):
    next_url = session.pop(LOGIN_NEXT_SESSION_KEY, None)
    if user_doc.get("onboarding_complete"):
        if _is_safe_login_next_url(next_url):
            return next_url
        return url_for("dashboard.dashboard")
    return url_for("settings.onboarding")


def _redirect_for_user_doc(user_doc):
    return _redirect_after_login(user_doc)


def _complete_appwrite_login(
    remote_user,
    provider="appwrite",
    email=None,
    provider_access_token=None,
    provider_uid=None,
    page_context="auth/session",
):
    remote_user = remote_user or {}
    remote_user_id = remote_user.get("$id") or remote_user.get("id")
    remote_email = remote_user.get("email") or ""
    if not remote_user_id:
        raise ValueError("Invalid Appwrite user.")
    if not email:
        email = remote_email

    appwrite_user_id = str(remote_user_id)
    user_doc = get_row_safe(COLLECTIONS["users"], appwrite_user_id, allow_missing=True)
    if not user_doc and email:
        user_doc = _find_user_by_email(email)
    created_user = False

    if not provider_access_token:
        identity_token = _provider_access_token_from_identities(appwrite_user_id, provider=provider)
        provider_access_token = identity_token.get("provider_access_token") or provider_access_token
        if not provider_uid:
            provider_uid = identity_token.get("provider_uid")
        if identity_token.get("provider"):
            provider = identity_token["provider"]

    provider_profile = _fetch_provider_profile(provider, provider_access_token)
    provider_name = provider_profile.get("name")
    provider_avatar_url = _provider_avatar_url(provider_profile, remote_user, provider=provider)
    remote_avatar_candidate = _provider_avatar_url({}, remote_user, provider=provider)
    _log_avatar_collection(
        user_id=appwrite_user_id,
        provider=provider,
        page_context=page_context,
        created_user=not bool(user_doc),
        has_provider_token=bool(provider_access_token),
        provider_profile_avatar=provider_profile.get("avatar_url"),
        remote_avatar_candidate=remote_avatar_candidate,
        resolved_avatar_url=provider_avatar_url,
        storage_result="pending",
    )

    discord_id_value = None
    discord_username_value = None
    if provider == "discord":
        discord_identity = _resolve_discord_link_identity(
            provider_uid=provider_uid,
            provider_access_token=provider_access_token,
            appwrite_user_ids=[appwrite_user_id],
        )
        discord_id_value = discord_identity.get("id")
        discord_username_value = discord_identity.get("username")

    name = provider_name or remote_user.get("name") or remote_user.get("displayName")
    picture_url = provider_avatar_url

    if not user_doc:
        created_at = format_datetime(datetime.utcnow())
        avatar_file_id = None
        avatar_file_size_bytes = 0
        storage_result = "none"
        if picture_url:
            picture_url, avatar_file_id, storage_result, avatar_file_size_bytes = _store_provider_avatar(
                appwrite_user_id,
                picture_url,
                page_context=page_context,
            )
        row_data = {
            "google_id": appwrite_user_id,
            "email": email,
            "name": name or remote_user.get("name"),
            "picture_url": picture_url,
            "avatar_file_id": avatar_file_id,
            "avatar_file_size_bytes": avatar_file_size_bytes,
            "tier": "free",
            "banner_color": "#fecae1",
            "avatar_source": "provider" if picture_url else None,
            "school": None,
            "major": None,
            "graduation_year": None,
            "onboarding_complete": False,
            "onboarding_step": 1,
            "created_at": created_at,
            "last_login": created_at,
        }
        if provider and provider != "appwrite":
            row_data["provider"] = provider
        if discord_id_value:
            row_data["discord_id"] = discord_id_value
            row_data["discord_username"] = discord_username_value
            row_data["discord_linked_at"] = created_at
        user_doc = create_row_safe(
            COLLECTIONS["users"],
            row_id=appwrite_user_id,
            data=row_data,
        )
        created_user = True

        create_row_safe(
            COLLECTIONS["user_settings"],
            row_id=appwrite_user_id,
            data={
                "user_id": appwrite_user_id,
                "ics_secret_token": secrets.token_urlsafe(32),
                "feed_refresh_minutes": 15,
                "preferred_calendar_view": "week",
                "interface_theme": "obsidian-dark",
                "theme": "dark",
                "sidebar_default": "expanded",
                "email_notifications": True,
                "product_updates": True,
                "task_sound_enabled": True,
                "chat_sound_enabled": True,
                "language": "en",
                "timezone": "",
                "created_at": created_at,
            },
        )
        try:
            invites.attribute_signup(
                request.cookies.get(INVITE_COOKIE),
                appwrite_user_id,
            )
        except Exception:
            logger.exception("Failed to attribute new user signup to invite")
    else:
        updates = {"last_login": format_datetime(datetime.utcnow())}
        if name:
            updates["name"] = name
        if picture_url and _avatar_can_use_provider(user_doc):
            stored_picture_url, stored_file_id, storage_result, stored_file_size_bytes = _store_provider_avatar(
                appwrite_user_id,
                picture_url,
                page_context=page_context,
            )
            previous_file_id = user_doc.get("avatar_file_id")
            updates["picture_url"] = stored_picture_url
            updates["avatar_source"] = "provider"
            updates["avatar_file_size_bytes"] = stored_file_size_bytes
            avatar_file_id = None
            if stored_file_id:
                avatar_file_id = stored_file_id
                if previous_file_id and previous_file_id != stored_file_id:
                    delete_avatar_file(previous_file_id)
            elif previous_file_id and storage_result == "provider_url_fallback":
                delete_avatar_file(previous_file_id)
            if stored_file_id or (previous_file_id and storage_result == "provider_url_fallback"):
                updates["avatar_file_id"] = avatar_file_id
        if email:
            updates["email"] = email
        if provider and provider != "appwrite":
            updates["provider"] = provider
        if discord_id_value:
            updates["discord_id"] = discord_id_value
            updates["discord_username"] = discord_username_value
            if not user_doc.get("discord_id"):
                updates["discord_linked_at"] = format_datetime(datetime.utcnow())

        row_id = user_doc.get("$id") or user_doc.get("id")
        if not row_id:
            raise ValueError("User lookup failed.")
        user_doc = update_row_safe(
            COLLECTIONS["users"],
            row_id,
            updates,
        )

    sync_chat_presence_labels_for_user(user_doc.get("$id") or user_doc.get("id"), user_doc)
    session.permanent = True
    login_user(
        user_from_doc(user_doc),
        remember=True,
        duration=current_app.config.get("AUTH_SESSION_DURATION", AUTH_SESSION_DURATION),
    )
    session["user_id"] = user_doc.get("$id") or user_doc.get("id")
    session["email"] = email or remote_email
    _set_oauth_session(provider, appwrite_user_id, email, name=name, picture_url=picture_url)
    if email or remote_email:
        try:
            notes_collaboration.claim_pending_invitations(session["user_id"], email or remote_email)
        except Exception:
            logger.exception("Failed to claim pending note invitations for %s", session["user_id"])

    if discord_id_value:
        try:
            discord_bridge.add_guild_member_role(discord_id_value)
        except Exception:
            logger.exception("Failed to grant Discord role on login for %s", discord_id_value)

    if created_user:
        emit_user_event(
            "New User Created",
            actor=format_actor(user_id=user_doc.get("$id") or user_doc.get("id"), username=user_doc.get("username") or user_doc.get("name")),
            target=format_user_target(user_doc),
            metadata={
                "page_context": page_context,
                "resource_type": "user",
                "resource_id": user_doc.get("$id") or user_doc.get("id"),
                "provider": provider,
                "email": email or remote_email,
                "default_settings_created": True,
            },
            color="green",
        )

    emit_user_event(
        "User Login",
        actor=format_actor(user_id=user_doc.get("$id") or user_doc.get("id"), username=user_doc.get("username") or user_doc.get("name")),
        target=format_user_target(user_doc),
        metadata={
            "page_context": page_context,
            "resource_type": "user",
            "resource_id": user_doc.get("$id") or user_doc.get("id"),
            "provider": provider,
            "created_user": created_user,
        },
        color="green",
    )

    return {
        "created_user": created_user,
        "email": email or remote_email,
        "redirect": _redirect_for_user_doc(user_doc),
        "user_doc": user_doc,
        "user_id": session["user_id"],
    }


@auth_bp.route("/")
def index():
    """Render the public landing page."""
    response = make_response(render_template(
        "landing.html",
        landing_user_authenticated=current_user.is_authenticated,
    ))
    response.headers["Content-Security-Policy"] = LANDING_CSP
    return response


@auth_bp.route("/login")
def login():
    """Render the sign-in page."""
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.dashboard"))
    next_url = request.args.get("next")
    if next_url:
        _store_login_next_url(next_url)
    error = _login_error_text(session.pop(AUTH_ERROR_SESSION_KEY, None))
    response = make_response(render_template("login.html", error=error))
    response.headers["Content-Security-Policy"] = LOGIN_CSP
    return response


@auth_bp.route("/auth/csrf")
def refresh_csrf_token():
    """Issue a fresh CSRF token without forcing a long-open page to reload."""
    response = jsonify({"ok": True})
    response.headers["Cache-Control"] = "no-store"
    response.set_cookie(
        "csrf_token",
        generate_csrf(),
        secure=current_app.config["SESSION_COOKIE_SECURE"],
        httponly=False,
        samesite="Lax",
    )
    return response


@auth_bp.route("/join")
@auth_bp.route("/sign-up")
@auth_bp.route("/welcome")
@auth_bp.route("/access")
def auth_entry_redirect():
    """Legacy entry URLs redirect to the sign-in page."""
    return redirect(url_for("auth.login"))


@auth_bp.route("/join/<code>")
def join_with_invite(code):
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.dashboard"))

    response = redirect(url_for("auth.index"))
    normalized = invites.normalize_code(code)
    if normalized and invites.invite_by_code(normalized):
        response.set_cookie(
            INVITE_COOKIE,
            normalized,
            max_age=INVITE_COOKIE_MAX_AGE,
            httponly=True,
            secure=current_app.config["SESSION_COOKIE_SECURE"],
            samesite="Lax",
        )
    return response


@auth_bp.route("/auth/appwrite/<provider>")
def appwrite_oauth_start(provider):
    """Start an Appwrite OAuth token flow from the server."""
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.dashboard"))

    provider_key = str(provider or "").strip().lower()
    if provider_key not in APPWRITE_OAUTH_PROVIDERS:
        abort(404)

    state = secrets.token_urlsafe(32)
    session[APPWRITE_OAUTH_STATE_KEY] = state
    session[APPWRITE_OAUTH_PROVIDER_KEY] = provider_key
    success_url = url_for("auth.appwrite_oauth_callback", state=state, _external=True)
    failure_url = _appwrite_oauth_failure_url(state)

    try:
        redirect_url = _appwrite_oauth_redirect_url(provider_key, success_url, failure_url)
    except Exception as exc:
        details = _log_oauth_exception(
            "Failed to start Appwrite OAuth flow: provider=%s success_scheme=%s",
            exc,
            provider_key,
            request.scheme,
        )
        error_code = _oauth_start_error_code(details)
        _clear_appwrite_oauth_state()
        return _login_error_redirect(error_code, {
            "provider": provider_key,
            "request_scheme": request.scheme,
            "success_url_scheme": success_url.split(":", 1)[0],
            "failure_url_scheme": failure_url.split(":", 1)[0],
            "appwrite_error": details,
        })

    return redirect(redirect_url)


@auth_bp.route("/auth/appwrite/failure/<state>")
def appwrite_oauth_failure(state):
    """Handle Appwrite/provider OAuth failure redirects without exposing query flags."""
    expected_state = session.get(APPWRITE_OAUTH_STATE_KEY)
    provider = session.get(APPWRITE_OAUTH_PROVIDER_KEY) or "appwrite"
    link_mode = bool(session.get(APPWRITE_OAUTH_LINK_MODE_KEY))
    if not expected_state or state != expected_state:
        logger.warning("Rejected Appwrite OAuth failure redirect with invalid state")
        _clear_appwrite_oauth_state()
        if link_mode:
            return redirect(url_for("settings.settings_page") + "?discord=error#account")
        return _login_error_redirect(AUTH_ERROR_OAUTH_STATE, {
            "provider": provider,
            "failure_phase": "provider_failure_redirect",
        })

    logger.warning("Appwrite OAuth provider flow failed: provider=%s link_mode=%s", provider, link_mode)
    _clear_appwrite_oauth_state()
    if link_mode:
        return redirect(url_for("settings.settings_page") + "?discord=error#account")
    return _login_error_redirect(AUTH_ERROR_OAUTH_PROVIDER, {
        "provider": provider,
        "failure_phase": "provider_failure_redirect",
    })


@auth_bp.route("/auth/appwrite/callback/<state>")
def appwrite_oauth_callback(state):
    """Complete Appwrite OAuth without relying on client-side Appwrite cookies."""
    expected_state = session.get(APPWRITE_OAUTH_STATE_KEY)
    provider = session.get(APPWRITE_OAUTH_PROVIDER_KEY) or "appwrite"
    if not expected_state or state != expected_state:
        logger.warning("Rejected Appwrite OAuth callback with invalid state")
        _clear_appwrite_oauth_state()
        return _login_error_redirect(AUTH_ERROR_OAUTH_STATE, {
            "provider": provider,
            "failure_phase": "callback",
        })

    user_id = request.args.get("userId") or request.args.get("user_id")
    secret = request.args.get("secret")
    if not user_id or not secret:
        logger.warning("Rejected Appwrite OAuth callback with missing credentials")
        _clear_appwrite_oauth_state()
        return _login_error_redirect(AUTH_ERROR_OAUTH_CREDENTIALS, {
            "provider": provider,
            "failure_phase": "callback",
            "has_user_id": bool(user_id),
            "has_secret": bool(secret),
        })

    try:
        appwrite_session = _account_to_dict(Account(appwrite_client).create_session(user_id, secret))
        provider = _session_field(appwrite_session, "provider") or provider
        provider_access_token = _session_field(
            appwrite_session,
            "providerAccessToken",
            "provideraccesstoken",
            "provider_access_token",
        )
        provider_uid = _session_field(
            appwrite_session,
            "providerUid",
            "provideruid",
            "provider_uid",
        )
        remote_user = _account_from_user_id(user_id)
        result = _complete_appwrite_login(
            remote_user,
            provider=provider,
            provider_access_token=provider_access_token,
            provider_uid=provider_uid,
            page_context="auth/appwrite/callback",
        )
    except Exception as exc:
        details = _log_oauth_exception(
            "Failed to complete Appwrite OAuth callback: provider=%s",
            exc,
            provider,
        )
        _clear_appwrite_oauth_state()
        return _login_error_redirect(AUTH_ERROR_OAUTH_CALLBACK, {
            "provider": provider,
            "failure_phase": "callback",
            "appwrite_error": details,
        })

    _clear_appwrite_oauth_state()
    response = redirect(result["redirect"])
    response.delete_cookie(INVITE_COOKIE, path="/")
    return response


@auth_bp.route("/auth/appwrite/discord/link")
@login_required
def appwrite_discord_link_start():
    """Start a Discord OAuth flow to link an account while staying logged in."""
    state = secrets.token_urlsafe(32)
    session[APPWRITE_OAUTH_STATE_KEY] = state
    session[APPWRITE_OAUTH_PROVIDER_KEY] = "discord"
    session[APPWRITE_OAUTH_LINK_MODE_KEY] = True
    success_url = url_for("auth.appwrite_discord_link_callback", state=state, _external=True)
    failure_url = _appwrite_oauth_failure_url(state)

    try:
        redirect_url = _appwrite_oauth_redirect_url("discord", success_url, failure_url)
    except Exception as exc:
        _log_oauth_exception(
            "Failed to start Discord link flow: success_scheme=%s",
            exc,
            request.scheme,
        )
        _clear_appwrite_oauth_state()
        return redirect(url_for("settings.settings_page") + "?discord=error#account")

    return redirect(redirect_url)


@auth_bp.route("/auth/appwrite/discord/link/callback/<state>")
@login_required
def appwrite_discord_link_callback(state):
    """Complete a Discord link flow and attach the identity to the current user."""
    settings_redirect = url_for("settings.settings_page") + "#account"
    expected_state = session.get(APPWRITE_OAUTH_STATE_KEY)
    link_mode = session.get(APPWRITE_OAUTH_LINK_MODE_KEY)
    if not expected_state or state != expected_state or not link_mode:
        logger.warning("Rejected Discord link callback with invalid state")
        _clear_appwrite_oauth_state()
        return redirect(url_for("settings.settings_page") + "?discord=error#account")

    user_id = request.args.get("userId") or request.args.get("user_id")
    secret = request.args.get("secret")
    if not user_id or not secret:
        logger.warning("Rejected Discord link callback with missing credentials")
        _clear_appwrite_oauth_state()
        return redirect(url_for("settings.settings_page") + "?discord=error#account")

    linked = False
    try:
        appwrite_session = _account_to_dict(Account(appwrite_client).create_session(user_id, secret))
        discord_identity = _resolve_discord_link_identity(
            provider_uid=_session_field(
                appwrite_session,
                "providerUid",
                "provideruid",
                "provider_uid",
            ),
            provider_access_token=_session_field(
                appwrite_session,
                "providerAccessToken",
                "provideraccesstoken",
                "provider_access_token",
            ),
            appwrite_user_ids=[user_id, str(current_user.id)],
        )
        discord_id_value = discord_identity.get("id")
        discord_username_value = discord_identity.get("username")

        if not discord_id_value:
            logger.warning(
                "Discord link failed to resolve a Discord ID: has_provider_uid=%s has_access_token=%s has_appwrite_identity=%s callback_user_id=%s current_user_id=%s",
                discord_identity.get("has_provider_uid"),
                discord_identity.get("has_access_token"),
                discord_identity.get("has_appwrite_identity"),
                user_id,
                current_user.id,
            )
        else:
            if not discord_username_value:
                # No username from the OAuth profile (e.g. providerUid-only):
                # resolve it through the bot so the UI can show a real handle.
                try:
                    discord_user = discord_bridge.fetch_discord_user(discord_id_value)
                    if discord_user:
                        discord_username_value = (
                            discord_user.get("username") or discord_user.get("global_name")
                        )
                except Exception:
                    logger.exception("Failed to resolve Discord username for %s", discord_id_value)
            update_row_safe(
                COLLECTIONS["users"],
                str(current_user.id),
                {
                    "discord_id": discord_id_value,
                    "discord_username": discord_username_value,
                    "discord_linked_at": format_datetime(datetime.utcnow()),
                },
            )
            linked = True
            try:
                discord_bridge.add_guild_member_role(discord_id_value)
            except Exception:
                logger.exception("Failed to grant Discord role for %s", discord_id_value)
    except Exception as exc:
        _log_oauth_exception("Failed to complete Discord link callback", exc)

    _clear_appwrite_oauth_state()
    if not linked:
        return redirect(url_for("settings.settings_page") + "?discord=error#account")
    return redirect(settings_redirect)


@auth_bp.route("/user/<user_id>")
def public_user_profile(user_id):
    """Render a public profile card for a user."""
    from appwrite_client import COLLECTIONS

    try:
        user_doc = get_row_safe(COLLECTIONS["users"], user_id, allow_missing=True)
    except AppwriteException:
        logger.exception("Failed to load public user profile")
        abort(404)
    if not user_doc:
        abort(404)
    return _render_public_profile(user_doc)


@auth_bp.route("/u/<username>")
def public_user_profile_by_username(username):
    """Render a public profile card for a username."""
    normalized = _normalize_username(username)
    if not normalized:
        abort(404)

    from appwrite_client import COLLECTIONS
    try:
        user_doc = first_row(
            COLLECTIONS["users"],
            [Query.equal("username", [normalized])],
        )
    except AppwriteException:
        logger.exception("Failed to load public user profile by username")
        abort(404)
    if not user_doc:
        abort(404)

    return _render_public_profile(user_doc)


def _render_public_profile(user_doc):
    viewer = None
    theme_preference = "system-match"
    if current_user.is_authenticated:
        viewer = {
            "email": current_user.email,
            "picture": current_user.picture_url,
            "tier": normalize_tier(getattr(current_user, "tier", None)),
        }
        try:
            from appwrite_client import COLLECTIONS
            settings_response = list_rows_safe(
                COLLECTIONS["user_settings"],
                [Query.equal("user_id", [str(current_user.id)]), Query.limit(1)],
            )
            rows = settings_response.get("rows", [])
            if rows:
                theme_preference = rows[0].get("interface_theme") or theme_preference
        except AppwriteException:
            logger.exception("Failed to load viewer theme for public profile")

    response = make_response(render_template(
        "user_profile.html",
        profile=_public_profile_payload(user_doc),
        viewer=viewer,
        theme_preference=theme_preference,
    ))
    response.headers["Content-Security-Policy"] = PUBLIC_PROFILE_CSP
    return response


@auth_bp.route("/user/profile-banner/<color>.svg")
def profile_banner_svg(color):
    """Serve a CSP-safe solid banner image for public profile cards."""
    normalized_color = _normalize_banner_color(color)
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 360" '
        'role="img" aria-hidden="true">'
        f'<rect width="1200" height="360" fill="{normalized_color}"/>'
        '</svg>'
    )
    return Response(svg, mimetype="image/svg+xml")


@auth_bp.route("/auth/session", methods=["POST"])
def appwrite_session():
    payload = request.get_json(silent=True) or {}
    user_id = payload.get("user_id")
    email = payload.get("email")
    jwt = payload.get("jwt")
    provider = payload.get("provider") or "appwrite"
    provider_access_token = payload.get("provider_access_token") or payload.get("providerAccessToken")

    if not jwt and not provider_access_token:
        return jsonify({"error": "Missing Appwrite session proof."}), 401

    try:
        from appwrite_client import COLLECTIONS
        if jwt:
            remote_user = _account_from_jwt(jwt)
            provider_identity = {}
        else:
            provider_identity = _fetch_provider_identity(provider, provider_access_token)
            if not provider_identity:
                return jsonify({"error": "Invalid provider session."}), 401
            remote_user = _account_from_user_id(user_id)
    except Exception:
        logger.exception("Failed to verify Appwrite session")
        return jsonify({"error": "Invalid Appwrite user."}), 401

    remote_user_id = remote_user.get("$id") or remote_user.get("id")
    remote_email = remote_user.get("email") or ""
    if not remote_user_id:
        return jsonify({"error": "Invalid Appwrite user."}), 401
    provider_email = (provider_identity or {}).get("email") or ""
    if not jwt and not provider_email:
        logger.warning("Provider token did not expose an email during session exchange: %s", provider)
        return jsonify({"error": "Provider email is required."}), 401
    if provider_email and remote_email and provider_email.lower() != remote_email.lower():
        logger.warning("Provider/Appwrite email mismatch during session exchange: %s vs %s", provider_email, remote_email)
        return jsonify({"error": "Email mismatch."}), 401
    if user_id and str(user_id) != str(remote_user_id):
        logger.warning("User id mismatch during Appwrite session exchange: %s vs %s", user_id, remote_user_id)
        return jsonify({"error": "User mismatch."}), 401
    if email and remote_email and email.lower() != remote_email.lower():
        logger.warning("Email mismatch during Appwrite session exchange: %s vs %s", email, remote_email)
        return jsonify({"error": "Email mismatch."}), 401

    if not email:
        email = remote_email

    try:
        result = _complete_appwrite_login(
            remote_user,
            provider=provider,
            email=email,
            provider_access_token=provider_access_token,
            page_context="auth/session",
        )
    except ValueError as exc:
        logger.warning("Unable to complete Appwrite session exchange: %s", exc)
        return jsonify({"error": str(exc)}), 500
    except AppwriteException:
        logger.exception("Failed to complete Appwrite session exchange")
        return jsonify({"error": "Unable to complete session exchange."}), 500

    response = jsonify(
        {
            "status": "ok",
            "user_id": result["user_id"],
            "redirect": result["redirect"],
        }
    )
    response.delete_cookie(INVITE_COOKIE, path="/")
    return response


@auth_bp.route("/logout", methods=["POST"])
def logout():
    """Clear session and revoke Google token if possible."""
    credentials_data = session.get("credentials")
    user_id = session.get("oauth_user_id") or session.get("user_id")
    if not user_id and current_user.is_authenticated:
        user_id = str(current_user.id)
    if current_user.is_authenticated:
        emit_user_event(
            "User Logout",
            actor=format_actor(current_user),
            target=format_user_target(user_id=str(current_user.id), username=current_user.username or current_user.name),
            metadata={
                "page_context": "auth/logout",
                "resource_type": "user",
                "resource_id": str(current_user.id),
                "provider": session.get("oauth_provider") or "appwrite",
                "oauth_user_id": session.get("oauth_user_id"),
            },
            color="gray",
        )
    if user_id:
        try:
            from appwrite_client import client as appwrite_client
            users_service = Users(appwrite_client)
            users_service.delete_sessions(str(user_id))
        except Exception:
            logger.exception("Failed to revoke Appwrite sessions for user")
    logout_user()
    remember_clear = session.get("_remember") == "clear"
    session.clear()
    if remember_clear:
        session["_remember"] = "clear"

    if credentials_data and credentials_data.get("token"):
        http_requests.post(
            "https://oauth2.googleapis.com/revoke",
            params={"token": credentials_data["token"]},
            headers={"content-type": "application/x-www-form-urlencoded"},
        )
    return redirect(url_for("auth.login"))
