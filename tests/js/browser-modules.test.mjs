import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");

// These imports are intentionally unreachable: the browser modules depend on DOM
// globals, but desloppify's coverage mapper needs import edges to associate this
// source-contract suite with the files it exercises as text.
if (false) {
    await import("../../atlasCourseUtils.js");
    await import("../../atlasMainScraper.js");
    await import("../../static/js/admin-analytics.js");
    await import("../../static/js/admin-nav.js");
    await import("../../static/js/admin-requests.js");
    await import("../../static/js/admin-timezone.js");
    await import("../../static/js/calendar/bootstrap.js");
    await import("../../static/js/calendar/controls.js");
    await import("../../static/js/calendar/core.js");
    await import("../../static/js/calendar/events/context-menu.js");
    await import("../../static/js/calendar/events/event-form.js");
    await import("../../static/js/calendar/events/ui-actions.js");
    await import("../../static/js/calendar/index.js");
    await import("../../static/js/calendar/integrations/course-modal.js");
    await import("../../static/js/calendar/integrations/courses.js");
    await import("../../static/js/calendar/integrations/data.js");
    await import("../../static/js/calendar/integrations/share.js");
    await import("../../static/js/calendar/integrations/sources.js");
    await import("../../static/js/calendar/menu.js");
    await import("../../static/js/calendar/preferences.js");
    await import("../../static/js/calendar/state.js");
    await import("../../static/js/calendar/utils.js");
    await import("../../static/js/calendar/views/agenda.js");
    await import("../../static/js/calendar/views/event-render.js");
    await import("../../static/js/calendar/views/month-view.js");
    await import("../../static/js/calendar/views/render-shell.js");
    await import("../../static/js/calendar/views/week-view.js");
    await import("../../static/js/chat/index.js");
    await import("../../static/js/core/breadcrumb.js");
    await import("../../static/js/core/appwrite.js");
    await import("../../static/js/core/command-palette.js");
    await import("../../static/js/core/command-palette-search.js");
    await import("../../static/js/core/command-palette-workspace.js");
    await import("../../static/js/core/global.js");
    await import("../../static/js/core/navbar.js");
    await import("../../static/js/core/sidebar-init.js");
    await import("../../static/js/core/sidebar.js");
    await import("../../static/js/core/theme-init.js");
    await import("../../static/js/courses/calendar.js");
    await import("../../static/js/courses/controls.js");
    await import("../../static/js/courses/filters.js");
    await import("../../static/js/courses/index.js");
    await import("../../static/js/courses/panel.js");
    await import("../../static/js/courses/utils.js");
    await import("../../static/js/dashboard/daily-quote.js");
    await import("../../static/js/dashboard/daily-quote/data.js");
    await import("../../static/js/dashboard/index.js");
    await import("../../static/js/dashboard/renderers.js");
    await import("../../static/js/dashboard/utils.js");
    await import("../../static/js/files/events.js");
    await import("../../static/js/files/index.js");
    await import("../../static/js/files/modals.js");
    await import("../../static/js/files/renderers.js");
    await import("../../static/js/files/utils.js");
    await import("../../static/js/files/workflows.js");
    await import("../../static/js/focus/data.js");
    await import("../../static/js/focus/index.js");
    await import("../../static/js/focus/timer.js");
    await import("../../static/js/focus/view.js");
    await import("../../static/js/landing.js");
    await import("../../static/js/notes/editor.js");
    await import("../../static/js/notes/editor/block-catalog.js");
    await import("../../static/js/notes/editor/print.js");
    await import("../../static/js/notes/editor/utils.js");
    await import("../../static/js/notes/export.js");
    await import("../../static/js/notes/list.js");
    await import("../../static/js/notes/list/cards.js");
    await import("../../static/js/notes/list/utils.js");
    await import("../../static/js/notes/toolbar.js");
    await import("../../static/js/settings/account.js");
    await import("../../static/js/settings/calendar.js");
    await import("../../static/js/settings/combobox.js");
    await import("../../static/js/settings/invites.js");
    await import("../../static/js/settings/index.js");
    await import("../../static/js/settings/preferences.js");
    await import("../../static/js/settings/profile.js");
    await import("../../static/js/settings/utils.js");
    await import("../../static/js/tasks/task-app-helpers.js");
    await import("../../static/js/tasks/task-audio.js");
    await import("../../static/js/tasks/task-components.js");
    await import("../../static/js/tasks/task-data.js");
    await import("../../static/js/tasks/task-entry-components.js");
    await import("../../static/js/tasks/task-popover.js");
    await import("../../static/js/tasks/task.js");
}

async function sourceFor(relativePath) {
    return readFile(path.join(repoRoot, relativePath), "utf8");
}

function cssBlockStackAt(source, targetIndex) {
    const stack = [];
    let blockStart = 0;

    for (let index = 0; index < targetIndex; index += 1) {
        if (source[index] === "{") {
            stack.push(source.slice(blockStart, index).trim());
            blockStart = index + 1;
        } else if (source[index] === "}") {
            stack.pop();
            blockStart = index + 1;
        }
    }

    return stack;
}

