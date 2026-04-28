/* ──────────────────────────────────────────────────────────────────────────────
   Dashboard Calendar & Assignments
   ──────────────────────────────────────────────────────────────────────────── */

/* ── Constants ─────────────────────────────────────────────────────────────── */
const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MINUTES_PER_DAY = 1440;
const HOUR_HEIGHT_PX = 60;
const WEEK_MINIMUM_DAY_WIDTH_PX = 100;
const ALL_DAY_MIN_HEIGHT_PX = 44;
const SIMULATED_CALENDAR_NAME = "Simulated Courses";
const CANVAS_CALENDAR_NAME = "Canvas";
const COURSES_SELECTION_STORAGE_KEY = "coursesSelectedSectionIds";
const COURSES_MODAL_ANIMATION_MS = 180;
const THEME_LOADER_STYLE_ID = "apstudy-theme-loader-styles";
const DEFAULT_DASHBOARD_VIEW = document.body?.dataset.defaultDashboardView === "month" ? "month" : "week";

/* ── Application State ─────────────────────────────────────────────────────── */
const state = {
    events: [],
    feedConfigured: false,
    loadingDashboard: false,
    view: DEFAULT_DASHBOARD_VIEW,
    anchorDate: new Date(),
    calendars: {},
    calendarColors: ["#ef4444", "#f97316", "#eab308", "#84cc16", "#0ea5e9", "#d946ef", "#b08968"],
    courses: {
        terms: [],
        sections: [],
        sectionsById: {},
        indexLoaded: false,
        selectedSectionIds: new Set(),
        pinnedSectionIds: new Set(),
        modalOpen: false,
        isClosing: false,
        animateOnOpen: false,
        showSelectedOnly: false,
        loading: false,
        error: "",
        searchQuery: "",
        searchInput: "",
        termFilter: "",
        filteredSectionIds: [],
        expandedDetails: new Set(),
    },
    ui: {
        calendarMenuOpen: false,
        contextMenuEl: null,
        rgbModalEl: null,
        contextAnchorEl: null,
        contextCalendarName: null,
        saveTimers: {},
        pendingCalendars: new Set(),
        weeklyAutoScrollKey: null,
    },
};

function initCalendarState() {
    const calendars = {};

    calendars[CANVAS_CALENDAR_NAME] = {
        visible: true,
        color: state.calendarColors[0],
        colorIndex: 0,
    };

    for (const event of state.events) {
        const cal = event.course || "Other";
        if (!calendars[cal]) {
            const colorIndex = Object.keys(calendars).length % state.calendarColors.length;
            calendars[cal] = {
                visible: true,
                color: state.calendarColors[colorIndex],
                colorIndex,
            };
        }
    }
    if (state.courses.selectedSectionIds.size > 0 && !calendars[SIMULATED_CALENDAR_NAME]) {
        const colorIndex = Object.keys(calendars).length % state.calendarColors.length;
        calendars[SIMULATED_CALENDAR_NAME] = {
            visible: true,
            color: state.calendarColors[colorIndex],
            colorIndex,
        };
    }
    state.calendars = calendars;
}

function persistCalendarPreference(calendarName) {
    const pref = state.calendars[calendarName];
    if (!pref) return Promise.resolve();

    state.ui.pendingCalendars.add(calendarName);
    renderCalendarMenu();

    return fetch("/api/calendar/preferences", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            calendar_name: calendarName,
            color_hex: pref.color,
            visible: pref.visible,
        }),
    }).catch((err) => {
        console.error("Failed to save preference:", err);
    }).finally(() => {
        state.ui.pendingCalendars.delete(calendarName);
        renderCalendarMenu();
    });
}

function queueCalendarPreferenceSave(calendarName, delayMs = 220) {
    if (state.ui.saveTimers[calendarName]) {
        clearTimeout(state.ui.saveTimers[calendarName]);
    }
    state.ui.saveTimers[calendarName] = setTimeout(() => {
        delete state.ui.saveTimers[calendarName];
        persistCalendarPreference(calendarName);
    }, delayMs);
}

function saveCalendarState() {
    const toSave = {};
    for (const [cal, data] of Object.entries(state.calendars)) {
        toSave[cal] = { visible: data.visible, color: data.color };
        queueCalendarPreferenceSave(cal, 0);
    }
    localStorage.setItem("calendarState", JSON.stringify(toSave));
}

async function loadCalendarState() {
    const saved = localStorage.getItem("calendarState");
    if (saved) {
        try {
            const data = JSON.parse(saved);
            for (const [cal, info] of Object.entries(data)) {
                if (state.calendars[cal]) {
                    state.calendars[cal].visible = info.visible;
                    state.calendars[cal].color = info.color;
                }
            }
        } catch (_) {
            // ignore
        }
    }

    try {
        const res = await fetch("/api/calendar/preferences", { credentials: "same-origin" });
        if (!res.ok) return;
        const payload = await res.json();
        const prefs = Array.isArray(payload.preferences) ? payload.preferences : [];
        for (const pref of prefs) {
            const name = pref.calendar_name;
            if (!name || !state.calendars[name]) continue;
            if (typeof pref.visible === "boolean") state.calendars[name].visible = pref.visible;
            if (typeof pref.color_hex === "string" && /^#[0-9a-fA-F]{6}$/.test(pref.color_hex)) {
                state.calendars[name].color = pref.color_hex;
            }
        }
    } catch (_) {
        // ignore
    }
}

/* ── Bootstrap ─────────────────────────────────────────────────────────────── */
document.addEventListener("DOMContentLoaded", () => {
    applyCoursesFiltersFromUrl();
    initializeCourseSelectionsFromStorage();
    wireControls();
    loadCalendarData();
});

function initializeCourseSelectionsFromStorage() {
    const persistedSelections = loadSelectedCourseSectionIds();
    state.courses.selectedSectionIds = new Set(persistedSelections);
}

/* ── Date Parsing ──────────────────────────────────────────────────────────── */

/**
 * Parse a date string from the API into a JavaScript Date.
 *
 * All-day events arrive as date-only strings like "2026-04-24".
 * Per ECMAScript spec, new Date("2026-04-24") interprets this as
 * UTC midnight, which shifts to the previous day in western timezones.
 * This function detects date-only strings and constructs local midnight
 * instead, so April 24 stays April 24 regardless of timezone.
 *
 * Timed events arrive as "2026-04-24T20:00:00Z" and are parsed normally;
 * the browser converts UTC to local time automatically.
 */
function parseEventDate(dateStr, isAllDay) {
    if (!dateStr) return new Date();

    if (isAllDay && /^\d{4}-\d{2}-\d{2}$/.test(dateStr)) {
        const parts = dateStr.split("-");
        return new Date(
            parseInt(parts[0], 10),
            parseInt(parts[1], 10) - 1,
            parseInt(parts[2], 10),
            0, 0, 0, 0
        );
    }

    return new Date(dateStr);
}

/* ── Data Loading ──────────────────────────────────────────────────────────── */
async function loadCalendarData() {
    state.loadingDashboard = true;
    render();

    try {
        const statusRes = await fetch("/api/calendar/status", { credentials: "same-origin" });
        if (!statusRes.ok) throw new Error("Unable to fetch calendar status");

        const status = await statusRes.json();
        state.feedConfigured = Boolean(status.feed_configured);

        if (!state.feedConfigured) {
            state.events = [];
            initCalendarState();
            await loadCalendarState();
            ensureSimulatedCalendarPreference();
            render();
            hydrateSelectedSimulatedSections();
            return;
        }

        await refreshCalendarFeed();

        const eventsRes = await fetch("/api/calendar/events", { credentials: "same-origin" });
        if (!eventsRes.ok) throw new Error("Unable to fetch calendar events");

        const payload = await eventsRes.json();
        state.events = Array.isArray(payload.events)
            ? payload.events
                    .filter((e) => e.start)
                    .map((e) => {
                        const isAllDay = Boolean(e.is_all_day);
                        return {
                            ...e,
                            startDate: parseEventDate(e.start, isAllDay),
                            endDate: e.end ? parseEventDate(e.end, isAllDay) : parseEventDate(e.start, isAllDay),
                            isAllDay,
                            isMultiDay: Boolean(e.is_multi_day),
                            spanDays: e.span_days || 1,
                        };
                    })
                    .sort((a, b) => a.startDate - b.startDate)
            : [];

        initCalendarState();
        await loadCalendarState();
        ensureSimulatedCalendarPreference();
        render();
        hydrateSelectedSimulatedSections();
    } catch (err) {
        state.feedConfigured = false;
        state.events = [];
        initCalendarState();
        await loadCalendarState();
        ensureSimulatedCalendarPreference();
        render();
        hydrateSelectedSimulatedSections();
        console.error(err);
    } finally {
        state.loadingDashboard = false;
        render();
    }
}

async function hydrateSelectedSimulatedSections() {
    if (!state.courses.selectedSectionIds.size) return;

    const missingIds = Array.from(state.courses.selectedSectionIds)
        .filter((id) => !state.courses.sectionsById[id]);

    if (!missingIds.length) {
        ensureSimulatedCalendarPreference();
        render();
        return;
    }

    try {
        const res = await fetch("/api/atlas/sections/by-id", {
            method: "POST",
            credentials: "same-origin",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                section_ids: missingIds,
                include_cancelled: true,
            }),
        });
        if (!res.ok) return;

        const payload = await res.json();
        const sections = Array.isArray(payload.sections) ? payload.sections : [];
        const foundIds = new Set();

        for (const section of sections) {
            const id = String(section.id || "");
            if (!id) continue;
            foundIds.add(id);
            const searchBlob = [
                section.course_title,
                section.subject,
                section.course_code,
                section.instructor,
                ...(Array.isArray(section.instructors_unique) ? section.instructors_unique : []),
            ].join(" ").toLowerCase();
            state.courses.sectionsById[id] = { ...section, searchBlob };
        }

        let selectionChanged = false;
        for (const id of missingIds) {
            if (!foundIds.has(id) && state.courses.selectedSectionIds.has(id)) {
                state.courses.selectedSectionIds.delete(id);
                selectionChanged = true;
            }
        }

        if (selectionChanged) {
            saveSelectedCourseSectionIds();
        }

        ensureSimulatedCalendarPreference();
        render();
    } catch (err) {
        console.error("Failed to hydrate selected simulated sections:", err);
    }
}

async function refreshCalendarFeed() {
    try {
        await fetch("/api/calendar/refresh", { method: "POST", credentials: "same-origin" });
    } catch (_) {
        /* silent */
    }
}

/* ── Courses Search & Modal ─────────────────────────────────────────────── */
function applyCoursesFiltersFromUrl() {
    const url = new URL(window.location.href);
    state.courses.searchQuery = (url.searchParams.get("search") || "").trim();
    state.courses.searchInput = state.courses.searchQuery;
    state.courses.termFilter = (url.searchParams.get("term") || "").trim();
}

