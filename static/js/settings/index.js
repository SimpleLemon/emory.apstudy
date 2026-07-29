/* settings.js
 * Single-page settings UI with hash navigation and Flask-backed saves.
 */

(function registerSettingsIndex(global) {
if (global.APStudySettingsIndexLoaded) {
  return;
}
global.APStudySettingsIndexLoaded = true;

const SETTINGS_STATE = {
  account: null,
  profile: null,
  settings: null,
  storageUsageBytes: 0,
  notesCount: 0,
  filesCount: 0,
  entitlements: null,
  connectedServices: [],
  otherCalendarUrls: [],
  discord: { linked: false, username: null },
  activeSection: 'account',
  pendingTheme: '',
  profileBaseline: null,
  profileDirty: false,
  profileSaving: false,
};

const SETTINGS_SECTION_IDS = ['account', 'tier', 'data', 'preferences', 'notifications'];
const SETTINGS_INTERFACE_THEMES = [
  'obsidian-dark',
  'parchment-light',
  'system-match',
  'nest-light',
  'nest-dark',
];
const SETTINGS_THEME_TO_INTERFACE_THEME = {
  dark: 'obsidian-dark',
  light: 'parchment-light',
  system: 'system-match',
};
const SETTINGS_PENDING_THEME_STORAGE_KEY = 'apstudy-theme-pending';
const SETTINGS_PENDING_THEME_UPDATED_KEY = 'apstudy-theme-updated-at';
const SETTINGS_MAX_OTHER_CALENDARS = 10;

const SETTINGS_ENDPOINTS = {
  bootstrap: '/settings/api/bootstrap',
  profile: '/settings/api/profile',
  avatarUpload: '/settings/api/avatar-upload',
  feedUrl: '/settings/api/feed-url',
  preferences: '/settings/api/interface-preferences',
  exportData: '/settings/api/export',
  deleteAccount: '/settings/api/account/delete',
  passwordRecovery: '/settings/api/account/recovery',
  universities: '/api/universities',
  discordUnlink: '/settings/api/discord/unlink',
  invites: '/settings/api/invites',
  dmThreads: '/api/chat/dm/threads',
};

const DISCORD_ICON_SVG = '<svg width="18" height="18" viewBox="0 0 24 24" class="settings-discord-icon" fill="currentColor" aria-hidden="true" xmlns="http://www.w3.org/2000/svg"><path d="M20.317 4.37a19.791 19.791 0 0 0-4.885-1.515.074.074 0 0 0-.079.037c-.21.375-.444.864-.608 1.25a18.27 18.27 0 0 0-5.487 0 12.64 12.64 0 0 0-.617-1.25.077.077 0 0 0-.079-.037 19.736 19.736 0 0 0-4.885 1.515.069.069 0 0 0-.032.027C.533 9.048-.32 13.58.099 18.057a.082.082 0 0 0 .031.057 19.9 19.9 0 0 0 5.993 3.03.078.078 0 0 0 .084-.028 14.006 14.006 0 0 0 1.226-1.994.076.076 0 0 0-.041-.106 13.107 13.107 0 0 1-1.872-.892.077.077 0 0 1-.008-.128 10.2 10.2 0 0 0 .372-.292.074.074 0 0 1 .077-.01c3.928 1.793 8.18 1.793 12.062 0a.074.074 0 0 1 .078.01c.12.098.246.196.373.292a.077.077 0 0 1-.006.127 12.299 12.299 0 0 1-1.873.892.077.077 0 0 0-.041.107c.36.698.772 1.362 1.225 1.993a.076.076 0 0 0 .084.028 19.839 19.839 0 0 0 6.002-3.03.077.077 0 0 0 .032-.054c.5-5.177-.838-9.674-3.549-13.66a.061.061 0 0 0-.031-.03zM8.02 15.33c-1.183 0-2.157-1.085-2.157-2.419 0-1.333.956-2.419 2.157-2.419 1.21 0 2.176 1.096 2.157 2.42 0 1.333-.956 2.418-2.157 2.418zm7.975 0c-1.183 0-2.157-1.085-2.157-2.419 0-1.333.955-2.419 2.157-2.419 1.21 0 2.176 1.096 2.157 2.42 0 1.333-.946 2.418-2.157 2.418z" /></svg>';

const elements = {};
const settingsUtils = window.APStudySettingsUtils.createSettingsUtils({
  elements,
  constants: {
    interfaceThemes: SETTINGS_INTERFACE_THEMES,
    sectionIds: SETTINGS_SECTION_IDS,
    themeToInterfaceTheme: SETTINGS_THEME_TO_INTERFACE_THEME,
  },
});
const {
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
} = settingsUtils;
const settingsCalendar = window.APStudySettingsCalendar.createSettingsCalendar({
  elements,
  state: SETTINGS_STATE,
  constants: {
    maxOtherCalendars: SETTINGS_MAX_OTHER_CALENDARS,
  },
  endpoints: SETTINGS_ENDPOINTS,
  callbacks: {
    fetchJson,
    showToast,
  },
});
const {
  bindCalendarControls,
  renderOtherCalendarRows,
  saveCalendarLinks,
} = settingsCalendar;
const settingsProfile = window.APStudySettingsProfile.createSettingsProfile({
  elements,
  state: SETTINGS_STATE,
  endpoints: SETTINGS_ENDPOINTS,
  callbacks: {
    copyText,
    escapeHtml,
    fetchFormData,
    fetchJson,
    formatDate,
    isEarlyMember,
    isEmorySchool,
    normalizeHexColor,
    normalizeUsername,
    populateFields,
    profileHandle,
    showToast,
  },
});
const {
  bindProfilePreviewControls,
  captureProfileBaseline,
  hasUnsavedProfileChanges,
  openProfileLink,
  paintBannerColor,
  renderProfilePreview,
  saveProfile,
  shareProfileLink,
  updateAvatarPreview,
} = settingsProfile;
const settingsPreferences = window.APStudySettingsPreferences.createSettingsPreferences({
  elements,
  state: SETTINGS_STATE,
  constants: {
    pendingThemeStorageKey: SETTINGS_PENDING_THEME_STORAGE_KEY,
    pendingThemeUpdatedKey: SETTINGS_PENDING_THEME_UPDATED_KEY,
  },
  endpoints: SETTINGS_ENDPOINTS,
  callbacks: {
    applySidebarDefault,
    applyThemePreference,
    fetchJson,
    getToggleState,
    normalizeSidebarDefault,
    normalizeThemeValue,
    resolveLocalTimezone,
    setToggleState,
    showToast,
  },
});
const {
  bindThemeChoiceButtons,
  savePreferences,
  syncThemeControls,
  syncToggleControls,
} = settingsPreferences;
const settingsAccount = window.APStudySettingsAccount.createSettingsAccount({
  elements,
  state: SETTINGS_STATE,
  endpoints: SETTINGS_ENDPOINTS,
  callbacks: {
    downloadJson,
    fetchJson,
    showToast,
  },
});
const {
  handleDeleteAccount,
  handleExportData,
  handlePasswordReset,
} = settingsAccount;
const settingsInvites = window.APStudySettingsInvites.createSettingsInvites({
  elements,
  endpoints: SETTINGS_ENDPOINTS,
  callbacks: {
    copyText,
    escapeHtml,
    fetchJson,
    formatDate,
    showToast,
  },
});
const {
  activateInvites,
  bindInviteControls,
} = settingsInvites;

function mountRegionComboboxes() {
  const mountSettingsCombobox = window.APStudySettingsCombobox?.mountSettingsCombobox;
  if (!mountSettingsCombobox) {
    return;
  }

  if (elements.languageComboboxRoot && elements.language) {
    elements.languageCombobox = mountSettingsCombobox({
      root: elements.languageComboboxRoot,
      input: elements.language,
      placeholder: 'Select language',
      searchable: false,
    });
  }

  if (elements.timezoneComboboxRoot && elements.timezone) {
    const timezoneOptions = listSupportedTimezones().map((timezone) => ({
      value: timezone,
      label: formatTimezoneLabel(timezone),
    }));

    elements.timezoneCombobox = mountSettingsCombobox({
      root: elements.timezoneComboboxRoot,
      input: elements.timezone,
      placeholder: 'Select timezone',
      searchable: true,
      options: timezoneOptions,
      quickActions: [{ id: 'device-timezone', label: 'Use device timezone' }],
      resolveQuickActionValue(action) {
        if (action.id === 'device-timezone') {
          const deviceTimezone = resolveLocalTimezone();
          if (deviceTimezone) {
            showToast('Timezone updated from your device.', 'success');
          }
          return deviceTimezone;
        }
        return '';
      },
    });
  }
}

function initializeSettingsPage() {
  cacheElements();
  renderSettingsSkeleton();
  bindNavigation();
  bindCopyButtons();
  bindToggleButtons();
  bindActionButtons();
  bindInviteControls();
  bindProfilePreviewControls();
  bindCalendarControls();
  bindDiscordControls();
  bindUnsavedChangesWarning();
  mountRegionComboboxes();
  elements.themeChoices = Array.from(document.querySelectorAll('.settings-theme-choice'));
  bindThemeChoiceButtons();
  void bootstrapSettingsPage();
}

function cacheElements() {
  elements.skeleton = document.getElementById('settings-skeleton');
  elements.sectionsWrap = document.querySelector('.settings-sections');
  elements.tabs = Array.from(document.querySelectorAll('.settings-tab'));
  elements.sections = Array.from(document.querySelectorAll('.settings-section'));
  elements.copyButtons = Array.from(document.querySelectorAll('[data-copy-target]'));
  elements.toggleButtons = Array.from(document.querySelectorAll('[data-toggle-field]'));
  elements.avatarPreview = document.getElementById('settings-avatar-preview');
  elements.profileTile = document.getElementById('settings-profile-tile');
  elements.previewName = document.getElementById('settings-preview-name');
  elements.previewHandle = document.getElementById('settings-preview-handle');
  elements.previewSchool = document.getElementById('settings-preview-school');
  elements.previewSchoolCard = document.getElementById('settings-preview-school-card');
  elements.previewMajor = document.getElementById('settings-preview-major');
  elements.previewGraduation = document.getElementById('settings-preview-graduation');
  elements.previewEducation = document.getElementById('settings-preview-education');
  elements.previewCreated = document.getElementById('settings-preview-created');
  elements.previewMemberCard = document.getElementById('settings-preview-member-card');
  elements.openProfile = document.getElementById('settings-open-profile');
  elements.shareProfile = document.getElementById('settings-share-profile');
  elements.avatarUpload = document.getElementById('settings-avatar-upload');
  elements.avatarUploadButton = document.getElementById('settings-avatar-upload-button');
  elements.avatarFileButton = document.getElementById('settings-avatar-file-button');
  elements.avatarUploadDropzone = document.getElementById('settings-avatar-dropzone');
  elements.avatarDropzonePreview = document.getElementById('settings-avatar-dropzone-preview');
  elements.avatarDropzonePlaceholder = document.getElementById('settings-avatar-dropzone-placeholder');
  elements.avatarUploadStatus = document.getElementById('settings-avatar-upload-status');
  elements.avatarModalStatus = document.getElementById('settings-avatar-modal-status');
  elements.avatarModal = document.getElementById('settings-avatar-modal');
  elements.avatarModalClosers = Array.from(document.querySelectorAll('[data-avatar-modal-close]'));
  elements.bannerColorPicker = document.getElementById('settings-banner-color-picker');
  elements.bannerSwatch = document.getElementById('settings-banner-swatch');
  elements.displayName = document.getElementById('settings-display-name');
  elements.username = document.getElementById('settings-username-input');
  elements.email = document.getElementById('settings-email');
  elements.accountCreated = document.getElementById('settings-account-created');
  elements.accountCreatedData = document.getElementById('settings-account-created-data');
  elements.userId = document.getElementById('settings-user-id');
  elements.accountUsername = document.getElementById('settings-username');
  elements.school = document.getElementById('settings-school');
  elements.universityOptions = document.getElementById('settings-university-options');
  elements.major = document.getElementById('settings-major');
  elements.graduationYear = document.getElementById('settings-graduation-year');
  elements.storageUsed = Array.from(document.querySelectorAll('[data-storage-used]'));
  elements.storageDetails = Array.from(document.querySelectorAll('[data-storage-details]'));
  elements.tierLabel = document.querySelector('[data-tier-label]');
  elements.tierStorage = document.querySelector('[data-tier-storage]');
  elements.tierStoragePercent = document.querySelector('[data-tier-storage-percent]');
  elements.tierStorageProgress = document.querySelector('[data-tier-storage-progress]');
  elements.tierStorageProgressTrack = document.querySelector('.settings-tier-progress[role="progressbar"]');
  elements.tierWarning = document.querySelector('[data-tier-warning]');
  elements.tierStatus = document.querySelector('[data-tier-status]');
  elements.tierLimits = Array.from(document.querySelectorAll('[data-tier-limit]'));
  elements.tierBadgeTrigger = document.getElementById('settings-tier-badge-trigger');
  elements.previewTierBadgeTrigger = document.getElementById('settings-preview-tier-badge-trigger');
  elements.tierBadge = document.getElementById('settings-tier-badge');
  elements.previewTierBadge = document.getElementById('settings-preview-tier-badge');
  elements.connectedServices = document.getElementById('settings-connected-services');
  elements.canvasFeedUrl = document.getElementById('settings-canvas-feed-url');
  elements.otherCalendarLinks = document.getElementById('settings-other-calendar-links');
  elements.otherCalendarCount = document.getElementById('settings-other-calendar-count');
  elements.addOtherCalendar = document.getElementById('settings-add-other-calendar');
  elements.saveCalendarLinks = document.getElementById('settings-save-calendar-links');
  elements.theme = document.getElementById('settings-theme');
  elements.sidebarDefault = document.getElementById('settings-sidebar-default');
  elements.language = document.getElementById('settings-language');
  elements.timezone = document.getElementById('settings-timezone');
  elements.languageComboboxRoot = document.querySelector('[data-settings-combobox="language"]');
  elements.timezoneComboboxRoot = document.querySelector('[data-settings-combobox="timezone"]');
  elements.saveProfile = document.getElementById('settings-save-profile');
  elements.saveAppearance = document.getElementById('settings-save-appearance');
  elements.saveNotifications = document.getElementById('settings-save-notifications');
  elements.saveRegion = document.getElementById('settings-save-region');
  elements.changePassword = document.getElementById('settings-change-password');
  elements.deleteAccount = document.getElementById('settings-delete-account');
  elements.exportData = document.getElementById('settings-export-data');
  elements.inviteCreateForm = document.getElementById('settings-invite-create');
  elements.inviteLabel = document.getElementById('settings-invite-label');
  elements.inviteCreateButton = document.getElementById('settings-invite-create-button');
  elements.invitesStatus = document.getElementById('settings-invites-status');
  elements.invitesList = document.getElementById('settings-invites-list');
  elements.toastHost = document.getElementById('settings-notifications');
  elements.discordButton = document.getElementById('settings-discord-button');
  elements.discordModal = document.getElementById('settings-discord-modal');
  elements.discordUnlink = document.getElementById('settings-discord-unlink');
  elements.discordRelink = document.getElementById('settings-discord-relink');
  elements.discordModalClosers = Array.from(document.querySelectorAll('[data-discord-modal-close]'));
}

function renderSettingsSkeleton() {
  elements.sectionsWrap?.classList.add('is-loading');
  elements.sectionsWrap?.setAttribute('aria-busy', 'true');
  if (!elements.skeleton) {
    return;
  }
  elements.skeleton.hidden = false;
}

function clearSettingsSkeleton() {
  elements.sectionsWrap?.classList.remove('is-loading');
  elements.sectionsWrap?.setAttribute('aria-busy', 'false');
  if (!elements.skeleton) {
    return;
  }
  elements.skeleton.hidden = true;
}

function bindNavigation() {
  elements.tabs.forEach((tab) => {
    tab.addEventListener('click', (event) => {
      event.preventDefault();
      const targetId = normalizeSectionId(tab.getAttribute('href'));
      if (!targetId) {
        return;
      }
      // Activate the section without scrolling — use show/hide behavior
      activateSection(targetId, { scroll: false, pushState: true });
    });
  });

  window.addEventListener('hashchange', () => {
    const targetId = normalizeSectionId(window.location.hash) || 'account';
    activateSection(targetId, { scroll: false, pushState: false });
  });

  // We intentionally do NOT observe scrolling for section activation.
  // Settings navigation behaves like tabbed panels: clicking a tab
  // shows the corresponding section without scrolling the page.
}

function bindCopyButtons() {
  elements.copyButtons.forEach((button) => {
    button.addEventListener('click', async () => {
      const targetId = button.getAttribute('data-copy-target');
      const input = targetId ? document.getElementById(targetId) : null;
      const value = input?.value || '';
      if (!value) {
        showToast('Nothing to copy.', 'error');
        return;
      }
      await copyText(value);
      flashCopyButton(button);
      showToast('Copied to clipboard.', 'success');
    });
  });
}

function bindToggleButtons() {
  elements.toggleButtons.forEach((button) => {
    button.addEventListener('click', () => {
      const field = button.getAttribute('data-toggle-field');
      if (!field) {
        return;
      }
      const nextValue = !button.classList.contains('is-active');
      setToggleState(button, nextValue);
    });
  });
}

function bindActionButtons() {
  elements.saveProfile?.addEventListener('click', () => void saveProfile());
  elements.saveAppearance?.addEventListener('click', () => void savePreferences());
  elements.saveNotifications?.addEventListener('click', () => void savePreferences());
  elements.saveRegion?.addEventListener('click', () => void savePreferences());
  elements.saveCalendarLinks?.addEventListener('click', () => void saveCalendarLinks());
  elements.openProfile?.addEventListener('click', () => openProfileLink());
  elements.shareProfile?.addEventListener('click', () => void shareProfileLink());
  elements.changePassword?.addEventListener('click', () => void handlePasswordReset());
  elements.deleteAccount?.addEventListener('click', () => void handleDeleteAccount());
  elements.exportData?.addEventListener('click', () => void handleExportData());
}

function bindUnsavedChangesWarning() {
  window.addEventListener('beforeunload', (event) => {
    if (SETTINGS_STATE.profileSaving || !hasUnsavedProfileChanges()) {
      return;
    }
    event.preventDefault();
    event.returnValue = '';
  });
}

async function bootstrapSettingsPage() {
  try {
    const bootstrapResponse = await fetchJson(SETTINGS_ENDPOINTS.bootstrap);

    SETTINGS_STATE.account = bootstrapResponse.account || bootstrapResponse.profile || null;
    SETTINGS_STATE.profile = bootstrapResponse.profile || null;
    SETTINGS_STATE.settings = bootstrapResponse.settings || null;
    SETTINGS_STATE.storageUsageBytes = Number(bootstrapResponse.storage_usage_bytes || 0);
    SETTINGS_STATE.notesCount = Number(bootstrapResponse.notes_count || 0);
    SETTINGS_STATE.filesCount = Number(bootstrapResponse.files_count || 0);
    SETTINGS_STATE.entitlements = bootstrapResponse.entitlements || null;
    SETTINGS_STATE.connectedServices = Array.isArray(bootstrapResponse.connected_services)
      ? bootstrapResponse.connected_services
      : [];
    SETTINGS_STATE.otherCalendarUrls = Array.isArray(bootstrapResponse.other_calendar_urls)
      ? bootstrapResponse.other_calendar_urls
      : [];
    SETTINGS_STATE.discord = bootstrapResponse.discord && typeof bootstrapResponse.discord === 'object'
      ? bootstrapResponse.discord
      : { linked: false, username: null };

    populateFields();
    renderEntitlements();
    notifyDiscordLinkResult();
    renderConnectedServices();
    syncThemeControls();
    syncToggleControls();
    syncHashOnLoad();
  } catch (error) {
    console.error(error);
    showToast(error.message || 'Refresh the page and try again.', 'error', { title: 'Couldn’t load settings' });
    syncHashOnLoad();
  } finally {
    clearSettingsSkeleton();
  }
}

function renderEntitlements() {
  const data = SETTINGS_STATE.entitlements;
  if (!data) return;
  const usage = data.usage || {};
  const limits = data.limits || {};
  const label = data.label || 'Free';
  if (elements.tierLabel) elements.tierLabel.textContent = label;

  const badgeNodes = [
    { image: elements.tierBadge, trigger: elements.tierBadgeTrigger },
    { image: elements.previewTierBadge, trigger: elements.previewTierBadgeTrigger },
  ].filter(({ image }) => image);
  badgeNodes.forEach(({ image, trigger }) => {
    const badge = data.badge;
    if (!badge) {
      image.hidden = true;
      if (trigger) trigger.hidden = true;
      return;
    }
    image.src = badge.asset;
    image.alt = '';
    image.hidden = false;
    if (trigger) {
      trigger.dataset.tooltip = label;
      trigger.setAttribute('aria-label', label);
      trigger.hidden = false;
    }
  });

  const used = Number(data.storage_usage_bytes || 0);
  const cap = data.storage_limit_bytes;
  const hasStorageCap = cap != null;
  const rawPercent = !hasStorageCap ? null : cap === 0 ? (used > 0 ? Infinity : 0) : (used / cap) * 100;
  const percent = rawPercent == null ? 0 : Math.min(100, rawPercent);
  if (elements.tierStorage) {
    elements.tierStorage.textContent = !hasStorageCap
      ? `${formatBytes(used)} used / Unlimited storage`
      : `${formatBytes(used)} used / ${formatBytes(cap)} (${Math.max(0, 100 - percent).toFixed(1)}% remaining)`;
  }
  if (elements.tierStoragePercent) {
    elements.tierStoragePercent.textContent = !hasStorageCap
      ? 'Unlimited'
      : rawPercent > 100 ? 'Over limit' : `${rawPercent.toFixed(0)}% used`;
  }
  if (elements.tierStorageProgress) elements.tierStorageProgress.style.transform = `scaleX(${percent / 100})`;
  const overLimit = Boolean(data.over_limit && Object.values(data.over_limit).some(Boolean)) || (hasStorageCap && used > cap);
  if (elements.tierStorageProgressTrack) {
    elements.tierStorageProgressTrack.setAttribute('aria-valuenow', String(Math.round(percent)));
    elements.tierStorageProgressTrack.setAttribute('aria-valuetext', !hasStorageCap ? `${formatBytes(used)} used, unlimited storage` : rawPercent === Infinity ? 'Storage limit reached' : `${rawPercent.toFixed(1)} percent used`);
    elements.tierStorageProgressTrack.classList.toggle('is-over-limit', overLimit);
  }
  if (elements.tierStatus) {
    elements.tierStatus.textContent = overLimit ? 'Needs attention' : 'Active';
    elements.tierStatus.classList.toggle('is-warning', overLimit);
  }
  if (elements.tierWarning) {
    elements.tierWarning.hidden = !overLimit;
    elements.tierWarning.textContent = overLimit ? 'You are over a current limit. Delete existing data before adding more.' : '';
  }

  const usageByLimit = {
    storage_bytes: limits.storage_bytes,
    max_file_size_bytes: limits.max_file_size_bytes,
    max_upload_files: limits.max_upload_files,
    max_notes: limits.max_notes,
    max_saved_courses: limits.max_saved_courses,
    max_seat_tracks: limits.max_seat_tracks,
    max_calendar_feeds: limits.max_calendar_feeds,
  };
  const usageByResource = {
    max_upload_files: 'files',
    max_notes: 'notes',
    max_saved_courses: 'saved_courses',
    max_seat_tracks: 'seat_tracks',
    max_calendar_feeds: 'calendar_feeds',
  };
  elements.tierLimits.forEach((node) => {
    const key = node.dataset.tierLimit;
    const limit = usageByLimit[key];
    if (key === 'storage_bytes' || key === 'max_file_size_bytes') {
      node.textContent = limit == null ? 'Unlimited' : formatBytes(limit);
      return;
    }
    node.textContent = limit == null ? 'Unlimited' : `${usage[usageByResource[key]] || 0} / ${limit}`;
  });
}

function populateFields() {
  const profile = SETTINGS_STATE.profile || {};
  const accountData = SETTINGS_STATE.account || {};
  const settings = SETTINGS_STATE.settings || {};

  const displayName = profile.name || accountData.name || '';
  const username = profile.username || '';
  const email = profile.email || accountData.email || '';
  const accountId = profile.id || accountData.$id || accountData.id || '';
  const createdAt = profile.member_since || formatDate(profile.created_at || accountData.registration || accountData.$createdAt);
  const avatarUrl = profile.picture_url || accountData.avatar || accountData.picture_url || '';
  const bannerColor = normalizeHexColor(profile.banner_color || '#fecae1');

  if (elements.displayName) {
    elements.displayName.value = displayName;
  }
  if (elements.username) {
    elements.username.value = username;
  }
  if (elements.email) {
    elements.email.value = email;
  }
  if (elements.accountCreated) {
    elements.accountCreated.value = createdAt || '';
  }
  if (elements.accountCreatedData) {
    elements.accountCreatedData.value = createdAt || '';
  }
  if (elements.userId) {
    elements.userId.value = accountId;
  }
  if (elements.accountUsername) {
    elements.accountUsername.value = username;
  }
  if (elements.school) {
    elements.school.value = profile.school || '';
  }
  if (elements.major) {
    elements.major.value = profile.major || '';
  }
  if (elements.graduationYear) {
    elements.graduationYear.value = profile.graduation_year || profile.class_year || '';
  }
  if (elements.bannerColorPicker) {
    elements.bannerColorPicker.value = bannerColor;
  }
  if (elements.storageUsed?.length) {
    const storageText = formatBytes(SETTINGS_STATE.storageUsageBytes);
    elements.storageUsed.forEach((node) => {
      node.textContent = storageText;
    });
  }
  if (elements.storageDetails?.length) {
    const detailsText = `${formatCount(SETTINGS_STATE.notesCount, 'note')}, ${formatCount(SETTINGS_STATE.filesCount, 'file')}`;
    elements.storageDetails.forEach((node) => {
      node.textContent = detailsText;
    });
  }

  updateAvatarPreview(avatarUrl);
  paintBannerColor(bannerColor);
  renderProfilePreview();
  captureProfileBaseline();
  renderDiscordButton();

  if (elements.languageCombobox) {
    elements.languageCombobox.setValue(settings.language || 'en');
  } else if (elements.language) {
    elements.language.value = settings.language || 'en';
  }
  if (elements.timezoneCombobox) {
    elements.timezoneCombobox.setValue(settings.timezone || '');
  } else if (elements.timezone) {
    elements.timezone.value = settings.timezone || '';
  }
  if (elements.canvasFeedUrl) {
    elements.canvasFeedUrl.value = settings.canvas_ical_url || '';
  }
  renderOtherCalendarRows(SETTINGS_STATE.otherCalendarUrls);
}

function renderConnectedServices() {
  if (!elements.connectedServices) {
    return;
  }
  if (!SETTINGS_STATE.connectedServices.length) {
    elements.connectedServices.innerHTML = '<div class="settings-empty-state">No connected services.</div>';
    return;
  }

  elements.connectedServices.innerHTML = SETTINGS_STATE.connectedServices.map((service) => {
    const label = escapeHtml(service.name || 'Connected service');
    const detail = escapeHtml(service.detail || service.description || 'Connected');
    return `<div class="settings-empty-state settings-connected-item"><strong>${label}</strong><p>${detail}</p></div>`;
  }).join('');
}

function renderDiscordButton() {
  const button = elements.discordButton;
  if (!button) {
    return;
  }
  const discord = SETTINGS_STATE.discord || {};
  button.hidden = false;
  if (discord.linked) {
    button.classList.add('is-linked');
    button.innerHTML = `${DISCORD_ICON_SVG}<span>Linked</span>`;
    button.setAttribute('aria-label', 'Manage linked Discord account');
  } else {
    button.classList.remove('is-linked');
    button.innerHTML = `<span>Link</span>${DISCORD_ICON_SVG}<span class="material-symbols-outlined" aria-hidden="true">open_in_new</span>`;
    button.setAttribute('aria-label', 'Link your Discord account');
  }
}

function bindDiscordControls() {
  elements.discordButton?.addEventListener('click', () => {
    const discord = SETTINGS_STATE.discord || {};
    if (discord.linked) {
      openDiscordModal();
      return;
    }
    const url = elements.discordButton.getAttribute('data-link-url');
    if (url) {
      window.location.href = url;
    }
  });
  elements.discordModalClosers.forEach((node) => {
    node.addEventListener('click', closeDiscordModal);
  });
  elements.discordUnlink?.addEventListener('click', () => void handleDiscordUnlink());
  elements.discordRelink?.addEventListener('click', () => void handleDiscordRelink());
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && elements.discordModal && !elements.discordModal.hidden) {
      closeDiscordModal();
    }
  });
}

