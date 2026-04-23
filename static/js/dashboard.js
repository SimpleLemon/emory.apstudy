/* ──────────────────────────────────────────────────────────────────────────────
   Dashboard Calendar & Assignments
   ──────────────────────────────────────────────────────────────────────────── */

/* ── Constants ─────────────────────────────────────────────────────────────── */
const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MINUTES_PER_DAY = 1440;
const HOUR_HEIGHT_PX = 60;
const WEEK_MINIMUM_DAY_WIDTH_PX = 100;
const ALL_DAY_MIN_HEIGHT_PX = 44;

/* ── Application State ─────────────────────────────────────────────────────── */
const state = {
    events: [],
    feedConfigured: false,
    view: "week",
    anchorDate: new Date(),
};

/* ── Bootstrap ─────────────────────────────────────────────────────────────── */
document.addEventListener("DOMContentLoaded", () => {
    wireControls();
    loadCalendarData();
});

/* ── Data Loading ──────────────────────────────────────────────────────────── */
async function loadCalendarData() {
    try {
        const statusRes = await fetch("/api/calendar/status", { credentials: "same-origin" });
        if (!statusRes.ok) throw new Error("Unable to fetch calendar status");

        const status = await statusRes.json();
        state.feedConfigured = Boolean(status.feed_configured);

        if (!state.feedConfigured) {
            state.events = [];
            render();
            return;
        }

        await refreshCalendarFeed();

        const eventsRes = await fetch("/api/calendar/events", { credentials: "same-origin" });
        if (!eventsRes.ok) throw new Error("Unable to fetch calendar events");

        const payload = await eventsRes.json();
        state.events = Array.isArray(payload.events)
            ? payload.events
                    .filter((e) => e.start)
                    .map((e) => ({
                        ...e,
                        startDate: new Date(e.start),
                        endDate: e.end ? new Date(e.end) : new Date(e.start),
                        isAllDay: Boolean(e.is_all_day),
                        isMultiDay: Boolean(e.is_multi_day),
                        spanDays: e.span_days || 1,
                    }))
                    .sort((a, b) => a.startDate - b.startDate)
            : [];

        render();
    } catch (err) {
        state.feedConfigured = false;
        state.events = [];
        render();
        console.error(err);
    }
}

async function refreshCalendarFeed() {
    try {
        await fetch("/api/calendar/refresh", { method: "POST", credentials: "same-origin" });
    } catch (_) {
        /* silent */
    }
}

/* ── Controls ──────────────────────────────────────────────────────────────── */
function wireControls() {
    document.getElementById("calendar-view-week")?.addEventListener("click", () => {
        state.view = "week";
        render();
    });
    document.getElementById("calendar-view-month")?.addEventListener("click", () => {
        state.view = "month";
        render();
    });
    document.getElementById("calendar-prev")?.addEventListener("click", () => {
        state.anchorDate = shiftAnchorDate(-1);
        render();
    });
    document.getElementById("calendar-next")?.addEventListener("click", () => {
        state.anchorDate = shiftAnchorDate(1);
        render();
    });
    document.getElementById("calendar-today")?.addEventListener("click", () => {
        state.anchorDate = new Date();
        render();
    });
}

function shiftAnchorDate(delta) {
    const next = new Date(state.anchorDate);
    if (state.view === "month") {
        next.setMonth(next.getMonth() + delta);
    } else {
        next.setDate(next.getDate() + delta * 7);
    }
    return next;
}

/* ── Top-Level Render ──────────────────────────────────────────────────────── */
function render() {
    updateHeader();
    updateViewToggleButtons();
    renderCalendarView();
    renderAssignments();
}

