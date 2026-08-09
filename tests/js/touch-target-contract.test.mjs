import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { readCssSource } from "./helpers/css-source.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const source = (file) => readFile(path.join(root, file), "utf8");

test("shared touch targets use one coarse-pointer contract without resizing calendar event geometry", async () => {
    const globalStyles = await readCssSource(root, "static/css/global.css");
    assert.match(globalStyles, /--touch-target-min:\s*44px/);
    assert.match(globalStyles, /--touch-target-gap:\s*4px/);
    assert.match(globalStyles, /@media \(pointer: coarse\)/);
    assert.match(globalStyles, /\[data-event-ref\]/);
    assert.match(globalStyles, /\.workspace-page-shell, \.chat-app, \.onboarding-shell/);
});

test("every audited application surface loads the shared target contract", async () => {
    const templates = [
        "calendar.html", "chat.html", "notes.html", "notes_editor.html", "task.html",
        "dashboard.html", "settings.html", "files.html", "onboarding.html",
    ];
    for (const template of templates) {
        const html = await source(`templates/${template}`);
        assert.match(html, /css\/global\.css/, `${template} must load global.css`);
    }
});
