import * as React from "react";
import { createPortal } from "react-dom";
import Sortable from "sortablejs";
import { AddTaskForm, TaskRow } from "./task-entry-components.js";
import { getFloatingPosition } from "./task-floating.js";

import {
    DEFAULT_LIST_NAMES,
    isRepeatingTaskCompleted,
    splitTasksByCompletion,
    sortedLists,
} from "./task-utils.js";

const h = React.createElement;

export function MaterialIcon({ name, className = "" }) {
    return h("span", { className: `material-symbols-outlined ${className}`, "aria-hidden": "true" }, name);
}

function cx(...parts) {
    return parts.filter(Boolean).join(" ");
}

function iconButton({ icon, label, className = "", active = false, triggerKey = "", title = label, ...props }) {
    const triggerProps = triggerKey ? { "data-task-menu-trigger": triggerKey } : {};
    return h("button", {
        type: "button",
        className: cx("task-icon-button", className, active && "is-on"),
        "aria-label": label,
        title,
        ...triggerProps,
        ...props,
    }, h(MaterialIcon, { name: icon }));
}

function buttonWithIcon({ icon, children, className, ...props }) {
    return h("button", { type: "button", className, ...props },
        h(MaterialIcon, { name: icon }),
        h("span", null, children)
    );
}

function menuTrigger({ id, kind, className, label, onOpen, stopPropagation = false, getPosition = menuAnchorFromEvent }) {
    return iconButton({
        icon: "more_vert",
        label,
        className,
        triggerKey: `${kind}:${id}`,
        onClick: (event) => {
            if (stopPropagation) event.stopPropagation();
            onOpen(id, getPosition(event));
        },
    });
}

function railButton({ active, icon, label, count, onClick }) {
    return h("button", {
        type: "button",
        className: cx("task-rail-primary", active && "is-active"),
        onClick,
    },
        h(MaterialIcon, { name: icon }),
        h("span", null, label),
        h("strong", null, count)
    );
}

function useSortable(ref, options, dependencies) {
    React.useEffect(() => {
        if (!ref.current) return undefined;
        const sortable = Sortable.create(ref.current, options());
        return () => sortable.destroy();
    }, dependencies);
}

function actionMenuItem(item, index, onClose) {
    if (item.separator) {
        return h("div", { key: `sep-${index}`, className: "task-action-menu-separator", role: "separator" });
    }
    const checkable = typeof item.checked === "boolean";
    return h("button", {
        key: `${item.label}-${index}`,
        type: "button",
        role: checkable ? "menuitemradio" : "menuitem",
        className: cx(item.danger && "is-danger", item.checked && "is-checked"),
        disabled: item.disabled,
        "aria-checked": checkable ? String(item.checked) : undefined,
        onClick: () => {
            onClose();
            item.onClick?.();
        },
    },
        h(MaterialIcon, { name: item.checked ? "check" : item.icon || "radio_button_unchecked" }),
        h("span", null, item.label)
    );
}

export function EmptyStarter({ onCreate }) {
    return h("section", { className: "task-empty-panel" },
        h("div", { className: "task-empty-icon" }, h(MaterialIcon, { name: "checklist" })),
        h("h2", null, "Start with a list"),
        h("p", null, "Create a named list for school, research, personal work, or anything else you want to keep moving."),
        h("div", { className: "task-empty-actions" },
            buttonWithIcon({ className: "task-primary-button", icon: "add", onClick: () => onCreate({ mode: "create" }), children: "Create new list" }),
            DEFAULT_LIST_NAMES.map((name) => h("button", { key: name, type: "button", onClick: () => onCreate({ mode: "quick", name }) }, name))
        )
    );
}

