import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { cp, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

import {
    buildCalendarExtension,
    validateCalendarExtensionOutput,
    validateCalendarExtensionSourceGraph,
} from "../../static/js/calendar/build-extension.mjs";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");

async function importCalendarExtensionModules() {
    const moduleRoot = await mkdtemp(path.join(os.tmpdir(), "apstudy-calendar-extension-modules-"));
    await writeFile(path.join(moduleRoot, "package.json"), '{"type":"module"}\n');
    await cp(path.join(repoRoot, "static/js/calendar/capabilities.js"), path.join(moduleRoot, "capabilities.js"));
    await cp(path.join(repoRoot, "static/js/calendar/extension-ui.js"), path.join(moduleRoot, "extension-ui.js"));
    await cp(path.join(repoRoot, "static/js/calendar/adapter.js"), path.join(moduleRoot, "adapter.js"));
    const [capabilities, extensionUi, adapter] = await Promise.all([
        import(pathToFileURL(path.join(moduleRoot, "capabilities.js")).href),
        import(pathToFileURL(path.join(moduleRoot, "extension-ui.js")).href),
        import(pathToFileURL(path.join(moduleRoot, "adapter.js")).href),
    ]);
    return { adapter, capabilities, extensionUi, moduleRoot };
}

function canvasData(writebacks = []) {
    return {
        source: {
            label: "BIO Canvas",
            accountLabel: "BIO Canvas",
            sourceId: "source-1",
            url: "https://canvas.example.edu",
        },
        completion: { status: "completed", source: "canvas" },
        routing: {
            state: "completed",
            destination: "local:completed",
            degraded: true,
            displayOverride: true,
        },
        writebacks,
        firstEvent: { event_ref: "canvas:source-1:event-1", source_id: "source-1", calendar_id: "local:completed" },
    };
}

test("calendar capabilities default safely, gate mutations, and preserve safe open-source access in read-only mode", async () => {
    const modules = await importCalendarExtensionModules();
    try {
        const { normalizeCalendarCapabilities, getSafeCanvasSourceUrl, getCalendarCapabilityData } = modules.capabilities;
        assert.deepEqual(normalizeCalendarCapabilities({}).actions, {
            routeDisplayOverride: false,
            retryWriteback: false,
            openSourceUrl: false,
        });
        assert.equal(normalizeCalendarCapabilities({ contractVersion: 99, actions: {
            routeDisplayOverride: true, retryWriteback: true, openSourceUrl: true,
        }}).supported, false);
        assert.deepEqual(normalizeCalendarCapabilities({ readOnly: true, actions: {
            routeDisplayOverride: true, retryWriteback: true, openSourceUrl: true,
        }}).actions, {
            routeDisplayOverride: false,
            retryWriteback: false,
            openSourceUrl: true,
        });
        assert.equal(getSafeCanvasSourceUrl("https://canvas.example.edu"), "https://canvas.example.edu");
        for (const unsafe of [
            "http://canvas.example.edu",
            "https://canvas.example.edu/courses/1",
            "https://user:secret@canvas.example.edu",
            "https://canvas.example.edu?token=secret",
            "javascript:alert(1)",
        ]) assert.equal(getSafeCanvasSourceUrl(unsafe), null, unsafe);

        const stateValues = [
            "waiting_for_canvas_session", "queued", "applied", "unsupported",
            "forbidden", "conflict", "retryable_failed", "cancelled",
        ];
        const normalizedData = getCalendarCapabilityData({ data: { writebacks: stateValues.map((state) => ({ state })) } });
        assert.deepEqual(normalizedData.writebacks.map((item) => item.state), stateValues);
    } finally {
        await rm(modules.moduleRoot, { recursive: true, force: true });
    }
});

test("calendar extension UI is root-scoped, accessible, state-complete, and lifecycle-cleaned", async () => {
    const modules = await importCalendarExtensionModules();
    try {
        const { createCalendarExtensionUi, getCalendarExtensionActionAvailability } = modules.extensionUi;
        const panel = {
            innerHTML: "",
            listeners: new Map(),
            setAttribute() {},
            addEventListener(type, listener) { this.listeners.set(type, listener); },
            remove() { this.removed = true; },
        };
        const root = {
            ownerDocument: { createElement() { return panel; } },
            querySelector() { return null; },
            appendChild(node) { this.child = node; },
        };
        const cleanup = [];
        const lifecycle = {
            addEventListener(target, type, listener) { target.addEventListener(type, listener); },
            addCleanup(callback) { cleanup.push(callback); },
            trackNode(node) { cleanup.push(() => node.remove()); },
        };
        const data = canvasData([
            { state: "waiting_for_canvas_session" },
            { state: "queued" },
            { state: "applied" },
            { state: "unsupported" },
            { state: "forbidden" },
            { state: "conflict" },
            { state: "retryable_failed", error_message: "try again" },
            { state: "cancelled" },
        ]);
        const adapter = {
            setCanvasRouting() {},
            retryWriteback() {},
            openSafeSourceUrl() {},
            actionSupport: { retryWriteback: true },
        };
        const capabilities = {
            contractVersion: 1,
            readOnly: false,
            actions: { routeDisplayOverride: true, retryWriteback: true, openSourceUrl: true },
            data,
        };
        const ui = createCalendarExtensionUi({
            root,
            state: { events: [] },
            adapter,
            capabilities,
            lifecycle,
        });
        assert.equal(root.child, panel);
        for (const label of [
            "BIO Canvas", "Account:", "Completed", "Canvas reported", "Routing", "Degraded",
            "Waiting for Canvas session", "Queued", "Applied", "Unsupported", "Forbidden",
            "Conflict", "Retryable failure", "Cancelled", "aria-live",
        ]) assert.match(panel.innerHTML, new RegExp(label.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
        assert.match(panel.innerHTML, /data-calendar-extension-action="route-display"/);
        assert.match(panel.innerHTML, /data-calendar-extension-action="retry-writeback:6"/);

        const availability = getCalendarExtensionActionAvailability({ capabilities, adapter, data });
        assert.equal(availability.routeEnabled, true);
        assert.equal(availability.retryEnabled, true);
        assert.equal(availability.openSourceEnabled, true);

        const readOnlyPanel = { ...panel, listeners: new Map(), removed: false };
        const readOnlyUi = createCalendarExtensionUi({
            root: {
                ownerDocument: { createElement() { return readOnlyPanel; } },
                querySelector() { return null; },
                appendChild(node) { this.child = node; },
            },
            state: { events: [] },
            adapter,
            capabilities: {
                contractVersion: 1,
                readOnly: true,
                actions: { routeDisplayOverride: true, retryWriteback: true, openSourceUrl: true },
                data,
            },
            lifecycle,
        });
        assert.equal(readOnlyUi.render instanceof Function, true);
        assert.doesNotMatch(readOnlyPanel.innerHTML, /route-display|display-override|retry-writeback/);
        assert.match(readOnlyPanel.innerHTML, /data-calendar-extension-action="open-source"/);
        ui.dispose();
        assert.equal(panel.removed, true);
        for (const callback of cleanup) callback();
    } finally {
        await rm(modules.moduleRoot, { recursive: true, force: true });
    }
});

test("calendar extension dispatch revalidates read-only state and the exact action capability", async () => {
    const modules = await importCalendarExtensionModules();
    try {
        const cases = [
            { readOnly: "true", actions: { routeDisplayOverride: true } },
            { readOnly: "false", actions: { routeDisplayOverride: true } },
            { readOnly: undefined, actions: { routeDisplayOverride: true } },
            { readOnly: { malformed: true }, actions: { routeDisplayOverride: true } },
            { readOnly: false, actions: { routeDisplayOverride: false, retryWriteback: true } },
            { readOnly: false, actions: { routeDisplayOverride: "true" } },
            { readOnly: false, actions: { routeDisplayOverride: true }, expectedCalls: 1 },
        ];
        for (const testCase of cases) {
            const listeners = new Map();
            const panel = {
                innerHTML: "",
                listeners,
                setAttribute() {},
                addEventListener(type, listener) { listeners.set(type, listener); },
                removeEventListener(type, listener) {
                    if (listeners.get(type) === listener) listeners.delete(type);
                },
                remove() {},
            };
            const root = {
                ownerDocument: { createElement() { return panel; } },
                querySelector() { return null; },
                appendChild() {},
            };
            let routeCalls = 0;
            const ui = modules.extensionUi.createCalendarExtensionUi({
                root,
                state: { events: [] },
                adapter: { setCanvasRouting: async () => { routeCalls += 1; return { ok: true }; } },
                capabilities: {
                    contractVersion: 1,
                    readOnly: testCase.readOnly,
                    actions: testCase.actions,
                    data: canvasData(),
                },
            });
            const clickHandler = listeners.get("click");
            clickHandler({
                target: { closest: () => ({
                    disabled: false,
                    getAttribute: () => "route-display",
                }) },
                preventDefault() {},
            });
            await new Promise((resolve) => setImmediate(resolve));
            assert.equal(routeCalls, testCase.expectedCalls || 0, JSON.stringify(testCase));
            ui.dispose();
        }
    } finally {
        await rm(modules.moduleRoot, { recursive: true, force: true });
    }
});

test("calendar extension removes the exact reused-panel listener across repeated mount and dispose", async () => {
    const modules = await importCalendarExtensionModules();
    try {
        const listeners = new Set();
        const panel = {
            innerHTML: "",
            setAttribute() {},
            addEventListener(type, listener) {
                if (type === "click") listeners.add(listener);
            },
            removeEventListener(type, listener) {
                if (type === "click") listeners.delete(listener);
            },
            remove() {},
        };
        const root = {
            ownerDocument: { createElement() { return panel; } },
            querySelector() { return panel; },
            appendChild() {},
        };
        let routeCalls = 0;
        const adapter = {
            setCanvasRouting: async () => { routeCalls += 1; return { ok: true }; },
        };
        const capabilities = {
            contractVersion: 1,
            readOnly: false,
            actions: { routeDisplayOverride: true },
            data: canvasData(),
        };
        const firstUi = modules.extensionUi.createCalendarExtensionUi({ root, state: { events: [] }, adapter, capabilities });
        assert.equal(listeners.size, 1);
        firstUi.dispose();
        firstUi.dispose();
        assert.equal(listeners.size, 0);

        const secondUi = modules.extensionUi.createCalendarExtensionUi({ root, state: { events: [] }, adapter, capabilities });
        assert.equal(listeners.size, 1);
        for (const listener of listeners) listener({
            target: { closest: () => ({ disabled: false, getAttribute: () => "route-display" }) },
            preventDefault() {},
        });
        await new Promise((resolve) => setImmediate(resolve));
        assert.equal(routeCalls, 1);
        secondUi.dispose();
        assert.equal(listeners.size, 0);
        for (const listener of listeners) listener({
            target: { closest: () => ({ disabled: false, getAttribute: () => "route-display" }) },
            preventDefault() {},
        });
        await new Promise((resolve) => setImmediate(resolve));
        assert.equal(routeCalls, 1);
    } finally {
        await rm(modules.moduleRoot, { recursive: true, force: true });
    }
});

test("calendar adapter calls only versioned routing/override/open contracts and leaves retry unsupported", async () => {
    const modules = await importCalendarExtensionModules();
    try {
        const calls = [];
        const runtimeWindow = {
            open(url, target, features) { calls.push({ type: "open", url, target, features }); return {}; },
        };
        const adapter = modules.adapter.createCalendarDataAdapter({
            window: runtimeWindow,
            fetch: async (url, options) => {
                calls.push({ type: "fetch", url, options });
                return { ok: true, json: async () => ({ ok: true }) };
            },
        });
        await adapter.setCanvasRouting({ sourceId: "source-1", state: "completed", destinationCalendarId: "local:done" });
        assert.equal(calls[0].url, "/api/extension/calendar/sources/source-1/routing");
        assert.equal(calls[0].options.method, "PUT");
        assert.deepEqual(JSON.parse(calls[0].options.body), {
            state: "completed", destination_calendar_id: "local:done", fallback_calendar_id: null,
        });
        await adapter.setDisplayOverride({ eventRef: "canvas:event-1", calendarId: "local:done" });
        assert.equal(calls[1].url, "/api/calendar/event-overrides");
        await adapter.openSafeSourceUrl({ url: "https://canvas.example.edu" });
        assert.deepEqual(calls[2], {
            type: "open", url: "https://canvas.example.edu", target: "_blank", features: "noopener,noreferrer",
        });
        const unsafe = await adapter.openSafeSourceUrl({ url: "https://canvas.example.edu/path?token=secret" });
        assert.equal(unsafe.state, "unsupported");
        assert.equal(adapter.actionSupport.retryWriteback, false);
        assert.equal((await adapter.retryWriteback()).state, "unsupported");
    } finally {
        await rm(modules.moduleRoot, { recursive: true, force: true });
    }
});

test("calendar extension artifact is manifest-hashed, local-only, scoped, secure, and reproducible", async () => {
    const firstDirectory = await mkdtemp(path.join(os.tmpdir(), "apstudy-calendar-extension-build-"));
    const secondDirectory = await mkdtemp(path.join(os.tmpdir(), "apstudy-calendar-extension-build-"));
    const filenames = ["calendar-extension.v1.js", "calendar-extension.v1.css", "manifest.json"];
    try {
        await buildCalendarExtension(firstDirectory);
        await buildCalendarExtension(secondDirectory);
        for (const filename of filenames) {
            const first = await readFile(path.join(firstDirectory, filename));
            const second = await readFile(path.join(secondDirectory, filename));
            assert.deepEqual(first, second, `${filename} differs between identical builds`);
        }

        const manifest = JSON.parse(await readFile(path.join(firstDirectory, "manifest.json"), "utf8"));
        assert.equal(manifest.contract_version, 1);
        assert.deepEqual(manifest.files.map(({ filename }) => filename), filenames.slice(0, 2));
        for (const { filename, sha256 } of manifest.files) {
            const bytes = await readFile(path.join(firstDirectory, filename));
            assert.equal(createHash("sha256").update(bytes).digest("hex"), sha256, filename);
        }
        const javascript = await readFile(path.join(firstDirectory, manifest.entry), "utf8");
        const stylesheet = await readFile(path.join(firstDirectory, manifest.stylesheet), "utf8");
        assert.doesNotMatch(javascript, /(?:from|import)\s*["']https?:\/\//);
        assert.doesNotMatch(javascript, /\beval\s*\(|new Function\s*\(/);
        assert.doesNotMatch(stylesheet, /@import\b|url\(\s*["']?(?:https?:|\/\/|data:|javascript:)/i);
        assert.doesNotMatch(stylesheet, /(?:^|[,{])\s*(?:html|body|:root|\.thenav|\.thefooter)\b/m);
        assert.match(javascript, /APStudyCalendarExtension/);
        assert.match(javascript, /contractVersion/);
    } finally {
        await rm(firstDirectory, { recursive: true, force: true });
        await rm(secondDirectory, { recursive: true, force: true });
    }
});

test("calendar extension build policy rejects remote executable imports before publication", async () => {
    const fixtureDirectory = await mkdtemp(path.join(os.tmpdir(), "apstudy-calendar-extension-policy-"));
    const outputDirectory = await mkdtemp(path.join(os.tmpdir(), "apstudy-calendar-extension-build-"));
    const fixtureEntry = path.join(fixtureDirectory, "entry.js");
    const sentinelPath = path.join(outputDirectory, "sentinel.txt");
    try {
        await writeFile(fixtureEntry, 'import "https://evil.example/remote.js";\n');
        await writeFile(sentinelPath, "previous trusted output\n");
        await assert.rejects(
            buildCalendarExtension(outputDirectory, { entry: fixtureEntry }),
            /remote executable import/,
        );
        assert.equal(await readFile(sentinelPath, "utf8"), "previous trusted output\n");
        await assert.rejects(validateCalendarExtensionSourceGraph(fixtureEntry), /remote executable import/);
    } finally {
        await rm(fixtureDirectory, { recursive: true, force: true });
        await rm(outputDirectory, { recursive: true, force: true });
    }
});

test("calendar extension build policy rejects dynamic-code call variants without replacing published artifacts", async () => {
    const fixtureDirectory = await mkdtemp(path.join(os.tmpdir(), "apstudy-calendar-extension-policy-"));
    const publishedDirectory = await mkdtemp(path.join(os.tmpdir(), "apstudy-calendar-extension-build-"));
    const stagedOutputDirectory = await mkdtemp(path.join(os.tmpdir(), "apstudy-calendar-extension-build-"));
    const fixtureEntry = path.join(fixtureDirectory, "entry.js");
    const sentinelPath = path.join(publishedDirectory, "sentinel.txt");
    const unsafeFixtures = [
        { label: "direct eval", source: 'eval("payload");\n', error: /forbidden eval call/ },
        { label: "optional eval", source: 'eval?.("payload");\n', error: /forbidden eval call/ },
        { label: "commented eval", source: 'eval /* comment */ ("payload");\n', error: /forbidden eval call/ },
        { label: "spaced optional eval", source: 'eval /* before */ ?. /* after */ ("payload");\n', error: /forbidden eval call/ },
        { label: "direct Function", source: 'Function("return 1");\n', error: /forbidden Function constructor call/ },
        { label: "optional Function", source: 'Function?.("return 1");\n', error: /forbidden Function constructor call/ },
        { label: "commented Function", source: 'Function /* comment */ ("return 1");\n', error: /forbidden Function constructor call/ },
        { label: "new Function", source: 'new Function("return 1");\n', error: /forbidden Function constructor call/ },
        {
            label: "spaced commented new Function",
            source: 'new /* before */ Function /* after */ (\n    "return 1"\n);\n',
            error: /forbidden Function constructor call/,
        },
    ];
    const safeNearMisses = `
        const evaluate = (value) => value;
        function FunctionLabel() { return "safe"; }
        const labels = {
            eval() { return "safe"; },
            Function() { return "safe"; },
        };
        evaluate("safe");
        FunctionLabel();
        labels.eval();
        labels.Function();
    `;
    try {
        await writeFile(sentinelPath, "previous trusted output\n");
        await writeFile(path.join(stagedOutputDirectory, "calendar-extension.v1.css"), "", "utf8");
        for (const fixture of unsafeFixtures) {
            await writeFile(fixtureEntry, fixture.source, "utf8");
            await assert.rejects(
                validateCalendarExtensionSourceGraph(fixtureEntry),
                fixture.error,
                `${fixture.label} passed source-graph validation`,
            );
            await assert.rejects(
                buildCalendarExtension(publishedDirectory, { entry: fixtureEntry }),
                fixture.error,
                `${fixture.label} build was not rejected`,
            );
            assert.equal(
                await readFile(sentinelPath, "utf8"),
                "previous trusted output\n",
                `${fixture.label} replaced published artifacts`,
            );

            await writeFile(
                path.join(stagedOutputDirectory, "calendar-extension.v1.js"),
                fixture.source,
                "utf8",
            );
            await assert.rejects(
                validateCalendarExtensionOutput(stagedOutputDirectory),
                fixture.error,
                `${fixture.label} passed staged-output validation`,
            );
        }

        await writeFile(fixtureEntry, safeNearMisses, "utf8");
        await validateCalendarExtensionSourceGraph(fixtureEntry);
        await writeFile(
            path.join(stagedOutputDirectory, "calendar-extension.v1.js"),
            safeNearMisses,
            "utf8",
        );
        await validateCalendarExtensionOutput(stagedOutputDirectory);
    } finally {
        await rm(fixtureDirectory, { recursive: true, force: true });
        await rm(publishedDirectory, { recursive: true, force: true });
        await rm(stagedOutputDirectory, { recursive: true, force: true });
    }
});
