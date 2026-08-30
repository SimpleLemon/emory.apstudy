import { expect, test } from "playwright/test";

function sectionsFixture() {
    return Array.from({ length: 12 }, (_, index) => ({
        id: `section-${index + 1}`,
        term: "Fall_2026",
        course_code: `TEST ${index + 1}`,
        course_title: `Course ${index + 1}`,
        section_number: "001",
        enrollment_status: "Open",
        seats_available: 10,
        enrollment_capacity: 20,
        live_snapshot_available: true,
        live_stale: false,
        schedule_display: "Mon 9:00 AM-10:00 AM",
        meetings: [{ day: "Mon", start: "0900", end: "1000" }],
    }));
}

async function mountCourses(page, baseURL, { delayLiveStatus = false, savedSectionId = null, documentHeight = 0 } = {}) {
    const sections = sectionsFixture();
    const savedSectionIds = Array.isArray(savedSectionId) ? savedSectionId : [savedSectionId].filter(Boolean);
    const savedCourses = savedSectionIds.map((sectionId) => ({
        id: `saved-${sectionId}`,
        section_id: sectionId,
        color_key: "course-color-01",
    }));
    const pageErrors = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));
    let sectionsRequests = 0;
    let releaseLiveStatus;
    const liveStatusReleased = new Promise((resolve) => {
        releaseLiveStatus = resolve;
    });

    await page.route("**/api/atlas/terms", (route) => route.fulfill({
        json: { terms: ["Fall_2026"], default_term: "Fall_2026" },
    }));
    await page.route("**/api/courses/saved", (route) => route.fulfill({ json: { courses: savedCourses } }));
    await page.route("**/api/courses/tracks", (route) => route.fulfill({
        json: { tracks: [], allowed_intervals_minutes: [30], tier: { key: "free", label: "Free" }, usage: 0, limit: 1 },
    }));
    await page.route("**/api/atlas/sections**", (route) => {
        if (!route.request().url().includes("/api/atlas/sections/verify")) sectionsRequests += 1;
        return route.fulfill({ json: { sections } });
    });
    await page.route("**/api/courses/section-status", async (route) => {
        if (delayLiveStatus) await liveStatusReleased;
        await route.fulfill({ json: { section: sections[4], last_updated_at: "2026-08-10T09:00:00Z" } });
    });

    await page.goto(`${baseURL}/static/js/courses/index.js`);
    await page.setContent(`<!doctype html><html><body>
        <style>
            #courses-panel-content { block-size: 220px; overflow: auto; }
            .course-card { display: block; block-size: 96px; box-sizing: border-box; }
            body { min-block-size: ${documentHeight}px; }
        </style>
        <main class="courses-main">
            <aside class="courses-panel">
                <section class="courses-panel-header"><p id="courses-result-summary">Loading courses</p></section>
                <section id="courses-search-stack" class="courses-search-stack"><input id="courses-search-input" type="search" /></section>
                <section id="courses-panel-content" class="courses-panel-content" aria-live="polite"></section>
            </aside>
        </main>
        <div id="calendar-section-6" class="courses-event" data-section-id="section-6">Calendar section 6</div>
        <div style="block-size: ${documentHeight}px"></div>
    </body></html>`);
    await page.evaluate(() => {
        window.APStudyUIPrimitives = { escapeHtml: (value) => String(value ?? "") };
        window.APStudyToast = { show() {} };
        window.APStudyConfirm = { request: async () => true };
        window.APStudyUndo = { stage: (options) => { window.__coursesUndo = options; } };
        window.APSTUDY_COURSES_DEFAULT_TERM = "Fall_2026";
        window.APSTUDY_COURSES_DEFAULT_CAMPUS = "atlanta";
    });
    for (const source of [
        "utils.js",
        "filters.js",
        "panel.js",
        "calendar.js",
        "edit.js",
        "controls.js",
        "verify.js",
        "index.js",
    ]) {
        await page.addScriptTag({ url: `${baseURL}/static/js/courses/${source}` });
    }
    await page.evaluate(() => document.dispatchEvent(new Event("DOMContentLoaded")));
    await expect.poll(() => {
        if (pageErrors.length) throw new Error(pageErrors.join("\n"));
        return page.locator(".course-card[data-section-id]").count();
    }).toBe(sections.length);

    return { releaseLiveStatus, sectionRequests: () => sectionsRequests };
}

async function openScrolledCourse(page) {
    const panel = page.locator("#courses-panel-content");
    await panel.evaluate((element) => { element.scrollTop = 384; });
    const before = await panel.evaluate((element) => element.scrollTop);
    const card = page.locator('.course-card[data-section-id="section-5"]');
    await card.click();
    await expect(page.getByRole("button", { name: "Close course details" })).toBeVisible();
    return { before, card, panel };
}

