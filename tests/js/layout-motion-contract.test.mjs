import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');

function read(relativePath) {
    return fs.readFileSync(path.join(repoRoot, relativePath), 'utf8');
}

test('first-party styles do not transition layout properties', () => {
    const files = fs.readdirSync(path.join(repoRoot, 'static/css'))
        .filter((filename) => filename.endsWith('.css') && filename !== 'tailwind.css');
    const forbidden = /transition(?:-property)?\s*:[^;}]*\b(?:width|height|min-height|max-height|margin(?:-\w+)?|grid-template-columns|left|right|top|bottom)\b/i;

    for (const filename of files) {
        const css = read(`static/css/${filename}`);
        assert.doesNotMatch(css, forbidden, `${filename} transitions a layout property`);
    }
});

test('shared shell transitions name compositor-safe or paint-only properties', () => {
    const layout = read('static/css/layout.css');
    assert.doesNotMatch(layout, /transition\s*:\s*all\b/i);
    assert.match(layout, /\.sidebar-container[\s\S]*transition:\s*transform 180ms ease/);
    assert.match(layout, /prefers-reduced-motion:\s*reduce[\s\S]*\.sidebar-container[\s\S]*transition:\s*none !important/);
});

test('progress indicators animate transforms rather than width', () => {
    const sources = [
        read('static/css/chat.css'),
        read('static/css/files.css'),
        read('static/css/settings.css'),
    ].join('\n');
    assert.doesNotMatch(sources, /transition\s*:[^;}]*\bwidth\b/i);
    assert.match(sources, /transform:\s*scaleX/);
    assert.match(read('static/js/files/workflows.js'), /style\.transform = `scaleX/);
    assert.match(read('static/js/settings/index.js'), /style\.transform = `scaleX/);
});
