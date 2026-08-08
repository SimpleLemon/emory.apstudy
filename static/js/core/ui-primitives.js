/*
 * Shared UI primitive ownership:
 * - Form field invalid-state helpers
 * - Loading and skeleton markup
 * - Toast notifications
 * - Confirmation dialogs
 *
 * This file is intentionally classic/deferred so every feature can consume the
 * globals below before global.js or a feature entry point executes.
 */
(() => {
    if (window.APStudyUIPrimitives) return;

    const escapeHtml = (value) => {
        const div = document.createElement('div');
        div.textContent = value == null ? '' : String(value);
        return div.innerHTML
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    };

    const normalizeFields = (fields) => (Array.isArray(fields) ? fields : [fields]).filter(Boolean);
    const autoClearHandlers = new WeakMap();
    window.APStudyFormField = window.APStudyFormField || {
        markInvalid(fields, { focus = true } = {}) {
            const list = normalizeFields(fields);
            list.forEach((field) => field.setAttribute('aria-invalid', 'true'));
            if (focus) list[0]?.focus?.({ preventScroll: true });
            return list[0] || null;
        },
        clearInvalid(fields) {
            normalizeFields(fields).forEach((field) => field.removeAttribute('aria-invalid'));
        },
        clearAll(root = document) {
            root?.querySelectorAll?.('[aria-invalid="true"]').forEach((field) => field.removeAttribute('aria-invalid'));
        },
        bindAutoClear(fields) {
            normalizeFields(fields).forEach((field) => {
                if (autoClearHandlers.has(field)) return;
                const handler = () => field.removeAttribute('aria-invalid');
                field.addEventListener('input', handler);
                field.addEventListener('change', handler);
                autoClearHandlers.set(field, handler);
            });
        },
    };

    window.APStudyLoader = window.APStudyLoader || {
        html(label = 'Loading...', options = {}) {
            const size = Number(options.sizePx) > 0 ? Number(options.sizePx) : 42;
            const tone = escapeHtml(options.textToneClass || 'text-on-surface-variant');
            return `<div class="flex w-full flex-col items-center justify-center gap-2 text-center text-sm ${tone}"><span class="loader" style="width:${size}px;height:${size}px" aria-hidden="true"></span><span>${escapeHtml(label)}</span></div>`;
        },
    };

    const skeletonBlock = (className = '') => `<div data-slot="skeleton" class="bg-muted rounded-md animate-pulse ${escapeHtml(className)}"></div>`;
    const skeletonShell = (label, className, body) => `<div class="apstudy-skeleton ${escapeHtml(className)}" role="status" aria-live="polite" aria-busy="true"><span class="sr-only">${escapeHtml(label)}</span><div class="contents" aria-hidden="true">${body}</div></div>`;
    window.APStudySkeleton = window.APStudySkeleton || {
        block: skeletonBlock,
        cards(options = {}) {
            const count = Math.max(1, Number(options.count || 3));
            const cards = Array.from({ length: count }, () => `<div class="apstudy-skeleton-card rounded-md border p-4">${skeletonBlock('h-5 w-32')}${skeletonBlock('mt-3 h-3 w-full')}</div>`).join('');
            return skeletonShell(options.label || 'Loading cards...', `apstudy-skeleton-cards ${options.className || ''}`, cards);
        },
        table(options = {}) {
            const rows = Math.max(1, Number(options.rows || 4));
            const body = Array.from({ length: rows }, () => `<div class="grid gap-4">${skeletonBlock('h-4 w-full')}</div>`).join('');
            return skeletonShell(options.label || 'Loading table...', `apstudy-skeleton-table ${options.className || ''}`, body);
        },
        fieldSet(options = {}) {
            const fields = Math.max(1, Number(options.fields || 4));
            const body = Array.from({ length: fields }, () => `<div class="grid gap-2">${skeletonBlock('h-3 w-20')}${skeletonBlock('h-10 w-full')}</div>`).join('');
            return skeletonShell(options.label || 'Loading form...', `apstudy-skeleton-fieldset ${options.className || ''}`, body);
        },
    };

    function ensureToastHost() {
        let host = document.getElementById('apstudy-toast-host');
        if (host) return host;
        host = document.createElement('div');
        host.id = 'apstudy-toast-host';
        host.className = 'apstudy-toast-host';
        host.setAttribute('role', 'region');
        host.setAttribute('aria-label', 'Notifications');
        (document.body || document.documentElement).appendChild(host);
        return host;
    }

    const toastIcons = {
        info: '<circle cx="12" cy="12" r="9"></circle><path d="M12 10.75v5.5"></path><path d="M12 7.5h.01"></path>',
        success: '<circle cx="12" cy="12" r="9"></circle><path d="m8.25 12.25 2.5 2.5 5-5.25"></path>',
        warning: '<path d="M10.25 4.25 3.2 17a2 2 0 0 0 1.75 3h14.1A2 2 0 0 0 20.8 17L13.75 4.25a2 2 0 0 0-3.5 0Z"></path><path d="M12 9v4.5"></path><path d="M12 17h.01"></path>',
        error: '<circle cx="12" cy="12" r="9"></circle><path d="m9 9 6 6"></path><path d="m15 9-6 6"></path>',
    };

    function toastText(value) {
        if (value instanceof Error) return String(value.message || '').trim();
        return value == null ? '' : String(value).trim();
    }

    function toastDuration(options, type, hasDetail, hasAction) {
        if (options.duration != null && Number.isFinite(Number(options.duration))) {
            return Math.max(0, Number(options.duration));
        }
        if (hasAction) return 10_000;
        if (hasDetail || type === 'warning' || type === 'error') return 7_000;
        return 4_000;
    }

    function createToastIcon(type) {
        const icon = document.createElement('span');
        icon.className = 'apstudy-toast__icon';
        icon.setAttribute('aria-hidden', 'true');
        const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        svg.setAttribute('viewBox', '0 0 24 24');
        svg.setAttribute('fill', 'none');
        svg.setAttribute('stroke', 'currentColor');
        svg.setAttribute('stroke-width', '1.9');
        svg.setAttribute('stroke-linecap', 'round');
        svg.setAttribute('stroke-linejoin', 'round');
        svg.innerHTML = toastIcons[type];
        icon.appendChild(svg);
        return icon;
    }

    function showToast(options = {}) {
        if (typeof options === 'string') options = { message: options };
        if (!options || typeof options !== 'object') return null;
        const type = ['info', 'success', 'warning', 'error'].includes(options.type) ? options.type : 'info';
        const titleText = toastText(options.title);
        const messageText = toastText(options.message);
        const primaryText = titleText || messageText;
        const detailText = titleText && messageText ? messageText : '';
        if (!primaryText) return null;
        const hasAction = Boolean(options.action && (options.action.label || options.action.text));
        const duration = toastDuration(options, type, Boolean(detailText), hasAction);
        const toast = document.createElement('div');
        toast.className = `apstudy-toast is-${type}`;
        toast.classList.toggle('is-compact', !detailText && !hasAction);
        toast.classList.toggle('has-detail', Boolean(detailText));
        toast.classList.toggle('has-action', hasAction);
        toast.setAttribute('role', type === 'error' ? 'alert' : 'status');
        toast.setAttribute('aria-atomic', 'true');
        const copy = document.createElement('div');
        copy.className = 'apstudy-toast__copy';
        const title = document.createElement('p');
        title.className = 'apstudy-toast__title';
        title.textContent = primaryText;
        copy.appendChild(title);
        if (detailText) {
            const message = document.createElement('p');
            message.className = 'apstudy-toast__message';
            message.textContent = detailText;
            copy.appendChild(message);
        }
        if (hasAction) {
            const action = document.createElement('button');
            action.type = 'button';
            action.className = 'apstudy-toast__action';
            action.textContent = String(options.action.label || options.action.text);
            action.addEventListener('click', async () => {
                let keepOpen = false;
                try {
                    const result = typeof options.action.onClick === 'function'
                        ? options.action.onClick()
                        : false;
                    if (result && typeof result.then === 'function') {
                        action.disabled = true;
                        action.setAttribute('aria-busy', 'true');
                        keepOpen = await result === true;
                    } else {
                        keepOpen = result === true;
                    }
                } catch (error) {
                    keepOpen = true;
                    if (typeof options.action.onError === 'function') {
                        options.action.onError(error);
                    }
                } finally {
                    action.disabled = false;
                    action.removeAttribute('aria-busy');
                }
                if (options.action.href) {
                    dismiss('action');
                    window.location.assign(options.action.href);
                } else if (!keepOpen) {
                    dismiss('action');
                }
            });
            copy.appendChild(action);
        }
        const close = document.createElement('button');
        close.type = 'button';
        close.className = 'apstudy-toast__close';
        close.setAttribute('aria-label', 'Dismiss notification');
        close.textContent = '×';
        const progress = duration > 0 ? document.createElement('span') : null;
        if (progress) {
            progress.className = 'apstudy-toast__progress';
            progress.setAttribute('aria-hidden', 'true');
            toast.style.setProperty('--toast-duration', `${duration}ms`);
        }
        toast.append(createToastIcon(type), copy, close);
        if (progress) toast.appendChild(progress);
        let dismissed = false;
        let timer = null;
        let remaining = duration;
        let startedAt = 0;
        const pauseReasons = new Set();
        const dismiss = (reason = 'programmatic') => {
            if (dismissed) return;
            dismissed = true;
            if (timer) window.clearTimeout(timer);
            if (typeof options.onDismiss === 'function') {
                try {
                    options.onDismiss(reason);
                } catch (error) {
                    console.error('Toast dismissal handler failed.', error);
                }
            }
            toast.classList.add('is-leaving');
            window.setTimeout(() => toast.remove(), 400);
        };
        close.addEventListener('click', () => dismiss('close'));
        const pauseTimer = () => {
            if (!timer) return;
            remaining = Math.max(0, remaining - (performance.now() - startedAt));
            window.clearTimeout(timer);
            timer = null;
            toast.classList.add('is-paused');
        };
        const resumeTimer = () => {
            if (dismissed || duration <= 0 || timer || pauseReasons.size) return;
            if (remaining <= 0) {
                dismiss('timeout');
                return;
            }
            toast.classList.remove('is-paused');
            startedAt = performance.now();
            timer = window.setTimeout(() => dismiss('timeout'), remaining);
        };
        const setPaused = (reason, paused) => {
            if (paused) pauseReasons.add(reason);
            else pauseReasons.delete(reason);
            if (pauseReasons.size) pauseTimer();
            else resumeTimer();
        };
        toast.addEventListener('mouseenter', () => setPaused('pointer', true));
        toast.addEventListener('mouseleave', () => setPaused('pointer', false));
        toast.addEventListener('focusin', () => setPaused('focus', true));
        toast.addEventListener('focusout', (event) => {
            if (!toast.contains(event.relatedTarget)) setPaused('focus', false);
        });
        ensureToastHost().prepend(toast);
        requestAnimationFrame(() => {
            toast.classList.add('is-visible');
            progress?.classList.add('is-running');
        });
        resumeTimer();
        return { dismiss };
    }
    window.APStudyToast = window.APStudyToast || {
        show: showToast,
        info: (message, options = {}) => showToast({ ...options, message, type: 'info' }),
        success: (message, options = {}) => showToast({ ...options, message, type: 'success' }),
        warning: (message, options = {}) => showToast({ ...options, message, type: 'warning' }),
        error: (message, options = {}) => showToast({ ...options, message, type: 'error' }),
    };

    const pendingUndoOperations = new Set();

    function undoErrorMessage(error, fallback) {
        const message = error instanceof Error ? error.message : String(error || '');
        return message.trim() || fallback;
    }

    function stageUndoableAction(options = {}) {
        if (!options || typeof options !== 'object') return null;
        const message = toastText(options.message);
        if (!message) return null;

        const record = {
            settled: false,
            restored: false,
            toast: null,
        };

        const restore = async (reason) => {
            if (record.restored) return;
            record.restored = true;
            if (typeof options.restore === 'function') {
                await options.restore({ reason });
            }
        };

        const undo = async (reason = 'action') => {
            if (record.settled) return;
            record.settled = true;
            pendingUndoOperations.delete(record);
            try {
                await restore(reason);
                if (typeof options.onUndo === 'function') options.onUndo({ reason });
            } catch (error) {
                window.APStudyToast?.error?.(
                    undoErrorMessage(error, options.undoErrorMessage || 'Refresh the page to recover the item.'),
                    { title: options.undoErrorTitle || 'Couldn’t undo removal' },
                );
            }
        };

        const commit = async (reason = 'timeout') => {
            if (record.settled) return;
            record.settled = true;
            pendingUndoOperations.delete(record);
            try {
                if (typeof options.commit === 'function') {
                    await options.commit({ reason });
                }
                if (typeof options.onCommit === 'function') options.onCommit({ reason });
            } catch (error) {
                try {
                    await restore('commit-error');
                } catch (restoreError) {
                    console.error('Unable to restore an item after deletion failed.', restoreError);
                }
                if (typeof options.onCommitError === 'function') {
                    options.onCommitError(error);
                } else {
                    window.APStudyToast?.error?.(
                        undoErrorMessage(error, options.errorMessage || 'The item was restored. Try again in a moment.'),
                        { title: options.errorTitle || 'Couldn’t delete item' },
                    );
                }
            }
        };

        pendingUndoOperations.add(record);
        record.commit = commit;
        record.toast = showToast({
            message,
            title: options.title,
            type: options.type || 'info',
            duration: options.duration == null ? 10_000 : options.duration,
            action: {
                label: options.actionLabel || 'Undo',
                onClick: () => {
                    void undo('action');
                },
            },
            onDismiss: (reason) => {
                if (reason !== 'action') void commit(reason);
            },
        });
        return {
            commit: () => {
                record.toast?.dismiss?.('commit');
            },
            dismiss: () => {
                record.toast?.dismiss?.('programmatic');
            },
            undo: () => {
                void undo('programmatic');
                record.toast?.dismiss?.('action');
            },
        };
    }

    window.APStudyUndo = window.APStudyUndo || {
        stage: stageUndoableAction,
        pendingCount: () => pendingUndoOperations.size,
    };

    window.addEventListener('pagehide', () => {
        [...pendingUndoOperations].forEach((record) => {
            void record.commit?.('pagehide');
        });
    });

    let activeConfirm = null;
    function ensureConfirm() {
        let root = document.getElementById('apstudy-confirm-root');
        if (root) return root;
        root = document.createElement('div');
        root.id = 'apstudy-confirm-root';
        root.className = 'apstudy-confirm';
        root.hidden = true;
        root.innerHTML = `<div class="apstudy-confirm__panel" role="dialog" aria-modal="true" aria-labelledby="apstudy-confirm-title" aria-describedby="apstudy-confirm-message"><div class="apstudy-confirm__icon" aria-hidden="true"><span class="material-symbols-outlined" aria-hidden="true">warning</span></div><div class="apstudy-confirm__copy"><h2 id="apstudy-confirm-title"></h2><p id="apstudy-confirm-message"></p></div><div class="apstudy-confirm__actions"><button type="button" class="apstudy-confirm__cancel" data-confirm-cancel>Cancel</button><button type="button" class="apstudy-confirm__accept" data-confirm-accept>Confirm</button></div></div>`;
        document.body.appendChild(root);
        root.querySelector('[data-confirm-cancel]').addEventListener('click', () => closeConfirm(false));
        root.querySelector('[data-confirm-accept]').addEventListener('click', () => closeConfirm(true));
        root.addEventListener('pointerdown', (event) => event.target === root && closeConfirm(false));
        return root;
    }
    function closeConfirm(value) {
        if (!activeConfirm) return;
        const record = activeConfirm;
        activeConfirm = null;
        record.root.hidden = true;
        document.body.classList.remove('apstudy-confirm-open');
        record.resolve(Boolean(value));
        record.previousFocus?.focus?.({ preventScroll: true });
    }
    window.APStudyConfirm = window.APStudyConfirm || {
        request(options = {}) {
            if (activeConfirm) closeConfirm(false);
            const root = ensureConfirm();
            root.querySelector('#apstudy-confirm-title').textContent = String(options.title || 'Are you sure?');
            root.querySelector('#apstudy-confirm-message').textContent = String(options.message || 'This action cannot be undone.');
            root.querySelector('[data-confirm-accept]').textContent = String(options.acceptLabel || options.confirmLabel || 'Confirm');
            root.querySelector('[data-confirm-cancel]').textContent = String(options.cancelLabel || 'Cancel');
            root.classList.toggle('is-danger', options.danger !== false);
            root.hidden = false;
            document.body.classList.add('apstudy-confirm-open');
            return new Promise((resolve) => {
                activeConfirm = { root, resolve, previousFocus: document.activeElement };
                requestAnimationFrame(() => root.querySelector('[data-confirm-cancel]')?.focus({ preventScroll: true }));
            });
        },
    };
    document.addEventListener('keydown', (event) => {
        if (activeConfirm && event.key === 'Escape') {
            event.preventDefault();
            closeConfirm(false);
        }
    });

    window.APStudyUIPrimitives = Object.freeze({
        escapeHtml,
        form: window.APStudyFormField,
        loader: window.APStudyLoader,
        skeleton: window.APStudySkeleton,
        toast: window.APStudyToast,
        undo: window.APStudyUndo,
        confirm: window.APStudyConfirm,
    });
})();
