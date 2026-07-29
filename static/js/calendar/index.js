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
const CANVAS_SOURCE_ID = "canvas";
const LOCAL_SOURCE_PREFIX = "local:";
const DEFAULT_LOCAL_CALENDAR_ID = "local:default";
const DEFAULT_LOCAL_CALENDAR_NAME = "Personal";
const TASK_CALENDAR_ID = "local:tasks";
const TASK_CALENDAR_NAME = "Tasks";
const COURSES_SELECTION_STORAGE_KEY = "coursesSelectedSectionIds";
const COURSES_MODAL_ANIMATION_MS = 180;
const DEFAULT_DASHBOARD_VIEW = document.body?.dataset.defaultDashboardView === "month" ? "month" : "week";
const EVENTS_CACHE_KEY = "calendarEventsCache";
const DEFAULT_CALENDAR_BUFFER_DAYS = 7;
const CALENDAR_BUFFER_DAYS = Number.isFinite(
    Number.parseInt(document.body?.dataset.calendarBufferDays, 10)
)
    ? Number.parseInt(document.body?.dataset.calendarBufferDays, 10)
    : DEFAULT_CALENDAR_BUFFER_DAYS;
const CALENDAR_READ_ONLY = document.body?.dataset.calendarReadonly === "true";
const PUBLIC_SHARE_CODE = document.body?.dataset.publicShareCode || "";
const PUBLIC_CALENDAR_TITLE = document.body?.dataset.publicCalendarTitle || "Shared Calendar";
const PUBLIC_CALENDAR_RANGE_LABEL = document.body?.dataset.publicCalendarRangeLabel || "Shared dates";
const CALENDAR_SHARE_CLOSE_MS = 140;
const PREFERENCE_SAVE_DELAY_MS = 1000;
const PREFERENCE_SAVE_TIMEOUT_MS = 5000;
const PREFERENCE_SAVE_RETRY_DELAYS_MS = [1000, 3000];
const PREFERENCE_SAVE_WARNING_COOLDOWN_MS = 60000;
const PREFERENCE_BATCH_LIMIT = 50;
const PREFERENCE_BATCH_ENDPOINT = "/api/calendar/preferences/batch";
const PREFERENCE_LOAD_RETRY_COOLDOWN_MS = 15000;
const TOGGLE_REFRESH_DELAY_MS = 1000;
const COMPACT_CALENDAR_QUERY = window.matchMedia("(max-width: 640px)");
const {
    formatDateKey,
    formatMonthGridDayLabel,
    dateToDayIndex,
    layoutTimedEvents,
    formatHourLabel,
    formatTimeOnly,
    formatTimedEventRange,
    formatAllDayRange,
    getStartOfWeek,
    isToday,
    getUrgencyLabel,
    getUrgencyLabelAllDay,
    getAccent,
    isTaskEvent,
    getTaskPriorityColor,
    createAccessibleEventPalette,
    getCssColorVariable,
    escapeHtml,
    formatMultilineText,
} = window.APStudyCalendarUtils;
const state = window.APStudyCalendarState.createCalendarState({
    defaultDashboardView: DEFAULT_DASHBOARD_VIEW,
    publicCalendarRangeLabel: PUBLIC_CALENDAR_RANGE_LABEL,
    publicCalendarTitle: PUBLIC_CALENDAR_TITLE,
    publicShareCode: PUBLIC_SHARE_CODE,
    readOnly: CALENDAR_READ_ONLY,
});
const calendarCore = window.APStudyCalendarCore.createCalendarCore({
    state,
    constants: {
        defaultLocalCalendarId: DEFAULT_LOCAL_CALENDAR_ID,
        defaultLocalCalendarName: DEFAULT_LOCAL_CALENDAR_NAME,
        localSourcePrefix: LOCAL_SOURCE_PREFIX,
        simulatedCalendarName: SIMULATED_CALENDAR_NAME,
    },
    callbacks: {
        buildSimulatedMeetingEvents: (...args) => buildSimulatedMeetingEvents(...args),
        getCurrentViewCountRange: () => getCurrentViewCountRange(),
    },
});
const {
    getCalendarEventByRef,
    getCalendarEventRef,
    getCalendarLabel,
    getCalendarOptionsForEventForm,
    getDefaultCalendarIdForEventForm,
    getEventCalendarKey,
    getEventCalendarLabel,
    getSavedCalendarInfo,
    initCalendarState,
    isLocalCalendar,
} = calendarCore;
const calendarMenu = window.APStudyCalendarMenu.createCalendarMenu({
    state,
    constants: {
        canvasCalendarName: CANVAS_CALENDAR_NAME,
        canvasSourceId: CANVAS_SOURCE_ID,
        simulatedCalendarName: SIMULATED_CALENDAR_NAME,
        taskCalendarId: TASK_CALENDAR_ID,
        taskCalendarName: TASK_CALENDAR_NAME,
    },
    callbacks: {
        buildSimulatedMeetingEvents: (...args) => buildSimulatedMeetingEvents(...args),
        getCalendarLabel,
        getEventCalendarKey,
        getStartOfWeek,
    },
    escapeHtml,
});
const {
    getCalendarEventCount,
    getCalendarLabelFromData,
    getCurrentViewCountRange,
    renderCalendarMenu,
} = calendarMenu;
const calendarRenderShell = window.APStudyCalendarRenderShell.createCalendarRenderShell({
    state,
    constants: {
        compactCalendarQuery: COMPACT_CALENDAR_QUERY,
        hourHeightPx: HOUR_HEIGHT_PX,
    },
    callbacks: {
        buildMobileCalendarAgendaHtml: () => buildMobileCalendarAgendaHtml(),
        buildUpcomingAgendaHtml: () => buildUpcomingAgendaHtml(),
        buildMonthViewHtml: () => buildMonthViewHtml(),
        buildWeekViewHtml: () => buildWeekViewHtml(),
        getStartOfWeek,
        hideCalendarHoverCard: () => hideCalendarHoverCard(),
        renderAssignments: () => renderAssignments(),
        renderCalendarMenu,
        renderCoursesModal: () => renderCoursesModal(),
    },
});
const {
    isCompactCalendarViewport,
    render,
    renderCalendarView,
} = calendarRenderShell;
const calendarPreferences = window.APStudyCalendarPreferences.createCalendarPreferences({
    state,
    constants: {
        batchEndpoint: PREFERENCE_BATCH_ENDPOINT,
        batchLimit: PREFERENCE_BATCH_LIMIT,
        loadRetryCooldownMs: PREFERENCE_LOAD_RETRY_COOLDOWN_MS,
        preferenceSaveDelayMs: PREFERENCE_SAVE_DELAY_MS,
        preferenceSaveRetryDelaysMs: PREFERENCE_SAVE_RETRY_DELAYS_MS,
        preferenceSaveTimeoutMs: PREFERENCE_SAVE_TIMEOUT_MS,
        preferenceSaveWarningCooldownMs: PREFERENCE_SAVE_WARNING_COOLDOWN_MS,
    },
    getSavedCalendarInfo,
    renderCalendarMenu,
});
const {
    ensureCalendarPreferencesLoaded,
    invalidateCalendarPreferencesCache,
    loadCalendarState,
    queueCalendarPreferenceSave,
    saveCalendarState,
    scheduleCalendarPreferenceFlush,
    trackCalendarMutation,
    writeCalendarStateToStorage,
} = calendarPreferences;
const calendarCourses = window.APStudyCalendarCourses.createCalendarCourses({
    state,
    constants: {
        coursesSelectionStorageKey: COURSES_SELECTION_STORAGE_KEY,
        coursesModalAnimationMs: COURSES_MODAL_ANIMATION_MS,
        simulatedCalendarName: SIMULATED_CALENDAR_NAME,
    },
    render,
    saveCalendarState,
    escapeHtml,
});
const {
    initializeCourseSelectionsFromStorage,
    hydrateSelectedSimulatedSections,
    applyCoursesFiltersFromUrl,
    writeCourseFiltersToUrl,
    openCoursesModal,
    closeCoursesModal,
    submitCoursesSearch,
    applyCourseFilters,
    toggleCourseSectionSelection,
    renderCoursesModal,
    ensureSimulatedCalendarPreference,
    buildSimulatedMeetingEvents,
} = calendarCourses;
const calendarData = window.APStudyCalendarData.createCalendarData({
    state,
    constants: {
        calendarBufferDays: CALENDAR_BUFFER_DAYS,
        eventsCacheKey: EVENTS_CACHE_KEY,
        simulatedCalendarName: SIMULATED_CALENDAR_NAME,
        taskCalendarId: TASK_CALENDAR_ID,
        taskCalendarName: TASK_CALENDAR_NAME,
        toggleRefreshDelayMs: TOGGLE_REFRESH_DELAY_MS,
    },
    buildSimulatedMeetingEvents,
    ensureSimulatedCalendarPreference,
    getEventCalendarKey,
    getStartOfWeek,
    hydrateSelectedSimulatedSections,
    initCalendarState,
    loadCalendarState,
    queueCalendarPreferenceSave,
    render,
    writeCalendarStateToStorage,
});
const {
    getBufferedRange,
    getCurrentRenderRange,
    getEventCalendarColor,
    getVisibleEvents,
    loadCalendarData,
    refreshCalendarFeed,
    runManualRefresh,
    setCalendarColor,
    toggleCalendarVisibility,
    ensureEventsForRange,
} = calendarData;
const calendarEventRender = window.APStudyCalendarEventRender.createCalendarEventRender({
    state,
    callbacks: {
        getCalendarEventColor: getEventCalendarColor,
        getCalendarEventRef,
        getEventCalendarLabel,
        getVisibleEvents,
    },
    formatters: {
        createAccessibleEventPalette,
        escapeHtml,
        formatAllDayRange,
        formatTimedEventRange,
        getCssColorVariable,
        getTaskPriorityColor,
        isTaskEvent,
    },
});
const {
    getEventBadgeColors,
    getEventBadgeStyle,
    getEventElementAttributes,
    getEventsForDay,
} = calendarEventRender;
const calendarUiActions = window.APStudyCalendarUiActions.createCalendarUiActions({
    state,
    callbacks: {
        getCalendarEventColor: getEventCalendarColor,
        getCalendarEventCount,
        getCalendarLabel,
        getEventBadgeColors,
        getEventBadgeStyle,
        getEventElementAttributes,
        openCalendarInfoModal: (...args) => openCalendarInfoModal(...args),
        openRgbModal: (...args) => openRgbModal(...args),
        setCalendarColor,
    },
    formatters: {
        escapeHtml,
        isTaskEvent,
    },
});
const {
    buildEventChip,
    closeCalendarContextMenu,
    openCalendarContextMenu,
    positionCalendarContextMenu,
} = calendarUiActions;
const calendarAgenda = window.APStudyCalendarAgenda.createCalendarAgenda({
    state,
    callbacks: {
        getCalendarEventColor: getEventCalendarColor,
        getCalendarEventLabel: getEventCalendarLabel,
        getCalendarEventRef,
        getEventBadgeColors,
        getEventElementAttributes,
        getEventsForDay,
        getVisibleEvents,
    },
    formatters: {
        escapeHtml,
        formatAllDayRange,
        formatMultilineText,
        formatTimedEventRange,
        getAccent,
        getStartOfWeek,
        getUrgencyLabel,
        getUrgencyLabelAllDay,
        isTaskEvent,
        isToday,
    },
});
const {
    buildMobileCalendarAgendaHtml,
    buildUpcomingAgendaHtml,
    renderAssignments,
} = calendarAgenda;
const calendarMonthView = window.APStudyCalendarMonthView.createCalendarMonthView({
    state,
    constants: {
        weekdays: WEEKDAYS,
    },
    callbacks: {
        buildEventChip,
        getEventBadgeColors,
        getEventBadgeStyle,
        getEventElementAttributes,
        getEventsForDay,
        getVisibleEvents,
    },
    formatters: {
        dateToDayIndex,
        escapeHtml,
        formatDateKey,
        formatMonthGridDayLabel,
        isToday,
    },
});
const {
    buildMonthViewHtml,
} = calendarMonthView;
const calendarWeekView = window.APStudyCalendarWeekView.createCalendarWeekView({
    state,
    constants: {
        allDayMinHeightPx: ALL_DAY_MIN_HEIGHT_PX,
        hourHeightPx: HOUR_HEIGHT_PX,
        weekMinimumDayWidthPx: WEEK_MINIMUM_DAY_WIDTH_PX,
        weekdays: WEEKDAYS,
    },
    callbacks: {
        getEventBadgeStyle,
        getEventElementAttributes,
        getEventsForDay,
        getVisibleEvents,
    },
    formatters: {
        dateToDayIndex,
        escapeHtml,
        formatHourLabel,
        formatTimeOnly,
        getStartOfWeek,
        isTaskEvent,
        isToday,
        layoutTimedEvents,
    },
});
const {
    buildWeekViewHtml,
} = calendarWeekView;
const calendarSources = window.APStudyCalendarSources.createCalendarSources({
    state,
    constants: {
        eventsCacheKey: EVENTS_CACHE_KEY,
    },
    closeCalendarContextMenu,
    escapeHtml,
    getBufferedRange,
    getCalendarEventCount,
    getCalendarLabel,
    getCurrentRenderRange,
    invalidateCalendarPreferencesCache,
    isLocalCalendar,
    loadCalendarData,
    queueCalendarPreferenceSave,
    renderCalendarMenu,
    runManualRefresh,
    setCalendarColor,
    trackCalendarMutation,
});
const {
    closeCalendarSourceCreateModal,
    closeRgbModal,
    closeSourceInfoModal,
    openCalendarInfoModal,
    openCalendarSourceCreateModal,
    openRgbModal,
} = calendarSources;
const calendarShare = window.APStudyCalendarShare.createCalendarShare({
    state,
    constants: {
        calendarShareCloseMs: CALENDAR_SHARE_CLOSE_MS,
        simulatedCalendarName: SIMULATED_CALENDAR_NAME,
    },
    escapeHtml,
    getCalendarLabel,
    getCalendarLabelFromData,
    trackCalendarMutation,
});
const {
    closeCalendarShareModal,
    openCalendarShareModal,
} = calendarShare;
const calendarControls = window.APStudyCalendarControls.createCalendarControls({
    state,
    callbacks: {
        applyCourseFilters,
        applyCoursesFiltersFromUrl,
        closeCalendarContextMenu,
        closeCalendarShareModal,
        closeCalendarSourceCreateModal,
        closeCoursesModal,
        closeRgbModal,
        closeSourceInfoModal,
        ensureCalendarPreferencesLoaded,
        ensureEventsForRange,
        getBufferedRange,
        getCalendarEventByRef,
        getCurrentRenderRange,
        getEventCalendarLabel,
        isCompactCalendarViewport,
        openCalendarContextMenu,
        openCalendarShareModal,
        openCalendarSourceCreateModal,
        openCoursesModal,
        positionCalendarContextMenu,
        render,
        renderAssignments,
        renderCalendarMenu,
        renderCalendarView,
        renderCoursesModal,
        runManualRefresh,
        scheduleCalendarPreferenceFlush,
        submitCoursesSearch,
        toggleCalendarVisibility,
        toggleCourseSectionSelection,
        writeCourseFiltersToUrl,
    },
    formatters: {
        escapeHtml,
        formatAllDayRange,
        formatMultilineText,
        formatTimedEventRange,
    },
});
const {
    hideCalendarHoverCard,
    wireControls,
} = calendarControls;
window.APStudyCalendarBootstrap.createCalendarBootstrap({
    state,
    callbacks: {
        applyCoursesFiltersFromUrl,
        ensureEventsForRange,
        getBufferedRange,
        getCalendarEventByRef,
        getCalendarOptionsForEventForm,
        getCurrentRenderRange,
        getDefaultCalendarIdForEventForm,
        initializeCourseSelectionsFromStorage,
        loadCalendarData,
        refreshCalendarFeed,
        render,
        runManualRefresh,
        wireControls,
    },
}).register();
/* ── Controls ──────────────────────────────────────────────────────────────── */
