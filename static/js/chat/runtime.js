import { avatarAttrs, avatarUrl, escapeHtml, formatMessageTimestamp, groupMessages, localDateKey, parseMessageDate, plural } from "./presentation.js";
import { CHAT_CACHE_SCHEMA, createPersistentChatCache, deltaLoadParams, mergeMessages, roomCachePayload, trimMessagesForPersistentCache, updateCacheCursors } from "./cache.js";

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

  let realtimeFallbackTimer = null;
  let realtimeHeartbeatTimer = null;
  let realtimeReconnectTimer = null;
  let chatEventSource = null;
  let chatEventCursor = { since: null, after_id: null };
  const seenChatEventIds = new Set();
  const seenChatMessageIds = new Set();
  let chatSoundCooldownTimer = null;
  let unreadSummaryRefreshTimer = null;
  let inlineProfilePopover = null;
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

  function currentTabId() {
    if (window.APStudyPresenceHeartbeat?.tabId) return window.APStudyPresenceHeartbeat.tabId;
    if (state.tabId) return state.tabId;
    try {
      state.tabId = sessionStorage.getItem(PRESENCE_TAB_ID_KEY);
      if (!state.tabId) {
        state.tabId = window.crypto?.randomUUID?.() || Math.random().toString(36).slice(2, 12);
        state.tabId = state.tabId.replace(/[^A-Za-z0-9_-]/g, "").slice(0, 64);
        sessionStorage.setItem(PRESENCE_TAB_ID_KEY, state.tabId);
      }
    } catch (_) {
      state.tabId = state.tabId || Math.random().toString(36).slice(2, 12);
    }
    return state.tabId;
  }

  function registerKnownUser(user) {
    if (!user?.id) return;
    state.knownUsers.set(String(user.id), { ...(state.knownUsers.get(String(user.id)) || {}), ...user });
  }

  function registerKnownUsersFromState() {
    registerKnownUser(state.user);
    for (const channel of state.channels || []) {
      for (const user of channel.online_users || []) registerKnownUser(user);
      for (const user of channel.active_users || []) registerKnownUser(user);
    }
    for (const thread of state.threads || []) {
      registerKnownUser(thread.other_user);
    }
  }

  function staleChannelPresence(channel) {
    return {
      ...channel,
      active_count: 0,
      active_users: [],
      online_count: 0,
      online_users: [],
    };
  }

  function staleThreadPresence(thread) {
    const other = thread?.other_user ? { ...thread.other_user, online: false } : thread?.other_user;
    return {
      ...thread,
      other_user: other,
      active_count: 0,
      presence_status: "offline",
    };
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

  function renderMessageLoader(label = "Loading messages...") {
    if (!els.messages) return;
    const loader = window.APStudyLoader?.html
      ? window.APStudyLoader.html(label, { sizePx: 46, textToneClass: "text-on-surface" })
      : `<div class="chat-empty">${escapeHtml(label)}</div>`;
    els.messages.innerHTML = `<div class="chat-message-loader" ${GRAMMARLY_DISABLED_ATTRS}>${loader}</div>`;
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

  function rememberChatMessageId(messageId) {
    const id = String(messageId || "");
    if (!id) return false;
    if (seenChatMessageIds.has(id)) return false;
    seenChatMessageIds.add(id);
    if (seenChatMessageIds.size > 5000) {
      seenChatMessageIds.clear();
      seenChatMessageIds.add(id);
    }
    return true;
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

  function dmPresenceStatus(thread) {
    if (thread?.presence_status) return normalizeLocalPresenceStatus(thread.presence_status);
    if (thread?.other_user?.presence_status) return normalizeLocalPresenceStatus(thread.other_user.presence_status);
    return thread?.other_user?.online ? "active" : "offline";
  }

  function presenceStatusLabel(status) {
    if (status === "active") return "Online";
    if (status === "busy") return "Busy";
    if (status === "focus") return "Focus mode";
    return "Offline";
  }

  function dmPresenceMarkup(status) {
    const label = presenceStatusLabel(status);
    return `
      <small class="chat-presence-line">
        <span>${label}</span>
      </small>
    `;
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

  function setHistoryBanner(channel) {
    if (!els.historyLimited) return;
    const invite = root.dataset.discordInviteUrl || "";
    if (els.joinDiscord) {
      els.joinDiscord.hidden = !invite;
      if (invite) els.joinDiscord.href = invite;
    }
    if (!channel || channel.kind !== "discord" || channel.history_limited !== true) {
      els.historyLimited.hidden = true;
      delete els.historyLimited.dataset.channelId;
      return;
    }
    els.historyLimited.dataset.channelId = channel.id;
    updateHistoryBannerVisibility();
  }

  function messagePaneIsScrollable() {
    if (!els.messages) return false;
    return els.messages.scrollHeight > els.messages.clientHeight + 1;
  }

  function updateHistoryBannerVisibility() {
    if (!els.historyLimited) return;
    const channelId = els.historyLimited.dataset.channelId;
    if (!channelId) {
      els.historyLimited.hidden = true;
      return;
    }
    const channel = activeChannel();
    const shouldConsider = Boolean(
      channel
      && channel.id === channelId
      && channel.kind === "discord"
      && channel.history_limited === true
    );
    if (!shouldConsider) {
      els.historyLimited.hidden = true;
      return;
    }
    const atTop = (els.messages?.scrollTop || 0) <= 16;
    els.historyLimited.hidden = !(messagePaneIsScrollable() && atTop);
  }

  function unreadAnnouncementMessages(messages, readState) {
    const lastReadAt = readState?.last_read_at ? parseMessageDate(readState.last_read_at) : null;
    const currentUserId = String(state.user?.id || "");
    return (messages || []).filter((message) => {
      if (String(message.user_id || "") === currentUserId) return false;
      const created = parseMessageDate(message.created_at);
      if (!created) return false;
      if (!lastReadAt) return true;
      return created.getTime() > lastReadAt.getTime();
    });
  }

  function announcementUnreadFitsInPane(unreadNodes) {
    if (!els.messages || !unreadNodes.length) return true;
    const paneTop = els.messages.getBoundingClientRect().top;
    const paneBottom = paneTop + els.messages.clientHeight;
    const first = unreadNodes[0].getBoundingClientRect();
    const last = unreadNodes[unreadNodes.length - 1].getBoundingClientRect();
    return first.top >= paneTop && last.bottom <= paneBottom;
  }

  async function markAnnouncementsRead() {
    const room = state.activeRoom;
    if (!room || room.type !== "channel" || room.id !== ANNOUNCEMENTS_CHANNEL_ID) return;
    const cache = cacheFor(room);
    const latest = latestMessageForRead(cache);
    if (!latest?.id) return;
    await fetchJson("/api/chat/read", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        scope_type: "channel",
        scope_id: room.id,
        message_id: latest.id,
      }),
    });
    state.roomReadState = {
      last_read_at: latest.created_at,
      last_read_message_id: latest.id,
    };
    clearRoomUnread(room);
    state.announcementsBannerVisible = false;
    if (els.announcementsUnread) els.announcementsUnread.hidden = true;
    void refreshChatSummary();
  }

  function updateAnnouncementsUnreadBanner(messages) {
    if (!els.announcementsUnread) return;
    const channel = activeChannel();
    if (!channel || channel.id !== ANNOUNCEMENTS_CHANNEL_ID) {
      els.announcementsUnread.hidden = true;
      state.announcementsBannerVisible = false;
      return;
    }
    const unread = unreadAnnouncementMessages(messages, state.roomReadState);
    if (!unread.length) {
      els.announcementsUnread.hidden = true;
      state.announcementsBannerVisible = false;
      return;
    }
    scheduleTransientFrame(() => {
      const unreadNodes = unread
        .map((message) => els.messages?.querySelector(`[data-message-id="${CSS.escape(message.id)}"]`))
        .filter(Boolean);
      if (!unreadNodes.length) {
        els.announcementsUnread.hidden = true;
        state.announcementsBannerVisible = false;
        return;
      }
      if (announcementUnreadFitsInPane(unreadNodes)) {
        void markAnnouncementsRead();
        return;
      }
      els.announcementsUnread.hidden = false;
      state.announcementsBannerVisible = true;
    });
  }

  function closeInlineProfilePopover() {
    if (inlineProfilePopover) {
      inlineProfilePopover.remove();
      inlineProfilePopover = null;
    }
  }

  function positionInlineProfilePopover(anchor, popover) {
    const padding = 8;
    const pane = els.messages;
    const bounds = pane?.getBoundingClientRect() || {
      left: padding,
      top: padding,
      right: window.innerWidth - padding,
      bottom: window.innerHeight - padding,
    };
    const anchorRect = anchor.getBoundingClientRect();
    popover.style.visibility = "hidden";
    popover.style.left = "0px";
    popover.style.top = "0px";
    document.body.appendChild(popover);
    const popoverRect = popover.getBoundingClientRect();
    let left = anchorRect.right + padding;
    let top = anchorRect.top;
    if (left + popoverRect.width > bounds.right) {
      left = anchorRect.left - popoverRect.width - padding;
    }
    if (left < bounds.left) {
      left = Math.min(Math.max(bounds.left, anchorRect.left), bounds.right - popoverRect.width);
      top = anchorRect.bottom + padding;
    }
    if (top + popoverRect.height > bounds.bottom) {
      top = Math.max(bounds.top, bounds.bottom - popoverRect.height);
    }
    if (top < bounds.top) top = bounds.top;
    if (left + popoverRect.width > bounds.right) {
      left = bounds.right - popoverRect.width;
    }
    if (left < bounds.left) left = bounds.left;
    popover.style.left = `${Math.round(left)}px`;
    popover.style.top = `${Math.round(top)}px`;
    popover.style.visibility = "visible";
  }

  function openInlineProfileForMessage(message, anchor) {
    if (!message || !anchor) return;
    closeInlineProfilePopover();
    const limited = !message.author_profile;
    const profileUser = message.author_profile || {
      id: message.user_id || "",
      name: message.author_name || "Nest User",
      username: message.author_username || "",
      picture_url: message.author_avatar_url,
      handle: message.author_username ? `@${message.author_username}` : `@${message.author_name || "nest-user"}`,
    };
    const popover = document.createElement("div");
    popover.className = `chat-inline-profile-popover${limited ? " is-limited" : ""}`;
    popover.innerHTML = `
      <button class="chat-inline-profile-close" type="button" data-close-inline-profile aria-label="Close profile">
        <span class="material-symbols-outlined" aria-hidden="true">close</span>
      </button>
      ${profileMarkup(profileUser, { showBlock: false, status: "offline" })}
    `;
    positionInlineProfilePopover(anchor, popover);
    inlineProfilePopover = popover;
    popover.querySelector("[data-close-inline-profile]")?.focus({ preventScroll: true });
  }

  function stickToBottom() {
    if (!els.messages) return;
    els.messages.scrollTop = els.messages.scrollHeight;
  }

  function messageStackElement() {
    if (!els.messages) return null;
    if (els.messages.querySelector(".chat-message-loader")) {
      els.messages.innerHTML = `<div class="chat-message-stack" ${GRAMMARLY_DISABLED_ATTRS}></div>`;
    }
    let stack = els.messages.querySelector(".chat-message-stack");
    if (!stack) {
      els.messages.innerHTML = `<div class="chat-message-stack" ${GRAMMARLY_DISABLED_ATTRS}></div>`;
      stack = els.messages.querySelector(".chat-message-stack");
    }
    return stack;
  }

  function renderedMessageIds() {
    const ids = new Set();
    if (!els.messages) return ids;
    for (const node of els.messages.querySelectorAll("[data-message-id]")) {
      const id = node.getAttribute("data-message-id");
      if (id) ids.add(id);
    }
    return ids;
  }

  function messageElementById(messageId) {
    if (!els.messages || !messageId) return null;
    return els.messages.querySelector(`[data-message-id="${String(messageId).replace(/\\/g, "\\\\").replace(/"/g, '\\"')}"]`);
  }

  function appendNewMessagesToDom(newMessages, allMessages) {
    if (!newMessages?.length) return true;
    const stack = messageStackElement();
    if (!stack) return false;

    const renderedIds = renderedMessageIds();
    const unseen = newMessages.filter((message) => message?.id && !renderedIds.has(String(message.id)));
    if (!unseen.length) return true;

    const empty = stack.querySelector(".chat-empty");
    if (empty) stack.innerHTML = "";

    const articles = stack.querySelectorAll("[data-message-id]");
    const lastArticle = articles[articles.length - 1];
    let lastMessage = null;
    let lastGroupEl = stack.querySelector(".chat-message-group:last-child");
    if (lastArticle) {
      const lastId = lastArticle.getAttribute("data-message-id");
      lastMessage = (allMessages || []).find((message) => String(message.id) === String(lastId)) || null;
    }

    for (const message of unseen) {
      if (lastMessage && shouldGroupMessage(lastMessage, message) && lastGroupEl) {
        lastGroupEl.insertAdjacentHTML("beforeend", renderContinuationMessage(message));
      } else {
        stack.insertAdjacentHTML("beforeend", renderMessageGroup({ id: message.id, messages: [message] }));
        lastGroupEl = stack.querySelector(".chat-message-group:last-child");
      }
      lastMessage = message;
    }
    return true;
  }

  function patchMessageInDom(message) {
    const el = messageElementById(message.id);
    if (!el) return false;
    const contentHtml = `
      <div class="chat-message-body" ${GRAMMARLY_DISABLED_ATTRS}>${message.rendered_html || escapeHtml(message.content || "")}</div>
      ${renderImages(message.images || [])}
      ${extensions.messageMedia?.renderAttachments?.(message.attachments || []) || ""}
      ${extensions.messageMedia?.renderGif?.(message.gif) || ""}
      ${renderPreviews(message.previews || [])}
    `;
    const isContinuation = el.classList.contains("chat-message-continuation");
    const content = el.querySelector(".chat-message-content");
    if (!content) return false;
    if (isContinuation) {
      content.innerHTML = contentHtml;
      content.querySelector(".chat-message-body")?.insertAdjacentHTML("afterend", renderDeliveryState(message));
    } else {
      const head = content.querySelector(".chat-message-head");
      content.innerHTML = head ? head.outerHTML : "";
      content.insertAdjacentHTML("beforeend", contentHtml);
      const nextHead = content.querySelector(".chat-message-head");
      nextHead?.querySelector(".chat-delivery-state, .chat-message-retry")?.remove();
      nextHead?.insertAdjacentHTML("beforeend", renderDeliveryState(message));
    }
    const deleteButton = renderDeleteButton(message);
    const existingDelete = el.querySelector(".chat-delete");
    if (deleteButton && !existingDelete) {
      el.insertAdjacentHTML("beforeend", deleteButton);
    } else if (!deleteButton && existingDelete) {
      existingDelete.remove();
    }
    return true;
  }

  function removeMessageFromDom(messageId) {
    const el = messageElementById(messageId);
    if (!el) return false;
    const group = el.closest(".chat-message-group");
    el.remove();
    if (group && !group.querySelector("[data-message-id]")) {
      group.remove();
    }
    const stack = messageStackElement();
    if (stack && !stack.querySelector("[data-message-id]") && !stack.querySelector(".chat-empty")) {
      stack.innerHTML = `<div class="chat-empty" ${GRAMMARLY_DISABLED_ATTRS}>No messages yet.</div>`;
    }
    return true;
  }

  function syncMessagesToDom(messages, { incremental = false, incoming = [], scrollToBottom = false } = {}) {
    if (!els.messages) return;
    if (incremental && messages?.length) {
      if (appendNewMessagesToDom(incoming, messages)) {
        updateAnnouncementsUnreadBanner(messages);
        updateHistoryBannerVisibility();
        if (scrollToBottom) stickToBottom();
        return;
      }
    }
    renderMessages(messages);
    if (scrollToBottom) stickToBottom();
  }

  function applyIncomingMessages(room, messages, { toBottom = false, markRead = true } = {}) {
    const cache = cacheFor(room);
    if (!cache || !messages?.length) return [];
    const previousMessages = cache.messages;
    const wasNearBottom = toBottom || isNearBottom();
    cache.messages = mergeMessages(cache.messages, messages);
    cache.loaded = true;
    cache.stale = false;
    updateCacheCursors(cache);
    schedulePersistentRoomSave(room);
    const incoming = messages.filter((message) => message?.id && !previousMessages.some((row) => row.id === message.id));
    syncMessagesToDom(cache.messages, {
      incremental: true,
      incoming,
      scrollToBottom: wasNearBottom,
    });
    if (wasNearBottom && markRead) markRoomRead(room, cache);
    if (!wasNearBottom && incoming.length && els.newMessages) els.newMessages.hidden = false;
    return messages;
  }

  async function fetchMessageById(messageId) {
    if (!messageId) return null;
    try {
      const payload = await fetchJson(`/api/chat/messages/${encodeURIComponent(messageId)}`);
      return payload?.message || null;
    } catch (_) {
      return null;
    }
  }

  async function ingestMessageUpdate(event) {
    const room = state.activeRoom;
    const cache = cacheFor(room);
    if (!room || !cache) return false;
    if (event.message_id) {
      const message = await fetchMessageById(event.message_id);
      if (message) {
        cache.messages = mergeMessages(cache.messages, [message]);
        updateCacheCursors(cache);
        schedulePersistentRoomSave(room);
        if (patchMessageInDom(message)) {
          updateAnnouncementsUnreadBanner(cache.messages);
          return true;
        }
      }
    }
    await loadMessages({ force: true, quiet: true, preserveScroll: true, light: true });
    return false;
  }

  async function ingestActiveRoomMessage(event) {
    const room = state.activeRoom;
    const cache = cacheFor(room);
    if (!room || !cache) return false;

    if (event.message_id && !cache.messages.some((message) => message.id === event.message_id)) {
      const message = await fetchMessageById(event.message_id);
      if (message) {
        applyIncomingMessages(room, [message]);
        playChatSound(event.actor_id);
        return true;
      }
    }

    const delta = deltaLoadParams(cache);
    const incoming = delta.after
      ? await loadMessages({ ...delta, quiet: true, force: true, light: true })
      : await loadMessages({ force: true, quiet: true });
    if (!incoming.length && event.message_id) {
      await loadMessages({ force: true, quiet: true });
    } else if (incoming.length) {
      playChatSound(event.actor_id);
      return true;
    }
    return incoming.length > 0;
  }

  function pollActiveRoomMessages() {
    const room = state.activeRoom;
    const cache = cacheFor(room);
    if (!room || !cache) return;
    const delta = deltaLoadParams(cache);
    if (delta.after) {
      void loadMessages({ ...delta, quiet: true, force: true, light: true });
    } else {
      void loadMessages({ force: true, quiet: true });
    }
  }

  function startRealtimeFallback() {
    if (chatRuntimePaused || chatRuntimeDisposed || realtimeFallbackTimer || state.realtimeReady) return;
    realtimeFallbackTimer = window.setInterval(() => {
      if (document.visibilityState !== "visible") return;
      if (state.realtimeReady) {
        stopRealtimeFallback();
        return;
      }
      pollActiveRoomMessages();
      void refreshChatSummary();
    }, REALTIME_FALLBACK_MS);
  }

  function stopRealtimeFallback() {
    if (!realtimeFallbackTimer) return;
    window.clearInterval(realtimeFallbackTimer);
    realtimeFallbackTimer = null;
  }

  function startRealtimeHeartbeat() {
    if (chatRuntimePaused || chatRuntimeDisposed || realtimeHeartbeatTimer) return;
    realtimeHeartbeatTimer = window.setInterval(() => {
      if (document.visibilityState !== "visible" || !state.realtimeReady) return;
      pollActiveRoomMessages();
    }, REALTIME_HEARTBEAT_MS);
  }

  function stopRealtimeHeartbeat() {
    if (!realtimeHeartbeatTimer) return;
    window.clearInterval(realtimeHeartbeatTimer);
    realtimeHeartbeatTimer = null;
  }

  function resetRealtimeConnection() {
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
    if (chatRuntimePaused || chatRuntimeDisposed || realtimeReconnectTimer) return;
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
    if (chatRuntimePaused || chatRuntimeDisposed) return;
    initializeChatEventStream();
    void loadInitialPresences();
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

  function renderApprovalNotice(channel) {
    setStatus(null);
    if (els.messages) {
      const denied = channel?.university_status === "denied";
      els.messages.innerHTML = `
        <div class="chat-empty chat-approval-state" ${GRAMMARLY_DISABLED_ATTRS}>
          <strong>${denied ? "University Channel Denied." : "Waiting Admin Approval."}</strong>
          <span>Email derek.chen@emory.edu for faster approval.</span>
        </div>
      `;
    }
    renderMembers([]);
  }

  function renderMessages(messages) {
    if (!els.messages) return;
    if (!messages || !messages.length) {
      els.messages.innerHTML = `<div class="chat-message-stack" ${GRAMMARLY_DISABLED_ATTRS}><div class="chat-empty" ${GRAMMARLY_DISABLED_ATTRS}>No messages yet.</div></div>`;
      updateAnnouncementsUnreadBanner([]);
      updateHistoryBannerVisibility();
      return;
    }
    els.messages.innerHTML = `
      <div class="chat-message-stack" ${GRAMMARLY_DISABLED_ATTRS}>
        ${renderMessageGroupsWithDays(groupMessages(messages))}
      </div>
    `;
    updateAnnouncementsUnreadBanner(messages);
    updateHistoryBannerVisibility();
  }

  function renderMessageGroupsWithDays(groups) {
    let previousDay = "";
    return groups.map((group) => {
      const date = parseMessageDate(group.messages?.[0]?.created_at);
      const day = localDateKey(date);
      const separator = day && day !== previousDay
        ? `<div class="chat-day-separator" role="separator"><span>${escapeHtml(date.toLocaleDateString([], { weekday: "long", month: "short", day: "numeric" }))}</span></div>`
        : "";
      previousDay = day;
      return `${separator}${renderMessageGroup(group)}`;
    }).join("");
  }

  function renderMessageGroup(group) {
    const [lead, ...continuations] = group.messages;
    return `
      <div class="chat-message-group" data-message-group="${escapeHtml(group.id)}" ${GRAMMARLY_DISABLED_ATTRS}>
        ${renderLeadMessage(lead)}
        ${continuations.map(renderContinuationMessage).join("")}
      </div>
    `;
  }

  function renderDeleteButton(message) {
    if (!message.can_delete) return "";
    return `
      <button class="chat-delete" type="button" data-delete-message="${escapeHtml(message.id)}" aria-label="Delete message" ${GRAMMARLY_DISABLED_ATTRS}>
        <span class="material-symbols-outlined" aria-hidden="true">delete</span>
      </button>
    `;
  }

  function renderDeliveryState(message) {
    if (message.delivery_state === "sending") return `<span class="chat-delivery-state">Sending…</span>`;
    if (message.delivery_state === "failed") return `<button class="chat-message-retry" type="button" data-retry-message="${escapeHtml(message.id)}">Not sent · Retry</button>`;
    return "";
  }

  function renderLeadMessage(message) {
    const deleteButton = message.can_delete
      ? renderDeleteButton(message)
      : "";
    return `
      <article class="chat-message" data-message-id="${escapeHtml(message.id)}" ${GRAMMARLY_DISABLED_ATTRS}>
        <button type="button" class="chat-message-avatar-button chat-author-button" data-author-message-id="${escapeHtml(message.id)}" aria-label="View ${escapeHtml(message.author_name || "author")} profile" ${GRAMMARLY_DISABLED_ATTRS}>
          <img class="chat-message-avatar" ${avatarAttrs(message.author_avatar_url, 84, "42px")} alt="">
        </button>
        <div class="chat-message-content" ${GRAMMARLY_DISABLED_ATTRS}>
          <div class="chat-message-head" ${GRAMMARLY_DISABLED_ATTRS}>
            <button type="button" class="chat-author-button" data-author-message-id="${escapeHtml(message.id)}" ${GRAMMARLY_DISABLED_ATTRS}>${escapeHtml(message.author_name || "Nest User")}</button>
            <span class="chat-message-time">${escapeHtml(formatMessageTimestamp(message.created_at))}</span>
            ${renderDeliveryState(message)}
          </div>
          <div class="chat-message-body" ${GRAMMARLY_DISABLED_ATTRS}>${message.rendered_html || escapeHtml(message.content || "")}</div>
          ${renderImages(message.images || [])}
          ${extensions.messageMedia?.renderAttachments?.(message.attachments || []) || ""}
          ${extensions.messageMedia?.renderGif?.(message.gif) || ""}
          ${renderPreviews(message.previews || [])}
        </div>
        ${deleteButton}
      </article>
    `;
  }

  function renderContinuationMessage(message) {
    return `
      <article class="chat-message chat-message-continuation" data-message-id="${escapeHtml(message.id)}" ${GRAMMARLY_DISABLED_ATTRS}>
        <span class="chat-message-continuation-time">${escapeHtml(formatClockTime(parseMessageDate(message.created_at)))}</span>
        <div class="chat-message-content" ${GRAMMARLY_DISABLED_ATTRS}>
          <div class="chat-message-body" ${GRAMMARLY_DISABLED_ATTRS}>${message.rendered_html || escapeHtml(message.content || "")}</div>
          ${renderDeliveryState(message)}
          ${renderImages(message.images || [])}
          ${extensions.messageMedia?.renderAttachments?.(message.attachments || []) || ""}
          ${extensions.messageMedia?.renderGif?.(message.gif) || ""}
          ${renderPreviews(message.previews || [])}
        </div>
        ${renderDeleteButton(message)}
      </article>
    `;
  }

  function renderImages(images) {
    if (!images.length) return "";
    return `
      <div class="chat-message-images" ${GRAMMARLY_DISABLED_ATTRS}>
        ${images.map((image) => `
          <img
            class="chat-message-image"
            src="${escapeHtml(image.proxy_url || image.url)}"
            alt="${escapeHtml(image.filename || "Discord image")}"
            loading="lazy"
            decoding="async"
            sizes="(max-width: 640px) 92vw, 520px"
          >
        `).join("")}
      </div>
    `;
  }

  function renderPreviews(previews) {
    if (!previews.length) return "";
    return previews.map((preview) => {
      const image = preview.image_url
        ? `<img src="${escapeHtml(preview.image_url)}" alt="" loading="lazy" decoding="async" sizes="(max-width: 640px) 92vw, 128px">`
        : "";
      return `
        <div class="chat-preview" ${GRAMMARLY_DISABLED_ATTRS}>
          <a href="${escapeHtml(preview.url || "#")}" target="_blank" rel="noopener noreferrer nofollow">
            <span class="chat-preview-copy" ${GRAMMARLY_DISABLED_ATTRS}>
              <strong>${escapeHtml(preview.title || preview.site_name || preview.url || "Link")}</strong>
              ${preview.site_name ? `<span>${escapeHtml(preview.site_name)}</span>` : ""}
              ${preview.description ? `<p>${escapeHtml(preview.description)}</p>` : ""}
            </span>
            ${image}
          </a>
        </div>
      `;
    }).join("");
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

  function updateCurrentMembersFromPayload(payload) {
    if (payload.thread && state.activeRoom?.type === "thread" && state.activeRoom.id === payload.thread.id) {
      renderDmProfile(payload.thread);
    }
    renderPresenceDrivenUi();
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

  function normalizeLocalPresenceStatus(value) {
    return ["active", "busy", "focus", "offline"].includes(value) ? value : "offline";
  }

  function rememberPresenceUser(user) {
    if (!user?.id) return;
    const normalized = {
      ...user,
      id: String(user.id),
      presence_status: normalizeLocalPresenceStatus(user.presence_status),
      active_chat_scopes: Array.isArray(user.active_chat_scopes) ? user.active_chat_scopes.map(String) : [],
      typing_channel_ids: Array.isArray(user.typing_channel_ids) ? user.typing_channel_ids.map(String) : [],
      typing_thread_ids: Array.isArray(user.typing_thread_ids) ? user.typing_thread_ids.map(String) : [],
    };
    state.presenceRecords.set(normalized.id, normalized);
    registerKnownUser(normalized);
  }

  function updatePresenceStatus(userId, status) {
    const id = String(userId || "");
    if (!id) return;
    const normalizedStatus = normalizeLocalPresenceStatus(status);
    const existing = state.presenceRecords.get(id) || state.knownUsers.get(id) || { id };
    rememberPresenceUser({
      ...existing,
      id,
      presence_status: normalizedStatus,
      online: normalizedStatus !== "offline",
    });
  }

  function presenceStatusForUser(userId, fallback = "offline") {
    const record = state.presenceRecords.get(String(userId || ""));
    return normalizeLocalPresenceStatus(record?.presence_status || fallback);
  }

  function usersForPresenceScope(scopeType, scopeId) {
    void scopeType;
    const id = String(scopeId || "");
    return Array.from(state.presenceRecords.values())
      .filter((user) => (user.active_chat_scopes || []).includes(id))
      .sort((a, b) => String(a.name || "").localeCompare(String(b.name || "")));
  }

  function typingUsersForActiveRoom() {
    const room = state.activeRoom;
    if (!room?.id) return [];
    const field = room.type === "channel" ? "typing_channel_ids" : "typing_thread_ids";
    return Array.from(state.presenceRecords.values())
      .filter((user) => user.id !== currentUserId())
      .filter((user) => (user[field] || []).includes(String(room.id)))
      .map((user) => state.knownUsers.get(user.id) || user);
  }

  function removePresenceScope(field, scopeId) {
    const id = String(scopeId || "");
    if (!id) return;
    for (const user of state.presenceRecords.values()) {
      if (!Array.isArray(user[field])) continue;
      user[field] = user[field].filter((value) => String(value) !== id);
    }
  }

  function renderTypingIndicator() {
    if (!els.typing) return;
    const users = typingUsersForActiveRoom();
    if (!users.length) {
      els.typing.hidden = true;
      els.typing.textContent = "";
      return;
    }
    const names = users.map((user) => user.name || user.username || "Someone");
    let label = "Several people are typing...";
    if (names.length === 1) label = `${names[0]} is typing...`;
    if (names.length === 2) label = `${names[0]} and ${names[1]} are typing...`;
    els.typing.hidden = false;
    els.typing.textContent = label;
  }

  function renderPresenceDrivenUi() {
    registerKnownUsersFromState();
    for (const channel of state.channels) {
      const users = (channel.online_users || channel.active_users || []).map((user) => {
        const status = presenceStatusForUser(user.id, user.presence_status || (user.online ? "active" : "offline"));
        return {
          ...user,
          presence_status: status,
          online: status !== "offline",
        };
      }).filter((user) => user.online);
      channel.online_users = users;
      channel.online_count = users.length;
      channel.active_users = users;
      channel.active_count = users.length;
      for (const user of users) registerKnownUser(user);
    }
    for (const thread of state.threads) {
      const other = thread.other_user || {};
      const status = presenceStatusForUser(other.id, other.presence_status || thread.presence_status);
      thread.presence_status = status;
      other.presence_status = status;
      other.online = status !== "offline";
      const scope = thread.presence_scope || { scope_type: "thread", scope_id: thread.id };
      thread.active_count = usersForPresenceScope(scope.scope_type, scope.scope_id).length;
    }
    updateRoomLists();
    renderHeader();
    const channel = activeChannel();
    const thread = activeThread();
    if (thread) {
      renderDmProfile(thread);
    } else if (channel && !channelIsPending(channel)) {
      const users = channel.online_users || channel.active_users || [];
      const activeProfile = state.activeProfile?.id
        ? users.find((user) => user.id === state.activeProfile.id)
        : null;
      if (activeProfile) showMemberProfile(activeProfile, { preserveFocus: true });
      else renderMembers(users);
    }
    renderTypingIndicator();
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

  function initializeChatEventStream() {
    if (chatRuntimePaused || chatRuntimeDisposed) return;
    if (state.realtimeReady || chatEventSource || state.realtimeConnecting) return;
    if (typeof window.EventSource !== "function") {
      startRealtimeFallback();
      return;
    }
    state.realtimeConnecting = true;
    try {
      const source = new EventSource(buildChatEventsStreamUrl());
      chatEventSource = source;
      source.onopen = () => {
        if (chatRuntimePaused || chatRuntimeDisposed) {
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
        pollActiveRoomMessages();
      };
      source.onmessage = (messageEvent) => {
        if (chatRuntimePaused || chatRuntimeDisposed) return;
        let payload;
        try {
          payload = JSON.parse(messageEvent.data);
        } catch {
          return;
        }
        const eventId = payload?.$id || payload?.id;
        if (eventId && seenChatEventIds.has(eventId)) return;
        if (eventId) seenChatEventIds.add(eventId);
        if (payload?.created_at) {
          chatEventCursor = { since: payload.created_at, after_id: eventId || null };
        }
        void handleRealtimePayload({ payload });
      };
      source.onerror = () => {
        if (chatRuntimePaused || chatRuntimeDisposed) return;
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
    const active = state.activeRoom;
    const eventRoom = event.scope_type === "channel"
      ? { type: "channel", id: event.scope_id || event.channel_id }
      : event.scope_type === "thread"
        ? { type: "thread", id: event.scope_id || event.thread_id }
        : null;

    if (event.event_type === "message_deleted") {
      removeMessageFromCaches(event.message_id);
      void refreshChatSummary();
      return;
    }

    if (event.event_type === "message_created") {
      if (event.message_id && !rememberChatMessageId(event.message_id)) return;
      if (eventRoom?.type === "thread" && !threadExists(eventRoom.id)) {
        const thread = await fetchThread(eventRoom.id);
        if (!thread) {
          await bootstrap({ preserveActive: true });
        }
      }
      if (eventRoom && active && roomKey(eventRoom) === roomKey(active)) {
        await ingestActiveRoomMessage(event);
      } else if (eventRoom) {
        markRoomStale(eventRoom);
        scheduleUnreadSummaryRefresh();
        playChatSound(event.actor_id);
      }
      return;
    }

    if (event.event_type === "message_updated") {
      if (eventRoom && active && roomKey(eventRoom) === roomKey(active)) {
        await ingestMessageUpdate(event);
      } else if (eventRoom) {
        markRoomStale(eventRoom);
      }
      return;
    }

    if (["thread_updated", "block_updated", "university_approved", "university_denied"].includes(event.event_type)) {
      if (eventRoom) markRoomStale(eventRoom);
      if (event.event_type === "thread_updated" && eventRoom?.type === "thread") {
        const thread = await fetchThread(eventRoom.id);
        if (thread) {
          void refreshChatSummary();
          return;
        }
      }
      await bootstrap({ preserveActive: true });
    }
  }

  async function loadInitialPresences() {
    try {
      const payload = await fetchJson("/api/presence/online");
      state.presenceRecords.clear();
      for (const user of payload.users || []) rememberPresenceUser(user);
      renderPresenceDrivenUi();
    } catch (error) {
      console.warn("Unable to load presence", error);
      renderPresenceDrivenUi();
    }
  }

  function visiblePresenceUserIds() {
    const ids = [];
    const add = (value) => {
      const id = String(value || "");
      if (id && id !== currentUserId() && !ids.includes(id)) ids.push(id);
    };
    for (const thread of state.threads || []) {
      add(thread.other_user?.id);
    }
    add(state.activeProfile?.id);
    const thread = activeThread();
    add(thread?.other_user?.id);
    return ids.slice(0, 200);
  }

  async function refreshPresenceStatuses() {
    const userIds = visiblePresenceUserIds();
    if (!userIds.length) return;
    try {
      const payload = await fetchJson("/api/presence/statuses", {
        method: "POST",
        body: JSON.stringify({ user_ids: userIds }),
      });
      for (const [userId, status] of Object.entries(payload.statuses || {})) {
        updatePresenceStatus(userId, status);
      }
    } catch (error) {
      console.warn("Unable to refresh presence statuses", error);
    }
  }

  async function refreshActiveRoomPresence() {
    const room = state.activeRoom;
    if (!room?.id || !["channel", "thread"].includes(room.type)) return;
    try {
      const payload = await fetchJson("/api/presence/room", {
        method: "POST",
        body: JSON.stringify({ scope_type: room.type, scope_id: room.id }),
      });
      if (!state.activeRoom || roomKey(state.activeRoom) !== roomKey(room)) return;
      const typingField = room.type === "channel" ? "typing_channel_ids" : "typing_thread_ids";
      removePresenceScope("active_chat_scopes", room.id);
      removePresenceScope(typingField, room.id);
      const roomUsers = payload.online_users || payload.active_users || [];
      const channel = room.type === "channel" ? state.channels.find((candidate) => candidate.id === room.id) : null;
      if (channel) {
        channel.online_users = roomUsers;
        channel.online_count = roomUsers.length;
        channel.active_users = roomUsers;
        channel.active_count = roomUsers.length;
      }
      for (const user of roomUsers) {
        const status = normalizeLocalPresenceStatus(user.presence_status || "active");
        rememberPresenceUser({
          ...user,
          presence_status: status,
          online: status !== "offline",
          active_chat_scopes: status === "active" ? [String(room.id)] : [],
        });
      }
      for (const user of payload.typing_users || []) {
        rememberPresenceUser({
          ...user,
          [typingField]: [String(room.id)],
        });
      }
    } catch (error) {
      console.warn("Unable to refresh room presence", error);
    }
  }

  function syncActiveRoomHeartbeat() {
    const coordinator = window.APStudyPresenceHeartbeat;
    if (!coordinator?.setChatRoom) return;
    coordinator.setChatRoom(state.activeRoom?.id || null);
  }

  async function refreshTargetedPresences() {
    syncActiveRoomHeartbeat();
    await Promise.all([refreshPresenceStatuses(), refreshActiveRoomPresence()]);
    renderPresenceDrivenUi();
  }

  function heartbeatPayload(kind, room = state.activeRoom) {
    if (kind === "typing") {
      if (!room?.id || !["channel", "thread"].includes(room.type)) return null;
      return {
        scope_type: room.type === "channel" ? "typing_channel" : "typing_thread",
        scope_id: room.id,
        tab_id: currentTabId(),
      };
    }
    return null;
  }

  async function sendPresenceHeartbeat(kind, room = state.activeRoom) {
    if (kind === "viewing") {
      syncActiveRoomHeartbeat();
      return null;
    }
    const payload = heartbeatPayload(kind, room);
    if (!payload) return null;
    try {
      await fetchJson("/api/presence/heartbeat", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      return `${payload.scope_type}:${payload.scope_id}`;
    } catch (error) {
      console.warn("Unable to update chat presence", error);
      return null;
    }
  }

  function clearTypingPresence() {
    window.clearTimeout(state.typingInputTimer);
    window.clearTimeout(state.typingClearTimer);
    state.typingInputTimer = null;
    state.typingClearTimer = null;
  }

  function refreshViewingPresence() {
    void refreshTargetedPresences();
  }

  function handleActiveRoomPresenceChange(previousRoom) {
    if (previousRoom && roomKey(previousRoom) !== roomKey(state.activeRoom)) {
      clearTypingPresence();
    }
    refreshViewingPresence();
  }

  function scheduleTypingPresence() {
    const channel = activeChannel();
    const thread = activeThread();
    if (!els.input || !els.input.value.trim()) {
      clearTypingPresence();
      return;
    }
    if ((channel && !channelIsWritable(channel)) || thread?.blocked) return;
    window.clearTimeout(state.typingInputTimer);
    state.typingInputTimer = window.setTimeout(() => {
      void sendPresenceHeartbeat("typing");
      window.clearTimeout(state.typingClearTimer);
      state.typingClearTimer = window.setTimeout(() => clearTypingPresence(), TYPING_PRESENCE_TTL_MS + 400);
    }, 150);
  }

  function startPresenceRefreshTimer() {
    if (chatRuntimePaused || chatRuntimeDisposed) return;
    window.clearInterval(state.presenceRefreshTimer);
    refreshViewingPresence();
    state.presenceRefreshTimer = window.setInterval(refreshViewingPresence, PRESENCE_REFRESH_MS);
  }

  function stopPresenceRefreshTimer() {
    window.clearInterval(state.presenceRefreshTimer);
    state.presenceRefreshTimer = null;
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

  async function deleteMessage(messageId) {
    if (!messageId) return;
    const ok = window.APStudyConfirm
      ? await window.APStudyConfirm.request({
        title: "Delete message?",
        message: "Messages can only be deleted within 5 minutes of sending.",
        acceptLabel: "Delete",
        danger: true,
      })
      : window.confirm("Delete this message?");
    if (!ok) return;
    const removed = removeMessageFromCaches(messageId);
    if (window.APStudyUndo?.stage) {
      window.APStudyUndo.stage({
        message: "Message deleted.",
        duration: 8_000,
        commit: ({ reason }) => fetchJson(`/api/chat/messages/${encodeURIComponent(messageId)}`, {
          method: "DELETE",
          keepalive: reason === "pagehide",
        }),
        restore: () => restoreMessagesToCaches(removed),
        errorTitle: "Couldn’t delete message",
      });
      return;
    }
    try {
      await fetchJson(`/api/chat/messages/${encodeURIComponent(messageId)}`, { method: "DELETE" });
    } catch (error) {
      restoreMessagesToCaches(removed);
      setStatus(error.message || "Unable to delete message.", "error");
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
    window.clearTimeout(realtimeReconnectTimer);
    realtimeReconnectTimer = null;
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
    els.messages?.addEventListener("scroll", () => {
      const cache = cacheFor(state.activeRoom);
      if (cache) cache.scrollTop = els.messages.scrollTop;
      updateHistoryBannerVisibility();
      window.clearTimeout(state.scrollSaveTimer);
      state.scrollSaveTimer = window.setTimeout(() => {
        schedulePersistentRoomSave(state.activeRoom);
      }, 350);
      if (els.messages.scrollTop <= 16 && cache?.hasMore && cache.oldestCursor && !state.loadingMessages) {
        void loadMessages({ before: cache.oldestCursor, preserveScroll: true, quiet: true });
      }
      closeInlineProfilePopover();
      if (isNearBottom() && els.newMessages) els.newMessages.hidden = true;
    });
    els.messages?.addEventListener("click", (event) => {
      const authorButton = event.target.closest("[data-author-message-id]");
      if (authorButton) {
        event.preventDefault();
        const messageId = authorButton.dataset.authorMessageId;
        const cache = cacheFor(state.activeRoom);
        const message = cache?.messages?.find((candidate) => candidate.id === messageId);
        if (message) openInlineProfileForMessage(message, authorButton);
        return;
      }
      const deleteButton = event.target.closest("[data-delete-message]");
      if (deleteButton) void deleteMessage(deleteButton.dataset.deleteMessage);
      const retryButton = event.target.closest("[data-retry-message]");
      if (retryButton) void retryMessage(retryButton.dataset.retryMessage);
    });
    els.newMessages?.addEventListener("click", () => {
      els.messages?.scrollTo({ top: els.messages.scrollHeight, behavior: "smooth" });
      els.newMessages.hidden = true;
    });
    els.memberList?.addEventListener("click", (event) => {
      const profileButton = event.target.closest("[data-profile-id]");
      if (!profileButton) return;
      const channel = activeChannel();
      const user = (channel?.online_users || channel?.active_users || []).find((candidate) => candidate.id === profileButton.dataset.profileId);
      if (user) {
        showMemberProfile(user);
      }
    });
    els.profileBack?.addEventListener("click", () => {
      const profileId = state.activeProfile?.id;
      state.activeProfile = null;
      const channel = activeChannel();
      renderMembers(channel?.online_users || channel?.active_users || []);
      if (!profileId) return;
      window.requestAnimationFrame(() => {
        els.memberList?.querySelector(`[data-profile-id="${CSS.escape(profileId)}"]`)?.focus({ preventScroll: true });
      });
    });
    els.profilePanel?.addEventListener("click", (event) => {
      const blockButton = event.target.closest("[data-block-user]");
      if (!blockButton) return;
      void toggleBlock(blockButton.dataset.blockUser, blockButton.dataset.blocked === "true");
    });
    els.announcementsRead?.addEventListener("click", () => {
      void markAnnouncementsRead();
    });
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
    document.addEventListener("click", (event) => {
      if (event.target.closest("[data-close-inline-profile]")) {
        closeInlineProfilePopover();
        return;
      }
      const menu = document.getElementById("chat-room-context-menu");
      if (menu && !menu.hidden && !menu.contains(event.target)) closeRoomContextMenu();
      if (inlineProfilePopover && !event.target.closest(".chat-inline-profile-popover") && !event.target.closest(".chat-author-button")) {
        closeInlineProfilePopover();
      }
    });
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
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") {
        const room = state.activeRoom;
        const cache = cacheFor(room);
        if (cache?.loaded) {
          if (cache.latestCursor) {
            const delta = deltaLoadParams(cache);
            void loadMessages({ ...delta, quiet: true, force: true, light: true })
              .finally(() => {
                markRoomRead(room, cache);
                void refreshChatSummary();
              });
          } else if (cache.stale) {
            void loadMessages({ force: true, quiet: true })
              .finally(() => {
                markRoomRead(room, cache);
                void refreshChatSummary();
              });
          } else {
            markRoomRead(room, cache);
            void refreshChatSummary();
          }
        } else {
          void refreshChatSummary();
        }
        refreshViewingPresence();
      } else {
        clearTypingPresence();
      }
    });
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