export function ActionMenu({ menu, onClose }) {
    const menuRef = React.useRef(null);
    const previousFocusRef = React.useRef(null);
    const onCloseRef = React.useRef(onClose);
    const menuKey = menu ? `${menu.type || "menu"}:${menu.id || "none"}:${menu.nonce || 0}` : "";
    const [position, setPosition] = React.useState({ key: "", top: 0, left: 0, ready: false });

    React.useEffect(() => {
        onCloseRef.current = onClose;
    }, [onClose]);

    React.useEffect(() => {
        if (!menu) return undefined;
        previousFocusRef.current = document.activeElement;
        const onPointerDown = (event) => {
            if (menuRef.current?.contains(event.target)) return;
            if (event.target?.closest?.("[data-task-menu-trigger]")) return;
            onCloseRef.current();
        };
        const onKeyDown = (event) => {
            if (event.key === "Escape") onCloseRef.current();
        };
        const onScroll = (event) => {
            if (menuRef.current?.contains(event.target)) return;
            onCloseRef.current();
        };
        const onResize = () => onCloseRef.current();
        document.addEventListener("pointerdown", onPointerDown);
        document.addEventListener("keydown", onKeyDown);
        window.addEventListener("scroll", onScroll, true);
        window.addEventListener("resize", onResize);
        window.visualViewport?.addEventListener("resize", onResize);
        return () => {
            document.removeEventListener("pointerdown", onPointerDown);
            document.removeEventListener("keydown", onKeyDown);
            window.removeEventListener("scroll", onScroll, true);
            window.removeEventListener("resize", onResize);
            window.visualViewport?.removeEventListener("resize", onResize);
            if (menuRef.current?.contains(document.activeElement)) {
                previousFocusRef.current?.focus?.({ preventScroll: true });
            }
        };
    }, [menu]);

    React.useLayoutEffect(() => {
        if (!menu || !menuRef.current) return;
        const reposition = () => {
            const menuRect = menuRef.current?.getBoundingClientRect();
            if (!menuRect) return;
            const anchor = menu.anchor || {
                top: menu.top || 0,
                bottom: menu.top || 0,
                left: menu.left || 0,
                right: menu.left || 0,
            };
            const next = getFloatingPosition(anchor, menuRect, { align: "end", gap: 8 });
            setPosition({ key: menuKey, ...next, ready: true });
        };
        reposition();
        if (typeof ResizeObserver === "undefined") return undefined;
        const observer = new ResizeObserver(reposition);
        observer.observe(menuRef.current);
        return () => observer.disconnect();
    }, [menu, menuKey]);

    const ready = Boolean(menu && position.key === menuKey && position.ready);
    React.useEffect(() => {
        if (ready) menuRef.current?.querySelector("button:not([disabled])")?.focus({ preventScroll: true });
    }, [menuKey, ready]);

    if (!menu) return null;

    const handleMenuKeyDown = (event) => {
        if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
        const items = Array.from(menuRef.current?.querySelectorAll("button:not([disabled])") || []);
        if (!items.length) return;
        event.preventDefault();
        const index = items.indexOf(document.activeElement);
        if (event.key === 'Home') items[0].focus();
        else if (event.key === 'End') items.at(-1).focus();
        else if (event.key === 'ArrowDown') items[(index + 1 + items.length) % items.length].focus();
        else items[(index - 1 + items.length) % items.length].focus();
    };
    const layer = h("div", {
        ref: menuRef,
        className: "task-action-menu",
        role: "menu",
        "aria-label": menu.title || "Task actions",
        "data-task-floating-layer": "action-menu",
        onKeyDown: handleMenuKeyDown,
        style: {
            top: `${ready ? position.top : 0}px`,
            left: `${ready ? position.left : 0}px`,
            visibility: ready ? "visible" : "hidden",
        },
    },
        menu.title ? h("div", { className: "task-action-menu-title" }, menu.title) : null,
        menu.items.map((item, index) => actionMenuItem(item, index, onCloseRef.current))
    );
    return document.body ? createPortal(layer, document.body) : layer;
}

