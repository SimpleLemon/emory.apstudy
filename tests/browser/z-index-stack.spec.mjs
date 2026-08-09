import { expect, test } from "playwright/test";

const harness = `<!doctype html><html><head>
    <link rel="stylesheet" href="/static/css/themes.css">
    <link rel="stylesheet" href="/static/css/global.css">
    <link rel="stylesheet" href="/static/css/layout.css">
    <link rel="stylesheet" href="/static/css/core/feedback-overlays.css">
</head><body>
    <div class="calendar-context-menu" id="dropdown" style="position:fixed;display:block;inset:20px;width:100px;height:100px"></div>
    <div class="notification-tray is-open" id="popover" style="display:block;inset:20px;width:100px;height:100px"></div>
    <div cmdk-overlay id="backdrop"></div>
    <div cmdk-dialog id="modal" style="display:block;width:100px;height:100px"></div>
    <div class="apstudy-toast-host" id="toast" style="inset:20px;width:100px;height:100px"></div>
    <div class="sidebar-tooltip visible" id="tooltip" style="display:block;inset:20px;width:100px;height:100px"></div>
    <a class="apstudy-skip-link" id="critical" href="#">Skip</a>
</body></html>`;

test("dropdowns, tray, command palette, modal, toast, tooltip, and critical UI follow one stack", async ({ page, baseURL }) => {
    await page.route("**/z-index-harness", (route) => route.fulfill({ contentType: "text/html", body: harness }));
    await page.goto(`${baseURL}/z-index-harness`, { waitUntil: "networkidle" });
    const values = await page.evaluate(() => Object.fromEntries(
        ["dropdown", "popover", "backdrop", "modal", "toast", "tooltip", "critical"]
            .map((id) => [id, Number(getComputedStyle(document.getElementById(id)).zIndex)])
    ));
    expect(values).toEqual({
        dropdown: 200,
        popover: 300,
        backdrop: 400,
        modal: 410,
        toast: 500,
        tooltip: 600,
        critical: 700,
    });
    await page.locator("#critical").focus();
    expect(await page.evaluate(() => document.elementFromPoint(30, 30)?.id)).toBe("critical");
});
