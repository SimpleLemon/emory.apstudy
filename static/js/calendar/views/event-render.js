(function () {
    function createCalendarEventRender({
        state,
        callbacks,
        formatters,
    }) {
        const {
            getCalendarEventColor,
            getCalendarEventRef,
            getEventCalendarLabel,
            getVisibleEvents,
        } = callbacks;
        const {
            createAccessibleEventPalette,
            escapeHtml,
            formatAllDayRange,
            formatTimedEventRange,
            getCssColorVariable,
            getTaskPriorityColor,
            isTaskEvent,
        } = formatters;

        function getEventElementAttributes(event) {
            const dataId = event.id || event.uid || "";
            const eventRef = getCalendarEventRef(event) || dataId;
            const sourceType = event.source_type || event.source || (event.id ? "user" : "feed");
            const taskId = event.task_id ? ` data-task-id="${escapeHtml(event.task_id)}"` : "";
            const title = event.title || "Untitled";
            const timeLabel = event.isAllDay ? formatAllDayRange(event) : formatTimedEventRange(event);
            const calendarLabel = getEventCalendarLabel(event) || "Calendar";
            const actionLabel = state.public.readOnly
                ? "View event"
                : isTaskEvent(event) && event.task_id
                    ? "Open task"
                    : "Open event";
            const accessibleLabel = `${title}, ${timeLabel}, ${calendarLabel}. ${actionLabel}. More actions available.`;
            return `data-event-id="${escapeHtml(dataId)}" data-event-ref="${escapeHtml(eventRef)}" data-event-source="${escapeHtml(sourceType)}"${taskId} role="button" aria-label="${escapeHtml(accessibleLabel)}" aria-haspopup="menu" tabindex="0"`;
        }

        function getEventsForDay(date) {
            const dayStart = new Date(date.getFullYear(), date.getMonth(), date.getDate(), 0, 0, 0, 0);
            const dayEnd = new Date(date.getFullYear(), date.getMonth(), date.getDate(), 23, 59, 59, 999);
            return getVisibleEvents().filter((e) => {
                const eStart = e.startDate;
                const eEnd = e.endDate || e.startDate;
                if (e.isAllDay) {
                    const eventStartDay = new Date(eStart.getFullYear(), eStart.getMonth(), eStart.getDate());
                    const eventEndDay = new Date(eEnd.getFullYear(), eEnd.getMonth(), eEnd.getDate());
                    const nextDayStart = new Date(date.getFullYear(), date.getMonth(), date.getDate() + 1);
                    return eventStartDay < nextDayStart && eventEndDay > dayStart;
                }
                if (eEnd <= eStart) return eStart >= dayStart && eStart <= dayEnd;
                return eStart <= dayEnd && eEnd > dayStart;
            });
        }

        function getEventBadgeStyle(event) {
            const colors = getEventBadgeColors(event);
            return `background-color: ${colors.background}; color: ${colors.text}; border: 1px solid ${colors.border};`;
        }

        function getEventBadgeColors(event) {
            const surface = getCssColorVariable("--color-surface-container", "#f0eeeb");
            const onSurface = getCssColorVariable("--color-on-surface", "#1f1f1e");
            if (isTaskEvent(event)) {
                const priorityColor = getTaskPriorityColor(event.priority);
                return createAccessibleEventPalette(priorityColor, surface, onSurface, {
                    completed: Boolean(event.completed),
                });
            }
            return createAccessibleEventPalette(
                getCalendarEventColor(event),
                surface,
                onSurface,
            );
        }

        return {
            getEventBadgeColors,
            getEventBadgeStyle,
            getEventElementAttributes,
            getEventsForDay,
        };
    }

    window.APStudyCalendarEventRender = { createCalendarEventRender };
})();
