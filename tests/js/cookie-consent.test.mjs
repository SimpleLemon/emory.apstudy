import assert from "node:assert/strict";
import test from "node:test";
import vm from "node:vm";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const source = await readFile(path.join(repoRoot, "static/js/core/cookie-consent.js"), "utf8");
const globalStyles = `${await readFile(path.join(repoRoot, "static/css/global.css"), "utf8")}\n${await readFile(path.join(repoRoot, "static/css/core/feedback-overlays.css"), "utf8")}`;

class FakeElement {
    constructor(tagName, document) {
        this.tagName = tagName.toUpperCase();
        this.ownerDocument = document;
        this.children = [];
        this.dataset = {};
        this.hidden = false;
        this.innerHTML = "";
        this.attributes = new Map();
        this.classList = { add() {}, remove() {} };
    }

    appendChild(child) {
        this.children.push(child);
        if (child.id) this.ownerDocument.elements.set(child.id, child);
        return child;
    }

    addEventListener() {}
    querySelector() { return null; }
    querySelectorAll() { return []; }
    setAttribute(name, value) { this.attributes.set(name, String(value)); }
    focus() {}

    remove() {
        if (this.id) this.ownerDocument.elements.delete(this.id);
        const parent = this.tagName === "SCRIPT" ? this.ownerDocument.head : this.ownerDocument.body;
        parent.children = parent.children.filter((child) => child !== this);
    }
}

function createHarness({ stored = null, cookie = "", dnt = "0", mode = "public-choice", measurementId = "G-0NT330ZX5L" } = {}) {
    const values = new Map();
    if (stored !== null) values.set("apstudy_cookie_consent", stored);
    const cookieWrites = [];
    const elements = new Map();
    const document = {
        readyState: "complete",
        elements,
        activeElement: null,
        createElement(tagName) { return new FakeElement(tagName, document); },
        getElementById(id) { return elements.get(id) || null; },
        querySelector() { return null; },
        querySelectorAll() { return []; },
        addEventListener() {},
    };
    document.head = new FakeElement("head", document);
    document.body = new FakeElement("body", document);
    document.body.dataset.analyticsMode = mode;
    document.body.dataset.analyticsMeasurementId = measurementId;
    Object.defineProperty(document, "cookie", {
        get: () => cookie,
        set: (value) => cookieWrites.push(value),
    });

    const listeners = new Map();
    let reloads = 0;
    const window = {
        document,
        navigator: { doNotTrack: dnt, globalPrivacyControl: false },
        localStorage: {
            getItem: (key) => values.get(key) ?? null,
            setItem: (key, value) => values.set(key, value),
        },
        location: {
            hostname: "nest.apstudy.org",
            reload: () => { reloads += 1; },
        },
        addEventListener: (name, handler) => listeners.set(name, handler),
        dispatchEvent() {},
    };
    const context = vm.createContext({
        window,
        document,
        navigator: window.navigator,
        console,
        Date,
        JSON,
        Number,
        CustomEvent: class CustomEvent { constructor(type, options) { this.type = type; this.detail = options?.detail; } },
    });
    vm.runInContext(source, context);
    return { window, document, values, cookieWrites, listeners, reloads: () => reloads };
}

function decision(choice, ageMs = 0) {
    return JSON.stringify({
        version: 2,
        choice,
        decidedAt: new Date(Date.now() - ageMs).toISOString(),
    });
}

