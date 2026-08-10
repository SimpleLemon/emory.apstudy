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

async function loadFilesWorkflows() {
    const source = await readFile(path.join(repoRoot, "static/js/files/workflows.js"), "utf8");
    const context = {
        window: {
            APStudyHttp: {},
            APStudyPendingMutations: {
                begin() {
                    return () => {};
                },
            },
        },
        FormData: class {
            append() {}
        },
    };
    vm.runInNewContext(source, context);
    return { workflows: context.window.APStudyFilesWorkflows, context };
}

function createUploadHarness(workflows, {
    context = null,
    useCoreSender = false,
    sendUpload,
    status = 201,
    payload = { uploaded: 1 },
} = {}) {
    const button = {
        disabled: false,
        classList: { toggle(_name, value) { button.busy = value; } },
        busy: false,
    };
    const progressWrap = { hidden: true };
    const progressBar = { style: {} };
    const state = {
        uploadItems: [{
            id: "upload-1",
            file: { name: "notes.pdf", size: 1 },
            name: "notes.pdf",
            visibility: "private",
            expiryDays: "7",
        }],
        uploadTargetFolderId: null,
        currentFolderId: null,
    };
    const els = {
        uploadButton: button,
        progressWrap,
        progressBar,
        uploadError: {},
    };
    const events = {};
    const notifications = [];
    const alerts = [];
    let senderCalls = 0;
    let corePromise = null;
    let folderLoads = 0;
    let closed = 0;
    const xhr = {
        status,
        responseType: "json",
        response: payload,
        getResponseHeader() { return "application/json"; },
    };
    const workflow = workflows.createFilesWorkflows({
        state,
        els,
        constants: {
            allowedExpiry: [1, 3, 7],
            defaultExpiry: 7,
            maxFileSizeBytes: 10,
            maxFileSizeLabel: "10 B",
            maxUploadFiles: 5,
        },
        callbacks: {
            clearFormError() {},
            closeModal: { close() { closed += 1; }, open() {} },
            firstUploadError(body) { return body?.errors?.[0]?.error || ""; },
            loadFolder: async () => { folderLoads += 1; },
            normalizeFolderId(value) { return value; },
            notify(message) { notifications.push(message); },
            parseUploadResponse(request) {
                return request?.response || null;
            },
            sendUpload(options) {
                senderCalls += 1;
                if (sendUpload) return sendUpload(options, xhr, events);
                Object.assign(events, options);
                return xhr;
            },
            setButtonBusy(target, busy) {
                target.disabled = busy;
                target.classList.toggle("is-busy", busy);
            },
            showAlert(message, type) { alerts.push({ message, type }); },
            uploadErrorMessage(request, body) {
                if (body?.error) return body.error;
                if (request?.status === 413) return "File is too large for the server upload limit.";
                if (request?.status === 502) return "Upload timed out. Try again or use a smaller file.";
                if (request?.status === 0) return "Network error during upload. Check your connection.";
                return `Upload failed (HTTP ${request?.status}).`;
            },
            apiJson() {},
            copyText() {},
            cssEscape(value) { return value; },
            downloadBlob() {},
            filenameFromDisposition() {},
            formatCount() {},
            formatExpiry() {},
            getFolderName() {},
            renderManager() {},
            shareExpiryOptionsHtml() {},
            showFormError() {},
            uploadItemHtml() { return ""; },
            expiryOptionForDate() {},
        },
    });
    if (useCoreSender) {
        context.window.APStudyHttp.uploadXhr = (url, options) => {
            senderCalls += 1;
            assert.equal(url, "/api/files/upload");
            assert.equal(options.pendingLabel, "file-upload");
            Object.assign(events, options);
            const finish = context.window.APStudyPendingMutations.begin(options.pendingLabel);
            corePromise = Promise.resolve(xhr).finally(finish);
            return corePromise;
        };
    }
    return {
        workflow,
        events,
        xhr,
        state,
        button,
        progressWrap,
        progressBar,
        notifications,
        alerts,
        get senderCalls() { return senderCalls; },
        get corePromise() { return corePromise; },
        get folderLoads() { return folderLoads; },
        get closed() { return closed; },
    };
}

