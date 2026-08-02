import assert from 'node:assert/strict';
import { readFile, stat } from 'node:fs/promises';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const manifestPath = path.join(repoRoot, 'static/css/global.css');
const importedFiles = [
    './global-foundation.css',
    './calendar-overlays.css',
    './command-palette.css',
    './responsive-overrides.css',
    './tier-badges.css',
];

test('global.css is an import-only manifest with the required split order', async () => {
    const manifest = await readFile(manifestPath, 'utf8');
    const expectedManifest = `${importedFiles.map((file) => `@import url("${file}");`).join('\n')}\n`;

    assert.equal(manifest, expectedManifest, 'global.css must contain exactly the five imports in order');
    assert.doesNotMatch(manifest, /[{}]/, 'global.css must not contain ordinary CSS rule blocks');

    for (const file of importedFiles) {
        assert.ok(file.startsWith('./'), `${file} must be a relative import`);
        const importedPath = path.resolve(path.dirname(manifestPath), file);
        const importedStats = await stat(importedPath);
        assert.ok(importedStats.isFile(), `${file} must resolve to a file`);
        assert.ok((await readFile(importedPath, 'utf8')).trim().length > 0, `${file} must not be empty`);
    }
});
