(function () {
  function collectMeetingOverrides(rows, { COURSE_DAYS, parseAtlasTimeToken, timeInputToAtlasToken }) {
    const validDays = new Set(COURSE_DAYS.map((day) => day.key));
    return (rows || []).flatMap((row) => {
      const day = String(row?.day || "");
      const start = timeInputToAtlasToken(row?.start);
      const end = timeInputToAtlasToken(row?.end);
      if (!validDays.has(day) || !start || !end || parseAtlasTimeToken(end) <= parseAtlasTimeToken(start)) {
        return [];
      }
      return [{ day, start, end }];
    });
  }

  function meetingRemovalFocusPlan(rowIndex, rowCount) {
    if (rowIndex >= 0 && rowIndex < rowCount - 1) return { rowIndex: rowIndex + 1 };
    if (rowIndex > 0) return { rowIndex: rowIndex - 1 };
    return { focusAddButton: true };
  }

  window.APStudyCoursesEdit = { collectMeetingOverrides, meetingRemovalFocusPlan };
})();
