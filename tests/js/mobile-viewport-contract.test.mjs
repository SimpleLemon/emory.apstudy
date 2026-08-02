import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const source = (file) => readFile(path.join(root, file), "utf8");

test("full-height application shells use the shared dynamic viewport fallback", async () => {
    const files = [
        "static/css/layout.css", "static/css/chat.css",
        "static/css/onboarding.css", "static/css/courses.css", "static/css/notes.css",
        "static/css/landing.css", "static/css/landing-demos.css",
    ];
    for (const file of files) {
        const css = await source(file);
        assert.match(css, /--app-viewport-height/, `${file} must use the shared viewport height`);
    }
    const globalStyles = await source("static/css/global.css");
    assert.match(globalStyles, /--app-viewport-height:\s*100vh/);
    assert.match(globalStyles, /@supports \(height: 100dvh\)/);
    assert.match(globalStyles, /\.min-h-screen[\s\S]*var\(--app-viewport-height\)/);
});

test("visual viewport sizing is loaded by the shared runtime and listens for keyboard-driven resizes", async () => {
    const globalRuntime = await source("static/js/core/global.js");
    const viewportRuntime = await source("static/js/core/viewport-sizing.js");
    assert.match(globalRuntime, /viewport-sizing\.js/);
    assert.match(viewportRuntime, /visualViewport/);
    assert.match(viewportRuntime, /addEventListener\?\.\("resize", update/);
    assert.match(viewportRuntime, /pointer: coarse/);
    assert.match(viewportRuntime, /removeProperty\("--app-viewport-height"\)/);
});

test("mobile full-height shells retain safe-area padding", async () => {
    const [layout, chat, onboarding] = await Promise.all([
        source("static/css/layout.css"), source("static/css/chat.css"), source("static/css/onboarding.css"),
    ]);
    assert.match(layout, /safe-area-inset-bottom/);
    assert.match(chat, /safe-area-inset-bottom/);
    assert.match(onboarding, /safe-area-inset-(left|right|bottom)/);
});
