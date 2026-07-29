(() => {
    if (window.APStudyCookieConsent) return;

    const STORAGE_KEY = "apstudy_cookie_consent";
    const POLICY_VERSION = 2;
    const MAX_AGE_MS = 183 * 24 * 60 * 60 * 1000;
    const ACCEPTED = "accepted";
    const REJECTED = "rejected";
    const MODE_AUTHENTICATED = "authenticated";
    const MODE_PUBLIC = "public-choice";
    const MODE_OFF = "off";
    const ANALYTICS_SCRIPT_ID = "apstudy-google-analytics";
    let analyticsLoaded = false;
    let preferencesOpen = false;
    let previousFocus = null;

    function analyticsMode() {
        const value = document.body?.dataset?.analyticsMode || MODE_OFF;
        return [MODE_AUTHENTICATED, MODE_PUBLIC, MODE_OFF].includes(value) ? value : MODE_OFF;
    }

    function analyticsId() {
        return document.body?.dataset?.analyticsMeasurementId || "";
    }

    function readStoredDecision() {
        try {
            const parsed = JSON.parse(window.localStorage.getItem(STORAGE_KEY) || "null");
            const decidedAt = Date.parse(parsed?.decidedAt || "");
            const age = Date.now() - decidedAt;
            if (parsed?.version !== POLICY_VERSION) return null;
            if (![ACCEPTED, REJECTED].includes(parsed?.choice)) return null;
            if (!Number.isFinite(decidedAt) || age < 0 || age > MAX_AGE_MS) return null;
            return parsed;
        } catch (_error) {
            return null;
        }
    }

    function storeDecision(choice) {
        const decision = {
            version: POLICY_VERSION,
            choice,
            decidedAt: new Date().toISOString(),
        };
        try {
            window.localStorage.setItem(STORAGE_KEY, JSON.stringify(decision));
        } catch (_error) {
            // The choice still applies to this page when browser storage is unavailable.
        }
        return decision;
    }

    function googleCookieNames() {
        return document.cookie
            .split(";")
            .map((part) => part.trim().split("=")[0])
            .filter((name) => name === "_ga" || name === "_gid" || name.startsWith("_ga_"));
    }

    function cookieDomains() {
        const hostname = window.location.hostname;
        const parts = hostname.split(".").filter(Boolean);
        const domains = ["", hostname, `.${hostname}`];
        if (parts.length > 2) domains.push(parts.slice(-2).join("."), `.${parts.slice(-2).join(".")}`);
        return [...new Set(domains)];
    }

    function clearAnalyticsCookies() {
        googleCookieNames().forEach((name) => {
            cookieDomains().forEach((domain) => {
                const domainAttribute = domain ? `; domain=${domain}` : "";
                document.cookie = `${name}=; Max-Age=0; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/${domainAttribute}; SameSite=Lax`;
            });
        });
    }

    function loadAnalytics() {
        const measurementId = analyticsId();
        if (!measurementId || analyticsLoaded || document.getElementById(ANALYTICS_SCRIPT_ID)) return;

        analyticsLoaded = true;
        window[`ga-disable-${measurementId}`] = false;
        window.dataLayer = window.dataLayer || [];
        window.gtag = window.gtag || function gtag() {
            window.dataLayer.push(arguments);
        };
        window.gtag("consent", "default", {
            analytics_storage: "granted",
            ad_storage: "denied",
            ad_user_data: "denied",
            ad_personalization: "denied",
        });
        window.gtag("js", new Date());
        window.gtag("config", measurementId, {
            allow_google_signals: false,
            allow_ad_personalization_signals: false,
        });

        const script = document.createElement("script");
        script.id = ANALYTICS_SCRIPT_ID;
        script.async = true;
        script.src = `https://www.googletagmanager.com/gtag/js?id=${encodeURIComponent(measurementId)}`;
        document.head.appendChild(script);
    }

    function removeAnalyticsRuntime() {
        const measurementId = analyticsId();
        if (measurementId) window[`ga-disable-${measurementId}`] = true;
        document.getElementById(ANALYTICS_SCRIPT_ID)?.remove();
        analyticsLoaded = false;
        clearAnalyticsCookies();
    }

    function ui() {
        return {
            dialog: document.getElementById("apstudy-consent-dialog"),
            settings: document.querySelector("[data-apstudy-consent-settings]"),
        };
    }

    function syncUi(decision = readStoredDecision()) {
        const { dialog, settings } = ui();
        const closeButton = dialog?.querySelector("[data-apstudy-consent-close]");
        if (closeButton) closeButton.hidden = false;
        if (settings) {
            settings.setAttribute("aria-expanded", String(preferencesOpen));
            settings.setAttribute("aria-label", preferencesOpen ? "Close cookie settings" : "Open cookie settings");
            settings.setAttribute("title", preferencesOpen ? "Close cookie settings" : "Open cookie settings");
        }
        document.querySelectorAll("[data-apstudy-consent-choice]").forEach((button) => {
            button.setAttribute("aria-pressed", String(button.dataset.apstudyConsentChoice === decision?.choice));
        });
    }

    function closePreferences() {
        const { dialog, settings } = ui();
        if (!dialog || !preferencesOpen) return;
        const returnFocus = previousFocus || settings;
        preferencesOpen = false;
        dialog.hidden = true;
        syncUi();
        returnFocus?.focus?.({ preventScroll: true });
        previousFocus = null;
    }

    function openPreferences({ focus = true } = {}) {
        const { dialog } = ui();
        if (!dialog || preferencesOpen) return;
        preferencesOpen = true;
        previousFocus = focus ? document.activeElement : null;
        dialog.hidden = false;
        syncUi();
        if (focus) dialog.querySelector("[data-apstudy-consent-choice]")?.focus();
    }

    function togglePreferences() {
        if (preferencesOpen) closePreferences();
        else openPreferences();
    }

    function setChoice(choice) {
        if (![ACCEPTED, REJECTED].includes(choice)) return;
        const hadAnalyticsRuntime = analyticsLoaded || Boolean(document.getElementById(ANALYTICS_SCRIPT_ID));
        const decision = storeDecision(choice);
        syncUi(decision);
        closePreferences();

        if (analyticsMode() === MODE_AUTHENTICATED || choice === ACCEPTED) {
            loadAnalytics();
        } else {
            removeAnalyticsRuntime();
            if (hadAnalyticsRuntime) {
                window.location.reload();
                return;
            }
        }
        window.dispatchEvent(new CustomEvent("apstudy-consent-change", { detail: decision }));
    }

    function renderPublicControls() {
        const root = document.createElement("div");
        root.id = "apstudy-consent-root";
        root.innerHTML = `
            <div class="apstudy-consent-stack">
                <div id="apstudy-consent-dialog" class="apstudy-consent-dialog" role="dialog" aria-labelledby="apstudy-consent-title" aria-describedby="apstudy-consent-description" hidden>
                    <div class="apstudy-consent-dialog__panel">
                        <button class="apstudy-consent-dialog__close" type="button" data-apstudy-consent-close aria-label="Close cookie settings">&times;</button>
                        <h2 id="apstudy-consent-title">Analytics preferences</h2>
                        <p id="apstudy-consent-description">Google Analytics may use cookies to help us understand how Nest is used. It is not required to use the site, and we do not sell your personal data.</p>
                        <div class="apstudy-consent-actions" aria-label="Analytics preference">
                            <button type="button" data-apstudy-consent-choice="rejected">Reject analytics</button>
                            <button type="button" data-apstudy-consent-choice="accepted">Allow analytics</button>
                        </div>
                        <a class="apstudy-consent-dialog__privacy" href="/privacy-policy#cookie-policy">Read the Privacy Policy</a>
                    </div>
                </div>
                <button class="apstudy-consent-settings" type="button" data-apstudy-consent-settings aria-haspopup="dialog" aria-expanded="false" aria-controls="apstudy-consent-dialog" aria-label="Open cookie settings" title="Open cookie settings">
                    <svg class="apstudy-consent-settings__icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
                        <path d="M20.75 12.3a8.75 8.75 0 1 1-9.05-9.05 3.35 3.35 0 0 0 4.05 4.05 3.35 3.35 0 0 0 4.05 4.05c.39.29.7.62.95.95Z" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" />
                        <circle cx="8.5" cy="12" r="1" fill="currentColor" />
                        <circle cx="12" cy="16" r="1" fill="currentColor" />
                        <circle cx="12" cy="8.5" r="1" fill="currentColor" />
                    </svg>
                    <span class="apstudy-consent-settings__label">Cookie settings</span>
                </button>
            </div>
        `;
        document.body.appendChild(root);

        root.addEventListener("click", (event) => {
            const choiceButton = event.target.closest?.("[data-apstudy-consent-choice]");
            if (choiceButton) {
                setChoice(choiceButton.dataset.apstudyConsentChoice);
                return;
            }
            if (event.target.closest?.("[data-apstudy-consent-settings]")) togglePreferences();
            if (event.target.closest?.("[data-apstudy-consent-close]")) closePreferences();
        });
        root.addEventListener("keydown", (event) => {
            if (event.key === "Escape") closePreferences();
        });
    }

    function initialize() {
        const mode = analyticsMode();
        if (mode === MODE_OFF) return;
        if (mode === MODE_AUTHENTICATED) {
            loadAnalytics();
            return;
        }

        renderPublicControls();
        const decision = readStoredDecision();
        syncUi(decision);
        if (decision?.choice === REJECTED) {
            removeAnalyticsRuntime();
        } else if (decision?.choice === ACCEPTED) {
            loadAnalytics();
        } else {
            openPreferences({ focus: false });
        }
    }

    window.APStudyCookieConsent = {
        openPreferences,
        closePreferences,
        getDecision: readStoredDecision,
        getMode: analyticsMode,
        setChoice,
        clearAnalyticsCookies,
        loadAnalytics,
        constants: {
            STORAGE_KEY, POLICY_VERSION, MAX_AGE_MS, ACCEPTED, REJECTED,
            MODE_AUTHENTICATED, MODE_PUBLIC, MODE_OFF,
        },
    };

    window.addEventListener("storage", (event) => {
        if (event.key !== STORAGE_KEY) return;
        const mode = analyticsMode();
        if (mode === MODE_AUTHENTICATED) {
            loadAnalytics();
            return;
        }
        if (mode !== MODE_PUBLIC) return;

        const decision = readStoredDecision();
        syncUi(decision);
        if (!decision) {
            openPreferences({ focus: false });
            removeAnalyticsRuntime();
            return;
        }
        if (decision?.choice === REJECTED) {
            const hadAnalyticsRuntime = analyticsLoaded || Boolean(document.getElementById(ANALYTICS_SCRIPT_ID));
            removeAnalyticsRuntime();
            if (hadAnalyticsRuntime) window.location.reload();
        } else {
            loadAnalytics();
        }
    });

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", initialize, { once: true });
    } else {
        initialize();
    }
})();
