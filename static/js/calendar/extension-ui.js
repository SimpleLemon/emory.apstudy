import {
    getCalendarCapabilityData,
    getSafeCanvasSourceUrl,
    normalizeCalendarCapabilities,
    normalizeWritebackState,
    writebackStateLabel,
} from "./capabilities.js";

function escapeHtml(value) {
    return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#39;");
}

function firstValue(...values) {
    return values.find((value) => value !== null && value !== undefined && String(value).trim() !== "") || "";
}

function completionLabel(value) {
    const normalized = String(value || "").trim().toLowerCase();
    if (["completed", "complete", "done", "graded", "submitted"].includes(normalized)) return "Completed";
    if (normalized === "incomplete") return "Incomplete";
    return value ? String(value) : "Not reported";
}

function provenanceLabel(value) {
    const normalized = String(value || "").trim().toLowerCase();
    if (normalized === "canvas") return "Canvas reported";
    if (normalized === "extension") return "Extension reported";
    return value ? String(value) : "Not reported";
}

function actionReason(enabled, capabilityEnabled, methodAvailable, missingData, label) {
    if (enabled) return "";
    if (missingData) return `${label} is unavailable because the Canvas identity is incomplete.`;
    if (!capabilityEnabled) return `${label} is disabled because this capability was not granted.`;
    if (!methodAvailable) return `${label} is unavailable because the adapter does not expose it.`;
    return `${label} is unavailable in this mode.`;
}

function actionButton({ action, label, enabled, reason, hidden = false }) {
    if (hidden) return "";
    const title = reason || label;
    return `<button type="button" class="calendar-extension-action" data-calendar-extension-action="${escapeHtml(action)}"${enabled ? "" : " disabled"} aria-label="${escapeHtml(label)}" title="${escapeHtml(title)}">${escapeHtml(label)}</button>`;
}

function actionCapability(action) {
    if (action === "open-source") return "openSourceUrl";
    if (action === "route-display" || action === "display-override") return "routeDisplayOverride";
    if (/^retry-writeback:\d+$/.test(action)) return "retryWriteback";
    return null;
}

function isMutationAction(action) {
    return action === "route-display"
        || action === "display-override"
        || /^retry-writeback:\d+$/.test(action);
}

export function getCalendarExtensionActionAvailability({ capabilities, adapter, data }) {
    const normalized = normalizeCalendarCapabilities(capabilities);
    const sourceUrl = getSafeCanvasSourceUrl(data?.source?.url);
    const sourceId = firstValue(data?.source?.sourceId, data?.source?.source_id, data?.firstEvent?.source_id);
    const eventRef = firstValue(data?.firstEvent?.event_ref, data?.firstEvent?.id);
    const destination = firstValue(
        data?.routing?.destinationCalendarId,
        data?.routing?.destination_calendar_id,
        data?.routing?.destination,
        data?.firstEvent?.calendar_id,
    );
    const routeMethod = typeof adapter?.setCanvasRouting === "function";
    const displayOverrideMethod = typeof adapter?.setDisplayOverride === "function";
    const routeTargetAvailable = routeMethod ? Boolean(sourceId) : Boolean(eventRef);
    const routeEnabled = Boolean(
        !normalized.readOnly
        && normalized.actions.routeDisplayOverride
        && (routeMethod || displayOverrideMethod)
        && adapter?.actionSupport?.routeDisplayOverride !== false
        && routeTargetAvailable
        && destination,
    );
    const retryEnabled = Boolean(
        !normalized.readOnly
        && normalized.actions.retryWriteback
        && typeof adapter?.retryWriteback === "function"
        && adapter?.actionSupport?.retryWriteback !== false,
    );
    const openSourceEnabled = Boolean(
        normalized.actions.openSourceUrl
        && typeof adapter?.openSafeSourceUrl === "function"
        && adapter?.actionSupport?.openSourceUrl !== false
        && sourceUrl,
    );
    return {
        sourceUrl,
        sourceId,
        eventRef,
        destination,
        routeMethod,
        displayOverrideMethod,
        routeEnabled,
        retryEnabled,
        openSourceEnabled,
        reasons: {
            route: actionReason(
                routeEnabled,
                normalized.actions.routeDisplayOverride,
                routeMethod || displayOverrideMethod,
                !routeTargetAvailable || !destination,
                "Display routing",
            ),
            retry: actionReason(
                retryEnabled,
                normalized.actions.retryWriteback,
                typeof adapter?.retryWriteback === "function",
                false,
                "Writeback retry",
            ),
            openSource: actionReason(
                openSourceEnabled,
                normalized.actions.openSourceUrl,
                typeof adapter?.openSafeSourceUrl === "function",
                !sourceUrl,
                "Open Canvas",
            ),
        },
    };
}

