/* global document, Event */

import { expect, test } from "playwright/test";

test("dashboard calendar popovers expose and clean up their accessible relationship across interactions", async ({ page, baseURL }) => {
    const pageErrors = [];
    const consoleErrors = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));
    page.on("console", (message) => { if (message.type() === "error") consoleErrors.push(message.text()); });
    await page.route("**/dashboard-calendar-accessibility-harness", (route) => route.fulfill({
        contentType: "text/html",
        body: `<!doctype html><html><body>
            <button id="outside" type="button">Outside</button>
            <button id="dashboard-edit-layout" type="button" aria-pressed="false"><span class="dashboard-action-label">Edit layout</span></button>
            <button id="dashboard-add-tile" type="button" hidden></button>
            <button id="dashboard-cancel-layout" type="button" hidden>Cancel</button>
            <section id="dashboard-daily-quote" hidden></section>
            <section id="dashboard-layout-toolbar" hidden><strong id="dashboard-layout-selection-label"></strong><button id="dashboard-move-earlier"></button><button id="dashboard-move-later"></button><button id="dashboard-customize-tile"></button><button id="dashboard-remove-tile"></button></section>
            <div id="dashboard-tiles"></div>
            <dialog id="dashboard-tile-drawer"><div id="dashboard-tile-drawer-body"></div></dialog>
            <dialog id="dashboard-discard-dialog"><button id="dashboard-keep-editing">Keep editing</button><button id="dashboard-confirm-discard">Discard</button></dialog>
            <div id="dashboard-layout-announcer"></div>
            <script>
                window.APStudyHttp = { fetchJson: async () => ({
                    available_tiles: ["calendar"],
                    dashboard_layout: { version: 4, daily_quote_visible: false, tiles: [{ instance_id: "calendar-1", type: "calendar", size: "standard", view: "month", density: "comfortable", item_limit: 5 }] },
                    tiles: { calendar: { month: "2026-07", events: [
                        { id: "event-1", date: "2026-07-15", title: "Office hours", color: "#3366cc", start: "2026-07-15T14:00:00" },
                        { id: "event-2", date: "2026-07-16", title: "Study group", color: "#cc6633", start: "2026-07-16T16:00:00" }
                    ] } },
                    checklist: { hidden: true, items: [] }
                }) };
            </script>
            <script src="${baseURL}/static/js/core/ui-primitives.js"></script>
            <script src="${baseURL}/static/js/dashboard/utils.js"></script>
            <script src="${baseURL}/static/js/dashboard/renderers.js"></script>
            <script src="${baseURL}/static/js/dashboard/layout-editor.js"></script>
            <script src="${baseURL}/static/js/dashboard/index.js"></script>
        </body></html>`,
    }));
    await page.goto(`${baseURL}/dashboard-calendar-accessibility-harness`, { waitUntil: "networkidle" });

    const firstDay = page.locator('.dashboard-day[data-date="2026-07-15"]');
    const secondDay = page.locator('.dashboard-day[data-date="2026-07-16"]');
    const popover = page.locator("#dashboard-popover");
    await expect(firstDay).toHaveAttribute("aria-expanded", "false");
    await expect(firstDay).not.toHaveAttribute("aria-describedby");

    await firstDay.hover();
    await expect(popover).toBeVisible();
    await expect(popover).toHaveAttribute("id", "dashboard-popover");
    await expect(popover).toHaveAttribute("role", "tooltip");
    await expect(firstDay).toHaveAttribute("aria-describedby", "dashboard-popover");
    await expect(firstDay).toHaveAttribute("aria-expanded", "true");
    await expect(popover).toContainText("Office hours");

    await secondDay.hover();
    await expect(popover).toContainText("Study group");
    await expect(popover).not.toContainText("Office hours");
    await expect(firstDay).not.toHaveAttribute("aria-describedby");
    await expect(secondDay).toHaveAttribute("aria-describedby", "dashboard-popover");

    await secondDay.focus();
    await secondDay.evaluate((element) => element.blur());
    await expect(popover).toBeHidden();
    await expect(secondDay).not.toHaveAttribute("aria-describedby");

    await firstDay.hover();
    await page.mouse.move(2, 2);
    await expect(popover).toBeHidden();

    await firstDay.hover();
    await firstDay.click();
    await page.mouse.move(2, 2);
    await expect(popover).toBeVisible();
    await expect(firstDay).toHaveAttribute("aria-expanded", "true");
    await page.keyboard.press("Escape");
    await expect(popover).toBeHidden();
    await expect(firstDay).not.toHaveAttribute("aria-describedby");

    await firstDay.hover();
    await firstDay.click();
    await page.locator("#outside").click();
    await expect(popover).toBeHidden();
    await expect(firstDay).toHaveAttribute("aria-expanded", "false");

    await firstDay.hover();
    await page.mouse.move(2, 2);
    await page.evaluate(() => document.dispatchEvent(new Event("DOMContentLoaded")));
    await expect(popover).toBeHidden();

    expect(pageErrors).toEqual([]);
    expect(consoleErrors).toEqual([]);
});

test("dashboard calendar markers and empty days remain accessible to native controls", async ({ page, baseURL }) => {
    await page.route("**/dashboard-calendar-accessibility-harness", (route) => route.fulfill({
        contentType: "text/html",
        body: `<!doctype html><html><body>
            <div id="dashboard-tiles"></div>
            <script>window.APStudyUIPrimitives = { escapeHtml: (value) => String(value ?? "") };</script>
            <script src="${baseURL}/static/js/dashboard/utils.js"></script>
            <script src="${baseURL}/static/js/dashboard/renderers.js"></script>
            <script>
                const html = window.APStudyDashboardRenderers.renderTile(
                    "calendar", "standard",
                    { month: "2026-07", events: [{ id: "event-1", date: "2026-07-15", title: "Office hours", start: "2026-07-15T14:00:00" }] },
                    { instance_id: "calendar-1", type: "calendar", view: "month" },
                );
                document.querySelector("#dashboard-tiles").innerHTML = html;
            </script>
        </body></html>`,
    }));
    await page.goto(`${baseURL}/dashboard-calendar-accessibility-harness`, { waitUntil: "networkidle" });

    const eventDay = page.locator('.dashboard-day[data-date="2026-07-15"]');
    await expect(eventDay).toHaveAttribute("aria-label", /Office hours/);
    await expect(eventDay.locator(".dashboard-day-markers")).not.toHaveAttribute("aria-hidden", "true");
    const emptyDay = page.locator(".dashboard-day:disabled").first();
    await expect(emptyDay).toBeDisabled();
    await expect(emptyDay).not.toHaveAttribute("tabindex");
    await emptyDay.focus();
    expect(await page.evaluate(() => document.activeElement?.classList.contains("dashboard-day"))).toBe(false);
});
