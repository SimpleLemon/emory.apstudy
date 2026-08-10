import * as React from "react";
import { TaskCalendar, TaskListbox } from "./task-form-controls.js";
import { isoToLocalInput, localInputToIso } from "./task-utils.js";

const h = React.createElement;

export const TIMED_REMINDER_OPTIONS = [
    { value: -1, label: "No alert" },
    { value: 0, label: "At deadline" },
    { value: 5, label: "5 minutes before" },
    { value: 10, label: "10 minutes before" },
    { value: 15, label: "15 minutes before" },
    { value: 30, label: "30 minutes before" },
    { value: 60, label: "1 hour before" },
    { value: 120, label: "2 hours before" },
    { value: 1440, label: "1 day before" },
    { value: 2880, label: "2 days before" },
];

export const DATE_ONLY_REMINDER_OPTIONS = [
    { value: -1, label: "No alert" },
    { value: -540, label: "Due date at 9 AM" },
    { value: 900, label: "1 day before at 9 AM" },
    { value: 2340, label: "2 days before at 9 AM" },
    { value: 9540, label: "1 week before at 9 AM" },
];

function optionExists(options, value) {
    return options.some((option) => option.value === Number(value));
}

export function parseDisplayTime(value, period) {
    const match = /^(\d{1,2}):([0-5]\d)$/.exec(String(value || "").trim());
    if (!match) return null;
    const hour = Number(match[1]);
    if (hour < 1 || hour > 12) return null;
    return `${String((hour % 12) + (period === "PM" ? 12 : 0)).padStart(2, "0")}:${match[2]}`;
}

export function displayTimeParts(value) {
    const match = /^(\d{2}):(\d{2})$/.exec(String(value || ""));
    if (!match) return { text: "", period: "PM" };
    const hour = Number(match[1]);
    return {
        text: `${hour % 12 || 12}:${match[2]}`,
        period: hour >= 12 ? "PM" : "AM",
    };
}

export function deadlineDraft(value = {}) {
    const local = isoToLocalInput(value.deadline_at);
    const parts = displayTimeParts(value.deadline_time);
    const hasTime = Boolean(value.deadline_time);
    const options = hasTime ? TIMED_REMINDER_OPTIONS : DATE_ONLY_REMINDER_OPTIONS;
    const requestedReminder = Number(value.reminder_minutes ?? (hasTime ? 10 : -1));
    return {
        date: local ? local.slice(0, 10) : "",
        hasTime,
        timeText: parts.text,
        period: parts.period,
        reminderMinutes: optionExists(options, requestedReminder) ? requestedReminder : (hasTime ? 10 : -1),
    };
}

export function deadlinePayload(draft, timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC") {
    if (!draft?.date) {
        return { deadline_at: null, deadline_time: null, reminder_minutes: -1, timezone };
    }
    const deadlineTime = draft.hasTime ? parseDisplayTime(draft.timeText, draft.period) : null;
    if (draft.hasTime && !deadlineTime) return null;
    const deadlineAt = localInputToIso(`${draft.date}T${deadlineTime || "00:00"}`);
    if (!deadlineAt) return null;
    return {
        deadline_at: deadlineAt,
        deadline_time: deadlineTime,
        reminder_minutes: Number(draft.reminderMinutes),
        timezone,
    };
}

export function reminderLabel(value, isDateOnly = false) {
    const options = isDateOnly ? DATE_ONLY_REMINDER_OPTIONS : TIMED_REMINDER_OPTIONS;
    return options.find((option) => option.value === Number(value))?.label || "No alert";
}

