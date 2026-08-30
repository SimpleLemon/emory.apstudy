const COURSE_DAYS = [
  { key: "Mon", index: 1 },
  { key: "Tue", index: 2 },
  { key: "Wed", index: 3 },
  { key: "Thu", index: 4 },
  { key: "Fri", index: 5 },
];
const COURSE_START_HOUR = 6;
const COURSE_END_HOUR = 24;
const COURSE_START_MINUTES = COURSE_START_HOUR * 60;
const COURSE_END_MINUTES = COURSE_END_HOUR * 60;
const COURSE_HOUR_HEIGHT = 64;
const COURSE_RESULT_LIMIT = 100;
const COURSE_LIVE_HYDRATION_OVERSCAN = 5;
const COMPACT_COURSES_QUERY = window.matchMedia("(max-width: 640px)");
const BODY_SCROLLING_COURSES_QUERY = window.matchMedia("(max-width: 1024px)");
const COURSE_COLOR_PALETTE = Array.from({ length: 16 }, (_, index) => ({
  key: `course-color-${String(index + 1).padStart(2, "0")}`,
}));
const {
  buildSectionSearchBlob,
  cssEscape,
  parseAtlasTimeToken,
  parseCoursesSectionDeepLink,
} = window.APStudyCoursesUtils;
const { collectMeetingOverrides, meetingRemovalFocusPlan } = window.APStudyCoursesEdit;
const availabilityVerifier = window.APStudyCoursesVerify.create({
  onStatusProgress: renderCourses,
});

const state = {
  loading: true,
  sectionsLoading: false,
  savingIds: new Set(),
  trackingIds: new Set(),
  terms: [],
  selectedTerm: window.APSTUDY_COURSES_DEFAULT_TERM || "",
  sections: [],
  sectionsById: {},
  currentSectionsRequest: 0,
  savedCoursesBySection: new Map(),
  tracksBySection: new Map(),
  allowedTrackIntervals: [30],
  trackingTier: { key: "free", label: "Free" },
  trackingUsage: 0,
  trackingLimit: null,
  removedSelectedSections: new Map(),
  activeCourseView: "search",
  searchQuery: "",
  dayFilters: new Set(),
  campusFilter: window.APSTUDY_COURSES_DEFAULT_CAMPUS || "atlanta",
  requirementFilter: "all",
  statusFilters: new Set(),
  filtersOpen: false,
  timeEnabled: false,
  timeStart: "06:00",
  timeEnd: "23:59",
  hoveredSectionId: null,
  detailSectionId: null,
  detailReturnContext: null,
  editingSectionId: null,
  editingSaving: false,
  detailLoading: false,
  detailLiveError: "",
  liveHydrationTimer: null,
  error: "",
  weekScrollTop: null,
  weekScrollLeft: null,
  weekScrollResetPending: false,
  initialScrollDone: false,
};

const courseFilters = window.APStudyCoursesFilters.create({
  state,
  COURSE_START_MINUTES,
  COURSE_END_MINUTES,
  getSection,
  rememberSection,
  getEffectiveAvailability: (section) => availabilityVerifier.getEffectiveAvailability(section),
  utils: window.APStudyCoursesUtils,
});
const { getFilteredSections, isAvailabilityVerificationPending } = courseFilters;
const coursePanel = window.APStudyCoursesPanel.create({
  state,
  COURSE_COLOR_PALETTE,
  COURSE_DAYS,
  COURSE_RESULT_LIMIT,
  getFilteredSections,
  getSection,
  isTrackable,
  getEffectiveAvailability,
  utils: window.APStudyCoursesUtils,
});
const {
  getCourseColor,
  buildMeetingRowHtml,
  renderPanel,
  renderTermSelect,
  syncFilterControls,
  timeInputToAtlasToken,
} = coursePanel;
const courseCalendar = window.APStudyCoursesCalendar.create({
  state,
  COURSE_DAYS,
  COURSE_START_HOUR,
  COURSE_END_HOUR,
  COURSE_START_MINUTES,
  COURSE_END_MINUTES,
  COURSE_HOUR_HEIGHT,
  COMPACT_COURSES_QUERY,
  getCourseColor,
  getSection,
  utils: window.APStudyCoursesUtils,
});
const {
  isCompactCoursesViewport,
  renderCalendar,
  resetWeekScroll,
} = courseCalendar;
const { wireControls } = window.APStudyCoursesControls.create({
  state,
  addCourse,
  buildMeetingRowHtml,
  changeTermBy,
  clearDetailReturnContext,
  closeDetail,
  isCompactCoursesViewport,
  loadSectionsForTerm,
  openDetail,
  removeCourse,
  removeTrack,
  renderCalendar,
  renderCourses,
  renderPanel,
  resetWeekScroll,
  refreshSectionStatus,
  saveEditedCourse,
  setTrack,
  startEditingCourse,
  syncFilterControls,
  verifyCurrentAvailability,
  meetingRemovalFocusPlan,
});