export function ListEditorDialog({ dialog, onClose, onSubmit }) {
    const [name, setName] = React.useState("");
    const [description, setDescription] = React.useState("");

    React.useEffect(() => {
        setName(dialog?.list?.name || "");
        setDescription(dialog?.list?.description || "");
    }, [dialog?.id, dialog?.list?.name, dialog?.list?.description]);

    React.useEffect(() => {
        if (!dialog) return undefined;
        const onKeyDown = (event) => {
            if (event.key === "Escape") onClose();
        };
        document.addEventListener("keydown", onKeyDown);
        return () => document.removeEventListener("keydown", onKeyDown);
    }, [dialog, onClose]);

    if (!dialog) return null;
    const creating = dialog.mode === "create";
    return h("div", { className: "task-modal", role: "dialog", "aria-modal": "true", "aria-labelledby": "task-list-dialog-title" },
        h("form", {
            className: "task-modal-panel",
            onSubmit: (event) => {
                event.preventDefault();
                const nextName = name.trim();
                if (!nextName) return;
                onSubmit({ name: nextName, description: description.trim() });
            },
        },
            h("header", { className: "task-modal-header" },
                h("div", null,
                    h("h2", { id: "task-list-dialog-title" }, creating ? "Create new list" : "Rename list"),
                    h("p", null, creating ? "Name it now; add context if it helps future you." : "Update the list name and optional description.")
                ),
                iconButton({ icon: "close", label: "Close", onClick: onClose })
            ),
            h("label", { className: "task-field" },
                h("span", null, "Name"),
                h("input", {
                    value: name,
                    maxLength: 120,
                    autoFocus: true,
                    onChange: (event) => setName(event.target.value),
                    placeholder: "School, research, personal..."
                })
            ),
            h("label", { className: "task-field" },
                h("span", null, "Description"),
                h("textarea", {
                    value: description,
                    maxLength: 1000,
                    onChange: (event) => setDescription(event.target.value),
                    placeholder: "Optional note for this list"
                })
            ),
            h("footer", { className: "task-modal-actions" },
                h("button", { type: "button", className: "task-secondary-button", onClick: onClose }, "Cancel"),
                buttonWithIcon({
                    type: "submit",
                    className: "task-primary-button",
                    disabled: !name.trim(),
                    icon: creating ? "add" : "check",
                    children: creating ? "Create list" : "Save",
                })
            )
        )
    );
}

function countIncomplete(tasks) {
    return tasks.reduce((count, task) => count + (isRepeatingTaskCompleted(task) ? 0 : 1), 0);
}

function menuAnchorFromEvent(event) {
    const rect = event.currentTarget.getBoundingClientRect();
    return {
        anchor: {
            top: rect.top,
            right: rect.right,
            bottom: rect.bottom,
            left: rect.left,
        },
    };
}

