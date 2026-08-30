(function () {
  function createCourseControls({
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
  }) {
    function wireControls() {
      let searchTimer = null;
      const scheduleSectionReload = () => {
        clearDetailReturnContext();
        if (state.activeCourseView !== "search") {
          renderPanel();
          return;
        }
        window.clearTimeout(searchTimer);
        searchTimer = window.setTimeout(() => {
          void loadSectionsForTerm(state.selectedTerm);
        }, 500);
      };

      let lastCompactCourses = isCompactCoursesViewport();
      window.addEventListener("resize", () => {
        const nextCompactCourses = isCompactCoursesViewport();
        if (nextCompactCourses === lastCompactCourses) return;
        lastCompactCourses = nextCompactCourses;
        renderCalendar();
      });

      const filterButton = document.getElementById("courses-filter-button");
      document.querySelector(".courses-view-toggle")?.addEventListener("click", (event) => {
        const button = event.target.closest("button[data-course-view]");
        if (!button) return;
        const nextView = button.dataset.courseView || "search";
        if (state.activeCourseView === nextView) return;
        if (state.activeCourseView === "selected" && nextView !== "selected") {
          state.removedSelectedSections.clear();
        }
        state.activeCourseView = nextView;
        clearDetailReturnContext();
        state.detailSectionId = null;
        state.editingSectionId = null;
        state.filtersOpen = false;
        renderPanel();
        void verifyCurrentAvailability();
      });

      filterButton?.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        state.filtersOpen = !state.filtersOpen;
        syncFilterControls();
        if (state.filtersOpen) {
          requestAnimationFrame(() => document.getElementById("courses-filter-popover")?.querySelector("select, input, button")?.focus({ preventScroll: true }));
        }
      });

      document.getElementById("courses-search-input")?.addEventListener("input", (event) => {
        state.searchQuery = event.target.value || "";
        clearDetailReturnContext();
        state.detailSectionId = null;
        state.editingSectionId = null;
        scheduleSectionReload();
      });

      document.getElementById("courses-term-select")?.addEventListener("change", (event) => {
        state.selectedTerm = event.target.value || "";
        clearDetailReturnContext();
        state.detailSectionId = null;
        state.editingSectionId = null;
        state.filtersOpen = false;
        state.removedSelectedSections.clear();
        resetWeekScroll();
        void loadSectionsForTerm(state.selectedTerm);
      });

      document.getElementById("courses-day-toggle")?.addEventListener("click", (event) => {
        const button = event.target.closest("button[data-day]");
        if (!button) return;
        const day = button.dataset.day || "";
        if (!day) return;
        if (state.dayFilters.has(day)) {
          state.dayFilters.delete(day);
        } else {
          state.dayFilters.add(day);
        }
        clearDetailReturnContext();
        state.detailSectionId = null;
        state.editingSectionId = null;
        scheduleSectionReload();
      });

      document.getElementById("courses-time-enabled")?.addEventListener("change", (event) => {
        state.timeEnabled = Boolean(event.target.checked);
        document.getElementById("courses-time-start").disabled = !state.timeEnabled;
        document.getElementById("courses-time-end").disabled = !state.timeEnabled;
        clearDetailReturnContext();
        state.detailSectionId = null;
        state.editingSectionId = null;
        scheduleSectionReload();
      });

      document.getElementById("courses-time-start")?.addEventListener("change", (event) => {
        state.timeStart = event.target.value || "06:00";
        scheduleSectionReload();
      });

      document.getElementById("courses-time-end")?.addEventListener("change", (event) => {
        state.timeEnd = event.target.value || "23:59";
        scheduleSectionReload();
      });

      document.getElementById("courses-campus-filter")?.addEventListener("change", (event) => {
        state.campusFilter = event.target.value || "all";
        clearDetailReturnContext();
        state.detailSectionId = null;
        state.editingSectionId = null;
        scheduleSectionReload();
      });

      document.getElementById("courses-requirement-filter")?.addEventListener("change", (event) => {
        state.requirementFilter = event.target.value || "all";
        clearDetailReturnContext();
        state.detailSectionId = null;
        state.editingSectionId = null;
        scheduleSectionReload();
      });

      document.getElementById("courses-availability-filter")?.addEventListener("change", (event) => {
        const input = event.target.closest('input[type="checkbox"]');
        if (!input) return;
        const status = String(input.value || "").trim().toLowerCase();
        if (!status) return;
        if (input.checked) state.statusFilters.add(status);
        else state.statusFilters.delete(status);
        clearDetailReturnContext();
        state.detailSectionId = null;
        state.editingSectionId = null;
        renderCourses();
      });

      document.getElementById("courses-prev-term")?.addEventListener("click", () => {
        changeTermBy(-1);
      });

      document.getElementById("courses-next-term")?.addEventListener("click", () => {
        changeTermBy(1);
      });

      document.addEventListener("click", (event) => {
        const popover = document.getElementById("courses-filter-popover");
        if (!state.filtersOpen || !popover || popover.contains(event.target) || filterButton?.contains(event.target)) {
          return;
        }
        state.filtersOpen = false;
        syncFilterControls();
      });

      document.addEventListener("pointerover", (event) => {
        const card = event.target.closest(".course-card[data-section-id]");
        if (!card) return;
        const id = card.dataset.sectionId;
        if (state.hoveredSectionId === id) return;
        state.hoveredSectionId = id;
        renderCalendar();
      });

      document.addEventListener("pointerout", (event) => {
        const card = event.target.closest(".course-card[data-section-id]");
        if (!card || card.contains(event.relatedTarget)) return;
        state.hoveredSectionId = null;
        renderCalendar();
      });

      document.addEventListener("click", (event) => {
        const closeDetailButton = event.target.closest("[data-close-detail]");
        if (closeDetailButton) {
          event.preventDefault();
          event.stopPropagation();
          closeDetail();
          return;
        }

        const editButton = event.target.closest("[data-open-edit-section-id]");
        if (editButton) {
          event.preventDefault();
          event.stopPropagation();
          startEditingCourse(editButton.dataset.openEditSectionId);
          return;
        }

        const cancelEdit = event.target.closest("[data-edit-cancel]");
        if (cancelEdit) {
          event.preventDefault();
          event.stopPropagation();
          state.editingSectionId = null;
          renderPanel();
          return;
        }

        const saveEdit = event.target.closest("[data-edit-save]");
        if (saveEdit) {
          event.preventDefault();
          event.stopPropagation();
          void saveEditedCourse(saveEdit.dataset.editSave);
          return;
        }

        const colorButton = event.target.closest("[data-course-color-key]");
        if (colorButton && !colorButton.disabled) {
          event.preventDefault();
          event.stopPropagation();
          const form = colorButton.closest(".courses-edit");
          form?.querySelectorAll("[data-course-color-key]").forEach((button) => {
            const selected = button === colorButton;
            button.classList.toggle("is-selected", selected);
            button.setAttribute("aria-pressed", selected ? "true" : "false");
          });
          return;
        }

        const addMeeting = event.target.closest("[data-add-meeting]");
        if (addMeeting) {
          event.preventDefault();
          event.stopPropagation();
          const editor = addMeeting.closest(".courses-edit-card")?.querySelector("[data-meeting-editor]");
          if (!editor) return;
          const usedDays = new Set(Array.from(editor.querySelectorAll("[data-meeting-day]")).map((select) => select.value));
          const nextDay = ["Mon", "Tue", "Wed", "Thu", "Fri"].find((day) => !usedDays.has(day)) || "Mon";
          editor.insertAdjacentHTML("beforeend", buildMeetingRowHtml({ day: nextDay }));
          editor.lastElementChild?.querySelector("[data-meeting-day]")?.focus();
          return;
        }

        const removeMeeting = event.target.closest("[data-remove-meeting]");
        if (removeMeeting) {
          event.preventDefault();
          event.stopPropagation();
          const row = removeMeeting.closest(".courses-meeting-row");
          const editor = row?.closest("[data-meeting-editor]");
          const rows = editor ? Array.from(editor.querySelectorAll(".courses-meeting-row")) : [];
          const plan = meetingRemovalFocusPlan(rows.indexOf(row), rows.length);
          const focusTarget = Number.isInteger(plan.rowIndex)
            ? rows[plan.rowIndex]?.querySelector("[data-meeting-day]")
            : editor?.closest(".courses-edit-card")?.querySelector("[data-add-meeting]");
          row?.remove();
          focusTarget?.focus();
          return;
        }

        const addButton = event.target.closest("[data-add-section-id]");
        if (addButton) {
          event.preventDefault();
          event.stopPropagation();
          const sectionId = addButton.dataset.addSectionId;
          const addedCourse = state.savedCoursesBySection.get(String(sectionId));
          if (addedCourse?.id) {
            void removeCourse(addedCourse.id, sectionId);
          } else {
            void addCourse(sectionId);
          }
          return;
        }

        const refreshButton = event.target.closest("[data-refresh-section-id]");
        if (refreshButton) {
          event.preventDefault();
          event.stopPropagation();
          void refreshSectionStatus(refreshButton.dataset.refreshSectionId);
          return;
        }

        const removeButton = event.target.closest("[data-remove-course-id]");
        if (removeButton) {
          event.preventDefault();
          event.stopPropagation();
          const sectionId = removeButton.dataset.sectionId;
          void removeCourse(removeButton.dataset.removeCourseId, sectionId);
          return;
        }

        const trackButton = event.target.closest("[data-track-section-id]");
        if (trackButton) {
          event.preventDefault();
          event.stopPropagation();
          const sectionId = trackButton.dataset.trackSectionId;
          const enabled = trackButton.getAttribute("aria-pressed") !== "true";
          void setTrack(sectionId, enabled);
          return;
        }

        const removeTrackButton = event.target.closest("[data-remove-track-id]");
        if (removeTrackButton) {
          event.preventDefault();
          event.stopPropagation();
          void removeTrack(removeTrackButton.dataset.removeTrackId, removeTrackButton.dataset.sectionId);
          return;
        }

        const eventBlock = event.target.closest(".courses-event[data-section-id], .courses-mobile-event[data-section-id]");
        if (eventBlock) {
          openDetail(eventBlock.dataset.sectionId, eventBlock);
          return;
        }

        const card = event.target.closest(".course-card[data-section-id]");
        if (card) {
          openDetail(card.dataset.sectionId, card);
        }
      });

      document.getElementById("courses-panel-content")?.addEventListener("change", (event) => {
        const select = event.target.closest("[data-track-interval-section-id]");
        if (!select) return;
        event.stopPropagation();
        void setTrack(select.dataset.trackIntervalSectionId, true, select.value);
      });

      window.addEventListener("keydown", (event) => {
        if (event.key === "Escape" && state.filtersOpen) {
          state.filtersOpen = false;
          syncFilterControls();
          filterButton?.focus({ preventScroll: true });
          return;
        }
        if (event.key === "Escape" && state.editingSectionId) {
          state.editingSectionId = null;
          renderPanel();
          return;
        }
        if (event.key === "Escape" && state.detailSectionId) {
          event.preventDefault();
          closeDetail();
        }
      });
    }

    return { wireControls };
  }

  window.APStudyCoursesControls = { create: createCourseControls };
})();