document.addEventListener("DOMContentLoaded", () => {
  wireControls();
  wireLiveHydrationControls();
  void bootstrap();
});

async function bootstrap() {
  try {
    await Promise.all([loadTerms(), loadSavedCourses(), loadTracks()]);
    if (state.selectedTerm) {
      await loadSectionsForTerm(state.selectedTerm);
    }
    await applyCoursesDeepLink();
  } catch (error) {
    console.error(error);
    state.error = error.message || "Unable to load courses.";
  } finally {
    state.loading = false;
    render();
  }
}

async function applyCoursesDeepLink() {
  const sectionId = parseCoursesSectionDeepLink(window.location);
  if (!sectionId) return;

  try {
    const payload = await fetchJson("/api/atlas/sections/by-id", {
      method: "POST",
      body: JSON.stringify({
        section_ids: [sectionId],
        include_cancelled: true,
      }),
    });
    const sections = Array.isArray(payload.sections) ? payload.sections : [];
    const section = sections.find((row) => String(row.id || "") === sectionId) || sections[0];
    if (!section) {
      showToast("Course section not found.", true);
      return;
    }

    rememberSection(section);
    const sectionTerm = section.term || state.selectedTerm;
    if (sectionTerm && state.terms.includes(sectionTerm)) {
      state.selectedTerm = sectionTerm;
      await loadSectionsForTerm(sectionTerm);
      rememberSection(section);
    }
    openDetail(String(section.id || sectionId));
    window.history.replaceState({}, "", window.location.pathname);
  } catch (error) {
    console.error(error);
    showToast(error.message || "Course section not found.", true);
  }
}

async function fetchJson(url, options = {}) {
  if (window.APStudyHttp?.fetchJson) {
    return window.APStudyHttp.fetchJson(url, options);
  }
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(payload.error || "Request failed.");
    if (payload && typeof payload === "object") {
      ["code", "resource", "limit", "current", "requested"].forEach((key) => {
        if (payload[key] != null) error[key] = payload[key];
      });
    }
    throw error;
  }
  return payload;
}

async function loadTerms() {
  const payload = await fetchJson("/api/atlas/terms");
  state.terms = Array.isArray(payload.terms) ? payload.terms : [];
  if (!state.terms.length) {
    throw new Error("No Emory Atlas terms are available.");
  }
  if (!state.selectedTerm || !state.terms.includes(state.selectedTerm)) {
    state.selectedTerm = payload.default_term && state.terms.includes(payload.default_term)
      ? payload.default_term
      : state.terms[0];
  }
}

