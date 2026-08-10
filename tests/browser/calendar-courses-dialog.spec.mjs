import { expect, test } from "playwright/test";

test("courses dialog traps focus, preserves rerender focus, inerts the page, and restores its trigger", async ({ page, baseURL }) => {
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto(`${baseURL}/static/js/calendar/integrations/course-modal.js`);
    await page.setContent(`<!doctype html><html data-theme="parchment-light"><head>
        <link rel="stylesheet" href="/static/css/themes.css">
        <link rel="stylesheet" href="/static/css/tailwind.css">
    </head><body>
        <button id="calendar-courses" type="button" aria-haspopup="dialog">Courses</button>
        <main id="background"><a href="#elsewhere">Background link</a></main>
    </body></html>`);
    await page.evaluate(async () => {
        await import("/static/js/calendar/integrations/course-modal.js");
        await import("/static/js/calendar/integrations/courses.js");
        await import("/static/js/calendar/controls.js");
        window.APStudyLoader = { html: (label) => `<p>${label}</p>` };
        const section = {
            id: "section-1",
            course_code: "CS 101",
            course_title: "Introduction to Computing",
            section_number: "1",
            term: "Fall_2026",
            enrollment_status: "Open",
            instructor: "Ada Lovelace",
            meetings: [],
            searchBlob: "cs 101 introduction to computing ada lovelace",
        };
        const state = {
            anchorDate: new Date(),
            view: "week",
            public: { readOnly: false },
            calendars: {},
            calendarColors: ["#0ea5e9"],
            courses: {
                terms: ["Fall_2026"],
                sections: [section],
                sectionsById: { "section-1": section },
                indexLoaded: true,
                selectedSectionIds: new Set(),
                pinnedSectionIds: new Set(),
                modalOpen: false,
                modalTriggerEl: null,
                isClosing: false,
                animateOnOpen: false,
                showSelectedOnly: false,
                loading: false,
                error: "",
                searchQuery: "",
                searchInput: "",
                termFilter: "",
                filteredSectionIds: ["section-1"],
                expandedDetails: new Set(),
            },
            ui: {
                calendarMenuOpen: false,
                expandedUpcomingRefs: new Set(),
                preferenceDirty: new Set(),
            },
        };
        let courses;
        courses = window.APStudyCalendarCourses.createCalendarCourses({
            state,
            constants: {
                coursesSelectionStorageKey: "browser-course-selections",
                coursesModalAnimationMs: 0,
                simulatedCalendarName: "Simulated Courses",
            },
            render: () => courses?.renderCoursesModal(),
            saveCalendarState() {},
            escapeHtml(value) {
                return String(value ?? "")
                    .replaceAll("&", "&amp;")
                    .replaceAll('"', "&quot;")
                    .replaceAll("<", "&lt;")
                    .replaceAll(">", "&gt;");
            },
        });
        const noop = () => {};
        const callbacks = new Proxy({
            closeCoursesModal: courses.closeCoursesModal,
            openCoursesModal: courses.openCoursesModal,
            renderCoursesModal: courses.renderCoursesModal,
            applyCourseFilters: courses.applyCourseFilters,
            applyCoursesFiltersFromUrl: courses.applyCoursesFiltersFromUrl,
            submitCoursesSearch: courses.submitCoursesSearch,
            toggleCourseSectionSelection: courses.toggleCourseSectionSelection,
            writeCourseFiltersToUrl: courses.writeCourseFiltersToUrl,
            isCompactCalendarViewport: () => false,
            getCurrentRenderRange: () => ({}),
            getBufferedRange: () => ({}),
        }, {
            get(target, property) { return property in target ? target[property] : noop; },
        });
        const formatters = new Proxy({}, { get: () => (value) => String(value ?? "") });
        window.APStudyCalendarControls.createCalendarControls({ state, callbacks, formatters }).wireControls();
        window.browserCourses = courses;
    });

    const trigger = page.getByRole("button", { name: "Courses" });
    await trigger.click();
    const dialog = page.getByRole("dialog", { name: "Courses" });
    await expect(dialog).toBeVisible();
    const search = dialog.getByRole("textbox", { name: "Search courses" });
    await expect(search).toBeFocused();
    await expect(page.locator("#background")).toHaveAttribute("inert", "");
    await expect(page.locator("#background")).toHaveAttribute("aria-hidden", "true");

    await search.fill("computing");
    await search.evaluate((input) => input.setSelectionRange(2, 5));
    await page.evaluate(() => window.browserCourses.renderCoursesModal());
    await expect(search).toBeFocused();
    await expect.poll(() => search.evaluate((input) => [input.selectionStart, input.selectionEnd])).toEqual([2, 5]);

    const info = dialog.getByRole("button", { name: "Toggle section details" });
    await info.focus();
    await page.evaluate(() => window.browserCourses.renderCoursesModal());
    await expect(info).toBeFocused();

    const lastAction = dialog.locator(".js-course-toggle");
    await lastAction.focus();
    await page.keyboard.press("Tab");
    await expect(dialog.getByRole("button", { name: "Close courses search" })).toBeFocused();
    await page.keyboard.press("Shift+Tab");
    await expect(lastAction).toBeFocused();

    await page.keyboard.press("Escape");
    await expect(dialog).toBeHidden();
    await expect(trigger).toBeFocused();
    await expect(page.locator("#background")).not.toHaveAttribute("inert", "");
    await expect(page.locator("#background")).not.toHaveAttribute("aria-hidden", "true");
    expect(errors).toEqual([]);
});
