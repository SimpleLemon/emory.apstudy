(function () {
    const {
        TILE_META,
        escapeHtml,
        escapeAttr,
        formatDateTime,
        formatRelative,
        formatBytes,
        normalizeTileSize,
        normalizeCalendarView,
        normalizeTaskListIds,
        tileTitle,
        groupEventsByDate,
        localDateKey,
    } = window.APStudyDashboardUtils;

    function tileShell(tileType, size, layoutItem, bodyHtml) {
        const meta = TILE_META[tileType];
        const view = layoutItem?.view;
        const safeSize = normalizeTileSize(tileType, size);
        const safeView = normalizeCalendarView(tileType, view);
        const title = String(layoutItem?.title || tileTitle(tileType, safeView)).trim();
        const instanceId = String(layoutItem?.instance_id || `legacy-${tileType}`);
        const density = layoutItem?.density === "compact" ? "compact" : "comfortable";
        return `
            <article class="dashboard-tile" data-tile-id="${escapeAttr(instanceId)}" data-tile-type="${escapeHtml(tileType)}" data-tile-size="${escapeHtml(safeSize)}" data-density="${density}" ${tileType === "calendar" ? `data-calendar-view="${escapeHtml(safeView)}"` : ""} aria-label="${escapeAttr(title)} dashboard tile">
                <div class="dashboard-tile-inner">
                    <header class="dashboard-tile-head">
                        <div class="dashboard-tile-title-row">
                            <span class="dashboard-tile-icon">
                                <span class="material-symbols-outlined" aria-hidden="true">${escapeHtml(meta.icon)}</span>
                            </span>
                            <div>
                                <h2>${escapeHtml(title)}</h2>
                            </div>
                        </div>
                        <div class="dashboard-tile-actions">
                            <a class="dashboard-tile-link" href="${escapeHtml(meta.href)}" aria-label="Open ${escapeHtml(title)}">
                                <span class="material-symbols-outlined" aria-hidden="true">open_in_new</span>
                            </a>
                        </div>
                    </header>
                    ${bodyHtml}
                </div>
            </article>
        `;
    }

    function emptyState(tileId) {
        const meta = TILE_META[tileId];
        return `
            <div class="dashboard-empty">
                <span class="material-symbols-outlined" aria-hidden="true">${escapeHtml(meta.emptyIcon)}</span>
                <p>${escapeHtml(meta.emptyText)}</p>
                <a class="dashboard-empty-link" href="${escapeHtml(meta.href)}">${escapeHtml(meta.emptyCta)}</a>
            </div>
        `;
    }

    function checklistHtml(checklist) {
        const percent = checklist.total ? Math.round((checklist.completed / checklist.total) * 100) : 0;
        return `
            <div class="dashboard-checklist-head">
                <div>
                    <h2>${checklist.complete ? "Academic setup complete" : "Complete your academic setup"}</h2>
                    <p>${checklist.complete ? "Everything needed for the dashboard is connected." : "These items update automatically from your account data."}</p>
                </div>
                <button class="dashboard-checklist-hide" type="button" aria-label="Hide checklist" title="Hide">
                    <span class="material-symbols-outlined" aria-hidden="true">close</span>
                </button>
            </div>
            <div class="dashboard-checklist-progress">
                <div class="dashboard-progress-track" aria-hidden="true">
                    <div class="dashboard-progress-fill" style="width:${percent}%"></div>
                </div>
                <span class="dashboard-progress-label">${checklist.completed}/${checklist.total}</span>
            </div>
            <div class="dashboard-checklist-items">
                ${(checklist.items || []).map((item) => `
                    <a class="dashboard-checklist-item ${item.complete ? "is-complete" : ""}" href="${escapeHtml(item.href || "/settings#account")}">
                        <span class="dashboard-check-icon">
                            <span class="material-symbols-outlined" aria-hidden="true">${item.complete ? "check" : "radio_button_unchecked"}</span>
                        </span>
                        <span>${escapeHtml(item.label)}</span>
                    </a>
                `).join("")}
            </div>
        `;
    }

    function renderTile(tileType, size, data, layoutItem = {}) {
        if (tileType === "calendar") return tileShell(tileType, size, layoutItem, renderCalendar(data, layoutItem));
        if (tileType === "tasks") return tileShell(tileType, size, layoutItem, renderTasks(data, layoutItem));
        if (tileType === "files") return tileShell(tileType, size, layoutItem, renderFiles(data, layoutItem));
        if (tileType === "notes") return tileShell(tileType, size, layoutItem, renderNotes(data, layoutItem));
        if (tileType === "messages") return tileShell(tileType, size, layoutItem, renderMessages(data, layoutItem));
        if (tileType === "courses") return tileShell(tileType, size, layoutItem, renderCourses(data, layoutItem));
        return "";
    }

    function renderCalendar(data, layoutItem = {}) {
        const safeView = normalizeCalendarView("calendar", layoutItem.view);
        if (safeView === "week") return renderCalendarGrid(data, "week");
        if (safeView === "upcoming") return renderUpcomingCalendar(data, layoutItem);
        return renderCalendarGrid(data, "month");
    }

    function renderCalendarGrid(data, view) {
        const events = view === "week"
            ? (Array.isArray(data.week_events) ? data.week_events : [])
            : (Array.isArray(data.events) ? data.events : []);
        const month = String(data.month || new Date().toISOString().slice(0, 7));
        const [yearValue, monthValue] = month.split("-").map((part) => Number(part));
        const year = Number.isFinite(yearValue) ? yearValue : new Date().getFullYear();
        const monthIndex = Number.isFinite(monthValue) ? monthValue - 1 : new Date().getMonth();
        const first = view === "week"
            ? new Date(`${data.week_start || localDateKey(new Date())}T00:00:00`)
            : new Date(year, monthIndex, 1);
        const start = new Date(first);
        if (view !== "week") start.setDate(first.getDate() - first.getDay());
        const todayKey = localDateKey(new Date());
        const eventMap = groupEventsByDate(events);
        const cells = [];
        const cellCount = view === "week" ? 7 : 42;
        for (let index = 0; index < cellCount; index += 1) {
            const day = new Date(start);
            day.setDate(start.getDate() + index);
            const key = localDateKey(day);
            const dayEvents = eventMap.get(key) || [];
            const eventTitles = dayEvents.map((event) => event.title || "Untitled event");
            const dateLabel = day.toLocaleDateString(undefined, {
                weekday: "long",
                month: "long",
                day: "numeric",
                year: "numeric",
            });
            const dayLabel = eventTitles.length
                ? `${dateLabel}. Events: ${eventTitles.join(", ")}`
                : dateLabel;
            const markerHtml = view === "week"
                ? dayEvents.slice(0, 5).map((event) => `<span class="dashboard-week-event-bar" style="--marker-color:${escapeHtml(event.color || "#6366f1")}"></span>`).join("")
                : dayEvents.slice(0, 5).map((event) => `<span class="dashboard-marker" style="--marker-color:${escapeHtml(event.color || "#6366f1")}"></span>`).join("");
            cells.push(`
                <button class="dashboard-day ${day.getMonth() === monthIndex ? "" : "is-muted"} ${key === todayKey ? "is-today" : ""}" type="button" data-date="${escapeHtml(key)}" aria-label="${escapeAttr(dayLabel)}" ${dayEvents.length ? "" : "disabled"}>
                    <span class="dashboard-day-number">${escapeHtml(formatMonthGridDayLabel(day))}</span>
                    <span class="dashboard-day-markers">
                        ${markerHtml}
                    </span>
                </button>
            `);
        }
        return `
            <div class="dashboard-calendar" data-calendar-events="${escapeAttr(JSON.stringify(events))}" data-calendar-view="${escapeHtml(view)}">
                <div class="dashboard-calendar-weekdays" aria-hidden="true">
                    ${["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].map((day) => `<span>${day}</span>`).join("")}
                </div>
                <div class="dashboard-calendar-grid">${cells.join("")}</div>
            </div>
        `;
    }

    function renderUpcomingCalendar(data, layoutItem = {}) {
        const upcomingDays = [7, 14, 30].includes(Number(layoutItem.upcoming_days)) ? Number(layoutItem.upcoming_days) : 7;
        const itemLimit = [3, 5, 8].includes(Number(layoutItem.item_limit)) ? Number(layoutItem.item_limit) : 5;
        const rangeEnd = Date.now() + upcomingDays * 24 * 60 * 60 * 1000;
        const items = (Array.isArray(data.upcoming_events) ? data.upcoming_events : [])
            .filter((event) => {
                const start = new Date(event.start || `${event.date}T00:00:00`).getTime();
                return Number.isNaN(start) || start <= rangeEnd;
            })
            .slice(0, itemLimit);
        if (!items.length) return emptyState("calendar");
        return `
            <div class="dashboard-calendar-upcoming">
                ${items.map((event) => `
                    <button class="dashboard-list-item dashboard-calendar-upcoming-item" type="button" data-date="${escapeHtml(event.date || String(event.start || "").slice(0, 10))}" data-calendar-event="${escapeAttr(JSON.stringify(event))}">
                        <div class="dashboard-list-main">
                            <span class="dashboard-list-title">${escapeHtml(event.title || "Untitled event")}</span>
                            <span class="dashboard-list-meta">${escapeHtml(formatDateTime(event.start) || event.date || "Upcoming")}</span>
                        </div>
                        <span class="dashboard-marker" style="--marker-color:${escapeHtml(event.color || "#6366f1")}" aria-hidden="true"></span>
                    </button>
                `).join("")}
            </div>
        `;
    }

    function formatMonthGridDayLabel(date) {
        if (date.getDate() !== 1) return String(date.getDate());
        return date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
    }

    function renderTasks(data, layoutItem = {}) {
        const taskLists = Array.isArray(data.lists) ? data.lists : [];
        const selectedTaskListIds = normalizeTaskListIds(layoutItem.task_list_ids, taskLists.map((list) => list.id));
        const sourceItems = Array.isArray(data.all_items) ? data.all_items : (Array.isArray(data.items) ? data.items : []);
        const deadlineDays = Number(layoutItem.deadline_days) === 7 ? 7 : 30;
        const deadlineEnd = Date.now() + deadlineDays * 24 * 60 * 60 * 1000;
        const priorities = Array.isArray(layoutItem.priorities) && layoutItem.priorities.length
            ? layoutItem.priorities
            : ["high", "medium", "low", "none"];
        const itemLimit = [3, 5, 8].includes(Number(layoutItem.item_limit)) ? Number(layoutItem.item_limit) : 5;
        const items = sourceItems.filter((task) => {
            if (selectedTaskListIds.length && !selectedTaskListIds.includes(task.list_id)) return false;
            if (!priorities.includes(String(task.priority || "none").toLowerCase())) return false;
            if (layoutItem.starred_only && !task.starred) return false;
            if (!task.deadline_at) return layoutItem.include_undated !== false;
            const deadline = new Date(task.deadline_at).getTime();
            if (Number.isNaN(deadline)) return false;
            if (deadline < Date.now()) return layoutItem.include_overdue !== false;
            return deadline <= deadlineEnd;
        }).slice(0, itemLimit);
        if (!items.length) return emptyState("tasks");
        return `
            <div class="dashboard-list">
                ${items.map((task) => `
                    <div class="dashboard-list-item">
                        <div class="dashboard-list-main">
                            <span class="dashboard-list-title">${escapeHtml(task.title)}</span>
                            <span class="dashboard-list-meta">${escapeHtml(formatDateTime(task.deadline_at) || "No deadline")}</span>
                        </div>
                        <span class="dashboard-pill ${task.overdue ? "is-overdue" : ""}">${task.overdue ? "Overdue" : escapeHtml(task.priority || "Task")}</span>
                    </div>
                `).join("")}
            </div>
        `;
    }

    function limitedItems(data, layoutItem) {
        const limit = [3, 5, 8].includes(Number(layoutItem?.item_limit)) ? Number(layoutItem.item_limit) : 5;
        return (Array.isArray(data.items) ? data.items : []).slice(0, limit);
    }

    function renderFiles(data, layoutItem) {
        const items = limitedItems(data, layoutItem);
        if (!items.length) return emptyState("files");
        return `
            <div class="dashboard-list">
                ${items.map((file) => `
                    <a class="dashboard-list-item" href="${escapeHtml(file.href || "/files")}">
                        <div class="dashboard-list-main">
                            <span class="dashboard-list-title">${escapeHtml(file.name)}</span>
                            <span class="dashboard-list-meta">${escapeHtml(formatRelative(file.updated_at))}</span>
                        </div>
                        <span class="dashboard-pill">${escapeHtml(formatBytes(file.size_bytes))}</span>
                    </a>
                `).join("")}
            </div>
        `;
    }

    function renderNotes(data, layoutItem) {
        const items = limitedItems(data, layoutItem);
        if (!items.length) return emptyState("notes");
        return `
            <div class="dashboard-list">
                ${items.map((note) => `
                    <a class="dashboard-list-item" href="${escapeHtml(note.href || "/notes")}">
                        <div class="dashboard-list-main">
                            <span class="dashboard-list-title">${escapeHtml(note.title)}</span>
                            <span class="dashboard-list-meta">${escapeHtml(formatRelative(note.updated_at))}</span>
                        </div>
                        <span class="dashboard-pill">Note</span>
                    </a>
                `).join("")}
            </div>
        `;
    }

    function renderMessages(data, layoutItem) {
        const items = limitedItems(data, layoutItem);
        if (!items.length) return emptyState("messages");
        return `
            <div class="dashboard-list">
                ${items.map((room) => `
                    <a class="dashboard-list-item" href="${escapeHtml(room.href || "/chat")}">
                        <div class="dashboard-list-main">
                            <span class="dashboard-list-title">${escapeHtml(room.label)}</span>
                            <span class="dashboard-list-meta">${escapeHtml(formatRelative(room.last_activity_at))}</span>
                        </div>
                        <span class="dashboard-pill">${room.type === "thread" ? "DM" : "Channel"}</span>
                    </a>
                `).join("")}
            </div>
        `;
    }

    function renderCourses(data, layoutItem) {
        const items = limitedItems(data, layoutItem);
        if (!items.length) return emptyState("courses");
        return `
            <div class="dashboard-courses-list">
                ${items.map((course) => `
                    <div class="dashboard-course">
                        <strong>${escapeHtml(course.code)}</strong>
                        <span>${escapeHtml(course.name || course.term || "Saved course")}</span>
                        <span>${escapeHtml([course.term, course.section].filter(Boolean).join(" · "))}</span>
                    </div>
                `).join("")}
            </div>
        `;
    }

    window.APStudyDashboardRenderers = {
        checklistHtml,
        emptyState,
        renderTile,
    };
})();