async function loadSectionsForTerm(term) {
  if (!term) return;
  const requestId = state.currentSectionsRequest + 1;
  state.currentSectionsRequest = requestId;
  state.sectionsLoading = true;
  state.error = "";
  render();
  try {
    const params = new URLSearchParams({
      term,
      include_cancelled: "0",
    });
    const query = state.searchQuery.trim();
    if (query) {
      params.set("q", query);
      params.set("limit", "500");
    }
    if (state.dayFilters.size) {
      params.set("days", Array.from(state.dayFilters).join(","));
    }
    if (state.timeEnabled) {
      params.set("time_start", timeInputToAtlasToken(state.timeStart) || "0600");
      params.set("time_end", timeInputToAtlasToken(state.timeEnd) || "2359");
    }
    if (state.campusFilter && state.campusFilter !== "all") {
      params.set("campus", state.campusFilter);
    }
    if (state.requirementFilter && state.requirementFilter !== "all") {
      params.set("requirement", state.requirementFilter);
    }
    const payload = await fetchJson(`/api/atlas/sections?${params.toString()}`);
    if (requestId !== state.currentSectionsRequest) return;
    state.sectionsById = Object.fromEntries(
      Object.entries(state.sectionsById).filter(([id]) => (
        state.savedCoursesBySection.has(id) || state.tracksBySection.has(id)
      ))
    );
    const rawSections = Array.isArray(payload.sections) ? payload.sections : [];
    state.sections = rawSections.map((section) => rememberSection(section)).filter(Boolean);
    void verifyCurrentAvailability();
  } catch (error) {
    if (requestId !== state.currentSectionsRequest) return;
    console.error(error);
    state.error = error.message || "Unable to load course sections.";
  } finally {
    if (requestId !== state.currentSectionsRequest) return;
    state.sectionsLoading = false;
    render();
  }
}

function buildAvailabilityQueryInput() {
  return {
    term: state.selectedTerm,
    query: state.searchQuery.trim(),
    days: Array.from(state.dayFilters).sort(),
    timeEnabled: state.timeEnabled,
    timeStart: state.timeStart,
    timeEnd: state.timeEnd,
    campus: state.campusFilter,
    requirement: state.requirementFilter,
  };
}

function getAvailabilityCandidates() {
  const candidates = getFilteredSections({ ignoreStatus: true });
  const sectionIds = candidates
    .map((section) => String(section?.id || section?.section_id || ""))
    .filter(Boolean);
  return { candidates, sectionIds };
}

function renderCourses() {
  if (state.loading || state.error || state.editingSectionId || state.detailSectionId || state.sectionsLoading) {
    renderPanel();
    return;
  }
  const candidates = getFilteredSections({ ignoreStatus: true });
  if (state.statusFilters.size && isAvailabilityVerificationPending(candidates)) {
    renderAvailabilityPendingState();
    return;
  }
  renderPanel();
}

function renderAvailabilityPendingState() {
  const summary = document.getElementById("courses-result-summary");
  const content = document.getElementById("courses-panel-content");
  if (!content) return;
  if (summary) summary.textContent = "Verifying live availability…";
  content.innerHTML = `<div class="courses-state" role="status" aria-live="polite">Verifying live availability…</div>`;
}

async function verifyCurrentAvailability() {
  let settledState = null;
  try {
    const { sectionIds } = getAvailabilityCandidates();
    const queryPromise = availabilityVerifier.startQuery({
      queryInput: buildAvailabilityQueryInput(),
      sectionIds,
    });
    renderCourses();
    settledState = await queryPromise;
  } catch (error) {
    console.error(error);
    return;
  }
  const currentState = availabilityVerifier.getState();
  if (currentState.generation !== settledState.generation
    || currentState.querySignature !== settledState.querySignature) {
    return;
  }
  renderCourses();
}

async function loadSavedCourses() {
  const payload = await fetchJson("/api/courses/saved");
  state.savedCoursesBySection = new Map();
  state.removedSelectedSections.clear();
  for (const course of payload.courses || []) {
    applySavedCourse(course);
  }
}

function applySavedCourse(course) {
  if (!course?.section_id) return;
  const sectionId = String(course.section_id);
  state.savedCoursesBySection.set(sectionId, course);
  rememberSection(course);
}

async function loadTracks() {
  const payload = await fetchJson("/api/courses/tracks");
  state.allowedTrackIntervals = payload.allowed_intervals_minutes || [30];
  state.trackingTier = payload.tier || { key: "free", label: "Free" };
  state.trackingUsage = Number(payload.usage || 0);
  state.trackingLimit = payload.limit ?? null;
  state.tracksBySection = new Map();
  for (const track of payload.tracks || []) {
    if (track.section_id) {
      state.tracksBySection.set(String(track.section_id), track);
    }
  }
}

