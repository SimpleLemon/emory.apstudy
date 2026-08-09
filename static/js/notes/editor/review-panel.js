import { escapeHtml } from '../../core/ui-primitives-module.js';

function requestId() {
    return globalThis.crypto?.randomUUID?.() || `comment-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

async function apiJson(url, options = {}, signal) {
    const response = await fetch(url, {
        ...options,
        signal,
        headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.error || 'Unable to load review data.');
    return payload;
}

function statusBadge(status) {
    return `<span class="notes-review-badge notes-review-badge--${escapeHtml(status || 'open')}">${escapeHtml(status || 'open')}</span>`;
}

function suggestionHtml(suggestion, canManageReviews) {
    return `
        <article class="notes-review-card notes-suggestion-card" data-suggestion-id="${escapeHtml(suggestion.id)}">
            <header><strong>${escapeHtml(suggestion.author?.name || 'Reviewer')}</strong>${statusBadge(suggestion.status)}</header>
            <p>${escapeHtml(suggestion.summary || suggestion.operation_kind || 'Suggested change')}</p>
            ${canManageReviews && suggestion.status === 'open' ? `
                <div class="notes-review-card-actions">
                    <button type="button" data-suggestion-action="accept" data-suggestion-id="${escapeHtml(suggestion.id)}">Accept</button>
                    <button type="button" data-suggestion-action="reject" data-suggestion-id="${escapeHtml(suggestion.id)}">Reject</button>
                </div>` : ''}
        </article>`;
}

function replyHtml(reply) {
    const deleted = Boolean(reply.deleted_at);
    return `
        <div class="notes-comment-reply${deleted ? ' is-deleted' : ''}" data-reply-id="${escapeHtml(reply.id)}">
            <p><strong>${escapeHtml(reply.author?.name || 'User')}</strong> ${deleted ? '<em>Reply deleted</em>' : escapeHtml(reply.body)}</p>
            ${reply.edited_at && !deleted ? '<small>Edited</small>' : ''}
            ${!deleted && (reply.can_edit || reply.can_delete) ? `<div class="notes-comment-message-actions">
                ${reply.can_edit ? '<button type="button" data-reply-action="edit">Edit</button>' : ''}
                ${reply.can_delete ? '<button type="button" data-reply-action="delete">Delete</button>' : ''}
            </div>` : ''}
        </div>`;
}

function commentHtml(thread) {
    const deleted = Boolean(thread.deleted_at);
    const detached = thread.anchor?.state === 'detached';
    return `
        <article class="notes-review-card notes-comment-card${detached ? ' is-detached' : ''}" data-comment-id="${escapeHtml(thread.id)}" tabindex="0">
            <header>
                <span class="notes-comment-author"><i style="--comment-color:${escapeHtml(thread.author?.color || '')}"></i><strong>${escapeHtml(thread.author?.name || 'Commenter')}</strong></span>
                ${statusBadge(detached ? 'detached' : thread.status)}
            </header>
            <p>${deleted ? '<em>Comment deleted</em>' : escapeHtml(thread.body || '')}</p>
            ${thread.edited_at && !deleted ? '<small>Edited</small>' : ''}
            ${thread.anchor?.quoted_text ? `<blockquote>${escapeHtml(thread.anchor.quoted_text)}</blockquote>` : ''}
            ${(thread.replies || []).length ? `<div class="notes-review-replies">${thread.replies.map(replyHtml).join('')}</div>` : ''}
            ${!deleted ? `<form class="notes-review-reply-form" data-comment-reply="${escapeHtml(thread.id)}">
                <input type="text" name="body" placeholder="Reply" maxlength="5000" required>
                <button type="submit">Reply</button>
            </form>` : ''}
            <div class="notes-review-card-actions">
                ${thread.can_edit && !deleted ? '<button type="button" data-comment-action="edit">Edit</button>' : ''}
                ${thread.can_delete && !deleted ? '<button type="button" data-comment-action="delete">Delete</button>' : ''}
                ${thread.can_resolve && !deleted ? `<button type="button" data-comment-action="${thread.status === 'resolved' ? 'reopen' : 'resolve'}">${thread.status === 'resolved' ? 'Reopen' : 'Resolve'}</button>` : ''}
            </div>
        </article>`;
}

function versionHtml(version) {
    return `<article class="notes-review-card"><header><strong>${escapeHtml(version.name || version.reason || 'Snapshot')}</strong><small>${escapeHtml(version.created_at || '')}</small></header><p>${escapeHtml(version.actor?.name ? `By ${version.actor.name}` : 'Automatic version')}</p><div class="notes-review-card-actions"><button type="button" data-version-restore="${escapeHtml(version.id)}">Restore</button></div></article>`;
}

function emptyHtml(message) {
    return `<p class="notes-review-empty">${escapeHtml(message)}</p>`;
}

export function bindReviewPanel({
    noteId,
    canReview,
    canManageReviews,
    canViewVersions,
    panel,
    reviewButton,
    historyButton,
    toast,
    captureAnchor,
    onThreads,
    onSelectThread,
    anchorTop,
} = {}) {
    if (!noteId || !panel) return null;
    const title = panel.querySelector('[data-review-panel-title]');
    const body = panel.querySelector('[data-review-panel-body]');
    const closeButton = panel.querySelector('[data-review-panel-close]');
    const lifecycle = new AbortController();
    let loadController = null;
    let mode = 'review';
    let tab = 'comments';
    let filter = 'open';
    let threads = [];
    let pendingAnchor = null;
    let activeThreadId = null;
    const inFlight = new Set();

    function visibleThreads() {
        return threads.filter((thread) => {
            if (filter === 'resolved') return thread.status === 'resolved';
            if (filter === 'detached') return thread.anchor?.state === 'detached';
            return thread.status === 'open' && thread.anchor?.state !== 'detached' && !thread.deleted_at;
        });
    }

    function alignCards() {
        if (window.innerWidth < 1180 || tab !== 'comments') return;
        let previousBottom = 0;
        panel.querySelectorAll('[data-comment-id]').forEach((card) => {
            const thread = threads.find((item) => String(item.id) === card.dataset.commentId);
            const top = anchorTop?.(thread);
            if (!Number.isFinite(top)) return;
            const desired = Math.max(0, top - panel.getBoundingClientRect().top - 80);
            const offset = Math.max(desired, previousBottom);
            card.style.marginTop = `${Math.max(0, offset - previousBottom)}px`;
            previousBottom = offset + card.offsetHeight + 10;
        });
    }

    function renderComments() {
        const visible = visibleThreads();
        title.textContent = 'Review';
        body.innerHTML = `
            <div class="notes-review-tabs" role="tablist">
                <button type="button" role="tab" data-review-tab="comments" aria-selected="true">Comments</button>
                <button type="button" role="tab" data-review-tab="suggestions" aria-selected="false">Suggestions</button>
            </div>
            <div class="notes-review-filters" aria-label="Comment filters">
                ${['open', 'resolved', 'detached'].map((value) => `<button type="button" data-review-filter="${value}" class="${filter === value ? 'is-active' : ''}">${value[0].toUpperCase()}${value.slice(1)}</button>`).join('')}
            </div>
            ${pendingAnchor ? `<form class="notes-review-new-comment" data-comment-create>
                ${pendingAnchor.quoted_text ? `<blockquote>${escapeHtml(pendingAnchor.quoted_text)}</blockquote>` : ''}
                <textarea name="body" rows="3" maxlength="5000" placeholder="Add a comment" required autofocus></textarea>
                <div><button type="button" data-comment-cancel>Cancel</button><button type="submit">Comment</button></div>
                <p class="notes-comment-form-error" data-comment-error hidden></p>
            </form>` : ''}
            <section class="notes-review-section notes-comment-thread-list">
                ${visible.length ? visible.map(commentHtml).join('') : emptyHtml(`No ${filter} comments.`)}
            </section>`;
        requestAnimationFrame(alignCards);
    }

    async function renderSuggestions() {
        const payload = await apiJson(`/api/notes/${encodeURIComponent(noteId)}/suggestions`, {}, loadController?.signal);
        title.textContent = 'Review';
        body.innerHTML = `
            <div class="notes-review-tabs" role="tablist">
                <button type="button" role="tab" data-review-tab="comments" aria-selected="false">Comments</button>
                <button type="button" role="tab" data-review-tab="suggestions" aria-selected="true">Suggestions</button>
            </div>
            <section class="notes-review-section">${(payload.suggestions || []).length ? payload.suggestions.map((item) => suggestionHtml(item, canManageReviews)).join('') : emptyHtml('No suggestions yet.')}</section>`;
    }

    async function loadComments() {
        const payload = await apiJson(`/api/notes/${encodeURIComponent(noteId)}/comments`, {}, loadController?.signal);
        threads = payload.threads || [];
        onThreads?.(threads, activeThreadId);
        renderComments();
    }

    async function renderReview() {
        if (!canReview) return;
        loadController?.abort();
        loadController = new AbortController();
        body.innerHTML = emptyHtml('Loading review activity...');
        if (tab === 'suggestions') await renderSuggestions();
        else await loadComments();
    }

    async function renderHistory() {
        if (!canViewVersions) return;
        loadController?.abort();
        loadController = new AbortController();
        title.textContent = 'Version history';
        body.innerHTML = emptyHtml('Loading versions...');
        const payload = await apiJson(`/api/notes/${encodeURIComponent(noteId)}/versions`, {}, loadController.signal);
        body.innerHTML = `<section class="notes-review-section"><h3>Snapshots</h3>${(payload.versions || []).length ? payload.versions.map(versionHtml).join('') : emptyHtml('No versions yet.')}</section><form class="notes-review-new-version" data-version-create><input type="text" name="name" placeholder="Snapshot name"><button type="submit">Create snapshot</button></form>`;
    }

    async function open(nextMode = 'review') {
        mode = nextMode;
        panel.hidden = false;
        panel.dataset.mode = mode;
        try {
            if (mode === 'history') await renderHistory();
            else await renderReview();
        } catch (error) {
            if (error.name !== 'AbortError') body.innerHTML = emptyHtml(error.message || 'Unable to load panel.');
        }
    }

    function close() { panel.hidden = true; }

    function startComment(anchor = captureAnchor?.()) {
        if (!canReview) return;
        pendingAnchor = anchor || { kind: 'document', state: 'detached', version: 1 };
        tab = 'comments';
        filter = 'open';
        void open('review').then(() => body.querySelector('[data-comment-create] textarea')?.focus());
    }

    function selectThread(id) {
        activeThreadId = String(id || '');
        onThreads?.(threads, activeThreadId);
        const thread = threads.find((item) => String(item.id) === activeThreadId);
        if (thread) onSelectThread?.(thread);
        panel.querySelectorAll('[data-comment-id]').forEach((card) => card.classList.toggle('is-active', card.dataset.commentId === activeThreadId));
    }

    async function perform(key, action) {
        if (inFlight.has(key)) return;
        inFlight.add(key);
        try { await action(); } finally { inFlight.delete(key); }
    }

    function handleClick(event) {
        const tabButton = event.target.closest('[data-review-tab]');
        if (tabButton) { tab = tabButton.dataset.reviewTab; void renderReview(); return; }
        const filterButton = event.target.closest('[data-review-filter]');
        if (filterButton) { filter = filterButton.dataset.reviewFilter; renderComments(); return; }
        if (event.target.closest('[data-comment-cancel]')) { pendingAnchor = null; renderComments(); return; }
        const card = event.target.closest('[data-comment-id]');
        if (card && !event.target.closest('button,input,form')) selectThread(card.dataset.commentId);
        const suggestionAction = event.target.closest('[data-suggestion-action]');
        if (suggestionAction) void perform(`suggestion:${suggestionAction.dataset.suggestionId}`, async () => {
            await apiJson(`/api/notes/${encodeURIComponent(noteId)}/suggestions/${encodeURIComponent(suggestionAction.dataset.suggestionId)}/${suggestionAction.dataset.suggestionAction}`, { method: 'POST', body: '{}' }, lifecycle.signal);
            toast?.show?.({ message: 'Suggestion updated.', type: 'success' });
            await renderReview();
        });
        const actionButton = event.target.closest('[data-comment-action]');
        if (actionButton && card) void perform(`comment:${card.dataset.commentId}`, async () => {
            const action = actionButton.dataset.commentAction;
            if (action === 'edit') {
                const current = threads.find((item) => String(item.id) === card.dataset.commentId);
                const value = window.prompt('Edit comment', current?.body || '');
                if (value == null) return;
                await apiJson(`/api/notes/${encodeURIComponent(noteId)}/comments/${encodeURIComponent(card.dataset.commentId)}`, { method: 'PATCH', body: JSON.stringify({ body: value }) }, lifecycle.signal);
            } else if (action === 'delete') {
                if (!window.confirm('Delete this comment?')) return;
                await apiJson(`/api/notes/${encodeURIComponent(noteId)}/comments/${encodeURIComponent(card.dataset.commentId)}`, { method: 'DELETE', body: '{}' }, lifecycle.signal);
            } else {
                await apiJson(`/api/notes/${encodeURIComponent(noteId)}/comments/${encodeURIComponent(card.dataset.commentId)}/${action}`, { method: 'POST', body: '{}' }, lifecycle.signal);
            }
            await loadComments();
        });
        const replyAction = event.target.closest('[data-reply-action]');
        const reply = replyAction?.closest('[data-reply-id]');
        if (replyAction && reply && card) void perform(`reply:${reply.dataset.replyId}`, async () => {
            const url = `/api/notes/${encodeURIComponent(noteId)}/comments/${encodeURIComponent(card.dataset.commentId)}/replies/${encodeURIComponent(reply.dataset.replyId)}`;
            if (replyAction.dataset.replyAction === 'delete') {
                if (!window.confirm('Delete this reply?')) return;
                await apiJson(url, { method: 'DELETE', body: '{}' }, lifecycle.signal);
            } else {
                const thread = threads.find((item) => String(item.id) === card.dataset.commentId);
                const current = thread?.replies?.find((item) => String(item.id) === reply.dataset.replyId);
                const value = window.prompt('Edit reply', current?.body || '');
                if (value == null) return;
                await apiJson(url, { method: 'PATCH', body: JSON.stringify({ body: value }) }, lifecycle.signal);
            }
            await loadComments();
        });
        const versionRestore = event.target.closest('[data-version-restore]');
        if (versionRestore && window.confirm('Restore this note version? A snapshot of the current state will be kept.')) void perform('restore', async () => {
            await apiJson(`/api/notes/${encodeURIComponent(noteId)}/versions/${encodeURIComponent(versionRestore.dataset.versionRestore)}/restore`, { method: 'POST', body: '{}' }, lifecycle.signal);
            window.location.reload();
        });
    }

    function handleSubmit(event) {
        const commentCreate = event.target.closest('[data-comment-create]');
        const commentReply = event.target.closest('[data-comment-reply]');
        const versionCreate = event.target.closest('[data-version-create]');
        if (!commentCreate && !commentReply && !versionCreate) return;
        event.preventDefault();
        const form = event.target;
        const formData = new FormData(form);
        const bodyValue = String(formData.get('body') || '').trim();
        const key = `submit:${commentReply?.dataset.commentReply || (commentCreate ? 'new' : 'version')}`;
        void perform(key, async () => {
            const submit = form.querySelector('[type="submit"]');
            if (submit) submit.disabled = true;
            try {
                if (commentCreate) {
                    await apiJson(`/api/notes/${encodeURIComponent(noteId)}/comments`, { method: 'POST', body: JSON.stringify({ body: bodyValue, anchor: pendingAnchor, client_request_id: requestId() }) }, lifecycle.signal);
                    pendingAnchor = null;
                    await loadComments();
                } else if (commentReply) {
                    await apiJson(`/api/notes/${encodeURIComponent(noteId)}/comments/${encodeURIComponent(commentReply.dataset.commentReply)}/replies`, { method: 'POST', body: JSON.stringify({ body: bodyValue, client_request_id: requestId() }) }, lifecycle.signal);
                    await loadComments();
                } else {
                    const name = String(formData.get('name') || '').trim() || 'Manual snapshot';
                    await apiJson(`/api/notes/${encodeURIComponent(noteId)}/versions`, { method: 'POST', body: JSON.stringify({ name }) }, lifecycle.signal);
                    await renderHistory();
                }
            } catch (error) {
                const errorNode = form.querySelector('[data-comment-error]');
                if (errorNode) { errorNode.textContent = error.message; errorNode.hidden = false; }
                else throw error;
            } finally {
                if (submit) submit.disabled = false;
            }
        });
    }

    const options = { signal: lifecycle.signal };
    reviewButton?.addEventListener('click', () => open('review'), options);
    historyButton?.addEventListener('click', () => open('history'), options);
    closeButton?.addEventListener('click', close, options);
    panel.addEventListener('click', handleClick, options);
    panel.addEventListener('submit', handleSubmit, options);
    window.addEventListener('resize', alignCards, options);
    window.addEventListener('scroll', alignCards, { ...options, passive: true });

    return {
        open,
        close,
        startComment,
        selectThread,
        refresh: () => (mode === 'review' && !panel.hidden ? renderReview() : Promise.resolve()),
        refreshDecorations: () => onThreads?.(threads, activeThreadId),
        destroy() {
            loadController?.abort();
            lifecycle.abort();
            panel.hidden = true;
            onThreads?.([], null);
        },
    };
}
