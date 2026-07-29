// Calendar event activation and context menu interactions.
(function () {
    const rootSelector = "#calendar-view-root";
    const eventSelector = "[data-event-ref], [data-event-id]";
    let menuEl = null;
    let currentContext = null;

    function ensureMenu() {
        if (menuEl) return menuEl;
        menuEl = document.createElement("div");
        menuEl.setAttribute("role", "menu");
        menuEl.setAttribute("aria-label", "Event actions");
        menuEl.className = "calendar-right-click-menu";
        menuEl.style.position = "fixed";
        menuEl.style.minWidth = "190px";
        menuEl.style.display = "none";
        document.body.appendChild(menuEl);
        return menuEl;
    }

    function closeMenu({ restoreFocus = false } = {}) {
        if (!menuEl) return;
        const anchorEl = currentContext?.anchorEl || null;
        menuEl.style.display = "none";
        menuEl.innerHTML = "";
        currentContext = null;
        if (restoreFocus && anchorEl?.isConnected) anchorEl.focus?.({ preventScroll: true });
    }

    function buildMenu(items) {
        const el = ensureMenu();
        el.innerHTML = "";
        items.forEach((item, index) => {
            const button = document.createElement("button");
            button.type = "button";
            button.setAttribute("role", "menuitem");
            button.className = `calendar-right-click-item${item.danger ? " is-danger" : ""}`;
            button.innerHTML = `
                <span class="material-symbols-outlined calendar-right-click-icon" aria-hidden="true">${item.icon || "radio_button_unchecked"}</span>
                <span>${item.label}</span>
            `;
            button.tabIndex = index === 0 ? 0 : -1;
            button.addEventListener("click", () => {
                const context = currentContext;
                closeMenu({ restoreFocus: true });
                item.onClick(context);
            });
            el.appendChild(button);
        });
        return el;
    }

    function isReadOnlyCalendar() {
        return Boolean(window.state?.public?.readOnly)
            || document.body?.dataset.calendarReadonly === "true";
    }

    function contextForTarget(target, { clientX = 0, clientY = 0 } = {}) {
        const eventEl = target.closest?.(eventSelector) || null;
        const dateAttrEl = target.closest?.("[data-date]") || null;
        const timeAttrEl = target.closest?.("[data-time]") || null;
        const eventId = eventEl?.getAttribute("data-event-id") || null;
        const eventRef = eventEl?.getAttribute("data-event-ref") || eventId;
        return {
            x: clientX,
            y: clientY,
            eventId,
            eventRef,
            event: eventRef && window.getCalendarEventByRef
                ? window.getCalendarEventByRef(eventRef)
                : null,
            date: dateAttrEl?.getAttribute("data-date") || null,
            time: timeAttrEl?.getAttribute("data-time") || null,
            anchorEl: eventEl || dateAttrEl || target,
            readOnly: isReadOnlyCalendar(),
        };
    }

    function getEventMenuItems(context) {
        const event = context.event;
        if (context.readOnly) {
            return event ? [
                { label: "View Event", icon: "visibility", onClick: openView },
            ] : [];
        }

        const isTask = event?.source_type === "task" || event?.type === "task";
        if (isTask) {
            return event?.task_id
                ? [{ label: "Open Task", icon: "open_in_new", onClick: openTask }]
                : [{ label: "View Event", icon: "visibility", onClick: openView }];
        }

        if (event?.source === "simulated") {
            return [
                { label: "View Event", icon: "visibility", onClick: openView },
                { label: "Duplicate Event", icon: "content_copy", onClick: duplicateEvent },
            ];
        }

        if (isImportedEvent(event)) {
            return [
                { label: "View / Edit Event", icon: "edit_calendar", onClick: openEdit },
                { label: "Duplicate Event", icon: "content_copy", onClick: duplicateEvent },
                { label: "Hide Imported Event", icon: "visibility_off", onClick: deleteEvent, danger: true },
            ];
        }

        return [
            { label: "View / Edit Event", icon: "edit_calendar", onClick: openEdit },
            { label: "Duplicate Event", icon: "content_copy", onClick: duplicateEvent },
            { label: "Delete Event", icon: "delete", onClick: deleteEvent, danger: true },
        ];
    }

    function openContextMenu(context) {
        closeMenu();
        currentContext = context;
        const items = context.eventRef
            ? getEventMenuItems(context)
            : [
                { label: "Create New Event", icon: "calendar_add_on", onClick: createNewFromContext },
                { label: "Go to Today", icon: "today", onClick: goToToday },
            ];
        if (!items.length) return;
        showMenuAt(context.x, context.y, buildMenu(items));
    }

    function onContextMenu(event) {
        const root = document.querySelector(rootSelector);
        if (!root?.contains(event.target)) return;
        event.preventDefault();
        openContextMenu(contextForTarget(event.target, event));
    }

    function showMenuAt(x, y) {
        const menu = ensureMenu();
        menu.style.display = "block";
        const pad = 8;
        const rect = menu.getBoundingClientRect();
        let left = x;
        let top = y;
        if (left + rect.width + pad > window.innerWidth) left = Math.max(pad, window.innerWidth - rect.width - pad);
        if (top + rect.height + pad > window.innerHeight) top = Math.max(pad, window.innerHeight - rect.height - pad);
        menu.style.left = `${left}px`;
        menu.style.top = `${top}px`;
        const first = menu.querySelector("[role=menuitem]");
        if (first) {
            menu.querySelectorAll("[role=menuitem]").forEach((item) => { item.tabIndex = -1; });
            first.tabIndex = 0;
            first.focus();
        }
    }

    function activateEventElement(eventEl) {
        const context = contextForTarget(eventEl);
        activateEvent(context.event, context.anchorEl);
    }

    function activateEvent(calendarEvent, anchorEl = null) {
        if (!calendarEvent) return false;
        const context = {
            event: calendarEvent,
            eventId: calendarEvent.id || null,
            eventRef: calendarEvent.event_ref || calendarEvent.id || null,
            anchorEl,
            readOnly: isReadOnlyCalendar(),
        };
        if (context.readOnly || context.event.source === "simulated") {
            openView(context);
            return true;
        }
        const isTask = context.event.source_type === "task" || context.event.type === "task";
        if (isTask && context.event.task_id) {
            openTask(context);
            return true;
        }
        openEdit(context);
        return true;
    }

    function openKeyboardContextMenu(eventEl) {
        const rect = eventEl.getBoundingClientRect();
        openContextMenu(contextForTarget(eventEl, {
            clientX: Math.round(rect.left + Math.min(rect.width / 2, 24)),
            clientY: Math.round(rect.bottom),
        }));
    }

    function onEventClick(event) {
        const root = document.querySelector(rootSelector);
        const eventEl = event.target.closest?.(eventSelector);
        if (!eventEl || !root?.contains(eventEl)) return;
        activateEventElement(eventEl);
    }

    function onEventKeyDown(event) {
        const root = document.querySelector(rootSelector);
        const eventEl = event.target.closest?.(eventSelector);
        if (!eventEl || !root?.contains(eventEl)) return;
        const opensMenu = event.key === "ContextMenu"
            || event.key === "Apps"
            || event.key === "Menu"
            || (event.shiftKey && event.key === "F10");
        if (opensMenu) {
            event.preventDefault();
            openKeyboardContextMenu(eventEl);
            return;
        }
        if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            activateEventElement(eventEl);
        }
    }

    function createNewFromContext(context) {
        const startDate = context.date;
        const time = context.time;
        const payload = {};
        if (startDate && time) {
            payload.start = `${startDate}T${time}:00`;
            const end = new Date(`${startDate}T${time}:00`);
            end.setHours(end.getHours() + 1);
            payload.end = end.toISOString();
        } else if (startDate) {
            payload.start = `${startDate}T09:00:00`;
            payload.end = `${startDate}T10:00:00`;
        }
        window.openCalendarEventForm?.({ mode: "create", data: payload });
    }

    function goToToday() {
        if (!window.state) return;
        window.state.anchorDate = new Date();
        window.render?.();
        if (window.ensureEventsForRange && window.getBufferedRangeForView) {
            window.ensureEventsForRange(window.getBufferedRangeForView());
        }
    }

    function openTask(context) {
        const taskId = context?.event?.task_id;
        if (taskId) window.location.href = `/tasks?task=${encodeURIComponent(taskId)}`;
    }

    function formDataForEvent(event) {
        if (!event) return null;
        return {
            ...event,
            start: event.start || event.startDate,
            end: event.end || event.endDate,
            all_day: event.all_day || event.is_all_day || event.isAllDay,
        };
    }

    function isImportedEvent(event) {
        return Boolean(event?.event_ref && String(event.event_ref).startsWith("feed:"));
    }

    function openView(context) {
        const data = formDataForEvent(context.event);
        if (data) window.openCalendarEventForm?.({ mode: "view", data, opener: context.anchorEl });
    }

    function openEdit(context) {
        const event = context.event;
        const data = formDataForEvent(event);
        if (!data) return;
        window.openCalendarEventForm?.({
            mode: isImportedEvent(event) ? "override" : "edit",
            data,
            opener: context.anchorEl,
        });
    }

    function duplicateEvent(context) {
        const data = formDataForEvent(context.event);
        if (!data) return;
        data.title = `${data.title || "Untitled"} (Copy)`;
        delete data.id;
        delete data.event_ref;
        window.openCalendarEventForm?.({ mode: "create", data, opener: context.anchorEl });
    }

    function hideEventElements(event, context) {
        const refs = new Set([
            event?.event_ref,
            event?.id,
            context?.eventRef,
            context?.eventId,
        ].filter(Boolean).map(String));
        const hidden = [];
        document.querySelectorAll(eventSelector).forEach((element) => {
            const ref = element.getAttribute("data-event-ref") || element.getAttribute("data-event-id");
            if (!refs.has(String(ref || ""))) return;
            hidden.push({ element, wasHidden: element.hidden });
            element.hidden = true;
        });
        return hidden;
    }

    function restoreEventElements(hidden) {
        hidden.forEach(({ element, wasHidden }) => {
            if (element?.isConnected) element.hidden = wasHidden;
        });
    }

    function showEventDeleteError() {
        window.APStudyToast?.show?.({
            title: "Couldn’t delete event",
            message: "Try again in a moment.",
            type: "error",
        });
    }

    async function deleteEvent(context) {
        const event = context.event;
        if (!event) return;
        const imported = isImportedEvent(event);
        const accepted = await (window.APStudyConfirm?.request?.({
            title: imported ? "Hide imported event?" : "Delete event?",
            message: imported
                ? "This imported event will be hidden from your calendar."
                : "This event will be removed from your calendar.",
            acceptLabel: imported ? "Hide event" : "Delete event",
            danger: true,
        }) ?? Promise.resolve(false));
        if (!accepted) return;
        const hiddenElements = hideEventElements(event, context);
        const commit = async ({ reason } = {}) => {
            const response = imported
                ? await fetch("/api/calendar/event-overrides/hide", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ event_ref: event.event_ref }),
                    keepalive: reason === "pagehide",
                })
                : await fetch(`/api/calendar/events/${encodeURIComponent(event.id || context.eventId)}`, {
                    method: "DELETE",
                    keepalive: reason === "pagehide",
                });
            if (!response.ok) throw new Error("delete failed");
            localStorage.removeItem("calendarEventsCache");
            window.loadCalendarData?.();
        };
        if (window.APStudyUndo?.stage) {
            window.APStudyUndo.stage({
                message: imported ? "Imported event hidden." : "Event deleted.",
                commit,
                restore: () => restoreEventElements(hiddenElements),
                onCommitError: showEventDeleteError,
            });
            return;
        }
        try {
            await commit();
        } catch (_) {
            restoreEventElements(hiddenElements);
            showEventDeleteError();
        }
    }

    function moveMenuFocus(direction) {
        const items = Array.from(menuEl?.querySelectorAll("[role=menuitem]") || []);
        if (!items.length) return;
        const currentIndex = Math.max(0, items.indexOf(document.activeElement));
        const nextIndex = (currentIndex + direction + items.length) % items.length;
        items.forEach((item) => { item.tabIndex = -1; });
        items[nextIndex].tabIndex = 0;
        items[nextIndex].focus();
    }

    function onMenuKeyDown(event) {
        if (!menuEl || menuEl.style.display === "none") return;
        if (event.key === "Escape") {
            event.preventDefault();
            closeMenu({ restoreFocus: true });
            return;
        }
        if (event.key === "ArrowDown" || (event.key === "Tab" && !event.shiftKey)) {
            event.preventDefault();
            moveMenuFocus(1);
            return;
        }
        if (event.key === "ArrowUp" || (event.key === "Tab" && event.shiftKey)) {
            event.preventDefault();
            moveMenuFocus(-1);
            return;
        }
        if (event.key === "Home" || event.key === "End") {
            event.preventDefault();
            const items = Array.from(menuEl.querySelectorAll("[role=menuitem]"));
            items.forEach((item) => { item.tabIndex = -1; });
            const next = event.key === "Home" ? items[0] : items.at(-1);
            if (next) {
                next.tabIndex = 0;
                next.focus();
            }
        }
    }

    function register() {
        const root = document.querySelector(rootSelector);
        if (!root || root.dataset.eventInteractionsWired === "true") return;
        root.dataset.eventInteractionsWired = "true";
        root.addEventListener("contextmenu", onContextMenu);
        root.addEventListener("click", onEventClick);
        root.addEventListener("keydown", onEventKeyDown);
        document.getElementById("calendar-week-time-scroller")?.addEventListener("scroll", () => closeMenu());
        document.addEventListener("click", (event) => {
            if (event instanceof MouseEvent && event.button === 2) return;
            if (menuEl?.style.display !== "none" && !menuEl?.contains(event.target)) closeMenu();
        });
        document.addEventListener("keydown", onMenuKeyDown);
    }

    window.APStudyCalendarEventMenu = {
        activateEvent,
        activateEventElement,
        closeMenu,
        getEventMenuItems,
        handleEventKeyDown: onEventKeyDown,
        openKeyboardContextMenu,
        register,
    };
    document.addEventListener("DOMContentLoaded", register);
}());
