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
        const icsState = new Map();
        const icsEligibleIds = new Set(["canvas", "tasks", "simulated_courses"]);
        let activeModalSession = null;
        let shareDataLoadPromise = null;

        function canonicalIcsCalendarId(value) {
            const candidate = String(value || "").trim();
            const aliases = {
                Canvas: "canvas",
                canvas: "canvas",
                Tasks: "tasks",
                tasks: "tasks",
                "local:tasks": "tasks",
                "Simulated Courses": "simulated_courses",
                "simulated courses": "simulated_courses",
                simulated_courses: "simulated_courses",
            };
            return aliases[candidate] || null;
        }

        function selectedCalendarIdsFromForm(form) {
            if (!form || form.include_scope?.value !== "selected") return [];
            return Array.from(form.querySelectorAll("input[name='calendar_ids']:checked"))
                .map((input) => input.value);
        }

        function getIcsSelectionEligibility({ includeAll = true, calendarIds = [] } = {}) {
            const canonicalIds = calendarIds.map(canonicalIcsCalendarId);
            const ids = [...new Set(canonicalIds.filter(Boolean))];
            const hasIneligibleCalendar = canonicalIds.some((id) => !id);
            return {
                eligible: !includeAll && !hasIneligibleCalendar && ids.length === 1 && icsEligibleIds.has(ids[0]),
                calendarId: !includeAll && ids.length === 1 ? ids[0] : null,
            };
        }

        function canCreateCalendarSubscription(calendarName) {
            return getIcsSelectionEligibility({
                includeAll: false,
                calendarIds: [calendarName],
            }).eligible;
        }

        function findActiveConfiguredIcsShare(calendarId) {
            return state.shares.items.find((share) => Boolean(
                share.isActive
                && share.icsConfigured
                && getIcsSelectionEligibility({
                    includeAll: share.includeAllCalendars !== false,
                    calendarIds: share.calendarIds || [],
                }).calendarId === calendarId,
            )) || null;
        }

        function applyCalendarSubscriptionIntent(calendarName, { matchExisting = true } = {}) {
            const eligibility = getIcsSelectionEligibility({ includeAll: false, calendarIds: [calendarName] });
            if (!eligibility.eligible) return null;
            const existing = matchExisting ? findActiveConfiguredIcsShare(eligibility.calendarId) : null;
            if (existing) {
                state.shares.editingId = existing.id;
                state.shares.draft = null;
                state.shares.notice = "This calendar already has an ICS subscription. Manage or re-enable it below.";
                return existing;
            }
            state.shares.editingId = null;
            state.shares.draft = {
                includeAllCalendars: false,
                calendarIds: [calendarName],
                dateScope: "all",
                fixedStart: "",
                fixedEnd: "",
                rollingDays: 30,
                icsEnabled: true,
            };
            state.shares.notice = "Review the single-calendar subscription, then create the link. Nothing has been changed yet.";
            return null;
        }

        function isCurrentModalSession(session) {
            return Boolean(
                session
                && activeModalSession === session
                && state.ui.shareModalEl === session.modal
                && !lifecycle?.isDisposed?.(),
            );
        }

        function getIcsState(shareId) {
            if (!icsState.has(shareId)) {
                icsState.set(shareId, { loading: false, saving: false, action: "", detail: null, expanded: false, error: "", notice: "" });
            }
            return icsState.get(shareId);
        }

        function setFocusTarget(selector) {
            state.shares.focusTarget = selector;
        }

        function focusSelectorFor(element) {
            if (!element || !element.matches?.("button, input, select, textarea, [tabindex]")) return "";
            const escape = view.CSS?.escape || ((value) => String(value).replace(/([\\"'()[\].:#,>+~*=])/g, "\\$1"));
            const shareId = element.getAttribute("data-share-id");
            if (shareId && element.hasAttribute("data-ics-action")) {
                return `.js-share-ics-action[data-share-id="${escape(shareId)}"][data-ics-action="${escape(element.getAttribute("data-ics-action"))}"]`;
            }
            if (shareId && element.hasAttribute("data-ics-url")) {
                return `.js-share-ics-copy[data-share-id="${escape(shareId)}"][data-ics-url="${escape(element.getAttribute("data-ics-url"))}"]`;
            }
            if (element.id) return `#${escape(element.id)}`;
            if (element.name) return `${element.tagName.toLowerCase()}[name="${escape(element.name)}"]${element.value ? `[value="${escape(element.value)}"]` : ""}`;
            if (element.classList?.contains("js-share-close")) return ".js-share-close";
            return ".calendar-share-dialog";
        }

        function rememberFocusedControl() {
            const modal = state.ui.shareModalEl;
            const active = doc.activeElement;
            if (modal && active && modal.contains?.(active)) {
                const selector = focusSelectorFor(active);
                if (selector) state.shares.focusTarget = selector;
            }
        }

        function restoreFocus() {
            const selector = state.shares.focusTarget;
            if (!selector) return;
            state.shares.focusTarget = "";
            const restore = () => modalFocus(selector);
            if (view.requestAnimationFrame) view.requestAnimationFrame(restore);
            else restore();
        }

        function modalFocus(selector) {
            const modal = state.ui.shareModalEl;
            const target = modal?.querySelector(selector) || modal?.querySelector(".js-share-close") || modal?.querySelector(".calendar-share-dialog");
            target?.focus?.({ preventScroll: true });
        }

        function formatRequestError(res, payload, fallback) {
            const code = String(payload?.code || "");
            const status = Number(res?.status || 0);
            if (code === "calendar_ics_disabled" || status === 403) {
                return "ICS subscriptions are not enabled for this account yet. Ask an administrator to enable access, then try again.";
            }
            if (status === 404 || code === "calendar_ics_not_found") {
                return "This share is no longer available. Refresh the share list and try again.";
            }
            if (status === 409 || code === "calendar_ics_selection_locked" || code === "calendar_ics_parent_revoked") {
                return "This share changed or was revoked. Refresh the share list before trying again.";
            }
            if (status === 422) {
                return payload?.error || "This calendar selection cannot be used for an ICS subscription. Choose one Nest calendar.";
            }
            if (status === 429) {
                return "Too many subscription requests were made. Wait a moment, then try again.";
            }
            if (status >= 500) {
                return "Nest could not prepare this subscription right now. Try again later; your browser share was not changed.";
            }
            return payload?.error || fallback;
        }

        async function requestJson(path, options = {}, fallback = "Unable to update calendar sharing.") {
            try {
                const result = await requestShare(path, options);
                const res = result.response || result;
                const payload = result.payload || await res.json().catch(() => ({}));
                if (!res.ok) throw new Error(formatRequestError(res, payload, fallback));
                return { res, payload };
            } catch (error) {
                if (error?.message && !/Failed to fetch|NetworkError|Load failed/i.test(error.message)) throw error;
                throw new Error("Could not reach Nest. Check your connection and try again; your share settings were kept.");
            }
        }

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
            activeModalSession = null;
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
            state.shares.formValidationError = false;
            state.shares.loading = false;
            state.shares.notice = "";
            state.shares.focusTarget = "";
        }

        function ensureCalendarShareDataLoaded() {
            if (state.shares.loaded) return Promise.resolve(state.shares.items);
            if (shareDataLoadPromise) return shareDataLoadPromise;
            const controller = requestController();
            let request;
            request = (async () => {
                try {
                    const result = dataAdapter?.loadShares
                        ? await dataAdapter.loadShares({ signal: controller.signal })
                        : { response: await fetch("/api/calendar/shares", { signal: controller.signal }) };
                    const res = result.response || result;
                    const payload = result.payload || await res.json().catch(() => ({}));
                    if (!res.ok) throw new Error(payload.error || "Unable to load share links.");
                    state.shares.items = Array.isArray(payload.shares) ? payload.shares : [];
                    state.shares.loaded = true;
                    return state.shares.items;
                } finally {
                    lifecycle?.releaseAbortController?.(controller);
                    if (shareDataLoadPromise === request) shareDataLoadPromise = null;
                }
            })();
            shareDataLoadPromise = request;
            return request;
        }

        async function hydrateCalendarShareModal(session) {
            try {
                await ensureCalendarShareDataLoaded();
            } catch (err) {
                if (!isCurrentModalSession(session)) return;
                state.shares.loading = false;
                state.shares.error = err.message || "Could not reach Nest. Check your connection and try again.";
                renderCalendarShareModal();
                return;
            }
            if (!isCurrentModalSession(session)) return;
            state.shares.loading = false;
            state.shares.error = "";
            if (session.subscriptionCalendar) {
                applyCalendarSubscriptionIntent(session.subscriptionCalendar);
            }
            renderCalendarShareModal();
        }

        function openCalendarShareModal({ subscriptionCalendar = "" } = {}) {
            if (state.public.readOnly) return;
            closeCalendarShareModal(true);
            const session = { modal: null, subscriptionCalendar };
            activeModalSession = session;
            state.shares.editingId = null;
            state.shares.draft = null;
            state.shares.error = "";
            state.shares.loading = !state.shares.loaded;
            state.shares.notice = "";
            if (subscriptionCalendar) {
                applyCalendarSubscriptionIntent(subscriptionCalendar, { matchExisting: state.shares.loaded });
            }
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
            session.modal = modal;
            renderCalendarShareModal();
            modalFocus(".js-share-close");
            void hydrateCalendarShareModal(session);
        }

        function openCalendarSubscriptionModal(calendarName) {
            if (!canCreateCalendarSubscription(calendarName)) return;
            openCalendarShareModal({ subscriptionCalendar: calendarName });
        }

        function renderCalendarShareModal() {
            const modal = state.ui.shareModalEl;
            if (!modal) return;
            rememberFocusedControl();
            const editingShare = state.shares.items.find((share) => share.id === state.shares.editingId) || null;
            const seed = editingShare || state.shares.draft || {
                includeAllCalendars: true,
                calendarIds: [],
                dateScope: "all",
                fixedStart: "",
                fixedEnd: "",
                rollingDays: 30,
            };
            const calendarEntries = Object.entries(state.calendars);
            if (!calendarEntries.some(([name]) => canonicalIcsCalendarId(name) === "simulated_courses")) {
                calendarEntries.push([simulatedCalendarName, {
                    color: "#b08968",
                    label: simulatedCalendarName,
                    defaultName: simulatedCalendarName,
                }]);
            }
            const shareableCalendars = calendarEntries
                .sort(([, a], [, b]) => getCalendarLabelFromData(a).localeCompare(getCalendarLabelFromData(b)));
            const selectedIds = new Set(seed.calendarIds || []);
            const includeAll = seed.includeAllCalendars !== false;
            const selectedCanonicalIds = [...selectedIds];
            const icsEligibility = getIcsSelectionEligibility({ includeAll, calendarIds: selectedCanonicalIds });
            const selectionLocked = Boolean(editingShare?.icsConfigured);
            const editingCode = editingShare?.shareCode || "";
            const formValidationError = Boolean(state.shares.formValidationError);
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
                        <input type="checkbox" name="calendar_ids" value="${escapeHtml(calendarName)}" ${includeAll || selectedIds.has(calendarName) || selectedIds.has(canonicalIcsCalendarId(calendarName)) ? "checked" : ""} ${includeAll || selectionLocked ? "disabled" : ""} ${formValidationError ? 'aria-describedby="calendar-share-form-error" aria-invalid="true"' : ""}>
                        <span class="calendar-share-calendar-dot" style="background:${data.color};"></span>
                        <span>${escapeHtml(getCalendarLabel(calendarName))}</span>
                    </label>
                `).join("")
                : '<p class="calendar-info-note">Load or add a calendar before limiting by calendar.</p>';
            const sharesList = state.shares.loading
                ? `<div class="calendar-share-empty" role="status" aria-live="polite" aria-atomic="true">${window.APStudyLoader.html("Loading share links...", { sizePx: 30, textToneClass: "text-on-surface" })}</div>`
                : state.shares.items.length
                    ? state.shares.items.map((share) => buildCalendarShareRowHtml(share, editingShare?.id)).join("")
                    : '<div class="calendar-share-empty">No share links yet.</div>';
            modal.innerHTML = `
                <div class="calendar-info-dialog calendar-share-dialog" role="dialog" aria-modal="true" aria-labelledby="calendar-share-title" tabindex="-1">
                    <div class="calendar-info-header">
                        <div class="calendar-info-heading">
                            <h3 id="calendar-share-title" class="calendar-info-title">${escapeHtml(modalTitle)}</h3>
                            <p class="calendar-info-subtitle">${escapeHtml(modalSubtitle)}</p>
                        </div>
                        <button type="button" class="js-share-close calendar-info-close" aria-label="Close calendar sharing">
                            <span class="material-symbols-outlined calendar-info-close-icon" aria-hidden="true">close</span>
                        </button>
                    </div>
                    <form id="calendar-share-form" ${formValidationError ? 'aria-describedby="calendar-share-form-error"' : ""}>
                        <div class="calendar-info-body">
                            ${editingBanner}
                            <div class="calendar-share-options">
                                <label class="calendar-share-radio">
                                    <input type="radio" name="include_scope" value="all" ${includeAll ? "checked" : ""} ${formValidationError ? 'aria-describedby="calendar-share-form-error" aria-invalid="true"' : ""}>
                                    <span>All calendars</span>
                                </label>
                                <label class="calendar-share-radio">
                                    <input type="radio" name="include_scope" value="selected" ${includeAll ? "" : "checked"} ${formValidationError ? 'aria-describedby="calendar-share-form-error" aria-invalid="true"' : ""}>
                                    <span>Selected calendars</span>
                                </label>
                            </div>
                            <div class="calendar-share-calendar-grid ${includeAll ? "is-disabled" : ""} ${selectionLocked ? "is-locked" : ""}" aria-disabled="${includeAll || selectionLocked ? "true" : "false"}">
                                ${calendarChoices}
                            </div>
                            ${selectionLocked ? '<p class="calendar-share-lock-note" role="status">Calendar selection is locked while this ICS subscription exists. Remove the subscription below to unlock it. The browser share remains separate.</p>' : ""}
                            ${!editingShare ? `
                                <section class="calendar-share-ics-create" aria-labelledby="calendar-share-ics-create-title">
                                    <div class="calendar-share-ics-create-heading">
                                        <h4 id="calendar-share-ics-create-title">Calendar subscription</h4>
                                        <p>Optional: subscribe to this one Nest calendar from Apple Calendar, Google Calendar, or Outlook.</p>
                                    </div>
                                    <label class="calendar-share-ics-optin" data-ics-create-option ${icsEligibility.eligible ? "" : "hidden"}>
                                        <input type="checkbox" name="ics_enabled" value="1" ${seed.icsEnabled ? "checked" : ""}>
                                        <span>
                                            <strong>Enable an ICS subscription</strong>
                                            <small>Exports the previous 30 days through the next 366 days, using UTC dates.</small>
                                        </span>
                                    </label>
                                    <p class="calendar-share-ics-selection-note" data-ics-selection-note ${icsEligibility.eligible ? "hidden" : ""}>Choose exactly one Nest calendar—Canvas, Tasks, or Simulated Courses—to enable an ICS subscription. Multi-calendar and all-calendar shares stay browser-only.</p>
                                    <p class="calendar-share-ics-selection-note" data-ics-simulated-note ${icsEligibility.calendarId === "simulated_courses" ? "" : "hidden"}>Simulated Courses exports only your saved server selections.</p>
                                </section>
                            ` : ""}
                            ${editingShare?.icsConfigured ? '<p class="calendar-share-ics-edit-note">Manage this share’s ICS subscription in its row below. It does not change the browser share.</p>' : ""}
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
                            ${state.shares.error ? `<p id="${formValidationError ? "calendar-share-form-error" : "calendar-share-error"}" class="calendar-info-error" role="alert" aria-live="assertive" aria-atomic="true">${escapeHtml(state.shares.error)}</p>` : ""}
                            ${state.shares.notice ? `<p class="calendar-share-notice" role="status" aria-live="polite" aria-atomic="true">${escapeHtml(state.shares.notice)}</p>` : ""}
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
            restoreFocus();
        }

        function buildCalendarShareRowHtml(share, editingId = null) {
            const inactive = !share.isActive;
            const isEditing = share.id === editingId;
            const statusLabel = isEditing ? "Editing now" : inactive ? "Revoked link" : "Active link";
            const selectionIds = share.calendarIds || [];
            const icsEligibility = getIcsSelectionEligibility({
                includeAll: share.includeAllCalendars !== false,
                calendarIds: selectionIds,
            });
            const ics = getIcsState(share.id);
            const icsStatus = !share.icsConfigured
                ? "Not configured"
                : share.icsEnabled ? "Enabled" : "Suspended";
            const icsActionBusy = ics.loading || ics.saving;
            const detail = ics.detail;
            const icsError = ics.error ? `<p class="calendar-share-ics-error" role="alert" aria-live="assertive" aria-atomic="true">${escapeHtml(ics.error)}</p>` : "";
            const icsNotice = ics.notice ? `<p class="calendar-share-ics-notice" role="status" aria-live="polite" aria-atomic="true">${escapeHtml(ics.notice)}</p>` : "";
            if (inactive) {
                return `
                    <article class="calendar-share-row is-inactive ${isEditing ? "is-editing" : ""}">
                        <div class="calendar-share-row-main">
                            <div class="calendar-share-row-title"><span>${statusLabel}</span><span class="calendar-share-code">${escapeHtml(share.shareCode || "")}</span></div>
                            <div class="calendar-share-row-meta">${escapeHtml(share.scopeLabel || "All shared dates")}</div>
                            <input class="calendar-info-input calendar-share-url" readonly aria-label="Share link" value="${escapeHtml(share.shareUrl || "")}" />
                        </div>
                        <div class="calendar-share-actions">
                            <button type="button" class="js-share-copy calendar-info-button calendar-info-button-secondary" data-share-id="${escapeHtml(share.id)}" disabled>Copy</button>
                            <button type="button" class="js-share-edit calendar-info-button calendar-info-button-secondary" data-share-id="${escapeHtml(share.id)}" ${isEditing ? "disabled" : ""}>${isEditing ? "Editing" : "Edit"}</button>
                            <button type="button" class="js-share-regenerate calendar-info-button calendar-info-button-secondary" data-share-id="${escapeHtml(share.id)}">Regenerate</button>
                            <button type="button" class="js-share-activate calendar-info-button calendar-info-button-primary" data-share-id="${escapeHtml(share.id)}">Reactivate</button>
                        </div>
                        <section class="calendar-share-ics" aria-label="ICS subscription">
                            <p class="calendar-share-ics-selection-note" role="note">ICS controls are unavailable while this browser share is revoked. Reactivate the share before managing its subscription.</p>
                        </section>
                    </article>`;
            }
            const icsUrls = detail?.configured ? `
                <div class="calendar-share-ics-urls" aria-label="ICS subscription URLs">
                    <div class="calendar-share-ics-url-row">
                        <div class="calendar-share-ics-url-copy">
                            <span class="calendar-share-ics-url-label">HTTPS URL</span>
                            <code class="calendar-share-ics-url-value" title="${escapeHtml(detail.httpsUrl || "")}">${escapeHtml(detail.httpsUrl || "")}</code>
                        </div>
                        <button type="button" class="js-share-ics-copy calendar-info-button calendar-info-button-secondary" data-share-id="${escapeHtml(share.id)}" data-ics-url="https" ${icsActionBusy ? "disabled" : ""}>Copy HTTPS URL</button>
                    </div>
                    <div class="calendar-share-ics-url-row">
                        <div class="calendar-share-ics-url-copy">
                            <span class="calendar-share-ics-url-label">webcal URL</span>
                            <code class="calendar-share-ics-url-value" title="${escapeHtml(detail.webcalUrl || "")}">${escapeHtml(detail.webcalUrl || "")}</code>
                        </div>
                        <button type="button" class="js-share-ics-copy calendar-info-button calendar-info-button-secondary" data-share-id="${escapeHtml(share.id)}" data-ics-url="webcal" ${icsActionBusy ? "disabled" : ""}>Copy webcal URL</button>
                    </div>
                </div>
            ` : "";
            const icsManagement = !icsEligibility.eligible
                ? `<p class="calendar-share-ics-selection-note">An ICS subscription requires a new share containing exactly one Nest calendar: Canvas, Tasks, or Simulated Courses.</p><button type="button" class="js-share-new-single calendar-info-button calendar-info-button-secondary" data-share-id="${escapeHtml(share.id)}">Create new single-calendar share</button>`
                : `
                    <div class="calendar-share-ics-summary">
                        <div class="calendar-share-ics-summary-copy">
                            <strong>ICS subscription · ${icsStatus}</strong>
                            <span>Separate from this browser share. Providers decide when they refresh.</span>
                        </div>
                        ${share.icsConfigured
                            ? `<button type="button" class="js-share-ics-details calendar-info-button calendar-info-button-secondary" data-share-id="${escapeHtml(share.id)}" ${icsActionBusy ? "disabled" : ""}>${ics.loading ? "Loading…" : ics.expanded ? "Hide details" : "Show details"}</button>`
                            : `<button type="button" class="js-share-ics-action calendar-info-button calendar-info-button-primary" data-share-id="${escapeHtml(share.id)}" data-ics-action="enable" ${icsActionBusy ? "disabled" : ""}>${ics.saving ? "Enabling…" : "Enable ICS subscription"}</button>`}
                    </div>
                    ${ics.expanded ? `<div class="calendar-share-ics-detail" ${detail?.configured ? "" : "hidden"}>
                        <p class="calendar-share-ics-window">Exports the previous <strong>30 days</strong> through the next <strong>366 days</strong>, with UTC date boundaries. Changes may take time to appear because Apple, Google, and Outlook control refresh timing.</p>
                        ${share.icsConfigured && !share.icsEnabled ? '<p class="calendar-share-ics-warning" role="note">This subscription is suspended. The retained URL will not work until you re-enable the subscription.</p>' : ""}
                        ${icsUrls}
                        ${share.calendarIds?.some((id) => canonicalIcsCalendarId(id) === "simulated_courses") ? '<p class="calendar-share-ics-window">Simulated Courses exports only your saved server selections.</p>' : ""}
                        <div class="calendar-share-ics-actions">
                            <button type="button" class="js-share-ics-action calendar-info-button calendar-info-button-secondary" data-share-id="${escapeHtml(share.id)}" data-ics-action="${share.icsEnabled ? "disable" : "enable"}" ${icsActionBusy ? "disabled" : ""}>${ics.saving ? "Working…" : share.icsEnabled ? "Disable subscription" : "Re-enable subscription"}</button>
                            <button type="button" class="js-share-ics-action calendar-info-button calendar-info-button-secondary" data-share-id="${escapeHtml(share.id)}" data-ics-action="rotate" ${icsActionBusy ? "disabled" : ""}>Rotate URL</button>
                            <button type="button" class="js-share-ics-action calendar-info-button calendar-info-button-danger" data-share-id="${escapeHtml(share.id)}" data-ics-action="remove" ${icsActionBusy ? "disabled" : ""}>Remove subscription</button>
                        </div>
                        <p class="calendar-share-ics-warning">Rotate invalidates the old URL. Remove permanently clears the credential and unlocks calendar selection. Neither changes this browser share.</p>
                    </div>` : ""}
                    ${icsError}${icsNotice}
                `;
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
                    <section class="calendar-share-ics" aria-label="ICS subscription">
                        ${icsManagement}
                    </section>
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
                const editingShare = state.shares.items.find((share) => share.id === state.shares.editingId);
                input.disabled = includeAll || Boolean(editingShare?.icsConfigured);
                if (includeAll) input.checked = true;
            });
            const editingShare = state.shares.items.find((share) => share.id === state.shares.editingId);
            if (form.include_scope) form.include_scope.disabled = Boolean(editingShare?.icsConfigured);
            const calendarGrid = modal.querySelector(".calendar-share-calendar-grid");
            if (calendarGrid) {
                calendarGrid.classList.toggle("is-disabled", includeAll);
                calendarGrid.setAttribute("aria-disabled", includeAll || Boolean(editingShare?.icsConfigured) ? "true" : "false");
            }
            const scope = form.date_scope?.value || "all";
            const fixedFields = modal.querySelector(".calendar-share-fixed-fields");
            const rollingField = modal.querySelector(".calendar-share-rolling-field");
            if (fixedFields) fixedFields.hidden = scope !== "fixed";
            if (rollingField) rollingField.hidden = scope !== "rolling";
            const icsOption = modal.querySelector("[data-ics-create-option]");
            const icsNote = modal.querySelector("[data-ics-selection-note]");
            const simulatedNote = modal.querySelector("[data-ics-simulated-note]");
            if (icsOption || icsNote) {
                const eligibility = getIcsSelectionEligibility({
                    includeAll,
                    calendarIds: selectedCalendarIdsFromForm(form),
                });
                if (icsOption) icsOption.hidden = !eligibility.eligible;
                if (icsNote) icsNote.hidden = eligibility.eligible;
                if (simulatedNote) simulatedNote.hidden = eligibility.calendarId !== "simulated_courses";
            }
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
                icsEnabled: Boolean(form.ics_enabled?.checked && getIcsSelectionEligibility({
                    includeAll,
                    calendarIds: selectedCalendarIdsFromForm(form),
                }).eligible),
            };
        }

        async function saveCalendarSharePayload(payload) {
            state.shares.saving = true;
            state.shares.error = "";
            state.shares.notice = "";
            state.shares.formValidationError = false;
            state.shares.draft = payload;
            const editingId = state.shares.editingId;
            renderCalendarShareModal();
            let validationError = false;
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
                if (!res.ok) {
                    validationError = res.status === 400 || res.status === 422;
                    throw new Error(formatRequestError(res, response, "Unable to save share link. Check the calendar selection and date range, then try again."));
                }
                const share = response.share;
                if (share?.id) {
                    const index = state.shares.items.findIndex((item) => item.id === share.id);
                    if (index >= 0) state.shares.items.splice(index, 1, share);
                    else state.shares.items.unshift(share);
                }
                state.shares.editingId = null;
                state.shares.draft = null;
                state.shares.notice = editingId ? "Share link updated." : "Share link created.";
            } catch (err) {
                state.shares.error = err.message || "Unable to save share link.";
                state.shares.formValidationError = validationError;
            } finally {
                state.shares.saving = false;
                renderCalendarShareModal();
            }
        }

        async function updateCalendarShare(shareId, path, options = {}) {
            state.shares.error = "";
            state.shares.notice = "";
            state.shares.formValidationError = false;
            renderCalendarShareModal();
            try {
                const result = await trackCalendarMutation(requestShare(path, {
                    method: options.method || "POST",
                    headers: { "Content-Type": "application/json" },
                    body: options.body ? JSON.stringify(options.body) : undefined,
                }));
                const res = result.response || result;
                const response = result.payload || await res.json().catch(() => ({}));
                if (!res.ok) throw new Error(formatRequestError(res, response, "Unable to update share link. Check the calendar selection and date range, then try again."));
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

        function replaceShareItem(share) {
            if (!share?.id) return;
            const index = state.shares.items.findIndex((item) => item.id === share.id);
            if (index >= 0) state.shares.items.splice(index, 1, share);
            else state.shares.items.unshift(share);
        }

        async function loadCalendarIcsDetail(shareId, force = false) {
            const share = state.shares.items.find((item) => item.id === shareId);
            if (!share || !share.icsConfigured) return false;
            const ics = getIcsState(shareId);
            if (ics.loading) return false;
            if (ics.expanded && !force) {
                ics.expanded = false;
                renderCalendarShareModal();
                return true;
            }
            ics.loading = true;
            ics.error = "";
            ics.notice = "";
            renderCalendarShareModal();
            try {
                const { payload } = await requestJson(
                    `/api/calendar/shares/${encodeURIComponent(shareId)}/ics`,
                    { method: "GET" },
                    "Unable to load the ICS subscription details.",
                );
                ics.detail = payload.ics || null;
                ics.expanded = true;
                return true;
            } catch (error) {
                ics.error = error.message || "Unable to load the ICS subscription details.";
                ics.expanded = true;
                return false;
            } finally {
                ics.loading = false;
                renderCalendarShareModal();
            }
        }

        function confirmIcsAction(action) {
            if (action === "rotate") {
                return view.confirm
                    ? view.confirm("Rotate this ICS URL? The old subscription URL will stop working and must be replaced in every calendar app. Your browser share will not change.")
                    : true;
            }
            if (action === "remove") {
                return view.confirm
                    ? view.confirm("Remove this ICS subscription? Its credential will be permanently cleared and calendar selection will unlock. Your browser share will not change.")
                    : true;
            }
            return true;
        }

        async function runCalendarIcsAction(shareId, action) {
            const share = state.shares.items.find((item) => item.id === shareId);
            const ics = getIcsState(shareId);
            if (!share || ics.saving || ics.loading || !confirmIcsAction(action)) return;
            if (action === "enable" && !getIcsSelectionEligibility({
                includeAll: share.includeAllCalendars !== false,
                calendarIds: share.calendarIds || [],
            }).eligible) {
                ics.error = "An ICS subscription requires exactly one eligible Nest calendar. Create a new single-calendar share first.";
                ics.expanded = true;
                renderCalendarShareModal();
                return;
            }
            ics.saving = true;
            ics.action = action;
            ics.error = "";
            ics.notice = "";
            setFocusTarget(`.js-share-ics-action[data-share-id="${shareId}"][data-ics-action="${action}"]`);
            if (action === "rotate") ics.detail = null;
            renderCalendarShareModal();
            try {
                const path = `/api/calendar/shares/${encodeURIComponent(shareId)}/ics`;
                const { payload } = await requestJson(path, {
                    method: action === "remove" ? "DELETE" : "POST",
                    headers: action === "remove" ? undefined : { "Content-Type": "application/json" },
                    body: action === "remove" ? undefined : { action },
                }, "Unable to update the ICS subscription.");
                if (payload.share) replaceShareItem(payload.share);
                if (action === "rotate") {
                    // Do not render or leave the old secret copyable while the new detail is fetched.
                    ics.detail = null;
                }
                ics.notice = action === "enable"
                    ? "ICS subscription enabled. Your browser share was not changed."
                    : action === "disable"
                        ? "ICS subscription suspended. The URL is retained for re-enabling."
                        : action === "rotate"
                            ? "ICS URL rotated. The previous URL is no longer valid."
                            : "ICS subscription removed. Calendar selection is unlocked; your browser share was not changed.";
                if (action === "remove") {
                    ics.detail = null;
                    ics.expanded = false;
                } else {
                    ics.expanded = true;
                }
            } catch (error) {
                ics.error = error.message || "Unable to update the ICS subscription.";
                ics.expanded = true;
            } finally {
                ics.saving = false;
                ics.action = "";
                renderCalendarShareModal();
            }
            if (action === "enable" || action === "rotate") {
                await loadCalendarIcsDetail(shareId, true);
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
            if (["include_scope", "date_scope", "calendar_ids", "ics_enabled"].includes(event.target?.name)) {
                syncCalendarShareModalFields();
            }
        }

        async function onCalendarShareModalSubmit(event) {
            if (event.target?.id !== "calendar-share-form") return;
            event.preventDefault();
            if (state.shares.saving) return;
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
                state.shares.draft = null;
                state.shares.error = "";
                state.shares.notice = "";
                renderCalendarShareModal();
                return;
            }
            const newSingleBtn = event.target.closest(".js-share-new-single");
            if (newSingleBtn) {
                state.shares.editingId = null;
                state.shares.draft = {
                    includeAllCalendars: false,
                    calendarIds: [],
                    dateScope: "all",
                    fixedStart: "",
                    fixedEnd: "",
                    rollingDays: 30,
                    icsEnabled: false,
                };
                state.shares.error = "";
                state.shares.notice = "Choose one Nest calendar below to create an ICS-ready share. Nothing was changed on the existing share.";
                setFocusTarget("input[name='include_scope'][value='selected']");
                renderCalendarShareModal();
                return;
            }
            const icsDetailsBtn = event.target.closest(".js-share-ics-details");
            if (icsDetailsBtn) {
                await loadCalendarIcsDetail(icsDetailsBtn.getAttribute("data-share-id"));
                return;
            }
            const icsActionBtn = event.target.closest(".js-share-ics-action");
            if (icsActionBtn) {
                await runCalendarIcsAction(
                    icsActionBtn.getAttribute("data-share-id"),
                    icsActionBtn.getAttribute("data-ics-action"),
                );
                return;
            }
            const icsCopyBtn = event.target.closest(".js-share-ics-copy");
            if (icsCopyBtn) {
                const shareId = icsCopyBtn.getAttribute("data-share-id");
                const ics = getIcsState(shareId);
                const url = ics.detail?.[icsCopyBtn.getAttribute("data-ics-url") === "webcal" ? "webcalUrl" : "httpsUrl"];
                if (!url || ics.saving) return;
                try {
                    if (!await copyTextToClipboard(url)) throw new Error("Clipboard unavailable");
                    ics.notice = `${icsCopyBtn.getAttribute("data-ics-url") === "webcal" ? "webcal" : "HTTPS"} URL copied.`;
                    ics.error = "";
                } catch (_error) {
                    state.shares.formValidationError = false;
                    ics.error = "Could not copy the URL. Select it manually, then copy it from this owner-only view.";
                }
                setFocusTarget(`.js-share-ics-copy[data-share-id="${shareId}"][data-ics-url="${icsCopyBtn.getAttribute("data-ics-url")}"]`);
                renderCalendarShareModal();
                return;
            }
            const copyBtn = event.target.closest(".js-share-copy");
            if (copyBtn) {
                const share = state.shares.items.find((item) => item.id === copyBtn.getAttribute("data-share-id"));
                if (!share?.shareUrl) return;
                state.shares.formValidationError = false;
                try {
                    if (!await copyTextToClipboard(share.shareUrl)) throw new Error("Clipboard unavailable");
                    state.shares.notice = "Share link copied.";
                    state.shares.error = "";
                } catch (_error) {
                    state.shares.error = "Could not copy the share link. Select the link field manually, then copy it.";
                    state.shares.notice = "";
                }
                setFocusTarget(`.js-share-copy[data-share-id="${share.id}"]`);
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
            canCreateCalendarSubscription,
            closeCalendarShareModal,
            openCalendarShareModal,
            openCalendarSubscriptionModal,
            renderCalendarShareModal,
        };
    }

    window.APStudyCalendarShare = { createCalendarShare };
})();
