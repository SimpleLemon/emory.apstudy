import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const source = await readFile(path.join(repoRoot, "static/js/tasks/task.js"), "utf8");
const helpersSource = await readFile(path.join(repoRoot, "static/js/tasks/task-app-helpers.js"), "utf8");

test("task app keeps list visibility mutations latest-write-wins", () => {
    assert.match(source, /const listsRef = React\.useRef\(lists\)/);
    assert.match(source, /const listMutationVersionsRef = React\.useRef\(new Map\(\)\)/);
    assert.match(source, /const version = \(versions\.get\(listId\) \|\| 0\) \+ 1/);
    assert.match(source, /if \(versions\.get\(listId\) !== version\) return/);
    assert.match(source, /const toggleListVisibility = React\.useCallback/);
    assert.match(source, /hidden: !currentList\.hidden/);
});

test("task printing follows the completed-list disclosure state and includes printable checkboxes", () => {
    assert.match(source, /completedOpenListIds, setCompletedOpenListIds\] = React\.useState/);
    assert.match(source, /onCompletedOpenChange: setCompletedListOpen/);
    assert.match(source, /const printCompletedOpen = Boolean\(printListId && completedOpenListIds\.has\(printListId\)\)/);
    assert.match(source, /includeCompleted: printCompletedOpen/);
    assert.match(helpersSource, /includeCompleted \|\| !isRepeatingTaskCompleted\(task\)/);
    assert.match(helpersSource, /className: "task-print-checkbox"/);
});
