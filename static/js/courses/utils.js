(function () {
  const { escapeHtml } = window.APStudyUIPrimitives;

  function buildSectionSearchBlob(section) {
    const parts = [];
    const push = (value) => {
      if (value === null || typeof value === "undefined") return;
      if (Array.isArray(value)) {
        value.forEach(push);
        return;
      }
      if (typeof value === "object") {
        push(value.name);
        push(value.email);
        push(value.instructor);
        push(value.course_title);
        push(value.course_name);
        return;
      }
      const text = String(value).trim();
      if (text) parts.push(text);
    };

    push([
      section.course_title,
      section.course_name,
      section.subject,
      section.catalog,
      section.catalog_number,
      section.course_code,
      section.instructor,
      section.instructor_name,
      section.instructors,
      section.crn,
      section.section_number,
      section.requirement_designation,
      section.requirements,
      section.requirement,
      section.ger,
      section.campus,
      section.campus_description,
      section.credit_hours,
      section.course_description,
      section.description,
    ]);

    return parts.join(" ").toLowerCase();
  }

  function compareCourseSections(a, b) {
    return compareText(a?.term, b?.term)
      || compareText(a?.course_code || courseCodeFromSection(a), b?.course_code || courseCodeFromSection(b))
      || compareText(a?.course_title || a?.course_name, b?.course_title || b?.course_name)
      || compareText(a?.section_number, b?.section_number)
      || compareText(a?.crn, b?.crn);
  }

  function courseCodeFromSection(section) {
    return `${section?.subject || ""} ${section?.catalog || section?.catalog_number || ""}`.trim();
  }

  function compareText(a, b) {
    return String(a || "").localeCompare(String(b || ""), undefined, { numeric: true, sensitivity: "base" });
  }

  function formatSeats(section) {
    const seats = section?.seats_available;
    const capacity = section?.enrollment_capacity;
    if (seats === null || typeof seats === "undefined" || seats === "") {
      return "Seats unknown";
    }
    if (capacity !== null && typeof capacity !== "undefined" && capacity !== "") {
      return `${seats} of ${capacity} seats`;
    }
    return `${seats} ${Number(seats) === 1 ? "seat" : "seats"}`;
  }

  function formatCourseCardSchedule(section) {
    const schedule = normalizeScheduleDisplay(section?.schedule_display || "");
    const instructor = section?.instructor || section?.instructor_name || "";
    const parts = [schedule, instructor].map((value) => String(value || "").trim()).filter(Boolean);
    return parts.length ? parts.join(" | ") : "TBA";
  }

  function normalizeScheduleDisplay(value) {
    return String(value || "").replace(/(\d(?::\d{2})?)\s*([ap])\b/gi, (_, time, suffix) => {
      return `${time} ${suffix.toLowerCase() === "a" ? "AM" : "PM"}`;
    });
  }

  function formatTermLabel(term) {
    const parts = String(term || "").split("_");
    if (parts.length !== 2) return term || "Term";
    return `${parts[0]} ${parts[1]}`;
  }

  function parseAtlasTimeToken(token) {
    const numeric = String(token || "").replace(/\D/g, "");
    if (!numeric) return null;
    const padded = numeric.length >= 4 ? numeric.slice(-4) : numeric.padStart(4, "0");
    const hour = Number.parseInt(padded.slice(0, 2), 10);
    const minute = Number.parseInt(padded.slice(2, 4), 10);
    if (!Number.isFinite(hour) || !Number.isFinite(minute) || hour > 24 || minute > 59) return null;
    return hour * 60 + minute;
  }

  function parseTimeInput(value) {
    const match = /^(\d{2}):(\d{2})$/.exec(String(value || ""));
    if (!match) return null;
    const hour = Number.parseInt(match[1], 10);
    const minute = Number.parseInt(match[2], 10);
    if (!Number.isFinite(hour) || !Number.isFinite(minute)) return null;
    return hour * 60 + minute;
  }

  function formatAtlasTime(token) {
    const minutes = parseAtlasTimeToken(token);
    if (minutes === null) return "TBA";
    const hour24 = Math.floor(minutes / 60);
    const minute = minutes % 60;
    const suffix = hour24 >= 12 ? "PM" : "AM";
    let hour = hour24 % 12;
    if (hour === 0) hour = 12;
    return `${hour}:${String(minute).padStart(2, "0")} ${suffix}`;
  }

  function formatHourLabel(hour) {
    if (hour === 0 || hour === 24) return "12 AM";
    if (hour < 12) return `${hour} AM`;
    if (hour === 12) return "12 PM";
    return `${hour - 12} PM`;
  }

  function formatDateRange(range) {
    if (!range?.start || !range?.end) return "N/A";
    return `${formatDate(range.start)} - ${formatDate(range.end)}`;
  }

  function formatDate(value) {
    const date = new Date(`${value}T00:00:00`);
    if (Number.isNaN(date.getTime())) return value;
    return date.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
  }

  function formatDateTime(value) {
    if (!value) return "just now";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return date.toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      year: "numeric",
      hour: "numeric",
      minute: "2-digit",
    });
  }

  function cssEscape(value) {
    if (window.CSS?.escape) return window.CSS.escape(String(value));
    return String(value).replace(/["\\]/g, "\\$&");
  }

  function parseCoursesSectionDeepLink(locationLike = window.location) {
    const hash = String(locationLike.hash || "").replace(/^#/, "");
    if (hash.startsWith("section=")) {
      const sectionId = decodeURIComponent(hash.slice("section=".length)).trim();
      if (sectionId) return sectionId;
    }
    const params = new URLSearchParams(locationLike.search || "");
    const querySection = String(params.get("section") || "").trim();
    return querySection || null;
  }

  window.APStudyCoursesUtils = {
    buildSectionSearchBlob,
    compareCourseSections,
    cssEscape,
    escapeHtml,
    formatAtlasTime,
    formatCourseCardSchedule,
    formatDateRange,
    formatDateTime,
    formatHourLabel,
    formatSeats,
    formatTermLabel,
    normalizeScheduleDisplay,
    parseAtlasTimeToken,
    parseCoursesSectionDeepLink,
    parseTimeInput,
  };
})();
