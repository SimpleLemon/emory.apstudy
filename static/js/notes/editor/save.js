const SAVE_DEBOUNCE_MS = 800;
const SAVE_DEBOUNCE_LARGE_DOC_MS = 1500;
const LARGE_DOCUMENT_BLOCK_COUNT = 120;
const SAVED_TIME_REFRESH_MS = 60000;

export function fingerprintTextParts(parts) {
    let hash = 2166136261;
    let length = 0;
    parts.forEach((part) => {
        const text = String(part ?? '');
        length += text.length;
        for (let index = 0; index < text.length; index += 1) {
            hash ^= text.charCodeAt(index);
            hash = Math.imul(hash, 16777619);
        }
        hash ^= 0;
        hash = Math.imul(hash, 16777619);
    });
    return `${length}:${(hash >>> 0).toString(36)}`;
}

export function notePayloadFingerprint(title, content) {
    return fingerprintTextParts([title, content]);
}

export function createNoteSaveRuntime({
    noteId,
    titleInput,
    saveStatus,
    saveRetry,
    getCanEdit,
    getEditor,
    getNoteCollaborationEnabled,
    getCurrentDocumentSnapshot,
    getTopLevelBlockCount,
}) {
    let saveDebounceTimer = null;
    let savedTimeRefreshTimer = null;
    let lastSavedAt = null;
    let noteHasPendingChanges = false;
    let lastSavedPayloadFingerprint = '';

    function clearSavedTimeRefresh() {
        if (savedTimeRefreshTimer) {
            clearInterval(savedTimeRefreshTimer);
            savedTimeRefreshTimer = null;
        }
    }

    function renderSavedTime() {
        if (!saveStatus || !lastSavedAt) return;
        saveStatus.textContent = `Saved ${formatRelativeSavedTime(lastSavedAt)}`;
    }

    function setLastSavedAt(value) {
        lastSavedAt = parseSavedDate(value) || new Date();
        renderSavedTime();
        clearSavedTimeRefresh();
        savedTimeRefreshTimer = window.setInterval(renderSavedTime, SAVED_TIME_REFRESH_MS);
    }

    function setSaveStatus(status, options = {}) {
        if (!saveStatus) return;
        if (saveRetry) saveRetry.hidden = true;

        saveStatus.classList.remove(
            'save-status-hidden',
            'save-status-saving',
            'save-status-saved',
            'save-status-error'
        );

        if (status === 'saving') {
            clearSavedTimeRefresh();
            saveStatus.textContent = options.message || 'Saving...';
            saveStatus.classList.add('save-status-saving');
            return;
        }

        if (status === 'connecting') {
            clearSavedTimeRefresh();
            saveStatus.textContent = 'Connecting...';
            saveStatus.classList.add('save-status-saving');
            return;
        }

        if (status === 'reconnecting') {
            clearSavedTimeRefresh();
            saveStatus.textContent = 'Reconnecting...';
            saveStatus.classList.add('save-status-saving');
            return;
        }

        if (status === 'offline-readonly') {
            clearSavedTimeRefresh();
            saveStatus.textContent = 'Offline — read only';
            saveStatus.classList.add('save-status-error');
            if (saveRetry) saveRetry.hidden = true;
            return;
        }

        if (status === 'saved') {
            setLastSavedAt(options.savedAt);
            saveStatus.classList.add('save-status-saved');
            return;
        }

        if (status === 'error') {
            clearSavedTimeRefresh();
            saveStatus.textContent = options.message || 'Save failed';
            saveStatus.classList.add('save-status-error');
            if (saveRetry && options.retry !== false) saveRetry.hidden = false;
        }
    }

    async function saveNote() {
        if (!getCanEdit() || !noteId || !titleInput || !getEditor() || getNoteCollaborationEnabled()) return;

        const documentSnapshot = getCurrentDocumentSnapshot();
        const content = JSON.stringify(documentSnapshot);
        const payload = {
            title: titleInput.value,
            content,
        };
        const payloadFingerprint = notePayloadFingerprint(payload.title, payload.content);
        if (payloadFingerprint === lastSavedPayloadFingerprint) {
            noteHasPendingChanges = false;
            return;
        }

        setSaveStatus('saving');

        try {
            const response = await (window.APStudyPendingMutations?.track(fetch(`/api/notes/${noteId}`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            }), 'notes-save') || fetch(`/api/notes/${noteId}`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            }));

            if (!response.ok) {
                throw new Error('Save request failed');
            }

            lastSavedPayloadFingerprint = payloadFingerprint;
            noteHasPendingChanges = false;
            setSaveStatus('saved', { savedAt: new Date().toISOString() });
        } catch (error) {
            console.error(error);
            setSaveStatus('error');
        }
    }

    function currentSaveDebounceMs() {
        const blockCount = getTopLevelBlockCount();
        return blockCount >= LARGE_DOCUMENT_BLOCK_COUNT ? SAVE_DEBOUNCE_LARGE_DOC_MS : SAVE_DEBOUNCE_MS;
    }

    function triggerDebouncedSave() {
        if (!getCanEdit() || getNoteCollaborationEnabled()) return;
        noteHasPendingChanges = true;
        if (saveDebounceTimer) {
            clearTimeout(saveDebounceTimer);
        }

        saveDebounceTimer = window.setTimeout(() => {
            saveNote();
        }, currentSaveDebounceMs());
    }

    function clearTimers() {
        if (saveDebounceTimer) window.clearTimeout(saveDebounceTimer);
        saveDebounceTimer = null;
        clearSavedTimeRefresh();
    }

    function setLastSavedPayloadFingerprint(value) {
        lastSavedPayloadFingerprint = value || '';
    }

    function hasPendingChanges() {
        return noteHasPendingChanges;
    }

    window.addEventListener('beforeunload', (event) => {
        if (!noteHasPendingChanges) return;
        event.preventDefault();
        event.returnValue = '';
    });

    return {
        clearTimers,
        hasPendingChanges,
        notePayloadFingerprint,
        saveNote,
        setLastSavedPayloadFingerprint,
        setSaveStatus,
        triggerDebouncedSave,
    };
}

function parseSavedDate(value) {
    if (typeof value !== 'string' || value.trim() === '') return null;

    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? null : parsed;
}

function formatRelativeSavedTime(date) {
    if (!(date instanceof Date) || Number.isNaN(date.getTime())) return '';

    const elapsedSeconds = Math.max(0, Math.floor((Date.now() - date.getTime()) / 1000));
    if (elapsedSeconds < 10) return 'just now';
    if (elapsedSeconds < 60) return `${elapsedSeconds} sec ago`;

    const elapsedMinutes = Math.floor(elapsedSeconds / 60);
    if (elapsedMinutes < 60) {
        return `${elapsedMinutes} min ago`;
    }

    const elapsedHours = Math.floor(elapsedMinutes / 60);
    if (elapsedHours < 24) {
        return `${elapsedHours} hr ago`;
    }

    const elapsedDays = Math.floor(elapsedHours / 24);
    if (elapsedDays < 7) {
        return `${elapsedDays} day${elapsedDays === 1 ? '' : 's'} ago`;
    }

    return date.toLocaleDateString(undefined, {
        month: 'short',
        day: 'numeric',
        year: date.getFullYear() === new Date().getFullYear() ? undefined : 'numeric',
    });
}
