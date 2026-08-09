import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

process.env.TZ = "America/New_York";
const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
globalThis.window = { APStudyUIPrimitives: { escapeHtml: String } };
const source = await readFile(path.join(repoRoot, "static/js/calendar/utils.js"), "utf8");
await import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);

const {
    calendarDayDifference,
    getUrgencyLabel,
    getUrgencyLabelAllDay,
} = window.APStudyCalendarUtils;

test("timed events use local calendar days instead of elapsed hours", () => {
    const now = new Date(2026, 6, 20, 15, 0);

    assert.equal(getUrgencyLabel(new Date(2026, 6, 20, 9, 0), now), "Today");
    assert.equal(getUrgencyLabel(new Date(2026, 6, 20, 23, 30), now), "Today");
    assert.equal(getUrgencyLabel(new Date(2026, 6, 21, 0, 15), new Date(2026, 6, 20, 23, 30)), "Tomorrow");
    assert.equal(getUrgencyLabel(new Date(2026, 6, 22, 9, 0), now), "In 2 days");
    assert.equal(getUrgencyLabel(new Date(2026, 6, 19, 23, 59), now), "Past");
});

test("a timed event spanning into today is Today while an event ending before today is Past", () => {
    const now = new Date(2026, 6, 20, 12, 0);

    assert.equal(
        getUrgencyLabel(new Date(2026, 6, 19, 22, 0), now, new Date(2026, 6, 20, 13, 0)),
        "Today",
    );
    assert.equal(
        getUrgencyLabel(new Date(2026, 6, 18, 22, 0), now, new Date(2026, 6, 19, 23, 0)),
        "Past",
    );
});

test("spring-forward and fall-back transitions do not change Tomorrow labels", () => {
    const beforeSpringForward = new Date(2026, 2, 7, 23, 30);
    const springTomorrow = new Date(2026, 2, 8, 23, 0);
    assert.ok(springTomorrow - beforeSpringForward < 24 * 60 * 60 * 1000);
    assert.equal(calendarDayDifference(springTomorrow, beforeSpringForward), 1);
    assert.equal(getUrgencyLabel(springTomorrow, beforeSpringForward), "Tomorrow");

    const beforeFallBack = new Date(2026, 9, 31, 23, 30);
    const fallTomorrow = new Date(2026, 10, 1, 23, 0);
    assert.ok(fallTomorrow - beforeFallBack > 24 * 60 * 60 * 1000);
    assert.equal(calendarDayDifference(fallTomorrow, beforeFallBack), 1);
    assert.equal(getUrgencyLabel(fallTomorrow, beforeFallBack), "Tomorrow");
});

test("all-day events honor exclusive end dates and local-day boundaries", () => {
    const now = new Date(2026, 6, 20, 16, 0);

    assert.equal(getUrgencyLabelAllDay({
        startDate: new Date(2026, 6, 19),
        endDate: new Date(2026, 6, 21),
    }, now), "Today");
    assert.equal(getUrgencyLabelAllDay({
        startDate: new Date(2026, 6, 21),
        endDate: new Date(2026, 6, 22),
    }, now), "Tomorrow");
    assert.equal(getUrgencyLabelAllDay({
        startDate: new Date(2026, 6, 19),
        endDate: new Date(2026, 6, 20),
    }, now), "Past");
    assert.equal(getUrgencyLabelAllDay({
        startDate: new Date(2026, 6, 20),
        endDate: null,
    }, now), "Today");
});

test("timed and all-day events on the same future date receive compatible labels", () => {
    const now = new Date(2026, 6, 20, 23, 45);
    const tomorrow = new Date(2026, 6, 21);

    assert.equal(getUrgencyLabel(new Date(2026, 6, 21, 0, 5), now), "Tomorrow");
    assert.equal(getUrgencyLabelAllDay({
        startDate: tomorrow,
        endDate: new Date(2026, 6, 22),
    }, now), "Tomorrow");
});
