import { avatarAttrs, avatarUrl, escapeHtml, plural } from "./presentation.js";
import { CHAT_CACHE_SCHEMA, createPersistentChatCache, deltaLoadParams, mergeMessages, roomCachePayload, trimMessagesForPersistentCache, updateCacheCursors } from "./cache.js";
import { createChatMessagesDom } from "./messages-dom.js";
import { createChatPresence } from "./presence.js";
import { createChatRealtime } from "./realtime.js";

export function startChatRuntime(extensions = {}) {
  const root = document.querySelector(".chat-app");
  if (!root) return;

  const PRESENCE_REFRESH_MS = 5000;
  const TYPING_PRESENCE_TTL_MS = 8000;
  const PRESENCE_TAB_ID_KEY = "apstudy-presence-tab-id";
  const ROOM_SCROLL_RESTORE_DELAY = 0;
  const DISCORD_HISTORY_CHANNEL_IDS = new Set(["nest_announcements", "nest_chat"]);
  const CHAT_PREFETCH_ROOM_LIMIT = 8;
  const REALTIME_FALLBACK_MS = 3000;
  const REALTIME_HEARTBEAT_MS = 8000;
  const REALTIME_RECONNECT_MS = 1500;
  const ANNOUNCEMENTS_CHANNEL_ID = "nest_announcements";
  const GRAMMARLY_DISABLED_ATTRS = 'data-gramm="false" data-gramm_editor="false" data-enable-grammarly="false" spellcheck="false"';

  const state = {
    user: root.dataset.currentUserId ? { id: root.dataset.currentUserId } : null,
    settings: { chat_sound_enabled: true },
    capabilities: {},
    channels: [],
    threads: [],
    university: null,
    activeRoom: null,
    activeProfile: null,
    roomCache: new Map(),
    roomUnread: new Map(),
    presenceRefreshTimer: null,
    typingInputTimer: null,
    typingClearTimer: null,
    presenceRecords: new Map(),
    knownUsers: new Map(),
    tabId: null,
    searchTimer: null,
    scrollSaveTimer: null,
    realtimeUnsubscribe: null,
    realtimeReady: false,
    realtimeConnecting: false,
    chatSummaryLoading: false,
    localReadSeq: 0,
    clearedReadRooms: new Set(),
    messageSendInFlight: false,
    contextMenuRoom: null,
    contextMenuAnchor: null,
    loadingMessages: false,
    roomReadState: null,
    announcementsBannerVisible: false,
    membersCollapsed: sessionStorage.getItem("apstudy-chat-members-collapsed") === null
      ? window.innerWidth < 1440
      : sessionStorage.getItem("apstudy-chat-members-collapsed") === "true",
    hydratedFromPersistentCache: false,
    persistentCacheReady: false,
    serverBootstrapped: false,
    prefetchingRooms: new Set(),
    failedMessages: new Map(),
  };

  window.NestChat = state;

  let chatSoundCooldownTimer = null;
  let unreadSummaryRefreshTimer = null;
  let chatRuntimePaused = false;
  let chatRuntimeDisposed = false;
  let chatRequestController = new AbortController();
  let roomPrefetchIdleId = null;
  const transientChatTimers = new Set();
  const transientChatFrames = new Set();
  const persistentChatCache = createPersistentChatCache();

  function scheduleTransientTimeout(callback, delay = 0) {
    const timer = window.setTimeout(() => {
      transientChatTimers.delete(timer);
      if (!chatRuntimePaused && !chatRuntimeDisposed) callback();
    }, delay);
    transientChatTimers.add(timer);
    return timer;
  }

  function scheduleTransientFrame(callback) {
    const frame = window.requestAnimationFrame(() => {
      transientChatFrames.delete(frame);
      if (!chatRuntimePaused && !chatRuntimeDisposed) callback();
    });
    transientChatFrames.add(frame);
    return frame;
  }

  function clearTransientWork() {
    transientChatTimers.forEach((timer) => window.clearTimeout(timer));
    transientChatTimers.clear();
    transientChatFrames.forEach((frame) => window.cancelAnimationFrame(frame));
    transientChatFrames.clear();
    if (roomPrefetchIdleId !== null && "cancelIdleCallback" in window) {
      window.cancelIdleCallback(roomPrefetchIdleId);
    }
    roomPrefetchIdleId = null;
  }

  const els = {
    channelList: document.getElementById("chat-channel-list"),
    dmList: document.getElementById("chat-dm-list"),
    dmNew: document.getElementById("chat-dm-new"),
    dmSearch: document.getElementById("chat-dm-search"),
    dmSearchInput: document.getElementById("chat-dm-search-input"),
    dmResults: document.getElementById("chat-dm-results"),
    roomSymbol: document.getElementById("chat-room-symbol"),
    roomName: document.getElementById("chat-room-name"),
    roomMeta: document.getElementById("chat-room-meta"),
    status: document.getElementById("chat-status"),
    historyLimited: document.getElementById("chat-history-limited"),
    announcementsUnread: document.getElementById("chat-announcements-unread"),
    announcementsRead: document.getElementById("chat-announcements-read"),
    joinDiscord: document.getElementById("chat-join-discord"),
    messages: document.getElementById("chat-messages"),
    typing: document.getElementById("chat-typing-indicator"),
    composer: document.getElementById("chat-composer"),
    input: document.getElementById("chat-message-input"),
    sendButton: document.querySelector(".chat-send-button"),
    newMessages: document.getElementById("chat-new-messages"),
    members: document.getElementById("chat-members"),
    memberList: document.getElementById("chat-member-list"),
    membersContext: document.getElementById("chat-members-context"),
    membersCount: document.getElementById("chat-members-count"),
    membersRestoreCount: document.getElementById("chat-members-restore-count"),
    profilePanel: document.getElementById("chat-profile-panel"),
    profileBack: document.querySelector("[data-profile-back]"),
    profileToggle: document.querySelector("[data-toggle-members]"),
    audio: document.getElementById("chat-audio"),
  };

  const extensionContext = {
    state,
    els,
    setStatus: (...args) => setStatus(...args),
    onComposerChange: () => updateComposerSubmitState(),
  };
  extensions.attachments?.init?.(extensionContext);
  extensions.mediaPicker?.init?.(extensionContext);
  extensions.messageMedia?.init?.();

  const actions = {};
  const runtimeContext = {
    root,
    state,
    els,
    extensions,
    config: {
      PRESENCE_REFRESH_MS,
      TYPING_PRESENCE_TTL_MS,
      PRESENCE_TAB_ID_KEY,
      REALTIME_FALLBACK_MS,
      REALTIME_HEARTBEAT_MS,
      REALTIME_RECONNECT_MS,
      ANNOUNCEMENTS_CHANNEL_ID,
      GRAMMARLY_DISABLED_ATTRS,
    },
    lifecycle: {
      get paused() {
        return chatRuntimePaused;
      },
      get disposed() {
        return chatRuntimeDisposed;
      },
    },
    actions,
    fetchJson: (...args) => fetchJson(...args),
  };

  function roomKey(room) {
    if (!room || !room.id || !room.type) return "";
    return `${room.type}:${room.id}`;
  }

  function unreadKey(type, id) {
    return roomKey({ type, id });
  }

  function requestedRoomFromLocation() {
    const params = new URLSearchParams(window.location.search || "");
    const channelId = params.get("channel");
    if (channelId) return { type: "channel", id: channelId };
    const threadId = params.get("thread");
    if (threadId) return { type: "thread", id: threadId };
    return null;
  }

  function currentUserId() {
    return String(state.user?.id || root.dataset.currentUserId || "");
  }

  function roomTarget(room) {
    if (!room) return null;
    if (room.type === "channel") return state.channels.find((channel) => channel.id === room.id) || null;
    if (room.type === "thread") return state.threads.find((thread) => thread.id === room.id) || null;
    return null;
  }

  function persistentCacheKey(suffix) {
    const userId = currentUserId();
    return userId ? `${CHAT_CACHE_SCHEMA}:user:${userId}:${suffix}` : "";
  }

  function applyRoomCachePayload(payload) {
    if (!payload?.room) return false;
    const cache = cacheFor(payload.room);
    if (!cache) return false;
    cache.messages = trimMessagesForPersistentCache(payload.messages || []);
    cache.hasMore = Boolean(payload.hasMore);
    cache.loaded = true;
    cache.stale = true;
    cache.scrollTop = Number(payload.scrollTop) || 0;
    updateCacheCursors(cache);
    return true;
  }

  async function persistRoomCache(room) {
    const cache = cacheFor(room);
    if (!cache?.loaded) return;
    await persistentChatCache.write(
      persistentCacheKey(`room:${roomKey(room)}`),
      roomCachePayload(room, cache)
    );
  }

  async function hydrateRoomFromPersistentCache(room) {
    const payload = await persistentChatCache.read(persistentCacheKey(`room:${roomKey(room)}`));
    return applyRoomCachePayload(payload);
  }

  async function persistBootstrapCache() {
    if (!currentUserId()) return;
    await persistentChatCache.write(persistentCacheKey("bootstrap"), {
      user: state.user,
      settings: state.settings,
      channels: (state.channels || []).map(staleChannelPresence),
      threads: (state.threads || []).map(staleThreadPresence),
      university: state.university,
      activeRoom: state.activeRoom,
      discordInviteUrl: root.dataset.discordInviteUrl || "",
      membersCollapsed: state.membersCollapsed,
      savedAt: Date.now(),
    });
  }

  async function hydrateFromPersistentCache() {
    const payload = await persistentChatCache.read(persistentCacheKey("bootstrap"));
    state.persistentCacheReady = true;
    if (!payload) return false;
    if (state.serverBootstrapped) return false;

    state.user = payload.user || state.user;
    state.settings = { ...state.settings, ...(payload.settings || {}) };
    state.capabilities = payload.capabilities || {};
    extensions.attachments?.configure?.(state.capabilities);
    extensions.mediaPicker?.configure?.(state.capabilities);
    state.channels = Array.isArray(payload.channels) ? payload.channels.map(staleChannelPresence) : [];
    state.threads = Array.isArray(payload.threads) ? payload.threads.map(staleThreadPresence) : [];
    state.university = payload.university || null;
    registerKnownUsersFromState();
    if (payload.discordInviteUrl) root.dataset.discordInviteUrl = payload.discordInviteUrl;
    if (typeof payload.membersCollapsed === "boolean") setMembersCollapsed(payload.membersCollapsed);
    updateRoomLists();
    await startRealtimeServices();

    const room = payload.activeRoom || (state.channels[0] && { type: "channel", id: state.channels[0].id });
    if (room) {
      await hydrateRoomFromPersistentCache(room);
      state.hydratedFromPersistentCache = true;
      await selectRoom(room, { fromCacheHydration: true, quiet: true });
    }
    return true;
  }

  function schedulePersistentBootstrapSave() {
    scheduleTransientTimeout(() => {
      void persistBootstrapCache();
    }, 0);
  }

  function schedulePersistentRoomSave(room) {
    scheduleTransientTimeout(() => {
      void persistRoomCache(room);
    }, 0);
  }

  function cacheFor(room) {
    const key = roomKey(room);
    if (!key) return null;
    if (!state.roomCache.has(key)) {
      state.roomCache.set(key, {
        messages: [],
        oldestCursor: null,
        latestCursor: null,
        latestMessageId: null,
        hasMore: false,
        loaded: false,
        stale: false,
        scrollTop: 0,
      });
    }
    return state.roomCache.get(key);
  }

  function saveActiveScroll() {
    const cache = cacheFor(state.activeRoom);
    if (cache && els.messages) {
      cache.scrollTop = els.messages.scrollTop;
      schedulePersistentRoomSave(state.activeRoom);
    }
  }

  function restoreScroll(cache, shouldBottom = false) {
    scheduleTransientTimeout(() => {
      if (!els.messages) return;
      if (shouldBottom) {
        els.messages.scrollTop = els.messages.scrollHeight;
        return;
      }
      if (typeof cache?.scrollTop === "number") {
        els.messages.scrollTop = cache.scrollTop;
      }
    }, ROOM_SCROLL_RESTORE_DELAY);
  }

  function isNearBottom() {
    if (!els.messages) return true;
    const remaining = els.messages.scrollHeight - els.messages.scrollTop - els.messages.clientHeight;
    return remaining < 140;
  }

  async function fetchJson(url, options = {}) {
    const requestOptions = {
      ...options,
      signal: options.signal || chatRequestController.signal,
    };
    if (window.APStudyHttp?.fetchJson) {
      return window.APStudyHttp.fetchJson(url, requestOptions);
    }
    const response = await fetch(url, {
      headers: { "Content-Type": "application/json", ...(requestOptions.headers || {}) },
      ...requestOptions,
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload.error || payload.message || "Something went wrong.");
    }
    return payload;
  }

  function normalizeUnreadRoom(room = {}) {
    const type = room.type === "channel" ? "channel" : room.type === "thread" ? "thread" : "";
    const id = String(room.id || "");
    if (!type || !id) return null;
    const count = Math.max(0, Number(room.unread_count || 0));
    return {
      type,
      id,
      unread_count: Math.min(count, 99),
      has_unread: room.has_unread === true || count > 0,
    };
  }

  function applyChatSummary(payload = {}) {
    const nextUnread = new Map();
    for (const room of payload.rooms || []) {
      const unread = normalizeUnreadRoom(room);
      if (!unread) continue;
      const key = unreadKey(unread.type, unread.id);
      if (state.clearedReadRooms.has(key) && unread.has_unread) {
        continue;
      }
      if (!unread.has_unread) {
        state.clearedReadRooms.delete(key);
      }
      nextUnread.set(key, unread);
    }
    for (const key of state.clearedReadRooms) {
      if (nextUnread.has(key)) continue;
      const [type, id] = key.split(":");
      if (!type || !id) continue;
      nextUnread.set(key, {
        type,
        id,
        unread_count: 0,
        has_unread: false,
      });
    }
    state.roomUnread = nextUnread;
    updateRoomLists();
    const reconciledPayload = chatSummaryPayloadFromUnreadMap(payload);
    window.dispatchEvent(new CustomEvent("apstudy-chat-summary", { detail: reconciledPayload }));
    return reconciledPayload;
  }

  function chatSummaryPayloadFromUnreadMap(payload = {}) {
    const rooms = Array.from(state.roomUnread.values()).map((room) => ({
      type: room.type,
      id: room.id,
      unread_count: Math.max(0, Number(room.unread_count || 0)),
      has_unread: room.has_unread === true && Number(room.unread_count || 0) > 0,
    }));
    const totalUnread = rooms.reduce((total, room) => total + Number(room.unread_count || 0), 0);
    return {
      ...payload,
      rooms,
      total_unread: Math.min(totalUnread, 99),
      unread_capped: totalUnread >= 99 || rooms.some((room) => Number(room.unread_count || 0) >= 99),
      has_unread: totalUnread > 0,
    };
  }

  async function refreshChatSummary() {
    if (state.chatSummaryLoading || document.visibilityState === "hidden") return null;
    state.chatSummaryLoading = true;
    const startReadSeq = state.localReadSeq;
    try {
      const payload = await fetchJson("/api/chat/summary", {
        headers: { Accept: "application/json" },
      });
      if (state.localReadSeq !== startReadSeq) {
        // A local read-state change (e.g. opening a room) happened while this
        // summary was in flight; discard it so it cannot resurrect a cleared badge.
        return null;
      }
      return applyChatSummary(payload);
    } catch (_) {
      return null;
    } finally {
      state.chatSummaryLoading = false;
    }
  }

  function unreadForRoom(type, id) {
    return state.roomUnread.get(unreadKey(type, id)) || { unread_count: 0, has_unread: false };
  }

  function setRoomUnread(room, unread = {}) {
    const key = roomKey(room);
    if (!key) return;
    const count = Math.max(0, Number(unread.unread_count || 0));
    state.roomUnread.set(key, {
      type: room.type,
      id: room.id,
      unread_count: Math.min(count, 99),
      has_unread: unread.has_unread === true || count > 0,
    });
    updateRoomLists();
  }

  function clearRoomUnread(room) {
    const key = roomKey(room);
    if (!key) return;
    state.localReadSeq += 1;
    state.clearedReadRooms.add(key);
    state.roomUnread.set(key, {
      type: room.type,
      id: room.id,
      unread_count: 0,
      has_unread: false,
    });
    updateRoomLists();
  }

  function latestMessageForRead(cache) {
    const messages = cache?.messages || [];
    for (let index = messages.length - 1; index >= 0; index -= 1) {
      const message = messages[index];
      if (message?.id) return message;
    }
    return null;
  }

  function shouldAutoMarkRoomRead(room, cache = cacheFor(room)) {
    if (room?.type === "channel" && room.id === ANNOUNCEMENTS_CHANNEL_ID) {
      return unreadAnnouncementMessages(cache?.messages, state.roomReadState).length === 0;
    }
    return true;
  }

  function markRoomRead(room, cache = cacheFor(room), { force = false } = {}) {
    if (!room?.type || !room?.id) return;
    if (!force && document.visibilityState === "hidden") return;
    if (!force && !shouldAutoMarkRoomRead(room, cache)) return;
    if (force) {
      clearRoomUnread(room);
      cancelUnreadSummaryRefresh();
      state.localReadSeq += 1;
    }
    const latest = latestMessageForRead(cache);
    const body = {
      scope_type: room.type === "channel" ? "channel" : "thread",
      scope_id: room.id,
    };
    // Force-read must use the server latest message so prefetched/stale Discord
    // caches cannot leave newer messages unread after the API call.
    if (!force && latest?.id) body.message_id = latest.id;
    return fetchJson("/api/chat/read", {
      method: "POST",
      body: JSON.stringify(body),
    })
      .then((payload) => {
        if (!force) clearRoomUnread(room);
        if (
          force
          && room.type === "channel"
          && room.id === ANNOUNCEMENTS_CHANNEL_ID
          && state.activeRoom?.type === "channel"
          && state.activeRoom.id === ANNOUNCEMENTS_CHANNEL_ID
        ) {
          const readState = payload?.read_state || {};
          state.roomReadState = {
            last_read_at: readState.last_read_at || latest?.created_at || state.roomReadState?.last_read_at || null,
            last_read_message_id: readState.last_read_message_id || latest?.id || state.roomReadState?.last_read_message_id || null,
          };
          state.announcementsBannerVisible = false;
          if (els.announcementsUnread) els.announcementsUnread.hidden = true;
        }
        state.localReadSeq += 1;
        window.dispatchEvent(new CustomEvent("apstudy-chat-read-state-change", { detail: { room } }));
        return refreshChatSummary();
      })
      .catch(() => {
        if (force) {
          state.localReadSeq += 1;
          return refreshChatSummary();
        }
        return null;
      });
  }

  function setStatus(message, tone = "info") {
    if (!els.status) return;
    if (!message) {
      els.status.hidden = true;
      els.status.textContent = "";
      els.status.dataset.tone = "";
      return;
    }
    els.status.hidden = false;
    els.status.dataset.tone = tone;
    els.status.textContent = message;
  }

  function focusComposerSoon() {
    if (!els.input || els.input.disabled || els.composer?.hidden) return;
    scheduleTransientTimeout(() => els.input?.focus({ preventScroll: true }), 0);
  }

  function playChatSound(actorId) {
    if (!state.settings.chat_sound_enabled || !els.audio) return;
    if (actorId && state.user && String(actorId) === String(state.user.id)) return;
    if (chatSoundCooldownTimer) return;
    try {
      els.audio.currentTime = 0;
      void els.audio.play();
    } catch (error) {
      void error;
      // Browsers may block sound until the user interacts with the page.
    }
    chatSoundCooldownTimer = window.setTimeout(() => {
      chatSoundCooldownTimer = null;
    }, 1500);
  }

  function scheduleUnreadSummaryRefresh() {
    if (unreadSummaryRefreshTimer) return;
    unreadSummaryRefreshTimer = window.setTimeout(() => {
      unreadSummaryRefreshTimer = null;
      void refreshChatSummary();
    }, 400);
  }

  function cancelUnreadSummaryRefresh() {
    if (!unreadSummaryRefreshTimer) return;
    window.clearTimeout(unreadSummaryRefreshTimer);
    unreadSummaryRefreshTimer = null;
  }

  function activeChannel() {
    if (state.activeRoom?.type !== "channel") return null;
    return state.channels.find((channel) => channel.id === state.activeRoom.id) || null;
  }

  function activeThread() {
    if (state.activeRoom?.type !== "thread") return null;
    return state.threads.find((thread) => thread.id === state.activeRoom.id) || null;
  }

  function threadExists(threadId) {
    return state.threads.some((thread) => thread.id === threadId);
  }

  function channelIsPending(channel) {
    return channel?.kind === "university" && !channel.approved;
  }

  function channelIsWritable(channel) {
    return Boolean(channel && !channel.read_only && channel.approved !== false);
  }

  function isAnnouncementsChannel(channel) {
    return channel?.id === ANNOUNCEMENTS_CHANNEL_ID;
  }

  function channelLabel(channel) {
    return channel?.label || channel?.school_name || channel?.name || "Channel";
  }

  function channelMeta(channel) {
    if (isAnnouncementsChannel(channel)) return "";
    if (channelIsPending(channel)) {
      if (channel.university_status === "denied") return "Denied";
      return "Waiting approval";
    }
    return `${channel.online_count ?? channel.active_count ?? 0} online`;
  }

  function channelSymbol(channel) {
    if (channel?.kind === "university") return "U";
    return "#";
  }

  function unreadBadgeMarkup(type, id) {
    const unread = unreadForRoom(type, id);
    if (!unread.has_unread || Number(unread.unread_count || 0) <= 0) return "";
    const count = Number(unread.unread_count || 0);
    const label = count >= 99 ? "99+" : String(count);
    const ariaLabel = `${label} unread ${label === "1" ? "message" : "messages"}`;
    return `<span class="chat-room-unread-badge" aria-label="${escapeHtml(ariaLabel)}">${escapeHtml(label)}</span>`;
  }

  function roomButton({ type, id, active, leading, title, meta, className = "" }) {
    const unread = unreadForRoom(type, id);
    const hasUnread = unread.has_unread && Number(unread.unread_count || 0) > 0;
    const button = document.createElement("button");
    button.type = "button";
    button.className = `chat-list-button ${className} ${active ? "is-active" : ""} ${hasUnread ? "has-unread" : ""}`.trim();
    button.dataset.roomType = type;
    button.dataset.roomId = id;
    button.innerHTML = `
      ${leading}
      <span class="chat-list-copy">
        <span class="chat-list-title">${escapeHtml(title)}</span>
        ${meta || ""}
      </span>
      ${unreadBadgeMarkup(type, id)}
    `;
    button.addEventListener("click", () => selectRoom({ type, id }));
    button.addEventListener("contextmenu", (event) => openRoomContextMenu({ type, id }, event));
    button.addEventListener("keydown", (event) => {
      if (event.key !== "ContextMenu" && !(event.shiftKey && event.key === "F10")) return;
      openRoomContextMenu({ type, id }, event);
    });
    return button;
  }

  function ensureRoomContextMenu() {
    let menu = document.getElementById("chat-room-context-menu");
    if (menu) return menu;
    menu = document.createElement("div");
    menu.id = "chat-room-context-menu";
    menu.className = "chat-room-context-menu";
    menu.hidden = true;
    menu.setAttribute("role", "menu");
    menu.setAttribute("data-gramm", "false");
    menu.setAttribute("data-gramm_editor", "false");
    menu.setAttribute("data-enable-grammarly", "false");
    menu.setAttribute("spellcheck", "false");
    menu.innerHTML = `
      <button type="button" class="chat-room-context-action" role="menuitem" data-chat-room-action="read" ${GRAMMARLY_DISABLED_ATTRS}>Mark as read</button>
    `;
    document.body.appendChild(menu);
    menu.addEventListener("click", (event) => {
      const actionButton = event.target.closest("[data-chat-room-action]");
      if (!actionButton || !state.contextMenuRoom) return;
      const room = { ...state.contextMenuRoom };
      closeRoomContextMenu();
      if (actionButton.dataset.chatRoomAction === "read") {
        void markRoomRead(room, cacheFor(room), { force: true });
      }
    });
    return menu;
  }

  function closeRoomContextMenu() {
    const menu = document.getElementById("chat-room-context-menu");
    const shouldRestoreFocus = Boolean(menu?.contains(document.activeElement));
    if (menu) menu.hidden = true;
    state.contextMenuRoom = null;
    if (shouldRestoreFocus) state.contextMenuAnchor?.focus?.({ preventScroll: true });
    state.contextMenuAnchor = null;
  }

  function openRoomContextMenu(room, event) {
    if (!room?.type || !room?.id) return;
    event.preventDefault();
    state.contextMenuRoom = room;
    state.contextMenuAnchor = event.currentTarget || null;
    const menu = ensureRoomContextMenu();
    const rect = event.currentTarget?.getBoundingClientRect?.() || { left: 0, bottom: 0 };
    const x = typeof event.clientX === "number" && event.clientX > 0 ? event.clientX : rect.left + 16;
    const y = typeof event.clientY === "number" && event.clientY > 0 ? event.clientY : rect.bottom;
    menu.hidden = false;
    const menuRect = menu.getBoundingClientRect();
    const left = Math.min(Math.max(8, x), window.innerWidth - menuRect.width - 8);
    const top = Math.min(Math.max(8, y), window.innerHeight - menuRect.height - 8);
    menu.style.left = `${left}px`;
    menu.style.top = `${top}px`;
    menu.querySelector("button")?.focus({ preventScroll: true });
  }

  function renderChannels() {
    if (!els.channelList) return;
    els.channelList.innerHTML = "";
    for (const channel of state.channels) {
      const active = state.activeRoom?.type === "channel" && state.activeRoom.id === channel.id;
      const leading = `<span class="chat-channel-symbol" aria-hidden="true">${escapeHtml(channelSymbol(channel))}</span>`;
      const metaText = channelMeta(channel);
      const meta = metaText ? `<small>${escapeHtml(metaText)}</small>` : "";
      const button = roomButton({
        type: "channel",
        id: channel.id,
        active,
        leading,
        title: channelLabel(channel),
        meta,
        className: channelIsPending(channel) ? "is-pending" : "",
      });
      els.channelList.appendChild(button);
    }
  }

  function renderThreads() {
    if (!els.dmList) return;
    els.dmList.innerHTML = "";
    if (!state.threads.length) {
      const empty = document.createElement("div");
      empty.className = "chat-empty chat-empty-compact";
      empty.setAttribute("data-gramm", "false");
      empty.setAttribute("data-gramm_editor", "false");
      empty.setAttribute("data-enable-grammarly", "false");
      empty.setAttribute("spellcheck", "false");
      empty.textContent = "No direct messages yet.";
      els.dmList.appendChild(empty);
      return;
    }
    for (const thread of state.threads) {
      const other = thread.other_user || {};
      const status = dmPresenceStatus(thread);
      const active = state.activeRoom?.type === "thread" && state.activeRoom.id === thread.id;
      const leading = `
        <span class="chat-avatar-wrap">
          <img class="chat-avatar-mini" ${avatarAttrs(other.picture_url, 48, "48px")} alt="">
          <span class="chat-presence-dot chat-presence-overlay is-${status}" aria-hidden="true"></span>
        </span>
      `;
      const button = roomButton({
        type: "thread",
        id: thread.id,
        active,
        leading,
        title: other.name || other.username || "Nest User",
        meta: dmPresenceMarkup(status),
        className: "chat-dm-button",
      });
      els.dmList.appendChild(button);
    }
  }

  function updateRoomLists() {
    renderChannels();
    renderThreads();
  }

  function composerIsWritable() {
    const channel = activeChannel();
    if (channel) return channelIsWritable(channel);
    const thread = activeThread();
    return Boolean(thread && !thread.blocked);
  }

  function updateComposerSubmitState() {
    if (!els.sendButton) return;
    const hasText = Boolean(els.input?.value.trim());
    const hasAttachment = Boolean(extensions.attachments?.readyIds?.().length);
    const hasGif = Boolean(extensions.mediaPicker?.hasSelection?.());
    const attachmentsBusy = Boolean(extensions.attachments?.isBusy?.());
    els.sendButton.disabled = !composerIsWritable()
      || (!hasText && !hasAttachment && !hasGif)
      || attachmentsBusy
      || state.messageSendInFlight;
  }

  function setComposer(enabled, placeholder) {
    if (!els.composer || !els.input || !els.sendButton) return;
    els.composer.hidden = false;
    els.input.disabled = !enabled;
    els.input.placeholder = placeholder || "Message";
    autosizeComposer();
    updateComposerSubmitState();
  }

  function renderHeader() {
    const channel = activeChannel();
    const thread = activeThread();
    if (channel) {
      els.roomSymbol.classList.remove("is-avatar");
      els.roomSymbol.textContent = channelSymbol(channel);
      els.roomName.textContent = channelLabel(channel);
      els.roomMeta.textContent = isAnnouncementsChannel(channel)
        ? ""
        : channelIsPending(channel)
          ? "Waiting for admin approval"
          : `${channel.online_count ?? channel.active_count ?? 0} online`;
      setHistoryBanner(channelIsPending(channel) ? null : channel);
      const placeholder = channelIsPending(channel)
        ? "Waiting for admin approval"
        : channel.read_only
          ? "Read-only channel"
          : `Message #${channelLabel(channel)}`;
      setComposer(channelIsWritable(channel), placeholder);
      return;
    }

    if (thread) {
      const other = thread.other_user || {};
      els.roomSymbol.classList.add("is-avatar");
      const status = dmPresenceStatus(thread);
      els.roomSymbol.innerHTML = `
        <span class="chat-room-avatar-wrap">
          <img ${avatarAttrs(other.picture_url, 48, "48px")} alt="">
          <span class="chat-presence-dot chat-presence-overlay is-${status}" aria-hidden="true"></span>
        </span>
      `;
      els.roomName.textContent = other.name || other.username || "Nest User";
      els.roomMeta.textContent = "";
      setHistoryBanner(null);
      setComposer(!thread.blocked, thread.blocked ? "This conversation is blocked" : `Message ${other.name || other.username || ""}`.trim());
      return;
    }

    els.roomSymbol.classList.remove("is-avatar");
    els.roomSymbol.textContent = "#";
    els.roomName.textContent = "Chat";
    els.roomMeta.textContent = "Loading...";
    setHistoryBanner(null);
    if (els.composer) els.composer.hidden = true;
  }

  function renderMembers(users) {
    if (!els.members || !els.memberList || !els.profilePanel) return;
    els.members.classList.remove("is-dm-profile", "is-profile-view");
    state.activeProfile = null;
    const onlineUsers = users || [];
    if (els.membersContext) els.membersContext.textContent = "Online";
    els.membersCount.textContent = plural(onlineUsers.length, "user", "users");
    els.membersRestoreCount.textContent = String(onlineUsers.length);
    if (els.profileBack) els.profileBack.hidden = true;
    els.profilePanel.hidden = true;
    els.profilePanel.innerHTML = "";
    if (!onlineUsers.length) {
      els.memberList.innerHTML = `<div class="chat-empty chat-empty-compact" ${GRAMMARLY_DISABLED_ATTRS}>No online users.</div>`;
      return;
    }
    els.memberList.innerHTML = onlineUsers.map((user) => `
      <button class="chat-member" type="button" data-profile-id="${escapeHtml(user.id)}" aria-label="View ${escapeHtml(user.name || user.username || "Nest User")} profile${user.tier_label ? `, ${escapeHtml(user.tier_label)}` : ""}" ${GRAMMARLY_DISABLED_ATTRS}>
        <img class="chat-member-avatar" ${avatarAttrs(user.picture_url, 84, "42px")} alt="">
        <span class="chat-member-copy">
          <strong>${escapeHtml(user.name || user.username || "Nest User")}</strong>
          <small>${escapeHtml(user.school || user.username || presenceStatusLabel(user.presence_status || "active"))}</small>
        </span>
        ${memberTierBadgeMarkup(user)}
      </button>
    `).join("");
  }

  function showMemberProfile(user, options = {}) {
    if (!user || !els.members || !els.profilePanel) return;
    state.activeProfile = user;
    els.members.classList.remove("is-dm-profile");
    els.members.classList.add("is-profile-view");
    if (els.membersContext) els.membersContext.textContent = "Profile";
    els.membersCount.textContent = user.name || user.username || "Nest User";
    if (els.profileBack) els.profileBack.hidden = false;
    els.profilePanel.hidden = false;
    els.profilePanel.innerHTML = profileMarkup(user, {
      showBlock: false,
      status: user.presence_status || (user.online ? "active" : "offline"),
    });
    if (!options.preserveFocus) {
      els.profileBack?.focus({ preventScroll: true });
    }
  }

  function renderDmProfile(thread) {
    if (!els.members || !els.memberList || !els.profilePanel) return;
    const other = thread?.other_user || {};
    const status = dmPresenceStatus(thread);
    state.activeProfile = null;
    els.members.classList.remove("is-profile-view");
    els.members.classList.add("is-dm-profile");
    if (els.membersContext) els.membersContext.textContent = "Conversation";
    els.membersCount.textContent = "Profile";
    els.membersRestoreCount.textContent = status === "offline" ? "0" : "1";
    if (els.profileBack) els.profileBack.hidden = true;
    els.memberList.innerHTML = "";
    els.profilePanel.hidden = false;
    els.profilePanel.innerHTML = profileMarkup(other, {
      status,
      blocked: Boolean(thread?.blocked),
      showBlock: Boolean(other.id),
    });
  }

  function normalizeHexColor(value) {
    const candidate = String(value || "").trim();
    const normalized = candidate.startsWith("#") ? candidate : `#${candidate}`;
    return /^#[0-9a-f]{6}$/i.test(normalized) ? normalized.toLowerCase() : "#fecae1";
  }

  function profileDetail(label, value, className = "") {
    return `
      <div class="${className}" ${GRAMMARLY_DISABLED_ATTRS}>
        <dt>${escapeHtml(label)}</dt>
        <dd>${escapeHtml(value || "Not set")}</dd>
      </div>
    `;
  }

  function tierBadgeMarkup(user, size = 24, triggerClass = "") {
    if (!user?.tier_badge?.asset || !user?.tier_label) return "";
    const className = `tier-badge-trigger${triggerClass ? ` ${triggerClass}` : ""}`;
    return `<span class="${className}" tabindex="0" role="img" aria-label="${escapeHtml(user.tier_label)}" data-tooltip="${escapeHtml(user.tier_label)}">
      <img class="tier-badge" src="${escapeHtml(user.tier_badge.asset)}" alt="" width="${size}" height="${size}" loading="lazy" decoding="async">
    </span>`;
  }

  function memberTierBadgeMarkup(user) {
    if (!user?.tier_badge?.asset || !user?.tier_label) return "";
    return `<span class="tier-badge-trigger chat-member-tier" aria-hidden="true" data-tooltip="${escapeHtml(user.tier_label)}">
      <img class="tier-badge" src="${escapeHtml(user.tier_badge.asset)}" alt="" width="20" height="20" loading="lazy" decoding="async">
    </span>`;
  }

  function profileMarkup(user, options = {}) {
    const status = normalizeLocalPresenceStatus(options.status || user?.presence_status || (user?.online ? "active" : "offline"));
    const handle = user?.handle || (user?.username ? `@${user.username}` : `@${user?.id || "apstudy-user"}`);
    const graduation = user?.graduation_year || user?.class_year || "";
    const memberSince = user?.member_since || "";
    const bannerColor = normalizeHexColor(user?.banner_color);
    const tierBadge = tierBadgeMarkup(user);
    const blockLabel = options.blocked ? "Unblock" : "Block";
    const blockAction = options.showBlock
      ? `<button type="button" data-block-user="${escapeHtml(user.id)}" data-blocked="${options.blocked ? "true" : "false"}">${blockLabel}</button>`
      : "";
    return `
      <div class="chat-profile-card" ${GRAMMARLY_DISABLED_ATTRS}>
        <div class="profile-tile" style="--profile-banner-color: ${escapeHtml(bannerColor)};">
          <div class="profile-tile-banner" aria-hidden="true"></div>
          <div class="profile-tile-body">
            <div class="profile-tile-avatar-frame">
              <img class="profile-tile-avatar" ${avatarAttrs(user?.picture_url, 150, "(max-width: 640px) 96px, 150px")} alt="${escapeHtml(user?.name || "Nest User")} avatar" width="150" height="150">
              <span class="chat-presence-dot chat-presence-overlay is-${status}" role="img" aria-label="${escapeHtml(presenceStatusLabel(status))}" title="${escapeHtml(presenceStatusLabel(status))}"></span>
            </div>
            <div class="profile-tile-heading">
              <h3>${escapeHtml(user?.name || user?.username || "Nest User")}</h3>
              <div class="chat-profile-meta">
                <p class="chat-profile-handle">${escapeHtml(handle)}</p>
                <span class="chat-profile-presence-label">${escapeHtml(presenceStatusLabel(status))}</span>
              </div>
              ${tierBadge}
            </div>
            <dl class="profile-tile-details">
              ${profileDetail("School", user?.school, user?.is_emory_school ? "profile-tile-detail-emory" : "")}
              ${profileDetail("Major", user?.major)}
              ${profileDetail("Graduation", graduation)}
              ${profileDetail("Education", user?.education_level)}
              ${profileDetail("Member Since", memberSince, user?.is_early_member ? "profile-tile-detail-early-member" : "")}
            </dl>
          </div>
        </div>
      </div>
      <div class="chat-profile-actions" ${GRAMMARLY_DISABLED_ATTRS}>
        ${user?.profile_url ? `<a href="${escapeHtml(user.profile_url)}">View profile</a>` : ""}
        ${blockAction}
      </div>
    `;
  }

  function updateChannel(payload) {
    if (!payload?.id) return;
    const index = state.channels.findIndex((channel) => channel.id === payload.id);
    if (index >= 0) {
      state.channels[index] = { ...state.channels[index], ...payload };
    } else {
      state.channels.push(payload);
    }
  }

  function updateThread(payload) {
    if (!payload?.id) return;
    registerKnownUser(payload.other_user);
    const index = state.threads.findIndex((thread) => thread.id === payload.id);
    if (index >= 0) {
      state.threads[index] = { ...state.threads[index], ...payload };
    } else {
      state.threads.unshift(payload);
    }
    state.threads.sort((a, b) => String(b.last_message_at || "").localeCompare(String(a.last_message_at || "")));
  }

  async function fetchThread(threadId) {
    if (!threadId) return null;
    try {
      const payload = await fetchJson(`/api/chat/dm/threads/${encodeURIComponent(threadId)}`);
      if (payload.thread) {
        updateThread(payload.thread);
        updateRoomLists();
        renderPresenceDrivenUi();
        schedulePersistentBootstrapSave();
        return payload.thread;
      }
    } catch (error) {
      setStatus(error.message || "Unable to load direct message.", "error");
    }
    return null;
  }

  function currentRoomUrl(room, params = {}) {
    const query = new URLSearchParams();
    for (const [key, value] of Object.entries(params)) {
      if (value) query.set(key, value);
    }
    const suffix = query.toString() ? `?${query.toString()}` : "";
    if (room.type === "channel") {
      return `/api/chat/channels/${encodeURIComponent(room.id)}/messages${suffix}`;
    }
    return `/api/chat/dm/threads/${encodeURIComponent(room.id)}/messages${suffix}`;
  }

  async function loadMessages({ before = null, after = null, after_message_id = null, force = false, preserveScroll = false, quiet = false, light = false } = {}) {
    const room = state.activeRoom;
    if (!room) return [];
    const cache = cacheFor(room);
    if (!cache) return [];
    if (state.loadingMessages && !force) return [];

    const channel = activeChannel();
    if (channelIsPending(channel)) {
      renderApprovalNotice(channel);
      return [];
    }

    const wasNearBottom = isNearBottom();
    const previousHeight = els.messages.scrollHeight;
    const previousTop = els.messages.scrollTop;
    const isDelta = Boolean(after || after_message_id);
    const useLight = light || (quiet && isDelta && !before);
    state.loadingMessages = true;
    if (!quiet && !before && !after && !after_message_id && !cache.loaded) renderMessageLoader();

    try {
      const payload = await fetchJson(currentRoomUrl(room, { before, after, after_message_id }));
      if (!state.activeRoom || roomKey(state.activeRoom) !== roomKey(room)) return [];

      const messages = payload.messages || [];
      const previousMessages = cache.messages;
      if (isDelta || before) {
        cache.messages = mergeMessages(cache.messages, messages);
      } else {
        cache.messages = mergeMessages([], messages);
      }
      cache.loaded = true;
      cache.stale = false;
      cache.hasMore = Boolean(payload.has_more);
      updateCacheCursors(cache);
      schedulePersistentRoomSave(room);
      if (!useLight) {
        if (payload.channel) updateChannel(payload.channel);
        if (payload.thread) updateThread(payload.thread);
        if (payload.read_state) state.roomReadState = payload.read_state;
        renderHeader();
        updateRoomLists();
        updateCurrentMembersFromPayload(payload);
        schedulePersistentBootstrapSave();
      }

      const incoming = messages.filter((message) => message?.id && !previousMessages.some((row) => row.id === message.id));
      const canIncremental = useLight && isDelta && !before;
      if (canIncremental) {
        if (incoming.length) {
          syncMessagesToDom(cache.messages, {
            incremental: true,
            incoming,
            scrollToBottom: wasNearBottom,
          });
        }
      } else {
        syncMessagesToDom(cache.messages, {
          scrollToBottom: !before && !isDelta && !preserveScroll && wasNearBottom,
        });
        if (before) {
          const delta = els.messages.scrollHeight - previousHeight;
          els.messages.scrollTop = previousTop + delta;
        } else if (!isDelta) {
          restoreScroll(cache, !preserveScroll);
        } else if (wasNearBottom) {
          stickToBottom();
        }
      }

      setStatus(null);

      if (!before && (wasNearBottom || !isDelta)) {
        markRoomRead(room, cache);
      }
      return messages;
    } catch (error) {
      setStatus(error.message || "Unable to load messages.", "error");
      return [];
    } finally {
      state.loadingMessages = false;
    }
  }

  async function prefetchRoomMessages(room) {
    const key = roomKey(room);
    if (!key || state.prefetchingRooms.has(key)) return;
    const cache = cacheFor(room);
    if (cache?.loaded && !cache.stale) return;
    state.prefetchingRooms.add(key);
    try {
      await hydrateRoomFromPersistentCache(room);
      const roomCache = cacheFor(room);
      const params = deltaLoadParams(roomCache);
      const payload = await fetchJson(currentRoomUrl(room, params));
      if (payload.channel) updateChannel(payload.channel);
      if (payload.thread) updateThread(payload.thread);
      const messages = payload.messages || [];
      roomCache.messages = params.after
        ? mergeMessages(roomCache.messages, messages)
        : mergeMessages([], messages);
      roomCache.loaded = true;
      roomCache.stale = false;
      roomCache.hasMore = Boolean(payload.has_more);
      updateCacheCursors(roomCache);
      schedulePersistentRoomSave(room);
      schedulePersistentBootstrapSave();
    } catch (_) {
      const roomCache = cacheFor(room);
      if (roomCache) roomCache.stale = true;
    } finally {
      state.prefetchingRooms.delete(key);
    }
  }

  function scheduleRoomPrefetches() {
    const rooms = [
      ...state.channels.map((channel) => ({ type: "channel", id: channel.id })),
      ...state.threads.slice(0, CHAT_PREFETCH_ROOM_LIMIT).map((thread) => ({ type: "thread", id: thread.id })),
    ]
      .filter((room) => room.id)
      .filter((room) => roomKey(room) !== roomKey(state.activeRoom))
      .slice(0, CHAT_PREFETCH_ROOM_LIMIT);
    if (!rooms.length) return;
    const run = () => {
      for (const room of rooms) {
        void prefetchRoomMessages(room);
      }
    };
    if ("requestIdleCallback" in window) {
      roomPrefetchIdleId = window.requestIdleCallback(() => {
        roomPrefetchIdleId = null;
        if (!chatRuntimePaused && !chatRuntimeDisposed) run();
      }, { timeout: 2500 });
    } else {
      scheduleTransientTimeout(run, 700);
    }
  }

  function renderCachedRoom(room, options = {}) {
    const cache = cacheFor(room);
    if (!cache) return false;
    if (!cache.loaded) return false;
    renderMessages(cache.messages);
    restoreScroll(cache, Boolean(options.toBottom));
    return true;
  }

  function markRoomStale(room) {
    const cache = cacheFor(room);
    if (cache) cache.stale = true;
  }

  function removeMessageFromCaches(messageId) {
    if (!messageId) return [];
    const removed = [];
    for (const [key, cache] of state.roomCache.entries()) {
      const nextMessages = [];
      cache.messages.forEach((message, index) => {
        if (message.id === messageId) {
          removed.push({ key, cache, message, index });
        } else {
          nextMessages.push(message);
        }
      });
      cache.messages = nextMessages;
      updateCacheCursors(cache);
      const [type, id] = key.split(":");
      if (type && id) schedulePersistentRoomSave({ type, id });
    }
    const activeCache = cacheFor(state.activeRoom);
    if (activeCache) {
      if (!removeMessageFromDom(messageId)) {
        renderMessages(activeCache.messages);
      } else {
        updateAnnouncementsUnreadBanner(activeCache.messages);
        updateHistoryBannerVisibility();
      }
    }
    return removed;
  }

  function restoreMessagesToCaches(removed = []) {
    for (const record of removed) {
      if (!record?.cache || record.cache.messages.some((message) => message.id === record.message?.id)) continue;
      record.cache.messages.splice(Math.min(record.index, record.cache.messages.length), 0, record.message);
      updateCacheCursors(record.cache);
      const [type, id] = String(record.key || "").split(":");
      if (type && id) schedulePersistentRoomSave({ type, id });
    }
    const activeCache = cacheFor(state.activeRoom);
    if (activeCache) renderMessages(activeCache.messages);
  }

  async function selectRoom(room, options = {}) {
    const previousRoom = state.activeRoom;
    saveActiveScroll();
    closeInlineProfilePopover();
    if (previousRoom && roomKey(previousRoom) !== roomKey(room)) {
      extensions.attachments?.resetForRoom?.();
      extensions.mediaPicker?.clear?.();
    }
    state.activeRoom = room;
    state.activeProfile = null;
    updateRoomLists();
    renderHeader();
    if (!options.suppressFocus) focusComposerSoon();
    setStatus(null);
    handleActiveRoomPresenceChange(previousRoom);

    const channel = activeChannel();
    const thread = activeThread();
    if (channelIsPending(channel)) {
      renderApprovalNotice(channel);
      schedulePersistentBootstrapSave();
      return;
    }

    if (thread) {
      renderDmProfile(thread);
    } else {
      renderMembers(channel?.online_users || channel?.active_users || []);
    }

    const cache = cacheFor(room);
    if (!cache.loaded) {
      await hydrateRoomFromPersistentCache(room);
    }
    if (renderCachedRoom(room)) {
      if (!cache.stale || latestMessageForRead(cache)) {
        markRoomRead(room, cache);
      }
      if (cache.latestCursor) {
        const delta = deltaLoadParams(cache);
        await loadMessages({ ...delta, quiet: true, force: true, light: true });
        markRoomRead(room);
      } else if (cache.stale) {
        await loadMessages({ force: true, quiet: true });
        markRoomRead(room);
      }
    } else {
      await loadMessages({ force: true });
      markRoomRead(room);
    }
    renderPresenceDrivenUi();
    schedulePersistentBootstrapSave();
  }

  async function bootstrap({ preserveActive = false } = {}) {
    const payload = await fetchJson("/api/chat/bootstrap");
    state.user = payload.user || state.user;
    state.settings = { ...state.settings, ...(payload.settings || {}) };
    state.capabilities = payload.capabilities || {};
    extensions.attachments?.configure?.(state.capabilities);
    extensions.mediaPicker?.configure?.(state.capabilities);
    state.channels = (payload.sections?.nest || []).map(staleChannelPresence);
    state.threads = (payload.sections?.direct_messages || []).map(staleThreadPresence);
    state.university = payload.university || null;
    registerKnownUsersFromState();
    if (payload.discord_invite_url) root.dataset.discordInviteUrl = payload.discord_invite_url;
    state.serverBootstrapped = true;
    updateRoomLists();
    await startRealtimeServices();
    setMembersCollapsed(state.membersCollapsed);
    schedulePersistentBootstrapSave();
    await refreshChatSummary();

    const requestedRoom = requestedRoomFromLocation();
    if (requestedRoom) {
      const requestedExists = requestedRoom.type === "channel"
        ? state.channels.some((channel) => channel.id === requestedRoom.id)
        : state.threads.some((thread) => thread.id === requestedRoom.id);
      if (requestedExists) {
        await selectRoom(requestedRoom, { suppressFocus: preserveActive });
        scheduleRoomPrefetches();
        return;
      }
    }

    const activeKey = roomKey(state.activeRoom);
    if (activeKey) {
      const [type, id] = activeKey.split(":");
      const stillExists = type === "channel"
        ? state.channels.some((channel) => channel.id === id)
        : state.threads.some((thread) => thread.id === id);
      if (stillExists) {
        await selectRoom({ type, id }, { suppressFocus: preserveActive });
        scheduleRoomPrefetches();
        return;
      }
    }

    const firstChannel = state.channels[0];
    const firstThread = state.threads[0];
    if (firstChannel) {
      await selectRoom({ type: "channel", id: firstChannel.id });
    } else if (firstThread) {
      await selectRoom({ type: "thread", id: firstThread.id });
    } else {
      renderHeader();
      renderMessages([]);
    }
    scheduleRoomPrefetches();
  }

  async function sendActiveMessage(event) {
    event.preventDefault();
    const room = state.activeRoom;
    if (!room || !els.input) return;
    const content = els.input.value.trim();
    const attachmentIds = extensions.attachments?.readyIds?.() || [];
    const gifSelection = extensions.mediaPicker?.selection?.() || {};
    if (!content && !attachmentIds.length && !gifSelection.gif_id) return;
    if (extensions.attachments?.isBusy?.()) {
      setStatus("Wait for attachments to finish uploading before sending.", "error");
      return;
    }
    if (state.messageSendInFlight) return;
    const channel = activeChannel();
    const thread = activeThread();
    if (channel && !channelIsWritable(channel)) return;
    if (thread?.blocked) return;

    state.messageSendInFlight = true;
    els.sendButton.disabled = true;
    clearTypingPresence(room);
    const localId = `pending-${crypto.randomUUID()}`;
    const payloadBody = { content, attachment_ids: attachmentIds, ...gifSelection };
    const optimistic = {
      id: localId,
      user_id: state.user?.id,
      author_name: state.user?.name || state.user?.username || "You",
      author_username: state.user?.username || "",
      author_avatar_url: state.user?.picture_url || state.user?.picture || "",
      content: content || (gifSelection.gif_id ? "GIF" : "Attachment"),
      rendered_html: escapeHtml(content || ""),
      created_at: new Date().toISOString(),
      delivery_state: "sending",
      can_delete: false,
      attachments: [],
    };
    applyIncomingMessages(room, [optimistic], { toBottom: true, markRead: false });
    try {
      const url = currentRoomUrl(room);
      const payload = await fetchJson(url, {
        method: "POST",
        body: JSON.stringify(payloadBody),
      });
      removeMessageFromCaches(localId);
      els.input.value = "";
      autosizeComposer();
      extensions.attachments?.clear?.();
      extensions.mediaPicker?.clear?.(true);
      const cache = cacheFor(room);
      if (cache && payload.message) {
        applyIncomingMessages(room, [payload.message], { toBottom: true });
      }
      refreshViewingPresence();
      schedulePersistentBootstrapSave();
    } catch (error) {
      const cache = cacheFor(room);
      const failed = cache?.messages?.find((message) => message.id === localId);
      if (failed) {
        failed.delivery_state = "failed";
        state.failedMessages.set(localId, { room, payload: payloadBody });
        patchMessageInDom(failed);
        schedulePersistentRoomSave(room);
      }
      setStatus(error.message || "Unable to send message.", "error");
    } finally {
      state.messageSendInFlight = false;
      updateComposerSubmitState();
    }
  }

  async function retryMessage(messageId) {
    const failed = state.failedMessages.get(messageId);
    if (!failed || state.messageSendInFlight) return;
    const cache = cacheFor(failed.room);
    const message = cache?.messages?.find((row) => row.id === messageId);
    if (message) { message.delivery_state = "sending"; patchMessageInDom(message); }
    state.messageSendInFlight = true;
    els.sendButton.disabled = true;
    try {
      const response = await fetchJson(currentRoomUrl(failed.room), { method: "POST", body: JSON.stringify(failed.payload) });
      state.failedMessages.delete(messageId);
      removeMessageFromCaches(messageId);
      if (response.message) applyIncomingMessages(failed.room, [response.message], { toBottom: true });
      extensions.attachments?.clear?.();
      extensions.mediaPicker?.clear?.(true);
    } catch (error) {
      if (message) { message.delivery_state = "failed"; patchMessageInDom(message); }
      setStatus(error.message || "Unable to send message.", "error");
    } finally {
      state.messageSendInFlight = false;
      updateComposerSubmitState();
    }
  }

  function handleComposerKeydown(event) {
    if (event.key !== "Enter" || event.isComposing) return;
    if (event.shiftKey) {
      scheduleTransientTimeout(() => {
        autosizeComposer();
        scheduleTypingPresence();
      }, 0);
      return;
    }
    event.preventDefault();
    if (state.messageSendInFlight) return;
    if (els.composer?.requestSubmit) {
      els.composer.requestSubmit();
    } else {
      void sendActiveMessage(event);
    }
  }

  function autosizeComposer() {
    if (!els.input) return;
    els.input.style.height = "auto";
    const nextHeight = Math.min(112, Math.max(24, els.input.scrollHeight));
    els.input.style.height = `${nextHeight}px`;
  }

  async function searchPeople() {
    const query = els.dmSearchInput.value.trim();
    if (query.length < 2) {
      els.dmResults.innerHTML = "";
      return;
    }
    try {
      const payload = await fetchJson(`/api/chat/dm/search?q=${encodeURIComponent(query)}`);
      const results = payload.results || [];
      els.dmResults.innerHTML = results.length
        ? results.map((user) => `
          <button type="button" class="chat-member chat-dm-result" data-start-dm="${escapeHtml(user.id)}" aria-label="Start a direct message with ${escapeHtml(user.name || user.username || "Nest User")}${user.tier_label ? `, ${escapeHtml(user.tier_label)}` : ""}" ${GRAMMARLY_DISABLED_ATTRS}>
            <img class="chat-member-avatar" ${avatarAttrs(user.picture_url, 84, "42px")} alt="">
            <span class="chat-member-copy">
              <strong>${escapeHtml(user.name || user.username || "Nest User")}</strong>
              <small>${escapeHtml([user.school, user.major].filter(Boolean).join(" · ") || user.username || "User")}</small>
            </span>
            ${memberTierBadgeMarkup(user)}
          </button>
        `).join("")
        : `<div class="chat-empty chat-empty-compact" ${GRAMMARLY_DISABLED_ATTRS}>No users found.</div>`;
    } catch (error) {
      els.dmResults.innerHTML = `<div class="chat-empty chat-empty-compact" ${GRAMMARLY_DISABLED_ATTRS}>${escapeHtml(error.message)}</div>`;
    }
  }

  async function startDm(userId) {
    try {
      const payload = await fetchJson("/api/chat/dm/threads", {
        method: "POST",
        body: JSON.stringify({ user_id: userId }),
      });
      updateThread(payload.thread);
      if (payload.thread?.id) {
        setRoomUnread({ type: "thread", id: payload.thread.id }, { unread_count: 0, has_unread: false });
      }
      renderThreads();
      els.dmSearch.hidden = true;
      els.dmSearchInput.value = "";
      els.dmResults.innerHTML = "";
      await selectRoom({ type: "thread", id: payload.thread.id });
      schedulePersistentBootstrapSave();
    } catch (error) {
      setStatus(error.message || "Unable to start direct message.", "error");
    }
  }

  async function toggleBlock(userId, currentlyBlocked) {
    try {
      const payload = await fetchJson(`/api/chat/blocks/${encodeURIComponent(userId)}`, {
        method: currentlyBlocked ? "DELETE" : "POST",
      });
      const thread = activeThread();
      if (thread) {
        thread.blocked = Boolean(payload.blocked);
        renderHeader();
        renderDmProfile(thread);
        schedulePersistentBootstrapSave();
      }
      if (currentlyBlocked && payload.blocked === false) {
        window.APStudyToast?.show?.({
          message: "User unblocked.",
          type: "info",
          duration: 10_000,
          action: {
            label: "Undo",
            onClick: () => toggleBlock(userId, false),
          },
        });
      }
    } catch (error) {
      setStatus(error.message || "Unable to update block.", "error");
    }
  }

  function setMembersCollapsed(collapsed) {
    state.membersCollapsed = Boolean(collapsed);
    sessionStorage.setItem("apstudy-chat-members-collapsed", String(state.membersCollapsed));
    root.classList.toggle("members-collapsed", state.membersCollapsed);
    els.profileToggle?.classList.toggle("is-active", !state.membersCollapsed);
    els.profileToggle?.setAttribute("aria-pressed", String(!state.membersCollapsed));
    const label = state.membersCollapsed ? "Show user profile" : "Hide user profile";
    els.profileToggle?.setAttribute("aria-label", label);
    els.profileToggle?.setAttribute("title", label);
    if (state.persistentCacheReady || state.serverBootstrapped) {
      schedulePersistentBootstrapSave();
    }
  }

  function stopChatRuntime({ dispose = false } = {}) {
    if (chatRuntimeDisposed || (chatRuntimePaused && !dispose)) return;
    chatRuntimePaused = true;
    if (dispose) chatRuntimeDisposed = true;

    const activeCache = cacheFor(state.activeRoom);
    if (activeCache && els.messages) {
      activeCache.scrollTop = els.messages.scrollTop;
      void persistRoomCache(state.activeRoom);
    }

    clearTypingPresence();
    resetRealtimeConnection();
    stopRealtimeFallback();
    stopRealtimeHeartbeat();
    stopPresenceRefreshTimer();
    clearRealtimeReconnectTimer();
    cancelUnreadSummaryRefresh();
    window.clearTimeout(chatSoundCooldownTimer);
    chatSoundCooldownTimer = null;
    window.clearTimeout(state.searchTimer);
    state.searchTimer = null;
    window.clearTimeout(state.scrollSaveTimer);
    state.scrollSaveTimer = null;
    clearTransientWork();
    chatRequestController.abort();
    window.APStudyPresenceHeartbeat?.clearChatRoom?.();
    els.audio?.pause?.();
    closeRoomContextMenu();
    closeInlineProfilePopover();
  }

  function resumeChatRuntime() {
    if (chatRuntimeDisposed || !chatRuntimePaused) return;
    chatRuntimePaused = false;
    chatRequestController = new AbortController();
    startPresenceRefreshTimer();
    if (state.serverBootstrapped) {
      void startRealtimeServices();
    } else {
      void startChat();
    }
    void refreshChatSummary();
    refreshViewingPresence();
  }

  function bindEvents() {
    els.composer?.addEventListener("submit", sendActiveMessage);
    els.input?.addEventListener("keydown", handleComposerKeydown);
    els.input?.addEventListener("input", () => {
      autosizeComposer();
      scheduleTypingPresence();
      updateComposerSubmitState();
    });
    bindMessagePaneEvents();
    els.dmNew?.addEventListener("click", () => {
      els.dmSearch.hidden = !els.dmSearch.hidden;
      if (!els.dmSearch.hidden) els.dmSearchInput.focus();
    });
    els.dmSearchInput?.addEventListener("input", () => {
      window.clearTimeout(state.searchTimer);
      state.searchTimer = window.setTimeout(searchPeople, 180);
    });
    els.dmResults?.addEventListener("click", (event) => {
      const button = event.target.closest("[data-start-dm]");
      if (button) void startDm(button.dataset.startDm);
    });
    bindMessageDocumentEvents();
    els.profileToggle?.addEventListener("click", () => setMembersCollapsed(!state.membersCollapsed));
    document.querySelector("[data-restore-members]")?.addEventListener("click", () => setMembersCollapsed(false));
    const rail = document.getElementById("chat-rail");
    const backdrop = document.getElementById("chat-drawer-backdrop");
    let drawerReturnFocus = null;
    const closeChatDrawers = () => {
      rail?.classList.remove("is-open");
      els.members?.classList.remove("is-open");
      [rail, els.members].forEach((drawer) => {
        drawer?.removeAttribute("role");
        drawer?.removeAttribute("aria-modal");
      });
      window.APStudyAccessibility?.syncDialogs?.();
      document.querySelectorAll("[data-open-rail], [data-open-members]").forEach((button) => button.setAttribute("aria-expanded", "false"));
      if (backdrop) backdrop.hidden = true;
      document.body.classList.remove("chat-drawer-open");
      drawerReturnFocus?.focus?.({ preventScroll: true });
      drawerReturnFocus = null;
    };
    const openChatDrawer = (drawer, trigger) => {
      if (!drawer) return;
      drawerReturnFocus = trigger || document.activeElement;
      if (drawer === rail) els.members?.classList.remove("is-open");
      else rail?.classList.remove("is-open");
      drawer.classList.add("is-open");
      drawer.setAttribute("role", "dialog");
      drawer.setAttribute("aria-modal", "true");
      trigger?.setAttribute("aria-expanded", "true");
      if (backdrop) backdrop.hidden = false;
      document.body.classList.add("chat-drawer-open");
      drawer.querySelector("[data-close-rail], [data-close-members], button, a[href]")?.focus({ preventScroll: true });
    };
    document.querySelector("[data-open-rail]")?.addEventListener("click", (event) => openChatDrawer(rail, event.currentTarget));
    document.querySelector("[data-close-rail]")?.addEventListener("click", closeChatDrawers);
    document.querySelector("[data-open-members]")?.addEventListener("click", (event) => openChatDrawer(els.members, event.currentTarget));
    document.querySelector("[data-close-members]")?.addEventListener("click", closeChatDrawers);
    backdrop?.addEventListener("click", closeChatDrawers);
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        closeRoomContextMenu();
        closeChatDrawers();
        closeInlineProfilePopover();
        extensions.mediaPicker?.close?.();
      }
    });
    els.channelList?.addEventListener("scroll", closeRoomContextMenu);
    els.dmList?.addEventListener("scroll", closeRoomContextMenu);
    window.addEventListener("resize", () => {
      closeRoomContextMenu();
      closeInlineProfilePopover();
      if (window.innerWidth > 1100) closeChatDrawers();
    });
    bindRealtimeEvents();
  }

  async function startChat() {
    setMembersCollapsed(state.membersCollapsed);
    renderMessageLoader();
    const cachePromise = hydrateFromPersistentCache().catch((error) => {
      if (error?.name === "AbortError") return false;
      console.warn("Unable to hydrate chat cache", error);
      state.persistentCacheReady = true;
      return false;
    });
    const bootstrapPromise = bootstrap().catch((error) => {
      if (error?.name === "AbortError") return false;
      setStatus(error.message || "Unable to load chat.", "error");
      throw error;
    });
    await Promise.allSettled([cachePromise, bootstrapPromise]);
  }

  const messagesDom = createChatMessagesDom(runtimeContext);
  const {
    applyIncomingMessages,
    bindDocumentEvents: bindMessageDocumentEvents,
    bindPaneEvents: bindMessagePaneEvents,
    closeInlineProfilePopover,
    patchMessageInDom,
    removeMessageFromDom,
    renderApprovalNotice,
    renderMessageLoader,
    renderMessages,
    setHistoryBanner,
    stickToBottom,
    syncMessagesToDom,
    unreadAnnouncementMessages,
    updateAnnouncementsUnreadBanner,
    updateHistoryBannerVisibility,
  } = messagesDom;
  const presence = createChatPresence(runtimeContext);
  const {
    clearTypingPresence,
    dmPresenceMarkup,
    dmPresenceStatus,
    handleActiveRoomPresenceChange,
    loadInitialPresences,
    normalizeLocalPresenceStatus,
    presenceStatusLabel,
    refreshViewingPresence,
    registerKnownUser,
    registerKnownUsersFromState,
    renderPresenceDrivenUi,
    scheduleTypingPresence,
    staleChannelPresence,
    staleThreadPresence,
    startPresenceRefreshTimer,
    stopPresenceRefreshTimer,
    updateCurrentMembersFromPayload,
  } = presence;
  const realtime = createChatRealtime(runtimeContext);
  const {
    bindEvents: bindRealtimeEvents,
    clearReconnectTimer: clearRealtimeReconnectTimer,
    resetRealtimeConnection,
    startRealtimeFallback,
    startRealtimeHeartbeat,
    startRealtimeServices,
    stopRealtimeFallback,
    stopRealtimeHeartbeat,
  } = realtime;
  Object.assign(actions, {
    applyIncomingMessages,
    bootstrap,
    cacheFor,
    channelIsPending,
    channelIsWritable,
    clearTypingPresence,
    closeRoomContextMenu,
    clearRoomUnread,
    currentUserId,
    fetchThread,
    loadInitialPresences,
    loadMessages,
    latestMessageForRead,
    markRoomRead,
    markRoomStale,
    mergeMessages,
    patchMessageInDom,
    playChatSound,
    refreshChatSummary,
    refreshViewingPresence,
    removeMessageFromCaches,
    restoreMessagesToCaches,
    roomKey,
    schedulePersistentRoomSave,
    scheduleTransientFrame,
    scheduleUnreadSummaryRefresh,
    threadExists,
    activeChannel,
    activeThread,
    renderDmProfile,
    renderHeader,
    renderMembers,
    profileMarkup,
    showMemberProfile,
    setStatus,
    isNearBottom,
    retryMessage,
    toggleBlock,
    updateRoomLists,
    updateAnnouncementsUnreadBanner,
    updateCacheCursors,
  });

  bindEvents();
  if (window.APStudyPageLifecycle?.register) {
    window.APStudyPageLifecycle.register({
      pause: () => stopChatRuntime(),
      resume: resumeChatRuntime,
      dispose: () => stopChatRuntime({ dispose: true }),
    });
  } else {
    window.addEventListener("pagehide", () => stopChatRuntime({ dispose: true }), { once: true });
  }
  startPresenceRefreshTimer();
  void startChat();
}