function updateHeader() {
    const title = document.getElementById("calendar-title");
    const subtitle = document.getElementById("calendar-subtitle");
    if (!title || !subtitle) return;

    const monthLabel = state.anchorDate.toLocaleDateString(undefined, { month: "long", year: "numeric" });

    if (state.view === "month") {
        title.textContent = monthLabel;
        subtitle.textContent = state.feedConfigured
            ? "Monthly view from your saved iCal feed"
            : "Monthly view (no .ics feed connected yet)";
        return;
    }

    const start = getStartOfWeek(state.anchorDate);
    const end = new Date(start);
    end.setDate(end.getDate() + 6);

    title.textContent = `Week of ${start.toLocaleDateString(undefined, { month: "short", day: "numeric" })} - ${end.toLocaleDateString(undefined, { month: "short", day: "numeric" })}`;
    subtitle.textContent = state.feedConfigured
        ? `Weekly view (${monthLabel}) from your saved iCal feed`
        : `Weekly view (${monthLabel}) with no .ics feed connected`;
}

function updateViewToggleButtons() {
    const weekBtn = document.getElementById("calendar-view-week");
    const monthBtn = document.getElementById("calendar-view-month");
    if (!weekBtn || !monthBtn) return;

    const active = "px-3 py-1.5 text-sm rounded-md bg-primary text-on-primary font-medium";
    const inactive = "px-3 py-1.5 text-sm rounded-md text-on-surface-variant hover:text-on-surface";

    weekBtn.className = state.view === "week" ? active : inactive;
    monthBtn.className = state.view === "month" ? active : inactive;
}

function renderCalendarView() {
    const root = document.getElementById("calendar-view-root");
    if (!root) return;
    root.innerHTML = state.view === "month" ? buildMonthViewHtml() : buildWeekViewHtml();
}

/* ── Month View ────────────────────────────────────────────────────────────── */
function buildMonthViewHtml() {
    const year = state.anchorDate.getFullYear();
    const month = state.anchorDate.getMonth();
    const monthStart = new Date(year, month, 1);
    const monthEnd = new Date(year, month + 1, 0);
    const gridStart = new Date(monthStart);
    gridStart.setDate(monthStart.getDate() - monthStart.getDay());

    const cells = [];
    for (let i = 0; i < 42; i++) {
        const day = new Date(gridStart);
        day.setDate(gridStart.getDate() + i);
        const inMonth = day >= monthStart && day <= monthEnd;
        const dayEvents = getEventsForDay(day);
        const isCurrentDay = isToday(day);
        const todayDateClass = isCurrentDay ? "text-red-500" : "text-on-surface-variant";
        const todayDateOutline = isCurrentDay
            ? '<span class="absolute inset-0 rounded-full border border-red-500"></span>'
            : "";

        cells.push(`
            <div class="rounded-lg border ${inMonth ? "border-outline-variant/10 bg-surface-container" : "border-outline-variant/5 bg-surface-container/40 opacity-70"} p-2.5 min-h-[98px] flex flex-col gap-1.5">
                <div class="relative inline-flex h-7 w-7 items-center justify-center text-xs font-semibold ${todayDateClass}">
                    ${todayDateOutline}
                    <span class="relative z-10">${day.getDate()}</span>
                </div>
                <div class="space-y-1">
                    ${dayEvents.slice(0, 3).map((e) => buildEventChip(e)).join("")}
                    ${dayEvents.length > 3 ? `<div class="text-[10px] text-on-surface-variant">+${dayEvents.length - 3} more</div>` : ""}
                </div>
            </div>
        `);
    }

    return `
        <div class="grid grid-cols-7 gap-2 mb-2">
            ${WEEKDAYS.map((d) => `<div class="text-[11px] uppercase tracking-[0.05em] text-center text-on-surface-variant font-semibold">${d}</div>`).join("")}
        </div>
        <div class="grid grid-cols-7 gap-2" style="min-height:680px;">
            ${cells.join("")}
        </div>
    `;
}

