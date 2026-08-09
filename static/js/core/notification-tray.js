(function notificationTrayModule(global) {
  const { escapeHtml } = global.APStudyUIPrimitives;
  const CATEGORY_META = {
    calendar: { label: 'Calendar', icon: 'calendar_month' },
    courses: { label: 'Courses', icon: 'school' },
    chat_dm: { label: 'Chat', icon: 'forum' },
    chat_mention: { label: 'Mention', icon: 'alternate_email' },
    notes: { label: 'Notes', icon: 'description' },
    test: { label: 'Test', icon: 'check_circle' },
  };

  function categoryMeta(value) {
    return CATEGORY_META[value] || { label: 'Nest', icon: 'notifications' };
  }

  function timeMeta(value) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return { short: '', full: '' };
    const elapsed = Math.max(0, Date.now() - date.getTime());
    const minutes = Math.floor(elapsed / 60000);
    const hours = Math.floor(minutes / 60);
    const days = Math.floor(hours / 24);
    const short = minutes < 1 ? 'Now' : minutes < 60 ? `${minutes}m` : hours < 24 ? `${hours}h` : days < 7 ? `${days}d` : date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
    return { short, full: date.toLocaleString() };
  }

  function mount(host, dependencies) {
    if (!host || host.dataset.notificationTrayMounted === 'true') return null;
    host.dataset.notificationTrayMounted = 'true';
    const state = {
      category: 'all',
      cursor: null,
      items: [],
      loadSequence: 0,
      loading: false,
      open: false,
      search: '',
      searchTimer: null,
      selected: new Set(),
      status: 'all',
      unreadCount: 0,
    };

    host.innerHTML = `
      <button type="button" class="navbar-button notification-bell" aria-label="Notifications" aria-haspopup="dialog" aria-controls="notification-tray" aria-expanded="false">
        <span class="material-symbols-outlined" aria-hidden="true">notifications</span>
        <span class="notification-badge" hidden></span>
      </button>
      <section id="notification-tray" class="notification-tray" role="dialog" aria-modal="false" aria-labelledby="notification-tray-title" hidden>
        <header class="notification-tray-head">
          <div>
            <h2 id="notification-tray-title">Notifications</h2>
            <p data-unread-label>All caught up</p>
          </div>
          <div class="notification-tray-head-actions">
            <button type="button" class="notification-text-action" data-read-all>Mark all read</button>
            <button type="button" class="notification-icon-button" data-close aria-label="Close notifications">
              <span class="material-symbols-outlined" aria-hidden="true">close</span>
            </button>
          </div>
        </header>
        <div class="notification-tray-tools">
          <label class="notification-search">
            <span class="material-symbols-outlined" aria-hidden="true">search</span>
            <span class="notification-visually-hidden">Search notifications</span>
            <input type="search" data-search placeholder="Search notifications" autocomplete="off" spellcheck="false">
          </label>
          <div class="notification-filter-row">
            <div class="notification-status-filter" role="group" aria-label="Read status">
              <button type="button" data-status="all" aria-pressed="true">All</button>
              <button type="button" data-status="unread" aria-pressed="false">Unread</button>
              <button type="button" data-status="read" aria-pressed="false">Read</button>
            </div>
            <label class="notification-category-filter">
              <span class="material-symbols-outlined" aria-hidden="true">filter_list</span>
              <span class="notification-visually-hidden">Filter by category</span>
              <select data-category aria-label="Filter notifications by category">
                <option value="all">All activity</option>
                <option value="calendar">Calendar</option>
                <option value="courses">Courses</option>
                <option value="chat">Chat</option>
                <option value="notes">Notes</option>
              </select>
            </label>
          </div>
        </div>
        <div class="notification-bulk-bar">
          <label class="notification-select-all">
            <input type="checkbox" data-select-all aria-label="Select all notifications shown">
            <span data-result-summary>0 shown</span>
          </label>
          <div class="notification-bulk-actions" data-bulk-actions hidden>
            <button type="button" data-mark-selected><span class="material-symbols-outlined" aria-hidden="true">done_all</span>Mark read</button>
            <button type="button" class="is-danger" data-delete-selected><span class="material-symbols-outlined" aria-hidden="true">delete</span>Delete</button>
          </div>
        </div>
        <div class="notification-tray-list" data-list role="feed" aria-busy="true" aria-label="Notifications"></div>
        <footer class="notification-tray-foot" data-footer hidden>
          <button type="button" data-more>Load earlier notifications</button>
        </footer>
      </section>`;

    const bell = host.querySelector('.notification-bell');
    const tray = host.querySelector('.notification-tray');
    const list = host.querySelector('[data-list]');
    const selectAll = host.querySelector('[data-select-all]');

    function setUnreadCount(value) {
      state.unreadCount = Math.max(0, Number(value) || 0);
      const badge = host.querySelector('.notification-badge');
      badge.textContent = state.unreadCount > 99 ? '99+' : String(state.unreadCount);
      badge.hidden = state.unreadCount === 0;
      host.querySelector('[data-unread-label]').textContent = state.unreadCount ? `${state.unreadCount} unread` : 'All caught up';
      host.querySelector('[data-read-all]').disabled = state.unreadCount === 0;
    }

    function loadingMarkup() {
      return `<div class="notification-tray-skeleton" aria-label="Loading notifications">
        ${Array.from({ length: 4 }, () => '<div><span></span><p><i></i><i></i></p></div>').join('')}
      </div>`;
    }

    function itemMarkup(item) {
      const meta = categoryMeta(item.category);
      const time = timeMeta(item.created_at);
      const title = escapeHtml(item.title || item.notification_type || 'Notification');
      const body = escapeHtml(item.body || item.message || '');
      const content = `<span class="notification-item-copy"><span class="notification-item-title"><strong>${title}</strong><span><span>${escapeHtml(meta.label)}</span><time datetime="${escapeHtml(item.created_at)}" title="${escapeHtml(time.full)}">${escapeHtml(time.short)}</time></span></span>${body ? `<span class="notification-item-body">${body}</span>` : ''}</span>`;
      let main;
      if (item.target_url) {
        main = `<a class="notification-item-main" href="${escapeHtml(item.target_url)}" data-notification-open>${content}<span class="material-symbols-outlined notification-item-open-icon" aria-hidden="true">arrow_outward</span></a>`;
      } else if (!item.is_read) {
        main = `<button type="button" class="notification-item-main" data-notification-read aria-label="Mark ${title} as read">${content}</button>`;
      } else {
        main = `<div class="notification-item-main">${content}</div>`;
      }
      return `<article class="notification-tray-item ${item.is_read ? '' : 'is-unread'}" data-id="${escapeHtml(item.id)}">
        <label class="notification-item-select"><input type="checkbox" data-select-item aria-label="Select ${title}" ${state.selected.has(String(item.id)) ? 'checked' : ''}></label>
        <span class="notification-item-icon is-${escapeHtml(item.category || 'default')}" aria-hidden="true"><span class="material-symbols-outlined" aria-hidden="true">${escapeHtml(meta.icon)}</span></span>
        ${main}
        ${item.is_read ? '' : '<span class="notification-unread-dot" aria-label="Unread"></span>'}
      </article>`;
    }

    function emptyMarkup() {
      const filtered = state.status !== 'all' || state.category !== 'all' || state.search;
      if (state.status === 'unread' && !state.search && state.category === 'all') {
        return '<div class="notification-tray-empty"><span class="material-symbols-outlined" aria-hidden="true">done_all</span><strong>You’re all caught up</strong><p>New updates will appear here as they arrive.</p></div>';
      }
      if (filtered) {
        return '<div class="notification-tray-empty"><span class="material-symbols-outlined" aria-hidden="true">manage_search</span><strong>No matching notifications</strong><p>Try another category, status, or search.</p><button type="button" data-reset-filters>Reset filters</button></div>';
      }
      return '<div class="notification-tray-empty"><span class="material-symbols-outlined" aria-hidden="true">notifications_none</span><strong>Nothing here yet</strong><p>Calendar, course, chat, and notes updates will collect here.</p></div>';
    }

    function renderItems() {
      list.innerHTML = state.items.length ? state.items.map(itemMarkup).join('') : emptyMarkup();
      list.setAttribute('aria-busy', 'false');
      host.querySelector('[data-footer]').hidden = !state.cursor;
      updateSelectionUi();
    }

    function restoreItemAtIndex(items, item, index) {
      if (!item || items.some((candidate) => String(candidate.id) === String(item.id))) return items;
      const next = [...items];
      next.splice(Math.min(Math.max(0, index), next.length), 0, item);
      return next;
    }

    function updateSelectionUi() {
      const visibleIds = state.items.map((item) => String(item.id));
      const selectedVisible = visibleIds.filter((id) => state.selected.has(id));
      selectAll.checked = visibleIds.length > 0 && selectedVisible.length === visibleIds.length;
      selectAll.indeterminate = selectedVisible.length > 0 && selectedVisible.length < visibleIds.length;
      selectAll.disabled = visibleIds.length === 0;
      host.querySelector('[data-result-summary]').textContent = selectedVisible.length ? `${selectedVisible.length} selected` : `${visibleIds.length} shown`;
      host.querySelector('[data-bulk-actions]').hidden = selectedVisible.length === 0;
    }

    async function load({ append = false, quiet = false } = {}) {
      const requestId = ++state.loadSequence;
      state.loading = true;
      if (!append && !quiet) {
        list.setAttribute('aria-busy', 'true');
        list.innerHTML = loadingMarkup();
      }
      const query = new URLSearchParams({ limit: '20', status: state.status });
      if (state.category !== 'all') query.set('category', state.category);
      if (state.search) query.set('search', state.search);
      if (append && state.cursor) query.set('before', state.cursor);
      try {
        const data = await dependencies.api(`/api/notifications?${query}`);
        if (requestId !== state.loadSequence) return;
        const incoming = Array.isArray(data.notifications) ? data.notifications : [];
        state.items = append ? [...state.items, ...incoming.filter((item) => !state.items.some((current) => current.id === item.id))] : incoming;
        state.cursor = data.next_cursor || null;
        const currentIds = new Set(state.items.map((item) => String(item.id)));
        state.selected = new Set([...state.selected].filter((id) => currentIds.has(id)));
        setUnreadCount(data.unread_count);
        dependencies.setUnreadCount(data.unread_count);
        renderItems();
      } catch (error) {
        if (requestId !== state.loadSequence) return;
        list.setAttribute('aria-busy', 'false');
        list.innerHTML = `<div class="notification-tray-empty is-error"><span class="material-symbols-outlined" aria-hidden="true">cloud_off</span><strong>Couldn’t load notifications</strong><p>${escapeHtml(error.message)}</p><button type="button" data-retry>Try again</button></div>`;
      } finally {
        if (requestId === state.loadSequence) state.loading = false;
      }
    }

    function setOpen(open, { keyboard = false } = {}) {
      state.open = Boolean(open);
      tray.hidden = !state.open;
      bell.setAttribute('aria-expanded', String(state.open));
      bell.classList.toggle('is-active', state.open);
      if (state.open) {
        void load();
        if (keyboard) requestAnimationFrame(() => host.querySelector('[data-search]')?.focus({ preventScroll: true }));
      }
    }

    async function markRead(ids) {
      if (!ids.length) return;
      const data = await dependencies.api('/api/notifications/read', { method: 'POST', body: JSON.stringify({ ids, read: true }) });
      const idSet = new Set(ids.map(String));
      state.items = state.items.map((item) => idSet.has(String(item.id)) ? { ...item, is_read: 1 } : item);
      state.selected = new Set([...state.selected].filter((id) => !idSet.has(id)));
      setUnreadCount(data.unread_count);
      dependencies.setUnreadCount(data.unread_count);
      if (state.status === 'unread') state.items = state.items.filter((item) => !idSet.has(String(item.id)));
      renderItems();
    }

    async function deleteSelected() {
      const ids = [...state.selected];
      if (!ids.length) return;
      const confirmed = await global.APStudyConfirm?.request?.({
        title: `Delete ${ids.length} notification${ids.length === 1 ? '' : 's'}?`,
        message: 'This removes the selected notifications from your history.',
        confirmLabel: 'Delete',
        tone: 'danger',
      });
      if (!confirmed) return;
      const idSet = new Set(ids);
      const removedItems = state.items
        .map((item, index) => ({ item, index }))
        .filter((record) => idSet.has(String(record.item.id)));
      const removedUnreadCount = removedItems.filter((record) => !record.item.is_read).length;
      state.items = state.items.filter((item) => !idSet.has(String(item.id)));
      state.selected.clear();
      setUnreadCount(Math.max(0, previousUnreadCount - removedUnreadCount));
      dependencies.setUnreadCount(state.unreadCount);
      renderItems();
      if (!global.APStudyUndo?.stage) {
        const data = await dependencies.api('/api/notifications', { method: 'DELETE', body: JSON.stringify({ ids }) });
        setUnreadCount(data.unread_count);
        dependencies.setUnreadCount(data.unread_count);
        return;
      }
      global.APStudyUndo.stage({
        message: `${ids.length} notification${ids.length === 1 ? '' : 's'} deleted.`,
        commit: ({ reason }) => dependencies.api('/api/notifications', {
          method: 'DELETE',
          body: JSON.stringify({ ids }),
          keepalive: reason === 'pagehide',
        }),
        restore: () => {
          state.items = removedItems.reduce(
            (items, record) => restoreItemAtIndex(items, record.item, record.index),
            state.items,
          );
          ids.forEach((id) => state.selected.add(String(id)));
          setUnreadCount(state.unreadCount + removedUnreadCount);
          dependencies.setUnreadCount(state.unreadCount);
          renderItems();
        },
        onCommit: () => { if (state.open) void load({ quiet: true }); },
        errorTitle: 'Couldn’t delete notifications',
      });
    }

    function resetFilters() {
      state.status = 'all';
      state.category = 'all';
      state.search = '';
      state.cursor = null;
      host.querySelector('[data-search]').value = '';
      host.querySelector('[data-category]').value = 'all';
      host.querySelectorAll('[data-status]').forEach((button) => button.setAttribute('aria-pressed', String(button.dataset.status === 'all')));
      void load();
    }

    bell.addEventListener('click', (event) => setOpen(!state.open, { keyboard: event.detail === 0 }));
    host.querySelector('[data-close]').addEventListener('click', () => { setOpen(false); bell.focus({ preventScroll: true }); });
    host.querySelector('[data-read-all]').addEventListener('click', async () => {
      try {
        const data = await dependencies.api('/api/notifications/read', { method: 'POST', body: JSON.stringify({ read: true }) });
        state.selected.clear();
        setUnreadCount(data.unread_count);
        dependencies.setUnreadCount(data.unread_count);
        await load({ quiet: true });
      } catch (error) {
        dependencies.showError(error);
      }
    });
    host.querySelectorAll('[data-status]').forEach((button) => button.addEventListener('click', () => {
      state.status = button.dataset.status;
      state.cursor = null;
      state.selected.clear();
      host.querySelectorAll('[data-status]').forEach((item) => item.setAttribute('aria-pressed', String(item === button)));
      void load();
    }));
    host.querySelector('[data-category]').addEventListener('change', (event) => {
      state.category = event.target.value;
      state.cursor = null;
      state.selected.clear();
      void load();
    });
    host.querySelector('[data-search]').addEventListener('input', (event) => {
      clearTimeout(state.searchTimer);
      state.searchTimer = setTimeout(() => {
        state.search = event.target.value.trim();
        state.cursor = null;
        state.selected.clear();
        void load();
      }, 250);
    });
    selectAll.addEventListener('change', () => {
      state.items.forEach((item) => selectAll.checked ? state.selected.add(String(item.id)) : state.selected.delete(String(item.id)));
      renderItems();
    });
    host.querySelector('[data-mark-selected]').addEventListener('click', () => markRead([...state.selected]).catch(dependencies.showError));
    host.querySelector('[data-delete-selected]').addEventListener('click', () => deleteSelected().catch(dependencies.showError));
    host.querySelector('[data-more]').addEventListener('click', () => load({ append: true }));
    list.addEventListener('change', (event) => {
      const checkbox = event.target.closest('[data-select-item]');
      const item = event.target.closest('[data-id]');
      if (!checkbox || !item) return;
      if (checkbox.checked) state.selected.add(item.dataset.id); else state.selected.delete(item.dataset.id);
      updateSelectionUi();
    });
    list.addEventListener('click', async (event) => {
      if (event.target.closest('[data-reset-filters]')) return resetFilters();
      if (event.target.closest('[data-retry]')) return load();
      const item = event.target.closest('[data-id]');
      const open = event.target.closest('[data-notification-open]');
      const read = event.target.closest('[data-notification-read]');
      if (!item || (!open && !read)) return;
      const modified = event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button > 0;
      if (!open || !modified) event.preventDefault();
      try {
        await markRead([item.dataset.id]);
      } catch (error) {
        dependencies.showError(error);
      } finally {
        if (open && !modified) global.location.assign(open.getAttribute('href'));
      }
    });
    document.addEventListener('pointerdown', (event) => {
      if (state.open && !host.contains(event.target)) setOpen(false);
    });
    document.addEventListener('keydown', (event) => {
      if (state.open && event.key === 'Escape') {
        event.preventDefault();
        setOpen(false);
        bell.focus({ preventScroll: true });
      }
    });

    const deepLink = new URL(global.location.href);
    if (deepLink.searchParams.get('notifications') === 'open') {
      deepLink.searchParams.delete('notifications');
      global.history.replaceState(global.history.state, '', `${deepLink.pathname}${deepLink.search}${deepLink.hash}`);
      setOpen(true);
    }

    return {
      close: () => setOpen(false),
      isOpen: () => state.open,
      open: () => setOpen(true),
      refresh: () => state.open ? load({ quiet: true }) : Promise.resolve(),
      setUnreadCount,
    };
  }

  global.APStudyNotificationTray = { mount };
})(window);
