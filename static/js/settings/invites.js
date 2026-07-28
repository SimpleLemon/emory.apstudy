(function registerSettingsInvites(global) {
  function createSettingsInvites({
    elements,
    endpoints,
    callbacks,
  }) {
    const {
      copyText,
      escapeHtml,
      fetchJson,
      formatDate,
      showToast,
    } = callbacks;

    let loaded = false;
    let loading = false;
    let data = null;

    function setStatus(message = '', type = 'info') {
      if (!elements.invitesStatus) {
        return;
      }
      elements.invitesStatus.textContent = message;
      elements.invitesStatus.dataset.type = type;
      elements.invitesStatus.hidden = !message;
    }

    function renderSkeleton() {
      if (!elements.invitesList) {
        return;
      }
      elements.invitesList.innerHTML = `
        <div class="settings-invite-loading" aria-hidden="true">
          <span></span><span></span><span></span>
        </div>
        <span class="sr-only">Loading invite links…</span>
      `;
    }

    function renderLoadError(error) {
      if (!elements.invitesList) {
        return;
      }
      const message = escapeHtml(error?.message || 'Invite links could not be loaded.');
      elements.invitesList.innerHTML = `
        <div class="settings-invite-empty settings-invite-empty-error">
          <strong>Couldn’t load invite links</strong>
          <p>${message}</p>
          <button type="button" class="settings-button settings-button-secondary" data-invite-action="retry">Try again</button>
        </div>
      `;
    }

    function personInitials(person) {
      const source = String(person.name || person.username || 'Nest user').trim();
      const parts = source.split(/\s+/).filter(Boolean);
      if (!parts.length) {
        return 'N';
      }
      return parts.slice(0, 2).map((part) => part[0]).join('').toUpperCase();
    }

    function personRow(person) {
      const name = escapeHtml(person.name || 'Nest user');
      const username = person.username
        ? `@${escapeHtml(person.username)}`
        : 'No username';
      const pictureUrl = person.picture_url ? escapeHtml(person.picture_url) : '';
      const status = person.status === 'joined' ? 'Joined' : 'Invited';
      const statusClass = person.status === 'joined' ? ' is-joined' : '';
      const messageButton = person.can_message
        ? `<button type="button" class="settings-invite-action" data-invite-action="message" data-user-id="${escapeHtml(person.user_id)}">Message</button>`
        : '';
      const image = pictureUrl
        ? `<img src="${pictureUrl}" alt="" width="40" height="40" loading="lazy" decoding="async" data-invite-avatar-image />`
        : '';

      return `
        <div class="settings-invite-person">
          <span class="settings-invite-avatar" aria-hidden="true">
            <span>${escapeHtml(personInitials(person))}</span>
            ${image}
          </span>
          <span class="settings-invite-person-copy">
            <strong>${name}</strong>
            <small>${username}</small>
          </span>
          <span class="settings-invite-person-status${statusClass}">${status}</span>
          ${messageButton}
        </div>
      `;
    }

    function peopleRegion(invitation) {
      const people = Array.isArray(invitation.people) ? invitation.people : [];
      if (!invitation.invited_count) {
        return '';
      }
      if (!people.length) {
        return `
          <p class="settings-invite-history-note">
            ${invitation.invited_count} attributed ${invitation.invited_count === 1 ? 'account is' : 'accounts are'} no longer available.
          </p>
        `;
      }
      return `
        <details class="settings-invite-people">
          <summary>
            <span>People</span>
            <span>${people.length}</span>
          </summary>
          <div class="settings-invite-person-list">
            ${people.map(personRow).join('')}
          </div>
        </details>
      `;
    }

    function inviteRow(invitation) {
      const inviteId = escapeHtml(invitation.id);
      const code = escapeHtml(invitation.code);
      const url = escapeHtml(invitation.url);
      const label = invitation.label
        ? escapeHtml(invitation.label)
        : 'No label';
      const activeLabel = invitation.is_active ? 'Active' : 'Inactive';
      const statusClass = invitation.is_active ? '' : ' is-inactive';
      const nextAction = invitation.is_active ? 'deactivate' : 'reactivate';
      const nextActionLabel = invitation.is_active ? 'Deactivate' : 'Reactivate';
      const invitedCount = Number(invitation.invited_count || 0);
      const joinedCount = Number(invitation.joined_count || 0);
      const created = invitation.created_at
        ? `Created ${escapeHtml(formatDate(invitation.created_at))}`
        : '';

      return `
        <article class="settings-invite-row${statusClass}" data-invite-id="${inviteId}">
          <div class="settings-invite-row-head">
            <div class="settings-invite-identity">
              <span class="settings-invite-code">${code}</span>
              <span class="settings-invite-state">${activeLabel}</span>
            </div>
            <div class="settings-invite-counts" aria-label="${invitedCount} invited, ${joinedCount} joined">
              <span
                class="tier-badge-trigger settings-invite-count settings-invite-count--invited"
                role="button"
                tabindex="0"
                aria-expanded="false"
                aria-label="${invitedCount} invited. People who signed up using this invite link."
                data-tooltip="People who signed up using this invite link."
              >
                <strong>${invitedCount}</strong><span>Invited</span>
              </span>
              <span
                class="tier-badge-trigger settings-invite-count settings-invite-count--joined"
                role="button"
                tabindex="0"
                aria-expanded="false"
                aria-label="${joinedCount} joined. People who completed onboarding and took a qualifying action."
                data-tooltip="People who completed onboarding and took a qualifying action."
              >
                <strong>${joinedCount}</strong><span>Joined</span>
              </span>
            </div>
          </div>
          <div class="settings-invite-label-row" data-invite-label-display>
            <div>
              <strong>${label}</strong>
              ${created ? `<small>${created}</small>` : ''}
            </div>
            <button type="button" class="settings-invite-action" data-invite-action="rename">Rename</button>
          </div>
          <form class="settings-invite-rename" data-invite-rename hidden>
            <label class="sr-only" for="settings-invite-rename-${inviteId}">Invite label</label>
            <input id="settings-invite-rename-${inviteId}" type="text" maxlength="80" value="${escapeHtml(invitation.label || '')}" placeholder="Optional label" />
            <button type="submit" class="settings-invite-action is-primary">Save</button>
            <button type="button" class="settings-invite-action" data-invite-action="cancel-rename">Cancel</button>
          </form>
          <div class="settings-invite-actions">
            <button type="button" class="settings-invite-action is-primary" data-invite-action="copy" data-invite-url="${url}">Copy link</button>
            <button type="button" class="settings-invite-action" data-invite-action="${nextAction}">${nextActionLabel}</button>
          </div>
          ${peopleRegion(invitation)}
        </article>
      `;
    }

    function syncCreateAvailability() {
      if (!data || !elements.inviteCreateButton || !elements.inviteLabel) {
        return;
      }
      const limit = Number(data.empty_invite_limit || 5);
      elements.inviteCreateButton.disabled = !data.can_create;
      elements.inviteLabel.disabled = !data.can_create;
      if (!data.can_create) {
        setStatus(
          `You’ve reached the limit of ${limit} unused invite links. Share an existing link before creating another.`,
          'error',
        );
      }
    }

    function bindAvatarFallbacks() {
      elements.invitesList?.querySelectorAll('[data-invite-avatar-image]').forEach((image) => {
        image.addEventListener('error', () => image.remove(), { once: true });
      });
    }

    function render() {
      if (!elements.invitesList || !data) {
        return;
      }
      const invitations = Array.isArray(data.invites) ? data.invites : [];
      if (!invitations.length) {
        elements.invitesList.innerHTML = `
          <div class="settings-invite-empty">
            <strong>No invite links yet</strong>
            <p>Add an optional label above, then create your first reusable link.</p>
          </div>
        `;
      } else {
        elements.invitesList.innerHTML = invitations.map(inviteRow).join('');
      }
      syncCreateAvailability();
      bindAvatarFallbacks();
    }

    function focusInviteAction(inviteId, action) {
      const row = Array.from(
        elements.invitesList?.querySelectorAll('[data-invite-id]') || [],
      ).find((candidate) => candidate.dataset.inviteId === String(inviteId));
      row?.querySelector(`[data-invite-action="${action}"]`)?.focus();
    }

    async function loadInvites({ force = false } = {}) {
      if (loading || (loaded && !force)) {
        return;
      }
      loading = true;
      renderSkeleton();
      setStatus();
      try {
        data = await fetchJson(endpoints.invites);
        loaded = true;
        render();
      } catch (error) {
        loaded = false;
        renderLoadError(error);
      } finally {
        loading = false;
      }
    }

    async function createInvite() {
      if (!elements.inviteCreateButton || !elements.inviteLabel) {
        return;
      }
      elements.inviteCreateButton.disabled = true;
      setStatus('Creating invite…');
      try {
        data = await fetchJson(endpoints.invites, {
          method: 'POST',
          body: JSON.stringify({ label: elements.inviteLabel.value.trim() }),
        });
        loaded = true;
        elements.inviteLabel.value = '';
        setStatus('Invite link created.', 'success');
        render();
        showToast('Invite link created.', 'success');
      } catch (error) {
        setStatus(error.message || 'Try again in a moment.', 'error');
        showToast(error.message || 'Try again in a moment.', 'error', { title: 'Couldn’t create invite' });
        if (data) {
          syncCreateAvailability();
        } else {
          elements.inviteCreateButton.disabled = false;
          elements.inviteLabel.disabled = false;
        }
      }
    }

    async function patchInvite(inviteId, updates, successMessage, focusAction) {
      setStatus(successMessage.replace(/\.$/, '…'));
      try {
        data = await fetchJson(`${endpoints.invites}/${encodeURIComponent(inviteId)}`, {
          method: 'PATCH',
          body: JSON.stringify(updates),
        });
        loaded = true;
        setStatus(successMessage, 'success');
        render();
        if (focusAction) {
          focusInviteAction(inviteId, focusAction);
        }
      } catch (error) {
        setStatus(error.message || 'Try again in a moment.', 'error');
        showToast(error.message || 'Try again in a moment.', 'error', { title: 'Couldn’t update invite' });
      }
    }

    function startRename(row) {
      const display = row.querySelector('[data-invite-label-display]');
      const form = row.querySelector('[data-invite-rename]');
      if (!display || !form) {
        return;
      }
      display.hidden = true;
      form.hidden = false;
      form.querySelector('input')?.focus();
    }

    function cancelRename(row) {
      const display = row.querySelector('[data-invite-label-display]');
      const form = row.querySelector('[data-invite-rename]');
      if (!display || !form) {
        return;
      }
      form.hidden = true;
      display.hidden = false;
      display.querySelector('[data-invite-action="rename"]')?.focus();
    }

    async function copyInvite(button) {
      const url = button.dataset.inviteUrl || '';
      if (!url) {
        return;
      }
      try {
        await copyText(url);
        const previousText = button.textContent;
        button.textContent = 'Copied';
        showToast('Invite link copied.', 'success');
        global.setTimeout(() => {
          if (button.isConnected) {
            button.textContent = previousText;
          }
        }, 1200);
      } catch (error) {
        showToast('Copy the invite URL manually and try again.', 'error', { title: 'Couldn’t copy link' });
      }
    }

    async function startMessage(button) {
      button.disabled = true;
      try {
        const response = await fetchJson(endpoints.dmThreads, {
          method: 'POST',
          body: JSON.stringify({ user_id: button.dataset.userId }),
        });
        const threadId = response?.thread?.id;
        if (!threadId) {
          throw new Error('The conversation could not be opened.');
        }
        global.location.assign(`/chat?thread=${encodeURIComponent(threadId)}`);
      } catch (error) {
        button.disabled = false;
        showToast(error.message || 'Try again in a moment.', 'error', { title: 'Couldn’t start message' });
      }
    }

    function bindInviteControls() {
      elements.inviteCreateForm?.addEventListener('submit', (event) => {
        event.preventDefault();
        void createInvite();
      });

      elements.invitesList?.addEventListener('click', (event) => {
        const button = event.target.closest('[data-invite-action]');
        if (!button) {
          return;
        }
        const row = button.closest('[data-invite-id]');
        const inviteId = row?.dataset.inviteId;
        const action = button.dataset.inviteAction;
        if (action === 'retry') {
          void loadInvites({ force: true });
        } else if (action === 'copy') {
          void copyInvite(button);
        } else if (action === 'rename' && row) {
          startRename(row);
        } else if (action === 'cancel-rename' && row) {
          cancelRename(row);
        } else if (action === 'deactivate' && inviteId) {
          void patchInvite(
            inviteId,
            { is_active: false },
            'Invite deactivated.',
            'reactivate',
          );
        } else if (action === 'reactivate' && inviteId) {
          void patchInvite(
            inviteId,
            { is_active: true },
            'Invite reactivated.',
            'deactivate',
          );
        } else if (action === 'message') {
          void startMessage(button);
        }
      });

      elements.invitesList?.addEventListener('submit', (event) => {
        const form = event.target.closest('[data-invite-rename]');
        const row = form?.closest('[data-invite-id]');
        if (!form || !row) {
          return;
        }
        event.preventDefault();
        const input = form.querySelector('input');
        void patchInvite(
          row.dataset.inviteId,
          { label: input?.value.trim() || '' },
          'Invite label updated.',
          'rename',
        );
      });

      elements.invitesList?.addEventListener('keydown', (event) => {
        if (event.key !== 'Escape') {
          return;
        }
        const row = event.target.closest('[data-invite-id]');
        if (row) {
          cancelRename(row);
        }
      });
    }

    return {
      activateInvites: loadInvites,
      bindInviteControls,
    };
  }

  global.APStudySettingsInvites = {
    createSettingsInvites,
  };
})(window);