export function ListRail(props) {
    const {
        lists,
        selectedListId,
        setSelectedListId,
        tasksByList,
        reorderLists,
        updateList,
        toggleListVisibility,
        openListDialog,
        openListMenu,
    } = props;
    const railRef = React.useRef(null);
    const [listsOpen, setListsOpen] = React.useState(true);
    const orderedLists = React.useMemo(() => sortedLists(lists), [lists]);
    const visibleLists = orderedLists.filter((list) => !list.hidden);
    const visibleTasks = visibleLists.flatMap((list) => tasksByList.get(list.id) || []);
    const allIncomplete = countIncomplete(visibleTasks);
    const starredCount = visibleTasks.filter((task) => task.starred).length;

    useSortable(railRef, () => ({
            animation: window.APStudyAccessibility?.prefersReducedMotion?.() ? 0 : 150,
            draggable: ".task-list-nav-item",
            handle: ".task-list-nav-content",
            filter: ".task-list-visibility, .task-list-row-menu",
            ghostClass: "task-sortable-ghost",
            chosenClass: "task-sortable-chosen",
            onEnd: () => {
                const ids = Array.from(railRef.current.querySelectorAll("[data-list-id]")).map((item) => item.getAttribute("data-list-id"));
                reorderLists(ids);
            },
        }), [orderedLists.map((list) => list.id).join("|"), reorderLists]);

    return h("aside", { className: "task-list-rail" },
        h("div", { className: "task-rail-main" },
            railButton({ active: selectedListId === "all", icon: "select_all", label: "All tasks", count: allIncomplete, onClick: () => setSelectedListId("all") }),
            railButton({ active: selectedListId === "starred", icon: "star", label: "Starred", count: starredCount, onClick: () => setSelectedListId("starred") })
        ),
        h("section", { className: "task-rail-section" },
            h("div", { className: "task-rail-section-header" },
                h("button", {
                    type: "button",
                    className: "task-rail-section-toggle",
                    onClick: () => setListsOpen((current) => !current),
                    "aria-expanded": listsOpen ? "true" : "false",
                },
                    h(MaterialIcon, { name: listsOpen ? "expand_more" : "chevron_right" }),
                    h("span", null, "Lists")
                ),
                buttonWithIcon({ className: "task-create-list-small", icon: "add", onClick: () => openListDialog({ mode: "create" }), children: "Create new list" })
            ),
            listsOpen ? h("div", { ref: railRef, className: "task-list-nav" },
                orderedLists.map((list) => {
                    const listTasks = tasksByList.get(list.id) || [];
                    const incompleteCount = countIncomplete(listTasks);
                    return h("div", {
                        key: list.id,
                        "data-list-id": list.id,
                        className: cx("task-list-nav-item", selectedListId === list.id && "is-active", list.hidden && "is-hidden-list"),
                    },
                        h("button", {
                            type: "button",
                            className: cx("task-list-visibility", !list.hidden && "is-checked"),
                            onClick: (event) => {
                                event.stopPropagation();
                                toggleListVisibility(list.id);
                            },
                            "aria-label": list.hidden ? `Show ${list.name}` : `Hide ${list.name}`,
                            "aria-pressed": list.hidden ? "false" : "true",
                        }, list.hidden ? null : h(MaterialIcon, { name: "check" })),
                        h("button", {
                            type: "button",
                            className: "task-list-nav-content",
                            onClick: () => setSelectedListId(list.id),
                        },
                            h("span", { className: "task-list-nav-text" },
                                h("span", { className: "task-list-nav-name" }, list.name),
                                list.description ? h("span", { className: "task-list-nav-description" }, list.description) : null
                            ),
                            h("strong", null, incompleteCount)
                        ),
                        menuTrigger({
                            id: list.id,
                            kind: "list",
                            className: "task-list-row-menu",
                            label: `Open options for ${list.name}`,
                            onOpen: openListMenu,
                            stopPropagation: true,
                        })
                    );
                })
            ) : null
        )
    );
}

function taskItemsEqual(previous, next) {
    if (previous.length !== next.length) return false;
    return previous.every((task, index) => task === next[index]);
}

function taskSectionPropsEqual(previous, next) {
    if (previous.list !== next.list || !taskItemsEqual(previous.tasks, next.tasks)) return false;
    if (previous.highlightTaskId !== next.highlightTaskId) return false;
    if (previous.expandedTaskId !== next.expandedTaskId) {
        const affectedIds = new Set([previous.expandedTaskId, next.expandedTaskId]);
        if (previous.tasks.some((task) => affectedIds.has(task.id))) return false;
    }
    return previous.updateList === next.updateList
        && previous.createTask === next.createTask
        && previous.updateTask === next.updateTask
        && previous.deleteTask === next.deleteTask
        && previous.completeTask === next.completeTask
        && previous.setExpandedTaskId === next.setExpandedTaskId
        && previous.reorderTasks === next.reorderTasks
        && previous.openListMenu === next.openListMenu
        && previous.openTaskMenu === next.openTaskMenu
        && previous.onCompletedOpenChange === next.onCompletedOpenChange;
}

