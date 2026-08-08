import("/static/js/core/cookie-consent.js").catch((error) => {
    console.error("Unable to initialize cookie consent", error);
});
import("/static/js/core/viewport-sizing.js")
    .then(({ installViewportSizing }) => installViewportSizing())
    .catch((error) => console.error("Unable to initialize viewport sizing", error));

(() => {
    if (window.__apstudyCsrfFetchInstalled || typeof window.fetch !== "function") return;
    window.__apstudyCsrfFetchInstalled = true;
    const nativeFetch = window.fetch.bind(window);
    const unsafeMethods = new Set(["POST", "PUT", "PATCH", "DELETE"]);
    const csrfFailureHeader = "X-APStudy-CSRF-Error";
    let csrfRefreshPromise = null;

    function csrfToken() {
        const entry = document.cookie
            .split(";")
            .map((item) => item.trim())
            .find((item) => item.startsWith("csrf_token="));
        return entry ? decodeURIComponent(entry.slice("csrf_token=".length)) : "";
    }

    function requestWithCsrfHeader(input, init = {}) {
        const request = input instanceof Request ? input : null;
        const headers = new Headers(request?.headers || undefined);
        new Headers(init.headers || undefined).forEach((value, key) => headers.set(key, value));
        const token = csrfToken();
        if (token && !headers.has("X-CSRFToken")) headers.set("X-CSRFToken", token);
        return new Request(request ? request.clone() : input, { ...init, headers });
    }

    async function refreshCsrfToken() {
        if (!csrfRefreshPromise) {
            csrfRefreshPromise = nativeFetch("/auth/csrf", {
                credentials: "same-origin",
                cache: "no-store",
                headers: { Accept: "application/json" },
            }).then((response) => {
                if (!response.ok || !csrfToken()) {
                    throw new Error("Unable to refresh CSRF token");
                }
            }).finally(() => {
                csrfRefreshPromise = null;
            });
        }
        return csrfRefreshPromise;
    }

    function isCsrfFailure(response) {
        return response.status === 400 && response.headers.get(csrfFailureHeader) === "1";
    }

    window.fetch = async (input, init = {}) => {
        const request = input instanceof Request ? input : null;
        const method = String(init.method || request?.method || "GET").toUpperCase();
        const url = new URL(request?.url || String(input), window.location.href);
        if (!unsafeMethods.has(method) || url.origin !== window.location.origin) {
            return nativeFetch(input, init);
        }

        const firstRequest = requestWithCsrfHeader(input, init);
        const response = await nativeFetch(firstRequest.clone());
        if (!isCsrfFailure(response)) return response;

        await refreshCsrfToken();
        const retryHeaders = new Headers(firstRequest.headers);
        const token = csrfToken();
        if (token) retryHeaders.set("X-CSRFToken", token);
        const retryRequest = new Request(firstRequest.clone(), { headers: retryHeaders });
        return nativeFetch(retryRequest);
    };
})();

(() => {
    if (window.APStudyPendingMutations) return;

    const activeTokens = new Set();

    function notify() {
        document.documentElement.toggleAttribute("data-pending-save", activeTokens.size > 0);
        window.dispatchEvent(new CustomEvent("apstudy-pending-save-change", {
            detail: { pending: activeTokens.size },
        }));
    }

    function begin(label = "save") {
        const token = { label, startedAt: Date.now() };
        activeTokens.add(token);
        notify();
        let ended = false;
        return () => {
            if (ended) return;
            ended = true;
            activeTokens.delete(token);
            notify();
        };
    }

    function track(promise, label = "save") {
        const end = begin(label);
        return Promise.resolve(promise).finally(end);
    }

    window.APStudyPendingMutations = {
        begin,
        track,
        hasPending: () => activeTokens.size > 0,
        count: () => activeTokens.size,
    };

    window.addEventListener("beforeunload", (event) => {
        if (!activeTokens.size) return;
        event.preventDefault();
        event.returnValue = "";
    });
})();

