import {
    clearCalendarCache,
    fetchJson,
    localInputToIso,
    normalizeList,
    normalizeTaskPreferences,
    normalizeTask,
    sortedLists,
    sortedTasks,
} from "./task-utils.js";

export function completedForDeleteSweep(task) {
    if (task.recurrence) return task.completed_occurrences?.length > 0;
    return Boolean(task.completed);
}

export function normalizeTaskBoard(payload) {
    return {
        lists: sortedLists((payload.lists || []).map(normalizeList)),
        tasks: (payload.tasks || []).map(normalizeTask),
        preferences: normalizeTaskPreferences(payload.preferences),
    };
}

export async function fetchTaskBoard() {
    return normalizeTaskBoard(await fetchJson("/api/tasks"));
}

export async function createTaskList({ name, description = "" }) {
    const listName = (name || "New List").trim();
    if (!listName) return null;
    const payload = await fetchJson("/api/task-lists", {
        method: "POST",
        body: JSON.stringify({ name: listName, description }),
    });
    return normalizeList(payload.list);
}

export async function updateTaskList(listId, updates) {
    const payload = await fetchJson(`/api/task-lists/${encodeURIComponent(listId)}`, {
        method: "PATCH",
        body: JSON.stringify(updates),
    });
    return normalizeList(payload.list);
}

export async function destroyTaskList(listId, options = {}) {
    await fetchJson(`/api/task-lists/${encodeURIComponent(listId)}`, {
        method: "DELETE",
        keepalive: options.keepalive === true,
    });
    clearCalendarCache();
}

export function removeCompletedTasksFromList(tasks, listId) {
    return tasks.flatMap((task) => {
        if (task.list_id !== listId) return [task];
        if (task.recurrence) return [{ ...task, completed_occurrences: [] }];
        return task.completed ? [] : [task];
    });
}

export async function destroyCompletedTasks(listId, options = {}) {
    await fetchJson(`/api/task-lists/${encodeURIComponent(listId)}/completed-tasks`, {
        method: "DELETE",
        keepalive: options.keepalive === true,
    });
    clearCalendarCache();
}

export function buildTaskDraftPayload(listId, draft, timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC") {
    const rawDeadline = draft.deadline_at || null;
    const deadlineAt = rawDeadline && /(?:Z|[+-]\d{2}:\d{2})$/.test(rawDeadline)
        ? rawDeadline
        : localInputToIso(rawDeadline);
    const deadlineTime = Object.prototype.hasOwnProperty.call(draft, "deadline_time")
        ? draft.deadline_time
        : rawDeadline ? rawDeadline.slice(11, 16) : null;
    return {
        list_id: listId,
        title: draft.title,
        priority: draft.priority || "none",
        deadline_at: deadlineAt,
        deadline_time: deadlineTime,
        reminder_minutes: deadlineAt ? Number(draft.reminder_minutes ?? (deadlineTime ? 10 : -1)) : -1,
        timezone: draft.timezone || timezone,
        recurrence: draft.recurrence || null,
    };
}

export async function createTaskRecord(listId, draft) {
    const response = await fetchJson("/api/tasks", {
        method: "POST",
        body: JSON.stringify(buildTaskDraftPayload(listId, draft)),
    });
    clearCalendarCache();
    return normalizeTask(response.task);
}

export async function updateTaskRecord(taskId, updates) {
    const response = await fetchJson(`/api/tasks/${encodeURIComponent(taskId)}`, {
        method: "PATCH",
        body: JSON.stringify(updates),
    });
    clearCalendarCache();
    return normalizeTask(response.task);
}

export async function destroyTaskRecord(taskId, options = {}) {
    await fetchJson(`/api/tasks/${encodeURIComponent(taskId)}`, {
        method: "DELETE",
        keepalive: options.keepalive === true,
    });
    clearCalendarCache();
}

export function buildCompletedTaskOptimistic(task, completed, now = new Date()) {
    const occurrenceKey = task.recurrence ? task.next_occurrence_key : "single";
    const normalizedOccurrenceKey = occurrenceKey || "single";
    const nextTask = task.recurrence
        ? {
            ...task,
            completed_occurrences: completed
                ? [...task.completed_occurrences, { occurrence_key: normalizedOccurrenceKey, completed_at: now.toISOString() }]
                : task.completed_occurrences.filter((item) => item.occurrence_key !== normalizedOccurrenceKey),
        }
        : { ...task, completed, completed_at: completed ? now.toISOString() : null };

    return { occurrenceKey, task: normalizeTask(nextTask) };
}

export async function completeTaskRecord(taskId, completed, occurrenceKey) {
    const response = await fetchJson(`/api/tasks/${encodeURIComponent(taskId)}/complete`, {
        method: "POST",
        body: JSON.stringify({ completed, occurrence_key: occurrenceKey }),
    });
    clearCalendarCache();
    return normalizeTask(response.task);
}

export function buildListOrderUpdates(orderedIds) {
    return orderedIds.map((id, index) => ({ id, order: (index + 1) * 1000 }));
}

export function applyListOrderUpdates(lists, updates) {
    const orderById = new Map(updates.map((item) => [item.id, item.order]));
    return sortedLists(lists.map((list) => {
        const order = orderById.get(list.id);
        return order == null ? list : normalizeList({ ...list, order });
    }));
}

export function taskOrderUpdatesFromDocument(root = document) {
    const containers = Array.from(root.querySelectorAll("[data-task-list-body]"));
    return containers.flatMap((container) => {
        const listId = container.getAttribute("data-list-id");
        return Array.from(container.querySelectorAll("[data-task-id]")).map((row, index) => ({
            id: row.getAttribute("data-task-id"),
            list_id: listId,
            order: (index + 1) * 1000,
        }));
    });
}

export function applyTaskOrderUpdates(tasks, updates) {
    const updateById = new Map(updates.map((item) => [item.id, item]));
    return tasks.map((task) => {
        const match = updateById.get(task.id);
        return match ? normalizeTask({ ...task, list_id: match.list_id, order: match.order }) : task;
    });
}

export async function persistListOrder(updates) {
    await fetchJson("/api/tasks/reorder", {
        method: "PATCH",
        body: JSON.stringify({ lists: updates }),
    });
}

export async function persistTaskOrder(updates) {
    await fetchJson("/api/tasks/reorder", {
        method: "PATCH",
        body: JSON.stringify({ tasks: updates }),
    });
    clearCalendarCache();
}

export function appendTask(tasks, task) {
    return sortedTasks([...tasks, normalizeTask(task)]);
}
