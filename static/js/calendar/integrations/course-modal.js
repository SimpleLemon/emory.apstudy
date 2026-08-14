(function () {
    function createCourseModalRenderer({
        root = document,
        lifecycle = null,
        state,
        escapeHtml,
        formatTermLabel,
        formatMeetingTime,
        formatSectionDateRange,
        formatSectionTypeLabel,
    }) {
        const backgroundState = [];
        const doc = root.ownerDocument || root;
        const view = doc.defaultView || window;
        const mountNode = root.nodeType === 9 ? (root.body || root.documentElement) : root;
        const query = (selector) => root?.querySelector?.(selector);

        function setCoursesModalBackgroundInert(inert) {
            const overlay = query("#courses-modal-overlay");
            if (inert) {
                backgroundState.length = 0;
                for (const element of Array.from(mountNode?.children || [])) {
                    if (element === overlay) continue;
                    backgroundState.push({
                        element,
                        inert: Boolean(element.inert),
                        ariaHidden: element.getAttribute("aria-hidden"),
                    });
                    element.inert = true;
                    element.setAttribute("aria-hidden", "true");
                }
                return;
            }
            for (const snapshot of backgroundState.splice(0)) {
                if (!snapshot.element.isConnected) continue;
                snapshot.element.inert = snapshot.inert;
                if (snapshot.ariaHidden === null) snapshot.element.removeAttribute("aria-hidden");
                else snapshot.element.setAttribute("aria-hidden", snapshot.ariaHidden);
            }
        }

        function captureCoursesModalViewState(overlay) {
            const scroller = overlay.querySelector("#courses-modal-scroll");
            const activeEl = doc.activeElement;
            const activeInModal = activeEl && overlay.contains(activeEl) ? activeEl : null;
            const activeId = activeInModal?.id || null;
            return {
                scrollTop: scroller ? scroller.scrollTop : 0,
                activeId,
                activeFocusKey: activeInModal?.dataset.coursesFocusKey || null,
                selectionStart: activeId === "courses-search-input" ? activeInModal.selectionStart : null,
                selectionEnd: activeId === "courses-search-input" ? activeInModal.selectionEnd : null,
            };
        }

        function restoreCoursesModalViewState(overlay, snapshot) {
            if (!snapshot) return false;
            const scroller = overlay.querySelector("#courses-modal-scroll");
            if (scroller && Number.isFinite(snapshot.scrollTop)) {
                scroller.scrollTop = snapshot.scrollTop;
            }
            const activeEl = snapshot.activeId
                ? overlay.querySelector(`#${snapshot.activeId}`)
                : Array.from(overlay.querySelectorAll("[data-courses-focus-key]")).find((element) => (
                    element.dataset.coursesFocusKey === snapshot.activeFocusKey
                ));
            if (!activeEl) return false;
            activeEl.focus({ preventScroll: true });
            if (
                snapshot.activeId === "courses-search-input"
                && typeof snapshot.selectionStart === "number"
                && typeof snapshot.selectionEnd === "number"
                && typeof activeEl.setSelectionRange === "function"
            ) {
                activeEl.setSelectionRange(snapshot.selectionStart, snapshot.selectionEnd);
            }
            return true;
        }

        function trapCoursesModalFocus(event) {
            if (event.key !== "Tab" || !state.courses.modalOpen) return;
            const panel = query("#courses-modal-panel");
            if (!panel) return;
            const focusable = Array.from(panel.querySelectorAll(
                'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [href], [tabindex]:not([tabindex="-1"])'
            )).filter((element) => !element.hidden && element.getAttribute("aria-hidden") !== "true");
            if (!focusable.length) return;
            const first = focusable[0];
            const last = focusable[focusable.length - 1];
            const activeElement = doc.activeElement;
            if (!panel.contains(activeElement)) {
                event.preventDefault();
                first.focus();
            } else if (event.shiftKey && activeElement === first) {
                event.preventDefault();
                last.focus();
            } else if (!event.shiftKey && activeElement === last) {
                event.preventDefault();
                first.focus();
            }
        }

        function renderCoursesModal() {
            let overlay = query("#courses-modal-overlay");
            if (!overlay) {
                overlay = doc.createElement("div");
                overlay.id = "courses-modal-overlay";
                overlay.className = "calendar-modal-overlay fixed inset-0 hidden items-center justify-center bg-black/55 backdrop-blur-[1px] p-4";
                if (lifecycle?.addEventListener) lifecycle.addEventListener(overlay, "keydown", trapCoursesModalFocus);
                else overlay.addEventListener("keydown", trapCoursesModalFocus);
                mountNode.appendChild(overlay);
                lifecycle?.trackNode?.(overlay);
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
                ? ["Loading courses", "..."].join("")
                : state.courses.error
                    ? escapeHtml(state.courses.error)
                    : isSelectedOnly
                        ? `${resultCount.toLocaleString()} pinned in this session · ${selectedCount.toLocaleString()} selected`
                        : `${resultCount.toLocaleString()} sections · ${selectedCount.toLocaleString()} selected`;
            const content = state.courses.loading
                ? `<div class="py-12">${window.APStudyLoader.html("Loading full course index...", { sizePx: 54, textToneClass: "text-on-surface" })}</div>`
                : state.courses.error
                    ? `<div class="py-12 text-center text-sm text-error" role="alert">${escapeHtml(state.courses.error)}</div>`
                    : !state.courses.indexLoaded
                        ? `<div class="py-12">${window.APStudyLoader.html("Loading full course index...", { sizePx: 54, textToneClass: "text-on-surface" })}</div>`
                        : buildCourseCardsHtml();
            overlay.innerHTML = `
                <section id="courses-modal-panel" role="dialog" aria-modal="true" aria-labelledby="courses-modal-title" class="w-full max-w-[1200px] h-[88vh] rounded-3xl border border-outline-variant/30 bg-surface shadow-2xl shadow-black/30 flex flex-col overflow-hidden transition-all duration-200 ${state.courses.animateOnOpen ? "-translate-y-3 opacity-0" : "translate-y-0 opacity-100"}">
                    <header class="px-5 sm:px-7 pt-5 sm:pt-6 pb-4 border-b border-outline-variant/20 bg-surface-container-low">
                        <div class="flex items-center justify-between gap-3 mb-4">
                            <h2 id="courses-modal-title" class="text-2xl font-headline font-bold text-on-surface">Courses</h2>
                            <button id="courses-modal-close" type="button" class="h-9 w-9 rounded-full bg-surface-container text-on-surface-variant hover:bg-surface-container-high hover:text-on-surface transition-colors" aria-label="Close courses search">✕</button>
                        </div>
                        <div class="grid grid-cols-1 md:grid-cols-[1fr_220px] gap-3">
                            <label class="relative block">
                                <span class="material-symbols-outlined absolute left-3 top-1/2 -translate-y-1/2 text-on-surface-variant text-[20px]" aria-hidden="true">search</span>
                                <input id="courses-search-input" type="text" value="${escapeHtml(state.courses.searchInput)}" placeholder="${escapeHtml(searchPlaceholder)}" aria-label="Search courses" class="w-full h-11 rounded-xl border border-outline-variant/35 bg-surface px-10 pr-[7.25rem] text-sm text-on-surface placeholder:text-on-surface-variant focus:outline-none focus:ring-2 focus:ring-primary/30" />
                                <button id="courses-search-submit" type="button" class="absolute right-1.5 top-1/2 -translate-y-1/2 h-8 px-3 rounded-lg bg-primary text-on-primary text-xs font-semibold hover:bg-primary-container hover:text-on-primary-container disabled:opacity-60 disabled:cursor-not-allowed" ${state.courses.loading ? "disabled" : ""}>Search</button>
                            </label>
                            <select id="courses-term-select" aria-label="Filter by term" ${termDisabled ? "disabled" : ""} class="h-11 rounded-xl border border-outline-variant/35 bg-surface px-3 text-sm text-on-surface focus:outline-none focus:ring-2 focus:ring-primary/30 disabled:opacity-60 disabled:cursor-not-allowed">
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
            const restoredFocus = restoreCoursesModalViewState(overlay, viewState);
            if (!restoredFocus) {
                overlay.querySelector("#courses-search-input")?.focus({ preventScroll: true });
            }
            overlay.classList.add("transition-opacity", "duration-200");
            if (state.courses.animateOnOpen) {
                overlay.classList.add("opacity-0", "pointer-events-none");
                const runFrame = lifecycle?.requestAnimationFrame || view.requestAnimationFrame.bind(view);
                runFrame(() => {
                    overlay.classList.remove("opacity-0", "pointer-events-none");
                    const mountedPanel = query("#courses-modal-panel");
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
                    ? '<span class="ml-2 inline-flex items-center rounded-full px-2 py-0.5 text-xs font-semibold border bg-amber-500/15 text-amber-700 border-amber-500/30">Cancelled</span>'
                    : "";
                const meetingRows = (Array.isArray(section.meetings) ? section.meetings : []).map((meeting) => {
                    const label = `${meeting.day || "?"} ${formatMeetingTime(meeting.start)}-${formatMeetingTime(meeting.end)}`;
                    return `<li>${escapeHtml(label)}</li>`;
                }).join("");
                const sectionDateRange = formatSectionDateRange(section);
                const sectionLabel = `Section ${section.section_number || "?"}`;
                const addButtonText = selected ? "Remove" : "+";
                const addButtonClasses = selected
                    ? "bg-error-container text-on-error-container hover:bg-error/90"
                    : "bg-primary text-on-primary hover:bg-primary-container hover:text-on-primary-container";
                const addDisabled = section.is_cancelled ? "disabled" : "";
                return `
                    <article class="w-full max-w-[360px] rounded-2xl border border-outline-variant/20 bg-surface-container-low p-4 sm:p-5 flex flex-col gap-3">
                        <div class="flex items-start justify-between gap-3">
                            <div>
                                <div class="text-lg font-semibold text-on-surface">${escapeHtml(section.course_code || "Unknown")} (${escapeHtml(sectionLabel)})</div>
                                <div class="text-sm text-on-surface-variant">${escapeHtml(section.course_title || "Untitled Course")}</div>
                            </div>
                            <button type="button" class="js-course-info-toggle h-8 w-8 rounded-full bg-surface-container text-on-surface-variant hover:bg-surface-container-high hover:text-on-surface transition-colors" data-section-id="${escapeHtml(sectionId)}" data-courses-focus-key="info:${escapeHtml(sectionId)}" aria-label="Toggle section details">i</button>
                        </div>
                        <div class="flex items-center gap-2 text-xs flex-wrap">
                            <span class="inline-flex rounded-full border border-outline-variant/35 px-2.5 py-1 font-medium text-on-surface-variant">${escapeHtml(formatTermLabel(section.term))}</span>
                            <span class="inline-flex rounded-full border px-2.5 py-1 font-medium ${statusClasses}">${escapeHtml(section.enrollment_status || "Unknown")}</span>
                            ${cancelledText}
                        </div>
                        <div class="text-sm text-on-surface-variant flex flex-wrap gap-x-4 gap-y-1">
                            <div><span class="material-symbols-outlined text-[16px] align-[-3px] mr-1" aria-hidden="true">schedule</span>${escapeHtml(section.schedule_display || "TBA")}</div>
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
                            <button type="button" ${addDisabled} class="js-course-toggle min-w-[84px] h-9 rounded-full px-4 text-sm font-semibold transition-colors ${addButtonClasses} disabled:opacity-50 disabled:cursor-not-allowed" data-section-id="${escapeHtml(sectionId)}" data-courses-focus-key="toggle:${escapeHtml(sectionId)}" title="${selected ? "Remove from simulated calendar" : "Add to simulated calendar"}">${addButtonText}</button>
                        </div>
                    </article>
                `;
            }).join("");
            return `<div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4 justify-items-center">${cards}</div>`;
        }

        return { renderCoursesModal, setCoursesModalBackgroundInert };
    }

    window.APStudyCalendarCourseModal = { createCourseModalRenderer };
}());
