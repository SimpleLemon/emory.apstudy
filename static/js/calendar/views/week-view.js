(function () {
    function createCalendarWeekView({
        state,
        constants,
        callbacks,
        formatters,
    }) {
        const {
            allDayMinHeightPx,
            hourHeightPx,
            weekMinimumDayWidthPx,
            weekdays,
        } = constants;
        const {
            getEventBadgeColors,
            getEventBadgeStyle,
            getEventElementAttributes,
            getEventsForDay,
            getVisibleEvents,
        } = callbacks;
        const {
            dateToDayIndex,
            escapeHtml,
            formatHourLabel,
            getStartOfWeek,
            isTaskEvent,
            isToday,
            layoutTimedEvents,
        } = formatters;

        function buildWeekViewHtml() {
            const weekStart = getStartOfWeek(state.anchorDate);
            const days = Array.from({ length: 7 }, (_, i) => {
                const d = new Date(weekStart);
                d.setDate(weekStart.getDate() + i);
                return d;
            });
            const weekEnd = new Date(days[6]);
            weekEnd.setHours(23, 59, 59, 999);
            const { events: allDayEvents, rowsCount: allDayRowsCount } = getAllDayEventsForWeek(days, weekStart, weekEnd);
            const hasAllDayEvents = allDayEvents.length > 0;
            const timedByDay = days.map((d) => getEventsForDay(d).filter((e) => !e.isAllDay && !isTimedMultiDayEvent(e)));
            const totalHours = 24;
            const timeGridHeight = totalHours * hourHeightPx;
            const dayColTemplate = `minmax(${weekMinimumDayWidthPx}px, 1fr)`;
            const gridCols = `4.75rem repeat(7, ${dayColTemplate})`;
            const dayHeaders = days.map((day, i) => {
                const dateLabel = `${day.getMonth() + 1}/${day.getDate()}`;
                const isCurrentDay = isToday(day);
                const todayMarkerClass = isCurrentDay ? "calendar-today-marker calendar-today-marker-week" : "";
                const dayNameClass = isCurrentDay ? "text-red-500" : "text-on-surface-variant";
                const dateNumClass = isCurrentDay ? "text-red-500 font-bold" : "text-on-surface";
                return `
                    <div class="px-3 py-3 text-center border-r border-calendar-rule last:border-r-0 flex items-center justify-center">
                        <div class="inline-flex flex-col items-center justify-center gap-0.5 ${todayMarkerClass}">
                            <div class="text-xs uppercase tracking-[0.05em] font-semibold ${dayNameClass}">${weekdays[i]}</div>
                            <div class="inline-flex h-7 w-7 items-center justify-center text-sm font-semibold ${dateNumClass}">
                                ${dateLabel}
                            </div>
                        </div>
                    </div>
                `;
            }).join("");
            const allDayRowCount = Math.max(1, allDayRowsCount || 0);
            const allDayRowHeight = Math.max(allDayMinHeightPx, allDayRowCount * 28 + 16);
            const allDayChips = allDayEvents.map((ev) => {
                const colStart = ev.gridColStart + 2;
                const colEnd = colStart + ev.gridSpan;
                const row = typeof ev.rowIndex === "number" ? ev.rowIndex + 1 : 1;
                if (isTimedMultiDayEvent(ev)) {
                    return renderTimedMultiDayEvent(ev, colStart, colEnd, row);
                }
                const badgeStyle = getEventBadgeStyle(ev);
                return `
                    <div ${getEventElementAttributes(ev)} class="calendar-event-shell relative" style="grid-column: ${colStart} / ${colEnd}; grid-row: ${row};">
                        <div class="text-xs px-2 py-1 rounded-md border truncate" style="${badgeStyle}">
                            ${escapeHtml(ev.title || "Untitled")}
                        </div>
                    </div>
                `;
            }).join("");
            const timeAxisCells = [];
            for (let h = 0; h < 24; h++) {
                timeAxisCells.push(`
                    <div class="border-b border-calendar-rule relative" style="height:${hourHeightPx}px;">
                        <span class="absolute right-2 top-0 -translate-y-1/2 text-xs text-on-surface-variant font-medium leading-none whitespace-nowrap">${formatHourLabel(h)}</span>
                    </div>
                `);
            }
            const dayColumns = days.map((day, idx) => {
                const positioned = layoutTimedEvents(timedByDay[idx]);
                const todayBg = isToday(day) ? "bg-primary/[0.03]" : "";
                const gridLines = [];
                for (let h = 0; h < 24; h++) {
                    const timeLabel = `${String(h).padStart(2, "0")}:00`;
                    gridLines.push(`<div data-time="${timeLabel}" class="border-b border-calendar-rule" style="height:${hourHeightPx}px;"></div>`);
                }
                const eventChips = positioned.map((e) => renderTimedEvent(e)).join("");
                return `
                    <div data-date="${day.getFullYear()}-${String(day.getMonth() + 1).padStart(2, "0")}-${String(day.getDate()).padStart(2, "0")}" class="relative border-r border-calendar-rule last:border-r-0 ${todayBg} overflow-visible">
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
                <div class="rounded-2xl border border-calendar-rule bg-transparent shadow-2xl shadow-black/10 overflow-hidden">
                    <div class="grid border-b border-calendar-rule bg-surface-container-low sticky top-0 z-30" style="grid-template-columns: ${gridCols}; column-gap: 0.1rem; padding-right: var(--scrollbar-offset, 0px);">
                        <div class="px-3 py-3 border-r border-calendar-rule"></div>
                        ${dayHeaders}
                    </div>
                    ${hasAllDayEvents ? `
                    <div class="grid border-b border-calendar-rule bg-surface-container-low sticky top-[3.75rem] z-20 items-start gap-y-1 px-1 py-0.5" style="grid-template-columns: ${gridCols}; column-gap: 0.1rem; padding-right: var(--scrollbar-offset, 0px); min-height:${allDayRowHeight}px;">
                        <div class="px-2 py-1 text-xs uppercase tracking-[0.08em] text-on-surface-variant font-semibold border-r border-calendar-rule flex items-start justify-end" style="grid-row: 1 / span ${allDayRowCount};">All day</div>
                        ${allDayChips}
                    </div>
                    ` : ""}
                    <div id="calendar-week-time-scroller" class="overflow-y-scroll overflow-x-auto" style="max-height:760px;">
                        <div class="grid" style="grid-template-columns: ${gridCols}; column-gap: 0.1rem; min-width: calc(4.75rem + 7 * ${weekMinimumDayWidthPx}px);">
                            <div class="border-r border-calendar-rule bg-surface-container-low overflow-visible pt-[0.35rem] pb-[0.35rem]">
                                ${timeAxisCells.join("")}
                            </div>
                            ${dayColumns}
                        </div>
                    </div>
                </div>
            `;
        }

        function formatCompactTime(date) {
            const parts = new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" }).formatToParts(date);
            const hour = parts.find((part) => part.type === "hour")?.value || "";
            const minute = parts.find((part) => part.type === "minute")?.value || "";
            const dayPeriod = parts.find((part) => part.type === "dayPeriod")?.value || "";
            const compactTime = minute && minute !== "00" ? `${hour}:${minute}` : hour;
            return `${compactTime} ${dayPeriod}`.trim();
        }

        function formatCompactTimeRange(event) {
            const start = event.startDate;
            const end = event.endDate || event.startDate;
            const sameDay = start.getFullYear() === end.getFullYear()
                && start.getMonth() === end.getMonth()
                && start.getDate() === end.getDate();
            if (sameDay) {
                return `${formatCompactTime(start)} – ${formatCompactTime(end)}`;
            }
            const startLabel = start.toLocaleDateString(undefined, { month: "short", day: "numeric" });
            const endLabel = end.toLocaleDateString(undefined, { month: "short", day: "numeric" });
            return `${startLabel}, ${formatCompactTime(start)} – ${endLabel}, ${formatCompactTime(end)}`;
        }

        function getAllDayEventsForWeek(days, weekStart, weekEnd) {
            const seen = new Set();
            const candidates = [];
            const weekStartDay = new Date(weekStart.getFullYear(), weekStart.getMonth(), weekStart.getDate());
            const weekEndExclusive = new Date(weekEnd.getFullYear(), weekEnd.getMonth(), weekEnd.getDate() + 1);
            for (const event of getVisibleEvents()) {
                if (!event.isAllDay && !isTimedMultiDayEvent(event)) continue;
                const eStart = new Date(event.startDate.getFullYear(), event.startDate.getMonth(), event.startDate.getDate());
                const eEnd = event.endDate || event.startDate;
                const eEndDay = new Date(eEnd.getFullYear(), eEnd.getMonth(), eEnd.getDate());
                const eventEndExclusive = event.isAllDay
                    ? eEndDay > eStart ? eEndDay : new Date(eStart.getFullYear(), eStart.getMonth(), eStart.getDate() + 1)
                    : new Date(eEndDay.getFullYear(), eEndDay.getMonth(), eEndDay.getDate() + 1);
                if (eStart >= weekEndExclusive || eventEndExclusive <= weekStartDay) continue;
                const key = event.uid || event.id || `${event.title}|${eStart.getTime()}|${event.isAllDay ? "all-day" : "timed"}`;
                if (seen.has(key)) continue;
                seen.add(key);
                const eventStartDay = dateToDayIndex(eStart, days);
                const lastVisibleDay = new Date(eventEndExclusive);
                lastVisibleDay.setDate(lastVisibleDay.getDate() - 1);
                const eventEndDay = dateToDayIndex(lastVisibleDay, days);
                const clampedStart = Math.max(0, eventStartDay);
                const clampedEnd = Math.min(6, eventEndDay);
                const span = clampedEnd - clampedStart + 1;
                if (span <= 0) continue;
                candidates.push({
                    ...event,
                    gridColStart: clampedStart,
                    gridSpan: span,
                });
            }
            candidates.sort((a, b) => a.gridColStart - b.gridColStart || b.gridSpan - a.gridSpan || (a.title || "").localeCompare(b.title || ""));
            const occupancy = [];
            for (const ev of candidates) {
                let placed = false;
                for (let r = 0; r < occupancy.length; r++) {
                    let ok = true;
                    for (let c = ev.gridColStart; c < ev.gridColStart + ev.gridSpan; c++) {
                        if (occupancy[r][c]) { ok = false; break; }
                    }
                    if (ok) {
                        for (let c = ev.gridColStart; c < ev.gridColStart + ev.gridSpan; c++) occupancy[r][c] = true;
                        ev.rowIndex = r;
                        placed = true;
                        break;
                    }
                }
                if (!placed) {
                    const newRow = Array(7).fill(false);
                    for (let c = ev.gridColStart; c < ev.gridColStart + ev.gridSpan; c++) newRow[c] = true;
                    occupancy.push(newRow);
                    ev.rowIndex = occupancy.length - 1;
                }
            }
            return {
                events: candidates.sort((a, b) => (a.rowIndex - b.rowIndex) || (a.gridColStart - b.gridColStart)),
                rowsCount: occupancy.length || 0,
            };
        }

        function isTimedMultiDayEvent(event) {
            if (!event || event.isAllDay) return false;
            const end = event.endDate || event.startDate;
            return end > event.startDate
                && (event.startDate.getFullYear() !== end.getFullYear()
                    || event.startDate.getMonth() !== end.getMonth()
                    || event.startDate.getDate() !== end.getDate());
        }

        function renderTimedMultiDayEvent(event, colStart, colEnd, row) {
            const colors = getEventBadgeColors?.(event) || {
                background: "var(--color-primary-container)",
                text: "var(--color-on-primary-container)",
                border: "var(--color-primary)",
                indicator: "var(--color-primary)",
            };
            const isTask = isTaskEvent(event);
            const taskClasses = isTask && event.completed ? " is-completed" : "";
            return `
                <div ${getEventElementAttributes(event)} class="calendar-event-shell calendar-week-spanning-event-shell relative${taskClasses}" style="grid-column: ${colStart} / ${colEnd}; grid-row: ${row};">
                    <div class="calendar-week-spanning-event" style="background-color:${colors.background}; color:${colors.text}; border-color:${colors.border};">
                        <span class="calendar-week-spanning-event-indicator" style="background-color:${colors.indicator};" aria-hidden="true"></span>
                        <span class="calendar-week-spanning-event-title">${isTask && event.completed ? "✓ " : ""}${escapeHtml(event.title || "Untitled")}</span>
                    </div>
                </div>
            `;
        }

        function renderTimedEvent(event) {
            const topPx = (event.layoutStartMinutes / 60) * hourHeightPx;
            const heightPx = Math.max((event.layoutDurationMinutes / 60) * hourHeightPx, 22);
            const leftPct = (event.layoutLane / event.layoutLaneCount) * 100;
            const widthPct = 100 / event.layoutLaneCount;
            const badgeStyle = getEventBadgeStyle(event);
            const isTask = isTaskEvent(event);
            const taskClasses = isTask && event.completed ? " is-completed" : "";
            const priorityLabel = isTask && event.priority && event.priority !== "none" ? event.priority : "";
            const showTimeRange = heightPx >= 44;
            return `
                <div ${getEventElementAttributes(event)} class="calendar-event-shell absolute px-0.5${taskClasses}" style="top:${topPx}px; left:${leftPct}%; width:calc(${widthPct}% - 0.25rem); height:${heightPx}px; z-index: 10;">
                    <div class="h-full rounded-lg border overflow-hidden shadow-lg shadow-black/10" style="${badgeStyle}">
                        <div class="h-full px-2 py-1.5 flex flex-col ${showTimeRange ? "justify-start gap-0.5" : "justify-center"} text-left overflow-hidden">
                            <div class="text-xs font-semibold leading-tight ${showTimeRange ? "line-clamp-1" : "line-clamp-2"}">${isTask && event.completed ? "✓ " : ""}${escapeHtml(event.title || "Untitled")}</div>
                            ${showTimeRange ? `<div class="text-[0.7rem] leading-tight">${priorityLabel ? `${escapeHtml(priorityLabel)} · ` : ""}${escapeHtml(formatCompactTimeRange(event))}</div>` : ""}
                        </div>
                    </div>
                </div>
            `;
        }

        return { buildWeekViewHtml };
    }

    window.APStudyCalendarWeekView = { createCalendarWeekView };
})();
