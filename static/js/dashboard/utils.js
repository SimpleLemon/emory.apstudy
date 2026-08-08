(function () {
    const { escapeHtml } = window.APStudyUIPrimitives;

    const TILE_META = {
        calendar: {
            title: "Monthly Calendar",
            icon: "calendar_today",
            href: "/calendar",
            emptyIcon: "event_busy",
            emptyText: "No events this month.",
            emptyCta: "Open calendar",
        },
        tasks: {
            title: "Upcoming Tasks",
            icon: "check_circle",
            href: "/tasks",
            emptyIcon: "done_all",
            emptyText: "Nothing is due soon.",
            emptyCta: "Open tasks",
        },
        files: {
            title: "Recent Files",
            icon: "folder",
            href: "/files",
            emptyIcon: "upload_file",
            emptyText: "No recent files yet.",
            emptyCta: "Open files",
        },
        notes: {
            title: "Recent Notes",
            icon: "article",
            href: "/notes",
            emptyIcon: "note_stack",
            emptyText: "No recent notes yet.",
            emptyCta: "Open notes",
        },
        messages: {
            title: "Messages",
            icon: "chat_bubble",
            href: "/chat",
            emptyIcon: "chat_bubble",
            emptyText: "No recent message activity.",
            emptyCta: "Open chat",
        },
        courses: {
            title: "Courses",
            icon: "school",
            href: "/courses",
            emptyIcon: "school",
            emptyText: "No saved courses yet.",
            emptyCta: "Open courses",
        },
    };

    const TILE_SIZE_RULES = {
        calendar: ["standard", "tall", "wide"],
        tasks: ["standard", "tall", "wide"],
        files: ["standard", "tall", "wide"],
        notes: ["standard", "tall", "wide"],
        messages: ["standard", "tall", "wide"],
        courses: ["standard", "wide"],
    };

    const DEFAULT_TILE_SIZES = {
        calendar: "standard",
        tasks: "standard",
        files: "standard",
        notes: "standard",
        messages: "standard",
        courses: "wide",
    };

    const TILE_SIZE_LABELS = {
        standard: "Standard",
        tall: "Tall",
        wide: "Wide",
    };

    const CALENDAR_VIEW_RULES = ["month", "week", "upcoming"];
    const DEFAULT_CALENDAR_VIEW = "month";
    const CALENDAR_VIEW_LABELS = {
        month: "Month",
        week: "Week",
        upcoming: "Upcoming",
    };
    const DUPLICATE_TILE_TYPES = ["calendar", "tasks"];
    const ITEM_LIMITS = [3, 5, 8];
    const DENSITIES = ["compact", "comfortable"];
    const CALENDAR_UPCOMING_DAYS = [7, 14, 30];
    const TASK_DEADLINE_DAYS = [7, 30];
    const TASK_PRIORITIES = ["high", "medium", "low", "none"];
    const MAX_TILES = 12;
    const MAX_DUPLICATE_TILES = 4;

    function escapeAttr(value) {
        return escapeHtml(value).replace(/"/g, "&quot;").replace(/'/g, "&#39;");
    }

    function fetchJson(url, options = {}) {
        return window.APStudyHttp.fetchJson(url, options);
    }

    function formatDateTime(value) {
        if (!value) return "";
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return "";
        return date.toLocaleString(undefined, {
            month: "short",
            day: "numeric",
            hour: "numeric",
            minute: "2-digit",
        });
    }

    function formatRelative(value) {
        if (!value) return "Recently";
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return "Recently";
        const diffMs = Date.now() - date.getTime();
        const minute = 60 * 1000;
        const hour = 60 * minute;
        const day = 24 * hour;
        if (diffMs < minute) return "Just now";
        if (diffMs < hour) return `${Math.max(1, Math.round(diffMs / minute))}m ago`;
        if (diffMs < day) return `${Math.max(1, Math.round(diffMs / hour))}h ago`;
        if (diffMs < 7 * day) return `${Math.max(1, Math.round(diffMs / day))}d ago`;
        return date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
    }

    function formatBytes(bytes) {
        const value = Number(bytes) || 0;
        if (value <= 0) return "0 B";
        const units = ["B", "KB", "MB", "GB"];
        let size = value;
        let unitIndex = 0;
        while (size >= 1024 && unitIndex < units.length - 1) {
            size /= 1024;
            unitIndex += 1;
        }
        return `${size >= 10 || unitIndex === 0 ? Math.round(size) : size.toFixed(1)} ${units[unitIndex]}`;
    }

    function normalizeTileSize(tileId, size) {
        const allowed = TILE_SIZE_RULES[tileId] || [];
        let normalized = String(size || DEFAULT_TILE_SIZES[tileId] || allowed[0] || "standard").trim().toLowerCase();
        if (normalized === "compact" || normalized === "medium") normalized = "standard";
        if (normalized === "large") normalized = "wide";
        return allowed.includes(normalized) ? normalized : (DEFAULT_TILE_SIZES[tileId] || allowed[0] || "standard");
    }

    function normalizeCalendarView(tileId, view) {
        if (tileId !== "calendar") return "";
        const normalized = String(view || DEFAULT_CALENDAR_VIEW).trim().toLowerCase();
        return CALENDAR_VIEW_RULES.includes(normalized) ? normalized : DEFAULT_CALENDAR_VIEW;
    }

    function normalizeTaskListIds(listIds, availableListIds = null) {
        if (!Array.isArray(listIds)) return [];
        const available = Array.isArray(availableListIds) ? new Set(availableListIds.map((item) => String(item))) : null;
        const normalized = [];
        for (const item of listIds) {
            const listId = String(item || "").trim();
            if (!listId || normalized.includes(listId)) continue;
            if (available && !available.has(listId)) continue;
            normalized.push(listId);
        }
        return normalized;
    }

    function parseTaskListIds(value) {
        if (!value) return [];
        try {
            return normalizeTaskListIds(JSON.parse(value));
        } catch {
            return [];
        }
    }

    function createInstanceId(tileType) {
        const uuid = globalThis.crypto?.randomUUID?.();
        return uuid || `${tileType}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
    }

    function normalizeChoice(value, allowed, fallback) {
        const normalized = String(value ?? fallback).trim().toLowerCase();
        return allowed.includes(normalized) ? normalized : fallback;
    }

    function normalizeNumberChoice(value, allowed, fallback) {
        const normalized = Number(value);
        return allowed.includes(normalized) ? normalized : fallback;
    }

    function tilePayload(tileType, size, view, taskListIds = null, options = {}) {
        const payload = {
            instance_id: String(options.instance_id || createInstanceId(tileType)),
            type: tileType,
            size: normalizeTileSize(tileType, size),
            density: normalizeChoice(options.density, DENSITIES, "comfortable"),
            item_limit: normalizeNumberChoice(options.item_limit, ITEM_LIMITS, 5),
        };
        const title = String(options.title || "").trim().slice(0, 60);
        if (title) payload.title = title;
        if (tileType === "calendar") {
            payload.view = normalizeCalendarView(tileType, view);
            payload.upcoming_days = normalizeNumberChoice(options.upcoming_days, CALENDAR_UPCOMING_DAYS, 7);
        }
        if (tileType === "tasks") {
            const listIds = normalizeTaskListIds(taskListIds);
            if (listIds.length) payload.task_list_ids = listIds;
            payload.deadline_days = normalizeNumberChoice(options.deadline_days, TASK_DEADLINE_DAYS, 30);
            payload.include_overdue = options.include_overdue !== false;
            payload.include_undated = options.include_undated !== false;
            payload.priorities = TASK_PRIORITIES.filter((priority) => (options.priorities || TASK_PRIORITIES).includes(priority));
            if (!payload.priorities.length) payload.priorities = TASK_PRIORITIES.slice();
            payload.starred_only = options.starred_only === true;
        }
        return payload;
    }

    function normalizeTileOrder(preferredOrder, availableTiles) {
        const available = Array.isArray(availableTiles)
            ? availableTiles.filter((tileId) => TILE_META[tileId])
            : Object.keys(TILE_META);
        const ordered = [];
        for (const tileId of Array.isArray(preferredOrder) ? preferredOrder : []) {
            if (available.includes(tileId) && !ordered.includes(tileId)) ordered.push(tileId);
        }
        for (const tileId of Object.keys(TILE_META)) {
            if (available.includes(tileId) && !ordered.includes(tileId)) ordered.push(tileId);
        }
        return ordered;
    }

    function normalizeTileLayout(preferredLayout, availableTiles) {
        const available = Array.isArray(availableTiles)
            ? availableTiles.filter((tileId) => TILE_META[tileId])
            : Object.keys(TILE_META);
        const version = Number(preferredLayout?.version || (Array.isArray(preferredLayout) ? 1 : 0));
        const preservesHiddenTiles = version >= 3;
        const source = Array.isArray(preferredLayout)
            ? preferredLayout
            : Array.isArray(preferredLayout?.tiles)
                ? preferredLayout.tiles
                : [];
        const layout = [];
        const seenInstances = new Set();
        const seenLegacyTypes = new Set();
        for (const item of source) {
            const tileType = typeof item === "string" ? item : (item?.type || item?.id);
            const instanceId = typeof item === "string"
                ? `legacy-${tileType}`
                : String(item?.instance_id || (item?.type ? item?.id : "") || `legacy-${tileType}`);
            if (!available.includes(tileType) || seenInstances.has(instanceId)) continue;
            if (version < 4 && seenLegacyTypes.has(tileType)) continue;
            layout.push(tilePayload(
                tileType,
                typeof item === "string" ? null : item?.size,
                typeof item === "string" ? null : item?.view,
                typeof item === "string" ? null : item?.task_list_ids,
                {
                    ...(typeof item === "string" ? {} : item),
                    instance_id: instanceId,
                },
            ));
            seenInstances.add(instanceId);
            seenLegacyTypes.add(tileType);
        }
        if (!preservesHiddenTiles) {
            for (const tileType of Object.keys(TILE_META)) {
                if (available.includes(tileType) && !seenLegacyTypes.has(tileType)) {
                    layout.push(tilePayload(tileType, null, null, null, { instance_id: `legacy-${tileType}` }));
                }
            }
        }
        return layout.slice(0, MAX_TILES);
    }

    function normalizeDashboardLayout(preferredLayout, availableTiles, quoteFallback = true) {
        const source = preferredLayout?.dashboard_layout || preferredLayout;
        return {
            version: 4,
            daily_quote_visible: typeof source?.daily_quote_visible === "boolean" ? source.daily_quote_visible : Boolean(quoteFallback),
            tiles: normalizeTileLayout(source, availableTiles),
        };
    }

    function cloneLayout(layout) {
        return JSON.parse(JSON.stringify(layout));
    }

    function layoutsEqual(first, second) {
        return JSON.stringify(first) === JSON.stringify(second);
    }

    function nearestTileSize(tileId, deltaX, deltaY, startSize) {
        const allowed = TILE_SIZE_RULES[tileId] || [];
        const current = normalizeTileSize(tileId, startSize);
        const growX = deltaX > 96;
        const growY = deltaY > 96;
        const shrinkX = deltaX < -96;
        const shrinkY = deltaY < -96;

        if ((shrinkX || shrinkY) && allowed.includes("standard")) {
            if (current === "wide" && shrinkX && !shrinkY && allowed.includes("tall")) return "tall";
            return "standard";
        }
        if ((growX && growY) || (current === "tall" && growX) || ((growX || growY) && !allowed.includes("tall"))) {
            if (allowed.includes("wide")) return "wide";
        }
        if (growY && allowed.includes("tall")) return "tall";
        if (growX && allowed.includes("wide")) return "wide";
        return current;
    }

    function summaryLayoutSource(summary) {
        if (summary?.dashboard_layout) return summary.dashboard_layout;
        if (Array.isArray(summary?.tile_layout)) {
            return {
                version: Number(summary.tile_layout_version || 4),
                daily_quote_visible: summary.daily_quote_visible,
                tiles: summary.tile_layout,
            };
        }
        return summary?.tile_layout || summary?.tile_order;
    }

    function groupEventsByDate(events) {
        const map = new Map();
        for (const event of events) {
            const key = event.date || String(event.start || "").slice(0, 10);
            if (!key) continue;
            if (!map.has(key)) map.set(key, []);
            map.get(key).push(event);
        }
        return map;
    }

    function eventDateKey(event) {
        return event?.date || String(event?.start || "").slice(0, 10) || localDateKey(new Date());
    }

    function localDateKey(date) {
        return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
    }

    function tileTitle(tileId, view) {
        const meta = TILE_META[tileId];
        if (tileId !== "calendar") return meta?.title || "";
        const safeView = normalizeCalendarView(tileId, view);
        return `Calendar: ${CALENDAR_VIEW_LABELS[safeView] || "Month"}`;
    }

    window.APStudyDashboardUtils = {
        TILE_META,
        TILE_SIZE_RULES,
        TILE_SIZE_LABELS,
        CALENDAR_VIEW_RULES,
        CALENDAR_VIEW_LABELS,
        CALENDAR_UPCOMING_DAYS,
        TASK_DEADLINE_DAYS,
        TASK_PRIORITIES,
        DUPLICATE_TILE_TYPES,
        ITEM_LIMITS,
        DENSITIES,
        MAX_TILES,
        MAX_DUPLICATE_TILES,
        escapeHtml,
        escapeAttr,
        fetchJson,
        formatDateTime,
        formatRelative,
        formatBytes,
        normalizeTileOrder,
        normalizeTileLayout,
        normalizeDashboardLayout,
        normalizeTileSize,
        normalizeCalendarView,
        normalizeTaskListIds,
        parseTaskListIds,
        tilePayload,
        createInstanceId,
        cloneLayout,
        layoutsEqual,
        tileTitle,
        eventDateKey,
        groupEventsByDate,
        localDateKey,
        nearestTileSize,
        summaryLayoutSource,
    };
})();
