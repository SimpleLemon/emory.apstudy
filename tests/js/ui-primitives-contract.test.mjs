import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const primitives = fs.readFileSync(path.join(repoRoot, 'static/js/core/ui-primitives.js'), 'utf8');
const primitivesModule = fs.readFileSync(path.join(repoRoot, 'static/js/core/ui-primitives-module.js'), 'utf8');
const feedbackOverlays = fs.readFileSync(path.join(repoRoot, 'static/css/core/feedback-overlays.css'), 'utf8');

test('shared UI primitive module has substantive owned APIs', () => {
    for (const api of ['APStudyFormField', 'APStudyLoader', 'APStudySkeleton', 'APStudyToast', 'APStudyUndo', 'APStudyConfirm']) {
        assert.match(primitives, new RegExp(`window\\.${api}`));
    }
    assert.match(primitives, /window\.APStudyUIPrimitives = Object\.freeze/);
    assert.match(primitives, /div\.textContent = value == null \? '' : String\(value\)/);
    assert.match(primitives, /\.replace\(\/"\/g, '&quot;'\)/);
    assert.match(primitives, /\.replace\(\/'\/g, '&#39;'\)/);
    assert.match(primitives, /Object\.freeze\(\{\s*escapeHtml,/);
    assert.match(primitivesModule, /import '\.\/ui-primitives\.js'/);
    assert.match(primitivesModule, /export const \{ escapeHtml \} = window\.APStudyUIPrimitives/);
    assert.ok(primitives.length > 8_000, 'ui-primitives.js must not become an empty compatibility shim');
    const globalSource = fs.readFileSync(path.join(repoRoot, 'static/js/core/global.js'), 'utf8');
    assert.doesNotMatch(globalSource, /window\.APStudy(?:FormField|Loader|Skeleton|Toast|Confirm)\s*=/);
});

test('toast primitive normalizes content, timing, and accessibility states', () => {
    assert.match(primitives, /if \(!primaryText\) return null/);
    assert.match(primitives, /toast\.setAttribute\('aria-atomic', 'true'\)/);
    assert.match(primitives, /toast\.append\(createToastIcon\(type\), copy, close\)/);
    assert.match(primitives, /if \(hasAction\) return 10_000/);
    assert.match(primitives, /return 7_000/);
    assert.match(primitives, /return 4_000/);
    assert.match(primitives, /remaining = Math\.max\(0, remaining - \(performance\.now\(\) - startedAt\)\)/);
    assert.doesNotMatch(primitives, /host\.setAttribute\('aria-live'/);
});

test('undo primitive delays commits, restores failures, and flushes on navigation', () => {
    assert.match(primitives, /function stageUndoableAction\(options = \{\}\)/);
    assert.match(primitives, /actionLabel \|\| 'Undo'/);
    assert.match(primitives, /if \(reason !== 'action'\) void commit\(reason\)/);
    assert.match(primitives, /await restore\('commit-error'\)/);
    assert.match(primitives, /window\.addEventListener\('pagehide'/);
    assert.match(primitives, /pendingUndoOperations/);
});

test('toast presentation follows the shared spacing, target-size, and viewport contracts', () => {
    assert.match(feedbackOverlays, /grid-template-columns:\s*32px minmax\(0, 1fr\) 32px/);
    assert.match(feedbackOverlays, /\.apstudy-toast\s*\{[^}]*flex:\s*0 0 auto/s);
    assert.match(feedbackOverlays, /\.apstudy-toast\.is-compact\s*\{[^}]*align-items:\s*center/s);
    assert.match(feedbackOverlays, /\.apstudy-toast__action\s*\{[^}]*min-height:\s*44px/s);
    assert.match(feedbackOverlays, /\.apstudy-toast__close::before\s*\{[^}]*inset:\s*-6px/s);
    assert.match(feedbackOverlays, /max-height:\s*calc\(var\(--app-viewport-height/);
    assert.match(feedbackOverlays, /env\(safe-area-inset-right/);
    assert.match(feedbackOverlays, /@media \(prefers-reduced-motion: reduce\)[\s\S]*\.apstudy-toast__progress\.is-running\s*\{\s*display:\s*none/s);
});

test('every template using global.js receives primitives first through the shared asset partial', () => {
    const diagnostics = fs.readFileSync(path.join(repoRoot, 'templates/_diagnostics_assets.html'), 'utf8');
    const runtime = fs.readFileSync(path.join(repoRoot, 'templates/_shared_runtime_assets.html'), 'utf8');
    assert.match(diagnostics, /include "_shared_runtime_assets\.html"/);
    assert.match(runtime, /js\/core\/ui-primitives\.js/);

    const templates = fs.readdirSync(path.join(repoRoot, 'templates'))
        .filter((filename) => filename.endsWith('.html'));
    for (const filename of templates) {
        const source = fs.readFileSync(path.join(repoRoot, 'templates', filename), 'utf8');
        if (!source.includes("js/core/global.js")) continue;
        assert.ok(source.includes('_diagnostics_assets.html'), `${filename} skips shared runtime assets`);
        assert.ok(source.indexOf('_diagnostics_assets.html') < source.indexOf('js/core/global.js'), `${filename} loads primitives after global.js`);
    }
});