(() => {
    if (window.APStudyAccessibility) return;

    const FOCUSABLE_SELECTOR = [
        "a[href]",
        "button:not([disabled])",
        "input:not([disabled]):not([type='hidden'])",
        "select:not([disabled])",
        "textarea:not([disabled])",
        "summary",
        "[contenteditable='true']",
        "[tabindex]:not([tabindex='-1'])",
    ].join(",");
    const modalRecords = new Map();
    const managedInertElements = new Set();
    let activeDialog = null;

    function isRendered(element) {
        if (!element?.isConnected || element.closest("[hidden]")) return false;
        const style = window.getComputedStyle(element);
        return style.display !== "none" && style.visibility !== "hidden";
    }

    function focusableElements(dialog) {
        return Array.from(dialog.querySelectorAll(FOCUSABLE_SELECTOR)).filter((element) => {
            if (!isRendered(element) || element.closest("[inert]")) return false;
            return element.getAttribute("aria-hidden") !== "true";
        });
    }

    function activateDialog(dialog, previousFocus = null) {
        if (!dialog || modalRecords.has(dialog)) return;
        const candidate = previousFocus && !dialog.contains(previousFocus)
            ? previousFocus
            : document.activeElement && !dialog.contains(document.activeElement)
                ? document.activeElement
                : null;
        modalRecords.set(dialog, { previousFocus: candidate });
        if (!dialog.hasAttribute("tabindex")) dialog.setAttribute("tabindex", "-1");
    }

    function syncModalInertness(dialog) {
        for (const element of managedInertElements) element.removeAttribute("inert");
        managedInertElements.clear();
        if (!dialog) return;

        let branch = dialog;
        while (branch?.parentElement) {
            const parent = branch.parentElement;
            for (const sibling of parent.children) {
                if (sibling === branch || sibling.hasAttribute("inert")) continue;
                if (sibling.matches("[data-event-close], [data-discord-modal-close], .calendar-event-backdrop, [class*='backdrop']")) continue;
                sibling.setAttribute("inert", "");
                managedInertElements.add(sibling);
            }
            if (parent === document.body) break;
            branch = parent;
        }
    }

    function syncDialogs() {
        const visible = Array.from(document.querySelectorAll('[role="dialog"][aria-modal="true"]')).filter(isRendered);
        visible.forEach((dialog) => activateDialog(dialog));

        for (const [dialog, record] of modalRecords) {
            if (visible.includes(dialog)) continue;
            modalRecords.delete(dialog);
            const focused = document.activeElement;
            if ((!focused || focused === document.body || dialog.contains(focused)) && record.previousFocus?.isConnected) {
                record.previousFocus.focus?.({ preventScroll: true });
            }
        }
        activeDialog = visible.at(-1) || null;
        syncModalInertness(activeDialog);
    }

    function installSkipLink() {
        const main = document.querySelector("main");
        if (!main || document.querySelector(".apstudy-skip-link")) return;
        if (!main.id) main.id = "main-content";
        if (!main.hasAttribute("tabindex")) main.setAttribute("tabindex", "-1");
        const link = document.createElement("a");
        link.className = "apstudy-skip-link";
        link.href = `#${main.id}`;
        link.textContent = "Skip to main content";
        document.body.prepend(link);
    }

    document.addEventListener("focusin", (event) => {
        const dialog = event.target?.closest?.('[role="dialog"][aria-modal="true"]');
        if (dialog && isRendered(dialog)) {
            activateDialog(dialog, event.relatedTarget);
            activeDialog = dialog;
            return;
        }
        if (activeDialog?.matches('[role="dialog"][aria-modal="true"]') && isRendered(activeDialog)) {
            const target = focusableElements(activeDialog)[0] || activeDialog;
            target.focus?.({ preventScroll: true });
        }
    }, true);

    document.addEventListener("keydown", (event) => {
        const menu = event.target?.closest?.('[role="menu"]');
        if (menu && ["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
            const items = Array.from(menu.querySelectorAll('[role^="menuitem"]:not([disabled])')).filter(isRendered);
            if (items.length) {
                event.preventDefault();
                const current = items.indexOf(document.activeElement);
                if (event.key === "Home") items[0].focus();
                else if (event.key === "End") items.at(-1).focus();
                else if (event.key === "ArrowDown") items[(current + 1 + items.length) % items.length].focus();
                else items[(current - 1 + items.length) % items.length].focus();
            }
            return;
        }
        if (event.key !== "Tab" || !activeDialog?.matches('[role="dialog"][aria-modal="true"]') || !isRendered(activeDialog)) return;
        const focusable = focusableElements(activeDialog);
        if (!focusable.length) {
            event.preventDefault();
            activeDialog.focus({ preventScroll: true });
            return;
        }
        const first = focusable[0];
        const last = focusable.at(-1);
        if (event.shiftKey && (document.activeElement === first || !activeDialog.contains(document.activeElement))) {
            event.preventDefault();
            last.focus({ preventScroll: true });
        } else if (!event.shiftKey && (document.activeElement === last || !activeDialog.contains(document.activeElement))) {
            event.preventDefault();
            first.focus({ preventScroll: true });
        }
    }, true);

    const start = () => {
        installSkipLink();
        syncDialogs();
        new MutationObserver(syncDialogs).observe(document.body, {
            attributes: true,
            attributeFilter: ["hidden", "style", "class", "aria-modal", "role"],
            childList: true,
            subtree: true,
        });
    };
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start, { once: true });
    else start();

    window.APStudyAccessibility = {
        prefersReducedMotion: () => window.matchMedia("(prefers-reduced-motion: reduce)").matches,
        syncDialogs,
    };
})();

