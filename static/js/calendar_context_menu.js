// Calendar context menu: intercept right-clicks and show custom menu
(function () {
    const rootSelector = "#calendar-view-root";
    let menuEl = null;
    let currentContext = null;

    function ensureMenu() {
        if (menuEl) return menuEl;
        menuEl = document.createElement("div");
        menuEl.setAttribute("role", "menu");
        menuEl.setAttribute("aria-label", "Calendar context menu");
        menuEl.className = "z-50 p-1.5 rounded-xl border shadow-lg bg-surface-container text-on-surface border-outline-variant/10";
        menuEl.style.position = "fixed";
        menuEl.style.minWidth = "170px";
        menuEl.style.display = "none";
        document.body.appendChild(menuEl);

        return menuEl;
    }

    function closeMenu() {
        if (!menuEl) return;
        menuEl.style.display = "none";
        menuEl.innerHTML = "";
        currentContext = null;
    }

    function buildMenu(items) {
        const el = ensureMenu();
        el.innerHTML = "";
        items.forEach((it, i) => {
            const btn = document.createElement("button");
            btn.setAttribute("role", "menuitem");
            btn.className = "w-full text-left px-3 py-1.5 text-sm rounded-md hover:bg-surface-container-highest";
            btn.style.display = 'block';
            btn.style.gap = '4px';
            btn.textContent = it.label;
            btn.tabIndex = i === 0 ? 0 : -1;
            btn.addEventListener("click", (e) => {
                it.onClick(currentContext);
                closeMenu();
            });
            el.appendChild(btn);
        });
        return el;
    }

    function getClosestAttribute(el, attr) {
        let cur = el;
        while (cur && cur !== document.body) {
            if (cur.hasAttribute && cur.hasAttribute(attr)) return cur.getAttribute(attr);
            cur = cur.parentElement;
        }
        return null;
    }

    function onContextMenu(e) {
        const root = document.querySelector(rootSelector);
        if (!root) return;
        // only handle right-clicks within calendar root
        if (!root.contains(e.target)) return;

        e.preventDefault();
        const context = {
            x: e.clientX,
            y: e.clientY,
            eventId: null,
            date: null,
            time: null,
            anchorEl: null,
        };
        // detect event element
        const eventEl = e.target.closest && e.target.closest('[data-event-id]');
        const dateAttrEl = e.target.closest && e.target.closest('[data-date]');
        const timeAttrEl = e.target.closest && e.target.closest('[data-time]');

        context.eventId = eventEl ? eventEl.getAttribute('data-event-id') : null;
        context.date = dateAttrEl ? dateAttrEl.getAttribute('data-date') : null;
        context.time = timeAttrEl ? timeAttrEl.getAttribute('data-time') : null;
        context.anchorEl = eventEl || dateAttrEl || e.target;

        // prepare context
        currentContext = context;

        // if menu open, replace
        closeMenu();
        currentContext = context;

        if (context.eventId) {
            const items = [
                { label: 'View / Edit Event', onClick: openEdit },
                { label: 'Duplicate Event', onClick: duplicateEvent },
                { label: 'Delete Event', onClick: deleteEvent },
            ];
            const el = buildMenu(items);
            showMenuAt(context.x, context.y, el);
            return;
        }

        // empty slot
        const items = [
            { label: 'Create New Event', onClick: createNewFromContext },
            { label: 'Go to Today', onClick: goToToday },
        ];
        const el = buildMenu(items);
        showMenuAt(context.x, context.y, el);
    }

    function showMenuAt(x, y, el) {
        const menu = ensureMenu();
        menu.style.display = "block";
        // position and avoid overflow
        const pad = 8;
        const rect = menu.getBoundingClientRect();
        let left = x;
        let top = y;
        if (left + rect.width + pad > window.innerWidth) left = Math.max(pad, window.innerWidth - rect.width - pad);
        if (top + rect.height + pad > window.innerHeight) top = Math.max(pad, window.innerHeight - rect.height - pad);
        menu.style.left = `${left}px`;
        menu.style.top = `${top}px`;
        // focus first item
        const first = menu.querySelector("[role=menuitem]");
        if (first) {
            menu.querySelectorAll("[role=menuitem]").forEach((it) => (it.tabIndex = -1));
            first.tabIndex = 0;
            first.focus();
        }
    }

    // action handlers
    function createNewFromContext(ctx) {
        const startDate = ctx.date;
        const time = ctx.time;
        const payload = {};
        if (startDate && time) {
            payload.start = `${startDate}T${time}:00`;
            // compute end one hour later
            const dt = new Date(`${startDate}T${time}:00`);
            dt.setHours(dt.getHours() + 1);
            payload.end = dt.toISOString();
        } else if (startDate) {
            payload.start = `${startDate}`;
            // default to 9:00 - 10:00 local
            payload.end = `${startDate}T10:00:00`;
        }
        // open form
        window.openCalendarEventForm && window.openCalendarEventForm({ mode: 'create', data: payload });
    }

    function goToToday() {
        if (window.state) {
            window.state.anchorDate = new Date();
            window.render && window.render();
        }
    }

    async function openEdit(ctx) {
        if (!ctx.eventId) return;
        try {
            const res = await fetch(`/api/calendar/events/${ctx.eventId}`, { credentials: 'same-origin' });
            if (!res.ok) throw new Error('fetch failed');
            const payload = await res.json();
            const ev = payload.event;
            window.openCalendarEventForm && window.openCalendarEventForm({ mode: 'edit', data: ev });
        } catch (err) {
            alert('Unable to load event');
        }
    }

    async function duplicateEvent(ctx) {
        if (!ctx.eventId) return;
        try {
            const res = await fetch(`/api/calendar/events/${ctx.eventId}`, { credentials: 'same-origin' });
            if (!res.ok) throw new Error('fetch failed');
            const payload = await res.json();
            const ev = payload.event;
            ev.title = `${ev.title || 'Untitled'} (Copy)`;
            // clear id to create new
            delete ev.id;
            window.openCalendarEventForm && window.openCalendarEventForm({ mode: 'create', data: ev });
        } catch (err) {
            alert('Unable to duplicate event');
        }
    }

    async function deleteEvent(ctx) {
        if (!ctx.eventId) return;
        if (!confirm('Are you sure you want to delete this event?')) return;
        try {
            const res = await fetch(`/api/calendar/events/${ctx.eventId}`, { method: 'DELETE', credentials: 'same-origin' });
            if (!res.ok) throw new Error('delete failed');
            // refresh calendar
            window.loadCalendarData && window.loadCalendarData();
        } catch (err) {
            alert('Unable to delete event');
        }
    }

    // attach
    document.addEventListener('DOMContentLoaded', () => {
        const root = document.querySelector(rootSelector);
        if (!root) return;
        root.addEventListener('contextmenu', onContextMenu);
        // close on scroll of the calendar scroller
        const scroller = document.getElementById('calendar-week-time-scroller');
        if (scroller) scroller.addEventListener('scroll', closeMenu);
        // persistent document listeners for dismissal and keyboard handling
        document.addEventListener('click', (e) => {
            // ignore right-click mouse events which have button === 2
            if (e instanceof MouseEvent && e.button === 2) return;
            if (!menuEl) return;
            if (menuEl.style.display === 'none') return;
            if (!menuEl.contains(e.target)) closeMenu();
        });

        document.addEventListener('keydown', (e) => {
            if (!menuEl || menuEl.style.display === 'none') return;
            const items = Array.from(menuEl.querySelectorAll('[role=menuitem]'));
            const idx = items.findIndex((it) => it.tabIndex === 0);
            if (e.key === 'Escape') {
                e.preventDefault();
                closeMenu();
                (currentContext && currentContext.anchorEl)?.focus?.();
                return;
            }
            if (e.key === 'ArrowDown') {
                e.preventDefault();
                const next = items[(idx + 1) % items.length] || items[0];
                items.forEach((it) => (it.tabIndex = -1));
                next.tabIndex = 0;
                next.focus();
                return;
            }
            if (e.key === 'ArrowUp') {
                e.preventDefault();
                const prev = items[(idx - 1 + items.length) % items.length] || items[items.length - 1];
                items.forEach((it) => (it.tabIndex = -1));
                prev.tabIndex = 0;
                prev.focus();
                return;
            }
            if (e.key === 'Tab') {
                closeMenu();
                return;
            }
            if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                items[idx] && items[idx].click();
            }
        });
    });

})();