async function closeAndWaitForRestore(page, close) {
    if (close === "escape") await page.keyboard.press("Escape");
    else await page.getByRole("button", { name: "Close course details" }).click();
    await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(resolve)));
}

test("closing course details restores the list position and originating card focus", async ({ page, baseURL }) => {
    await mountCourses(page, baseURL);
    const { before, card, panel } = await openScrolledCourse(page);

    await closeAndWaitForRestore(page, "button");

    await expect.poll(() => panel.evaluate((element) => element.scrollTop)).toBe(before);
    await expect(card).toBeFocused();
});

test("Escape closes course details with the same scroll and focus restoration", async ({ page, baseURL }) => {
    await mountCourses(page, baseURL);
    const { before, card, panel } = await openScrolledCourse(page);

    await closeAndWaitForRestore(page, "escape");

    await expect.poll(() => panel.evaluate((element) => element.scrollTop)).toBe(before);
    await expect(card).toBeFocused();
});

test("a late live-status rerender keeps the restored course-list position", async ({ page, baseURL }) => {
    const { releaseLiveStatus } = await mountCourses(page, baseURL, { delayLiveStatus: true });
    const { before, panel } = await openScrolledCourse(page);

    await page.getByRole("button", { name: "Close course details" }).evaluate((button) => button.click());
    releaseLiveStatus();
    await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));

    await expect.poll(() => panel.evaluate((element) => element.scrollTop)).toBe(before);
});

test("undoing a removal restores the original detail return position", async ({ page, baseURL }) => {
    await mountCourses(page, baseURL, { savedSectionId: "section-5" });
    const { before, card, panel } = await openScrolledCourse(page);

    await page.getByRole("button", { name: "Remove Class" }).click();
    await expect.poll(() => page.evaluate(() => Boolean(window.__coursesUndo))).toBe(true);
    await page.evaluate(() => window.__coursesUndo.restore());
    await expect(page.getByRole("button", { name: "Close course details" })).toBeVisible();

    await closeAndWaitForRestore(page, "button");

    await expect.poll(() => panel.evaluate((element) => element.scrollTop)).toBe(before);
    await expect(card).toBeFocused();
});

test("a calendar detail for another section cannot inherit the original list return context", async ({ page, baseURL }) => {
    await mountCourses(page, baseURL, { savedSectionId: "section-6" });
    const { card: cardA, panel } = await openScrolledCourse(page);

    await page.locator("#calendar-section-6").click();
    await expect(page.getByRole("heading", { name: "TEST 6" })).toBeVisible();
    await page.getByRole("button", { name: "Remove Class" }).click();
    await expect.poll(() => page.evaluate(() => Boolean(window.__coursesUndo))).toBe(true);
    await page.evaluate(() => window.__coursesUndo.restore());
    await expect(page.getByRole("heading", { name: "TEST 6" })).toBeVisible();

    await closeAndWaitForRestore(page, "button");

    await expect.poll(() => panel.evaluate((element) => element.scrollTop)).toBe(0);
    await expect(cardA).not.toBeFocused();
});

test("compact layouts preserve panel and document positions for button and Escape closes", async ({ page, baseURL }) => {
    await page.setViewportSize({ width: 800, height: 600 });
    await mountCourses(page, baseURL, { documentHeight: 2_000 });
    const panel = page.locator("#courses-panel-content");
    const card = page.locator('.course-card[data-section-id="section-5"]');

    await panel.evaluate((element) => { element.scrollTop = 384; });
    await page.evaluate(() => window.scrollTo(0, 240));
    const expectedPanelScroll = await panel.evaluate((element) => element.scrollTop);
    const expectedDocumentScroll = await page.evaluate(() => window.scrollY);

    await card.evaluate((element) => element.click());
    await closeAndWaitForRestore(page, "button");
    await expect.poll(() => panel.evaluate((element) => element.scrollTop)).toBe(expectedPanelScroll);
    await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(expectedDocumentScroll);

    await card.evaluate((element) => element.click());
    await closeAndWaitForRestore(page, "escape");
    await expect.poll(() => panel.evaluate((element) => element.scrollTop)).toBe(expectedPanelScroll);
    await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(expectedDocumentScroll);
});

test("a list change invalidates an earlier detail return snapshot", async ({ page, baseURL }) => {
    const { sectionRequests } = await mountCourses(page, baseURL);
    const { panel } = await openScrolledCourse(page);

    const requestsBeforeSearch = sectionRequests();
    await page.locator("#courses-search-input").fill("Course");
    await expect.poll(() => sectionRequests()).toBe(requestsBeforeSearch + 1);
    const currentPosition = await panel.evaluate((element) => element.scrollTop);
    await page.locator('.course-card[data-section-id="section-6"]').evaluate((element) => element.click());

    await closeAndWaitForRestore(page, "button");

    await expect.poll(() => panel.evaluate((element) => element.scrollTop)).toBe(currentPosition);
});
