/* global queueMicrotask, URL */

import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import vm from "node:vm";

const globalSource = fs.readFileSync("static/js/core/global.js", "utf8");

function httpRuntimeSource() {
    const start = globalSource.indexOf("window.APStudyHttp = window.APStudyHttp || {");
    const end = globalSource.indexOf("\n\nfunction initializePresenceHeartbeat", start);
    return globalSource.slice(start, end);
}

function createXhrFactory(responses, created) {
    return () => {
        const response = responses.shift() || {};
        const xhr = {
            status: 0,
            response: null,
            responseType: "",
            timeout: 0,
            upload: {},
            requestHeaders: {},
            responseHeaders: response.headers || {},
            open(method, url) {
                this.method = method;
                this.url = url;
            },
            setRequestHeader(name, value) {
                this.requestHeaders[name] = value;
            },
            getResponseHeader(name) {
                const match = Object.entries(this.responseHeaders)
                    .find(([key]) => key.toLowerCase() === String(name).toLowerCase());
                return match?.[1] || null;
            },
            send(body) {
                this.body = body;
                this.upload.onprogress?.({ lengthComputable: true, loaded: 1, total: 2 });
                queueMicrotask(() => {
                    this.status = response.status ?? 0;
                    this.response = response.body ?? null;
                    (response.event === "error" ? this.onerror : this.onload)?.();
                });
            },
        };
        created.push(xhr);
        return xhr;
    };
}

function installHttpRuntime({ csrf, pendingMutations } = {}) {
    const window = {
        location: {
            href: "https://nest.apstudy.org/files",
            origin: "https://nest.apstudy.org",
        },
        APStudyCsrf: csrf,
        APStudyPendingMutations: pendingMutations,
    };
    vm.runInNewContext(httpRuntimeSource(), {
        window,
        URL,
        Error,
        XMLHttpRequest: class {},
    });
    return window.APStudyHttp;
}

test("upload XHR injects CSRF, refreshes once, preserves progress, and tracks the mutation", async () => {
    let token = "stale";
    let refreshes = 0;
    let tracked = 0;
    const progress = [];
    const created = [];
    const http = installHttpRuntime({
        csrf: {
            token: () => token,
            isFailure: (status, getHeader) => status === 400 && getHeader("X-APStudy-CSRF-Error") === "1",
            refresh: async () => {
                refreshes += 1;
                token = "fresh";
            },
        },
        pendingMutations: {
            track: async (promise, label) => {
                tracked += 1;
                assert.equal(label, "file-upload");
                return promise;
            },
        },
    });
    const xhrFactory = createXhrFactory([
        { status: 400, headers: { "X-APStudy-CSRF-Error": "1" } },
        { status: 201, body: { files: [{ id: "file-1" }] } },
    ], created);
    const body = { multipart: true };

    const result = await http.uploadXhr("/api/files/upload", {
        body,
        xhrFactory,
        pendingLabel: "file-upload",
        onProgress: (event) => progress.push(event.loaded),
    });

    assert.equal(result.status, 201);
    assert.equal(refreshes, 1);
    assert.equal(tracked, 1);
    assert.deepEqual(progress, [1, 1]);
    assert.equal(created.length, 2);
    assert.equal(created[0].requestHeaders["X-CSRFToken"], "stale");
    assert.equal(created[1].requestHeaders["X-CSRFToken"], "fresh");
    assert.equal(created[0].body, body);
    assert.equal(created[1].body, body);
});

test("upload XHR resolves network errors for feature-specific messaging without leaking CSRF cross-origin", async () => {
    const created = [];
    const http = installHttpRuntime({
        csrf: {
            token: () => "secret-token",
            isFailure: () => false,
            refresh: async () => {},
        },
    });
    const xhrFactory = createXhrFactory([{ status: 0, event: "error" }], created);

    const result = await http.uploadXhr("https://uploads.example.test/file", {
        body: "payload",
        xhrFactory,
    });

    assert.equal(result.status, 0);
    assert.equal(created[0].requestHeaders["X-CSRFToken"], undefined);
});
