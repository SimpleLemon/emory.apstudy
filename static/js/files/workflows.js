(function registerFilesWorkflows(global) {
    function createFilesWorkflows({
        state,
        els,
        constants,
        callbacks,
    }) {
        const {
            allowedExpiry,
            defaultExpiry,
            maxFileSizeBytes,
            maxFileSizeLabel,
            maxUploadFiles,
        } = constants;
        const {
            apiJson,
            clearFormError,
            closeModal,
            copyText,
            cssEscape,
            downloadBlob,
            filenameFromDisposition,
            firstUploadError,
            formatCount,
            formatExpiry,
            getFolderName,
            loadFolder,
            normalizeFolderId,
            notify,
            parseUploadResponse,
            renderManager,
            setButtonBusy,
            shareExpiryOptionsHtml,
            showAlert,
            showFormError,
            uploadErrorMessage,
            uploadItemHtml,
            expiryOptionForDate,
            sendUpload,
        } = callbacks;

        function openUploadModal(folderId = state.currentFolderId, files = []) {
            state.uploadTargetFolderId = normalizeFolderId(folderId);
            state.uploadTargetName = getFolderName(
                state.uploadTargetFolderId,
                state.currentFolder,
                state.allFolders,
            );
            state.uploadItems = [];
            if (els.uploadTarget) els.uploadTarget.textContent = state.uploadTargetName;
            clearFormError(els.uploadError);
            resetProgress();
            renderUploadItems();
            closeModal.open(els.uploadModal, els.dropzone);
            if (files.length) addUploadFiles(files);
        }

        function addUploadFiles(files) {
            clearFormError(els.uploadError);
            const incoming = Array.from(files || []).filter(Boolean);
            if (!incoming.length) return;
            const remaining = maxUploadFiles - state.uploadItems.length;
            if (remaining <= 0) {
                notify(`You can upload up to ${maxUploadFiles} files at once.`, "error", { modalError: els.uploadError });
                return;
            }
            const accepted = incoming.slice(0, remaining);
            if (incoming.length > remaining) {
                notify(`Only ${remaining} more file${remaining === 1 ? "" : "s"} can be added.`, "warning", { modalError: els.uploadError });
            }
            accepted.forEach((file) => {
                state.uploadItems.push({
                    id: `upload-${Date.now()}-${Math.random().toString(16).slice(2)}`,
                    file,
                    name: file.name,
                    visibility: "private",
                    expiryDays: String(defaultExpiry),
                });
            });
            renderUploadItems();
        }

        function renderUploadItems() {
            if (!els.selectedList) return;
            if (!state.uploadItems.length) {
                els.selectedList.innerHTML = "";
                return;
            }
            els.selectedList.innerHTML = state.uploadItems
                .map((item) => uploadItemHtml(item, allowedExpiry))
                .join("");

            els.selectedList.querySelectorAll(".files-selected-item").forEach((row) => {
                const item = state.uploadItems.find((candidate) => candidate.id === row.dataset.uploadId);
                if (!item) return;
                row.querySelector("[data-upload-name]")?.addEventListener("input", (event) => {
                    item.name = event.target.value;
                });
                row.querySelector("[data-upload-visibility]")?.addEventListener("change", (event) => {
                    item.visibility = event.target.value;
                });
                row.querySelector("[data-upload-expiry]")?.addEventListener("change", (event) => {
                    item.expiryDays = event.target.value;
                });
                row.querySelector("[data-upload-remove]")?.addEventListener("click", () => {
                    const index = state.uploadItems.findIndex((candidate) => candidate.id === item.id);
                    if (index < 0) return;
                    const [removedItem] = state.uploadItems.splice(index, 1);
                    renderUploadItems();
                    window.APStudyUndo?.stage?.({
                        message: `${removedItem.name || removedItem.file?.name || "File"} removed from this upload.`,
                        restore: () => {
                            state.uploadItems.splice(Math.min(index, state.uploadItems.length), 0, removedItem);
                            renderUploadItems();
                        },
                    });
                });
            });
        }

        async function uploadSelectedFiles() {
            clearFormError(els.uploadError);
            if (!state.uploadItems.length) {
                notify("Select at least one file to upload.", "error", { modalError: els.uploadError });
                return;
            }
            for (const item of state.uploadItems) {
                if (!item.name.trim()) {
                    const row = els.selectedList?.querySelector(`[data-upload-id="${item.id}"]`);
                    const nameInput = row?.querySelector("[data-upload-name]");
                    notify("Filename cannot be empty.", "error", { modalError: els.uploadError, field: nameInput });
                    return;
                }
                if (item.file.size > maxFileSizeBytes) {
                    notify(`${item.file.name} exceeds the ${maxFileSizeLabel} limit.`, "error", { modalError: els.uploadError });
                    return;
                }
                if (item.file.size === 0) {
                    notify(`${item.file.name} is empty.`, "error", { modalError: els.uploadError });
                    return;
                }
            }

            const formData = new FormData();
            formData.append("folderId", state.uploadTargetFolderId || "root");
            state.uploadItems.forEach((item) => {
                formData.append("file", item.file);
                formData.append("filename", item.name.trim());
                formData.append("visibility", item.visibility);
                formData.append("expiryDays", item.expiryDays);
            });

            setButtonBusy(els.uploadButton, true);
            showProgress(0);
            let xhr = null;
            let finishUploadMutation = null;
            let cleanedUp = false;
            const cleanupUpload = () => {
                if (cleanedUp) return;
                cleanedUp = true;
                setButtonBusy(els.uploadButton, false);
                resetProgress();
                finishUploadMutation?.();
            };
            const reportUploadError = (request, error = null) => {
                const message = error?.message || uploadErrorMessage(request, parseUploadResponse(request));
                notify(message || "Upload failed. Try again in a moment.", "error", { modalError: els.uploadError });
            };
            const handleLoad = async (eventOrRequest) => {
                const request = eventOrRequest?.currentTarget || eventOrRequest || xhr;
                try {
                    const payload = parseUploadResponse(request) || {};
                    if (request?.status < 200 || request?.status >= 300) {
                        const message = uploadErrorMessage(request, payload);
                        notify(message, "error", { modalError: els.uploadError });
                        return;
                    }
                    closeModal.close(els.uploadModal);
                    await loadFolder(state.currentFolderId);
                    if (payload.errors?.length) {
                        showAlert(firstUploadError(payload) || "Some files could not be uploaded.", "error");
                    } else {
                        showAlert("Upload complete.");
                    }
                } catch (error) {
                    reportUploadError(request, error);
                } finally {
                    cleanupUpload();
                }
            };
            const handleError = (errorOrEvent) => {
                const request = errorOrEvent?.xhr || errorOrEvent?.request
                    || errorOrEvent?.currentTarget || xhr;
                try {
                    reportUploadError(request, errorOrEvent instanceof Error ? errorOrEvent : null);
                } finally {
                    cleanupUpload();
                }
            };
            const handleAbort = () => cleanupUpload();
            const onProgress = (event) => {
                if (!event.lengthComputable) return;
                showProgress(Math.round((event.loaded / event.total) * 100));
            };
            try {
                if (typeof global.APStudyHttp?.uploadXhr === "function") {
                    const request = global.APStudyHttp.uploadXhr("/api/files/upload", {
                        method: "POST",
                        body: formData,
                        responseType: "json",
                        onProgress,
                        pendingLabel: "file-upload",
                    });
                    Promise.resolve(request).then(handleLoad, handleError);
                    return;
                }

                // The injected sender exists only for isolated workflow tests. In the
                // application, refusing to send is safer than bypassing shared CSRF.
                if (typeof sendUpload !== "function") {
                    throw new Error("Upload service is unavailable. Refresh the page and try again.");
                }
                finishUploadMutation = global.APStudyPendingMutations?.begin("file-upload");
                xhr = sendUpload({
                    url: "/api/files/upload",
                    method: "POST",
                    body: formData,
                    responseType: "json",
                    onProgress,
                    onLoad: handleLoad,
                    onError: handleError,
                    onAbort: handleAbort,
                });
                if (!xhr) throw new Error("Upload sender did not return a request");
            } catch (error) {
                reportUploadError(xhr, error);
                cleanupUpload();
            }
        }

        function openShareModal(type, item) {
            state.shareContext = { type, item };
            clearFormError(els.shareError);
            renderShareModal();
            closeModal.open(els.shareModal, els.shareVisibility);
        }

        function renderShareModal() {
            const context = state.shareContext;
            if (!context?.item) return;
            const { type, item } = context;
            const isFile = type === "file";
            const name = isFile ? item.filename : item.name;
            if (els.shareTitle) els.shareTitle.textContent = isFile ? "Share/Expiry" : "Share";
            if (els.shareSubtitle) {
                els.shareSubtitle.textContent = isFile
                    ? "Manage public link access and expiration for this file."
                    : "Manage public link access for this folder.";
            }
            if (els.shareName) els.shareName.textContent = name || "Selected item";
            if (els.shareStatus) {
                els.shareStatus.textContent = item.isPublic
                    ? `Public link active${isFile ? ` / ${formatExpiry(item.expiresAt)}` : ""}`
                    : `${isFile ? "File" : "Folder"} is private`;
            }
            if (els.shareVisibility) {
                els.shareVisibility.checked = Boolean(item.isPublic);
                els.shareVisibility.setAttribute("aria-label", item.isPublic ? "Make private" : "Create public link");
            }
            if (els.shareExpiryField) els.shareExpiryField.hidden = !isFile;
            if (els.folderShareNote) els.folderShareNote.hidden = isFile;
            if (els.shareExpiry && isFile) {
                const selected = expiryOptionForDate(item.expiresAt, {
                    allowedExpiryOptions: allowedExpiry,
                    defaultExpiryDays: defaultExpiry,
                });
                els.shareExpiry.innerHTML = shareExpiryOptionsHtml(allowedExpiry, selected);
            }
            const hasLink = Boolean(item.isPublic && item.shareUrl);
            if (els.shareLinkWrap) els.shareLinkWrap.hidden = !hasLink;
            if (els.shareLink) els.shareLink.value = hasLink ? item.shareUrl : "";
        }

        async function saveShareVisibility() {
            const context = state.shareContext;
            if (!context?.item || !els.shareVisibility) return;
            const visibility = els.shareVisibility.checked ? "public" : "private";
            const endpoint = context.type === "file"
                ? `/api/files/my/${encodeURIComponent(context.item.id)}/visibility`
                : `/api/files/folders/${encodeURIComponent(context.item.id)}/visibility`;
            setShareBusy(true);
            clearFormError(els.shareError);
            try {
                const updated = await apiJson(endpoint, {
                    method: "POST",
                    body: JSON.stringify({ visibility }),
                });
                updateSharedItem(context.type, updated);
                showAlert(visibility === "public" ? "Public link enabled." : "Link disabled.");
            } catch (error) {
                notify(error.message || "Try again in a moment.", "error", { modalError: els.shareError, title: "Couldn’t update sharing" });
                renderShareModal();
            } finally {
                setShareBusy(false);
            }
        }

        async function saveShareExpiry() {
            const context = state.shareContext;
            if (context?.type !== "file" || !context.item || !els.shareExpiry) return;
            const expiryDays = els.shareExpiry.value;
            setShareBusy(true);
            clearFormError(els.shareError);
            try {
                const updated = await apiJson(`/api/files/my/${encodeURIComponent(context.item.id)}`, {
                    method: "PATCH",
                    body: JSON.stringify({ expiryDays }),
                });
                updateSharedItem("file", updated);
                showAlert("Expiration updated.");
            } catch (error) {
                notify(error.message || "Try again in a moment.", "error", { modalError: els.shareError, title: "Couldn’t update expiration" });
                renderShareModal();
            } finally {
                setShareBusy(false);
            }
        }

        async function copyCurrentShareLink() {
            const link = state.shareContext?.item?.shareUrl;
            if (!link) return;
            await copyShareLink(link);
        }

        function updateSharedItem(type, updated) {
            if (!updated?.id || !state.shareContext) return;
            if (type === "file") {
                state.files = state.files.map((file) => file.id === updated.id ? { ...file, ...updated } : file);
            } else {
                state.folders = state.folders.map((folder) => folder.id === updated.id ? { ...folder, ...updated } : folder);
                state.allFolders = state.allFolders.map((folder) => folder.id === updated.id ? { ...folder, ...updated } : folder);
            }
            state.shareContext.item = type === "file"
                ? state.files.find((file) => file.id === updated.id) || updated
                : state.folders.find((folder) => folder.id === updated.id)
                    || state.allFolders.find((folder) => folder.id === updated.id)
                    || updated;
            renderManager();
            renderShareModal();
        }

        function setShareBusy(busy) {
            if (els.shareVisibility) els.shareVisibility.disabled = busy;
            if (els.shareExpiry) els.shareExpiry.disabled = busy;
            if (els.copyShareButton) els.copyShareButton.disabled = busy || !state.shareContext?.item?.shareUrl;
        }

        async function setFolderVisibility(folder, visibility, copyAfter = false) {
            try {
                const updated = await apiJson(`/api/files/folders/${encodeURIComponent(folder.id)}/visibility`, {
                    method: "POST",
                    body: JSON.stringify({ visibility }),
                });
                if (copyAfter && updated.shareUrl) {
                    await copyText(updated.shareUrl);
                    await loadFolder(state.currentFolderId);
                    showAlert("Folder link copied.");
                } else {
                    await loadFolder(state.currentFolderId);
                    showAlert(visibility === "public" ? "Folder link created." : "Folder made private.");
                }
            } catch (error) {
                showAlert(error.message || "Try again in a moment.", "error", { title: "Couldn’t update folder sharing" });
            }
        }

        async function copyShareLink(url) {
            try {
                await copyText(url);
                showAlert("Link copied.");
            } catch (error) {
                showAlert("Check your browser permissions and try again.", "error", { title: "Couldn’t copy link" });
            }
        }

        function downloadFile(fileId) {
            global.location.href = `/api/files/my/${encodeURIComponent(fileId)}/download`;
        }

        async function downloadSelectedFiles() {
            const fileIds = Array.from(state.selectedFileIds);
            if (!fileIds.length) {
                showAlert("Select at least one file to download.", "error");
                return;
            }
            if (fileIds.length === 1) {
                downloadFile(fileIds[0]);
                return;
            }
            try {
                setButtonBusy(els.bulkDownload, true);
                const response = await fetch("/api/files/bulk-download.zip", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ fileIds }),
                });
                if (!response.ok) {
                    let message = "Unable to download selected files.";
                    try {
                        const payload = await response.json();
                        message = payload.error || message;
                    } catch (_error) {
                        message = response.statusText || message;
                    }
                    throw new Error(message);
                }
                const blob = await response.blob();
                downloadBlob(blob, filenameFromDisposition(response.headers.get("Content-Disposition")) || "file-share-selected.zip");
            } catch (error) {
                showAlert(error.message || "Try again in a moment.", "error", { title: "Couldn’t download files" });
            } finally {
                setButtonBusy(els.bulkDownload, false);
            }
        }

        function setSelection(type, id, selected) {
            if (!id) return;
            const target = type === "file" ? state.selectedFileIds : state.selectedFolderIds;
            if (selected) target.add(id);
            else target.delete(id);
            updateRowSelection(type, id, selected);
            updateSelectionBar();
        }

        function updateRowSelection(type, id, selected) {
            const selector = type === "file" ? `[data-file-id="${cssEscape(id)}"]` : `[data-folder-id="${cssEscape(id)}"]`;
            document.querySelector(selector)?.classList.toggle("is-selected", selected);
        }

        function clearSelection() {
            state.selectedFileIds.clear();
            state.selectedFolderIds.clear();
            document.querySelectorAll("[data-select-file], [data-select-folder]").forEach((checkbox) => {
                checkbox.checked = false;
            });
            document.querySelectorAll(".files-row.is-selected").forEach((row) => row.classList.remove("is-selected"));
            updateSelectionBar();
        }

        function updateSelectionBar() {
            const total = selectedTotal();
            if (els.selectionBar) els.selectionBar.hidden = total === 0;
            if (els.selectionCount) els.selectionCount.textContent = `${formatCount(total, "item")} selected`;
            if (els.bulkMove) els.bulkMove.disabled = total === 0;
            if (els.bulkDelete) els.bulkDelete.disabled = total === 0;
            if (els.bulkDownload) {
                els.bulkDownload.disabled = state.selectedFileIds.size === 0;
                els.bulkDownload.title = state.selectedFileIds.size === 0 ? "Select at least one file to download" : "";
            }
        }

        function selectedTotal() {
            return state.selectedFileIds.size + state.selectedFolderIds.size;
        }

        function showProgress(percent) {
            if (els.progressWrap) els.progressWrap.hidden = false;
            if (els.progressBar) els.progressBar.style.transform = `scaleX(${Math.max(0, Math.min(100, percent)) / 100})`;
        }

        function resetProgress() {
            if (els.progressWrap) els.progressWrap.hidden = true;
            if (els.progressBar) els.progressBar.style.transform = "scaleX(0)";
        }

        return {
            addUploadFiles,
            clearSelection,
            copyCurrentShareLink,
            downloadFile,
            downloadSelectedFiles,
            openShareModal,
            openUploadModal,
            saveShareExpiry,
            saveShareVisibility,
            selectedTotal,
            setFolderVisibility,
            setSelection,
            uploadSelectedFiles,
            updateSelectionBar,
        };
    }

    global.APStudyFilesWorkflows = {
        createFilesWorkflows,
    };
})(window);
