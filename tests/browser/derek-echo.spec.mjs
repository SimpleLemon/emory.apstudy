import { expect, test } from "playwright/test";

const clockMarkup = `
  <section id="clock" data-echo-clock style="width:420px;height:280px">
    <div class="echo-clock-face" aria-hidden="true">
      ${["h1", "h2", "m1", "m2"].map((key) => `
        <span class="echo-flip" data-echo-digit-slot="${key}">
          <span class="echo-flip-face echo-flip-face--under"><span data-echo-flip-under>0</span></span>
          <span class="echo-flip-half echo-flip-half--top"><span data-echo-flip-top>0</span></span>
          <span class="echo-flip-half echo-flip-half--bottom"><span data-echo-flip-bottom>0</span></span>
        </span>
      `).join("")}
    </div>
    <span data-echo-clock-label></span>
    <p class="echo-date" data-echo-date></p>
  </section>
`;

const modalMarkup = `
  <div data-echo-event-modal hidden>
    <button type="button" class="echo-modal-backdrop" data-echo-event-close aria-label="Close event details"></button>
    <div role="dialog" aria-modal="true" aria-labelledby="event-title">
      <h2 id="event-title" data-echo-event-title>Event details</h2>
      <button type="button" data-echo-event-close>Close</button>
      <div data-echo-event-details></div>
    </div>
  </div>
`;

async function loadEchoHarness(page, baseURL) {
  await page.goto(`${baseURL}/static/js/derek/echo-utils.js`);
  await page.setContent(`<!doctype html><html><head>
    <link rel="stylesheet" href="${baseURL}/static/css/global.css">
    <link rel="stylesheet" href="${baseURL}/static/css/derek-echo.css">
  </head><body>${clockMarkup}
    <div id="calendar-shell" style="width:720px;height:420px">
      <div id="calendar" data-echo-calendar class="echo-week" style="height:100%"></div>
    </div>
    <button type="button" data-echo-next aria-label="Show upcoming events"></button>
    <div data-echo-agenda></div>
    ${modalMarkup}
  </body></html>`);
  await page.evaluate(async () => {
    window.__echoUtils = await import("/static/js/derek/echo-utils.js");
    window.__echoClock = await import("/static/js/derek/echo-clock.js");
    window.__echoCalendar = await import("/static/js/derek/echo-calendar.js");
    window.__echoDetails = await import("/static/js/derek/echo-event-details.js");
  });
}

function localEventPayload() {
  return {
    calendar_sources: [{ id: "personal", display_name: "Personal" }],
    events: [{
      event_ref: "user:event-1",
      title: "Office hours",
      start: "2026-07-25T09:00:00",
      end: "2026-07-25T10:00:00",
      calendar_id: "personal",
      course: "ISOM 351",
      type: "Study session",
      description: "Bring your notes.",
    }, {
      event_ref: "user:event-2",
      title: "All day planning",
      start: "2026-07-26",
      end: "2026-07-27",
      is_all_day: true,
      calendar_id: "personal",
    }],
  };
}

function upcomingEventPayload() {
  return {
    calendar_sources: [{ id: "personal", display_name: "Personal" }],
    events: [{
      event_ref: "user:event-today",
      title: "Today event",
      start: "2026-07-25T09:00:00",
      end: "2026-07-25T10:00:00",
      calendar_id: "personal",
    }, {
      event_ref: "user:event-tomorrow",
      title: "Tomorrow event",
      start: "2026-07-26T09:00:00",
      end: "2026-07-26T10:00:00",
      calendar_id: "personal",
    }, {
      event_ref: "user:event-three-days",
      title: "Three day event",
      start: "2026-07-28T09:00:00",
      end: "2026-07-28T10:00:00",
      calendar_id: "personal",
    }],
  };
}

test("Echo clock flips only changed digits and honors reduced motion", async ({ page, baseURL }) => {
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await loadEchoHarness(page, baseURL);
  const result = await page.evaluate(() => {
    const controller = window.__echoClock.createEchoClock({ root: document.querySelector("[data-echo-clock]") });
    controller.update(new Date(2026, 6, 25, 9, 8), { animate: false });
    controller.update(new Date(2026, 6, 25, 9, 9), { animate: true });
    return {
      flipping: [...document.querySelectorAll("[data-echo-digit-slot].is-flipping")]
        .map((node) => node.dataset.echoDigitSlot),
      label: document.querySelector("[data-echo-clock-label]").textContent,
      dateFits: (() => {
        const date = document.querySelector("[data-echo-date]");
        return date.scrollWidth <= date.clientWidth + 1;
      })(),
    };
  });
  expect(result.flipping).toEqual(["m2"]);
  expect(result.label).toContain("9:09");
  expect(result.dateFits).toBe(true);

  await page.emulateMedia({ reducedMotion: "reduce" });
  const reduced = await page.evaluate(() => {
    const controller = window.__echoClock.createEchoClock({ root: document.querySelector("[data-echo-clock]") });
    controller.update(new Date(2026, 6, 25, 9, 9), { animate: false });
    controller.update(new Date(2026, 6, 25, 9, 10), { animate: true });
    return document.querySelectorAll("[data-echo-digit-slot].is-flipping").length;
  });
  expect(reduced).toBe(0);
});

