(function () {
    function createCalendarMenu({
        root = document,
        state,
        constants,
        callbacks,
        escapeHtml,
    }) {
        const {
            canvasCalendarName,
            canvasSourceId,
            simulatedCalendarName,
            taskCalendarId,
            taskCalendarName,
        } = constants;
        const {
            buildSimulatedMeetingEvents,
            getCalendarLabel,
            getEventCalendarKey,
            getStartOfWeek,
        } = callbacks;

        const query = (selector) => root?.querySelector?.(selector);

        function renderCalendarMenu() {
            const container = query("#calendar-menu");
            if (!container) return;
            if (!state.ui.calendarMenuOpen) {
                container.style.display = "none";
                container.innerHTML = "";
                query("#calendar-toggle-menu")?.setAttribute("aria-expanded", "false");
                return;
            }
            container.innerHTML = buildCalendarMenuHtml();
            container.style.display = "block";
            query("#calendar-toggle-menu")?.setAttribute("aria-expanded", "true");
        }

        function buildCalendarMenuHtml() {
            const calendars = Object.entries(state.calendars).sort(([, a], [, b]) => getCalendarLabelFromData(a).localeCompare(getCalendarLabelFromData(b)));
            const showSimulated = state.courses.selectedSectionIds.size > 0;
            const notice = state.ui.preferenceNotice
                ? `<div class="calendar-source-notice" role="status">${escapeHtml(state.ui.preferenceNotice)}</div>`
                : "";
            const buildRows = (items) => items.map(([cal, data]) => {
                const checked = data.visible ? "checked" : "";
                const label = getCalendarLabel(cal);
                const eventCount = getCalendarEventCount(cal);
                const kindLabel = data.kind === "local" ? "Local" : (data.editable ? "Feed" : "");
                const meta = `${eventCount} event${eventCount === 1 ? "" : "s"}${kindLabel ? ` · ${kindLabel}` : ""}`;
                const feedStatus = String(data.status || "");
                const showFeedWarning = feedStatus === "quarantined" || feedStatus === "failing";
                const warningText = data.lastErrorMessage
                    || (feedStatus === "quarantined"
                        ? "This calendar feed was paused after repeated fetch failures."
                        : "This calendar feed failed to refresh.");
                const warning = showFeedWarning ? `
                        <div class="calendar-source-warning" role="status" title="${escapeHtml(warningText)}">
                            <svg class="calendar-source-warning-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
                                <path fill="currentColor" d="M1 21h22L12 2 1 21zm12-3h-2v-2h2v2zm0-4h-2v-4h2v4z"/>
                            </svg>
                            <span class="calendar-source-warning-text">${escapeHtml(warningText)}</span>
                        </div>
                ` : "";
                const moreButton = state.public.readOnly ? "" : `
                        <button type="button"
                            class="js-calendar-more calendar-source-more"
                            data-calendar-name="${escapeHtml(cal)}"
                            aria-label="Calendar options">
                            <span class="material-symbols-outlined calendar-source-more-icon" style="font-variation-settings:'FILL' 1,'wght' 700,'GRAD' 0,'opsz' 24;" aria-hidden="true">more_horiz</span>
                        </button>
                `;
                return `
                    <div class="calendar-source-row">
                        <label class="calendar-source-label">
                            <input type="checkbox" ${checked} data-native-control
                                class="js-calendar-checkbox calendar-source-checkbox"
                                data-calendar-name="${escapeHtml(cal)}"
                                style="background-color:${data.color};">
                            <span class="calendar-source-text">
                                <span class="calendar-source-name">${escapeHtml(label)}</span>
                                <span class="calendar-source-meta">${escapeHtml(meta)}</span>
                                ${warning}
                            </span>
                        </label>
                        ${moreButton}
                    </div>
                `;
            }).join("");
            const nestCalendars = calendars.filter(([name]) => {
                if (name === canvasSourceId || name === canvasCalendarName) return true;
                if (name === taskCalendarId || name === taskCalendarName) return true;
                if (name === simulatedCalendarName) return showSimulated;
                return false;
            });
            const otherCalendars = calendars.filter(([name]) => {
                if (name === canvasSourceId || name === canvasCalendarName) return false;
                if (name === taskCalendarId || name === taskCalendarName) return false;
                if (name === simulatedCalendarName) return false;
                return true;
            });
            const nestSection = `
                <div class="px-3 pt-3 pb-1 text-xs uppercase tracking-[0.08em] text-on-surface-variant font-semibold">Nest</div>
                ${buildRows(nestCalendars)}
            `;
            const otherSection = `
                <div class="h-px bg-outline-variant/25 my-1"></div>
                <div class="calendar-source-section-head">
                    <span>Other</span>
                    ${state.public.readOnly ? "" : `<button type="button" class="js-calendar-add-source calendar-source-add" aria-label="Add other calendar">
                        <span class="material-symbols-outlined calendar-source-add-icon" aria-hidden="true">add</span>
                    </button>`}
                </div>
                ${otherCalendars.length ? buildRows(otherCalendars) : '<div class="px-3 py-2 text-sm text-on-surface-variant">No other calendars</div>'}
            `;
            return `
                <div class="calendar-source-menu absolute top-full right-0 mt-2">
                    ${notice}
                    ${nestSection}
                    ${otherSection}
                </div>
            `;
        }

        function getCalendarLabelFromData(data) {
            return data?.label || data?.defaultName || "";
        }

        function getCurrentViewCountRange() {
            if (state.view === "upcoming") {
                const start = new Date();
                start.setHours(0, 0, 0, 0);
                const end = new Date(start);
                end.setDate(end.getDate() + 31);
                return { start, end };
            }
            if (state.view === "month") {
                const year = state.anchorDate.getFullYear();
                const month = state.anchorDate.getMonth();
                return {
                    start: new Date(year, month, 1, 0, 0, 0, 0),
                    end: new Date(year, month + 1, 1, 0, 0, 0, 0),
                };
            }
            const start = getStartOfWeek(state.anchorDate);
            const end = new Date(start);
            end.setDate(start.getDate() + 7);
            return { start, end };
        }

        function eventOverlapsCountRange(event, range) {
            const start = event.startDate;
            const end = event.endDate || event.startDate;
            if (!start || !end) return false;
            if (event.isAllDay) {
                const eventStartDay = new Date(start.getFullYear(), start.getMonth(), start.getDate());
                const eventEndDay = new Date(end.getFullYear(), end.getMonth(), end.getDate());
                return eventStartDay < range.end && eventEndDay > range.start;
            }
            return start < range.end && end > range.start;
        }

        function getCountableEventsForCurrentView() {
            const range = getCurrentViewCountRange();
            const events = [...state.events];
            if (!state.public.readOnly && state.courses.selectedSectionIds.size > 0) {
                events.push(...buildSimulatedMeetingEvents(range.start, range.end));
            }
            return events.filter((event) => eventOverlapsCountRange(event, range));
        }

        function getCalendarEventCount(calendarName) {
            return getCountableEventsForCurrentView().filter((event) => getEventCalendarKey(event) === calendarName).length;
        }

        return {
            getCalendarEventCount,
            getCalendarLabelFromData,
            getCurrentViewCountRange,
            renderCalendarMenu,
        };
    }

    window.APStudyCalendarMenu = { createCalendarMenu };
})();
