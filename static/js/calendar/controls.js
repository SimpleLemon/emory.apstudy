(function () {
    function createCalendarControls({
        state,
        callbacks,
        formatters,
    }) {
        const {
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
        } = callbacks;
        const {
            escapeHtml,
            formatAllDayRange,
            formatMultilineText,
            formatTimedEventRange,
        } = formatters;

        function wireControls() {
            const refreshThemeDependentColors = () => {
                renderCalendarView();
                renderAssignments();
            };
            document.addEventListener("apstudy-theme-change", refreshThemeDependentColors);
            window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
                if (document.documentElement.dataset.theme === "system-match") {
                    refreshThemeDependentColors();
                }
            });
            let lastCompactCalendar = isCompactCalendarViewport();
            window.addEventListener("resize", () => {
                const nextCompactCalendar = isCompactCalendarViewport();
                if (nextCompactCalendar === lastCompactCalendar) return;
                lastCompactCalendar = nextCompactCalendar;
                renderCalendarView();
            });
            if (!state.public.readOnly && sessionStorage.getItem("openCoursesPanelOnLoad") === "true") {
                sessionStorage.removeItem("openCoursesPanelOnLoad");
                setTimeout(() => {
                    if (!state.courses.modalOpen) {
                        openCoursesModal(null);
                    }
                }, 100);
            }
            if (!state.public.readOnly) {
                document.addEventListener("profile-my-courses-click", (event) => {
                    if (state.courses.modalOpen) {
                        closeCoursesModal();
                        return;
                    }
                    openCoursesModal(event.detail?.trigger || document.activeElement);
                });
                document.getElementById("calendar-courses")?.addEventListener("click", (event) => {
                    openCoursesModal(event.currentTarget);
                });
            }
            document.getElementById("calendar-view-week")?.addEventListener("click", () => {
                state.view = "week";
                render();
                void ensureEventsForRange(getBufferedRange(getCurrentRenderRange()));
            });
            document.getElementById("calendar-view-month")?.addEventListener("click", () => {
                state.view = "month";
                render();
                void ensureEventsForRange(getBufferedRange(getCurrentRenderRange()));
            });
            document.getElementById("calendar-view-upcoming")?.addEventListener("click", () => {
                state.view = "upcoming";
                state.anchorDate = new Date();
                render();
                void ensureEventsForRange(getBufferedRange(getCurrentRenderRange()));
            });
            document.getElementById("calendar-prev")?.addEventListener("click", () => {
                state.anchorDate = shiftAnchorDate(-1);
                render();
                void ensureEventsForRange(getBufferedRange(getCurrentRenderRange()));
            });
            document.getElementById("calendar-next")?.addEventListener("click", () => {
                state.anchorDate = shiftAnchorDate(1);
                render();
                void ensureEventsForRange(getBufferedRange(getCurrentRenderRange()));
            });
            document.getElementById("calendar-refresh")?.addEventListener("click", () => {
                void runManualRefresh(getBufferedRange(getCurrentRenderRange()));
            });
            document.getElementById("calendar-share")?.addEventListener("click", () => {
                openCalendarShareModal();
            });
            document.getElementById("calendar-toggle-menu")?.addEventListener("click", (event) => {
                event.stopPropagation();
                const opening = !state.ui.calendarMenuOpen;
                state.ui.calendarMenuOpen = opening;
                if (!state.ui.calendarMenuOpen) {
                    closeCalendarContextMenu();
                    closeRgbModal();
                } else {
                    void ensureCalendarPreferencesLoaded();
                    if (state.ui.preferenceDirty.size) {
                        scheduleCalendarPreferenceFlush(0);
                    }
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
                const addBtn = event.target.closest(".js-calendar-add-source");
                if (addBtn) {
                    event.preventDefault();
                    event.stopPropagation();
                    if (state.public.readOnly) return;
                    openCalendarSourceCreateModal();
                    return;
                }
                const moreBtn = event.target.closest(".js-calendar-more");
                if (!moreBtn) return;
                event.stopPropagation();
                if (state.public.readOnly) return;
                const calendarName = moreBtn.getAttribute("data-calendar-name");
                if (!calendarName) return;
                if (state.ui.contextMenuEl && state.ui.contextCalendarName === calendarName && state.ui.contextAnchorEl === moreBtn) {
                    closeCalendarContextMenu();
                    return;
                }
                openCalendarContextMenu(calendarName, moreBtn);
            });
            document.addEventListener("pointerdown", (event) => {
                const popoverRoot = document.getElementById("calendar-popover-root");
                const inRoot = popoverRoot ? popoverRoot.contains(event.target) : false;
                const inContext = state.ui.contextMenuEl ? state.ui.contextMenuEl.contains(event.target) : false;
                const inRgb = state.ui.rgbModalEl ? state.ui.rgbModalEl.contains(event.target) : false;
                const inSourceInfo = state.ui.sourceInfoModalEl ? state.ui.sourceInfoModalEl.contains(event.target) : false;
                const inSourceCreate = state.ui.sourceCreateModalEl ? state.ui.sourceCreateModalEl.contains(event.target) : false;
                const inShare = state.ui.shareModalEl ? state.ui.shareModalEl.contains(event.target) : false;
                if (!inRoot && !inContext && !inRgb && !inSourceInfo && !inSourceCreate && !inShare) {
                    closeAllCalendarPopups();
                }
            }, true);
            window.addEventListener("resize", () => {
                callbacks.positionCalendarContextMenu();
            });
            window.addEventListener("scroll", () => {
                callbacks.positionCalendarContextMenu();
                positionCalendarHoverCard();
            }, true);
            window.addEventListener("resize", () => {
                positionCalendarHoverCard();
            });
            wireCalendarHoverCard();
            document.addEventListener("click", (event) => {
                const upcomingToggle = event.target.closest(".js-upcoming-toggle");
                if (upcomingToggle) {
                    event.preventDefault();
                    const eventRef = upcomingToggle.getAttribute("data-event-ref");
                    if (!eventRef) return;
                    if (state.ui.expandedUpcomingRefs.has(eventRef)) {
                        state.ui.expandedUpcomingRefs.delete(eventRef);
                    } else {
                        state.ui.expandedUpcomingRefs.add(eventRef);
                    }
                    renderAssignments();
                    return;
                }
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
                if (event.key === "Escape") {
                    closeAllCalendarPopups();
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

        function wireCalendarHoverCard() {
            const root = document.getElementById("calendar-view-root");
            if (!root || root.dataset.hoverCardWired === "true") return;
            root.dataset.hoverCardWired = "true";
            root.addEventListener("pointerover", (event) => {
                if (event.pointerType === "touch") return;
                const eventEl = getCalendarEventElement(event.target);
                if (!eventEl) return;
                if (event.relatedTarget && eventEl.contains(event.relatedTarget)) return;
                showCalendarHoverCard(eventEl);
            });
            root.addEventListener("pointerout", (event) => {
                const eventEl = getCalendarEventElement(event.target);
                if (!eventEl) return;
                const related = event.relatedTarget;
                if (related && (eventEl.contains(related) || state.ui.hoverCardEl?.contains(related))) return;
                scheduleCalendarHoverCardHide();
            });
            root.addEventListener("focusin", (event) => {
                const eventEl = getCalendarEventElement(event.target);
                if (eventEl) showCalendarHoverCard(eventEl);
            });
            root.addEventListener("focusout", (event) => {
                const related = event.relatedTarget;
                if (related && state.ui.hoverCardEl?.contains(related)) return;
                scheduleCalendarHoverCardHide(80);
            });
        }

        function getCalendarEventElement(target) {
            if (!(target instanceof Element)) return null;
            const root = document.getElementById("calendar-view-root");
            const eventEl = target.closest("[data-event-ref]");
            return eventEl && root?.contains(eventEl) ? eventEl : null;
        }

        function ensureCalendarHoverCard() {
            if (state.ui.hoverCardEl) return state.ui.hoverCardEl;
            const card = document.createElement("div");
            card.className = "calendar-event-hover-card";
            card.setAttribute("role", "tooltip");
            card.hidden = true;
            card.addEventListener("pointerenter", () => {
                if (state.ui.hoverCardHideTimer) {
                    clearTimeout(state.ui.hoverCardHideTimer);
                    state.ui.hoverCardHideTimer = null;
                }
            });
            card.addEventListener("pointerleave", () => scheduleCalendarHoverCardHide());
            document.body.appendChild(card);
            state.ui.hoverCardEl = card;
            return card;
        }

        function showCalendarHoverCard(anchorEl) {
            const eventRef = anchorEl.getAttribute("data-event-ref");
            const event = getCalendarEventByRef(eventRef);
            if (!event) return;
            if (state.ui.hoverCardHideTimer) {
                clearTimeout(state.ui.hoverCardHideTimer);
                state.ui.hoverCardHideTimer = null;
            }
            const card = ensureCalendarHoverCard();
            state.ui.hoverCardAnchorEl = anchorEl;
            card.innerHTML = buildCalendarHoverCardHtml(event);
            card.hidden = false;
            card.style.visibility = "hidden";
            positionCalendarHoverCard();
            card.style.visibility = "";
        }

        function scheduleCalendarHoverCardHide(delayMs = 120) {
            if (state.ui.hoverCardHideTimer) clearTimeout(state.ui.hoverCardHideTimer);
            state.ui.hoverCardHideTimer = setTimeout(() => {
                hideCalendarHoverCard();
            }, delayMs);
        }

        function hideCalendarHoverCard() {
            if (state.ui.hoverCardHideTimer) {
                clearTimeout(state.ui.hoverCardHideTimer);
                state.ui.hoverCardHideTimer = null;
            }
            if (state.ui.hoverCardEl) {
                state.ui.hoverCardEl.hidden = true;
                state.ui.hoverCardEl.innerHTML = "";
            }
            state.ui.hoverCardAnchorEl = null;
        }

        function positionCalendarHoverCard() {
            const card = state.ui.hoverCardEl;
            const anchorEl = state.ui.hoverCardAnchorEl;
            if (!card || card.hidden || !anchorEl || !document.body.contains(anchorEl)) return;
            const margin = 12;
            const gap = 8;
            const wide = window.innerWidth >= 900;
            const preferredWidth = wide ? 420 : 320;
            const width = Math.max(260, Math.min(preferredWidth, window.innerWidth - margin * 2));
            card.style.width = `${width}px`;
            card.style.maxHeight = `${Math.max(180, window.innerHeight - margin * 2)}px`;
            card.style.left = "0px";
            card.style.top = "0px";
            const anchorRect = anchorEl.getBoundingClientRect();
            const cardRect = card.getBoundingClientRect();
            let left = anchorRect.left;
            let top = anchorRect.bottom + gap;
            if (wide && anchorRect.right + gap + cardRect.width + margin <= window.innerWidth) {
                left = anchorRect.right + gap;
                top = anchorRect.top + (anchorRect.height - cardRect.height) / 2;
            } else if (wide && anchorRect.left - gap - cardRect.width >= margin) {
                left = anchorRect.left - cardRect.width - gap;
                top = anchorRect.top + (anchorRect.height - cardRect.height) / 2;
            } else {
                const availableBelow = Math.max(0, window.innerHeight - anchorRect.bottom - gap - margin);
                const availableAbove = Math.max(0, anchorRect.top - gap - margin);
                const placeBelow = availableBelow >= availableAbove;
                const availableHeight = Math.max(120, placeBelow ? availableBelow : availableAbove);
                card.style.maxHeight = `${availableHeight}px`;
                const adjustedRect = card.getBoundingClientRect();
                if (left + cardRect.width + margin > window.innerWidth) {
                    left = window.innerWidth - adjustedRect.width - margin;
                }
                if (left < margin) left = margin;
                top = placeBelow ? anchorRect.bottom + gap : anchorRect.top - adjustedRect.height - gap;
            }
            const finalRect = card.getBoundingClientRect();
            top = Math.min(Math.max(top, margin), Math.max(margin, window.innerHeight - finalRect.height - margin));
            card.style.left = `${Math.round(left)}px`;
            card.style.top = `${Math.round(top)}px`;
        }

        function buildCalendarHoverCardHtml(event) {
            const timeDisplay = event.isAllDay ? formatAllDayRange(event) : formatTimedEventRange(event);
            const calendarLabel = getEventCalendarLabel(event);
            return `
                <div class="calendar-event-hover-title">${escapeHtml(event.title || "Untitled")}</div>
                <div class="calendar-event-hover-meta">${escapeHtml(timeDisplay)}</div>
                <div class="calendar-event-hover-calendar">${escapeHtml(calendarLabel)}</div>
                ${event.description ? `<div class="calendar-event-hover-description">${formatMultilineText(event.description)}</div>` : ""}
            `;
        }

        function closeCalendarDropdown() {
            state.ui.calendarMenuOpen = false;
            renderCalendarMenu();
        }

        function closeAllCalendarPopups() {
            closeCalendarContextMenu();
            closeRgbModal();
            closeSourceInfoModal();
            closeCalendarSourceCreateModal();
            closeCalendarShareModal();
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

        return {
            closeAllCalendarPopups,
            closeCalendarDropdown,
            hideCalendarHoverCard,
            positionCalendarHoverCard,
            wireControls,
        };
    }

    window.APStudyCalendarControls = { createCalendarControls };
})();
