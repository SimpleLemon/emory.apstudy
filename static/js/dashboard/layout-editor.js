(function () {
    "use strict";

    const {
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
        cloneLayout,
        createInstanceId,
        escapeAttr,
        escapeHtml,
        layoutsEqual,
        normalizeDashboardLayout,
        tilePayload,
        tileTitle,
    } = window.APStudyDashboardUtils;

    const TYPE_DESCRIPTIONS = {
        calendar: "See your month, week, or a focused upcoming agenda.",
        tasks: "Build a focused task view from lists, dates, priorities, and stars.",
        files: "Keep recently updated files within reach.",
        notes: "Return to the notes you edited most recently.",
        messages: "Jump back into your latest conversations.",
        courses: "Keep saved courses and sections visible.",
        daily_quote: "Show the daily motivation banner above your grid.",
    };

    function createLayoutEditor(options) {
        const { elements, getSummary, render, persist, showToast } = options;
        const state = {
            editing: false,
            saving: false,
            selectedId: null,
            saved: null,
            draft: null,
            removed: [],
            sortable: null,
            drawerMode: "catalog",
            drawerType: null,
            drawerInstanceId: null,
        };

        function load(layout, quoteFallback = true) {
            const available = getSummary()?.available_tiles;
            const normalized = normalizeDashboardLayout(layout, available, quoteFallback);
            const hasServerQuotePreference = typeof (layout?.dashboard_layout || layout)?.daily_quote_visible === "boolean";
            if (!state.editing) {
                state.saved = cloneLayout(normalized);
                state.draft = cloneLayout(normalized);
            }
            if (hasServerQuotePreference) {
                window.APStudyDashboardDailyQuote?.commitVisibility?.(normalized.daily_quote_visible);
            }
            syncQuotePreview();
            syncChrome();
        }

        function currentLayout() {
            return cloneLayout(state.draft || state.saved || { version: 4, daily_quote_visible: true, tiles: [] });
        }

        function isDirty() {
            return Boolean(state.editing && state.saved && state.draft && !layoutsEqual(state.saved, state.draft));
        }

        function selectedTile() {
            return state.draft?.tiles.find((tile) => tile.instance_id === state.selectedId) || null;
        }

        function selectedLabel() {
            if (state.selectedId === "daily_quote") return "Daily Quote";
            const tile = selectedTile();
            return tile?.title || tileTitle(tile?.type, tile?.view) || "Dashboard tile";
        }

        function enter() {
            if (!state.saved) return;
            state.editing = true;
            state.selectedId = null;
            state.removed = [];
            state.draft = cloneLayout(state.saved);
            render(state.draft);
            syncChrome();
        }

        async function done() {
            if (!state.editing || state.saving) return;
            if (!isDirty()) {
                exit();
                return;
            }
            state.saving = true;
            syncChrome();
            try {
                const saved = await persist(state.draft);
                state.saved = normalizeDashboardLayout(saved, getSummary()?.available_tiles, state.draft.daily_quote_visible);
                state.draft = cloneLayout(state.saved);
                window.APStudyDashboardDailyQuote?.commitVisibility?.(state.saved.daily_quote_visible);
                exit();
                showToast("Dashboard layout saved.", "success");
            } catch (error) {
                showToast(error.message || "Try again in a moment.", "error", { title: "Couldn’t save dashboard layout" });
            } finally {
                state.saving = false;
                syncChrome();
            }
        }

        function exit({ restoreFocus = false } = {}) {
            state.editing = false;
            state.selectedId = null;
            state.removed = [];
            closeDrawer();
            destroySortable();
            render(state.saved);
            syncChrome();
            if (restoreFocus) elements.editToggle?.focus({ preventScroll: true });
        }

        function cancel() {
            if (!state.editing) return;
            if (isDirty()) {
                elements.discardDialog?.showModal?.();
                return;
            }
            exit({ restoreFocus: true });
        }

        function confirmDiscard() {
            state.draft = cloneLayout(state.saved);
            elements.discardDialog?.close?.();
            exit({ restoreFocus: true });
        }

        function select(instanceId, { focusToolbar = false } = {}) {
            if (!state.editing) return;
            if (instanceId !== "daily_quote" && !state.draft.tiles.some((tile) => tile.instance_id === instanceId)) return;
            state.selectedId = instanceId;
            syncSelection();
            syncToolbar();
            if (focusToolbar) elements.toolbarCustomize?.focus({ preventScroll: true });
        }

        function bindTiles() {
            syncQuotePreview();
            const tiles = Array.from(elements.tiles?.querySelectorAll(".dashboard-tile[data-tile-id]") || []);
            tiles.forEach((tile) => {
                if (state.editing) {
                    tile.tabIndex = 0;
                    tile.setAttribute("role", "button");
                    tile.setAttribute("aria-label", `Select ${tile.getAttribute("aria-label") || "dashboard tile"} for editing`);
                    tile.querySelector(".dashboard-tile-inner")?.setAttribute("inert", "");
                    tile.querySelector(".dashboard-tile-inner")?.setAttribute("aria-hidden", "true");
                    tile.addEventListener("click", () => select(tile.dataset.tileId));
                    tile.addEventListener("keydown", (event) => {
                        if (event.key !== "Enter" && event.key !== " ") return;
                        event.preventDefault();
                        select(tile.dataset.tileId, { focusToolbar: true });
                    });
                } else {
                    tile.removeAttribute("tabindex");
                    tile.removeAttribute("role");
                    tile.removeAttribute("aria-pressed");
                    tile.querySelector(".dashboard-tile-inner")?.removeAttribute("inert");
                    tile.querySelector(".dashboard-tile-inner")?.removeAttribute("aria-hidden");
                }
            });
            if (elements.quoteSlot) {
                const selectable = state.editing && state.draft?.daily_quote_visible;
                elements.quoteSlot.classList.toggle("is-layout-selectable", selectable);
                if (selectable) {
                    elements.quoteSlot.tabIndex = 0;
                    elements.quoteSlot.setAttribute("role", "button");
                    elements.quoteSlot.setAttribute("aria-label", "Select Daily Quote banner for editing");
                } else {
                    elements.quoteSlot.removeAttribute("tabindex");
                    elements.quoteSlot.removeAttribute("role");
                    elements.quoteSlot.removeAttribute("aria-label");
                    elements.quoteSlot.removeAttribute("aria-pressed");
                }
                elements.quoteSlot.onclick = selectable ? () => select("daily_quote") : null;
                elements.quoteSlot.onkeydown = selectable ? (event) => {
                    if (event.key !== "Enter" && event.key !== " ") return;
                    event.preventDefault();
                    select("daily_quote", { focusToolbar: true });
                } : null;
            }
            syncSelection();
            syncToolbar();
            setupSortable();
        }

        function syncSelection() {
            elements.tiles?.querySelectorAll(".dashboard-tile").forEach((tile) => {
                const selected = state.editing && tile.dataset.tileId === state.selectedId;
                tile.classList.toggle("is-selected", selected);
                if (state.editing) tile.setAttribute("aria-pressed", selected ? "true" : "false");
                else tile.removeAttribute("aria-pressed");
            });
            elements.quoteSlot?.classList.toggle("is-selected", state.editing && state.selectedId === "daily_quote");
            if (state.editing && elements.quoteSlot?.classList.contains("is-layout-selectable")) {
                elements.quoteSlot.setAttribute("aria-pressed", state.selectedId === "daily_quote" ? "true" : "false");
            } else elements.quoteSlot?.removeAttribute("aria-pressed");
        }

        function syncChrome() {
            document.body.classList.toggle("dashboard-editing-layout", state.editing);
            if (elements.editToggle) {
                elements.editToggle.setAttribute("aria-pressed", state.editing ? "true" : "false");
                elements.editToggle.disabled = state.saving;
                const icon = elements.editToggle.querySelector(".material-symbols-outlined");
                const label = elements.editToggle.querySelector(".dashboard-action-label");
                if (icon) icon.textContent = state.editing ? "check" : "dashboard_customize";
                if (label) label.textContent = state.saving ? "Saving…" : (state.editing ? "Done" : "Edit layout");
                elements.editToggle.setAttribute("aria-label", state.editing ? "Save dashboard layout" : "Edit dashboard layout");
            }
            if (elements.addTile) {
                elements.addTile.hidden = !state.editing;
                elements.addTile.disabled = !state.editing || state.draft?.tiles.length >= MAX_TILES;
            }
            if (elements.cancel) elements.cancel.hidden = !state.editing;
            syncToolbar();
        }

        function syncToolbar() {
            const hasSelection = state.editing && Boolean(state.selectedId);
            if (!elements.toolbar) return;
            elements.toolbar.hidden = !hasSelection;
            if (!hasSelection) return;
            if (elements.toolbarLabel) elements.toolbarLabel.textContent = selectedLabel();
            const quoteSelected = state.selectedId === "daily_quote";
            const index = state.draft.tiles.findIndex((tile) => tile.instance_id === state.selectedId);
            if (elements.toolbarMoveEarlier) elements.toolbarMoveEarlier.disabled = quoteSelected || index <= 0;
            if (elements.toolbarMoveLater) elements.toolbarMoveLater.disabled = quoteSelected || index < 0 || index >= state.draft.tiles.length - 1;
            if (elements.toolbarCustomize) elements.toolbarCustomize.hidden = quoteSelected;
        }

        function moveSelected(direction) {
            const index = state.draft.tiles.findIndex((tile) => tile.instance_id === state.selectedId);
            const nextIndex = index + direction;
            if (index < 0 || nextIndex < 0 || nextIndex >= state.draft.tiles.length) return;
            const [tile] = state.draft.tiles.splice(index, 1);
            state.draft.tiles.splice(nextIndex, 0, tile);
            render(state.draft);
            syncToolbar();
            announce(`${selectedLabel()} moved ${direction < 0 ? "earlier" : "later"}.`);
        }

        function removeSelected() {
            if (state.selectedId === "daily_quote") {
                state.draft.daily_quote_visible = false;
                state.removed.push({ kind: "quote" });
            } else {
                const index = state.draft.tiles.findIndex((tile) => tile.instance_id === state.selectedId);
                if (index < 0) return;
                const [tile] = state.draft.tiles.splice(index, 1);
                state.removed.push({ kind: "tile", tile, index });
            }
            const removedLabel = selectedLabel();
            state.selectedId = null;
            render(state.draft);
            showToast(`${removedLabel} removed from this draft.`, "info", {
                action: { label: "Undo", onClick: undoRemove },
            });
        }

        function undoRemove() {
            const removed = state.removed.pop();
            if (!removed) return;
            if (removed.kind === "quote") {
                state.draft.daily_quote_visible = true;
                state.selectedId = "daily_quote";
            } else {
                state.draft.tiles.splice(Math.min(removed.index, state.draft.tiles.length), 0, removed.tile);
                state.selectedId = removed.tile.instance_id;
            }
            render(state.draft);
        }

        function openCatalog() {
            state.drawerMode = "catalog";
            state.drawerType = null;
            state.drawerInstanceId = null;
            setDrawerSide("right");
            renderDrawer();
            elements.drawer?.showModal?.();
        }

        function openCustomize() {
            const tile = selectedTile();
            if (!tile) return;
            state.drawerMode = "edit";
            state.drawerType = tile.type;
            state.drawerInstanceId = tile.instance_id;
            positionDrawerForTile(tile.instance_id);
            renderDrawer();
            elements.drawer?.showModal?.();
        }

        function setDrawerSide(side) {
            if (!elements.drawer) return;
            elements.drawer.dataset.side = side === "left" ? "left" : "right";
        }

        function positionDrawerForTile(instanceId) {
            const tile = Array.from(elements.tiles?.querySelectorAll(".dashboard-tile[data-tile-id]") || [])
                .find((candidate) => candidate.dataset.tileId === instanceId);
            if (!tile) {
                setDrawerSide("right");
                return;
            }
            const rect = tile.getBoundingClientRect();
            const center = rect.left + (rect.width / 2);
            setDrawerSide(center >= window.innerWidth / 2 ? "left" : "right");
        }

        function closeDrawer() {
            if (elements.drawer?.open) elements.drawer.close();
        }

        function typeCount(tileType) {
            return state.draft.tiles.filter((tile) => tile.type === tileType).length;
        }

        function canAdd(tileType) {
            if (state.draft.tiles.length >= MAX_TILES) return false;
            const count = typeCount(tileType);
            return DUPLICATE_TILE_TYPES.includes(tileType) ? count < MAX_DUPLICATE_TILES : count === 0;
        }

        function catalogHtml() {
            const available = (getSummary()?.available_tiles || Object.keys(TILE_META)).filter((type) => TILE_META[type]);
            const entries = ["daily_quote", ...available];
            return `
                <div class="dashboard-drawer-head">
                    <div><h2>Add a tile</h2><p>Choose a view, then configure it before adding.</p></div>
                    <button class="dashboard-drawer-close" type="button" data-drawer-close aria-label="Close tile catalog"><span class="material-symbols-outlined" aria-hidden="true">close</span></button>
                </div>
                <div class="dashboard-catalog-list">
                    ${entries.map((tileType) => {
                        const quoteVisible = tileType === "daily_quote" && state.draft.daily_quote_visible;
                        const disabled = tileType === "daily_quote" ? quoteVisible : !canAdd(tileType);
                        const meta = TILE_META[tileType];
                        const title = tileType === "daily_quote" ? "Daily Quote" : meta.title;
                        const icon = tileType === "daily_quote" ? "format_quote" : meta.icon;
                        const count = tileType === "daily_quote" ? Number(quoteVisible) : typeCount(tileType);
                        const action = disabled ? "Added" : (count ? "Add another" : "Configure");
                        return `<button class="dashboard-catalog-item" type="button" data-catalog-type="${tileType}" ${disabled ? "disabled" : ""}>
                            <span class="dashboard-catalog-icon material-symbols-outlined" aria-hidden="true">${icon}</span>
                            <span><strong>${escapeHtml(title)}</strong><small>${escapeHtml(TYPE_DESCRIPTIONS[tileType])}</small></span>
                            <span class="dashboard-catalog-action">${action}</span>
                        </button>`;
                    }).join("")}
                </div>
                <p class="dashboard-catalog-limit">${state.draft.tiles.length} of ${MAX_TILES} grid tiles used</p>
            `;
        }

        function optionTags(values, selected, name, labels = {}) {
            return values.map((value) => `<label class="dashboard-choice"><input type="radio" name="${name}" value="${escapeAttr(value)}" ${String(selected) === String(value) ? "checked" : ""}><span>${escapeHtml(labels[value] || value)}</span></label>`).join("");
        }

        function checkboxTags(values, selected, name, labels = {}) {
            const selectedSet = new Set(selected || []);
            return values.map((value) => `<label class="dashboard-check-choice"><input type="checkbox" name="${name}" value="${escapeAttr(value)}" ${selectedSet.has(value) ? "checked" : ""}><span>${escapeHtml(labels[value] || value)}</span></label>`).join("");
        }

        function formHtml(tileType, existing = null) {
            const tile = existing || tilePayload(tileType, null, null, null, { instance_id: createInstanceId(tileType) });
            const taskLists = getSummary()?.tiles?.tasks?.lists || [];
            const title = tile.title || "";
            return `
                <form class="dashboard-tile-form" data-tile-form data-instance-id="${escapeAttr(tile.instance_id)}" data-tile-type="${tileType}">
                    <div class="dashboard-drawer-head">
                        <div><h2>${existing ? "Customize tile" : `Add ${escapeHtml(TILE_META[tileType].title)}`}</h2><p>${existing ? "Changes preview instantly and remain in your draft." : "Configure this tile before adding it."}</p></div>
                        <button class="dashboard-drawer-close" type="button" data-drawer-close aria-label="Close tile settings"><span class="material-symbols-outlined" aria-hidden="true">close</span></button>
                    </div>
                    <label class="dashboard-form-field"><span>Custom title <small>Optional</small></span><input name="title" maxlength="60" value="${escapeAttr(title)}" placeholder="${escapeAttr(tileTitle(tileType, tile.view))}"></label>
                    <fieldset class="dashboard-form-field dashboard-desktop-size"><legend>Desktop size</legend><div class="dashboard-choice-row">${optionTags(TILE_SIZE_RULES[tileType], tile.size, "size", TILE_SIZE_LABELS)}</div></fieldset>
                    ${tileType === "calendar" ? `
                        <fieldset class="dashboard-form-field"><legend>Calendar view</legend><div class="dashboard-choice-row">${optionTags(CALENDAR_VIEW_RULES, tile.view, "view", CALENDAR_VIEW_LABELS)}</div></fieldset>
                        <fieldset class="dashboard-form-field"><legend>Upcoming range</legend><div class="dashboard-choice-row">${optionTags(CALENDAR_UPCOMING_DAYS, tile.upcoming_days, "upcoming_days", {7:"7 days",14:"14 days",30:"30 days"})}</div></fieldset>
                    ` : ""}
                    ${tileType === "tasks" ? `
                        <fieldset class="dashboard-form-field"><legend>Task lists <small>None selected means all lists</small></legend><div class="dashboard-check-grid">${checkboxTags(taskLists.map((list) => list.id), tile.task_list_ids, "task_list_ids", Object.fromEntries(taskLists.map((list) => [list.id, list.name || "Untitled List"])))}</div></fieldset>
                        <fieldset class="dashboard-form-field"><legend>Deadline window</legend><div class="dashboard-choice-row">${optionTags(TASK_DEADLINE_DAYS, tile.deadline_days, "deadline_days", {7:"7 days",30:"30 days"})}</div></fieldset>
                        <fieldset class="dashboard-form-field"><legend>Include</legend><div class="dashboard-check-grid"><label class="dashboard-check-choice"><input type="checkbox" name="include_overdue" ${tile.include_overdue !== false ? "checked" : ""}><span>Overdue</span></label><label class="dashboard-check-choice"><input type="checkbox" name="include_undated" ${tile.include_undated !== false ? "checked" : ""}><span>No deadline</span></label><label class="dashboard-check-choice"><input type="checkbox" name="starred_only" ${tile.starred_only ? "checked" : ""}><span>Starred only</span></label></div></fieldset>
                        <fieldset class="dashboard-form-field"><legend>Priorities</legend><div class="dashboard-check-grid">${checkboxTags(TASK_PRIORITIES, tile.priorities, "priorities", {high:"High",medium:"Medium",low:"Low",none:"None"})}</div></fieldset>
                    ` : ""}
                    <fieldset class="dashboard-form-field"><legend>Items shown</legend><div class="dashboard-choice-row">${optionTags(ITEM_LIMITS, tile.item_limit, "item_limit", {3:"3 items",5:"5 items",8:"8 items"})}</div></fieldset>
                    ${!["calendar", "tasks"].includes(tileType) ? `<fieldset class="dashboard-form-field"><legend>Row density</legend><div class="dashboard-choice-row">${optionTags(DENSITIES, tile.density, "density", {compact:"Compact",comfortable:"Comfortable"})}</div></fieldset>` : ""}
                    <div class="dashboard-drawer-actions"><button class="dashboard-drawer-secondary" type="button" data-drawer-back>${existing ? "Close" : "Back"}</button><button class="dashboard-drawer-primary" type="submit">${existing ? "Done" : "Add tile"}</button></div>
                </form>
            `;
        }

        function renderDrawer() {
            if (!elements.drawerBody) return;
            const existing = state.drawerMode === "edit"
                ? state.draft.tiles.find((tile) => tile.instance_id === state.drawerInstanceId)
                : null;
            elements.drawerBody.innerHTML = state.drawerMode === "catalog"
                ? catalogHtml()
                : formHtml(state.drawerType, existing);
        }

        function readForm(form, { validate = true } = {}) {
            const data = new FormData(form);
            const tileType = form.dataset.tileType;
            const existing = state.draft.tiles.find((tile) => tile.instance_id === form.dataset.instanceId);
            const priorities = data.getAll("priorities");
            if (tileType === "tasks" && !priorities.length) {
                if (validate) showToast("Select at least one task priority.", "warning");
                return null;
            }
            return tilePayload(
                tileType,
                data.get("size") || existing?.size,
                data.get("view") || existing?.view,
                data.getAll("task_list_ids"),
                {
                    ...existing,
                    instance_id: form.dataset.instanceId,
                    title: data.get("title"),
                    density: data.get("density") || existing?.density,
                    item_limit: Number(data.get("item_limit")),
                    upcoming_days: Number(data.get("upcoming_days")),
                    deadline_days: Number(data.get("deadline_days")),
                    include_overdue: data.has("include_overdue"),
                    include_undated: data.has("include_undated"),
                    priorities,
                    starred_only: data.has("starred_only"),
                },
            );
        }

        function previewForm(form) {
            if (state.drawerMode !== "edit") return;
            const nextTile = readForm(form, { validate: false });
            if (!nextTile) return;
            const existingIndex = state.draft.tiles.findIndex((tile) => tile.instance_id === nextTile.instance_id);
            if (existingIndex < 0) return;
            state.draft.tiles[existingIndex] = nextTile;
            render(state.draft);
            syncToolbar();
        }

        function submitForm(form) {
            const nextTile = readForm(form);
            if (!nextTile) return;
            const existingIndex = state.draft.tiles.findIndex((tile) => tile.instance_id === nextTile.instance_id);
            if (existingIndex >= 0) {
                state.draft.tiles[existingIndex] = nextTile;
            } else {
                const selectedIndex = state.draft.tiles.findIndex((tile) => tile.instance_id === state.selectedId);
                state.draft.tiles.splice(selectedIndex >= 0 ? selectedIndex + 1 : state.draft.tiles.length, 0, nextTile);
            }
            state.selectedId = nextTile.instance_id;
            closeDrawer();
            render(state.draft);
            requestAnimationFrame(() => elements.tiles?.querySelector(`[data-tile-id="${CSS.escape(nextTile.instance_id)}"]`)?.scrollIntoView({ behavior: prefersReducedMotion() ? "auto" : "smooth", block: "center" }));
        }

        function setupSortable() {
            destroySortable();
            if (!state.editing || !elements.tiles || typeof Sortable === "undefined") return;
            state.sortable = Sortable.create(elements.tiles, {
                animation: prefersReducedMotion() ? 0 : 150,
                draggable: ".dashboard-tile",
                ghostClass: "dashboard-tile-ghost",
                dragClass: "is-dragging",
                onStart: (event) => select(event.item?.dataset.tileId),
                onEnd: () => {
                    const byId = new Map(state.draft.tiles.map((tile) => [tile.instance_id, tile]));
                    state.draft.tiles = Array.from(elements.tiles.querySelectorAll(".dashboard-tile[data-tile-id]"))
                        .map((tile) => byId.get(tile.dataset.tileId))
                        .filter(Boolean);
                    syncToolbar();
                    announce(`${selectedLabel()} moved.`);
                },
            });
        }

        function destroySortable() {
            state.sortable?.destroy();
            state.sortable = null;
        }

        function syncQuotePreview() {
            if (!elements.quoteSlot || !state.draft) return;
            window.APStudyDashboardDailyQuote?.previewVisibility?.(state.draft.daily_quote_visible);
        }

        function announce(message) {
            if (!elements.announcer) return;
            elements.announcer.textContent = "";
            requestAnimationFrame(() => { elements.announcer.textContent = message; });
        }

        function prefersReducedMotion() {
            return window.APStudyAccessibility?.prefersReducedMotion?.()
                || window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches;
        }

        elements.editToggle?.addEventListener("click", () => state.editing ? void done() : enter());
        elements.cancel?.addEventListener("click", cancel);
        elements.addTile?.addEventListener("click", openCatalog);
        elements.toolbarMoveEarlier?.addEventListener("click", () => moveSelected(-1));
        elements.toolbarMoveLater?.addEventListener("click", () => moveSelected(1));
        elements.toolbarCustomize?.addEventListener("click", openCustomize);
        elements.toolbarRemove?.addEventListener("click", removeSelected);
        elements.discardConfirm?.addEventListener("click", confirmDiscard);
        elements.discardKeep?.addEventListener("click", () => elements.discardDialog?.close?.());
        elements.drawer?.addEventListener("click", (event) => {
            if (event.target === elements.drawer) closeDrawer();
            const close = event.target.closest("[data-drawer-close]");
            if (close) closeDrawer();
            const back = event.target.closest("[data-drawer-back]");
            if (back) {
                if (state.drawerMode === "edit") closeDrawer();
                else {
                    state.drawerMode = "catalog";
                    renderDrawer();
                }
            }
            const catalogItem = event.target.closest("[data-catalog-type]");
            if (!catalogItem || catalogItem.disabled) return;
            const tileType = catalogItem.dataset.catalogType;
            if (tileType === "daily_quote") {
                state.draft.daily_quote_visible = true;
                state.selectedId = "daily_quote";
                closeDrawer();
                render(state.draft);
                return;
            }
            state.drawerMode = "add";
            state.drawerType = tileType;
            state.drawerInstanceId = null;
            renderDrawer();
        });
        elements.drawer?.addEventListener("submit", (event) => {
            if (!event.target.matches("[data-tile-form]")) return;
            event.preventDefault();
            submitForm(event.target);
        });
        elements.drawer?.addEventListener("input", (event) => {
            const form = event.target.closest?.("[data-tile-form]");
            if (form) previewForm(form);
        });
        window.addEventListener("beforeunload", (event) => {
            if (!isDirty()) return;
            event.preventDefault();
            event.returnValue = "";
        });

        return {
            bindTiles,
            currentLayout,
            isEditing: () => state.editing,
            load,
            pause: destroySortable,
            resume: setupSortable,
        };
    }

    window.APStudyDashboardLayoutEditor = { create: createLayoutEditor };
})();
