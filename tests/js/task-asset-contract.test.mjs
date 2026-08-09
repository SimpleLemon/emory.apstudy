import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const read = (relativePath) => readFile(path.join(repoRoot, relativePath), "utf8");

test("Tasks loads one local bundle without framework import maps or runtime CDNs", async () => {
    const [template, bundle, config, packageJson] = await Promise.all([
        read("templates/task.html"),
        read("static/js/tasks/dist/task-app.js"),
        read("vite.tasks.config.mjs"),
        read("package.json"),
    ]);

    assert.doesNotMatch(template, /type="importmap"|esm\.sh|cdn\.jsdelivr\.net|unpkg\.com/);
    assert.match(template, /js\/tasks\/task-loader\.js/);
    assert.match(template, /data-task-app-entry[^>]+js\/tasks\/dist\/task-app\.js/);
    assert.ok(template.indexOf("js/tasks/task-loader.js") < template.indexOf("js/tasks/dist/task-app.js"));
    assert.doesNotMatch(bundle, /(?:from\s*|import\s*\()\s*["']https?:\/\//);
    assert.doesNotMatch(bundle, /esm\.sh|cdn\.jsdelivr\.net|unpkg\.com/);
    assert.equal((bundle.match(/react\.production\.min\.js/g) || []).length, 1);
    assert.equal((bundle.match(/Sortable 1\.15\.0/g) || []).length, 1);
    assert.ok(Buffer.byteLength(bundle) < 400_000, "Tasks bundle should remain below 400 KB uncompressed");
    assert.match(config, /dedupe:\s*\["react", "react-dom"\]/);
    assert.match(config, /inlineDynamicImports:\s*true/);
    assert.match(config, /entryFileNames:\s*"task-app\.js"/);

    const parsedPackage = JSON.parse(packageJson);
    assert.equal(parsedPackage.dependencies.sortablejs, "1.15.0");
    assert.equal(parsedPackage.scripts["build:tasks"], "vite build --config vite.tasks.config.mjs");
});

test("Tasks stylesheet uses the APStudy primary fallback", async () => {
    const taskCss = await read("static/css/task.css");

    assert.match(taskCss, /var\(--color-primary, #0060aa\)/);
    assert.doesNotMatch(taskCss, /var\(--color-primary, #3b82f6\)/);
});

function createLoaderEnvironment() {
    const windowListeners = new Map();
    const documentListeners = new Map();
    const timers = [];
    const document = {
        activeElement: null,
        createElement(tagName) {
            return createElement(tagName);
        },
        getElementById(id) {
            return id === "task-root" ? root : null;
        },
        addEventListener(type, listener) {
            documentListeners.set(type, listener);
        },
    };
    function createElement(tagName) {
        return {
            tagName: tagName.toUpperCase(),
            attributes: new Map(),
            children: [],
            className: "",
            dataset: {},
            textContent: "",
            setAttribute(name, value) { this.attributes.set(name, value); },
            getAttribute(name) { return this.attributes.get(name); },
            addEventListener(type, listener) { this.listeners ||= new Map(); this.listeners.set(type, listener); },
            append(...children) { this.children.push(...children); },
            appendChild(child) { this.children.push(child); },
            replaceChildren(...children) { this.children = children; },
            focus() { document.activeElement = this; },
        };
    }
    const root = createElement("div");
    root.dataset.taskAppState = "loading";
    const window = {
        APSTUDY_TASK_LOAD_TIMEOUT_MS: 0,
        location: { reload() {} },
        addEventListener(type, listener) { windowListeners.set(type, listener); },
        setTimeout(callback) { timers.push(callback); return timers.length; },
        clearTimeout() {},
    };
    return { document, documentListeners, root, timers, window, windowListeners };
}

async function loadTaskLoader(environment, suffix) {
    globalThis.window = environment.window;
    globalThis.document = environment.document;
    const source = await read("static/js/tasks/task-loader.js");
    await import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}#${suffix}`);
}

test("task loader replaces a stranded skeleton with a focused visible error", async () => {
    const environment = createLoaderEnvironment();
    await loadTaskLoader(environment, "failure");
    environment.windowListeners.get("error")({
        target: { matches: (selector) => selector === "script[data-task-app-entry]" },
    });

    assert.equal(environment.root.dataset.taskAppState, "error");
    assert.equal(environment.root.children.length, 1);
    const panel = environment.root.children[0];
    assert.equal(panel.getAttribute("role"), "alert");
    assert.equal(panel.getAttribute("aria-live"), "assertive");
    assert.equal(panel.children[1].textContent, "Tasks couldn’t start");
    assert.equal(panel.children[3].textContent, "Reload Tasks");
    assert.equal(environment.document.activeElement, panel.children[3]);
});

test("a successfully initialized local task bundle disarms the error watchdog", async () => {
    const environment = createLoaderEnvironment();
    await loadTaskLoader(environment, "success");
    environment.window.APStudyTaskLoader.ready();
    environment.documentListeners.get("DOMContentLoaded")();
    environment.windowListeners.get("error")({
        target: { matches: () => true },
    });

    assert.equal(environment.root.dataset.taskAppState, "ready");
    assert.equal(environment.root.children.length, 0);
    assert.equal(environment.timers.length, 0);
});
