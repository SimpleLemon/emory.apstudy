import { Server } from '@hocuspocus/server';
import * as Y from 'yjs';

const HOST = process.env.NOTES_COLLABORATION_HOST || '127.0.0.1';
const PORT = Number(process.env.NOTES_COLLABORATION_PORT || 1234);
const FLASK_BASE_URL = (process.env.NEST_FLASK_INTERNAL_URL || 'http://127.0.0.1:8000').replace(/\/$/, '');
const INTERNAL_SECRET = process.env.NOTES_COLLABORATION_INTERNAL_SECRET || process.env.NOTES_COLLABORATION_SECRET || '';
const MAX_UPDATE_BYTES = Number(process.env.NOTES_COLLABORATION_MAX_UPDATE_BYTES || 512 * 1024);
const MAX_DOCUMENT_BYTES = Number(process.env.NOTES_COLLABORATION_MAX_DOCUMENT_BYTES || 10 * 1024 * 1024);
const ORIGIN_ALLOWLIST = (process.env.NOTES_COLLABORATION_ORIGINS || '')
    .split(',')
    .map((origin) => origin.trim())
    .filter(Boolean);

function normalizeNoteId(documentName) {
    const value = String(documentName || '').trim();
    if (!value) return '';
    return value.replace(/^notes[:/]/, '');
}

function internalHeaders(extra = {}) {
    return {
        'Content-Type': 'application/json',
        ...(INTERNAL_SECRET ? { 'X-Nest-Collaboration-Secret': INTERNAL_SECRET } : {}),
        ...extra,
    };
}

function requestOrigin(requestHeaders) {
    return String(requestHeaders?.origin || '').trim();
}

function validateOrigin(requestHeaders) {
    if (!ORIGIN_ALLOWLIST.length) return true;
    const origin = requestOrigin(requestHeaders);
    return ORIGIN_ALLOWLIST.includes(origin);
}

async function parseJsonResponse(response) {
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
        const message = payload.error || `Flask request failed with ${response.status}`;
        const error = new Error(message);
        error.status = response.status;
        error.payload = payload;
        throw error;
    }
    return payload;
}

async function verifyTicket(ticket, noteId) {
    const response = await fetch(`${FLASK_BASE_URL}/api/internal/notes/collaboration-token/verify`, {
        method: 'POST',
        headers: internalHeaders(),
        body: JSON.stringify({ ticket, note_id: noteId }),
    });
    return parseJsonResponse(response);
}

async function fetchStoredDocument(noteId) {
    const response = await fetch(`${FLASK_BASE_URL}/api/internal/notes/${encodeURIComponent(noteId)}/collaboration-document`, {
        headers: INTERNAL_SECRET ? { 'X-Nest-Collaboration-Secret': INTERNAL_SECRET } : {},
    });
    if (response.status === 404) return null;
    if (!response.ok) {
        throw new Error(`Unable to load collaboration document ${noteId}: ${response.status}`);
    }
    const bytes = new Uint8Array(await response.arrayBuffer());
    if (bytes.byteLength > MAX_DOCUMENT_BYTES) {
        throw new Error(`Collaboration document ${noteId} exceeds size limit.`);
    }
    return bytes;
}

async function storeDocument(noteId, document) {
    const update = Y.encodeStateAsUpdate(document);
    if (update.byteLength > MAX_DOCUMENT_BYTES) {
        throw new Error(`Collaboration document ${noteId} exceeds size limit.`);
    }
    const response = await fetch(`${FLASK_BASE_URL}/api/internal/notes/${encodeURIComponent(noteId)}/collaboration-document`, {
        method: 'PUT',
        headers: internalHeaders(),
        body: JSON.stringify({
            ydoc_base64: Buffer.from(update).toString('base64'),
            schema_version: 1,
        }),
    });
    return parseJsonResponse(response);
}

async function healthPayload() {
    const startedAt = globalThis.__nestNotesCollaborationStartedAt || new Date().toISOString();
    globalThis.__nestNotesCollaborationStartedAt = startedAt;
    try {
        const response = await fetch(`${FLASK_BASE_URL}/api/internal/notes/collaboration-health`, {
            headers: INTERNAL_SECRET ? { 'X-Nest-Collaboration-Secret': INTERNAL_SECRET } : {},
        });
        return {
            ok: response.ok,
            status: response.ok ? 'ready' : 'degraded',
            flask_status: response.status,
            started_at: startedAt,
        };
    } catch (error) {
        return {
            ok: false,
            status: 'degraded',
            flask_error: error.message,
            started_at: startedAt,
        };
    }
}

const server = Server.configure({
    name: 'nest-notes-collaboration',
    address: HOST,
    port: PORT,
    debounce: 750,
    maxDebounce: 3000,
    timeout: 30000,
    quiet: process.env.NOTES_COLLABORATION_QUIET === '1',
    unloadImmediately: false,

    async onRequest({ request, response }) {
        if (request.url === '/health' || request.url === '/healthz') {
            const payload = await healthPayload();
            response.writeHead(payload.ok ? 200 : 503, { 'Content-Type': 'application/json' });
            response.end(JSON.stringify(payload));
            throw null;
        }
    },

    async onAuthenticate({ token, documentName, requestHeaders, connection }) {
        if (!validateOrigin(requestHeaders)) {
            throw new Error('Origin is not allowed.');
        }
        const noteId = normalizeNoteId(documentName);
        if (!noteId) throw new Error('Missing note ID.');
        const verification = await verifyTicket(token, noteId);
        connection.readOnly = !verification.can_write;
        return {
            noteId,
            userId: verification.user_id,
            role: verification.role,
            public: verification.public,
            anonymous: verification.anonymous,
            awarenessAllowed: verification.awareness_allowed,
            user: verification.user,
            accessRevision: verification.access_revision,
        };
    },

    async onLoadDocument({ documentName }) {
        const noteId = normalizeNoteId(documentName);
        const update = await fetchStoredDocument(noteId);
        const document = new Y.Doc();
        document.getMap('nest:meta').set('schemaVersion', 1);
        if (update) {
            Y.applyUpdate(document, update);
        }
        return document;
    },

    async beforeHandleMessage({ connection, update }) {
        if (update?.byteLength > MAX_UPDATE_BYTES) {
            throw new Error('Collaboration update is too large.');
        }
    },

    async onAwarenessUpdate({ context, added, updated }) {
        if (!context?.awarenessAllowed && (added.length || updated.length)) {
            throw new Error('Awareness is disabled for this collaboration connection.');
        }
    },

    async onStoreDocument({ documentName, document }) {
        const noteId = normalizeNoteId(documentName);
        await storeDocument(noteId, document);
    },
});

server.listen();

process.on('SIGTERM', async () => {
    await server.destroy();
    process.exit(0);
});

process.on('SIGINT', async () => {
    await server.destroy();
    process.exit(0);
});
