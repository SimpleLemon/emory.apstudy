export const DEFAULT_LIST_NAMES = ["School", "Research", "Personal"];
export const PRIORITY_OPTIONS = [
    { value: "none", label: "None" },
    { value: "low", label: "Low" },
    { value: "medium", label: "Medium" },
    { value: "high", label: "High" },
];
export const REPEAT_UNITS = [
    { value: "day", label: "day(s)" },
    { value: "week", label: "week(s)" },
    { value: "month", label: "month(s)" },
    { value: "year", label: "year(s)" },
];
export const DEFAULT_RECURRENCE_UNIT = "week";
export const LIST_SORT_OPTIONS = [
    { value: "default", label: "Default", icon: "drag_indicator" },
    { value: "date", label: "Date", icon: "calendar_today" },
    { value: "deadline", label: "Deadline", icon: "event" },
    { value: "title", label: "Title", icon: "sort_by_alpha" },
];

export function todayDateString() {
    const now = new Date();
    return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
}

function dateString(date) {
    return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

export function defaultRecurrenceEndDate(baseDate = new Date()) {
    const source = baseDate instanceof Date && !Number.isNaN(baseDate.getTime()) ? baseDate : new Date();
    const year = source.getFullYear();
    const targetMonth = source.getMonth() + 1;
    const lastDayOfTargetMonth = new Date(year, targetMonth + 1, 0).getDate();
    const clampedDay = Math.min(source.getDate(), lastDayOfTargetMonth);
    const endDate = new Date(year, targetMonth, clampedDay);
    endDate.setDate(endDate.getDate() - 1);
    return dateString(endDate);
}

export function createDefaultRecurrence() {
    return { every: 1, unit: DEFAULT_RECURRENCE_UNIT, startDate: todayDateString(), endDate: null };
}

export function isoToLocalInput(value) {
    return window.APStudyDate?.isoToLocalInput ? window.APStudyDate.isoToLocalInput(value) : "";
}

export function localInputToIso(value) {
    return window.APStudyDate?.localInputToIso ? window.APStudyDate.localInputToIso(value) : null;
}

export function formatDeadline(value) {
    if (!value) return "";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "";
    return date.toLocaleString(undefined, {
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
    });
}

export function formatRepeat(recurrence) {
    if (!recurrence) return "";
    const every = Number(recurrence.every || 1);
    const unit = recurrence.unit || "day";
    return every === 1 ? `Every ${unit}` : `Every ${every} ${unit}s`;
}

export function normalizeTask(task) {
    return {
        ...task,
        priority: task.priority || "none",
        starred: Boolean(task.starred),
        reminder_minutes: Number(task.reminder_minutes ?? (task.deadline_time ? 10 : -1)),
        completed_occurrences: Array.isArray(task.completed_occurrences) ? task.completed_occurrences : [],
    };
}

export function normalizeList(list) {
    return {
        ...list,
        description: list.description || "",
        hidden: Boolean(list.hidden),
        sort_mode: list.sort_mode || "default",
    };
}

export async function fetchJson(url, options = {}) {
    return window.APStudyHttp.fetchJson(url, {
        ...options,
        pendingLabel: options.pendingLabel || "task-save",
    });
}

export function clearCalendarCache() {
    try {
        localStorage.removeItem("calendarEventsCache");
    } catch (err) {
        console.warn("Failed to clear calendar cache after task change.", err);
    }
}

export function isRepeatingTaskCompleted(task) {
    if (!task?.recurrence) return Boolean(task?.completed);
    const key = task.next_occurrence_key;
    return Boolean(key && task.completed_occurrences?.some((item) => item.occurrence_key === key));
}

export function splitTasksByCompletion(tasks) {
    return (tasks || []).reduce((groups, task) => {
        groups[isRepeatingTaskCompleted(task) ? "completed" : "active"].push(task);
        return groups;
    }, { active: [], completed: [] });
}

export function normalizeTaskPreferences(preferences) {
    return {
        task_sound_enabled: preferences?.task_sound_enabled !== false,
    };
}

export function sortedLists(lists) {
    return [...lists].map(normalizeList).sort((a, b) => (a.order || 0) - (b.order || 0) || (a.name || "").localeCompare(b.name || ""));
}

export function sortedTasks(tasks) {
    return [...tasks].sort((a, b) => (a.order || 0) - (b.order || 0) || (a.title || "").localeCompare(b.title || ""));
}

function timestamp(value, fallback = 0) {
    if (!value) return fallback;
    const time = new Date(value).getTime();
    return Number.isNaN(time) ? fallback : time;
}

function priorityRank(value) {
    return { high: 3, medium: 2, low: 1, none: 0 }[String(value || "none").toLowerCase()] || 0;
}

export function sortTasksForList(tasks, sortMode = "default") {
    if (sortMode === "date") {
        return [...tasks].sort((a, b) => timestamp(b.created_at) - timestamp(a.created_at) || (a.order || 0) - (b.order || 0));
    }
    if (sortMode === "deadline") {
        const noDeadline = Number.MAX_SAFE_INTEGER;
        return [...tasks].sort((a, b) => (
            timestamp(a.deadline_at, noDeadline) - timestamp(b.deadline_at, noDeadline)
            || priorityRank(b.priority) - priorityRank(a.priority)
            || (a.order || 0) - (b.order || 0)
            || (a.title || "").localeCompare(b.title || "")
        ));
    }
    if (sortMode === "title") {
        return [...tasks].sort((a, b) => (a.title || "").localeCompare(b.title || "") || (a.order || 0) - (b.order || 0));
    }
    return sortedTasks(tasks);
}
