import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");

async function loadFilesUtils() {
    const source = await readFile(path.join(repoRoot, "static/js/files/utils.js"), "utf8");
    const document = {
        getElementById() {
            return null;
        },
    };
    const context = {
        document,
        window: { APStudyUIPrimitives: { escapeHtml: String } },
        Date,
        fetch() {
            throw new Error("fetch should not run while loading utils");
        },
    };
    vm.runInNewContext(source, context);
    return context.window.APStudyFilesUtils;
}

test("upload helpers parse JSON fallback and map HTTP status codes", async () => {
    const utils = await loadFilesUtils();

    const xhr413 = {
        status: 413,
        response: null,
        responseText: "<html>Request Entity Too Large</html>",
        getResponseHeader() {
            return "text/html";
        },
    };
    assert.equal(utils.parseUploadResponse(xhr413), null);
    assert.equal(
        utils.uploadErrorMessage(xhr413, null),
        "File is too large for the server upload limit.",
    );

    const xhr400 = {
        status: 400,
        response: null,
        responseText: JSON.stringify({
            error: "File exceeds the storage bucket size limit.",
            errors: [{ index: 0, error: "File exceeds the storage bucket size limit." }],
        }),
        getResponseHeader() {
            return "application/json";
        },
    };
    const payload = utils.parseUploadResponse(xhr400);
    assert.equal(payload.error, "File exceeds the storage bucket size limit.");
    assert.equal(utils.uploadErrorMessage(xhr400, payload), payload.error);

    const xhrNetwork = { status: 0, response: null, responseText: "", getResponseHeader() { return ""; } };
    assert.equal(
        utils.uploadErrorMessage(xhrNetwork, null),
        "Network error during upload. Check your connection.",
    );
});

test("files workflows use upload response helpers and notify on failure", async () => {
    const workflowsSource = await readFile(path.join(repoRoot, "static/js/files/workflows.js"), "utf8");
    const indexSource = await readFile(path.join(repoRoot, "static/js/files/index.js"), "utf8");
    const modalsSource = await readFile(path.join(repoRoot, "static/js/files/modals.js"), "utf8");
    const templateSource = await readFile(path.join(repoRoot, "templates/files.html"), "utf8");
    const fileInputMatch = templateSource.match(/<input[^>]+id="file-share-input"[^>]*>/);

    assert.match(workflowsSource, /parseUploadResponse\(xhr\)/);
    assert.match(workflowsSource, /uploadErrorMessage\(xhr, payload\)/);
    assert.match(workflowsSource, /notify\(message, "error", \{ modalError: els\.uploadError \}\)/);
    assert.match(indexSource, /callbacks:\s*\{[\s\S]*parseUploadResponse,[\s\S]*uploadErrorMessage,/);
    assert.match(indexSource, /function notify\(message, type = "info", options = \{\}\)/);
    assert.match(modalsSource, /notify\(error\.message \|\| "Try again in a moment\.", "error", \{ modalError: els\.folderError, title: "Couldn’t save changes" \}\)/);
    assert.ok(fileInputMatch);
    assert.match(fileInputMatch[0], /type="file"/);
    assert.match(fileInputMatch[0], /multiple/);
    assert.match(fileInputMatch[0], /class="files-visually-hidden"/);
    assert.doesNotMatch(fileInputMatch[0], /\shidden(?:\s|=|>)/);
});
