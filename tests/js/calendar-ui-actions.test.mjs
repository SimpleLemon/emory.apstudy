import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";
import path from "node:path";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const source = await readFile(path.join(repoRoot, "static/js/calendar/events/ui-actions.js"), "utf8");
const indexSource = await readFile(path.join(repoRoot, "static/js/calendar/index.js"), "utf8");

class Element {
    constructor(doc, tag = "div", attrs = {}) {
        this.ownerDocument = doc;
        this.tagName = tag.toUpperCase();
        this.attrs = attrs;
        this.children = [];
        this.listeners = {};
        this.className = attrs.class || "";
        this.style = {};
    }

    get classList() {
        return { contains: (name) => this.className.split(/\s+/).includes(name) };
    }

    getAttribute(name) { return this.attrs[name] ?? null; }
    hasAttribute(name) { return Object.hasOwn(this.attrs, name); }
    setAttribute(name, value) { this.attrs[name] = String(value); }

    matches(selector) {
        return selector.split(",").some((part) => {
            const trimmed = part.trim();
            const classes = [...trimmed.matchAll(/\.([\w-]+)/g)].map((match) => match[1]);
            if (classes.some((name) => !this.className.split(/\s+/).includes(name))) return false;
            const tag = trimmed.match(/^[a-z]+/i)?.[0];
            return !tag || tag.toUpperCase() === this.tagName;
        });
    }

    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
    querySelectorAll(selector) { return this.children.filter((child) => child.matches(selector)); }
    closest(selector) { return this.matches(selector) ? this : this.parent?.closest(selector) || null; }
    appendChild(child) { child.parent = this; this.children.push(child); return child; }
    remove() { if (this.parent) this.parent.children = this.parent.children.filter((child) => child !== this); }
    addEventListener(type, handler) { this.listeners[type] = handler; }
    getBoundingClientRect() { return { right: 300, bottom: 100, top: 80 }; }

    set innerHTML(html) {
        this.html = html;
        this.children = [];
        const re = /<(button|div)[^>]*>/gi;
        let match;
        while ((match = re.exec(html))) {
            const attrs = {};
            for (const [, key, value] of match[0].matchAll(/([\w-]+)(?:="([^"]*)")?/g)) attrs[key] = value ?? true;
            this.appendChild(new Element(this.ownerDocument, match[1], attrs));
        }
    }

    get innerHTML() { return this.html || ""; }
}

class Document extends Element {
    constructor() {
        super(null, "body");
        this.ownerDocument = this;
        this.nodeType = 9;
        this.body = this;
        this.documentElement = this;
        this.defaultView = { innerWidth: 1200, innerHeight: 800 };
    }