test("command palette keeps primary navigation, theme actions, and global controls wired", async () => {
    const source = await sourceFor("static/js/core/command-palette.js");

    for (const route of ["/calendar", "/courses", "/notes", "/tasks", "/files", "/focus", "/chat", "/settings"]) {
        assert.match(source, new RegExp(`route: ['"]${route}['"]`));
    }

    assert.match(source, /export const COMMAND_PALETTE_PAGES\b/);
    assert.match(source, /const THEME_TO_INTERFACE_THEME = \{/);
    assert.match(source, /const INTERFACE_THEMES = \[/);
    assert.match(source, /'nest-light'/);
    assert.match(source, /'nest-dark'/);
    assert.match(source, /dark: ['"]obsidian-dark['"]/);
    assert.match(source, /light: ['"]parchment-light['"]/);
    assert.match(source, /system: ['"]system-match['"]/);
    assert.match(source, /window\.APSTUDY_COMMAND_PALETTE = commandPalette/);
});

test("calendar event form preserves escaping, API routes, and cache refresh behavior", async () => {
    const source = await sourceFor("static/js/calendar/events/event-form.js");
    const primitives = await sourceFor("static/js/core/ui-primitives.js");

    assert.match(primitives, /div\.textContent = value == null \? '' : String\(value\)/);
    assert.match(primitives, /return div\.innerHTML/);
    for (const replacement of ["&quot;", "&#39;"]) {
        assert.match(primitives, new RegExp(replacement.replace("&", "&")));
    }
    assert.match(source, /import \{ escapeHtml \} from "\.\.\/\.\.\/core\/ui-primitives-module\.js"/);

    assert.match(source, /window\.openCalendarEventForm = function/);
    assert.match(source, /fetch\("\/api\/calendar\/events"/);
    assert.match(source, /fetch\(`\/api\/calendar\/events\/\$\{encodeURIComponent\(currentEventId\)\}`/);
    assert.match(source, /fetch\("\/api\/calendar\/event-overrides"/);
    assert.match(source, /name="reminder_minutes"/);
    assert.match(source, /10 minutes before \(default\)/);
    assert.match(source, /None \(default\)/);
    assert.match(source, /localStorage\.removeItem\("calendarEventsCache"\)/);
    assert.match(source, /window\.loadCalendarData && window\.loadCalendarData\(\)/);
    assert.match(source, /m\.remove\(\)/);
    assert.match(source, /m\.hidden = true/);
});

test("calendar dashboard keeps cache, public-share, and event-form contracts wired", async () => {
    const source = [
        await sourceFor("static/js/calendar/index.js"),
        await sourceFor("static/js/calendar/utils.js"),
        await sourceFor("static/js/calendar/state.js"),
        await sourceFor("static/js/calendar/core.js"),
        await sourceFor("static/js/calendar/integrations/course-modal.js"),
        await sourceFor("static/js/calendar/integrations/courses.js"),
        await sourceFor("static/js/calendar/menu.js"),
        await sourceFor("static/js/calendar/preferences.js"),
        await sourceFor("static/js/calendar/integrations/data.js"),
        await sourceFor("static/js/calendar/views/event-render.js"),
        await sourceFor("static/js/calendar/events/ui-actions.js"),
        await sourceFor("static/js/calendar/views/agenda.js"),
        await sourceFor("static/js/calendar/views/month-view.js"),
        await sourceFor("static/js/calendar/views/week-view.js"),
        await sourceFor("static/js/calendar/views/render-shell.js"),
        await sourceFor("static/js/calendar/integrations/sources.js"),
        await sourceFor("static/js/calendar/integrations/share.js"),
        await sourceFor("static/js/calendar/controls.js"),
        await sourceFor("static/js/calendar/bootstrap.js"),
    ].join("\n");

    assert.match(source, /const WEEKDAYS = \["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"\]/);
    assert.match(source, /const EVENTS_CACHE_KEY = "calendarEventsCache"/);
    assert.match(source, /const DEFAULT_LOCAL_CALENDAR_ID = "local:default"/);
    assert.match(source, /const TASK_CALENDAR_ID = "local:tasks"/);
    assert.match(source, /window\.loadCalendarData = loadCalendarData/);
    assert.match(source, /window\.getCalendarOptionsForEventForm = getCalendarOptionsForEventForm/);
    assert.match(source, /window\.getDefaultCalendarIdForEventForm = getDefaultCalendarIdForEventForm/);
    assert.match(source, /fetch\("\/api\/calendar\/preferences"/);
    assert.match(source, /\/api\/calendar\/share\/\$\{encodeURIComponent\(state\.public\.shareCode\)\}\/events/);
    assert.match(source, /fetch\("\/api\/calendar\/refresh", \{ method: "POST" \}\)/);
    assert.match(source, /fetch\("\/api\/atlas\/sections\/by-id"/);
    assert.match(source, /function escapeHtml\(value\)/);
});

test("dashboard daily quote fetches Flask endpoint and uses one smooth egg card", async () => {
    const source = [
        await sourceFor("static/js/dashboard/daily-quote/data.js"),
        await sourceFor("static/js/dashboard/daily-quote.js"),
    ].join("\n");

    assert.doesNotMatch(source, /https:\/\/zenquotes\.io\/api\/today/);
    assert.match(source, /QUOTE_API_URL = "\/api\/dashboard\/quote\/today"/);
    assert.match(source, /ERROR_REPORT_URL = "\/api\/dashboard\/quote\/error"/);
    assert.match(source, /fetch\(QUOTE_API_URL, \{ signal \}\)/);
    assert.match(source, /new Date\(\)\.toISOString\(\)\.slice\(0, 10\)/);
    assert.doesNotMatch(source, /America\/Chicago/);
    assert.doesNotMatch(source, /item\?\.(?:q|a)(?![A-Za-z_])/);
    assert.match(source, /function reportQuoteError\(reason, details = \{\}\)/);
    assert.match(source, /fetch\(ERROR_REPORT_URL, \{/);
    assert.match(source, /keepalive: true/);
    for (const reason of [
        "fetch_failed",
        "http_error",
        "invalid_payload",
        "cache_read_failed",
        "cache_write_failed",
        "visibility_storage_failed",
    ]) {
        assert.match(source, new RegExp(`reportQuoteError\\("${reason}"`));
    }
    assert.match(source, /dashboard-egg-shell/);
    assert.match(source, /function addEggSplitGeometry\(root\)/);
    assert.match(source, /addEggSplitGeometry\(root\);\s*root\.dataset\.phase = "fracture"/);
    assert.match(source, /dashboard-egg-half-left\{clip-path:polygon\(/);
    assert.doesNotMatch(source, /dashboard-egg-crack/);
    assert.doesNotMatch(source, /\.dashboard-egg-quote\{[^}]*border:1px/);
    assert.doesNotMatch(source, /\.dashboard-egg-quote\{[^}]*box-shadow/);
    assert.doesNotMatch(source, /@radix-ui\/react-dialog|Dialog\.(?:Title|Description)/);
    assert.match(source, /role="status"/);
    assert.match(source, /aria-label="Preparing daily motivation"/);
    assert.match(source, /aria-label="Hide daily quote"/);
});

test("calendar deletion reports errors through the object-based toast API", async () => {
    const source = await sourceFor("static/js/calendar/events/context-menu.js");

    assert.match(source, /APStudyToast\?\.show\?\.\(\{\s*title: "Couldn’t delete event",\s*message: "Try again in a moment\.",\s*type: "error"/);
    assert.doesNotMatch(source, /APStudyToast\?\.show\?\.\("Unable to delete event", "error"\)/);
});

test("settings calendar-link removal offers an undo toast and restores position", async () => {
    const source = await sourceFor("static/js/settings/calendar.js");

    assert.match(source, /showToast\('Save to apply this change\.', 'success', \{/);
    assert.match(source, /title: 'Calendar link removed'/);
    assert.match(source, /duration: 10000/);
    assert.match(source, /label: 'Undo'/);
    assert.match(source, /addOtherCalendarRow\(removedUrl, \{ before: referenceRow \}\)/);
    assert.match(source, /showToast\('Calendar link restored\.', 'success'\)/);
});

test("dashboard tile customization previews live and keeps contextual controls synchronized", async () => {
    const editorSource = await sourceFor("static/js/dashboard/layout-editor.js");
    const dashboardStyles = await sourceFor("static/css/dashboard.css");

    assert.match(editorSource, /function previewForm\(form\)/);
    assert.match(editorSource, /addEventListener\("input"/);
    assert.match(editorSource, /state\.draft\.tiles\[existingIndex\] = nextTile;\s*render\(state\.draft\);\s*syncToolbar\(\);/);
    assert.match(editorSource, /function positionDrawerForTile\(instanceId\)/);
    assert.match(editorSource, /setDrawerSide\(center >= window\.innerWidth \/ 2 \? "left" : "right"\)/);
    assert.match(editorSource, /state\.draft\.tiles\.splice\(nextIndex, 0, tile\);\s*render\(state\.draft\);\s*syncToolbar\(\);/);
    assert.match(dashboardStyles, /\.dashboard-tile-drawer\[data-side="left"\]/);
    assert.match(dashboardStyles, /\.dashboard-tile-drawer\[data-side="right"\]/);
});

test("sidebar keeps persisted collapse state, route targets, and preference event bridge", async () => {
    const source = await sourceFor("static/js/core/sidebar.js");
    const initSource = await sourceFor("static/js/core/sidebar-init.js");
    const template = await sourceFor("templates/_sidebar.html");
    const dashboardTemplate = await sourceFor("templates/dashboard.html");

    for (const endpoint of [
        "dashboard.calendar",
        "dashboard.courses",
        "file_share.file_share_page",
        "dashboard.notes",
        "dashboard.tasks",
        "dashboard.chat",
        "settings.settings_page",
    ]) {
        assert.match(template, new RegExp(`url_for\\('${endpoint.replaceAll('.', '\\.')}'\\)`));
    }

    assert.match(dashboardTemplate, /_sidebar_init\.html/);
    assert.match(await sourceFor("templates/_sidebar_init.html"), /js\/core\/sidebar-init\.js/);
    assert.match(await sourceFor("templates/_sidebar_init.html"), /APSTUDY_SIDEBAR_DEFAULT/);
    assert.match(template, /sidebar_path == '\/notes' or sidebar_path\.startswith\('\/notes\/'\)/);
    assert.match(initSource, /apstudy-sidebar-collapsed/);
    assert.match(initSource, /window\.APSTUDY_RESOLVE_SIDEBAR_COLLAPSED = resolveSidebarCollapsed/);
    assert.match(source, /function normalizeSidebarDefault\(value\)/);
    assert.match(source, /resolveSidebarCollapsed\(sidebarDefault\)/);
    assert.match(source, /syncSidebarCollapsedHtmlClass\(shouldCollapse\)/);
    assert.match(source, /localStorage\.setItem\('sidebar-collapsed', String\(shouldCollapse\)\)/);
    assert.match(source, /window\.APSTUDY_SET_SIDEBAR_COLLAPSED = applySidebarCollapsedState/);
    assert.match(source, /apstudy-sidebar-default-change/);
    assert.match(source, /const idleMs = 300000/);
    assert.match(source, /document\.addEventListener\('pointerdown', markActive/);
    assert.match(source, /document\.visibilityState === 'hidden' \|\| isIdle\(\)/);
});

test("mobile shell uses a hamburger drawer without breaking desktop sidebar persistence", async () => {
    const navbarSource = await sourceFor("static/js/core/navbar.js");
    const sidebarSource = await sourceFor("static/js/core/sidebar.js");
    const layoutStyles = await sourceFor("static/css/layout.css");

    assert.match(navbarSource, /id="navbar-menu-btn"/);
    assert.match(navbarSource, /window\.APSTUDY_SYNC_MOBILE_NAV_BUTTON/);
    assert.match(navbarSource, /APSTUDY_TOGGLE_MOBILE_SIDEBAR/);

    assert.match(sidebarSource, /window\.matchMedia\('\(max-width: 1024px\)'\)/);
    assert.match(sidebarSource, /sidebar-mobile-backdrop/);
    assert.match(sidebarSource, /mobile-sidebar-open/);
    assert.match(sidebarSource, /window\.APSTUDY_SET_MOBILE_SIDEBAR_OPEN = setMobileSidebarOpen/);
    assert.match(sidebarSource, /localStorage\.setItem\('sidebar-collapsed', String\(shouldCollapse\)\)/);

    assert.match(layoutStyles, /html\.apstudy-sidebar-collapsed/);
    assert.match(layoutStyles, /\.navbar-menu-button/);
    assert.match(layoutStyles, /\.sidebar-container\.mobile-open/);
    assert.match(layoutStyles, /transform: translateX\(-105%\)/);
    assert.match(layoutStyles, /body\.mobile-sidebar-open/);
    assert.match(layoutStyles, /min-height: 48px/);

    const collapsedWidthUsages = [...layoutStyles.matchAll(/var\(--sidebar-collapsed\)/g)];
    assert.ok(collapsedWidthUsages.length >= 3);
    for (const usage of collapsedWidthUsages) {
        assert.ok(
            cssBlockStackAt(layoutStyles, usage.index).includes("@media (min-width: 1025px)"),
            "collapsed sidebar geometry must not override the mobile drawer",
        );
    }
});

test("mobile dashboard keeps controls compact, visible, and touch friendly", async () => {
    const layoutStyles = await sourceFor("static/css/layout.css");
    const dashboardStyles = await sourceFor("static/css/dashboard.css");
    const profileStyles = await sourceFor("static/css/user-profile.css");
    const dashboardTemplate = await sourceFor("templates/dashboard.html");
    const dashboardSource = await sourceFor("static/js/dashboard/index.js");

    assert.match(layoutStyles, /\.navbar-avatar img\s*\{[^}]*width:\s*32px;[^}]*height:\s*32px;/s);
    assert.match(profileStyles, /\.user-profile-page \.navbar-avatar img\s*\{[^}]*width:\s*32px;[^}]*height:\s*32px;/s);
    assert.match(layoutStyles, /--navbar-height:\s*calc\(var\(--navbar-content-height\) \+ env\(safe-area-inset-top\)\)/);
    assert.match(dashboardTemplate, /viewport-fit=cover/);
    assert.match(dashboardTemplate, /aria-label="Edit dashboard layout"/);
    assert.match(dashboardTemplate, /class="dashboard-action-label">Edit layout/);
    assert.match(dashboardSource, /Finish editing dashboard layout/);
    assert.match(dashboardSource, /icon\.textContent = state\.editMode \? "done" : "dashboard_customize"/);
    assert.doesNotMatch(dashboardStyles, /\.dashboard-tile-link span:last-child/);
    assert.ok(dashboardStyles.includes("grid-auto-rows: auto;"));
    assert.ok(dashboardStyles.includes(".dashboard-day {\n        min-height: 44px;"));
    assert.ok(dashboardStyles.includes(".dashboard-header-link .dashboard-action-label"));
    assert.ok(dashboardStyles.includes("@media (pointer: coarse)"));
    assert.ok(dashboardStyles.includes("@media (max-width: 359px)"));
});

test("calendar and courses switch dense schedules to compact mobile agenda renderers", async () => {
    const calendarSource = [
        await sourceFor("static/js/calendar/index.js"),
        await sourceFor("static/js/calendar/views/agenda.js"),
        await sourceFor("static/js/calendar/views/render-shell.js"),
    ].join("\n");
    const coursesSource = [
        await sourceFor("static/js/courses/index.js"),
        await sourceFor("static/js/courses/calendar.js"),
    ].join("\n");
    const globalStyles = await sourceFor("static/css/global.css");
    const coursesStyles = await sourceFor("static/css/courses.css");

    assert.match(calendarSource, /COMPACT_CALENDAR_QUERY = window\.matchMedia\("\(max-width: 640px\)"\)/);
    assert.match(calendarSource, /buildMobileCalendarAgendaHtml/);
    assert.match(calendarSource, /calendar-mobile-agenda/);
    assert.match(calendarSource, /compactCalendar \? buildMobileCalendarAgendaHtml\(\)/);
    assert.match(calendarSource, /function buildCalendarSkeletonHtml\(\)/);
    assert.match(calendarSource, /function buildAssignmentsSkeletonHtml\(\)/);
    assert.match(calendarSource, /calendar-skeleton-week-frame/);
    assert.doesNotMatch(calendarSource, /APStudySkeleton\?\.(table|cards)/);

    assert.match(coursesSource, /COMPACT_COURSES_QUERY = window\.matchMedia\("\(max-width: 640px\)"\)/);
    assert.match(coursesSource, /buildMobileCoursesAgenda/);
    assert.match(coursesSource, /courses-mobile-agenda/);
    assert.match(coursesSource, /isCompactCoursesViewport\(\)/);

    assert.match(globalStyles, /\.calendar-mobile-agenda/);
    assert.match(coursesStyles, /\.courses-mobile-agenda/);
    assert.match(coursesStyles, /\.course-remove-button\.courses-mobile-remove/);
});

test("theme init normalizes theme aliases and persists pending settings safely", async () => {
    const source = await sourceFor("static/js/core/theme-init.js");

    assert.doesNotMatch(source, /console-discord\.js|document\.write/);
    assert.match(source, /var DARK_THEMES = \['obsidian-dark', 'nest-dark'\]/);
    assert.match(source, /return 'obsidian-dark'/);
    assert.match(source, /return 'parchment-light'/);
    assert.match(source, /return 'system-match'/);
    assert.match(source, /window\.APSTUDY_SET_THEME_PREFERENCE = applyThemePreference/);
    assert.match(source, /new CustomEvent\('apstudy-theme-change'/);
    assert.match(source, /fetch\('\/settings\/api\/interface-preferences'/);
    assert.match(source, /clearPendingTheme\(\)/);
});

test("dashboard uses the shared theme preference without page-specific remapping", async () => {
    const template = await sourceFor("templates/dashboard.html");

    assert.match(template, /js\/core\/theme-init\.js/);
    assert.doesNotMatch(template, /js\/dashboard\/theme\.js/);
});

test("slow workspace pages reserve their loaded layout with page-specific skeletons", async () => {
    const dashboardTemplate = await sourceFor("templates/dashboard.html");
    const taskTemplate = await sourceFor("templates/task.html");
    const calendarSource = await sourceFor("static/js/calendar/views/render-shell.js");
    const settingsTemplate = await sourceFor("templates/settings.html");

    assert.match(dashboardTemplate, /dashboard-skeleton-tile/);
    assert.doesNotMatch(dashboardTemplate, /apstudy-skeleton-cards/);
    assert.match(await sourceFor("static/css/dashboard.css"), /\.dashboard-loading\s*\{[^}]*grid-row:\s*span 2;/s);
    assert.match(await sourceFor("static/css/dashboard.css"), /\.dashboard-loading\s*\{[^}]*grid-row:\s*span 4;/s);
    assert.match(taskTemplate, /task-skeleton-layout/);
    assert.match(calendarSource, /calendar-skeleton-week-frame/);
    assert.match(settingsTemplate, /settings-skeleton-account-layout/);
    assert.match(settingsTemplate, /settings-skeleton-data-grid/);
});

test("notes, files, and tasks share the compact workspace title style", async () => {
    const layoutStyles = await sourceFor("static/css/layout.css");
    const notesTemplate = await sourceFor("templates/notes.html");
    const filesTemplate = await sourceFor("templates/files.html");
    const taskSource = await sourceFor("static/js/tasks/task.js");

    assert.match(layoutStyles, /\.workspace-page-title\s*\{[^}]*font-size:\s*20px;[^}]*font-weight:\s*740;/s);
    assert.match(notesTemplate, /aria-label="breadcrumb"/);
    assert.match(notesTemplate, /breadcrumb-list/);
    assert.match(filesTemplate, /class="breadcrumb files-breadcrumbs"/);
    assert.match(taskSource, /task-title workspace-page-title/);
});

test("console discord batches browser console output for server logs", async () => {
    const source = await sourceFor("static/js/core/console-discord.js");

    assert.match(source, /\/api\/debug\/console/);
    assert.match(source, /\["log", "info", "warn", "error", "debug"\]/);
    assert.match(source, /unhandledrejection/);
    assert.match(source, /sendBeacon/);
    assert.match(source, /forwarding/);
});

test("notes list guards destructive actions and supports safe card menus", async () => {
    const listSource = await sourceFor("static/js/notes/list.js");
    const cardsSource = await sourceFor("static/js/notes/list/cards.js");
    const dragSource = await sourceFor("static/js/notes/list/drag-drop.js");
    const template = await sourceFor("templates/notes.html");
    const styles = `${await sourceFor("static/css/notes.css")}\n${await sourceFor("static/css/notes/editor.css")}`;
    const source = [
        listSource,
        await sourceFor("static/js/notes/list/utils.js"),
        cardsSource,
        dragSource,
    ].join("\n");

    assert.match(source, /function apiJson\(url, options = \{\}\)/);
    assert.match(source, /APStudyPendingMutations\?\.track\(request, 'notes-save'\)/);
    assert.match(source, /apiJson\(`\/api\/notes\/\$\{encodeURIComponent\(noteId\)\}`/);
    assert.match(source, /apiJson\(`\/api\/notes\/folders\/\$\{encodeURIComponent\(folderId\)\}`/);
    assert.doesNotMatch(cardsSource, /NotesExport/);
    assert.doesNotMatch(cardsSource, /data-action="(?:move-earlier|move-later|export-txt|export-pdf)"/);
    assert.match(cardsSource, /data-action="share"/);
    assert.match(cardsSource, /APStudyNotesSharing\?\.open/);
    assert.match(source, /data-action="move"/);
    assert.match(listSource, /function positionCardMenu\(button, menu\)/);
    assert.match(listSource, /const placeBelow = spaceBelow >= spaceAbove/);
    assert.match(listSource, /handleCardMenuViewportScroll/);
    assert.match(listSource, /\['ArrowDown', 'ArrowUp', 'Home', 'End'\]/);
    assert.match(dragSource, /function canUseNoteDrag\(scope = global\)/);
    assert.match(dragSource, /min-width: 600px/);
    assert.match(dragSource, /delayOnTouchOnly: true/);
    assert.match(dragSource, /touchStartThreshold: 8/);
    assert.match(dragSource, /folder-card\.is-drop-target/);
    assert.match(template, /sortablejs@1\.15\.0\/Sortable\.min\.js/);
    assert.match(styles, /\.folder-card\.is-drop-target/);
    assert.match(styles, /\.note-card-ghost/);
    assert.doesNotMatch(cardsSource, /more_horiz/);
    assert.equal((cardsSource.match(/more_vert/g) || []).length, 2);
    assert.match(cardsSource, /openFolder\(folderId\)/);
    assert.match(listSource, /state\.currentFolderId = folderIdFromLocation\(\)/);
    assert.match(listSource, /function syncViewControls\(\)/);
    assert.match(listSource, /if \(els\.pageActions\) els\.pageActions\.hidden = readOnly/);
    assert.match(listSource, /if \(els\.notesEmptyNewNote\) els\.notesEmptyNewNote\.hidden = readOnly/);
    assert.match(template, /id="notes-root-breadcrumb"[^>]*aria-haspopup="menu"/);
    assert.match(template, /class="breadcrumb-trigger"/);
    assert.match(template, /id="notes-view-label">My Notes</);
    assert.match(template, /id="notes-new-button"[\s\S]*?add_2[\s\S]*?<span>New<\/span>/);
    assert.match(template, /id="notes-view-menu"[^>]*role="menu"/);
    assert.doesNotMatch(template, /class="notes-view-tabs"/);
    assert.match(template, /id="notes-folder-breadcrumb"/);
    assert.match(template, /id="notes-breadcrumb-separator-item"/);
    assert.doesNotMatch(template, /id="btn-share-folder"/);
    assert.doesNotMatch(cardsSource, /data-action="share-folder"/);
    assert.match(template, /js\/notes\/sharing\.js/);
    assert.doesNotMatch(template, /js\/notes\/export\.js/);
    assert.match(styles, /\.notes-page-actions\[hidden\],[\s\S]*?#notes-empty-new-note\[hidden\]\s*\{\s*display:\s*none !important;/);
    assert.match(styles, /@keyframes notes-popover-in/);
    assert.match(styles, /\.note-card\s*\{[^}]*min-height:\s*104px;[^}]*padding:\s*12px 10px 12px 14px;/s);
    assert.match(styles, /\.folder-card-menu-btn\s*\{[^}]*right:\s*6px;/s);
    assert.match(styles, /\.folder-card-menu\s*\{[^}]*position:\s*fixed;[^}]*overflow-y:\s*auto;/s);
});

test("notes editor keeps autosave, BlockNote schema, and load/save endpoints wired", async () => {
    const source = await sourceFor("static/js/notes/editor.js");
    const toolbarSource = await sourceFor("static/js/notes/toolbar.js");
    const catalogSource = await sourceFor("static/js/notes/editor/block-catalog.js");
    const keyboardSource = await sourceFor("static/js/notes/editor/keyboard-shortcuts.js");
    const printSource = await sourceFor("static/js/notes/editor/print.js");
    const styles = `${await sourceFor("static/css/notes.css")}\n${await sourceFor("static/css/notes/editor.css")}`;
    const editorTemplate = await sourceFor("templates/notes_editor.html");
    const notesTemplate = await sourceFor("templates/notes.html");

    assert.match(source, /const SAVE_DEBOUNCE_MS = 800/);
    assert.match(source, /const SAVED_TIME_REFRESH_MS = 60000/);
    assert.match(source, /const NORMAL_HISTORY_DEPTH = 100/);
    assert.match(source, /const LONG_DOCUMENT_HISTORY_DEPTH = 35/);
    assert.match(keyboardSource, /const preserveRangeSelectionShortcuts = Extension\.create\(/);
    assert.match(keyboardSource, /addProseMirrorPlugins\(\)/);
    assert.match(keyboardSource, /new Plugin\(\{/);
    assert.match(keyboardSource, /handleDOMEvents:\s*\{\s*keydown\(view, event\)/);
    assert.match(keyboardSource, /event\.shiftKey[\s\S]*?\(event\.metaKey \|\| event\.ctrlKey\)[\s\S]*?!event\.altKey[\s\S]*?event\.key === 'ArrowUp' \|\| event\.key === 'ArrowDown'/);
    assert.match(keyboardSource, /if \(!isRangeVerticalArrow\) return false;[\s\S]*?return true;/);
    assert.match(keyboardSource, /'Shift-Enter'/);
    assert.match(keyboardSource, /createSelectAllShortcuts/);
    assert.match(keyboardSource, /'Mod-a'/);
    assert.match(keyboardSource, /isEditorBodyFocused/);
    assert.match(source, /createSelectAllShortcuts\(\(\) => blockNoteEditorRef\)/);
    assert.match(source, /function historyDepthForDocument\(documentValue\)/);
    assert.match(source, /disableExtensions: \['history'\]/);
    assert.match(source, /History\.configure\(\{ depth: historyDepth, newGroupDelay: 500 \}\)/);
    assert.match(source, /let noteEditorReactRoot = null/);
    assert.match(source, /noteEditorReactRoot\?\.unmount\(\)/);
    assert.match(source, /window\.APStudyPageLifecycle\?\.register\?\.\(\{/);
    assert.match(source, /pause: releaseNoteEditorRuntime/);
    assert.match(editorTemplate, /data-navigation-retention="discard"/);
    assert.doesNotMatch(editorTemplate, /js\/core\/navbar\.js/);
    assert.doesNotMatch(editorTemplate, /<global class="thenav"/);
    assert.match(editorTemplate, /data-sidebar-mobile-toggle/);
    assert.match(styles, /body\.notes-editor-body\s*\{[^}]*--navbar-height:\s*0px;[^}]*grid-template-rows:\s*minmax\(0, 1fr\);/s);
    assert.match(styles, /body\.notes-editor-body main\s*\{[^}]*grid-row:\s*1;[^}]*max-width:\s*none;[^}]*padding:\s*0;/s);
    assert.match(source, /preserveRangeSelectionShortcuts/);
    assert.match(source, /listItemHardBreakShortcuts/);
    assert.match(source, /from '\.\/toolbar\.js'/);
    assert.match(source, /from '\.\/editor\/block-catalog\.js'/);
    assert.match(source, /await import\('\.\/editor\/print\.js'\)/);
    assert.match(toolbarSource, /const notesEditorSchema = BlockNoteSchema\.create/);
    assert.match(toolbarSource, /createReactBlockSpec/);
    assert.match(toolbarSource, /type: 'callout'/);
    assert.match(toolbarSource, /type: 'bookmark'/);
    assert.match(toolbarSource, /type: 'divider'/);
    assert.match(toolbarSource, /const lazyImageBlock = createReactBlockSpec\(imageBlockConfig/);
    assert.match(toolbarSource, /function LazyImagePreview\(\{ url, alt, width, alignment = 'left' \}\)/);
    assert.match(toolbarSource, /inlineImageSpec/);
    assert.match(toolbarSource, /IntersectionObserver/);
    assert.match(toolbarSource, /loading: 'lazy'/);
    assert.match(toolbarSource, /decoding: 'async'/);
    assert.match(toolbarSource, /fetchPriority: 'low'/);
    assert.match(toolbarSource, /parse: imageParse/);
    assert.match(toolbarSource, /isCollapsed: collapsedProp/);
    assert.match(toolbarSource, /createReactStyleSpec/);
    assert.match(toolbarSource, /fontSizeStyle/);
    assert.doesNotMatch(source, /FormattingToolbarController/);
    assert.doesNotMatch(source, /function NotesSelectionToolbar\(\)/);
    assert.doesNotMatch(source, /function toolbarButton\(/);
    assert.match(source, /function NotesSlashMenu\(props\)/);
    assert.match(source, /function NotesSideMenu\(props\)/);
    assert.match(source, /freezeMenu\?\.\(\)/);
    assert.match(source, /unfreezeMenu\?\.\(\)/);
    assert.match(source, /document\.addEventListener\('pointerdown', handlePointerDown\)/);
    assert.match(source, /event\.key !== 'Escape'/);
    assert.match(source, /draggable: true/);
    assert.match(source, /onDragStart: \(event\) => blockDragStart\?\.\(event, block\)/);
    assert.match(source, /onDragEnd: blockDragEnd/);
    assert.match(source, /selectActionBlock\(\); moveSelectedBlocks\('up'\); closeMenu\(\)/);
    assert.match(source, /selectActionBlock\(\); moveSelectedBlocks\('down'\); closeMenu\(\)/);
    assert.match(source, /formattingToolbar: false/);
    assert.match(source, /slashMenu: false/);
    assert.match(source, /filePanel: false/);
    assert.match(source, /function handleNotesPaste\(\{ event, editor, defaultPasteHandler \}\)/);
    assert.match(source, /pasteHandler: handleNotesPaste/);
    assert.match(source, /from '\.\/editor\/markdown-repair\.js'/);
    assert.match(source, /normalizeClipboardMarkdown/);
    assert.match(source, /normalizeCopiedPlainText/);
    assert.match(source, /const looksStructured = clipboardTextLooksStructured\(plainText\)/);
    assert.match(source, /if \(looksStructured\) \{\s*void editor\.pasteMarkdown\?\.\(normalizeClipboardMarkdown\(plainText\)\);/);
    assert.match(source, /prioritizeMarkdownOverHTML: false/);
    assert.match(source, /plainTextAsMarkdown: false/);
    assert.doesNotMatch(source, /clipboardTextHasMarkdownSyntax/);
    assert.match(source, /clipboardData\.setData\('text\/plain', normalizeCopiedPlainText\(plainText\)\)/);
    assert.match(source, /clipboardData\?\.getData\('blocknote\/html'\)/);
    assert.match(source, /function safeSetBlockSelection\(anchor, head = anchor\)/);
    assert.match(source, /editorInstance\.setTextCursorPosition\?\.\(anchor\)/);
    assert.doesNotMatch(source, /setSelection\?\.\(block, block\)/);
    assert.match(source, /let latestDocumentSnapshot = null/);
    assert.match(source, /function currentDocumentSnapshot\(\)/);
    assert.match(source, /function notePayloadFingerprint\(title, content\)/);
    assert.match(source, /let lastSavedPayloadFingerprint = ''/);
    assert.doesNotMatch(source, /lastSavedPayloadHash/);
    assert.match(source, /const content = JSON\.stringify\(documentSnapshot\)/);
    assert.doesNotMatch(source, /const updatedNote = await response\.json\(\)/);
    assert.doesNotMatch(source, /if \(normalizeEditorDocument\(\)\) return;/);
    assert.doesNotMatch(source, /schedulePastedContentNormalization|replaceBlocks\(documentSnapshot/);
    assert.match(source, /let activePageSetupTrigger = null/);
    assert.match(source, /activePageSetupTrigger = trigger \|\| null/);
    assert.match(source, /activePageSetupTrigger\?\.contains\(event\.target\)/);
    assert.doesNotMatch(source, /application\/x-nest-blocknote\+json/);
    assert.match(source, /notes\/tools\/link-preview/);
    assert.match(source, /function toggleHeadingCollapse/);
    assert.match(source, /function syncHeadingCollapseChrome/);
    assert.match(catalogSource, /export const BLOCK_CATALOG = \[/);
    assert.match(catalogSource, /key: 'bookmark'/);
    assert.match(catalogSource, /key: 'image'/);
    assert.match(catalogSource, /key: 'video'/);
    assert.match(catalogSource, /export const FORMAT_COLORS = \[/);
    assert.match(catalogSource, /export const FONT_SIZE_PRESETS = \[/);
    assert.match(source, /window\.APStudyPendingMutations\?\.track\(fetch\(`\/api\/notes\/\$\{noteId\}`/);
    assert.match(source, /fetch\(`\/api\/notes\/\$\{noteId\}`, \{ signal: editorLoadController\.signal \}\)/);
    assert.match(source, /triggerDebouncedSave\(\)/);
    assert.match(source, /React\.createElement\(NoteEditor, \{/);
    assert.match(source, /function insertBlockPayload\(block/);
    assert.match(source, /editorInstance\.insertBlocks\?\.\(/);
    assert.match(source, /ATOM_BLOCK_TYPES\.has\(insertedOrUpdated\.type\) \|\| insertedOrUpdated\.type === 'table'/);
    assert.match(source, /editorInstance\.openSuggestionMenu\?\.\('\/'\)/);
    assert.match(source, /function canRunHistoryAction\(action\)/);
    assert.match(source, /editorInstance\._tiptapEditor\?\.can\?\.\(\)/);
    assert.match(source, /button\.disabled = disabled/);
    assert.match(styles, /--notes-bg-base: var\(--color-surface-container-lowest\)/);
    assert.match(styles, /--notes-bg-toolbar: var\(--color-surface-container-low\)/);
    assert.match(styles, /--notes-editor-content-width: 720px/);
    assert.match(styles, /max-width: var\(--notes-editor-content-width\)/);
    assert.match(styles, /\.notes-writing-toolbar\s*\{[^}]*position:\s*sticky;[^}]*top:\s*var\(--notes-topbar-height\);[^}]*z-index:\s*45;/s);
    assert.match(styles, /\.notes-toolbar-segment \+ \.notes-toolbar-segment::before,[^{]*\{[^}]*width:\s*1px;[^}]*height:\s*22px;/s);
    assert.match(styles, /@media \(min-width:\s*701px\)[\s\S]*?\.editor-title-input\s*\{[^}]*max-width:\s*none;[^}]*text-align:\s*center;/s);
    assert.match(styles, /\.notes-writing-toolbar\s*\{[^}]*order:\s*-1;[^}]*margin:\s*0 calc\(-1 \* var\(--notes-editor-surface-padding-inline\)\);[^}]*border-radius:\s*0;/s);
    assert.match(styles, /@media \(min-width:\s*701px\)[\s\S]*?\.notes-writing-toolbar\s*\{[^}]*width:\s*auto;/s);
    assert.ok(styles.includes('content: "\\2022" !important;'));
    assert.ok(styles.includes('content: attr(data-index) "." !important;'));
    assert.match(styles, /\.bn-block-content\[data-content-type="bulletListItem"\],[^{]*\.bn-block-content\[data-content-type="numberedListItem"\]\s*\{[^}]*align-items:\s*baseline !important;/s);
    assert.match(styles, /\.bn-block-content\[data-content-type="checkListItem"\] > div > div:has\(> input\[type="checkbox"\]\)\s*\{[^}]*align-items:\s*center;[^}]*min-height:\s*1\.7em;/s);
    assert.match(styles, /\.blocknote-container \.bn-block-content\[data-content-type="checkListItem"\] input\[type="checkbox"\]\s*\{[^}]*margin:\s*0 0\.6em 0 0 !important;/s);
    assert.match(styles, /\.blocknote-container \.ProseMirror-selectednode,[^{]*\{[^}]*outline:\s*none !important;[^}]*box-shadow:\s*none !important;/s);
    assert.match(styles, /\.blocknote-container \[data-content-type="table"\] table/);
    assert.match(styles, /\.bn-inline-content:has\(> \.ProseMirror-trailingBreak:only-child\):before\s*\{[^}]*font-size:\s*inherit !important;[^}]*font-weight:\s*inherit !important;[^}]*line-height:\s*inherit !important;/s);
    assert.match(styles, /\.blocknote-container \.bn-block-content\[data-content-type="quote"\]/);
    assert.match(styles, /\.blocknote-container \.bn-block-content\[data-content-type="codeBlock"\]/);
    assert.match(styles, /\.notes-callout-block/);
    assert.match(styles, /\.notes-bookmark-block/);
    assert.match(styles, /\.notes-lazy-image-placeholder/);
    assert.match(styles, /\.notes-lazy-image-frame/);
    assert.match(styles, /\.notes-slash-menu/);
    assert.match(styles, /\.notes-side-tools/);
    assert.match(styles, /\.notes-side-button \.material-symbols-outlined\s*\{[^}]*font-size:\s*20px;[^}]*line-height:\s*1;/s);
    assert.match(styles, /\.notes-block-select-handle\s*\{[^}]*cursor:\s*grab;/s);
    assert.match(styles, /\.notes-side-menu\s*\{[^}]*left:\s*calc\(100% \+ 6px\);/s);
    assert.doesNotMatch(styles, /\.notes-context-(?:toolbar|button|popover|menu-item)/);
    assert.match(styles, /\.notes-block-hidden-by-collapse/);
    assert.match(styles, /\.blocknote-container \[data-content-type="horizontalRule"\]/);
    assert.match(styles, /\.blocknote-container \[data-content-type="pageBreak"\],[\s\S]*?align-items:\s*center !important;[\s\S]*?min-height:\s*1\.75em !important;/);
    assert.match(styles, /\.notes-divider-block\s*\{[^}]*margin:\s*0 !important;/s);
    assert.match(styles, /\.blocknote-container \.bn-block-group \.bn-block-group/);
    assert.match(styles, /\.blocknote-container \.bn-block-group \.bn-block-group\s*\{[^}]*min-height:\s*0 !important;/s);
    assert.match(styles, /\.blocknote-container \.bn-block-group \.bn-block-group > \.bn-block-outer::before\s*\{[^}]*content:\s*none !important;[^}]*border-left:\s*0 !important;[^}]*display:\s*none !important;/s);
    assert.match(styles, /\.blocknote-container \.bn-editor > \.bn-block-group,[\s\S]*?\.blocknote-container \.ProseMirror > \.bn-block-group\s*\{[^}]*min-height:\s*200px;/);
    assert.doesNotMatch(styles, /\.blocknote-container \[class\*="bn-block-group"\]\s*\{[^}]*min-height:\s*200px;/s);
    assert.match(styles, /\.notes-writing-toolbar button:disabled/);
    assert.match(editorTemplate, /data-toolbar-menu-trigger="add-block"/);
    assert.match(editorTemplate, /data-toolbar-menu-trigger="font-size"/);
    assert.match(editorTemplate, /data-toolbar-menu-trigger="text-color"/);
    assert.match(editorTemplate, /data-toolbar-menu-trigger="highlight-color"/);
    assert.doesNotMatch(editorTemplate, /more_horiz|data-toolbar-menu-trigger="block-actions"|data-toolbar-menu="block-actions"/);
    assert.match(editorTemplate, /data-editor-action="page-setup"[^>]*aria-expanded="false"/);
    assert.match(editorTemplate, /data-editor-action="page-setup"[^>]*aria-controls="notes-page-setup-popover"/);
    assert.match(editorTemplate, /data-editor-action="page-setup"[\s\S]*?data-note-print[\s\S]*?notes-toolbar-history/);
    assert.match(editorTemplate, /class="notes-viewer-print-button"[^>]*data-note-print[^>]*disabled/);
    assert.match(printSource, /editor\.blocksToHTMLLossy\(printableBlocks\)/);
    assert.match(printSource, /hiddenBlockIds/);
    assert.match(printSource, /windowRef\.addEventListener\?\.\('afterprint'/);
    assert.match(printSource, /Symbol\.for\('apstudy\.notes\.active-print-promise'\)/);
    assert.match(printSource, /querySelectorAll\?\.\('\.notes-print-surface'\)/);
    assert.match(printSource, /PRINT_IMAGE_TIMEOUT_MS = 2000/);
    assert.match(source, /document\.addEventListener\('click', handleNotePrintClick, true\)/);
    assert.match(source, /document\.addEventListener\('keydown', handleNotePrintShortcut, true\)/);
    assert.match(printSource, /isNotePrintShortcut\(event\)/);
    assert.match(source, /hiddenBlockIds: hidden\.keys\(\)/);
    assert.match(source, /APStudyToast\?\.error\?\.\('Try again in a moment\.', \{ title: 'Couldn’t prepare note for printing' \}\)/);
    assert.match(styles, /@media print\s*\{/);
    assert.match(styles, /body\.notes-editor-body\.notes-print-prepared > \.notes-print-surface/);
    assert.match(styles, /body\.notes-editor-body > :not\(\.notes-print-surface\)/);
    assert.match(styles, /body\.notes-editor-body > \.apstudy-skip-link/);
    assert.doesNotMatch(styles, /body\.notes-editor-body:not\(\.notes-print-prepared\)/);
    assert.match(styles, /background:\s*#ffffff !important/);
    assert.match(styles, /--notes-print-font-family/);
    assert.match(styles, /--notes-print-side-margin/);
    assert.match(editorTemplate, /css\/notes\.css'\) }}\?v=notes-print-3/);
    assert.match(editorTemplate, /notes-editor-bundle-17/);
    assert.match(editorTemplate, /notes-editor-v17\.js/);
    assert.doesNotMatch(editorTemplate, /notes-editor-v17\.js[^"']*\?v=/);
    assert.match(await sourceFor("static/js/notes/list/utils.js"), /notes-editor-v17\.js/);
    assert.match(editorTemplate, /data-block-type="codeBlock"/);
    assert.match(editorTemplate, /data-block-type="callout"/);
    assert.match(source, /action === 'copy-blocks'/);
    assert.match(source, /action === 'cut-blocks'/);
    assert.match(source, /action === 'duplicate-blocks'/);
    assert.match(source, /action === 'delete-blocks'/);
    assert.match(source, /action === 'move-blocks-up'/);
    assert.match(source, /action === 'move-blocks-down'/);
    assert.match(source, /event\.altKey && !event\.shiftKey && !event\.metaKey && !event\.ctrlKey && event\.key === 'ArrowUp'/);
    assert.match(source, /event\.altKey && !event\.shiftKey && !event\.metaKey && !event\.ctrlKey && event\.key === 'ArrowDown'/);
    assert.match(source, /action === 'toggle-heading-collapse'/);
    assert.match(editorTemplate, /data-toolbar-menu="overflow" role="toolbar" aria-label="More writing tools" aria-orientation="horizontal"/);
    assert.match(styles, /\.notes-toolbar-overflow-menu\s*\{[^}]*display:\s*flex;[^}]*width:\s*max-content;[^}]*flex-wrap:\s*nowrap;[^}]*overflow-x:\s*auto;/s);
    assert.match(styles, /\.notes-toolbar-overflow-menu \.notes-toolbar-segment\s*\{[^}]*width:\s*auto;[^}]*flex:\s*0 0 auto;[^}]*flex-wrap:\s*nowrap;/s);
    assert.match(styles, /\.notes-toolbar-overflow-menu \.notes-toolbar-label,[^{]*\{[^}]*display:\s*none;/s);
    assert.doesNotMatch(styles, /\.notes-toolbar-overflow-menu \.notes-toolbar-segment\s*\{[^}]*width:\s*100%;/s);
    assert.match(source, /const isOverflowToolbar = menu\.classList\.contains\('notes-toolbar-overflow-menu'\)/);
    assert.match(source, /floatingPopoverPosition\(\{/);
    assert.match(source, /position\.left - originRect\.left/);
    assert.match(source, /position\.top - originRect\.top/);
    assert.doesNotMatch(styles, /\.notes-page-setup-popover\s*\{[^}]*transform:\s*translate\(-50%, -50%\)/s);
    assert.match(source, /menu\.style\.maxWidth = isOverflowToolbar/);
    assert.match(source, /b\.priority - a\.priority \|\| b\.index - a\.index/);
    assert.match(source, /handlePageSetupToolbarClick\([\s\S]*?writingToolbar,[\s\S]*?pageSetupPopover/);
    assert.doesNotMatch(source, /pageSetupTrigger\?\.addEventListener\('click'/);
    assert.match(source, /activePageSetupTriggerRect = triggerRect \|\| usableTriggerRect\(trigger\)/);
    assert.match(source, /Symbol\.for\('apstudy\.notes\.editor\.runtime'\)/);
    assert.match(source, /claimElementBinding\(writingToolbar, 'notesEditorToolbarBound'\)/);
    assert.match(source, /action === 'basic-style'[\s\S]*?button\.removeAttribute\('aria-pressed'\)/);
    assert.match(source, /editorPage\?\.addEventListener\('scroll', schedulePageSetupPopoverPosition/);
    assert.match(source, /window\.addEventListener\('resize', schedulePageSetupPopoverPosition\)/);
    assert.match(editorTemplate, /id="notes-page-setup-popover"[^>]*role="dialog"[^>]*aria-describedby="notes-page-setup-description"/);
    assert.match(styles, /\.notes-setup-margin-field\s*\{[^}]*display:\s*grid;/s);
    assert.doesNotMatch(editorTemplate, /<global class="thefooter"><\/global>/);
    assert.match(notesTemplate, /<global class="thefooter"><\/global>/);
    assert.doesNotMatch(styles, /STATIC TOOLBAR/);
    assert.doesNotMatch(styles, /padding-top:\s*58px/);
    assert.doesNotMatch(styles, /#4ade80/i);
});

test("notes sharing keeps canonical links, view-only capabilities, and folder inheritance UI wired", async () => {
    const listSource = await sourceFor("static/js/notes/list.js");
    const cardsSource = await sourceFor("static/js/notes/list/cards.js");
    const sharingSource = await sourceFor("static/js/notes/sharing.js");
    const editorSource = await sourceFor("static/js/notes/editor.js");
    const editorTemplate = await sourceFor("templates/notes_editor.html");
    const styles = `${await sourceFor("static/css/notes.css")}\n${await sourceFor("static/css/notes/editor.css")}`;
    const notesTemplate = await sourceFor("templates/notes.html");
    const folderTemplate = await sourceFor("templates/notes_shared_folder.html");
    const navbarSource = await sourceFor("static/js/core/navbar.js");

    assert.doesNotMatch(`${listSource}\n${cardsSource}`, /\/notes\/editor\/\$\{/);
    assert.match(cardsSource, /global\.location\.href = `\/notes\/\$\{encodeURIComponent\(noteId\)\}`/);
    assert.match(listSource, /state\.viewMode === 'shared' \? '\/api\/notes\/shared' : '\/api\/notes'/);
    assert.doesNotMatch(listSource, /initDragDrop|Sortable/);
    assert.match(cardsSource, /folder\.is_shared \|\| readOnly \? 'folder_shared' : 'folder'/);
    assert.match(notesTemplate, /data-notes-view="shared"[\s\S]*?>Shared with Me</);
    assert.doesNotMatch(notesTemplate, /id="btn-share-folder"/);
    assert.doesNotMatch(cardsSource, /data-action="share-folder"/);

    assert.match(sharingSource, /\/api\/notes\/share-users\?q=/);
    assert.match(sharingSource, /public: modal\.querySelector\('\[data-share-public\]'\)\.value === 'public'/);
    assert.match(sharingSource, /expected_revision: sharing\.revision/);
    assert.match(sharingSource, /grants: users\.map\(\(user\) => \(\{ user_id: user\.id, role: normalizeRole\(user\.role\) \}\)\)/);
    assert.match(sharingSource, /invitations: pending\.map\(\(invite\) => \(\{ email: invite\.email, role: normalizeRole\(invite\.role\) \}\)\)/);
    assert.match(sharingSource, /Anyone with the link/);
    assert.match(sharingSource, /Viewer/);
    assert.match(sharingSource, /data-share-copy/);

    assert.match(editorSource, /let canEdit = noteContext\.access\?\.can_edit === true/);
    assert.match(editorSource, /editable: canEdit/);
    assert.match(editorSource, /if \(!canEdit \|\| !noteId \|\| !titleInput \|\| !editorInstance \|\| noteCollaborationEnabled\) return/);
    assert.match(editorSource, /canEdit \? React\.createElement\(SuggestionMenuController/);
    assert.match(editorSource, /canEdit \? React\.createElement\(SideMenuController/);
    assert.match(editorSource, /if \(!canEdit\) \{\s*button\?\.remove\(\);/);
    assert.match(editorTemplate, /id="notes-share-button"[\s\S]*?data-notes-share-resource="note"/);
    assert.match(editorTemplate, /js\/notes\/sharing\.js/);
    assert.match(editorTemplate, /Shared by <a class="notes-viewer-owner" href="\{\{ owner\.profile_url \}\}">\{\{ owner\.name \}\}<\/a>/);
    assert.match(styles, /body\.notes-editor-body\s*\{[^}]*--navbar-height:\s*0px;[^}]*display:\s*grid !important;[^}]*grid-template-rows:\s*minmax\(0, 1fr\);/s);
    assert.match(styles, /body\[data-note-read-only="true"\] \.blocknote-container \.notes-block-selected > \.bn-block\s*\{[^}]*background:\s*transparent;[^}]*box-shadow:\s*none;/s);
    assert.match(editorSource, /if \(!canEdit\) \{[\s\S]*?querySelectorAll\('\.notes-block-selected'\)[\s\S]*?lastSelectedBlockIds = new Set\(\);/);
    assert.match(editorTemplate, /{% if access\.can_edit %}[\s\S]*?notes-toolbar-page-setup/);
    assert.match(editorSource, /handlePageSetupToolbarClick\([\s\S]*?writingToolbar,[\s\S]*?pageSetupPopover,[\s\S]*?openPageSetupPopover,[\s\S]*?closePageSetupPopover/);

    assert.match(folderTemplate, /folder_shared/);
    assert.match(folderTemplate, /url_for\('dashboard\.note_document'/);
    assert.match(folderTemplate, /meta name="robots" content="noindex,nofollow"/);
    assert.match(navbarSource, /navPlaceholder\.dataset\.authenticated !== 'false'/);
    assert.match(navbarSource, /navbar-login-button/);
});

test("courses page keeps Atlas APIs, filtering state, and schedule constants connected", async () => {
    const source = await sourceFor("static/js/courses/index.js");
    const template = await sourceFor("templates/courses.html");
    const combinedSource = [
        source,
        await sourceFor("static/js/courses/utils.js"),
        await sourceFor("static/js/courses/filters.js"),
        await sourceFor("static/js/courses/panel.js"),
        await sourceFor("static/js/courses/calendar.js"),
        await sourceFor("static/js/courses/controls.js"),
    ].join("\n");
    const styles = await sourceFor("static/css/courses.css");

    assert.match(source, /const COURSE_DAYS = \[/);
    assert.match(source, /const COURSE_RESULT_LIMIT = 100/);
    assert.match(source, /selectedTerm: window\.APSTUDY_COURSES_DEFAULT_TERM/);
    assert.match(source, /activeCourseView: "search"/);
    assert.match(combinedSource, /button\[data-course-view\]/);
    assert.match(combinedSource, /state\.activeCourseView = nextView/);
    assert.match(source, /fetchJson\("\/api\/atlas\/terms"\)/);
    assert.match(source, /new URLSearchParams\(\{\s*term,\s*include_cancelled: "0",\s*\}\)/);
    assert.match(source, /params\.set\("q", query\)/);
    assert.match(source, /COURSE_LIVE_HYDRATION_OVERSCAN = 5/);
    assert.match(source, /fetchJson\("\/api\/courses\/section-status\/batch"/);
    assert.match(source, /body: JSON\.stringify\(\{ section_ids: sectionIds, force: false \}\)/);
    assert.doesNotMatch(source, /APStudyAtlasLive\.fetchSectionStatus/);
    assert.match(source, /fetchJson\("\/api\/courses\/saved"\)/);
    assert.match(source, /fetchJson\("\/api\/courses\/tracks"\)/);
    assert.match(source, /buildSectionSearchBlob\(normalized\)/);
    assert.match(combinedSource, /state\.activeCourseView === "selected"/);
    assert.match(combinedSource, /state\.activeCourseView === "tracked"/);
    assert.doesNotMatch(combinedSource, /\.filter\(\(\[, track\]\) => Boolean\(track\?\.enabled\)\)/);
    assert.match(source, /allowedTrackIntervals: \[30\]/);
    assert.match(combinedSource, /data-track-interval-section-id/);
    assert.match(combinedSource, /data-remove-track-id/);
    assert.match(combinedSource, /checking every 3 hours until it closes/);
    assert.match(source, /removedSelectedSections: new Map\(\)/);
    assert.match(source, /state\.removedSelectedSections\.set\(String\(sectionId\), \{ \.\.\.removedSection, id: String\(sectionId\) \}\)/);
    assert.match(combinedSource, /return sections\.sort\(compareCourseSections\)/);
    assert.match(combinedSource, /\.filter\(Boolean\)\s*\.sort\(compareCourseSections\)/);
    assert.match(combinedSource, /function layoutConflictGroup\(events\)/);
    assert.match(combinedSource, /event\.start >= activeGroup\.end/);
    assert.match(combinedSource, /course-card-tracked/);
    assert.match(combinedSource, /notifications_active/);
    assert.match(combinedSource, /if \(section\?\.is_cancelled\) return false/);
    assert.match(combinedSource, /if \(seats === 0\) return true/);
    assert.match(combinedSource, /normalizeScheduleDisplay/);
    assert.match(combinedSource, /detail: `\$\{formatAtlasTime\(meeting\.start\)\}-\$\{formatAtlasTime\(meeting\.end\)\}`/);
    assert.doesNotMatch(source, /detail: `[^`]*\| Sec/);
    assert.match(combinedSource, /No tracked courses match your filters\./);
    assert.match(styles, /\.courses-view-toggle/);
    assert.match(styles, /\.course-section-inline/);
    assert.match(styles, /\.course-card-tracked/);
    assert.match(styles, /\.course-card-meta-row/);
    assert.match(combinedSource, /function buildCourseResultsSkeletonHtml\(/);
    assert.match(combinedSource, /function buildCourseScheduleSkeletonHtml\(/);
    assert.match(template, /courses-results-skeleton/);
    assert.match(template, /courses-schedule-skeleton/);
    assert.match(styles, /\.courses-skeleton-week-frame/);
});

test("files page keeps upload limits, modal elements, and share/delete endpoints wired", async () => {
    const source = await sourceFor("static/js/files/index.js");
    const utilsSource = await sourceFor("static/js/files/utils.js");
    const renderersSource = await sourceFor("static/js/files/renderers.js");
    const modalsSource = await sourceFor("static/js/files/modals.js");
    const workflowsSource = await sourceFor("static/js/files/workflows.js");
    const eventsSource = await sourceFor("static/js/files/events.js");
    const combinedSource = `${source}\n${utilsSource}\n${renderersSource}\n${modalsSource}\n${workflowsSource}\n${eventsSource}`;

    assert.match(source, /const MAX_FILE_SIZE_BYTES = Number\(CONFIG\.maxFileSize\) \|\| \(50 \* 1024 \* 1024\)/);
    assert.match(source, /const MAX_FILE_SIZE_LABEL = String\(CONFIG\.maxFileSizeLabel/);
    assert.match(source, /const MAX_UPLOAD_FILES = Number\(CONFIG\.maxUploadFiles\) \|\| 5/);
    assert.match(source, /allowedExpiryOptions/);
    assert.match(combinedSource, /parseUploadResponse/);
    assert.match(combinedSource, /uploadErrorMessage/);
    assert.match(combinedSource, /function notify\(message, type = "info", options = \{\}\)/);
    assert.match(workflowsSource, /exceeds the \$\{maxFileSizeLabel\} limit/);
    assert.match(combinedSource, /file-share-upload-modal/);
    assert.match(combinedSource, /file-share-confirm-modal/);
    assert.match(combinedSource, /file-share-share-modal/);
    assert.match(combinedSource, /bulkDelete/);
    assert.match(combinedSource, /copyShareButton/);
});

test("settings page keeps account, theme, calendar, and destructive endpoints centralized", async () => {
    const template = await sourceFor("templates/settings.html");
    const styles = await sourceFor("static/css/settings.css");
    const invitesSource = await sourceFor("static/js/settings/invites.js");
    const source = [
        await sourceFor("static/js/settings/index.js"),
        await sourceFor("static/js/settings/utils.js"),
        await sourceFor("static/js/settings/calendar.js"),
        await sourceFor("static/js/settings/profile.js"),
        await sourceFor("static/js/settings/preferences.js"),
        await sourceFor("static/js/settings/account.js"),
        await sourceFor("static/js/settings/notifications.js"),
        invitesSource,
      ].join("\n");

    for (const endpoint of [
        "/settings/api/bootstrap",
        "/settings/api/profile",
        "/settings/api/avatar-upload",
        "/settings/api/feed-url",
        "/settings/api/interface-preferences",
        "/settings/api/export",
        "/settings/api/account/delete",
        "/settings/api/account/recovery",
        "/settings/api/invites",
    ]) {
        assert.match(source, new RegExp(endpoint.replaceAll("/", "\\/")));
    }

    assert.doesNotMatch(template, /appwrite@|js\/core\/appwrite\.js/);
    assert.match(template, /\{% set settings_assets_version = 'settings-[^']+' %\}/);
    assert.match(template, /data-probe-appwrite-session="false"/);
    assert.match(template, /id="settings-skeleton"/);
    assert.match(template, /settings-sections[^"]*is-loading/);
    assert.match(source, /const SETTINGS_SECTION_IDS = \['account', 'tier', 'data', 'preferences', 'notifications'\]/);
    assert.match(source, /section\.hidden = !isActive/);
    assert.match(styles, /\.settings-section\[hidden\]\s*\{\s*display:\s*none/);
    assert.doesNotMatch(source, /global\.alert|window\.alert/);
    assert.match(source, /push_configured/);
    assert.match(source, /const SETTINGS_INTERFACE_THEMES = \[/);
    assert.match(source, /'nest-light'/);
    assert.match(source, /'nest-dark'/);
    assert.match(source, /function renderSettingsSkeleton\(\)/);
    assert.match(template, /settings-page-skeleton/);
    assert.match(template, /settings-skeleton-account-layout/);
    assert.match(template, /settings-skeleton-data-grid/);
    assert.match(template, /settings-skeleton-tier-grid/);
    assert.match(template, /settings-theme-preview/);
    assert.match(template, /<strong>System<\/strong><small>Match your device<\/small>/);
    assert.doesNotMatch(template, /class="settings-theme-choice theme-[^"]+"[^>]*><\/button>/);
    assert.doesNotMatch(template.slice(template.indexOf('id="preferences"'), template.indexOf('id="notifications"')), /data-toggle-field="(email_notifications|product_updates|task_sound_enabled|chat_sound_enabled)"/);
    assert.match(template.slice(template.indexOf('id="notifications"')), /<h3>Updates &amp; sounds<\/h3>/);
    assert.doesNotMatch(source, /APStudySkeleton\?\.fieldSet/);
    assert.match(source, /function clearSettingsSkeleton\(\)/);
    assert.match(source, /interface_theme: interfaceTheme/);
    assert.match(template, /class="notification-channel-grid"/);
    assert.match(template, /data-push-toggle="course_push_enabled"/);
    assert.match(template, /data-push-toggle="course_email_enabled"/);
    assert.match(template, /Email isn’t available for calendar and tasks/);
    assert.match(source, /button\.disabled = !hasNotificationDevice/);
    assert.match(styles, /\.notification-channel-row:hover/);
    assert.match(source, /const SETTINGS_MAX_OTHER_CALENDARS = 10/);
    assert.match(source, /const USERNAME_RESERVED = new Set\(\[/);
    assert.match(source, /window\.APSTUDY_THEME_PREFERENCE = interfaceTheme/);
    assert.match(source, /apstudy-sidebar-default-change/);
    assert.match(source, /function renderEntitlements\(\)/);
    assert.match(source, /data\.storage_limit_bytes/);
    assert.match(template, /settings-tier-card/);
    assert.match(template, /data-tab="tier">Tier &amp; Invites/);
    assert.match(template, /settings-invites-card/);
    assert.match(template, /id="settings-invites-list"/);
    assert.match(invitesSource, /function focusInviteAction\(inviteId, action\)/);
    assert.match(invitesSource, /focusInviteAction\(inviteId, focusAction\)/);
    assert.match(invitesSource, /data-invite-action="rename"]'\)\?\.focus\(\)/);
    assert.match(invitesSource, /settings-invite-count--invited/);
    assert.match(invitesSource, /People who signed up using this invite link\./);
    assert.match(invitesSource, /People who completed onboarding and took a qualifying action\./);
    assert.match(styles, /\.settings-invite-list\s*\{[^}]*grid-template-columns: repeat\(2/);
    assert.match(styles, /\.settings-invite-row\s*\{[^}]*border-radius: var\(--radius-card\)/);
    assert.match(styles, /\.settings-invite-count:hover/);
    const tierSection = template.slice(template.indexOf('id="tier"'), template.indexOf('id="data"'));
    const dataSection = template.slice(template.indexOf('id="data"'), template.indexOf('id="preferences"'));
    assert.match(tierSection, /settings-tier-card/);
    assert.match(tierSection, /settings-invites-card/);
    assert.doesNotMatch(tierSection, /id="settings-export-data"/);
    assert.match(dataSection, /id="settings-export-data"/);
    assert.match(dataSection, /settings-stat-card/);
    assert.match(template, /settings-preview-tier-badge/);
});

test("task app shell keeps data-layer wiring, destructive confirms, and mount contract", async () => {
    const source = await sourceFor("static/js/tasks/task.js");
    const helperSource = await sourceFor("static/js/tasks/task-app-helpers.js");
    const template = await sourceFor("templates/task.html");

    assert.match(source, /from "\.\/task-components\.js"/);
    assert.match(source, /from "\.\/task-app-helpers\.js"/);
    assert.match(source, /from "\.\/task-data\.js"/);
    assert.match(source, /from "\.\/task-utils\.js"/);
    assert.match(helperSource, /function requestDestructiveAction\(\{ title, message, acceptLabel \}\)/);
    assert.match(helperSource, /window\.APStudyConfirm\?\.request\?\.\(\{/);
    assert.match(helperSource, /function groupTasksByList\(lists, tasks, listById\)/);
    assert.match(helperSource, /function PrintSheet\(\{ list, tasks(?:, includeCompleted = false)? \}\)/);
    assert.match(source, /const \{ lists: nextLists, tasks: nextTasks, preferences \} = await fetchTaskBoard\(\)/);
    assert.match(source, /buildCompletedTaskOptimistic\(task, completed\)/);
    assert.match(source, /completeTaskRecord\(task\.id, completed, optimistic\.occurrenceKey\)/);
    assert.match(source, /buildListOrderUpdates\(orderedIds\)/);
    assert.match(source, /taskOrderUpdatesFromDocument\(\)/);
    assert.match(source, /taskReactRoot = createRoot\(mount\)/);
    assert.match(source, /taskReactRoot\.render\(h\(TaskInitializationBoundary/);
    assert.match(source, /window\.APStudyTaskLoader\?\.ready\?\.\(\)/);
    assert.match(source, /window\.APStudyTaskLoader\?\.fail\?\.\(error\)/);
    assert.match(source, /taskReactRoot\?\.unmount\(\)/);
    assert.match(source, /window\.APStudyPageLifecycle\?\.register\?\.\(\{/);
    assert.match(source, /createTaskSounds\(\{ completeSound, uncompleteSound \}\)/);
    assert.match(source, /function buildTaskLoadingHtml\(\)/);
    assert.match(template, /task-skeleton-layout/);
    assert.doesNotMatch(source, /APStudySkeleton\?\.fieldSet/);
    assert.doesNotMatch(source, /use-sound|useSound/);
    assert.doesNotMatch(template, /appwrite@|js\/core\/appwrite\.js|use-sound/);
    assert.match(template, /data-probe-appwrite-session="false"/);
    assert.match(template, /data-command-palette-preload="false"/);
});

test("appwrite bootstrap exposes configured SDK clients globally", async () => {
    const source = await sourceFor("static/js/core/appwrite.js");

    assert.match(source, /document\.querySelector\('meta\[name="apstudy-appwrite-endpoint"\]'\)\?\.content/);
    assert.match(source, /document\.querySelector\('meta\[name="apstudy-appwrite-project-id"\]'\)\?\.content/);
    assert.match(source, /const APPWRITE_ENDPOINT = configMeta\.endpoint \|\| "https:\/\/nyc\.cloud\.appwrite\.io\/v1"/);
    assert.match(source, /const APPWRITE_PROJECT_ID = configMeta\.projectId \|\| "69f77663000c16abdff2"/);
    assert.match(source, /new Appwrite\.Client\(\)/);
    assert.match(source, /\.setEndpoint\(APPWRITE_ENDPOINT\)/);
    assert.match(source, /\.setProject\(APPWRITE_PROJECT_ID\)/);
    assert.match(source, /window\.account = account/);
    assert.match(source, /window\.databases = databases/);
    assert.match(source, /window\.storage = storage/);
    assert.doesNotMatch(source, /window\.presences/);
    assert.doesNotMatch(source, /window\.realtime/);
    assert.doesNotMatch(source, /window\.Channel = Appwrite\.Channel/);
});

test("calendar context menu keeps task, event, override, and keyboard flows wired", async () => {
    const source = await sourceFor("static/js/calendar/events/context-menu.js");

    assert.match(source, /const rootSelector = "#calendar-view-root"/);
    assert.match(source, /role", "menu"/);
    assert.match(source, /const eventSelector = "\[data-event-ref\], \[data-event-id\]"/);
    assert.match(source, /window\.openCalendarEventForm\?\.\(\{ mode: "create"/);
    assert.match(source, /mode: isImportedEvent\(event\) \? "override" : "edit"/);
    assert.match(source, /window\.APStudyConfirm\?\.request\?\.\(\{/);
    assert.match(source, /fetch\("\/api\/calendar\/event-overrides\/hide"/);
    assert.match(source, /fetch\(`\/api\/calendar\/events\/\$\{encodeURIComponent\(event\.id \|\| context\.eventId\)\}`/);
    assert.match(source, /localStorage\.removeItem\("calendarEventsCache"\)/);
    assert.match(source, /event\.key === "ArrowDown"/);
    assert.match(source, /closeMenu\(\{ restoreFocus: true \}\)/);
});

test("global chrome keeps lifecycle, navigation, mutation, confirmation, loader, date, and auth helpers", async () => {
    const chromeSource = await sourceFor("static/js/core/global-chrome.js");
    const source = await sourceFor("static/js/core/global.js");
    const primitiveSource = await sourceFor("static/js/core/ui-primitives.js");

    assert.match(chromeSource, /window\.APStudyPageLifecycle = \{/);
    assert.match(chromeSource, /window\.APStudyNavigation = \{/);
    assert.match(chromeSource, /window\.addEventListener\('pagehide', handlePageHide\)/);
    assert.match(chromeSource, /window\.addEventListener\('pageshow', handlePageShow\)/);
    assert.match(chromeSource, /navigationRetention === 'discard'/);
    assert.match(chromeSource, /window\.location\.replace\(url\.href\)/);
    assert.doesNotMatch(chromeSource, /window\.APStudyPendingMutations = \{/);
    assert.match(source, /window\.APStudyPendingMutations = \{/);
    assert.match(source, /new CustomEvent\("apstudy-pending-save-change"/);
    assert.match(primitiveSource, /window\.APStudyConfirm = window\.APStudyConfirm \|\|/);
    assert.match(primitiveSource, /id = 'apstudy-confirm-root'/);
    assert.match(primitiveSource, /window\.APStudyLoader = window\.APStudyLoader \|\|/);
    assert.match(primitiveSource, /window\.APStudySkeleton = window\.APStudySkeleton \|\|/);
    assert.match(primitiveSource, /table\(options = \{\}\)/);
    assert.match(primitiveSource, /cards\(options = \{\}\)/);
    assert.match(primitiveSource, /fieldSet\(options = \{\}\)/);
    assert.match(primitiveSource, /block: skeletonBlock/);
    assert.match(primitiveSource, /data-slot="skeleton"/);
    assert.match(source, /window\.APStudyDate = \{/);
    assert.match(source, /function clearClientState\(options = \{\}\)/);
    assert.match(source, /function markClientLoggedOut\(\)/);
    assert.match(source, /function shouldEnforceAuth\(\)/);
    assert.match(source, /APStudyAppwriteSessionProbe/);
    assert.doesNotMatch(source, /account\.get\(/);
    assert.doesNotMatch(source, /window\.location\.replace\(`\$\{window\.location\.origin\}\/logout`\)/);
    assert.match(source, /account\.deleteSession\("current"\)/);
    const stopHeartbeatIndex = source.indexOf('window.APStudyPresenceHeartbeat?.stop?.();');
    const deleteSessionIndex = source.indexOf('account.deleteSession("current")');
    assert.ok(stopHeartbeatIndex >= 0, "logout stops presence heartbeats");
    assert.ok(stopHeartbeatIndex < deleteSessionIndex, "logout stops heartbeats before revoking the session");
    assert.match(source, /stop: stopHeartbeat/);
    assert.match(source, /clearClientState\(\{ includeCookies: false \}\)/);
    assert.match(source, /document\.querySelectorAll\("\[data-logout\]"\)/);
});

test("login template uses server-started Appwrite OAuth links", async () => {
    const source = await sourceFor("templates/login.html");

    assert.match(source, /url_for\('auth\.appwrite_oauth_start', provider='google'\)/);
    assert.match(source, /url_for\('auth\.appwrite_oauth_start', provider='github'\)/);
    assert.match(source, /url_for\('auth\.appwrite_oauth_start', provider='discord'\)/);
    assert.doesNotMatch(source, /createOAuth2Session/);
    assert.doesNotMatch(source, /js\/login\.js/);
    assert.doesNotMatch(source, /js\/appwrite\.js/);
});

test("landing page keeps product proof visible and wires the new signup journey accessibly", async () => {
    const template = await sourceFor("templates/landing.html");
    const source = await sourceFor("static/js/landing.js");
    const styles = await sourceFor("static/css/landing.css");
    const demoStyles = await sourceFor("static/css/landing-demos.css");

    assert.match(template, /css\/landing\.css/);
    assert.match(template, /js\/landing\.js/);
    assert.match(template, /images\/landing\/nest-interface-hero\.png/);
    assert.match(template, /apple-touch-icon\.png/);
    assert.match(template, /landing_nav_cta_label = 'Open Nest' if landing_is_authenticated else 'Start free'/);
    assert.match(template, /landing_hero_cta_label = 'Go to dashboard' if landing_is_authenticated else 'Start free'/);
    assert.match(template, /landing_final_cta_label = 'Continue in Nest' if landing_is_authenticated else 'Start free'/);
    assert.match(template, /Your semester,/);
    assert.match(template, /data-landing-rotator/);
    assert.match(template, /data-landing-marquee/);
    assert.match(template, /Students from these universities use Nest/);
    assert.match(template, /Texas A&amp;M/);
    assert.match(template, /UCLA/);
    assert.match(template, /UC Berkeley/);
    assert.match(template, /University of Arizona/);
    assert.match(template, /Emory University/);
    assert.equal((template.match(/class="university-mark /g) || []).length, 10);
    assert.doesNotMatch(template, /not endorsed or sponsored by these institutions/);
    assert.match(template, /feature-panel--dashboard/);
    assert.match(template, /feature-panel--calendar/);
    assert.match(template, /feature-panel--tasks/);
    assert.match(template, /feature-panel--workspace/);
    assert.match(template, /id="tab-tasks"/);
    assert.match(template, /id="panel-tasks"/);
    assert.match(template, /data-landing-tab="tasks"/);
    assert.match(template, /data-landing-panel="tasks"/);
    assert.match(template, /landing-app-demo-dashboard/);
    assert.match(demoStyles, /\.demo-dashboard-icon\s*\{[\s\S]*font-size:\s*14px;[\s\S]*font-variation-settings:[\s\S]*height:\s*18px;[\s\S]*line-height:\s*1;[\s\S]*width:\s*18px;/);
    assert.match(template, /landing-app-demo-calendar/);
    assert.match(template, /landing-app-demo-tasks/);
    assert.match(template, /landing-app-demo-workspace/);
    assert.match(template, /course-planner-demo/);
    assert.match(template, /See your week\. Then get to work\./);
    assert.match(template, /At Emory, course planning goes deeper\./);
    assert.match(template, /Frequently Asked Questions/);
    assert.match(template, /Ready for a better week\?/);
    assert.doesNotMatch(template, /class="course-metadata"/);
    assert.match(template, /data-landing-faq/);
    assert.equal((template.match(/data-landing-faq-trigger/g) || []).length, 6);
    assert.equal((template.match(/data-landing-faq-panel/g) || []).length, 6);
    assert.match(template, /id="faq-trigger-1" aria-expanded="true"/);
    assert.match(template, /id="faq-trigger-2" aria-expanded="false"/);
    assert.match(template, /Finish BIOL 141 lab draft/);
    assert.match(template, /School/);
    assert.match(template, /Completed/);
    assert.match(template, /Foundations of Modern Biol I/);
    assert.match(template, /data-dashboard-week-grid/);
    assert.match(template, /data-landing-calendar-title/);
    assert.match(template, /data-landing-calendar-events/);
    assert.match(template, /data-landing-calendar-agenda/);
    assert.match(template, /View dashboard/);
    assert.match(template, /Explore calendar/);
    assert.match(template, /Organize tasks/);
    assert.match(template, /Open workspace/);
    assert.match(template, /data-landing-cta="nav"/);
    assert.match(template, /data-landing-cta="hero"/);
    assert.match(template, /data-landing-cta="final"/);
    assert.doesNotMatch(template, /data-reveal/);
    assert.doesNotMatch(template, /workflow-sequence/);
    assert.doesNotMatch(template, /landing-connection-band/);
    assert.doesNotMatch(template, /landing-nav-login/);
    assert.doesNotMatch(template, /demo-navbar/);
    assert.doesNotMatch(template, /demo-sidebar/);
    assert.doesNotMatch(template, /demo-shell/);
    assert.match(template, /data-analytics-measurement-id="G-0NT330ZX5L"/);
    assert.doesNotMatch(template, /googletagmanager\.com\/gtag/);
    assert.doesNotMatch(template, /cdn\.tailwindcss\.com/);
    assert.doesNotMatch(template, /placehold\.co/);
    assert.match(demoStyles, /\.landing-app-demo-dashboard/);
    assert.match(demoStyles, /\.demo-week-shell/);
    assert.match(demoStyles, /\.demo-mobile-agenda/);
    assert.match(demoStyles, /\.course-planner-demo/);
    assert.match(styles, /@media \(max-width: 980px\)/);
    assert.match(styles, /@media \(max-width: 760px\)/);
    assert.match(styles, /@media \(prefers-reduced-motion: reduce\)/);
    assert.match(styles, /html\[data-theme="nest-dark"\]/);
    assert.match(styles, /--landing-page-bg: #f7f6f3/);
    assert.match(styles, /--color-surface-container-low: #f7f6f3/);
    assert.match(styles, /\.landing-marquee-track\[aria-hidden="true"\]/);
    assert.doesNotMatch(styles, /landing-reveal-ready/);
    assert.match(source, /typeof window\.gtag !== "function"/);
    assert.match(source, /"landing_cta_click"/);
    assert.match(source, /matchMedia\("\(prefers-reduced-motion: reduce\)"\)/);
    assert.doesNotMatch(source, /IntersectionObserver/);
    assert.match(source, /querySelector\("\[data-landing-rotator\]"\)/);
    assert.match(source, /}, 2000\)/);
    assert.match(source, /querySelector\("\[data-landing-faq\]"\)/);
    assert.match(source, /data-landing-faq-trigger/);
    assert.match(source, /event\.key === "ArrowDown"/);
    assert.match(source, /querySelector\("\[data-landing-tabs\]"\)/);
    assert.match(source, /data-landing-tab/);
    assert.match(source, /aria-selected/);
    assert.match(source, /panel\.hidden = !selected/);
    assert.match(source, /event\.key === "ArrowRight"/);
    assert.match(source, /event\.key === "Home"/);
    assert.match(source, /event\.key === "End"/);
    assert.match(source, /querySelector\("\[data-dashboard-week-grid\]"\)/);
    assert.match(source, /Week of \$\{monthDay\(weekStart\)\} - \$\{monthDay\(weekEnd\)\}/);
    assert.match(source, /showcaseEvents\.map/);
});

test("onboarding presents five accessible stages without changing its saved step contract", async () => {
    const template = await sourceFor("templates/onboarding.html");
    const onboardingSource = await sourceFor("static/js/onboarding/index.js");
    const styles = await sourceFor("static/css/onboarding.css");

    assert.match(template, /css\/onboarding\.css/);
    assert.match(template, /data-onboarding-stepper/);
    for (const stage of [1, 2, 3, 4, 5]) {
        assert.match(template, new RegExp(`data-onboarding-stage="${stage}"`));
        assert.match(template, new RegExp(`data-step="${stage}"`));
    }
    assert.match(template, /aria-current="step"/);
    assert.match(template, /Step \{\{ step if step else 1 \}\} of 5/);
    assert.match(template, /role="alert" aria-live="assertive" tabindex="-1"/);
    assert.match(template, /js\/onboarding\/index\.js/);
    assert.doesNotMatch(template, /<script type="module">/);
    assert.match(onboardingSource, /const percent = Math\.round\(\(safeStep \/ 5\) \* 100\)/);
    assert.match(onboardingSource, /setProperty\('--onboarding-progress', String\(percent \/ 100\)\)/);
    assert.match(onboardingSource, /stepLabel\.textContent = `Step \$\{safeStep\} of 5`/);
    assert.match(onboardingSource, /activeHeading\.focus/);
    assert.match(onboardingSource, /wizardStatus\.focus/);
    assert.match(onboardingSource, /saveStep\(1,/);
    assert.match(onboardingSource, /saveStep\(2,/);
    assert.match(onboardingSource, /saveStep\(3,/);
    assert.match(onboardingSource, /saveStep\(4\)/);
    assert.match(styles, /grid-template-columns: repeat\(5, minmax\(0, 1fr\)\)/);
    assert.match(styles, /@media \(max-width: 680px\)/);
    assert.match(styles, /@media \(prefers-reduced-motion: reduce\)/);
});

test("navbar keeps avatar sizing, command palette shortcut, and logout/account flows", async () => {
    const source = await sourceFor("static/js/core/navbar.js");

    assert.match(source, /window\.APSTUDY_AVATAR_URL_FOR_SIZE = avatarUrlForSize/);
    assert.match(source, /nearestDiscordAvatarSize\(normalizedSize\)/);
    assert.match(source, /googleAvatarUrlForSize\(rawUrl, normalizedSize\)/);
    assert.match(source, /import\('\/static\/js\/core\/command-palette\.js'\)/);
    assert.match(source, /commandPalettePreload !== 'false'/);
    assert.match(source, /window\.APSTUDY_COMMAND_PALETTE_SHORTCUT_BOUND/);
    assert.match(source, /event\.metaKey && !event\.ctrlKey/);
    assert.match(source, /window\.APStudyNavigation\?\.go\?\.\('\/settings#account'\)/);
    assert.match(source, /runLogoutFlow\(\)/);
    assert.match(source, /navbar-avatar--grade-a/);
    assert.match(source, /navbar-avatar--grade-aa/);
    assert.match(source, /navbar-avatar--developer/);
    assert.match(source, /dataset\.userTier/);
});

test("browser notifications preflight capabilities and never use native alert dialogs", async () => {
    const source = await sourceFor("static/js/core/notifications.js");
    const worker = await sourceFor("static/service-worker.js");
    assert.match(source, /push_configured/);
    assert.match(source, /PushManager/);
    assert.match(source, /isSecureContext/);
    assert.match(source, /serviceWorker\.ready/);
    assert.match(source, /NotAllowedError/);
    assert.match(source, /AbortError/);
    assert.match(source, /forceRefresh/);
    assert.match(source, /applicationServerKey/);
    assert.match(source, /ready\.update\(\)/);
    assert.match(source, /APStudyToast/);
    assert.match(source, /desktop_tablet/);
    assert.match(source, /userAgentData\?\.mobile/);
    assert.match(source, /acknowledgedPendingIds/);
    assert.match(source, /\/api\/notifications\/sync/);
    assert.match(source, /\/api\/notifications\/foreground-ack/);
    assert.match(source, /pending_foreground_ids/);
    assert.match(worker, /\/dashboard\?notifications=open/);
    assert.doesNotMatch(source, /global\.alert|window\.alert/);
    const settingsSource = await sourceFor("static/js/settings/notifications.js");
    assert.match(settingsSource, /maxTouchPoints/);
    assert.match(settingsSource, /config\.devices\.length === 0/);
});

test("shared selection controls avoid browser-default checkbox and radio rendering", async () => {
    const source = await sourceFor("static/css/global.css");

    assert.match(source, /:where\(input\[type="checkbox"\], input\[type="radio"\]\):not\(\[data-native-control\]\)/);
    assert.match(source, /appearance:\s*none/);
    assert.match(source, /:checked::before/);
    assert.match(source, /:indeterminate::before/);
    assert.match(source, /webkit-calendar-picker-indicator/);
});

test("navbar notification tray owns filtering, bulk actions, and read behavior", async () => {
    const source = await sourceFor("static/js/core/notification-tray.js");
    const blueprint = await sourceFor("blueprints/notifications_api.py");
    const notesBlueprint = await sourceFor("blueprints/notes_api.py");
    assert.match(source, /data-notification-open/);
    assert.match(source, /data-notification-read/);
    assert.match(source, /data-status="unread"/);
    assert.match(source, /data-category/);
    assert.match(source, /data-search/);
    assert.match(source, /data-select-all/);
    assert.match(source, /data-delete-selected/);
    assert.match(source, /\/api\/notifications\/read/);
    assert.match(source, /item\.target_url/);
    assert.doesNotMatch(source, /target_url \|\| '#'/);
    assert.match(source, /APStudyConfirm\?\.request/);
    assert.match(blueprint, /redirect\(f"\{url_for\('dashboard\.dashboard'\)\}\?notifications=open"\)/);
    assert.doesNotMatch(blueprint, /render_template\("notifications\.html"/);
    assert.doesNotMatch(notesBlueprint, /@notes_api_bp\.route\("\/api\/notifications/);
});

test("tier badge assets and admin controls stay wired to the supported roles", async () => {
    const adminTemplate = await sourceFor("templates/admin_tiers.html");
    const authUsersTemplate = await sourceFor("templates/partials/admin_auth_users.html");
    const detailTemplate = await sourceFor("templates/admin_detail.html");
    const entitlementService = await sourceFor("services/entitlements.py");
    const globalStyles = await sourceFor("static/css/global.css");

    for (const asset of [
        "grade-a-egg.png",
        "grade-aa-egg.png",
        "developer-dinosaur-egg.png",
    ]) {
        assert.match(entitlementService, new RegExp(asset));
    }
    assert.match(adminTemplate, /admin-tier-config/);
    assert.match(adminTemplate, /admin\.save_admin_tiers/);
    assert.match(authUsersTemplate, /admin\.update_user_tier/);
    assert.match(detailTemplate, /admin\.update_user_tier/);
    assert.match(globalStyles, /\.tier-badge/);
    assert.match(globalStyles, /background:\s*transparent/);
});

test("notes export keeps markdown/plain-text conversion and PDF fallback paths", async () => {
    const source = await sourceFor("static/js/notes/export.js");

    assert.match(source, /function blockNoteJsonToMarkdown\(blockNoteJson\)/);
    assert.match(source, /function blockNoteJsonToPlainText\(blockNoteJson\)/);
    assert.match(source, /case 'callout':/);
    assert.match(source, /case 'bookmark':/);
    assert.match(source, /case 'image':/);
    assert.match(source, /case 'video':/);
    assert.match(source, /case 'divider':/);
    assert.match(source, /function exportNoteJsonToTxt\(blockNoteJson, title\)/);
    assert.match(source, /async function exportEditorContainerToPdf\(container, title\)/);
    assert.match(source, /html2pdf\.bundle\.min\.js/);
    assert.match(source, /window\.jspdf\?\.jsPDF/);
    assert.match(source, /sanitizeFilename\(normalizeTitle\(title\)\)/);
    assert.match(source, /window\.NotesExport = \{/);
});
