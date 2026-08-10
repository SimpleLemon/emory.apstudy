import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const moduleUrl = (source) => `data:text/javascript;charset=utf-8,${encodeURIComponent(source)}`;
const source = await readFile(path.join(repoRoot, "static/js/tasks/task-entry-components.js"), "utf8");
const taskAppSource = await readFile(path.join(repoRoot, "static/js/tasks/task.js"), "utf8");
const controlsSource = await readFile(path.join(repoRoot, "static/js/tasks/task-form-controls.js"), "utf8");
const deadlineSource = await readFile(path.join(repoRoot, "static/js/tasks/task-deadline.js"), "utf8");
const reactStub = "const React = { createElement() {}, memo: (component) => component, useState() {}, useEffect() {}, useRef() {}, useId() {} };";
const entryModule = await import(moduleUrl(source
    .replace('import * as React from "react";', reactStub)
    .replace('import { AddTaskPopover } from "./task-popover.js";', "const AddTaskPopover = () => null;")
    .replace('import { DeadlinePanel, formatTaskDeadline, reminderLabel } from "./task-deadline.js";', "const DeadlinePanel = () => null; const formatTaskDeadline = () => \"\"; const reminderLabel = () => \"\";")
    .replace('import { TaskListbox } from "./task-form-controls.js";', "const TaskListbox = () => null;")
    .replace('import { RepeatMenuContent } from "./task-recurrence.js";', "const RepeatMenuContent = () => null;")
    .replace(/import \{\n    PRIORITY_OPTIONS,\n    createDefaultRecurrence,\n    formatRepeat,\n    isRepeatingTaskCompleted,\n\} from "\.\/task-utils\.js";/, "const PRIORITY_OPTIONS = []; const createDefaultRecurrence = () => ({}); const formatRepeat = () => \"\"; const isRepeatingTaskCompleted = () => false;")));

test("quick-add validation and failed-create recovery keep the draft actionable", () => {
    assert.equal(entryModule.validateTaskTitle(""), "Enter a task title before adding it.");
    assert.equal(entryModule.validateTaskTitle("   "), "Enter a task title before adding it.");
    assert.equal(entryModule.validateTaskTitle("Read chapter 3"), "");
    assert.equal(entryModule.taskErrorMessage(new Error("Server unavailable"), "Unable to create task."), "Server unavailable");

    const createIndex = source.indexOf("await createTask(listId");
    const resetIndex = source.indexOf("reset();", createIndex);
    const catchIndex = source.indexOf("catch (err)", createIndex);
    assert.ok(createIndex >= 0 && resetIndex > createIndex && catchIndex > resetIndex);
    assert.match(source, /setSubmitError\(taskErrorMessage\(err, "Unable to create task\. Try again\."\)\)/);
    assert.match(source, /"aria-invalid": titleError \? "true" : undefined/);
});

test("failed priority and deadline updates leave their floating layer open with the server error", () => {
    assert.match(taskAppSource, /return \{ ok: false, error: message \};/);
    assert.match(controlsSource, /if \(result\?\.ok === false\) \{\n\s+setError\(result\.error \|\| "Unable to update this field\. Try again\."\);\n\s+return;/);
    assert.match(deadlineSource, /if \(result\?\.ok === false\) setError\(result\.error \|\| "Unable to update the deadline\. Try again\."\);/);
    assert.match(source, /if \(result\?\.ok !== false\) setDetailPopover\(null\);/);
});

test("failed recurrence updates keep the repeat editor open", () => {
    assert.match(source, /const clearRepeat = async \(\) => \{[\s\S]*if \(result\?\.ok === false\) return result;[\s\S]*setDetailPopover\(null\);/);
    assert.match(source, /const saveRepeat = async \(\) => \{[\s\S]*if \(result\?\.ok === false\) return result;[\s\S]*setDetailPopover\(null\);/);
});