function isConcreteFullPage(name, source) {
    return name !== "base.html" && (
        source.includes("<!DOCTYPE html>") || /\{%\s*extends\s+["']base\.html["']\s*%\}/.test(source)
    );
}

test("public analytics stays off until an explicit choice is stored", () => {
    const defaultHarness = createHarness();
    const malformedHarness = createHarness({ stored: "not-json" });
    const expiredHarness = createHarness({ stored: decision("accepted", 184 * 24 * 60 * 60 * 1000) });

    assert.equal(defaultHarness.document.head.children.length, 0);
    assert.equal(malformedHarness.document.head.children.length, 0);
    assert.equal(expiredHarness.document.head.children.length, 0);
    assert.equal(defaultHarness.document.body.children.length, 1);
    assert.equal(defaultHarness.window.APStudyCookieConsent.getDecision(), null);
});

test("acceptance persists and injects Google Analytics only once", () => {
    const harness = createHarness();
    harness.window.APStudyCookieConsent.setChoice("accepted");
    harness.window.APStudyCookieConsent.loadAnalytics();

    const saved = JSON.parse(harness.values.get("apstudy_cookie_consent"));
    assert.equal(saved.choice, "accepted");
    assert.equal(harness.document.head.children.length, 1);
    assert.equal(harness.document.head.children[0].id, "apstudy-google-analytics");
    assert.match(harness.document.head.children[0].src, /googletagmanager\.com\/gtag\/js/);
    assert.equal(harness.window.dataLayer.length, 3);
    assert.equal(harness.window.dataLayer[0][2].ad_storage, "denied");
    assert.equal(harness.window.dataLayer[2][2].allow_google_signals, false);
    assert.equal(harness.window.dataLayer[2][2].allow_ad_personalization_signals, false);
});

test("a saved acceptance loads analytics while rejection keeps it disabled", () => {
    const acceptedHarness = createHarness({ stored: decision("accepted") });
    const rejectedHarness = createHarness({ stored: decision("rejected") });

    assert.equal(acceptedHarness.document.head.children.length, 1);
    assert.equal(rejectedHarness.document.head.children.length, 0);
});

test("authenticated pages load analytics despite public rejection and render no controls", () => {
    const harness = createHarness({ mode: "authenticated", stored: decision("rejected") });

    assert.equal(harness.document.head.children.length, 1);
    assert.equal(harness.document.body.children.length, 0);
    assert.equal(harness.window.APStudyCookieConsent.getMode(), "authenticated");
});

test("off-mode pages neither load analytics nor render controls", () => {
    const harness = createHarness({ mode: "off", stored: decision("accepted") });

    assert.equal(harness.document.head.children.length, 0);
    assert.equal(harness.document.body.children.length, 0);
});

test("rejection persists, clears legacy Google cookies, and reloads after withdrawal", () => {
    const harness = createHarness({ cookie: "_ga=one; _ga_ABC=two; _gid=three; session=keep" });
    harness.window.APStudyCookieConsent.setChoice("accepted");
    harness.window.APStudyCookieConsent.setChoice("rejected");

    assert.equal(JSON.parse(harness.values.get("apstudy_cookie_consent")).choice, "rejected");
    assert.equal(harness.document.head.children.length, 0);
    assert.equal(harness.reloads(), 1);
    assert.ok(harness.cookieWrites.some((value) => value.startsWith("_ga=")));
    assert.ok(harness.cookieWrites.some((value) => value.startsWith("_ga_ABC=")));
    assert.ok(harness.cookieWrites.some((value) => value.startsWith("_gid=")));
    assert.ok(harness.cookieWrites.every((value) => !value.startsWith("session=")));
});

test("privacy signals do not replace the explicit public analytics choice", () => {
    const harness = createHarness({ dnt: "1" });
    assert.equal(harness.document.head.children.length, 0);
    assert.doesNotMatch(harness.document.body.children[0].innerHTML, /privacy signal/);

    harness.window.APStudyCookieConsent.setChoice("rejected");
    assert.equal(harness.document.head.children.length, 0);
});

test("accept and reject controls use the same action class and prominence", () => {
    assert.equal((source.match(/data-apstudy-consent-choice="accepted"/g) || []).length, 1);
    assert.equal((source.match(/data-apstudy-consent-choice="rejected"/g) || []).length, 1);
    assert.match(source, /class="apstudy-consent-actions"/);
    assert.match(source, /togglePreferences/);
});

test("public analytics controls form a clear bottom-right responsive stack", () => {
    assert.match(source, /class="apstudy-consent-stack"/);
    assert.match(globalStyles, /\.apstudy-consent-stack\s*\{[^}]*right:\s*max\(20px,[^}]*bottom:\s*max\(20px,[^}]*width:\s*min\(420px,/s);
    assert.match(globalStyles, /\.apstudy-consent-dialog__panel\s*\{[^}]*width:\s*100%;[^}]*max-height:\s*min\(72vh,\s*520px\);/s);
    assert.match(globalStyles, /\.apstudy-consent-settings\s*\{[^}]*width:\s*58px;[^}]*height:\s*58px;[^}]*border-radius:\s*50%;/s);
    assert.match(globalStyles, /\.apstudy-consent-dialog\s*\{\s*width:\s*100%;\s*\}/s);
    assert.match(globalStyles, /@media \(max-width:\s*767px\)/);
});

test("first visit opens a closable anchored consent dialog without loading analytics", () => {
    const firstVisit = createHarness();

    assert.equal(firstVisit.document.body.children.length, 1);
    assert.match(source, /if \(!decision\) \{\s*openPreferences\(\{ focus: false \}\);/);
    assert.match(source, /if \(!dialog \|\| !preferencesOpen\) return;/);
    assert.match(source, /class="apstudy-consent-settings__icon"/);
    assert.match(source, /aria-expanded="false" aria-controls="apstudy-consent-dialog" aria-label="Open cookie settings"/);
    assert.doesNotMatch(source, /aria-modal="true"/);
});

test("full templates declare authenticated, public-choice, hybrid, or off analytics modes", async () => {
    const authenticatedTemplates = [
        "admin.html", "admin_analytics.html", "admin_apswiftly.html", "admin_auth.html", "admin_detail.html", "admin_tiers.html",
        "calendar.html", "chat.html", "courses.html", "dashboard.html", "derek_echo.html", "files.html", "focus.html", "notes.html",
        "onboarding.html", "settings.html", "task.html",
    ];
    const publicTemplates = [
        "landing.html",
        "legal_document.html",
        "user_profile.html",
        "calendar_share.html",
        "file_share_download.html",
        "file_share_folder.html",
        "notes_shared_folder.html",
    ];
    const offTemplates = ["404.html", "login.html"];
    const hybridTemplates = ["notes_editor.html"];
    const templateDirectory = path.join(repoRoot, "templates");
    const { readdir } = await import("node:fs/promises");
    const templateNames = (await readdir(templateDirectory)).filter((name) => name.endsWith(".html"));

    for (const name of templateNames) {
        const templateSource = await readFile(path.join(templateDirectory, name), "utf8");
        assert.doesNotMatch(templateSource, /googletagmanager\.com\/gtag/, `${name} must not load GA directly`);
        if (!isConcreteFullPage(name, templateSource)) continue;
        if (authenticatedTemplates.includes(name)) assert.match(templateSource, /data-analytics-mode="authenticated"/);
        else if (publicTemplates.includes(name)) assert.match(templateSource, /data-analytics-mode="public-choice"/);
        else if (offTemplates.includes(name)) assert.match(templateSource, /data-analytics-mode="off"/);
        else if (hybridTemplates.includes(name)) {
            assert.match(templateSource, /data-analytics-mode="\{\{ 'authenticated' if viewer_authenticated and access\.role == 'owner' else 'public-choice' \}\}"/);
        } else {
            assert.fail(`${name} has no expected analytics mode`);
        }
    }
});
