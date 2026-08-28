export const SIMULATED_CALENDAR_ID = "simulated_courses";
export const SIMULATED_CALENDAR_LABEL = "Simulated Courses";

const MEETING_DAY_NUMBERS = {
  Sun: 0,
  Mon: 1,
  Tue: 2,
  Wed: 3,
  Thu: 4,
  Fri: 5,
  Sat: 6,
};

const COURSE_COLOR_HEX = {
  "course-color-01": "#2563eb",
  "course-color-02": "#0891b2",
  "course-color-03": "#059669",
  "course-color-04": "#65a30d",
  "course-color-05": "#ca8a04",
  "course-color-06": "#ea580c",
  "course-color-07": "#dc2626",
  "course-color-08": "#e11d48",
  "course-color-09": "#9333ea",
  "course-color-10": "#7c3aed",
  "course-color-11": "#4f46e5",
  "course-color-12": "#0d9488",
  "course-color-13": "#7e22ce",
  "course-color-14": "#b45309",
  "course-color-15": "#334155",
  "course-color-16": "#be185d",
};

export function parseAtlasTimeToken(timeToken) {
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

function localDateKey(date) {
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${date.getFullYear()}-${month}-${day}`;
}

function localDateTime(date) {
  const minutes = String(date.getMinutes()).padStart(2, "0");
  return `${localDateKey(date)}T${String(date.getHours()).padStart(2, "0")}:${minutes}:00`;
}

function parseDateOnly(dateStr) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(String(dateStr || ""))) return null;
  const [year, month, day] = String(dateStr).split("-").map((value) => parseInt(value, 10));
  if (Number.isNaN(year) || Number.isNaN(month) || Number.isNaN(day)) return null;
  return new Date(year, month - 1, day, 0, 0, 0, 0);
}

function courseColor(course) {
  return COURSE_COLOR_HEX[course?.color_key] || COURSE_COLOR_HEX["course-color-01"];
}

export function buildSimulatedCourseEvents(courses, { start, end } = {}) {
  if (!(start instanceof Date) || !(end instanceof Date)) return [];
  const events = [];
  for (const course of Array.isArray(courses) ? courses : []) {
    const sectionId = String(course?.section_id || course?.id || "");
    if (!sectionId) continue;
    const meetings = Array.isArray(course?.meetings) ? course.meetings : [];
    if (!meetings.length) continue;
    const range = course?.date_range || {};
    const rangeStart = parseDateOnly(range.start);
    const rangeEnd = parseDateOnly(range.end);
    if (!rangeStart || !rangeEnd || rangeEnd < rangeStart) continue;
    const firstDay = rangeStart > start ? rangeStart : start;
    const lastDay = rangeEnd < end ? rangeEnd : end;
    if (lastDay < firstDay) continue;
    for (const meeting of meetings) {
      const meetingDay = MEETING_DAY_NUMBERS[String(meeting?.day || "").trim()];
      if (meetingDay === undefined) continue;
      const parsedStart = parseAtlasTimeToken(meeting?.start);
      const parsedEnd = parseAtlasTimeToken(meeting?.end);
      if (!parsedStart || !parsedEnd) continue;
      for (const day = new Date(firstDay); day <= lastDay; day.setDate(day.getDate() + 1)) {
        if (day.getDay() !== meetingDay) continue;
        const eventStart = new Date(
          day.getFullYear(), day.getMonth(), day.getDate(),
          parsedStart.hour, parsedStart.minute, 0, 0,
        );
        const eventEnd = new Date(
          day.getFullYear(), day.getMonth(), day.getDate(),
          parsedEnd.hour, parsedEnd.minute, 0, 0,
        );
        if (eventEnd <= eventStart) continue;
        const courseCode = String(course?.course_code || "").trim();
        const courseTitle = String(course?.course_title || course?.course_name || "").trim();
        const sectionNumber = String(course?.section_number || "").trim();
        const instructor = String(course?.instructor || course?.instructor_name || "").trim();
        events.push({
          uid: `${sectionId}|${localDateKey(day)}|${meeting.start}|${meeting.end}`,
          title: courseCode || courseTitle || "Class",
          description: [
            courseTitle,
            sectionNumber ? `Sec ${sectionNumber}` : "",
            instructor || "TBA",
          ].filter(Boolean).join(" | "),
          start: localDateTime(eventStart),
          end: localDateTime(eventEnd),
          is_all_day: false,
          calendar_id: SIMULATED_CALENDAR_ID,
          course: courseTitle || courseCode || SIMULATED_CALENDAR_LABEL,
          type: "class-meeting",
          location: String(course?.location || "").trim(),
          source: "simulated",
          color: courseColor(course),
        });
      }
    }
  }
  return events.sort((a, b) => new Date(a.start) - new Date(b.start));
}

export function simulatedCoursesPayload(courses, range) {
  return {
    calendar_sources: [{
      id: SIMULATED_CALENDAR_ID,
      display_name: SIMULATED_CALENDAR_LABEL,
    }],
    events: buildSimulatedCourseEvents(courses, range),
  };
}

export async function fetchSimulatedCourses(fetchFn = fetch) {
  const response = await fetchFn("/api/courses/saved", {
    credentials: "same-origin",
    headers: { Accept: "application/json" },
  });
  if (!response.ok) return [];
  const payload = await response.json().catch(() => null);
  return Array.isArray(payload?.courses) ? payload.courses : [];
}
