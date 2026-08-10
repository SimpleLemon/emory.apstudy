/* global window, document, navigator, fetch, atob, setTimeout, clearTimeout, Notification, DOMException */

(function notificationShell(global) {
  const FOREGROUND_SYNC_MS = 15000;
  const LEADER_RETRY_MS = 5000;
  const LEADER_LEASE_MS = 20000;
  const LEADER_LOCK_NAME = 'apstudy-notification-foreground-sync';
  const LEADER_LEASE_KEY = 'apstudy-notification-sync-leader';
  const state = {
    foregroundReady: false,
    knownIds: new Set(),
    syncTimer: null,
    syncing: false,
    tabId: '',
    tray: null,
    unread: 0,
    channel: null,
    electionTimer: null,
    isLeader: false,
    lockPending: false,
    releaseLock: null,
  };

  const csrf = () => document.cookie.match(/(?:^|; )csrf_token=([^;]*)/)?.[1] || '';

  async function api(url, options = {}) {
    const headers = { ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...(options.headers || {}) };
    if (options.method && options.method !== 'GET') headers['X-CSRFToken'] = decodeURIComponent(csrf());
    const response = await fetch(url, { credentials: 'same-origin', ...options, headers });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.error || 'Notification request failed.');
    return payload;
  }

  function base64Key(value) {
    const padding = '='.repeat((4 - value.length % 4) % 4);
    return Uint8Array.from(atob((value + padding).replace(/-/g, '+').replace(/_/g, '/')), (char) => char.charCodeAt(0));
  }

  function keysMatch(left, right) {
    if (!left || !right || left.byteLength !== right.byteLength) return false;
    const a = new Uint8Array(left);
    const b = new Uint8Array(right);
    return a.every((value, index) => value === b[index]);
  }

  function support() {
    if (!global.isSecureContext) return { supported: false, reason: 'secure-context' };
    if (!('Notification' in global)) return { supported: false, reason: 'notifications' };
    if (!('serviceWorker' in navigator)) return { supported: false, reason: 'service-worker' };
    if (!('PushManager' in global)) return { supported: false, reason: 'push-manager' };
    return { supported: true, reason: '' };
  }

  function mappedError(error) {
    const name = String(error?.name || '');
    if (name === 'NotAllowedError') return new Error('Notifications are blocked. Allow them for nest.apstudy.org in your browser’s site settings, then try again.');
    if (name === 'AbortError') return new Error('The browser could not reach its push service. In Brave, turn on push messaging in Privacy and security, fully restart Brave, then try again.');
    if (name === 'InvalidStateError') return new Error('The saved browser subscription is no longer valid. Reload the page and enable notifications again.');
    if (name === 'SecurityError') return new Error('Background notifications require a secure HTTPS connection.');
    return error instanceof Error ? error : new Error('This browser could not enable background notifications.');
  }

  async function withTimeout(promise, milliseconds, message) {
    let timeoutId;
    const timeout = new Promise((_, reject) => { timeoutId = setTimeout(() => reject(new Error(message)), milliseconds); });
    try { return await Promise.race([promise, timeout]); } finally { clearTimeout(timeoutId); }
  }

  async function registration() {
    if (!support().supported) throw new Error('This browser does not support background notifications.');
    await navigator.serviceWorker.register('/service-worker.js', { scope: '/', updateViaCache: 'none' });
    const ready = await withTimeout(navigator.serviceWorker.ready, 12000, 'The notification service did not start. Reload the page and try again.');
    await withTimeout(ready.update(), 12000, 'The notification service could not be refreshed. Reload the page and try again.');
    return ready;
  }

  async function enable(deviceName, options = {}) {
    if (!support().supported) throw new Error('This browser does not support background notifications.');
    try {
      const config = await api('/api/notifications/preferences');
      if (!config.push_configured || !config.vapid_public_key) throw new Error('Background notifications are temporarily unavailable. Please try again later.');
      const permission = Notification.permission === 'granted' ? 'granted' : await Notification.requestPermission();
      if (permission !== 'granted') throw new DOMException('Permission denied', 'NotAllowedError');
      const reg = await registration();
      const serverKey = base64Key(config.vapid_public_key);
      let subscription = await reg.pushManager.getSubscription();
      let replacedEndpoint = '';
      const keyChanged = subscription && !keysMatch(subscription.options?.applicationServerKey, serverKey);
      if (subscription && (options.forceRefresh || keyChanged)) {
        replacedEndpoint = subscription.endpoint;
        await subscription.unsubscribe();
        subscription = null;
      }
      if (!subscription) {
        subscription = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: serverKey });
      }
      if (replacedEndpoint) {
        await api('/api/notifications/subscriptions/current/delete', {
          method: 'POST',
          body: JSON.stringify({ endpoint: replacedEndpoint }),
        });
      }
      return await api('/api/notifications/subscriptions', {
        method: 'POST',
        body: JSON.stringify({
          subscription: subscription.toJSON(),
          device_name: deviceName || navigator.userAgentData?.platform || navigator.platform || 'This browser',
        }),
      });
    } catch (error) {
      throw mappedError(error);
    }
  }

  async function disableCurrent() {
    const reg = await navigator.serviceWorker?.getRegistration('/');
    const subscription = await reg?.pushManager.getSubscription();
    if (!subscription) return;
    await api('/api/notifications/subscriptions/current/delete', { method: 'POST', body: JSON.stringify({ endpoint: subscription.endpoint }) });
    await subscription.unsubscribe();
  }

  function setUnreadCount(value) {
    state.unread = Math.max(0, Number(value) || 0);
    const host = document.getElementById('navbar-notifications-host');
    const badge = host?.querySelector('.notification-badge');
    if (badge) {
      badge.textContent = state.unread > 99 ? '99+' : String(state.unread);
      badge.hidden = state.unread === 0;
    }
    state.tray?.setUnreadCount(state.unread);
    if (navigator.setAppBadge) {
      const badgeUpdate = state.unread
        ? navigator.setAppBadge(state.unread)
        : navigator.clearAppBadge ? navigator.clearAppBadge() : navigator.setAppBadge(0);
      Promise.resolve(badgeUpdate).catch(() => {});
    }
  }

  function broadcastSyncState(data = {}) {
    state.channel?.postMessage?.({
      type: 'notification-sync',
      unread_count: state.unread,
      focus_mode_active: data.focus_mode_active,
    });
  }

  function showError(error) {
    global.APStudyToast?.show?.({
      title: 'Couldn’t update notifications',
      message: error?.message || 'Try again in a moment.',
      type: 'error',
    });
  }

  function mountTray(host) {
    if (!global.APStudyNotificationTray) {
      if (document.querySelector('script[data-nest-notification-tray]')) return;
      const script = document.createElement('script');
      script.src = '/static/js/core/notification-tray.js';
      script.dataset.nestNotificationTray = 'true';
      script.addEventListener('load', () => mountTray(host), { once: true });
      document.head.appendChild(script);
      return;
    }
    state.tray = global.APStudyNotificationTray.mount(host, { api, setUnreadCount, showError });
    state.tray?.setUnreadCount(state.unread);
  }

  function renderBell() {
    const host = document.getElementById('navbar-notifications-host');
    if (!host) return;
    mountTray(host);
    void refreshCount(host);
    startForegroundDelivery();
  }

  async function showIntentPrompt() {
    if (!('Notification' in global) || Notification.permission !== 'default' || document.querySelector('.notification-intent-prompt')) return;
    try {
      const config = await api('/api/notifications/preferences');
      if (config.preferences.prompt_dismissed) return;
      const prompt = document.createElement('aside');
      prompt.className = 'notification-intent-prompt';
      prompt.setAttribute('role', 'dialog');
      prompt.setAttribute('aria-label', 'Enable notifications');
      prompt.innerHTML = '<strong>Stay ahead of what matters</strong><p>Let Nest alert you about deadlines, course openings, and messages—even when this tab is closed.</p><div><button type="button" data-not-now>Not now</button><button type="button" data-enable>Enable notifications</button></div>';
      document.body.appendChild(prompt);
      prompt.querySelector('[data-enable]').addEventListener('click', async () => {
        const button = prompt.querySelector('[data-enable]');
        button.disabled = true;
        try {
          await enable();
          prompt.remove();
          global.APStudyToast?.show?.({ message: 'Notifications enabled.', type: 'success' });
        } catch (error) {
          global.APStudyToast?.show?.({ title: 'Couldn’t enable notifications', message: error.message || 'Check your browser settings and try again.', type: 'error' });
          button.disabled = false;
        }
      });
      prompt.querySelector('[data-not-now]').addEventListener('click', async () => {
        await api('/api/notifications/preferences', { method: 'PATCH', body: JSON.stringify({ prompt_dismissed: true }) });
        prompt.remove();
      });
    } catch (_) {}
  }

  async function refreshCount() {
    try {
      const data = await api('/api/notifications/unread-count');
      setUnreadCount(data.unread_count);
    } catch (_) {}
  }

  function isLaptopOrTablet() {
    if (navigator.userAgentData?.mobile === true) return false;
    if (/Mobi|Android.*Mobile|iPhone|iPod/i.test(navigator.userAgent || '')) return false;
    return Boolean(global.matchMedia?.('(min-width: 600px), (pointer: fine)')?.matches);
  }

  function isForegroundActive() {
    return isLaptopOrTablet() && document.visibilityState === 'visible' && (document.hasFocus?.() ?? true);
  }

  function foregroundTabId() {
    if (state.tabId) return state.tabId;
    const key = 'apstudy-notification-tab-id';
    try {
      const existing = sessionStorage.getItem(key);
      if (existing) {
        state.tabId = existing;
        return state.tabId;
      }
      const created = (global.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`).replace(/[^A-Za-z0-9_-]/g, '').slice(0, 64);
      sessionStorage.setItem(key, created);
      state.tabId = created;
    } catch (_) {
      state.tabId = (global.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`).replace(/[^A-Za-z0-9_-]/g, '').slice(0, 64);
    }
    return state.tabId;
  }

  function remember(items) {
    items.forEach((item) => state.knownIds.add(String(item.id)));
    while (state.knownIds.size > 250) state.knownIds.delete(state.knownIds.values().next().value);
  }

  async function markToastOpened(item) {
    try {
      const data = await api('/api/notifications/read', { method: 'POST', body: JSON.stringify({ ids: [item.id], read: true }) });
      setUnreadCount(data.unread_count);
      await state.tray?.refresh();
    } catch (error) {
      showError(error);
    } finally {
      if (item.target_url) global.location.assign(item.target_url);
    }
  }

  function showInApp(item) {
    const action = item.target_url
      ? { label: 'Open', onClick: () => { void markToastOpened(item); return true; } }
      : { label: 'Mark read', onClick: () => { void markToastOpened(item); } };
    global.APStudyToast?.show?.({
      title: item.title || item.notification_type || 'New notification',
      message: item.body || item.message || 'Open notifications to view the update.',
      type: 'info',
      duration: 7000,
      action,
    });
  }

  async function showForegroundItems(items) {
    if (!items.length) return;
    if (state.tray?.isOpen()) {
      await state.tray.refresh();
      return;
    }
    items.slice(0, 2).reverse().forEach(showInApp);
    if (items.length > 2) {
      global.APStudyToast?.show?.({
        title: `${items.length - 2} more updates`,
        message: 'They’re ready in your notification tray.',
        type: 'info',
        duration: 7000,
        action: { label: 'View', onClick: () => { state.tray?.open(); } },
      });
    }
  }

  async function syncForeground({ inactive = false, keepalive = false } = {}) {
    if (!isLaptopOrTablet()) return;
    const active = !inactive && isForegroundActive();
    if (!active && !inactive) return;
    if (active && !state.isLeader) return;
    if (state.syncing && !inactive) return;
    if (inactive) state.foregroundReady = false;
    else state.syncing = true;
    try {
      const data = await api('/api/notifications/sync', {
        method: 'POST',
        body: JSON.stringify({
          active,
          device_class: isLaptopOrTablet() ? 'desktop_tablet' : 'mobile',
          tab_id: foregroundTabId(),
        }),
        keepalive,
      });
      if (typeof data.focus_mode_active === 'boolean') {
        global.APStudyProfileStatus?.setFocusMode?.(data.focus_mode_active);
      }
      if (!active || !data.active) return;
      const items = Array.isArray(data.notifications) ? data.notifications : [];
      const pendingIds = Array.isArray(data.pending_foreground_ids) ? data.pending_foreground_ids.map(String) : [];
      const pending = new Set(pendingIds);
      const visibleIds = new Set(items.map((item) => String(item.id)));
      const acknowledgedPendingIds = pendingIds.filter((id) => visibleIds.has(id));
      const fresh = items.filter((item) => (
        !item.is_read
        && item.foreground_enabled !== false
        && !state.knownIds.has(String(item.id))
        && (state.foregroundReady || pending.has(String(item.id)))
      ));
      remember(items);
      state.foregroundReady = true;
      setUnreadCount(data.unread_count);
      broadcastSyncState(data);
      await showForegroundItems(fresh);
      if (acknowledgedPendingIds.length) {
        await api('/api/notifications/foreground-ack', { method: 'POST', body: JSON.stringify({ ids: acknowledgedPendingIds }) });
      }
    } catch (_) {
      // Foreground sync is opportunistic; the feed and background push remain available.
    } finally {
      if (!inactive) state.syncing = false;
    }
  }

  function stopSyncTimer() {
    if (!state.syncTimer) return;
    global.clearInterval(state.syncTimer);
    state.syncTimer = null;
  }

  function setLeader(active) {
    const next = active === true;
    if (state.isLeader === next) return;
    state.isLeader = next;
    stopSyncTimer();
    if (next && isForegroundActive()) {
      void syncForeground();
      state.syncTimer = global.setInterval(syncForeground, FOREGROUND_SYNC_MS);
    }
  }

  function readLease() {
    try {
      return JSON.parse(global.localStorage?.getItem(LEADER_LEASE_KEY) || 'null');
    } catch (_) {
      return null;
    }
  }

  function claimLease() {
    if (!isForegroundActive()) return;
    const now = Date.now();
    const tabId = foregroundTabId();
    const current = readLease();
    if (current?.tab_id !== tabId && Number(current?.expires_at || 0) > now) {
      setLeader(false);
      return;
    }
    try {
      global.localStorage?.setItem(LEADER_LEASE_KEY, JSON.stringify({ tab_id: tabId, expires_at: now + LEADER_LEASE_MS }));
      setLeader(readLease()?.tab_id === tabId);
    } catch (_) {
      // Storage can be unavailable in strict private modes. One tab still syncs.
      setLeader(true);
    }
  }

  function releaseLeadership() {
    if (state.releaseLock) {
      state.releaseLock();
      state.releaseLock = null;
    }
    if (!navigator.locks?.request) {
      const lease = readLease();
      if (lease?.tab_id === foregroundTabId()) {
        try { global.localStorage?.removeItem(LEADER_LEASE_KEY); } catch (_) {}
      }
    }
    setLeader(false);
  }

  function requestLeadership() {
    if (!isForegroundActive()) return;
    if (!navigator.locks?.request) {
      claimLease();
      return;
    }
    if (state.lockPending || state.isLeader) return;
    state.lockPending = true;
    void navigator.locks.request(LEADER_LOCK_NAME, { ifAvailable: true }, async (lock) => {
      if (!lock) return;
      setLeader(true);
      await new Promise((resolve) => { state.releaseLock = resolve; });
      state.releaseLock = null;
      setLeader(false);
    }).catch(() => {
      claimLease();
    }).finally(() => {
      state.lockPending = false;
    });
  }

  function initializeCrossTabSync() {
    if ('BroadcastChannel' in global) {
      state.channel = new global.BroadcastChannel('apstudy-notification-sync');
      state.channel.addEventListener('message', (event) => {
        if (event.data?.type !== 'notification-sync') return;
        setUnreadCount(event.data.unread_count);
        if (typeof event.data.focus_mode_active === 'boolean') {
          global.APStudyProfileStatus?.setFocusMode?.(event.data.focus_mode_active);
        }
      });
    }
    requestLeadership();
    state.electionTimer = global.setInterval(requestLeadership, LEADER_RETRY_MS);
  }

  function startForegroundDelivery() {
    if (state.electionTimer) return;
    initializeCrossTabSync();
    global.addEventListener('focus', requestLeadership);
    global.addEventListener('blur', () => {
      void syncForeground({ inactive: true, keepalive: true });
      releaseLeadership();
    });
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) {
        void syncForeground({ inactive: true, keepalive: true });
        releaseLeadership();
      } else requestLeadership();
    });
    global.addEventListener('pagehide', () => {
      void syncForeground({ inactive: true, keepalive: true });
      releaseLeadership();
      state.channel?.close?.();
    });
  }

  global.APStudyNotifications = {
    api,
    disableCurrent,
    enable,
    refreshCount,
    registration,
    support,
  };
  global.addEventListener('apstudy:notification-intent', showIntentPrompt);
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', renderBell); else renderBell();
})(window);
