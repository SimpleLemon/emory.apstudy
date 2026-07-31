import * as React from 'react';
import { createRoot } from 'react-dom/client';

import {
    SideMenuController,
    SuggestionMenuController,
    useCreateBlockNote,
    useEditorContentOrSelectionChange,
} from '@blocknote/react';
import { BlockNoteView } from '@blocknote/mantine';
import { History } from '@tiptap/extension-history';
import { notesEditorSchema } from '../toolbar.js';
import {
    blockIconClass,
    blockPayloadForCatalogItem,
    catalogItemByKey,
    filterBlockCatalog,
} from './block-catalog.js';
import { listItemHardBreakShortcuts, preserveRangeSelectionShortcuts, createSelectAllShortcuts } from './keyboard-shortcuts.js';
import { normalizeCopiedPlainText, normalizeImportedMarkdownBlocks } from './markdown-repair.js';
import { buildLoadingIndicatorHtml, documentHasText, isBlankTitle } from './utils.js';
import { handleNotesPaste } from './paste.js';

const NORMAL_HISTORY_DEPTH = 100;
const LONG_DOCUMENT_HISTORY_DEPTH = 35;
const LARGE_DOCUMENT_BLOCK_COUNT = 120;
const GRAMMARLY_DISABLED_ATTRS = 'data-gramm="false" data-gramm_editor="false" data-enable-grammarly="false" spellcheck="false"';