function rememberSection(section) {
  const id = String(section?.section_id || section?.id || "");
  if (!id) return null;
  const normalized = { ...state.sectionsById[id], ...section, id };
  if (state.savedCoursesBySection.has(id)) {
    Object.assign(normalized, getDisplayCourse(id));
  }
  normalized.searchBlob = buildSectionSearchBlob(normalized);
  state.sectionsById[id] = normalized;
  return normalized;
}

function getDisplayCourse(sectionId) {
  const savedCourse = state.savedCoursesBySection.get(String(sectionId));
  if (!savedCourse) return {};
  const display = {};
  [
    "term",
    "subject",
    "catalog",
    "crn",
    "course_code",
    "course_title",
    "course_name",
    "section_number",
    "instructor",
    "instructor_name",
    "instructors",
    "schedule_type",
    "schedule_display",
    "meetings",
    "date_range",
    "location",
    "credit_hours",
    "requirement_designation",
    "requirements",
    "campus",
    "campus_description",
    "course_description",
    "description",
    "grading_mode",
    "grading_mode_options",
    "instruction_method",
    "atlas_key",
    "color_key",
    "overrides",
    "updated_at",
  ].forEach((key) => {
    if (typeof savedCourse[key] !== "undefined" && savedCourse[key] !== null) {
      display[key] = savedCourse[key];
    }
  });
  display.section_id = savedCourse.section_id || sectionId;
  return display;
}

function render() {
  renderTermSelect();
  renderCourses();
  renderCalendar();
  scheduleVisibleLiveHydration();
}

function changeTermBy(delta) {
  const currentIndex = state.terms.indexOf(state.selectedTerm);
  const nextIndex = currentIndex + delta;
  if (nextIndex < 0 || nextIndex >= state.terms.length) return;
  state.selectedTerm = state.terms[nextIndex];
  clearDetailReturnContext();
  state.detailSectionId = null;
  state.editingSectionId = null;
  state.filtersOpen = false;
  state.removedSelectedSections.clear();
  resetWeekScroll();
  renderTermSelect();
  void loadSectionsForTerm(state.selectedTerm);
}

function startEditingCourse(sectionId) {
  if (!sectionId || !state.savedCoursesBySection.has(String(sectionId))) return;
  state.detailSectionId = sectionId;
  state.editingSectionId = sectionId;
  state.filtersOpen = false;
  renderPanel();
  scrollPanelContentToTop();
}

async function saveEditedCourse(sectionId) {
  const savedCourse = state.savedCoursesBySection.get(String(sectionId));
  if (!savedCourse?.id || state.editingSaving) return;
  const form = document.querySelector(`.courses-edit[data-editing-section-id="${cssEscape(sectionId)}"]`);
  if (!form) return;

  const selectedColor = form.querySelector("[data-course-color-key].is-selected")?.dataset.courseColorKey
    || savedCourse.color_key
    || COURSE_COLOR_PALETTE[0].key;
  const overrides = collectEditOverrides(form);

  state.editingSaving = true;
  state.savingIds.add(sectionId);
  renderPanel();
  try {
    const payload = await fetchJson(`/api/courses/saved/${encodeURIComponent(savedCourse.id)}`, {
      method: "PATCH",
      body: JSON.stringify({ color_key: selectedColor, overrides }),
    });
    if (payload.course?.section_id) {
      applySavedCourse(payload.course);
      state.detailSectionId = String(payload.course.section_id);
      state.editingSectionId = null;
    }
    showToast("Class updated.");
  } catch (error) {
    console.error(error);
    showToast(error.message || "Try again in a moment.", true, { title: "Couldn’t update class" });
  } finally {
    state.editingSaving = false;
    state.savingIds.delete(sectionId);
    render();
  }
}

