import { getSafeCanvasSourceUrl } from "./capabilities.js";

function responseJson(response) {
    return response.json().catch(() => ({}));
}

export function createCalendarDataAdapter(overrides = {}) {
        const runtimeWindow = overrides.window || globalThis.window || globalThis;
        const fetchImplementation = overrides.fetch || runtimeWindow.fetch;
        const request = (url, options) => fetchImplementation.call(runtimeWindow, url, options);
        const defaultAdapter = {
            async loadRange({ range, readOnly = false, shareCode = "", signal } = {}) {
                const baseUrl = readOnly && shareCode
                    ? `/api/calendar/share/${encodeURIComponent(shareCode)}/events`
                    : "/api/calendar/events";
                const params = range
                    ? `?${new URLSearchParams({
                        start: range.start.toISOString(),
                        end: range.end.toISOString(),
                    })}`
                    : "";
                const response = await request(`${baseUrl}${params}`, { signal });
                if (!response.ok) throw new Error("Unable to fetch calendar events");
                return responseJson(response);
            },
            async loadPreferences({ signal } = {}) {
                const response = await request("/api/calendar/preferences", { signal });
                return { response, payload: await responseJson(response) };
            },
            async savePreferences({ endpoint, body, signal } = {}) {
                const response = await request(endpoint || "/api/calendar/preferences/batch", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(body),
                    signal,
                });
                return { response, payload: await responseJson(response) };
            },
            async refresh({ signal } = {}) {
                return request("/api/calendar/refresh", { method: "POST", signal });
            },
            async loadShares({ signal } = {}) {
                const response = await request("/api/calendar/shares", { signal });
                return { response, payload: await responseJson(response) };
            },
            async saveShare({ path, method = "POST", body, signal } = {}) {
                const response = await request(path, {
                    method,
                    headers: { "Content-Type": "application/json" },
                    body: body === undefined ? undefined : JSON.stringify(body),
                    signal,
                });
                return { response, payload: await responseJson(response) };
            },
            async createEvent({ payload, signal } = {}) {
                const response = await request("/api/calendar/events", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(payload),
                    signal,
                });
                return { response, payload: await responseJson(response) };
            },
            async updateEvent({ eventId, payload, signal } = {}) {
                const response = await request(`/api/calendar/events/${encodeURIComponent(eventId)}`, {
                    method: "PUT",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(payload),
                    signal,
                });
                return { response, payload: await responseJson(response) };
            },
            async overrideEvent({ payload, signal } = {}) {
                const response = await request("/api/calendar/event-overrides", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(payload),
                    signal,
                });
                return { response, payload: await responseJson(response) };
            },
            async deleteEvent({ eventId, signal } = {}) {
                return request(`/api/calendar/events/${encodeURIComponent(eventId)}`, {
                    method: "DELETE",
                    signal,
                });
            },
            async hideEvent({ eventRef, signal } = {}) {
                return request("/api/calendar/event-overrides/hide", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ event_ref: eventRef }),
                    signal,
                });
            },
            async saveSource({ path, method = "POST", body, signal } = {}) {
                const response = await request(path, {
                    method,
                    headers: { "Content-Type": "application/json" },
                    body: body === undefined ? undefined : JSON.stringify(body),
                    signal,
                });
                return { response, payload: await responseJson(response) };
            },
            async loadCourses({ signal } = {}) {
                const [termsResponse, sectionsResponse] = await Promise.all([
                    request("/api/atlas/terms", { signal }),
                    request("/api/atlas/sections?include_cancelled=1", { signal }),
                ]);
                return {
                    termsResponse,
                    sectionsResponse,
                    termsPayload: await responseJson(termsResponse),
                    sectionsPayload: await responseJson(sectionsResponse),
                };
            },
            async loadCourseSectionsById({ sectionIds = [], signal } = {}) {
                const response = await request("/api/atlas/sections/by-id", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ section_ids: sectionIds, include_cancelled: true }),
                    signal,
                });
                return { response, payload: await responseJson(response) };
            },
            async setCanvasRouting({ sourceId, state, destinationCalendarId, fallbackCalendarId, signal } = {}) {
                const response = await request(
                    `/api/extension/calendar/sources/${encodeURIComponent(sourceId || "")}/routing`,
                    {
                        method: "PUT",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({
                            state,
                            destination_calendar_id: destinationCalendarId || null,
                            fallback_calendar_id: fallbackCalendarId || null,
                        }),
                        signal,
                    },
                );
                return { response, payload: await responseJson(response), ok: response.ok };
            },
            async setDisplayOverride({ eventRef, calendarId, signal } = {}) {
                const response = await request("/api/calendar/event-overrides", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ event_ref: eventRef, calendar_id: calendarId }),
                    signal,
                });
                return { response, payload: await responseJson(response), ok: response.ok };
            },
            async retryWriteback() {
                return {
                    ok: false,
                    state: "unsupported",
                    reason: "The active Canvas adapter does not expose a retry operation.",
                };
            },
            async openSafeSourceUrl({ url } = {}) {
                const safeUrl = getSafeCanvasSourceUrl(url);
                if (!safeUrl || typeof runtimeWindow.open !== "function") {
                    return {
                        ok: false,
                        state: "unsupported",
                        reason: "Only a credential-free HTTPS Canvas origin may be opened.",
                    };
                }
                const opened = runtimeWindow.open(safeUrl, "_blank", "noopener,noreferrer");
                return { ok: Boolean(opened), url: safeUrl };
            },
        };

        const adapter = { ...defaultAdapter, ...(overrides || {}) };
        adapter.actionSupport = {
            routeDisplayOverride: true,
            retryWriteback: false,
            openSourceUrl: true,
            ...(overrides.actionSupport || {}),
        };
        return adapter;
}

if (typeof window !== "undefined") {
    window.APStudyCalendarAdapter = {
        ...(window.APStudyCalendarAdapter || {}),
        createCalendarDataAdapter,
    };
}
