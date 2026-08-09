(function () {
    function createCalendarCore({
        state,
        constants,
        callbacks,
    }) {
        const {
            defaultLocalCalendarId,
            defaultLocalCalendarName,
            localSourcePrefix,
            simulatedCalendarName,
        } = constants;
        const {
            buildSimulatedMeetingEvents,
            getCurrentViewCountRange,
        } = callbacks;

        function initCalendarState() {
            const calendars = {};
            const addCalendar = (key, options = {}) => {
                if (!key || calendars[key]) return;
                const colorIndex = Object.keys(calendars).length % state.calendarColors.length;
                calendars[key] = {
                    visible: true,
                    color: options.color || options.color_hex || state.calendarColors[colorIndex],
                    colorIndex,
                    label: options.label || key,
                    defaultName: options.defaultName || options.label || key,
                    url: options.url || "",
                    kind: options.kind || "local",
                    editable: Boolean(options.editable),
                    sourceId: options.sourceId || null,
                    legacyNames: Array.isArray(options.legacyNames) ? options.legacyNames.filter(Boolean) : [],
                    status: options.status || "",
                    lastErrorMessage: options.lastErrorMessage || "",
                };
            };
            for (const source of state.calendarSources) {
                const sourceId = source.id || "";
                addCalendar(sourceId, {
                    label: source.display_name || source.default_name || sourceId,
                    defaultName: source.default_name || source.display_name || sourceId,
                    url: source.url || "",
                    color: source.color_hex || source.color || "",
                    kind: source.kind || "external",
                    editable: source.editable !== false,
                    sourceId,
                    legacyNames: [source.default_name, ...(Array.isArray(source.legacy_names) ? source.legacy_names : [])],
                    status: source.status || "",
                    lastErrorMessage: source.last_error_message || "",
                });
            }
            for (const event of state.events) {
                const cal = getEventCalendarKey(event);
                const fallbackLabel = cal === defaultLocalCalendarId ? defaultLocalCalendarName : (event.course || "Other");
                addCalendar(cal, {
                    label: fallbackLabel,
                    defaultName: fallbackLabel,
                    legacyNames: [fallbackLabel],
                });
            }
            if (!state.public.readOnly && state.courses.selectedSectionIds.size > 0 && !calendars[simulatedCalendarName]) {
                addCalendar(simulatedCalendarName, {
                    label: simulatedCalendarName,
                    defaultName: simulatedCalendarName,
                    legacyNames: [simulatedCalendarName],
                });
            }
            state.calendars = calendars;
        }

        function getCalendarLabel(calendarName) {
            return state.calendars[calendarName]?.label || calendarName || "Other";
        }

        function isLocalCalendar(calendarName) {
            return String(calendarName || "").startsWith(localSourcePrefix);
        }

        function getCalendarOptionsForEventForm() {
            const entries = Object.entries(state.calendars)
                .filter(([name]) => name !== simulatedCalendarName)
                .sort(([, a], [, b]) => getCalendarLabelFromData(a).localeCompare(getCalendarLabelFromData(b)));
            if (!entries.some(([name]) => name === defaultLocalCalendarId)) {
                entries.unshift([
                    defaultLocalCalendarId,
                    {
                        label: defaultLocalCalendarName,
                        defaultName: defaultLocalCalendarName,
                        color: state.calendarColors[4] || "#0ea5e9",
                        kind: "local",
                        editable: true,
                        sourceId: defaultLocalCalendarId,
                    },
                ]);
            }
            return entries.map(([id, data]) => ({
                id,
                label: getCalendarLabelFromData(data) || id,
                color: data.color || state.calendarColors[4] || "#0ea5e9",
                kind: data.kind || (isLocalCalendar(id) ? "local" : "external"),
            }));
        }

        function getDefaultCalendarIdForEventForm() {
            const localVisible = Object.entries(state.calendars).find(([name, data]) => isLocalCalendar(name) && data.visible !== false);
            if (localVisible) return localVisible[0];
            const localAny = Object.keys(state.calendars).find((name) => isLocalCalendar(name));
            return localAny || defaultLocalCalendarId;
        }

        function getCalendarEventRef(event) {
            if (!event) return "";
            return event.event_ref || (event.id ? `user:${event.id}` : "") || (event.uid ? `legacy:${event.uid}` : "");
        }

        function getEventsForCurrentContextLookup() {
            const range = getCurrentViewCountRange();
            const events = [...state.events];
            if (!state.public.readOnly && state.courses.selectedSectionIds.size > 0) {
                events.push(...buildSimulatedMeetingEvents(range.start, range.end));
            }
            return events;
        }

        function getCalendarEventByRef(eventRef) {
            if (!eventRef) return null;
            return getEventsForCurrentContextLookup().find((event) => getCalendarEventRef(event) === eventRef) || null;
        }

        function getEventCalendarKey(event) {
            if (event?.source_type === "user" || (event?.id && !event?.uid)) {
                return event.calendar_id || defaultLocalCalendarId;
            }
            return event.calendar_id || event.course || "Other";
        }

        function getEventCalendarLabel(event) {
            return getCalendarLabel(getEventCalendarKey(event));
        }

        function getSavedCalendarInfo(saved, key) {
            if (!saved || !key) return null;
            if (saved[key]) return saved[key];
            const legacyNames = state.calendars[key]?.legacyNames || [];
            for (const legacyName of legacyNames) {
                if (saved[legacyName]) return saved[legacyName];
            }
            return null;
        }

        function getCalendarLabelFromData(data) {
            return data?.label || data?.defaultName || "";
        }

        return {
            getCalendarEventByRef,
            getCalendarEventRef,
            getCalendarLabel,
            getCalendarOptionsForEventForm,
            getDefaultCalendarIdForEventForm,
            getEventCalendarKey,
            getEventCalendarLabel,
            getSavedCalendarInfo,
            initCalendarState,
            isLocalCalendar,
        };
    }

    window.APStudyCalendarCore = { createCalendarCore };
})();