export const TaskSection = React.memo(function TaskSection(props) {
    const {
        list,
        tasks,
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
        onCompletedOpenChange,
    } = props;
    const [nameDraft, setNameDraft] = React.useState(list.name);
    const [completedOpen, setCompletedOpen] = React.useState(false);
    const bodyRef = React.useRef(null);
    const taskGroups = React.useMemo(() => splitTasksByCompletion(tasks), [tasks]);
    const incompleteCount = taskGroups.active.length;
    const completedCount = taskGroups.completed.length;

    React.useEffect(() => {
        setNameDraft(list.name);
    }, [list.name]);

    React.useEffect(() => {
        onCompletedOpenChange?.(list.id, !list.collapsed && completedOpen);
        return () => onCompletedOpenChange?.(list.id, false);
    }, [completedOpen, list.collapsed, list.id, onCompletedOpenChange]);

    useSortable(bodyRef, () => ({
            group: { name: "tasks", pull: true, put: true },
            animation: window.APStudyAccessibility?.prefersReducedMotion?.() ? 0 : 150,
            draggable: ".task-row",
            handle: ".task-row-drag",
            ghostClass: "task-sortable-ghost",
            chosenClass: "task-sortable-chosen",
            onEnd: reorderTasks,
        }), [list.id, taskGroups.active.map((task) => task.id).join("|"), reorderTasks]);

    const commitName = () => {
        const next = nameDraft.trim() || "Untitled List";
        if (next !== list.name) updateList(list.id, { name: next });
        setNameDraft(next);
    };

    return h("article", { className: "task-list-section", "data-print-list-id": list.id },
        h("div", { className: "task-list-section-header" },
            iconButton({
                icon: list.collapsed ? "chevron_right" : "expand_more",
                label: list.collapsed ? "Expand list" : "Collapse list",
                className: "task-collapse-button",
                onClick: () => updateList(list.id, { collapsed: !list.collapsed }),
            }),
            h("div", { className: "task-list-heading" },
                h("input", {
                    className: "task-list-title-input",
                    value: nameDraft,
                    onChange: (event) => setNameDraft(event.target.value),
                    onBlur: commitName,
                    onKeyDown: (event) => {
                        if (event.key === "Enter") event.currentTarget.blur();
                    },
                    "aria-label": "List name",
                }),
                list.description ? h("p", { className: "task-list-section-description" }, list.description) : null
            ),
            h("span", { className: "task-list-count" }, `${incompleteCount} left`),
            menuTrigger({
                id: list.id,
                kind: "list",
                className: "task-list-options-button",
                label: "List options",
                onOpen: openListMenu,
            })
        ),
        !list.collapsed && h("div", { ref: bodyRef, className: "task-list-body", "data-task-list-body": "true", "data-list-id": list.id },
            taskGroups.active.length === 0 && completedCount === 0
                ? h("div", { className: "task-list-empty" }, "No tasks in this list yet.")
                : taskGroups.active.map((task) => h(TaskRow, {
                    key: task.id,
                    task,
                    isExpanded: expandedTaskId === task.id,
                    setExpandedTaskId,
                    updateTask,
                    deleteTask,
                    completeTask,
                    highlighted: highlightTaskId && task.id === highlightTaskId,
                    openTaskMenu,
                }))
        ),
        !list.collapsed && h(AddTaskForm, { listId: list.id, createTask }),
        !list.collapsed && completedCount > 0 ? h("section", { className: "task-completed-section" },
            h("button", {
                type: "button",
                className: "task-completed-toggle",
                onClick: () => setCompletedOpen((current) => !current),
                "aria-expanded": completedOpen ? "true" : "false",
            },
                h(MaterialIcon, { name: completedOpen ? "expand_more" : "chevron_right" }),
                h("span", null, "Completed"),
                h("strong", null, completedCount)
            ),
            completedOpen ? h("div", { className: "task-completed-list" },
                taskGroups.completed.map((task) => h(TaskRow, {
                    key: task.id,
                    task,
                    isExpanded: expandedTaskId === task.id,
                    setExpandedTaskId,
                    updateTask,
                    deleteTask,
                    completeTask,
                    highlighted: highlightTaskId && task.id === highlightTaskId,
                    openTaskMenu,
                    draggable: false,
                }))
            ) : null
        ) : null
    );
}, taskSectionPropsEqual);
