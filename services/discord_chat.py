"""Service boundary for Discord-backed chat operations.

The Flask chat blueprint registers the callback-driven Discord adapters during
application bootstrap. Scheduler and Gateway code imports only this module, so
background callers remain independent of blueprint modules while request-time
tests retain their established blueprint patch seams.
"""


_handlers = {}


def register_discord_chat_handlers(**handlers):
    """Register the chat domain handlers used by background integrations."""
    _handlers.update(handlers)


def _handler(name):
    handler = _handlers.get(name)
    if handler is None:
        raise RuntimeError(f"Discord chat handler is not registered: {name}")
    return handler


def sync_discord_channels(emit_events=True, emit_delete_events=None):
    return _handler("sync_discord_channels")(emit_events, emit_delete_events)


def ingest_discord_gateway_message(message, *, event_type="create"):
    return _handler("ingest_discord_gateway_message")(
        message,
        event_type=event_type,
    )


def delete_discord_gateway_message(discord_channel_id, discord_message_id):
    return _handler("delete_discord_gateway_message")(
        discord_channel_id,
        discord_message_id,
    )


def delete_discord_gateway_messages(discord_channel_id, discord_message_ids):
    return _handler("delete_discord_gateway_messages")(
        discord_channel_id,
        discord_message_ids,
    )