/* ── Week View ─────────────────────────────────────────────────────────────── */
function buildWeekViewHtml() {
    const weekStart = getStartOfWeek(state.anchorDate);
    const days = Array.from({ length: 7 }, (_, i) => {
        const d = new Date(weekStart);
        d.setDate(weekStart.getDate() + i);
        return d;
    });

    const weekEnd = new Date(days[6]);
    weekEnd.setHours(23, 59, 59, 999);

    // Collect all-day / multi-day events for the week, deduplicated
    const allDayEvents = getAllDayEventsForWeek(days, weekStart, weekEnd);

    // Timed events per day
    const timedByDay = days.map((d) => getEventsForDay(d).filter((e) => !e.isAllDay));

    const totalHours = 24;
    const timeGridHeight = totalHours * HOUR_HEIGHT_PX;
    const dayColTemplate = `minmax(${WEEK_MINIMUM_DAY_WIDTH_PX}px, 1fr)`;
    const gridCols = `4.75rem repeat(7, ${dayColTemplate})`;

    /* ── Day-of-week header row ── */
    const dayHeaders = days.map((day, i) => {
        const dateLabel = `${day.getMonth() + 1}/${day.getDate()}`;
        const isCurrentDay = isToday(day);
        const todayHeaderClass = isCurrentDay ? "border border-red-500 text-red-500" : "";
        const todayClass = isCurrentDay ? "text-red-500" : "text-on-surface";
        return `
            <div class="px-3 py-3 text-center border-r border-calendar-rule last:border-r-0 flex items-center justify-center">
                <div class="inline-flex min-h-[3rem] min-w-[4.75rem] flex-col items-center justify-center rounded-full px-3 py-1 ${todayHeaderClass}">
                    <div class="text-[11px] uppercase tracking-[0.05em] font-semibold ${isCurrentDay ? "text-red-500" : "text-on-surface-variant"}">${WEEKDAYS[i]}</div>
                    <div class="text-sm font-semibold ${todayClass}">${dateLabel}</div>
                </div>
            </div>
        `;
    }).join("");

    /* ── All-day row with spanning chips ── */
    const allDayRowHeight = Math.max(ALL_DAY_MIN_HEIGHT_PX, allDayEvents.length * 28 + 16);
    const allDayChips = allDayEvents.map((ev) => {
        const badgeClass = getEventBadgeClass(ev.type);
        // grid-column is 1-indexed; column 1 is the time-axis label, columns 2-8 are days
        const colStart = ev.gridColStart + 2; // +2 because col 1 is the "All day" label
        const colEnd = colStart + ev.gridSpan;
        const details = [ev.title || "Untitled", formatAllDayRange(ev), ev.description || "No description"].join("\n");
        return `
            <div class="group relative" style="grid-column: ${colStart} / ${colEnd};">
                <div class="${badgeClass} text-[10px] px-2 py-1 rounded-md border truncate" title="${escapeHtml(details)}">
                    ${escapeHtml(ev.title || "Untitled")}
                </div>
                <div class="hidden group-hover:block absolute left-0 top-full mt-1 z-40 w-64 rounded-lg border border-outline-variant/30 bg-surface p-2.5 shadow-xl shadow-black/20">
                    <div class="text-xs font-semibold text-on-surface mb-1">${escapeHtml(ev.title || "Untitled")}</div>
                    <div class="text-[11px] text-on-surface-variant">${escapeHtml(formatAllDayRange(ev))}</div>
                    ${ev.course ? `<div class="text-[10px] text-on-surface-variant mt-1">${escapeHtml(ev.course)}</div>` : ""}
                    ${ev.description ? `<div class="text-[11px] text-on-surface-variant mt-1.5 leading-relaxed">${escapeHtml(ev.description)}</div>` : ""}
                </div>
            </div>
        `;
    }).join("");

    /* ── Time axis ── */
    const timeAxisCells = [];
    for (let h = 0; h < 24; h++) {
        timeAxisCells.push(`
            <div class="border-b border-calendar-rule px-2 flex items-start justify-end" style="height:${HOUR_HEIGHT_PX}px;">
                <span class="text-[11px] text-on-surface-variant font-medium leading-none -translate-y-[0.35rem]">${formatHourLabel(h)}</span>
            </div>
        `);
    }

    /* ── Day columns ── */
    const dayColumns = days.map((day, idx) => {
        const positioned = layoutTimedEvents(timedByDay[idx]);
        const todayBg = isToday(day) ? "bg-primary/[0.03]" : "";

        const gridLines = [];
        for (let h = 0; h < 24; h++) {
            gridLines.push(`<div class="border-b border-calendar-rule" style="height:${HOUR_HEIGHT_PX}px;"></div>`);
        }

        const eventChips = positioned.map((e) => renderTimedEvent(e)).join("");

        return `
            <div class="relative border-r border-calendar-rule last:border-r-0 ${todayBg} overflow-hidden">
                <div class="absolute inset-0 pointer-events-none">
                    ${gridLines.join("")}
                </div>
                <div class="relative" style="height:${timeGridHeight}px;">
                    ${eventChips}
                </div>
            </div>
        `;
    }).join("");

    return `
        <div class="rounded-2xl border border-calendar-rule bg-surface-container shadow-2xl shadow-black/10 overflow-hidden">
            <!-- Day-of-week headers (sticky) -->
            <div class="grid border-b border-calendar-rule bg-surface-container sticky top-0 z-30" style="grid-template-columns: ${gridCols};">
                <div class="px-3 py-3 border-r border-calendar-rule"></div>
                ${dayHeaders}
            </div>

            <!-- All-day events (sticky, grid-based spanning) -->
            <div class="grid border-b border-calendar-rule bg-surface-container sticky top-[3.75rem] z-20 items-start gap-y-1 p-1" style="grid-template-columns: ${gridCols}; min-height: ${allDayRowHeight}px;">
                <div class="px-2 py-2 text-[10px] uppercase tracking-[0.08em] text-on-surface-variant font-semibold border-r border-calendar-rule flex items-start justify-end" style="grid-row: 1 / span ${Math.max(1, allDayEvents.length)};">All day</div>
                ${allDayChips || '<div class="col-span-7 text-[11px] text-on-surface-variant/60 py-1 px-2">&nbsp;</div>'}
            </div>

            <!-- Scrollable time grid -->
            <div class="overflow-y-auto overflow-x-auto" style="max-height:760px;">
                <div class="grid" style="grid-template-columns: ${gridCols}; min-width: calc(4.75rem + 7 * ${WEEK_MINIMUM_DAY_WIDTH_PX}px);">
                    <div class="border-r border-calendar-rule bg-surface-container">
                        ${timeAxisCells.join("")}
                    </div>
                    ${dayColumns}
                </div>
            </div>
        </div>
    `;
}

