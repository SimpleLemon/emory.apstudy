import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createServer } from 'node:http';
import { once } from 'node:events';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { after, before, test } from 'node:test';

import { HocuspocusProvider, HocuspocusProviderWebsocket } from '@hocuspocus/provider';
import * as Y from 'yjs';
import WebSocket from 'ws';

const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const ALLOWED_ORIGIN = 'https://allowed.test';

const wait = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

async function waitFor(predicate, { timeout = 5000, interval = 25 } = {}) {
    const deadline = Date.now() + timeout;
    let lastError;
    while (Date.now() < deadline) {
        try {
            const result = await predicate();
            if (result) return result;
        } catch (error) {
            lastError = error;
        }
        await wait(Math.min(interval, Math.max(1, deadline - Date.now())));
    }
    throw lastError || new Error(`Condition was not met within ${timeout}ms.`);
}

function waitForEvent(emitter, event, { predicate = () => true, timeout = 4000 } = {}) {
    return new Promise((resolve, reject) => {
        let timer;
        const handler = (payload) => {
            if (!predicate(payload)) return;
            clearTimeout(timer);
            emitter.off(event, handler);
            resolve(payload);
        };
        timer = setTimeout(() => {
            emitter.off(event, handler);
            reject(new Error(`Timed out waiting for ${event}.`));
        }, timeout);
        emitter.on(event, handler);
    });
}

function encodeDocument(document) {
    return Buffer.from(Y.encodeStateAsUpdate(document));
}

function decodeDocument(bytes) {
    const document = new Y.Doc();
    Y.applyUpdate(document, bytes);
    return document;
}

async function requestBody(request) {
    const chunks = [];
    for await (const chunk of request) chunks.push(chunk);
    return Buffer.concat(chunks).toString('utf8');
}

function sendJson(response, status, payload) {
    response.writeHead(status, { 'Content-Type': 'application/json' });
    response.end(JSON.stringify(payload));
}

function createFlaskFixture() {
    const state = {
        healthStatus: 200,
        requests: [],
        storeRequests: [],
        verifyRequests: [],
        storedDocuments: new Map(),
        tickets: new Map([
            ['write-ticket', {
                user_id: 'user-1',
                role: 'editor',
                can_write: true,
                public: false,
                anonymous: false,
                awareness_allowed: true,
                access_revision: 4,
            }],
            ['read-ticket', {
                user_id: 'user-2',
                role: 'viewer',
                can_write: false,
                public: false,
                anonymous: false,
                awareness_allowed: true,
                access_revision: 4,
            }],
        ]),
    };

    const server = createServer(async (request, response) => {
        const url = new URL(request.url, 'http://127.0.0.1');
        const body = await requestBody(request);
        state.requests.push({
            method: request.method,
            path: url.pathname,
            headers: request.headers,
            body,
        });

        if (url.pathname === '/api/internal/notes/collaboration-health') {
            const ok = state.healthStatus < 400;
            sendJson(response, state.healthStatus, {
                ok,
                schema_version: ok ? 1 : 0,
                persistence: 'fixture',
            });
            return;
        }

        if (url.pathname === '/api/internal/notes/collaboration-token/verify') {
            const payload = JSON.parse(body || '{}');
            state.verifyRequests.push(payload);
            const ticket = state.tickets.get(payload.ticket);
            if (!ticket) {
                sendJson(response, 401, { error: 'collaboration_token_invalid' });
                return;
            }
            sendJson(response, 200, {
                ok: true,
                note_id: payload.note_id,
                user: null,
                ...ticket,
            });
            return;
        }

        const documentMatch = url.pathname.match(
            /^\/api\/internal\/notes\/([^/]+)\/collaboration-document$/,
        );
        if (documentMatch) {
            const noteId = decodeURIComponent(documentMatch[1]);
            if (request.method === 'GET') {
                const document = state.storedDocuments.get(noteId);
                if (!document) {
                    sendJson(response, 404, { error: 'collaboration_document_not_found' });
                    return;
                }
                response.writeHead(200, { 'Content-Type': 'application/octet-stream' });
                response.end(document);
                return;
            }

            if (request.method === 'PUT') {
                const payload = JSON.parse(body || '{}');
                const document = Buffer.from(payload.ydoc_base64 || '', 'base64');
                state.storedDocuments.set(noteId, document);
                state.storeRequests.push({ noteId, payload, document });
                sendJson(response, 200, { ok: true, note_id: noteId });
                return;
            }
        }

        response.writeHead(404);
        response.end('not found');
    });

    return {
        state,
        async start() {
            await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
        },
        async stop() {
            if (server.listening) await new Promise((resolve) => server.close(resolve));
        },
        get url() {
            return `http://127.0.0.1:${server.address().port}`;
        },
    };
}

