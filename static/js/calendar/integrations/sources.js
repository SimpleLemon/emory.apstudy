(function () {
    function createCalendarSources({
        root = document,
        lifecycle = null,
        dataAdapter = null,
        state,
        constants,
        closeCalendarContextMenu,
        escapeHtml,
        getBufferedRange,
        getCalendarEventCount,
        getCalendarLabel,
        getCurrentRenderRange,
        invalidateCalendarPreferencesCache,
        isLocalCalendar,
        loadCalendarData,
        queueCalendarPreferenceSave,
        renderCalendarMenu,
        runManualRefresh,
        setCalendarColor,
        trackCalendarMutation,
    }) {
        const { eventsCacheKey } = constants;
        const doc = root.ownerDocument || root;
        const view = doc.defaultView || window;
        const storage = view.localStorage || localStorage;
        const mountNode = root.nodeType === 9 ? (root.body || root.documentElement) : root;
        const listen = lifecycle?.addEventListener
            ? lifecycle.addEventListener.bind(lifecycle)
            : (target, type, handler) => target.addEventListener(type, handler);

        async function fetchWithLifecycle(url, options = {}) {
            const controller = lifecycle?.trackAbortController?.() || new AbortController();
            try {
                if (dataAdapter?.saveSource) {
                    let body = options.body;
                    if (typeof body === "string") {
                        try { body = JSON.parse(body); } catch (_) { /* keep the raw body */ }
                    }
                    return await dataAdapter.saveSource({
                        path: url,
                        method: options.method || "POST",
                        body,
                        signal: controller.signal,
                    });
                }
                return await fetch(url, { ...options, signal: controller.signal });
            } finally {
                lifecycle?.releaseAbortController?.(controller);
            }
        }

        function closeRgbModal() {
            if (state.ui.rgbModalEl) {
                state.ui.rgbModalEl.remove();
                state.ui.rgbModalEl = null;
            }
        }

        function closeSourceInfoModal() {
            if (state.ui.sourceInfoModalEl) {
                state.ui.sourceInfoModalEl.remove();
                state.ui.sourceInfoModalEl = null;
            }
        }

        function closeCalendarSourceCreateModal() {
            if (state.ui.sourceCreateModalEl) {
                state.ui.sourceCreateModalEl.remove();
                state.ui.sourceCreateModalEl = null;
            }
        }

        function openCalendarInfoModal(calendarName) {
            closeSourceInfoModal();
            const calendar = state.calendars[calendarName];
            if (!calendar) return;
            const editable = Boolean(calendar.editable && calendar.sourceId);
            const isLocal = calendar.kind === "local" || isLocalCalendar(calendarName);
            const eventCount = getCalendarEventCount(calendarName);
            const label = getCalendarLabel(calendarName);
            const modal = doc.createElement("div");
            modal.className = "calendar-info-modal";
            modal.innerHTML = `
                <div class="calendar-info-dialog" role="dialog" aria-modal="true" aria-labelledby="calendar-info-title">
                    <div class="calendar-info-header">
                        <div class="calendar-info-heading">
                            <h3 id="calendar-info-title" class="calendar-info-title">Calendar Info</h3>
                            <p class="calendar-info-subtitle">${escapeHtml(label)}</p>
                        </div>
                        <button type="button" class="js-source-info-close calendar-info-close" aria-label="Close calendar info">
                            <span class="material-symbols-outlined calendar-info-close-icon" aria-hidden="true">close</span>
                        </button>
                    </div>
                    <div class="calendar-info-body">
                        <div class="calendar-info-stats">
                            <div class="calendar-info-stat">
                                <div class="calendar-info-stat-label">Events</div>
                                <div class="calendar-info-stat-value">${eventCount}</div>
                            </div>
                            <div class="calendar-info-stat">
                                <div class="calendar-info-stat-label">Type</div>
                                <div class="calendar-info-stat-value">${escapeHtml(calendar.kind || "local")}</div>
                            </div>
                        </div>
                        <label class="calendar-info-field">
                            <span class="calendar-info-label">Name</span>
                            <input class="js-source-info-name calendar-info-input" type="text" value="${escapeHtml(label)}" ${editable ? "" : "disabled"}>
                        </label>
                        ${isLocal ? "" : `<label class="calendar-info-field">
                            <span class="calendar-info-label">URL</span>
                            <input class="js-source-info-url calendar-info-input" type="url" inputmode="url" value="${escapeHtml(calendar.url || "")}" placeholder="https://calendar.example.com/feed.ics" ${editable ? "" : "disabled"}>
                        </label>`}
                        ${editable ? "" : '<p class="calendar-info-note">This calendar is generated inside Nest and does not have an editable feed URL.</p>'}
                        <p class="js-source-info-error calendar-info-error hidden" role="alert"></p>
                    </div>
                    <div class="calendar-info-footer">
                        <button type="button" class="js-source-info-cancel calendar-info-button calendar-info-button-secondary">Close</button>
                        ${editable ? '<button type="button" class="js-source-info-save calendar-info-button calendar-info-button-primary">Save</button>' : ""}
                    </div>
                </div>
            `;
            listen(modal, "click", async (event) => {
                if (event.target === modal || event.target.closest(".js-source-info-close") || event.target.closest(".js-source-info-cancel")) {
                    closeSourceInfoModal();
                    return;
                }
                const saveButton = event.target.closest(".js-source-info-save");
                if (!saveButton || saveButton.disabled) return;
                await saveCalendarSourceInfo(calendarName, modal, saveButton);
            });
            mountNode.appendChild(modal);
            lifecycle?.trackNode?.(modal);
            state.ui.sourceInfoModalEl = modal;
            modal.querySelector(".js-source-info-name")?.focus();
        }

        async function saveCalendarSourceInfo(calendarName, modal, saveButton) {
            const calendar = state.calendars[calendarName];
            if (!calendar?.sourceId) return;
            const nameInput = modal.querySelector(".js-source-info-name");
            const urlInput = modal.querySelector(".js-source-info-url");
            const errorEl = modal.querySelector(".js-source-info-error");
            const payload = {
                source_id: calendar.sourceId,
                display_name: nameInput?.value?.trim() || "",
                url: urlInput?.value?.trim() || "",
            };
            const showError = (message) => {
                if (!errorEl) return;
                errorEl.textContent = message || "Unable to save calendar.";
                errorEl.classList.remove("hidden");
            };
            if (calendar.kind !== "local" && !payload.url) {
                window.APStudyFormField?.markInvalid?.(urlInput);
                showError("Calendar URL is required.");
                return;
            }
            saveButton.disabled = true;
            const previousLabel = saveButton.textContent;
            saveButton.textContent = "Saving...";
            try {
                const result = await trackCalendarMutation(fetchWithLifecycle("/api/calendar/sources", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(payload),
                }));
                const res = result.response || result;
                const response = result.payload || await res.json().catch(() => ({}));
                if (!res.ok) {
                    throw new Error(response.error || "Unable to save calendar.");
                }
                storage.removeItem(eventsCacheKey);
                invalidateCalendarPreferencesCache();
                closeSourceInfoModal();
                closeCalendarContextMenu();
                state.ui.calendarMenuOpen = true;
                if (response.refresh_required) {
                    await runManualRefresh(getBufferedRange(getCurrentRenderRange()));
                } else {
                    await loadCalendarData();
                }
            } catch (err) {
                showError(err.message || "Unable to save calendar.");
            } finally {
                saveButton.disabled = false;
                saveButton.textContent = previousLabel;
            }
        }

        function openCalendarSourceCreateModal() {
            closeCalendarSourceCreateModal();
            closeCalendarContextMenu();
            closeRgbModal();
            const modal = doc.createElement("div");
            modal.className = "calendar-info-modal";
            const colors = state.calendarColors;
            let mode = "url";
            let selectedColor = colors[4] || "#0ea5e9";
            let draftName = "";
            let draftUrl = "";
            const renderBody = () => {
                modal.innerHTML = `
                    <div class="calendar-info-dialog" role="dialog" aria-modal="true" aria-labelledby="calendar-source-create-title">
                        <div class="calendar-info-header">
                            <div class="calendar-info-heading">
                                <h3 id="calendar-source-create-title" class="calendar-info-title">Add Calendar</h3>
                                <p class="calendar-info-subtitle">Create a local calendar or subscribe from a URL.</p>
                            </div>
                            <button type="button" class="js-source-create-close calendar-info-close" aria-label="Close add calendar">
                                <span class="material-symbols-outlined calendar-info-close-icon" aria-hidden="true">close</span>
                            </button>
                        </div>
                        <div class="calendar-info-body">
                            <div class="calendar-source-mode-toggle" role="group" aria-label="Calendar source type">
                                <button type="button" class="js-source-create-mode calendar-source-mode ${mode === "url" ? "is-active" : ""}" data-mode="url">From URL</button>
                                <button type="button" class="js-source-create-mode calendar-source-mode ${mode === "local" ? "is-active" : ""}" data-mode="local">Create New Calendar</button>
                            </div>
                            <label class="calendar-info-field">
                                <span class="calendar-info-label">Name (optional)</span>
                                <input class="js-source-create-name calendar-info-input" type="text" value="${escapeHtml(draftName)}" placeholder="${mode === "url" ? "Subscribed Calendar" : "Personal"}">
                            </label>
                            ${mode === "url" ? `
                            <label class="calendar-info-field">
                                <span class="calendar-info-label">URL</span>
                                <input class="js-source-create-url calendar-info-input" type="url" inputmode="url" value="${escapeHtml(draftUrl)}" placeholder="https://calendar.example.com/feed.ics">
                            </label>
                            ` : ""}
                            <div class="calendar-info-field">
                                <span class="calendar-info-label">Color</span>
                                <div class="calendar-context-color-grid">
                                    ${colors.map((color) => `
                                        <button type="button"
                                            class="js-source-create-color calendar-context-preset"
                                            data-color="${color}"
                                            aria-label="Use ${escapeHtml(color)}"
                                            style="background:${color}; border-color:${selectedColor === color ? "var(--color-on-surface)" : "transparent"};">
                                        </button>
                                    `).join("")}
                                </div>
                            </div>
                            <p class="js-source-create-error calendar-info-error hidden" role="alert"></p>
                        </div>
                        <div class="calendar-info-footer">
                            <button type="button" class="js-source-create-cancel calendar-info-button calendar-info-button-secondary">Cancel</button>
                            <button type="button" class="js-source-create-save calendar-info-button calendar-info-button-primary">Add</button>
                        </div>
                    </div>
                `;
                modal.querySelector(".js-source-create-name")?.focus();
            };
            renderBody();
            listen(modal, "click", async (event) => {
                if (event.target === modal || event.target.closest(".js-source-create-close") || event.target.closest(".js-source-create-cancel")) {
                    closeCalendarSourceCreateModal();
                    return;
                }
                const modeBtn = event.target.closest(".js-source-create-mode");
                if (modeBtn) {
                    draftName = modal.querySelector(".js-source-create-name")?.value?.trim() || draftName;
                    draftUrl = modal.querySelector(".js-source-create-url")?.value?.trim() || draftUrl;
                    mode = modeBtn.getAttribute("data-mode") === "local" ? "local" : "url";
                    renderBody();
                    return;
                }
                const colorBtn = event.target.closest(".js-source-create-color");
                if (colorBtn) {
                    draftName = modal.querySelector(".js-source-create-name")?.value?.trim() || draftName;
                    draftUrl = modal.querySelector(".js-source-create-url")?.value?.trim() || draftUrl;
                    selectedColor = colorBtn.getAttribute("data-color") || selectedColor;
                    renderBody();
                    return;
                }
                const saveButton = event.target.closest(".js-source-create-save");
                if (!saveButton || saveButton.disabled) return;
                const name = modal.querySelector(".js-source-create-name")?.value?.trim() || "";
                const calendarUrl = modal.querySelector(".js-source-create-url")?.value?.trim() || "";
                const errorEl = modal.querySelector(".js-source-create-error");
                const showError = (message) => {
                    if (!errorEl) return;
                    errorEl.textContent = message || "Unable to add calendar.";
                    errorEl.classList.remove("hidden");
                };
                if (mode === "url" && !calendarUrl) {
                    const urlInput = modal.querySelector(".js-source-create-url");
                    window.APStudyFormField?.markInvalid?.(urlInput);
                    showError("Calendar URL is required.");
                    return;
                }
                saveButton.disabled = true;
                saveButton.textContent = "Adding...";
                try {
                    const result = await trackCalendarMutation(fetchWithLifecycle(mode === "local" ? "/api/calendar/sources/local" : "/api/calendar/sources/url", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({
                            display_name: name,
                            url: calendarUrl,
                            color_hex: selectedColor,
                        }),
                    }));
                    const res = result.response || result;
                    const response = result.payload || await res.json().catch(() => ({}));
                    if (!res.ok) {
                        throw new Error(response.error || "Unable to add calendar.");
                    }
                    storage.removeItem(eventsCacheKey);
                    invalidateCalendarPreferencesCache();
                    closeCalendarSourceCreateModal();
                    state.ui.calendarMenuOpen = true;
                    await loadCalendarData();
                } catch (err) {
                    showError(err.message || "Unable to add calendar.");
                    saveButton.disabled = false;
                    saveButton.textContent = "Add";
                }
            });
            mountNode.appendChild(modal);
            lifecycle?.trackNode?.(modal);
            state.ui.sourceCreateModalEl = modal;
            modal.querySelector(".js-source-create-name")?.focus();
        }

        function rgbToHex(r, g, b) {
            const toHex = (value) => Math.max(0, Math.min(255, Number(value) || 0)).toString(16).padStart(2, "0");
            return `#${toHex(r)}${toHex(g)}${toHex(b)}`;
        }

        function openRgbModal(calendarName) {
            closeRgbModal();
            const current = state.calendars[calendarName]?.color || "#6366f1";
            const match = /^#([0-9a-fA-F]{6})$/.exec(current);
            const hex = match ? match[1] : "6366f1";
            const initial = {
                r: parseInt(hex.slice(0, 2), 16),
                g: parseInt(hex.slice(2, 4), 16),
                b: parseInt(hex.slice(4, 6), 16),
            };
            const modal = doc.createElement("div");
            modal.className = "calendar-modal-overlay fixed inset-0 bg-black/50 backdrop-blur-[2px] flex items-center justify-center p-4";
            modal.innerHTML = `
                <div class="w-full max-w-[480px] rounded-2xl border border-outline-variant/40 bg-surface p-5 shadow-2xl" role="dialog" aria-modal="true" aria-label="Custom calendar color">
                    <div class="flex items-center justify-between mb-4">
                        <h3 class="text-base font-semibold text-on-surface">Custom Color</h3>
                        <button type="button" class="js-rgb-close p-1 rounded bg-surface-container text-on-surface-variant hover:bg-surface-container-high hover:text-on-surface" aria-label="Close color picker">
                            <span class="material-symbols-outlined text-[18px]" aria-hidden="true">close</span>
                        </button>
                    </div>
                    <div class="grid grid-cols-1 sm:grid-cols-[1fr_1fr] gap-4">
                        <div class="space-y-3">
                            ${["r", "g", "b"].map((channel) => `
                                <div>
                                    <div class="flex items-center justify-between text-xs text-on-surface-variant mb-1.5">
                                        <span>${channel.toUpperCase()}</span>
                                        <input type="number" min="0" max="255" value="${initial[channel]}" aria-label="${channel.toUpperCase()} color value" class="js-rgb-number w-16 rounded border border-outline-variant/50 bg-surface-container-low px-2 py-1 text-right text-sm text-on-surface" data-channel="${channel}">
                                    </div>
                                    <input type="range" min="0" max="255" value="${initial[channel]}" aria-label="${channel.toUpperCase()} color slider" class="js-rgb-range w-full" data-channel="${channel}">
                                </div>
                            `).join("")}
                        </div>
                        <div class="rounded-xl border border-outline-variant/40 bg-surface-container-low p-3 flex flex-col gap-2">
                            <div class="text-xs text-on-surface-variant">Preview</div>
                            <div class="h-16 rounded-lg border border-black/15 js-rgb-preview"></div>
                            <div class="text-xs text-on-surface-variant">Hex</div>
                            <div class="font-mono text-sm text-on-surface js-rgb-hex"></div>
                        </div>
                    </div>
                    <div class="mt-5 flex justify-end gap-2">
                        <button type="button" class="js-rgb-cancel px-3 py-1.5 rounded-md text-sm bg-surface-container text-on-surface-variant hover:bg-surface-container-high hover:text-on-surface transition-colors">Cancel</button>
                        <button type="button" class="js-rgb-save px-3 py-1.5 rounded-md bg-primary text-on-primary text-sm font-medium hover:bg-primary-container hover:text-on-primary-container disabled:opacity-60 disabled:cursor-not-allowed">Save</button>
                    </div>
                </div>
            `;
            const readRgb = () => {
                const getValue = (channel) => {
                    const num = modal.querySelector(`.js-rgb-number[data-channel='${channel}']`);
                    return Math.max(0, Math.min(255, Number(num?.value || 0)));
                };
                return { r: getValue("r"), g: getValue("g"), b: getValue("b") };
            };
            const paintPreview = () => {
                const { r, g, b } = readRgb();
                const hexValue = rgbToHex(r, g, b);
                const preview = modal.querySelector(".js-rgb-preview");
                const hexText = modal.querySelector(".js-rgb-hex");
                if (preview) preview.style.background = hexValue;
                if (hexText) hexText.textContent = hexValue.toUpperCase();
            };
            listen(modal, "input", (event) => {
                const range = event.target.closest(".js-rgb-range");
                const number = event.target.closest(".js-rgb-number");
                if (range) {
                    const channel = range.getAttribute("data-channel");
                    const input = modal.querySelector(`.js-rgb-number[data-channel='${channel}']`);
                    if (input) input.value = range.value;
                    paintPreview();
                    return;
                }
                if (number) {
                    const channel = number.getAttribute("data-channel");
                    const next = Math.max(0, Math.min(255, Number(number.value || 0)));
                    number.value = String(next);
                    const input = modal.querySelector(`.js-rgb-range[data-channel='${channel}']`);
                    if (input) input.value = String(next);
                    paintPreview();
                }
            });
            listen(modal, "click", async (event) => {
                if (event.target === modal || event.target.closest(".js-rgb-close") || event.target.closest(".js-rgb-cancel")) {
                    closeRgbModal();
                    return;
                }
                if (event.target.closest(".js-rgb-save")) {
                    const saveButton = modal.querySelector(".js-rgb-save");
                    if (saveButton?.disabled) return;
                    const { r, g, b } = readRgb();
                    const hexValue = rgbToHex(r, g, b);
                    if (!/^#[0-9a-fA-F]{6}$/.test(hexValue)) return;
                    if (saveButton) {
                        saveButton.disabled = true;
                        saveButton.innerHTML = `<span class="inline-flex flex-col items-center justify-center gap-1 leading-none"><span class="loader" style="width:16px; height:16px;" aria-hidden="true"></span><span class="text-xs">Saving...</span></span>`;
                    }
                    setCalendarColor(calendarName, hexValue);
                    queueCalendarPreferenceSave(calendarName, 0);
                    closeRgbModal();
                    closeCalendarContextMenu();
                    state.ui.calendarMenuOpen = true;
                    renderCalendarMenu();
                }
            });
            mountNode.appendChild(modal);
            lifecycle?.trackNode?.(modal);
            state.ui.rgbModalEl = modal;
            paintPreview();
        }

        return {
            closeCalendarSourceCreateModal,
            closeRgbModal,
            closeSourceInfoModal,
            openCalendarInfoModal,
            openCalendarSourceCreateModal,
            openRgbModal,
        };
    }

    window.APStudyCalendarSources = { createCalendarSources };
})();
