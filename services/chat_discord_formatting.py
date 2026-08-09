"""Pure Discord media, identity, rendering, and message-payload helpers."""

import hashlib
import html
import json
import re


DISCORD_IMAGE_EXTENSIONS = {".gif", ".jpeg", ".jpg", ".png", ".webp"}
DISCORD_USER_MENTION_RE = re.compile(r"&lt;@!?(\d+)&gt;")
DISCORD_ROLE_MENTION_RE = re.compile(r"&lt;@(?:&amp;|&)(\d+)&gt;")
DISCORD_CUSTOM_EMOJI_RE = re.compile(r"&lt;(a?):([A-Za-z0-9_]{2,32}):(\d+)&gt;")


def discord_previews(message):
    previews = []
    for embed in message.get("embeds") or []:
        image = embed.get("image") or embed.get("thumbnail") or {}
        previews.append({
            "url": embed.get("url") or "",
            "title": embed.get("title") or "",
            "description": embed.get("description") or "",
            "image_url": image.get("url") or "",
            "site_name": (embed.get("provider") or {}).get("name") or "",
            "content_type": embed.get("type") or "",
        })
    return previews[:2]


def discord_images(message, *, attachment_is_image_fn):
    images = []
    for attachment in message.get("attachments") or []:
        if not attachment_is_image_fn(attachment):
            continue
        url = attachment.get("url") or attachment.get("proxy_url") or ""
        if not url:
            continue
        images.append({
            "kind": "discord_image",
            "url": url,
            "proxy_url": attachment.get("proxy_url") or "",
            "filename": attachment.get("filename") or "Image",
            "width": attachment.get("width"),
            "height": attachment.get("height"),
            "content_type": attachment.get("content_type") or "",
        })
    return images[:4]


def discord_attachment_is_image(attachment, *, image_extensions=DISCORD_IMAGE_EXTENSIONS):
    content_type = str(attachment.get("content_type") or "").split(";", 1)[0].strip().lower()
    if content_type.startswith("image/"):
        return True
    filename = str(attachment.get("filename") or "").lower()
    return any(filename.endswith(extension) for extension in image_extensions)


def discord_media_json(
    previews,
    images,
    *,
    bounded_string_fn,
    text_limit,
):
    media = list(previews or []) + list(images or [])
    compact_media = []
    for item in media:
        if not isinstance(item, dict):
            continue
        compact_media.append({
            key: bounded_string_fn(value, 2048) if isinstance(value, str) else value
            for key, value in item.items()
            if value not in (None, "")
        })

    while compact_media:
        text = json.dumps(compact_media, separators=(",", ":"))
        if len(text) <= text_limit:
            return text
        compact_media.pop()
    return "[]"


def discord_message_row_id(channel, discord_message_id):
    discord_channel_id = str(channel.get("discord_channel_id") or "")
    discord_id = str(discord_message_id or "")
    if not discord_channel_id or not discord_id:
        return None
    digest = hashlib.sha1(f"{discord_channel_id}:{discord_id}".encode("utf-8")).hexdigest()[:24]
    return f"discord_{digest}"


def discord_message_external_id(channel, discord_message_id):
    discord_channel_id = str(channel.get("discord_channel_id") or "")
    discord_id = str(discord_message_id or "")
    if not discord_channel_id or not discord_id:
        return None
    return f"discord:{discord_channel_id}:{discord_id}"


def discord_avatar(author, *, default_avatar):
    avatar_hash = author.get("avatar")
    user_id = author.get("id")
    if avatar_hash and user_id:
        extension = "gif" if str(avatar_hash).startswith("a_") else "png"
        return f"https://cdn.discordapp.com/avatars/{user_id}/{avatar_hash}.{extension}?size=128"
    return default_avatar


def discord_mention_name(user):
    return (
        user.get("global_name")
        or user.get("nick")
        or user.get("username")
        or "Discord User"
    )


def discord_user_mentions(message, *, mention_name_fn):
    mentions = {}
    for user in message.get("mentions") or []:
        user_id = str(user.get("id") or "")
        if user_id:
            mentions[user_id] = mention_name_fn(user)
    return mentions


