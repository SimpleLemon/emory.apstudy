import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";
import path from "node:path";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const source = await readFile(path.join(repoRoot, "static/js/calendar/integrations/share.js"), "utf8");

class Element {
    constructor(doc, tag = "div", attrs = {}) { this.ownerDocument = doc; this.tagName = tag.toUpperCase(); this.attrs = attrs; this.children = []; this.listeners = {}; this.checked = attrs.checked; this.disabled = attrs.disabled; this.value = attrs.value || ""; this.name = attrs.name || ""; this.id = attrs.id || ""; this.className = attrs.class || ""; }
    get classList() { return { contains: (name) => this.className.split(/\s+/).includes(name), toggle: () => {} }; }
    get parentElement() { return this.parent; }
    getAttribute(name) { return this.attrs[name] ?? null; }
    hasAttribute(name) { return Object.hasOwn(this.attrs, name); }
    setAttribute(name, value) { this.attrs[name] = String(value); }
    matches(selector) { return selector.split(",").some((part) => this.matchOne(part.trim())); }
    matchOne(selector) {
        const classes = [...selector.matchAll(/\.([\w-]+)/g)].map((m) => m[1]);
        if (classes.some((name) => !this.className.split(/\s+/).includes(name))) return false;
        const id = selector.match(/#([\w-]+)/)?.[1]; if (id && id !== this.id) return false;
        for (const [, name, value] of selector.matchAll(/\[([\w-]+)(?:=["']?([^\]"']+)["']?)?\]/g)) if (!this.hasAttribute(name) || (value && this.getAttribute(name) !== value)) return false;
        if (selector.includes(":checked") && !this.checked) return false;
        const tag = selector.match(/^[a-z]+/i)?.[0]; return !tag || tag.toUpperCase() === this.tagName;
    }
    querySelectorAll(selector) { return this.children.filter((child) => child.matches(selector)); }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
    closest(selector) { return this.matches(selector) ? this : this.parent?.closest(selector) || null; }
    contains(node) { return node === this || this.children.includes(node); }
    appendChild(child) { child.parent = this; this.children.push(child); return child; }
    remove() { if (this.parent) this.parent.children = this.parent.children.filter((child) => child !== this); }
    focus() { this.ownerDocument.activeElement = this; }
    select() { this.focus(); }
    addEventListener(type, handler) { this.listeners[type] = handler; }
    set innerHTML(html) { this.html = html; this.renderCount = (this.renderCount || 0) + 1; this.children = []; const re = /<(input|button|select|form|p|div|section|article|code|h3)[^>]*>/gi; let m; while ((m = re.exec(html))) { const attrs = {}; for (const [, key, value] of m[0].matchAll(/([\w-]+)(?:="([^"]*)")?/g)) attrs[key] = value ?? true; const child = new Element(this.ownerDocument, m[1], attrs); child.parent = this; this.children.push(child); } }
    get innerHTML() { return this.html || ""; }
}

class Document extends Element {
    constructor() { super(null, "body"); this.ownerDocument = this; this.body = this; this.documentElement = this; this.activeElement = null; this.clipboardOk = true; this.defaultView = { navigator: { clipboard: { writeText: async () => { if (!this.clipboardOk) throw new Error("Clipboard unavailable"); } } }, CSS: { escape: (v) => String(v).replace(/[^\w-]/g, "\\$&") }, requestAnimationFrame: (fn) => fn(), confirm: () => true, setTimeout: (fn) => fn() }; }
    createElement(tag) { return new Element(this, tag); }
    execCommand() { return this.clipboardOk; }
}

async function fixture({ shares = [], saveResponse = null, detailResponse = null, loadShares = null, autoOpen = true } = {}) {
    const document = new Document();
    const context = { document, window: document.defaultView, AbortController, console, setTimeout: (fn) => fn() };
    vm.runInNewContext(source, context);
    context.window.APStudyLoader = { html: (text) => text };
    const state = { public: { readOnly: false }, calendars: { Canvas: { color: "#123" }, Tasks: { color: "#456" } }, shares: { items: shares, loading: false, saving: false, loaded: false, editingId: null, draft: null, focusTarget: "", error: "", notice: "" }, ui: { shareModalEl: null } };
    let rotateRequestSawOldUrl = false;
    let loadCalls = 0;
    let saveCalls = 0;
    let lastSave = null;
    const adapter = { async loadShares(options) { loadCalls += 1; if (loadShares) return loadShares(options); return { response: { ok: true, status: 200, json: async () => ({ shares }) } }; }, async saveShare(options) { lastSave = options; const { path, method } = options; if (path.endsWith("/ics") && method === "GET") return { response: { ok: (detailResponse?.status || 200) < 400, status: detailResponse?.status || 200, json: async () => detailResponse?.body || {} } }; if (path.endsWith("/ics")) rotateRequestSawOldUrl = state.ui.shareModalEl?.innerHTML.includes("secret.test") || false; saveCalls += 1; return { response: { ok: (saveResponse?.status || 200) < 400, status: saveResponse?.status || 200, json: async () => saveResponse?.body || {} } }; } };
    const share = context.window.APStudyCalendarShare.createCalendarShare({ root: document, lifecycle: null, dataAdapter: adapter, state, constants: { calendarShareCloseMs: 0, simulatedCalendarName: "Simulated Courses" }, escapeHtml: (v) => String(v ?? ""), getCalendarLabel: (v) => v, getCalendarLabelFromData: (v) => v.label || v.defaultName || "", trackCalendarMutation: (p) => p });
    if (autoOpen) {
        share.openCalendarShareModal();
        await new Promise((resolve) => setImmediate(resolve));
    }
    return { document, state, share, modal: () => state.ui.shareModalEl, saveResponse, detailResponse, rotateRequestSawOldUrl: () => rotateRequestSawOldUrl, loadCalls: () => loadCalls, saveCalls: () => saveCalls, lastSave: () => lastSave };
}

const active = { id: "active", shareCode: "active-code", shareUrl: "https://nest.test/share/active", isActive: true, includeAllCalendars: false, calendarIds: ["canvas"], icsConfigured: true, icsEnabled: true };
const inactive = { ...active, id: "inactive", isActive: false };

function deferred() {
    let resolve;
    let reject;
    const promise = new Promise((resolvePromise, rejectPromise) => {
        resolve = resolvePromise;
        reject = rejectPromise;
    });
    return { promise, reject, resolve };
}

function loadResponse({ ok = true, status = 200, shares = [], error = "" } = {}) {
    return { response: { ok, status, json: async () => error ? { error } : { shares } } };
}

test("share behavior covers eligibility, focus, lifecycle safety, and accessible recovery", async () => {
    const f = await fixture({ shares: [active, inactive], saveResponse: { status: 200, body: { share: active } }, detailResponse: { status: 200, body: {} } });
    const modal = f.modal();
    f.state.shares.draft = { includeAllCalendars: false, calendarIds: ["Simulated Courses"], dateScope: "all", rollingDays: 30 };
    f.share.renderCalendarShareModal();
    assert.match(modal.innerHTML, /Simulated Courses/);
    assert.doesNotMatch(modal.innerHTML, /data-ics-create-option hidden/);
    f.state.shares.draft.calendarIds = ["Canvas", "Simulated Courses"];
    f.share.renderCalendarShareModal();
    assert.match(modal.innerHTML, /data-ics-create-option hidden/);
    f.state.shares.draft = null;
    f.share.renderCalendarShareModal();
    assert.doesNotMatch(modal.innerHTML, /https:\/\/secret/);
    assert.doesNotMatch(modal.innerHTML, /calendar-share-row is-inactive[\s\S]*js-share-ics-action/);
    modal.querySelector(".js-share-close").focus();
    f.state.shares.notice = "Working…";
    f.state.shares.focusTarget = ".js-share-close";
    f.share.renderCalendarShareModal();
    assert.equal(f.document.activeElement, modal.querySelector(".js-share-close"), "focus survives an innerHTML rerender");
    f.detailResponse.status = 200;
    f.detailResponse.body = { ics: { configured: true, httpsUrl: "https://secret.test/new", webcalUrl: "webcal://secret.test/new" } };
    assert.ok(modal.children.some((element) => element.className.includes("js-share-ics-details")), modal.children.map((element) => element.className).join("|"));
    await modal.listeners.click({ target: modal.children.find((element) => element.className.includes("js-share-ics-details") && element.getAttribute("data-share-id") === "active") });
    assert.match(modal.innerHTML, /https:\/\/secret\.test\/new/);
    f.saveResponse.status = 200; f.saveResponse.body = { share: active };
    f.detailResponse.status = 500; f.detailResponse.body = {};
    const rotate = modal.children.find((element) => element.className.includes("js-share-ics-action") && element.getAttribute("data-share-id") === "active" && element.getAttribute("data-ics-action") === "rotate");
    rotate.focus();
    await modal.listeners.click({ target: rotate });
    assert.equal(f.rotateRequestSawOldUrl(), false, "rotate request is issued only after the old URL is cleared");
    assert.doesNotMatch(modal.innerHTML, /https:\/\/secret\.test\/new/, "rotation clears the old URL before failed refresh");
    assert.equal(f.document.activeElement.getAttribute("data-ics-action"), "rotate", "lifecycle focus is restored");
    const copy = modal.children.find((element) => element.className.includes("js-share-copy") && element.getAttribute("data-share-id") === "active");
    f.document.clipboardOk = false;
    await modal.listeners.click({ target: copy });
    assert.match(modal.innerHTML, /Select the link field manually/);
    assert.doesNotMatch(modal.innerHTML, /aria-describedby="calendar-share-form-error"/, "clipboard errors do not mark the form invalid");
    f.document.clipboardOk = true;
    await modal.listeners.click({ target: modal.children.find((element) => element.className.includes("js-share-copy") && element.getAttribute("data-share-id") === "active") });
    assert.match(modal.innerHTML, /Share link copied/);
    f.state.shares.error = "Could not copy the share link. Select the link field manually, then copy it.";
    f.state.shares.notice = "";
    f.state.shares.focusTarget = ".js-share-close";
    // The module's real render path is exercised by the same modal node on the next state update.
    f.share.renderCalendarShareModal();
    assert.match(modal.innerHTML, /role="alert" aria-live="assertive" aria-atomic="true"/);
});

test("422 create errors are associated with the form and double-submit is guarded", async () => {
    const f = await fixture({ saveResponse: { status: 422, body: { error: "Choose exactly one eligible Nest calendar." } } });
    const modal = f.modal();
    const form = modal.querySelector("#calendar-share-form");
    form.include_scope = { value: "selected" };
    form.date_scope = { value: "all" };
    form.rolling_days = { value: "30" };
    form.fixed_start = { value: "" };
    form.fixed_end = { value: "" };
    form.querySelectorAll = () => [];
    const submit = { target: form, preventDefault() {} };
    await Promise.all([modal.listeners.submit(submit), modal.listeners.submit(submit)]);
    assert.equal(f.saveCalls(), 1, "actual submit path ignores the second submit while saving");
    assert.match(modal.innerHTML, /aria-describedby="calendar-share-form-error"/);
    assert.match(modal.innerHTML, /aria-invalid="true"/);
    assert.match(modal.innerHTML, /Choose exactly one eligible Nest calendar/);
    assert.ok(modal.querySelector("#calendar-share-form"), "share form remains present while saving");
});

test("suspended subscriptions explain retained URL behavior and 400 uses validation fallback", async () => {
    const suspended = { ...active, icsEnabled: false };
    const f = await fixture({ shares: [suspended], saveResponse: { status: 400, body: {} }, detailResponse: { status: 200, body: { ics: { configured: true, httpsUrl: "https://secret.test/suspended", webcalUrl: "webcal://secret.test/suspended" } } } });
    const modal = f.modal();
    await modal.listeners.click({ target: modal.children.find((element) => element.className.includes("js-share-ics-details")) });
    assert.match(modal.innerHTML, /will not work until you re-enable/);
    const form = modal.querySelector("#calendar-share-form");
    form.include_scope = { value: "all" }; form.date_scope = { value: "all" }; form.rolling_days = { value: "30" }; form.fixed_start = { value: "" }; form.fixed_end = { value: "" }; form.querySelectorAll = () => [];
    await modal.listeners.submit({ target: form, preventDefault() {} });
    assert.match(modal.innerHTML, /Unable to save share link/);
    assert.match(modal.innerHTML, /aria-invalid="true"/);
});

test("calendar subscription intent preselects one calendar and does not submit until confirmed", async () => {
    const f = await fixture();
    f.share.openCalendarSubscriptionModal("Simulated Courses");
    await new Promise((resolve) => setImmediate(resolve));
    const modal = f.modal();

    assert.equal(f.state.shares.editingId, null);
    assert.deepEqual(Array.from(f.state.shares.draft.calendarIds), ["Simulated Courses"]);
    assert.equal(f.state.shares.draft.includeAllCalendars, false);
    assert.equal(f.state.shares.draft.icsEnabled, true);
    assert.match(modal.innerHTML, /name="calendar_ids"[^>]*value="Simulated Courses"[^>]*checked/);
    assert.match(modal.innerHTML, /name="ics_enabled"[^>]*checked/);
    assert.equal(f.saveCalls(), 0, "opening the intent never creates a server share");

    const form = modal.querySelector("#calendar-share-form");
    form.include_scope = { value: "selected" };
    form.date_scope = { value: "all" };
    form.rolling_days = { value: "30" };
    form.fixed_start = { value: "" };
    form.fixed_end = { value: "" };
    form.ics_enabled = { checked: true };
    form.querySelectorAll = () => [{ value: "Simulated Courses" }];
    await modal.listeners.submit({ target: form, preventDefault() {} });
    assert.equal(f.saveCalls(), 1);
    assert.deepEqual(JSON.parse(f.lastSave().body), {
        includeAllCalendars: false,
        calendarIds: ["Simulated Courses"],
        dateScope: "all",
        fixedStart: null,
        fixedEnd: null,
        rollingDays: 30,
        icsEnabled: true,
    });
});

test("active matching ICS share is opened for management while revoked and broad shares do not block creation", async () => {
    const activeSimulated = {
        ...active,
        id: "active-simulated",
        calendarIds: ["simulated_courses"],
    };
    const matching = await fixture({ shares: [activeSimulated], detailResponse: { status: 200, body: { ics: { configured: true } } } });
    matching.share.openCalendarSubscriptionModal("Simulated Courses");
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(matching.state.shares.editingId, "active-simulated");
    assert.match(matching.modal().innerHTML, /This calendar already has an ICS subscription/);
    assert.equal(matching.saveCalls(), 0);

    const revoked = { ...activeSimulated, id: "revoked", isActive: false };
    const allCalendars = { ...activeSimulated, id: "all", includeAllCalendars: true, calendarIds: [] };
    const multiCalendar = { ...activeSimulated, id: "multi", calendarIds: ["simulated_courses", "tasks"] };
    const fresh = await fixture({ shares: [revoked, allCalendars, multiCalendar] });
    fresh.share.openCalendarSubscriptionModal("Simulated Courses");
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(fresh.state.shares.editingId, null);
    assert.deepEqual(Array.from(fresh.state.shares.draft.calendarIds), ["Simulated Courses"]);
    assert.equal(fresh.state.shares.draft.icsEnabled, true);
    assert.equal(fresh.saveCalls(), 0);
});

test("stale share-load completion cannot replace a newer calendar subscription intent", async () => {
    const pendingLoad = deferred();
    const f = await fixture({ autoOpen: false, loadShares: () => pendingLoad.promise });

    f.share.openCalendarSubscriptionModal("Simulated Courses");
    const firstModal = f.modal();
    assert.equal(f.state.shares.loading, true);
    assert.deepEqual(Array.from(f.state.shares.draft.calendarIds), ["Simulated Courses"]);

    f.share.closeCalendarShareModal(true);
    f.share.openCalendarSubscriptionModal("Canvas");
    const currentModal = f.modal();
    assert.notEqual(currentModal, firstModal);
    assert.deepEqual(Array.from(f.state.shares.draft.calendarIds), ["Canvas"]);
    const renderCountBeforeLoad = currentModal.renderCount;

    pendingLoad.resolve(loadResponse());
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(f.modal(), currentModal);
    assert.equal(currentModal.renderCount, renderCountBeforeLoad + 1, "only the current modal session may render after the shared fetch");
    assert.deepEqual(Array.from(f.state.shares.draft.calendarIds), ["Canvas"]);
    assert.match(currentModal.innerHTML, /name="calendar_ids"[^>]*value="Canvas"[^>]*checked/);
    assert.equal(f.state.shares.editingId, null);
    assert.equal(f.state.shares.error, "");
    assert.equal(f.state.shares.loading, false);
    assert.equal(f.loadCalls(), 1, "concurrent modal sessions share one cache fetch");
});

test("a failed share load affects only its current modal session and a reopen retries cleanly", async () => {
    let attempt = 0;
    const f = await fixture({
        autoOpen: false,
        loadShares: async () => {
            attempt += 1;
            return attempt === 1
                ? loadResponse({ ok: false, status: 503, error: "Share links are temporarily unavailable." })
                : loadResponse();
        },
    });

    f.share.openCalendarSubscriptionModal("Simulated Courses");
    await new Promise((resolve) => setImmediate(resolve));
    assert.match(f.state.shares.error, /temporarily unavailable/);
    assert.equal(f.state.shares.loading, false);

    f.share.closeCalendarShareModal(true);
    f.share.openCalendarSubscriptionModal("Tasks");
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(f.loadCalls(), 2);
    assert.equal(f.state.shares.error, "");
    assert.equal(f.state.shares.loading, false);
    assert.deepEqual(Array.from(f.state.shares.draft.calendarIds), ["Tasks"]);
    assert.match(f.modal().innerHTML, /name="calendar_ids"[^>]*value="Tasks"[^>]*checked/);
});

test("suspended configured shares open management instead of preparing duplicates", async () => {
    const suspended = {
        ...active,
        id: "suspended-simulated",
        calendarIds: ["simulated_courses"],
        icsEnabled: false,
    };
    const f = await fixture({ shares: [suspended] });
    f.share.openCalendarSubscriptionModal("Simulated Courses");
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(f.state.shares.editingId, "suspended-simulated");
    assert.equal(f.state.shares.draft, null);
    assert.match(f.modal().innerHTML, /ICS subscription · Suspended/);
    assert.match(f.modal().innerHTML, /js-share-ics-details/);
    assert.equal(f.saveCalls(), 0);
});

test("Canvas and Tasks use the direct single-calendar subscription flow", async () => {
    const f = await fixture();
    for (const calendarName of ["Canvas", "Tasks"]) {
        assert.equal(f.share.canCreateCalendarSubscription(calendarName), true);
        f.share.openCalendarSubscriptionModal(calendarName);
        await new Promise((resolve) => setImmediate(resolve));
        assert.equal(f.state.shares.editingId, null);
        assert.deepEqual(Array.from(f.state.shares.draft.calendarIds), [calendarName]);
        assert.equal(f.state.shares.draft.includeAllCalendars, false);
        assert.equal(f.state.shares.draft.icsEnabled, true);
        assert.match(f.modal().innerHTML, new RegExp(`name="calendar_ids"[^>]*value="${calendarName}"[^>]*checked`));
    }
    assert.equal(f.saveCalls(), 0);
});
