/* files/utils.js - shared File Share helpers */
(() => {
    const { escapeHtml } = window.APStudyUIPrimitives;
    const DEFAULT_ALLOWED_EXPIRY = [1, 3, 7, 14, 30];

    function normalizeExpiryConfig(config = {}) {
        const allowedExpiry = Array.isArray(config.allowedExpiryOptions) && config.allowedExpiryOptions.length
            ? config.allowedExpiryOptions
            : DEFAULT_ALLOWED_EXPIRY;
        const defaultExpiry = Number(config.defaultExpiryDays) || allowedExpiry[0] || 1;
        return { allowedExpiry, defaultExpiry };
    }

    function normalizeFolderId(value) {
        if (value === null || value === undefined) return null;
        const text = String(value).trim();
        if (!text || ["root", "none", "null", "undefined"].includes(text.toLowerCase())) return null;
        return text;
    }

    function getElements(rootDocument = document) {
        return {
            refresh: rootDocument.getElementById("file-share-refresh"),
            newButton: rootDocument.getElementById("file-share-new-button"),
            newMenu: rootDocument.getElementById("file-share-new-menu"),
            newFolder: rootDocument.querySelector("[data-new-folder]"),
            fileUpload: rootDocument.querySelector("[data-file-upload]"),
            breadcrumbs: rootDocument.getElementById("file-share-breadcrumbs"),
            dropSurface: rootDocument.getElementById("file-share-drop-surface"),
            folderTitle: rootDocument.getElementById("file-share-folder-title"),
            folderMeta: rootDocument.getElementById("file-share-folder-meta"),
            folderZip: rootDocument.getElementById("file-share-folder-zip"),
            alert: rootDocument.getElementById("file-share-alert"),
            loading: rootDocument.getElementById("file-share-loading"),
            foldersSection: rootDocument.getElementById("file-share-folders-section"),
            foldersRoot: rootDocument.getElementById("file-share-folders"),
            filesSection: rootDocument.getElementById("file-share-files-section"),
            filesRoot: rootDocument.getElementById("file-share-files"),
            empty: rootDocument.getElementById("file-share-empty"),
            uploadModal: rootDocument.getElementById("file-share-upload-modal"),
            uploadTarget: rootDocument.getElementById("file-share-upload-target"),
            input: rootDocument.getElementById("file-share-input"),
            pick: rootDocument.getElementById("file-share-pick"),
            dropzone: rootDocument.getElementById("file-share-dropzone"),
            selectedList: rootDocument.getElementById("file-share-selected-list"),
            uploadButton: rootDocument.getElementById("file-share-upload"),
            progressWrap: rootDocument.getElementById("file-share-progress-wrap"),
            progressBar: rootDocument.getElementById("file-share-progress-bar"),
            uploadError: rootDocument.getElementById("file-share-error"),
            folderModal: rootDocument.getElementById("file-share-folder-modal"),
            folderForm: rootDocument.getElementById("file-share-folder-form"),
            folderModalTitle: rootDocument.getElementById("file-share-folder-modal-title"),
            folderModalSubtitle: rootDocument.getElementById("file-share-folder-modal-subtitle"),
            folderName: rootDocument.getElementById("file-share-folder-name"),
            folderSave: rootDocument.getElementById("file-share-folder-save"),
            folderError: rootDocument.getElementById("file-share-folder-error"),
            moveModal: rootDocument.getElementById("file-share-move-modal"),
            moveForm: rootDocument.getElementById("file-share-move-form"),
            moveTitle: rootDocument.getElementById("file-share-move-title"),
            moveSubtitle: rootDocument.getElementById("file-share-move-subtitle"),
            moveDestination: rootDocument.getElementById("file-share-move-destination"),
            moveError: rootDocument.getElementById("file-share-move-error"),
            moveSave: rootDocument.getElementById("file-share-move-save"),
            shareModal: rootDocument.getElementById("file-share-share-modal"),
            shareTitle: rootDocument.getElementById("file-share-share-title"),
            shareSubtitle: rootDocument.getElementById("file-share-share-subtitle"),
            shareName: rootDocument.getElementById("file-share-share-name"),
            shareStatus: rootDocument.getElementById("file-share-share-status"),
            shareVisibility: rootDocument.getElementById("file-share-share-visibility"),
            shareExpiryField: rootDocument.getElementById("file-share-expiry-field"),
            shareExpiry: rootDocument.getElementById("file-share-share-expiry"),
            folderShareNote: rootDocument.getElementById("file-share-folder-note"),
            shareLinkWrap: rootDocument.getElementById("file-share-link-wrap"),
            shareLink: rootDocument.getElementById("file-share-share-link"),
            copyShareButton: rootDocument.getElementById("file-share-copy-link"),
            shareError: rootDocument.getElementById("file-share-share-error"),
            confirmModal: rootDocument.getElementById("file-share-confirm-modal"),
            confirmForm: rootDocument.getElementById("file-share-confirm-form"),
            confirmTitle: rootDocument.getElementById("file-share-confirm-title"),
            confirmMessage: rootDocument.getElementById("file-share-confirm-message"),
            confirmField: rootDocument.getElementById("file-share-confirm-field"),
            confirmFieldLabel: rootDocument.getElementById("file-share-confirm-field-label"),
            confirmInput: rootDocument.getElementById("file-share-confirm-input"),
            confirmError: rootDocument.getElementById("file-share-confirm-error"),
            confirmSubmit: rootDocument.getElementById("file-share-confirm-submit"),
            selectionBar: rootDocument.getElementById("file-share-selection-bar"),
            selectionCount: rootDocument.getElementById("file-share-selection-count"),
            selectionClear: rootDocument.getElementById("file-share-selection-clear"),
            bulkMove: rootDocument.getElementById("file-share-bulk-move"),
            bulkDownload: rootDocument.getElementById("file-share-bulk-download"),
            bulkDelete: rootDocument.getElementById("file-share-bulk-delete"),
        };
    }

    function hasFiles(event) {
        return Array.from(event.dataTransfer?.types || []).includes("Files");
    }

    function isInteractiveTarget(target) {
        return Boolean(target.closest("button, a, input, select, textarea, label"));
    }

    function fileIcon(file) {
        const mime = file.mimeType || "";
        if (mime.startsWith("image/")) return "image";
        if (mime.includes("pdf")) return "picture_as_pdf";
        if (mime.includes("zip") || mime.includes("compressed")) return "folder_zip";
        return "description";
    }

    function uploadFileMime(file) {
        return String(file?.type || file?.mimeType || "").toLowerCase();
    }

    function uploadFileExtension(file) {
        const name = String(file?.name || "");
        const dot = name.lastIndexOf(".");
        if (dot > 0 && dot < name.length - 1) {
            return name.slice(dot + 1).toLowerCase();
        }
        return "";
    }

    function fileExtensionLabel(file) {
        const mime = uploadFileMime(file);
        const extension = uploadFileExtension(file);
        if (mime.includes("pdf") || extension === "pdf") return "PDF";
        if (mime.startsWith("image/") || ["jpg", "jpeg", "png", "gif", "webp", "svg"].includes(extension)) {
            return (extension || "IMG").slice(0, 4).toUpperCase();
        }
        if (mime.startsWith("video/") || ["mp4", "mov", "webm", "mkv"].includes(extension)) {
            return (extension || "VID").slice(0, 4).toUpperCase();
        }
        if (mime.includes("zip") || mime.includes("compressed") || ["zip", "rar", "7z", "tar", "gz"].includes(extension)) {
            return (extension || "ZIP").slice(0, 4).toUpperCase();
        }
        if (extension) return extension.slice(0, 4).toUpperCase();
        return "FILE";
    }

    function fileExtensionBadgeTone(file) {
        const mime = uploadFileMime(file);
        const extension = uploadFileExtension(file);
        if (mime.includes("pdf") || extension === "pdf") return "pdf";
        if (mime.startsWith("image/") || ["jpg", "jpeg", "png", "gif", "webp", "svg"].includes(extension)) return "image";
        if (mime.startsWith("video/") || ["mp4", "mov", "webm", "mkv"].includes(extension)) return "video";
        if (mime.includes("zip") || mime.includes("compressed") || ["zip", "rar", "7z", "tar", "gz"].includes(extension)) return "archive";
        return "default";
    }

    function fileIconForUpload(file) {
        const mime = uploadFileMime(file);
        const extension = uploadFileExtension(file);
        if (mime.startsWith("image/") || ["jpg", "jpeg", "png", "gif", "webp", "svg"].includes(extension)) return "image";
        if (mime.includes("pdf") || extension === "pdf") return "picture_as_pdf";
        if (mime.startsWith("video/") || ["mp4", "mov", "webm", "mkv"].includes(extension)) return "movie";
        if (mime.includes("zip") || mime.includes("compressed") || ["zip", "rar", "7z", "tar", "gz"].includes(extension)) return "folder_zip";
        return "description";
    }

    function formatFolderStatus(folder) {
        return `${folder.isPublic ? "Shared" : "Private"} / No folder expiry`;
    }

    function formatFileStatus(file) {
        return `${file.isPublic ? "Shared" : "Private"} / ${formatExpiry(file.expiresAt)}`;
    }

    function formatFolderCounts(folder) {
        const folders = Number(folder.folderCount) || 0;
        const files = Number(folder.fileCount) || 0;
        if (!folders && !files) return "Empty folder";
        return `${formatCount(folders, "folder")} / ${formatCount(files, "file")}`;
    }

    function formatCount(count, label) {
        const number = Number(count) || 0;
        return `${number.toLocaleString()} ${label}${number === 1 ? "" : "s"}`;
    }

    function formatBytes(bytes) {
        const number = Number(bytes) || 0;
        if (number < 1024) return `${number} B`;
        const units = ["KB", "MB", "GB"];
        let value = number / 1024;
        for (const unit of units) {
            if (value < 1024 || unit === units[units.length - 1]) {
                return `${value.toFixed(value >= 10 ? 1 : 2)} ${unit}`;
            }
            value /= 1024;
        }
        return `${number} B`;
    }

    function formatDateTime(value) {
        if (!value) return "Unknown";
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return "Unknown";
        return date.toLocaleString([], {
            month: "short",
            day: "numeric",
            year: "numeric",
            hour: "numeric",
            minute: "2-digit",
        });
    }

    function formatExpiry(value) {
        if (!value) return "No expiry";
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return "Expiry unknown";
        return `Expires ${date.toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" })}`;
    }

    function expiryOptionForDate(value, config = {}) {
        const { allowedExpiry, defaultExpiry } = normalizeExpiryConfig(config);
        const fallback = String(defaultExpiry);
        if (!value) return fallback;
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return fallback;
        const msPerDay = 24 * 60 * 60 * 1000;
        const daysRemaining = Math.max(1, Math.ceil((date.getTime() - Date.now()) / msPerDay));
        if (allowedExpiry.includes(daysRemaining)) return String(daysRemaining);
        const closest = allowedExpiry.reduce((best, days) => (
            Math.abs(days - daysRemaining) < Math.abs(best - daysRemaining) ? days : best
        ), Number(defaultExpiry) || allowedExpiry[0]);
        return String(closest);
    }

    function formatRelative(value) {
        if (!value) return "";
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return "";
        const diffSeconds = Math.max(0, Math.floor((Date.now() - date.getTime()) / 1000));
        if (diffSeconds < 60) return "less than a minute ago";
        const diffMinutes = Math.floor(diffSeconds / 60);
        if (diffMinutes < 60) return `${diffMinutes} minute${diffMinutes === 1 ? "" : "s"} ago`;
        const diffHours = Math.floor(diffMinutes / 60);
        if (diffHours < 24) return `${diffHours} hour${diffHours === 1 ? "" : "s"} ago`;
        const diffDays = Math.floor(diffHours / 24);
        if (diffDays < 30) return `${diffDays} day${diffDays === 1 ? "" : "s"} ago`;
        return formatDateTime(value);
    }

    function firstUploadError(payload) {
        return payload?.errors?.[0]?.error || "";
    }

    function parseUploadResponse(xhr) {
        if (!xhr) return null;
        const contentType = xhr.getResponseHeader?.("Content-Type") || "";
        if (contentType.includes("application/json") && xhr.response && typeof xhr.response === "object") {
            return xhr.response;
        }
        const raw = xhr.responseText;
        if (!raw) return null;
        try {
            return JSON.parse(raw);
        } catch (_error) {
            return null;
        }
    }

    function uploadErrorMessage(xhr, payload) {
        if (payload?.error) return payload.error;
        const firstError = firstUploadError(payload);
        if (firstError) return firstError;
        const status = xhr?.status || 0;
        if (status === 413) return "File is too large for the server upload limit.";
        if (status === 401 || status === 403) return "Session expired. Please sign in again.";
        if (status === 502 || status === 504) return "Upload timed out. Try again or use a smaller file.";
        if (status === 0) return "Network error during upload. Check your connection.";
        if (status) return `Upload failed (HTTP ${status}).`;
        return "Upload failed.";
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
        element.textContent = "";
        element.hidden = true;
        if (field) {
            window.APStudyFormField?.clearInvalid?.(field);
        }
    }

    function setButtonBusy(button, busy) {
        if (!button) return;
        button.disabled = busy;
        button.classList.toggle("is-busy", busy);
    }

    async function apiJson(url, options = {}) {
        return window.APStudyHttp.fetchJson(url, {
            ...options,
            pendingLabel: options.pendingLabel || "files-save",
        });
    }

    function flattenFolders(allFolders) {
        const childrenByParent = new Map();
        allFolders.forEach((folder) => {
            const parent = normalizeFolderId(folder.parentFolderId);
            if (!childrenByParent.has(parent)) childrenByParent.set(parent, []);
            childrenByParent.get(parent).push(folder);
        });
        const flattened = [];
        const visit = (parentId, depth) => {
            const children = childrenByParent.get(parentId) || [];
            children.forEach((folder) => {
                flattened.push({ ...folder, depth });
                visit(folder.id, depth + 1);
            });
        };
        visit(null, 0);
        return flattened;
    }

    function isDescendantFolder(foldersById, folderId, possibleDescendantId) {
        let currentId = normalizeFolderId(possibleDescendantId);
        const targetId = normalizeFolderId(folderId);
        const seen = new Set();
        while (currentId && !seen.has(currentId)) {
            if (currentId === targetId) return true;
            seen.add(currentId);
            currentId = normalizeFolderId(foldersById.get(currentId)?.parentFolderId);
        }
        return false;
    }

    function getFolderName(folderId, currentFolder, allFolders) {
        const normalized = normalizeFolderId(folderId);
        if (!normalized) return "My Files";
        if (currentFolder?.id === normalized) return currentFolder.name;
        return allFolders.find((folder) => folder.id === normalized)?.name || "Selected folder";
    }

    async function copyText(text) {
        if (!text) return;
        if (navigator.clipboard?.writeText) {
            await navigator.clipboard.writeText(text);
            return;
        }
        const input = document.createElement("input");
        input.value = text;
        document.body.appendChild(input);
        input.select();
        document.execCommand("copy");
        input.remove();
    }

    function downloadBlob(blob, filename) {
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url;
        link.download = filename;
        document.body.appendChild(link);
        link.click();
        link.remove();
        URL.revokeObjectURL(url);
    }

    function filenameFromDisposition(header) {
        if (!header) return "";
        const utfMatch = header.match(/filename\*=UTF-8''([^;]+)/i);
        if (utfMatch) return decodeURIComponent(utfMatch[1].replace(/"/g, ""));
        const asciiMatch = header.match(/filename="?([^";]+)"?/i);
        return asciiMatch ? asciiMatch[1] : "";
    }

    function cssEscape(value) {
        if (window.CSS?.escape) return window.CSS.escape(value);
        return String(value).replace(/["\\]/g, "\\$&");
    }

    window.APStudyFilesUtils = {
        normalizeExpiryConfig,
        getElements,
        normalizeFolderId,
        hasFiles,
        isInteractiveTarget,
        fileIcon,
        fileExtensionLabel,
        fileExtensionBadgeTone,
        fileIconForUpload,
        formatFolderStatus,
        formatFileStatus,
        formatFolderCounts,
        formatCount,
        formatBytes,
        formatDateTime,
        formatExpiry,
        expiryOptionForDate,
        formatRelative,
        firstUploadError,
        parseUploadResponse,
        uploadErrorMessage,
        showFormError,
        clearFormError,
        setButtonBusy,
        apiJson,
        flattenFolders,
        isDescendantFolder,
        getFolderName,
        copyText,
        downloadBlob,
        filenameFromDisposition,
        cssEscape,
        escapeHtml,
    };
})();
