import { avatarAttrs, avatarUrl, escapeHtml, plural } from "./presentation.js";
import { CHAT_CACHE_SCHEMA, createPersistentChatCache, deltaLoadParams, mergeMessages, roomCachePayload, trimMessagesForPersistentCache, updateCacheCursors } from "./cache.js";
import { createChatComposer } from "./composer.js";
import { createChatMessagesDom } from "./messages-dom.js";
import { createChatPresence } from "./presence.js";
import { createChatRealtime } from "./realtime.js";
import { createChatRooms } from "./rooms.js";

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
  let chatRuntimePaused = false;
  let chatRuntimeDisposed = false;
  let chatRequestController = new AbortController();
  let loadingMessagesToken = null;
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

  function latestMessageForRead(cache) {
    const messages = cache?.messages || [];
    for (let index = messages.length - 1; index >= 0; index -= 1) {
      const message = messages[index];
      if (message?.id) return message;
    }
    return null;
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

  async function loadMessages({ before = null, after = null, after_message_id = null, force = false, preserveScroll = false, quiet = false, light = false, signal = null, roomSelection = null } = {}) {
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
    const isCurrentRoomLoad = () => Boolean(
      state.activeRoom
      && roomKey(state.activeRoom) === roomKey(room)
      && (!roomSelection || roomSelection.isCurrent?.(state.activeRoom))
    );
    state.loadingMessages = true;
    const requestToken = Symbol("chat-message-load");
    loadingMessagesToken = requestToken;
    if (!quiet && !before && !after && !after_message_id && !cache.loaded) renderMessageLoader();

    try {
      const requestSignal = signal || roomSelection?.signal || null;
      const requestOptions = requestSignal ? { signal: requestSignal } : {};
      const payload = await fetchJson(currentRoomUrl(room, { before, after, after_message_id }), requestOptions);
      if (!isCurrentRoomLoad()) return [];

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
      if (error?.name !== "AbortError" && isCurrentRoomLoad()) {
        setStatus(error.message || "Unable to load messages.", "error");
      }
      return [];
    } finally {
      if (loadingMessagesToken === requestToken) {
        state.loadingMessages = false;
        loadingMessagesToken = null;
      }
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
    cancelRoomSelection();
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
    bindComposerEvents();
    bindMessagePaneEvents();
    bindRoomDmEvents();
    bindMessageDocumentEvents();
    bindRoomShellEvents();
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
  const rooms = createChatRooms(runtimeContext);
  const {
    activeChannel,
    activeThread,
    bindDmEvents: bindRoomDmEvents,
    bindShellEvents: bindRoomShellEvents,
    cancelUnreadSummaryRefresh,
    cancelRoomSelection,
    channelIsPending,
    channelIsWritable,
    clearRoomUnread,
    closeRoomContextMenu,
    fetchThread,
    markRoomRead,
    refreshChatSummary,
    renderHeader,
    renderThreads,
    scheduleUnreadSummaryRefresh,
    selectRoom,
    setMembersCollapsed,
    setRoomUnread,
    threadExists,
    toggleBlock,
    updateChannel,
    updateRoomLists,
    updateThread,
  } = rooms;
  const composer = createChatComposer(runtimeContext);
  const {
    bindEvents: bindComposerEvents,
    retryMessage,
    setComposer,
    updateComposerSubmitState,
  } = composer;
  Object.assign(actions, {
    applyIncomingMessages,
    bootstrap,
    cacheFor,
    channelIsPending,
    channelIsWritable,
    clearTypingPresence,
    cancelRoomSelection,
    closeInlineProfilePopover,
    closeRoomContextMenu,
    clearRoomUnread,
    currentRoomUrl,
    currentUserId,
    deltaLoadParams,
    dmPresenceMarkup,
    dmPresenceStatus,
    fetchThread,
    handleActiveRoomPresenceChange,
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
    scheduleTransientTimeout,
    scheduleTransientFrame,
    scheduleUnreadSummaryRefresh,
    threadExists,
    activeChannel,
    activeThread,
    renderDmProfile,
    renderHeader,
    renderMembers,
    renderApprovalNotice,
    renderMessageLoader,
    profileMarkup,
    registerKnownUser,
    showMemberProfile,
    setStatus,
    isNearBottom,
    retryMessage,
    saveActiveScroll,
    focusComposerSoon,
    hydrateRoomFromPersistentCache,
    renderCachedRoom,
    renderPresenceDrivenUi,
    schedulePersistentBootstrapSave,
    scheduleTypingPresence,
    setComposer,
    setHistoryBanner,
    memberTierBadgeMarkup,
    toggleBlock,
    unreadAnnouncementMessages,
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