    createElement(tag) { return new Element(this, tag); }
}

function fixture({ readOnly = false } = {}) {
    const document = new Document();
    const context = { document, window: document.defaultView, console };
    vm.runInNewContext(source, context);
    const state = {
        public: { readOnly },
        calendars: {
            Canvas: { color: "#123456" },
            Tasks: { color: "#654321" },
            "Simulated Courses": { color: "#b08968" },
            Personal: { color: "#0ea5e9" },
        },
        calendarColors: ["#ef4444"],
        ui: { contextMenuEl: null, contextAnchorEl: null, contextCalendarName: null },
    };
    const calls = [];
    const actions = context.window.APStudyCalendarUiActions.createCalendarUiActions({
        root: document,
        state,
        callbacks: {
            canCreateCalendarSubscription: (calendarName) => ["Canvas", "Tasks", "Simulated Courses"].includes(calendarName),
            getCalendarEventColor: () => "#000",
            getCalendarEventCount: () => 11,
            getCalendarLabel: (calendarName) => calendarName,
            getEventBadgeColors: () => ({}),
            getEventBadgeStyle: () => "",
            getEventElementAttributes: () => "",
            openCalendarInfoModal: (calendarName) => calls.push(["info", calendarName]),
            openCalendarSubscriptionModal: (calendarName) => calls.push(["subscription", calendarName]),
            openRgbModal: () => calls.push(["color"]),
            setCalendarColor: () => calls.push(["preset"]),
        },
        formatters: { escapeHtml: (value) => String(value ?? ""), isTaskEvent: () => false },
    });
    return { actions, calls, document, state };
}

test("eligible calendar menu exposes an accessible subscription action and preserves existing actions", () => {
    const fixtureData = fixture();
    const anchor = fixtureData.document.createElement("button");
    fixtureData.actions.openCalendarContextMenu("Simulated Courses", anchor);
    const menu = fixtureData.state.ui.contextMenuEl;
    const subscription = menu.querySelector(".js-context-subscription");

    assert.ok(subscription);
    assert.match(menu.innerHTML, /Create subscription link…/);
    assert.match(menu.innerHTML, /<svg[^>]+aria-hidden="true"/);
    assert.ok(menu.querySelector(".js-context-info"));
    assert.ok(menu.querySelector(".js-context-preset"));
    assert.ok(menu.querySelector(".js-context-custom"));

    menu.listeners.click({ target: subscription });
    assert.deepEqual(fixtureData.calls, [["subscription", "Simulated Courses"]]);
    assert.equal(fixtureData.state.ui.contextMenuEl, null);
});

test("ineligible and read-only calendars do not expose subscription action", () => {
    const normal = fixture();
    normal.actions.openCalendarContextMenu("Personal", normal.document.createElement("button"));
    assert.equal(normal.state.ui.contextMenuEl.querySelector(".js-context-subscription"), null);

    const readOnly = fixture({ readOnly: true });
    readOnly.actions.openCalendarContextMenu("Simulated Courses", readOnly.document.createElement("button"));
    assert.equal(readOnly.state.ui.contextMenuEl.querySelector(".js-context-subscription"), null);
});

test("Canvas and Tasks menu actions use the direct subscription callback", () => {
    const fixtureData = fixture();
    for (const calendarName of ["Canvas", "Tasks"]) {
        fixtureData.actions.openCalendarContextMenu(calendarName, fixtureData.document.createElement("button"));
        const menu = fixtureData.state.ui.contextMenuEl;
        const subscription = menu.querySelector(".js-context-subscription");
        assert.ok(subscription, `${calendarName} should expose the subscription action`);
        menu.listeners.click({ target: subscription });
    }
    assert.deepEqual(fixtureData.calls, [
        ["subscription", "Canvas"],
        ["subscription", "Tasks"],
    ]);
});

test("the actual index wiring passes share eligibility and opener callbacks to the per-calendar menu", () => {
    const start = indexSource.indexOf("const calendarShare = window.APStudyCalendarShare.createCalendarShare(");
    const end = indexSource.indexOf("const {\n    buildEventChip,", start);
    assert.ok(start >= 0 && end > start, "calendar share/UI wiring block should remain discoverable");
    const wiring = indexSource.slice(start, end);
    const calls = [];
    let uiCallbacks = null;
    const canCreateCalendarSubscription = (calendarName) => ["canvas", "tasks", "simulated_courses"].includes(String(calendarName).toLowerCase().replaceAll(" ", "_"));
    const openCalendarSubscriptionModal = (calendarName) => calls.push(calendarName);
    const context = {
        window: {
            APStudyCalendarShare: {
                createCalendarShare: () => ({
                    canCreateCalendarSubscription,
                    closeCalendarShareModal() {},
                    openCalendarShareModal() {},
                    openCalendarSubscriptionModal,
                }),
            },
            APStudyCalendarUiActions: {
                createCalendarUiActions: ({ callbacks }) => {
                    uiCallbacks = callbacks;
                    return {};
                },
            },
        },
        pageRoot: {},
        lifecycle: {},
        adapter: {},
        state: {},
        CALENDAR_SHARE_CLOSE_MS: 140,
        SIMULATED_CALENDAR_NAME: "Simulated Courses",
        escapeHtml: (value) => String(value),
        getCalendarLabel: (value) => value,
        getCalendarLabelFromData: () => "",
        trackCalendarMutation: (value) => value,
        getEventCalendarColor: () => "",
        getCalendarEventCount: () => 0,
        getEventBadgeColors: () => ({}),
        getEventBadgeStyle: () => "",
        getEventElementAttributes: () => "",
        openCalendarInfoModal() {},
        openRgbModal() {},
        setCalendarColor() {},
        isTaskEvent: () => false,
    };

    vm.runInNewContext(wiring, context);
    assert.equal(uiCallbacks.canCreateCalendarSubscription, canCreateCalendarSubscription);
    assert.equal(uiCallbacks.canCreateCalendarSubscription("Canvas"), true);
    uiCallbacks.openCalendarSubscriptionModal("Canvas");
    assert.deepEqual(calls, ["Canvas"]);
});