export function createReactShell({
    noteContext,
    noteId,
    titleInput,
    blocknoteRoot,
    writingToolbar,
    editorPage,
    shareButton,
    collaboratorsRoot,
    getCanEdit,
    setEditorReadOnlyMode,
    getEditor,
    setEditorInstance,
    setNotePrintReady,
    setNoteCollaborationEnabled,
    setSaveStatus,
    setLastSavedPayloadFingerprint,
    notePayloadFingerprint,
    getEditorPageDisposed,
    currentDocumentSnapshot,
    invalidateDocumentSnapshot,
    captureHistoryBaseline,
    updateEditorChrome,
    triggerDebouncedSave,
    bindWritingToolbar,
    bindImageRuntime,
    insertImageFromDialog,
    bindCollaborativeTitle,
    bindLazyReviewPanel,
    pageSetup,
    safeSetBlockSelection,
    selectBlockRange,
    insertCatalogItem,
    copySelectedBlocks,
    duplicateSelectedBlocks,
    deleteSelectedBlocks,
    moveSelectedBlocks,
    toggleHeadingCollapse,
    updateBlockPayloadForPreservedText,
    focusEditorBody,
}) {
    let noteEditorReactRoot = null;
    let editorLoadController = null;
    let editorReadyTimer = null;
    let editorInitialFocusTimer = null;
    let activeCollaborationSession = null;
    let activeCollaborativeTitleCleanup = null;

    function historyDepthForDocument(documentValue) {
        return (Array.isArray(documentValue) ? documentValue.length : 0) >= LARGE_DOCUMENT_BLOCK_COUNT
            ? LONG_DOCUMENT_HISTORY_DEPTH
            : NORMAL_HISTORY_DEPTH;
    }

    function materialIcon(name, className = 'material-symbols-outlined') {
        return React.createElement('span', {
            className,
            'aria-hidden': 'true',
        }, name);
    }

    function iconHtml(icon) {
        if (!icon) return '';
        if (icon.startsWith?.('H')) {
            return icon;
        }
        return icon;
    }

    function renderMissingNoteState(message = 'This note could not be opened.') {
        if (titleInput) {
            titleInput.value = '';
            titleInput.disabled = true;
        }
        if (writingToolbar) writingToolbar.hidden = true;
        if (blocknoteRoot) {
            blocknoteRoot.innerHTML = `
            <div class="notes-editor-empty-state">
                <span class="material-symbols-outlined" aria-hidden="true">description</span>
                <h2>Note unavailable</h2>
                <p>${message}</p>
                <a href="/notes" class="btn-primary">Back to notes</a>
            </div>
        `;
        }
        setSaveStatus('error', { message: 'Unable to load', retry: false });
    }

    function NotesSlashMenu(props) {
        const { items, selectedIndex, onItemClick } = props;
        return React.createElement(
            'div',
            { className: 'notes-slash-menu', role: 'listbox' },
            items.map((item, index) => React.createElement(
                'button',
                {
                    key: item.key || item.label,
                    type: 'button',
                    className: `notes-slash-item${index === selectedIndex ? ' is-active' : ''}`,
                    role: 'option',
                    'aria-selected': String(index === selectedIndex),
                    onMouseDown: (event) => event.preventDefault(),
                    onClick: (event) => {
                        event.stopPropagation();
                        onItemClick?.(item);
                    },
                },
                React.createElement('span', { className: blockIconClass(item.icon), 'aria-hidden': 'true' }, iconHtml(item.icon)),
                React.createElement('span', null,
                    React.createElement('strong', null, item.label),
                    React.createElement('small', null, item.description)
                )
            ))
        );
    }

    function NotesSideMenu(props) {
        const { block, blockDragStart, blockDragEnd, freezeMenu, unfreezeMenu } = props;
        const [open, setOpen] = React.useState(false);
        const [turnIntoOpen, setTurnIntoOpen] = React.useState(false);
        const toolsRef = React.useRef(null);
        const closeMenu = React.useCallback(() => {
            setTurnIntoOpen(false);
            setOpen(false);
        }, []);
        const selectActionBlock = React.useCallback(() => {
            if (!getEditor() || !block) return;
            safeSetBlockSelection(block, block);
        }, [block]);

        React.useEffect(() => {
            if (!open) return undefined;

            freezeMenu?.();
            const handlePointerDown = (event) => {
                if (toolsRef.current?.contains(event.target)) return;
                closeMenu();
            };
            const handleKeyDown = (event) => {
                if (event.key !== 'Escape') return;
                event.preventDefault();
                closeMenu();
            };
            document.addEventListener('pointerdown', handlePointerDown);
            document.addEventListener('keydown', handleKeyDown);

            return () => {
                document.removeEventListener('pointerdown', handlePointerDown);
                document.removeEventListener('keydown', handleKeyDown);
                unfreezeMenu?.();
            };
        }, [closeMenu, freezeMenu, open, unfreezeMenu]);

        const select = (event) => {
            event.preventDefault();
            selectBlockRange(block, event.shiftKey);
        };
        const addBelow = async (event) => {
            event.preventDefault();
            getEditor()?.setTextCursorPosition?.(block);
            await insertCatalogItem(catalogItemByKey('paragraph'), event.currentTarget.getBoundingClientRect());
        };
        const duplicate = () => {
            selectActionBlock();
            duplicateSelectedBlocks();
            closeMenu();
        };
        const remove = () => {
            selectActionBlock();
            deleteSelectedBlocks();
            closeMenu();
        };
        const turnIntoItems = filterBlockCatalog('', { includeAtoms: false, turnIntoOnly: true });
        const turnIntoMenu = turnIntoOpen ? React.createElement(
            'div',
            { className: 'notes-side-submenu' },
            turnIntoItems.map((item) => React.createElement(
                'button',
                {
                    key: item.key,
                    type: 'button',
                    onClick: () => {
                        selectActionBlock();
                        const payload = blockPayloadForCatalogItem(item);
                        getEditor().updateBlock(block, updateBlockPayloadForPreservedText(block, payload));
                        closeMenu();
                        updateEditorChrome();
                        triggerDebouncedSave();
                    },
                },
                React.createElement('span', { className: blockIconClass(item.icon), 'aria-hidden': 'true' }, iconHtml(item.icon)),
                React.createElement('span', null, item.label)
            ))
        ) : null;
        return React.createElement(
            'div',
            {
                ref: toolsRef,
                className: 'notes-side-tools',
                onClick: (event) => event.stopPropagation(),
            },
            React.createElement('button', {
                type: 'button',
                className: 'notes-side-button',
                title: 'Add block below',
                'aria-label': 'Add block below',
                onMouseDown: (event) => event.preventDefault(),
                onClick: addBelow,
            }, materialIcon('add')),
            React.createElement('button', {
                type: 'button',
                className: 'notes-side-button notes-block-select-handle',
                title: 'Select block',
                'aria-label': 'Select block',
                draggable: true,
                onDragStart: (event) => blockDragStart?.(event, block),
                onDragEnd: blockDragEnd,
                onClick: select,
            }, materialIcon('drag_indicator')),
            React.createElement('button', {
                type: 'button',
                className: 'notes-side-button',
                title: 'Block actions',
                'aria-label': 'Block actions',
                'aria-expanded': String(open),
                onMouseDown: (event) => event.preventDefault(),
                onClick: () => {
                    if (open) {
                        closeMenu();
                    } else {
                        setOpen(true);
                    }
                },
            }, materialIcon('more_vert')),
            open ? React.createElement(
            'div',
            {
                className: 'notes-side-menu',
                onMouseDown: (event) => event.preventDefault(),
            },
                React.createElement('button', { type: 'button', onClick: () => { selectActionBlock(); void copySelectedBlocks(); closeMenu(); } }, materialIcon('content_copy'), React.createElement('span', null, 'Copy')),
                React.createElement('button', { type: 'button', 'aria-expanded': String(turnIntoOpen), onClick: () => setTurnIntoOpen(!turnIntoOpen) }, materialIcon('swap_vert'), React.createElement('span', null, 'Turn into')),
                turnIntoMenu,
                React.createElement('button', { type: 'button', onClick: duplicate }, materialIcon('content_copy'), React.createElement('span', null, 'Duplicate')),
                React.createElement('button', { type: 'button', onClick: () => { selectActionBlock(); moveSelectedBlocks('up'); closeMenu(); } }, materialIcon('arrow_upward'), React.createElement('span', null, 'Move up')),
                React.createElement('button', { type: 'button', onClick: () => { selectActionBlock(); moveSelectedBlocks('down'); closeMenu(); } }, materialIcon('arrow_downward'), React.createElement('span', null, 'Move down')),
                block.type === 'heading'
                    ? React.createElement('button', { type: 'button', onClick: () => { selectActionBlock(); toggleHeadingCollapse(block); closeMenu(); } }, materialIcon(block.props?.isCollapsed ? 'unfold_more' : 'unfold_less'), React.createElement('span', null, 'Collapse'))
                    : null,
                React.createElement('button', { type: 'button', className: 'is-danger', onClick: remove }, materialIcon('delete'), React.createElement('span', null, 'Delete'))
            ) : null
        );
    }

    function NoteEditor({ initialContent, initialContentWasNormalized = false, collaborationSession = null }) {
        const canEdit = getCanEdit();
        const historyDepth = historyDepthForDocument(initialContent);
        let blockNoteEditorRef = null;
        const tiptapExtensions = [
            preserveRangeSelectionShortcuts,
            listItemHardBreakShortcuts,
            createSelectAllShortcuts(() => blockNoteEditorRef),
        ];
        if (!collaborationSession) {
            tiptapExtensions.unshift(History.configure({ depth: historyDepth, newGroupDelay: 500 }));
        }
        const editorOptions = {
            schema: notesEditorSchema,
            pasteHandler: (options) => handleNotesPaste({
                ...options,
                noteId,
                onChange: triggerDebouncedSave,
            }),
            disableExtensions: ['history'],
            _tiptapOptions: {
                extensions: tiptapExtensions,
            },
            placeholders: {
                default: undefined,
                emptyDocument: "Enter text or type '/' for commands",
            },
        };
        if (collaborationSession) {
            editorOptions.collaboration = {
                fragment: collaborationSession.fragment,
                provider: collaborationSession.provider,
                user: collaborationSession.user,
                showCursorLabels: 'activity',
            };
        } else {
            editorOptions.initialContent = initialContent;
        }
        const editor = useCreateBlockNote(editorOptions);
        blockNoteEditorRef = editor;

        const getSlashItems = React.useCallback(async (query) => (
            filterBlockCatalog(query).map((item) => ({
                ...item,
                onItemClick: async () => {
                    await insertCatalogItem(item, null, editor);
                },
            }))
        ), [editor]);

        React.useEffect(() => {
            setEditorInstance(editor);
            setNotePrintReady(true);
            editorReadyTimer = window.setTimeout(() => {
                editorReadyTimer = null;
                if (getEditor() !== editor) return;
                captureHistoryBaseline();
                invalidateDocumentSnapshot();
                const documentSnapshot = currentDocumentSnapshot();
                updateEditorChrome({ structureChanged: true, contentChanged: true, documentSnapshot });
                if (getCanEdit() && initialContentWasNormalized && !collaborationSession) {
                    triggerDebouncedSave();
                }
            }, 0);

            return () => {
                if (editorReadyTimer) {
                    window.clearTimeout(editorReadyTimer);
                    editorReadyTimer = null;
                }
                if (getEditor() === editor) {
                    setEditorInstance(null);
                    setNotePrintReady(false);
                }
            };
        }, [editor]);

        React.useEffect(() => {
            return bindImageRuntime({
                editor,
                editorPage,
                noteId,
                onChange: triggerDebouncedSave,
                openDialog: insertImageFromDialog,
            });
        }, [editor]);

        useEditorContentOrSelectionChange(() => {
            updateEditorChrome({ immediate: true });
        }, editor);

        return React.createElement(
            BlockNoteView,
            {
                editor,
                editable: canEdit,
                formattingToolbar: false,
                linkToolbar: false,
                sideMenu: false,
                slashMenu: false,
                filePanel: false,
                theme: document.documentElement.classList.contains('dark') ? 'dark' : 'light',
                onChange: () => {
                    invalidateDocumentSnapshot();
                    if (!getCanEdit()) return;
                    updateEditorChrome({ contentChanged: true });
                    triggerDebouncedSave();
                },
            },
            canEdit ? React.createElement(SuggestionMenuController, {
                triggerCharacter: '/',
                getItems: getSlashItems,
                suggestionMenuComponent: NotesSlashMenu,
            }) : null,
            canEdit ? React.createElement(SideMenuController, {
                sideMenu: NotesSideMenu,
            }) : null
        );
    }

    async function initEditorPage() {
        if (getEditorPageDisposed()) return;
        if (!noteId || !titleInput) {
            renderMissingNoteState('Open or create a note from the Notes page first.');
            return;
        }

        const rootElement = blocknoteRoot;
        if (!rootElement) return;

        rootElement.innerHTML = `
        <div class="rounded-2xl border border-outline-variant/20 bg-surface-container p-10 text-center min-h-[320px] flex items-center justify-center">
            ${buildLoadingIndicatorHtml('Loading note...', { sizePx: 54, textToneClass: 'text-on-surface' })}
        </div>
    `;

        let note = null;

        try {
            editorLoadController?.abort();
            editorLoadController = new AbortController();
            const response = await fetch(`/api/notes/${noteId}`, { signal: editorLoadController.signal });
            if (!response.ok) {
                throw new Error('Failed to fetch note');
            }
            note = await response.json();
        } catch (error) {
            if (error?.name === 'AbortError' || getEditorPageDisposed()) return;
            console.error(error);
            renderMissingNoteState('The note may have been deleted or is unavailable.');
            return;
        } finally {
            editorLoadController = null;
        }

        if (getEditorPageDisposed()) return;

        const noteTitle = typeof note?.title === 'string' ? note.title : '';
        titleInput.value = noteTitle;
        if (shareButton) shareButton.dataset.resourceTitle = noteTitle || 'Untitled';
        pageSetup.setLoadedPageSetup(note?.page_setup, note?.global_page_setup);
        setNoteCollaborationEnabled(note?.collaboration_enabled === true);
        if (note?.updated_at) {
            setSaveStatus('saved', { savedAt: note.updated_at });
        }
        if (typeof note?.content === 'string') {
            setLastSavedPayloadFingerprint(notePayloadFingerprint(noteTitle, note.content));
        }

        if (note?.collaboration_enabled === true) {
            setSaveStatus('connecting');
            try {
                const { createNoteCollaborationSession } = await import('./collaboration.js');
                activeCollaborationSession = await createNoteCollaborationSession({
                    noteId,
                    access: note?.access || noteContext.access,
                    presenceRoot: collaboratorsRoot,
                    onStatus: (status) => setSaveStatus(status),
                });
                activeCollaborativeTitleCleanup = bindCollaborativeTitle(activeCollaborationSession, noteTitle);
            } catch (error) {
                console.error('Failed to connect note collaboration', error);
                activeCollaborationSession = null;
                setEditorReadOnlyMode(true);
                setSaveStatus('offline-readonly');
            }
        }
        bindLazyReviewPanel({
            canReview: note?.access?.can_review === true,
            canManageReviews: note?.access?.can_manage_reviews === true,
            canViewVersions: note?.access?.can_edit === true,
        });

        let parsedContent = undefined;
        let parsedContentWasNormalized = false;
        if (typeof note?.content === 'string' && note.content.trim() !== '') {
            try {
                parsedContent = JSON.parse(note.content);
            } catch (error) {
                parsedContent = undefined;
            }
        }

        if (Array.isArray(parsedContent)) {
            const normalized = normalizeImportedMarkdownBlocks(parsedContent);
            parsedContent = normalized.blocks;
            parsedContentWasNormalized = normalized.changed;
        }

        noteEditorReactRoot = createRoot(rootElement);

        try {
            noteEditorReactRoot.render(React.createElement(NoteEditor, {
                initialContent: parsedContent,
                initialContentWasNormalized: parsedContentWasNormalized,
                collaborationSession: activeCollaborationSession,
            }));
            if (getCanEdit()) bindWritingToolbar();
        } catch (error) {
            console.error('Failed to mount note editor', error);
            setSaveStatus('error');
        }

        if (getCanEdit()) {
            titleInput.addEventListener('input', () => {
                triggerDebouncedSave();
            });
            titleInput.addEventListener('keydown', (event) => {
                if (event.key !== 'Enter') return;
                event.preventDefault();
                focusEditorBody();
            });
        }
        rootElement.addEventListener('click', (event) => {
            if (!getCanEdit()) return;
            const collapseButton = event.target.closest('.notes-heading-collapse-toggle');
            if (collapseButton) {
                event.preventDefault();
                event.stopPropagation();
                const blockId = collapseButton.closest('.bn-block-outer[data-id]')?.dataset.id;
                const block = blockId ? getEditor()?.getBlock?.(blockId) : null;
                toggleHeadingCollapse(block);
                return;
            }
            focusEditorBody();
        });
        rootElement.addEventListener('copy', normalizeNativeEditorCopy);
        rootElement.addEventListener('cut', normalizeNativeEditorCopy);
        editorInitialFocusTimer = window.setTimeout(() => {
            editorInitialFocusTimer = null;
            if (getEditorPageDisposed()) return;
            const isNewBlankNote = isBlankTitle(noteTitle) && !documentHasText(parsedContent);
            const documentSnapshot = currentDocumentSnapshot();
            updateEditorChrome({ structureChanged: true, contentChanged: true, documentSnapshot });
            if (!getCanEdit() || !isNewBlankNote) return;
            titleInput.focus({ preventScroll: true });
            titleInput.select();
        }, 0);
    }

    function clearTimers() {
        editorLoadController?.abort();
        editorLoadController = null;
        if (editorReadyTimer) window.clearTimeout(editorReadyTimer);
        if (editorInitialFocusTimer) window.clearTimeout(editorInitialFocusTimer);
        editorReadyTimer = null;
        editorInitialFocusTimer = null;
    }

    function release() {
        clearTimers();
        activeCollaborativeTitleCleanup?.();
        activeCollaborativeTitleCleanup = null;
        activeCollaborationSession?.destroy?.();
        activeCollaborationSession = null;
        noteEditorReactRoot?.unmount();
        noteEditorReactRoot = null;
    }

    function normalizeNativeEditorCopy(event) {
        const clipboardData = event.clipboardData;
        if (!clipboardData) return;
        const plainText = clipboardData.getData('text/plain');
        if (!plainText) return;
        clipboardData.setData('text/plain', normalizeCopiedPlainText(plainText));
    }

    return {
        clearTimers,
        initEditorPage,
        release,
        renderMissingNoteState,
    };
}
