import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const read = (relativePath) => readFile(new URL(`../../${relativePath}`, import.meta.url), "utf8");

test("onboarding keeps executable behavior out of the server-rendered template", async () => {
  const [template, entry] = await Promise.all([
    read("templates/onboarding.html"),
    read("static/js/onboarding/index.js"),
  ]);
  assert.match(template, /<script type="module" src="\{\{ url_for\('static', filename='js\/onboarding\/index\.js'\) \}\}"><\/script>/);
  assert.match(template, /<script type="application\/json" id="onboarding-data">/);
  assert.doesNotMatch(template, /function initializeOnboarding|addEventListener\(/);
  assert.match(entry, /import \{ createThemeSelector \} from '\.\/theme-selector\.js'/);
});

test("chat runtime delegates cache and presentation responsibilities", async () => {
  const [runtime, cache, presentation] = await Promise.all([
    read("static/js/chat/runtime.js"),
    read("static/js/chat/cache.js"),
    read("static/js/chat/presentation.js"),
  ]);
  assert.match(runtime, /from "\.\/cache\.js"/);
  assert.match(runtime, /from "\.\/presentation\.js"/);
  assert.doesNotMatch(runtime, /function openChatCacheDb|function groupMessages|function escapeHtml/);
  assert.match(cache, /export function createPersistentChatCache/);
  assert.match(presentation, /import \{ escapeHtml \} from "\.\.\/core\/ui-primitives-module\.js"/);
  assert.match(presentation, /export \{ escapeHtml \}/);
  assert.match(presentation, /export function groupMessages/);
});

test("shared and feature stylesheets load their extracted responsibilities in cascade order", async () => {
  const [sharedAssets, globalCss, overlays, notesTemplate, notesCss, notesEditorCss, analyticsTemplate, adminCss, analyticsCss, responsiveCss] = await Promise.all([
    read("templates/_shared_runtime_assets.html"),
    read("static/css/global.css"),
    read("static/css/core/feedback-overlays.css"),
    read("templates/notes_editor.html"),
    read("static/css/notes.css"),
    read("static/css/notes/editor.css"),
    read("templates/admin_analytics.html"),
    read("static/css/admin.css"),
    read("static/css/admin/analytics.css"),
    read("static/css/admin/responsive.css"),
  ]);
  assert.match(sharedAssets, /css\/core\/feedback-overlays\.css/);
  assert.doesNotMatch(globalCss, /\.apstudy-toast-host\s*\{/);
  assert.match(overlays, /\.apstudy-toast-host\s*\{/);

  assert.ok(notesTemplate.indexOf("css/notes.css") < notesTemplate.indexOf("css/notes/editor.css"));
  assert.doesNotMatch(notesCss, /body\.notes-editor-body\s*\{/);
  assert.match(notesEditorCss, /body\.notes-editor-body\s*\{/);

  const adminIndex = analyticsTemplate.indexOf("css/admin.css");
  const analyticsIndex = analyticsTemplate.indexOf("css/admin/analytics.css");
  const responsiveIndex = analyticsTemplate.indexOf("css/admin/responsive.css");
  assert.ok(adminIndex < analyticsIndex && analyticsIndex < responsiveIndex);
  assert.doesNotMatch(adminCss, /\.admin-analytics-shell\s*\{/);
  assert.match(analyticsCss, /\.admin-analytics-shell\s*\{/);
  assert.match(responsiveCss, /@media \(max-width: 720px\)/);
});
