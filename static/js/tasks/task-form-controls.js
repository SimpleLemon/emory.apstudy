import * as React from "react";
import { createPortal } from "react-dom";
import { getFloatingPosition } from "./task-floating.js";

const h = React.createElement;
const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

function cx(...parts) {
    return parts.filter(Boolean).join(" ");
}

function localDateString(date) {
    return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

function parseDate(value) {
    const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(value || ""));
    if (!match) return null;
    const date = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
    return Number.isNaN(date.getTime()) ? null : date;
}

export function calendarCells(monthValue) {
    const month = parseDate(monthValue) || new Date();
    const first = new Date(month.getFullYear(), month.getMonth(), 1);
    const start = new Date(first);
    start.setDate(1 - first.getDay());
    return Array.from({ length: 42 }, (_, index) => {
        const date = new Date(start);
        date.setDate(start.getDate() + index);
        return {
            value: localDateString(date),
            day: date.getDate(),
            inMonth: date.getMonth() === first.getMonth(),
        };
    });
}

export function moveDate(value, days) {
    const date = parseDate(value) || new Date();
    date.setDate(date.getDate() + days);
    return localDateString(date);
}

function monthValueFor(value) {
    const date = parseDate(value) || new Date();
    return localDateString(new Date(date.getFullYear(), date.getMonth(), 1));
}

function monthLabel(value) {
    const date = parseDate(value) || new Date();
    return date.toLocaleDateString(undefined, { month: "long", year: "numeric" });
}

function displayDate(value, placeholder) {
    const date = parseDate(value);
    return date ? date.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" }) : placeholder;
}

export function TaskListbox({ value, options, onChange, label, disabled = false, className = "" }) {
    const [open, setOpen] = React.useState(false);
    const rootRef = React.useRef(null);
    const triggerRef = React.useRef(null);
    const listRef = React.useRef(null);
    const listId = React.useId();
    const selectedIndex = Math.max(0, options.findIndex((option) => option.value === value));
    const selected = options[selectedIndex] || options[0];

    React.useEffect(() => {
        if (!open) return undefined;
        const closeOutside = (event) => {
            if (rootRef.current?.contains(event.target) || listRef.current?.contains(event.target)) return;
            setOpen(false);
        };
        document.addEventListener("pointerdown", closeOutside);
        return () => document.removeEventListener("pointerdown", closeOutside);
    }, [open]);

    const [position, setPosition] = React.useState({ top: 0, left: 0, width: 190, ready: false });

    React.useLayoutEffect(() => {
        if (!open || !triggerRef.current || !listRef.current) return undefined;
        const reposition = () => {
            const triggerRect = triggerRef.current?.getBoundingClientRect();
            if (!triggerRect || !listRef.current) return;
            const width = Math.min(
                Math.max(triggerRect.width, 190),
                Math.max(1, window.innerWidth - 20),
            );
            listRef.current.style.width = `${width}px`;
            const next = getFloatingPosition(triggerRect, listRef.current.getBoundingClientRect(), {
                align: "start",
                gap: 5,
            });
            setPosition({ ...next, width, ready: true });
        };
        reposition();
        window.addEventListener("scroll", reposition, true);
        window.addEventListener("resize", reposition);
        window.visualViewport?.addEventListener("resize", reposition);
        const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(reposition);
        observer?.observe(listRef.current);
        return () => {
            window.removeEventListener("scroll", reposition, true);
            window.removeEventListener("resize", reposition);
            window.visualViewport?.removeEventListener("resize", reposition);
            observer?.disconnect();
        };
    }, [open, selectedIndex, options.length]);

    React.useEffect(() => {
        if (!open) return;
        listRef.current?.querySelector(`[data-option-index="${selectedIndex}"]`)?.focus({ preventScroll: true });
    }, [open, selectedIndex]);

    const choose = (option) => {
        onChange(option.value);
        setOpen(false);
        triggerRef.current?.focus({ preventScroll: true });
    };

    const onListKeyDown = (event) => {
        const buttons = Array.from(listRef.current?.querySelectorAll("[role='option']") || []);
        const index = buttons.indexOf(document.activeElement);
        if (event.key === "Escape") {
            event.preventDefault();
            setOpen(false);
            triggerRef.current?.focus({ preventScroll: true });
        } else if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
            event.preventDefault();
            const next = event.key === "Home" ? 0
                : event.key === "End" ? buttons.length - 1
                    : (index + (event.key === "ArrowDown" ? 1 : -1) + buttons.length) % buttons.length;
            buttons[next]?.focus();
        }
    };

    const menu = open ? h("div", {
        ref: listRef,
        id: listId,
        className: "task-custom-select-menu",
        role: "listbox",
        "aria-label": label,
        "data-task-floating-layer": "listbox",
        "data-task-listbox-menu": "true",
        onKeyDown: onListKeyDown,
        style: {
            top: `${position.ready ? position.top : 0}px`,
            left: `${position.ready ? position.left : 0}px`,
            width: `${position.width}px`,
            visibility: position.ready ? "visible" : "hidden",
        },
    }, options.map((option, index) => h("button", {
        key: option.value,
        type: "button",
        role: "option",
        className: cx(option.value === value && "is-selected"),
        "aria-selected": String(option.value === value),
        "data-option-index": index,
        onClick: () => choose(option),
    },
        h("span", { className: "material-symbols-outlined", "aria-hidden": "true" }, option.value === value ? "check" : option.icon || "radio_button_unchecked"),
        h("span", null, option.label)
    ))) : null;

    return h("div", { ref: rootRef, className: cx("task-custom-select", className, open && "is-open") },
        h("button", {
            ref: triggerRef,
            type: "button",
            className: "task-custom-select-trigger",
            disabled,
            role: "combobox",
            "aria-label": label,
            "aria-haspopup": "listbox",
            "aria-expanded": String(open),
            "aria-controls": listId,
            onClick: () => setOpen((current) => !current),
            onKeyDown: (event) => {
                if (["ArrowDown", "ArrowUp", "Enter", " "].includes(event.key)) {
                    event.preventDefault();
                    setOpen(true);
                }
            },
        },
            h("span", null, selected?.label || "Select"),
            h("span", { className: "material-symbols-outlined", "aria-hidden": "true" }, "expand_more")
        ),
        menu && (document.body ? createPortal(menu, document.body) : menu)
    );
}

