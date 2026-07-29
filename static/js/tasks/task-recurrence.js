import * as React from "react";
import { TaskDatePicker, TaskListbox } from "./task-form-controls.js";
import { REPEAT_UNITS, defaultRecurrenceEndDate, todayDateString } from "./task-utils.js";

const h = React.createElement;

function cx(...parts) {
    return parts.filter(Boolean).join(" ");
}

export function RecurrenceFields({ recurrence, onChange, compact = false }) {
    const update = (updates) => {
        const next = { ...recurrence, ...updates };
        if (next.endDate && next.startDate && next.endDate < next.startDate) next.endDate = next.startDate;
        onChange(next);
    };
    const onDate = Boolean(recurrence.endDate);
    const endDate = recurrence.endDate || defaultRecurrenceEndDate();

    return h("div", { className: cx("task-repeat-grid", compact && "task-repeat-grid-compact") },
        h("label", { className: "task-repeat-every-field" },
            h("span", null, "Every"),
            h("input", {
                type: "number",
                min: "1",
                max: "365",
                step: "1",
                value: recurrence.every || 1,
                onChange: (event) => {
                    const value = Number(event.target.value);
                    update({ every: Number.isFinite(value) ? Math.min(365, Math.max(1, value)) : 1 });
                },
                "aria-label": compact ? "Repeat every" : undefined,
            })
        ),
        h("div", { className: "task-repeat-unit-field" },
            h("span", null, "Unit"),
            h(TaskListbox, {
                value: recurrence.unit || "week",
                options: REPEAT_UNITS,
                label: "Repeat unit",
                onChange: (unit) => update({ unit }),
            })
        ),
        h("div", { className: "task-repeat-start-field" },
            h("span", null, "Starts"),
            h(TaskDatePicker, {
                value: recurrence.startDate || todayDateString(),
                label: "Repeat start date",
                onChange: (startDate) => update({ startDate }),
            })
        ),
        h("fieldset", { className: "task-repeat-end-field" },
            h("legend", null, "End"),
            h("div", { className: "task-repeat-end-options", role: "group", "aria-label": "Repeat end" },
                h("button", {
                    type: "button",
                    className: cx("task-repeat-end-option", !onDate && "is-selected"),
                    "aria-pressed": String(!onDate),
                    onClick: () => update({ endDate: null }),
                }, h("span", { className: "material-symbols-outlined", "aria-hidden": "true" }, !onDate ? "check_circle" : "radio_button_unchecked"), "Never"),
                h("button", {
                    type: "button",
                    className: cx("task-repeat-end-option", onDate && "is-selected"),
                    "aria-pressed": String(onDate),
                    onClick: () => update({ endDate }),
                }, h("span", { className: "material-symbols-outlined", "aria-hidden": "true" }, onDate ? "check_circle" : "radio_button_unchecked"), "On a date")
            ),
            onDate ? h(TaskDatePicker, {
                value: endDate,
                label: "Repeat end date",
                onChange: (nextEndDate) => update({ endDate: nextEndDate }),
            }) : null
        )
    );
}

export function RepeatMenuContent({ recurrence, onChange, onCancel, onClear, onDone }) {
    return h("div", { className: "task-add-repeat-popover" },
        h(RecurrenceFields, { recurrence, onChange, compact: true }),
        h("div", { className: "task-add-popover-actions" },
            onClear ? h("button", { type: "button", className: "task-secondary-button task-repeat-clear", onClick: onClear }, "Clear") : null,
            h("button", { type: "button", className: "task-secondary-button", onClick: onCancel }, "Cancel"),
            h("button", { type: "button", className: "task-primary-button", onClick: onDone }, "Done")
        )
    );
}
