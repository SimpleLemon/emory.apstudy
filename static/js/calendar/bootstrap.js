(function () {
    function createCalendarBootstrap({
        state,
        callbacks,
    }) {
        const {
            applyCoursesFiltersFromUrl,
            ensureEventsForRange,
            getBufferedRange,
            getCalendarEventByRef,
            getCalendarOptionsForEventForm,
            getCurrentRenderRange,
            getDefaultCalendarIdForEventForm,
            initializeCourseSelectionsFromStorage,
            hydrateSavedCourses,
            loadCalendarData,
            refreshCalendarFeed,
            render,
            runManualRefresh,
            wireControls,
        } = callbacks;

        function readEventDeepLink() {
            if (state.public.readOnly) return null;
            const params = new URLSearchParams(window.location.search);
            const eventRef = params.get("event");
            if (!eventRef) return null;
            const dateValue = params.get("date");
            const dateMatch = String(dateValue || "").match(/^(\d{4})-(\d{2})-(\d{2})$/);
            if (dateMatch) {
                const candidate = new Date(
                    Number(dateMatch[1]),
                    Number(dateMatch[2]) - 1,
                    Number(dateMatch[3])
                );
                if (!Number.isNaN(candidate.getTime())) state.anchorDate = candidate;
            }
            return eventRef;
        }

        function clearEventDeepLink() {
            const url = new URL(window.location.href);
            url.searchParams.delete("event");
            url.searchParams.delete("date");
            window.history.replaceState({}, "", `${url.pathname}${url.search}${url.hash}`);
        }

        function openEventDeepLink(eventRef) {
            if (!eventRef) return;
            const calendarEvent = getCalendarEventByRef(eventRef);
            clearEventDeepLink();
            if (calendarEvent && window.APStudyCalendarEventMenu?.activateEvent?.(calendarEvent)) return;
            window.APStudyToast?.show?.({
                title: "Event not found",
                message: "That calendar event is no longer available.",
                type: "error",
            });
        }

        function register() {
            document.addEventListener("DOMContentLoaded", async () => {
                const eventRef = readEventDeepLink();
                if (!state.public.readOnly) {
                    applyCoursesFiltersFromUrl();
                    initializeCourseSelectionsFromStorage();
                    if (typeof hydrateSavedCourses === "function") {
                        await hydrateSavedCourses();
                    }
                }
                wireControls();
                await loadCalendarData();
                openEventDeepLink(eventRef);
            });
            window.state = state;
            window.render = render;
            window.loadCalendarData = loadCalendarData;
            window.refreshCalendarFeed = refreshCalendarFeed;
            window.ensureEventsForRange = ensureEventsForRange;
            window.runManualRefresh = runManualRefresh;
            window.getBufferedRangeForView = () => getBufferedRange(getCurrentRenderRange());
            window.getCalendarEventByRef = getCalendarEventByRef;
            window.getCalendarOptionsForEventForm = getCalendarOptionsForEventForm;
            window.getDefaultCalendarIdForEventForm = getDefaultCalendarIdForEventForm;
            window.getCalendarColorForEventForm = (calendarName) => state.calendars[calendarName]?.color || state.calendarColors[4] || "#0ea5e9";
            window.getStandardCalendarColors = () => [...state.calendarColors];
        }

        return { register };
    }

    window.APStudyCalendarBootstrap = { createCalendarBootstrap };
})();
