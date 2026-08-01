"""Small server-side GIPHY resolver used to prevent arbitrary media URL injection."""

from __future__ import annotations

import json
import urllib.parse
import urllib.request

from services.environment_config import runtime_environment_config


API_ROOT = "https://api.giphy.com/v1/gifs"


class GiphyError(RuntimeError):
    pass


def api_key():
    return runtime_environment_config().giphy_api_key.strip()


def is_available():
    return bool(api_key())


def resolve_gif(gif_id, query=""):
    key = api_key()
    normalized_id = str(gif_id or "").strip()
    if not key:
        raise GiphyError("GIF sharing is not configured.")
    if not normalized_id or len(normalized_id) > 80 or not all(char.isalnum() or char in "-_" for char in normalized_id):
        raise GiphyError("That GIF is unavailable.")
    url = f"{API_ROOT}/{urllib.parse.quote(normalized_id)}?" + urllib.parse.urlencode({"api_key": key, "rating": "pg"})
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise GiphyError("Unable to load that GIF right now.") from exc
    data = payload.get("data") if isinstance(payload, dict) else None
    images = data.get("images") if isinstance(data, dict) else None
    original = images.get("original") if isinstance(images, dict) else None
    preview = images.get("fixed_width") if isinstance(images, dict) else None
    media_url = (original or {}).get("webp") or (original or {}).get("url")
    preview_url = (preview or {}).get("webp") or media_url
    parsed_media = urllib.parse.urlparse(str(media_url or ""))
    parsed_preview = urllib.parse.urlparse(str(preview_url or ""))
    if (
        parsed_media.scheme != "https"
        or not (parsed_media.hostname == "giphy.com" or str(parsed_media.hostname or "").endswith(".giphy.com"))
        or parsed_preview.scheme != "https"
        or not (parsed_preview.hostname == "giphy.com" or str(parsed_preview.hostname or "").endswith(".giphy.com"))
    ):
        raise GiphyError("That GIF is unavailable.")
    return {
        "kind": "giphy_gif",
        "id": normalized_id,
        "title": str(data.get("title") or "GIF")[:200],
        "url": media_url,
        "preview_url": preview_url,
        "width": int((original or {}).get("width") or 0),
        "height": int((original or {}).get("height") or 0),
        "query": str(query or "")[:120],
        "provider": "GIPHY",
    }
