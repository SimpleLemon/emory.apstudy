import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");

function ensureDateHelpers() {
    const pad2 = (value) => String(value).padStart(2, "0");
    if (!globalThis.window) {
        globalThis.window = {};
    }
    globalThis.window.APStudyDate = {
        toLocalInputValue(date) {
            if (!(date instanceof Date) || Number.isNaN(date.getTime())) return "";
            return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())}T${pad2(date.getHours())}:${pad2(date.getMinutes())}`;
        },
        isoToLocalInput(value) {
            if (!value) return "";
            const date = new Date(value);
            return Number.isNaN(date.getTime()) ? "" : this.toLocalInputValue(date);
        },
        localInputToIso(value) {
            if (!value) return null;
            const date = new Date(value);
            return Number.isNaN(date.getTime()) ? null : date.toISOString();
        },
    };
}

function moduleUrl(source) {
    return `data:text/javascript;charset=utf-8,${encodeURIComponent(source)}`;
}

async function importTaskData() {
    ensureDateHelpers();
    const utilsSource = await readFile(path.join(repoRoot, "static/js/tasks/task-utils.js"), "utf8");
    const dataSource = await readFile(path.join(repoRoot, "static/js/tasks/task-data.js"), "utf8");
    return import(moduleUrl(dataSource.replace('from "./task-utils.js"', `from "${moduleUrl(utilsSource)}"`)));
}

const data = await importTaskData();

test("builds task API payloads with normalized deadline and recurrence fields", () => {
    const payload = data.buildTaskDraftPayload("list-1", {
        title: "Read",
        priority: "",
        deadline_at: "2026-05-20T14:30",
        recurrence: { every: 1, unit: "week" },
    }, "America/New_York");

    assert.equal(payload.list_id, "list-1");
    assert.equal(payload.priority, "none");
    assert.equal(payload.deadline_time, "14:30");
    assert.equal(payload.reminder_minutes, 10);
    assert.equal(payload.timezone, "America/New_York");
    assert.deepEqual(payload.recurrence, { every: 1, unit: "week" });
    assert.match(payload.deadline_at, /^2026-05-20T/);
});

test("task payloads preserve priority and distinguish date-only from timed deadlines", () => {
    const dateOnly = data.buildTaskDraftPayload("list-1", {
        title: "Submit outline",
        priority: "high",
        deadline_at: "2026-05-20",
        deadline_time: null,
        reminder_minutes: -540,
    }, "America/Chicago");
    assert.equal(dateOnly.priority, "high");
    assert.equal(dateOnly.deadline_time, null);
    assert.equal(dateOnly.reminder_minutes, -540);
    assert.match(dateOnly.deadline_at, /^2026-05-20T/);

    const timed = data.buildTaskDraftPayload("list-1", {
        title: "Join review",
        priority: "urgent",
        deadline_at: "2026-05-20T14:30",
        deadline_time: "14:30",
        reminder_minutes: 10,
    }, "America/Chicago");
    assert.equal(timed.priority, "urgent");
    assert.equal(timed.deadline_time, "14:30");
    assert.equal(timed.reminder_minutes, 10);
});

test("computes optimistic completed state for one-off and recurring tasks", () => {
    const now = new Date("2026-05-20T12:00:00Z");
    assert.deepEqual(data.buildCompletedTaskOptimistic({ id: "one-off", completed_occurrences: [] }, true, now), {
        occurrenceKey: "single",
        task: {
            id: "one-off",
            completed: true,
            completed_at: "2026-05-20T12:00:00.000Z",
            completed_occurrences: [],
            priority: "none",
            starred: false,
            reminder_minutes: -1,
        },
    });

    const recurring = data.buildCompletedTaskOptimistic({
        id: "repeat",
        recurrence: { every: 1, unit: "day" },
        next_occurrence_key: "2026-05-20",
        completed_occurrences: [],
    }, true, now);
    assert.equal(recurring.occurrenceKey, "2026-05-20");
    assert.deepEqual(recurring.task.completed_occurrences, [{
        occurrence_key: "2026-05-20",
        completed_at: "2026-05-20T12:00:00.000Z",
    }]);
});

test("creates list and task ordering updates without mutating input arrays", () => {
    const lists = [{ id: "a", name: "A", order: 1000 }, { id: "b", name: "B", order: 2000 }];
    const listUpdates = data.buildListOrderUpdates(["b", "a"]);
    assert.deepEqual(listUpdates, [{ id: "b", order: 1000 }, { id: "a", order: 2000 }]);
    assert.deepEqual(data.applyListOrderUpdates(lists, listUpdates).map((item) => `${item.id}:${item.order}`), ["b:1000", "a:2000"]);
    assert.deepEqual(lists.map((item) => `${item.id}:${item.order}`), ["a:1000", "b:2000"]);

    const tasks = [{ id: "one", list_id: "old", order: 1000 }, { id: "two", list_id: "old", order: 2000 }];
    const taskUpdates = [{ id: "two", list_id: "new", order: 1000 }];
    assert.deepEqual(data.applyTaskOrderUpdates(tasks, taskUpdates).map((item) => `${item.id}:${item.list_id}:${item.order}`), [
        "one:old:1000",
        "two:new:1000",
    ]);
});

test("normalizes task board payloads returned by the API layer", () => {
    const board = data.normalizeTaskBoard({
        lists: [{ id: "b", name: "B", order: 2 }, { id: "a", name: "A", order: 1, hidden: true }],
        tasks: [{ id: "task", title: "Write", starred: 1 }],
        preferences: { task_sound_enabled: false },
    });

    assert.deepEqual(board.lists.map((item) => item.id), ["a", "b"]);
    assert.equal(board.lists[0].hidden, true);
    assert.equal(board.tasks[0].priority, "none");
    assert.equal(board.tasks[0].starred, true);
    assert.equal(board.preferences.task_sound_enabled, false);
});
