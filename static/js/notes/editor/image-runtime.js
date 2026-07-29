import {
    NOTE_IMAGE_ACCEPT,
    droppedImageFiles,
    noteImageError,
    removeInlineImage,
    restoreInlineImage,
    updateInlineImage,
} from './images.js';

const pendingImageFiles = new Map();
let activeDialog = null;
let activeDialogResolve = null;

function csrfToken() {
    const entry = document.cookie.split(';').map((item) => item.trim()).find((item) => item.startsWith('csrf_token='));
    return entry ? decodeURIComponent(entry.slice('csrf_token='.length)) : '';
}

function closeImageDialog(result = null) {
    activeDialog?.remove();
    activeDialog = null;
    const resolve = activeDialogResolve;
    activeDialogResolve = null;
    resolve?.(result);
}

export function requestImageSource(anchorRect, validateUrl) {
    closeImageDialog();
    return new Promise((resolve) => {
        activeDialogResolve = resolve;
        activeDialog = document.createElement('form');
        activeDialog.className = 'notes-url-block-popover notes-image-insert-popover';
        activeDialog.innerHTML = `
            <div class="notes-image-tabs" role="tablist">
                <button type="button" class="is-active" data-image-tab="upload" role="tab" aria-selected="true">Upload</button>
                <button type="button" data-image-tab="url" role="tab" aria-selected="false">Embed URL</button>
            </div>
            <div data-image-panel="upload">
                <label class="notes-image-dropzone">
                    <span class="material-symbols-outlined" aria-hidden="true">add_photo_alternate</span>
                    <strong>Choose or drop an image</strong>
                    <small>JPEG, PNG, GIF, or WebP · up to 10 MiB</small>
                    <input type="file" name="file" accept="${NOTE_IMAGE_ACCEPT}" />
                </label>
            </div>
            <div data-image-panel="url" hidden>
                <label class="notes-url-block-field"><span>Image URL</span><input type="url" name="url" placeholder="https://example.com/image.png" autocomplete="off" data-gramm="false" data-gramm_editor="false" data-enable-grammarly="false" spellcheck="false" /></label>
            </div>
            <div class="notes-url-block-error" role="alert" hidden></div>
            <div class="notes-url-block-actions"><button type="button" data-image-cancel>Cancel</button><button type="submit">Insert</button></div>
        `;
        document.body.appendChild(activeDialog);
        const rect = anchorRect || { left: 16, bottom: 80 };
        const width = Math.min(400, window.innerWidth - 16);
        activeDialog.style.width = `${width}px`;
        activeDialog.style.left = `${Math.max(8, Math.min(rect.left || 8, window.innerWidth - width - 8))}px`;
        activeDialog.style.top = `${Math.max(8, Math.min((rect.bottom || 80) + 8, window.innerHeight - 380))}px`;
        let activeTab = 'upload';
        let droppedFile = null;
        const error = activeDialog.querySelector('.notes-url-block-error');
        const showError = (message) => {
            error.hidden = !message;
            error.textContent = message || '';
        };
        activeDialog.querySelectorAll('[data-image-tab]').forEach((button) => button.addEventListener('click', () => {
            activeTab = button.dataset.imageTab;
            activeDialog.querySelectorAll('[data-image-tab]').forEach((tab) => {
                const active = tab === button;
                tab.classList.toggle('is-active', active);
                tab.setAttribute('aria-selected', String(active));
            });
            activeDialog.querySelectorAll('[data-image-panel]').forEach((panel) => { panel.hidden = panel.dataset.imagePanel !== activeTab; });
            if (activeTab === 'url') {
                const urlInput = activeDialog.querySelector('[name="url"]');
                window.APStudyFormField?.clearInvalid?.(urlInput);
            }
            showError('');
        }));
        const dropzone = activeDialog.querySelector('.notes-image-dropzone');
        dropzone.addEventListener('dragover', (event) => { event.preventDefault(); dropzone.classList.add('is-dragging'); });
        dropzone.addEventListener('dragleave', () => dropzone.classList.remove('is-dragging'));
        dropzone.addEventListener('drop', (event) => {
            event.preventDefault();
            dropzone.classList.remove('is-dragging');
            droppedFile = droppedImageFiles(event.dataTransfer)[0] || null;
            showError(noteImageError(droppedFile));
            if (droppedFile) dropzone.querySelector('strong').textContent = droppedFile.name;
        });
        activeDialog.addEventListener('submit', (event) => {
            event.preventDefault();
            if (activeTab === 'url') {
                const urlInput = activeDialog.querySelector('[name="url"]');
                const url = validateUrl(urlInput?.value);
                if (!url) {
                    window.APStudyFormField?.markInvalid?.(urlInput);
                    return showError('Enter a valid http or https URL.');
                }
                window.APStudyFormField?.clearInvalid?.(urlInput);
                closeImageDialog({ url });
                return;
            }
            const file = droppedFile || activeDialog.querySelector('[name="file"]')?.files?.[0];
            const message = noteImageError(file);
            if (message) return showError(message);
            closeImageDialog({ file });
        });
        activeDialog.querySelector('[data-image-cancel]').addEventListener('click', () => closeImageDialog());
    });
}