/* ── All-Day Event Collection for Week View ────────────────────────────────── */
function getAllDayEventsForWeek(days, weekStart, weekEnd) {
    // Collect unique all-day events that overlap this week
    const seen = new Set();
    const results = [];

    for (const event of state.events) {
        if (!event.isAllDay) continue;

        const eStart = event.startDate;
        const eEnd = event.endDate || event.startDate;

        // Check overlap with week
        if (eStart > weekEnd || eEnd < weekStart) continue;

        // Deduplicate by uid or title+start
        const key = event.uid || `${event.title}|${eStart.getTime()}`;
        if (seen.has(key)) continue;
        seen.add(key);

        // Calculate grid column start and span relative to the 7-day week
        // Column 0 = Sunday (days[0]), Column 6 = Saturday (days[6])
        const eventStartDay = dateToDayIndex(eStart, days);
        const eventEndDay = dateToDayIndex(new Date(eEnd.getTime() - 1), days); // -1ms because iCal DTEND for DATE is exclusive

        const clampedStart = Math.max(0, eventStartDay);
        const clampedEnd = Math.min(6, eventEndDay);
        const span = clampedEnd - clampedStart + 1;

        if (span <= 0) continue;

        results.push({
            ...event,
            gridColStart: clampedStart,
            gridSpan: span,
        });
    }

    // Sort: wider spans first, then alphabetical
    results.sort((a, b) => b.gridSpan - a.gridSpan || (a.title || "").localeCompare(b.title || ""));
    return results;
}

