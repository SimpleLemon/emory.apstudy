import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');

function source(relativePath) {
    return fs.readFileSync(path.join(repoRoot, relativePath), 'utf8');
}

test('entity deletion flows stage a real shared undo window', () => {
    for (const relativePath of [
        'static/js/tasks/task.js',
        'static/js/courses/index.js',
        'static/js/notes/list.js',
        'static/js/files/index.js',
        'static/js/calendar/events/context-menu.js',
        'static/js/chat/messages-dom.js',
        'static/js/core/notification-tray.js',
        'static/js/settings/account.js',
        'static/js/settings/index.js',
        'static/js/settings/notifications.js',
        'static/js/onboarding/index.js',
        'static/js/focus/index.js',
    ]) {
        assert.match(source(relativePath), /APStudyUndo(?:\?\.|\.)(?:stage)/, `${relativePath} does not stage undo`);
    }
});

test('draft and attachment removals expose undo without losing the captured item', () => {
    for (const relativePath of [
        'static/js/chat/attachments.js',
        'static/js/chat/media-picker.js',
        'static/js/files/workflows.js',
        'static/js/notes/editor/image-runtime.js',
        'static/js/notes/sharing.js',
        'static/js/calendar/integrations/courses.js',
        'static/js/dashboard/daily-quote.js',
    ]) {
        const content = source(relativePath);
        assert.match(content, /APStudyUndo\?\.stage|APStudyUndo\.stage/, `${relativePath} has no undo toast`);
        assert.match(content, /restore:/, `${relativePath} does not restore the removed item`);
    }
});

test('admin delete forms are intercepted by the shared undo flow', () => {
    const template = source('templates/admin_detail.html');
    const runtime = source('static/js/admin-delete-undo.js');
    assert.match(template, /data-undoable-delete="User account"/);
    assert.match(template, /data-undoable-delete="Folder"/);
    assert.match(template, /data-undoable-delete="File"/);
    assert.match(template, /js\/admin-delete-undo\.js/);
    assert.match(runtime, /APStudyUndo\.stage/);
    assert.match(runtime, /body: new FormData\(form\)/);
});
