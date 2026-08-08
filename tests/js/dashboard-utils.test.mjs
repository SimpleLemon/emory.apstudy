import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const plain = (value) => JSON.parse(JSON.stringify(value));
const dashboardBundle = (await Promise.all([
    "static/js/dashboard/utils.js",
    "static/js/dashboard/renderers.js",
    "static/js/dashboard/index.js",
].map((relativePath) => readFile(path.join(repoRoot, relativePath), "utf8")))).join("\n");

function loadDashboardUtils(source) {
    const document = {
        getElementById() {
            return null;
        },
        addEventListener() {},
        createElement() {
            return {
                textContent: "",
                get innerHTML() {
                    return String(this.textContent)
                        .replace(/&/g, "&amp;")
                        .replace(/</g, "&lt;")
                        .replace(/>/g, "&gt;");
                },
            };
        },
        body: {
            appendChild() {},
        },
        querySelector() {
            return null;
        },
    };
    const context = {
        document,
        window: {
            APStudyUIPrimitives: { escapeHtml: String },
            setTimeout() {},
        },
        Date,
        fetch() {
            throw new Error("fetch should not run while loading utils");
        },
    };
    context.window.document = document;
    vm.runInNewContext(source, context);
    return context.window.APStudyDashboardUtils;
}

test("dashboard utilities group events and format bytes", async () => {
    const utils = loadDashboardUtils(dashboardBundle);

    const grouped = utils.groupEventsByDate([
        { title: "A", date: "2026-05-01" },
        { title: "B", start: "2026-05-01T12:00:00Z" },
        { title: "C", date: "2026-05-02" },
    ]);

    assert.equal(grouped.get("2026-05-01").length, 2);
    assert.equal(grouped.get("2026-05-02").length, 1);
    assert.equal(utils.formatBytes(1536), "1.5 KB");
});

test("dashboard utility applies saved tile order while appending missing tiles", async () => {
    const utils = loadDashboardUtils(dashboardBundle);

    assert.deepEqual(
        Array.from(utils.normalizeTileOrder(["messages", "calendar", "messages"], ["calendar", "tasks", "messages"])),
        ["messages", "calendar", "tasks"],
    );
});

test("dashboard utility normalizes v1 and v2 tile layouts", async () => {
    const utils = loadDashboardUtils(dashboardBundle);

    const legacy = plain(utils.normalizeTileLayout(["messages", "calendar"], ["calendar", "tasks", "messages"]));
    assert.deepEqual(legacy.map(({ type, size }) => ({ type, size })), [
        { type: "messages", size: "standard" },
        { type: "calendar", size: "standard" },
        { type: "tasks", size: "standard" },
    ]);
    assert.equal(legacy[1].view, "month");
    assert.equal(legacy[1].instance_id, "legacy-calendar");

    assert.deepEqual(
        plain(utils.normalizeTileLayout(
            { version: 2, tiles: [{ id: "calendar", size: "wide" }, { id: "tasks", size: "compact" }] },
            ["calendar", "tasks"],
        )).map(({ type, size }) => ({ type, size })),
        [{ type: "calendar", size: "wide" }, { type: "tasks", size: "standard" }],
    );

    assert.deepEqual(
        plain(utils.normalizeTileLayout(
            { version: 3, tiles: [{ id: "calendar", size: "tall", view: "week" }, { id: "tasks", size: "wide", task_list_ids: ["list-1", "list-1", "list-2"] }] },
            ["calendar", "tasks"],
        )).map(({ type, size, view, task_list_ids }) => ({ type, size, view, task_list_ids })),
        [
            { type: "calendar", size: "tall", view: "week", task_list_ids: undefined },
            { type: "tasks", size: "wide", view: undefined, task_list_ids: ["list-1", "list-2"] },
        ],
    );
});

test("dashboard utility applies new size and calendar view rules", async () => {
    const utils = loadDashboardUtils(dashboardBundle);

    assert.equal(utils.normalizeTileSize("calendar", "medium"), "standard");
    assert.equal(utils.normalizeTileSize("tasks", "large"), "wide");
    assert.equal(utils.normalizeTileSize("courses", "tall"), "wide");
    assert.equal(utils.normalizeCalendarView("calendar", "upcoming"), "upcoming");
    assert.equal(utils.normalizeCalendarView("calendar", "agenda"), "month");
    assert.equal(utils.nearestTileSize("tasks", 0, 140, "standard"), "tall");
    assert.equal(utils.nearestTileSize("tasks", 140, 140, "standard"), "wide");
    assert.equal(utils.nearestTileSize("courses", 0, 140, "standard"), "wide");
    assert.deepEqual(plain(utils.normalizeTaskListIds(["list-1", "", "list-1", "list-2"], ["list-1"])), ["list-1"]);
});

test("dashboard calendar helpers label views and derive event dates", async () => {
    const utils = loadDashboardUtils(dashboardBundle);

    assert.equal(utils.tileTitle("calendar", "month"), "Calendar: Month");
    assert.equal(utils.tileTitle("calendar", "week"), "Calendar: Week");
    assert.equal(utils.tileTitle("calendar", "upcoming"), "Calendar: Upcoming");
    assert.equal(utils.tileTitle("tasks"), "Upcoming Tasks");
    assert.equal(utils.eventDateKey({ date: "2026-05-27" }), "2026-05-27");
    assert.equal(utils.eventDateKey({ start: "2026-05-28T12:00:00Z" }), "2026-05-28");
    assert.equal(utils.escapeAttr('"quoted"'), "&quot;quoted&quot;");
});

test("dashboard summary tile layout arrays preserve v3 hidden tiles", async () => {
    const utils = loadDashboardUtils(dashboardBundle);

    const summaryLayout = utils.summaryLayoutSource({
        tile_layout_version: 3,
        tile_layout: [{ id: "calendar", size: "standard", view: "month" }],
        tile_order: ["calendar"],
    });

    const normalized = plain(utils.normalizeTileLayout(summaryLayout, ["calendar", "tasks"]));
    assert.equal(normalized.length, 1);
    assert.equal(normalized[0].type, "calendar");
    assert.equal(normalized[0].view, "month");
});

test("dashboard v4 layouts preserve duplicate instances and per-tile settings", async () => {
    const utils = loadDashboardUtils(dashboardBundle);
    const layout = utils.normalizeDashboardLayout({
        version: 4,
        daily_quote_visible: false,
        tiles: [
            {
                instance_id: "tasks-week",
                type: "tasks",
                title: "This week",
                size: "standard",
                item_limit: 3,
                deadline_days: 7,
                include_overdue: true,
                include_undated: false,
                priorities: ["high", "medium"],
                starred_only: false,
            },
            {
                instance_id: "tasks-starred",
                type: "tasks",
                size: "wide",
                item_limit: 8,
                deadline_days: 30,
                priorities: ["high", "medium", "low", "none"],
                starred_only: true,
            },
        ],
    }, ["tasks"]);

    assert.equal(layout.daily_quote_visible, false);
    assert.deepEqual(plain(layout.tiles.map((tile) => tile.instance_id)), ["tasks-week", "tasks-starred"]);
    assert.equal(layout.tiles[0].title, "This week");
    assert.equal(layout.tiles[0].deadline_days, 7);
    assert.equal(layout.tiles[1].starred_only, true);
    assert.equal(utils.layoutsEqual(layout, utils.cloneLayout(layout)), true);
});
