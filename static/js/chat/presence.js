export function createChatPresence(context) {
  const { root, state, els, config, lifecycle, actions } = context;
  const {
    PRESENCE_REFRESH_MS,
    TYPING_PRESENCE_TTL_MS,
    PRESENCE_TAB_ID_KEY,
  } = config;

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

  function normalizeLocalPresenceStatus(value) {
    return ["active", "busy", "focus", "offline"].includes(value) ? value : "offline";
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
      .filter((user) => user.id !== actions.currentUserId())
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
    actions.updateRoomLists();
    actions.renderHeader();
    const channel = actions.activeChannel();
    const thread = actions.activeThread();
    if (thread) {
      actions.renderDmProfile(thread);
    } else if (channel && !actions.channelIsPending(channel)) {
      const users = channel.online_users || channel.active_users || [];
      const activeProfile = state.activeProfile?.id
        ? users.find((user) => user.id === state.activeProfile.id)
        : null;
      if (activeProfile) actions.showMemberProfile(activeProfile, { preserveFocus: true });
      else actions.renderMembers(users);
    }
    renderTypingIndicator();
  }

  function updateCurrentMembersFromPayload(payload) {
    if (payload.thread && state.activeRoom?.type === "thread" && state.activeRoom.id === payload.thread.id) {
      actions.renderDmProfile(payload.thread);
    }
    renderPresenceDrivenUi();
  }

  async function loadInitialPresences() {
    try {
      const payload = await context.fetchJson("/api/presence/online");
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
      if (id && id !== actions.currentUserId() && !ids.includes(id)) ids.push(id);
    };
    for (const thread of state.threads || []) {
      add(thread.other_user?.id);
    }
    add(state.activeProfile?.id);
    const thread = actions.activeThread();
    add(thread?.other_user?.id);
    return ids.slice(0, 200);
  }

  async function refreshPresenceStatuses() {
    const userIds = visiblePresenceUserIds();
    if (!userIds.length) return;
    try {
      const payload = await context.fetchJson("/api/presence/statuses", {
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
      const payload = await context.fetchJson("/api/presence/room", {
        method: "POST",
        body: JSON.stringify({ scope_type: room.type, scope_id: room.id }),
      });
      if (!state.activeRoom || actions.roomKey(state.activeRoom) !== actions.roomKey(room)) return;
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
      await context.fetchJson("/api/presence/heartbeat", {
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
    if (previousRoom && actions.roomKey(previousRoom) !== actions.roomKey(state.activeRoom)) {
      clearTypingPresence();
    }
    refreshViewingPresence();
  }

  function scheduleTypingPresence() {
    const channel = actions.activeChannel();
    const thread = actions.activeThread();
    if (!els.input || !els.input.value.trim()) {
      clearTypingPresence();
      return;
    }
    if ((channel && !actions.channelIsWritable(channel)) || thread?.blocked) return;
    window.clearTimeout(state.typingInputTimer);
    state.typingInputTimer = window.setTimeout(() => {
      void sendPresenceHeartbeat("typing");
      window.clearTimeout(state.typingClearTimer);
      state.typingClearTimer = window.setTimeout(() => clearTypingPresence(), TYPING_PRESENCE_TTL_MS + 400);
    }, 150);
  }

  function startPresenceRefreshTimer() {
    if (lifecycle.paused || lifecycle.disposed) return;
    window.clearInterval(state.presenceRefreshTimer);
    refreshViewingPresence();
    state.presenceRefreshTimer = window.setInterval(refreshViewingPresence, PRESENCE_REFRESH_MS);
  }

  function stopPresenceRefreshTimer() {
    window.clearInterval(state.presenceRefreshTimer);
    state.presenceRefreshTimer = null;
  }

  return {
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
  };
}
