import { escapeHtml } from '../../core/ui-primitives-module.js';

async function apiJson(url, options = {}) {
    const response = await fetch(url, {
        ...options,
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
        <article class="notes-review-card">
            <header>
                <strong>${escapeHtml(suggestion.author?.name || 'Reviewer')}</strong>
                ${statusBadge(suggestion.status)}
            </header>
            <p>${escapeHtml(suggestion.summary || suggestion.operation_kind || 'Suggested change')}</p>
            ${canManageReviews && suggestion.status === 'open' ? `
                <div class="notes-review-card-actions">
                    <button type="button" data-suggestion-action="accept" data-suggestion-id="${escapeHtml(suggestion.id)}">Accept</button>
                    <button type="button" data-suggestion-action="reject" data-suggestion-id="${escapeHtml(suggestion.id)}">Reject</button>
                </div>
            ` : ''}
        </article>
    `;
}

function commentHtml(thread, canManageReviews) {
    return `
        <article class="notes-review-card">
            <header>
                <strong>${escapeHtml(thread.author?.name || 'Commenter')}</strong>
                ${statusBadge(thread.status)}
            </header>
            <p>${escapeHtml(thread.body || '')}</p>
            ${thread.quoted_text ? `<blockquote>${escapeHtml(thread.quoted_text)}</blockquote>` : ''}
            ${thread.replies?.length ? `
                <div class="notes-review-replies">
                    ${thread.replies.map((reply) => `<p><strong>${escapeHtml(reply.author?.name || 'User')}:</strong> ${escapeHtml(reply.body)}</p>`).join('')}
                </div>
            ` : ''}
            <form class="notes-review-reply-form" data-comment-reply="${escapeHtml(thread.id)}">
                <input type="text" name="body" placeholder="Reply">
                <button type="submit">Reply</button>
            </form>
            ${canManageReviews ? `
                <div class="notes-review-card-actions">
                    <button type="button" data-comment-action="${thread.status === 'resolved' ? 'reopen' : 'resolve'}" data-comment-id="${escapeHtml(thread.id)}">
                        ${thread.status === 'resolved' ? 'Reopen' : 'Resolve'}
                    </button>
                </div>
            ` : ''}
        </article>
    `;
}

function versionHtml(version) {
    return `
        <article class="notes-review-card">
            <header>
                <strong>${escapeHtml(version.name || version.reason || 'Snapshot')}</strong>
                <small>${escapeHtml(version.created_at || '')}</small>
            </header>
            <p>${escapeHtml(version.actor?.name ? `By ${version.actor.name}` : 'Automatic version')}</p>
            <div class="notes-review-card-actions">
                <button type="button" data-version-restore="${escapeHtml(version.id)}">Restore</button>
            </div>
        </article>
    `;
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
} = {}) {
    if (!noteId || !panel) return null;
    const title = panel.querySelector('[data-review-panel-title]');
    const body = panel.querySelector('[data-review-panel-body]');
    const closeButton = panel.querySelector('[data-review-panel-close]');
    let mode = 'review';

    async function renderReview() {
        if (!canReview) return;
        title.textContent = 'Review';
        body.innerHTML = emptyHtml('Loading review activity...');
        const [suggestions, comments] = await Promise.all([
            apiJson(`/api/notes/${encodeURIComponent(noteId)}/suggestions`),
            apiJson(`/api/notes/${encodeURIComponent(noteId)}/comments`),
        ]);
        body.innerHTML = `
            <section class="notes-review-section">
                <h3>Suggestions</h3>
                ${(suggestions.suggestions || []).length ? suggestions.suggestions.map((item) => suggestionHtml(item, canManageReviews)).join('') : emptyHtml('No suggestions yet.')}
            </section>
            <section class="notes-review-section">
                <h3>Comments</h3>
                ${(comments.threads || []).length ? comments.threads.map((thread) => commentHtml(thread, canManageReviews)).join('') : emptyHtml('No comments yet.')}
                <form class="notes-review-new-comment" data-comment-create>
                    <textarea name="body" rows="3" placeholder="Add a comment"></textarea>
                    <button type="submit">Comment</button>
                </form>
            </section>
        `;
    }

    async function renderHistory() {
        if (!canViewVersions) return;
        title.textContent = 'Version history';
        body.innerHTML = emptyHtml('Loading versions...');
        const payload = await apiJson(`/api/notes/${encodeURIComponent(noteId)}/versions`);
        body.innerHTML = `
            <section class="notes-review-section">
                <h3>Snapshots</h3>
                ${(payload.versions || []).length ? payload.versions.map(versionHtml).join('') : emptyHtml('No versions yet.')}
            </section>
            <form class="notes-review-new-version" data-version-create>
                <input type="text" name="name" placeholder="Snapshot name">
                <button type="submit">Create snapshot</button>
            </form>
        `;
    }

    async function open(nextMode) {
        mode = nextMode;
        panel.hidden = false;
        panel.dataset.mode = mode;
        try {
            if (mode === 'history') await renderHistory();
            else await renderReview();
        } catch (error) {
            body.innerHTML = emptyHtml(error.message || 'Unable to load panel.');
        }
    }

    function close() {
        panel.hidden = true;
    }

    reviewButton?.addEventListener('click', () => open('review'));
    historyButton?.addEventListener('click', () => open('history'));
    closeButton?.addEventListener('click', close);
    panel.addEventListener('click', async (event) => {
        const suggestionAction = event.target.closest('[data-suggestion-action]');
        if (suggestionAction) {
            await apiJson(`/api/notes/${encodeURIComponent(noteId)}/suggestions/${encodeURIComponent(suggestionAction.dataset.suggestionId)}/${suggestionAction.dataset.suggestionAction}`, { method: 'POST', body: '{}' });
            toast?.show?.({ message: 'Suggestion updated.', type: 'success' });
            await renderReview();
            return;
        }
        const commentAction = event.target.closest('[data-comment-action]');
        if (commentAction) {
            await apiJson(`/api/notes/${encodeURIComponent(noteId)}/comments/${encodeURIComponent(commentAction.dataset.commentId)}/${commentAction.dataset.commentAction}`, { method: 'POST', body: '{}' });
            toast?.show?.({ message: 'Comment updated.', type: 'success' });
            await renderReview();
            return;
        }
        const versionRestore = event.target.closest('[data-version-restore]');
        if (versionRestore && window.confirm('Restore this note version? A snapshot of the current state will be kept.')) {
            await apiJson(`/api/notes/${encodeURIComponent(noteId)}/versions/${encodeURIComponent(versionRestore.dataset.versionRestore)}/restore`, { method: 'POST', body: '{}' });
            toast?.show?.({ message: 'Version restored. Reloading note...', type: 'success' });
            window.location.reload();
        }
    });
    panel.addEventListener('submit', async (event) => {
        const commentCreate = event.target.closest('[data-comment-create]');
        const commentReply = event.target.closest('[data-comment-reply]');
        const versionCreate = event.target.closest('[data-version-create]');
        if (!commentCreate && !commentReply && !versionCreate) return;
        event.preventDefault();
        const formData = new FormData(event.target);
        const bodyValue = String(formData.get('body') || '').trim();
        if (commentCreate) {
            await apiJson(`/api/notes/${encodeURIComponent(noteId)}/comments`, { method: 'POST', body: JSON.stringify({ body: bodyValue }) });
            await renderReview();
            return;
        }
        if (commentReply) {
            await apiJson(`/api/notes/${encodeURIComponent(noteId)}/comments/${encodeURIComponent(commentReply.dataset.commentReply)}/replies`, { method: 'POST', body: JSON.stringify({ body: bodyValue }) });
            await renderReview();
            return;
        }
        const name = String(formData.get('name') || '').trim() || 'Manual snapshot';
        await apiJson(`/api/notes/${encodeURIComponent(noteId)}/versions`, { method: 'POST', body: JSON.stringify({ name }) });
        await renderHistory();
    });

    return { open, close };
}
