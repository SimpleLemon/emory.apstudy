import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { readCssSource } from "./helpers/css-source.mjs";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const primitiveSource = await readFile(path.join(repoRoot, "static/js/core/ui-primitives.js"), "utf8");
const globalStyles = await readCssSource(repoRoot, "static/css/global.css");

test("ui-primitives.js exposes APStudyFormField helpers", () => {
    assert.match(primitiveSource, /window\.APStudyFormField\s*=/);
    assert.match(primitiveSource, /\bmarkInvalid\b/);
    assert.match(primitiveSource, /\bclearInvalid\b/);
    assert.match(primitiveSource, /\bclearAll\b/);
    assert.match(primitiveSource, /\bbindAutoClear\b/);
    assert.match(primitiveSource, /setAttribute\('aria-invalid', 'true'\)/);
    assert.match(primitiveSource, /removeAttribute\('aria-invalid'\)/);
});

test("global.css styles invalid inputs and groups with theme error token", () => {
    assert.match(globalStyles, /\[aria-invalid="true"\]/);
    assert.match(globalStyles, /border-color:\s*var\(--color-error/);
    assert.match(globalStyles, /\[aria-invalid="true"\]:is\(:focus, :focus-visible\)/);
    assert.match(globalStyles, /\[id\$="-group"\]/);
});

test("global.css defines shared ui-search-field component classes", () => {
    assert.match(globalStyles, /\.ui-search-field\b/);
    assert.match(globalStyles, /\.ui-search-field__input\b/);
    assert.match(globalStyles, /\.ui-search-field--admin\b/);
    assert.match(globalStyles, /\.ui-search-field--compact\b/);
    assert.match(globalStyles, /\.ui-search-field--onboarding\b/);
});