test("upload helpers safely parse JSON, text, HTML, and proxy responses", async () => {
    const utils = await loadFilesUtils();

    let responseTextReads = 0;
    const xhrJson = {
        status: 201,
        responseType: "json",
        response: { uploaded: 1 },
        get responseText() {
            responseTextReads += 1;
            throw new Error("responseText is invalid for json responseType");
        },
        getResponseHeader() {
            return "application/json";
        },
    };
    assert.equal(utils.parseUploadResponse(xhrJson).uploaded, 1);
    assert.equal(responseTextReads, 0);

    const xhrText = {
        responseType: "text",
        response: JSON.stringify({ error: "Text response" }),
        responseText: JSON.stringify({ error: "Text response" }),
        getResponseHeader() { return "text/plain"; },
    };
    assert.equal(utils.parseUploadResponse(xhrText).error, "Text response");

    const xhrHtml = {
        status: 400,
        responseType: "json",
        response: null,
        get responseText() {
            throw new Error("HTML proxy response must not be read as JSON text");
        },
        getResponseHeader() { return "text/html"; },
    };
    assert.equal(utils.parseUploadResponse(xhrHtml), null);

    const xhrParseFailure = {
        responseType: "text",
        responseText: "not-json",
        getResponseHeader() { return "text/plain"; },
    };
    assert.equal(utils.parseUploadResponse(xhrParseFailure), null);

    const xhr413 = {
        status: 413,
        responseType: "json",
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
        responseType: "text",
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

    assert.equal(
        utils.uploadErrorMessage({ status: 400, responseType: "json", response: null }, null),
        "Upload failed (HTTP 400).",
    );
    assert.equal(
        utils.uploadErrorMessage({ status: 502, responseType: "json", response: null }, null),
        "Upload timed out. Try again or use a smaller file.",
    );

    const xhrNetwork = { status: 0, response: null, responseText: "", getResponseHeader() { return ""; } };
    assert.equal(
        utils.uploadErrorMessage(xhrNetwork, null),
        "Network error during upload. Check your connection.",
    );
});

test("upload workflow preserves progress and cleans up after JSON 201 partial success", async () => {
    const { workflows } = await loadFilesWorkflows();
    const harness = createUploadHarness(workflows, {
        payload: { errors: [{ error: "One file was skipped." }] },
    });
    await harness.workflow.uploadSelectedFiles();
    assert.equal(harness.senderCalls, 1);
    assert.equal(harness.events.responseType, "json");
    harness.events.onProgress({ lengthComputable: true, loaded: 50, total: 100 });
    assert.equal(harness.progressBar.style.transform, "scaleX(0.5)");
    await harness.events.onLoad({ currentTarget: harness.xhr });
    assert.equal(harness.folderLoads, 1);
    assert.equal(harness.closed, 1);
    assert.deepEqual(harness.alerts, [{ message: "One file was skipped.", type: "error" }]);
    assert.equal(harness.button.disabled, false);
    assert.equal(harness.progressWrap.hidden, true);
});

test("upload workflow prefers APStudyHttp.uploadXhr and does not double-track pending work", async () => {
    const { workflows, context } = await loadFilesWorkflows();
    let pendingStarts = 0;
    let pendingEnds = 0;
    context.window.APStudyPendingMutations.begin = () => {
        pendingStarts += 1;
        return () => { pendingEnds += 1; };
    };
    const harness = createUploadHarness(workflows, { context, useCoreSender: true });
    await harness.workflow.uploadSelectedFiles();
    harness.events.onProgress({ lengthComputable: true, loaded: 25, total: 100 });
    await harness.corePromise;
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(harness.senderCalls, 1);
    assert.equal(typeof harness.events.onProgress, "function");
    assert.equal(pendingStarts, 1);
    assert.equal(pendingEnds, 1);
    assert.equal(harness.button.disabled, false);
});

test("upload workflow maps HTML HTTP failures and always clears busy state", async () => {
    const { workflows } = await loadFilesWorkflows();
    for (const status of [400, 413, 502]) {
        const harness = createUploadHarness(workflows, { status, payload: null });
        await harness.workflow.uploadSelectedFiles();
        await harness.events.onLoad({ currentTarget: harness.xhr });
        assert.equal(harness.button.disabled, false, `HTTP ${status} button cleanup`);
        assert.equal(harness.progressWrap.hidden, true, `HTTP ${status} progress cleanup`);
        assert.match(harness.notifications[0], status === 413
            ? /too large/
            : status === 502 ? /timed out/ : /HTTP 400/);
    }
});

test("upload workflow maps network errors, aborts, and sender failures without leaking pending state", async () => {
    const { workflows, context } = await loadFilesWorkflows();
    let pendingEnds = 0;
    context.window.APStudyPendingMutations.begin = () => () => { pendingEnds += 1; };

    const network = createUploadHarness(workflows, { status: 0, payload: null });
    await network.workflow.uploadSelectedFiles();
    network.events.onError({ currentTarget: network.xhr });
    assert.match(network.notifications[0], /Network error/);
    assert.equal(network.button.disabled, false);
    assert.equal(network.progressWrap.hidden, true);

    const aborted = createUploadHarness(workflows);
    await aborted.workflow.uploadSelectedFiles();
    aborted.events.onAbort();
    assert.equal(aborted.button.disabled, false);
    assert.equal(aborted.progressWrap.hidden, true);

    const senderError = createUploadHarness(workflows, {
        sendUpload() { throw new Error("Unable to start upload"); },
    });
    await senderError.workflow.uploadSelectedFiles();
    assert.equal(senderError.button.disabled, false);
    assert.equal(senderError.progressWrap.hidden, true);
    assert.equal(pendingEnds, 3);
});

test("files workflows use upload response helpers and notify on failure", async () => {
    const workflowsSource = await readFile(path.join(repoRoot, "static/js/files/workflows.js"), "utf8");
    const indexSource = await readFile(path.join(repoRoot, "static/js/files/index.js"), "utf8");
    const modalsSource = await readFile(path.join(repoRoot, "static/js/files/modals.js"), "utf8");
    const templateSource = await readFile(path.join(repoRoot, "templates/files.html"), "utf8");
    const fileInputMatch = templateSource.match(/<input[^>]+id="file-share-input"[^>]*>/);

    assert.match(workflowsSource, /parseUploadResponse\(request\)/);
    assert.match(workflowsSource, /uploadErrorMessage\(request, payload\)/);
    assert.match(workflowsSource, /sendUpload/);
    assert.match(workflowsSource, /APStudyHttp\?\.uploadXhr/);
    assert.match(workflowsSource, /pendingLabel: "file-upload"/);
    assert.match(workflowsSource, /responseType: "json"/);
    assert.match(workflowsSource, /onProgress/);
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