function writeCourseFiltersToUrl() {
    const url = new URL(window.location.href);
    const search = state.courses.searchQuery.trim();
    const term = state.courses.termFilter.trim();

    if (search) {
        url.searchParams.set("search", search);
    } else {
        url.searchParams.delete("search");
    }

    if (term) {
        url.searchParams.set("term", term);
    } else {
        url.searchParams.delete("term");
    }

    window.history.replaceState({}, "", `${url.pathname}${url.search}${url.hash}`);
}

function formatTermLabel(term) {
    const parts = String(term || "").split("_");
    if (parts.length !== 2) return term || "Unknown";
    return `${parts[0]} ${parts[1]}`;
}

function openCoursesModal() {
    if (state.courses.modalOpen && !state.courses.isClosing) {
        closeCoursesModal();
        return;
    }

    state.courses.modalOpen = true;
    state.courses.isClosing = false;
    state.courses.animateOnOpen = true;
    state.courses.searchInput = state.courses.searchQuery;
    state.courses.pinnedSectionIds = new Set(state.courses.selectedSectionIds);
    state.courses.showSelectedOnly = state.courses.pinnedSectionIds.size > 0;
    applyCourseFilters();
    document.body.classList.add("overflow-hidden");
    renderCoursesModal();

    if (!state.courses.indexLoaded && !state.courses.loading) {
        void loadCoursesIndex();
    }
}

function closeCoursesModal() {
    if (!state.courses.modalOpen || state.courses.isClosing) return;

    const overlay = document.getElementById("courses-modal-overlay");
    const panel = document.getElementById("courses-modal-panel");

    state.courses.isClosing = true;
    if (overlay) {
        overlay.classList.add("opacity-0", "pointer-events-none");
    }
    if (panel) {
        panel.classList.add("-translate-y-3", "opacity-0");
    }

    window.setTimeout(() => {
        state.courses.pinnedSectionIds = new Set();
        state.courses.showSelectedOnly = false;
        state.courses.modalOpen = false;
        state.courses.isClosing = false;
        document.body.classList.remove("overflow-hidden");
        renderCoursesModal();
    }, COURSES_MODAL_ANIMATION_MS);
}

function ensureThemeLoaderStyles() {
    if (document.getElementById(THEME_LOADER_STYLE_ID)) return;

    const style = document.createElement("style");
    style.id = THEME_LOADER_STYLE_ID;
    style.textContent = `
        .loader {
            width: 42px;
            height: 42px;
            display: inline-block;
            position: relative;
            box-sizing: border-box;
            color: var(--color-primary, #3b82f6);
            animation: apstudyLoaderRotation 1s linear infinite;
        }

        .loader::after,
        .loader::before {
            content: "";
            box-sizing: border-box;
            position: absolute;
            width: 50%;
            height: 50%;
            top: 0;
            border-radius: 50%;
            background-color: var(--color-error, #ef4444);
            animation: apstudyLoaderScale50 1s infinite ease-in-out;
        }

        .loader::before {
            top: auto;
            bottom: 0;
            background-color: var(--color-primary, #3b82f6);
            animation-delay: 0.5s;
        }

        @keyframes apstudyLoaderRotation {
            0% { transform: rotate(0deg); }
            100% { transform: rotate(360deg); }
        }

        @keyframes apstudyLoaderScale50 {
            0%, 100% { transform: scale(0); }
            50% { transform: scale(1); }
        }
    `;
    document.head.appendChild(style);
}

function buildLoadingIndicatorHtml(label = "Loading...", options = {}) {
    ensureThemeLoaderStyles();
    const sizePx = Number(options.sizePx) > 0 ? Number(options.sizePx) : 42;
    const textToneClass = options.textToneClass || "text-on-surface-variant";
    return `
        <div class="flex w-full flex-col items-center justify-center gap-2 text-center text-sm ${textToneClass}">
            <span class="loader" style="width:${sizePx}px; height:${sizePx}px;" aria-hidden="true"></span>
            <span>${escapeHtml(label)}</span>
        </div>
    `;
}

async function submitCoursesSearch() {
    state.courses.searchQuery = (state.courses.searchInput || "").trim();
    state.courses.showSelectedOnly = false;

    if (!state.courses.indexLoaded) {
        await loadCoursesIndex();
        return;
    }

    applyCourseFilters();
    writeCourseFiltersToUrl();
    renderCoursesModal();
}

function loadSelectedCourseSectionIds() {
    const raw = localStorage.getItem(COURSES_SELECTION_STORAGE_KEY);
    if (!raw) return [];

    try {
        const parsed = JSON.parse(raw);
        return Array.isArray(parsed) ? parsed.map((item) => String(item)) : [];
    } catch (_) {
        return [];
    }
}

function saveSelectedCourseSectionIds() {
    localStorage.setItem(
        COURSES_SELECTION_STORAGE_KEY,
        JSON.stringify(Array.from(state.courses.selectedSectionIds))
    );
}

async function loadCoursesIndex() {
    if (state.courses.loading) return;
    if (state.courses.indexLoaded) {
        applyCourseFilters();
        writeCourseFiltersToUrl();
        renderCoursesModal();
        return;
    }

    state.courses.loading = true;
    state.courses.error = "";
    renderCoursesModal();

    try {
        const [termsRes, sectionsRes] = await Promise.all([
            fetch("/api/atlas/terms", { credentials: "same-origin" }),
            fetch("/api/atlas/sections?include_cancelled=1", { credentials: "same-origin" }),
        ]);

        if (!termsRes.ok) throw new Error("Unable to load terms");
        if (!sectionsRes.ok) throw new Error("Unable to load sections");

        const termsPayload = await termsRes.json();
        const sectionsPayload = await sectionsRes.json();

        state.courses.terms = Array.isArray(termsPayload.terms) ? termsPayload.terms : [];
        state.courses.sections = Array.isArray(sectionsPayload.sections) ? sectionsPayload.sections : [];
        state.courses.indexLoaded = true;
        state.courses.sectionsById = {};

        for (const section of state.courses.sections) {
            const id = String(section.id || "");
            if (!id) continue;
            const searchBlob = [
                section.course_title,
                section.subject,
                section.course_code,
                section.instructor,
                ...(Array.isArray(section.instructors_unique) ? section.instructors_unique : []),
            ].join(" ").toLowerCase();
            state.courses.sectionsById[id] = { ...section, searchBlob };
        }

        const validTerms = new Set(state.courses.terms);
        if (state.courses.termFilter && !validTerms.has(state.courses.termFilter)) {
            state.courses.termFilter = "";
        }

        const persistedSelections = loadSelectedCourseSectionIds();
        state.courses.selectedSectionIds = new Set(
            persistedSelections.filter((id) => Boolean(state.courses.sectionsById[id]))
        );

        ensureSimulatedCalendarPreference();
        applyCourseFilters();
        writeCourseFiltersToUrl();
        saveSelectedCourseSectionIds();
        saveCalendarState();
    } catch (err) {
        console.error(err);
        state.courses.indexLoaded = false;
        state.courses.error = err?.message || "Failed to load courses";
    } finally {
        state.courses.loading = false;
        render();
    }
}

function applyCourseFilters() {
    if (state.courses.showSelectedOnly) {
        state.courses.filteredSectionIds = Array.from(state.courses.pinnedSectionIds);
        return;
    }

    if (!state.courses.indexLoaded) {
        state.courses.filteredSectionIds = [];
        return;
    }

    const term = state.courses.termFilter;
    const query = state.courses.searchQuery.trim().toLowerCase();

    const filteredIds = [];
    for (const section of state.courses.sections) {
        const id = String(section.id || "");
        if (!id) continue;

        if (term && section.term !== term) continue;

        const full = state.courses.sectionsById[id];
        if (query && (!full || !full.searchBlob.includes(query))) continue;
        filteredIds.push(id);
    }

    state.courses.filteredSectionIds = filteredIds;
}

function toggleCourseSectionSelection(sectionId) {
    const section = state.courses.sectionsById[sectionId];
    if (!section || section.is_cancelled) return;

    if (state.courses.selectedSectionIds.has(sectionId)) {
        state.courses.selectedSectionIds.delete(sectionId);
    } else {
        state.courses.selectedSectionIds.add(sectionId);
    }

    ensureSimulatedCalendarPreference();
    saveSelectedCourseSectionIds();
    saveCalendarState();
    render();
}

function formatMeetingTime(timeToken) {
    const parsed = parseAtlasTimeToken(timeToken);
    if (!parsed) return "TBA";

    let hour = parsed.hour;
    const suffix = hour >= 12 ? "p" : "a";
    hour %= 12;
    if (hour === 0) hour = 12;
    return `${hour}:${String(parsed.minute).padStart(2, "0")}${suffix}`;
}

function parseDateOnlyToLocal(dateStr) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(String(dateStr || ""))) return null;
    const [year, month, day] = String(dateStr).split("-").map((v) => parseInt(v, 10));
    return new Date(year, month - 1, day, 0, 0, 0, 0);
}

function getSectionDateBounds(section) {
    const range = section?.date_range || {};
    const start = parseDateOnlyToLocal(range.start);
    const end = parseDateOnlyToLocal(range.end);
    return { start, end };
}

function formatSectionDateRange(section) {
    const bounds = getSectionDateBounds(section);
    if (!bounds.start || !bounds.end) return "N/A";
    const startStr = bounds.start.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
    const endStr = bounds.end.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
    return `${startStr} - ${endStr}`;
}

function formatSectionTypeLabel(typeValue) {
    const raw = String(typeValue || "").trim().toUpperCase();
    const known = {
        LEC: "LEC (Lecture)",
    };
    if (!raw) return "N/A";
    return known[raw] || raw;
}

function captureCoursesModalViewState(overlay) {
    const scroller = overlay.querySelector("#courses-modal-scroll");
    const activeEl = document.activeElement;
    const activeInModal = activeEl && overlay.contains(activeEl) ? activeEl : null;
    const activeId = activeInModal?.id || null;

    return {
        scrollTop: scroller ? scroller.scrollTop : 0,
        activeId,
        selectionStart: activeId === "courses-search-input" ? activeInModal.selectionStart : null,
        selectionEnd: activeId === "courses-search-input" ? activeInModal.selectionEnd : null,
    };
}

function restoreCoursesModalViewState(overlay, snapshot) {
    if (!snapshot) return;

    const scroller = overlay.querySelector("#courses-modal-scroll");
    if (scroller && Number.isFinite(snapshot.scrollTop)) {
        scroller.scrollTop = snapshot.scrollTop;
    }

    if (!snapshot.activeId) return;

    const activeEl = overlay.querySelector(`#${snapshot.activeId}`);
    if (!activeEl) return;

    activeEl.focus({ preventScroll: true });
    if (
        snapshot.activeId === "courses-search-input"
        && typeof snapshot.selectionStart === "number"
        && typeof snapshot.selectionEnd === "number"
        && typeof activeEl.setSelectionRange === "function"
    ) {
        activeEl.setSelectionRange(snapshot.selectionStart, snapshot.selectionEnd);
    }
}

