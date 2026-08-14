(function () {
    function createCalendarCourses({
        root = document,
        lifecycle = null,
        dataAdapter = null,
        state,
        constants,
        render,
        saveCalendarState,
        escapeHtml,
    }) {
        const {
            coursesSelectionStorageKey,
            coursesModalAnimationMs,
            simulatedCalendarName,
        } = constants;

        const query = (selector) => root?.querySelector?.(selector);
        const doc = root.ownerDocument || document;
        const view = doc.defaultView || window;
        const storage = view.localStorage || localStorage;
        const getActiveElement = () => doc.activeElement;

        function requestController() {
            return lifecycle?.trackAbortController?.() || new AbortController();
        }

        function initializeCourseSelectionsFromStorage() {
            const persistedSelections = loadSelectedCourseSectionIds();
            state.courses.selectedSectionIds = new Set(persistedSelections);
        }

        async function hydrateSelectedSimulatedSections() {
            if (!state.courses.selectedSectionIds.size) return;
            const missingIds = Array.from(state.courses.selectedSectionIds)
                .filter((id) => !state.courses.sectionsById[id]);
            if (!missingIds.length) {
                ensureSimulatedCalendarPreference();
                render();
                return;
            }
            const controller = requestController();
            try {
                const result = dataAdapter?.loadCourseSectionsById
                    ? await dataAdapter.loadCourseSectionsById({ sectionIds: missingIds, signal: controller.signal })
                    : { response: await fetch("/api/atlas/sections/by-id", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ section_ids: missingIds, include_cancelled: true }),
                        signal: controller.signal,
                    }) };
                const res = result.response || result;
                if (!res.ok) return;
                const payload = result.payload || await res.json();
                const sections = Array.isArray(payload.sections) ? payload.sections : [];
                const foundIds = new Set();
                for (const section of sections) {
                    const id = String(section.id || "");
                    if (!id) continue;
                    foundIds.add(id);
                    const searchBlob = [
                        section.course_title,
                        section.subject,
                        section.course_code,
                        section.instructor,
                        ...(Array.isArray(section.instructors_unique) ? section.instructors_unique : []),
                    ].join(" ").toLowerCase();
                    state.courses.sectionsById[id] = { ...section, searchBlob };
                }
                let selectionChanged = false;
                for (const id of missingIds) {
                    if (!foundIds.has(id) && state.courses.selectedSectionIds.has(id)) {
                        state.courses.selectedSectionIds.delete(id);
                        selectionChanged = true;
                    }
                }
                if (selectionChanged) {
                    saveSelectedCourseSectionIds();
                }
                ensureSimulatedCalendarPreference();
                render();
            } catch (err) {
                console.error("Failed to hydrate selected simulated sections:", err);
            } finally {
                lifecycle?.releaseAbortController?.(controller);
            }
        }

        function applyCoursesFiltersFromUrl() {
            const url = new URL(view.location.href);
            state.courses.searchQuery = (url.searchParams.get("search") || "").trim();
            state.courses.searchInput = state.courses.searchQuery;
            state.courses.termFilter = (url.searchParams.get("term") || "").trim();
        }

        function writeCourseFiltersToUrl() {
            const url = new URL(view.location.href);
            const search = state.courses.searchQuery.trim();
            const term = state.courses.termFilter.trim();
            if (search) {
                url.searchParams.set("search", search);
            } else {
                url.searchParams.delete("search");
            }
            if (term) {
                url.searchParams.set("term", term);
            } else {
                url.searchParams.delete("term");
            }
            view.history.replaceState({}, "", `${url.pathname}${url.search}${url.hash}`);
        }

        function formatTermLabel(term) {
            const parts = String(term || "").split("_");
            if (parts.length !== 2) return term || "Unknown";
            return `${parts[0]} ${parts[1]}`;
        }

        function openCoursesModal(trigger = getActiveElement()) {
            if (state.courses.modalOpen && !state.courses.isClosing) {
                closeCoursesModal();
                return;
            }
            state.courses.modalOpen = true;
            state.courses.isClosing = false;
            state.courses.animateOnOpen = true;
            state.courses.modalTriggerEl = trigger && typeof trigger.focus === "function" ? trigger : null;
            state.courses.searchInput = state.courses.searchQuery;
            state.courses.pinnedSectionIds = new Set(state.courses.selectedSectionIds);
            state.courses.showSelectedOnly = state.courses.pinnedSectionIds.size > 0;
            applyCourseFilters();
            root.classList.add("overflow-hidden");
            renderCoursesModal();
            setCoursesModalBackgroundInert(true);
            if (!state.courses.indexLoaded && !state.courses.loading) {
                void loadCoursesIndex();
            }
        }

        function closeCoursesModal() {
            if (!state.courses.modalOpen || state.courses.isClosing) return;
            const overlay = query("#courses-modal-overlay");
            const panel = query("#courses-modal-panel");
            state.courses.isClosing = true;
            if (overlay) {
                overlay.classList.add("opacity-0", "pointer-events-none");
            }
            if (panel) {
                panel.classList.add("-translate-y-3", "opacity-0");
            }
            const schedule = lifecycle?.setTimeout || view.setTimeout.bind(view);
            schedule(() => {
                const trigger = state.courses.modalTriggerEl;
                state.courses.pinnedSectionIds = new Set();
                state.courses.showSelectedOnly = false;
                state.courses.modalOpen = false;
                state.courses.isClosing = false;
                state.courses.modalTriggerEl = null;
                root.classList.remove("overflow-hidden");
                renderCoursesModal();
                setCoursesModalBackgroundInert(false);
                if (trigger?.isConnected) trigger.focus({ preventScroll: true });
            }, coursesModalAnimationMs);
        }

        async function submitCoursesSearch() {
            state.courses.searchQuery = (state.courses.searchInput || "").trim();
            state.courses.showSelectedOnly = false;
            if (!state.courses.indexLoaded) {
                await loadCoursesIndex();
                return;
            }
            applyCourseFilters();
            writeCourseFiltersToUrl();
            renderCoursesModal();
        }

        function loadSelectedCourseSectionIds() {
            const raw = storage.getItem(coursesSelectionStorageKey);
            if (!raw) return [];
            try {
                const parsed = JSON.parse(raw);
                return Array.isArray(parsed) ? parsed.map((item) => String(item)) : [];
            } catch (err) {
                console.warn("Ignoring invalid saved course selections:", err);
                storage.removeItem(coursesSelectionStorageKey);
                return [];
            }
        }

        function saveSelectedCourseSectionIds() {
            storage.setItem(
                coursesSelectionStorageKey,
                JSON.stringify(Array.from(state.courses.selectedSectionIds))
            );
        }

        async function loadCoursesIndex() {
            if (state.courses.loading) return;
            if (state.courses.indexLoaded) {
                applyCourseFilters();
                writeCourseFiltersToUrl();
                renderCoursesModal();
                return;
            }
            state.courses.loading = true;
            state.courses.error = "";
            renderCoursesModal();
            const controller = requestController();
            try {
                const result = dataAdapter?.loadCourses
                    ? await dataAdapter.loadCourses({ signal: controller.signal })
                    : await (async () => {
                        const [termsRes, sectionsRes] = await Promise.all([
                            fetch("/api/atlas/terms", { signal: controller.signal }),
                            fetch("/api/atlas/sections?include_cancelled=1", { signal: controller.signal }),
                        ]);
                        return {
                            termsResponse: termsRes,
                            sectionsResponse: sectionsRes,
                            termsPayload: await termsRes.json(),
                            sectionsPayload: await sectionsRes.json(),
                        };
                    })();
                const { termsResponse: termsRes, sectionsResponse: sectionsRes, termsPayload, sectionsPayload } = result;
                if (!termsRes.ok) throw new Error("Unable to load terms");
                if (!sectionsRes.ok) throw new Error("Unable to load sections");
                state.courses.terms = Array.isArray(termsPayload.terms) ? termsPayload.terms : [];
                state.courses.sections = Array.isArray(sectionsPayload.sections) ? sectionsPayload.sections : [];
                state.courses.indexLoaded = true;
                state.courses.sectionsById = {};
                for (const section of state.courses.sections) {
                    const id = String(section.id || "");
                    if (!id) continue;
                    const searchBlob = [
                        section.course_title,
                        section.subject,
                        section.course_code,
                        section.instructor,
                        ...(Array.isArray(section.instructors_unique) ? section.instructors_unique : []),
                    ].join(" ").toLowerCase();
                    state.courses.sectionsById[id] = { ...section, searchBlob };
                }
                const validTerms = new Set(state.courses.terms);
                if (state.courses.termFilter && !validTerms.has(state.courses.termFilter)) {
                    state.courses.termFilter = "";
                }
                const persistedSelections = loadSelectedCourseSectionIds();
                state.courses.selectedSectionIds = new Set(
                    persistedSelections.filter((id) => Boolean(state.courses.sectionsById[id]))
                );
                ensureSimulatedCalendarPreference();
                applyCourseFilters();
                writeCourseFiltersToUrl();
                saveSelectedCourseSectionIds();
                saveCalendarState();
            } catch (err) {
                console.error(err);
                state.courses.indexLoaded = false;
                state.courses.error = err?.message || "Failed to load courses";
            } finally {
                lifecycle?.releaseAbortController?.(controller);
                state.courses.loading = false;
                render();
            }
        }

        function applyCourseFilters() {
            if (state.courses.showSelectedOnly) {
                state.courses.filteredSectionIds = Array.from(state.courses.pinnedSectionIds);
                return;
            }
            if (!state.courses.indexLoaded) {
                state.courses.filteredSectionIds = [];
                return;
            }
            const term = state.courses.termFilter;
            const query = state.courses.searchQuery.trim().toLowerCase();
            const filteredIds = [];
            for (const section of state.courses.sections) {
                const id = String(section.id || "");
                if (!id) continue;
                if (term && section.term !== term) continue;
                const full = state.courses.sectionsById[id];
                if (query && (!full || !full.searchBlob.includes(query))) continue;
                filteredIds.push(id);
            }
            state.courses.filteredSectionIds = filteredIds;
        }

        function toggleCourseSectionSelection(sectionId) {
            const section = state.courses.sectionsById[sectionId];
            if (!section || section.is_cancelled) return;
            const removing = state.courses.selectedSectionIds.has(sectionId);
            if (removing) {
                state.courses.selectedSectionIds.delete(sectionId);
            } else {
                state.courses.selectedSectionIds.add(sectionId);
            }
            ensureSimulatedCalendarPreference();
            saveSelectedCourseSectionIds();
            saveCalendarState();
            render();
            if (removing) {
                window.APStudyUndo?.stage?.({
                    message: `${section.course_code || section.course_title || "Course"} removed from the simulated calendar.`,
                    restore: () => {
                        state.courses.selectedSectionIds.add(sectionId);
                        ensureSimulatedCalendarPreference();
                        saveSelectedCourseSectionIds();
                        saveCalendarState();
                        render();
                    },
                });
            }
        }

        function formatMeetingTime(timeToken) {
            const parsed = parseAtlasTimeToken(timeToken);
            if (!parsed) return "TBA";
            let hour = parsed.hour;
            const suffix = hour >= 12 ? "p" : "a";
            hour %= 12;
            if (hour === 0) hour = 12;
            return `${hour}:${String(parsed.minute).padStart(2, "0")}${suffix}`;
        }

        function parseDateOnlyToLocal(dateStr) {
            if (!/^\d{4}-\d{2}-\d{2}$/.test(String(dateStr || ""))) return null;
            const [year, month, day] = String(dateStr).split("-").map((value) => parseInt(value, 10));
            return new Date(year, month - 1, day, 0, 0, 0, 0);
        }

        function getSectionDateBounds(section) {
            const range = section?.date_range || {};
            const start = parseDateOnlyToLocal(range.start);
            const end = parseDateOnlyToLocal(range.end);
            return { start, end };
        }

        function formatSectionDateRange(section) {
            const bounds = getSectionDateBounds(section);
            if (!bounds.start || !bounds.end) return "N/A";
            const startStr = bounds.start.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
            const endStr = bounds.end.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
            return `${startStr} - ${endStr}`;
        }

        function formatSectionTypeLabel(typeValue) {
            const raw = String(typeValue || "").trim().toUpperCase();
            const known = {
                LEC: "LEC (Lecture)",
            };
            if (!raw) return "N/A";
            return known[raw] || raw;
        }

        const { renderCoursesModal, setCoursesModalBackgroundInert } = window.APStudyCalendarCourseModal.createCourseModalRenderer({
            root,
            lifecycle,
            state,
            escapeHtml,
            formatTermLabel,
            formatMeetingTime,
            formatSectionDateRange,
            formatSectionTypeLabel,
        });

        function ensureSimulatedCalendarPreference() {
            if (state.courses.selectedSectionIds.size === 0) return;
            if (state.calendars[simulatedCalendarName]) {
                state.calendars[simulatedCalendarName].visible = true;
                return;
            }
            const colorIndex = Object.keys(state.calendars).length % state.calendarColors.length;
            state.calendars[simulatedCalendarName] = {
                visible: true,
                color: state.calendarColors[colorIndex],
                colorIndex,
                label: simulatedCalendarName,
                defaultName: simulatedCalendarName,
                kind: "simulated",
                editable: false,
                sourceId: null,
                url: "",
                legacyNames: [simulatedCalendarName],
            };
        }

        function buildSimulatedMeetingEvents(startDate, endDate) {
            const dayTokenMap = {
                Sun: 0,
                Mon: 1,
                Tue: 2,
                Wed: 3,
                Thu: 4,
                Fri: 5,
                Sat: 6,
            };
            const events = [];
            for (const sectionId of state.courses.selectedSectionIds) {
                const section = state.courses.sectionsById[sectionId];
                if (!section) continue;
                const sectionBounds = getSectionDateBounds(section);
                const constrainedStart = sectionBounds.start && sectionBounds.start > startDate ? sectionBounds.start : startDate;
                const constrainedEnd = sectionBounds.end && sectionBounds.end < endDate ? sectionBounds.end : endDate;
                if (constrainedEnd < constrainedStart) continue;
                const meetings = Array.isArray(section.meetings) ? section.meetings : [];
                for (let cursor = new Date(constrainedStart); cursor <= constrainedEnd; cursor.setDate(cursor.getDate() + 1)) {
                    for (const meeting of meetings) {
                        const meetingDay = dayTokenMap[meeting.day];
                        if (meetingDay !== cursor.getDay()) continue;
                        const parsedStart = parseAtlasTimeToken(meeting.start);
                        const parsedEnd = parseAtlasTimeToken(meeting.end);
                        if (!parsedStart || !parsedEnd) continue;
                        const eventStart = new Date(cursor.getFullYear(), cursor.getMonth(), cursor.getDate(), parsedStart.hour, parsedStart.minute, 0, 0);
                        const eventEnd = new Date(cursor.getFullYear(), cursor.getMonth(), cursor.getDate(), parsedEnd.hour, parsedEnd.minute, 0, 0);
                        if (eventEnd <= eventStart) continue;
                        const uid = `${sectionId}|${cursor.toISOString().slice(0, 10)}|${meeting.start}|${meeting.end}`;
                        events.push({
                            uid,
                            title: `${section.course_code}`.trim(),
                            description: `${section.course_title} | Sec ${section.section_number || "?"} | ${section.instructor || "TBA"}`,
                            course: simulatedCalendarName,
                            type: "class-meeting",
                            is_all_day: false,
                            isAllDay: false,
                            isMultiDay: false,
                            spanDays: 1,
                            source: "simulated",
                            startDate: eventStart,
                            endDate: eventEnd,
                        });
                    }
                }
            }
            return events.sort((a, b) => a.startDate - b.startDate);
        }

        function parseAtlasTimeToken(timeToken) {
            const numeric = String(timeToken || "").replace(/\D/g, "");
            if (!numeric) return null;
            const padded = numeric.length >= 4 ? numeric.slice(-4) : numeric.padStart(4, "0");
            const hour = parseInt(padded.slice(0, 2), 10);
            const minute = parseInt(padded.slice(2, 4), 10);
            if (Number.isNaN(hour) || Number.isNaN(minute) || hour > 23 || minute > 59) {
                return null;
            }
            return { hour, minute };
        }

        return {
            initializeCourseSelectionsFromStorage,
            hydrateSelectedSimulatedSections,
            applyCoursesFiltersFromUrl,
            writeCourseFiltersToUrl,
            openCoursesModal,
            closeCoursesModal,
            submitCoursesSearch,
            applyCourseFilters,
            toggleCourseSectionSelection,
            renderCoursesModal,
            ensureSimulatedCalendarPreference,
            buildSimulatedMeetingEvents,
        };
    }

    window.APStudyCalendarCourses = { createCalendarCourses };
}());
