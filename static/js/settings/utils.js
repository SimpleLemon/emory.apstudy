(function () {
  const { escapeHtml } = window.APStudyUIPrimitives;

  function createSettingsUtils({
    elements,
    constants,
  }) {
    const {
      interfaceThemes,
      sectionIds,
      themeToInterfaceTheme,
    } = constants;

    function setToggleState(button, active) {
      if (!button) {
        return;
      }
      button.classList.toggle('is-active', Boolean(active));
      button.setAttribute('aria-pressed', active ? 'true' : 'false');
    }

    function getToggleState(fieldName) {
      const button = document.querySelector(`[data-toggle-field="${fieldName}"]`);
      return Boolean(button && button.classList.contains('is-active'));
    }

    function normalizeThemeValue(value) {
      const normalized = String(value || '').trim().toLowerCase();
      if (interfaceThemes.includes(normalized)) {
        return normalized;
      }
      if (themeToInterfaceTheme[normalized]) {
        return themeToInterfaceTheme[normalized];
      }
      return 'obsidian-dark';
    }

    function applyThemePreference(theme) {
      const interfaceTheme = normalizeThemeValue(theme);
      window.APSTUDY_THEME_PREFERENCE = interfaceTheme;
      localStorage.setItem('apstudy-theme', interfaceTheme);
      document.documentElement.setAttribute('data-theme', interfaceTheme);
      const darkThemes = ['obsidian-dark', 'nest-dark'];
      const isDark = interfaceTheme === 'system-match'
        ? window.matchMedia('(prefers-color-scheme: dark)').matches
        : darkThemes.includes(interfaceTheme);
      document.documentElement.classList.toggle('dark', isDark);
    }

    function normalizeSidebarDefault(value) {
      const normalized = String(value || '').trim().toLowerCase();
      if (normalized === 'collapsed') {
        return 'collapsed';
      }
      return 'expanded';
    }

    function applySidebarDefault(value) {
      const shouldCollapse = normalizeSidebarDefault(value) === 'collapsed';
      localStorage.setItem('sidebar-collapsed', String(shouldCollapse));
      if (typeof window.APSTUDY_SET_SIDEBAR_COLLAPSED === 'function') {
        window.APSTUDY_SET_SIDEBAR_COLLAPSED(shouldCollapse);
        return;
      }
      document.dispatchEvent(new CustomEvent('apstudy-sidebar-default-change', {
        detail: { collapsed: shouldCollapse },
      }));
    }

    function normalizeHexColor(value) {
      let normalized = String(value || '').trim();
      if (!normalized) {
        return '#fecae1';
      }
      if (!normalized.startsWith('#')) {
        normalized = `#${normalized}`;
      }
      return /^#[0-9a-fA-F]{6}$/.test(normalized) ? normalized.toLowerCase() : '#fecae1';
    }

    function normalizeUsername(value) {
      return String(value || '').trim().toLowerCase();
    }

    function profileHandle(name, username, userId) {
      const normalizedUsername = String(username || '').trim();
      if (normalizedUsername) {
        return `@${normalizedUsername}`;
      }
      const slug = String(name || '')
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, '-')
        .replace(/^-+|-+$/g, '');
      return `@${slug || userId || 'apstudy-user'}`;
    }

    function isEmorySchool(value) {
      const normalized = String(value || '').trim().toLowerCase();
      return normalized === 'emory' || normalized === 'emory university';
    }

    function isEarlyMember(value) {
      if (!value) {
        return false;
      }
      const date = new Date(value);
      if (Number.isNaN(date.getTime())) {
        return false;
      }
      return date.getTime() < Date.UTC(2026, 7, 20);
    }

    function normalizeSectionId(hashValue) {
      if (!hashValue) {
        return '';
      }
      const normalized = String(hashValue).replace(/^#/, '').trim().toLowerCase();
      return sectionIds.includes(normalized) ? normalized : '';
    }

    function resolveLocalTimezone() {
      try {
        return Intl.DateTimeFormat().resolvedOptions().timeZone || '';
      } catch (error) {
        return '';
      }
    }

    const FALLBACK_TIMEZONES = [
      'UTC',
      'America/New_York',
      'America/Chicago',
      'America/Denver',
      'America/Los_Angeles',
      'America/Phoenix',
      'America/Anchorage',
      'Pacific/Honolulu',
      'Europe/London',
      'Europe/Paris',
      'Europe/Berlin',
      'Asia/Tokyo',
      'Asia/Shanghai',
      'Asia/Kolkata',
      'Australia/Sydney',
    ];

    function formatTimezoneLabel(timezone) {
      const value = String(timezone || '').trim();
      if (!value) {
        return '';
      }
      try {
        const formatter = new Intl.DateTimeFormat(undefined, {
          timeZone: value,
          timeZoneName: 'shortOffset',
        });
        const parts = formatter.formatToParts(new Date());
        const offset = parts.find((part) => part.type === 'timeZoneName')?.value || '';
        const readable = value.replace(/_/g, ' ');
        return offset ? `${readable} (${offset})` : readable;
      } catch (error) {
        return value.replace(/_/g, ' ');
      }
    }

    function listSupportedTimezones() {
      try {
        if (typeof Intl.supportedValuesOf === 'function') {
          return Intl.supportedValuesOf('timeZone').slice().sort((left, right) => left.localeCompare(right));
        }
      } catch (error) {
        // Fall through to the static list.
      }
      return FALLBACK_TIMEZONES.slice();
    }

    function formatBytes(bytes) {
      const size = Number(bytes || 0);
      if (!Number.isFinite(size) || size <= 0) {
        return '0 B';
      }
      const units = ['B', 'KB', 'MB', 'GB', 'TB'];
      let value = size;
      let unitIndex = 0;
      while (value >= 1024 && unitIndex < units.length - 1) {
        value /= 1024;
        unitIndex += 1;
      }
      return `${value >= 10 || unitIndex === 0 ? value.toFixed(0) : value.toFixed(1)} ${units[unitIndex]}`;
    }

    function formatCount(value, singularLabel) {
      const count = Number(value || 0);
      const normalizedCount = Number.isFinite(count) && count >= 0 ? Math.floor(count) : 0;
      const pluralLabel = `${singularLabel}s`;
      return `${normalizedCount} ${normalizedCount === 1 ? singularLabel : pluralLabel}`;
    }

    function formatDate(value) {
      if (!value) {
        return '';
      }
      const date = new Date(value);
      if (Number.isNaN(date.getTime())) {
        return String(value);
      }
      return date.toLocaleDateString([], { month: 'short', day: 'numeric', year: 'numeric' });
    }

    async function fetchJson(url, options = {}) {
      return window.APStudyHttp.fetchJson(url, {
        ...options,
        pendingLabel: options.pendingLabel || 'settings-save',
      });
    }

    async function fetchFormData(url, formData) {
      const request = fetch(url, {
        method: 'POST',
        body: formData,
      });
      const response = await (window.APStudyPendingMutations?.track(request, 'settings-save') || request);
      const contentType = response.headers.get('content-type') || '';
      const data = contentType.includes('application/json') ? await response.json() : null;
      if (!response.ok) {
        throw new Error((data && data.error) || 'Request failed.');
      }
      return data;
    }

    async function copyText(text) {
      if (!text) {
        return;
      }
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(text);
        return;
      }
      const temporaryInput = document.createElement('input');
      temporaryInput.value = text;
      document.body.appendChild(temporaryInput);
      temporaryInput.select();
      document.execCommand('copy');
      temporaryInput.remove();
    }

    function flashCopyButton(button) {
      const previousHtml = button.innerHTML;
      button.classList.add('is-copied');
      button.innerHTML = '<span class="material-symbols-outlined" aria-hidden="true">check</span>';
      window.setTimeout(() => {
        button.classList.remove('is-copied');
        button.innerHTML = previousHtml;
      }, 1200);
    }

    function showToast(message, type, options = {}) {
      if (window.APStudyToast) {
        window.APStudyToast.show({
          message,
          title: options.title,
          type: type === 'warning' ? 'warning' : type === 'error' ? 'error' : type === 'info' ? 'info' : 'success',
          action: options.action,
          duration: options.duration,
        });
      }
    }

    function downloadJson(filename, data) {
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 2000);
    }

    return {
      applySidebarDefault,
      applyThemePreference,
      copyText,
      downloadJson,
      escapeHtml,
      fetchFormData,
      fetchJson,
      flashCopyButton,
      formatBytes,
      formatCount,
      formatDate,
      formatTimezoneLabel,
      getToggleState,
      isEarlyMember,
      isEmorySchool,
      listSupportedTimezones,
      normalizeHexColor,
      normalizeSectionId,
      normalizeSidebarDefault,
      normalizeThemeValue,
      normalizeUsername,
      profileHandle,
      resolveLocalTimezone,
      setToggleState,
      showToast,
    };
  }

  window.APStudySettingsUtils = { createSettingsUtils };
})();
