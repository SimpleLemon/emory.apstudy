// Simple event create/edit modal and API integration
(function () {
    let modal = null;
    let currentMode = 'create';
    let currentEventId = null;

    function ensureModal() {
        if (modal) return modal;
        modal = document.createElement('div');
        modal.className = 'fixed inset-0 z-50 flex items-center justify-center p-4';
        modal.style.display = 'none';
        modal.innerHTML = `
            <div id="apstudy-event-backdrop" class="absolute inset-0 bg-black/50" tabindex="-1"></div>
            <div role="dialog" aria-modal="true" aria-label="Event form" class="relative w-full max-w-lg rounded-2xl bg-surface-container text-on-surface border border-outline-variant/10 shadow-xl p-5">
                <h3 class="text-lg font-semibold text-on-surface mb-3">Event</h3>
                <form id="apstudy-event-form">
                    <div class="mb-2">
                        <label class="text-sm text-on-surface-variant">Title</label>
                        <input name="title" required class="w-full mt-1 p-2 rounded-lg bg-surface-container-highest text-on-surface border border-outline-variant/20 focus:outline-none focus:ring-2 focus:ring-primary focus:border-primary" />
                    </div>
                    <div class="mb-2">
                        <label class="text-sm text-on-surface-variant">Description</label>
                        <textarea name="description" class="w-full mt-1 p-2 rounded-lg bg-surface-container-highest text-on-surface border border-outline-variant/20 focus:outline-none focus:ring-2 focus:ring-primary focus:border-primary" rows="3"></textarea>
                    </div>
                    <div class="grid grid-cols-2 gap-2">
                        <div>
                            <label class="text-sm text-on-surface-variant">Start</label>
                            <input name="start" type="datetime-local" class="w-full mt-1 p-2 rounded-lg bg-surface-container-highest text-on-surface border border-outline-variant/20 focus:outline-none focus:ring-2 focus:ring-primary focus:border-primary" />
                        </div>
                        <div>
                            <label class="text-sm text-on-surface-variant">End</label>
                            <input name="end" type="datetime-local" class="w-full mt-1 p-2 rounded-lg bg-surface-container-highest text-on-surface border border-outline-variant/20 focus:outline-none focus:ring-2 focus:ring-primary focus:border-primary" />
                        </div>
                    </div>
                    <div class="flex items-center gap-3 mt-3">
                        <label class="flex items-center gap-2 text-sm text-on-surface-variant"><input name="all_day" type="checkbox" class="accent-primary" /> All day</label>
                        <label class="flex items-center gap-2 text-sm text-on-surface-variant">Color <input name="color" type="color" class="h-8 w-8 p-0 rounded-full border border-outline-variant/20" /></label>
                    </div>
                    <div class="flex justify-end gap-2 mt-4">
                        <button type="button" id="apstudy-event-cancel" class="px-3 py-1.5 rounded-lg bg-surface-container-high text-on-surface hover:bg-surface-container">Cancel</button>
                        <button type="submit" id="apstudy-event-submit" class="px-3 py-1.5 rounded-lg bg-primary text-on-primary hover:opacity-90">Save</button>
                    </div>
                    <div id="apstudy-event-error" class="text-sm text-red-400 mt-2 hidden"></div>
                </form>
            </div>
        `;
        document.body.appendChild(modal);

        modal.querySelector('#apstudy-event-cancel').addEventListener('click', closeModal);
        modal.querySelector('form').addEventListener('submit', onSubmit);
        modal.addEventListener('click', (e) => {
            if (e.target && e.target.id === 'apstudy-event-backdrop') closeModal();
        });

        return modal;
    }

    function closeModal() {
        const m = ensureModal();
        m.style.display = 'none';
        currentEventId = null;
    }

    function isoLocalForInput(dtStr) {
        if (!dtStr) return '';
        // accept date-only or full ISO
        if (/^\d{4}-\d{2}-\d{2}$/.test(dtStr)) return dtStr + 'T00:00';
        const d = new Date(dtStr);
        if (isNaN(d)) return '';
        // produce yyyy-mm-ddThh:mm
        const pad = (n) => String(n).padStart(2, '0');
        return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
    }

    function openForm({ mode = 'create', data = {} } = {}) {
        const m = ensureModal();
        currentMode = mode;
        const form = m.querySelector('form');
        form.reset();
        m.querySelector('#apstudy-event-error').classList.add('hidden');

        form.title.value = data.title || '';
        form.description.value = data.description || '';
        form.start.value = isoLocalForInput(data.start || data.start_date || data.startDate);
        form.end.value = isoLocalForInput(data.end || data.end_date || data.endDate);
        form.all_day.checked = Boolean(data.is_all_day || data.all_day || false);
        form.color.value = data.color || '#6366f1';

        if (mode === 'edit' && data.id) currentEventId = data.id;
        m.style.display = 'flex';
    }

    async function onSubmit(e) {
        e.preventDefault();
        const m = ensureModal();
        const form = m.querySelector('form');
        const payload = {
            title: form.title.value.trim(),
            description: form.description.value.trim(),
            start_date: form.start.value ? new Date(form.start.value).toISOString() : null,
            end_date: form.end.value ? new Date(form.end.value).toISOString() : null,
            all_day: form.all_day.checked,
            color: form.color.value,
        };

        if (!payload.title) {
            showError('Title is required');
            return;
        }

        if (!payload.start_date || !payload.end_date) {
            showError('Start and end are required');
            return;
        }

        try {
            let res;
            if (currentMode === 'edit' && currentEventId) {
                res = await fetch(`/api/calendar/events/${currentEventId}`, {
                    method: 'PUT',
                    credentials: 'same-origin',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload),
                });
            } else {
                res = await fetch('/api/calendar/events', {
                    method: 'POST',
                    credentials: 'same-origin',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload),
                });
            }

            const json = await res.json();
            if (!res.ok) {
                showError(json.error || 'Save failed');
                return;
            }
            closeModal();
            // refresh calendar
            window.loadCalendarData && window.loadCalendarData();
        } catch (err) {
            showError('Save failed');
        }
    }

    function showError(msg) {
        const m = ensureModal();
        const el = m.querySelector('#apstudy-event-error');
        el.textContent = msg;
        el.classList.remove('hidden');
    }

    // expose for context menu
    window.openCalendarEventForm = function (opts) {
        openForm(opts);
    };

    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') closeModal();
    });

})();