(() => {
    const pad2 = (value) => String(value).padStart(2, "0");

    function toLocalInputValue(date) {
        if (!(date instanceof Date) || Number.isNaN(date.getTime())) return "";
        return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())}T${pad2(date.getHours())}:${pad2(date.getMinutes())}`;
    }

    function isoToLocalInput(value) {
        if (!value) return "";
        const date = new Date(value);
        return Number.isNaN(date.getTime()) ? "" : toLocalInputValue(date);
    }

    function localInputToIso(value) {
        if (!value) return null;
        const date = new Date(value);
        return Number.isNaN(date.getTime()) ? null : date.toISOString();
    }

    window.APStudyDate = {
        toLocalInputValue,
        isoToLocalInput,
        localInputToIso,
    };
})();

function clearClientState(options = {}) {
    const includeCookies = options.includeCookies !== false;
    try {
        sessionStorage.clear();
    } catch (error) {
        console.warn("Failed to clear session storage", error);
    }

    try {
        localStorage.clear();
    } catch (error) {
        console.warn("Failed to clear local storage", error);
    }

    try {
        if (window.indexedDB?.deleteDatabase) {
            window.indexedDB.deleteDatabase("apstudy-chat-cache");
        }
    } catch (error) {
        console.warn("Failed to clear chat cache", error);
    }

    if (includeCookies) {
        document.cookie.split(";").forEach((cookie) => {
            const name = cookie.split("=")[0].trim();
            if (!name) {
                return;
            }
            document.cookie = `${name}=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/`;
        });
    }
}

function markClientLoggedOut() {
    try {
        sessionStorage.setItem("apstudy-logged-out", "true");
    } catch (error) {
        console.warn("Failed to mark logged out", error);
    }
}

function shouldEnforceAuth() {
    if (window.location.pathname === "/login") {
        return false;
    }
    const nav = document.querySelector("global.thenav");
    if (nav && nav.hasAttribute("data-profile-picture")) {
        return true;
    }
    return document.body?.dataset?.requiresAuth === "true";
}

async function ensureAppwriteSession() {
    if (document.body?.dataset?.probeAppwriteSession !== "true" || !shouldEnforceAuth()) return;
    if (typeof window.APStudyAppwriteSessionProbe !== "function") return;
    await window.APStudyAppwriteSessionProbe();
}

async function runLogoutFlow() {
    window.APStudyPresenceHeartbeat?.stop?.();
    try {
        await window.APStudyNotifications?.disableCurrent?.();
    } catch (error) {
        console.warn('Unable to revoke this browser notification subscription during logout.', error);
    }
    if (window.account && typeof account.deleteSession === "function") {
        try {
            await account.deleteSession("current");
        } catch (error) {
            console.warn("Failed to clear Appwrite session", error);
        }
    }

    clearClientState({ includeCookies: false });
    markClientLoggedOut();
    try {
        const response = await fetch("/logout", {
            method: "POST",
            credentials: "same-origin",
        });
        if (!response.ok) throw new Error("Logout failed");
        window.location.assign(`${window.location.origin}/login`);
    } catch (error) {
        console.error(error);
        window.APStudyToast?.show?.({
            title: "Couldn’t log out",
            message: "Refresh the page and try again.",
            type: "error",
        });
    }
}

function drainServerToasts() {
    if (window.__apstudyToastsDrained) return;
    window.__apstudyToastsDrained = true;
    if (!window.APStudyToast || typeof fetch !== "function") return;

    const toastRequest = {
        method: "GET",
        headers: { Accept: "application/json" },
    };
    toastRequest["credentials"] = "same-origin";
    fetch("/api/toasts", toastRequest)
        .then((response) => (response.ok ? response.json() : null))
        .then((payload) => {
            const toasts = Array.isArray(payload)
                ? payload
                : Array.isArray(payload?.toasts)
                  ? payload.toasts
                  : [];
            toasts.forEach((entry) => {
                if (!entry || typeof entry !== "object") return;
                window.APStudyToast.show({
                    message: entry.message,
                    title: entry.title,
                    type: entry.type,
                    duration: entry.duration,
                    action: entry.action,
                });
            });
        })
        .catch(() => {});
}

window.APStudyHttp = window.APStudyHttp || {
    async fetchJson(url, options = {}) {
        const {
            errorFactory = null,
            jsonMode = "content-type",
            pendingLabel = null,
            ...requestOptions
        } = options;
        const headers = { ...(requestOptions.headers || {}) };
        if (requestOptions.body && !headers["Content-Type"]) {
            headers["Content-Type"] = "application/json";
        }
        const method = String(requestOptions.method || "GET").toUpperCase();
        let request = fetch(url, { ...requestOptions, headers });
        if (method !== "GET" && pendingLabel && window.APStudyPendingMutations?.track) {
            request = window.APStudyPendingMutations.track(request, pendingLabel);
        }
        const response = await request;
        const contentType = response.headers.get("Content-Type") || "";
        const payload = jsonMode === "required"
            ? await response.json()
            : jsonMode === "optional"
              ? await response.json().catch(() => ({}))
              : contentType.includes("application/json")
                ? await response.json()
                : null;
        if (!response.ok) {
            if (typeof errorFactory === "function") {
                throw errorFactory(payload, response);
            }
            const message = payload?.error || payload?.message || response.statusText || "Request failed.";
            const error = new Error(message);
            if (payload && typeof payload === "object") {
                ["code", "resource", "limit", "current", "requested"].forEach((key) => {
                    if (payload[key] != null) error[key] = payload[key];
                });
            }
            throw error;
        }
        return payload ?? {};
    },
};

function initializePresenceHeartbeat() {
    if (window.APStudyPresenceHeartbeatStarted) return;
    const nav = document.querySelector("global.thenav[data-user-email]");
    if (!nav || typeof fetch !== "function") return;
    window.APStudyPresenceHeartbeatStarted = true;

    const tabKey = "apstudy-presence-tab-id";
    const siteHeartbeatMs = 60000;
    const chatHeartbeatMs = 5000;
    const chatRoomScopeKey = "chat-room";
    const extraScopes = new Map();
    let intervalId = null;
    let stopped = false;
    let focusPaused = false;
    let tabId = "";
    try {
        tabId = sessionStorage.getItem(tabKey) || "";
    } catch (_) {
        tabId = "";
    }
    if (!tabId) {
        const random = window.crypto?.randomUUID?.() || `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
        tabId = random.replace(/[^A-Za-z0-9_-]/g, "").slice(0, 64);
        try {
            sessionStorage.setItem(tabKey, tabId);
        } catch (_) {
            // In private or locked-down contexts, a memory-only tab id is fine.
        }
    }

    function isChatPage() {
        return window.location.pathname === "/chat";
    }

    function heartbeatIntervalMs() {
        return isChatPage() ? chatHeartbeatMs : siteHeartbeatMs;
    }

    function pageScopes() {
        if (!isChatPage()) return [{ scope_type: "site", scope_id: "global" }];
        const scopes = [{ scope_type: "chat", scope_id: "global" }];
        for (const scope of extraScopes.values()) {
            if (scope?.scope_type && scope?.scope_id) scopes.push(scope);
        }
        return scopes;
    }

    function postHeartbeat(scope, keepalive) {
        if (stopped) return Promise.resolve();
        const body = JSON.stringify({ ...scope, tab_id: tabId });
        return fetch("/api/presence/heartbeat", {
            method: "POST",
            headers: { "Content-Type": "application/json", Accept: "application/json" },
            body,
            credentials: "same-origin",
            keepalive,
        }).catch(() => {});
    }

    function sendHeartbeat({ keepalive = false } = {}) {
        if (stopped) return Promise.resolve([]);
        return Promise.all(pageScopes().map((scope) => postHeartbeat(scope, keepalive)));
    }

    function startTimer() {
        if (intervalId) window.clearInterval(intervalId);
        intervalId = window.setInterval(sendHeartbeat, heartbeatIntervalMs());
    }

    function stopTimer() {
        if (!intervalId) return;
        window.clearInterval(intervalId);
        intervalId = null;
    }

    function stopHeartbeat() {
        stopped = true;
        stopTimer();
        extraScopes.clear();
    }

    function pauseHeartbeat() {
        void sendHeartbeat({ keepalive: true });
        stopTimer();
    }

    function resumeHeartbeat() {
        if (focusPaused) return;
        void sendHeartbeat();
        startTimer();
    }

    function setFocusPaused(active) {
        focusPaused = active === true;
        if (focusPaused) pauseHeartbeat();
        else resumeHeartbeat();
    }

    function setChatRoom(roomId) {
        const scopeId = String(roomId || "").trim();
        const previous = extraScopes.get(chatRoomScopeKey)?.scope_id || "";
        if (scopeId) {
            extraScopes.set(chatRoomScopeKey, { scope_type: "chat", scope_id: scopeId });
        } else {
            extraScopes.delete(chatRoomScopeKey);
        }
        if (isChatPage() && previous !== scopeId) void sendHeartbeat();
    }

    focusPaused = document.body.classList.contains("focus-mode-active");
    if (!focusPaused) {
        sendHeartbeat();
        startTimer();
    }
    window.addEventListener("apstudy:focus-state", (event) => {
        setFocusPaused(event.detail?.active === true);
    });
    document.addEventListener("visibilitychange", () => sendHeartbeat({ keepalive: true }));
    if (window.APStudyPageLifecycle?.register) {
        window.APStudyPageLifecycle.register({
            pause: pauseHeartbeat,
            resume: resumeHeartbeat,
            dispose: pauseHeartbeat,
        });
    } else {
        window.addEventListener("pagehide", pauseHeartbeat, { once: true });
    }
    window.APStudyPresenceHeartbeat = {
        send: sendHeartbeat,
        setChatRoom,
        clearChatRoom: () => setChatRoom(null),
        pause: pauseHeartbeat,
        resume: resumeHeartbeat,
        stop: stopHeartbeat,
        tabId,
        siteHeartbeatMs,
        chatHeartbeatMs,
    };
}