function dateToDayIndex(date, weekDays) {
    // Returns 0-6 index of which day column this date falls in,
    // or negative / >6 if outside the week
    const dayStart = new Date(date.getFullYear(), date.getMonth(), date.getDate());
    const weekStart = new Date(weekDays[0].getFullYear(), weekDays[0].getMonth(), weekDays[0].getDate());
    const diffMs = dayStart - weekStart;
    return Math.floor(diffMs / (1000 * 60 * 60 * 24));
}

/* ── Timed Event Rendering (week view) ─────────────────────────────────────── */
function renderTimedEvent(event) {
    const topPx = (event.layoutStartMinutes / 60) * HOUR_HEIGHT_PX;
    const heightPx = Math.max((event.layoutDurationMinutes / 60) * HOUR_HEIGHT_PX, 22);
    const leftPct = (event.layoutLane / event.layoutLaneCount) * 100;
    const widthPct = 100 / event.layoutLaneCount;
    const badgeClass = getEventBadgeClass(event.type);
    const course = event.course ? `<div class="text-[10px] text-on-surface-variant mt-1">${escapeHtml(event.course)}</div>` : "";
    const details = [event.title || "Untitled", formatTimeOnly(event.startDate), event.description || "No description"].join("\n");

    return `
        <div class="group absolute px-0.5" style="top:${topPx}px; left:${leftPct}%; width:calc(${widthPct}% - 0.25rem); height:${heightPx}px; z-index: 10;">
            <div class="h-full rounded-lg border ${badgeClass} overflow-hidden shadow-lg shadow-black/10">
                <div class="h-full px-2 py-1.5 flex flex-col gap-0.5 text-left">
                    <div class="text-[11px] font-semibold leading-tight line-clamp-2">${escapeHtml(event.title || "Untitled")}</div>
                    <div class="text-[10px] text-on-surface-variant">${escapeHtml(formatTimeOnly(event.startDate))}</div>
                </div>
            </div>
            <div class="hidden group-hover:block absolute left-0 top-full mt-1 z-40 w-64 rounded-lg border border-outline-variant/30 bg-surface p-2.5 shadow-xl shadow-black/20 pointer-events-none">
                <div class="text-xs font-semibold text-on-surface mb-1">${escapeHtml(event.title || "Untitled")}</div>
                <div class="text-[11px] text-on-surface-variant">${escapeHtml(formatDateTime(event.startDate))}</div>
                ${course}
                ${event.description ? `<div class="text-[11px] text-on-surface-variant mt-1.5 leading-relaxed">${escapeHtml(event.description)}</div>` : ""}
            </div>
        </div>
    `;
}

/* ── Event Chip (month + all-day row) ──────────────────────────────────────── */
function buildEventChip(event, options = {}) {
    const badgeClass = getEventBadgeClass(event.type);
    const sizeClass = options.compact ? "text-[10px] px-2 py-1" : "text-[11px] px-2 py-1";
    const course = event.course ? `<div class="text-[10px] text-on-surface-variant mt-1">${escapeHtml(event.course)}</div>` : "";
    const timeDisplay = event.isAllDay ? formatAllDayRange(event) : formatDateTime(event.startDate);
    const details = [event.title || "Untitled", timeDisplay, event.description || "No description"].join("\n");

    return `
        <div class="group relative">
            <div class="${badgeClass} ${sizeClass} rounded-md border truncate" title="${escapeHtml(details)}">
                ${escapeHtml(event.title || "Untitled")}
            </div>
            <div class="hidden group-hover:block absolute left-0 top-full mt-1 z-20 w-64 rounded-lg border border-outline-variant/30 bg-surface p-2.5 shadow-xl shadow-black/20">
                <div class="text-xs font-semibold text-on-surface mb-1">${escapeHtml(event.title || "Untitled")}</div>
                <div class="text-[11px] text-on-surface-variant">${escapeHtml(timeDisplay)}</div>
                ${course}
                ${event.description ? `<div class="text-[11px] text-on-surface-variant mt-1.5 leading-relaxed">${escapeHtml(event.description)}</div>` : ""}
            </div>
        </div>
    `;
}

