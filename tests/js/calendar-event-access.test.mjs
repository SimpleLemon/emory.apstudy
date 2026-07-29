import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
globalThis.window = {};
globalThis.document = {
    addEventListener() {},
    body: { dataset: {} },
};

async function loadBrowserModule(relativePath) {
    const source = await readFile(path.join(repoRoot, relativePath), "utf8");
    const url = `data:text/javascript;base64,${Buffer.from(source).toString("base64")}#${relativePath}`;
    await import(url);
}

await loadBrowserModule("static/js/calendar/views/event-render.js");
await loadBrowserModule("static/js/calendar/views/week-view.js");
await loadBrowserModule("static/js/calendar/views/month-view.js");
await loadBrowserModule("static/js/calendar/views/agenda.js");
await loadBrowserModule("static/js/calendar/events/context-menu.js");

const escapeHtml = (value) => String(value)
    .replaceAll("&", "&amp;")
    .replaceAll('"', "&quot;");
const timedEvent = {
    id: "event-1",
    event_ref: "event:event-1",
    title: 'Office Hours & "Review"',
    startDate: new Date(2026, 6, 20, 13, 30),
    endDate: new Date(2026, 6, 20, 14, 30),
    isAllDay: false,
    source_type: "event",
};
const allDayEvent = {
    id: "event-2",
    event_ref: "event:event-2",
    uid: "all-day-2",
    title: "Registration Day",
    startDate: new Date(2026, 6, 21),
    endDate: new Date(2026, 6, 22),
    isAllDay: true,
    isMultiDay: false,
    spanDays: 1,
    source_type: "event",
};
const spanningTimedEvent = {
    id: "event-spanning",
    event_ref: "event:event-spanning",
    title: "TX > London",
    startDate: new Date(2026, 6, 20, 22),
    endDate: new Date(2026, 6, 22, 8),
    isAllDay: false,
    source_type: "event",
};

function createEventRenderer(readOnly = false, events = [timedEvent, allDayEvent]) {
    return window.APStudyCalendarEventRender.createCalendarEventRender({
        state: { public: { readOnly } },
        callbacks: {
            getCalendarEventColor: () => "#336699",
            getCalendarEventRef: (event) => event.event_ref,
            getEventCalendarLabel: () => "Personal Calendar",
            getVisibleEvents: () => events,
        },
        formatters: {
            adjustHexLuminance: (color) => color,
            escapeHtml,
            formatAllDayRange: () => "Jul 21 (All day)",
            formatTimedEventRange: () => "Jul 20, 1:30 PM - 2:30 PM",
            getTaskPriorityColor: () => "#336699",
            hexToRgba: () => "rgba(51, 102, 153, .2)",
            isTaskEvent: (event) => event.source_type === "task",
        },
    });
}

test("timed, all-day, task, and read-only events receive control semantics and useful names", () => {
    const editable = createEventRenderer(false);
    const readOnly = createEventRenderer(true);
    const task = { ...timedEvent, id: "task-event", event_ref: "task:42", source_type: "task", task_id: "42" };

    const timedAttributes = editable.getEventElementAttributes(timedEvent);
    assert.match(timedAttributes, /role="button"/);
    assert.match(timedAttributes, /tabindex="0"/);
    assert.match(timedAttributes, /aria-haspopup="menu"/);
    assert.match(timedAttributes, /Office Hours &amp; &quot;Review&quot;/);
    assert.match(timedAttributes, /Jul 20, 1:30 PM - 2:30 PM/);
    assert.match(timedAttributes, /Personal Calendar\. Open event\. More actions available\./);

    assert.match(editable.getEventElementAttributes(allDayEvent), /Jul 21 \(All day\)/);
    assert.match(editable.getEventElementAttributes(task), /Open task/);
    assert.match(readOnly.getEventElementAttributes(timedEvent), /View event/);
});

test("timed events ending at midnight do not occupy the following day", () => {
    const midnightEnd = {
        ...timedEvent,
        id: "event-midnight",
        event_ref: "event:event-midnight",
        startDate: new Date(2026, 6, 23, 22),
        endDate: new Date(2026, 6, 24, 0),
    };
    const renderer = createEventRenderer(false, [midnightEnd]);

    assert.equal(renderer.getEventsForDay(new Date(2026, 6, 23)).length, 1);
    assert.equal(renderer.getEventsForDay(new Date(2026, 6, 24)).length, 0);
});

