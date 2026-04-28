/* file_share.js - modal multi-file upload and file list UI
   Reads runtime config from window.FILE_SHARE_CONFIG (injected by template)
*/
const MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024;
const MAX_FILES = 5;

document.addEventListener("DOMContentLoaded", () => {
    const openModalBtn = document.getElementById("file-share-open-modal");
    const closeModalBtn = document.getElementById("file-share-close-modal");
    const modal = document.getElementById("file-share-modal");
    const inputEl = document.getElementById("file-share-input");
    const pickButton = document.getElementById("file-share-pick");
    const dropzone = document.getElementById("file-share-dropzone");
    const selectedList = document.getElementById("file-share-selected-list");
    const uploadButton = document.getElementById("file-share-upload");
    const progressWrap = document.getElementById("file-share-progress-wrap");
    const progressBar = document.getElementById("file-share-progress-bar");
    const errorBox = document.getElementById("file-share-error");
    const filesRoot = document.getElementById("file-share-files");

    const allowedExpiry = (window.FILE_SHARE_CONFIG && window.FILE_SHARE_CONFIG.allowedExpiryOptions) || [1,3,7,14,30];

    if (openModalBtn) openModalBtn.addEventListener("click", openModal);
    if (closeModalBtn) closeModalBtn.addEventListener("click", closeModal);
    if (pickButton && inputEl) pickButton.addEventListener("click", () => inputEl.click());
    if (inputEl) inputEl.addEventListener("change", () => handleSelection(Array.from(inputEl.files || [])));

    if (dropzone) {
        dropzone.addEventListener("click", () => inputEl && inputEl.click());
        dropzone.addEventListener("dragover", (e) => { e.preventDefault(); dropzone.classList.add("border-primary", "bg-primary/5"); });
        dropzone.addEventListener("dragleave", (e) => { e.preventDefault(); dropzone.classList.remove("border-primary", "bg-primary/5"); });
        dropzone.addEventListener("drop", (e) => { e.preventDefault(); e.stopPropagation(); dropzone.classList.remove("border-primary", "bg-primary/5"); handleSelection(Array.from(e.dataTransfer?.files || [])); });
    }

    if (uploadButton) uploadButton.addEventListener("click", () => { void uploadAll(); });

    // Selected files model
    let selectedFiles = [];

    async function openModal() {
        if (!modal) return;
        modal.classList.remove("hidden");
        clearError();
        selectedFiles = [];
        if (selectedList) selectedList.innerHTML = "";
        if (inputEl) inputEl.value = "";
    }
    function closeModal() { if (modal) modal.classList.add("hidden"); }

    function handleSelection(files) {
        clearError();
        if (!files || files.length === 0) return;

        if (files.length > MAX_FILES) {
            showError(`Selected more than ${MAX_FILES} files — only the first ${MAX_FILES} will be uploaded.`);
            files = files.slice(0, MAX_FILES);
        }

        selectedFiles = [];
        if (selectedList) selectedList.innerHTML = "";

        files.forEach((file, idx) => {
            const id = `fileitem-${Date.now()}-${idx}`;
            const wrapper = document.createElement("div");
            wrapper.className = "rounded-lg border border-outline-variant/20 bg-surface-container-lowest px-3 py-3 flex flex-col gap-2";
            wrapper.id = id;

            const titleRow = document.createElement("div"); titleRow.className = "flex items-center justify-between gap-2";
            const nameInput = document.createElement("input"); nameInput.type = "text"; nameInput.value = file.name; nameInput.className = "flex-1 rounded-md border border-outline-variant/15 px-3 py-2 text-sm bg-surface-container-lowest";
            const sizeEl = document.createElement("div"); sizeEl.className = "text-xs text-on-surface-variant ml-3"; sizeEl.textContent = formatBytes(file.size);
            titleRow.appendChild(nameInput); titleRow.appendChild(sizeEl);

            const controlsRow = document.createElement("div"); controlsRow.className = "flex items-center gap-2 mt-2";
            const visSelect = document.createElement("select"); visSelect.className = "rounded-md border border-outline-variant/15 px-2 py-1 text-sm bg-surface-container-lowest"; visSelect.innerHTML = `<option value=\"private\">Private</option><option value=\"public\">Public</option>`;
            const expirySelect = document.createElement("select"); expirySelect.className = "rounded-md border border-outline-variant/15 px-2 py-1 text-sm bg-surface-container-lowest";
            allowedExpiry.forEach(d => { const o = document.createElement("option"); o.value = String(d); o.textContent = `${d} day${d !== 1 ? "s" : ""}`; expirySelect.appendChild(o); });
            const removeBtn = document.createElement("button"); removeBtn.type = "button"; removeBtn.className = "text-xs text-error"; removeBtn.textContent = "Remove";
            removeBtn.addEventListener("click", () => { selectedFiles = selectedFiles.filter(s => s.id !== id); wrapper.remove(); });

            controlsRow.appendChild(visSelect); controlsRow.appendChild(expirySelect); controlsRow.appendChild(removeBtn);
            wrapper.appendChild(titleRow); wrapper.appendChild(controlsRow);
            if (selectedList) selectedList.appendChild(wrapper);

            selectedFiles.push({ id, file, nameInput, visSelect, expirySelect });
        });
    }

    async function uploadAll() {
        clearError();
        if (!selectedFiles.length) { showError("Select at least one file to upload."); return; }

        for (const item of selectedFiles) {
            if (item.file.size > MAX_FILE_SIZE_BYTES) { showError(`File ${item.file.name} exceeds the 50 MB limit.`); return; }
            if (!item.nameInput.value || !item.nameInput.value.trim()) { showError("Filename cannot be empty."); return; }
        }

        uploadButton.disabled = true;
        if (progressWrap) progressWrap.classList.remove("hidden");
        if (progressBar) progressBar.style.width = "0%";

        const formData = new FormData();
        selectedFiles.forEach(item => {
            formData.append("file", item.file);
            formData.append("filename", item.nameInput.value.trim());
            formData.append("visibility", item.visSelect.value);
            formData.append("expiryDays", item.expirySelect.value);
        });

        const xhr = new XMLHttpRequest(); xhr.open("POST", "/api/files/upload", true); xhr.responseType = "json";
        xhr.upload.onprogress = (e) => { if (!e.lengthComputable) return; const pct = Math.max(0, Math.min(100, Math.round((e.loaded / e.total) * 100))); if (progressBar) progressBar.style.width = `${pct}%`; };
        xhr.onload = async () => {
            uploadButton.disabled = false; if (progressWrap) progressWrap.classList.add("hidden"); if (progressBar) progressBar.style.width = "0%";
            if (xhr.status < 200 || xhr.status >= 300) { showError(xhr.response?.error || "Upload failed."); return; }
            const payload = xhr.response || {};
            if (payload.errors && payload.errors.length) showError((payload.errors[0] && payload.errors[0].error) || "Some files failed to upload.");
            closeModal(); await loadFiles();
        };
        xhr.onerror = () => { uploadButton.disabled = false; if (progressWrap) progressWrap.classList.add("hidden"); if (progressBar) progressBar.style.width = "0%"; showError("Upload failed."); };
        xhr.send(formData);
    }

    async function loadFiles() {
        try {
            const response = await fetch("/api/files/my", { credentials: "same-origin" });
            if (!response.ok) throw new Error("Unable to load files.");
            const payload = await response.json(); renderFiles(Array.isArray(payload.files) ? payload.files : []);
        } catch (err) {
            console.error(err); if (filesRoot) filesRoot.innerHTML = `<div class=\"rounded-2xl border border-outline-variant/20 bg-surface-container-low p-6 text-sm text-on-surface-variant\">Unable to load files right now.</div>`;
        }
    }

    function renderFiles(files) {
        if (!files || files.length === 0) { if (filesRoot) filesRoot.innerHTML = `<div class=\"rounded-2xl border border-outline-variant/20 bg-surface-container-low p-6 text-sm text-on-surface-variant\">Use the \"New\" button to add files.</div>`; return; }

        const html = files.map(file => {
            const publicBadge = file.isPublic ? `<span class=\"inline-flex items-center rounded-full bg-primary/10 px-2.5 py-1 text-[11px] font-medium text-primary\">Public</span>` : `<span class=\"inline-flex items-center rounded-full bg-surface-container-high px-2.5 py-1 text-[11px] font-medium text-on-surface-variant\">Private</span>`;
            const makePublicButton = !file.isPublic ? `<button type=\"button\" data-make-public=\"${escapeHtml(file.id)}\" class=\"inline-flex items-center justify-center gap-1.5 rounded-md border border-outline-variant/20 bg-surface-container-high px-3 py-2 text-xs font-medium text-on-surface hover:bg-surface-container-highest transition-colors\">Make public</button>` : "";
            const copyButton = file.isPublic && file.shareUrl ? `<button type=\"button\" data-copy-link=\"${escapeHtml(file.shareUrl)}\" class=\"inline-flex items-center justify-center gap-1.5 rounded-md border border-outline-variant/20 bg-surface-container-high px-3 py-2 text-xs font-medium text-on-surface hover:bg-surface-container-highest transition-colors\">Copy Link</button>` : "";
            return `
                <article class=\"rounded-2xl border border-outline-variant/10 bg-surface-container-low p-5 flex flex-col gap-4 shadow-lg shadow-black/5\">
                    <div class=\"flex items-start justify-between gap-3\">
                        <div class=\"min-w-0\">
                            <h3 class=\"text-sm font-semibold text-on-surface truncate\">${escapeHtml(file.filename)}</h3>
                            <p class=\"text-xs text-on-surface-variant mt-1\">${formatBytes(file.fileSizeBytes)}</p>
                        </div>
                        ${publicBadge}
                    </div>
                    <div class=\"grid grid-cols-2 gap-3 text-xs text-on-surface-variant text-center\">
                        <div class=\"rounded-lg bg-surface-container-lowest border border-outline-variant/15 px-3 py-2\">Downloads<br><span class=\"text-sm font-medium text-on-surface\">${file.downloads ?? 0}</span></div>
                        <div class=\"rounded-lg bg-surface-container-lowest border border-outline-variant/15 px-3 py-2\">Expires<br><span class=\"text-sm font-medium text-on-surface\">${formatDate(file.expiresAt)}</span></div>
                    </div>
                    <div class=\"flex flex-wrap gap-2\">
                        <a href=\"/api/files/my/${encodeURIComponent(file.id)}/download\" class=\"inline-flex items-center justify-center gap-1.5 rounded-md bg-primary text-on-primary px-3 py-2 text-xs font-medium hover:opacity-95 transition-opacity no-underline\">Download</a>
                        ${makePublicButton}
                        ${copyButton}
                        <button type=\"button\" data-delete-file=\"${escapeHtml(file.id)}\" class=\"inline-flex items-center justify-center gap-1.5 rounded-md border border-error/20 bg-error-container/40 px-3 py-2 text-xs font-medium text-error hover:bg-error-container/60 transition-colors\">Delete</button>
                    </div>
                </article>
            `;
        }).join('');
        if (filesRoot) filesRoot.innerHTML = html;

        // events
        filesRoot.querySelectorAll('[data-copy-link]').forEach(btn => btn.addEventListener('click', async () => { const link = btn.getAttribute('data-copy-link'); if (!link) return; await copyText(link); btn.textContent = 'Copied'; setTimeout(() => btn.textContent = 'Copy Link', 1200); }));
        filesRoot.querySelectorAll('[data-make-public]').forEach(btn => btn.addEventListener('click', async () => { const id = btn.getAttribute('data-make-public'); if (!id) return; await makeFilePublic(id); }));
        filesRoot.querySelectorAll('[data-delete-file]').forEach(btn => btn.addEventListener('click', async () => { const id = btn.getAttribute('data-delete-file'); if (!id) return; if (!confirm('Delete this file? This cannot be undone.')) return; await deleteFile(id); }));
    }

    async function makeFilePublic(fileId) { try { const resp = await fetch(`/api/files/my/${encodeURIComponent(fileId)}/visibility`, { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ visibility: 'public' }) }); if (!resp.ok) throw new Error('Failed'); await loadFiles(); } catch (err) { console.error(err); showError('Unable to change visibility.'); } }
    async function deleteFile(fileId) { try { const resp = await fetch(`/api/files/my/${encodeURIComponent(fileId)}`, { method: 'DELETE', credentials: 'same-origin' }); if (!resp.ok) throw new Error('Failed'); await loadFiles(); } catch (err) { console.error(err); showError('Unable to delete that file.'); } }

    function clearError() { if (errorBox) { errorBox.classList.add('hidden'); errorBox.textContent = ''; } }
    function showError(msg) { if (errorBox) { errorBox.textContent = msg; errorBox.classList.remove('hidden'); } }

    // init
    void loadFiles();
});

function formatBytes(bytes) { if (!Number.isFinite(bytes)) return '0 B'; if (bytes < 1024) return `${bytes} B`; const units = ['KB','MB','GB']; let value = bytes / 1024; for (const unit of units) { if (value < 1024 || unit === units[units.length-1]) return `${value.toFixed(value >= 10 ? 1 : 2)} ${unit}`; value /= 1024; } return `${bytes} B`; }

function formatDate(value) { if (!value) return 'Unknown'; const date = new Date(value); if (Number.isNaN(date.getTime())) return value; return date.toLocaleString([], { month: 'short', day: 'numeric', year: 'numeric' }); }

async function copyText(text) { if (!text) return; if (navigator.clipboard && navigator.clipboard.writeText) return navigator.clipboard.writeText(text); const tmp = document.createElement('input'); tmp.value = text; document.body.appendChild(tmp); tmp.select(); document.execCommand('copy'); tmp.remove(); }

function escapeHtml(value) { return String(value).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;').replaceAll("'","&#39;"); }