def discord_user_mention_label(
    user_id,
    mentions,
    *,
    fetch_user_fn,
    mention_name_fn,
):
    label = mentions.get(user_id)
    if label:
        return label
    fetched = fetch_user_fn(user_id)
    if fetched:
        return mention_name_fn(fetched)
    return "Discord User"


def discord_role_mentions(*, fetch_roles_fn):
    roles = {}
    for role in fetch_roles_fn():
        role_id = str(role.get("id") or "")
        if role_id:
            roles[role_id] = role.get("name") or "Unknown Role"
    return roles


def mention_span(label, class_name="chat-mention"):
    return f'<span class="{class_name}">{html.escape(label)}</span>'


def emoji_img(animated, name, emoji_id):
    extension = "gif" if animated else "png"
    url = f"https://cdn.discordapp.com/emojis/{emoji_id}.{extension}?size=48&quality=lossless"
    escaped_url = html.escape(url, quote=True)
    escaped_name = html.escape(name)
    return (
        f'<img class="chat-custom-emoji" '
        f'src="{escaped_url}" alt=":{escaped_name}:" title=":{escaped_name}:" '
        'loading="lazy" decoding="async">'
    )


def render_discord_content(
    content,
    message,
    *,
    render_markdown_fn,
    user_mentions_fn,
    role_mentions_fn,
    user_mention_label_fn,
    mention_span_fn,
    emoji_img_fn,
    role_mention_re=DISCORD_ROLE_MENTION_RE,
    user_mention_re=DISCORD_USER_MENTION_RE,
    custom_emoji_re=DISCORD_CUSTOM_EMOJI_RE,
):
    rendered = render_markdown_fn(content)
    user_mentions = user_mentions_fn(message)
    role_mentions = role_mentions_fn() if "&lt;@&" in rendered or "&lt;@&amp;" in rendered else {}

    def replace_user(match):
        label = user_mention_label_fn(match.group(1), user_mentions)
        return mention_span_fn(f"@{label}")

    def replace_role(match):
        label = role_mentions.get(match.group(1), "Unknown Role")
        return mention_span_fn(f"@{label}", "chat-mention chat-mention-role")

    rendered = role_mention_re.sub(replace_role, rendered)
    rendered = user_mention_re.sub(replace_user, rendered)
    return custom_emoji_re.sub(
        lambda match: emoji_img_fn(match.group(1), match.group(2), match.group(3)),
        rendered,
    )


def discord_message_payload(
    channel,
    message,
    *,
    partial=False,
    row_id_fn,
    external_id_fn,
    format_datetime_fn,
    now_fn,
    discord_avatar_fn,
    render_discord_content_fn,
    media_json_fn,
    previews_fn,
    images_fn,
    bounded_chat_message_value_fn,
):
    channel_id = row_id_fn(channel)
    discord_id = str(message.get("id") or "")
    if not discord_id:
        return None
    external_id = external_id_fn(channel, discord_id)
    author = message.get("author") or {}
    payload = {
        "channel_id": channel_id,
        "source": "discord",
        "external_id": external_id,
        "discord_message_id": discord_id,
        "updated_at": format_datetime_fn(now_fn()),
    }
    if author or not partial:
        payload.update({
            "author_name": author.get("global_name") or author.get("username") or "Discord User",
            "author_username": author.get("username") or "",
            "author_avatar_url": discord_avatar_fn(author),
        })
    if "content" in message or not partial:
        content = message.get("content") or ""
        payload.update({
            "content": content,
            "rendered_html": render_discord_content_fn(content, message),
        })
    if any(key in message for key in ("embeds", "attachments")) or not partial:
        payload["link_preview_json"] = media_json_fn(previews_fn(message), images_fn(message))
    if "webhook_id" in message or not partial:
        payload["discord_webhook_id"] = message.get("webhook_id")
    if "timestamp" in message or not partial:
        payload["created_at"] = format_datetime_fn(message.get("timestamp") or now_fn())
    return {
        key: bounded_chat_message_value_fn(key, value)
        for key, value in payload.items()
    }
