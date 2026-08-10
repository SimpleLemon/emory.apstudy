import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const source = await readFile(path.join(repoRoot, "static/js/tasks/task-floating.js"), "utf8");
const moduleUrl = `data:text/javascript;charset=utf-8,${encodeURIComponent(source)}`;

globalThis.window = { innerWidth: 390, innerHeight: 844 };
globalThis.document = { documentElement: { clientWidth: 390, clientHeight: 844 } };

const { composedEventPath, getFloatingPosition, shouldCloseFloatingLayer } = await import(moduleUrl);

function floatingNode(attributes = {}) {
    return {
        getAttribute(name) {
            return Object.prototype.hasOwnProperty.call(attributes, name) ? attributes[name] : null;
        },
    };
}

function pointerEvent(path) {
    return { target: path[0], composedPath: () => path };
}

test("floating layers stay below an anchor when there is room", () => {
    assert.deepEqual(
        getFloatingPosition(
            { top: 100, right: 160, bottom: 140, left: 100 },
            { width: 180, height: 200 },
            { align: "start" },
        ),
        { top: 148, left: 100 },
    );
});

test("floating layers flip above an anchor near the viewport bottom", () => {
    assert.deepEqual(
        getFloatingPosition(
            { top: 760, right: 340, bottom: 800, left: 300 },
            { width: 180, height: 120 },
            { align: "end" },
        ),
        { top: 632, left: 160 },
    );
});

test("floating layers clamp to the viewport when an anchor is near an edge", () => {
    assert.deepEqual(
        getFloatingPosition(
            { top: 300, right: 390, bottom: 340, left: 370 },
            { width: 260, height: 100 },
            { align: "start" },
        ),
        { top: 348, left: 120 },
    );
});

test("nested portal selections stay inside their explicit floating-layer owner", () => {
    const parentLayer = floatingNode();
    const nestedPortal = floatingNode({ "data-task-floating-owner": "quick-add-1" });
    const option = floatingNode();

    assert.equal(shouldCloseFloatingLayer(pointerEvent([option, nestedPortal]), {
        layers: [parentLayer],
        owner: "quick-add-1",
        triggerAttribute: "data-task-add-popover-trigger",
    }), false);
    assert.equal(shouldCloseFloatingLayer(pointerEvent([option]), {
        layers: [parentLayer],
        owner: "quick-add-1",
        triggerAttribute: "data-task-add-popover-trigger",
    }), true);
});

test("floating-layer close checks remain isolated across repeated opens", () => {
    const parentLayer = floatingNode();
    const firstPortal = floatingNode({ "data-task-floating-owner": "quick-add-1" });
    const secondPortal = floatingNode({ "data-task-floating-owner": "quick-add-2" });

    assert.equal(shouldCloseFloatingLayer(pointerEvent([firstPortal]), { layers: [parentLayer], owner: "quick-add-1" }), false);
    assert.equal(shouldCloseFloatingLayer(pointerEvent([firstPortal]), { layers: [parentLayer], owner: "quick-add-2" }), true);
    assert.equal(shouldCloseFloatingLayer(pointerEvent([secondPortal]), { layers: [parentLayer], owner: "quick-add-2" }), false);
});

test("floating-layer checks fall back to a parent path when composedPath is unavailable", () => {
    const parentLayer = floatingNode();
    const nestedPortal = { parentNode: parentLayer };
    assert.deepEqual(composedEventPath({ target: nestedPortal }).slice(0, 2), [nestedPortal, parentLayer]);
    assert.equal(shouldCloseFloatingLayer({ target: nestedPortal }, { layers: [parentLayer] }), false);
});
