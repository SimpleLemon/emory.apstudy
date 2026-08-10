(function registerSettingsProfile(global) {
  const USERNAME_MIN_LENGTH = 3;
  const USERNAME_MAX_LENGTH = 20;
  const PROFILE_TEXT_FIELDS = [
    { element: 'displayName', counter: 'settings-display-name-counter', error: 'settings-display-name-error', label: 'Display name', minimum: 1, maximum: 80 },
    { element: 'school', counter: 'settings-school-counter', error: 'settings-school-error', label: 'School', minimum: 0, maximum: 160 },
    { element: 'major', counter: 'settings-major-counter', error: 'settings-major-error', label: 'Major', minimum: 0, maximum: 120 },
  ];
  const USERNAME_PATTERN = /^[a-zA-Z0-9_-]+$/;
  const USERNAME_RESERVED = new Set([
    'account',
    'admin',
    'api',
    'auth',
    'calendar',
    'dashboard',
    'data',
    'files',
    'login',
    'logout',
    'notes',
    'onboarding',
    'preferences',
    'profile',
    'settings',
    'signup',
    'u',
    'user',
    'users',
  ]);

  function createSettingsProfile({
    elements,
    state,
    endpoints,
    callbacks,
  }) {
    const {
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
    } = callbacks;
    let schoolSuggestionTimer = null;
    let avatarModalCloseTimer = null;

    function codePointLength(value) {
      return Array.from(String(value || '').trim()).length;
    }

    function validateProfileTextField(field, { announce = true } = {}) {
      const input = elements[field.element];
      const counter = document.getElementById(field.counter);
      const error = document.getElementById(field.error);
      if (!input) return true;
      const value = input.value.trim();
      const length = codePointLength(value);
      let message = '';
      if (length < field.minimum) {
        message = `${field.label} is required.`;
      } else if (length > field.maximum) {
        message = `${field.label} must be ${field.maximum} characters or fewer.`;
      }
      if (counter) counter.textContent = `${length} / ${field.maximum} characters`;
      if (error) {
        error.textContent = announce ? message : '';
        error.hidden = !message;
      }
      input.setCustomValidity(message);
      input.setAttribute('aria-invalid', String(Boolean(message)));
      if (message) global.APStudyFormField?.markInvalid?.(input);
      else global.APStudyFormField?.clearInvalid?.(input);
      return !message;
    }

    function validateProfileTextFields(options) {
      return PROFILE_TEXT_FIELDS.every((field) => validateProfileTextField(field, options));
    }

    function hasImageFiles(event) {
      const types = Array.from(event.dataTransfer?.types || []);
      return types.includes('Files');
    }

    function setAvatarUploadBusy(isBusy) {
      if (elements.avatarUpload) elements.avatarUpload.disabled = isBusy;
      if (elements.avatarUploadButton) elements.avatarUploadButton.disabled = isBusy;
      if (elements.avatarFileButton) elements.avatarFileButton.disabled = isBusy;
      elements.avatarUploadDropzone?.classList.toggle('is-uploading', isBusy);
      if (elements.avatarUploadDropzone) {
        elements.avatarUploadDropzone.tabIndex = isBusy ? -1 : 0;
      }
    }

    function setAvatarUploadStatus(message) {
      if (elements.avatarUploadStatus) elements.avatarUploadStatus.textContent = message;
      if (elements.avatarModalStatus) elements.avatarModalStatus.textContent = message;
    }

    function syncAvatarDropzonePreview(value) {
      const avatarValue = value && String(value).trim() ? String(value).trim() : '';
      const hasAvatar = Boolean(avatarValue);

      if (elements.avatarDropzonePreview) {
        if (hasAvatar) {
          elements.avatarDropzonePreview.src = settingsAvatarUrlForSize(avatarValue, 176);
          elements.avatarDropzonePreview.removeAttribute('hidden');
        } else {
          elements.avatarDropzonePreview.setAttribute('hidden', '');
        }
        elements.avatarDropzonePreview.onerror = () => {
          elements.avatarDropzonePreview.onerror = null;
          elements.avatarDropzonePreview.setAttribute('hidden', '');
          elements.avatarDropzonePlaceholder?.removeAttribute('hidden');
        };
      }

      if (elements.avatarDropzonePlaceholder) {
        if (hasAvatar) {
          elements.avatarDropzonePlaceholder.setAttribute('hidden', '');
        } else {
          elements.avatarDropzonePlaceholder.removeAttribute('hidden');
        }
      }
    }

    function handleAvatarFile(file) {
      if (!file) {
        return;
      }
      void uploadAvatar(file);
    }

    function openAvatarModal() {
      if (!elements.avatarModal) {
        elements.avatarUpload?.click();
        return;
      }
      global.clearTimeout(avatarModalCloseTimer);
      elements.avatarModal.hidden = false;
      elements.avatarModal.classList.add('is-open');
      document.body.classList.add('settings-avatar-modal-open');
      requestAnimationFrame(() => {
        elements.avatarUploadDropzone?.focus({ preventScroll: true });
      });
    }

    function closeAvatarModal({ returnFocus = true } = {}) {
      if (!elements.avatarModal) {
        return;
      }
      global.clearTimeout(avatarModalCloseTimer);
      elements.avatarModal.hidden = true;
      elements.avatarModal.classList.remove('is-open');
      document.body.classList.remove('settings-avatar-modal-open');
      elements.avatarUploadDropzone?.classList.remove('is-active');
      if (returnFocus) {
        elements.avatarUploadButton?.focus({ preventScroll: true });
      }
    }

    function bindProfilePreviewControls() {
      elements.avatarUploadButton?.addEventListener('click', () => {
        openAvatarModal();
      });
      elements.avatarFileButton?.addEventListener('click', () => {
        elements.avatarUpload?.click();
      });
      elements.avatarUpload?.addEventListener('change', () => {
        const file = elements.avatarUpload.files && elements.avatarUpload.files[0];
        handleAvatarFile(file);
      });
      elements.avatarUploadDropzone?.addEventListener('click', () => {
        elements.avatarUpload?.click();
      });
      elements.avatarUploadDropzone?.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          elements.avatarUpload?.click();
        }
      });
      elements.avatarUploadDropzone?.addEventListener('dragover', (event) => {
        if (!hasImageFiles(event)) {
          return;
        }
        event.preventDefault();
        elements.avatarUploadDropzone.classList.add('is-active');
      });
      elements.avatarUploadDropzone?.addEventListener('dragleave', () => {
        elements.avatarUploadDropzone.classList.remove('is-active');
      });
      elements.avatarUploadDropzone?.addEventListener('drop', (event) => {
        if (!hasImageFiles(event)) {
          return;
        }
        event.preventDefault();
        event.stopPropagation();
        elements.avatarUploadDropzone.classList.remove('is-active');
        const file = event.dataTransfer?.files && event.dataTransfer.files[0];
        handleAvatarFile(file);
      });
      elements.avatarModalClosers?.forEach((node) => {
        node.addEventListener('click', () => closeAvatarModal());
      });
      document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape' && elements.avatarModal && !elements.avatarModal.hidden) {
          closeAvatarModal();
        }
      });
      elements.displayName?.addEventListener('input', () => {
        validateProfileTextField(PROFILE_TEXT_FIELDS[0]);
        renderProfilePreview();
        updateProfileDirtyState();
      });
      elements.username?.addEventListener('input', renderProfilePreview);
      elements.username?.addEventListener('input', updateProfileDirtyState);
      global.APStudyFormField?.bindAutoClear?.(elements.username);
      elements.school?.addEventListener('input', () => {
        validateProfileTextField(PROFILE_TEXT_FIELDS[1]);
        renderProfilePreview();
        updateProfileDirtyState();
      });
      elements.school?.addEventListener('input', debounceSchoolSuggestions);
      elements.major?.addEventListener('input', () => {
        validateProfileTextField(PROFILE_TEXT_FIELDS[2]);
        renderProfilePreview();
        updateProfileDirtyState();
      });
      elements.graduationYear?.addEventListener('input', renderProfilePreview);
      elements.graduationYear?.addEventListener('input', updateProfileDirtyState);
      elements.bannerColorPicker?.addEventListener('input', () => {
        const nextColor = normalizeHexColor(elements.bannerColorPicker.value);
        paintBannerColor(nextColor);
        updateProfileDirtyState();
      });
      validateProfileTextFields();
    }

    function getProfileUrl() {
      const profile = state.profile || {};
      const accountData = state.account || {};
      const username = elements.username?.value.trim() || profile.username || '';
      if (username) {
        return `${global.location.origin}/u/${encodeURIComponent(username)}`;
      }
      const userId = profile.id
        || elements.userId?.value
        || accountData.$id
        || accountData.id
        || '';
      if (!userId) {
        return '';
      }
      return `${global.location.origin}/user/${encodeURIComponent(userId)}`;
    }

    function openProfileLink() {
      const profileUrl = getProfileUrl();
      if (!profileUrl) {
        showToast('Profile link is unavailable right now.', 'error');
        return;
      }
      global.open(profileUrl, '_blank', 'noopener');
    }

    async function shareProfileLink() {
      const profileUrl = getProfileUrl();
      if (!profileUrl) {
        showToast('Profile link is unavailable right now.', 'error');
        return;
      }
      await copyText(profileUrl);
      showToast('Copied profile link.', 'success');
    }

    async function saveProfile() {
      const currentProfile = state.profile || {};
      if (!validateProfileTextFields()) {
        return;
      }
      const rawUsername = elements.username?.value.trim() || '';
      if (!rawUsername) {
        global.APStudyFormField?.markInvalid?.(elements.username);
        showToast('Username is required.', 'error');
        return;
      }
      const normalizedUsername = normalizeUsername(rawUsername);
      if (!USERNAME_PATTERN.test(normalizedUsername)) {
        global.APStudyFormField?.markInvalid?.(elements.username);
        showToast('Please only use numbers, letters, dashes -, or underscores _.', 'error');
        return;
      }
      if (normalizedUsername.length < USERNAME_MIN_LENGTH || normalizedUsername.length > USERNAME_MAX_LENGTH) {
        global.APStudyFormField?.markInvalid?.(elements.username);
        showToast('Username must be between 3 and 20 characters.', 'error');
        return;
      }
      if (USERNAME_RESERVED.has(normalizedUsername)) {
        global.APStudyFormField?.markInvalid?.(elements.username);
        showToast('That username is reserved.', 'error');
        return;
      }
      global.APStudyFormField?.clearInvalid?.(elements.username);
      if (elements.username) {
        elements.username.value = normalizedUsername;
      }
      const payload = {
        name: elements.displayName?.value.trim() || '',
        username: normalizedUsername,
        picture_url: currentProfile.picture_url || '',
        avatar_source: currentProfile.avatar_source || (currentProfile.picture_url ? 'provider' : ''),
        banner_color: normalizeHexColor(elements.bannerColorPicker?.value || currentProfile.banner_color || '#fecae1'),
        school: elements.school?.value.trim() || '',
        major: elements.major?.value.trim() || '',
        graduation_year: elements.graduationYear?.value.trim() || '',
      };

      try {
        state.profileSaving = true;
        const response = await fetchJson(endpoints.profile, {
          method: 'POST',
          body: JSON.stringify(payload),
        });

        state.profile = {
          ...(state.profile || {}),
          ...response,
        };
        populateFields();
        updateNavbarAvatar(response.picture_url || '');
        captureProfileBaseline();
        showToast('Profile saved.', 'success');
      } catch (error) {
        showToast(error.message || 'Try again in a moment.', 'error', { title: 'Couldn’t save profile' });
      } finally {
        state.profileSaving = false;
      }
    }

    async function uploadAvatar(file) {
      const allowedTypes = ['image/jpeg', 'image/png', 'image/gif', 'image/webp'];
      if (!allowedTypes.includes(file.type)) {
        showToast('Avatar must be a JPG, PNG, GIF, or WebP image.', 'error');
        if (elements.avatarUpload) elements.avatarUpload.value = '';
        return;
      }
      if (file.size > 10 * 1024 * 1024) {
        showToast('Avatar must be 10 MB or smaller.', 'error');
        if (elements.avatarUpload) elements.avatarUpload.value = '';
        return;
      }

      const formData = new FormData();
      formData.append('avatar', file);
      setAvatarUploadBusy(true);
      setAvatarUploadStatus('Uploading...');

      try {
        const response = await fetchFormData(endpoints.avatarUpload, formData);
        state.profile = {
          ...(state.profile || {}),
          ...response,
        };
        updateAvatarPreview(response.picture_url || '');
        updateNavbarAvatar(response.picture_url || '');
        setAvatarUploadStatus('Avatar uploaded.');
        captureProfileBaseline();
        showToast('Avatar uploaded.', 'success');
        avatarModalCloseTimer = global.setTimeout(() => closeAvatarModal(), 450);
      } catch (error) {
        setAvatarUploadStatus('JPG, PNG, GIF, or WebP. Max 10 MB.');
        showToast(error.message || 'Check the image and try again.', 'error', { title: 'Couldn’t upload avatar' });
      } finally {
        setAvatarUploadBusy(false);
        if (elements.avatarUpload) {
          elements.avatarUpload.value = '';
        }
      }
    }

    function updateNavbarAvatar(pictureUrl) {
      const navbarAvatar = document.querySelector('#navbar-avatar-btn img');
      if (!navbarAvatar || !pictureUrl) {
        return;
      }
      navbarAvatar.src = settingsAvatarUrlForSize(pictureUrl, 48);
      navbarAvatar.srcset = `${settingsAvatarUrlForSize(pictureUrl, 48)} 1x, ${settingsAvatarUrlForSize(pictureUrl, 96)} 2x`;
      navbarAvatar.sizes = '48px';
    }

    function debounceSchoolSuggestions() {
      if (!elements.school || !elements.universityOptions) {
        return;
      }
      global.clearTimeout(schoolSuggestionTimer);
      schoolSuggestionTimer = global.setTimeout(() => {
        void loadSchoolSuggestions(elements.school.value);
      }, 180);
    }

    async function loadSchoolSuggestions(query) {
      const term = String(query || '').trim();
      if (term.length < 2 || !elements.universityOptions) {
        return;
      }
      try {
        const data = await fetchJson(`${endpoints.universities}?q=${encodeURIComponent(term)}`);
        const results = Array.isArray(data.results) ? data.results : [];
        elements.universityOptions.innerHTML = results.map((school) => {
          const label = [school.name, school.city, school.state].filter(Boolean).join(' - ');
          return `<option value="${escapeHtml(school.name)}" label="${escapeHtml(label)}"></option>`;
        }).join('');
      } catch (error) {
        console.warn('Unable to load school suggestions', error);
      }
    }

    function updateAvatarPreview(value) {
      const avatarValue = value && value.trim() ? value.trim() : '';
      syncAvatarDropzonePreview(avatarValue);
      if (!elements.avatarPreview) {
        return;
      }
      elements.avatarPreview.src = settingsAvatarUrlForSize(avatarValue, 150);
      elements.avatarPreview.srcset = `${settingsAvatarUrlForSize(avatarValue, 150)} 1x, ${settingsAvatarUrlForSize(avatarValue, 300)} 2x`;
      elements.avatarPreview.sizes = '(max-width: 640px) 96px, 150px';
      elements.avatarPreview.onerror = () => {
        elements.avatarPreview.onerror = null;
        elements.avatarPreview.src = settingsAvatarUrlForSize('', 150);
        elements.avatarPreview.srcset = `${settingsAvatarUrlForSize('', 150)} 1x, ${settingsAvatarUrlForSize('', 300)} 2x`;
      };
      renderProfilePreview();
    }

    function settingsAvatarUrlForSize(url, size = 32) {
      if (typeof global.APSTUDY_AVATAR_URL_FOR_SIZE === 'function') {
        return global.APSTUDY_AVATAR_URL_FOR_SIZE(url, size);
      }
      return String(url || '').trim();
    }

    function renderProfilePreview() {
      validateProfileTextFields();
      const profile = state.profile || {};
      const accountData = state.account || {};
      const displayName = elements.displayName?.value.trim() || profile.name || accountData.name || 'APStudy User';
      const username = elements.username?.value.trim() || profile.username || '';
      const school = elements.school?.value.trim() || profile.school || 'Not set';
      const major = elements.major?.value.trim() || profile.major || 'Not set';
      const graduation = elements.graduationYear?.value.trim()
        || profile.graduation_year
        || profile.class_year
        || 'Not set';
      const education = profile.education_level || 'Not set';
      const createdAt = elements.accountCreated?.value
        || profile.member_since
        || formatDate(profile.created_at || accountData.registration || accountData.$createdAt)
        || 'Not set';

      if (elements.previewName) elements.previewName.textContent = displayName;
      if (elements.previewHandle) {
        elements.previewHandle.textContent = profileHandle(
          displayName,
          username,
          profile.id || accountData.$id || accountData.id,
        );
      }
      if (elements.previewSchool) elements.previewSchool.textContent = school;
      if (elements.previewMajor) elements.previewMajor.textContent = major;
      if (elements.previewGraduation) elements.previewGraduation.textContent = graduation;
      if (elements.previewEducation) elements.previewEducation.textContent = education;
      if (elements.previewCreated) elements.previewCreated.textContent = createdAt;
      elements.previewSchoolCard?.classList.toggle('profile-tile-detail-emory', isEmorySchool(school));
      elements.previewMemberCard?.classList.toggle(
        'profile-tile-detail-early-member',
        isEarlyMember(profile.created_at || accountData.registration || accountData.$createdAt),
      );
    }

    function captureProfileBaseline() {
      state.profileBaseline = getProfileFormValues();
      state.profileDirty = false;
    }

    function getProfileFormValues() {
      return {
        name: elements.displayName?.value.trim() || '',
        username: elements.username?.value.trim() || '',
        school: elements.school?.value.trim() || '',
        major: elements.major?.value.trim() || '',
        graduation_year: elements.graduationYear?.value.trim() || '',
        banner_color: normalizeHexColor(elements.bannerColorPicker?.value || ''),
      };
    }

    function hasUnsavedProfileChanges() {
      if (!state.profileBaseline) {
        return false;
      }
      const currentValues = getProfileFormValues();
      const baseline = state.profileBaseline;
      return Object.keys(baseline).some((key) => currentValues[key] !== baseline[key]);
    }

    function updateProfileDirtyState() {
      state.profileDirty = hasUnsavedProfileChanges();
    }

    function paintBannerColor(value) {
      const color = normalizeHexColor(value);
      if (elements.profileTile) {
        elements.profileTile.style.setProperty('--profile-banner-color', color);
      }
      if (elements.bannerSwatch) {
        elements.bannerSwatch.style.setProperty('--settings-banner-tile-color', color);
      }
    }

    return {
      bindProfilePreviewControls,
      captureProfileBaseline,
      hasUnsavedProfileChanges,
      openProfileLink,
      paintBannerColor,
      renderProfilePreview,
      saveProfile,
      shareProfileLink,
      updateAvatarPreview,
      validateProfileTextFields,
    };
  }

  global.APStudySettingsProfile = {
    createSettingsProfile,
  };
})(window);
