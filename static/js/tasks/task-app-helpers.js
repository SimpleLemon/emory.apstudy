import {
    LIST_SORT_OPTIONS,
    formatRepeat,
    isRepeatingTaskCompleted,
    sortTasksForList,
} from "./task-utils.js";
import { formatTaskDeadline } from "./task-deadline.js";
import { completedForDeleteSweep } from "./task-data.js";
import * as React from "react";

const h = React.createElement;

export function PrintSheet({ list, tasks, includeCompleted = false }) {
    if (!list) return null;
    const printableTasks = (tasks || []).filter((task) => includeCompleted || !isRepeatingTaskCompleted(task));
    return h("section", { className: "task-print-sheet", "aria-hidden": "true" },
        h("header", null,
            h("h1", null, list.name),
            list.description ? h("p", null, list.description) : null
        ),
        printableTasks.length
            ? h("ul", { className: "task-print-list" }, printableTasks.map((task) => {
                const completed = isRepeatingTaskCompleted(task);
                return h("li", { key: task.id, className: completed ? "is-completed" : "" },
                    h("span", { className: "task-print-checkbox", "aria-hidden": "true" }, completed ? "✓" : ""),
                    h("div", { className: "task-print-content" },
                        h("div", { className: "task-print-title" }, task.title),
                        h("div", { className: "task-print-meta" },
                            task.priority && task.priority !== "none" ? h("span", null, `Priority: ${task.priority}`) : null,
                            task.deadline_at ? h("span", null, `Deadline: ${formatTaskDeadline(task)}`) : null,
                            task.recurrence ? h("span", null, formatRepeat(task.recurrence)) : null,
                            completed ? h("span", null, "Completed") : null
                        )
                    )
                );
            }))
            : h("p", null, "No tasks in this list.")
    );
}

export function replaceById(items, id, nextItem) {
    return items.map((item) => item.id === id ? nextItem : item);
}

export function mergeById(items, id, updates, normalize) {
    const existing = items.find((item) => item.id === id);
    return existing ? replaceById(items, id, normalize({ ...existing, ...updates })) : items;
}

export function removeById(items, id) {
    return items.filter((item) => item.id !== id);
}

export function groupTasksByList(lists, tasks, listById) {
    const grouped = new Map(lists.map((list) => [list.id, []]));
    for (const task of tasks) {
        const listTasks = grouped.get(task.list_id) || [];
        listTasks.push(task);
        grouped.set(task.list_id, listTasks);
    }
    for (const [listId, listTasks] of grouped) {
        grouped.set(listId, sortTasksForList(listTasks, listById.get(listId)?.sort_mode));
    }
    return grouped;
}

export function toggleActionMenu(setActionMenu, type, id, buildMenu) {
    setActionMenu((current) => current?.type === type && current.id === id
        ? null
        : { type, id, nonce: Date.now(), ...buildMenu() });
}

export function listMenuItems({ list, listTasks, updateList, openListDialog, printListById, deleteCompletedTasks, deleteList, moveList, canMoveEarlier, canMoveLater }) {
    return [
        ...LIST_SORT_OPTIONS.map((option) => ({
            label: option.label,
            icon: option.icon,
            checked: (list.sort_mode || "default") === option.value,
            onClick: () => updateList(list.id, { sort_mode: option.value }),
        })),
        { separator: true },
        { label: "Move list earlier", icon: "arrow_upward", disabled: !canMoveEarlier, onClick: () => moveList(list.id, -1) },
        { label: "Move list later", icon: "arrow_downward", disabled: !canMoveLater, onClick: () => moveList(list.id, 1) },
        { separator: true },
        { label: "Rename list", icon: "edit", onClick: () => openListDialog({ mode: "edit", list }) },
        { label: "Print list", icon: "print", onClick: () => printListById(list.id) },
        {
            label: "Delete all completed tasks",
            icon: "playlist_remove",
            disabled: !listTasks.some(completedForDeleteSweep),
            onClick: () => deleteCompletedTasks(list.id),
        },
        { label: "Delete list", icon: "delete", danger: true, onClick: () => deleteList(list.id) },
    ];
}

export function taskMenuItems(taskId, position, deleteTask, { moveTask, moveTaskToList, lists = [], currentListId = "", canMoveEarlier = false, canMoveLater = false } = {}) {
    return [
        { label: "Configure task", icon: "tune", onClick: () => position.configure?.() },
        { label: "Move task earlier", icon: "arrow_upward", disabled: !canMoveEarlier, onClick: () => moveTask(taskId, -1) },
        { label: "Move task later", icon: "arrow_downward", disabled: !canMoveLater, onClick: () => moveTask(taskId, 1) },
        ...lists.filter((list) => list.id !== currentListId).map((list) => ({
            label: `Move to ${list.name}`,
            icon: "drive_file_move",
            onClick: () => moveTaskToList(taskId, list.id),
        })),
        { label: "Delete task", icon: "delete", danger: true, onClick: () => deleteTask(taskId) },
    ];
}

export function requestDestructiveAction({ title, message, acceptLabel }) {
    return window.APStudyConfirm?.request?.({
        title,
        message,
        acceptLabel,
        danger: true,
    }) ?? Promise.resolve(false);
}
