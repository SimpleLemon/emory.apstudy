(function () {
    function createCalendarControls({
        root = document,
        lifecycle = null,
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

        const doc = root.ownerDocument || document;
        const view = doc.defaultView || window;
        const session = view.sessionStorage || null;
        const colorSchemeQuery = view.matchMedia?.("(prefers-color-scheme: dark)");
        const ElementConstructor = view.Element || globalThis.Element;
        const query = (selector) => root?.querySelector?.(selector);
        const listen = (target, type, handler, options) => lifecycle?.addEventListener
            ? lifecycle.addEventListener(target, type, handler, options)
            : (target?.addEventListener(type, handler, options), () => target?.removeEventListener(type, handler, options));

        function wireControls() {
            const refreshThemeDependentColors = () => {
                renderCalendarView();
                renderAssignments();
            };
            listen(doc, "apstudy-theme-change", refreshThemeDependentColors);
            listen(colorSchemeQuery, "change", () => {
                if (doc.documentElement.dataset.theme === "system-match") {
                    refreshThemeDependentColors();
                }
            });
            let lastCompactCalendar = isCompactCalendarViewport();
            listen(view, "resize", () => {
                const nextCompactCalendar = isCompactCalendarViewport();
                if (nextCompactCalendar === lastCompactCalendar) return;
                lastCompactCalendar = nextCompactCalendar;
                renderCalendarView();
            });
            if (!state.public.readOnly && session?.getItem("openCoursesPanelOnLoad") === "true") {
                session.removeItem("openCoursesPanelOnLoad");
                const schedule = lifecycle?.setTimeout || view.setTimeout.bind(view);
                schedule(() => {
                    if (!state.courses.modalOpen) {
                        openCoursesModal(null);
                    }
                }, 100);
            }
            if (!state.public.readOnly) {
                listen(doc, "profile-my-courses-click", (event) => {
                    if (state.courses.modalOpen) {
                        closeCoursesModal();
                        return;
                    }
                    openCoursesModal(event.detail?.trigger || doc.activeElement);
                });
                listen(query("#calendar-courses"), "click", (event) => {
                    openCoursesModal(event.currentTarget);
                });
            }
            listen(query("#calendar-view-week"), "click", () => {
                state.view = "week";
                render();
                void ensureEventsForRange(getBufferedRange(getCurrentRenderRange()));
            });
            listen(query("#calendar-view-month"), "click", () => {
                state.view = "month";
                render();
                void ensureEventsForRange(getBufferedRange(getCurrentRenderRange()));
            });
            listen(query("#calendar-view-upcoming"), "click", () => {
                state.view = "upcoming";
                state.anchorDate = new Date();
                render();
                void ensureEventsForRange(getBufferedRange(getCurrentRenderRange()));
            });
            listen(query("#calendar-prev"), "click", () => {
                state.anchorDate = shiftAnchorDate(-1);
                render();
                void ensureEventsForRange(getBufferedRange(getCurrentRenderRange()));
            });
            listen(query("#calendar-next"), "click", () => {
                state.anchorDate = shiftAnchorDate(1);
                render();
                void ensureEventsForRange(getBufferedRange(getCurrentRenderRange()));
            });
            listen(query("#calendar-refresh"), "click", () => {
                void runManualRefresh(getBufferedRange(getCurrentRenderRange()));
            });
            listen(query("#calendar-share"), "click", () => {
                openCalendarShareModal();
            });
            listen(query("#calendar-toggle-menu"), "click", (event) => {
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
            listen(query("#calendar-menu"), "change", (event) => {
                const checkbox = event.target.closest(".js-calendar-checkbox");
                if (!checkbox) return;
                event.stopPropagation();
                const calendarName = checkbox.getAttribute("data-calendar-name");
                if (!calendarName) return;
                toggleCalendarVisibility(calendarName);
                state.ui.calendarMenuOpen = true;
                renderCalendarMenu();
            });
            listen(query("#calendar-menu"), "click", (event) => {
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
            listen(doc, "pointerdown", (event) => {
                const popoverRoot = query("#calendar-popover-root");
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
            listen(view, "resize", () => {
                callbacks.positionCalendarContextMenu();
            });
            listen(view, "scroll", () => {
                callbacks.positionCalendarContextMenu();
                positionCalendarHoverCard();
            }, true);
            listen(view, "resize", () => {
                positionCalendarHoverCard();
            });
            wireCalendarHoverCard();
            listen(root, "click", (event) => {
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
            listen(root, "input", (event) => {
                const searchInput = event.target.closest("#courses-search-input");
                if (!searchInput) return;
                state.courses.searchInput = searchInput.value || "";
            });
            listen(root, "keydown", (event) => {
                if (event.key !== "Enter") return;
                const searchInput = event.target.closest("#courses-search-input");
                if (!searchInput) return;
                event.preventDefault();
                submitCoursesSearch();
            });
            listen(root, "change", (event) => {
                const termSelect = event.target.closest("#courses-term-select");
                if (!termSelect) return;
                state.courses.termFilter = termSelect.value || "";
                applyCourseFilters();
                writeCourseFiltersToUrl();
                renderCoursesModal();
            });
            listen(view, "keydown", (event) => {
                if (event.key === "Escape" && state.courses.modalOpen) {
                    closeCoursesModal();
                }
                if (event.key === "Escape") {
                    closeAllCalendarPopups();
                }
            });
            listen(view, "popstate", () => {
                applyCoursesFiltersFromUrl();
                state.courses.searchInput = state.courses.searchQuery;
                applyCourseFilters();
                if (state.courses.modalOpen) {
                    renderCoursesModal();
                }
            });
        }

        function wireCalendarHoverCard() {
            const viewRoot = query("#calendar-view-root");
            if (!viewRoot) return;
            listen(viewRoot, "pointerover", (event) => {
                if (event.pointerType === "touch") return;
                const eventEl = getCalendarEventElement(event.target);
                if (!eventEl) return;
                if (event.relatedTarget && eventEl.contains(event.relatedTarget)) return;
                showCalendarHoverCard(eventEl);
            });
            listen(viewRoot, "pointerout", (event) => {
                const eventEl = getCalendarEventElement(event.target);
                if (!eventEl) return;
                const related = event.relatedTarget;
                if (related && (eventEl.contains(related) || state.ui.hoverCardEl?.contains(related))) return;
                scheduleCalendarHoverCardHide();
            });
            listen(viewRoot, "focusin", (event) => {
                const eventEl = getCalendarEventElement(event.target);
                if (eventEl) showCalendarHoverCard(eventEl);
            });
            listen(viewRoot, "focusout", (event) => {
                const related = event.relatedTarget;
                if (related && state.ui.hoverCardEl?.contains(related)) return;
                scheduleCalendarHoverCardHide(80);
            });
        }

        function getCalendarEventElement(target) {
            if (ElementConstructor && !(target instanceof ElementConstructor)) return null;
            const viewRoot = query("#calendar-view-root");
            const eventEl = target.closest("[data-event-ref]");
            return eventEl && viewRoot?.contains(eventEl) ? eventEl : null;
        }

        function ensureCalendarHoverCard() {
            if (state.ui.hoverCardEl) return state.ui.hoverCardEl;
            const card = doc.createElement("div");
            card.className = "calendar-event-hover-card";
            card.setAttribute("role", "tooltip");
            card.hidden = true;
            listen(card, "pointerenter", () => {
                if (state.ui.hoverCardHideTimer) {
                    lifecycle?.clearTimeout?.(state.ui.hoverCardHideTimer);
                    state.ui.hoverCardHideTimer = null;
                }
            });
            listen(card, "pointerleave", () => scheduleCalendarHoverCardHide());
            root.appendChild(card);
            lifecycle?.trackNode?.(card);
            state.ui.hoverCardEl = card;
            return card;
        }

        function showCalendarHoverCard(anchorEl) {
            const eventRef = anchorEl.getAttribute("data-event-ref");
            const event = getCalendarEventByRef(eventRef);
            if (!event) return;
            if (state.ui.hoverCardHideTimer) {
                lifecycle?.clearTimeout?.(state.ui.hoverCardHideTimer);
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
            if (state.ui.hoverCardHideTimer) lifecycle?.clearTimeout?.(state.ui.hoverCardHideTimer);
            const schedule = lifecycle?.setTimeout || view.setTimeout.bind(view);
            state.ui.hoverCardHideTimer = schedule(() => {
                hideCalendarHoverCard();
            }, delayMs);
        }

        function hideCalendarHoverCard() {
            if (state.ui.hoverCardHideTimer) {
                lifecycle?.clearTimeout?.(state.ui.hoverCardHideTimer);
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
            if (!card || card.hidden || !anchorEl || !root.contains(anchorEl)) return;
            const margin = 12;
            const gap = 8;
            const wide = view.innerWidth >= 900;
            const preferredWidth = wide ? 420 : 320;
            const width = Math.max(260, Math.min(preferredWidth, view.innerWidth - margin * 2));
            card.style.width = `${width}px`;
            card.style.maxHeight = `${Math.max(180, view.innerHeight - margin * 2)}px`;
            card.style.left = "0px";
            card.style.top = "0px";
            const anchorRect = anchorEl.getBoundingClientRect();
            const cardRect = card.getBoundingClientRect();
            let left = anchorRect.left;
            let top = anchorRect.bottom + gap;
            if (wide && anchorRect.right + gap + cardRect.width + margin <= view.innerWidth) {
                left = anchorRect.right + gap;
                top = anchorRect.top + (anchorRect.height - cardRect.height) / 2;
            } else if (wide && anchorRect.left - gap - cardRect.width >= margin) {
                left = anchorRect.left - cardRect.width - gap;
                top = anchorRect.top + (anchorRect.height - cardRect.height) / 2;
            } else {
                const availableBelow = Math.max(0, view.innerHeight - anchorRect.bottom - gap - margin);
                const availableAbove = Math.max(0, anchorRect.top - gap - margin);
                const placeBelow = availableBelow >= availableAbove;
                const availableHeight = Math.max(120, placeBelow ? availableBelow : availableAbove);
                card.style.maxHeight = `${availableHeight}px`;
                const adjustedRect = card.getBoundingClientRect();
                if (left + cardRect.width + margin > view.innerWidth) {
                    left = view.innerWidth - adjustedRect.width - margin;
                }
                if (left < margin) left = margin;
                top = placeBelow ? anchorRect.bottom + gap : anchorRect.top - adjustedRect.height - gap;
            }
            const finalRect = card.getBoundingClientRect();
            top = Math.min(Math.max(top, margin), Math.max(margin, view.innerHeight - finalRect.height - margin));
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
