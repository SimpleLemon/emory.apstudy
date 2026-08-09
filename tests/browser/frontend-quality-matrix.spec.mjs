import { expect, test } from "playwright/test";

const viewportCases = [
  { width: 320, height: 720, colorScheme: "light" },
  { width: 360, height: 780, colorScheme: "dark", reducedMotion: "reduce" },
  { width: 768, height: 900, colorScheme: "light" },
  { width: 1024, height: 768, colorScheme: "dark" },
  { width: 1440, height: 900, colorScheme: "light" },
];

for (const viewport of viewportCases) {
  test(`landing remains error-free and overflow-free at ${viewport.width}px`, async ({ page, baseURL }) => {
    const pageErrors = [];
    const consoleErrors = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));
    page.on("console", (message) => { if (message.type() === "error") consoleErrors.push(message.text()); });
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await page.emulateMedia({ colorScheme: viewport.colorScheme, reducedMotion: viewport.reducedMotion || "no-preference" });
    await page.goto(`${baseURL}/`, { waitUntil: "networkidle" });
    await expect(page.locator("main")).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1)).toBe(true);
    expect(await page.locator("img").evaluateAll((images) => images.filter((image) => image.complete && image.naturalWidth === 0).map((image) => image.currentSrc))).toEqual([]);
    expect(pageErrors).toEqual([]);
    expect(consoleErrors).toEqual([]);
  });
}

test("landing tolerates 200 percent text sizing at mobile width", async ({ page, baseURL }) => {
  const pageErrors = [];
  const consoleErrors = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  page.on("console", (message) => { if (message.type() === "error") consoleErrors.push(message.text()); });
  await page.setViewportSize({ width: 360, height: 780 });
  await page.goto(`${baseURL}/`, { waitUntil: "networkidle" });
  await page.evaluate(() => { document.documentElement.style.fontSize = "200%"; });
  await expect(page.locator("main")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1)).toBe(true);
  expect(pageErrors).toEqual([]);
  expect(consoleErrors).toEqual([]);
});

test("landing calendar demo binds the shared escapeHtml primitive", async ({ page, baseURL }) => {
  const pageErrors = [];
  const consoleErrors = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  page.on("console", (message) => { if (message.type() === "error") consoleErrors.push(message.text()); });
  await page.goto(`${baseURL}/`, { waitUntil: "networkidle" });
  await page.getByRole("tab", { name: "Calendar" }).click();
  await expect(page.locator('[data-landing-calendar-events] .demo-week-event')).not.toHaveCount(0);
  await expect(page.locator('[data-landing-calendar-agenda]')).toContainText("BIOL 141");
  expect(pageErrors).toEqual([]);
  expect(consoleErrors).toEqual([]);
});

test("computed calendar event colors retain AA contrast in light and dark themes", async ({ page, baseURL }) => {
  await page.setContent(`<!doctype html><html><body><div id="events"></div><script src="${baseURL}/static/js/core/ui-primitives.js"></script><script src="${baseURL}/static/js/calendar/utils.js"></script></body></html>`);
  const results = await page.evaluate(() => {
    const accents = ["#000000", "#ffffff", "#777777", "#ef4444", "rgb(18, 120, 210)"];
    const themes = [
      { surface: "#f0eeeb", onSurface: "#1f1f1e" },
      { surface: "#171719", onSurface: "#f5f5f4" },
    ];
    return themes.flatMap((theme) => accents.map((accent) => {
      const palette = window.APStudyCalendarUtils.createAccessibleEventPalette(accent, theme.surface, theme.onSurface);
      const element = document.createElement("div");
      element.style.cssText = `background:${palette.background};color:${palette.text};border:1px solid ${palette.border}`;
      document.querySelector("#events").appendChild(element);
      const computed = getComputedStyle(element);
      return {
        text: window.APStudyCalendarUtils.contrastRatio(computed.color, computed.backgroundColor),
        border: window.APStudyCalendarUtils.contrastRatio(computed.borderTopColor, computed.backgroundColor),
      };
    }));
  });
  for (const result of results) {
    expect(result.text).toBeGreaterThanOrEqual(4.5);
    expect(result.border).toBeGreaterThanOrEqual(3);
  }
});
