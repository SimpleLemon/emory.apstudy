import { ActionMenu, EmptyStarter, ListEditorDialog, ListRail, MaterialIcon, TaskSection } from "./task-components.js";
import {
    PrintSheet,
    groupTasksByList,
    listMenuItems,
    mergeById,
    removeById,
    replaceById,
    requestDestructiveAction,
    taskMenuItems,
    toggleActionMenu,
} from "./task-app-helpers.js";
import {
    appendTask,
    applyListOrderUpdates,
    applyTaskOrderUpdates,
    buildCompletedTaskOptimistic,
    buildListOrderUpdates,
    completeTaskRecord,
    createTaskList,
    createTaskRecord,
    destroyCompletedTasks,
    destroyTaskList,
    destroyTaskRecord,
    fetchTaskBoard,
    persistListOrder,
    persistTaskOrder,
    removeCompletedTasksFromList,
    taskOrderUpdatesFromDocument,
    updateTaskList,
    updateTaskRecord,
} from "./task-data.js";
import {
    isRepeatingTaskCompleted,
    normalizeList,
    normalizeTask,
    sortedLists,
} from "./task-utils.js";
import { createTaskSounds } from "./task-audio.js";
import * as React from "react";
import { createRoot } from "react-dom/client";

const h = React.createElement;

function restoreItemAtIndex(items, item, index) {
    if (!item || items.some((candidate) => candidate.id === item.id)) return items;
    const next = [...items];
    next.splice(Math.min(Math.max(0, index), next.length), 0, item);
    return next;
}

function promptForTaskNotifications(task, previousTask = null) {
    const enabled = Boolean(task?.deadline_at) && Number(task?.reminder_minutes) !== -1;
    const wasEnabled = Boolean(previousTask?.deadline_at) && Number(previousTask?.reminder_minutes) !== -1;
    if (enabled && !wasEnabled) {
        window.dispatchEvent(new CustomEvent("apstudy:notification-intent", { detail: { source: "tasks" } }));
    }
}

function buildTaskLoadingHtml() {
    const block = (className) => window.APStudySkeleton?.block?.(className)
        || `<div data-slot="skeleton" class="bg-muted rounded-md animate-pulse ${className}"></div>`;
    const taskRows = Array.from({ length: 5 }, (_, index) => `
        <div class="task-row task-skeleton-row">
            <div class="task-row-main">
                ${block("task-skeleton-checkbox")}
                <div class="flex flex-1 flex-col gap-2">
                    ${block(index % 2 ? "h-3 w-3/4" : "h-3 w-full")}
                    ${block("h-3 w-1/3")}
                </div>
                ${block("h-8 w-8")}
            </div>
        </div>
    `).join("");

    return `
        <div class="task-app task-skeleton-app">
            <header class="task-header">
                <div>
                    <h1 class="task-title workspace-page-title">Tasks</h1>
                    <p class="workspace-page-subtitle">Lists, deadlines, repeat schedules, and calendar-synced work.</p>
                </div>
            </header>
            <div class="task-skeleton apstudy-skeleton" role="status" aria-live="polite" aria-busy="true">
                <span class="sr-only">Loading tasks...</span>
                <div class="contents task-skeleton-layout" aria-hidden="true">
                    <aside class="task-list-rail task-skeleton-rail">
                        <div class="task-skeleton-rail-item">${block("size-5")} ${block("h-3 flex-1")}</div>
                        <div class="task-skeleton-rail-item">${block("size-5")} ${block("h-3 w-4/5")}</div>
                        <div class="task-skeleton-rail-divider"></div>
                        ${Array.from({ length: 3 }, (_, index) => `<div class="task-skeleton-rail-item">${block("size-5")} ${block(index === 2 ? "h-3 w-2/3" : "h-3 flex-1")} ${block("h-5 w-6 rounded-full")}</div>`).join("")}
                    </aside>
                    <section class="task-workspace">
                        <article class="task-list-section task-skeleton-section">
                            <div class="task-list-section-header">
                                ${block("h-8 w-8")}
                                <div class="flex flex-col gap-2">${block("h-5 w-36")} ${block("h-3 w-48")}</div>
                                ${block("h-3 w-12")}
                                ${block("h-8 w-8")}
                            </div>
                            <div class="task-list-body">${taskRows}</div>
                        </article>
                    </section>
                </div>
            </div>
        </div>
    `;
}