export function TaskCalendar({ value, onChange, label = "Choose date" }) {
    const [month, setMonth] = React.useState(() => monthValueFor(value));
    const gridRef = React.useRef(null);
    const pendingFocusRef = React.useRef("");
    const today = localDateString(new Date());
    const cells = calendarCells(month);

    React.useEffect(() => {
        if (value) setMonth(monthValueFor(value));
    }, [value]);

    React.useLayoutEffect(() => {
        if (!pendingFocusRef.current) return;
        gridRef.current?.querySelector(`[data-date="${pendingFocusRef.current}"]`)?.focus({ preventScroll: true });
        pendingFocusRef.current = "";
    }, [month]);

    const focusDate = (nextValue) => {
        pendingFocusRef.current = nextValue;
        const nextMonth = monthValueFor(nextValue);
        if (nextMonth !== month) setMonth(nextMonth);
        else {
            gridRef.current?.querySelector(`[data-date="${nextValue}"]`)?.focus({ preventScroll: true });
            pendingFocusRef.current = "";
        }
    };

    const changeMonth = (offset) => {
        const current = parseDate(month) || new Date();
        const next = new Date(current.getFullYear(), current.getMonth() + offset, 1);
        setMonth(localDateString(next));
    };

    return h("section", { className: "task-calendar", "aria-label": label },
        h("header", { className: "task-calendar-header" },
            h("button", { type: "button", onClick: () => changeMonth(-1), "aria-label": "Previous month" }, h("span", { className: "material-symbols-outlined", "aria-hidden": "true" }, "chevron_left")),
            h("strong", { "aria-live": "polite" }, monthLabel(month)),
            h("button", { type: "button", onClick: () => changeMonth(1), "aria-label": "Next month" }, h("span", { className: "material-symbols-outlined", "aria-hidden": "true" }, "chevron_right"))
        ),
        h("div", { className: "task-calendar-weekdays", "aria-hidden": "true" }, WEEKDAYS.map((day) => h("span", { key: day }, day))),
        h("div", { ref: gridRef, className: "task-calendar-grid", role: "grid", "aria-label": monthLabel(month) }, cells.map((cell) => h("button", {
            key: cell.value,
            type: "button",
            role: "gridcell",
            className: cx(!cell.inMonth && "is-outside", cell.value === today && "is-today", cell.value === value && "is-selected"),
            "aria-label": displayDate(cell.value, ""),
            "aria-selected": String(cell.value === value),
            "data-date": cell.value,
            tabIndex: cell.value === (value || today) || (!cells.some((item) => item.value === (value || today)) && cell.value === cells[0].value) ? 0 : -1,
            onClick: () => onChange(cell.value),
            onKeyDown: (event) => {
                const movement = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7 }[event.key];
                if (movement) {
                    event.preventDefault();
                    focusDate(moveDate(cell.value, movement));
                } else if (event.key === "Home" || event.key === "End") {
                    event.preventDefault();
                    const date = parseDate(cell.value);
                    focusDate(moveDate(cell.value, event.key === "Home" ? -date.getDay() : 6 - date.getDay()));
                } else if (event.key === "PageUp" || event.key === "PageDown") {
                    event.preventDefault();
                    const date = parseDate(cell.value);
                    date.setMonth(date.getMonth() + (event.key === "PageDown" ? 1 : -1));
                    focusDate(localDateString(date));
                }
            },
        }, cell.day))),
        h("button", { type: "button", className: "task-calendar-today", onClick: () => { setMonth(monthValueFor(today)); onChange(today); } }, "Today")
    );
}

