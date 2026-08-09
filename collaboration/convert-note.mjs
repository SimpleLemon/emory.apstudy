import { JSDOM } from 'jsdom';
import { readFile, unlink, writeFile } from 'node:fs/promises';
import * as Y from 'yjs';
import { prosemirrorJSONToYDoc, yDocToProsemirrorJSON } from 'y-prosemirror';

const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'https://nest.apstudy.org/' });
for (const key of [
    'window', 'document', 'navigator', 'Node', 'HTMLElement', 'Element', 'DocumentFragment',
    'MutationObserver', 'DOMParser', 'File', 'Blob', 'Event', 'KeyboardEvent', 'getComputedStyle',
]) {
    if (dom.window[key] !== undefined) {
        Object.defineProperty(globalThis, key, { value: dom.window[key], configurable: true, writable: true });
    }
}
globalThis.requestAnimationFrame ||= (callback) => setTimeout(callback, 0);
globalThis.cancelAnimationFrame ||= clearTimeout;
globalThis.IntersectionObserver ||= class {
    observe() {}
    disconnect() {}
};

const toolbarSource = await readFile(new URL('../static/js/notes/toolbar.js', import.meta.url), 'utf8');
const toolbarModuleUrl = new URL(`./.notes-schema-runtime-${process.pid}.mjs`, import.meta.url);
await writeFile(toolbarModuleUrl, toolbarSource, 'utf8');
let BlockNoteEditor;
let notesEditorSchema;
try {
    [{ BlockNoteEditor }, { notesEditorSchema }] = await Promise.all([
        import('@blocknote/core'),
        import(toolbarModuleUrl.href),
    ]);
} finally {
    await unlink(toolbarModuleUrl).catch(() => {});
}

let raw = '';
for await (const chunk of process.stdin) raw += chunk;
const payload = JSON.parse(raw || '{}');
if (!Array.isArray(payload.blocks)) throw new Error('blocks must be an array');

const editor = BlockNoteEditor.create({ schema: notesEditorSchema, initialContent: payload.blocks });
const prosemirrorJson = editor._tiptapEditor.state.doc.toJSON();
const ydoc = prosemirrorJSONToYDoc(editor._tiptapEditor.schema, prosemirrorJson, 'document-store');
ydoc.getText('title').insert(0, String(payload.title || ''));
const settings = ydoc.getMap('note-settings');
Object.entries(payload.page_setup || {}).forEach(([key, value]) => settings.set(key, value));
ydoc.getMap('nest:meta').set('schemaVersion', 1);

const roundTrip = yDocToProsemirrorJSON(ydoc, 'document-store');
const roundTripNode = editor._tiptapEditor.schema.nodeFromJSON(roundTrip);
if (!editor._tiptapEditor.state.doc.eq(roundTripNode)) {
    throw new Error('BlockNote JSON did not survive the Yjs round trip');
}

process.stdout.write(JSON.stringify({
    ydoc_base64: Buffer.from(Y.encodeStateAsUpdate(ydoc)).toString('base64'),
    state_vector_base64: Buffer.from(Y.encodeStateVector(ydoc)).toString('base64'),
    block_count: payload.blocks.length,
    schema_version: 1,
}));

editor._tiptapEditor.destroy();
ydoc.destroy();
dom.window.close();