function initializeGlobalChrome() {
    ensureAppwriteSession();
    drainServerToasts();
    initializePresenceHeartbeat();
    initializeTierBadgeTooltips();

    // Bind any links/buttons that request logout
    const logoutLinks = document.querySelectorAll("[data-logout]");
    if (logoutLinks.length) {
        logoutLinks.forEach((link) => {
            link.addEventListener("click", (event) => {
                event.preventDefault();
                runLogoutFlow();
            });
        });
    }

    // Render footer
    const footer = document.querySelector("global.thefooter");
    if (footer) {
        footer.innerHTML = `
<footer class="bg-surface w-full py-12 border-t border-outline-variant/30">
    <div class="flex flex-col md:flex-row justify-between items-center px-12 max-w-7xl mx-auto">
        <span class="font-body text-xs uppercase tracking-[0.05em] font-normal text-on-surface-variant">© 2026 Nest.APStudy.org. Your work, your space, your nest.</span>
        <div class="flex flex-wrap justify-center gap-6 mt-4 md:mt-0">
            <a class="font-body text-xs uppercase tracking-[0.05em] font-normal text-on-surface-variant hover:text-primary transition-colors" href="mailto:derek.chen@emory.edu">Support</a>
            <a class="font-body text-xs uppercase tracking-[0.05em] font-normal text-on-surface-variant hover:text-primary transition-colors" href="/privacy-policy">Privacy</a>
            <a class="font-body text-xs uppercase tracking-[0.05em] font-normal text-on-surface-variant hover:text-primary transition-colors" href="/terms-of-service">Terms</a>
        </div>
    </div>
</footer>
`;
    }
}

