(function () {
  function createCoursePanel({
    state,
    COURSE_COLOR_PALETTE,
    COURSE_DAYS,
    COURSE_RESULT_LIMIT,
    getFilteredSections,
    getSection,
    isTrackable,
    utils,
  }) {
    const {
      cssEscape,
      escapeHtml,
      formatCourseCardSchedule,
      formatDateRange,
      formatDateTime,
      formatSeats,
      formatTermLabel,
      normalizeScheduleDisplay,
      parseAtlasTimeToken,
      parseTimeInput,
    } = utils;

    function buildCourseResultsSkeletonHtml(label = "Loading courses...") {
      const block = (className) => window.APStudySkeleton?.block?.(className)
        || `<div data-slot="skeleton" class="bg-muted rounded-md animate-pulse ${className}"></div>`;
      return `
        <div class="courses-results-skeleton apstudy-skeleton" role="status" aria-live="polite" aria-busy="true">
          <span class="sr-only">${escapeHtml(label)}</span>
          <div class="contents" aria-hidden="true">
            ${Array.from({ length: 4 }, (_, index) => `
              <article class="course-card course-skeleton-card">
                <div class="course-card-top">
                  <div class="course-card-title">
                    ${block(index % 2 ? "h-4 w-28" : "h-4 w-36")}
                    ${block(index % 2 ? "h-3 w-4/5" : "h-3 w-full")}
                  </div>
                  ${block("h-8 w-[72px]")}
                </div>
                <div class="course-card-schedule">${block("h-3 w-3/4")}</div>
                <div class="course-card-meta">${block("h-6 w-14 rounded-full")} ${block("h-6 w-16 rounded-full")} ${block("h-6 w-12 rounded-full")}</div>
              </article>
            `).join("")}
          </div>
        </div>
      `;
    }

    function renderTermSelect() {
      const select = document.getElementById("courses-term-select");
      if (!select) return;
      select.innerHTML = state.terms
        .map((term) => `<option value="${escapeHtml(term)}" ${term === state.selectedTerm ? "selected" : ""}>${escapeHtml(formatTermLabel(term))}</option>`)
        .join("");
      const searchInput = document.getElementById("courses-search-input");
      if (searchInput) {
        const termLabel = formatTermLabel(state.selectedTerm);
        searchInput.placeholder = termLabel ? `Search ${termLabel} classes` : "Search classes";
      }
    }

    function renderPanel() {
      const summary = document.getElementById("courses-result-summary");
      const content = document.getElementById("courses-panel-content");
      if (!summary || !content) return;

      document.querySelector(".courses-panel")?.classList.toggle("is-detailing", Boolean(state.detailSectionId || state.editingSectionId));
      syncViewControls();
      syncFilterControls();

      if (state.loading) {
        summary.textContent = "Loading Emory Atlas...";
        content.innerHTML = buildCourseResultsSkeletonHtml();
        return;
      }
      if (state.error) {
        summary.textContent = "Course search unavailable";
        content.innerHTML = `<div class="courses-state">${escapeHtml(state.error)}</div>`;
        return;
      }
      if (state.editingSectionId) {
        summary.textContent = "Edit class";
        content.innerHTML = buildEditHtml(state.editingSectionId);
        return;
      }
      if (state.detailSectionId) {
        summary.textContent = "Course details";
        content.innerHTML = buildDetailHtml(state.detailSectionId);
        return;
      }
      if (state.sectionsLoading) {
        summary.textContent = `Loading ${formatTermLabel(state.selectedTerm)}...`;
        content.innerHTML = buildCourseResultsSkeletonHtml(`Loading sections for ${formatTermLabel(state.selectedTerm)}...`);
        return;
      }

      const filtered = getFilteredSections();
      const visible = filtered.slice(0, COURSE_RESULT_LIMIT);
      summary.textContent = getPanelSummaryText(filtered.length);
      if (!visible.length) {
        content.innerHTML = `<div class="courses-state">${escapeHtml(getEmptyStateText())}</div>`;
        return;
      }

      const capNote = filtered.length > COURSE_RESULT_LIMIT
        ? `<div class="courses-state">Showing first ${COURSE_RESULT_LIMIT.toLocaleString()} matches. Refine your search for more specific results.</div>`
        : "";
      content.innerHTML = visible.map(buildCourseCardHtml).join("") + capNote;
    }

    function syncViewControls() {
      document.querySelectorAll("[data-course-view]").forEach((button) => {
        const selected = button.dataset.courseView === state.activeCourseView;
        button.classList.toggle("is-active", selected);
        button.setAttribute("aria-pressed", selected ? "true" : "false");
      });
    }

    function getPanelSummaryText(count) {
      const term = formatTermLabel(state.selectedTerm);
      if (state.activeCourseView === "selected") {
        return `${count.toLocaleString()} selected ${count === 1 ? "course" : "courses"} in ${term}`;
      }
      if (state.activeCourseView === "tracked") {
        return `${count.toLocaleString()} tracked ${count === 1 ? "course" : "courses"} in ${term}`;
      }
      return `${count.toLocaleString()} sections in ${term}`;
    }

    function getEmptyStateText() {
      if (state.activeCourseView === "selected") return "No selected courses match your filters.";
      if (state.activeCourseView === "tracked") return "No tracked courses match your filters.";
      return "No sections match your filters.";
    }

    function syncFilterControls() {
      const filterButton = document.getElementById("courses-filter-button");
      const filterPopover = document.getElementById("courses-filter-popover");
      const filterCount = document.getElementById("courses-filter-count");
      const timeEnabled = document.getElementById("courses-time-enabled");
      const timeStart = document.getElementById("courses-time-start");
      const timeEnd = document.getElementById("courses-time-end");
      const campusFilter = document.getElementById("courses-campus-filter");
      const requirementFilter = document.getElementById("courses-requirement-filter");
      const availabilityFilter = document.getElementById("courses-availability-filter");
      const activeFilterCount = state.dayFilters.size
        + (state.timeEnabled ? 1 : 0)
        + (state.campusFilter && state.campusFilter !== "all" ? 1 : 0)
        + (state.requirementFilter && state.requirementFilter !== "all" ? 1 : 0)
        + state.statusFilters.size;
      if (filterButton) {
        filterButton.setAttribute("aria-expanded", state.filtersOpen ? "true" : "false");
        filterButton.classList.toggle("is-active", state.filtersOpen || activeFilterCount > 0);
      }
      if (filterPopover) filterPopover.hidden = !state.filtersOpen;
      if (filterCount) {
        filterCount.textContent = String(activeFilterCount);
        filterCount.hidden = activeFilterCount === 0;
      }
      if (timeEnabled) timeEnabled.checked = state.timeEnabled;
      if (timeStart) {
        timeStart.value = state.timeStart;
        timeStart.disabled = !state.timeEnabled;
      }
      if (timeEnd) {
        timeEnd.value = state.timeEnd;
        timeEnd.disabled = !state.timeEnabled;
      }
      if (campusFilter) campusFilter.value = state.campusFilter || "all";
      if (requirementFilter) requirementFilter.value = state.requirementFilter || "all";
      availabilityFilter?.querySelectorAll('input[type="checkbox"]').forEach((input) => {
        input.checked = state.statusFilters.has(String(input.value || "").toLowerCase());
      });
      document.querySelectorAll("#courses-day-toggle button[data-day]").forEach((button) => {
        button.classList.toggle("is-active", state.dayFilters.has(button.dataset.day));
      });
    }

    function buildCourseCardHtml(section) {
      const id = section.id;
      const addedCourse = state.savedCoursesBySection.get(id);
      const isAdded = Boolean(addedCourse);
      const isTracked = Boolean(state.tracksBySection.get(id)?.enabled);
      const track = state.tracksBySection.get(id);
      const colorClass = isAdded ? getCourseColor(addedCourse).key : "";
      const status = section.enrollment_status || "Unknown";
      const statusClass = status.toLowerCase() === "open" ? "is-open" : status.toLowerCase() === "closed" ? "is-closed" : "";
      const hasLiveSnapshot = section.live_snapshot_available === true;
      const seats = hasLiveSnapshot ? formatSeats(section) : "Loading seats";
      const seatsClass = hasLiveSnapshot ? "" : "is-loading";
      const saving = state.savingIds.has(id);
      const sectionLabel = section.section_number ? ` <span class="course-section-inline">&middot; Sec ${escapeHtml(section.section_number)}</span>` : "";
      const scheduleDetail = formatCourseCardSchedule(section);
      return `
        <article class="course-card ${isAdded ? `is-added ${escapeHtml(colorClass)}` : ""}" data-section-id="${escapeHtml(id)}" tabindex="0">
          <div class="course-card-top">
            <div class="course-card-title">
              <strong>${escapeHtml(section.course_code || "Course")}${sectionLabel}</strong>
              <span>${escapeHtml(section.course_title || "Untitled course")}</span>
            </div>
            <button type="button" class="course-card-action ${isAdded ? "is-added" : ""}" data-add-section-id="${escapeHtml(id)}" ${saving ? "disabled" : ""}>${isAdded ? "Added" : "Add"}</button>
          </div>
          <div class="course-card-schedule">${escapeHtml(scheduleDetail)}</div>
          <div class="course-card-meta-row">
            <div class="course-card-meta">
              <span class="course-chip ${statusClass}">${escapeHtml(status)}</span>
              <span class="course-chip ${seatsClass}">${escapeHtml(seats)}</span>
              <span class="course-chip">${escapeHtml(section.schedule_type || "Type")}</span>
              <span class="course-chip">${escapeHtml(formatCampus(section))}</span>
            </div>
            ${track ? `<span class="course-card-tracked ${isTracked ? "" : "is-paused"} material-symbols-outlined" aria-label="Tracking ${isTracked ? "active" : "paused"}" title="Tracking ${isTracked ? "active" : "paused"}">${isTracked ? "notifications_active" : "notifications_paused"}</span>` : ""}
          </div>
        </article>
      `;
    }

    function buildDetailHtml(sectionId) {
      const section = getSection(sectionId);
      if (!section) return `<div class="courses-state">Course section not found.</div>`;
      const addedCourse = state.savedCoursesBySection.get(sectionId);
      const track = state.tracksBySection.get(sectionId);
      const trackEnabled = Boolean(track?.enabled);
      const canTrack = isTrackable(section);
      const saving = state.savingIds.has(sectionId);
      const tracking = state.trackingIds.has(sectionId);
      const status = section.enrollment_status || "Unknown";
      const statusClass = status.toLowerCase() === "open" ? "is-open" : status.toLowerCase() === "closed" ? "is-closed" : "";
      const liveText = state.detailLoading
        ? "Refreshing seats from Atlas..."
        : state.detailLiveError
          ? `Atlas live refresh unavailable. Showing saved catalog data.`
          : section.live_updated_at
            ? `Updated ${formatDateTime(section.live_updated_at)}`
            : "Showing local catalog data.";
      const description = section.course_description || section.description || "Description unavailable.";
      const selectedInterval = Number(track?.interval_minutes || state.allowedTrackIntervals[0] || 30);
      const trackDescription = section.is_cancelled
        ? "This section is cancelled."
        : trackEnabled
          ? "Email alerts are on for class seats and waitlist openings."
          : canTrack
            ? "Email me when a class seat or waitlist place opens."
            : "Email me if this section’s availability changes.";
      const waitlistTotal = section.waitlist_total !== null && section.waitlist_total !== undefined && Number.isFinite(Number(section.waitlist_total)) ? Number(section.waitlist_total) : null;
      const waitlistCapacity = section.waitlist_capacity !== null && section.waitlist_capacity !== undefined && Number.isFinite(Number(section.waitlist_capacity)) ? Number(section.waitlist_capacity) : null;
      const waitlistText = waitlistTotal === null || waitlistCapacity === null ? "Unavailable" : `${waitlistTotal} of ${waitlistCapacity} filled`;

      return `
        <article class="courses-detail">
          <div class="courses-detail-header">
            <div>
              <h2>${escapeHtml(section.course_code || "Course")}</h2>
              <p>${escapeHtml(section.course_title || "Untitled course")}</p>
              ${section.section_number ? `<span class="courses-section-kicker">Section ${escapeHtml(section.section_number)}</span>` : ""}
            </div>
            <button class="courses-detail-close" type="button" data-close-detail aria-label="Close course details">
              <span class="material-symbols-outlined" aria-hidden="true">close</span>
            </button>
          </div>
          <div class="courses-detail-meta">
            <span class="course-chip">${escapeHtml(formatTermLabel(section.term))}</span>
            <span class="course-chip ${statusClass}">${escapeHtml(status)}</span>
          </div>
          <section class="track-control">
            <div class="track-control-text">
              <strong>Track availability</strong>
              <span>${trackDescription}</span>
            </div>
            <div class="track-control-actions">
              ${track ? `
                <label class="track-interval">
                  <span>Every</span>
                  <select data-track-interval-section-id="${escapeHtml(sectionId)}" aria-label="Check interval" ${tracking || !trackEnabled ? "disabled" : ""}>
                    ${[5, 15, 30].map((minutes) => {
                      const allowed = state.allowedTrackIntervals.includes(minutes);
                      return `<option value="${minutes}" ${minutes === selectedInterval ? "selected" : ""} ${allowed ? "" : "disabled"}>${minutes} min${allowed ? "" : " · higher tier"}</option>`;
                    }).join("")}
                  </select>
                </label>
              ` : ""}
              <button type="button" class="track-toggle" data-track-section-id="${escapeHtml(sectionId)}" aria-label="${trackEnabled ? "Turn off" : "Turn on"} availability tracking" aria-pressed="${trackEnabled ? "true" : "false"}" ${tracking || section.is_cancelled ? "disabled" : ""}></button>
            </div>
            ${track ? `
              <div class="track-settings">
                <span class="track-tier-note">${escapeHtml(state.trackingTier.label)} · ${state.trackingUsage}${state.trackingLimit === null ? "" : ` of ${state.trackingLimit}`} active</span>
                ${track.cooldown_until_closed ? `<span class="track-cooldown"><span class="material-symbols-outlined" aria-hidden="true">schedule</span>Availability found; checking every 3 hours until it closes.</span>` : ""}
                <dl class="track-timing">
                  <div><dt>Last checked</dt><dd>${escapeHtml(track.last_checked_at ? formatDateTime(track.last_checked_at) : "Pending")}</dd></div>
                  <div><dt>Next check</dt><dd>${escapeHtml(track.next_check_at ? formatDateTime(track.next_check_at) : "Soon")}</dd></div>
                </dl>
              </div>
            ` : ""}
          </section>
          <div class="courses-detail-actions">
            <button type="button" class="courses-secondary-action" data-refresh-section-id="${escapeHtml(sectionId)}" ${state.detailLoading ? "disabled" : ""}>
              <span class="material-symbols-outlined" aria-hidden="true">sync</span>
              <span>${state.detailLoading ? "Refreshing" : "Refresh"}</span>
            </button>
            ${addedCourse
              ? `
                <button type="button" class="courses-secondary-action" data-open-edit-section-id="${escapeHtml(sectionId)}" ${saving ? "disabled" : ""}>
                  <span class="material-symbols-outlined" aria-hidden="true">edit</span>
                  <span>Edit</span>
                </button>
                <button type="button" class="courses-danger-action" data-remove-course-id="${escapeHtml(addedCourse.id)}" data-section-id="${escapeHtml(sectionId)}" ${saving ? "disabled" : ""}>Remove Class</button>
              `
              : `<button type="button" class="courses-primary-action" data-add-section-id="${escapeHtml(sectionId)}" ${saving ? "disabled" : ""}>Add Class</button>`
            }
          </div>
          <section class="courses-detail-card">
            ${detailRow("Instructor", formatInstructors(section))}
            ${detailRow("Schedule", normalizeScheduleDisplay(section.schedule_display || "TBA"))}
            ${detailRow("CRN", section.crn || "N/A")}
            ${detailRow("Type", section.schedule_type || "N/A")}
            ${detailRow("Location", section.location || "TBA")}
            ${detailRow("Campus", formatCampus(section))}
            ${detailRow("Seats", formatSeats(section))}
            ${detailRow("Waitlist", waitlistText)}
            ${detailRow("Credits", section.credit_hours || "N/A")}
            ${detailRow("Grading Mode", section.grading_mode || "N/A")}
            ${detailRow("Instruction Method", section.instruction_method || "N/A")}
            ${detailRow("Requirement", formatRequirement(section))}
            ${detailRow("Dates", formatDateRange(section.date_range))}
            ${detailRow("Live Status", liveText)}
          </section>
          <section class="courses-description-card">
            <span class="material-symbols-outlined" aria-hidden="true">notes</span>
            <div>
              <h3>Description</h3>
              <p>${escapeHtml(description)}</p>
            </div>
          </section>
        </article>
      `;
    }

    function buildEditHtml(sectionId) {
      const section = getSection(sectionId);
      const savedCourse = state.savedCoursesBySection.get(String(sectionId));
      if (!section || !savedCourse) return `<div class="courses-state">Saved course not found.</div>`;
      const selectedColorKey = getCourseColor(savedCourse).key;
      const meetings = normalizeMeetingsForEdit(section.meetings);
      const saving = state.editingSaving || state.savingIds.has(sectionId);

      return `
        <article class="courses-edit" data-editing-section-id="${escapeHtml(sectionId)}">
          <div class="courses-detail-header">
            <div>
              <h2>Edit Class</h2>
              <p>${escapeHtml(section.course_code || "Course")} ${section.section_number ? `Section ${escapeHtml(section.section_number)}` : ""}</p>
            </div>
            <button class="courses-detail-close" type="button" data-edit-cancel aria-label="Close edit class">
              <span class="material-symbols-outlined" aria-hidden="true">close</span>
            </button>
          </div>

          <section class="courses-edit-card courses-edit-color-card">
            <div class="courses-edit-section-heading">
              <h3>Color</h3>
              <span>Choose how this class appears on your week.</span>
            </div>
            <div class="courses-color-grid" role="group" aria-label="Course color">
              ${COURSE_COLOR_PALETTE.map((color) => {
                const isSelected = selectedColorKey === color.key;
                return `
                  <button
                    type="button"
                    class="courses-color-swatch ${escapeHtml(color.key)} ${isSelected ? "is-selected" : ""}"
                    data-course-color-key="${escapeHtml(color.key)}"
                    aria-label="${escapeHtml(color.key.replace("course-color-", "Color "))}"
                    aria-pressed="${isSelected ? "true" : "false"}"
                  >
                    <span class="material-symbols-outlined" aria-hidden="true">check</span>
                  </button>
                `;
              }).join("")}
            </div>
          </section>

          <section class="courses-edit-card">
            <div class="courses-edit-grid">
              ${editField("course_code", "Course Code", section.course_code || "")}
              ${editField("section_number", "Section", section.section_number || "")}
              ${editField("course_title", "Title", section.course_title || "", "wide")}
              ${editField("instructor", "Instructor", section.instructor || section.instructor_name || "", "wide")}
              ${editField("schedule_type", "Type", section.schedule_type || "")}
              ${editField("credit_hours", "Credits", section.credit_hours || "")}
              ${editField("location", "Location", section.location || "")}
              ${editField("campus", "Campus", formatCampus(section))}
              ${editField("requirement_designation", "Requirement", formatRequirement(section) === "N/A" ? "" : formatRequirement(section))}
              ${editField("schedule_display", "Schedule Text", section.schedule_display || "", "wide")}
            </div>
          </section>

          <section class="courses-edit-card">
            <div class="courses-edit-section-heading">
              <h3>Meeting Times</h3>
              <span>Each row appears on your weekly view. Add a row for another meeting on the same day.</span>
            </div>
            <div class="courses-meeting-editor" data-meeting-editor>
              ${meetings.length ? meetings.map(buildMeetingRowHtml).join("") : buildMeetingRowHtml()}
            </div>
            <button type="button" class="courses-secondary-action courses-add-meeting" data-add-meeting>
              <span class="material-symbols-outlined" aria-hidden="true">add</span>
              <span>Add meeting</span>
            </button>
          </section>

          <section class="courses-edit-card">
            ${editTextarea("course_description", "Description", section.course_description || section.description || "")}
            ${editTextarea("course_notes", "Notes", section.course_notes || "")}
          </section>

          <div class="courses-detail-actions courses-edit-actions">
            <button type="button" class="courses-primary-action" data-edit-save="${escapeHtml(sectionId)}" ${saving ? "disabled" : ""}>${saving ? "Saving..." : "Save"}</button>
            <button type="button" class="courses-secondary-action" data-edit-cancel ${saving ? "disabled" : ""}>Cancel</button>
          </div>
        </article>
      `;
    }

    function editField(name, label, value, className = "") {
      return `
        <label class="courses-edit-field ${className}">
          <span>${escapeHtml(label)}</span>
          <input type="text" name="${escapeHtml(name)}" value="${escapeHtml(value)}" />
        </label>
      `;
    }

    function editTextarea(name, label, value) {
      return `
        <label class="courses-edit-field wide">
          <span>${escapeHtml(label)}</span>
          <textarea name="${escapeHtml(name)}" rows="5">${escapeHtml(value)}</textarea>
        </label>
      `;
    }

    function getCourseColor(course) {
      return COURSE_COLOR_PALETTE.find((color) => color.key === course?.color_key) || COURSE_COLOR_PALETTE[0];
    }

    function normalizeMeetingsForEdit(meetings) {
      return (meetings || [])
        .map((meeting) => ({
          day: meeting.day,
          start: meeting.start,
          end: meeting.end,
          startInput: atlasTokenToTimeInput(meeting.start),
          endInput: atlasTokenToTimeInput(meeting.end),
        }))
        .filter((meeting) => COURSE_DAYS.some((day) => day.key === meeting.day));
    }

    function buildMeetingRowHtml(meeting = {}) {
      const day = COURSE_DAYS.some((option) => option.key === meeting.day) ? meeting.day : COURSE_DAYS[0].key;
      return `
        <div class="courses-meeting-row">
          <select data-meeting-day aria-label="Meeting day">
            ${COURSE_DAYS.map((option) => `<option value="${escapeHtml(option.key)}" ${option.key === day ? "selected" : ""}>${escapeHtml(option.key)}</option>`).join("")}
          </select>
          <input type="time" data-meeting-start aria-label="Meeting start time" value="${escapeHtml(meeting.startInput || "09:00")}" min="06:00" max="23:59" />
          <input type="time" data-meeting-end aria-label="Meeting end time" value="${escapeHtml(meeting.endInput || "09:50")}" min="06:00" max="23:59" />
          <button type="button" class="courses-meeting-remove" data-remove-meeting aria-label="Remove meeting">
            <span class="material-symbols-outlined" aria-hidden="true">close</span>
          </button>
        </div>
      `;
    }

    function atlasTokenToTimeInput(token) {
      const minutes = parseAtlasTimeToken(token);
      if (minutes === null) return "";
      const hour = Math.floor(minutes / 60);
      const minute = minutes % 60;
      return `${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}`;
    }

    function timeInputToAtlasToken(value) {
      const minutes = parseTimeInput(value);
      if (minutes === null) return "";
      const hour = Math.floor(minutes / 60);
      const minute = minutes % 60;
      return `${String(hour).padStart(2, "0")}${String(minute).padStart(2, "0")}`;
    }

    function detailRow(label, value) {
      return `<div class="courses-detail-row"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value || "N/A")}</strong></div>`;
    }

    function formatInstructors(section) {
      const instructors = Array.isArray(section?.instructors) ? section.instructors : [];
      const names = instructors
        .map((instructor) => {
          if (typeof instructor === "string") return instructor;
          const email = instructor?.email ? ` (${instructor.email})` : "";
          return instructor?.name ? `${instructor.name}${email}` : "";
        })
        .filter(Boolean);
      return names.length ? names.join(", ") : section?.instructor || "TBA";
    }

    function formatRequirement(section) {
      const value = section?.requirements || section?.requirement_designation || section?.requirement || section?.ger;
      if (Array.isArray(value)) return value.filter(Boolean).join(", ") || "N/A";
      return value || "N/A";
    }

    function formatCampus(section) {
      return section?.campus || section?.campus_description || "Atlanta";
    }

    return {
      cssEscape,
      getCourseColor,
      buildMeetingRowHtml,
      renderPanel,
      renderTermSelect,
      syncFilterControls,
      timeInputToAtlasToken,
    };
  }

  window.APStudyCoursesPanel = { create: createCoursePanel };
})();
