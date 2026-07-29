import { expect, test } from "playwright/test";

test("week rendering gives isolated groups full width and overlapping groups deterministic lanes", async ({ page, baseURL }) => {
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto(`${baseURL}/static/js/calendar/utils.js`);
    await page.setContent(`<!doctype html><html><body><div id="calendar"></div></body></html>`);
    await page.evaluate(async () => {
        await import("/static/js/calendar/utils.js");
        await import("/static/js/calendar/views/week-view.js");
        const day = new Date(2026, 6, 19);
        const at = (hour, minute = 0) => new Date(2026, 6, 19, hour, minute);
        const events = [
            { id: "before", title: "Before", startDate: at(8), endDate: at(9) },
            { id: "pair-a", title: "Pair A", startDate: at(10), endDate: at(11) },
            { id: "pair-b", title: "Pair B", startDate: at(10, 30), endDate: at(11, 30) },
            { id: "after", title: "After", startDate: at(12), endDate: at(13) },
            { id: "triple-a", title: "Triple A", startDate: at(14), endDate: at(16) },
            { id: "triple-b", title: "Triple B", startDate: at(14), endDate: at(15, 30) },
            { id: "triple-c", title: "Triple C", startDate: at(14), endDate: at(15) },
        ];
        const view = window.APStudyCalendarWeekView.createCalendarWeekView({
            state: { anchorDate: day },
            constants: { allDayMinHeightPx: 44, hourHeightPx: 60, weekMinimumDayWidthPx: 148, weekdays: ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"] },
            callbacks: {
                getEventBadgeStyle: () => "background:#fff;color:#000;border-color:#000;",
                getEventElementAttributes: (event) => `data-event-id="${event.id}"`,
                getEventsForDay: (date) => date.getDate() === day.getDate() ? events : [],
                getVisibleEvents: () => events,
            },
            formatters: {
                ...window.APStudyCalendarUtils,
                escapeHtml: String,
                formatHourLabel: (hour) => String(hour),
                isTaskEvent: () => false,
                isToday: () => false,
            },
        });
        document.getElementById("calendar").innerHTML = view.buildWeekViewHtml();
    });

    const widths = await page.locator("[data-event-id]").evaluateAll((elements) => Object.fromEntries(
        elements.map((element) => [element.dataset.eventId, element.getBoundingClientRect().width]),
    ));
    expect(widths.before).toBeGreaterThan(widths["pair-a"] * 1.8);
    expect(widths.after).toBeGreaterThan(widths["pair-b"] * 1.8);
    expect(Math.abs(widths["pair-a"] - widths["pair-b"])).toBeLessThan(1);
    expect(widths["pair-a"]).toBeGreaterThan(widths["triple-a"] * 1.4);
    expect(Math.max(widths["triple-a"], widths["triple-b"], widths["triple-c"])
        - Math.min(widths["triple-a"], widths["triple-b"], widths["triple-c"])).toBeLessThan(1);
    expect(errors).toEqual([]);
});

test("month rendering turns a timed cross-day event into one compact spanning band", async ({ page, baseURL }) => {
    await page.goto(`${baseURL}/static/js/calendar/utils.js`);
    await page.setContent(`<!doctype html><html><head><link rel="stylesheet" href="${baseURL}/static/css/tailwind.css"></head><body><div id="calendar"></div></body></html>`);
    await page.evaluate(async () => {
        await import("/static/js/calendar/utils.js");
        await import("/static/js/calendar/views/month-view.js");
        const spanning = {
            id: "spanning",
            title: "TX > London",
            startDate: new Date(2026, 6, 20, 22),
            endDate: new Date(2026, 6, 22, 8),
            isAllDay: false,
        };
        const sameDay = {
            id: "same-day",
            title: "Same day",
            startDate: new Date(2026, 6, 20, 10),
            endDate: new Date(2026, 6, 20, 11),
            isAllDay: false,
        };
        const midnightEnd = {
            id: "midnight-end",
            title: "Ends at midnight",
            startDate: new Date(2026, 6, 23, 22),
            endDate: new Date(2026, 6, 24, 0),
            isAllDay: false,
        };
        const events = [spanning, sameDay, midnightEnd];
        const overlapsDay = (event, date) => (
            event.startDate <= new Date(date.getFullYear(), date.getMonth(), date.getDate(), 23, 59, 59, 999)
            && event.endDate > new Date(date.getFullYear(), date.getMonth(), date.getDate())
        );
        const view = window.APStudyCalendarMonthView.createCalendarMonthView({
            state: { anchorDate: new Date(2026, 6, 20) },
            constants: { weekdays: ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"] },
            callbacks: {
                getEventBadgeColors: () => ({ background: "#234", text: "#fff", border: "#8cf", indicator: "#8cf" }),
                getEventBadgeStyle: () => "background:#fff;color:#000;border-color:#000;",
                getEventElementAttributes: (event) => `data-event-id="${event.id}"`,
                getEventsForDay: (date) => events.filter((event) => overlapsDay(event, date)),
                getVisibleEvents: () => events,
                buildEventChip: (event) => `<div data-month-chip-id="${event.id}">${event.title}</div>`,
            },
            formatters: {
                ...window.APStudyCalendarUtils,
                escapeHtml: String,
                formatDateKey: (date) => date.toISOString().slice(0, 10),
                formatMonthGridDayLabel: (date) => String(date.getDate()),
                isToday: () => false,
            },
        });
        document.getElementById("calendar").innerHTML = view.buildMonthViewHtml();
    });

    const spanning = page.locator('[data-event-id="spanning"]');
    await expect(spanning).toHaveCount(1);
    await expect(spanning).toHaveClass(/calendar-month-spanning-event-shell/);
    await expect(spanning).toHaveAttribute("style", /grid-column:2 \/ 5/);
    await expect(spanning.locator(".calendar-month-spanning-event")).toHaveText("TX > London");
    await expect(page.locator('[data-month-chip-id="spanning"]')).toHaveCount(0);
    await expect(page.locator('[data-month-chip-id="same-day"]')).toHaveCount(1);
    await expect(page.locator('[data-event-id="midnight-end"]')).toHaveCount(0);
    await expect(page.locator('[data-month-chip-id="midnight-end"]')).toHaveCount(1);
    expect(await spanning.evaluate((element) => element.getBoundingClientRect().height)).toBeLessThanOrEqual(27);
});

test("week rendering keeps cross-day bands at compact all-day density", async ({ page, baseURL }) => {
    await page.goto(`${baseURL}/static/js/calendar/utils.js`);
    await page.setContent(`<!doctype html><html><head><link rel="stylesheet" href="${baseURL}/static/css/tailwind.css"></head><body><div id="calendar"></div></body></html>`);
    await page.evaluate(async () => {
        await import("/static/js/calendar/utils.js");
        await import("/static/js/calendar/views/week-view.js");
        const weekStart = new Date(2026, 6, 19);
        const spanning = {
            id: "spanning",
            title: "TX > London",
            startDate: new Date(2026, 6, 20, 22),
            endDate: new Date(2026, 6, 22, 8),
            isAllDay: false,
        };
        const midnightEnd = {
            id: "midnight-end",
            title: "Ends at midnight",
            startDate: new Date(2026, 6, 23, 22),
            endDate: new Date(2026, 6, 24, 0),
            isAllDay: false,
        };
        const events = [spanning, midnightEnd];
        const view = window.APStudyCalendarWeekView.createCalendarWeekView({
            state: { anchorDate: weekStart },
            constants: { allDayMinHeightPx: 44, hourHeightPx: 60, weekMinimumDayWidthPx: 148, weekdays: ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"] },
            callbacks: {
                getEventBadgeStyle: () => "background:#234;color:#fff;border-color:#8cf;",
                getEventElementAttributes: (event) => `data-event-id="${event.id}"`,
                getEventsForDay: (date) => events.filter((event) => (
                    event.startDate <= new Date(date.getFullYear(), date.getMonth(), date.getDate(), 23, 59, 59, 999)
                    && event.endDate > new Date(date.getFullYear(), date.getMonth(), date.getDate())
                )),
                getVisibleEvents: () => events,
            },
            formatters: {
                ...window.APStudyCalendarUtils,
                escapeHtml: String,
                formatHourLabel: (hour) => String(hour),
                isTaskEvent: () => false,
                isToday: () => false,
            },
        });
        document.getElementById("calendar").innerHTML = view.buildWeekViewHtml();
    });

    const spanning = page.locator('[data-event-id="spanning"]');
    await expect(spanning).toHaveCount(1);
    await expect(spanning).not.toHaveClass(/calendar-week-spanning-event-shell/);
    await expect(spanning).toHaveAttribute("style", /grid-column: 3 \/ 6/);
    expect(await spanning.evaluate((element) => element.getBoundingClientRect().height)).toBeLessThan(32);
    const midnightEnd = page.locator('[data-event-id="midnight-end"]');
    await expect(midnightEnd).toHaveCount(1);
    await expect(midnightEnd).toHaveClass(/absolute/);
    await expect(midnightEnd).toHaveAttribute("style", /height:120px/);
});
