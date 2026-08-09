import { Extension } from '@tiptap/core';
import { Plugin, PluginKey, Selection } from '@tiptap/pm/state';
import { Decoration, DecorationSet } from '@tiptap/pm/view';
import * as Y from 'yjs';
import {
    absolutePositionToRelativePosition,
    relativePositionToAbsolutePosition,
    ySyncPluginKey,
} from 'y-prosemirror';

const commentPluginKey = new PluginKey('nest-inline-comments');
const reviewModePluginKey = new PluginKey('nest-review-mode');

function blockInfoAtPosition(doc, position) {
    const resolved = doc.resolve(Math.max(0, Math.min(position, doc.content.size)));
    for (let depth = resolved.depth; depth > 0; depth -= 1) {
        const node = resolved.node(depth);
        const id = node?.attrs?.id;
        if (!id) continue;
        const start = resolved.before(depth) + 1;
        return { id: String(id), start, node };
    }
    return null;
}

function blockPosition(doc, id) {
    let match = null;
    doc.descendants((node, position) => {
        if (match || String(node?.attrs?.id || '') !== String(id || '')) return;
        match = { start: position + 1, node };
    });
    return match;
}

function syncBinding(state) {
    const sync = ySyncPluginKey.getState(state);
    return sync?.binding && !sync.binding.isDestroyed ? sync.binding : null;
}

function yjsRelativeJson(state, position) {
    const binding = syncBinding(state);
    if (!binding) return null;
    const relative = absolutePositionToRelativePosition(position, binding.type, binding.mapping);
    return JSON.stringify(Y.relativePositionToJSON(relative));
}

function absoluteFromYjs(state, value) {
    if (!value) return null;
    const binding = syncBinding(state);
    if (!binding) return null;
    try {
        return relativePositionToAbsolutePosition(
            binding.doc,
            binding.type,
            Y.createRelativePositionFromJSON(JSON.parse(value)),
            binding.mapping
        );
    } catch (error) {
        return null;
    }
}

export function captureCommentAnchor(editor) {
    const state = editor?._tiptapEditor?.state;
    if (!state) return { kind: 'document', state: 'detached', version: 1 };
    const { from, to, empty } = state.selection;
    const startBlock = blockInfoAtPosition(state.doc, from);
    const endBlock = blockInfoAtPosition(state.doc, to);
    const quotedText = empty ? '' : state.doc.textBetween(from, to, ' ', ' ').slice(0, 1000);
    const relativeStart = yjsRelativeJson(state, from);
    const relativeEnd = yjsRelativeJson(state, to);
    if (relativeStart && relativeEnd) {
        return {
            kind: 'yjs',
            state: 'attached',
            version: 1,
            relative_start: relativeStart,
            relative_end: relativeEnd,
            start_block_id: startBlock?.id || null,
            end_block_id: endBlock?.id || startBlock?.id || null,
            quoted_text: quotedText,
        };
    }
    if (!startBlock) return { kind: 'document', state: 'detached', version: 1, quoted_text: quotedText };
    const startOffset = Math.max(0, from - startBlock.start);
    const endOffset = Math.max(0, to - (endBlock?.start ?? startBlock.start));
    const beforeStart = Math.max(startBlock.start, from - 80);
    const afterEnd = Math.min(state.doc.content.size, to + 80);
    return {
        kind: 'legacy',
        state: 'attached',
        version: 1,
        start_block_id: startBlock.id,
        start_offset: startOffset,
        end_block_id: endBlock?.id || startBlock.id,
        end_offset: endOffset,
        quoted_text: quotedText,
        context_before: state.doc.textBetween(beforeStart, from, ' ', ' ').slice(-250),
        context_after: state.doc.textBetween(to, afterEnd, ' ', ' ').slice(0, 250),
    };
}

