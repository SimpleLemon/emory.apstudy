"""
Inbound service webhooks.
"""

import hashlib
import hmac

from flask import Blueprint, jsonify, request

from services.discord_audit import emit_server_log_event
from services.environment_config import runtime_environment_config


webhooks_bp = Blueprint("webhooks", __name__)


def _github_webhook_secret():
    return (runtime_environment_config().github_webhook_secret or "").strip()


def _github_unsigned_allowed():
    configured = runtime_environment_config()
    return configured.github_webhook_allow_unsigned or configured.flask_debug_raw == "1"


def _valid_github_signature(payload, signature_header, secret):
    if not signature_header or not secret:
        return False
    scheme, _, provided = signature_header.partition("=")
    if scheme != "sha256" or not provided:
        return False
    expected = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(provided, expected)


def _branch_from_ref(ref):
    ref = str(ref or "")
    if ref.startswith("refs/heads/"):
        return ref.removeprefix("refs/heads/")
    if ref.startswith("refs/tags/"):
        return ref.removeprefix("refs/tags/")
    return ref or "unknown"


def _commit_author(commit):
    author = commit.get("author") or {}
    name = author.get("name") or author.get("username")
    email = author.get("email")
    if name and email:
        return f"{name} <{email}>"
    return name or email or "Unknown"


def _commit_title(message):
    return str(message or "No commit message").splitlines()[0][:180]


def _emit_commit_event(payload, commit):
    repository = payload.get("repository") or {}
    pusher = payload.get("pusher") or {}
    branch = _branch_from_ref(payload.get("ref"))
    commit_id = str(commit.get("id") or "")
    short_sha = commit_id[:7] if commit_id else "unknown"
    repo_name = repository.get("full_name") or repository.get("name") or "unknown repository"
    metadata = {
        "repository": repo_name,
        "branch": branch,
        "commit": short_sha,
        "message": _commit_title(commit.get("message")),
        "author": _commit_author(commit),
        "pusher": pusher.get("name") or pusher.get("email") or "Unknown",
        "url": commit.get("url"),
        "compare": payload.get("compare"),
    }
    return emit_server_log_event(
        "GitHub Commit Pushed",
        actor=metadata["pusher"],
        target=f"{repo_name}@{branch}",
        metadata=metadata,
        color="gray",
    )


@webhooks_bp.route("/webhooks/github", methods=["POST"])
def github_webhook():
    payload_bytes = request.get_data(cache=True)
    secret = _github_webhook_secret()
    if secret:
        signature = request.headers.get("X-Hub-Signature-256", "")
        if not _valid_github_signature(payload_bytes, signature, secret):
            return jsonify({"error": "invalid_signature"}), 401
    elif not _github_unsigned_allowed():
        return jsonify({"error": "webhook_secret_not_configured"}), 503

    if request.headers.get("X-GitHub-Event") != "push":
        return jsonify({"status": "ignored"}), 202

    payload = request.get_json(silent=True) or {}
    commits = payload.get("commits") or []
    sent = 0
    for commit in commits:
        if isinstance(commit, dict):
            sent += 1 if _emit_commit_event(payload, commit) else 0

    return jsonify({"status": "ok", "commits": len(commits), "sent": sent})
