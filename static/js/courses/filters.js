(function () {
  const AVAILABILITY_PHASES = {
    UNVERIFIED: "unverified",
    PENDING: "pending",
    VERIFIED: "verified",
    UNAVAILABLE: "unavailable",
  };
  const STATUS_LABELS = { open: "Open", closed: "Closed", waitlist: "Waitlist" };

  function createCourseFilters({
    state,
    COURSE_START_MINUTES,
    COURSE_END_MINUTES,
    getSection,
    rememberSection,
    utils,
    getEffectiveAvailability,
  }) {
    const {
      buildSectionSearchBlob,
      compareCourseSections,
      parseAtlasTimeToken,
      parseTimeInput,
    } = utils;

    function defaultGetEffectiveAvailability(section) {
      const raw = section && typeof section === "object"
        ? String(section.enrollment_status || "").trim().toLowerCase()
        : "";
      return {
        phase: AVAILABILITY_PHASES.UNVERIFIED,
        status: STATUS_LABELS[raw] || null,
        source: "catalog",
      };
    }

    const resolveAvailability = typeof getEffectiveAvailability === "function"
      ? getEffectiveAvailability
      : defaultGetEffectiveAvailability;

    function getFilteredSections(options) {
      const ignoreStatus = Boolean(options && options.ignoreStatus);
      const query = state.searchQuery.trim().toLowerCase();
      return getSectionsForActiveView().filter((section) => {
        if (section.term && section.term !== state.selectedTerm) return false;
        const searchBlob = section.searchBlob || buildSectionSearchBlob(section);
        if (query && !searchBlob.includes(query)) return false;
        if (!sectionMatchesCampus(section)) return false;
        if (!sectionMatchesRequirement(section)) return false;
        if (!ignoreStatus && !sectionMatchesEnrollmentStatus(section)) return false;
        if (!sectionMatchesDay(section)) return false;
        if (!sectionMatchesTime(section)) return false;
        return true;
      });
    }

    function getSectionsForActiveView() {
      if (state.activeCourseView === "selected") {
        const sections = Array.from(state.savedCoursesBySection.entries())
          .map(([sectionId, course]) => resolvePanelSection(sectionId, course))
          .filter(Boolean);
        for (const [sectionId, section] of state.removedSelectedSections.entries()) {
          if (state.savedCoursesBySection.has(sectionId)) continue;
          const resolved = resolvePanelSection(sectionId, section);
          if (resolved) sections.push(resolved);
        }
        return sections.sort(compareCourseSections);
      }
      if (state.activeCourseView === "tracked") {
        return Array.from(state.tracksBySection.entries())
          .map(([sectionId, track]) => resolvePanelSection(sectionId, track))
          .filter(Boolean)
          .sort(compareCourseSections);
      }
      return state.sections;
    }

    function resolvePanelSection(sectionId, fallback) {
      const section = getSection(sectionId) || rememberSection(fallback);
      if (!section) return null;
      if (!section.searchBlob) section.searchBlob = buildSectionSearchBlob(section);
      return section;
    }

    function sectionMatchesDay(section) {
      if (!state.dayFilters.size) return true;
      return (section.meetings || []).some((meeting) => state.dayFilters.has(meeting.day));
    }

    function sectionMatchesCampus(section) {
      const campus = String(state.campusFilter || "all").toLowerCase();
      if (!campus || campus === "all") return true;
      const value = String(section.campus || section.campus_description || "").toLowerCase();
      if (!value) return campus === "atlanta";
      if (campus === "oxford") return value.includes("oxford");
      if (campus === "atlanta") return value.includes("atlanta") || value.includes("main") || value === "emory";
      return value.includes(campus);
    }

    function sectionMatchesEnrollmentStatus(section) {
      if (!state.statusFilters.size) return true;
      const availability = resolveAvailability(section);
      if (!availability || availability.phase !== AVAILABILITY_PHASES.VERIFIED) return false;
      const status = String(availability.status || "").trim().toLowerCase();
      return state.statusFilters.has(status);
    }

    function isAvailabilityVerificationPending(sections) {
      if (!state.statusFilters.size) return false;
      const candidates = Array.isArray(sections)
        ? sections
        : getFilteredSections({ ignoreStatus: true });
      return candidates.some((section) => {
        const availability = resolveAvailability(section);
        return Boolean(availability && availability.phase === AVAILABILITY_PHASES.PENDING);
      });
    }

    const GER_ALIASES = window.APSTUDY_COURSES_GER_ALIASES || {};
    const GER_COMPOSITE = window.APSTUDY_COURSES_GER_COMPOSITE || {};
    const GER_REQUIREMENTS = window.APSTUDY_COURSES_GENERAL_ED_REQUIREMENTS || [];

    function requirementValues(section) {
      return [
        section.requirement_designation,
        section.requirements,
        section.requirement,
        section.ger,
        section.attributes,
      ].flatMap((value) => Array.isArray(value) ? value : [value])
        .map((value) => String(value || "").trim())
        .filter(Boolean);
    }

    function normalizeRequirement(value) {
      return String(value || "").toLowerCase().replace(/[^a-z0-9*]+/g, "");
    }

    function canonicalRequirement(requirement) {
      const normalized = normalizeRequirement(requirement);
      return GER_REQUIREMENTS.find((option) => normalizeRequirement(option) === normalized) || String(requirement || "").trim();
    }

    function buildMatchTokens(requirement) {
      const canonical = canonicalRequirement(requirement);
      const values = [canonical, ...(GER_ALIASES[canonical] || [])];
      const tokens = new Set();
      values.forEach((value) => {
        const token = normalizeRequirement(value);
        if (!token) return;
        tokens.add(token);
        const relaxed = token.replace(/\*+$/, "");
        if (relaxed) tokens.add(relaxed);
      });
      return tokens;
    }

    function valuesMatchTokens(values, tokens) {
      return values.some((value) => {
        const token = normalizeRequirement(value);
        if (!token) return false;
        return tokens.has(token) || tokens.has(token.replace(/\*+$/, ""));
      });
    }

    function matchesRequirementValue(requirement, section) {
      const tokens = buildMatchTokens(requirement);
      if (!tokens.size) return false;
      return valuesMatchTokens(requirementValues(section), tokens);
    }

    function matchesEthnComponent(section) {
      if (matchesRequirementValue("Race and Ethnicity(*)", section)) return true;
      return requirementValues(section).some((value) => {
        const text = String(value).toLowerCase();
        return text.includes("race") && text.includes("ethnic");
      });
    }

    function sectionMatchesRequirement(section) {
      const requirement = String(state.requirementFilter || "all").trim();
      if (!requirement || requirement.toLowerCase() === "all") return true;
      const values = requirementValues(section).map((value) => value.toLowerCase());
      if (requirement.toLowerCase() === "starred") return values.some((value) => value.includes("*"));
      const canonical = canonicalRequirement(requirement);
      const composite = GER_COMPOSITE[canonical];
      if (composite?.length) {
        return composite.every((part) => (
          part === "Race and Ethnicity(*)"
            ? matchesEthnComponent(section)
            : matchesRequirementValue(part, section)
        ));
      }
      return matchesRequirementValue(requirement, section);
    }

    function sectionMatchesTime(section) {
      if (!state.timeEnabled) return true;
      const start = parseTimeInput(state.timeStart) ?? COURSE_START_MINUTES;
      const end = parseTimeInput(state.timeEnd) ?? COURSE_END_MINUTES;
      const rangeStart = Math.min(start, end);
      const rangeEnd = Math.max(start, end);
      return (section.meetings || []).some((meeting) => {
        if (state.dayFilters.size && !state.dayFilters.has(meeting.day)) return false;
        const meetingStart = parseAtlasTimeToken(meeting.start);
        const meetingEnd = parseAtlasTimeToken(meeting.end);
        if (meetingStart === null || meetingEnd === null) return false;
        return meetingStart < rangeEnd && meetingEnd > rangeStart;
      });
    }

    return { getFilteredSections, isAvailabilityVerificationPending };
  }

  window.APStudyCoursesFilters = { create: createCourseFilters };
})();
