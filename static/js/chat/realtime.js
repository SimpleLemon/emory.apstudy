import { deltaLoadParams } from "./cache.js";

export function createChatRealtime(context) {
  const { state, config, lifecycle, actions } = context;
  const {
    REALTIME_FALLBACK_MS,
    REALTIME_HEARTBEAT_MS,
    REALTIME_RECONNECT_MS,
  } = config;

  let realtimeFallbackTimer = null;
  let realtimeHeartbeatTimer = null;
  let realtimeReconnectTimer = null;
  let chatEventSource = null;
  let chatEventCursor = { since: null, after_id: null };
  let connectionGeneration = 0;
  const seenChatEventIds = new Set();
  const seenChatMessageIds = new Set();
  const SEEN_ID_LIMIT = 5000;

  function rememberId(seenIds, value) {
    const id = String(value || "");
    if (!id) return false;
    if (seenIds.has(id)) return false;
    seenIds.add(id);
    if (seenIds.size > SEEN_ID_LIMIT) {
      const oldest = seenIds.values().next().value;
      seenIds.delete(oldest);
    }
    return true;
  }

  function rememberChatEventId(eventId) {
    return rememberId(seenChatEventIds, eventId);
  }

  function rememberChatMessageId(messageId) {
    return rememberId(seenChatMessageIds, messageId);
  }

  function isActiveRoom(room) {
    return Boolean(
      room
      && state.activeRoom
      && actions.roomKey(state.activeRoom) === actions.roomKey(room)
    );
  }

  async function fetchMessageById(messageId) {
    if (!messageId) return null;
    try {
      const payload = await context.fetchJson(`/api/chat/messages/${encodeURIComponent(messageId)}`);
      return payload?.message || null;
    } catch (_) {
      return null;
    }
  }

  async function ingestMessageUpdate(event) {
    const room = state.activeRoom;
    const cache = actions.cacheFor(room);
    if (!room || !cache) return false;
    if (event.message_id) {
      const message = await fetchMessageById(event.message_id);
      if (message) {
        cache.messages = actions.mergeMessages(cache.messages, [message]);
        actions.updateCacheCursors(cache);
        actions.schedulePersistentRoomSave(room);
        if (!isActiveRoom(room)) return false;
        if (actions.patchMessageInDom(message)) {
          actions.updateAnnouncementsUnreadBanner(cache.messages);
          return true;
        }
      }
    }
    if (!isActiveRoom(room)) return false;
    await actions.loadMessages({ force: true, quiet: true, preserveScroll: true, light: true });
    return false;
  }

  async function ingestActiveRoomMessage(event) {
    const room = state.activeRoom;
    const cache = actions.cacheFor(room);
    if (!room || !cache) return false;

    if (event.message_id && !cache.messages.some((message) => message.id === event.message_id)) {
      const message = await fetchMessageById(event.message_id);
      if (message) {
        actions.applyIncomingMessages(room, [message]);
        if (!isActiveRoom(room)) return false;
        actions.playChatSound(event.actor_id);
        return true;
      }
    }

    if (!isActiveRoom(room)) return false;

    const delta = deltaLoadParams(cache);
    const incoming = delta.after
      ? await actions.loadMessages({ ...delta, quiet: true, force: true, light: true })
      : await actions.loadMessages({ force: true, quiet: true });
    if (!incoming.length && event.message_id) {
      await actions.loadMessages({ force: true, quiet: true });
    } else if (incoming.length) {
      actions.playChatSound(event.actor_id);
      return true;
    }
    return isActiveRoom(room) && incoming.length > 0;
  }

  function pollActiveRoomMessages() {
    if (state.realtimeReady) return;
    const room = state.activeRoom;
    const cache = actions.cacheFor(room);
    if (!room || !cache) return;
    const delta = deltaLoadParams(cache);
    if (delta.after) {
      void actions.loadMessages({ ...delta, quiet: true, force: true, light: true });
    } else {
      void actions.loadMessages({ force: true, quiet: true });
    }
  }

  function startRealtimeFallback() {
    if (lifecycle.paused || lifecycle.disposed || realtimeFallbackTimer || state.realtimeReady) return;
    realtimeFallbackTimer = window.setInterval(() => {
      if (document.visibilityState !== "visible") return;
      if (state.realtimeReady) {
        stopRealtimeFallback();
        return;
      }
      pollActiveRoomMessages();
      void actions.refreshChatSummary();
    }, REALTIME_FALLBACK_MS);
  }

  function stopRealtimeFallback() {
    if (!realtimeFallbackTimer) return;
    window.clearInterval(realtimeFallbackTimer);
    realtimeFallbackTimer = null;
  }

  function startRealtimeHeartbeat() {
    // EventSource owns message liveness while connected. Active-room polling
    // is reserved for the fallback loop so the two paths cannot overlap.
    void REALTIME_HEARTBEAT_MS;
  }

  function stopRealtimeHeartbeat() {
    if (!realtimeHeartbeatTimer) return;
    window.clearInterval(realtimeHeartbeatTimer);
    realtimeHeartbeatTimer = null;
  }

  function resetRealtimeConnection() {
    connectionGeneration += 1;
    if (chatEventSource) {
      chatEventSource.close();
      chatEventSource = null;
    }
    if (state.realtimeUnsubscribe) {
      state.realtimeUnsubscribe();
      state.realtimeUnsubscribe = null;
    }
    state.realtimeReady = false;
    state.realtimeConnecting = false;
    stopRealtimeHeartbeat();
  }

  function buildChatEventsStreamUrl() {
    const params = new URLSearchParams();
    if (chatEventCursor.since) params.set("since", chatEventCursor.since);
    if (chatEventCursor.after_id) params.set("after_id", chatEventCursor.after_id);
    const qs = params.toString();
    return qs ? `/api/chat/events/stream?${qs}` : "/api/chat/events/stream";
  }

  function scheduleRealtimeReconnect() {
    if (lifecycle.paused || lifecycle.disposed || realtimeReconnectTimer) return;
    resetRealtimeConnection();
    startRealtimeFallback();
    realtimeReconnectTimer = window.setTimeout(() => {
      realtimeReconnectTimer = null;
      initializeChatEventStream();
    }, REALTIME_RECONNECT_MS);
  }

  function handleRealtimeDisconnect() {
    scheduleRealtimeReconnect();
  }

  async function startRealtimeServices() {
    if (lifecycle.paused || lifecycle.disposed) return;
    initializeChatEventStream();
    void actions.loadInitialPresences();
  }

  function initializeChatEventStream() {
    if (lifecycle.paused || lifecycle.disposed) return;
    if (state.realtimeReady || chatEventSource || state.realtimeConnecting) return;
    if (typeof window.EventSource !== "function") {
      startRealtimeFallback();
      return;
    }
    state.realtimeConnecting = true;
    const generation = ++connectionGeneration;
    try {
      const source = new EventSource(buildChatEventsStreamUrl());
      chatEventSource = source;
      source.onopen = () => {
        if (generation !== connectionGeneration || source !== chatEventSource || lifecycle.paused || lifecycle.disposed) {
          source.close();
          return;
        }
        state.realtimeReady = true;
        state.realtimeConnecting = false;
        state.realtimeUnsubscribe = () => {
          if (chatEventSource) {
            chatEventSource.close();
            chatEventSource = null;
          }
        };
        stopRealtimeFallback();
        startRealtimeHeartbeat();
      };
      source.onmessage = (messageEvent) => {
        if (generation !== connectionGeneration || source !== chatEventSource || lifecycle.paused || lifecycle.disposed) return;
        let payload;
        try {
          payload = JSON.parse(messageEvent.data);
        } catch {
          return;
        }
        const eventId = payload?.$id || payload?.id;
        if (eventId && !rememberChatEventId(eventId)) return;
        if (payload?.created_at) {
          chatEventCursor = { since: payload.created_at, after_id: eventId || null };
        }
        void handleRealtimePayload({ payload });
      };
      source.onerror = () => {
        if (generation !== connectionGeneration || source !== chatEventSource || lifecycle.paused || lifecycle.disposed) return;
        handleRealtimeDisconnect();
      };
    } catch (error) {
      console.warn("Chat event stream unavailable", error);
      state.realtimeConnecting = false;
      handleRealtimeDisconnect();
    }
  }

  function normalizeChatEvent(response) {
    const raw = response?.payload ?? response?.row ?? response;
    if (!raw || typeof raw !== "object") return null;
    const nested = raw.data && typeof raw.data === "object" && !Array.isArray(raw.data) ? raw.data : null;
    if (!nested) return raw;
    return {
      ...nested,
      ...raw,
      scope_type: raw.scope_type || nested.scope_type,
      scope_id: raw.scope_id || nested.scope_id,
      event_type: raw.event_type || nested.event_type,
      message_id: raw.message_id || nested.message_id,
      thread_id: raw.thread_id || nested.thread_id,
      channel_id: raw.channel_id || nested.channel_id,
      actor_id: raw.actor_id || nested.actor_id,
    };
  }

  function eventIsRelevant(event) {
    if (!event) return false;
    if (event.scope_type === "channel") {
      return state.channels.some((channel) => channel.id === event.scope_id);
    }
    if (event.scope_type === "thread") {
      return state.threads.some((thread) => thread.id === event.scope_id) || event.thread_id;
    }
    if (event.scope_type === "university") {
      return Boolean(state.university?.school_key && state.university.school_key === event.scope_id);
    }
    return false;
  }

  async function handleRealtimePayload(response) {
    const event = normalizeChatEvent(response);
    if (!eventIsRelevant(event)) return;
    const eventRoom = event.scope_type === "channel"
      ? { type: "channel", id: event.scope_id || event.channel_id }
      : event.scope_type === "thread"
        ? { type: "thread", id: event.scope_id || event.thread_id }
        : null;

    if (event.event_type === "message_deleted") {
      actions.removeMessageFromCaches(event.message_id);
      void actions.refreshChatSummary();
      return;
    }

    if (event.event_type === "message_created") {
      if (event.message_id && !rememberChatMessageId(event.message_id)) return;
      if (eventRoom?.type === "thread" && !actions.threadExists(eventRoom.id)) {
        const thread = await actions.fetchThread(eventRoom.id);
        if (!thread) {
          await actions.bootstrap({ preserveActive: true });
        }
      }
      const active = state.activeRoom;
      if (eventRoom && active && actions.roomKey(eventRoom) === actions.roomKey(active)) {
        await ingestActiveRoomMessage(event);
      } else if (eventRoom) {
        actions.markRoomStale(eventRoom);
        actions.scheduleUnreadSummaryRefresh();
        actions.playChatSound(event.actor_id);
      }
      return;
    }

    if (event.event_type === "message_updated") {
      const active = state.activeRoom;
      if (eventRoom && active && actions.roomKey(eventRoom) === actions.roomKey(active)) {
        await ingestMessageUpdate(event);
      } else if (eventRoom) {
        actions.markRoomStale(eventRoom);
      }
      return;
    }

    if (["thread_updated", "block_updated", "university_approved", "university_denied"].includes(event.event_type)) {
      if (eventRoom) actions.markRoomStale(eventRoom);
      if (event.event_type === "thread_updated" && eventRoom?.type === "thread") {
        const thread = await actions.fetchThread(eventRoom.id);
        if (thread) {
          void actions.refreshChatSummary();
          return;
        }
      }
      await actions.bootstrap({ preserveActive: true });
    }
  }

  function bindEvents() {
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") {
        const room = state.activeRoom;
        const cache = actions.cacheFor(room);
        if (cache?.loaded) {
          if (cache.latestCursor) {
            const delta = deltaLoadParams(cache);
            void actions.loadMessages({ ...delta, quiet: true, force: true, light: true })
              .finally(() => {
                actions.markRoomRead(room, cache);
                void actions.refreshChatSummary();
              });
          } else if (cache.stale) {
            void actions.loadMessages({ force: true, quiet: true })
              .finally(() => {
                actions.markRoomRead(room, cache);
                void actions.refreshChatSummary();
              });
          } else {
            actions.markRoomRead(room, cache);
            void actions.refreshChatSummary();
          }
        } else {
          void actions.refreshChatSummary();
        }
        actions.refreshViewingPresence();
      } else {
        actions.clearTypingPresence();
      }
    });
  }

  function clearReconnectTimer() {
    window.clearTimeout(realtimeReconnectTimer);
    realtimeReconnectTimer = null;
  }

  return {
    bindEvents,
    clearReconnectTimer,
    handleRealtimePayload,
    resetRealtimeConnection,
    startRealtimeFallback,
    startRealtimeHeartbeat,
    startRealtimeServices,
    stopRealtimeFallback,
    stopRealtimeHeartbeat,
  };
}
