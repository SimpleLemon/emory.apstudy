(() => {
    const CONFIG = window.FILE_SHARE_CONFIG || {};
    const FilesUtils = window.APStudyFilesUtils || {};
    const {
        getElements,
        normalizeFolderId,
        hasFiles,
        isInteractiveTarget,
        formatCount,
        formatExpiry,
        expiryOptionForDate: expiryOptionForDateFromUtils,
        firstUploadError,
        parseUploadResponse,
        uploadErrorMessage,
        showFormError,
        clearFormError,
        setButtonBusy,
        apiJson,
        flattenFolders: flattenFoldersFromUtils,
        isDescendantFolder,
        getFolderName: getFolderNameFromUtils,
        copyText,
        downloadBlob,
        filenameFromDisposition,
        cssEscape,
        escapeHtml,
    } = FilesUtils;
    const FilesRenderers = window.APStudyFilesRenderers || {};
    const { folderCardHtml, fileCardHtml, uploadItemHtml, shareExpiryOptionsHtml, fileMenuItems, folderMenuItems, renderEmptyState: renderEmptyStateFromUtils, setLoading: setLoadingFromUtils, toggleNewMenu: toggleNewMenuFromUtils, closeNewMenu: closeNewMenuFromUtils, openActionMenu: openActionMenuFromUtils, closeActionMenu: closeActionMenuFromUtils } = FilesRenderers;
    const MAX_FILE_SIZE_BYTES = Number(CONFIG.maxFileSize) || (50 * 1024 * 1024);
    const MAX_FILE_SIZE_LABEL = String(CONFIG.maxFileSizeLabel || "50 MB");
    const MAX_UPLOAD_FILES = Number(CONFIG.maxUploadFiles) || 5;
    const ALLOWED_EXPIRY = Array.isArray(CONFIG.allowedExpiryOptions) && CONFIG.allowedExpiryOptions.length
        ? CONFIG.allowedExpiryOptions
        : [1, 3, 7, 14, 30];
    const DEFAULT_EXPIRY = Number(CONFIG.defaultExpiryDays) || ALLOWED_EXPIRY[0] || 1;

    const state = {
        currentFolderId: null,
        currentFolder: null,
        breadcrumbs: [],
        folders: [],
        files: [],
        allFolders: [],
        selectedFileIds: new Set(),
        selectedFolderIds: new Set(),
        uploadItems: [],
        uploadTargetFolderId: null,
        uploadTargetName: "My Files",
        folderModalMode: null,
        moveContext: null,
        confirmContext: null,
        shareContext: null,
        activeModal: null,
        lastFocusedElement: null,
        actionMenu: null,
        actionMenuAnchor: null,
        dragDepth: 0,
    };

    let els = {};
    let modals = null;
    let workflows = null;
    const initialParams = new URLSearchParams(window.location.search);
    const initialFolderId = initialParams.get("folder");
    let pendingFileId = initialParams.get("file");

    function restoreAtIndex(items, item, index) {
        if (!item || index < 0 || items.some((candidate) => candidate.id === item.id)) return items;
        const next = [...items];
        next.splice(Math.min(Math.max(0, index), next.length), 0, item);
        return next;
    }

    document.addEventListener("DOMContentLoaded", () => {
        els = getElements();
        modals = window.APStudyFilesModals.createFilesModals({
            state,
            els,
            callbacks: {
                apiJson,
                clearFormError,
                clearSelection,
                escapeHtml,
                flattenFolders: flattenFoldersFromUtils,
                formatCount,
                isDescendantFolder,
                loadFolder,
                normalizeFolderId,
                setButtonBusy,
                showAlert,
                showFormError,
                notify,
            },
        });
        workflows = window.APStudyFilesWorkflows.createFilesWorkflows({
            state,
            els,
            constants: {
                allowedExpiry: ALLOWED_EXPIRY,
                defaultExpiry: DEFAULT_EXPIRY,
                maxFileSizeBytes: MAX_FILE_SIZE_BYTES,
                maxFileSizeLabel: MAX_FILE_SIZE_LABEL,
                maxUploadFiles: MAX_UPLOAD_FILES,
            },
            callbacks: {
                apiJson,
                clearFormError,
                closeModal: { close: closeModal, open: openModal },
                copyText,
                cssEscape,
                downloadBlob,
                expiryOptionForDate: expiryOptionForDateFromUtils,
                filenameFromDisposition,
                firstUploadError,
                formatCount,
                formatExpiry,
                getFolderName: getFolderNameFromUtils,
                loadFolder,
                normalizeFolderId,
                renderManager,
                setButtonBusy,
                shareExpiryOptionsHtml,
                showAlert,
                showFormError,
                notify,
                parseUploadResponse,
                uploadItemHtml,
                uploadErrorMessage,
            },
        });
        window.APStudyFilesEvents.bindFilesEvents({
            els,
            state,
            actions: {
                addUploadFiles,
                clearSelection,
                closeModal,
                copyCurrentShareLink,
                downloadSelectedFiles,
                loadFolder,
                openBulkDeleteConfirm,
                openFolderModal,
                openMoveModal,
                openUploadModal,
                runConfirmAction,
                saveFolderModal,
                saveMoveModal,
                saveShareExpiry,
                saveShareVisibility,
                selectedTotal,
                uploadSelectedFiles,
            },
            callbacks: {
                closeActionMenu: closeActionMenuFromUtils,
                closeNewMenu: closeNewMenuFromUtils,
                hasFiles,
                toggleNewMenu: toggleNewMenuFromUtils,
            },
        });
        void loadFolder(initialFolderId);
    });

    async function loadFolder(folderId) {
        state.currentFolderId = normalizeFolderId(folderId);
        clearAlert();
        closeActionMenuFromUtils(state);
        setLoading(true);
        clearSelection();

        try {
            const query = state.currentFolderId ? `?folderId=${encodeURIComponent(state.currentFolderId)}` : "";
            const payload = await apiJson(`/api/files/my${query}`);
            state.currentFolder = payload.currentFolder || null;
            state.breadcrumbs = Array.isArray(payload.breadcrumbs) ? payload.breadcrumbs : [];
            state.folders = Array.isArray(payload.folders) ? payload.folders : [];
            state.files = Array.isArray(payload.files) ? payload.files : [];
            state.allFolders = Array.isArray(payload.allFolders) ? payload.allFolders : [];
            renderManager();
            revealPendingFile();
        } catch (error) {
            console.error(error);
            state.folders = [];
            state.files = [];
            renderManager();
            showAlert(error.message || "Refresh the page and try again.", "error", { title: "Couldn’t load files" });
        } finally {
            setLoading(false);
            renderEmptyState();
        }
    }

    function revealPendingFile() {
        if (!pendingFileId || !els.filesRoot) return;
        const selector = `[data-file-id="${cssEscape(pendingFileId)}"]`;
        const row = els.filesRoot.querySelector(selector);
        pendingFileId = null;
        const url = new URL(window.location.href);
        url.searchParams.delete("file");
        url.searchParams.delete("folder");
        window.history.replaceState({}, "", `${url.pathname}${url.search}${url.hash}`);

        if (!row) {
            showAlert("That file is no longer available in this folder.", "error", { title: "File not found" });
            return;
        }
        row.classList.add("is-search-target");
        window.requestAnimationFrame(() => {
            row.scrollIntoView({ behavior: "smooth", block: "center" });
            row.focus({ preventScroll: true });
        });
        window.setTimeout(() => row.classList.remove("is-search-target"), 2400);
    }

    function renderManager() {
        renderHeader();
        renderBreadcrumbs();
        renderFolders();
        renderFiles();
        renderEmptyState();
        updateSelectionBar();
    }

    function renderHeader() {
        const title = state.currentFolder?.name || "My Files";
        const folderCount = state.folders.length;
        const fileCount = state.files.length;
        if (els.folderTitle) els.folderTitle.textContent = title;
        if (els.folderMeta) {
            els.folderMeta.textContent = `${formatCount(folderCount, "folder")} / ${formatCount(fileCount, "file")}`;
        }
        if (els.folderZip) {
            els.folderZip.href = `/api/files/folders/${encodeURIComponent(state.currentFolderId || "root")}/download.zip`;
        }
    }

    function renderBreadcrumbs() {
        if (!els.breadcrumbs) return;
        const crumbs = state.breadcrumbs.length
            ? state.breadcrumbs
            : [{ id: null, name: "My Files" }];
        const items = crumbs.map((crumb, index) => ({
            label: crumb.name || "My Files",
            id: crumb.id || "root",
            current: index === crumbs.length - 1,
        }));
        window.APStudyBreadcrumb?.renderBreadcrumb(els.breadcrumbs, items, {
            collapseAfter: 4,
            escapeHtml,
            onNavigate: (folderId) => void loadFolder(folderId),
        });
    }

    function renderFolders() {
        if (!els.foldersRoot || !els.foldersSection) return;
        els.foldersSection.hidden = state.folders.length === 0;
        els.foldersRoot.innerHTML = state.folders
            .map((folder) => folderCardHtml(folder, state.selectedFolderIds.has(folder.id)))
            .join("");

        els.foldersRoot.querySelectorAll(".files-folder-card").forEach((row) => {
            const folder = state.folders.find((item) => item.id === row.dataset.folderId);
            if (!folder) return;
            row.addEventListener("click", (event) => {
                if (isInteractiveTarget(event.target)) return;
                void loadFolder(folder.id);
            });
            row.addEventListener("keydown", (event) => {
                if (event.key === "Enter" && !isInteractiveTarget(event.target)) {
                    event.preventDefault();
                    void loadFolder(folder.id);
                }
            });
            row.addEventListener("dragover", (event) => {
                if (!hasFiles(event)) return;
                event.preventDefault();
                event.stopPropagation();
                row.classList.add("is-folder-drag-target");
            });
            row.addEventListener("dragleave", () => row.classList.remove("is-folder-drag-target"));
            row.addEventListener("drop", (event) => {
                if (!hasFiles(event)) return;
                event.preventDefault();
                event.stopPropagation();
                row.classList.remove("is-folder-drag-target");
                openUploadModal(folder.id, Array.from(event.dataTransfer?.files || []));
            });
        });
        els.foldersRoot.querySelectorAll("[data-select-folder]").forEach((checkbox) => {
            checkbox.addEventListener("change", () => {
                setSelection("folder", checkbox.dataset.selectFolder, checkbox.checked);
            });
        });
        els.foldersRoot.querySelectorAll("[data-folder-menu]").forEach((button) => {
            button.addEventListener("click", (event) => {
                event.stopPropagation();
                const folder = state.folders.find((item) => item.id === button.dataset.folderMenu);
                if (folder) openFolderMenu(button, folder);
            });
        });
    }

    function renderFiles() {
        if (!els.filesRoot || !els.filesSection) return;
        els.filesSection.hidden = state.files.length === 0;
        els.filesRoot.innerHTML = state.files
            .map((file) => fileCardHtml(file, state.selectedFileIds.has(file.id)))
            .join("");

        els.filesRoot.querySelectorAll("[data-select-file]").forEach((checkbox) => {
            checkbox.addEventListener("change", () => {
                setSelection("file", checkbox.dataset.selectFile, checkbox.checked);
            });
        });
        els.filesRoot.querySelectorAll("[data-file-menu]").forEach((button) => {
            button.addEventListener("click", (event) => {
                event.stopPropagation();
                const file = state.files.find((item) => item.id === button.dataset.fileMenu);
                if (file) openFileMenu(button, file);
            });
        });
    }

    function renderEmptyState() {
        renderEmptyStateFromUtils(els.empty, els.loading, state.folders, state.files);
    }

    function setLoading(isLoading) {
        setLoadingFromUtils(els, isLoading);
    }

    function openFileMenu(anchor, file) {
        openActionMenuFromUtils(anchor, fileMenuItems(file, {
            downloadFile,
            openShareModal,
            openFolderModal,
            openMoveModal,
            openFileDeleteConfirm,
        }), state);
    }

    function openFolderMenu(anchor, folder) {
        openActionMenuFromUtils(anchor, folderMenuItems(folder, {
            openShareModal,
            openFolderModal,
            openMoveModal,
            openFolderDeleteConfirm,
        }), state);
    }

    function openFolderModal(mode, item = null) {
        return modals.openFolderModal(mode, item);
    }

    async function saveFolderModal() {
        return modals.saveFolderModal();
    }

    function openMoveModal(context) {
        return modals.openMoveModal(context);
    }

    async function saveMoveModal() {
        return modals.saveMoveModal();
    }

    function openUploadModal(...args) {
        return workflows.openUploadModal(...args);
    }

    function addUploadFiles(...args) {
        return workflows.addUploadFiles(...args);
    }

    async function uploadSelectedFiles(...args) {
        return workflows.uploadSelectedFiles(...args);
    }

    function openFileDeleteConfirm(file) {
        openConfirm({
            title: "Delete file?",
            message: `${file.filename} will be deleted. You’ll have a short time to undo.`,
            submitLabel: "Delete",
            onConfirm: async () => {
                const fileIndex = state.files.findIndex((item) => item.id === file.id);
                state.files = state.files.filter((item) => item.id !== file.id);
                state.selectedFileIds.delete(file.id);
                renderManager();
                window.APStudyUndo?.stage?.({
                    message: `${file.filename} deleted.`,
                    commit: ({ reason }) => apiJson(`/api/files/my/${encodeURIComponent(file.id)}`, {
                        method: "DELETE",
                        keepalive: reason === "pagehide",
                    }),
                    restore: () => {
                        state.files = restoreAtIndex(state.files, file, fileIndex);
                        renderManager();
                    },
                    errorTitle: "Couldn’t delete file",
                });
                if (!window.APStudyUndo?.stage) {
                    try {
                        await apiJson(`/api/files/my/${encodeURIComponent(file.id)}`, { method: "DELETE" });
                    } catch (error) {
                        state.files = restoreAtIndex(state.files, file, fileIndex);
                        renderManager();
                        throw error;
                    }
                }
            },
        });
    }

    function openFolderDeleteConfirm(folder) {
        openConfirm({
            title: "Delete folder?",
            message: `${folder.name} and everything inside it will be deleted. Type the folder name to confirm.`,
            submitLabel: "Delete",
            requiredText: folder.name,
            requiredLabel: "Type the folder name",
            onConfirm: async () => {
                const folderIndex = state.folders.findIndex((item) => item.id === folder.id);
                const allFolderIndex = state.allFolders.findIndex((item) => item.id === folder.id);
                const allFolder = state.allFolders[allFolderIndex] || folder;
                state.folders = state.folders.filter((item) => item.id !== folder.id);
                state.allFolders = state.allFolders.filter((item) => item.id !== folder.id);
                state.selectedFolderIds.delete(folder.id);
                renderManager();
                window.APStudyUndo?.stage?.({
                    message: `${folder.name} and its contents deleted.`,
                    commit: ({ reason }) => apiJson(`/api/files/folders/${encodeURIComponent(folder.id)}`, {
                        method: "DELETE",
                        keepalive: reason === "pagehide",
                    }),
                    restore: () => {
                        state.folders = restoreAtIndex(state.folders, folder, folderIndex);
                        state.allFolders = restoreAtIndex(state.allFolders, allFolder, allFolderIndex);
                        renderManager();
                    },
                    errorTitle: "Couldn’t delete folder",
                });
                if (!window.APStudyUndo?.stage) {
                    try {
                        await apiJson(`/api/files/folders/${encodeURIComponent(folder.id)}`, { method: "DELETE" });
                    } catch (error) {
                        state.folders = restoreAtIndex(state.folders, folder, folderIndex);
                        state.allFolders = restoreAtIndex(state.allFolders, allFolder, allFolderIndex);
                        renderManager();
                        throw error;
                    }
                }
            },
        });
    }

    function openBulkDeleteConfirm() {
        const fileIds = Array.from(state.selectedFileIds);
        const folderIds = Array.from(state.selectedFolderIds);
        if (!fileIds.length && !folderIds.length) return;
        const requiresText = folderIds.length > 0;
        openConfirm({
            title: "Delete selected items?",
            message: requiresText
                ? "Selected folders and files will be deleted. Type DELETE to confirm."
                : "Selected files will be deleted. You’ll have a short time to undo.",
            submitLabel: "Delete",
            requiredText: requiresText ? "DELETE" : "",
            requiredLabel: "Type DELETE",
            onConfirm: async () => {
                const fileIdSet = new Set(fileIds);
                const folderIdSet = new Set(folderIds);
                const removedFiles = state.files
                    .map((item, index) => ({ item, index }))
                    .filter((record) => fileIdSet.has(record.item.id));
                const removedFolders = state.folders
                    .map((item, index) => ({ item, index }))
                    .filter((record) => folderIdSet.has(record.item.id));
                const removedAllFolders = state.allFolders
                    .map((item, index) => ({ item, index }))
                    .filter((record) => folderIdSet.has(record.item.id));
                state.files = state.files.filter((item) => !fileIdSet.has(item.id));
                state.folders = state.folders.filter((item) => !folderIdSet.has(item.id));
                state.allFolders = state.allFolders.filter((item) => !folderIdSet.has(item.id));
                clearSelection();
                renderManager();
                const total = fileIds.length + folderIds.length;
                window.APStudyUndo?.stage?.({
                    message: `${formatCount(total, "item")} deleted.`,
                    commit: async ({ reason }) => {
                        for (const fileId of fileIds) {
                            await apiJson(`/api/files/my/${encodeURIComponent(fileId)}`, {
                                method: "DELETE",
                                keepalive: reason === "pagehide",
                            });
                        }
                        for (const folderId of folderIds) {
                            await apiJson(`/api/files/folders/${encodeURIComponent(folderId)}`, {
                                method: "DELETE",
                                keepalive: reason === "pagehide",
                            });
                        }
                    },
                    restore: async ({ reason }) => {
                        if (reason === "commit-error") {
                            await loadFolder(state.currentFolderId);
                            return;
                        }
                        state.files = removedFiles.reduce(
                            (items, record) => restoreAtIndex(items, record.item, record.index),
                            state.files,
                        );
                        state.folders = removedFolders.reduce(
                            (items, record) => restoreAtIndex(items, record.item, record.index),
                            state.folders,
                        );
                        state.allFolders = removedAllFolders.reduce(
                            (items, record) => restoreAtIndex(items, record.item, record.index),
                            state.allFolders,
                        );
                        renderManager();
                    },
                    errorTitle: "Couldn’t delete every selected item",
                    errorMessage: "The folder was refreshed to show what remains.",
                });
                if (!window.APStudyUndo?.stage) {
                    for (const fileId of fileIds) {
                        await apiJson(`/api/files/my/${encodeURIComponent(fileId)}`, { method: "DELETE" });
                    }
                    for (const folderId of folderIds) {
                        await apiJson(`/api/files/folders/${encodeURIComponent(folderId)}`, { method: "DELETE" });
                    }
                }
            },
        });
    }

    function openConfirm(...args) {
        return modals.openConfirm(...args);
    }

    async function runConfirmAction(...args) {
        return modals.runConfirmAction(...args);
    }

    function openShareModal(...args) {
        return workflows.openShareModal(...args);
    }

    async function saveShareVisibility(...args) {
        return workflows.saveShareVisibility(...args);
    }

    async function saveShareExpiry(...args) {
        return workflows.saveShareExpiry(...args);
    }

    async function copyCurrentShareLink(...args) {
        return workflows.copyCurrentShareLink(...args);
    }

    async function setFolderVisibility(...args) {
        return workflows.setFolderVisibility(...args);
    }

    function downloadFile(...args) {
        return workflows.downloadFile(...args);
    }

    async function downloadSelectedFiles(...args) {
        return workflows.downloadSelectedFiles(...args);
    }

    function setSelection(...args) {
        return workflows.setSelection(...args);
    }

    function clearSelection(...args) {
        return workflows.clearSelection(...args);
    }

    function updateSelectionBar(...args) {
        return workflows.updateSelectionBar(...args);
    }

    function selectedTotal(...args) {
        return workflows.selectedTotal(...args);
    }

    function openModal(modal, focusTarget = null) {
        return modals.openModal(modal, focusTarget);
    }

    function closeModal(...args) {
        return modals.closeModal(...args);
    }

    function showAlert(message, type = "info", options = {}) {
        if (!window.APStudyToast) return;
        const toastType =
            type === "error" ? "error" : type === "warning" ? "warning" : type === "info" ? "info" : "success";
        window.APStudyToast.show({
            message,
            title: options.title,
            type: toastType,
            action: options.action,
            duration: options.duration,
        });
    }

    function notify(message, type = "info", options = {}) {
        const { modalError, field } = options;
        if (modalError) showFormError(modalError, message, field);
        if (type === "error" || type === "warning") {
            showAlert(message, type, options);
        }
    }

    function clearAlert() {
        if (!els.alert) return;
        els.alert.hidden = true;
        els.alert.textContent = "";
        els.alert.classList.remove("files-alert-error");
    }

})();
