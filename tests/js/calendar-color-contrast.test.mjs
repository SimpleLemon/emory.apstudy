import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
globalThis.window = { APStudyUIPrimitives: { escapeHtml: String } };
globalThis.document = { documentElement: {} };

async function loadBrowserModule(relativePath) {
    const source = await readFile(path.join(repoRoot, relativePath), "utf8");
    await import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}#${relativePath}`);
}

await loadBrowserModule("static/js/calendar/utils.js");
await loadBrowserModule("static/js/calendar/views/event-render.js");
await loadBrowserModule("static/js/calendar/views/render-shell.js");

const {
    contrastRatio,
    createAccessibleEventPalette,
    parseCssColor,
} = window.APStudyCalendarUtils;

const themePalettes = {
    "obsidian-dark": { surface: "#1c2026", onSurface: "#dfe2eb" },
    "parchment-light": { surface: "#f0eeeb", onSurface: "#1f1f1e" },
    "system-light": { surface: "#f0eeeb", onSurface: "#1f1f1e" },
    "system-dark": { surface: "#1c2026", onSurface: "#dfe2eb" },
    "nest-light": { surface: "#EAEAE4", onSurface: "#222222" },
    "nest-dark": { surface: "#101730", onSurface: "#d6ddf0" },
};
const predefinedColors = ["#ef4444", "#f97316", "#eab308", "#84cc16", "#0ea5e9", "#d946ef", "#b08968"];
const adversarialColors = [
    "#000000",
    "#ffffff",
    "#010101",
    "#fefefe",
    "#777777",
    "#808080",
    "#7f8081",
    "#123456",
    "#abcdef",
    "rgb(245, 128, 12)",
];

for (const [theme, tokens] of Object.entries(themePalettes)) {
    for (const color of [...predefinedColors, ...adversarialColors]) {
        test(`${theme} ${color} event palette meets text, border, and indicator contrast`, () => {
            const palette = createAccessibleEventPalette(color, tokens.surface, tokens.onSurface);

            assert.ok(
                contrastRatio(palette.text, palette.background) >= 4.5,
                `text ${palette.text} on ${palette.background}`,
            );
            assert.ok(
                contrastRatio(palette.border, palette.background) >= 3,
                `border ${palette.border} on event ${palette.background}`,
            );
            assert.ok(
                contrastRatio(palette.border, tokens.surface) >= 3,
                `border ${palette.border} on surface ${tokens.surface}`,
            );
            assert.equal(palette.indicator, palette.border);
            for (const value of Object.values(palette)) assert.ok(parseCssColor(value), value);
        });
    }
}

test("event renderer uses the active theme surface and returns solid accessible colors", () => {
    const tokens = { "--color-surface-container": "#101730", "--color-on-surface": "#d6ddf0" };
    globalThis.getComputedStyle = () => ({
        getPropertyValue(name) { return tokens[name] || ""; },
    });
    const renderer = window.APStudyCalendarEventRender.createCalendarEventRender({
        state: { public: { readOnly: false } },
        callbacks: {
            getCalendarEventColor: () => "rgb(255, 255, 255)",
            getCalendarEventRef: () => "event:1",
            getEventCalendarLabel: () => "Personal",
            getVisibleEvents: () => [],
        },
        formatters: {
            createAccessibleEventPalette,
            escapeHtml: String,
            formatAllDayRange: () => "Jul 20 (All day)",
            formatTimedEventRange: () => "Jul 20, 1 PM - 2 PM",
            getCssColorVariable: window.APStudyCalendarUtils.getCssColorVariable,
            getTaskPriorityColor: () => "#ef4444",
            isTaskEvent: (event) => event.source_type === "task",
        },
    });
    const palette = renderer.getEventBadgeColors({ source_type: "event" });

    assert.ok(contrastRatio(palette.text, palette.background) >= 4.5);
    assert.ok(contrastRatio(palette.border, palette.background) >= 3);
    assert.ok(contrastRatio(palette.indicator, tokens["--color-surface-container"]) >= 3);
    assert.doesNotMatch(renderer.getEventBadgeStyle({ source_type: "event" }), /rgba|color-mix/);
});

test("completed tasks retain accessible contrast and a distinct neutral palette", () => {
    const theme = themePalettes["parchment-light"];
    const active = createAccessibleEventPalette("#ef4444", theme.surface, theme.onSurface);
    const completed = createAccessibleEventPalette("#ef4444", theme.surface, theme.onSurface, { completed: true });

    assert.notEqual(completed.background, active.background);
    assert.notEqual(completed.indicator, active.indicator);
    assert.ok(contrastRatio(completed.text, completed.background) >= 4.5);
    assert.ok(contrastRatio(completed.border, completed.background) >= 3);
    assert.ok(contrastRatio(completed.indicator, theme.surface) >= 3);
});

test("calendar recomputes inline contrast colors after theme and system-scheme changes", async () => {
    const source = await readFile(path.join(repoRoot, "static/js/calendar/controls.js"), "utf8");

    assert.match(source, /document\.addEventListener\("apstudy-theme-change", refreshThemeDependentColors\)/);
    assert.match(source, /window\.matchMedia\("\(prefers-color-scheme: dark\)"\)\.addEventListener\("change"/);
    assert.match(source, /document\.documentElement\.dataset\.theme === "system-match"/);
    assert.match(source, /renderCalendarView\(\);\s*renderAssignments\(\);/);
});

test("upcoming view keeps period arrows visible and disables them", () => {
    const elements = new Map();
    const makeElement = (id) => {
        const attributes = new Map();
        const element = {
            className: "",
            disabled: false,
            hidden: false,
            style: {},
            textContent: "",
            classList: { toggle() {} },
            getAttribute(name) { return attributes.get(name) || null; },
            setAttribute(name, value) { attributes.set(name, value); },
        };
        elements.set(id, element);
        return element;
    };
    [
        "calendar-title",
        "calendar-subtitle",
        "calendar-view-week",
        "calendar-view-month",
        "calendar-view-upcoming",
        "calendar-period-controls",
        "calendar-prev",
        "calendar-next",
        "calendar-view-root",
    ].forEach(makeElement);
    elements.get("calendar-view-root").closest = () => ({ classList: { toggle() {} } });
    document.getElementById = (id) => elements.get(id) || null;

    const state = {
        anchorDate: new Date(2026, 6, 20),
        loadingDashboard: false,
        public: { readOnly: true, title: "Shared Calendar", rangeLabel: "Shared dates" },
        ui: {},
        view: "upcoming",
    };
    const shell = window.APStudyCalendarRenderShell.createCalendarRenderShell({
        state,
        constants: { compactCalendarQuery: { matches: false }, hourHeightPx: 60 },
        callbacks: {
            buildMobileCalendarAgendaHtml: () => "",
            buildMonthViewHtml: () => "",
            buildUpcomingAgendaHtml: () => "",
            buildWeekViewHtml: () => "",
            getStartOfWeek: (date) => new Date(date),
            hideCalendarHoverCard() {},
            renderAssignments() {},
            renderCalendarMenu() {},
        },
    });

    shell.render();
    assert.equal(elements.get("calendar-period-controls").hidden, false);
    assert.equal(elements.get("calendar-prev").disabled, true);
    assert.equal(elements.get("calendar-next").disabled, true);
    assert.equal(elements.get("calendar-prev").getAttribute("aria-disabled"), "true");
    assert.equal(elements.get("calendar-next").getAttribute("aria-disabled"), "true");

    state.view = "week";
    shell.render();
    assert.equal(elements.get("calendar-prev").disabled, false);
    assert.equal(elements.get("calendar-next").disabled, false);
});