function initializeTierBadgeTooltips() {
    const interactiveBadges = () => [...document.querySelectorAll('.tier-badge-trigger:not([aria-hidden="true"])')];
    const closeBadges = (except = null) => {
        interactiveBadges().forEach((badge) => {
            if (badge === except) return;
            badge.classList.remove('is-tooltip-open');
            badge.setAttribute('aria-expanded', 'false');
        });
    };

    interactiveBadges().forEach((badge) => {
        badge.setAttribute('role', 'button');
        badge.setAttribute('aria-expanded', 'false');
    });

    document.addEventListener('click', (event) => {
        const badge = event.target.closest('.tier-badge-trigger:not([aria-hidden="true"])');
        if (!badge) {
            closeBadges();
            return;
        }

        const willOpen = !badge.classList.contains('is-tooltip-open');
        closeBadges(badge);
        badge.classList.toggle('is-tooltip-open', willOpen);
        badge.setAttribute('aria-expanded', String(willOpen));
    });

    document.addEventListener('keydown', (event) => {
        const badge = event.target.closest?.('.tier-badge-trigger:not([aria-hidden="true"])');
        if (badge && (event.key === 'Enter' || event.key === ' ')) {
            event.preventDefault();
            badge.click();
        } else if (event.key === 'Escape') {
            closeBadges();
        }
    });
}

if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initializeGlobalChrome);
} else {
    initializeGlobalChrome();
}