async function startCollaboration(fixture, options = {}) {
    const portProbe = createServer();
    await new Promise((resolve) => portProbe.listen(0, '127.0.0.1', resolve));
    const port = portProbe.address().port;
    await new Promise((resolve) => portProbe.close(resolve));

    const child = spawn(process.execPath, ['collaboration/server.mjs'], {
        cwd: REPO_ROOT,
        env: {
            ...process.env,
            NODE_ENV: 'testing',
            NOTES_COLLABORATION_HOST: '127.0.0.1',
            NOTES_COLLABORATION_PORT: String(port),
            NOTES_COLLABORATION_QUIET: '1',
            NOTES_COLLABORATION_ORIGINS: ALLOWED_ORIGIN,
            NOTES_COLLABORATION_INTERNAL_SECRET: 'fixture-secret',
            NOTES_COLLABORATION_SECRET: '',
            NOTES_COLLABORATION_MAX_UPDATE_BYTES: String(512 * 1024),
            NOTES_COLLABORATION_MAX_DOCUMENT_BYTES: String(options.maxDocumentBytes || 10 * 1024 * 1024),
            NEST_FLASK_INTERNAL_URL: fixture.url,
        },
        stdio: ['ignore', 'pipe', 'pipe'],
    });
    let output = '';
    child.stdout.on('data', (chunk) => { output += chunk.toString(); });
    child.stderr.on('data', (chunk) => { output += chunk.toString(); });

    const baseUrl = `http://127.0.0.1:${port}`;
    await waitFor(async () => {
        if (child.exitCode !== null) {
            throw new Error(`Collaboration server exited early: ${output}`);
        }
        try {
            const response = await fetch(`${baseUrl}/health`);
            return response.status === 200;
        } catch {
            return false;
        }
    });

    return {
        baseUrl,
        websocketUrl: `ws://127.0.0.1:${port}`,
        child,
        async stop() {
            if (child.exitCode !== null) return;
            const exited = once(child, 'exit');
            child.kill('SIGTERM');
            await Promise.race([exited, wait(2500)]);
            if (child.exitCode === null) {
                child.kill('SIGKILL');
                await exited;
            }
        },
    };
}

function createProvider(server, { name, token, origin = ALLOWED_ORIGIN, awareness = null }) {
    class OriginWebSocket extends WebSocket {
        constructor(address, protocols) {
            super(address, protocols, { headers: { Origin: origin } });
        }
    }

    const websocketProvider = new HocuspocusProviderWebsocket({
        url: server.websocketUrl,
        connect: false,
        WebSocketPolyfill: OriginWebSocket,
        delay: 10,
        initialDelay: 0,
        minDelay: 10,
        maxDelay: 100,
        maxAttempts: 1,
        timeout: 2000,
        jitter: false,
        messageReconnectTimeout: 10000,
        quiet: true,
    });
    const document = new Y.Doc();
    const provider = new HocuspocusProvider({
        name,
        document,
        token,
        awareness,
        broadcast: false,
        quiet: true,
        websocketProvider,
    });
    return { document, provider, websocketProvider };
}

async function destroyProvider(handle) {
    handle.provider.destroy();
    handle.websocketProvider.destroy();
}

let fixture;
let collaboration;

before(async () => {
    fixture = createFlaskFixture();
    await fixture.start();
    collaboration = await startCollaboration(fixture);
});

after(async () => {
    await collaboration?.stop();
    await fixture?.stop();
});

test('health endpoints report Flask readiness and preserve the internal secret boundary', async () => {
    const ready = await fetch(`${collaboration.baseUrl}/health`);
    const readyPayload = await ready.json();
    assert.equal(ready.status, 200);
    assert.equal(readyPayload.ok, true);
    assert.equal(readyPayload.status, 'ready');
    assert.equal(readyPayload.flask_status, 200);
    assert.match(readyPayload.started_at, /^20\d\d-/);

    const healthz = await fetch(`${collaboration.baseUrl}/healthz`);
    const healthzPayload = await healthz.json();
    assert.equal(healthz.status, 200);
    assert.equal(healthzPayload.started_at, readyPayload.started_at);

    fixture.state.healthStatus = 503;
    const degraded = await fetch(`${collaboration.baseUrl}/health`);
    const degradedPayload = await degraded.json();
    assert.equal(degraded.status, 503);
    assert.deepEqual(
        {
            ok: degradedPayload.ok,
            status: degradedPayload.status,
            flask_status: degradedPayload.flask_status,
        },
        { ok: false, status: 'degraded', flask_status: 503 },
    );
    fixture.state.healthStatus = 200;

    const healthRequest = [...fixture.state.requests]
        .reverse()
        .find((request) => request.path === '/api/internal/notes/collaboration-health');
    assert.equal(healthRequest.headers['x-nest-collaboration-secret'], 'fixture-secret');
});

