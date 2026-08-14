(function () {
    function createCalendarShare({
        root = document,
        lifecycle = null,
        dataAdapter = null,
        state,
        constants,
        escapeHtml,
        getCalendarLabel,
        getCalendarLabelFromData,
        trackCalendarMutation,
    }) {
        const { calendarShareCloseMs, simulatedCalendarName } = constants;
        const doc = root.ownerDocument || root;
        const view = doc.defaultView || window;
        const mountNode = root.nodeType === 9 ? (root.body || root.documentElement) : root;

        function requestController() {
            return lifecycle?.trackAbortController?.() || new AbortController();
        }

        async function requestShare(path, options = {}) {
            const controller = requestController();
            try {
                if (dataAdapter?.saveShare) {
                    return await dataAdapter.saveShare({ ...options, path, signal: controller.signal });
                }
                const response = await fetch(path, { ...options, signal: controller.signal });
                return { response };
            } finally {
                lifecycle?.releaseAbortController?.(controller);
            }
        }

        function closeCalendarShareModal(immediate = false) {
            if (state.ui.shareModalEl) {
                const modal = state.ui.shareModalEl;
                if (immediate) {
                    modal.remove();
                } else {
                    modal.classList.add("is-closing");
                    const schedule = lifecycle?.setTimeout || view.setTimeout.bind(view);
                    schedule(() => {
                        modal.remove();
                    }, calendarShareCloseMs);
                }
                state.ui.shareModalEl = null;
            }
            state.shares.editingId = null;
            state.shares.error = "";
        }

        async function loadCalendarShares(force = false) {
            if (state.public.readOnly || lifecycle?.isDisposed?.()) return;
            if (state.shares.loading || (state.shares.loaded && !force)) return;
            state.shares.loading = true;
            state.shares.error = "";
            renderCalendarShareModal();
            try {
                const controller = requestController();
                try {
                    const result = dataAdapter?.loadShares
                        ? await dataAdapter.loadShares({ signal: controller.signal })
                        : { response: await fetch("/api/calendar/shares", { signal: controller.signal }) };
                    const res = result.response || result;
                    const payload = result.payload || await res.json().catch(() => ({}));
                    if (!res.ok) throw new Error(payload.error || "Unable to load share links.");
                    state.shares.items = Array.isArray(payload.shares) ? payload.shares : [];
                    state.shares.loaded = true;
                } finally {
                    lifecycle?.releaseAbortController?.(controller);
                }
            } catch (err) {
                state.shares.error = err.message || "Unable to load share links.";
            } finally {
                state.shares.loading = false;
                renderCalendarShareModal();
            }
        }

        function openCalendarShareModal() {
            if (state.public.readOnly) return;
            closeCalendarShareModal(true);
            const modal = doc.createElement("div");
            modal.className = "calendar-info-modal calendar-share-modal";
            const listen = lifecycle?.addEventListener
                ? lifecycle.addEventListener.bind(lifecycle)
                : (target, type, handler) => target.addEventListener(type, handler);
            listen(modal, "click", onCalendarShareModalClick);
            listen(modal, "change", onCalendarShareModalChange);
            listen(modal, "submit", onCalendarShareModalSubmit);
            mountNode.appendChild(modal);
            lifecycle?.trackNode?.(modal);
            state.ui.shareModalEl = modal;
            renderCalendarShareModal();
            void loadCalendarShares();
        }

        function renderCalendarShareModal() {
            const modal = state.ui.shareModalEl;
            if (!modal) return;
            const editingShare = state.shares.items.find((share) => share.id === state.shares.editingId) || null;
            const seed = editingShare || {
                includeAllCalendars: true,
                calendarIds: [],
                dateScope: "all",
                fixedStart: "",
                fixedEnd: "",
                rollingDays: 30,
            };
            const shareableCalendars = Object.entries(state.calendars)
                .filter(([name]) => name !== simulatedCalendarName)
                .sort(([, a], [, b]) => getCalendarLabelFromData(a).localeCompare(getCalendarLabelFromData(b)));
            const selectedIds = new Set(seed.calendarIds || []);
            const includeAll = seed.includeAllCalendars !== false;
            const editingCode = editingShare?.shareCode || "";
            const modalTitle = editingShare ? "Edit Shared Link" : "Share Calendar";
            const modalSubtitle = editingShare
                ? `Updating settings for share link ${editingCode || "selected link"}.`
                : "Create reusable read-only links for selected calendars and dates.";
            const editingBanner = editingShare ? `
                <div class="calendar-share-editing-banner" role="status">
                    <span class="material-symbols-outlined calendar-share-editing-icon" aria-hidden="true">edit</span>
                    <span>
                        <strong>Editing shared link</strong>
                        <span>${escapeHtml(editingCode || "Selected link")} · ${escapeHtml(editingShare.scopeLabel || "All shared dates")}</span>
                    </span>
                </div>
            ` : "";
            const calendarChoices = shareableCalendars.length
                ? shareableCalendars.map(([calendarName, data]) => `
                    <label class="calendar-share-calendar-choice">
                        <input type="checkbox" name="calendar_ids" value="${escapeHtml(calendarName)}" ${includeAll || selectedIds.has(calendarName) ? "checked" : ""} ${includeAll ? "disabled" : ""}>
                        <span class="calendar-share-calendar-dot" style="background:${data.color};"></span>
                        <span>${escapeHtml(getCalendarLabel(calendarName))}</span>
                    </label>
                `).join("")
                : '<p class="calendar-info-note">Load or add a calendar before limiting by calendar.</p>';
            const sharesList = state.shares.loading
                ? `<div class="calendar-share-empty">${window.APStudyLoader.html("Loading share links...", { sizePx: 30, textToneClass: "text-on-surface" })}</div>`
                : state.shares.items.length
                    ? state.shares.items.map((share) => buildCalendarShareRowHtml(share, editingShare?.id)).join("")
                    : '<div class="calendar-share-empty">No share links yet.</div>';
            modal.innerHTML = `
                <div class="calendar-info-dialog calendar-share-dialog" role="dialog" aria-modal="true" aria-labelledby="calendar-share-title">
                    <div class="calendar-info-header">
                        <div class="calendar-info-heading">
                            <h3 id="calendar-share-title" class="calendar-info-title">${escapeHtml(modalTitle)}</h3>
                            <p class="calendar-info-subtitle">${escapeHtml(modalSubtitle)}</p>
                        </div>
                        <button type="button" class="js-share-close calendar-info-close" aria-label="Close calendar sharing">
                            <span class="material-symbols-outlined calendar-info-close-icon" aria-hidden="true">close</span>
                        </button>
                    </div>
                    <form id="calendar-share-form">
                        <div class="calendar-info-body">
                            ${editingBanner}
                            <div class="calendar-share-options">
                                <label class="calendar-share-radio">
                                    <input type="radio" name="include_scope" value="all" ${includeAll ? "checked" : ""}>
                                    <span>All calendars</span>
                                </label>
                                <label class="calendar-share-radio">
                                    <input type="radio" name="include_scope" value="selected" ${includeAll ? "" : "checked"}>
                                    <span>Selected calendars</span>
                                </label>
                            </div>
                            <div class="calendar-share-calendar-grid ${includeAll ? "is-disabled" : ""}" aria-disabled="${includeAll ? "true" : "false"}">
                                ${calendarChoices}
                            </div>
                            <label class="calendar-info-field">
                                <span class="calendar-info-label">Date range</span>
                                <select name="date_scope" class="calendar-info-input">
                                    <option value="all" ${seed.dateScope === "all" ? "selected" : ""}>All shared dates</option>
                                    <option value="fixed" ${seed.dateScope === "fixed" ? "selected" : ""}>Fixed date range</option>
                                    <option value="rolling" ${seed.dateScope === "rolling" ? "selected" : ""}>Rolling window</option>
                                </select>
                            </label>
                            <div class="calendar-share-fixed-fields">
                                <label class="calendar-info-field">
                                    <span class="calendar-info-label">Start</span>
                                    <input name="fixed_start" type="date" class="calendar-info-input" value="${escapeHtml(seed.fixedStart || "")}">
                                </label>
                                <label class="calendar-info-field">
                                    <span class="calendar-info-label">End</span>
                                    <input name="fixed_end" type="date" class="calendar-info-input" value="${escapeHtml(seed.fixedEnd || "")}">
                                </label>
                            </div>
                            <label class="calendar-info-field calendar-share-rolling-field">
                                <span class="calendar-info-label">Rolling days</span>
                                <input name="rolling_days" type="number" min="1" max="366" step="1" class="calendar-info-input" value="${escapeHtml(seed.rollingDays || 30)}">
                            </label>
                            ${state.shares.error ? `<p class="calendar-info-error">${escapeHtml(state.shares.error)}</p>` : ""}
                            ${state.shares.notice ? `<p class="calendar-share-notice">${escapeHtml(state.shares.notice)}</p>` : ""}
                            <section class="calendar-share-list" aria-label="Calendar share links">
                                <div class="calendar-share-list-head">
                                    <span>Links</span>
                                    ${editingShare ? '<button type="button" class="js-share-new calendar-info-button calendar-info-button-secondary">New Link</button>' : ""}
                                </div>
                                ${sharesList}
                            </section>
                        </div>
                        <div class="calendar-info-footer">
                            <button type="submit" class="calendar-info-button calendar-info-button-primary" ${state.shares.saving ? "disabled" : ""}>
                                ${state.shares.saving ? "Saving..." : editingShare ? "Save Link" : "Create Link"}
                            </button>
                        </div>
                    </form>
                </div>
            `;
            syncCalendarShareModalFields();
        }

        function buildCalendarShareRowHtml(share, editingId = null) {
            const inactive = !share.isActive;
            const isEditing = share.id === editingId;
            const statusLabel = isEditing ? "Editing now" : inactive ? "Revoked link" : "Active link";
            return `
                <article class="calendar-share-row ${inactive ? "is-inactive" : ""} ${isEditing ? "is-editing" : ""}">
                    <div class="calendar-share-row-main">
                        <div class="calendar-share-row-title">
                            <span>${statusLabel}</span>
                            <span class="calendar-share-code">${escapeHtml(share.shareCode || "")}</span>
                        </div>
                        <div class="calendar-share-row-meta">${escapeHtml(share.scopeLabel || "All shared dates")}</div>
                        <input class="calendar-info-input calendar-share-url" readonly aria-label="Share link" value="${escapeHtml(share.shareUrl || "")}">
                    </div>
                    <div class="calendar-share-actions">
                        <button type="button" class="js-share-copy calendar-info-button calendar-info-button-secondary" data-share-id="${escapeHtml(share.id)}" ${inactive ? "disabled" : ""}>Copy</button>
                        <button type="button" class="js-share-edit calendar-info-button calendar-info-button-secondary" data-share-id="${escapeHtml(share.id)}" ${isEditing ? "disabled" : ""}>${isEditing ? "Editing" : "Edit"}</button>
                        <button type="button" class="js-share-regenerate calendar-info-button calendar-info-button-secondary" data-share-id="${escapeHtml(share.id)}">Regenerate</button>
                        ${inactive
                            ? `<button type="button" class="js-share-activate calendar-info-button calendar-info-button-primary" data-share-id="${escapeHtml(share.id)}">Reactivate</button>`
                            : `<button type="button" class="js-share-revoke calendar-info-button calendar-info-button-secondary" data-share-id="${escapeHtml(share.id)}">Revoke</button>`}
                    </div>
                </article>
            `;
        }

        function syncCalendarShareModalFields() {
            const modal = state.ui.shareModalEl;
            if (!modal) return;
            const form = modal.querySelector("#calendar-share-form");
            if (!form) return;
            const includeAll = form.include_scope?.value !== "selected";
            form.querySelectorAll("input[name='calendar_ids']").forEach((input) => {
                input.disabled = includeAll;
                if (includeAll) input.checked = true;
            });
            const calendarGrid = modal.querySelector(".calendar-share-calendar-grid");
            if (calendarGrid) {
                calendarGrid.classList.toggle("is-disabled", includeAll);
                calendarGrid.setAttribute("aria-disabled", includeAll ? "true" : "false");
            }
            const scope = form.date_scope?.value || "all";
            const fixedFields = modal.querySelector(".calendar-share-fixed-fields");
            const rollingField = modal.querySelector(".calendar-share-rolling-field");
            if (fixedFields) fixedFields.hidden = scope !== "fixed";
            if (rollingField) rollingField.hidden = scope !== "rolling";
        }

        function calendarShareFormPayload(form) {
            const includeAll = form.include_scope?.value !== "selected";
            return {
                includeAllCalendars: includeAll,
                calendarIds: includeAll
                    ? []
                    : Array.from(form.querySelectorAll("input[name='calendar_ids']:checked")).map((input) => input.value),
                dateScope: form.date_scope?.value || "all",
                fixedStart: form.fixed_start?.value || null,
                fixedEnd: form.fixed_end?.value || null,
                rollingDays: form.rolling_days?.value ? Number(form.rolling_days.value) : null,
            };
        }

        async function saveCalendarSharePayload(payload) {
            state.shares.saving = true;
            state.shares.error = "";
            state.shares.notice = "";
            const editingId = state.shares.editingId;
            renderCalendarShareModal();
            try {
                const result = await trackCalendarMutation(requestShare(
                    editingId ? `/api/calendar/shares/${encodeURIComponent(editingId)}` : "/api/calendar/shares",
                    {
                        method: editingId ? "PATCH" : "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify(payload),
                    }
                ));
                const res = result.response || result;
                const response = result.payload || await res.json().catch(() => ({}));
                if (!res.ok) throw new Error(response.error || "Unable to save share link.");
                const share = response.share;
                if (share?.id) {
                    const index = state.shares.items.findIndex((item) => item.id === share.id);
                    if (index >= 0) state.shares.items.splice(index, 1, share);
                    else state.shares.items.unshift(share);
                }
                state.shares.editingId = null;
                state.shares.notice = editingId ? "Share link updated." : "Share link created.";
            } catch (err) {
                state.shares.error = err.message || "Unable to save share link.";
            } finally {
                state.shares.saving = false;
                renderCalendarShareModal();
            }
        }

        async function updateCalendarShare(shareId, path, options = {}) {
            state.shares.error = "";
            state.shares.notice = "";
            renderCalendarShareModal();
            try {
                const result = await trackCalendarMutation(requestShare(path, {
                    method: options.method || "POST",
                    headers: { "Content-Type": "application/json" },
                    body: options.body ? JSON.stringify(options.body) : undefined,
                }));
                const res = result.response || result;
                const response = result.payload || await res.json().catch(() => ({}));
                if (!res.ok) throw new Error(response.error || "Unable to update share link.");
                const share = response.share;
                if (share?.id) {
                    const index = state.shares.items.findIndex((item) => item.id === share.id);
                    if (index >= 0) state.shares.items.splice(index, 1, share);
                    else state.shares.items.unshift(share);
                }
                state.shares.notice = options.notice || "Share link updated.";
                return share || true;
            } catch (err) {
                state.shares.error = err.message || "Unable to update share link.";
                return null;
            } finally {
                renderCalendarShareModal();
            }
        }

        async function copyTextToClipboard(value) {
            if (!value) return false;
            if (view.navigator?.clipboard?.writeText) {
                await view.navigator.clipboard.writeText(value);
                return true;
            }
            const textarea = doc.createElement("textarea");
            textarea.value = value;
            textarea.setAttribute("readonly", "");
            textarea.style.position = "fixed";
            textarea.style.left = "-9999px";
            root.appendChild(textarea);
            textarea.select();
            const ok = doc.execCommand("copy");
            textarea.remove();
            return ok;
        }

        function onCalendarShareModalChange(event) {
            if (event.target?.name === "include_scope" || event.target?.name === "date_scope") {
                syncCalendarShareModalFields();
            }
        }

        async function onCalendarShareModalSubmit(event) {
            if (event.target?.id !== "calendar-share-form") return;
            event.preventDefault();
            await saveCalendarSharePayload(calendarShareFormPayload(event.target));
        }

        async function onCalendarShareModalClick(event) {
            if (event.target === state.ui.shareModalEl || event.target.closest(".js-share-close")) {
                closeCalendarShareModal();
                return;
            }
            const newBtn = event.target.closest(".js-share-new");
            if (newBtn) {
                state.shares.editingId = null;
                state.shares.error = "";
                state.shares.notice = "";
                renderCalendarShareModal();
                return;
            }
            const copyBtn = event.target.closest(".js-share-copy");
            if (copyBtn) {
                const share = state.shares.items.find((item) => item.id === copyBtn.getAttribute("data-share-id"));
                if (!share?.shareUrl) return;
                await copyTextToClipboard(share.shareUrl);
                state.shares.notice = "Share link copied.";
                renderCalendarShareModal();
                return;
            }
            const editBtn = event.target.closest(".js-share-edit");
            if (editBtn) {
                state.shares.editingId = editBtn.getAttribute("data-share-id");
                state.shares.error = "";
                state.shares.notice = "";
                renderCalendarShareModal();
                return;
            }
            const regenerateBtn = event.target.closest(".js-share-regenerate");
            if (regenerateBtn) {
                const shareId = regenerateBtn.getAttribute("data-share-id");
                await updateCalendarShare(shareId, `/api/calendar/shares/${encodeURIComponent(shareId)}/regenerate`, { notice: "Share link regenerated." });
                return;
            }
            const revokeBtn = event.target.closest(".js-share-revoke");
            if (revokeBtn) {
                const shareId = revokeBtn.getAttribute("data-share-id");
                const share = state.shares.items.find((item) => item.id === shareId);
                const revoked = await updateCalendarShare(shareId, `/api/calendar/shares/${encodeURIComponent(shareId)}`, { method: "DELETE", notice: "Share link revoked." });
                if (revoked) {
                    window.APStudyToast?.show?.({
                        message: "Share link revoked.",
                        type: "info",
                        duration: 10_000,
                        action: {
                            label: "Undo",
                            onClick: async () => {
                                const restored = await updateCalendarShare(shareId, `/api/calendar/shares/${encodeURIComponent(shareId)}`, {
                                    method: "PATCH",
                                    body: { ...share, isActive: true },
                                    notice: "Share link restored.",
                                });
                                if (restored) {
                                    window.APStudyToast?.success?.("Share link restored.");
                                    return false;
                                }
                                return true;
                            },
                        },
                    });
                }
                return;
            }
            const activateBtn = event.target.closest(".js-share-activate");
            if (activateBtn) {
                const shareId = activateBtn.getAttribute("data-share-id");
                const share = state.shares.items.find((item) => item.id === shareId);
                await updateCalendarShare(shareId, `/api/calendar/shares/${encodeURIComponent(shareId)}`, {
                    method: "PATCH",
                    body: { ...share, isActive: true },
                    notice: "Share link reactivated.",
                });
            }
        }

        return {
            closeCalendarShareModal,
            openCalendarShareModal,
        };
    }

    window.APStudyCalendarShare = { createCalendarShare };
})();
