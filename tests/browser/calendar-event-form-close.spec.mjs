import { expect, test } from "playwright/test";

test("closing an event editor removes its hit area and returns control to the calendar", async ({ page, baseURL }) => {
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto(`${baseURL}/static/js/calendar/events/event-form.js`);
    await page.setContent(`<!doctype html><html><head>
        <link rel="stylesheet" href="${baseURL}/static/css/global.css">
    </head><body>
        <button id="outside" type="button">Calendar control</button>
    </body></html>`);
    await page.addScriptTag({ url: `${baseURL}/static/js/calendar/events/event-form.js`, type: "module" });

    await page.evaluate(() => {
        window.openCalendarEventForm({
            mode: "edit",
            data: {
                id: "event-1",
                title: "Office hours",
                start: "2026-07-20T14:00:00",
                end: "2026-07-20T15:00:00",
            },
        });
    });

    await expect(page.locator(".calendar-event-dialog")).toBeVisible();
    await page.locator(".calendar-event-backdrop").click({ position: { x: 8, y: 8 } });
    await expect(page.locator(".calendar-event-modal")).toHaveCount(0);

    await page.locator("#outside").click();
    await expect(page.locator("#outside")).toBeFocused();
    expect(errors).toEqual([]);
});

test("upcoming view keeps the period-navigation slot stable", async ({ page, baseURL }) => {
    await page.setContent(`<!doctype html><html><head>
        <link rel="stylesheet" href="${baseURL}/static/css/index.css">
    </head><body>
        <div id="calendar-period-controls" class="calendar-period-controls">
            <span>divider</span><button type="button">Previous</button><button type="button">Next</button>
        </div>
    </body></html>`);

    const visibleWidth = await page.locator("#calendar-period-controls").evaluate((element) => element.getBoundingClientRect().width);
    await page.locator("#calendar-period-controls").evaluate((element) => element.hidden = true);
    const hiddenState = await page.locator("#calendar-period-controls").evaluate((element) => ({
        width: element.getBoundingClientRect().width,
        visibility: getComputedStyle(element).visibility,
        pointerEvents: getComputedStyle(element).pointerEvents,
    }));

    expect(hiddenState.width).toBe(visibleWidth);
    expect(hiddenState.visibility).toBe("hidden");
    expect(hiddenState.pointerEvents).toBe("none");
});