/* ── Assignments Section ───────────────────────────────────────────────────── */
function renderAssignments() {
    const root = document.getElementById("assignments-root");
    if (!root) return;

    const now = new Date();
    const upcoming = state.events.filter((e) => e.endDate >= now || e.startDate >= now).slice(0, 12);

    if (!upcoming.length) {
        root.innerHTML = `
            <div class="md:col-span-2 lg:col-span-3 rounded-xl border border-outline-variant/20 bg-surface-container p-6 text-sm text-on-surface-variant">
                No assignments found yet. Your calendar is still available above.
            </div>
        `;
        return;
    }

    root.innerHTML = upcoming.map((event) => {
        const urgency = event.isAllDay ? getUrgencyLabelAllDay(event) : getUrgencyLabel(event.startDate);
        const accent = getAccent(event.type, event.startDate);
        const timeDisplay = event.isAllDay ? formatAllDayRange(event) : formatDateTime(event.startDate);

        return `
            <article class="bg-surface-container hover:bg-surface-container-high transition-all duration-300 rounded-xl p-5 relative overflow-hidden flex flex-col gap-3">
                <div class="absolute left-0 top-0 bottom-0 w-1 ${accent.bar}"></div>
                <div class="flex justify-between items-start gap-3">
                    <span class="text-[10px] uppercase tracking-[0.05em] font-bold px-2 py-1 rounded ${accent.tag}">${escapeHtml(urgency)}</span>
                    <span class="text-[11px] text-on-surface-variant">${escapeHtml(timeDisplay)}</span>
                </div>
                <div>
                    <h3 class="text-base font-headline font-semibold text-on-surface leading-tight mb-1">${escapeHtml(event.title || "Untitled")}</h3>
                    <p class="text-sm text-on-surface-variant">${escapeHtml(event.course || "Other")}</p>
                </div>
                ${event.description ? `<p class="text-xs text-on-surface-variant line-clamp-3">${escapeHtml(event.description)}</p>` : ""}
            </article>
        `;
    }).join("");
}

/* ── Event Query Helpers ───────────────────────────────────────────────────── */
function getEventsForDay(date) {
    const dayStart = new Date(date.getFullYear(), date.getMonth(), date.getDate(), 0, 0, 0, 0);
    const dayEnd = new Date(date.getFullYear(), date.getMonth(), date.getDate(), 23, 59, 59, 999);
    return state.events.filter((e) => {
        const eStart = e.startDate;
        const eEnd = e.endDate || e.startDate;
        return eStart <= dayEnd && eEnd >= dayStart;
    });
}

/* ── Lane Layout for Overlapping Timed Events ──────────────────────────────── */
function layoutTimedEvents(events) {
    const items = events.map((e) => {
        const startMin = e.startDate.getHours() * 60 + e.startDate.getMinutes();
        const endDate = e.endDate || e.startDate;
        const durMin = Math.max(30, Math.ceil((endDate - e.startDate) / 60000));
        return { ...e, layoutStartMinutes: startMin, layoutDurationMinutes: durMin, layoutLane: 0, layoutLaneCount: 1 };
    }).sort((a, b) => a.layoutStartMinutes - b.layoutStartMinutes || a.layoutDurationMinutes - b.layoutDurationMinutes);

    const laneEnds = [];
    for (const item of items) {
        let lane = 0;
        while (lane < laneEnds.length && laneEnds[lane] > item.layoutStartMinutes) lane++;
        if (lane === laneEnds.length) laneEnds.push(0);
        laneEnds[lane] = item.layoutStartMinutes + item.layoutDurationMinutes;
        item.layoutLane = lane;
    }

    const count = Math.max(1, laneEnds.length);
    items.forEach((item) => { item.layoutLaneCount = count; });
    return items;
}

