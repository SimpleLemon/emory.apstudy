import { avatarAttrs, escapeHtml } from "./presentation.js";

export function createRoomSelectionCoordinator() {
  let generation = 0;
  let activeController = null;

  function begin(room) {
    activeController?.abort();
    const controller = new AbortController();
    const selection = {
      room,
      generation: ++generation,
      signal: controller.signal,
      controller,
    };
    selection.isCurrent = (activeRoom) => isCurrent(selection, activeRoom);
    activeController = controller;
    return selection;
  }

  function isCurrent(selection, activeRoom) {
    return Boolean(
      selection
      && selection.generation === generation
      && activeController === selection.controller
      && selection.room?.type === activeRoom?.type
      && selection.room?.id === activeRoom?.id
      && !selection.signal.aborted
    );
  }

  function cancel() {
    activeController?.abort();
    activeController = null;
    generation += 1;
  }

  return { begin, cancel, isCurrent };
}

export function createChatRooms(context) {
  const { root, state, els, extensions, config, actions } = context;
  const { ANNOUNCEMENTS_CHANNEL_ID, GRAMMARLY_DISABLED_ATTRS } = config;
  let unreadSummaryRefreshTimer = null;
  const roomSelection = createRoomSelectionCoordinator();

  function unreadKey(type, id) {
    return actions.roomKey({ type, id });
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
      const payload = await context.fetchJson("/api/chat/summary", {
        headers: { Accept: "application/json" },
      });
      if (state.localReadSeq !== startReadSeq) {
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
    const key = actions.roomKey(room);
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
    const key = actions.roomKey(room);
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

  function shouldAutoMarkRoomRead(room, cache = actions.cacheFor(room)) {
    if (room?.type === "channel" && room.id === ANNOUNCEMENTS_CHANNEL_ID) {
      return actions.unreadAnnouncementMessages(cache?.messages, state.roomReadState).length === 0;
    }
    return true;
  }

  function markRoomRead(room, cache = actions.cacheFor(room), { force = false } = {}) {
    if (!room?.type || !room?.id) return;
    if (!force && document.visibilityState === "hidden") return;
    if (!force && !shouldAutoMarkRoomRead(room, cache)) return;
    if (force) {
      clearRoomUnread(room);
      cancelUnreadSummaryRefresh();
      state.localReadSeq += 1;
    }
    const latest = actions.latestMessageForRead(cache);
    const body = {
      scope_type: room.type === "channel" ? "channel" : "thread",
      scope_id: room.id,
    };
    if (!force && latest?.id) body.message_id = latest.id;
    return context.fetchJson("/api/chat/read", {
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
        void markRoomRead(room, actions.cacheFor(room), { force: true });
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
      els.channelList.appendChild(roomButton({
        type: "channel",
        id: channel.id,
        active,
        leading,
        title: channelLabel(channel),
        meta,
        className: channelIsPending(channel) ? "is-pending" : "",
      }));
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
      const status = actions.dmPresenceStatus(thread);
      const active = state.activeRoom?.type === "thread" && state.activeRoom.id === thread.id;
      const leading = `
        <span class="chat-avatar-wrap">
          <img class="chat-avatar-mini" ${avatarAttrs(other.picture_url, 48, "48px")} alt="">
          <span class="chat-presence-dot chat-presence-overlay is-${status}" aria-hidden="true"></span>
        </span>
      `;
      els.dmList.appendChild(roomButton({
        type: "thread",
        id: thread.id,
        active,
        leading,
        title: other.name || other.username || "Nest User",
        meta: actions.dmPresenceMarkup(status),
        className: "chat-dm-button",
      }));
    }
  }

  function updateRoomLists() {
    renderChannels();
    renderThreads();
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
      actions.setHistoryBanner(channelIsPending(channel) ? null : channel);
      const placeholder = channelIsPending(channel)
        ? "Waiting for admin approval"
        : channel.read_only
          ? "Read-only channel"
          : `Message #${channelLabel(channel)}`;
      actions.setComposer(channelIsWritable(channel), placeholder);
      return;
    }
    if (thread) {
      const other = thread.other_user || {};
      els.roomSymbol.classList.add("is-avatar");
      const status = actions.dmPresenceStatus(thread);
      els.roomSymbol.innerHTML = `
        <span class="chat-room-avatar-wrap">
          <img ${avatarAttrs(other.picture_url, 48, "48px")} alt="">
          <span class="chat-presence-dot chat-presence-overlay is-${status}" aria-hidden="true"></span>
        </span>
      `;
      els.roomName.textContent = other.name || other.username || "Nest User";
      els.roomMeta.textContent = "";
      actions.setHistoryBanner(null);
      actions.setComposer(!thread.blocked, thread.blocked ? "This conversation is blocked" : `Message ${other.name || other.username || ""}`.trim());
      return;
    }
    els.roomSymbol.classList.remove("is-avatar");
    els.roomSymbol.textContent = "#";
    els.roomName.textContent = "Chat";
    els.roomMeta.textContent = "Loading...";
    actions.setHistoryBanner(null);
    if (els.composer) els.composer.hidden = true;
  }

  function updateChannel(payload) {
    if (!payload?.id) return;
    const index = state.channels.findIndex((channel) => channel.id === payload.id);
    if (index >= 0) state.channels[index] = { ...state.channels[index], ...payload };
    else state.channels.push(payload);
  }

  function updateThread(payload) {
    if (!payload?.id) return;
    actions.registerKnownUser(payload.other_user);
    const index = state.threads.findIndex((thread) => thread.id === payload.id);
    if (index >= 0) state.threads[index] = { ...state.threads[index], ...payload };
    else state.threads.unshift(payload);
    state.threads.sort((a, b) => String(b.last_message_at || "").localeCompare(String(a.last_message_at || "")));
  }

  async function fetchThread(threadId) {
    if (!threadId) return null;
    try {
      const payload = await context.fetchJson(`/api/chat/dm/threads/${encodeURIComponent(threadId)}`);
      if (payload.thread) {
        updateThread(payload.thread);
        updateRoomLists();
        actions.renderPresenceDrivenUi();
        actions.schedulePersistentBootstrapSave();
        return payload.thread;
      }
    } catch (error) {
      actions.setStatus(error.message || "Unable to load direct message.", "error");
    }
    return null;
  }

  async function selectRoom(room, options = {}) {
    const previousRoom = state.activeRoom;
    const selection = roomSelection.begin(room);
    actions.saveActiveScroll();
    actions.closeInlineProfilePopover();
    if (previousRoom && actions.roomKey(previousRoom) !== actions.roomKey(room)) {
      extensions.attachments?.resetForRoom?.();
      extensions.mediaPicker?.clear?.();
    }
    state.activeRoom = room;
    state.activeProfile = null;
    actions.renderMessageLoader?.();
    updateRoomLists();
    renderHeader();
    if (!options.suppressFocus) actions.focusComposerSoon();
    actions.setStatus(null);
    actions.handleActiveRoomPresenceChange(previousRoom);

    if (!roomSelection.isCurrent(selection, state.activeRoom)) return;

    const channel = activeChannel();
    const thread = activeThread();
    if (channelIsPending(channel)) {
      actions.renderApprovalNotice(channel);
      actions.schedulePersistentBootstrapSave();
      return;
    }
    if (thread) actions.renderDmProfile(thread);
    else actions.renderMembers(channel?.online_users || channel?.active_users || []);

    const cache = actions.cacheFor(room);
    if (!cache.loaded) await actions.hydrateRoomFromPersistentCache(room);
    if (!roomSelection.isCurrent(selection, state.activeRoom)) return;
    if (actions.renderCachedRoom(room)) {
      if (!cache.stale || actions.latestMessageForRead(cache)) markRoomRead(room, cache);
      if (cache.latestCursor) {
        const delta = actions.deltaLoadParams(cache);
        await actions.loadMessages({ ...delta, quiet: true, force: true, light: true, roomSelection: selection });
        if (!roomSelection.isCurrent(selection, state.activeRoom)) return;
        markRoomRead(room);
      } else if (cache.stale) {
        await actions.loadMessages({ force: true, quiet: true, roomSelection: selection });
        if (!roomSelection.isCurrent(selection, state.activeRoom)) return;
        markRoomRead(room);
      }
    } else {
      await actions.loadMessages({ force: true, roomSelection: selection });
      if (!roomSelection.isCurrent(selection, state.activeRoom)) return;
      markRoomRead(room);
    }
    actions.renderPresenceDrivenUi();
    actions.schedulePersistentBootstrapSave();
  }

  function cancelRoomSelection() {
    roomSelection.cancel();
  }

  async function searchPeople() {
    const query = els.dmSearchInput.value.trim();
    if (query.length < 2) {
      els.dmResults.innerHTML = "";
      return;
    }
    try {
      const payload = await context.fetchJson(`/api/chat/dm/search?q=${encodeURIComponent(query)}`);
      const results = payload.results || [];
      els.dmResults.innerHTML = results.length
        ? results.map((user) => `
          <button type="button" class="chat-member chat-dm-result" data-start-dm="${escapeHtml(user.id)}" aria-label="Start a direct message with ${escapeHtml(user.name || user.username || "Nest User")}${user.tier_label ? `, ${escapeHtml(user.tier_label)}` : ""}" ${GRAMMARLY_DISABLED_ATTRS}>
            <img class="chat-member-avatar" ${avatarAttrs(user.picture_url, 84, "42px")} alt="">
            <span class="chat-member-copy">
              <strong>${escapeHtml(user.name || user.username || "Nest User")}</strong>
              <small>${escapeHtml([user.school, user.major].filter(Boolean).join(" · ") || user.username || "User")}</small>
            </span>
            ${actions.memberTierBadgeMarkup(user)}
          </button>
        `).join("")
        : `<div class="chat-empty chat-empty-compact" ${GRAMMARLY_DISABLED_ATTRS}>No users found.</div>`;
    } catch (error) {
      els.dmResults.innerHTML = `<div class="chat-empty chat-empty-compact" ${GRAMMARLY_DISABLED_ATTRS}>${escapeHtml(error.message)}</div>`;
    }
  }

  async function startDm(userId) {
    try {
      const payload = await context.fetchJson("/api/chat/dm/threads", {
        method: "POST",
        body: JSON.stringify({ user_id: userId }),
      });
      updateThread(payload.thread);
      if (payload.thread?.id) setRoomUnread({ type: "thread", id: payload.thread.id }, { unread_count: 0, has_unread: false });
      renderThreads();
      els.dmSearch.hidden = true;
      els.dmSearchInput.value = "";
      els.dmResults.innerHTML = "";
      await selectRoom({ type: "thread", id: payload.thread.id });
      actions.schedulePersistentBootstrapSave();
    } catch (error) {
      actions.setStatus(error.message || "Unable to start direct message.", "error");
    }
  }

  async function toggleBlock(userId, currentlyBlocked) {
    try {
      const payload = await context.fetchJson(`/api/chat/blocks/${encodeURIComponent(userId)}`, {
        method: currentlyBlocked ? "DELETE" : "POST",
      });
      const thread = activeThread();
      if (thread) {
        thread.blocked = Boolean(payload.blocked);
        renderHeader();
        actions.renderDmProfile(thread);
        actions.schedulePersistentBootstrapSave();
      }
      if (currentlyBlocked && payload.blocked === false) {
        window.APStudyToast?.show?.({
          message: "User unblocked.",
          type: "info",
          duration: 10_000,
          action: { label: "Undo", onClick: () => toggleBlock(userId, false) },
        });
      }
    } catch (error) {
      actions.setStatus(error.message || "Unable to update block.", "error");
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
    if (state.persistentCacheReady || state.serverBootstrapped) actions.schedulePersistentBootstrapSave();
  }

  function bindDmEvents() {
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
  }

  function bindShellEvents() {
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
        actions.closeInlineProfilePopover();
        extensions.mediaPicker?.close?.();
      }
    });
    els.channelList?.addEventListener("scroll", closeRoomContextMenu);
    els.dmList?.addEventListener("scroll", closeRoomContextMenu);
    window.addEventListener("resize", () => {
      closeRoomContextMenu();
      actions.closeInlineProfilePopover();
      if (window.innerWidth > 1100) closeChatDrawers();
    });
  }

  return {
    activeChannel,
    activeThread,
    bindDmEvents,
    bindShellEvents,
    cancelUnreadSummaryRefresh,
    channelIsPending,
    channelIsWritable,
    cancelRoomSelection,
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
  };
}