export function insertInlineImageNode(editor, props) {
    if (!editor) return false;
    try {
        editor.insertInlineContent([{ type: 'inlineImage', props }]);
        return true;
    } catch (error) {
        const current = editor.getTextCursorPosition?.()?.block;
        if (!current) return false;
        const paragraph = editor.insertBlocks?.([{ type: 'paragraph' }], current, 'after')?.[0];
        if (!paragraph) return false;
        editor.setTextCursorPosition(paragraph);
        editor.insertInlineContent([{ type: 'inlineImage', props }]);
        return true;
    }
}

export function uploadInlineImage(file, clientId, { editor, noteId, onChange }) {
    const errorMessage = noteImageError(file);
    if (errorMessage) {
        updateInlineImage(editor, clientId, { status: 'error', error: errorMessage });
        return;
    }
    pendingImageFiles.set(clientId, file);
    const xhr = new XMLHttpRequest();
    xhr.open('POST', `/api/notes/${encodeURIComponent(noteId)}/media`);
    const token = csrfToken();
    if (token) xhr.setRequestHeader('X-CSRFToken', token);
    xhr.upload.onprogress = (event) => {
        if (event.lengthComputable) window.dispatchEvent(new CustomEvent('notes-image-upload-progress', { detail: { clientId, progress: Math.round((event.loaded / event.total) * 100) } }));
    };
    xhr.onload = () => {
        let payload = {};
        try { payload = JSON.parse(xhr.responseText || '{}'); } catch (error) { payload = {}; }
        if (xhr.status < 200 || xhr.status >= 300) {
            updateInlineImage(editor, clientId, { status: 'error', error: payload.error || 'Upload failed.' });
            return;
        }
        pendingImageFiles.delete(clientId);
        updateInlineImage(editor, clientId, {
            status: 'ready', error: '', url: payload.url || '', mediaId: payload.id || '',
            alt: payload.name || file.name || '', width: Math.max(48, Math.min(240, Number(payload.width || 240))),
        });
        onChange();
    };
    xhr.onerror = () => updateInlineImage(editor, clientId, { status: 'error', error: 'Network error during upload.' });
    const formData = new FormData();
    formData.append('file', file, file.name || 'clipboard-image.png');
    xhr.send(formData);
}

export function insertInlineImageFile(editor, file, options) {
    const clientId = globalThis.crypto?.randomUUID?.() || `image-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    if (!insertInlineImageNode(editor, { url: '', mediaId: '', clientId, alt: file.name || '', width: 240, layout: 'inline', alignment: 'left', status: 'uploading', error: '' })) return false;
    uploadInlineImage(file, clientId, { editor, ...options });
    return true;
}

export function bindImageRuntime({ editor, editorPage, noteId, onChange, openDialog }) {
    const options = { noteId, onChange };
    const retry = (event) => {
        const clientId = event.detail?.clientId;
        const file = pendingImageFiles.get(clientId);
        if (file) {
            updateInlineImage(editor, clientId, { status: 'uploading', error: '' });
            uploadInlineImage(file, clientId, { editor, ...options });
        }
    };
    const remove = (event) => {
        const removed = removeInlineImage(editor, event.detail?.clientId);
        if (!removed) return;
        onChange();
        window.APStudyUndo?.stage?.({
            message: 'Image deleted from note.',
            restore: () => {
                if (restoreInlineImage(editor, removed)) onChange();
            },
        });
    };
    const replace = (event) => {
        const input = document.createElement('input');
        input.type = 'file';
        input.accept = NOTE_IMAGE_ACCEPT;
        input.addEventListener('change', () => {
            const file = input.files?.[0];
            if (!file) return;
            updateInlineImage(editor, event.detail?.clientId, { status: 'uploading', error: '' });
            uploadInlineImage(file, event.detail?.clientId, { editor, ...options });
        }, { once: true });
        input.click();
    };
    const legacyInsert = async (event) => {
        const block = editor.getBlock?.(event.detail?.blockId);
        if (block) editor.setTextCursorPosition?.(block);
        const inserted = await openDialog(event.detail?.anchorRect, editor);
        if (inserted && block) editor.removeBlocks?.([block]);
    };
    const drop = (event) => {
        const files = droppedImageFiles(event.dataTransfer);
        if (!files.length) return;
        event.preventDefault();
        const position = editor.prosemirrorView?.posAtCoords?.({ left: event.clientX, top: event.clientY });
        if (position?.pos) editor._tiptapEditor?.commands?.setTextSelection?.(position.pos);
        files.forEach((file) => insertInlineImageFile(editor, file, options));
    };
    window.addEventListener('notes-image-retry', retry);
    window.addEventListener('notes-image-remove', remove);
    window.addEventListener('notes-image-replace', replace);
    window.addEventListener('notes-image-insert', legacyInsert);
    editorPage?.addEventListener('drop', drop, true);
    return () => {
        closeImageDialog();
        window.removeEventListener('notes-image-retry', retry);
        window.removeEventListener('notes-image-remove', remove);
        window.removeEventListener('notes-image-replace', replace);
        window.removeEventListener('notes-image-insert', legacyInsert);
        editorPage?.removeEventListener('drop', drop, true);
    };
}