function collectEditOverrides(form) {
  const valueFor = (name) => form.querySelector(`[name="${name}"]`)?.value?.trim() || "";
  const overrides = {
    course_code: valueFor("course_code"),
    course_title: valueFor("course_title"),
    section_number: valueFor("section_number"),
    instructor: valueFor("instructor"),
    schedule_type: valueFor("schedule_type"),
    schedule_display: valueFor("schedule_display"),
    location: valueFor("location"),
    credit_hours: valueFor("credit_hours"),
    requirement_designation: valueFor("requirement_designation"),
    campus: valueFor("campus"),
    course_description: valueFor("course_description"),
    course_notes: valueFor("course_notes"),
    meetings: [],
  };

  overrides.meetings = collectMeetingOverrides(
    Array.from(form.querySelectorAll(".courses-meeting-row")).map((row) => ({
      day: row.querySelector("[data-meeting-day]")?.value,
      start: row.querySelector("[data-meeting-start]")?.value,
      end: row.querySelector("[data-meeting-end]")?.value,
    })),
    { COURSE_DAYS, parseAtlasTimeToken, timeInputToAtlasToken },
  );

  return overrides;
}

function openDetail(sectionId, opener = null) {
  if (!sectionId) return;
  captureDetailReturnContext(sectionId, opener);
  state.detailSectionId = sectionId;
  state.editingSectionId = null;
  state.detailLiveError = "";
  state.filtersOpen = false;
  renderPanel();
  scrollPanelContentToTop();
  void refreshSectionStatus(sectionId, { force: false });
}

function captureDetailReturnContext(sectionId, opener) {
  const normalizedSectionId = String(sectionId);
  if (state.detailReturnContext?.sectionId === normalizedSectionId) return;
  clearDetailReturnContext();
  const content = document.getElementById("courses-panel-content");
  const hasListOrigin = opener instanceof HTMLElement
    && content?.contains(opener)
    && opener.matches(".course-card[data-section-id]")
    && String(opener.dataset.sectionId) === normalizedSectionId;
  if (!hasListOrigin) return;
  const focusedElement = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  state.detailReturnContext = {
    sectionId: normalizedSectionId,
    panelScrollTop: content?.scrollTop || 0,
    documentScroll: BODY_SCROLLING_COURSES_QUERY.matches
      ? { left: window.scrollX || 0, top: window.scrollY || 0 }
      : null,
    opener: opener instanceof HTMLElement ? opener : focusedElement,
  };
}

function clearDetailReturnContext() {
  state.detailReturnContext = null;
}

function closeDetail() {
  const closingSectionId = String(state.detailSectionId || state.editingSectionId || "");
  const returnContext = state.detailReturnContext?.sectionId === closingSectionId
    ? state.detailReturnContext
    : null;
  state.detailSectionId = null;
  state.editingSectionId = null;
  state.detailLiveError = "";
  clearDetailReturnContext();
  renderPanel();
  if (returnContext) restoreDetailReturnContext(returnContext);
}

function restoreDetailReturnContext(returnContext) {
  const restoreScroll = () => {
    const content = document.getElementById("courses-panel-content");
    if (content) content.scrollTop = returnContext.panelScrollTop;
    if (returnContext.documentScroll && BODY_SCROLLING_COURSES_QUERY.matches) {
      window.scrollTo({
        left: returnContext.documentScroll.left,
        top: returnContext.documentScroll.top,
        behavior: "auto",
      });
    }
  };

  restoreScroll();
  window.requestAnimationFrame?.(restoreScroll);

  const fallback = document.getElementById("courses-search-input") || document.getElementById("courses-result-summary");
  const focusTarget = focusCourseCard(returnContext.sectionId) || getConnectedFocusTarget(returnContext.opener) || fallback;
  if (focusTarget === document.getElementById("courses-result-summary")) {
    focusTarget.tabIndex = -1;
  }
  if (focusTarget !== document.activeElement) focusTarget?.focus?.({ preventScroll: true });
}

function focusCourseCard(sectionId) {
  const content = document.getElementById("courses-panel-content");
  const card = content?.querySelector(`.course-card[data-section-id="${cssEscape(sectionId)}"]`);
  card?.focus?.({ preventScroll: true });
  return card || null;
}