export function TaskDatePicker({ value, onChange, label, placeholder = "Choose date", disabled = false }) {
    const [open, setOpen] = React.useState(false);
    const rootRef = React.useRef(null);
    const triggerRef = React.useRef(null);
    const menuRef = React.useRef(null);
    const [position, setPosition] = React.useState({ top: 0, left: 0, width: 318, ready: false });

    React.useEffect(() => {
        if (!open) return undefined;
        const closeOutside = (event) => {
            if (rootRef.current?.contains(event.target) || menuRef.current?.contains(event.target)) return;
            setOpen(false);
        };
        document.addEventListener("pointerdown", closeOutside);
        return () => document.removeEventListener("pointerdown", closeOutside);
    }, [open]);

    React.useLayoutEffect(() => {
        if (!open || !triggerRef.current || !menuRef.current) return undefined;
        const reposition = () => {
            const triggerRect = triggerRef.current?.getBoundingClientRect();
            if (!triggerRect || !menuRef.current) return;
            const width = Math.min(318, Math.max(0, window.innerWidth - 20));
            menuRef.current.style.width = `${width}px`;
            const next = getFloatingPosition(triggerRect, menuRef.current.getBoundingClientRect(), {
                align: "start",
                gap: 5,
            });
            setPosition({ ...next, width, ready: true });
        };
        reposition();
        window.addEventListener("scroll", reposition, true);
        window.addEventListener("resize", reposition);
        window.visualViewport?.addEventListener("resize", reposition);
        const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(reposition);
        observer?.observe(menuRef.current);
        return () => {
            window.removeEventListener("scroll", reposition, true);
            window.removeEventListener("resize", reposition);
            window.visualViewport?.removeEventListener("resize", reposition);
            observer?.disconnect();
        };
    }, [open, value]);

    const menu = open ? h("div", {
        ref: menuRef,
        className: "task-date-picker-menu",
        role: "dialog",
        "aria-label": label,
        "data-task-floating-layer": "date-picker",
        style: {
            top: `${position.ready ? position.top : 0}px`,
            left: `${position.ready ? position.left : 0}px`,
            width: `${position.width}px`,
            visibility: position.ready ? "visible" : "hidden",
        },
    }, h(TaskCalendar, {
        value,
        onChange: (next) => { onChange(next); setOpen(false); },
        label,
    })) : null;

    return h("div", { ref: rootRef, className: cx("task-date-picker", open && "is-open") },
        h("button", {
            type: "button",
            className: "task-date-picker-trigger",
            ref: triggerRef,
            disabled,
            "aria-label": label,
            "aria-haspopup": "dialog",
            "aria-expanded": String(open),
            onClick: () => setOpen((current) => !current),
        },
            h("span", { className: "material-symbols-outlined", "aria-hidden": "true" }, "calendar_today"),
            h("span", null, displayDate(value, placeholder))
        ),
        menu && (document.body ? createPortal(menu, document.body) : menu)
    );
}
