import assert from "node:assert/strict";
import { cp, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");

function createBrowserRuntime() {
    const listeners = new Map();
    const removals = [];
    const listen = (target, type, listener, options) => {
        if (!listeners.has(type)) listeners.set(type, new Set());
        listeners.get(type).add({ target, listener, options });
    };
    const remove = (target, type, listener) => {
        for (const entry of listeners.get(type) || []) {
            if (entry.target === target && entry.listener === listener) listeners.get(type).delete(entry);
        }
        removals.push({ target, type, listener });
    };
    const view = {
        setTimeout,
        clearTimeout,
        requestAnimationFrame: (callback) => setTimeout(callback, 0),
        cancelAnimationFrame: clearTimeout,
        matchMedia: () => ({ matches: false }),
        AbortController,
        fetch: async () => ({ ok: true, json: async () => ({}) }),
        innerWidth: 1280,
        innerHeight: 900,
        location: { href: "https://example.test/calendar", search: "" },
        history: { replaceState() {} },
        addEventListener(type, listener, options) {
            listen(view, type, listener, options);
        },
        removeEventListener(type, listener) {
            remove(view, type, listener);
        },
        dispatchEvent() {},
    };
    const document = {
        nodeType: 9,
        body: { dataset: {} },
        documentElement: { dataset: {} },
        defaultView: view,
        activeElement: null,
        addEventListener(type, listener, options) {
            listen(document, type, listener, options);
        },
        removeEventListener(type, listener) {
            remove(document, type, listener);
        },
        querySelector: () => null,
        querySelectorAll: () => [],
        getElementById: () => null,
        createElement: () => ({
            style: {},
            dataset: {},
            classList: { add() {}, remove() {}, toggle() {} },
            appendChild() {},
            remove() {},
            setAttribute() {},
            addEventListener() {},
            removeEventListener() {},
        }),
    };
    view.document = document;
    view.window = view;
    return { document, listeners, removals, view };
}

function factoryNamespace(methods = {}) {
    return new Proxy(methods, {
        get(target, property) {
            if (property in target) return target[property];
            return (...args) => ({ ...args });
        },
    });
}

function installCalendarFactories(view, document) {
    const create = (methods = {}) => (...args) => factoryNamespace(methods);
    const state = { public: { readOnly: false }, ui: {}, courses: {}, calendars: {}, calendarColors: [] };
    Object.assign(view, {
        APStudyCalendarUtils: factoryNamespace({
            formatDateKey: () => "",
            formatMonthGridDayLabel: () => "",
            dateToDayIndex: () => 0,
            layoutTimedEvents: () => [],
            formatHourLabel: () => "",
            formatTimeOnly: () => "",
            formatTimedEventRange: () => "",
            formatAllDayRange: () => "",
            getStartOfWeek: () => new Date(),
            isToday: () => false,
            getUrgencyLabel: () => "",
            getUrgencyLabelAllDay: () => "",
            getAccent: () => "",
            isTaskEvent: () => false,
            getTaskPriorityColor: () => "",
            createAccessibleEventPalette: () => [],
            getCssColorVariable: () => "",
            escapeHtml: (value) => String(value),
            formatMultilineText: (value) => String(value),
        }),
        APStudyCalendarState: { createCalendarState: () => state },
        APStudyCalendarCore: { createCalendarCore: create({}) },
        APStudyCalendarMenu: { createCalendarMenu: create({}) },
        APStudyCalendarRenderShell: { createCalendarRenderShell: create({}) },
        APStudyCalendarPreferences: { createCalendarPreferences: create({}) },
        APStudyCalendarCourses: { createCalendarCourses: create({}) },
        APStudyCalendarData: { createCalendarData: create({}) },
        APStudyCalendarEventRender: { createCalendarEventRender: create({}) },
        APStudyCalendarUiActions: { createCalendarUiActions: create({}) },
        APStudyCalendarAgenda: { createCalendarAgenda: create({}) },
        APStudyCalendarMonthView: { createCalendarMonthView: create({}) },
        APStudyCalendarWeekView: { createCalendarWeekView: create({}) },
        APStudyCalendarSources: { createCalendarSources: create({}) },
        APStudyCalendarShare: { createCalendarShare: create({}) },
        APStudyCalendarControls: { createCalendarControls: create({}) },
        APStudyCalendarBootstrap: {
            createCalendarBootstrap: () => ({
                register() {
                    document.addEventListener("DOMContentLoaded", () => {});
                },
            }),
        },
    });
}

async function importCalendarGraph(runtime) {
    const moduleRoot = await mkdtemp(path.join(os.tmpdir(), "apstudy-calendar-esm-"));
    await writeFile(path.join(moduleRoot, "package.json"), '{"type":"module"}\n');
    await cp(
        path.join(repoRoot, "static/js/calendar"),
        path.join(moduleRoot, "static/js/calendar"),
        { recursive: true },
    );
    await cp(
        path.join(repoRoot, "static/js/core/ui-primitives.js"),
        path.join(moduleRoot, "static/js/core/ui-primitives.js"),
    );
    await cp(
        path.join(repoRoot, "static/js/core/ui-primitives-module.js"),
        path.join(moduleRoot, "static/js/core/ui-primitives-module.js"),
    );
    globalThis.window = runtime.view;
    globalThis.document = runtime.document;
    const manifest = JSON.parse(await readFile(path.join(moduleRoot, "static/js/calendar/manifest.json"), "utf8"));
    const importFile = (relativePath) => import(`${pathToFileURL(path.join(moduleRoot, relativePath)).href}?v=${manifest.version}`);
    const [entry, index, adapter, lifecycle] = await Promise.all([
        importFile("static/js/calendar/entry.js"),
        importFile("static/js/calendar/index.js"),
        importFile("static/js/calendar/adapter.js"),
        importFile("static/js/calendar/lifecycle.js"),
    ]);
    return { adapter, entry, index, lifecycle, moduleRoot };
}

function createElementRoot(document, pageRoot) {
    return {
        nodeType: 1,
        ownerDocument: document,
        closest: (selector) => selector === "#calendar-app-root" ? pageRoot : null,
        querySelector: () => null,
        appendChild() {},
    };
}

test("the actual calendar ESM graph exposes contracts, mounts once, and disposes idempotently", async () => {
    const runtime = createBrowserRuntime();
    const previousWindow = globalThis.window;
    const previousDocument = globalThis.document;
    const graph = await importCalendarGraph(runtime);
    try {
        assert.equal(typeof graph.entry.bootCalendar, "function");
        assert.equal(typeof graph.index.mountCalendar, "function");
        assert.equal(typeof graph.adapter.createCalendarDataAdapter, "function");
        assert.equal(typeof graph.lifecycle.createCalendarLifecycle, "function");
        assert.equal(runtime.view.APStudyCalendarAdapter.createCalendarDataAdapter, graph.adapter.createCalendarDataAdapter);
        assert.equal(runtime.view.APStudyCalendarLifecycle.createCalendarLifecycle, graph.lifecycle.createCalendarLifecycle);

        const adapter = graph.adapter.createCalendarDataAdapter({
            fetch: async (url) => ({ ok: true, json: async () => ({ url }) }),
        });
        const payload = await adapter.loadRange({
            range: { start: new Date("2026-01-01T00:00:00Z"), end: new Date("2026-01-02T00:00:00Z") },
        });
        assert.match(payload.url, /^\/api\/calendar\/events\?/);

        installCalendarFactories(runtime.view, runtime.document);
        const pageRoot = { nodeType: 1, ownerDocument: runtime.document, querySelector: () => null };
        const root = createElementRoot(runtime.document, pageRoot);
        const domReadyBeforeMount = runtime.listeners.get("DOMContentLoaded")?.size || 0;
        const first = graph.entry.bootCalendar(root, {
            adapterOverrides: { fetch: runtime.view.fetch, window: runtime.view },
        });
        const mountedListeners = runtime.listeners.get("DOMContentLoaded")?.size || 0;
        assert.equal(mountedListeners, domReadyBeforeMount + 1);

        const second = graph.entry.bootCalendar(root, {
            adapterOverrides: { fetch: runtime.view.fetch, window: runtime.view },
        });
        assert.equal(runtime.listeners.get("DOMContentLoaded")?.size || 0, mountedListeners);
        first();
        assert.equal(runtime.listeners.get("DOMContentLoaded")?.size || 0, mountedListeners);
        second();
        assert.equal(runtime.listeners.get("DOMContentLoaded")?.size || 0, domReadyBeforeMount);
    } finally {
        globalThis.window = previousWindow;
        globalThis.document = previousDocument;
        await rm(graph.moduleRoot, { recursive: true, force: true });
    }
});

test("lifecycle cleanup remains idempotent and missing roots fail safely", async () => {
    const runtime = createBrowserRuntime();
    const previousWindow = globalThis.window;
    const previousDocument = globalThis.document;
    const graph = await importCalendarGraph(runtime);
    try {
        const lifecycle = graph.lifecycle.createCalendarLifecycle({ view: runtime.view });
        const target = {
            addEventListener() {},
            removeEventListener() { target.removed = true; },
            removed: false,
        };
        const node = { removeCalls: 0, remove() { this.removeCalls += 1; } };
        const observer = { disconnectCalls: 0, disconnect() { this.disconnectCalls += 1; } };
        const controller = lifecycle.trackAbortController();
        lifecycle.addEventListener(target, "change", () => {});
        lifecycle.trackObserver(observer);
        lifecycle.trackNode(node);
        lifecycle.dispose();
        lifecycle.dispose();

        assert.equal(controller.signal.aborted, true);
        assert.equal(target.removed, true);
        assert.equal(observer.disconnectCalls, 1);
        assert.equal(node.removeCalls, 1);
        assert.doesNotThrow(() => graph.index.mountCalendar(null, {}, {}));
        assert.doesNotThrow(() => graph.entry.bootCalendar(null));
    } finally {
        globalThis.window = previousWindow;
        globalThis.document = previousDocument;
        await rm(graph.moduleRoot, { recursive: true, force: true });
    }
});

test("normal and share templates use the module entry and explicit Element app root", async () => {
    const [calendar, share] = await Promise.all([
        readFile(path.join(repoRoot, "templates/calendar.html"), "utf8"),
        readFile(path.join(repoRoot, "templates/calendar_share.html"), "utf8"),
    ]);
    for (const template of [calendar, share]) {
        assert.match(template, /<script type="module" src="\{\{ url_for\('static', filename='js\/calendar\/entry\.js', v=calendar_asset_version\) \}\}"><\/script>/);
        assert.match(template, /id="calendar-app-root"[\s\S]*id="calendar-view-root"/);
        assert.ok(template.indexOf('id="calendar-app-root"') < template.indexOf('id="calendar-view-root"'));
    }
    assert.doesNotMatch(share, /<script[^>]+src="[^\"]*calendar\/index\.js"[^>]*defer/);
});