test('authenticated clients load normalized documents and persist Yjs updates', async () => {
    const initial = new Y.Doc();
    initial.getMap('content').set('title', 'Saved title');
    fixture.state.storedDocuments.set('note-1', encodeDocument(initial));
    fixture.state.storeRequests.length = 0;

    const handle = createProvider(collaboration, {
        name: 'notes:note-1',
        token: 'write-ticket',
    });
    try {
        const synced = waitForEvent(handle.provider, 'synced', {
            predicate: (payload) => payload?.state === true,
        });
        handle.websocketProvider.connect();
        await synced;

        assert.equal(handle.document.getMap('content').get('title'), 'Saved title');
        assert.equal(handle.document.getMap('nest:meta').get('schemaVersion'), 1);
        assert.deepEqual(fixture.state.verifyRequests.at(-1), {
            ticket: 'write-ticket',
            note_id: 'note-1',
        });

        handle.document.getMap('content').set('title', 'Updated title');
        await waitFor(() => fixture.state.storeRequests.length > 0, { timeout: 5000 });
        const persisted = decodeDocument(fixture.state.storeRequests.at(-1).document);
        assert.equal(persisted.getMap('content').get('title'), 'Updated title');
        assert.equal(fixture.state.storeRequests.at(-1).payload.schema_version, 1);

        const documentRequest = [...fixture.state.requests]
            .reverse()
            .find((request) => request.path.endsWith('/collaboration-document'));
        assert.equal(documentRequest.headers['x-nest-collaboration-secret'], 'fixture-secret');
    } finally {
        await destroyProvider(handle);
    }
});

test('rejects disallowed origins and missing note IDs before ticket verification', async () => {
    const verifyCount = fixture.state.verifyRequests.length;
    const cases = [
        { name: 'notes:note-1', origin: 'https://evil.test' },
        { name: 'notes:', origin: ALLOWED_ORIGIN },
    ];

    for (const scenario of cases) {
        const handle = createProvider(collaboration, {
            name: scenario.name,
            token: 'write-ticket',
            origin: scenario.origin,
        });
        try {
            const failed = waitForEvent(handle.provider, 'authenticationFailed');
            handle.websocketProvider.connect();
            const payload = await failed;
            assert.equal(payload.reason, 'permission-denied');
        } finally {
            await destroyProvider(handle);
        }
    }

    assert.equal(fixture.state.verifyRequests.length, verifyCount);
});

test('returns Flask ticket failures without authenticating the WebSocket', async () => {
    const verifyCount = fixture.state.verifyRequests.length;
    const handle = createProvider(collaboration, {
        name: 'notes:note-1',
        token: 'invalid-ticket',
    });
    try {
        const failed = waitForEvent(handle.provider, 'authenticationFailed');
        handle.websocketProvider.connect();
        const payload = await failed;
        assert.equal(payload.reason, 'permission-denied');
    } finally {
        await destroyProvider(handle);
    }
    assert.equal(fixture.state.verifyRequests.length, verifyCount + 1);
    assert.equal(fixture.state.verifyRequests.at(-1).ticket, 'invalid-ticket');
});

test('allows read-only clients to sync without persisting their updates', async () => {
    fixture.state.storeRequests.length = 0;
    const handle = createProvider(collaboration, {
        name: 'notes:read-only',
        token: 'read-ticket',
    });
    try {
        const synced = waitForEvent(handle.provider, 'synced', {
            predicate: (payload) => payload?.state === true,
        });
        handle.websocketProvider.connect();
        await synced;
        handle.document.getMap('content').set('title', 'Must not persist');
        await wait(1100);
        assert.equal(fixture.state.storeRequests.length, 0);
        assert.equal(handle.provider.hasUnsyncedChanges, true);
    } finally {
        await destroyProvider(handle);
    }
});

test('rejects stored documents over the configured size limit', async () => {
    fixture.state.storedDocuments.set('oversized', Buffer.alloc(8, 7));
    const limited = await startCollaboration(fixture, { maxDocumentBytes: 4 });
    const handle = createProvider(limited, {
        name: 'notes:oversized',
        token: 'write-ticket',
    });
    try {
        const failed = waitForEvent(handle.provider, 'authenticationFailed');
        handle.websocketProvider.connect();
        const payload = await failed;
        assert.equal(payload.reason, 'permission-denied');
    } finally {
        await destroyProvider(handle);
        await limited.stop();
        fixture.state.storedDocuments.delete('oversized');
    }
    const oversizedRequest = [...fixture.state.requests]
        .reverse()
        .find((request) => request.path.endsWith('/oversized/collaboration-document'));
    assert.equal(oversizedRequest.method, 'GET');
});
