(function () {
    function createCalendarRenderShell({
        state,
        constants,
        callbacks,
    }) {
        const {
            compactCalendarQuery,
            hourHeightPx,
        } = constants;
        const {
            buildMobileCalendarAgendaHtml,
            buildUpcomingAgendaHtml,
            buildMonthViewHtml,
            buildWeekViewHtml,
            getStartOfWeek,
            hideCalendarHoverCard,
            renderAssignments,
            renderCalendarMenu,
            renderCoursesModal,
        } = callbacks;

        function render() {
            updateHeader();
            updateViewToggleButtons();
            renderCalendarMenu();
            renderCalendarView();
            renderAssignments();
            if (!state.public.readOnly) {
                renderCoursesModal();
            }
        }

        function updateHeader() {
            const title = document.getElementById("calendar-title");
            const subtitle = document.getElementById("calendar-subtitle");
            if (!title || !subtitle) return;
            const monthLabel = state.anchorDate.toLocaleDateString(undefined, { month: "long", year: "numeric" });
            if (state.public.readOnly) {
                const periodLabel = state.view === "upcoming"
                    ? "Next 30 days"
                    : state.view === "month"
                    ? monthLabel
                    : (() => {
                        const start = getStartOfWeek(state.anchorDate);
                        const end = new Date(start);
                        end.setDate(end.getDate() + 6);
                        return `Week of ${start.toLocaleDateString(undefined, { month: "short", day: "numeric" })} - ${end.toLocaleDateString(undefined, { month: "short", day: "numeric" })}`;
                    })();
                title.textContent = state.public.title || "Shared Calendar";
                subtitle.textContent = `${periodLabel} · ${state.public.rangeLabel || "Shared dates"}`;
                return;
            }
            if (state.view === "upcoming") {
                title.textContent = "Upcoming events";
                subtitle.textContent = "Your next 30 days, organized by date";
                return;
            }
            if (state.view === "month") {
                title.textContent = monthLabel;
                subtitle.textContent = state.feedConfigured
                    ? "Monthly view from your saved iCal feed"
                    : "Monthly view (no .ics feed connected yet)";
                return;
            }
            const start = getStartOfWeek(state.anchorDate);
            const end = new Date(start);
            end.setDate(end.getDate() + 6);
            title.textContent = `Week of ${start.toLocaleDateString(undefined, { month: "short", day: "numeric" })} - ${end.toLocaleDateString(undefined, { month: "short", day: "numeric" })}`;
            subtitle.textContent = state.feedConfigured
                ? `Weekly view (${monthLabel}) from your saved iCal feed`
                : `Weekly view (${monthLabel}) with no .ics feed connected`;
        }

        function updateViewToggleButtons() {
            const weekBtn = document.getElementById("calendar-view-week");
            const monthBtn = document.getElementById("calendar-view-month");
            const upcomingBtn = document.getElementById("calendar-view-upcoming");
            if (!weekBtn || !monthBtn) return;
            const active = "inline-flex h-8 min-w-[84px] items-center justify-center rounded-md px-3 text-sm font-medium text-on-primary shadow-sm transition-colors";
            const inactive = "inline-flex h-8 min-w-[84px] items-center justify-center rounded-md bg-transparent px-3 text-sm font-medium text-on-surface-variant transition-colors hover:bg-surface-container-high hover:text-on-surface";
            weekBtn.className = state.view === "week" ? active : inactive;
            monthBtn.className = state.view === "month" ? active : inactive;
            if (upcomingBtn) upcomingBtn.className = state.view === "upcoming" ? active : inactive;
            weekBtn.setAttribute("aria-pressed", String(state.view === "week"));
            monthBtn.setAttribute("aria-pressed", String(state.view === "month"));
            upcomingBtn?.setAttribute("aria-pressed", String(state.view === "upcoming"));
            [weekBtn, monthBtn, upcomingBtn].filter(Boolean).forEach((button) => {
                button.style.background = button.getAttribute("aria-pressed") === "true" ? "var(--color-primary)" : "";
            });
            const periodControls = document.getElementById("calendar-period-controls");
            if (periodControls) periodControls.hidden = false;
            const periodDisabled = state.view === "upcoming";
            ["calendar-prev", "calendar-next"].forEach((id) => {
                const button = document.getElementById(id);
                if (!button) return;
                button.disabled = periodDisabled;
                button.setAttribute("aria-disabled", String(periodDisabled));
            });
            if (state.view !== "week") {
                state.ui.weeklyAutoScrollKey = null;
            }
        }

        function renderCalendarView() {
            const root = document.getElementById("calendar-view-root");
            if (!root) return;
            hideCalendarHoverCard();
            root.closest(".calendar-surface")?.classList.toggle("is-upcoming", state.view === "upcoming");
            if (state.loadingDashboard) {
                root.innerHTML = buildCalendarSkeletonHtml();
                return;
            }
            const compactCalendar = isCompactCalendarViewport();
            root.innerHTML = state.view === "upcoming"
                ? buildUpcomingAgendaHtml()
                : compactCalendar ? buildMobileCalendarAgendaHtml() : (state.view === "month" ? buildMonthViewHtml() : buildWeekViewHtml());
            if (state.view === "week" && !compactCalendar) {
                applyWeekAutoScroll();
            }
        }

        function buildCalendarSkeletonHtml() {
            const block = (className) => window.APStudySkeleton?.block?.(className)
                || `<div data-slot="skeleton" class="bg-muted rounded-md animate-pulse ${className}"></div>`;
            if (compactCalendarQuery.matches) {
                return `
                    <div class="calendar-skeleton calendar-mobile-skeleton apstudy-skeleton" role="status" aria-live="polite" aria-busy="true">
                        <span class="sr-only">Loading calendar...</span>
                        <div class="contents" aria-hidden="true">
                            ${Array.from({ length: 4 }, (_, index) => `
                                <section class="calendar-mobile-skeleton-day">
                                    ${block("h-4 w-24")}
                                    <div class="calendar-mobile-skeleton-event">${block("size-8 rounded-md")} <div class="flex flex-1 flex-col gap-2">${block(index % 2 ? "h-3 w-4/5" : "h-3 w-full")} ${block("h-3 w-2/5")}</div></div>
                                    <div class="calendar-mobile-skeleton-event">${block("size-8 rounded-md")} <div class="flex flex-1 flex-col gap-2">${block("h-3 w-3/4")} ${block("h-3 w-1/3")}</div></div>
                                </section>
                            `).join("")}
                        </div>
                    </div>
                `;
            }
            const dayHeaders = Array.from({ length: 7 }, (_, index) => `
                <div class="calendar-skeleton-week-header-cell">
                    ${block("h-3 w-8")}
                    ${block(index === 2 ? "h-7 w-7 rounded-full" : "h-5 w-8")}
                </div>
            `).join("");
            const dayColumns = Array.from({ length: 7 }, (_, index) => `
                <div class="calendar-skeleton-day-column">
                    <div class="calendar-skeleton-event calendar-skeleton-event-${index % 3}">${block(index % 2 ? "h-4 w-3/4" : "h-4 w-full")}</div>
                    ${index % 2 === 0 ? `<div class="calendar-skeleton-event calendar-skeleton-event-late">${block("h-4 w-4/5")}</div>` : ""}
                </div>
            `).join("");
            return `
                <div class="calendar-skeleton calendar-week-skeleton apstudy-skeleton" role="status" aria-live="polite" aria-busy="true">
                    <span class="sr-only">Loading calendar...</span>
                    <div class="contents" aria-hidden="true">
                        <div class="calendar-skeleton-week-frame">
                            <div class="calendar-skeleton-week-header">
                                <div class="calendar-skeleton-time-header"></div>
                                ${dayHeaders}
                            </div>
                            <div class="calendar-skeleton-week-body">
                                <div class="calendar-skeleton-time-axis">
                                    ${Array.from({ length: 12 }, () => `<div>${block("h-3 w-8")}</div>`).join("")}
                                </div>
                                ${dayColumns}
                            </div>
                        </div>
                    </div>
                </div>
            `;
        }

        function isCompactCalendarViewport() {
            return compactCalendarQuery.matches;
        }

        function getWeekKeyFromAnchor(anchorDate) {
            const weekStart = getStartOfWeek(anchorDate);
            return `${weekStart.getFullYear()}-${String(weekStart.getMonth() + 1).padStart(2, "0")}-${String(weekStart.getDate()).padStart(2, "0")}`;
        }

        function applyWeekAutoScroll() {
            const scroller = document.getElementById("calendar-week-time-scroller");
            if (!scroller) return;
            const scrollbarWidth = scroller.offsetWidth - scroller.clientWidth;
            const calendarContainer = scroller.closest(".rounded-2xl");
            if (calendarContainer) {
                calendarContainer.style.setProperty("--scrollbar-offset", `${scrollbarWidth}px`);
            }
            const weekKey = getWeekKeyFromAnchor(state.anchorDate);
            if (state.ui.weeklyAutoScrollKey === weekKey) return;
            const targetMinutes = (7 * 60) + 30;
            const targetScrollTop = Math.round((targetMinutes / 60) * hourHeightPx);
            scroller.scrollTop = targetScrollTop;
            state.ui.weeklyAutoScrollKey = weekKey;
        }

        return {
            isCompactCalendarViewport,
            render,
            renderCalendarView,
        };
    }

    window.APStudyCalendarRenderShell = { createCalendarRenderShell };
})();