function getConnectedFocusTarget(element) {
  if (!(element instanceof HTMLElement) || !element.isConnected || element.matches(":disabled")) return null;
  if (element.tabIndex >= 0 || element.matches("a[href], button, input, select, textarea, [contenteditable='true']")) {
    return element;
  }
  return null;
}

async function refreshSectionStatus(sectionId, options = {}) {
  state.detailLoading = true;
  renderPanel();
  try {
    const payload = await fetchJson("/api/courses/section-status", {
      method: "POST",
      body: JSON.stringify({
        section_id: sectionId,
        force: options.force !== false,
      }),
    });
    if (payload.section) {
      payload.section.live_updated_at = payload.last_updated_at || new Date().toISOString();
      rememberSection(payload.section);
    }
    state.detailLiveError = payload.live_error || "";
    if (payload.live_error) {
      showToast(payload.live_error || "Live Atlas status unavailable.", true);
    }
  } catch (error) {
    console.error(error);
    state.detailLiveError = error.message || "Live status unavailable.";
    showToast(state.detailLiveError, true);
  } finally {
    state.detailLoading = false;
    const focusedCardId = state.detailSectionId
      ? null
      : document.activeElement?.closest?.(".course-card[data-section-id]")?.dataset.sectionId;
    renderPanel();
    if (focusedCardId) focusCourseCard(focusedCardId);
    renderCalendar();
  }
}

async function addCourse(sectionId) {
  if (!sectionId || state.savedCoursesBySection.has(sectionId)) return;
  state.savingIds.add(sectionId);
  render();
  try {
    const payload = await fetchJson("/api/courses/saved", {
      method: "POST",
      body: JSON.stringify({ section_id: sectionId }),
    });
    if (payload.course?.section_id) {
      applySavedCourse(payload.course);
      state.removedSelectedSections.delete(String(payload.course.section_id));
    }
    showToast("Class added.");
  } catch (error) {
    console.error(error);
    showToast(error.message || "Try again in a moment.", true, { title: "Couldn’t add class" });
  } finally {
    state.savingIds.delete(sectionId);
    render();
  }
}

async function removeCourse(courseId, sectionId) {
  if (!courseId) return;
  const accepted = await (window.APStudyConfirm?.request?.({
    title: "Remove class?",
    message: "This class will be removed from your weekly view.",
    acceptLabel: "Remove class",
    danger: true,
  }) ?? Promise.resolve(false));
  if (!accepted) return;
  if (sectionId) state.savingIds.add(sectionId);
  const savedCourse = sectionId ? state.savedCoursesBySection.get(String(sectionId)) : null;
  const removedSection = sectionId ? getSection(sectionId) : null;
  const previousDetailSectionId = state.detailSectionId;
  const previousEditingSectionId = state.editingSectionId;
  const restoresRemovedDetail = state.detailSectionId === sectionId || state.editingSectionId === sectionId;
  const previousDetailReturnContext = restoresRemovedDetail && state.detailReturnContext?.sectionId === String(sectionId)
    ? state.detailReturnContext
    : null;
  if (sectionId) state.savedCoursesBySection.delete(String(sectionId));
  if (sectionId && state.activeCourseView === "selected" && removedSection) {
    state.removedSelectedSections.set(String(sectionId), { ...removedSection, id: String(sectionId) });
  }
  if (restoresRemovedDetail) clearDetailReturnContext();
  if (state.detailSectionId === sectionId) state.detailSectionId = null;
  if (state.editingSectionId === sectionId) state.editingSectionId = null;
  render();
  window.APStudyUndo?.stage?.({
    message: `${removedSection?.course_code || removedSection?.course_title || "Class"} removed.`,
    commit: ({ reason }) => fetchJson(`/api/courses/saved/${encodeURIComponent(courseId)}`, {
      method: "DELETE",
      keepalive: reason === "pagehide",
    }),
    restore: () => {
      if (sectionId && savedCourse) state.savedCoursesBySection.set(String(sectionId), savedCourse);
      if (sectionId) state.removedSelectedSections.delete(String(sectionId));
      state.detailSectionId = previousDetailSectionId;
      state.editingSectionId = previousEditingSectionId;
      state.detailReturnContext = restoresRemovedDetail && previousDetailReturnContext?.sectionId === String(sectionId)
        ? previousDetailReturnContext
        : null;
      if (sectionId) state.savingIds.delete(sectionId);
      render();
    },
    onCommit: () => {
      if (sectionId) state.savingIds.delete(sectionId);
      render();
    },
    errorTitle: "Couldn’t remove class",
  });
  if (!window.APStudyUndo?.stage) {
    try {
      await fetchJson(`/api/courses/saved/${encodeURIComponent(courseId)}`, { method: "DELETE" });
    } catch (error) {
      if (sectionId && savedCourse) state.savedCoursesBySection.set(String(sectionId), savedCourse);
      showToast(error.message || "Try again in a moment.", true, { title: "Couldn’t remove class" });
    } finally {
      if (sectionId) state.savingIds.delete(sectionId);
      render();
    }
  }
}