function renderCoursesModal() {
    let overlay = document.getElementById("courses-modal-overlay");
    if (!overlay) {
        overlay = document.createElement("div");
        overlay.id = "courses-modal-overlay";
        overlay.className = "fixed inset-0 z-[90] hidden items-center justify-center bg-black/55 backdrop-blur-[1px] p-4";
        document.body.appendChild(overlay);
    }

    if (!state.courses.modalOpen) {
        overlay.classList.add("hidden");
        overlay.classList.remove("flex");
        overlay.innerHTML = "";
        return;
    }

    overlay.classList.remove("hidden");
    overlay.classList.add("flex");
    const viewState = captureCoursesModalViewState(overlay);

    const terms = state.courses.terms;
    const selectedCount = state.courses.selectedSectionIds.size;
    const resultCount = state.courses.filteredSectionIds.length;
    const isSelectedOnly = state.courses.showSelectedOnly;
    const termDisabled = !state.courses.indexLoaded || isSelectedOnly || state.courses.loading;
    const searchPlaceholder = isSelectedOnly
        ? "Press Enter or Search to load and find more courses"
        : "Search title, subject, code, or instructor";

    const statusLine = state.courses.loading || (!state.courses.indexLoaded && !state.courses.error)
        ? "Loading courses..."
        : state.courses.error
            ? escapeHtml(state.courses.error)
            : isSelectedOnly
                ? `${resultCount.toLocaleString()} pinned in this session · ${selectedCount.toLocaleString()} selected`
                : `${resultCount.toLocaleString()} sections · ${selectedCount.toLocaleString()} selected`;

    const content = state.courses.loading
        ? `<div class="py-12">${buildLoadingIndicatorHtml("Loading full course index...", { sizePx: 54, textToneClass: "text-on-surface" })}</div>`
        : state.courses.error
            ? `<div class="py-12 text-center text-sm text-error">${escapeHtml(state.courses.error)}</div>`
            : !state.courses.indexLoaded
                ? `<div class="py-12">${buildLoadingIndicatorHtml("Loading full course index...", { sizePx: 54, textToneClass: "text-on-surface" })}</div>`
            : buildCourseCardsHtml();

    overlay.innerHTML = `
        <section id="courses-modal-panel" class="w-full max-w-[1200px] h-[88vh] rounded-3xl border border-outline-variant/30 bg-surface shadow-2xl shadow-black/30 flex flex-col overflow-hidden transition-all duration-200 ${state.courses.animateOnOpen ? "-translate-y-3 opacity-0" : "translate-y-0 opacity-100"}">
            <header class="px-5 sm:px-7 pt-5 sm:pt-6 pb-4 border-b border-outline-variant/20 bg-surface-container-low">
                <div class="flex items-center justify-between gap-3 mb-4">
                    <h2 class="text-2xl font-headline font-semibold text-on-surface tracking-tight">Courses</h2>
                    <button id="courses-modal-close" type="button" class="h-9 w-9 rounded-full border border-outline-variant/40 text-on-surface-variant hover:text-on-surface hover:bg-surface-container-high transition-colors" aria-label="Close courses search">✕</button>
                </div>
                <div class="grid grid-cols-1 md:grid-cols-[1fr_220px] gap-3">
                    <label class="relative block">
                        <span class="material-symbols-outlined absolute left-3 top-1/2 -translate-y-1/2 text-on-surface-variant text-[20px]">search</span>
                        <input id="courses-search-input" type="text" value="${escapeHtml(state.courses.searchInput)}" placeholder="${escapeHtml(searchPlaceholder)}" class="w-full h-11 rounded-xl border border-outline-variant/35 bg-surface px-10 pr-[7.25rem] text-sm text-on-surface placeholder:text-on-surface-variant focus:outline-none focus:ring-2 focus:ring-primary/30" />
                        <button id="courses-search-submit" type="button" class="absolute right-1.5 top-1/2 -translate-y-1/2 h-8 px-3 rounded-lg bg-primary text-on-primary text-xs font-semibold hover:brightness-95 disabled:opacity-60 disabled:cursor-not-allowed" ${state.courses.loading ? "disabled" : ""}>Search</button>
                    </label>
                    <select id="courses-term-select" ${termDisabled ? "disabled" : ""} class="h-11 rounded-xl border border-outline-variant/35 bg-surface px-3 text-sm text-on-surface focus:outline-none focus:ring-2 focus:ring-primary/30 disabled:opacity-60 disabled:cursor-not-allowed">
                        <option value="" ${state.courses.termFilter ? "" : "selected"}>All Terms</option>
                        ${terms.map((term) => `<option value="${escapeHtml(term)}" ${state.courses.termFilter === term ? "selected" : ""}>${escapeHtml(formatTermLabel(term))}</option>`).join("")}
                    </select>
                </div>
                <div class="text-xs text-on-surface-variant mt-3">${statusLine}</div>
            </header>
            <div id="courses-modal-scroll" class="flex-1 overflow-auto px-5 sm:px-7 py-5 bg-surface">
                ${content}
            </div>
        </section>
    `;

    restoreCoursesModalViewState(overlay, viewState);

    overlay.classList.add("transition-opacity", "duration-200");
    if (state.courses.animateOnOpen) {
        overlay.classList.add("opacity-0", "pointer-events-none");
        window.requestAnimationFrame(() => {
            overlay.classList.remove("opacity-0", "pointer-events-none");
            const mountedPanel = document.getElementById("courses-modal-panel");
            mountedPanel?.classList.remove("-translate-y-3", "opacity-0");
        });
        state.courses.animateOnOpen = false;
    }
}

function buildCourseCardsHtml() {
    if (!state.courses.filteredSectionIds.length) {
        if (state.courses.showSelectedOnly) {
            return '<div class="py-12 text-center text-sm text-on-surface-variant">No pinned courses to show in this session.</div>';
        }
        return '<div class="py-12 text-center text-sm text-on-surface-variant">No sections match your filters.</div>';
    }

    const cards = state.courses.filteredSectionIds.map((sectionId) => {
        const section = state.courses.sectionsById[sectionId];
        if (!section) return "";

        const selected = state.courses.selectedSectionIds.has(sectionId);
        const expanded = state.courses.expandedDetails.has(sectionId);
        const isOpen = String(section.enrollment_status || "").toLowerCase() === "open";
        const statusClasses = isOpen
            ? "bg-emerald-500/15 text-emerald-600 border-emerald-500/40"
            : "bg-red-500/15 text-red-600 border-red-500/40";
        const cancelledText = section.is_cancelled
            ? '<span class="ml-2 inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold border bg-amber-500/15 text-amber-700 border-amber-500/30">Cancelled</span>'
            : "";

        const meetingRows = (Array.isArray(section.meetings) ? section.meetings : []).map((meeting) => {
            const label = `${meeting.day || "?"} ${formatMeetingTime(meeting.start)}-${formatMeetingTime(meeting.end)}`;
            return `<li>${escapeHtml(label)}</li>`;
        }).join("");
        const sectionDateRange = formatSectionDateRange(section);

        const sectionLabel = `Section ${section.section_number || "?"}`;
        const addButtonText = selected ? "Remove" : "+";
        const addButtonClasses = selected
            ? "border border-red-500/40 text-red-600 bg-red-500/10 hover:bg-red-500/20"
            : "border border-primary/40 text-primary bg-primary/10 hover:bg-primary/20";
        const addDisabled = section.is_cancelled ? "disabled" : "";

        return `
            <article class="w-full max-w-[360px] rounded-2xl border border-outline-variant/20 bg-surface-container-low p-4 sm:p-5 flex flex-col gap-3">
                <div class="flex items-start justify-between gap-3">
                    <div>
                        <div class="text-lg font-semibold text-on-surface">${escapeHtml(section.course_code || "Unknown")} (${escapeHtml(sectionLabel)})</div>
                        <div class="text-sm text-on-surface-variant">${escapeHtml(section.course_title || "Untitled Course")}</div>
                    </div>
                    <button type="button" class="js-course-info-toggle h-8 w-8 rounded-full border border-outline-variant/40 text-on-surface-variant hover:text-on-surface hover:bg-surface-container-high" data-section-id="${escapeHtml(sectionId)}" aria-label="Toggle section details">i</button>
                </div>

                <div class="flex items-center gap-2 text-xs flex-wrap">
                    <span class="inline-flex rounded-full border border-outline-variant/35 px-2.5 py-1 font-medium text-on-surface-variant">${escapeHtml(formatTermLabel(section.term))}</span>
                    <span class="inline-flex rounded-full border px-2.5 py-1 font-medium ${statusClasses}">${escapeHtml(section.enrollment_status || "Unknown")}</span>
                    ${cancelledText}
                </div>

                <div class="text-sm text-on-surface-variant flex flex-wrap gap-x-4 gap-y-1">
                    <div><span class="material-symbols-outlined text-[16px] align-[-3px] mr-1">schedule</span>${escapeHtml(section.schedule_display || "TBA")}</div>
                </div>

                <div class="text-sm text-on-surface-variant">
                    Instructor: ${escapeHtml(section.instructor || "TBA")}
                </div>

                ${expanded ? `
                <div class="rounded-xl border border-outline-variant/20 bg-surface p-3 text-xs text-on-surface-variant space-y-2">
                    <div>Section Type: <span class="text-on-surface">${escapeHtml(formatSectionTypeLabel(section.schedule_type))}</span></div>
                    <div>Course Reference Number (CRN): <span class="text-on-surface">${escapeHtml(section.crn || "N/A")}</span></div>
                    <div>Section Date Range: <span class="text-on-surface">${escapeHtml(sectionDateRange)}</span></div>
                    <div>Status: <span class="text-on-surface">${escapeHtml(section.enrollment_status || "Unknown")}</span></div>
                    <div>Enrollment Count: <span class="text-on-surface">${escapeHtml(section.enrollment_count || "N/A")}</span></div>
                    <div>Cancelled: <span class="text-on-surface">${section.is_cancelled ? "Yes" : "No"}</span></div>
                    <div>Meeting Times:</div>
                    <ul class="list-disc list-inside text-on-surface-variant space-y-0.5">${meetingRows || "<li>TBA</li>"}</ul>
                </div>
                ` : ""}

                <div class="flex justify-end">
                    <button type="button" ${addDisabled} class="js-course-toggle min-w-[84px] h-9 rounded-full text-sm font-semibold transition-colors ${addButtonClasses} disabled:opacity-50 disabled:cursor-not-allowed" data-section-id="${escapeHtml(sectionId)}" title="${selected ? "Remove from simulated calendar" : "Add to simulated calendar"}">${addButtonText}</button>
                </div>
            </article>
        `;
    }).join("");

    return `<div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4 justify-items-center">${cards}</div>`;
}

/* ── Calendar Filtering ────────────────────────────────────────────────────── */
function getVisibleEvents() {
    const baseEvents = state.events.filter((e) => {
        const cal = e.course || "Other";
        return state.calendars[cal]?.visible !== false;
    });

    if (state.calendars[SIMULATED_CALENDAR_NAME]?.visible === false || state.courses.selectedSectionIds.size === 0) {
        return baseEvents;
    }

    const renderRange = getCurrentRenderRange();
    const simulatedEvents = buildSimulatedMeetingEvents(renderRange.start, renderRange.end);
    return baseEvents.concat(simulatedEvents);
}