/* ── Date / Time Formatting ────────────────────────────────────────────────── */
function formatHourLabel(hour) {
    if (hour === 0) return "12 AM";
    if (hour < 12) return `${hour} AM`;
    if (hour === 12) return "12 PM";
    return `${hour - 12} PM`;
}

function formatTimeOnly(date) {
    return date.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}

function formatDateTime(date) {
    return date.toLocaleDateString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

function formatAllDayRange(event) {
    const startStr = event.startDate.toLocaleDateString(undefined, { month: "short", day: "numeric" });
    if (!event.isMultiDay || event.spanDays <= 1) {
        return `${startStr} (All day)`;
    }
    // For multi-day: show start - end range
    // endDate for all-day iCal events is exclusive, so subtract 1 day for display
    const displayEnd = new Date(event.endDate.getTime() - 86400000);
    const endStr = displayEnd.toLocaleDateString(undefined, { month: "short", day: "numeric" });
    return `${startStr} - ${endStr} (All day)`;
}

function getStartOfWeek(date) {
    const d = new Date(date);
    d.setHours(0, 0, 0, 0);
    d.setDate(d.getDate() - d.getDay());
    return d;
}

function isToday(date) {
    const now = new Date();
    return date.getFullYear() === now.getFullYear() && date.getMonth() === now.getMonth() && date.getDate() === now.getDate();
}

/* ── Urgency & Accent ──────────────────────────────────────────────────────── */
function getUrgencyLabel(startDate) {
    const diffDays = Math.floor((startDate - new Date()) / (1000 * 60 * 60 * 24));
    if (diffDays < 0) return "Past Due";
    if (diffDays === 0) return "Due Today";
    if (diffDays === 1) return "Due Tomorrow";
    if (diffDays < 7) return `Due in ${diffDays} days`;
    return startDate.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function getUrgencyLabelAllDay(event) {
    const now = new Date();
    const todayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const eventStartDay = new Date(event.startDate.getFullYear(), event.startDate.getMonth(), event.startDate.getDate());
    const eventEndDay = new Date(event.endDate.getTime() - 1); // exclusive end
    const eventEndDayStart = new Date(eventEndDay.getFullYear(), eventEndDay.getMonth(), eventEndDay.getDate());

    if (eventEndDayStart < todayStart) return "Past";
    if (eventStartDay <= todayStart && eventEndDayStart >= todayStart) return "Today";

    const diffDays = Math.floor((eventStartDay - todayStart) / (1000 * 60 * 60 * 24));
    if (diffDays === 1) return "Tomorrow";
    if (diffDays < 7) return `In ${diffDays} days`;
    return event.startDate.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function getAccent(type, startDate) {
    const soon = (startDate - new Date()) / (1000 * 60 * 60 * 24) <= 1;
    if (soon) return { bar: "bg-error", tag: "bg-error-container/20 text-error" };
    if (type === "quiz") return { bar: "bg-tertiary", tag: "bg-tertiary-container/30 text-tertiary" };
    return { bar: "bg-primary", tag: "bg-primary/20 text-primary-fixed" };
}

function getEventBadgeClass(type) {
    if (type === "quiz") return "bg-tertiary-container/20 text-tertiary border-tertiary/30";
    if (type === "assignment") return "bg-primary/10 text-primary-fixed border-primary/30";
    return "bg-secondary-container/25 text-on-secondary-container border-secondary-container/30";
}

/* ── HTML Escaping ─────────────────────────────────────────────────────────── */
function escapeHtml(text) {
    return String(text).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#39;");
}