async function setTrack(sectionId, enabled, intervalMinutes = null) {
  if (!sectionId) return;
  const wasEnabled = Boolean(state.tracksBySection.get(String(sectionId))?.enabled);
  state.trackingIds.add(sectionId);
  renderPanel();
  try {
    const payload = await fetchJson("/api/courses/tracks", {
      method: "POST",
      body: JSON.stringify({
        section_id: sectionId,
        enabled,
        ...(intervalMinutes ? { interval_minutes: Number(intervalMinutes) } : {}),
      }),
    });
    if (payload.section) rememberSection(payload.section);
    if (payload.track?.section_id) {
      state.tracksBySection.set(String(payload.track.section_id), payload.track);
    }
    if (enabled !== wasEnabled) state.trackingUsage = Math.max(0, state.trackingUsage + (enabled ? 1 : -1));
    showToast(intervalMinutes ? `Checking every ${Number(intervalMinutes)} minutes.` : enabled ? "Tracking enabled." : "Tracking paused.");
    if (enabled && !wasEnabled) window.dispatchEvent(new CustomEvent('apstudy:notification-intent', { detail: { source: 'course-tracking' } }));
  } catch (error) {
    console.error(error);
    const limitReached = error?.code === "tier_limit";
    showToast(
      error.message || "Try again in a moment.",
      true,
      { title: limitReached ? "Tracking limit reached" : "Couldn’t update tracking" },
    );
  } finally {
    state.trackingIds.delete(sectionId);
    render();
  }
}

async function removeTrack(trackId, sectionId) {
  if (!trackId || !sectionId) return;
  const track = state.tracksBySection.get(String(sectionId));
  const wasEnabled = Boolean(track?.enabled);
  const previousUsage = state.trackingUsage;
  state.trackingIds.add(sectionId);
  state.tracksBySection.delete(String(sectionId));
  if (wasEnabled) state.trackingUsage = Math.max(0, state.trackingUsage - 1);
  renderPanel();
  window.APStudyUndo?.stage?.({
    message: "Course tracker removed.",
    commit: ({ reason }) => fetchJson(`/api/courses/tracks/${encodeURIComponent(trackId)}`, {
      method: "DELETE",
      keepalive: reason === "pagehide",
    }),
    restore: () => {
      if (track) state.tracksBySection.set(String(sectionId), track);
      state.trackingUsage = previousUsage;
      state.trackingIds.delete(sectionId);
      render();
    },
    onCommit: () => {
      state.trackingIds.delete(sectionId);
      render();
    },
    errorTitle: "Couldn’t remove tracker",
  });
  if (!window.APStudyUndo?.stage) {
    try {
      await fetchJson(`/api/courses/tracks/${encodeURIComponent(trackId)}`, { method: "DELETE" });
    } catch (error) {
      if (track) state.tracksBySection.set(String(sectionId), track);
      state.trackingUsage = previousUsage;
      showToast(error.message || "Try again in a moment.", true, { title: "Couldn’t remove tracker" });
    } finally {
      state.trackingIds.delete(sectionId);
      render();
    }
  }
}

