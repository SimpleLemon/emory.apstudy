import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const dataUrl = (source) => `data:text/javascript;base64,${Buffer.from(source).toString("base64")}`;
const escapeBridgeUrl = dataUrl(`export const escapeHtml = (value) => String(value ?? "")
  .replaceAll("&", "&amp;")
  .replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;")
  .replaceAll("'", "&#39;");`);
const utilsSource = await readFile(path.join(repoRoot, "static/js/derek/echo-utils.js"), "utf8");
const utilsUrl = dataUrl(utilsSource.replace("../core/ui-primitives-module.js", escapeBridgeUrl));

async function loadEchoModule(relativePath) {
  const source = await readFile(path.join(repoRoot, relativePath), "utf8");
  return import(dataUrl(source
    .replace("../core/ui-primitives-module.js", escapeBridgeUrl)
    .replaceAll('"./echo-utils.js"', `"${utilsUrl}"`)));
}

const utils = await loadEchoModule("static/js/derek/echo-utils.js");
const clock = await loadEchoModule("static/js/derek/echo-clock.js");
const calendar = await loadEchoModule("static/js/derek/echo-calendar.js");
const details = await loadEchoModule("static/js/derek/echo-event-details.js");

test("Echo clock preserves 12-hour leading-zero digits and quarter-hour keys", () => {
  const date = new Date(2026, 6, 25, 0, 9);
  assert.deepEqual(clock.formatEchoDigits(date), { h1: "1", h2: "2", m1: "0", m2: "9" });
  assert.equal(utils.getQuarterHourMinutes(new Date(2026, 6, 25, 9, 14)), 0);
  assert.equal(utils.getQuarterHourMinutes(new Date(2026, 6, 25, 9, 15)), 15);
  assert.match(utils.getQuarterHourKey(new Date(2026, 6, 25, 9, 17)), /-9-15$/);
});

test("calendar marker snaps to the current quarter-hour and can be centered", () => {
  const position = calendar.getNowMarkerPosition(new Date(2026, 6, 25, 9, 17));
  assert.equal(position.topPx, (9 * 60 + 15) / 60 * calendar.HOUR_HEIGHT_PX);
  assert.match(position.quarterHourKey, /-9-15$/);
});

test("upcoming labels include the local date for the next seven days", () => {
  const now = new Date(2026, 6, 25, 9, 17);
  assert.equal(calendar.formatUpcomingDayLabel(new Date(2026, 6, 25), now), "Today (7/25)");
  assert.equal(calendar.formatUpcomingDayLabel(new Date(2026, 6, 26), now), "Tomorrow (7/26)");
  assert.equal(calendar.formatUpcomingDayLabel(new Date(2026, 6, 28), now), "In 3 Days (7/28)");
});

test("event normalization adds stable keys and readable source labels", () => {
  const [event] = calendar.normalizeEvents({
    calendar_sources: [{ id: "personal", display_name: "Personal" }],
    events: [{
      event_ref: "user:event-1",
      title: "Office hours",
      start: "2026-07-25T09:00:00",
      end: "2026-07-25T10:00:00",
      calendar_id: "personal",
    }],
  });
  assert.equal(event.eventKey, "user:event-1");
  assert.equal(event.calendarLabel, "Personal");
  assert.equal(event.isAllDay, false);
});

test("event details render every present field and escape untrusted text", () => {
  const html = details.buildEventDetailsHtml({
    title: "Office hours",
    startDate: new Date(2026, 6, 25, 9),
    endDate: new Date(2026, 6, 25, 10),
    isAllDay: false,
    calendarLabel: "Personal",
    course: "ISOM 351",
    type: "Study session",
    description: "Bring <script>alert(1)</script>",
  });
  assert.match(html, /Office hours|When/);
  assert.match(html, /Personal/);
  assert.match(html, /ISOM 351/);
  assert.match(html, /Study session/);
  assert.match(html, /Bring &lt;script&gt;alert\(1\)&lt;\/script&gt;/);
  assert.doesNotMatch(html, /<script>alert/);
});