export function resolveCommentAnchor(editor, anchor) {
    const state = editor?._tiptapEditor?.state;
    if (!state || !anchor || anchor.state === 'detached') return null;
    if (anchor.kind === 'yjs') {
        const from = absoluteFromYjs(state, anchor.relative_start);
        const to = absoluteFromYjs(state, anchor.relative_end);
        if (from == null || to == null) return null;
        return { from: Math.min(from, to), to: Math.max(from, to) };
    }
    const start = blockPosition(state.doc, anchor.start_block_id);
    const end = blockPosition(state.doc, anchor.end_block_id || anchor.start_block_id);
    if (!start || !end) return null;
    const from = Math.min(state.doc.content.size, start.start + Number(anchor.start_offset || 0));
    const to = Math.min(state.doc.content.size, end.start + Number(anchor.end_offset || 0));
    return { from: Math.min(from, to), to: Math.max(from, to) };
}

function decorationsForThreads(state, threads, activeId) {
    const decorations = [];
    for (const thread of threads || []) {
        if (thread.status !== 'open' || thread.deleted_at) continue;
        const range = resolveCommentAnchor({ _tiptapEditor: { state } }, thread.anchor);
        if (!range) continue;
        const from = range.from;
        const to = range.to > from ? range.to : Math.min(state.doc.content.size, from + 1);
        if (to <= from) continue;
        decorations.push(Decoration.inline(from, to, {
            class: `notes-comment-highlight${String(thread.id) === String(activeId || '') ? ' is-active' : ''}`,
            'data-comment-id': String(thread.id),
            tabindex: '0',
            role: 'button',
            'aria-label': `Open comment by ${thread.author?.name || 'collaborator'}`,
        }));
    }
    return DecorationSet.create(state.doc, decorations);
}

export function updateCommentDecorations(editor, threads, activeId) {
    const tiptap = editor?._tiptapEditor;
    if (!tiptap) return;
    tiptap.view.dispatch(tiptap.state.tr.setMeta(commentPluginKey, { threads, activeId }));
}

export function scrollToCommentAnchor(editor, thread) {
    const tiptap = editor?._tiptapEditor;
    const range = resolveCommentAnchor(editor, thread?.anchor);
    if (!tiptap || !range) return false;
    const coordinates = tiptap.view.coordsAtPos(range.from);
    window.scrollTo({ top: Math.max(0, window.scrollY + coordinates.top - 180), behavior: 'smooth' });
    tiptap.view.dispatch(tiptap.state.tr.setSelection(Selection.near(tiptap.state.doc.resolve(range.from))));
    return true;
}

export function commentAnchorViewportTop(editor, thread) {
    const tiptap = editor?._tiptapEditor;
    const range = resolveCommentAnchor(editor, thread?.anchor);
    if (!tiptap || !range) return null;
    try {
        return tiptap.view.coordsAtPos(range.from).top;
    } catch (error) {
        return null;
    }
}

export function createNotesReviewExtension({ getMode, onSuggestion } = {}) {
    return Extension.create({
        name: 'nestReviewRuntime',
        addProseMirrorPlugins() {
            return [
                new Plugin({
                    key: commentPluginKey,
                    state: {
                        init: () => DecorationSet.empty,
                        apply(transaction, previous, _oldState, newState) {
                            const payload = transaction.getMeta(commentPluginKey);
                            if (payload) return decorationsForThreads(newState, payload.threads, payload.activeId);
                            return transaction.docChanged ? previous.map(transaction.mapping, transaction.doc) : previous;
                        },
                    },
                    props: { decorations: (state) => commentPluginKey.getState(state) },
                }),
                new Plugin({
                    key: reviewModePluginKey,
                    filterTransaction(transaction, state) {
                        if (!transaction.docChanged) return true;
                        const mode = getMode?.() || 'editing';
                        if (mode === 'editing') return true;
                        if (mode === 'suggesting') {
                            queueMicrotask(() => onSuggestion?.({
                                operation_kind: 'document_change',
                                target_kind: 'body',
                                summary: 'Suggested document change',
                                operations: [{
                                    type: 'replace_document',
                                    before: state.doc.toJSON(),
                                    after: transaction.doc.toJSON(),
                                }],
                            }));
                        }
                        return false;
                    },
                }),
            ];
        },
    });
}
