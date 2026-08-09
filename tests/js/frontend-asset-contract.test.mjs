import assert from "node:assert/strict";
import { access, readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");

function repoPath(relativePath) {
    return path.join(repoRoot, relativePath);
}

async function sourceFor(relativePath) {
    return readFile(repoPath(relativePath), "utf8");
}

test("the orphaned browser Appwrite surface is gone", async () => {
    await assert.rejects(
        access(repoPath("static/js/core/appwrite.js")),
        (error) => error?.code === "ENOENT",
    );
    await assert.rejects(
        access(repoPath("templates/_appwrite_meta.html")),
        (error) => error?.code === "ENOENT",
    );

    const [packageSource, lockSource, globalSource, noteEditor, settings, tasks] = await Promise.all([
        sourceFor("package.json"),
        sourceFor("package-lock.json"),
        sourceFor("static/js/core/global.js"),
        sourceFor("templates/notes_editor.html"),
        sourceFor("templates/settings.html"),
        sourceFor("templates/task.html"),
    ]);

    assert.doesNotMatch(packageSource, /["']appwrite["']\s*:/);
    assert.doesNotMatch(lockSource, /node_modules\/appwrite|json-bigint|bignumber\.js/);
    assert.doesNotMatch(globalSource, /APStudyAppwriteSessionProbe|account\.deleteSession|probeAppwriteSession/);
    assert.doesNotMatch(noteEditor, /APPWRITE_DATABASE_ID|apstudy-appwrite-/);
    assert.doesNotMatch(settings, /data-probe-appwrite-session/);
    assert.doesNotMatch(tasks, /data-probe-appwrite-session/);
});

test("dashboard and notes use the deferred first-party Sortable bridge", async () => {
    const [dashboard, notes, bridgeSource, buildConfig, builtBridge] = await Promise.all([
        sourceFor("templates/dashboard.html"),
        sourceFor("templates/notes.html"),
        sourceFor("static/js/vendor/sortable-global.js"),
        sourceFor("vite.sortable.config.mjs"),
        sourceFor("static/js/vendor/dist/sortable-global.js"),
    ]);
    const localAsset = "js/vendor/dist/sortable-global.js";

    for (const [name, template, consumer] of [
        ["dashboard", dashboard, "js/dashboard/layout-editor.js"],
        ["notes", notes, "js/notes/list/drag-drop.js"],
    ]) {
        assert.doesNotMatch(template, /https:\/\/cdn\.jsdelivr\.net\/npm\/sortablejs/);
        const assetIndex = template.indexOf(localAsset);
        assert.ok(assetIndex >= 0, `${name} loads the local Sortable bridge`);
        assert.match(template.slice(assetIndex - 40, assetIndex + localAsset.length + 40), /defer/);
        assert.ok(assetIndex < template.indexOf(consumer), `${name} loads Sortable before its consumer`);
    }

    assert.match(bridgeSource, /import Sortable from ["']sortablejs["']/);
    assert.match(bridgeSource, /window\.Sortable = Sortable/);
    assert.match(buildConfig, /vite build|format: ["']iife["']/);
    assert.match(buildConfig, /static\/js\/vendor\/dist/);
    assert.match(builtBridge, /Sortable/);
});

test("the dashboard editor is the only dashboard Sortable owner", async () => {
    const dashboardSource = await sourceFor("static/js/dashboard/index.js");
    const editorSource = await sourceFor("static/js/dashboard/layout-editor.js");

    assert.doesNotMatch(dashboardSource, /\bSortable\b|state\.sortable|setupSortable|destroySortable/);
    assert.match(editorSource, /Sortable\.create/);
    assert.match(editorSource, /state\.sortable/);
});