function openDiscordModal() {
  if (!elements.discordModal) {
    return;
  }
  const discord = SETTINGS_STATE.discord || {};
  const username = discord.username || 'account';
  if (elements.discordUnlink) {
    elements.discordUnlink.textContent = `Unlink ${username}`;
  }
  elements.discordModal.hidden = false;
  elements.discordModal.classList.add('is-open');
  document.body.classList.add('settings-discord-modal-open');
  requestAnimationFrame(() => elements.discordUnlink?.focus({ preventScroll: true }));
}

function closeDiscordModal() {
  if (!elements.discordModal) {
    return;
  }
  elements.discordModal.hidden = true;
  elements.discordModal.classList.remove('is-open');
  document.body.classList.remove('settings-discord-modal-open');
}

async function handleDiscordUnlink() {
  const previousDiscord = { ...(SETTINGS_STATE.discord || {}) };
  SETTINGS_STATE.discord = { linked: false, username: null };
  renderDiscordButton();
  closeDiscordModal();
  if (window.APStudyUndo?.stage) {
    window.APStudyUndo.stage({
      message: 'Discord account unlinked.',
      commit: ({ reason }) => fetchJson(SETTINGS_ENDPOINTS.discordUnlink, {
        method: 'POST',
        keepalive: reason === 'pagehide',
      }),
      restore: () => {
        SETTINGS_STATE.discord = previousDiscord;
        renderDiscordButton();
      },
      errorTitle: 'Couldn’t unlink Discord',
    });
    return;
  }
  try {
    await fetchJson(SETTINGS_ENDPOINTS.discordUnlink, { method: 'POST' });
  } catch (error) {
    SETTINGS_STATE.discord = previousDiscord;
    renderDiscordButton();
    showToast(error.message || 'Try again in a moment.', 'error', { title: 'Couldn’t unlink Discord' });
  }
}

