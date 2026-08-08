import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import vm from 'node:vm';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const utilsSource = readFileSync(path.join(root, 'static/js/notes/list/utils.js'), 'utf8');

function loadUtils() {
    const context = {
        window: { APStudyUIPrimitives: { escapeHtml: String } },
        global: {},
        fetch: async () => ({ ok: true, headers: { get: () => 'application/json' }, json: async () => ({}) }),
    };
    context.global = context.window;
    vm.runInNewContext(utilsSource, context);
    return context.window.APStudyNotesListUtils;
}

test('notePreview prefers preview_text without parsing content', () => {
    const { notePreview } = loadUtils();
    const preview = notePreview({
        preview_text: 'From server',
        content: '[{"type":"paragraph","content":[{"text":"Ignored"}]}]',
    });
    assert.equal(preview, 'From server');
});

test('notePreview falls back to content parsing when preview_text missing', () => {
    const { notePreview } = loadUtils();
    const preview = notePreview({
        content: '[{"type":"paragraph","content":[{"text":"Legacy body"}]}]',
    });
    assert.equal(preview, 'Legacy body');
});
