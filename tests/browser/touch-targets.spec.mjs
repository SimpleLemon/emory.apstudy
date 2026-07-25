import { expect, test } from "playwright/test";

test.use({ hasTouch: true, viewport: { width: 320, height: 720 } });

test("representative touch controls meet the shared target contract without horizontal overflow", async ({ page, baseURL }) => {
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto(`${baseURL}/static/css/global.css`);
    await page.setContent(`<!doctype html><html><head>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <link rel="stylesheet" href="/static/css/themes.css">
        <link rel="stylesheet" href="/static/css/global.css">
        <link rel="stylesheet" href="/static/css/layout.css">
        <link rel="stylesheet" href="/static/css/index.css">
        <link rel="stylesheet" href="/static/css/chat.css">
        <link rel="stylesheet" href="/static/css/notes.css">
        <link rel="stylesheet" href="/static/css/task.css">
        <link rel="stylesheet" href="/static/css/dashboard.css">
        <link rel="stylesheet" href="/static/css/settings.css">
        <link rel="stylesheet" href="/static/css/files.css">
        <link rel="stylesheet" href="/static/css/onboarding.css">
    </head><body>
        <global class="thenav"><button aria-label="Open navigation">N</button></global>
        <global class="thesidebar"><a href="#home">Home</a></global>
        <main class="workspace-page-shell">
            <button id="calendar-control">Calendar</button>
            <div id="calendar-event" role="button" data-event-ref="event:1" aria-label="Short event" style="height:20px">Event</div>
            <button id="notes-control" class="notes-icon-button" aria-label="Notes menu">N</button>
            <button id="task-control" class="task-star-button" aria-label="Star task">T</button>
            <button id="dashboard-control" class="dashboard-icon-button" aria-label="Dashboard menu">D</button>
            <button id="settings-control" class="settings-button settings-data-export" aria-label="Export settings">S</button>
            <button id="files-control" class="files-icon-button" aria-label="Files menu">F</button>
            <input id="settings-input" aria-label="Setting" value="value">
        </main>
        <main class="chat-app">
            <div class="chat-composer-tools"><button id="chat-control" class="chat-composer-tool" aria-label="Add media">C</button></div>
            <div class="chat-emoji-grid">${Array.from({ length: 12 }, (_, index) => `<button aria-label="Emoji ${index}">${index}</button>`).join("")}</div>
        </main>
        <section class="onboarding-shell"><button id="onboarding-control">Continue</button></section>
    </body></html>`);

    const controls = [
        "global.thenav button", "global.thesidebar a", "#calendar-control", "#notes-control",
        "#task-control", "#dashboard-control", "#settings-control", "#files-control",
        "#settings-input", "#chat-control", ".chat-emoji-grid button:first-child", "#onboarding-control",
    ];
    for (const selector of controls) {
        const size = await page.locator(selector).evaluate((element) => {
            const rect = element.getBoundingClientRect();
            return { width: rect.width, height: rect.height };
        });
        expect(size.height, `${selector} height`).toBeGreaterThanOrEqual(44);
        const requiresWidth = await page.locator(selector).evaluate((element) => element.matches("[aria-label], global a"));
        if (requiresWidth) {
            expect(size.width, `${selector} width`).toBeGreaterThanOrEqual(44);
        }
    }

    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await expect(page.locator("#calendar-event")).toHaveCSS("height", "20px");
    const composerGap = await page.locator(".chat-composer-tools").evaluate((element) => parseFloat(getComputedStyle(element).columnGap));
    expect(composerGap).toBeGreaterThanOrEqual(4);
    expect(errors).toEqual([]);
});
