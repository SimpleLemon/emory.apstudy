import { HocuspocusProvider } from '@hocuspocus/provider';
import * as Y from 'yjs';
import { escapeHtml } from '../../core/ui-primitives-module.js';

const ACTIVE_ROLES = new Set(['owner', 'editor', 'reviewer']);
const CURSOR_COLORS = ['#7c3aed', '#0ea5e9', '#16a34a', '#f97316', '#db2777', '#0891b2', '#9333ea', '#dc2626'];

export function deterministicCollaboratorColor(seed) {
    const text = String(seed || 'nest');
    let hash = 0;
    for (let index = 0; index < text.length; index += 1) {
        hash = ((hash << 5) - hash + text.charCodeAt(index)) | 0;
    }
    return CURSOR_COLORS[Math.abs(hash) % CURSOR_COLORS.length];
}

function initialsFor(value) {
    const words = String(value || 'Nest User').trim().split(/\s+/).filter(Boolean);
    return words.slice(0, 2).map((word) => word[0]?.toUpperCase() || '').join('') || 'N';
}

function absoluteWebSocketUrl(path) {
    const target = new URL(path || '/ws/notes', window.location.href);
    target.protocol = target.protocol === 'https:' ? 'wss:' : 'ws:';
    return target.href.replace(/\/$/, '');
}

function safeUserFromToken(tokenPayload, access) {
    const user = tokenPayload.user || {};
    const role = user.role || access?.role || 'viewer';
    const id = user.id || tokenPayload.user_id || '';
    const name = user.name || user.username || 'Nest User';
    return {
        id,
        name,
        username: user.username || '',
        picture_url: user.picture_url || '',
        role,
        color: deterministicCollaboratorColor(id || name),
    };
}

export function collaboratorsFromAwareness(provider, currentUserId) {
    const states = provider?.awareness?.getStates?.();
    if (!states) return [];
    const byUser = new Map();
    states.forEach((state) => {
        const user = state?.user;
        if (!user?.id || String(user.id) === String(currentUserId || '')) return;
        const role = String(user.role || '').toLowerCase();
        if (!ACTIVE_ROLES.has(role)) return;
        byUser.set(String(user.id), {
            id: String(user.id),
            name: user.name || user.username || 'Nest User',
            username: user.username || '',
            picture_url: user.picture_url || '',
            role,
            color: user.color || deterministicCollaboratorColor(user.id),
        });
    });
    return [...byUser.values()].sort((a, b) => a.name.localeCompare(b.name));
}

function collaboratorAvatarHtml(user) {
    const color = user.color || deterministicCollaboratorColor(user.id);
    const label = `${user.name || 'Nest User'} · ${user.role === 'reviewer' ? 'Suggesting' : 'Editing'}`;
    const image = user.picture_url
        ? `<img src="${escapeHtml(user.picture_url)}" alt="" loading="lazy">`
        : `<span aria-hidden="true">${escapeHtml(initialsFor(user.name || user.username))}</span>`;
    return `
        <button type="button" class="notes-collaborator-avatar" style="--collaborator-color: ${escapeHtml(color)}" title="${escapeHtml(label)}" aria-label="${escapeHtml(label)}">
            ${image}
            <i aria-hidden="true" class="notes-collaborator-indicator notes-collaborator-indicator--${user.role === 'reviewer' ? 'suggesting' : 'editing'}"></i>
        </button>
    `;
}

export function renderTopbarPresence(root, collaborators) {
    if (!root) return;
    const visible = collaborators.slice(0, 5);
    const overflow = Math.max(0, collaborators.length - visible.length);
    root.hidden = collaborators.length === 0;
    root.innerHTML = collaborators.length ? `
        <div class="notes-collaborator-stack" aria-label="Active editors and reviewers">
            ${visible.map(collaboratorAvatarHtml).join('')}
            ${overflow ? `<button type="button" class="notes-collaborator-overflow" aria-label="${overflow} more active collaborators">+${overflow}</button>` : ''}
        </div>
        <div class="notes-collaborator-list" hidden>
            ${collaborators.map((user) => `
                <div class="notes-collaborator-list-row">
                    ${collaboratorAvatarHtml(user)}
                    <span>${escapeHtml(user.name)}</span>
                    <small>${escapeHtml(user.role === 'reviewer' ? 'Suggesting' : 'Editing')}</small>
                </div>
            `).join('')}
        </div>
    ` : '';
}

export function bindPresenceOverflow(root) {
    if (!root || root._notesPresenceOverflowBound) return;
    root._notesPresenceOverflowBound = true;
    root.addEventListener('click', (event) => {
        const trigger = event.target.closest('.notes-collaborator-overflow');
        if (!trigger) return;
        const list = root.querySelector('.notes-collaborator-list');
        if (list) list.hidden = !list.hidden;
    });
}

export async function createNoteCollaborationSession({
    noteId,
    access,
    presenceRoot,
    onStatus,
} = {}) {
    if (!noteId) return null;
    const tokenResponse = await fetch(`/api/notes/${encodeURIComponent(noteId)}/collaboration-token`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: '{}',
    });
    const tokenPayload = await tokenResponse.json().catch(() => ({}));
    if (!tokenResponse.ok) {
        throw new Error(tokenPayload.error || 'Unable to connect to note collaboration.');
    }

    const document = new Y.Doc();
    const provider = new HocuspocusProvider({
        url: absoluteWebSocketUrl(tokenPayload.provider_url || '/ws/notes'),
        name: `notes:${noteId}`,
        document,
        token: tokenPayload.token,
        onStatus: ({ status }) => {
            if (status === 'connected') onStatus?.('saved');
            if (status === 'connecting') onStatus?.('connecting');
            if (status === 'disconnected') onStatus?.('reconnecting');
        },
        onAuthenticationFailed: () => onStatus?.('offline-readonly'),
    });
    const user = safeUserFromToken(tokenPayload, access);
    if (tokenPayload.awareness_allowed) {
        provider.awareness?.setLocalStateField?.('user', user);
        const renderPresence = () => {
            renderTopbarPresence(presenceRoot, collaboratorsFromAwareness(provider, user.id));
        };
        bindPresenceOverflow(presenceRoot);
        provider.awareness?.on?.('change', renderPresence);
        renderPresence();
    }

    return {
        document,
        provider,
        fragment: document.getXmlFragment('document-store'),
        user,
        access: tokenPayload.access || access,
        destroy() {
            provider.destroy();
            document.destroy();
        },
    };
}
