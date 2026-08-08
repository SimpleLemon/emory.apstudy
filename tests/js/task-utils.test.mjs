import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");

async function importBrowserModule(relativePath) {
    const source = await readFile(path.join(repoRoot, relativePath), "utf8");
    return import(`data:text/javascript;charset=utf-8,${encodeURIComponent(source)}`);
}

const utils = await importBrowserModule("static/js/tasks/task-utils.js");

test("normalizes task and list defaults without mutating inputs", () => {
    const task = { id: "task-1", title: "Read", completed_occurrences: "bad" };
    const list = { id: "list-1", name: "Research" };

    assert.deepEqual(utils.normalizeTask(task), {
        id: "task-1",
        title: "Read",
        completed_occurrences: [],
        priority: "none",
        starred: false,
        reminder_minutes: -1,
    });
    assert.deepEqual(utils.normalizeList(list), {
        id: "list-1",
        name: "Research",
        description: "",
        hidden: false,
        sort_mode: "default",
    });
    assert.equal(task.priority, undefined);
    assert.equal(list.hidden, undefined);
});

test("builds fresh default recurrence rules", () => {
    const first = utils.createDefaultRecurrence();
    const second = utils.createDefaultRecurrence();

    assert.deepEqual(
        { every: first.every, unit: first.unit, endDate: first.endDate },
        { every: 1, unit: "week", endDate: null }
    );
    assert.match(first.startDate, /^\d{4}-\d{2}-\d{2}$/);
    assert.notEqual(first, second);
});

test("computes default recurrence end date one clamped month minus one day ahead", () => {
    assert.equal(utils.defaultRecurrenceEndDate(new Date(2026, 4, 22)), "2026-06-21");
    assert.equal(utils.defaultRecurrenceEndDate(new Date(2026, 0, 31)), "2026-02-27");
    assert.equal(utils.defaultRecurrenceEndDate(new Date(2024, 0, 31)), "2024-02-28");
});

test("sorts task lists and tasks with stable fallback fields", () => {
    assert.deepEqual(
        utils.sortedLists([
            { id: "b", name: "Beta", order: 2 },
            { id: "a", name: "Alpha", order: 2 },
            { id: "c", name: "Core", order: 1 },
        ]).map((item) => item.id),
        ["c", "a", "b"]
    );

    assert.deepEqual(
        utils.sortedTasks([
            { id: "task-b", title: "Beta", order: 1 },
            { id: "task-a", title: "Alpha", order: 1 },
            { id: "task-c", title: "Core", order: 0 },
        ]).map((item) => item.id),
        ["task-c", "task-a", "task-b"]
    );
});

test("sorts visible task rows by user-selected list mode", () => {
    const tasks = [
        { id: "no-deadline", title: "Zeta", order: 1 },
        { id: "late", title: "Alpha", order: 0, created_at: "2026-05-10T12:00:00Z", deadline_at: "2026-05-22T12:00:00Z" },
        { id: "early", title: "Gamma", order: 2, created_at: "2026-05-20T12:00:00Z", deadline_at: "2026-05-21T12:00:00Z" },
    ];

    assert.deepEqual(utils.sortTasksForList(tasks, "date").map((item) => item.id), ["early", "late", "no-deadline"]);
    assert.deepEqual(utils.sortTasksForList(tasks, "deadline").map((item) => item.id), ["early", "late", "no-deadline"]);
    assert.deepEqual(utils.sortTasksForList(tasks, "title").map((item) => item.id), ["late", "early", "no-deadline"]);
    assert.deepEqual(tasks.map((item) => item.id), ["no-deadline", "late", "early"]);
});

test("sorts deadline ties by priority before manual order", () => {
    const tasks = [
        { id: "same-low", title: "Low", priority: "low", order: 0, deadline_at: "2026-05-21T12:00:00Z" },
        { id: "same-high", title: "High", priority: "high", order: 2, deadline_at: "2026-05-21T12:00:00Z" },
        { id: "same-medium", title: "Medium", priority: "medium", order: 1, deadline_at: "2026-05-21T12:00:00Z" },
        { id: "no-deadline-high", title: "No Deadline High", priority: "high", order: 0 },
        { id: "no-deadline-none", title: "No Deadline None", priority: "none", order: 1 },
    ];

    assert.deepEqual(
        utils.sortTasksForList(tasks, "deadline").map((item) => item.id),
        ["same-high", "same-medium", "same-low", "no-deadline-high", "no-deadline-none"]
    );
});

test("detects completed recurring occurrences by next occurrence key", () => {
    assert.equal(utils.isRepeatingTaskCompleted({ completed: true }), true);
    assert.equal(utils.isRepeatingTaskCompleted({
        recurrence: { every: 1, unit: "week" },
        next_occurrence_key: "2026-05-20",
        completed_occurrences: [{ occurrence_key: "2026-05-20" }],
    }), true);
    assert.equal(utils.isRepeatingTaskCompleted({
        recurrence: { every: 1, unit: "week" },
        next_occurrence_key: "2026-05-27",
        completed_occurrences: [{ occurrence_key: "2026-05-20" }],
    }), false);
});

test("splits active and completed tasks without changing their order", () => {
    const tasks = [
        { id: "active", completed: false },
        { id: "done", completed: true },
        {
            id: "repeat-done",
            recurrence: { every: 1, unit: "week" },
            next_occurrence_key: "2026-05-20",
            completed_occurrences: [{ occurrence_key: "2026-05-20" }],
        },
    ];

    const groups = utils.splitTasksByCompletion(tasks);
    assert.deepEqual(groups.active.map((task) => task.id), ["active"]);
    assert.deepEqual(groups.completed.map((task) => task.id), ["done", "repeat-done"]);
});

test("normalizes task preferences from API payloads", () => {
    assert.deepEqual(utils.normalizeTaskPreferences(), { task_sound_enabled: true });
    assert.deepEqual(utils.normalizeTaskPreferences({ task_sound_enabled: false }), { task_sound_enabled: false });
    assert.deepEqual(utils.normalizeTaskPreferences({ task_sound_enabled: true }), { task_sound_enabled: true });
});

test("fetchJson tracks mutations and raises server-provided errors", async () => {
    const trackedLabels = [];
    globalThis.window = {
        APStudyPendingMutations: {
            track(request, label) {
                trackedLabels.push(label);
                return request;
            },
        },
        APStudyHttp: {
            async fetchJson(_url, options) {
                const request = options.method === "POST"
                    ? Promise.resolve({ saved: true })
                    : Promise.reject(new Error("Nope"));
                return window.APStudyPendingMutations.track(request, options.pendingLabel);
            },
        },
    };

    assert.deepEqual(await utils.fetchJson("/api/tasks", { method: "POST", body: "{}" }), { saved: true });
    assert.deepEqual(trackedLabels, ["task-save"]);
    await assert.rejects(() => utils.fetchJson("/api/tasks"), /Nope/);
});
