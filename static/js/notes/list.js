(function () {
    const els = {
        page: document.getElementById('notes-page'),
        notesGrid: document.getElementById('notes-grid'),
        foldersGrid: document.getElementById('folders-grid'),
        foldersSection: document.getElementById('folders-section'),
        notesSection: document.getElementById('notes-section'),
        notesEmptyState: document.getElementById('notes-empty-state'),
        notesEmptyNewNote: document.getElementById('notes-empty-new-note'),
        notesEmptyTitle: document.getElementById('notes-empty-title'),
        notesEmptySubtitle: document.getElementById('notes-empty-subtitle'),
        notesSectionTitle: document.getElementById('notes-section-title'),
        rootBreadcrumb: document.getElementById('notes-root-breadcrumb'),
        breadcrumbSeparatorItem: document.getElementById('notes-breadcrumb-separator-item'),
        folderBreadcrumbItem: document.getElementById('notes-folder-breadcrumb-item'),
        folderBreadcrumb: document.getElementById('notes-folder-breadcrumb'),
        viewLabel: document.getElementById('notes-view-label'),
        viewMenu: document.getElementById('notes-view-menu'),
        btnNewNote: document.getElementById('btn-new-note'),
        btnNewFolder: document.getElementById('btn-new-folder'),
        newButton: document.getElementById('notes-new-button'),
        newMenu: document.getElementById('notes-new-menu'),
        pageActions: document.querySelector('.notes-page-actions'),
        tabMine: document.getElementById('notes-tab-mine'),
        tabShared: document.getElementById('notes-tab-shared'),
        alert: document.getElementById('notes-alert'),
        foldersCount: document.getElementById('folders-count'),
        notesCount: document.getElementById('notes-count'),
        folderModal: document.getElementById('notes-folder-modal'),
        folderForm: document.getElementById('notes-folder-form'),
        folderTitle: document.getElementById('notes-folder-modal-title'),
        folderSubtitle: document.getElementById('notes-folder-modal-subtitle'),
        folderName: document.getElementById('notes-folder-name'),
        folderError: document.getElementById('notes-folder-error'),
        folderSave: document.getElementById('notes-folder-save'),
        moveModal: document.getElementById('notes-move-modal'),
        moveForm: document.getElementById('notes-move-form'),
        moveDestination: document.getElementById('notes-move-destination'),
        moveError: document.getElementById('notes-move-error'),
        moveSave: document.getElementById('notes-move-save'),
    };

    const state = {
        folders: [],
        notes: [],
        currentFolderId: null,
        viewMode: 'mine',
        folderModalMode: 'create',
        activeFolder: null,
        moveNote: null,
        activeModal: null,
        lastFocusedElement: null,
        loading: false,
    };
    const dragDrop = window.APStudyNotesListDragDrop?.createNotesListDragDrop?.({
        notesGrid: els.notesGrid,
        foldersGrid: els.foldersGrid,
        page: els.page,
        onStart: () => closeAllMenus(),
        onEnd: handleNoteDragEnd,
    });
    const {
        apiJson,
        buildLoadingIndicatorHtml,
        escapeHtml,
        formatCount,
        formatDate,
        notePreview,
        preloadEditorBundle,
    } = window.APStudyNotesListUtils;
    const noteCardFactory = window.APStudyNotesListCards.createNotesListCards({
        callbacks: {
            closeAllMenus,
            escapeHtml,
            formatCount,
            formatDate,
            notePreview,
            openFolder,
            openSharedFolder,
            openFolderModal,
            openMoveModal,
            preloadEditorBundle,
            toggleCardMenu,
        },
    });
    const { createFolderCard, createNoteCard } = noteCardFactory;

    function noteIdOf(note) {
        return note?.$id || note?.id || '';
    }

    function restoreAtIndex(items, item, index) {
        if (!item || items.some((candidate) => noteIdOf(candidate) === noteIdOf(item))) return items;
        const next = [...items];
        next.splice(Math.min(Math.max(0, index), next.length), 0, item);
        return next;
    }

    function notesByFolderMap(notes = state.notes) {
        const grouped = {};
        notes.forEach((note) => {
            const folderId = note.folder_id || 'root';
            if (!grouped[folderId]) grouped[folderId] = [];
            grouped[folderId].push(note);
        });
        return grouped;
    }

    function updateCounts(notesByFolder) {
        const visibleFolderId = state.currentFolderId || 'root';
        if (els.foldersCount) els.foldersCount.textContent = formatCount(state.folders.length, 'folder');
        if (els.notesCount) {
            els.notesCount.textContent = formatCount((notesByFolder[visibleFolderId] || []).length, 'note');
        }
    }

    function setButtonBusy(button, busy) {
        if (!button) return;
        button.disabled = busy;
        button.classList.toggle('is-busy', busy);
    }

    function showAlert(message, type = 'info', options = {}) {
        if (!window.APStudyToast) return;
        const toastType =
            type === 'error' ? 'error' : type === 'warning' ? 'warning' : type === 'success' ? 'success' : 'info';
        window.APStudyToast.show({
            message,
            title: options.title,
            type: toastType,
            action: options.action,
            duration: options.duration,
        });
    }

    function clearAlert() {
        if (!els.alert) return;
        els.alert.hidden = true;
        els.alert.textContent = '';
        els.alert.classList.remove('notes-alert-error');
    }

    function showFormError(element, message, field) {
        if (!element) return;
        element.textContent = message;
        element.hidden = false;
        if (field) {
            window.APStudyFormField?.markInvalid?.(field, { focus: false });
        }
    }

    function clearFormError(element, field) {
        if (!element) return;
        element.textContent = '';
        element.hidden = true;
        if (field) {
            window.APStudyFormField?.clearInvalid?.(field);
        }
    }

    function setLoadingState(isLoading, label = 'Loading notes...') {
        state.loading = isLoading;
        els.page?.classList.toggle('is-loading', isLoading);
        if (!els.notesGrid) return;
        if (isLoading) {
            dragDrop?.destroy?.();
            els.notesGrid.innerHTML = `
                <div class="notes-loading-card">
                    ${buildLoadingIndicatorHtml(label, { sizePx: 52, textToneClass: 'text-on-surface' })}
                </div>
            `;
            if (els.notesEmptyState) els.notesEmptyState.style.display = 'none';
        }
    }

    function closeAllMenus({ restoreFocus = false } = {}) {
        const openMenu = document.querySelector('.note-card-menu:not(.note-menu-hidden), .folder-card-menu:not(.note-menu-hidden)');
        const trigger = openMenu?.closest('.note-card, .folder-card')?.querySelector('.note-card-menu-btn, .folder-card-menu-btn');
        document.querySelectorAll('.note-card-menu, .folder-card-menu').forEach((menu) => {
            menu.classList.add('note-menu-hidden');
            menu.style.removeProperty('top');
            menu.style.removeProperty('left');
            menu.style.removeProperty('max-height');
            menu.style.removeProperty('visibility');
            menu.style.removeProperty('--notes-menu-origin-x');
            delete menu.dataset.placement;
        });
        document.querySelectorAll('.note-card-menu-btn, .folder-card-menu-btn').forEach((button) => {
            button.setAttribute('aria-expanded', 'false');
            button.classList.remove('is-menu-open');
        });
        if (restoreFocus) trigger?.focus({ preventScroll: true });
    }

    function positionCardMenu(button, menu) {
        const viewportMargin = 8;
        const menuGap = 6;
        const triggerRect = button.getBoundingClientRect();
        const viewportWidth = document.documentElement.clientWidth;
        const viewportHeight = document.documentElement.clientHeight;

        menu.style.visibility = 'hidden';
        menu.classList.remove('note-menu-hidden');
        const naturalHeight = menu.scrollHeight;
        const menuWidth = menu.offsetWidth;
        const spaceAbove = Math.max(0, triggerRect.top - menuGap - viewportMargin);
        const spaceBelow = Math.max(0, viewportHeight - triggerRect.bottom - menuGap - viewportMargin);
        const placeBelow = spaceBelow >= spaceAbove;
        const availableHeight = placeBelow ? spaceBelow : spaceAbove;
        const renderedHeight = Math.min(naturalHeight, availableHeight);
        const maxLeft = Math.max(viewportMargin, viewportWidth - menuWidth - viewportMargin);
        const left = Math.min(Math.max(viewportMargin, triggerRect.right - menuWidth), maxLeft);
        const originX = Math.min(Math.max(triggerRect.left + (triggerRect.width / 2) - left, 12), menuWidth - 12);
        const top = placeBelow
            ? triggerRect.bottom + menuGap
            : triggerRect.top - menuGap - renderedHeight;

        menu.style.left = `${Math.round(left)}px`;
        menu.style.top = `${Math.round(Math.max(viewportMargin, top))}px`;
        menu.style.maxHeight = `${Math.max(0, Math.floor(availableHeight))}px`;
        menu.style.setProperty('--notes-menu-origin-x', `${Math.round(originX)}px`);
        menu.dataset.placement = placeBelow ? 'below' : 'above';
        menu.style.removeProperty('visibility');
    }

    function toggleCardMenu(button, menu) {
        const isHidden = menu.classList.contains('note-menu-hidden');
        closeAllMenus();
        if (isHidden) {
            positionCardMenu(button, menu);
            button.setAttribute('aria-expanded', 'true');
            button.classList.add('is-menu-open');
            menu.querySelector('button')?.focus({ preventScroll: true });
        }
    }

    function handleCardMenuViewportScroll(event) {
        if (event.target?.closest?.('.note-card-menu, .folder-card-menu')) return;
        closeAllMenus();
    }

    function openModal(modal, focusTarget = null) {
        if (!modal) return;
        state.lastFocusedElement = document.activeElement;
        modal.hidden = false;
        state.activeModal = modal;
        document.body.classList.add('notes-modal-open');
        requestAnimationFrame(() => {
            const target = focusTarget || modal.querySelector('input, select, button, [tabindex]:not([tabindex="-1"])');
            target?.focus({ preventScroll: true });
        });
    }

    function closeModal(modal = state.activeModal) {
        if (!modal) return;
        modal.hidden = true;
        if (state.activeModal === modal) state.activeModal = null;
        if (!document.querySelector('.notes-modal:not([hidden])')) {
            document.body.classList.remove('notes-modal-open');
        }
        state.lastFocusedElement?.focus?.({ preventScroll: true });
    }

    function folderById(folderId) {
        return state.folders.find((folder) => noteIdOf(folder) === folderId) || null;
    }

    function folderIdFromLocation() {
        return new URLSearchParams(window.location.search).get('folder') || null;
    }

    function viewModeFromLocation() {
        return new URLSearchParams(window.location.search).get('view') === 'shared' ? 'shared' : 'mine';
    }

    function updateFolderLocation(folderId, { replace = false } = {}) {
        const url = new URL(window.location.href);
        if (folderId) {
            url.searchParams.set('folder', folderId);
        } else {
            url.searchParams.delete('folder');
        }
        url.searchParams.delete('view');
        const method = replace ? 'replaceState' : 'pushState';
        window.history[method]({ notesFolderId: folderId }, '', `${url.pathname}${url.search}${url.hash}`);
    }

    function updateViewLocation(viewMode, { replace = false } = {}) {
        const url = new URL(window.location.href);
        url.searchParams.delete('folder');
        if (viewMode === 'shared') url.searchParams.set('view', 'shared');
        else url.searchParams.delete('view');
        const method = replace ? 'replaceState' : 'pushState';
        window.history[method]({ notesView: viewMode }, '', `${url.pathname}${url.search}${url.hash}`);
    }

    function openSharedFolder(folderId) {
        if (!folderId) return;
        window.location.href = `/notes/folders/${encodeURIComponent(folderId)}`;
    }

    function setPopoverOpen(menu, button, open) {
        if (!menu || !button) return;
        menu.hidden = !open;
        button.setAttribute('aria-expanded', String(open));
    }

    function closeHeaderPopovers({ restoreFocus = false } = {}) {
        const openButton = !els.newMenu?.hidden
            ? els.newButton
            : (!els.viewMenu?.hidden ? els.rootBreadcrumb : null);
        setPopoverOpen(els.newMenu, els.newButton, false);
        setPopoverOpen(els.viewMenu, els.rootBreadcrumb, false);
        if (restoreFocus) openButton?.focus();
    }

    function toggleHeaderPopover(menu, button) {
        const shouldOpen = Boolean(menu?.hidden);
        closeHeaderPopovers();
        setPopoverOpen(menu, button, shouldOpen);
        if (shouldOpen) requestAnimationFrame(() => menu?.querySelector('[role^="menuitem"]')?.focus({ preventScroll: true }));
    }

    function syncViewControls() {
        const readOnly = state.viewMode === 'shared';
        if (els.pageActions) els.pageActions.hidden = readOnly;
        if (els.notesEmptyNewNote) els.notesEmptyNewNote.hidden = readOnly;
        if (els.viewLabel) els.viewLabel.textContent = readOnly ? 'Shared with Me' : 'My Notes';
        els.tabMine?.classList.toggle('is-active', !readOnly);
        els.tabMine?.setAttribute('aria-checked', String(!readOnly));
        els.tabShared?.classList.toggle('is-active', readOnly);
        els.tabShared?.setAttribute('aria-checked', String(readOnly));
        if (readOnly) setPopoverOpen(els.newMenu, els.newButton, false);
    }

    async function setViewMode(viewMode, { updateHistory = true } = {}) {
        const nextMode = viewMode === 'shared' ? 'shared' : 'mine';
        if (state.viewMode === nextMode && state.notes.length) return;
        state.viewMode = nextMode;
        state.currentFolderId = null;
        syncViewControls();
        if (updateHistory) updateViewLocation(nextMode);
        await loadAndRender();
    }

    function updateBreadcrumb() {
        if (state.viewMode === 'shared') {
            if (els.breadcrumbSeparatorItem) els.breadcrumbSeparatorItem.hidden = true;
            if (els.folderBreadcrumbItem) els.folderBreadcrumbItem.hidden = true;
            return;
        }
        const folder = folderById(state.currentFolderId);
        const inFolder = Boolean(folder);
        if (els.breadcrumbSeparatorItem) els.breadcrumbSeparatorItem.hidden = !inFolder;
        if (els.folderBreadcrumbItem) els.folderBreadcrumbItem.hidden = !inFolder;
        if (els.folderBreadcrumb) {
            els.folderBreadcrumb.textContent = folder?.name || '';
            if (inFolder) {
                els.folderBreadcrumb.setAttribute('aria-current', 'page');
            } else {
                els.folderBreadcrumb.removeAttribute('aria-current');
            }
        }
    }

    function openFolder(folderId, { updateHistory = true } = {}) {
        const nextFolderId = folderById(folderId) ? folderId : null;
        if (state.currentFolderId === nextFolderId) return;
        state.currentFolderId = nextFolderId;
        if (updateHistory) updateFolderLocation(nextFolderId);
        renderCurrentView();
    }

    function openFolderModal(mode, folder = null) {
        state.folderModalMode = mode;
        state.activeFolder = folder;
        clearFormError(els.folderError, els.folderName);
        window.APStudyFormField?.clearInvalid?.(els.folderName);
        if (els.folderTitle) els.folderTitle.textContent = mode === 'rename' ? 'Rename folder' : 'New folder';
        if (els.folderSubtitle) {
            els.folderSubtitle.textContent = mode === 'rename'
                ? 'Update this folder name.'
                : 'Create a folder for related notes.';
        }
        if (els.folderName) els.folderName.value = mode === 'rename' ? (folder?.name || '') : 'New Folder';
        if (els.folderSave) els.folderSave.textContent = mode === 'rename' ? 'Save changes' : 'Create folder';
        openModal(els.folderModal, els.folderName);
        els.folderName?.select();
    }

    function openMoveModal(note) {
        state.moveNote = note;
        clearFormError(els.moveError);
        const currentFolderId = note.folder_id || '';
        if (els.moveDestination) {
            els.moveDestination.innerHTML = [
                '<option value="">No folder</option>',
                ...state.folders.map((folder) => {
                    const folderId = noteIdOf(folder);
                    const selected = folderId === currentFolderId ? ' selected' : '';
                    return `<option value="${escapeHtml(folderId)}"${selected}>${escapeHtml(folder.name || 'Untitled Folder')}</option>`;
                }),
            ].join('');
        }
        openModal(els.moveModal, els.moveDestination);
    }

    function renderCurrentView() {
        dragDrop?.destroy?.();
        const notesByFolder = notesByFolderMap();
        const currentFolder = folderById(state.currentFolderId);
        if (state.currentFolderId && !currentFolder) {
            state.currentFolderId = null;
            updateFolderLocation(null, { replace: true });
        }

        updateBreadcrumb();
        syncViewControls();
        updateCounts(notesByFolder);

        if (els.foldersGrid) els.foldersGrid.innerHTML = '';
        const readOnly = state.viewMode === 'shared';
        const showFolders = (readOnly || !state.currentFolderId) && Boolean(state.folders.length);
        if (els.foldersSection) els.foldersSection.hidden = !showFolders;
        if (showFolders) {
            state.folders.forEach((folder) => {
                const folderId = noteIdOf(folder);
                const folderNotes = readOnly ? (folder.notes || []) : (notesByFolder[folderId] || []);
                els.foldersGrid.appendChild(createFolderCard(folder, folderNotes, { readOnly }));
            });
        }

        if (els.notesGrid) els.notesGrid.innerHTML = '';
        const visibleFolderId = state.currentFolderId || 'root';
        const visibleNotes = notesByFolder[visibleFolderId] || [];
        if (els.notesSectionTitle) {
            els.notesSectionTitle.textContent = readOnly ? 'Shared notes' : state.currentFolderId ? 'Notes' : 'Unfiled notes';
        }
        if (els.notesEmptyTitle) {
            els.notesEmptyTitle.textContent = readOnly ? 'Nothing shared with you yet' : state.currentFolderId ? 'This folder is empty' : 'No notes yet';
        }
        if (els.notesEmptySubtitle) {
            els.notesEmptySubtitle.textContent = readOnly
                ? 'Notes and folders shared directly with your Nest account will appear here.'
                : state.currentFolderId
                ? 'Create a note here or move an existing note into this folder.'
                : 'Start a note, then organize it into folders when you are ready.';
        }
        if (!visibleNotes.length) {
            if (els.notesEmptyState) els.notesEmptyState.style.display = '';
        } else {
            if (els.notesEmptyState) els.notesEmptyState.style.display = 'none';
            visibleNotes.forEach((note) => els.notesGrid.appendChild(createNoteCard(note, { readOnly })));
        }
        if (!readOnly) dragDrop?.mount?.();
    }

    function render(data) {
        state.notes = data.notes || [];
        state.folders = data.folders || [];
        renderCurrentView();
    }

    async function loadAndRender(options = {}) {
        if (options.clearAlert !== false) clearAlert();
        setLoadingState(true, 'Loading notes...');
        try {
            const data = await apiJson(state.viewMode === 'shared' ? '/api/notes/shared' : '/api/notes');
            render(data);
        } catch (error) {
            render({ notes: [], folders: [] });
            showAlert(error.message || 'Refresh the page and try again.', 'error', { title: 'Couldn’t load notes' });
        } finally {
            setLoadingState(false);
        }
    }

    async function createNote() {
        clearAlert();
        setButtonBusy(els.btnNewNote, true);
        setButtonBusy(els.notesEmptyNewNote, true);
        try {
            const note = await apiJson('/api/notes', {
                method: 'POST',
                body: JSON.stringify({ title: '', content: '', folder_id: state.currentFolderId }),
            });
            const noteId = noteIdOf(note);
            if (noteId) {
                window.location.href = `/notes/${encodeURIComponent(noteId)}`;
                return;
            }
            await loadAndRender({ clearAlert: false });
        } catch (error) {
            showAlert(error.message || 'Try again in a moment.', 'error', { title: 'Couldn’t create note' });
        } finally {
            setButtonBusy(els.btnNewNote, false);
            setButtonBusy(els.notesEmptyNewNote, false);
        }
    }

    async function saveFolderModal() {
        const name = (els.folderName?.value || '').trim();
        if (!name) {
            showFormError(els.folderError, 'Enter a folder name.', els.folderName);
            return;
        }
        clearFormError(els.folderError, els.folderName);
        setButtonBusy(els.folderSave, true);
        try {
            if (state.folderModalMode === 'rename') {
                const folderId = noteIdOf(state.activeFolder);
                const updated = await apiJson(`/api/notes/folders/${encodeURIComponent(folderId)}`, {
                    method: 'PATCH',
                    body: JSON.stringify({ name }),
                });
                state.folders = state.folders.map((folder) => (
                    noteIdOf(folder) === folderId ? { ...folder, ...updated } : folder
                ));
                renderCurrentView();
                showAlert('Folder renamed.');
            } else {
                const created = await apiJson('/api/notes/folders', {
                    method: 'POST',
                    body: JSON.stringify({ name }),
                });
                state.folders.push(created);
                renderCurrentView();
                showAlert('Folder created.');
            }
            closeModal(els.folderModal);
        } catch (error) {
            showFormError(els.folderError, error.message || 'Unable to save folder.');
        } finally {
            setButtonBusy(els.folderSave, false);
        }
    }

    async function saveMoveModal() {
        const noteId = noteIdOf(state.moveNote);
        if (!noteId) return;
        const folderId = els.moveDestination?.value || null;
        clearFormError(els.moveError);
        setButtonBusy(els.moveSave, true);
        try {
            const updated = await apiJson(`/api/notes/${encodeURIComponent(noteId)}`, {
                method: 'PATCH',
                body: JSON.stringify({ folder_id: folderId }),
            });
            closeModal(els.moveModal);
            const { content: _content, page_setup: _pageSetup, global_page_setup: _global, ...listFields } = updated || {};
            state.notes = state.notes.map((note) => (
                noteIdOf(note) === noteId ? { ...note, ...listFields } : note
            ));
            renderCurrentView();
            showAlert(folderId ? `Moved to ${folderById(folderId)?.name || 'folder'}.` : 'Moved to unfiled notes.');
        } catch (error) {
            showFormError(els.moveError, error.message || 'Unable to move note.');
        } finally {
            setButtonBusy(els.moveSave, false);
        }
    }

    async function deleteNote(noteCard, button) {
        const noteId = noteCard?.dataset.noteId;
        if (!noteId) return;
        const note = state.notes.find((item) => noteIdOf(item) === noteId);
        const title = note?.title || 'Untitled';
        const accepted = await (window.APStudyConfirm?.request?.({
            title: 'Delete note?',
            message: `"${title}" will be deleted. You’ll have a short time to undo.`,
            acceptLabel: 'Delete note',
            danger: true,
        }) ?? Promise.resolve(false));
        if (!accepted) return;
        const noteIndex = state.notes.findIndex((item) => noteIdOf(item) === noteId);
        state.notes = state.notes.filter((item) => noteIdOf(item) !== noteId);
        renderCurrentView();
        window.APStudyUndo?.stage?.({
            message: `"${title}" deleted.`,
            commit: ({ reason }) => apiJson(`/api/notes/${encodeURIComponent(noteId)}`, {
                method: 'DELETE',
                keepalive: reason === 'pagehide',
            }),
            restore: () => {
                state.notes = restoreAtIndex(state.notes, note, noteIndex);
                renderCurrentView();
            },
            errorTitle: 'Couldn’t delete note',
        });
        if (!window.APStudyUndo?.stage) {
            setButtonBusy(button, true);
            try {
                await apiJson(`/api/notes/${encodeURIComponent(noteId)}`, { method: 'DELETE' });
            } catch (error) {
                state.notes = restoreAtIndex(state.notes, note, noteIndex);
                renderCurrentView();
                showAlert(error.message || 'Try again in a moment.', 'error', { title: 'Couldn’t delete note' });
            } finally {
                setButtonBusy(button, false);
            }
        }
    }

    async function deleteFolder(folderCard, button) {
        const folderId = folderCard?.dataset.folderId;
        if (!folderId) return;
        const folder = folderById(folderId);
        const noteCount = state.notes.filter((note) => note.folder_id === folderId).length;
        const accepted = await (window.APStudyConfirm?.request?.({
            title: 'Delete folder?',
            message: `"${folder?.name || 'This folder'}" and ${formatCount(noteCount, 'note')} inside it will be deleted.`,
            acceptLabel: 'Delete folder',
            danger: true,
        }) ?? Promise.resolve(false));
        if (!accepted) return;
        const folderIndex = state.folders.findIndex((item) => noteIdOf(item) === folderId);
        const removedNotes = state.notes
            .map((note, index) => ({ note, index }))
            .filter((record) => record.note.folder_id === folderId);
        state.notes = state.notes.filter((note) => note.folder_id !== folderId);
        state.folders = state.folders.filter((item) => noteIdOf(item) !== folderId);
        renderCurrentView();
        window.APStudyUndo?.stage?.({
            message: `"${folder?.name || 'Folder'}" and ${formatCount(noteCount, 'note')} deleted.`,
            commit: ({ reason }) => apiJson(`/api/notes/folders/${encodeURIComponent(folderId)}`, {
                method: 'DELETE',
                keepalive: reason === 'pagehide',
            }),
            restore: () => {
                state.notes = removedNotes.reduce(
                    (items, record) => restoreAtIndex(items, record.note, record.index),
                    state.notes,
                );
                state.folders = restoreAtIndex(state.folders, folder, folderIndex);
                renderCurrentView();
            },
            errorTitle: 'Couldn’t delete folder',
        });
        if (!window.APStudyUndo?.stage) {
            setButtonBusy(button, true);
            try {
                await apiJson(`/api/notes/folders/${encodeURIComponent(folderId)}`, { method: 'DELETE' });
            } catch (error) {
                state.notes = removedNotes.reduce(
                    (items, record) => restoreAtIndex(items, record.note, record.index),
                    state.notes,
                );
                state.folders = restoreAtIndex(state.folders, folder, folderIndex);
                renderCurrentView();
                showAlert(error.message || 'Try again in a moment.', 'error', { title: 'Couldn’t delete folder' });
            } finally {
                setButtonBusy(button, false);
            }
        }
    }

    async function handleNoteDragEnd(event, { droppedOnFolder = false, droppedFolderId = null } = {}) {
        const item = event?.item;
        const noteId = item?.dataset.noteId;
        if (!noteId) return;

        const note = state.notes.find((entry) => noteIdOf(entry) === noteId);
        if (!note) return;

        const destinationFolderId = droppedOnFolder
            ? droppedFolderId
            : (state.currentFolderId || null);
        const orderStep = 1000;

        try {
            if (droppedOnFolder) {
                const destinationNotes = state.notes.filter((entry) => (
                    noteIdOf(entry) !== noteId && (entry.folder_id || null) === destinationFolderId
                ));
                const order = (Math.max(0, ...destinationNotes.map((entry) => Number(entry.order) || 0)) || 0) + orderStep;
                await apiJson(`/api/notes/${encodeURIComponent(noteId)}`, {
                    method: 'PATCH',
                    body: JSON.stringify({ folder_id: destinationFolderId, order }),
                });
                note.folder_id = destinationFolderId;
                note.order = order;
                renderCurrentView();
                showAlert(destinationFolderId
                    ? `Moved to ${folderById(destinationFolderId)?.name || 'folder'}.`
                    : 'Moved to unfiled notes.');
                return;
            }

            const orderedCards = [...els.notesGrid.querySelectorAll('.note-card')];
            const orderUpdates = orderedCards.map((card, index) => {
                const entry = state.notes.find((candidate) => noteIdOf(candidate) === card.dataset.noteId);
                return entry ? { entry, order: (index + 1) * orderStep } : null;
            }).filter(Boolean);
            const changedUpdates = orderUpdates.filter(({ entry, order }) => Number(entry.order) !== order);
            await Promise.all(changedUpdates.map(({ entry, order }) => apiJson(
                `/api/notes/${encodeURIComponent(noteIdOf(entry))}`,
                {
                    method: 'PATCH',
                    body: JSON.stringify({ order }),
                },
            )));
            orderUpdates.forEach(({ entry, order }) => {
                entry.order = order;
            });
        } catch (error) {
            showAlert(error.message || 'Try again in a moment.', 'error', { title: 'Couldn’t move note' });
            await loadAndRender();
        }
    }

    function bindEvents() {
        els.newButton?.addEventListener('click', (event) => {
            event.stopPropagation();
            toggleHeaderPopover(els.newMenu, els.newButton);
        });
        els.rootBreadcrumb?.addEventListener('click', (event) => {
            event.stopPropagation();
            toggleHeaderPopover(els.viewMenu, els.rootBreadcrumb);
        });
        els.btnNewNote?.addEventListener('click', () => {
            closeHeaderPopovers();
            void createNote();
        });
        els.notesEmptyNewNote?.addEventListener('click', createNote);
        els.btnNewFolder?.addEventListener('click', () => {
            closeHeaderPopovers();
            openFolderModal('create');
        });
        els.tabMine?.addEventListener('click', () => {
            closeHeaderPopovers();
            if (state.viewMode === 'mine') openFolder(null);
            else void setViewMode('mine');
        });
        els.tabShared?.addEventListener('click', () => {
            closeHeaderPopovers();
            void setViewMode('shared');
        });
        els.folderForm?.addEventListener('submit', (event) => {
            event.preventDefault();
            void saveFolderModal();
        });
        els.moveForm?.addEventListener('submit', (event) => {
            event.preventDefault();
            void saveMoveModal();
        });
        document.querySelectorAll('[data-close-notes-modal]').forEach((button) => {
            button.addEventListener('click', () => closeModal(button.closest('.notes-modal')));
        });
        document.querySelectorAll('.notes-modal').forEach((modal) => {
            modal.addEventListener('mousedown', (event) => {
                if (event.target === modal) closeModal(modal);
            });
        });
        document.addEventListener('click', (event) => {
            if (!event.target.closest('.notes-new-menu-wrap, .notes-view-switcher')) {
                closeHeaderPopovers();
            }
            if (!event.target.closest('.note-card-menu, .folder-card-menu, .note-card-menu-btn, .folder-card-menu-btn')) {
                closeAllMenus();
            }
            const deleteNoteButton = event.target.closest('.note-menu-item-danger[data-action="delete"]');
            if (deleteNoteButton) {
                event.stopPropagation();
                closeAllMenus();
                void deleteNote(deleteNoteButton.closest('.note-card'), deleteNoteButton);
                return;
            }
            const deleteFolderButton = event.target.closest('.note-menu-item-danger[data-action="delete-folder"]');
            if (deleteFolderButton) {
                event.stopPropagation();
                closeAllMenus();
                void deleteFolder(deleteFolderButton.closest('.folder-card'), deleteFolderButton);
            }
        });
        document.addEventListener('keydown', (event) => {
            const openMenu = event.target.closest?.('.note-card-menu:not(.note-menu-hidden), .folder-card-menu:not(.note-menu-hidden)');
            if (openMenu && ['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
                const items = [...openMenu.querySelectorAll('[role="menuitem"]:not(:disabled)')];
                const currentIndex = items.indexOf(document.activeElement);
                let nextIndex = currentIndex;
                if (event.key === 'ArrowDown') nextIndex = (currentIndex + 1) % items.length;
                if (event.key === 'ArrowUp') nextIndex = (currentIndex - 1 + items.length) % items.length;
                if (event.key === 'Home') nextIndex = 0;
                if (event.key === 'End') nextIndex = items.length - 1;
                event.preventDefault();
                items[nextIndex]?.focus({ preventScroll: true });
                return;
            }
            if (event.key !== 'Escape') return;
            if (state.activeModal) {
                closeModal();
                return;
            }
            if (!els.newMenu?.hidden || !els.viewMenu?.hidden) {
                closeHeaderPopovers({ restoreFocus: true });
                return;
            }
            closeAllMenus({ restoreFocus: true });
        });
        window.addEventListener('resize', () => {
            closeAllMenus();
            dragDrop?.sync?.();
        });
        window.addEventListener('scroll', handleCardMenuViewportScroll, true);
        window.addEventListener('popstate', () => {
            state.viewMode = viewModeFromLocation();
            state.currentFolderId = folderIdFromLocation();
            syncViewControls();
            void loadAndRender();
        });
    }

    document.addEventListener('DOMContentLoaded', () => {
        state.viewMode = viewModeFromLocation();
        state.currentFolderId = folderIdFromLocation();
        syncViewControls();
        bindEvents();
        void loadAndRender();
    });
})();