export function formatTaskDeadline(task) {
    if (!task?.deadline_at) return "";
    const date = new Date(task.deadline_at);
    if (Number.isNaN(date.getTime())) return "";
    if (!task.deadline_time) {
        return date.toLocaleDateString(undefined, { month: "short", day: "numeric", year: date.getFullYear() !== new Date().getFullYear() ? "numeric" : undefined });
    }
    return date.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

export function DeadlinePanel({ value, onApply, onCancel, onClear, floatingOwner = "" }) {
    const [draft, setDraft] = React.useState(() => deadlineDraft(value));
    const [error, setError] = React.useState("");
    const [saving, setSaving] = React.useState(false);
    const reminderOptions = draft.hasTime ? TIMED_REMINDER_OPTIONS : DATE_ONLY_REMINDER_OPTIONS;

    const apply = async () => {
        const payload = deadlinePayload(draft);
        if (!draft.date) {
            setError("Choose a deadline date.");
            return;
        }
        if (!payload) {
            setError("Enter a time like 9:00, then choose AM or PM.");
            return;
        }
        setError("");
        setSaving(true);
        try {
            const result = await onApply(payload);
            if (result?.ok === false) setError(result.error || "Unable to update the deadline. Try again.");
        } catch (err) {
            setError(err.message || "Unable to update the deadline. Try again.");
        } finally {
            setSaving(false);
        }
    };

    const clear = async () => {
        setError("");
        setSaving(true);
        try {
            const result = await onClear?.();
            if (result?.ok === false) setError(result.error || "Unable to clear the deadline. Try again.");
        } catch (err) {
            setError(err.message || "Unable to clear the deadline. Try again.");
        } finally {
            setSaving(false);
        }
    };

    const toggleTime = () => {
        setError("");
        setDraft((current) => current.hasTime
            ? { ...current, hasTime: false, timeText: "", period: "PM", reminderMinutes: -1 }
            : { ...current, hasTime: true, timeText: "", period: "PM", reminderMinutes: 10 });
    };

    return h("div", { className: "task-deadline-panel" },
        h("div", { className: "task-deadline-heading" },
            h("div", null, h("strong", null, "Deadline"), h("span", null, "Choose a date, then optionally add a time and alert.")),
            draft.date ? h("button", { type: "button", className: "task-deadline-time-toggle", onClick: toggleTime },
                h("span", { className: "material-symbols-outlined", "aria-hidden": "true" }, draft.hasTime ? "event_busy" : "schedule"),
                draft.hasTime ? "Remove time" : "Add time"
            ) : null
        ),
        h(TaskCalendar, {
            value: draft.date,
            onChange: (date) => { setError(""); setDraft((current) => ({ ...current, date })); },
            label: "Task deadline date",
        }),
        draft.hasTime ? h("div", { className: "task-deadline-time-row" },
            h("label", { className: "task-deadline-time-field" },
                h("span", null, "Time"),
                h("input", {
                    type: "text",
                    inputMode: "numeric",
                    autoComplete: "off",
                    placeholder: "9:00",
                    value: draft.timeText,
                    "aria-invalid": error && !parseDisplayTime(draft.timeText, draft.period) ? "true" : undefined,
                    onChange: (event) => { setError(""); setDraft((current) => ({ ...current, timeText: event.target.value })); },
                })
            ),
            h("div", { className: "task-deadline-period", role: "group", "aria-label": "Time period" }, ["AM", "PM"].map((period) => h("button", {
                key: period,
                type: "button",
                className: draft.period === period ? "is-selected" : "",
                "aria-pressed": String(draft.period === period),
                onClick: () => setDraft((current) => ({ ...current, period })),
            }, period)))
        ) : null,
        draft.date ? h("label", { className: "task-deadline-alert-field" },
            h("span", null, "Alert"),
            h(TaskListbox, {
                value: draft.reminderMinutes,
                options: reminderOptions,
                label: "Deadline alert",
                floatingOwner,
                onChange: (reminderMinutes) => setDraft((current) => ({ ...current, reminderMinutes })),
            })
        ) : null,
        error ? h("p", { className: "task-deadline-error", role: "alert" }, error) : null,
        h("div", { className: "task-add-popover-actions task-deadline-actions" },
            value?.deadline_at ? h("button", { type: "button", className: "task-secondary-button task-deadline-clear", disabled: saving, onClick: () => { void clear(); } }, "Clear") : null,
            h("span", { className: "task-deadline-action-spacer" }),
            h("button", { type: "button", className: "task-secondary-button", onClick: onCancel }, "Cancel"),
            h("button", { type: "button", className: "task-primary-button", disabled: saving, onClick: () => { void apply(); } }, saving ? "Saving…" : "Apply")
        )
    );
}