function getCurrentRenderRange() {
    if (state.view === "month") {
        const year = state.anchorDate.getFullYear();
        const month = state.anchorDate.getMonth();
        const monthStart = new Date(year, month, 1);
        const gridStart = new Date(monthStart);
        gridStart.setDate(monthStart.getDate() - monthStart.getDay());
        const gridEnd = new Date(gridStart);
        gridEnd.setDate(gridStart.getDate() + 41);
        gridEnd.setHours(23, 59, 59, 999);
        return { start: gridStart, end: gridEnd };
    }

    const weekStart = getStartOfWeek(state.anchorDate);
    const weekEnd = new Date(weekStart);
    weekEnd.setDate(weekStart.getDate() + 6);
    weekEnd.setHours(23, 59, 59, 999);
    return { start: weekStart, end: weekEnd };
}

function ensureSimulatedCalendarPreference() {
    if (state.courses.selectedSectionIds.size === 0) return;
    if (state.calendars[SIMULATED_CALENDAR_NAME]) {
        state.calendars[SIMULATED_CALENDAR_NAME].visible = true;
        return;
    }

    const colorIndex = Object.keys(state.calendars).length % state.calendarColors.length;
    state.calendars[SIMULATED_CALENDAR_NAME] = {
        visible: true,
        color: state.calendarColors[colorIndex],
        colorIndex,
    };
}

function buildSimulatedMeetingEvents(startDate, endDate) {
    const dayTokenMap = {
        Sun: 0,
        Mon: 1,
        Tue: 2,
        Wed: 3,
        Thu: 4,
        Fri: 5,
        Sat: 6,
    };
    const events = [];

    for (const sectionId of state.courses.selectedSectionIds) {
        const section = state.courses.sectionsById[sectionId];
        if (!section) continue;

        const sectionBounds = getSectionDateBounds(section);
        const constrainedStart = sectionBounds.start && sectionBounds.start > startDate ? sectionBounds.start : startDate;
        const constrainedEnd = sectionBounds.end && sectionBounds.end < endDate ? sectionBounds.end : endDate;
        if (constrainedEnd < constrainedStart) continue;

        const meetings = Array.isArray(section.meetings) ? section.meetings : [];
        for (let cursor = new Date(constrainedStart); cursor <= constrainedEnd; cursor.setDate(cursor.getDate() + 1)) {
            for (const meeting of meetings) {
                const meetingDay = dayTokenMap[meeting.day];
                if (meetingDay !== cursor.getDay()) continue;

                const parsedStart = parseAtlasTimeToken(meeting.start);
                const parsedEnd = parseAtlasTimeToken(meeting.end);
                if (!parsedStart || !parsedEnd) continue;

                const eventStart = new Date(cursor.getFullYear(), cursor.getMonth(), cursor.getDate(), parsedStart.hour, parsedStart.minute, 0, 0);
                const eventEnd = new Date(cursor.getFullYear(), cursor.getMonth(), cursor.getDate(), parsedEnd.hour, parsedEnd.minute, 0, 0);
                if (eventEnd <= eventStart) continue;

                const uid = `${sectionId}|${cursor.toISOString().slice(0, 10)}|${meeting.start}|${meeting.end}`;
                events.push({
                    uid,
                    title: `${section.course_code}`.trim(),
                    description: `${section.course_title} | Sec ${section.section_number || "?"} | ${section.instructor || "TBA"}`,
                    course: SIMULATED_CALENDAR_NAME,
                    type: "class-meeting",
                    is_all_day: false,
                    isAllDay: false,
                    isMultiDay: false,
                    spanDays: 1,
                    source: "simulated",
                    startDate: eventStart,
                    endDate: eventEnd,
                });
            }
        }
    }

    return events.sort((a, b) => a.startDate - b.startDate);
}

function parseAtlasTimeToken(timeToken) {
    const numeric = String(timeToken || "").replace(/\D/g, "");
    if (!numeric) return null;

    const padded = numeric.length >= 4 ? numeric.slice(-4) : numeric.padStart(4, "0");
    const hour = parseInt(padded.slice(0, 2), 10);
    const minute = parseInt(padded.slice(2, 4), 10);
    if (Number.isNaN(hour) || Number.isNaN(minute) || hour > 23 || minute > 59) {
        return null;
    }
    return { hour, minute };
}

function getEventCalendarColor(event) {
    const cal = event.course || "Other";
    return state.calendars[cal]?.color || "#6366f1";
}

function toggleCalendarVisibility(calendarName) {
    if (state.calendars[calendarName]) {
        state.calendars[calendarName].visible = !state.calendars[calendarName].visible;
        localStorage.setItem("calendarState", JSON.stringify(Object.fromEntries(
            Object.entries(state.calendars).map(([cal, data]) => [cal, { visible: data.visible, color: data.color }])
        )));
        queueCalendarPreferenceSave(calendarName);
        render();
    }
}

function setCalendarColor(calendarName, color) {
    if (state.calendars[calendarName]) {
        state.calendars[calendarName].color = color;
        localStorage.setItem("calendarState", JSON.stringify(Object.fromEntries(
            Object.entries(state.calendars).map(([cal, data]) => [cal, { visible: data.visible, color: data.color }])
        )));
        queueCalendarPreferenceSave(calendarName);
        render();
    }
}

/* ── Controls ──────────────────────────────────────────────────────────────── */
function wireControls() {
    // Check if courses panel should be opened on load (e.g., from redirect)
    if (sessionStorage.getItem("openCoursesPanelOnLoad") === "true") {
        sessionStorage.removeItem("openCoursesPanelOnLoad");
        // Open courses modal after a short delay to ensure DOM is ready
        setTimeout(() => {
            if (!state.courses.modalOpen) {
                openCoursesModal();
            }
        }, 100);
    }

    // Handle My Courses button from profile menu
    document.addEventListener("profile-my-courses-click", () => {
        if (state.courses.modalOpen) {
            closeCoursesModal();
            return;
        }
        openCoursesModal();
    });

    document.getElementById("calendar-view-week")?.addEventListener("click", () => {
        state.view = "week";
        render();
    });
    document.getElementById("calendar-view-month")?.addEventListener("click", () => {
        state.view = "month";
        render();
    });
    document.getElementById("calendar-prev")?.addEventListener("click", () => {
        state.anchorDate = shiftAnchorDate(-1);
        render();
    });
    document.getElementById("calendar-next")?.addEventListener("click", () => {
        state.anchorDate = shiftAnchorDate(1);
        render();
    });

    document.getElementById("calendar-toggle-menu")?.addEventListener("click", (event) => {
        event.stopPropagation();
        state.ui.calendarMenuOpen = !state.ui.calendarMenuOpen;
        if (!state.ui.calendarMenuOpen) {
            closeCalendarContextMenu();
            closeRgbModal();
        }
        renderCalendarMenu();
    });

    document.getElementById("calendar-menu")?.addEventListener("change", (event) => {
        const checkbox = event.target.closest(".js-calendar-checkbox");
        if (!checkbox) return;
        event.stopPropagation();
        const calendarName = checkbox.getAttribute("data-calendar-name");
        if (!calendarName) return;
        toggleCalendarVisibility(calendarName);
        state.ui.calendarMenuOpen = true;
        renderCalendarMenu();
    });

    document.getElementById("calendar-menu")?.addEventListener("click", (event) => {
        const moreBtn = event.target.closest(".js-calendar-more");
        if (!moreBtn) return;
        event.stopPropagation();
        const calendarName = moreBtn.getAttribute("data-calendar-name");
        if (!calendarName) return;
        openCalendarContextMenu(calendarName, moreBtn);
    });

    document.addEventListener("pointerdown", (event) => {
        const popoverRoot = document.getElementById("calendar-popover-root");
        const inRoot = popoverRoot ? popoverRoot.contains(event.target) : false;
        const inContext = state.ui.contextMenuEl ? state.ui.contextMenuEl.contains(event.target) : false;
        const inRgb = state.ui.rgbModalEl ? state.ui.rgbModalEl.contains(event.target) : false;

        if (!inRoot && !inContext && !inRgb) {
            closeAllCalendarPopups();
        }
    }, true);

    window.addEventListener("resize", () => {
        positionCalendarContextMenu();
    });

    window.addEventListener("scroll", () => {
        positionCalendarContextMenu();
    }, true);

    document.addEventListener("click", (event) => {
        const closeBtn = event.target.closest("#courses-modal-close");
        if (closeBtn) {
            closeCoursesModal();
            return;
        }

        if (event.target.id === "courses-modal-overlay") {
            closeCoursesModal();
            return;
        }

        const infoBtn = event.target.closest(".js-course-info-toggle");
        if (infoBtn) {
            event.preventDefault();
            const sectionId = infoBtn.getAttribute("data-section-id");
            if (!sectionId) return;
            if (state.courses.expandedDetails.has(sectionId)) {
                state.courses.expandedDetails.delete(sectionId);
            } else {
                state.courses.expandedDetails.add(sectionId);
            }
            renderCoursesModal();
            return;
        }

        const addBtn = event.target.closest(".js-course-toggle");
        if (addBtn) {
            event.preventDefault();
            const sectionId = addBtn.getAttribute("data-section-id");
            if (!sectionId) return;
            toggleCourseSectionSelection(sectionId);
            return;
        }

        const searchSubmitBtn = event.target.closest("#courses-search-submit");
        if (searchSubmitBtn) {
            event.preventDefault();
            submitCoursesSearch();
            return;
        }
    });

    document.addEventListener("input", (event) => {
        const searchInput = event.target.closest("#courses-search-input");
        if (!searchInput) return;
        state.courses.searchInput = searchInput.value || "";
    });

    document.addEventListener("keydown", (event) => {
        if (event.key !== "Enter") return;
        const searchInput = event.target.closest("#courses-search-input");
        if (!searchInput) return;
        event.preventDefault();
        submitCoursesSearch();
    });

    document.addEventListener("change", (event) => {
        const termSelect = event.target.closest("#courses-term-select");
        if (!termSelect) return;
        state.courses.termFilter = termSelect.value || "";
        applyCourseFilters();
        writeCourseFiltersToUrl();
        renderCoursesModal();
    });

    window.addEventListener("keydown", (event) => {
        if (event.key === "Escape" && state.courses.modalOpen) {
            closeCoursesModal();
        }
    });

    window.addEventListener("popstate", () => {
        applyCoursesFiltersFromUrl();
        state.courses.searchInput = state.courses.searchQuery;
        applyCourseFilters();
        if (state.courses.modalOpen) {
            renderCoursesModal();
        }
    });
}

function closeCalendarDropdown() {
    state.ui.calendarMenuOpen = false;
    renderCalendarMenu();
}

function closeCalendarContextMenu() {
    if (state.ui.contextMenuEl) {
        state.ui.contextMenuEl.remove();
        state.ui.contextMenuEl = null;
    }
    state.ui.contextAnchorEl = null;
    state.ui.contextCalendarName = null;
}