function getSection(sectionId) {
  return state.sectionsById[String(sectionId)] || state.savedCoursesBySection.get(String(sectionId));
}

function isTrackable(section) {
  if (section?.is_cancelled) return false;
  const availability = availabilityVerifier.getEffectiveAvailability(section);
  if (!availability || availability.phase !== "verified" || availability.current !== true) return false;
  if (String(availability.status || "").toLowerCase() === "closed") return true;
  return availability.seatsAvailable === 0;
}

function getEffectiveAvailability(section) {
  return availabilityVerifier.getEffectiveAvailability(section);
}

function scrollPanelContentToTop() {
  const content = document.getElementById("courses-panel-content");
  if (!content) return;
  content.scrollTop = 0;
  window.requestAnimationFrame?.(() => {
    content.scrollTop = 0;
  });
}

function wireLiveHydrationControls() {
  const content = document.getElementById("courses-panel-content");
  content?.addEventListener("scroll", scheduleVisibleLiveHydration, { passive: true });
  window.addEventListener("resize", scheduleVisibleLiveHydration);
}

function scheduleVisibleLiveHydration() {
  window.clearTimeout(state.liveHydrationTimer);
  state.liveHydrationTimer = window.setTimeout(hydrateVisibleLiveSections, 140);
}

function visibleHydrationSectionIds() {
  if (state.loading || state.sectionsLoading || state.detailSectionId || state.editingSectionId) return [];
  const content = document.getElementById("courses-panel-content");
  if (!content) return [];
  const cards = Array.from(content.querySelectorAll(".course-card[data-section-id]"));
  if (!cards.length) return [];

  const contentRect = content.getBoundingClientRect();
  const selected = [];
  let lastVisibleIndex = -1;
  cards.forEach((card, index) => {
    const rect = card.getBoundingClientRect();
    const visible = rect.bottom >= contentRect.top && rect.top <= contentRect.bottom;
    if (!visible) return;
    selected.push(card.dataset.sectionId);
    lastVisibleIndex = Math.max(lastVisibleIndex, index);
  });

  if (lastVisibleIndex < 0) {
    lastVisibleIndex = Math.min(cards.length - 1, COURSE_LIVE_HYDRATION_OVERSCAN - 1);
    for (let index = 0; index <= lastVisibleIndex; index += 1) {
      selected.push(cards[index].dataset.sectionId);
    }
  }

  for (
    let index = lastVisibleIndex + 1;
    index < cards.length && index <= lastVisibleIndex + COURSE_LIVE_HYDRATION_OVERSCAN;
    index += 1
  ) {
    selected.push(cards[index].dataset.sectionId);
  }

  return Array.from(new Set(selected));
}

async function hydrateVisibleLiveSections() {
  const sectionIds = visibleHydrationSectionIds();
  if (!sectionIds.length) return;
  const beforeState = availabilityVerifier.getState();
  let settledState = null;
  try {
    settledState = await availabilityVerifier.requestDetails(sectionIds);
  } catch (error) {
    console.error(error);
    return;
  }
  const currentState = availabilityVerifier.getState();
  if (!settledState
    || beforeState.generation !== currentState.generation
    || currentState.generation !== settledState.generation) {
    return;
  }
  const changed = currentState.detailedIds.size !== beforeState.detailedIds.size
    || currentState.detailErrors.size !== beforeState.detailErrors.size
    || currentState.errors.size !== beforeState.errors.size;
  if (!changed) return;
  rerenderAfterLiveHydration();
}

function rerenderAfterLiveHydration() {
  const before = document.getElementById("courses-panel-content")?.scrollTop || 0;
  renderTermSelect();
  renderCourses();
  renderCalendar();
  const content = document.getElementById("courses-panel-content");
  if (content) content.scrollTop = before;
  scheduleVisibleLiveHydration();
}

function showToast(message, isError = false, options = {}) {
  if (!window.APStudyToast) return null;
  return window.APStudyToast.show({
    message,
    title: options.title,
    type: isError ? "error" : "success",
    action: options.action,
    duration: options.duration,
  });
}
