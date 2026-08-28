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
const courses = await loadEchoModule("static/js/derek/echo-courses.js");

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

const echoWindow = {
  start: new Date(2026, 7, 24, 0, 0, 0, 0),
  end: new Date(2026, 7, 30, 23, 59, 59, 999),
};

function savedCourse(overrides = {}) {
  return {
    id: "course-row-1",
    section_id: "Fall_2026|ISOM|351|12345|1",
    course_code: "ISOM 351",
    course_title: "Operating Systems",
    section_number: "1",
    instructor: "",
    location: "Goizueta 300",
    color_key: "course-color-03",
    date_range: { start: "2026-08-25", end: "2026-08-26" },
    meetings: [
      { day: "Tue", start: "0930", end: "1045" },
      { day: "Wed", start: "1430", end: "1600" },
    ],
    ...overrides,
  };
}

test("atlas time tokens parse padded, short, and invalid values", () => {
  assert.deepEqual(courses.parseAtlasTimeToken("0930"), { hour: 9, minute: 30 });
  assert.deepEqual(courses.parseAtlasTimeToken("930"), { hour: 9, minute: 30 });
  assert.deepEqual(courses.parseAtlasTimeToken("1435"), { hour: 14, minute: 35 });
  assert.equal(courses.parseAtlasTimeToken("2430"), null);
  assert.equal(courses.parseAtlasTimeToken("TBA"), null);
  assert.equal(courses.parseAtlasTimeToken(""), null);
});

test("simulated course events land on meeting days inside the echo window", () => {
  const events = courses.buildSimulatedCourseEvents([savedCourse()], echoWindow);
  assert.deepEqual(events.map((event) => event.start), [
    "2026-08-25T09:30:00",
    "2026-08-26T14:30:00",
  ]);
  assert.equal(events[0].end, "2026-08-25T10:45:00");
  assert.equal(events[0].uid, "Fall_2026|ISOM|351|12345|1|2026-08-25|0930|1045");
  assert.equal(events[0].title, "ISOM 351");
  assert.equal(events[0].description, "Operating Systems | Sec 1 | TBA");
  assert.equal(events[0].calendar_id, "simulated_courses");
  assert.equal(events[0].course, "Operating Systems");
  assert.equal(events[0].location, "Goizueta 300");
  assert.equal(events[0].color, "#059669");
  assert.equal(events[0].is_all_day, false);
  assert.equal(events[0].source, "simulated");
});

test("simulated course events clamp to the section date range and window", () => {
  const longRange = savedCourse({
    section_id: "Fall_2026|MATH|211|54321|2",
    course_code: "MATH 211",
    color_key: "unknown-key",
    date_range: { start: "2026-01-01", end: "2026-12-31" },
    meetings: [{ day: "Fri", start: "1000", end: "1100" }],
  });
  const events = courses.buildSimulatedCourseEvents([longRange], echoWindow);
  assert.deepEqual(events.map((event) => event.start), ["2026-08-28T10:00:00"]);
  assert.equal(events[0].color, "#2563eb");
});

test("saved courses without usable sections or meetings are skipped", () => {
  const events = courses.buildSimulatedCourseEvents([
    savedCourse({ section_id: "", id: "" }),
    savedCourse({ meetings: [] }),
    savedCourse({ date_range: null }),
    savedCourse({ date_range: { start: "2026-08-26", end: "2026-08-25" } }),
    savedCourse({ meetings: [{ day: "Weekend", start: "1000", end: "1100" }] }),
    savedCourse({ meetings: [{ day: "Tue", start: "1045", end: "0930" }] }),
    null,
  ], echoWindow);
  assert.deepEqual(events, []);
});

test("simulated payload normalizes into echo events labeled as Simulated Courses", () => {
  const payload = courses.simulatedCoursesPayload([savedCourse()], echoWindow);
  assert.deepEqual(payload.calendar_sources, [{
    id: "simulated_courses",
    display_name: "Simulated Courses",
  }]);
  const [event] = calendar.normalizeEvents(payload);
  assert.equal(event.calendarLabel, "Simulated Courses");
  assert.equal(event.eventKey, "Fall_2026|ISOM|351|12345|1|2026-08-25|0930|1045");
  assert.equal(event.startDate.getFullYear(), 2026);
  assert.equal(event.startDate.getMonth(), 7);
  assert.equal(event.startDate.getDate(), 25);
  assert.equal(event.startDate.getHours(), 9);
  assert.equal(event.isAllDay, false);
});

test("simulated course fetch degrades gracefully on failures", async () => {
  const okCourses = [{ id: "course-row-1" }];
  const okResponse = { ok: true, json: async () => ({ courses: okCourses }) };
  assert.deepEqual(await courses.fetchSimulatedCourses(async () => okResponse), okCourses);

  const forbidden = { ok: false, status: 403, json: async () => ({}) };
  assert.deepEqual(await courses.fetchSimulatedCourses(async () => forbidden), []);

  const malformed = { ok: true, json: async () => { throw new Error("bad json"); } };
  assert.deepEqual(await courses.fetchSimulatedCourses(async () => malformed), []);
});
