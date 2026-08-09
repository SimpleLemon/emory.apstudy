import { JSDOM } from 'jsdom';
import { readFile, unlink, writeFile } from 'node:fs/promises';
import * as Y from 'yjs';
import { prosemirrorJSONToYDoc } from 'y-prosemirror';

const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'https://nest.apstudy.org/' });
for (const key of ['window', 'document', 'navigator', 'Node', 'HTMLElement', 'Element', 'DocumentFragment', 'MutationObserver', 'DOMParser', 'File', 'Blob', 'Event', 'KeyboardEvent', 'getComputedStyle']) {
    if (dom.window[key] !== undefined) Object.defineProperty(globalThis, key, { value: dom.window[key], configurable: true, writable: true });
}
globalThis.requestAnimationFrame ||= (callback) => setTimeout(callback, 0);
globalThis.cancelAnimationFrame ||= clearTimeout;
globalThis.IntersectionObserver ||= class { observe() {} disconnect() {} };

const toolbarSource = await readFile(new URL('../static/js/notes/toolbar.js', import.meta.url), 'utf8');
const runtimeSchemaUrl = new URL(`./.notes-schema-runtime-${process.pid}.mjs`, import.meta.url);
await writeFile(runtimeSchemaUrl, toolbarSource, 'utf8');
let BlockNoteEditor;
let notesEditorSchema;
try {
    [{ BlockNoteEditor }, { notesEditorSchema }] = await Promise.all([import('@blocknote/core'), import(runtimeSchemaUrl.href)]);
} finally {
    await unlink(runtimeSchemaUrl).catch(() => {});
}

let raw = '';
for await (const chunk of process.stdin) raw += chunk;
const payload = JSON.parse(raw || '{}');
const current = new Y.Doc();
Y.applyUpdate(current, Buffer.from(payload.ydoc_base64 || '', 'base64'));
const currentVector = Buffer.from(Y.encodeStateVector(current));
const expectedVector = Buffer.from(payload.base_state_vector || '', 'base64');
if (!expectedVector.length || !currentVector.equals(expectedVector)) throw new Error('suggestion_conflicted');

const operations = Array.isArray(payload.operations) ? payload.operations : [];
const operation = operations[operations.length - 1] || {};
let result = current;
let contentJson = null;
let title = current.getText('title').toString();
const pageSetup = Object.fromEntries(current.getMap('note-settings').entries());

if (payload.target_kind === 'body' && operation.type === 'replace_document') {
    const editor = BlockNoteEditor.create({ schema: notesEditorSchema, initialContent: [{ type: 'paragraph', content: '' }] });
    const node = editor._tiptapEditor.schema.nodeFromJSON(operation.after);
    result = prosemirrorJSONToYDoc(editor._tiptapEditor.schema, node.toJSON(), 'document-store');
    result.getText('title').insert(0, title);
    Object.entries(pageSetup).forEach(([key, value]) => result.getMap('note-settings').set(key, value));
    result.getMap('nest:meta').set('schemaVersion', 1);
    editor._tiptapEditor.commands.setContent(node.toJSON());
    contentJson = JSON.stringify(editor.document);
    editor._tiptapEditor.destroy();
} else if (payload.target_kind === 'title' && operation.type === 'replace_title') {
    const yTitle = current.getText('title');
    yTitle.delete(0, yTitle.length);
    title = String(operation.after || 'Untitled');
    yTitle.insert(0, title);
} else if (payload.target_kind === 'page_setup' && operation.type === 'patch_page_setup') {
    current.getMap('note-settings').set(String(operation.key), operation.after);
} else {
    throw new Error('unsupported_suggestion_operation');
}

process.stdout.write(JSON.stringify({
    ydoc_base64: Buffer.from(Y.encodeStateAsUpdate(result)).toString('base64'),
    title,
    content_json: contentJson,
    page_setup: Object.fromEntries(result.getMap('note-settings').entries()),
}));

if (result !== current) result.destroy();
current.destroy();
dom.window.close();