function TaskApp({ completeSound, uncompleteSound }) {
    const [lists, setLists] = React.useState([]);
    const [tasks, setTasks] = React.useState([]);
    const [selectedListId, setSelectedListId] = React.useState("all");
    const [loading, setLoading] = React.useState(true);
    const [error, setError] = React.useState("");
    const [expandedTaskId, setExpandedTaskId] = React.useState("");
    const [soundEnabled, setSoundEnabled] = React.useState(true);
    const [listDialog, setListDialog] = React.useState(null);
    const [actionMenu, setActionMenu] = React.useState(null);
    const [printListId, setPrintListId] = React.useState("");
    const [completedOpenListIds, setCompletedOpenListIds] = React.useState(() => new Set());
    const highlightTaskId = React.useMemo(() => new URLSearchParams(window.location.search).get("task") || "", []);
    const sounds = React.useMemo(() => createTaskSounds({ completeSound, uncompleteSound }), [completeSound, uncompleteSound]);

    React.useEffect(() => {
        if (error && window.APStudyToast) {
            window.APStudyToast.error(error);
        }
    }, [error]);
    const listsRef = React.useRef(lists);
    const tasksRef = React.useRef(tasks);
    const listMutationVersionsRef = React.useRef(new Map());
    const taskMutationVersionsRef = React.useRef(new Map());

    const setListsAndRef = React.useCallback((nextListsOrUpdater) => {
        setLists((current) => {
            const nextLists = typeof nextListsOrUpdater === "function" ? nextListsOrUpdater(current) : nextListsOrUpdater;
            listsRef.current = nextLists;
            return nextLists;
        });
    }, []);

    const setTasksAndRef = React.useCallback((nextTasksOrUpdater) => {
        setTasks((current) => {
            const nextTasks = typeof nextTasksOrUpdater === "function" ? nextTasksOrUpdater(current) : nextTasksOrUpdater;
            tasksRef.current = nextTasks;
            return nextTasks;
        });
    }, []);

    React.useEffect(() => {
        listsRef.current = lists;
    }, [lists]);

    React.useEffect(() => {
        tasksRef.current = tasks;
    }, [tasks]);

    React.useEffect(() => () => sounds.dispose(), [sounds]);

    const listById = React.useMemo(() => new Map(lists.map((list) => [list.id, list])), [lists]);

    const tasksByList = React.useMemo(() => {
        return groupTasksByList(lists, tasks, listById);
    }, [lists, listById, tasks]);
    const listByIdRef = React.useRef(listById);
    const tasksByListRef = React.useRef(tasksByList);
    listByIdRef.current = listById;
    tasksByListRef.current = tasksByList;

    const orderedLists = React.useMemo(() => sortedLists(lists), [lists]);
    const visibleOrderedLists = React.useMemo(() => orderedLists.filter((list) => !list.hidden), [orderedLists]);
    const allListsHidden = lists.length > 0 && visibleOrderedLists.length === 0;

    const load = React.useCallback(async () => {
        setError("");
        try {
            const { lists: nextLists, tasks: nextTasks, preferences } = await fetchTaskBoard();
            setListsAndRef(nextLists);
            setTasksAndRef(nextTasks);
            setSoundEnabled(preferences?.task_sound_enabled !== false);
            if (highlightTaskId) {
                const match = nextTasks.find((task) => task.id === highlightTaskId || task.$id === highlightTaskId);
                const matchList = match ? nextLists.find((list) => list.id === match.list_id) : null;
                if (match && matchList && !matchList.hidden) {
                    setSelectedListId(match.list_id || "all");
                    setExpandedTaskId(match.id);
                }
            } else setSelectedListId((current) => (
                current !== "all" && current !== "starred" && !nextLists.some((list) => list.id === current && !list.hidden)
                    ? "all"
                    : current
            ));
        } catch (err) {
            setError(err.message || "Unable to load tasks.");
        } finally {
            setLoading(false);
        }
    }, [highlightTaskId, setListsAndRef, setTasksAndRef]);

    React.useEffect(() => {
        load();
    }, []);

    React.useEffect(() => {
        if (!printListId) return undefined;
        const clearPrintList = () => setPrintListId("");
        window.addEventListener("afterprint", clearPrintList, { once: true });
        const timer = window.setTimeout(() => window.print(), 60);
        return () => {
            window.clearTimeout(timer);
            window.removeEventListener("afterprint", clearPrintList);
        };
    }, [printListId]);

    const updateTaskInState = React.useCallback((updatedTask) => {
        setTasksAndRef((current) => replaceById(current, updatedTask.id, normalizeTask(updatedTask)));
    }, [setTasksAndRef]);

    const createList = React.useCallback(async ({ name, description = "" }) => {
        const listName = (name || "New List").trim();
        if (!listName) return;
        setError("");
        try {
            const created = await createTaskList({ name: listName, description });
            setListsAndRef((current) => sortedLists([...current, created]));
            setSelectedListId(created.id);
            setListDialog(null);
        } catch (err) {
            setError(err.message || "Unable to create list.");
        }
    }, [setListsAndRef]);

    const updateList = React.useCallback(async (listId, updates) => {
        const previousLists = listsRef.current;
        const versions = listMutationVersionsRef.current;
        const version = (versions.get(listId) || 0) + 1;
        versions.set(listId, version);
        const nextUpdates = { ...updates };
        setListsAndRef((current) => mergeById(current, listId, nextUpdates, normalizeList));
        if (nextUpdates.hidden) {
            setSelectedListId((current) => current === listId ? "all" : current);
        }
        try {
            const updatedList = await updateTaskList(listId, nextUpdates);
            if (versions.get(listId) !== version) return;
            setListsAndRef((current) => sortedLists(replaceById(current, listId, updatedList)));
        } catch (err) {
            if (versions.get(listId) !== version) return;
            setError(err.message || "Unable to update list.");
            setListsAndRef(previousLists);
        }
    }, [setListsAndRef]);

    const toggleListVisibility = React.useCallback((listId) => {
        const currentList = listsRef.current.find((list) => list.id === listId);
        if (!currentList) return;
        updateList(listId, { hidden: !currentList.hidden });
    }, [updateList]);

    const deleteList = React.useCallback(async (listId) => {
        const list = listByIdRef.current.get(listId);
        const accepted = await requestDestructiveAction({
            title: "Delete list?",
            message: `Delete "${list?.name || "this list"}" and every task inside it?`,
            acceptLabel: "Delete list",
        });
        if (!accepted) return;
        const previousLists = listsRef.current;
        const previousTasks = tasksRef.current;
        const listIndex = previousLists.findIndex((item) => item.id === listId);
        const removedTasks = previousTasks
            .map((task, index) => ({ task, index }))
            .filter((record) => record.task.list_id === listId);
        const previousSelectedListId = selectedListId;
        setListsAndRef((current) => removeById(current, listId));
        setTasksAndRef((current) => current.filter((task) => task.list_id !== listId));
        setSelectedListId((current) => current === listId ? "all" : current);
        window.APStudyUndo?.stage?.({
            message: `"${list?.name || "List"}" and its tasks were deleted.`,
            commit: ({ reason }) => destroyTaskList(listId, { keepalive: reason === "pagehide" }),
            restore: () => {
                setListsAndRef((current) => restoreItemAtIndex(current, list, listIndex));
                setTasksAndRef((current) => removedTasks.reduce(
                    (items, record) => restoreItemAtIndex(items, record.task, record.index),
                    current,
                ));
                setSelectedListId(previousSelectedListId);
            },
            errorTitle: "Couldn’t delete list",
        });
    }, [selectedListId, setListsAndRef, setTasksAndRef]);

    const deleteCompletedTasks = React.useCallback(async (listId) => {
        const list = listByIdRef.current.get(listId);
        const accepted = await requestDestructiveAction({
            title: "Delete completed tasks?",
            message: `Delete completed tasks in "${list?.name || "this list"}"? Recurring task definitions will stay, but completed occurrences will be cleared.`,
            acceptLabel: "Delete completed",
        });
        if (!accepted) return;
        const previousTasks = tasksRef.current;
        const removedTasks = previousTasks
            .map((task, index) => ({ task, index }))
            .filter((record) => (
                record.task.list_id === listId && (
                    record.task.recurrence ? record.task.completed_occurrences?.length : record.task.completed
                )
            ));
        setTasksAndRef((current) => removeCompletedTasksFromList(current, listId));
        window.APStudyUndo?.stage?.({
            message: `${removedTasks.length} completed task${removedTasks.length === 1 ? "" : "s"} deleted from "${list?.name || "this list"}".`,
            commit: ({ reason }) => destroyCompletedTasks(listId, { keepalive: reason === "pagehide" }),
            restore: () => setTasksAndRef((current) => removedTasks.reduce((items, record) => {
                const existingIndex = items.findIndex((task) => task.id === record.task.id);
                if (existingIndex < 0) return restoreItemAtIndex(items, record.task, record.index);
                const next = [...items];
                next[existingIndex] = {
                    ...next[existingIndex],
                    completed: record.task.completed,
                    completed_at: record.task.completed_at,
                    completed_occurrences: record.task.completed_occurrences,
                };
                return next;
            }, current)),
            errorTitle: "Couldn’t delete completed tasks",
        });
    }, [setTasksAndRef]);

    const createTask = React.useCallback(async (listId, draft) => {
        setError("");
        try {
            const task = await createTaskRecord(listId, draft);
            setTasksAndRef((current) => appendTask(current, task));
            promptForTaskNotifications(task);
            return task;
        } catch (err) {
            setError(err.message || "Unable to create task.");
            throw err;
        }
    }, [setTasksAndRef]);

    const updateTask = React.useCallback(async (taskId, updates) => {
        const previousTask = tasksRef.current.find((task) => task.id === taskId) || null;
        const versions = taskMutationVersionsRef.current;
        const version = (versions.get(taskId) || 0) + 1;
        versions.set(taskId, version);
        setError("");
        setTasksAndRef((current) => mergeById(current, taskId, updates, normalizeTask));
        try {
            const updatedTask = await updateTaskRecord(taskId, updates);
            if (versions.get(taskId) !== version) return updatedTask;
            updateTaskInState(updatedTask);
            promptForTaskNotifications(updatedTask, previousTask);
            return updatedTask;
        } catch (err) {
            const message = err.message || "Unable to update task.";
            if (versions.get(taskId) !== version) {
                return { ok: false, stale: true, error: message };
            }
            setError(message);
            if (previousTask) {
                setTasksAndRef((current) => replaceById(current, taskId, previousTask));
            }
            return { ok: false, error: message };
        }
    }, [setTasksAndRef, updateTaskInState]);

    const deleteTask = React.useCallback(async (taskId) => {
        const task = tasksRef.current.find((item) => item.id === taskId);
        const accepted = await requestDestructiveAction({
            title: "Delete task?",
            message: `Delete "${task?.title || "this task"}"?`,
            acceptLabel: "Delete task",
        });
        if (!accepted) return;
        const previous = tasksRef.current;
        const taskIndex = previous.findIndex((item) => item.id === taskId);
        setTasksAndRef((current) => removeById(current, taskId));
        window.APStudyUndo?.stage?.({
            message: `"${task?.title || "Task"}" deleted.`,
            commit: ({ reason }) => destroyTaskRecord(taskId, { keepalive: reason === "pagehide" }),
            restore: () => setTasksAndRef((current) => restoreItemAtIndex(current, task, taskIndex)),
            errorTitle: "Couldn’t delete task",
        });
    }, [setTasksAndRef]);

    const completeTask = React.useCallback(async (task, completed) => {
        const previous = tasksRef.current;
        const optimistic = buildCompletedTaskOptimistic(task, completed);
        setTasksAndRef((current) => replaceById(current, task.id, optimistic.task));
        completed ? sounds.playComplete(soundEnabled) : sounds.playUncomplete(soundEnabled);
        try {
            updateTaskInState(await completeTaskRecord(task.id, completed, optimistic.occurrenceKey));
        } catch (err) {
            setError(err.message || "Unable to update completion.");
            setTasksAndRef(previous);
        }
    }, [setTasksAndRef, soundEnabled, sounds, updateTaskInState]);

    const reorderLists = React.useCallback(async (orderedIds) => {
        const updates = buildListOrderUpdates(orderedIds);
        setListsAndRef((current) => applyListOrderUpdates(current, updates));
        try {
            await persistListOrder(updates);
        } catch (err) {
            setError(err.message || "Unable to reorder lists.");
            load();
        }
    }, [load, setListsAndRef]);

    const reorderTasks = React.useCallback(async () => {
        const updates = taskOrderUpdatesFromDocument();
        setTasksAndRef((current) => applyTaskOrderUpdates(current, updates));
        try {
            await persistTaskOrder(updates);
        } catch (err) {
            setError(err.message || "Unable to reorder tasks.");
            load();
        }
    }, [load, setTasksAndRef]);

    const persistTaskUpdates = React.useCallback(async (updates) => {
        setTasksAndRef((current) => applyTaskOrderUpdates(current, updates));
        try {
            await persistTaskOrder(updates);
        } catch (err) {
            setError(err.message || "Unable to reorder tasks.");
            load();
        }
    }, [load, setTasksAndRef]);

    const moveList = React.useCallback((listId, direction) => {
        const ids = sortedLists(listsRef.current).map((list) => list.id);
        const index = ids.indexOf(listId);
        const target = index + direction;
        if (index < 0 || target < 0 || target >= ids.length) return;
        [ids[index], ids[target]] = [ids[target], ids[index]];
        void reorderLists(ids);
    }, [reorderLists]);

    const moveTask = React.useCallback((taskId, direction) => {
        const task = tasksRef.current.find((item) => item.id === taskId);
        if (!task) return;
        const ordered = [...(tasksByListRef.current.get(task.list_id) || [])];
        const index = ordered.findIndex((item) => item.id === taskId);
        const target = index + direction;
        if (index < 0 || target < 0 || target >= ordered.length) return;
        [ordered[index], ordered[target]] = [ordered[target], ordered[index]];
        if ((listByIdRef.current.get(task.list_id)?.sort_mode || "default") !== "default") {
            void updateList(task.list_id, { sort_mode: "default" });
        }
        void persistTaskUpdates(ordered.map((item, orderIndex) => ({ id: item.id, list_id: task.list_id, order: (orderIndex + 1) * 1000 })));
    }, [persistTaskUpdates, updateList]);

    const moveTaskToList = React.useCallback((taskId, targetListId) => {
        const task = tasksRef.current.find((item) => item.id === taskId);
        if (!task || !listByIdRef.current.has(targetListId) || task.list_id === targetListId) return;
        const source = (tasksByListRef.current.get(task.list_id) || []).filter((item) => item.id !== taskId);
        const target = [...(tasksByListRef.current.get(targetListId) || []), task];
        const updates = [
            ...source.map((item, index) => ({ id: item.id, list_id: task.list_id, order: (index + 1) * 1000 })),
            ...target.map((item, index) => ({ id: item.id, list_id: targetListId, order: (index + 1) * 1000 })),
        ];
        void persistTaskUpdates(updates);
    }, [persistTaskUpdates]);

    const openListDialog = React.useCallback((request) => {
        if (request?.mode === "quick") {
            createList({ name: request.name, description: "" });
            return;
        }
        setListDialog({ id: `${request.mode}-${request.list?.id || "new"}-${Date.now()}`, ...request });
    }, [createList]);

    const submitListDialog = React.useCallback(async (values) => {
        if (!listDialog) return;
        if (listDialog.mode === "create") {
            await createList(values);
            return;
        }
        if (listDialog.list?.id) {
            await updateList(listDialog.list.id, values);
            setListDialog(null);
        }
    }, [createList, listDialog, updateList]);

    const printListById = React.useCallback((listId) => {
        setPrintListId(listId);
    }, []);

    const setCompletedListOpen = React.useCallback((listId, open) => {
        setCompletedOpenListIds((current) => {
            const hasList = current.has(listId);
            if (hasList === open) return current;
            const next = new Set(current);
            if (open) next.add(listId);
            else next.delete(listId);
            return next;
        });
    }, []);

    const openListMenu = React.useCallback((listId, position) => {
        const list = listByIdRef.current.get(listId);
        if (!list) return;
        const listTasks = tasksByListRef.current.get(listId) || [];
        toggleActionMenu(setActionMenu, "list", listId, () => ({
            anchor: position.anchor,
            title: "Sort by",
            items: listMenuItems({
                list,
                listTasks,
                updateList,
                openListDialog,
                printListById,
                deleteCompletedTasks,
                deleteList,
                moveList,
                canMoveEarlier: sortedLists(listsRef.current).findIndex((item) => item.id === listId) > 0,
                canMoveLater: sortedLists(listsRef.current).findIndex((item) => item.id === listId) < listsRef.current.length - 1,
            }),
        }));
    }, [deleteCompletedTasks, deleteList, moveList, openListDialog, printListById, updateList]);

    const openTaskMenu = React.useCallback((taskId, position) => {
        const task = tasksRef.current.find((item) => item.id === taskId);
        const ordered = task ? (tasksByListRef.current.get(task.list_id) || []) : [];
        const taskIndex = ordered.findIndex((item) => item.id === taskId);
        toggleActionMenu(setActionMenu, "task", taskId, () => ({
            anchor: position.anchor,
            items: taskMenuItems(taskId, position, deleteTask, {
                moveTask,
                moveTaskToList,
                lists: sortedLists(listsRef.current),
                currentListId: task?.list_id || "",
                canMoveEarlier: taskIndex > 0,
                canMoveLater: taskIndex >= 0 && taskIndex < ordered.length - 1,
            }),
        }));
    }, [deleteTask, moveTask, moveTaskToList]);

    const workspaceLists = React.useMemo(() => {
        if (selectedListId === "all") return visibleOrderedLists;
        if (selectedListId === "starred") {
            return visibleOrderedLists
                .map((list) => ({ ...list, starredOnly: true }))
                .filter((list) => (tasksByList.get(list.id) || []).some((task) => task.starred));
        }
        const selected = listById.get(selectedListId);
        return selected && !selected.hidden ? [selected] : [];
    }, [listById, selectedListId, tasksByList, visibleOrderedLists]);

    const printListRecord = listById.get(printListId);
    const printTasks = printListRecord ? tasksByList.get(printListRecord.id) || [] : [];
    const printCompletedOpen = Boolean(printListId && completedOpenListIds.has(printListId));

    if (loading) {
        return h("div", {
            dangerouslySetInnerHTML: { __html: buildTaskLoadingHtml() },
        });
    }

    return h(React.Fragment, null,
        h("div", { className: "task-app" },
            h("header", { className: "task-header" },
                h("div", null,
                    h("h1", { className: "task-title workspace-page-title" }, "Tasks"),
                    h("p", { className: "workspace-page-subtitle" }, "Lists, deadlines, repeat schedules, and calendar-synced work.")
                )
            ),
            error ? h("p", { className: "task-operation-error", role: "alert" }, error) : null,
            lists.length === 0
                ? h(EmptyStarter, { onCreate: openListDialog })
                : h("div", { className: "task-layout" },
                    h(ListRail, {
                        lists,
                        selectedListId,
                        setSelectedListId,
                        tasksByList,
                        reorderLists,
                        updateList,
                        toggleListVisibility,
                        openListDialog,
                        openListMenu,
                    }),
                    h("section", { className: "task-workspace" },
                        allListsHidden
                            ? h("section", { className: "task-hidden-empty" },
                                h(MaterialIcon, { name: "visibility_off" }),
                                h("h2", null, "All lists are hidden"),
                                h("p", null, "Select a list to see your tasks")
                            )
                            : workspaceLists.length === 0
                                ? h("section", { className: "task-hidden-empty" },
                                    h(MaterialIcon, { name: selectedListId === "starred" ? "star" : "checklist" }),
                                    h("h2", null, selectedListId === "starred" ? "No starred tasks yet" : "Select a list to see your tasks"),
                                    h("p", null, selectedListId === "starred" ? "Star a task from any visible list to pin it here." : "Choose All tasks, Starred, or a visible list.")
                                )
                                : workspaceLists.map((list) => {
                                    const listTasks = tasksByList.get(list.id) || [];
                                    const sectionTasks = list.starredOnly ? listTasks.filter((task) => task.starred) : listTasks;
                                    return h(TaskSection, {
                                        key: `${list.starredOnly ? "starred" : "list"}-${list.id}`,
                                        list,
                                        tasks: sectionTasks,
                                        updateList,
                                        createTask,
                                        updateTask,
                                        deleteTask,
                                        completeTask,
                                        expandedTaskId,
                                        setExpandedTaskId,
                                        reorderTasks,
                                        highlightTaskId,
                                        openListMenu,
                                        openTaskMenu,
                                        onCompletedOpenChange: setCompletedListOpen,
                                    });
                                })
                    )
                ),
        ),
        h(ActionMenu, { menu: actionMenu, onClose: () => setActionMenu(null) }),
        h(ListEditorDialog, { dialog: listDialog, onClose: () => setListDialog(null), onSubmit: submitListDialog }),
        h(PrintSheet, {
            list: printListRecord,
            tasks: printTasks.filter((task) => printCompletedOpen || !isRepeatingTaskCompleted(task)),
            includeCompleted: printCompletedOpen,
        })
    );
}

class TaskInitializationBoundary extends React.Component {
    componentDidCatch(error) {
        window.APStudyTaskLoader?.fail?.(error);
        console.error("Unable to render the Tasks application.", error);
    }

    render() {
        return this.props.children;
    }
}

function TaskAppMount(props) {
    React.useEffect(() => {
        window.APStudyTaskLoader?.ready?.();
    }, []);
    return h(TaskApp, props);
}

const mount = document.getElementById("task-root");
let taskReactRoot = null;
if (mount) {
    try {
        taskReactRoot = createRoot(mount);
        taskReactRoot.render(h(TaskInitializationBoundary, null,
            h(TaskAppMount, {
                completeSound: mount.dataset.completeSound || "/static/audio/task-pop.mp3",
                uncompleteSound: mount.dataset.uncompleteSound || "/static/audio/task-pop-down.mp3",
            })
        ));
        window.APStudyPageLifecycle?.register?.({
            dispose() {
                taskReactRoot?.unmount();
                taskReactRoot = null;
            },
        });
    } catch (error) {
        window.APStudyTaskLoader?.fail?.(error);
        console.error("Unable to initialize the Tasks application.", error);
    }
} else {
    window.APStudyTaskLoader?.fail?.(new Error("Task mount was not found."));
}
