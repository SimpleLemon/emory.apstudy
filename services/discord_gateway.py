import asyncio
import logging
import threading

from services.discord_chat import (
    delete_discord_gateway_message,
    delete_discord_gateway_messages,
    ingest_discord_gateway_message,
    sync_discord_channels,
)
from services.environment_config import runtime_environment_config

logger = logging.getLogger(__name__)
_bridge = None


class DiscordGatewayBridge:
    def __init__(self, app):
        self.app = app
        self.thread = None
        self.loop = None
        self.client = None
        self.started = False

    def start(self):
        if self.started:
            return True
        configured = runtime_environment_config()
        if configured.discord_gateway_enabled_raw == "0":
            logger.info("Discord Gateway listener disabled (DISCORD_GATEWAY_ENABLED=0).")
            return False
        if not (configured.discord_bot_token or "").strip():
            logger.info("Discord Gateway listener skipped; DISCORD_BOT_TOKEN is not configured.")
            return False
        self.thread = threading.Thread(target=self._run, name="discord-gateway-listener", daemon=True)
        self.thread.start()
        self.started = True
        return True

    def stop(self):
        client = self.client
        loop = self.loop
        if client and loop and loop.is_running():
            try:
                asyncio.run_coroutine_threadsafe(client.close(), loop)
            except Exception:
                logger.exception("Failed to stop Discord Gateway listener")
        self.started = False

    def _run(self):
        try:
            asyncio.run(self._run_client())
        except Exception:
            logger.exception("Discord Gateway listener stopped unexpectedly")
        finally:
            self.started = False
            self.client = None
            self.loop = None

    async def _run_client(self):
        try:
            import discord
        except ImportError:
            logger.warning("discord.py is not installed; Discord Gateway listener cannot start.")
            return

        intents = discord.Intents.none()
        intents.guilds = True
        intents.guild_messages = True
        intents.message_content = True

        client = discord.Client(intents=intents)
        self.client = client
        self.loop = asyncio.get_running_loop()

        @client.event
        async def on_ready():
            logger.info("Discord Gateway listener connected as %s.", client.user)
            await self._reconcile_once("ready")

        @client.event
        async def on_resumed():
            logger.info("Discord Gateway listener resumed.")

        @client.event
        async def on_message(message):
            self._handle_message_create(_message_to_payload(message))

        @client.event
        async def on_raw_message_edit(payload):
            self._handle_message_update(_raw_payload_data(payload))

        @client.event
        async def on_raw_message_delete(payload):
            self._handle_message_delete(str(payload.channel_id), str(payload.message_id))

        @client.event
        async def on_raw_bulk_message_delete(payload):
            self._handle_bulk_message_delete(
                str(payload.channel_id),
                [str(message_id) for message_id in payload.message_ids],
            )

        token = (runtime_environment_config().discord_bot_token or "").strip()
        await client.start(token, reconnect=True)

    async def _reconcile_once(self, reason):
        try:
            await asyncio.to_thread(self._run_reconcile, reason)
        except Exception:
            logger.exception("Discord Gateway reconciliation failed")

    def _run_reconcile(self, reason):
        with self.app.app_context():
            created, deleted = sync_discord_channels(emit_events=False, emit_delete_events=True)
            logger.info(
                "Discord Gateway reconciliation (%s): %s new message(s), %s deleted message(s).",
                reason,
                created,
                deleted,
            )

    def _handle_message_create(self, payload):
        with self.app.app_context():
            ingest_discord_gateway_message(payload, event_type="create")

    def _handle_message_update(self, payload):
        with self.app.app_context():
            ingest_discord_gateway_message(payload, event_type="update")

    def _handle_message_delete(self, channel_id, message_id):
        with self.app.app_context():
            delete_discord_gateway_message(channel_id, message_id)

    def _handle_bulk_message_delete(self, channel_id, message_ids):
        with self.app.app_context():
            delete_discord_gateway_messages(channel_id, message_ids)


def _raw_payload_data(payload):
    data = dict(getattr(payload, "data", None) or {})
    if "channel_id" not in data and getattr(payload, "channel_id", None):
        data["channel_id"] = str(payload.channel_id)
    if "id" not in data and getattr(payload, "message_id", None):
        data["id"] = str(payload.message_id)
    return data


def _user_payload(user):
    return {
        "id": str(getattr(user, "id", "") or ""),
        "username": getattr(user, "name", "") or "",
        "global_name": getattr(user, "global_name", None) or getattr(user, "display_name", None) or "",
        "avatar": getattr(getattr(user, "avatar", None), "key", None) or "",
    }


def _message_to_payload(message):
    return {
        "id": str(getattr(message, "id", "") or ""),
        "channel_id": str(getattr(getattr(message, "channel", None), "id", "") or getattr(message, "channel_id", "") or ""),
        "content": getattr(message, "content", "") or "",
        "timestamp": getattr(message, "created_at", None),
        "edited_timestamp": getattr(message, "edited_at", None),
        "webhook_id": str(getattr(message, "webhook_id", "") or "") or None,
        "author": _user_payload(getattr(message, "author", None)),
        "mentions": [_user_payload(user) for user in getattr(message, "mentions", [])],
        "embeds": [embed.to_dict() for embed in getattr(message, "embeds", [])],
        "attachments": [attachment.to_dict() for attachment in getattr(message, "attachments", [])],
    }


def start_discord_gateway(app):
    global _bridge
    if _bridge is None:
        _bridge = DiscordGatewayBridge(app)
    return _bridge.start()


def shutdown_discord_gateway():
    global _bridge
    if _bridge:
        _bridge.stop()
    _bridge = None


def discord_gateway_status():
    return {
        "enabled": runtime_environment_config().discord_gateway_enabled_raw != "0",
        "started": bool(_bridge and _bridge.started),
        "thread_alive": bool(_bridge and _bridge.thread and _bridge.thread.is_alive()),
    }
