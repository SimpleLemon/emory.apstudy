(function () {
    function createCalendarData({
        lifecycle = null,
        dataAdapter = null,
        state,
        constants,
        buildSimulatedMeetingEvents,
        ensureSimulatedCalendarPreference,
        getEventCalendarKey,
        getStartOfWeek,
        hydrateSelectedSimulatedSections,
        initCalendarState,
        loadCalendarState,
        queueCalendarPreferenceSave,
        render,
        writeCalendarStateToStorage,
    }) {
        const {
            calendarBufferDays,
            eventsCacheKey,
            simulatedCalendarName,
            taskCalendarId,
            taskCalendarName,
            toggleRefreshDelayMs,
        } = constants;

        function requestSignal() {
            return lifecycle?.trackAbortController?.() || new AbortController();
        }

        function parseEventDate(dateStr, isAllDay) {
            if (!dateStr) return new Date();
            if (isAllDay && /^\d{4}-\d{2}-\d{2}$/.test(dateStr)) {
                const parts = dateStr.split("-");
                return new Date(
                    parseInt(parts[0], 10),
                    parseInt(parts[1], 10) - 1,
                    parseInt(parts[2], 10),
                    0, 0, 0, 0
                );
            }
            return new Date(dateStr);
        }

        function readEventsCache() {
            const raw = localStorage.getItem(eventsCacheKey);
            if (!raw) return null;
            try {
                return JSON.parse(raw);
            } catch (err) {
                console.warn("Ignoring invalid cached calendar events:", err);
                localStorage.removeItem(eventsCacheKey);
                return null;
            }
        }

        function parseCachedRange(range) {
            if (!range?.start || !range?.end) return null;
            const start = new Date(range.start);
            const end = new Date(range.end);
            if (Number.isNaN(start?.getTime()) || Number.isNaN(end?.getTime())) return null;
            return { start, end };
        }

        function writeEventsCache(payload, range) {
            try {
                localStorage.setItem(eventsCacheKey, JSON.stringify({
                    cached_at: Date.now(),
                    range: range ? { start: range.start.toISOString(), end: range.end.toISOString() } : null,
                    payload,
                }));
            } catch (err) {
                console.warn("Failed to cache calendar events:", err);
            }
        }

        function normalizeEventsList(events) {
            return Array.isArray(events)
                ? events
                        .filter((e) => e.start)
                        .map((e) => {
                            const isAllDay = Boolean(e.is_all_day);
                            return {
                                ...e,
                                startDate: parseEventDate(e.start, isAllDay),
                                endDate: e.end ? parseEventDate(e.end, isAllDay) : parseEventDate(e.start, isAllDay),
                                isAllDay,
                                isMultiDay: Boolean(e.is_multi_day),
                                spanDays: e.span_days || 1,
                            };
                        })
                        .sort((a, b) => a.startDate - b.startDate)
                : [];
        }

        function eventOverlapsRange(event, rangeStart, rangeEnd) {
            const start = event.startDate;
            const end = event.endDate || event.startDate;
            if (!start || !end) return false;
            return start < rangeEnd && end > rangeStart;
        }

        function replaceEventsInRange(existingEvents, newEvents, range) {
            if (!range) return newEvents;
            const filtered = existingEvents.filter((event) => !eventOverlapsRange(event, range.start, range.end));
            return filtered.concat(newEvents).sort((a, b) => a.startDate - b.startDate);
        }

        function mergeRanges(a, b) {
            if (!a) return b;
            if (!b) return a;
            return {
                start: a.start < b.start ? a.start : b.start,
                end: a.end > b.end ? a.end : b.end,
            };
        }

        function rangeCovers(a, b) {
            if (!a || !b) return false;
            return a.start <= b.start && a.end >= b.end;
        }

        function getBufferedRange(baseRange) {
            const start = new Date(baseRange.start);
            const end = new Date(baseRange.end);
            start.setDate(start.getDate() - calendarBufferDays);
            end.setDate(end.getDate() + calendarBufferDays);
            return { start, end };
        }

        function buildEventsUrl(range) {
            const baseUrl = state.public.readOnly && state.public.shareCode
                ? `/api/calendar/share/${encodeURIComponent(state.public.shareCode)}/events`
                : "/api/calendar/events";
            if (!range) return baseUrl;
            const params = new URLSearchParams({
                start: range.start.toISOString(),
                end: range.end.toISOString(),
            });
            return `${baseUrl}?${params.toString()}`;
        }

        function getRangeKey(range) {
            return `${range.start.toISOString()}|${range.end.toISOString()}`;
        }

        async function fetchEventsForRange(range) {
            if (lifecycle?.isDisposed?.()) return { events: [] };
            const controller = requestSignal();
            try {
                if (dataAdapter?.loadRange) {
                    return await dataAdapter.loadRange({
                        range,
                        readOnly: state.public.readOnly,
                        shareCode: state.public.shareCode,
                        signal: controller.signal,
                    });
                }
                const res = await fetch(buildEventsUrl(range), { signal: controller.signal });
                if (!res.ok) throw new Error("Unable to fetch calendar events");
                return res.json();
            } finally {
                lifecycle?.releaseAbortController?.(controller);
            }
        }

        async function applyEventsPayload(payload, options = {}) {
            if (lifecycle?.isDisposed?.()) return;
            const range = options.range || null;
            state.calendarSources = Array.isArray(payload?.calendar_sources) ? payload.calendar_sources : [];
            const newEvents = normalizeEventsList(payload?.events);
            const mergeRange = Boolean(options.mergeRange);
            state.events = mergeRange
                ? replaceEventsInRange(state.events, newEvents, range)
                : newEvents;

            if (payload) {
                if (typeof payload.feed_configured === "boolean") {
                    state.feedConfigured = payload.feed_configured;
                }
                state.eventsMeta = {
                    lastFetched: payload.last_fetched || null,
                    refreshIntervalMinutes: payload.refresh_interval_minutes || null,
                };
            }

            if (range) {
                state.loadedRange = mergeRanges(state.loadedRange, range);
            }

            initCalendarState();
            if (!state.public.readOnly) {
                await loadCalendarState();
                ensureSimulatedCalendarPreference();
            }
            render();
            if (!state.public.readOnly && options.shouldHydrate) {
                await hydrateSelectedSimulatedSections();
            }
            if (!state.public.readOnly && !options.fromCache) {
                writeEventsCache(payload, range);
            }
        }

        function shouldRefreshInBackground(payload) {
            if (!payload?.feed_configured) return false;
            const interval = Number(payload.refresh_interval_minutes || 0);
            if (!interval) return false;
            if (!payload.last_fetched) return true;
            const lastFetched = new Date(payload.last_fetched);
            if (Number.isNaN(lastFetched.getTime())) return false;
            return Date.now() - lastFetched.getTime() > interval * 60 * 1000;
        }

        async function maybeRefreshIfStale(payload, range) {
            if (state.public.readOnly || lifecycle?.isDisposed?.()) return;
            if (!shouldRefreshInBackground(payload) || state.refreshInFlight) return;
            state.refreshInFlight = true;
            try {
                await refreshCalendarFeed();
                const refreshed = await fetchEventsForRange(range);
                await applyEventsPayload(refreshed, { range, mergeRange: true });
            } catch (err) {
                console.error("Background refresh failed:", err);
            } finally {
                state.refreshInFlight = false;
            }
        }

        async function ensureEventsForRange(range) {
            if (lifecycle?.isDisposed?.()) return;
            if (state.loadedRange && rangeCovers(state.loadedRange, range)) return;
            const key = getRangeKey(range);
            if (state.pendingRanges.has(key)) return;
            state.pendingRanges.add(key);
            try {
                const payload = await fetchEventsForRange(range);
                await applyEventsPayload(payload, { range, mergeRange: true });
            } catch (err) {
                console.error("Failed to load additional calendar range:", err);
            } finally {
                state.pendingRanges.delete(key);
            }
        }

        async function runManualRefresh(range) {
            if (state.public.readOnly || lifecycle?.isDisposed?.()) return;
            if (state.refreshInFlight) return;
            state.refreshInFlight = true;
            try {
                await refreshCalendarFeed();
                const payload = await fetchEventsForRange(range);
                await applyEventsPayload(payload, { range, mergeRange: true });
            } catch (err) {
                console.error("Manual refresh failed:", err);
            } finally {
                state.refreshInFlight = false;
            }
        }

        async function loadCalendarData() {
            if (lifecycle?.isDisposed?.()) return;
            state.loadingDashboard = true;
            render();

            const cached = state.public.readOnly ? null : readEventsCache();
            const cachedRange = state.public.readOnly ? null : parseCachedRange(cached?.range);
            if (!state.public.readOnly && cached?.payload) {
                state.loadingDashboard = false;
                await applyEventsPayload(cached.payload, {
                    range: cachedRange,
                    mergeRange: false,
                    fromCache: true,
                    shouldHydrate: false,
                });
            }

            const desiredRange = getBufferedRange(getCurrentRenderRange());
            try {
                const payload = await fetchEventsForRange(desiredRange);
                state.loadingDashboard = false;
                await applyEventsPayload(payload, {
                    range: desiredRange,
                    mergeRange: true,
                    shouldHydrate: true,
                });
                void maybeRefreshIfStale(payload, desiredRange);
            } catch (err) {
                if (!cached?.payload) {
                    state.feedConfigured = false;
                    state.events = [];
                    initCalendarState();
                    if (!state.public.readOnly) {
                        await loadCalendarState();
                        ensureSimulatedCalendarPreference();
                    }
                    render();
                    if (!state.public.readOnly) {
                        hydrateSelectedSimulatedSections();
                    }
                }
                console.error(err);
            } finally {
                state.loadingDashboard = false;
                render();
            }
        }

        async function refreshCalendarFeed() {
            if (lifecycle?.isDisposed?.()) return false;
            let controller = null;
            try {
                controller = requestSignal();
                const res = dataAdapter?.refresh
                    ? await dataAdapter.refresh({ signal: controller.signal })
                    : await fetch("/api/calendar/refresh", { method: "POST", signal: controller.signal });
                return res.ok;
            } catch (err) {
                console.warn("Calendar feed refresh request failed:", err);
            } finally {
                lifecycle?.releaseAbortController?.(controller);
            }
            return false;
        }

        function getVisibleEvents() {
            const baseEvents = state.events.filter((e) => {
                const cal = getEventCalendarKey(e);
                return state.calendars[cal]?.visible !== false;
            });
            if (state.public.readOnly || state.calendars[simulatedCalendarName]?.visible === false || state.courses.selectedSectionIds.size === 0) {
                return baseEvents;
            }
            const renderRange = getCurrentRenderRange();
            const simulatedEvents = buildSimulatedMeetingEvents(renderRange.start, renderRange.end);
            return baseEvents.concat(simulatedEvents);
        }

        function getCurrentRenderRange() {
            if (state.view === "upcoming") {
                const start = new Date();
                start.setHours(0, 0, 0, 0);
                const end = new Date(start);
                end.setDate(end.getDate() + 30);
                end.setHours(23, 59, 59, 999);
                return { start, end };
            }
            if (state.view === "month") {
                const year = state.anchorDate.getFullYear();
                const month = state.anchorDate.getMonth();
                const monthStart = new Date(year, month, 1);
                const gridStart = new Date(monthStart);
                gridStart.setDate(monthStart.getDate() - monthStart.getDay());
                const gridEnd = new Date(gridStart);
                gridEnd.setDate(gridStart.getDate() + 41);
                gridEnd.setHours(23, 59, 59, 999);
                return { start: gridStart, end: gridEnd };
            }
            const weekStart = getStartOfWeek(state.anchorDate);
            const weekEnd = new Date(weekStart);
            weekEnd.setDate(weekStart.getDate() + 6);
            weekEnd.setHours(23, 59, 59, 999);
            return { start: weekStart, end: weekEnd };
        }

        function getEventCalendarColor(event) {
            if (event?.color) return event.color;
            const cal = getEventCalendarKey(event);
            return state.calendars[cal]?.color || "#6366f1";
        }

        function isExternalFeedCalendar(calendarName, calendarData) {
            if (!calendarData) return false;
            if (calendarName === simulatedCalendarName) return false;
            if (calendarName === taskCalendarId || calendarName === taskCalendarName) return false;
            if (calendarData.kind === "canvas" || calendarData.kind === "external") return true;
            if (calendarName === "canvas" || String(calendarName).startsWith("feed:")) return true;
            return false;
        }

        function scheduleCalendarToggleRefresh(delayMs = toggleRefreshDelayMs) {
            if (state.ui.toggleRefreshTimer) {
                lifecycle?.clearTimeout?.(state.ui.toggleRefreshTimer);
            }
            const schedule = lifecycle?.setTimeout || window.setTimeout.bind(window);
            state.ui.toggleRefreshTimer = schedule(() => {
                state.ui.toggleRefreshTimer = null;
                void runCalendarToggleRefresh();
            }, delayMs);
        }

        function queueCalendarVisibilityRefresh(calendarName, visible) {
            if (state.public.readOnly) return;
            if (!visible) return;
            const calendar = state.calendars[calendarName];
            if (!calendar) return;
            if (isExternalFeedCalendar(calendarName, calendar)) {
                state.ui.toggleRefreshNeedsFeed = true;
                state.ui.toggleRefreshNeedsEvents = true;
            } else {
                state.ui.toggleRefreshNeedsEvents = true;
            }
            scheduleCalendarToggleRefresh();
        }

        async function runCalendarToggleRefresh() {
            if (state.public.readOnly) return;
            const needsFeed = state.ui.toggleRefreshNeedsFeed;
            const needsEvents = state.ui.toggleRefreshNeedsEvents;
            state.ui.toggleRefreshNeedsFeed = false;
            state.ui.toggleRefreshNeedsEvents = false;

            if (!needsFeed && !needsEvents) return;
            if (state.refreshInFlight) {
                state.ui.toggleRefreshNeedsFeed = state.ui.toggleRefreshNeedsFeed || needsFeed;
                state.ui.toggleRefreshNeedsEvents = state.ui.toggleRefreshNeedsEvents || needsEvents;
                scheduleCalendarToggleRefresh();
                return;
            }

            const range = getBufferedRange(getCurrentRenderRange());
            if (!needsFeed && needsEvents && state.loadedRange && rangeCovers(state.loadedRange, range)) {
                render();
                return;
            }

            try {
                if (needsFeed) {
                    state.refreshInFlight = true;
                    await refreshCalendarFeed();
                }
                if (needsEvents) {
                    const payload = await fetchEventsForRange(range);
                    await applyEventsPayload(payload, { range, mergeRange: true });
                }
            } catch (err) {
                console.error("Calendar refresh after toggle failed:", err);
            } finally {
                if (needsFeed) {
                    state.refreshInFlight = false;
                }
            }
        }

        function toggleCalendarVisibility(calendarName) {
            const calendar = state.calendars[calendarName];
            if (!calendar) return;
            calendar.visible = !calendar.visible;
            if (!state.public.readOnly) {
                writeCalendarStateToStorage();
                queueCalendarPreferenceSave(calendarName);
            }
            queueCalendarVisibilityRefresh(calendarName, calendar.visible);
            render();
        }

        function setCalendarColor(calendarName, color) {
            if (state.public.readOnly) return;
            if (state.calendars[calendarName]) {
                state.calendars[calendarName].color = color;
                writeCalendarStateToStorage();
                queueCalendarPreferenceSave(calendarName);
                render();
            }
        }

        return {
            applyEventsPayload,
            fetchEventsForRange,
            getBufferedRange,
            getCurrentRenderRange,
            getEventCalendarColor,
            getVisibleEvents,
            loadCalendarData,
            refreshCalendarFeed,
            runManualRefresh,
            setCalendarColor,
            toggleCalendarVisibility,
            ensureEventsForRange,
        };
    }

    window.APStudyCalendarData = { createCalendarData };
})();
