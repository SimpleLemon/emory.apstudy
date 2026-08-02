import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { readCssSource } from './helpers/css-source.mjs';

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const globalCss = await readCssSource(repoRoot, 'static/css/global.css');
const overlayCss = fs.readFileSync(path.join(repoRoot, 'static/css/core/feedback-overlays.css'), 'utf8');
const sharedCss = `${globalCss}\n${overlayCss}`;

test('shared z-index ladder is documented and strictly ordered', () => {
    const expected = {
        content: 10,
        sticky: 100,
        dropdown: 200,
        popover: 300,
        backdrop: 400,
        modal: 410,
        toast: 500,
        tooltip: 600,
        'critical-overlay': 700,
    };
    for (const [token, value] of Object.entries(expected)) {
        assert.match(globalCss, new RegExp(`--z-${token}:\\s*${value};`));
    }
    assert.match(globalCss, /viewport-level UI must use one of these semantic tokens/);
});

test('first-party sources contain no arbitrary four- or five-digit z-index literals', () => {
    const roots = ['static/css', 'static/js', 'templates'];
    const files = roots.flatMap((root) => fs.readdirSync(path.join(repoRoot, root), { recursive: true })
        .filter((name) => /\.(?:css|js|html)$/.test(name))
        .filter((name) => !name.includes('/dist/') && name !== 'tailwind.css')
        .map((name) => path.join(root, name)));
    for (const relativePath of files) {
        const source = fs.readFileSync(path.join(repoRoot, relativePath), 'utf8');
        assert.doesNotMatch(source, /z-index\s*:\s*[1-9]\d{3,}\b/, relativePath);
    }
});

test('shared overlay surfaces use semantic z-index tokens', () => {
    for (const selector of [
        '.calendar-context-menu',
        '.calendar-event-hover-card',
        '.calendar-info-modal',
        '.apstudy-toast-host',
        '[cmdk-overlay]',
        '[cmdk-dialog]',
    ]) {
        const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
        assert.match(sharedCss, new RegExp(`${escaped}[\\s\\S]{0,300}z-index:\\s*var\\(--z-`));
    }
});
