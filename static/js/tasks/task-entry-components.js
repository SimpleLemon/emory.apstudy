import * as React from "react";
import { AddTaskPopover } from "./task-popover.js";
import { DeadlinePanel, formatTaskDeadline, reminderLabel } from "./task-deadline.js";
import { TaskListbox } from "./task-form-controls.js";
import { RepeatMenuContent } from "./task-recurrence.js";

import {
    PRIORITY_OPTIONS,
    createDefaultRecurrence,
    formatRepeat,
    isRepeatingTaskCompleted,
} from "./task-utils.js";

const h = React.createElement;

function cx(...parts) {
    return parts.filter(Boolean).join(" ");
}

function MaterialIcon({ name, className = "" }) {
    return h("span", { className: `material-symbols-outlined ${className}`, "aria-hidden": "true" }, name);
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

function menuTrigger({ id, kind, className, label, onOpen, getPosition = menuAnchorFromEvent }) {
    return iconButton({
        icon: "more_vert",
        label,
        className,
        triggerKey: `${kind}:${id}`,
        onClick: (event) => onOpen(id, getPosition(event)),
    });
}

function PriorityBadge({ priority }) {
    if (!priority || priority === "none") return null;
    return h("span", { className: `task-priority task-priority-${priority}` },
        h(MaterialIcon, { name: "flag" }),
        priority
    );
}

function TaskDetails({ task, updateTask }) {
    const [detailPopover, setDetailPopover] = React.useState(null);
    const [repeatDraft, setRepeatDraft] = React.useState(task.recurrence || createDefaultRecurrence());

    React.useEffect(() => {
        if (!detailPopover) setRepeatDraft(task.recurrence || createDefaultRecurrence());
    }, [detailPopover, task.recurrence]);

    const openDetailPopover = (type, event) => {
        event.preventDefault();
        const rect = event.currentTarget.getBoundingClientRect();
        if (type === "repeat") setRepeatDraft(task.recurrence || createDefaultRecurrence());
        setDetailPopover((current) => current?.type === type
            ? null
            : {
                type,
                nonce: Date.now(),
                anchor: {
                    top: rect.top,
                    right: rect.right,
                    bottom: rect.bottom,
                    left: rect.left,
                },
            });
    };

    const closeDetailPopover = () => {
        setRepeatDraft(task.recurrence || createDefaultRecurrence());
        setDetailPopover(null);
    };

    const clearRepeat = () => {
        updateTask(task.id, { recurrence: null });
        setRepeatDraft(createDefaultRecurrence());
        setDetailPopover(null);
    };

    const saveRepeat = () => {
        updateTask(task.id, { recurrence: repeatDraft });
        setDetailPopover(null);
    };

    return h("div", { className: "task-details" },
        h("div", { className: "task-detail-field" },
            h("span", null, "Priority"),
            h(TaskListbox, {
                value: task.priority || "none",
                options: PRIORITY_OPTIONS,
                label: "Task priority",
                onChange: (priority) => updateTask(task.id, { priority }),
            })
        ),
        h("div", { className: "task-detail-field" },
            h("span", null, "Deadline"),
            h("button", {
                type: "button",
                className: cx("task-add-control task-detail-deadline", task.deadline_at && "is-active"),
                onClick: (event) => openDetailPopover("due", event),
                "aria-expanded": detailPopover?.type === "due" ? "true" : "false",
                "data-task-add-popover-trigger": "due-detail",
            },
                h(MaterialIcon, { name: task.deadline_at ? "event_upcoming" : "event" }),
                h("span", null, task.deadline_at ? formatTaskDeadline(task) : "Add deadline")
            )
        ),
        h("div", { className: "task-detail-repeat-field" },
            h("span", null, "Repeat"),
            h("button", {
                type: "button",
                className: cx("task-add-control", task.recurrence && "is-active"),
                onClick: (event) => openDetailPopover("repeat", event),
                "aria-expanded": detailPopover?.type === "repeat" ? "true" : "false",
                "data-task-add-popover-trigger": "repeat-detail",
            },
                h(MaterialIcon, { name: "sync" }),
                h("span", null, task.recurrence ? formatRepeat(task.recurrence) : "Repeat")
            )
        ),
        h(AddTaskPopover, { popover: detailPopover, onClose: closeDetailPopover },
            detailPopover?.type === "due" ? h(DeadlinePanel, {
                value: task,
                onCancel: closeDetailPopover,
                onClear: () => {
                    updateTask(task.id, { deadline_at: null, deadline_time: null, reminder_minutes: -1 });
                    setDetailPopover(null);
                },
                onApply: (payload) => {
                    updateTask(task.id, payload);
                    setDetailPopover(null);
                },
            }) : h(RepeatMenuContent, {
                recurrence: repeatDraft,
                onChange: setRepeatDraft,
                onCancel: closeDetailPopover,
                onClear: clearRepeat,
                onDone: saveRepeat,
            })
        )
    );
}

export const TaskRow = React.memo(function TaskRow({ task, isExpanded, setExpandedTaskId, updateTask, completeTask, highlighted, openTaskMenu, draggable = true }) {
    const [titleDraft, setTitleDraft] = React.useState(task.title);
    const completed = isRepeatingTaskCompleted(task);
    React.useEffect(() => setTitleDraft(task.title), [task.title]);

    const commitTitle = () => {
        const next = titleDraft.trim();
        if (next && next !== task.title) updateTask(task.id, { title: next });
        if (!next) setTitleDraft(task.title);
    };

    return h("div", {
        className: cx("task-row", completed && "is-completed", highlighted && "is-highlighted"),
        "data-task-id": task.id,
    },
        h("div", { className: "task-row-main" },
            draggable ? h("span", { className: "task-row-drag", "aria-hidden": "true" }, h(MaterialIcon, { name: "drag_indicator" })) : h("span", { className: "task-row-drag-spacer", "aria-hidden": "true" }),
            h("button", {
                type: "button",
                className: cx("task-checkbox", completed && "is-checked"),
                onClick: () => completeTask(task, !completed),
                "aria-label": completed ? "Mark task incomplete" : "Complete task",
            }, completed ? h(MaterialIcon, { name: "check" }) : null),
            h("div", { className: "task-title-wrap" },
                h("input", {
                    className: "task-title-input",
                    value: titleDraft,
                    onChange: (event) => setTitleDraft(event.target.value),
                    onBlur: commitTitle,
                    onKeyDown: (event) => {
                        if (event.key === "Enter") event.currentTarget.blur();
                    },
                    "aria-label": "Task title",
                }),
                h("span", { className: "task-title-strike", "aria-hidden": "true" })
            ),
            h(PriorityBadge, { priority: task.priority }),
            task.deadline_at ? h("span", { className: "task-row-meta" },
                h(MaterialIcon, { name: task.deadline_time ? "schedule" : "calendar_today" }),
                formatTaskDeadline(task),
                Number(task.reminder_minutes) !== -1 ? h("span", {
                    className: "material-symbols-outlined task-reminder-icon",
                    title: reminderLabel(task.reminder_minutes, !task.deadline_time),
                    "aria-label": reminderLabel(task.reminder_minutes, !task.deadline_time),
                }, "notifications_active") : null
            ) : null,
            task.recurrence ? h("span", { className: "task-row-meta" }, h(MaterialIcon, { name: "sync" }), formatRepeat(task.recurrence)) : null,
            h("button", {
                type: "button",
                className: cx("task-star-button", task.starred && "is-starred"),
                onClick: () => updateTask(task.id, { starred: !task.starred }),
                "aria-label": task.starred ? "Remove star" : "Star task",
                "aria-pressed": task.starred ? "true" : "false",
                title: task.starred ? "Remove star" : "Star task",
            }, h(MaterialIcon, { name: "star" })),
            menuTrigger({
                id: task.id,
                kind: "task",
                className: "task-row-menu-button",
                label: "Task options",
                onOpen: openTaskMenu,
                getPosition: (event) => ({
                    ...menuAnchorFromEvent(event),
                    configure: () => setExpandedTaskId(isExpanded ? "" : task.id),
                }),
            })
        ),
        isExpanded ? h(TaskDetails, { task, updateTask }) : null
    );
});

export function AddTaskForm({ listId, createTask }) {
    const [focused, setFocused] = React.useState(false);
    const [title, setTitle] = React.useState("");
    const [priority, setPriority] = React.useState("none");
    const [deadline, setDeadline] = React.useState({ deadline_at: null, deadline_time: null, reminder_minutes: -1 });
    const [repeatEnabled, setRepeatEnabled] = React.useState(false);
    const [recurrence, setRecurrence] = React.useState(createDefaultRecurrence);
    const [repeatDraft, setRepeatDraft] = React.useState(createDefaultRecurrence);
    const [popover, setPopover] = React.useState(null);
    const [saving, setSaving] = React.useState(false);
    const expanded = focused || title || deadline.deadline_at || repeatEnabled || priority !== "none" || Boolean(popover);

    const reset = () => {
        setTitle("");
        setPriority("none");
        setDeadline({ deadline_at: null, deadline_time: null, reminder_minutes: -1 });
        setRepeatEnabled(false);
        setRecurrence(createDefaultRecurrence());
        setRepeatDraft(createDefaultRecurrence());
        setPopover(null);
    };

    const openPopover = (type, event) => {
        event.preventDefault();
        const rect = event.currentTarget.getBoundingClientRect();
        if (type === "repeat") {
            setRepeatDraft(repeatEnabled ? recurrence : createDefaultRecurrence());
        }
        setPopover((current) => current?.type === type
            ? null
            : {
                type,
                nonce: Date.now(),
                anchor: {
                    top: rect.top,
                    right: rect.right,
                    bottom: rect.bottom,
                    left: rect.left,
                },
            });
    };

    const controlButton = ({ type, icon, label, active, value }) => h("button", {
        type: "button",
        className: cx("task-add-control", active && "is-active"),
        onClick: (event) => openPopover(type, event),
        "aria-label": label,
        "aria-expanded": popover?.type === type ? "true" : "false",
        "data-task-add-popover-trigger": type,
    },
        h(MaterialIcon, { name: icon }),
        h("span", null, value || label)
    );

    const popoverContent = () => {
        if (!popover) return null;
        if (popover.type === "due") {
            return h(DeadlinePanel, {
                value: deadline,
                onCancel: () => setPopover(null),
                onClear: () => {
                    setDeadline({ deadline_at: null, deadline_time: null, reminder_minutes: -1 });
                    setPopover(null);
                },
                onApply: (payload) => {
                    setDeadline(payload);
                    setPopover(null);
                },
            });
        }
        if (popover.type === "priority") {
            return h("div", { className: "task-add-choice-list", role: "menu" },
                PRIORITY_OPTIONS.map((option) => h("button", {
                    key: option.value,
                    type: "button",
                    className: cx(priority === option.value && "is-selected"),
                    onClick: () => {
                        setPriority(option.value);
                        setPopover(null);
                    },
                    role: "menuitemradio",
                    "aria-checked": priority === option.value ? "true" : "false",
                },
                    h(MaterialIcon, { name: priority === option.value ? "check" : option.value === "none" ? "radio_button_unchecked" : "flag" }),
                    h("span", null, option.label)
                ))
            );
        }
        return h(RepeatMenuContent, {
            recurrence: repeatDraft,
            onChange: setRepeatDraft,
            onCancel: () => {
                setRepeatDraft(recurrence);
                setPopover(null);
            },
            onClear: repeatEnabled ? () => {
                setRepeatEnabled(false);
                setRecurrence(createDefaultRecurrence());
                setRepeatDraft(createDefaultRecurrence());
                setPopover(null);
            } : undefined,
            onDone: () => {
                setRepeatEnabled(true);
                setRecurrence(repeatDraft);
                setPopover(null);
            },
        });
    };

    return h("form", {
        className: `task-add-form ${expanded ? "is-expanded" : ""}`,
        onFocus: () => setFocused(true),
        onBlur: (event) => {
            if (!event.currentTarget.contains(event.relatedTarget)) {
                setFocused(false);
            }
        },
        onSubmit: async (event) => {
            event.preventDefault();
            if (!title.trim()) return;
            setSaving(true);
            try {
                await createTask(listId, {
                    title: title.trim(),
                    priority,
                    ...deadline,
                    recurrence: repeatEnabled ? recurrence : null,
                });
                reset();
            } catch (err) {
                console.warn("Task creation failed; app-level error banner was shown.", err);
            } finally {
                setSaving(false);
            }
        },
    },
        h("div", { className: "task-add-primary" },
            h(MaterialIcon, { name: "add" }),
            h("input", {
                value: title,
                onChange: (event) => setTitle(event.target.value),
                placeholder: "Add a task",
                disabled: saving,
            }),
            h("button", { type: "submit", disabled: saving || !title.trim() }, "Add")
        ),
        expanded ? h("div", { className: "task-add-entrybar" },
            controlButton({
                type: "due",
                icon: deadline.deadline_at ? "event_upcoming" : "event",
                label: "Due date",
                active: Boolean(deadline.deadline_at),
                value: deadline.deadline_at ? formatTaskDeadline(deadline) : "",
            }),
            controlButton({
                type: "priority",
                icon: "flag",
                label: "Priority",
                active: priority !== "none",
                value: priority !== "none" ? PRIORITY_OPTIONS.find((option) => option.value === priority)?.label : "",
            }),
            controlButton({
                type: "repeat",
                icon: "sync",
                label: "Repeat",
                active: repeatEnabled,
                value: repeatEnabled ? formatRepeat(recurrence) : "",
            })
        ) : null,
        h(AddTaskPopover, { popover, onClose: () => setPopover(null) }, popoverContent())
    );
}
