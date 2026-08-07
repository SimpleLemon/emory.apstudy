"""OAuth provider identity helpers."""

import logging

import requests as http_requests


# Preserve the existing auth-path logging namespace after extraction.
logger = logging.getLogger("blueprints.auth")


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