export function createCalendarExtensionUi({
    root,
    state,
    adapter = {},
    capabilities = {},
    lifecycle = null,
} = {}) {
    const doc = root?.ownerDocument;
    const normalizedCapabilities = normalizeCalendarCapabilities(capabilities);
    const existingPanel = root?.querySelector?.("[data-calendar-extension-status]");
    const panel = existingPanel || doc?.createElement?.("section");
    if (!panel || typeof root?.appendChild !== "function") return { dispose() {}, render() {} };

    const createdPanel = !existingPanel;
    let disposed = false;
    let notice = "";
    let clickHandler = null;
    let clickHandlerActive = false;
    const panelId = "calendar-extension-status-title";
    if (createdPanel) {
        panel.className = "calendar-extension-status";
        panel.setAttribute("data-calendar-extension-status", "true");
        panel.setAttribute("aria-labelledby", panelId);
        root.appendChild(panel);
        lifecycle?.trackNode?.(panel);
    }

    function getData() {
        return getCalendarCapabilityData(normalizedCapabilities, state?.events || []);
    }

    function statusText(data) {
        if (notice) return notice;
        if (normalizedCapabilities.readOnly || normalizedCapabilities.shareMode) {
            return "Read-only calendar: Canvas mutation controls are hidden.";
        }
        if (!normalizedCapabilities.supported) {
            return "Canvas extension actions are unavailable for this contract version.";
        }
        if (data.routing?.degraded === true || data.firstEvent?.routing_degraded === true) {
            return "Canvas events are visible, but display routing is degraded.";
        }
        return "Canvas event provenance and integration state are shown below.";
    }

    function render() {
        if (disposed) return;
        const data = getData();
        const hasCanvasData = Object.keys(normalizedCapabilities.data).length > 0
            || Boolean(data.firstEvent?.event_ref)
            || Boolean(data.source?.sourceId || data.source?.source_id);
        panel.hidden = !hasCanvasData;
        if (!hasCanvasData) {
            panel.innerHTML = "";
            return;
        }
        const availability = getCalendarExtensionActionAvailability({
            capabilities: normalizedCapabilities,
            adapter,
            data,
        });
        const sourceLabel = firstValue(data.source?.label, data.source?.accountLabel, "Canvas source");
        const accountLabel = firstValue(data.source?.accountLabel, data.source?.account_label, sourceLabel);
        const destination = availability.destination || "Not assigned";
        const routingLabel = data.routing?.degraded === true || data.firstEvent?.routing_degraded === true
            ? `Degraded; displaying in ${destination}`
            : `Displaying in ${destination}`;
        const displayOverride = data.routing?.displayOverride === true || data.firstEvent?.has_override === true;
        const writebacks = Array.isArray(data.writebacks) ? data.writebacks : [];
        const writebackMarkup = writebacks.length
            ? writebacks.map((writeback, index) => {
                const stateValue = normalizeWritebackState(writeback?.state || writeback?.status || writeback?.mirror_state);
                const retryLabel = `Retry ${writebackStateLabel(stateValue).toLowerCase()} writeback`;
                const retryReason = availability.retryEnabled
                    ? "Retry this Canvas writeback"
                    : availability.reasons.retry;
                return `<li class="calendar-extension-state calendar-extension-state--${escapeHtml(stateValue)}">
                    <span class="calendar-extension-state-label">${escapeHtml(writebackStateLabel(stateValue))}</span>
                    <span class="calendar-extension-state-detail">${escapeHtml(writeback?.error_message || writeback?.errorMessage || "Canvas mirror state")}</span>
                    ${stateValue === "retryable_failed" ? actionButton({
                        action: `retry-writeback:${index}`,
                        label: retryLabel,
                        enabled: availability.retryEnabled,
                        reason: retryReason,
                        hidden: normalizedCapabilities.readOnly,
                    }) : ""}
                </li>`;
            }).join("")
            : `<li class="calendar-extension-state calendar-extension-state--unsupported"><span class="calendar-extension-state-label">No writeback state reported</span><span class="calendar-extension-state-detail">The adapter did not provide a mirror result.</span></li>`;
        const openSourceReason = availability.reasons.openSource || "Open the safe Canvas source origin";
        const routeAction = availability.routeMethod ? "route-display" : "display-override";
        const routeLabel = availability.routeMethod ? "Apply Canvas display route" : "Apply display override";
        const routeReason = availability.reasons.route || routeLabel;

        panel.innerHTML = `<div class="calendar-extension-status-header">
            <div>
                <p class="calendar-extension-eyebrow">Canvas integration</p>
                <h2 id="${panelId}" class="calendar-extension-title">${escapeHtml(sourceLabel)}</h2>
                <p class="calendar-extension-account">Account: ${escapeHtml(accountLabel)}</p>
            </div>
            <div class="calendar-extension-actions">
                ${actionButton({
                    action: "open-source",
                    label: "Open Canvas",
                    enabled: availability.openSourceEnabled,
                    reason: openSourceReason,
                })}
                ${actionButton({
                    action: routeAction,
                    label: routeLabel,
                    enabled: availability.routeEnabled,
                    reason: routeReason,
                    hidden: normalizedCapabilities.readOnly,
                })}
            </div>
        </div>
        <p class="calendar-extension-status-text" role="status" aria-live="polite">${escapeHtml(statusText(data))}</p>
        <dl class="calendar-extension-facts">
            <div><dt>Completion</dt><dd>${escapeHtml(completionLabel(data.completion?.status))}</dd></div>
            <div><dt>Provenance</dt><dd>${escapeHtml(provenanceLabel(data.completion?.source))}</dd></div>
            <div><dt>Routing</dt><dd>${escapeHtml(routingLabel)}</dd></div>
            <div><dt>Display override</dt><dd>${displayOverride ? "Applied" : "Not applied"}</dd></div>
        </dl>
        <div class="calendar-extension-writebacks">
            <h3>Writeback and mirror state</h3>
            <ul aria-label="Canvas writeback and mirror states">${writebackMarkup}</ul>
        </div>
        <p class="calendar-extension-source-note">Source URL actions only accept a credential-free HTTPS Canvas origin.</p>`;
    }

    async function runAdapterAction(action, data) {
        if (disposed) return;
        const capabilityName = actionCapability(action);
        const dispatchCapabilities = normalizeCalendarCapabilities(capabilities);
        const capabilityGranted = capabilityName !== null
            && dispatchCapabilities.actions[capabilityName] === true;
        const writableDispatch = dispatchCapabilities.readOnlyValid === true
            && dispatchCapabilities.readOnly === false
            && dispatchCapabilities.shareModeValid === true
            && dispatchCapabilities.shareMode === false;
        if (!capabilityGranted || (isMutationAction(action) && !writableDispatch)) return;
        if (action.startsWith("retry-writeback:")) {
            const index = Number(action.split(":")[1]);
            if (!Number.isSafeInteger(index) || index < 0 || index >= data.writebacks.length) return;
        }
        const controller = lifecycle?.trackAbortController?.();
        const signal = controller?.signal;
        try {
            let result;
            if (action === "open-source") {
                result = await adapter.openSafeSourceUrl({ url: getSafeCanvasSourceUrl(data.source?.url), signal });
            } else if (action === "route-display") {
                const route = data.routing || {};
                result = await adapter.setCanvasRouting({
                    sourceId: firstValue(route.sourceId, route.source_id, data.source?.sourceId, data.firstEvent?.source_id),
                    state: firstValue(route.state, data.completion?.status === "completed" ? "completed" : "incomplete"),
                    destinationCalendarId: firstValue(route.destinationCalendarId, route.destination_calendar_id, route.destination, data.firstEvent?.calendar_id),
                    fallbackCalendarId: firstValue(route.fallbackCalendarId, route.fallback_calendar_id),
                    signal,
                });
            } else if (action === "display-override") {
                result = await adapter.setDisplayOverride({
                    eventRef: firstValue(data.firstEvent?.event_ref, data.firstEvent?.id),
                    calendarId: firstValue(data.routing?.destination, data.firstEvent?.calendar_id),
                    signal,
                });
            } else if (action.startsWith("retry-writeback:")) {
                const index = Number(action.split(":")[1]);
                result = await adapter.retryWriteback({ writeback: data.writebacks[index], signal });
            }
            if (result?.state === "unsupported" || result?.ok === false || result?.response?.ok === false) {
                notice = result?.reason || "The adapter does not support this Canvas action.";
            } else {
                notice = "Canvas action requested.";
            }
        } catch (error) {
            notice = error?.message || "Canvas action could not be completed.";
        } finally {
            lifecycle?.releaseAbortController?.(controller);
            render();
        }
    }

    clickHandler = (event) => {
        const button = event.target?.closest?.("[data-calendar-extension-action]");
        if (!button || button.disabled) return;
        const action = button.getAttribute("data-calendar-extension-action");
        if (!action) return;
        event.preventDefault();
        void runAdapterAction(action, getData());
    };
    if (typeof panel.addEventListener === "function") {
        panel.addEventListener("click", clickHandler);
        clickHandlerActive = true;
    }
    const removeClickHandler = () => {
        if (!clickHandlerActive) return;
        clickHandlerActive = false;
        panel.removeEventListener?.("click", clickHandler);
    };
    const cleanupUi = () => {
        if (disposed) return;
        disposed = true;
        removeClickHandler();
        if (createdPanel) panel.remove?.();
    };
    lifecycle?.addCleanup?.(cleanupUi);

    render();
    return {
        dispose() {
            cleanupUi();
        },
        render,
    };
}