test("weekly, monthly, mobile, and upcoming renderers preserve event control attributes", () => {
    const attributes = (event) => `role="button" aria-label="${escapeHtml(event.title)} accessible" data-event-ref="${event.event_ref}" tabindex="0"`;
    const visibleEvents = [timedEvent, allDayEvent, spanningTimedEvent];
    const upcomingStart = new Date(Date.now() + 86400000);
    const upcomingEvent = {
        ...timedEvent,
        id: "event-upcoming",
        event_ref: "event:event-upcoming",
        title: "Upcoming study session",
        startDate: upcomingStart,
        endDate: new Date(upcomingStart.getTime() + 3600000),
    };
    const eventsForDay = (day) => visibleEvents.filter((event) => event.startDate.toDateString() === day.toDateString());
    const sharedFormatters = {
        dateToDayIndex(date, days) {
            return Math.round((new Date(date.getFullYear(), date.getMonth(), date.getDate()) - days[0]) / 86400000);
        },
        escapeHtml,
        getStartOfWeek(date) {
            const start = new Date(date);
            start.setDate(start.getDate() - start.getDay());
            start.setHours(0, 0, 0, 0);
            return start;
        },
        isToday: () => false,
    };

    const week = window.APStudyCalendarWeekView.createCalendarWeekView({
        state: { anchorDate: new Date(2026, 6, 20) },
        constants: { allDayMinHeightPx: 44, hourHeightPx: 60, weekMinimumDayWidthPx: 100, weekdays: ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"] },
        callbacks: {
            getEventBadgeColors: () => ({
                background: "#234",
                text: "#fff",
                border: "#8cf",
                indicator: "#8cf",
            }),
            getEventBadgeStyle: () => "",
            getEventElementAttributes: attributes,
            getEventsForDay: eventsForDay,
            getVisibleEvents: () => visibleEvents,
        },
        formatters: {
            ...sharedFormatters,
            formatHourLabel: (hour) => `${hour}:00`,
            isTaskEvent: (event) => event.source_type === "task",
            layoutTimedEvents: (events) => events.map((event) => ({
                ...event,
                layoutStartMinutes: 810,
                layoutDurationMinutes: 60,
                layoutLane: 0,
                layoutLaneCount: 1,
            })),
        },
    }).buildWeekViewHtml();
    assert.match(week, /aria-label="Office Hours &amp; &quot;Review&quot; accessible"/);
    assert.match(week, /aria-label="Registration Day accessible"/);
    assert.doesNotMatch(week, /calendar-week-spanning-event/);
    assert.match(week, /TX > London/);

    const month = window.APStudyCalendarMonthView.createCalendarMonthView({
        state: { anchorDate: new Date(2026, 6, 20) },
        constants: { weekdays: ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"] },
        callbacks: {
            buildEventChip: (event) => `<div ${attributes(event)}></div>`,
            getEventBadgeColors: () => ({
                background: "#234",
                text: "#fff",
                border: "#8cf",
                indicator: "#8cf",
            }),
            getEventBadgeStyle: () => "",
            getEventElementAttributes: attributes,
            getEventsForDay: eventsForDay,
            getVisibleEvents: () => visibleEvents,
        },
        formatters: {
            ...sharedFormatters,
            formatDateKey: (date) => date.toISOString().slice(0, 10),
            formatMonthGridDayLabel: (date) => String(date.getDate()),
        },
    }).buildMonthViewHtml();
    assert.match(month, /aria-label="Office Hours &amp; &quot;Review&quot; accessible"/);
    assert.match(month, /aria-label="Registration Day accessible"/);
    assert.match(month, /calendar-month-spanning-event/);
    assert.equal((month.match(/data-event-ref="event:event-spanning"/g) || []).length, 1);

    const agendaRenderer = window.APStudyCalendarAgenda.createCalendarAgenda({
        state: { anchorDate: new Date(2026, 6, 20), view: "week", loadingDashboard: false, ui: { expandedUpcomingRefs: new Set() } },
        callbacks: {
            getCalendarEventColor: () => "#336699",
            getCalendarEventLabel: () => "Personal Calendar",
            getCalendarEventRef: (event) => event.event_ref,
            getEventBadgeColors: () => ({ indicator: "#123456" }),
            getEventElementAttributes: attributes,
            getEventsForDay: eventsForDay,
            getVisibleEvents: () => [...visibleEvents, upcomingEvent],
        },
        formatters: {
            ...sharedFormatters,
            formatAllDayRange: () => "Jul 21 (All day)",
            formatMultilineText: escapeHtml,
            formatTimedEventRange: () => "Jul 20, 1:30 PM - 2:30 PM",
            getAccent: () => ({ bar: "", tag: "" }),
            getUrgencyLabel: () => "Today",
            getUrgencyLabelAllDay: () => "Tomorrow",
            isTaskEvent: (event) => event.source_type === "task",
        },
    });
    const agenda = agendaRenderer.buildMobileCalendarAgendaHtml();
    assert.match(agenda, /<article role="button" aria-label="Office Hours &amp; &quot;Review&quot; accessible"/);
    assert.match(agenda, /<article role="button" aria-label="Registration Day accessible"/);

    const upcoming = agendaRenderer.buildUpcomingAgendaHtml();
    assert.match(upcoming, /calendar-upcoming-agenda/);
    assert.match(upcoming, /calendar-upcoming-date/);
    assert.match(upcoming, /<article role="button" aria-label="Upcoming study session accessible"/);
    assert.match(upcoming, /Personal Calendar/);
});

test("event menus expose only valid actions for local, imported, simulated, task, and read-only events", () => {
    const getLabels = (event, readOnly = false) => window.APStudyCalendarEventMenu
        .getEventMenuItems({ event, readOnly })
        .map((item) => item.label);

    assert.deepEqual(getLabels(timedEvent), ["View / Edit Event", "Duplicate Event", "Delete Event"]);
    assert.deepEqual(getLabels({ ...timedEvent, event_ref: "feed:abc" }), [
        "View / Edit Event",
        "Duplicate Event",
        "Hide Imported Event",
    ]);
    assert.deepEqual(getLabels({ ...timedEvent, source: "simulated" }), ["View Event", "Duplicate Event"]);
    assert.deepEqual(getLabels({ ...timedEvent, source_type: "task", task_id: "42" }), ["Open Task"]);
    assert.deepEqual(getLabels(timedEvent, true), ["View Event"]);
});

test("Enter and Space perform the primary event action", () => {
    const calls = [];
    const eventElement = {
        isConnected: true,
        closest: () => eventElement,
        getAttribute(name) {
            if (name === "data-event-id") return "event-1";
            if (name === "data-event-ref") return "event:event-1";
            return null;
        },
    };
    const root = { contains: (candidate) => candidate === eventElement };
    document.querySelector = () => root;
    window.getCalendarEventByRef = () => timedEvent;
    window.openCalendarEventForm = (options) => calls.push(options);

    let prevented = false;
    window.APStudyCalendarEventMenu.handleEventKeyDown({
        key: "Enter",
        target: eventElement,
        preventDefault() { prevented = true; },
    });
    assert.equal(prevented, true);
    assert.equal(calls[0].mode, "edit");
    assert.equal(calls[0].opener, eventElement);

    document.body.dataset.calendarReadonly = "true";
    window.APStudyCalendarEventMenu.handleEventKeyDown({
        key: " ",
        target: eventElement,
        preventDefault() {},
    });
    assert.equal(calls[1].mode, "view");
    delete document.body.dataset.calendarReadonly;
});

test("context-menu keyboard contract traps focus and restores the exact anchor", async () => {
    const source = await readFile(path.join(repoRoot, "static/js/calendar/events/context-menu.js"), "utf8");

    assert.match(source, /event\.shiftKey && event\.key === "F10"/);
    assert.match(source, /event\.key === "ContextMenu"/);
    assert.match(source, /event\.key === "Enter" \|\| event\.key === " "/);
    assert.match(source, /event\.key === "Tab" && !event\.shiftKey/);
    assert.match(source, /event\.key === "Tab" && event\.shiftKey/);
    assert.match(source, /closeMenu\(\{ restoreFocus: true \}\)/);
    assert.match(source, /anchorEl\.focus\?\.\(\{ preventScroll: true \}\)/);
});
