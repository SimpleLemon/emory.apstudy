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

const { getFloatingPosition } = await import(moduleUrl);

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
