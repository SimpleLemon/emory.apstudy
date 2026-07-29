(function adminDeleteUndo(global) {
    function deletionTargets(form) {
        const row = form.closest('tr');
        if (row) {
            const targets = [row];
            const detailRow = row.nextElementSibling;
            if (detailRow?.querySelector('.admin-raw-details')) targets.push(detailRow);
            return targets;
        }
        return [form.closest('.admin-danger-zone') || form];
    }

    function setTargetsHidden(targets, hidden) {
        targets.forEach((target) => {
            if (target) target.hidden = hidden;
        });
    }

    async function submitDeletion(form, options = {}) {
        const response = await fetch(form.action, {
            method: String(form.method || 'POST').toUpperCase(),
            body: new FormData(form),
            credentials: 'same-origin',
            keepalive: options.keepalive === true,
        });
        if (!response.ok) throw new Error('The deletion request was not accepted.');
        return response;
    }

    document.addEventListener('submit', (event) => {
        const form = event.target.closest('form[data-undoable-delete]');
        if (!form || !global.APStudyUndo?.stage) return;
        event.preventDefault();
        const label = form.dataset.undoableDelete || 'Item';
        const targets = deletionTargets(form);
        let responseUrl = '';
        setTargetsHidden(targets, true);
        global.APStudyUndo.stage({
            title: `${label} deletion scheduled`,
            message: 'The deletion will be completed when this notice closes.',
            type: 'warning',
            commit: async ({ reason }) => {
                const response = await submitDeletion(form, {
                    keepalive: reason === 'pagehide',
                });
                responseUrl = response.url;
            },
            restore: () => setTargetsHidden(targets, false),
            onCommit: ({ reason }) => {
                if (reason !== 'pagehide' && responseUrl) global.location.assign(responseUrl);
            },
            errorTitle: `Couldn’t delete ${label.toLowerCase()}`,
        });
    });
})(window);
