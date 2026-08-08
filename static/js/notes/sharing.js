(function registerNotesSharing(global) {
    const { escapeHtml } = global.APStudyUIPrimitives;
    const ROLE_OPTIONS = [
        { value: 'viewer', label: 'Viewer', description: 'Can view live content.' },
        { value: 'reviewer', label: 'Reviewer', description: 'Can comment and suggest changes.' },
        { value: 'editor', label: 'Editor', description: 'Can edit and manage sharing.' },
    ];
    const VALID_ROLES = new Set(ROLE_OPTIONS.map((role) => role.value));
    let activeModal = null;
    let returnFocus = null;
    let searchTimer = null;

    function normalizeRole(value) {
        const role = String(value || 'viewer').toLowerCase();
        return VALID_ROLES.has(role) ? role : 'viewer';
    }

    function roleLabel(role) {
        return ROLE_OPTIONS.find((option) => option.value === normalizeRole(role))?.label || 'Viewer';
    }

    function roleSelectHtml(selectedRole, attrs = '') {
        const role = normalizeRole(selectedRole);
        return `
            <select class="notes-share-role-select" ${attrs}>
                ${ROLE_OPTIONS.map((option) => `
                    <option value="${option.value}" ${option.value === role ? 'selected' : ''}>${option.label}</option>
                `).join('')}
            </select>
        `;
    }

    function initialsFor(value) {
        const words = String(value || 'Nest User').trim().split(/\s+/).filter(Boolean);
        return words.slice(0, 2).map((word) => word[0]?.toUpperCase() || '').join('') || 'N';
    }

    async function apiJson(url, options = {}) {
        const response = await fetch(url, {
            ...options,
            headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
            const error = new Error(payload.error || 'Unable to update sharing.');
            error.payload = payload;
            error.status = response.status;
            throw error;
        }
        return payload;
    }

    function endpointFor(resourceType, resourceId) {
        const encoded = encodeURIComponent(resourceId);
        return resourceType === 'folder'
            ? `/api/notes/folders/${encoded}/sharing`
            : `/api/notes/${encoded}/sharing`;
    }

    function close() {
        if (!activeModal) return;
        activeModal.remove();
        activeModal = null;
        clearTimeout(searchTimer);
        document.body.classList.remove('notes-modal-open');
        returnFocus?.focus?.({ preventScroll: true });
        returnFocus = null;
    }

    async function copyText(value) {
        if (navigator.clipboard?.writeText) {
            await navigator.clipboard.writeText(value);
            return;
        }
        const input = document.createElement('textarea');
        input.value = value;
        input.style.position = 'fixed';
        input.style.opacity = '0';
        document.body.appendChild(input);
        input.select();
        document.execCommand('copy');
        input.remove();
    }

    function avatarHtml(person) {
        if (person?.picture_url) {
            return `<img src="${escapeHtml(person.picture_url)}" alt="" loading="lazy">`;
        }
        return `<span class="notes-share-initials" aria-hidden="true">${escapeHtml(initialsFor(person?.name || person?.username))}</span>`;
    }

    function renderSelected(modal, users) {
        const list = modal.querySelector('[data-share-selected]');
        if (!list) return;
        list.innerHTML = users.length ? users.map((user) => `
            <div class="notes-share-person" data-selected-user="${escapeHtml(user.id)}">
                ${avatarHtml(user)}
                <span class="notes-share-person-copy">
                    <strong>${escapeHtml(user.name || user.username || 'Nest User')}</strong>
                    <small>${escapeHtml(user.username ? `@${user.username}` : 'Nest user')}</small>
                </span>
                ${roleSelectHtml(user.role, `data-user-role="${escapeHtml(user.id)}" aria-label="Role for ${escapeHtml(user.name || user.username || 'user')}"`)}
                <button type="button" class="notes-share-remove" data-remove-user="${escapeHtml(user.id)}" aria-label="Remove ${escapeHtml(user.name || user.username || 'user')}">
                    <span class="material-symbols-outlined" aria-hidden="true">close</span>
                </button>
            </div>
        `).join('') : '<p class="notes-share-empty">No direct user grants yet.</p>';
    }

    function renderPending(modal, pending) {
        const list = modal.querySelector('[data-share-pending]');
        if (!list) return;
        list.innerHTML = pending.length ? pending.map((invite) => `
            <div class="notes-share-person notes-share-person--pending" data-pending-email="${escapeHtml(invite.email)}">
                <span class="notes-share-initials" aria-hidden="true">@</span>
                <span class="notes-share-person-copy">
                    <strong>${escapeHtml(invite.email)}</strong>
                    <small>Pending · expires ${escapeHtml(invite.expires_at || 'in 7 days')}</small>
                </span>
                ${roleSelectHtml(invite.role, `data-pending-role="${escapeHtml(invite.email)}" aria-label="Pending role for ${escapeHtml(invite.email)}"`)}
                <button type="button" class="notes-share-remove" data-remove-pending="${escapeHtml(invite.email)}" aria-label="Remove pending invitation for ${escapeHtml(invite.email)}">
                    <span class="material-symbols-outlined" aria-hidden="true">close</span>
                </button>
            </div>
        `).join('') : '<p class="notes-share-empty">No pending email invitations.</p>';
    }

    function renderInherited(modal, inherited) {
        const list = modal.querySelector('[data-share-inherited]');
        if (!list) return;
        list.innerHTML = inherited.length ? inherited.map((entry) => `
            <div class="notes-share-person notes-share-person--inherited">
                ${entry.id === '*' ? '<span class="material-symbols-outlined" aria-hidden="true">link</span>' : avatarHtml(entry)}
                <span class="notes-share-person-copy">
                    <strong>${escapeHtml(entry.name || entry.username || 'Inherited access')}</strong>
                    <small>${escapeHtml(roleLabel(entry.role))} from ${escapeHtml(entry.source_label || 'folder')}</small>
                </span>
                <span class="notes-share-role">${escapeHtml(roleLabel(entry.role))}</span>
            </div>
        `).join('') : '<p class="notes-share-empty">No inherited access.</p>';
    }

    function renderSearchResults(modal, results, selectedIds, emailState) {
        const container = modal.querySelector('[data-share-results]');
        if (!container) return;
        const available = results.filter((user) => !selectedIds.has(String(user.id)));
        container.hidden = false;
        const resultHtml = available.map((user) => `
            <button type="button" class="notes-share-result" data-add-user="${escapeHtml(user.id)}">
                ${avatarHtml(user)}
                <span>
                    <strong>${escapeHtml(user.name || user.username || 'Nest User')}</strong>
                    <small>${escapeHtml(user.username ? `@${user.username}` : 'Nest user')} · add as ${escapeHtml(roleLabel(modal._shareAddRole))}</small>
                </span>
            </button>
        `).join('');
        const emailHtml = emailState?.status === 'unmatched' ? `
            <button type="button" class="notes-share-result notes-share-result--email" data-add-email="${escapeHtml(emailState.query)}">
                <span class="material-symbols-outlined" aria-hidden="true">alternate_email</span>
                <span>
                    <strong>${escapeHtml(emailState.query)}</strong>
                    <small>${escapeHtml(emailState.warning || 'Invite by email. Recipient must sign up with this OAuth email.')}</small>
                </span>
            </button>
        ` : '';
        container.innerHTML = resultHtml || emailHtml
            ? `${resultHtml}${emailHtml}`
            : '<p class="notes-share-empty">No matching users.</p>';
    }

    function setError(modal, message = '') {
        const error = modal.querySelector('[data-share-error]');
        if (!error) return;
        error.textContent = message;
        error.hidden = !message;
    }

    function buildModal(resourceType, resourceId, resourceTitle, sharing, onSaved) {
        const users = [...(sharing.users || [])].map((user) => ({ ...user, role: normalizeRole(user.role) }));
        const pending = [...(sharing.pending_invitations || [])].map((invite) => ({ ...invite, role: normalizeRole(invite.role) }));
        const inherited = [...(sharing.inherited || [])];
        const modal = document.createElement('div');
        modal.className = 'notes-modal';
        modal.setAttribute('role', 'dialog');
        modal.setAttribute('aria-modal', 'true');
        modal.setAttribute('aria-labelledby', 'notes-share-title');
        modal._shareAddRole = 'viewer';
        modal._shareUndoOperations = new Set();
        modal.innerHTML = `
            <div class="notes-modal-panel notes-share-panel">
                <header class="notes-modal-header">
                    <div>
                        <h2 id="notes-share-title">Share ${escapeHtml(resourceTitle || (resourceType === 'folder' ? 'folder' : 'note'))}</h2>
                        <p>Add Nest users directly or invite an email to claim access after OAuth signup.</p>
                    </div>
                    <button type="button" class="notes-icon-button" data-share-close aria-label="Close sharing">
                        <span class="material-symbols-outlined" aria-hidden="true">close</span>
                    </button>
                </header>
                <label class="notes-field notes-share-general">
                    <span>General access</span>
                    <select data-share-public>
                        <option value="restricted" ${sharing.public ? '' : 'selected'}>Restricted</option>
                        <option value="public" ${sharing.public ? 'selected' : ''}>Anyone with the link · Viewer</option>
                    </select>
                </label>
                <div class="notes-share-link-row">
                    <input type="text" readonly value="${escapeHtml(sharing.share_url || '')}" data-share-url aria-label="Share link">
                    <button type="button" class="btn-secondary" data-share-copy>Copy link</button>
                </div>
                <div class="notes-share-people">
                    <div class="notes-share-add-row">
                        <label class="notes-field">
                            <span>Add people or email</span>
                            <input type="search" autocomplete="off" placeholder="Search name, @username, or exact email" data-share-search>
                        </label>
                        <label class="notes-field notes-share-role-field">
                            <span>Role</span>
                            ${roleSelectHtml('viewer', 'data-share-add-role')}
                        </label>
                    </div>
                    <div class="notes-share-results" data-share-results hidden></div>
                    <section class="notes-share-section">
                        <h3>Direct access</h3>
                        <div class="notes-share-selected" data-share-selected></div>
                    </section>
                    <section class="notes-share-section">
                        <h3>Pending email invitations</h3>
                        <p class="notes-share-help">Pending recipients must register through OAuth using exactly the invited email.</p>
                        <div class="notes-share-selected" data-share-pending></div>
                    </section>
                    ${inherited.length ? `
                    <section class="notes-share-section">
                        <h3>Inherited access</h3>
                        <div class="notes-share-selected" data-share-inherited></div>
                    </section>
                    ` : ''}
                </div>
                <div class="notes-form-error" data-share-error role="alert" hidden></div>
                <footer class="notes-modal-actions">
                    <button type="button" class="btn-secondary" data-share-close>Cancel</button>
                    <button type="button" class="btn-primary" data-share-save>Save</button>
                </footer>
            </div>
        `;
        renderSelected(modal, users);
        renderPending(modal, pending);
        renderInherited(modal, inherited);

        modal.addEventListener('mousedown', (event) => {
            if (event.target === modal) close();
        });
        modal.addEventListener('change', (event) => {
            const userRole = event.target.closest('[data-user-role]');
            if (userRole) {
                const user = users.find((entry) => String(entry.id) === userRole.dataset.userRole);
                if (user) user.role = normalizeRole(userRole.value);
                return;
            }
            const pendingRole = event.target.closest('[data-pending-role]');
            if (pendingRole) {
                const invite = pending.find((entry) => String(entry.email) === pendingRole.dataset.pendingRole);
                if (invite) invite.role = normalizeRole(pendingRole.value);
                return;
            }
            const addRole = event.target.closest('[data-share-add-role]');
            if (addRole) {
                modal._shareAddRole = normalizeRole(addRole.value);
            }
        });
        modal.addEventListener('click', async (event) => {
            if (event.target.closest('[data-share-close]')) {
                close();
                return;
            }
            const remove = event.target.closest('[data-remove-user]');
            if (remove) {
                const index = users.findIndex((user) => String(user.id) === remove.dataset.removeUser);
                const [removedUser] = index >= 0 ? users.splice(index, 1) : [];
                renderSelected(modal, users);
                if (removedUser && global.APStudyUndo?.stage) {
                    let undoController = null;
                    const forget = () => modal._shareUndoOperations.delete(undoController);
                    undoController = global.APStudyUndo.stage({
                        message: `Access removed for ${removedUser.name || removedUser.username || 'user'}.`,
                        restore: () => {
                            if (!modal.isConnected) return;
                            users.splice(Math.min(index, users.length), 0, removedUser);
                            renderSelected(modal, users);
                        },
                        onUndo: forget,
                        onCommit: forget,
                    });
                    modal._shareUndoOperations.add(undoController);
                }
                return;
            }
            const removePending = event.target.closest('[data-remove-pending]');
            if (removePending) {
                const index = pending.findIndex((invite) => String(invite.email) === removePending.dataset.removePending);
                const [removedInvite] = index >= 0 ? pending.splice(index, 1) : [];
                renderPending(modal, pending);
                if (removedInvite && global.APStudyUndo?.stage) {
                    let undoController = null;
                    const forget = () => modal._shareUndoOperations.delete(undoController);
                    undoController = global.APStudyUndo.stage({
                        message: `Invitation for ${removedInvite.email} removed.`,
                        restore: () => {
                            if (!modal.isConnected) return;
                            pending.splice(Math.min(index, pending.length), 0, removedInvite);
                            renderPending(modal, pending);
                        },
                        onUndo: forget,
                        onCommit: forget,
                    });
                    modal._shareUndoOperations.add(undoController);
                }
                return;
            }
            const add = event.target.closest('[data-add-user]');
            if (add) {
                const results = modal._shareSearchResults || [];
                const user = results.find((entry) => String(entry.id) === add.dataset.addUser);
                if (user && !users.some((entry) => String(entry.id) === String(user.id))) {
                    users.push({ ...user, role: normalizeRole(modal._shareAddRole) });
                }
                renderSelected(modal, users);
                modal.querySelector('[data-share-results]').hidden = true;
                modal.querySelector('[data-share-search]').value = '';
                return;
            }
            const addEmail = event.target.closest('[data-add-email]');
            if (addEmail) {
                const email = addEmail.dataset.addEmail;
                if (email && !pending.some((entry) => String(entry.email).toLowerCase() === email.toLowerCase())) {
                    pending.push({ email, role: normalizeRole(modal._shareAddRole), status: 'pending' });
                }
                renderPending(modal, pending);
                modal.querySelector('[data-share-results]').hidden = true;
                modal.querySelector('[data-share-search]').value = '';
                return;
            }
            if (event.target.closest('[data-share-copy]')) {
                try {
                    await copyText(sharing.share_url || '');
                    global.APStudyToast?.show?.({ message: 'Share link copied.', type: 'success' });
                } catch {
                    setError(modal, 'Unable to copy the link.');
                }
                return;
            }
            const save = event.target.closest('[data-share-save]');
            if (!save) return;
            modal._shareUndoOperations.forEach((operation) => operation?.commit?.());
            modal._shareUndoOperations.clear();
            save.disabled = true;
            setError(modal);
            try {
                const updated = await apiJson(endpointFor(resourceType, resourceId), {
                    method: 'PATCH',
                    body: JSON.stringify({
                        expected_revision: sharing.revision,
                        public: modal.querySelector('[data-share-public]').value === 'public',
                        grants: users.map((user) => ({ user_id: user.id, role: normalizeRole(user.role) })),
                        invitations: pending.map((invite) => ({ email: invite.email, role: normalizeRole(invite.role) })),
                    }),
                });
                onSaved?.(updated);
                global.APStudyToast?.show?.({ message: 'Sharing updated.', type: 'success' });
                close();
            } catch (error) {
                const conflict = error.payload?.code === 'sharing_revision_conflict';
                setError(modal, conflict ? 'Sharing changed in another tab. Reopen this dialog to review the latest access.' : (error.message || 'Unable to update sharing.'));
                save.disabled = false;
            }
        });

        const search = modal.querySelector('[data-share-search]');
        search.addEventListener('input', () => {
            clearTimeout(searchTimer);
            const query = search.value.trim();
            const results = modal.querySelector('[data-share-results]');
            if (query.length < 2) {
                results.hidden = true;
                results.innerHTML = '';
                return;
            }
            searchTimer = setTimeout(async () => {
                try {
                    const payload = await apiJson(`/api/notes/share-users?q=${encodeURIComponent(query)}`);
                    modal._shareSearchResults = payload.results || [];
                    renderSearchResults(
                        modal,
                        modal._shareSearchResults,
                        new Set(users.map((user) => String(user.id))),
                        payload.email
                    );
                } catch (error) {
                    results.hidden = false;
                    results.innerHTML = `<p class="notes-share-empty">${escapeHtml(error.message)}</p>`;
                }
            }, 250);
        });
        return modal;
    }

    async function open({ resourceType, resourceId, resourceTitle, onSaved } = {}) {
        if (!resourceId || !['note', 'folder'].includes(resourceType)) return;
        close();
        returnFocus = document.activeElement;
        try {
            const sharing = await apiJson(endpointFor(resourceType, resourceId));
            activeModal = buildModal(resourceType, resourceId, resourceTitle, sharing, onSaved);
            document.body.appendChild(activeModal);
            document.body.classList.add('notes-modal-open');
            activeModal.querySelector('[data-share-search]')?.focus({ preventScroll: true });
        } catch (error) {
            global.APStudyToast?.show?.({
                title: 'Couldn’t load sharing settings',
                message: error.message || 'Try again in a moment.',
                type: 'error',
            });
        }
    }

    document.addEventListener('click', (event) => {
        const trigger = event.target.closest('[data-notes-share-resource]');
        if (!trigger) return;
        event.preventDefault();
        event.stopPropagation();
        void open({
            resourceType: trigger.dataset.notesShareResource,
            resourceId: trigger.dataset.resourceId,
            resourceTitle: trigger.dataset.resourceTitle,
        });
    });
    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape' && activeModal) close();
    });

    global.APStudyNotesSharing = { open, close };
})(window);