function closeRgbModal() {
    if (state.ui.rgbModalEl) {
        state.ui.rgbModalEl.remove();
        state.ui.rgbModalEl = null;
    }
}

function closeAllCalendarPopups() {
    closeCalendarContextMenu();
    closeRgbModal();
    closeCalendarDropdown();
}

function shiftAnchorDate(delta) {
    const next = new Date(state.anchorDate);
    if (state.view === "month") {
        next.setMonth(next.getMonth() + delta);
    } else {
        next.setDate(next.getDate() + delta * 7);
    }
    return next;
}

/* ── Calendar Menu Rendering ─────────────────────────────────────────────── */
function buildCalendarMenuHtml() {
    const calendars = Object.entries(state.calendars).sort(([a], [b]) => a.localeCompare(b));
    const showSimulated = state.courses.selectedSectionIds.size > 0;

    const buildRows = (items) => items.map(([cal, data]) => {
        const checked = data.visible ? "checked" : "";
        const busy = state.ui.pendingCalendars.has(cal);
        return `
            <div class="flex items-center justify-between px-4 py-1.5 hover:bg-surface-container-high transition-colors gap-1.5 group">
                <label class="flex items-center gap-2 flex-1 cursor-pointer">
                    <input type="checkbox" ${checked} ${busy ? "disabled" : ""}
                        class="js-calendar-checkbox w-5 h-5 rounded border border-black/30 appearance-none cursor-pointer disabled:cursor-not-allowed disabled:opacity-60"
                        data-calendar-name="${escapeHtml(cal)}"
                        style="background-color:${data.color};">
                    <span class="text-sm text-on-surface truncate">${escapeHtml(cal)}</span>
                </label>
                <button type="button"
                    class="js-calendar-more p-1.5 hover:bg-surface-container rounded opacity-0 group-hover:opacity-100 transition-opacity disabled:opacity-40"
                    data-calendar-name="${escapeHtml(cal)}"
                    ${busy ? "disabled" : ""}
                    aria-label="Calendar options">
                    <span class="material-symbols-outlined text-[20px]" style="font-variation-settings:'FILL' 1,'wght' 700,'GRAD' 0,'opsz' 24;">more_horiz</span>
                </button>
            </div>
        `;
    }).join("");

    const emoryCalendars = calendars.filter(([name]) => {
        if (name === CANVAS_CALENDAR_NAME) return true;
        if (name === SIMULATED_CALENDAR_NAME) return showSimulated;
        return false;
    });

    const otherCalendars = calendars.filter(([name]) => {
        if (name === CANVAS_CALENDAR_NAME) return false;
        if (name === SIMULATED_CALENDAR_NAME) return false;
        return true;
    });

    const emorySection = `
        <div class="px-4 pt-1.5 pb-0.5 text-[10px] uppercase tracking-[0.08em] text-on-surface-variant font-semibold">Emory</div>
        ${buildRows(emoryCalendars)}
    `;

    const otherSection = `
        <div class="h-px bg-outline-variant/25 my-0.5"></div>
        <div class="px-4 pt-1.5 pb-0.5 text-[10px] uppercase tracking-[0.08em] text-on-surface-variant font-semibold">Other</div>
        ${otherCalendars.length ? buildRows(otherCalendars) : '<div class="px-4 py-1.5 text-sm text-on-surface-variant">No other calendars</div>'}
    `;

    return `
        <div class="absolute top-full right-0 mt-1 w-64 rounded-lg border border-outline-variant/40 bg-surface shadow-xl shadow-black/20 z-50 py-0.5">
            ${emorySection}
            ${otherSection}
        </div>
    `;
}

function openCalendarContextMenu(calendarName, anchorEl) {
    closeCalendarContextMenu();

    state.ui.contextCalendarName = calendarName;
    state.ui.contextAnchorEl = anchorEl;

    const currentColor = state.calendars[calendarName]?.color || "#6366f1";
    const eventCount = state.events.filter((event) => (event.course || "Other") === calendarName).length;
    const pending = state.ui.pendingCalendars.has(calendarName);

    const menu = document.createElement("div");
    menu.className = "fixed min-w-[260px] rounded-2xl border border-outline-variant/40 shadow-2xl z-[70] overflow-hidden";
    menu.style.background = "linear-gradient(180deg, color-mix(in srgb, var(--color-surface-container-high) 90%, white 10%) 0%, color-mix(in srgb, var(--color-surface-container) 93%, black 7%) 100%)";
    menu.style.backdropFilter = "blur(12px)";
    menu.innerHTML = `
        <div class="py-2">
            <button type="button" ${pending ? "disabled" : ""} class="js-context-info w-full px-4 py-2.5 text-sm text-on-surface hover:bg-surface-container-high/70 transition-colors flex items-center gap-3 disabled:opacity-60 disabled:cursor-not-allowed">
                <span class="material-symbols-outlined text-[18px]">info</span>
                <span>Get Info</span>
            </button>
            <div class="h-px bg-outline-variant/40 my-2"></div>
            <div class="px-4 py-2">
                <div class="text-[11px] uppercase tracking-[0.08em] text-on-surface-variant font-semibold mb-2">Colors</div>
                <div class="flex gap-2.5 flex-wrap">
                    ${state.calendarColors.map((color) => `
                        <button type="button" ${pending ? "disabled" : ""}
                            class="js-context-preset w-7 h-7 rounded-full border-2 transition-transform hover:scale-105 disabled:opacity-60 disabled:cursor-not-allowed"
                            data-color="${color}"
                            style="background:${color}; border-color:${currentColor === color ? "#111" : "transparent"};">
                        </button>
                    `).join("")}
                </div>
            </div>
            <div class="h-px bg-outline-variant/40 my-2"></div>
            <button type="button" ${pending ? "disabled" : ""} class="js-context-custom w-full px-4 py-2.5 text-sm text-on-surface hover:bg-surface-container-high/70 transition-colors flex items-center gap-3 disabled:opacity-60 disabled:cursor-not-allowed">
                <span class="material-symbols-outlined text-[18px]">palette</span>
                <span>Custom Color...</span>
            </button>
            <div class="px-4 pt-2 text-[11px] text-on-surface-variant">${escapeHtml(calendarName)} · ${eventCount} events</div>
        </div>
    `;

    menu.addEventListener("click", async (event) => {
        const infoBtn = event.target.closest(".js-context-info");
        if (infoBtn) {
            showCalendarInfo(calendarName, eventCount);
            closeCalendarContextMenu();
            return;
        }

        const presetBtn = event.target.closest(".js-context-preset");
        if (presetBtn) {
            const color = presetBtn.getAttribute("data-color");
            if (color && !state.ui.pendingCalendars.has(calendarName)) {
                setCalendarColor(calendarName, color);
                closeCalendarContextMenu();
            }
            return;
        }

        const customBtn = event.target.closest(".js-context-custom");
        if (customBtn) {
            if (!state.ui.pendingCalendars.has(calendarName)) {
                openRgbModal(calendarName);
            }
        }
    });

    document.body.appendChild(menu);
    state.ui.contextMenuEl = menu;
    positionCalendarContextMenu();
}

function showCalendarInfo(calendarName, eventCount) {
    const info = `Calendar: ${calendarName}\nEvents: ${eventCount}`;
    alert(info);
}

function positionCalendarContextMenu() {
    if (!state.ui.contextMenuEl || !state.ui.contextAnchorEl) return;
    const menu = state.ui.contextMenuEl;
    const anchorRect = state.ui.contextAnchorEl.getBoundingClientRect();
    const menuRect = menu.getBoundingClientRect();

    let left = anchorRect.right - menuRect.width;
    let top = anchorRect.bottom + 8;

    if (left + menuRect.width > window.innerWidth - 8) left = window.innerWidth - menuRect.width - 8;
    if (left < 8) left = 8;

    if (top + menuRect.height > window.innerHeight - 8) {
        top = anchorRect.top - menuRect.height - 8;
    }
    if (top < 8) top = 8;

    menu.style.left = `${left}px`;
    menu.style.top = `${top}px`;
}

function rgbToHex(r, g, b) {
    const toHex = (value) => Math.max(0, Math.min(255, Number(value) || 0)).toString(16).padStart(2, "0");
    return `#${toHex(r)}${toHex(g)}${toHex(b)}`;
}

