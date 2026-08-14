(function () {
    function createCalendarAgenda({
        root = document,
        state,
        callbacks,
        formatters,
    }) {
        const {
            getCalendarEventColor,
            getCalendarEventLabel,
            getEventBadgeColors,
            getEventElementAttributes,
            getEventsForDay,
            getVisibleEvents,
        } = callbacks;
        const {
            escapeHtml,
            formatAllDayRange,
            formatTimedEventRange,
            getStartOfWeek,
            getUrgencyLabel,
            getUrgencyLabelAllDay,
            isTaskEvent,
            isToday,
        } = formatters;

        const query = (selector) => root?.querySelector?.(selector);

        function buildMobileCalendarAgendaHtml() {
            const allDays = getMobileAgendaDays();
            const days = state.view === "month"
                ? allDays.filter((day) => getEventsForDay(day).length > 0 || isToday(day))
                : allDays;
            const visibleDays = days.length ? days : allDays.slice(0, 1);
            return `
                <div class="calendar-mobile-agenda" data-calendar-mobile-view="${escapeHtml(state.view)}">
                    ${visibleDays.map((day) => renderMobileAgendaDay(day)).join("")}
                </div>
            `;
        }

        function getMobileAgendaDays() {
            if (state.view === "month") {
                const year = state.anchorDate.getFullYear();
                const month = state.anchorDate.getMonth();
                const monthEnd = new Date(year, month + 1, 0);
                return Array.from({ length: monthEnd.getDate() }, (_, index) => new Date(year, month, index + 1));
            }
            const weekStart = getStartOfWeek(state.anchorDate);
            return Array.from({ length: 7 }, (_, index) => {
                const day = new Date(weekStart);
                day.setDate(weekStart.getDate() + index);
                return day;
            });
        }

        function renderMobileAgendaDay(day) {
            const dayEvents = getEventsForDay(day)
                .slice()
                .sort((a, b) => {
                    if (a.isAllDay !== b.isAllDay) return a.isAllDay ? -1 : 1;
                    return a.startDate - b.startDate || (a.title || "").localeCompare(b.title || "");
                });
            const dateLabel = day.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
            const todayClass = isToday(day) ? " is-today" : "";
            return `
                <section class="calendar-mobile-day${todayClass}">
                    <header class="calendar-mobile-day-header">
                        <span>${escapeHtml(dateLabel)}</span>
                        <strong>${dayEvents.length}</strong>
                    </header>
                    <div class="calendar-mobile-event-list">
                        ${dayEvents.length ? dayEvents.map(renderMobileAgendaEvent).join("") : '<p class="calendar-mobile-empty">No events</p>'}
                    </div>
                </section>
            `;
        }

        function renderMobileAgendaEvent(event) {
            const timeDisplay = event.isAllDay ? formatAllDayRange(event) : formatTimedEventRange(event);
            const calendarLabel = getCalendarEventLabel(event);
            const indicatorColor = getEventBadgeColors(event).indicator;
            const completedClass = isTaskEvent(event) && event.completed ? " is-completed" : "";
            return `
                <article ${getEventElementAttributes(event)} class="calendar-mobile-event calendar-event-shell${completedClass}">
                    <span class="calendar-mobile-event-bar" style="background-color:${escapeHtml(indicatorColor)}"></span>
                    <div class="calendar-mobile-event-copy">
                        <h3>${isTaskEvent(event) && event.completed ? "✓ " : ""}${escapeHtml(event.title || "Untitled")}</h3>
                        <p>${escapeHtml(timeDisplay)}</p>
                        <small>${escapeHtml(calendarLabel)}</small>
                    </div>
                </article>
            `;
        }

        function buildUpcomingAgendaHtml() {
            const now = new Date();
            const rangeEnd = new Date(now);
            rangeEnd.setDate(rangeEnd.getDate() + 30);
            const upcoming = getVisibleEvents()
                .filter((e) => e.source !== "simulated")
                .filter((e) => e.endDate >= now || e.startDate >= now)
                .filter((e) => e.startDate <= rangeEnd)
                .slice(0, 40);
            if (!upcoming.length) {
                return `
                    <div class="calendar-upcoming-empty">
                        <span class="material-symbols-outlined" aria-hidden="true">event_upcoming</span>
                        <div>
                            <h2>Your next 30 days are clear</h2>
                            <p>New events and deadlines will appear here as they are added.</p>
                        </div>
                    </div>
                `;
            }

            const grouped = new Map();
            upcoming.forEach((event) => {
                const day = new Date(event.startDate.getFullYear(), event.startDate.getMonth(), event.startDate.getDate());
                const key = day.toISOString();
                if (!grouped.has(key)) grouped.set(key, { day, events: [] });
                grouped.get(key).events.push(event);
            });

            return `
                <div class="calendar-upcoming-agenda" aria-label="Events in the next 30 days">
                    ${Array.from(grouped.values()).map(({ day, events }) => renderUpcomingDay(day, events)).join("")}
                </div>
            `;
        }

        function renderUpcomingDay(day, events) {
            const today = isToday(day);
            const yesterday = new Date(day);
            yesterday.setDate(yesterday.getDate() - 1);
            const relativeLabel = today ? "Today" : isToday(yesterday) ? "Tomorrow" : day.toLocaleDateString(undefined, { weekday: "long" });
            const dateLabel = day.toLocaleDateString(undefined, { month: "short", day: "numeric" });
            return `
                <section class="calendar-upcoming-day${today ? " is-today" : ""}">
                    <header class="calendar-upcoming-date">
                        <span class="calendar-upcoming-weekday">${escapeHtml(relativeLabel)}</span>
                        <span class="calendar-upcoming-date-label">${escapeHtml(dateLabel)}</span>
                    </header>
                    <div class="calendar-upcoming-events">
                        ${events.map(renderUpcomingEvent).join("")}
                    </div>
                </section>
            `;
        }

        function renderUpcomingEvent(event) {
                const urgency = event.isAllDay
                    ? getUrgencyLabelAllDay(event)
                    : getUrgencyLabel(event.startDate, new Date(), event.endDate);
                const calendarColor = getCalendarEventColor(event);
                const timeDisplay = event.isAllDay ? formatAllDayRange(event) : formatTimedEventRange(event);
                const calendarLabel = getCalendarEventLabel(event);
                const description = String(event.description || "").trim().replace(/\s+/g, " ");
                return `
                    <article ${getEventElementAttributes(event)} class="calendar-upcoming-event calendar-event-shell${isTaskEvent(event) && event.completed ? " is-completed" : ""}">
                        <span class="calendar-upcoming-color" style="background-color:${escapeHtml(calendarColor)}" aria-hidden="true"></span>
                        <div class="calendar-upcoming-time">${escapeHtml(timeDisplay)}</div>
                        <div class="calendar-upcoming-copy">
                            <h3>${isTaskEvent(event) && event.completed ? "✓ " : ""}${escapeHtml(event.title || "Untitled")}</h3>
                            <p><span>${escapeHtml(calendarLabel)}</span>${description ? `<span aria-hidden="true">·</span><span>${escapeHtml(description)}</span>` : ""}</p>
                        </div>
                        <span class="calendar-upcoming-urgency">${escapeHtml(urgency)}</span>
                        <span class="material-symbols-outlined calendar-upcoming-arrow" aria-hidden="true">chevron_right</span>
                    </article>
                `;
        }

        function renderAssignments() {
            const assignmentsRoot = query("#assignments-root");
            if (assignmentsRoot) assignmentsRoot.innerHTML = buildUpcomingAgendaHtml();
        }

        function buildAssignmentsSkeletonHtml() {
            const block = (className) => window.APStudySkeleton?.block?.(className)
                || `<div data-slot="skeleton" class="bg-muted rounded-md animate-pulse ${className}"></div>`;
            return `
                <div class="calendar-assignments-skeleton apstudy-skeleton md:col-span-2 lg:col-span-3" role="status" aria-live="polite" aria-busy="true">
                    <span class="sr-only">Loading upcoming events...</span>
                    <div class="contents" aria-hidden="true">
                        ${Array.from({ length: 3 }, (_, index) => `
                            <article class="calendar-assignment-skeleton-card rounded-xl border border-outline-variant/20 bg-surface-container p-5 sm:p-6">
                                <div class="flex items-start justify-between gap-3">
                                    ${block("h-6 w-20")}
                                    ${block("h-4 w-24")}
                                </div>
                                <div class="mt-5 flex flex-col gap-3">
                                    ${block(index === 1 ? "h-5 w-3/4" : "h-5 w-full")}
                                    <div class="flex items-center gap-2">${block("size-3 rounded-full")} ${block("h-3 w-24")}</div>
                                </div>
                                <div class="mt-5 flex flex-col gap-2">${block("h-3 w-full")} ${block("h-3 w-4/5")}</div>
                            </article>
                        `).join("")}
                    </div>
                </div>
            `;
        }

        return {
            buildMobileCalendarAgendaHtml,
            buildUpcomingAgendaHtml,
            renderAssignments,
        };
    }

    window.APStudyCalendarAgenda = { createCalendarAgenda };
})();
