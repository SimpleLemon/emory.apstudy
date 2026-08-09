(function () {
    const { escapeHtml } = window.APStudyUIPrimitives;
    const TASK_CALENDAR_ID = "local:tasks";

    function formatDateKey(date) {
        return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
    }

    function formatMonthGridDayLabel(date) {
        if (date.getDate() !== 1) return String(date.getDate());
        return date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
    }

    function dateToDayIndex(date, weekDays) {
        const dayStart = new Date(date.getFullYear(), date.getMonth(), date.getDate());
        const weekStartDay = new Date(weekDays[0].getFullYear(), weekDays[0].getMonth(), weekDays[0].getDate());
        const diffMs = dayStart - weekStartDay;
        return Math.round(diffMs / (1000 * 60 * 60 * 24));
    }

    function layoutTimedEvents(events) {
        const items = events.map((event, originalIndex) => {
            const startMin = event.startDate.getHours() * 60 + event.startDate.getMinutes();
            const endDate = event.endDate || event.startDate;
            const durMin = Math.max(30, Math.ceil((endDate - event.startDate) / 60000));
            return {
                ...event,
                layoutOriginalIndex: originalIndex,
                layoutStartMinutes: startMin,
                layoutDurationMinutes: durMin,
                layoutLane: 0,
                layoutLaneCount: 1,
            };
        }).sort((a, b) => (
            a.layoutStartMinutes - b.layoutStartMinutes
            || b.layoutDurationMinutes - a.layoutDurationMinutes
            || a.layoutOriginalIndex - b.layoutOriginalIndex
        ));

        function assignGroupLanes(group) {
            const laneEnds = [];
            for (const item of group) {
                let lane = laneEnds.findIndex((end) => end <= item.layoutStartMinutes);
                if (lane < 0) {
                    lane = laneEnds.length;
                    laneEnds.push(0);
                }
                laneEnds[lane] = item.layoutStartMinutes + item.layoutDurationMinutes;
                item.layoutLane = lane;
            }
            const laneCount = Math.max(1, laneEnds.length);
            group.forEach((item) => { item.layoutLaneCount = laneCount; });
        }

        let groupStart = 0;
        let groupEnd = -Infinity;
        for (let index = 0; index < items.length; index++) {
            const item = items[index];
            if (index > groupStart && item.layoutStartMinutes >= groupEnd) {
                assignGroupLanes(items.slice(groupStart, index));
                groupStart = index;
                groupEnd = -Infinity;
            }
            groupEnd = Math.max(groupEnd, item.layoutStartMinutes + item.layoutDurationMinutes);
        }
        if (items.length) assignGroupLanes(items.slice(groupStart));

        return items;
    }

    function formatHourLabel(hour) {
        if (hour === 0) return "12 AM";
        if (hour < 12) return `${hour} AM`;
        if (hour === 12) return "12 PM";
        return `${hour - 12} PM`;
    }

    function formatTimeOnly(date) {
        return date.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
    }

    function formatDateTime(value) {
        return value.toLocaleDateString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
    }

    function formatTimedEventRange(event) {
        const start = event.startDate;
        const end = event.endDate || event.startDate;
        const sameDay =
            start.getFullYear() === end.getFullYear()
            && start.getMonth() === end.getMonth()
            && start.getDate() === end.getDate();
        if (sameDay) {
            const dateLabel = start.toLocaleDateString(undefined, { month: "short", day: "numeric" });
            return `${dateLabel}, ${formatTimeOnly(start)} - ${formatTimeOnly(end)}`;
        }
        return `${formatDateTime(start)} - ${formatDateTime(end)}`;
    }

    function formatAllDayRange(event) {
        const startStr = event.startDate.toLocaleDateString(undefined, { month: "short", day: "numeric" });
        if (!event.isMultiDay || event.spanDays <= 1) {
            return `${startStr} (All day)`;
        }
        const displayEnd = new Date(event.endDate);
        displayEnd.setDate(displayEnd.getDate() - 1);
        const endStr = displayEnd.toLocaleDateString(undefined, { month: "short", day: "numeric" });
        return `${startStr} - ${endStr} (All day)`;
    }

    function getStartOfWeek(date) {
        const day = new Date(date);
        day.setHours(0, 0, 0, 0);
        day.setDate(day.getDate() - day.getDay());
        return day;
    }

    function isToday(date) {
        const now = new Date();
        return date.getFullYear() === now.getFullYear() && date.getMonth() === now.getMonth() && date.getDate() === now.getDate();
    }

    function localCalendarDayNumber(date) {
        return Math.floor(Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()) / 86400000);
    }

    function calendarDayDifference(date, referenceDate) {
        return localCalendarDayNumber(date) - localCalendarDayNumber(referenceDate);
    }

    function futureUrgencyLabel(startDate, now) {
        const diffDays = calendarDayDifference(startDate, now);
        if (diffDays === 1) return "Tomorrow";
        if (diffDays < 7) return `In ${diffDays} days`;
        return startDate.toLocaleDateString(undefined, { month: "short", day: "numeric" });
    }

    function getUrgencyLabel(startDate, now = new Date(), endDate = null) {
        const startDiff = calendarDayDifference(startDate, now);
        if (startDiff === 0) return "Today";
        if (startDiff > 0) return futureUrgencyLabel(startDate, now);
        if (endDate && calendarDayDifference(endDate, now) >= 0) return "Today";
        return "Past";
    }

    function getUrgencyLabelAllDay(event, now = new Date()) {
        const startDate = event.startDate;
        const validExclusiveEnd = event.endDate instanceof Date
            && !Number.isNaN(event.endDate.getTime())
            && event.endDate > startDate
            ? event.endDate
            : new Date(startDate.getFullYear(), startDate.getMonth(), startDate.getDate() + 1);
        const lastDay = new Date(
            validExclusiveEnd.getFullYear(),
            validExclusiveEnd.getMonth(),
            validExclusiveEnd.getDate() - 1,
        );
        if (calendarDayDifference(lastDay, now) < 0) return "Past";
        if (calendarDayDifference(startDate, now) <= 0) return "Today";
        return futureUrgencyLabel(startDate, now);
    }

    function getAccent(type, startDate) {
        const soon = (startDate - new Date()) / (1000 * 60 * 60 * 24) <= 1;
        if (soon) return { bar: "bg-error", tag: "bg-error-container/20 text-error" };
        if (type === "task") return { bar: "bg-secondary", tag: "bg-secondary-container/30 text-secondary" };
        if (type === "quiz") return { bar: "bg-tertiary", tag: "bg-tertiary-container/30 text-tertiary" };
        return { bar: "bg-primary", tag: "bg-primary/20 text-primary-fixed" };
    }

    function getEventBadgeClass(type, event) {
        if (event) {
            return "border-2";
        }
        if (type === "quiz") return "bg-tertiary-container/20 text-tertiary border-tertiary/30";
        if (type === "assignment") return "bg-primary/10 text-primary-fixed border-primary/30";
        return "bg-secondary-container/25 text-on-secondary-container border-secondary-container/30";
    }

    function isTaskEvent(event) {
        return event?.source_type === "task" || event?.type === "task" || event?.calendar_id === TASK_CALENDAR_ID;
    }

    function getTaskPriorityColor(priority) {
        const value = String(priority || "none").toLowerCase();
        if (value === "high") return "#ef4444";
        if (value === "medium") return "#f59e0b";
        if (value === "low") return "#22c55e";
        return "#0ea5e9";
    }

    function adjustHexLuminance(hex, amount) {
        const rgb = hexToRgb(hex);
        if (!rgb) return hex;
        const channel = (value) => {
            if (amount >= 0) {
                return Math.round(value + (255 - value) * amount);
            }
            return Math.round(value * (1 + amount));
        };
        return rgbToHex(channel(rgb.r), channel(rgb.g), channel(rgb.b));
    }

    function rgbToHex(r, g, b) {
        const toHex = (value) => {
            const hex = Math.max(0, Math.min(255, Math.round(value))).toString(16).padStart(2, "0");
            return hex;
        };
        return `#${toHex(r)}${toHex(g)}${toHex(b)}`;
    }

    function parseCssColor(value) {
        const input = String(value || "").trim();
        const shortHex = input.match(/^#([0-9a-fA-F]{3})$/);
        if (shortHex) {
            return {
                r: parseInt(shortHex[1][0].repeat(2), 16),
                g: parseInt(shortHex[1][1].repeat(2), 16),
                b: parseInt(shortHex[1][2].repeat(2), 16),
            };
        }
        const normalized = input.replace(/^#/, "");
        if (/^[0-9a-fA-F]{6}$/.test(normalized)) {
            return {
                r: parseInt(normalized.slice(0, 2), 16),
                g: parseInt(normalized.slice(2, 4), 16),
                b: parseInt(normalized.slice(4, 6), 16),
            };
        }
        const rgb = input.match(/^rgba?\(\s*(\d+(?:\.\d+)?)\s*[, ]\s*(\d+(?:\.\d+)?)\s*[, ]\s*(\d+(?:\.\d+)?)/i);
        if (!rgb) return null;
        return {
            r: Math.max(0, Math.min(255, Number(rgb[1]))),
            g: Math.max(0, Math.min(255, Number(rgb[2]))),
            b: Math.max(0, Math.min(255, Number(rgb[3]))),
        };
    }

    function hexToRgb(hex) {
        return parseCssColor(hex);
    }

    function relativeLuminance(color) {
        const rgb = typeof color === "string" ? parseCssColor(color) : color;
        if (!rgb) return 0;
        const channel = (value) => {
            const normalized = value / 255;
            return normalized <= 0.04045
                ? normalized / 12.92
                : ((normalized + 0.055) / 1.055) ** 2.4;
        };
        return 0.2126 * channel(rgb.r) + 0.7152 * channel(rgb.g) + 0.0722 * channel(rgb.b);
    }

    function contrastRatio(first, second) {
        const firstLuminance = relativeLuminance(first);
        const secondLuminance = relativeLuminance(second);
        const lighter = Math.max(firstLuminance, secondLuminance);
        const darker = Math.min(firstLuminance, secondLuminance);
        return (lighter + 0.05) / (darker + 0.05);
    }

    function mixColors(foreground, background, foregroundAmount) {
        const front = parseCssColor(foreground) || parseCssColor("#6366f1");
        const back = parseCssColor(background) || parseCssColor("#ffffff");
        const amount = Math.max(0, Math.min(1, foregroundAmount));
        return rgbToHex(
            front.r * amount + back.r * (1 - amount),
            front.g * amount + back.g * (1 - amount),
            front.b * amount + back.b * (1 - amount),
        );
    }

    function bestTextColor(background, preferred) {
        const candidates = [preferred, "#000000", "#ffffff"].filter((color, index, all) => (
            parseCssColor(color) && all.indexOf(color) === index
        ));
        const passing = candidates.find((color) => contrastRatio(color, background) >= 4.5);
        if (passing) return rgbToHex(...Object.values(parseCssColor(passing)));
        return candidates.sort((a, b) => contrastRatio(b, background) - contrastRatio(a, background))[0];
    }

    function accessibleAccent(accent, backgrounds, minimumRatio = 3) {
        const validBackgrounds = backgrounds.filter((color) => parseCssColor(color));
        const base = parseCssColor(accent) ? rgbToHex(...Object.values(parseCssColor(accent))) : "#6366f1";
        const meetsContrast = (color) => validBackgrounds.every((background) => (
            contrastRatio(color, background) >= minimumRatio
        ));
        if (meetsContrast(base)) return base;
        for (let step = 1; step <= 20; step++) {
            const amount = step / 20;
            const darker = mixColors("#000000", base, amount);
            const lighter = mixColors("#ffffff", base, amount);
            if (meetsContrast(darker)) return darker;
            if (meetsContrast(lighter)) return lighter;
        }
        return contrastRatio("#000000", validBackgrounds[0]) >= contrastRatio("#ffffff", validBackgrounds[0])
            ? "#000000"
            : "#ffffff";
    }

    function createAccessibleEventPalette(accent, surface, onSurface, { completed = false } = {}) {
        const resolvedSurface = parseCssColor(surface) ? surface : "#f0eeeb";
        const darkSurface = relativeLuminance(resolvedSurface) < 0.18;
        const resolvedAccent = completed
            ? darkSurface ? "#94a3b8" : "#64748b"
            : accent;
        const background = mixColors(resolvedAccent, resolvedSurface, darkSurface ? 0.28 : 0.14);
        const text = bestTextColor(background, onSurface);
        const indicator = accessibleAccent(resolvedAccent, [background, resolvedSurface], 3);
        return {
            background,
            text,
            border: indicator,
            indicator,
        };
    }

    function getCssColorVariable(name, fallback) {
        const value = typeof getComputedStyle === "function"
            ? getComputedStyle(document.documentElement).getPropertyValue(name).trim()
            : "";
        return parseCssColor(value) ? value : fallback;
    }

    function hexToRgba(hex, alpha) {
        const rgb = hexToRgb(hex);
        if (!rgb) return `rgba(99, 102, 241, ${alpha})`;
        return `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, ${alpha})`;
    }

    function formatMultilineText(text) {
        return escapeHtml(text).replace(/\r\n|\r|\n/g, "<br>");
    }

    window.APStudyCalendarUtils = {
        formatDateKey,
        formatMonthGridDayLabel,
        dateToDayIndex,
        layoutTimedEvents,
        formatHourLabel,
        formatTimeOnly,
        formatDateTime,
        formatTimedEventRange,
        formatAllDayRange,
        calendarDayDifference,
        getStartOfWeek,
        isToday,
        getUrgencyLabel,
        getUrgencyLabelAllDay,
        getAccent,
        getEventBadgeClass,
        isTaskEvent,
        getTaskPriorityColor,
        adjustHexLuminance,
        contrastRatio,
        createAccessibleEventPalette,
        getCssColorVariable,
        hexToRgb,
        hexToRgba,
        mixColors,
        parseCssColor,
        relativeLuminance,
        escapeHtml,
        formatMultilineText,
    };
}());
