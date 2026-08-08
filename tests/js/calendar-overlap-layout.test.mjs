import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
globalThis.window = { APStudyUIPrimitives: { escapeHtml: String } };
globalThis.document = { documentElement: {} };
const utilsSource = await readFile(path.join(root, "static/js/calendar/utils.js"), "utf8");
await import(`data:text/javascript;base64,${Buffer.from(utilsSource).toString("base64")}#calendar-overlap-layout`);
const { layoutTimedEvents } = window.APStudyCalendarUtils;

function at(hour, minute = 0) {
    return new Date(2026, 6, 20, hour, minute);
}

function event(id, startHour, startMinute, endHour, endMinute) {
    return { id, startDate: at(startHour, startMinute), endDate: at(endHour, endMinute) };
}

function layout(...events) {
    return Object.fromEntries(layoutTimedEvents(events).map((item) => [item.id, item]));
}

test("isolated events before and after an overlap group regain full width", () => {
    const result = layout(
        event("before", 8, 0, 9, 0),
        event("overlap-a", 10, 0, 11, 0),
        event("overlap-b", 10, 30, 11, 30),
        event("after", 12, 0, 13, 0),
    );
    assert.equal(result.before.layoutLaneCount, 1);
    assert.equal(result.after.layoutLaneCount, 1);
    assert.equal(result["overlap-a"].layoutLaneCount, 2);
    assert.equal(result["overlap-b"].layoutLaneCount, 2);
});

test("adjacent events do not share lanes", () => {
    const result = layout(event("first", 9, 0, 10, 0), event("second", 10, 0, 11, 0));
    assert.equal(result.first.layoutLaneCount, 1);
    assert.equal(result.second.layoutLaneCount, 1);
    assert.equal(result.first.layoutLane, 0);
    assert.equal(result.second.layoutLane, 0);
});

test("nested events reuse a lane when their inner intervals are adjacent", () => {
    const result = layout(
        event("outer", 9, 0, 12, 0),
        event("inner-a", 9, 30, 10, 0),
        event("inner-b", 10, 0, 10, 30),
    );
    assert.equal(result.outer.layoutLane, 0);
    assert.equal(result["inner-a"].layoutLane, 1);
    assert.equal(result["inner-b"].layoutLane, 1);
    assert.deepEqual(new Set(Object.values(result).map((item) => item.layoutLaneCount)), new Set([2]));
});

test("chained overlaps form one connected deterministic two-lane group", () => {
    const result = layout(
        event("a", 9, 0, 10, 0),
        event("b", 9, 30, 10, 30),
        event("c", 10, 0, 11, 0),
    );
    assert.deepEqual([result.a.layoutLane, result.b.layoutLane, result.c.layoutLane], [0, 1, 0]);
    assert.deepEqual(Object.values(result).map((item) => item.layoutLaneCount), [2, 2, 2]);
});

test("simultaneous events receive stable distinct lanes", () => {
    const result = layout(
        event("long", 9, 0, 11, 0),
        event("medium", 9, 0, 10, 30),
        event("short", 9, 0, 10, 0),
    );
    assert.deepEqual([result.long.layoutLane, result.medium.layoutLane, result.short.layoutLane], [0, 1, 2]);
    assert.deepEqual(Object.values(result).map((item) => item.layoutLaneCount), [3, 3, 3]);
});
