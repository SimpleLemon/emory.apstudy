(function () {
    function createCalendarMonthView({
        state,
        constants,
        callbacks,
        formatters,
    }) {
        const { weekdays } = constants;
        const {
            buildEventChip,
            getEventBadgeColors,
            getEventBadgeStyle,
            getEventElementAttributes,
            getEventsForDay,
            getVisibleEvents,
        } = callbacks;
        const {
            dateToDayIndex,
            escapeHtml,
            formatDateKey,
            formatMonthGridDayLabel,
            isToday,
        } = formatters;

        function buildMonthViewHtml() {
            const year = state.anchorDate.getFullYear();
            const month = state.anchorDate.getMonth();
            const monthStart = new Date(year, month, 1);
            const monthEnd = new Date(year, month + 1, 0);
            const gridStart = new Date(monthStart);
            gridStart.setDate(monthStart.getDate() - monthStart.getDay());
            const weeks = [];
            for (let weekIndex = 0; weekIndex < 6; weekIndex++) {
                const weekStart = new Date(gridStart);
                weekStart.setDate(gridStart.getDate() + weekIndex * 7);
                const days = Array.from({ length: 7 }, (_, dayIndex) => {
                    const day = new Date(weekStart);
                    day.setDate(weekStart.getDate() + dayIndex);
                    return day;
                });
                const { events: spanningEvents, rowsCount } = getSpanningEventsForMonthWeek(days);
                const spanningRowCount = rowsCount || 0;
                const gridEndLine = spanningRowCount + 3;
                const occupiedSpanningRowsByDay = days.map((_, dayIndex) => spanningEvents.reduce((max, event) => {
                    const start = event.gridColStart;
                    const end = event.gridColStart + event.gridSpan - 1;
                    if (dayIndex < start || dayIndex > end) return max;
                    return Math.max(max, (event.rowIndex || 0) + 1);
                }, 0));
                const rowTemplate = spanningRowCount > 0
                    ? `2.75rem repeat(${spanningRowCount}, 1.65rem) minmax(3.25rem, 1fr)`
                    : "2.75rem minmax(4.9rem, 1fr)";
                const rowMinHeight = Math.max(120, 96 + spanningRowCount * 26);
                const dayCells = days.map((day, dayIndex) => {
                    const inMonth = day >= monthStart && day <= monthEnd;
                    const bgClass = inMonth ? "bg-surface-container" : "opacity-70";
                    const backgroundStyle = inMonth ? "" : "background:color-mix(in srgb, var(--color-surface-container) 40%, transparent);";
                    return `
                        <div
                            data-date="${formatDateKey(day)}"
                            class="calendar-month-day-cell ${dayIndex === 6 ? "calendar-month-day-cell-edge" : ""} ${bgClass} border-r border-b border-calendar-rule ${dayIndex === 6 ? "border-r-0" : ""}"
                            style="grid-column:${dayIndex + 1}; grid-row:1 / ${gridEndLine}; ${backgroundStyle}"
                        ></div>
                    `;
                }).join("");
                const dayNumbers = days.map((day, dayIndex) => {
                    const isCurrentDay = isToday(day);
                    const todayMarkerClass = isCurrentDay ? "calendar-today-marker calendar-today-marker-month" : "";
                    const todayTextClass = isCurrentDay ? "text-red-500 font-bold" : "text-on-surface-variant";
                    return `
                        <div class="calendar-month-day-number-slot relative" style="grid-column:${dayIndex + 1}; grid-row:1; z-index:10;">
                            <div class="calendar-month-day-number inline-flex items-center justify-center text-xs font-semibold ${todayTextClass} ${todayMarkerClass}">
                                ${escapeHtml(formatMonthGridDayLabel(day))}
                            </div>
                        </div>
                    `;
                }).join("");
                const spanningBars = spanningEvents.map((event) => (
                    isTimedMultiDayEvent(event)
                        ? renderMonthTimedMultiDayEvent(event)
                        : renderMonthAllDayEvent(event)
                )).join("");
                const timedEventColumns = days.map((day, dayIndex) => {
                    const dayEvents = getEventsForDay(day).filter((event) => (
                        !event.isAllDay && !isTimedMultiDayEvent(event)
                    ));
                    const dayTimedRowStart = (occupiedSpanningRowsByDay[dayIndex] || 0) + 2;
                    return `
                        <div class="relative px-1 pb-2.5 space-y-1" style="grid-column:${dayIndex + 1}; grid-row:${dayTimedRowStart} / ${gridEndLine}; z-index:10;">
                            ${dayEvents.slice(0, 3).map((e) => buildEventChip(e, { monthView: true })).join("")}
                            ${dayEvents.length > 3 ? `<div class="text-xs text-on-surface-variant">+${dayEvents.length - 3} more</div>` : ""}
                        </div>
                    `;
                }).join("");
                weeks.push(`
                    <div class="grid relative" style="grid-template-columns:repeat(7, minmax(0, 1fr)); grid-template-rows:${rowTemplate}; min-height:${rowMinHeight}px;">
                        ${dayCells}
                        ${dayNumbers}
                        ${spanningBars}
                        ${timedEventColumns}
                    </div>
                `);
            }
            return `
                <div class="calendar-month-header grid grid-cols-7 border-l border-t border-r border-calendar-rule bg-surface-container-low" style="border-top-left-radius:1rem; border-top-right-radius:1rem;">
                    ${weekdays.map((d, index) => `<div class="calendar-month-header-cell ${index === 6 ? "calendar-month-header-cell-edge" : ""} text-xs uppercase tracking-[0.05em] text-center text-on-surface-variant font-semibold py-2 border-r border-b border-calendar-rule ${index === 6 ? "border-r-0" : ""}">${d}</div>`).join("")}
                </div>
                <div class="calendar-month-grid border-l border-r border-calendar-rule shadow-2xl shadow-black/10" style="border-bottom-left-radius:1rem; border-bottom-right-radius:1rem;">
                    ${weeks.join("")}
                </div>
            `;
        }

        function getSpanningEventsForMonthWeek(days) {
            const weekStart = new Date(days[0].getFullYear(), days[0].getMonth(), days[0].getDate());
            const weekEndExclusive = new Date(days[6].getFullYear(), days[6].getMonth(), days[6].getDate() + 1);
            const seen = new Set();
            const candidates = [];
            for (const event of getVisibleEvents()) {
                if (!event.isAllDay && !isTimedMultiDayEvent(event)) continue;
                const eStart = new Date(event.startDate.getFullYear(), event.startDate.getMonth(), event.startDate.getDate());
                const eventEndExclusive = getEventEndDayExclusive(event);
                if (eStart >= weekEndExclusive || eventEndExclusive <= weekStart) continue;
                const key = event.uid || event.id || `${event.title}|${event.startDate.getTime()}|${event.isAllDay ? "all-day" : "timed"}`;
                if (seen.has(key)) continue;
                seen.add(key);
                const lastVisibleDay = new Date(eventEndExclusive);
                lastVisibleDay.setDate(lastVisibleDay.getDate() - 1);
                const clampedStart = Math.max(0, dateToDayIndex(eStart, days));
                const clampedEnd = Math.min(6, dateToDayIndex(lastVisibleDay, days));
                const span = clampedEnd - clampedStart + 1;
                if (span <= 0) continue;
                candidates.push({
                    ...event,
                    gridColStart: clampedStart,
                    gridSpan: span,
                    continuesFromPreviousWeek: eStart < weekStart,
                    continuesToNextWeek: eventEndExclusive > weekEndExclusive,
                });
            }
            packSpanningEvents(candidates);
            const rowsCount = candidates.reduce((max, event) => Math.max(max, (event.rowIndex || 0) + 1), 0);
            return {
                events: candidates.sort((a, b) => (a.rowIndex - b.rowIndex) || (a.gridColStart - b.gridColStart)),
                rowsCount,
            };
        }

        function getEventEndDayExclusive(event) {
            const startDay = new Date(event.startDate.getFullYear(), event.startDate.getMonth(), event.startDate.getDate());
            const end = event.endDate instanceof Date && !Number.isNaN(event.endDate.getTime()) && event.endDate > event.startDate
                ? event.endDate
                : null;
            if (!end) return new Date(startDay.getFullYear(), startDay.getMonth(), startDay.getDate() + 1);
            if (event.isAllDay) {
                const endDay = new Date(end.getFullYear(), end.getMonth(), end.getDate());
                return endDay > startDay
                    ? endDay
                    : new Date(startDay.getFullYear(), startDay.getMonth(), startDay.getDate() + 1);
            }
            const lastOccupiedInstant = new Date(end.getTime() - 1);
            return new Date(
                lastOccupiedInstant.getFullYear(),
                lastOccupiedInstant.getMonth(),
                lastOccupiedInstant.getDate() + 1,
            );
        }

        function isTimedMultiDayEvent(event) {
            if (!event || event.isAllDay || !(event.startDate instanceof Date) || Number.isNaN(event.startDate.getTime())) return false;
            const end = event.endDate;
            if (!(end instanceof Date) || Number.isNaN(end.getTime()) || end <= event.startDate) return false;
            const lastOccupiedInstant = new Date(end.getTime() - 1);
            return event.startDate.getFullYear() !== lastOccupiedInstant.getFullYear()
                || event.startDate.getMonth() !== lastOccupiedInstant.getMonth()
                || event.startDate.getDate() !== lastOccupiedInstant.getDate();
        }

        function packSpanningEvents(events) {
            events.sort((a, b) => a.gridColStart - b.gridColStart || b.gridSpan - a.gridSpan || (a.title || "").localeCompare(b.title || ""));
            const occupancy = [];
            for (const event of events) {
                let placed = false;
                for (let row = 0; row < occupancy.length; row++) {
                    let canFit = true;
                    for (let col = event.gridColStart; col < event.gridColStart + event.gridSpan; col++) {
                        if (occupancy[row][col]) {
                            canFit = false;
                            break;
                        }
                    }
                    if (!canFit) continue;
                    for (let col = event.gridColStart; col < event.gridColStart + event.gridSpan; col++) occupancy[row][col] = true;
                    event.rowIndex = row;
                    placed = true;
                    break;
                }
                if (placed) continue;
                const newRow = Array(7).fill(false);
                for (let col = event.gridColStart; col < event.gridColStart + event.gridSpan; col++) newRow[col] = true;
                occupancy.push(newRow);
                event.rowIndex = occupancy.length - 1;
            }
        }

        function renderMonthAllDayEvent(event) {
            const badgeStyle = getEventBadgeStyle(event);
            const colStart = event.gridColStart + 1;
            const colEnd = colStart + event.gridSpan;
            const row = (event.rowIndex || 0) + 2;
            const radiusStyle = `border-top-left-radius:${event.continuesFromPreviousWeek ? "0" : "0.375rem"}; border-bottom-left-radius:${event.continuesFromPreviousWeek ? "0" : "0.375rem"}; border-top-right-radius:${event.continuesToNextWeek ? "0" : "0.375rem"}; border-bottom-right-radius:${event.continuesToNextWeek ? "0" : "0.375rem"};`;
            const padLeft = event.continuesFromPreviousWeek ? "0" : "0.25rem";
            const padRight = event.continuesToNextWeek ? "0" : "0.25rem";
            return `
                <div ${getEventElementAttributes(event)} class="calendar-event-shell relative" style="grid-column:${colStart} / ${colEnd}; grid-row:${row}; z-index:20; padding-left:${padLeft}; padding-right:${padRight};">
                    <div class="text-xs px-2 py-1 border truncate leading-none" style="${badgeStyle}; ${radiusStyle}">
                        ${escapeHtml(event.title || "Untitled")}
                    </div>
                </div>
            `;
        }

        function renderMonthTimedMultiDayEvent(event) {
            const colors = getEventBadgeColors(event);
            const colStart = event.gridColStart + 1;
            const colEnd = colStart + event.gridSpan;
            const row = (event.rowIndex || 0) + 2;
            const radiusStyle = `border-top-left-radius:${event.continuesFromPreviousWeek ? "0" : "0.375rem"}; border-bottom-left-radius:${event.continuesFromPreviousWeek ? "0" : "0.375rem"}; border-top-right-radius:${event.continuesToNextWeek ? "0" : "0.375rem"}; border-bottom-right-radius:${event.continuesToNextWeek ? "0" : "0.375rem"};`;
            const padLeft = event.continuesFromPreviousWeek ? "0" : "0.25rem";
            const padRight = event.continuesToNextWeek ? "0" : "0.25rem";
            return `
                <div ${getEventElementAttributes(event)} class="calendar-event-shell calendar-month-spanning-event-shell relative" style="grid-column:${colStart} / ${colEnd}; grid-row:${row}; z-index:20; padding-left:${padLeft}; padding-right:${padRight};">
                    <div class="calendar-month-spanning-event flex h-full min-w-0 items-center gap-2 overflow-hidden border px-2 py-1 text-xs leading-none" style="background-color:${colors.background}; color:${colors.text}; border-color:${colors.border}; ${radiusStyle}">
                        ${event.continuesFromPreviousWeek ? "" : `<span class="inline-block h-4 w-1 shrink-0 rounded-full" style="background-color:${colors.indicator};" aria-hidden="true"></span>`}
                        <span class="min-w-0 truncate font-medium">${escapeHtml(event.title || "Untitled")}</span>
                    </div>
                </div>
            `;
        }

        return { buildMonthViewHtml };
    }

    window.APStudyCalendarMonthView = { createCalendarMonthView };
})();