function openRgbModal(calendarName) {
    closeRgbModal();

    const current = state.calendars[calendarName]?.color || "#6366f1";
    const match = /^#([0-9a-fA-F]{6})$/.exec(current);
    const hex = match ? match[1] : "6366f1";
    const initial = {
        r: parseInt(hex.slice(0, 2), 16),
        g: parseInt(hex.slice(2, 4), 16),
        b: parseInt(hex.slice(4, 6), 16),
    };

    const modal = document.createElement("div");
    modal.className = "fixed inset-0 z-[80] bg-black/50 backdrop-blur-[2px] flex items-center justify-center p-4";
    modal.innerHTML = `
        <div class="w-full max-w-[480px] rounded-2xl border border-outline-variant/40 bg-surface p-5 shadow-2xl" role="dialog" aria-modal="true" aria-label="Custom calendar color">
            <div class="flex items-center justify-between mb-4">
                <h3 class="text-base font-semibold text-on-surface">Custom Color</h3>
                <button type="button" class="js-rgb-close p-1 rounded hover:bg-surface-container-high" aria-label="Close color picker">
                    <span class="material-symbols-outlined text-[18px]">close</span>
                </button>
            </div>

            <div class="grid grid-cols-1 sm:grid-cols-[1fr_1fr] gap-4">
                <div class="space-y-3">
                    ${["r", "g", "b"].map((channel) => `
                        <div>
                            <div class="flex items-center justify-between text-xs text-on-surface-variant mb-1.5">
                                <span>${channel.toUpperCase()}</span>
                                <input type="number" min="0" max="255" value="${initial[channel]}" class="js-rgb-number w-16 rounded border border-outline-variant/50 bg-surface-container-low px-2 py-1 text-right text-sm text-on-surface" data-channel="${channel}">
                            </div>
                            <input type="range" min="0" max="255" value="${initial[channel]}" class="js-rgb-range w-full" data-channel="${channel}">
                        </div>
                    `).join("")}
                </div>

                <div class="rounded-xl border border-outline-variant/40 bg-surface-container-low p-3 flex flex-col gap-2">
                    <div class="text-xs text-on-surface-variant">Preview</div>
                    <div class="h-16 rounded-lg border border-black/15 js-rgb-preview"></div>
                    <div class="text-xs text-on-surface-variant">Hex</div>
                    <div class="font-mono text-sm text-on-surface js-rgb-hex"></div>
                </div>
            </div>

            <div class="mt-5 flex justify-end gap-2">
                <button type="button" class="js-rgb-cancel px-3 py-1.5 rounded-md border border-outline-variant/50 text-sm text-on-surface hover:bg-surface-container-high">Cancel</button>
                <button type="button" class="js-rgb-save px-3 py-1.5 rounded-md bg-primary text-on-primary text-sm font-medium disabled:opacity-60 disabled:cursor-not-allowed">Save</button>
            </div>
        </div>
    `;

    const readRgb = () => {
        const getValue = (channel) => {
            const num = modal.querySelector(`.js-rgb-number[data-channel='${channel}']`);
            return Math.max(0, Math.min(255, Number(num?.value || 0)));
        };
        return { r: getValue("r"), g: getValue("g"), b: getValue("b") };
    };

    const paintPreview = () => {
        const { r, g, b } = readRgb();
        const hexValue = rgbToHex(r, g, b);
        const preview = modal.querySelector(".js-rgb-preview");
        const hexText = modal.querySelector(".js-rgb-hex");
        if (preview) preview.style.background = hexValue;
        if (hexText) hexText.textContent = hexValue.toUpperCase();
    };

    modal.addEventListener("input", (event) => {
        const range = event.target.closest(".js-rgb-range");
        const number = event.target.closest(".js-rgb-number");

        if (range) {
            const channel = range.getAttribute("data-channel");
            const input = modal.querySelector(`.js-rgb-number[data-channel='${channel}']`);
            if (input) input.value = range.value;
            paintPreview();
            return;
        }

        if (number) {
            const channel = number.getAttribute("data-channel");
            const next = Math.max(0, Math.min(255, Number(number.value || 0)));
            number.value = String(next);
            const input = modal.querySelector(`.js-rgb-range[data-channel='${channel}']`);
            if (input) input.value = String(next);
            paintPreview();
        }
    });

    modal.addEventListener("click", async (event) => {
        if (event.target === modal || event.target.closest(".js-rgb-close") || event.target.closest(".js-rgb-cancel")) {
            closeRgbModal();
            return;
        }

        if (event.target.closest(".js-rgb-save")) {
            const saveButton = modal.querySelector(".js-rgb-save");
            if (saveButton?.disabled) return;

            const { r, g, b } = readRgb();
            const hexValue = rgbToHex(r, g, b);
            if (!/^#[0-9a-fA-F]{6}$/.test(hexValue)) return;

            if (saveButton) {
                saveButton.disabled = true;
                saveButton.innerHTML = `<span class="inline-flex flex-col items-center justify-center gap-1 leading-none"><span class="loader" style="width:16px; height:16px;" aria-hidden="true"></span><span class="text-[10px]">Saving...</span></span>`;
            }

            setCalendarColor(calendarName, hexValue);
            await persistCalendarPreference(calendarName);
            closeRgbModal();
            closeCalendarContextMenu();
            state.ui.calendarMenuOpen = true;
            renderCalendarMenu();
        }
    });

    document.body.appendChild(modal);
    state.ui.rgbModalEl = modal;
    paintPreview();
}

/* ── Top-Level Render ──────────────────────────────────────────────────────── */
function render() {
    updateHeader();
    updateViewToggleButtons();
    renderCalendarMenu();
    renderCalendarView();
    renderAssignments();
    renderCoursesModal();
}

function renderCalendarMenu() {
    const container = document.getElementById("calendar-menu");
    if (!container) return;
    if (!state.ui.calendarMenuOpen) {
        container.style.display = "none";
        container.innerHTML = "";
        return;
    }
    container.innerHTML = buildCalendarMenuHtml();
    container.style.display = "block";
}

function updateHeader() {
    const title = document.getElementById("calendar-title");
    const subtitle = document.getElementById("calendar-subtitle");
    if (!title || !subtitle) return;

    const monthLabel = state.anchorDate.toLocaleDateString(undefined, { month: "long", year: "numeric" });

    if (state.view === "month") {
        title.textContent = monthLabel;
        subtitle.textContent = state.feedConfigured
            ? "Monthly view from your saved iCal feed"
            : "Monthly view (no .ics feed connected yet)";
        return;
    }

    const start = getStartOfWeek(state.anchorDate);
    const end = new Date(start);
    end.setDate(end.getDate() + 6);

    title.textContent = `Week of ${start.toLocaleDateString(undefined, { month: "short", day: "numeric" })} - ${end.toLocaleDateString(undefined, { month: "short", day: "numeric" })}`;
    subtitle.textContent = state.feedConfigured
        ? `Weekly view (${monthLabel}) from your saved iCal feed`
        : `Weekly view (${monthLabel}) with no .ics feed connected`;
}

function updateViewToggleButtons() {
    const weekBtn = document.getElementById("calendar-view-week");
    const monthBtn = document.getElementById("calendar-view-month");
    if (!weekBtn || !monthBtn) return;

    const active = "px-3 py-1.5 text-sm rounded-md bg-primary text-on-primary font-medium";
    const inactive = "px-3 py-1.5 text-sm rounded-md text-on-surface-variant hover:text-on-surface";

    weekBtn.className = state.view === "week" ? active : inactive;
    monthBtn.className = state.view === "month" ? active : inactive;

    if (state.view !== "week") {
        state.ui.weeklyAutoScrollKey = null;
    }
}

function renderCalendarView() {
    const root = document.getElementById("calendar-view-root");
    if (!root) return;

    if (state.loadingDashboard) {
        root.innerHTML = `
            <div class="rounded-2xl border border-calendar-rule bg-surface-container shadow-2xl shadow-black/10 p-10 min-h-[420px] flex items-center justify-center">
                ${buildLoadingIndicatorHtml("Loading calendar...", { sizePx: 54, textToneClass: "text-on-surface" })}
            </div>
        `;
        return;
    }

    root.innerHTML = state.view === "month" ? buildMonthViewHtml() : buildWeekViewHtml();

    if (state.view === "week") {
        applyWeekAutoScroll();
    }
}

function getWeekKeyFromAnchor(anchorDate) {
    const weekStart = getStartOfWeek(anchorDate);
    return `${weekStart.getFullYear()}-${String(weekStart.getMonth() + 1).padStart(2, "0")}-${String(weekStart.getDate()).padStart(2, "0")}`;
}

function applyWeekAutoScroll() {
    const scroller = document.getElementById("calendar-week-time-scroller");
    if (!scroller) return;

    const weekKey = getWeekKeyFromAnchor(state.anchorDate);
    if (state.ui.weeklyAutoScrollKey === weekKey) return;

    const targetMinutes = (7 * 60) + 30;
    const targetScrollTop = Math.round((targetMinutes / 60) * HOUR_HEIGHT_PX);
    scroller.scrollTop = targetScrollTop;
    state.ui.weeklyAutoScrollKey = weekKey;
}

/* ── Month View ────────────────────────────────────────────────────────────── */
function buildMonthViewHtml() {
    const year = state.anchorDate.getFullYear();
    const month = state.anchorDate.getMonth();
    const monthStart = new Date(year, month, 1);
    const monthEnd = new Date(year, month + 1, 0);
    const gridStart = new Date(monthStart);
    gridStart.setDate(monthStart.getDate() - monthStart.getDay());

    const cells = [];
    for (let i = 0; i < 42; i++) {
        const day = new Date(gridStart);
        day.setDate(gridStart.getDate() + i);
        const inMonth = day >= monthStart && day <= monthEnd;
        const dayEvents = getEventsForDay(day);
        const isCurrentDay = isToday(day);
        const todayDateClass = isCurrentDay ? "text-red-500" : "text-on-surface-variant";
        const todayDateOutline = isCurrentDay
            ? '<span class="absolute inset-0 rounded-full border border-red-500"></span>'
            : "";

        cells.push(`
            <div data-date="${day.getFullYear()}-${String(day.getMonth() + 1).padStart(2, "0")}-${String(day.getDate()).padStart(2, "0")}" class="rounded-lg border ${inMonth ? "border-outline-variant/10 bg-surface-container" : "border-outline-variant/5 bg-surface-container/40 opacity-70"} p-2.5 min-h-[98px] flex flex-col gap-1.5">
                <div class="relative inline-flex h-7 w-7 items-center justify-center text-xs font-semibold ${todayDateClass}">
                    ${todayDateOutline}
                    <span class="relative z-10">${day.getDate()}</span>
                </div>
                <div class="space-y-1">
                    ${dayEvents.slice(0, 3).map((e) => buildEventChip(e, { monthView: true })).join("")}
                    ${dayEvents.length > 3 ? `<div class="text-[10px] text-on-surface-variant">+${dayEvents.length - 3} more</div>` : ""}
                </div>
            </div>
        `);
    }

    return `
        <div class="grid grid-cols-7 gap-2 mb-2">
            ${WEEKDAYS.map((d) => `<div class="text-[11px] uppercase tracking-[0.05em] text-center text-on-surface-variant font-semibold">${d}</div>`).join("")}
        </div>
        <div class="grid grid-cols-7 gap-2" style="min-height:680px;">
            ${cells.join("")}
        </div>
    `;
}

/* ── Week View ─────────────────────────────────────────────────────────────── */
function buildWeekViewHtml() {
    const weekStart = getStartOfWeek(state.anchorDate);
    const days = Array.from({ length: 7 }, (_, i) => {
        const d = new Date(weekStart);
        d.setDate(weekStart.getDate() + i);
        return d;
    });

    const weekEnd = new Date(days[6]);
    weekEnd.setHours(23, 59, 59, 999);

    const allDayEvents = getAllDayEventsForWeek(days, weekStart, weekEnd);
    const hasAllDayEvents = allDayEvents.length > 0;
    const timedByDay = days.map((d) => getEventsForDay(d).filter((e) => !e.isAllDay));

    const totalHours = 24;
    const timeGridHeight = totalHours * HOUR_HEIGHT_PX;
    const dayColTemplate = `minmax(${WEEK_MINIMUM_DAY_WIDTH_PX}px, 1fr)`;
    const gridCols = `4.75rem repeat(7, ${dayColTemplate})`;

    const dayHeaders = days.map((day, i) => {
        const dateLabel = `${day.getMonth() + 1}/${day.getDate()}`;
        const isCurrentDay = isToday(day);
        const todayHeaderClass = isCurrentDay ? "border border-red-500 text-red-500" : "";
        const todayClass = isCurrentDay ? "text-red-500" : "text-on-surface";
        return `
            <div class="px-3 py-3 text-center border-r border-calendar-rule last:border-r-0 flex items-center justify-center">
                <div class="inline-flex min-h-[3rem] min-w-[4.75rem] flex-col items-center justify-center rounded-full px-3 py-1 ${todayHeaderClass}">
                    <div class="text-[11px] uppercase tracking-[0.05em] font-semibold ${isCurrentDay ? "text-red-500" : "text-on-surface-variant"}">${WEEKDAYS[i]}</div>
                    <div class="text-sm font-semibold ${todayClass}">${dateLabel}</div>
                </div>
            </div>
        `;
    }).join("");

    const allDayRowHeight = Math.max(ALL_DAY_MIN_HEIGHT_PX, allDayEvents.length * 28 + 16);
    const allDayChips = allDayEvents.map((ev) => {
        const badgeStyle = getEventBadgeStyle(ev);
        const colStart = ev.gridColStart + 2;
        const colEnd = colStart + ev.gridSpan;
        return `
            <div class="group relative" style="grid-column: ${colStart} / ${colEnd};">
                <div class="text-[10px] px-2 py-1 rounded-md border truncate" style="${badgeStyle}">
                    ${escapeHtml(ev.title || "Untitled")}
                </div>
                <div class="hidden group-hover:block absolute left-0 top-full mt-1 z-40 w-64 rounded-lg border border-outline-variant/30 bg-surface p-2.5 shadow-xl shadow-black/20">
                    <div class="text-xs font-semibold text-on-surface mb-1">${escapeHtml(ev.title || "Untitled")}</div>
                    <div class="text-[11px] text-on-surface-variant">${escapeHtml(formatAllDayRange(ev))}</div>
                    ${ev.course ? `<div class="text-[10px] text-on-surface-variant mt-1">${escapeHtml(ev.course)}</div>` : ""}
                    ${ev.description ? `<div class="text-[11px] text-on-surface-variant mt-1.5 leading-relaxed">${escapeHtml(ev.description)}</div>` : ""}
                </div>
            </div>
        `;
    }).join("");

    const timeAxisCells = [];
    for (let h = 0; h < 24; h++) {
        timeAxisCells.push(`
            <div class="border-b border-calendar-rule px-2 flex items-start justify-end" style="height:${HOUR_HEIGHT_PX}px;">
                <span class="relative z-10 text-[11px] text-on-surface-variant font-medium leading-none -translate-y-[0.35rem]">${formatHourLabel(h)}</span>
            </div>
        `);
    }

    const dayColumns = days.map((day, idx) => {
        const positioned = layoutTimedEvents(timedByDay[idx]);
        const todayBg = isToday(day) ? "bg-primary/[0.03]" : "";

        const gridLines = [];
        for (let h = 0; h < 24; h++) {
            const timeLabel = `${String(h).padStart(2, "0")}:00`;
            gridLines.push(`<div data-time="${timeLabel}" class="border-b border-calendar-rule" style="height:${HOUR_HEIGHT_PX}px;"></div>`);
        }

        const eventChips = positioned.map((e) => renderTimedEvent(e)).join("");

        return `
            <div data-date="${day.getFullYear()}-${String(day.getMonth() + 1).padStart(2, "0")}-${String(day.getDate()).padStart(2, "0")}" class="relative border-r border-calendar-rule last:border-r-0 ${todayBg} overflow-visible">
                <div class="absolute inset-0 pointer-events-none">
                    ${gridLines.join("")}
                </div>
                <div class="relative" style="height:${timeGridHeight}px;">
                    ${eventChips}
                </div>
            </div>
        `;
    }).join("");

    return `
        <div class="rounded-2xl border border-calendar-rule bg-surface-container shadow-2xl shadow-black/10 overflow-hidden">
            <div class="grid border-b border-calendar-rule bg-surface-container sticky top-0 z-30" style="grid-template-columns: ${gridCols};">
                <div class="px-3 py-3 border-r border-calendar-rule"></div>
                ${dayHeaders}
            </div>

            ${hasAllDayEvents ? `
            <div class="grid border-b border-calendar-rule bg-surface-container sticky top-[3.75rem] z-20 items-start gap-y-1 p-1" style="grid-template-columns: ${gridCols}; min-height: ${allDayRowHeight}px;">
                <div class="px-2 py-2 text-[10px] uppercase tracking-[0.08em] text-on-surface-variant font-semibold border-r border-calendar-rule flex items-start justify-end" style="grid-row: 1 / span ${Math.max(1, allDayEvents.length)};">All day</div>
                ${allDayChips}
            </div>
            ` : ""}

            <div id="calendar-week-time-scroller" class="overflow-y-auto overflow-x-auto" style="max-height:760px;">
                <div class="grid" style="grid-template-columns: ${gridCols}; min-width: calc(4.75rem + 7 * ${WEEK_MINIMUM_DAY_WIDTH_PX}px);">
                    <div class="border-r border-calendar-rule bg-surface-container">
                        ${timeAxisCells.join("")}
                    </div>
                    ${dayColumns}
                </div>
            </div>
        </div>
    `;
}

/* ── All-Day Event Collection for Week View ────────────────────────────────── */
function getAllDayEventsForWeek(days, weekStart, weekEnd) {
    const seen = new Set();
    const results = [];

    for (const event of getVisibleEvents()) {
        if (!event.isAllDay) continue;

        const eStart = event.startDate;
        const eEnd = event.endDate || event.startDate;

        if (eStart > weekEnd || eEnd < weekStart) continue;

        const key = event.uid || `${event.title}|${eStart.getTime()}`;
        if (seen.has(key)) continue;
        seen.add(key);

        // Use local date methods since all-day dates are stored as local midnight
        const eventStartDay = dateToDayIndex(eStart, days);
        // iCal DTEND for all-day is exclusive, so subtract 1 day for the last visible day
        const lastVisibleDay = new Date(eEnd);
        lastVisibleDay.setDate(lastVisibleDay.getDate() - 1);
        const eventEndDay = dateToDayIndex(lastVisibleDay, days);

        const clampedStart = Math.max(0, eventStartDay);
        const clampedEnd = Math.min(6, eventEndDay);
        const span = clampedEnd - clampedStart + 1;

        if (span <= 0) continue;

        results.push({
            ...event,
            gridColStart: clampedStart,
            gridSpan: span,
        });
    }

    results.sort((a, b) => b.gridSpan - a.gridSpan || (a.title || "").localeCompare(b.title || ""));
    return results;
}

/**
 * Returns the 0-6 day-column index for a date relative to the week.
 * Uses local date components since all-day events are parsed as local midnight
 * and timed events are already converted to local by the Date constructor.
 */
function dateToDayIndex(date, weekDays) {
    const dayStart = new Date(date.getFullYear(), date.getMonth(), date.getDate());
    const weekStartDay = new Date(weekDays[0].getFullYear(), weekDays[0].getMonth(), weekDays[0].getDate());
    const diffMs = dayStart - weekStartDay;
    return Math.round(diffMs / (1000 * 60 * 60 * 24));
}

/* ── Timed Event Rendering (week view) ─────────────────────────────────────── */
function renderTimedEvent(event) {
    const topPx = (event.layoutStartMinutes / 60) * HOUR_HEIGHT_PX;
    const heightPx = Math.max((event.layoutDurationMinutes / 60) * HOUR_HEIGHT_PX, 22);
    const leftPct = (event.layoutLane / event.layoutLaneCount) * 100;
    const widthPct = 100 / event.layoutLaneCount;
    const badgeStyle = getEventBadgeStyle(event);
    const course = event.course ? `<div class="text-[10px] text-on-surface-variant mt-1">${escapeHtml(event.course)}</div>` : "";
    const timeDisplay = formatTimedEventRange(event);

    const dataId = event.id || event.uid || "";
    return `
        <div data-event-id="${dataId}" class="group absolute px-0.5" style="top:${topPx}px; left:${leftPct}%; width:calc(${widthPct}% - 0.25rem); height:${heightPx}px; z-index: 10;">
            <div class="h-full rounded-lg border overflow-hidden shadow-lg shadow-black/10" style="${badgeStyle}">
                <div class="h-full px-2 py-1.5 flex flex-col gap-0.5 text-left">
                    <div class="text-[11px] font-semibold leading-tight line-clamp-2">${escapeHtml(event.title || "Untitled")}</div>
                    <div class="text-[10px]">${escapeHtml(formatTimeOnly(event.startDate))}</div>
                </div>
            </div>
            <div class="hidden group-hover:block absolute left-0 top-full mt-1 z-40 w-64 rounded-lg border border-outline-variant/30 bg-surface p-2.5 shadow-xl shadow-black/20 pointer-events-none">
                <div class="text-xs font-semibold text-on-surface mb-1">${escapeHtml(event.title || "Untitled")}</div>
                <div class="text-[11px] text-on-surface-variant">${escapeHtml(timeDisplay)}</div>
                ${course}
                ${event.description ? `<div class="text-[11px] text-on-surface-variant mt-1.5 leading-relaxed">${escapeHtml(event.description)}</div>` : ""}
            </div>
        </div>
    `;
}

/* ── Event Chip (month + all-day row) ──────────────────────────────────────── */
function buildEventChip(event, options = {}) {
    const sizeClass = options.compact ? "text-[10px] px-2 py-1" : "text-[11px] px-2 py-1";
    const badgeStyle = getEventBadgeStyle(event);
    const badgeColors = getEventBadgeColors(event);
    const calendarColor = getEventCalendarColor(event);
    const isMonthTimedChip = Boolean(options.monthView) && !event.isAllDay;
    const course = event.course ? `<div class="text-[10px] text-on-surface-variant mt-1">${escapeHtml(event.course)}</div>` : "";
    const timeDisplay = event.isAllDay ? formatAllDayRange(event) : formatTimedEventRange(event);

    if (isMonthTimedChip) {
        const dataId = event.id || event.uid || "";
        return `
            <div data-event-id="${dataId}" class="group relative">
                <div class="${sizeClass} rounded-md border bg-surface-container-high/50 truncate flex items-center gap-2" style="border-color: ${badgeColors.border};">
                    <span class="inline-block h-4 w-1 rounded-full shrink-0" style="background-color: ${calendarColor};"></span>
                    <span class="truncate text-on-surface font-medium">${escapeHtml(event.title || "Untitled")}</span>
                </div>
                <div class="hidden group-hover:block absolute left-0 top-full mt-1 z-20 w-64 rounded-lg border border-outline-variant/30 bg-surface p-2.5 shadow-xl shadow-black/20">
                    <div class="text-xs font-semibold text-on-surface mb-1">${escapeHtml(event.title || "Untitled")}</div>
                    <div class="text-[11px] text-on-surface-variant">${escapeHtml(timeDisplay)}</div>
                    ${course}
                    ${event.description ? `<div class="text-[11px] text-on-surface-variant mt-1.5 leading-relaxed">${escapeHtml(event.description)}</div>` : ""}
                </div>
            </div>
        `;
    }

    const dataId = event.id || event.uid || "";
    return `
        <div data-event-id="${dataId}" class="group relative">
            <div class="${sizeClass} rounded-md border truncate" style="${badgeStyle}">
                ${escapeHtml(event.title || "Untitled")}
            </div>
            <div class="hidden group-hover:block absolute left-0 top-full mt-1 z-20 w-64 rounded-lg border border-outline-variant/30 bg-surface p-2.5 shadow-xl shadow-black/20">
                <div class="text-xs font-semibold text-on-surface mb-1">${escapeHtml(event.title || "Untitled")}</div>
                <div class="text-[11px] text-on-surface-variant">${escapeHtml(timeDisplay)}</div>
                ${course}
                ${event.description ? `<div class="text-[11px] text-on-surface-variant mt-1.5 leading-relaxed">${escapeHtml(event.description)}</div>` : ""}
            </div>
        </div>
    `;
}

/* ── Assignments Section ───────────────────────────────────────────────────── */
function renderAssignments() {
    const root = document.getElementById("assignments-root");
    if (!root) return;

    if (state.loadingDashboard) {
        root.innerHTML = `
            <div class="md:col-span-2 lg:col-span-3 rounded-xl border border-outline-variant/20 bg-surface-container p-10 text-center">
                ${buildLoadingIndicatorHtml("Loading upcoming assignments...", { sizePx: 46, textToneClass: "text-on-surface" })}
            </div>
        `;
        return;
    }

    const now = new Date();
    const upcoming = getVisibleEvents()
        .filter((e) => e.source !== "simulated")
        .filter((e) => e.endDate >= now || e.startDate >= now)
        .slice(0, 12);

    if (!upcoming.length) {
        root.innerHTML = `
            <div class="md:col-span-2 lg:col-span-3 rounded-xl border border-outline-variant/20 bg-surface-container p-6 text-sm text-on-surface-variant">
                No assignments found yet. Your calendar is still available above.
            </div>
        `;
        return;
    }

    root.innerHTML = upcoming.map((event) => {
        const urgency = event.isAllDay ? getUrgencyLabelAllDay(event) : getUrgencyLabel(event.startDate);
        const accent = getAccent(event.type, event.startDate);
        const timeDisplay = event.isAllDay ? formatAllDayRange(event) : formatDateTime(event.startDate);

        return `
            <article class="bg-surface-container hover:bg-surface-container-high transition-all duration-300 rounded-xl p-5 relative overflow-hidden flex flex-col gap-3">
                <div class="absolute left-0 top-0 bottom-0 w-1 ${accent.bar}"></div>
                <div class="flex justify-between items-start gap-3">
                    <span class="text-[10px] uppercase tracking-[0.05em] font-bold px-2 py-1 rounded ${accent.tag}">${escapeHtml(urgency)}</span>
                    <span class="text-[11px] text-on-surface-variant">${escapeHtml(timeDisplay)}</span>
                </div>
                <div>
                    <h3 class="text-base font-headline font-semibold text-on-surface leading-tight mb-1">${escapeHtml(event.title || "Untitled")}</h3>
                    <p class="text-sm text-on-surface-variant">${escapeHtml(event.course || "Other")}</p>
                </div>
                ${event.description ? `<p class="text-xs text-on-surface-variant line-clamp-3">${escapeHtml(event.description)}</p>` : ""}
            </article>
        `;
    }).join("");
}

/* ── Event Query Helpers ───────────────────────────────────────────────────── */
function getEventsForDay(date) {
    const dayStart = new Date(date.getFullYear(), date.getMonth(), date.getDate(), 0, 0, 0, 0);
    const dayEnd = new Date(date.getFullYear(), date.getMonth(), date.getDate(), 23, 59, 59, 999);
    return getVisibleEvents().filter((e) => {
        const eStart = e.startDate;
        const eEnd = e.endDate || e.startDate;

        if (e.isAllDay) {
            // All-day events: DTEND is exclusive.
            // An event on Apr 24 has start=Apr 24 00:00, end=Apr 25 00:00.
            // It should appear on Apr 24 only, not Apr 25.
            // Use date-only comparison: event overlaps day if
            // eventStart < dayEnd (exclusive) AND eventEnd > dayStart (exclusive)
            const eventStartDay = new Date(eStart.getFullYear(), eStart.getMonth(), eStart.getDate());
            const eventEndDay = new Date(eEnd.getFullYear(), eEnd.getMonth(), eEnd.getDate());
            const thisDayStart = new Date(date.getFullYear(), date.getMonth(), date.getDate());
            const nextDayStart = new Date(date.getFullYear(), date.getMonth(), date.getDate() + 1);
            // Event occupies [eventStartDay, eventEndDay) since DTEND is exclusive
            return eventStartDay < nextDayStart && eventEndDay > thisDayStart;
        }

        // Timed events: inclusive overlap check
        return eStart <= dayEnd && eEnd >= dayStart;
    });
}

/* ── Lane Layout for Overlapping Timed Events ──────────────────────────────── */
function layoutTimedEvents(events) {
    const items = events.map((e) => {
        const startMin = e.startDate.getHours() * 60 + e.startDate.getMinutes();
        const endDate = e.endDate || e.startDate;
        const durMin = Math.max(30, Math.ceil((endDate - e.startDate) / 60000));
        return { ...e, layoutStartMinutes: startMin, layoutDurationMinutes: durMin, layoutLane: 0, layoutLaneCount: 1 };
    }).sort((a, b) => a.layoutStartMinutes - b.layoutStartMinutes || a.layoutDurationMinutes - b.layoutDurationMinutes);

    const laneEnds = [];
    for (const item of items) {
        let lane = 0;
        while (lane < laneEnds.length && laneEnds[lane] > item.layoutStartMinutes) lane++;
        if (lane === laneEnds.length) laneEnds.push(0);
        laneEnds[lane] = item.layoutStartMinutes + item.layoutDurationMinutes;
        item.layoutLane = lane;
    }

    const count = Math.max(1, laneEnds.length);
    items.forEach((item) => { item.layoutLaneCount = count; });
    return items;
}

/* ── Date / Time Formatting ────────────────────────────────────────────────── */
function formatHourLabel(hour) {
    if (hour === 0) return "12 AM";
    if (hour < 12) return `${hour} AM`;
    if (hour === 12) return "12 PM";
    return `${hour - 12} PM`;
}

function formatTimeOnly(date) {
    return date.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}

function formatDateTime(date) {
    return date.toLocaleDateString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

function formatTimedEventRange(event) {
    const start = event.startDate;
    const end = event.endDate || event.startDate;
    const sameDay =
        start.getFullYear() === end.getFullYear()
        && start.getMonth() === end.getMonth()
        && start.getDate() === end.getDate();

    if (sameDay) {
        const dateLabel = start.toLocaleDateString(undefined, { month: "short", day: "numeric" });
        return `${dateLabel}, ${formatTimeOnly(start)} - ${formatTimeOnly(end)}`;
    }

    return `${formatDateTime(start)} - ${formatDateTime(end)}`;
}

function formatAllDayRange(event) {
    const startStr = event.startDate.toLocaleDateString(undefined, { month: "short", day: "numeric" });
    if (!event.isMultiDay || event.spanDays <= 1) {
        return `${startStr} (All day)`;
    }
    // endDate for all-day iCal events is exclusive, so subtract 1 day for display
    const displayEnd = new Date(event.endDate);
    displayEnd.setDate(displayEnd.getDate() - 1);
    const endStr = displayEnd.toLocaleDateString(undefined, { month: "short", day: "numeric" });
    return `${startStr} - ${endStr} (All day)`;
}

function getStartOfWeek(date) {
    const d = new Date(date);
    d.setHours(0, 0, 0, 0);
    d.setDate(d.getDate() - d.getDay());
    return d;
}

function isToday(date) {
    const now = new Date();
    return date.getFullYear() === now.getFullYear() && date.getMonth() === now.getMonth() && date.getDate() === now.getDate();
}

/* ── Urgency & Accent ──────────────────────────────────────────────────────── */
function getUrgencyLabel(startDate) {
    const diffDays = Math.floor((startDate - new Date()) / (1000 * 60 * 60 * 24));
    if (diffDays < 0) return "Past Due";
    if (diffDays === 0) return "Due Today";
    if (diffDays === 1) return "Due Tomorrow";
    if (diffDays < 7) return `Due in ${diffDays} days`;
    return startDate.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function getUrgencyLabelAllDay(event) {
    const now = new Date();
    const todayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const eventStartDay = new Date(event.startDate.getFullYear(), event.startDate.getMonth(), event.startDate.getDate());
    // Exclusive end: subtract 1 day
    const eventLastDay = new Date(event.endDate);
    eventLastDay.setDate(eventLastDay.getDate() - 1);
    const eventEndDayStart = new Date(eventLastDay.getFullYear(), eventLastDay.getMonth(), eventLastDay.getDate());

    if (eventEndDayStart < todayStart) return "Past";
    if (eventStartDay <= todayStart && eventEndDayStart >= todayStart) return "Today";

    const diffDays = Math.floor((eventStartDay - todayStart) / (1000 * 60 * 60 * 24));
    if (diffDays === 1) return "Tomorrow";
    if (diffDays < 7) return `In ${diffDays} days`;
    return event.startDate.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function getAccent(type, startDate) {
    const soon = (startDate - new Date()) / (1000 * 60 * 60 * 24) <= 1;
    if (soon) return { bar: "bg-error", tag: "bg-error-container/20 text-error" };
    if (type === "quiz") return { bar: "bg-tertiary", tag: "bg-tertiary-container/30 text-tertiary" };
    return { bar: "bg-primary", tag: "bg-primary/20 text-primary-fixed" };
}

function getEventBadgeClass(type, event) {
    if (event) {
        return "border-2";
    }
    if (type === "quiz") return "bg-tertiary-container/20 text-tertiary border-tertiary/30";
    if (type === "assignment") return "bg-primary/10 text-primary-fixed border-primary/30";
    return "bg-secondary-container/25 text-on-secondary-container border-secondary-container/30";
}

function getEventBadgeStyle(event) {
    const colors = getEventBadgeColors(event);
    return `background-color: ${colors.background}; color: ${colors.text}; border: 1px solid ${colors.border};`;
}

function getEventBadgeColors(event) {
    const color = getEventCalendarColor(event);
    const isDark = document.documentElement.classList.contains("dark");
    const textColor = adjustHexLuminance(color, isDark ? 0.34 : -0.30);
    const bgAlpha = isDark ? 0.24 : 0.13;
    const borderAlpha = isDark ? 0.48 : 0.34;
    return {
        background: hexToRgba(color, bgAlpha),
        text: textColor,
        border: hexToRgba(color, borderAlpha),
    };
}

function adjustHexLuminance(hex, amount) {
    const rgb = hexToRgb(hex);
    if (!rgb) return hex;
    const channel = (value) => {
        if (amount >= 0) {
            return Math.round(value + (255 - value) * amount);
        }
        return Math.round(value * (1 + amount));
    };
    return rgbToHex(channel(rgb.r), channel(rgb.g), channel(rgb.b));
}

function hexToRgb(hex) {
    const normalized = String(hex || "").trim().replace(/^#/, "");
    if (!/^[0-9a-fA-F]{6}$/.test(normalized)) return null;
    return {
        r: parseInt(normalized.slice(0, 2), 16),
        g: parseInt(normalized.slice(2, 4), 16),
        b: parseInt(normalized.slice(4, 6), 16),
    };
}

function rgbToHex(r, g, b) {
    const clamp = (n) => Math.max(0, Math.min(255, n));
    const toHex = (n) => clamp(n).toString(16).padStart(2, "0");
    return `#${toHex(r)}${toHex(g)}${toHex(b)}`;
}

function hexToRgba(hex, alpha) {
    const rgb = hexToRgb(hex);
    if (!rgb) return `rgba(99, 102, 241, ${alpha})`;
    return `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, ${alpha})`;
}

/* ── HTML Escaping ─────────────────────────────────────────────────────────── */
function escapeHtml(text) {
    return String(text).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#39;");
}