async function handleDiscordRelink() {
  // Unlinking before re-linking keeps a single Discord identity per account.
  try {
    await fetchJson(SETTINGS_ENDPOINTS.discordUnlink, { method: 'POST' });
  } catch (error) {
    console.error(error);
  }
  const url = elements.discordButton?.getAttribute('data-link-url');
  if (url) {
    window.location.href = url;
  } else {
    closeDiscordModal();
  }
}

function notifyDiscordLinkResult() {
  let params;
  try {
    params = new URLSearchParams(window.location.search);
  } catch (error) {
    return;
  }
  if (params.get('discord') === 'error') {
    showToast('Try connecting your Discord account again.', 'error', { title: 'Couldn’t link Discord' });
    params.delete('discord');
    const query = params.toString();
    const newUrl = `${window.location.pathname}${query ? `?${query}` : ''}${window.location.hash}`;
    window.history.replaceState(null, '', newUrl);
  }
}

function syncHashOnLoad() {
  const targetId = normalizeSectionId(window.location.hash) || 'account';
  // Activate the section without scrolling. If a hash is present,
  // show the corresponding panel directly (no scrollIntoView).
  activateSection(targetId, { scroll: false, pushState: false });
}

function activateSection(sectionId, options = {}) {
  const normalized = SETTINGS_SECTION_IDS.includes(sectionId) ? sectionId : 'account';
  SETTINGS_STATE.activeSection = normalized;

  elements.tabs.forEach((tab) => {
    const isActive = tab.getAttribute('data-tab') === normalized;
    tab.classList.toggle('is-active', isActive);
    tab.setAttribute('aria-current', isActive ? 'page' : 'false');
  });

  if (options.pushState) {
    history.pushState(null, '', `#${normalized}`);
  }

  // Show/hide sections like tab panels instead of scrolling.
  elements.sections.forEach((section) => {
    const isActive = section.id === normalized;
    section.hidden = !isActive;
    section.setAttribute('aria-hidden', isActive ? 'false' : 'true');
  });

  if (normalized === 'tier') {
    void activateInvites();
  }
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initializeSettingsPage);
} else {
  initializeSettingsPage();
}
})(window);