test("Echo calendar centers the quarter-hour marker and opens read-only event details", async ({ page, baseURL }) => {
  await loadEchoHarness(page, baseURL);
  await page.evaluate((payload) => {
    const now = new Date(2026, 6, 25, 9, 17);
    const events = window.__echoCalendar.normalizeEvents(payload);
    const modal = window.__echoDetails.createEchoEventDetails({
      modal: document.querySelector("[data-echo-event-modal]"),
      titleNode: document.querySelector("[data-echo-event-title]"),
      bodyNode: document.querySelector("[data-echo-event-details]"),
    });
    window.__echoCalendar.renderTwoDayCalendar(document.querySelector("[data-echo-calendar]"), events, {
      now,
      onEventActivate: (eventKey, trigger) => modal.open(events.find((event) => event.eventKey === eventKey), trigger),
    });
    window.__firstScroll = window.__echoCalendar.centerNowMarker(document.querySelector("[data-echo-calendar]"), now);
    const next = new Date(2026, 6, 25, 9, 30);
    window.__echoCalendar.updateNowMarker(document.querySelector("[data-echo-calendar]"), next);
    window.__secondScroll = window.__echoCalendar.centerNowMarker(document.querySelector("[data-echo-calendar]"), next);
  }, localEventPayload());

  await expect(page.locator("[data-echo-now-marker]")).toHaveAttribute("style", /translateY\(342px\)/);
  expect(await page.evaluate(() => window.__secondScroll > window.__firstScroll)).toBe(true);

  const event = page.getByRole("button", { name: /Office hours/ });
  await event.click();
  await expect(page.locator("[data-echo-event-modal]")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Office hours" })).toBeVisible();
  await expect(page.locator("[data-echo-event-details]")).toContainText("ISOM 351");
  await expect(page.locator("[data-echo-event-details]")).toContainText("Bring your notes.");
  await expect(page.getByRole("button", { name: /edit/i })).toHaveCount(0);

  await page.keyboard.press("Escape");
  await expect(page.locator("[data-echo-event-modal]")).toBeHidden();
  await expect(event).toBeFocused();

  await event.press("Enter");
  await expect(page.locator("[data-echo-event-modal]")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(event).toBeFocused();

  await event.press("Space");
  await expect(page.locator("[data-echo-event-modal]")).toBeVisible();
  await page.locator("[data-echo-event-modal] .echo-modal-backdrop").click({ position: { x: 5, y: 5 } });
  await expect(page.locator("[data-echo-event-modal]")).toBeHidden();
  await expect(event).toBeFocused();

  const allDayEvent = page.getByRole("button", { name: /All day planning/ });
  await allDayEvent.click();
  await expect(page.getByRole("heading", { name: "All day planning" })).toBeVisible();
  await expect(page.locator("[data-echo-event-details]")).toContainText("All day");
});

test("Echo upcoming tile centers its next event and agenda spans seven days", async ({ page, baseURL }) => {
  await loadEchoHarness(page, baseURL);
  await page.evaluate((payload) => {
    const now = new Date(2026, 6, 25, 9, 17);
    const events = window.__echoCalendar.normalizeEvents(payload);
    window.__echoCalendar.renderNextEvent(document.querySelector("[data-echo-next]"), events[0], now);
    window.__echoCalendar.renderAgendaList(document.querySelector("[data-echo-agenda]"), events, now);
  }, upcomingEventPayload());

  await expect(page.locator("[data-echo-next]")).toContainText("Today (7/25)");
  await expect(page.locator("[data-echo-next]")).toContainText("Today event");
  await expect(page.locator("[data-echo-agenda]")).toContainText("Today (7/25)");
  await expect(page.locator("[data-echo-agenda]")).toContainText("Tomorrow (7/26)");
  await expect(page.locator("[data-echo-agenda]")).toContainText("In 3 Days (7/28)");
  await expect(page.locator("[data-echo-agenda]")).not.toContainText("In 4 Days");

  const tileLayout = await page.locator("[data-echo-next]").evaluate((tile) => {
    const row = tile.querySelector(".echo-next-row");
    const line = tile.querySelector(".echo-next-color");
    return {
      rowAlign: getComputedStyle(row).alignItems,
      lineWidth: line.getBoundingClientRect().width,
      tileWidth: tile.getBoundingClientRect().width,
    };
  });
  expect(tileLayout.rowAlign).toBe("center");
  expect(tileLayout.lineWidth).toBeGreaterThan(tileLayout.tileWidth * 0.9);
});
