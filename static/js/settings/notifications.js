(function notificationSettings(global) {
  const selected = (button, value) => { button.classList.toggle('is-active', value); button.setAttribute('aria-pressed', String(value)); };
  const toast = (message, type = 'success', title = '') => global.APStudyToast?.show?.({ message, title, type: type === 'error' ? 'error' : 'success' });
  const setActionStatus = (message = '', type = 'info') => {
    const status = document.getElementById('notification-action-status');
    if (!status) return;
    status.textContent = message;
    status.dataset.type = type;
    status.hidden = !message;
  };
  let config = null;
  async function load() {
    const api = global.APStudyNotifications?.api;
    if (!api) return setTimeout(load, 50);
    try { config = await api('/api/notifications/preferences'); render(); } catch (error) { toast(error.message || 'Refresh the page and try again.', 'error', 'Couldn’t load notification settings'); }
  }
  function render() {
    const capability = global.APStudyNotifications.support();
    const permission = !('Notification' in global) ? 'unsupported' : Notification.permission;
    const status = document.getElementById('notification-permission-status');
    const enable = document.getElementById('notification-enable');
    const test = document.getElementById('notification-test');
    const recovery = document.getElementById('notification-recovery');
    const standalone = matchMedia('(display-mode: standalone)').matches || navigator.standalone;
    const ios = /iPad|iPhone|iPod/.test(navigator.userAgent)
      || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
    if (!config.push_configured) { status.textContent = 'Temporarily unavailable'; enable.disabled = true; recovery.hidden = false; recovery.textContent = 'Nest’s background notification service is being configured. Try again shortly.'; }
    else if (ios && !standalone) { status.textContent = 'Install Nest on your Home Screen to enable notifications.'; enable.disabled = true; recovery.hidden = false; recovery.textContent = 'In Safari, tap Share, then Add to Home Screen. Open the installed Nest app and return here.'; }
    else if (permission === 'denied') { status.textContent = 'Blocked in browser'; enable.disabled = true; recovery.hidden = false; recovery.textContent = 'Allow notifications for nest.apstudy.org in your browser’s site settings, then reload this page.'; }
    else if (!capability.supported) { status.textContent = 'Not supported by this browser'; enable.disabled = true; recovery.hidden = false; recovery.textContent = 'Use a current version of Brave, Chrome, Edge, Firefox, or Safari. On iPhone and iPad, install Nest on the Home Screen first.'; }
    else { status.textContent = permission === 'granted' ? 'Enabled on this browser' : 'Not enabled on this browser'; enable.textContent = permission === 'granted' ? 'Repair subscription' : 'Enable notifications'; enable.disabled = false; recovery.hidden = true; }
    test.disabled = !config.push_configured || config.devices.length === 0;
    test.title = config.devices.length ? '' : 'Enable notifications on a browser before sending a test.';
    const hasNotificationDevice = config.devices.length > 0;
    document.querySelectorAll('[data-push-toggle]').forEach((button) => {
      selected(button, Boolean(config.preferences[button.dataset.pushToggle]));
      const normalControl = button.closest('[data-normal-control]');
      if (!normalControl) return;
      button.disabled = !hasNotificationDevice;
      normalControl.classList.toggle('is-unavailable', !hasNotificationDevice);
      normalControl.tabIndex = hasNotificationDevice ? -1 : 0;
      if (hasNotificationDevice) {
        normalControl.removeAttribute('aria-label');
        normalControl.removeAttribute('data-notification-tooltip');
      } else {
        normalControl.setAttribute('aria-label', 'Notifications unavailable until a browser is enabled');
        normalControl.dataset.notificationTooltip = 'Enable notifications on a browser to use this option.';
      }
    });
    const devices = document.getElementById('notification-devices');
    devices.innerHTML = config.devices.length ? config.devices.map((device) => `<div class="notification-device"><span><strong>${escape(device.device_name)}</strong><small>Last active ${new Date(device.last_seen_at || device.created_at).toLocaleDateString()}</small></span><button type="button" class="settings-button settings-button-secondary" data-revoke="${device.id}">Revoke</button></div>`).join('') : '<p class="settings-help-text">No browsers are registered yet.</p>';
  }
  async function enable() {
    const button = document.getElementById('notification-enable');
    button.disabled = true;
    setActionStatus('Connecting this browser…');
    try { await global.APStudyNotifications.enable(undefined, { forceRefresh: Notification.permission === 'granted' }); setActionStatus('This browser is ready for background notifications.', 'success'); toast('Notifications enabled.'); await load(); }
    catch (error) { setActionStatus(error.message, 'error'); toast(error.message || 'Check your browser settings and try again.', 'error', 'Couldn’t enable notifications'); await load(); }
    finally { if (button && config?.push_configured && Notification.permission !== 'denied') button.disabled = false; }
  }
  async function save() {
    const preferences = {};
    document.querySelectorAll('[data-push-toggle]').forEach((button) => { preferences[button.dataset.pushToggle] = button.classList.contains('is-active'); });
    try { config.preferences = (await global.APStudyNotifications.api('/api/notifications/preferences', { method: 'PATCH', body: JSON.stringify(preferences) })).preferences; toast('Notification preferences saved.'); render(); } catch (error) { toast(error.message || 'Try again in a moment.', 'error', 'Couldn’t save notification preferences'); }
  }
  async function test() { try { const result = await global.APStudyNotifications.api('/api/notifications/test', { method: 'POST', body: '{}' }); toast(`Test accepted by ${result.accepted} device${result.accepted === 1 ? '' : 's'}.`); } catch (error) { setActionStatus(error.message, 'error'); toast(error.message || 'Try again in a moment.', 'error', 'Couldn’t send test notification'); } }
  async function revokeDevice(id) {
    const index = config?.devices?.findIndex((device) => String(device.id) === String(id)) ?? -1;
    const device = index >= 0 ? config.devices[index] : null;
    if (!device) return;
    config.devices.splice(index, 1);
    render();
    if (!global.APStudyUndo?.stage) {
      await global.APStudyNotifications.api(`/api/notifications/subscriptions/${encodeURIComponent(id)}`, { method: 'DELETE' });
      await load();
      return;
    }
    global.APStudyUndo.stage({
      message: `${device.device_name || 'Browser'} revoked.`,
      commit: ({ reason }) => global.APStudyNotifications.api(`/api/notifications/subscriptions/${encodeURIComponent(id)}`, {
        method: 'DELETE',
        keepalive: reason === 'pagehide',
      }),
      restore: () => {
        config.devices.splice(Math.min(index, config.devices.length), 0, device);
        render();
      },
      onCommit: () => { void load(); },
      errorTitle: 'Couldn’t revoke browser',
    });
  }
  function escape(value) { const node = document.createElement('span'); node.textContent = value || ''; return node.innerHTML; }
  document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('notification-enable')?.addEventListener('click', enable);
    document.getElementById('notification-save')?.addEventListener('click', save);
    document.getElementById('notification-test')?.addEventListener('click', test);
    document.querySelectorAll('[data-push-toggle]').forEach((button) => button.addEventListener('click', () => {
      if (!button.disabled) selected(button, !button.classList.contains('is-active'));
    }));
    document.getElementById('notification-devices')?.addEventListener('click', (event) => {
      const id = event.target.closest('[data-revoke]')?.dataset.revoke;
      if (id) void revokeDevice(id).catch((error) => toast(error.message || 'Try again in a moment.', 'error', 'Couldn’t revoke browser'));
    });
    void load();
  });
})(window);
