import { checkBlockHasDefaultProp, checkBlockTypeHasDefaultProp, mapTableCell } from '@blocknote/core';
import {
    FONT_SIZE_PRESETS,
    blockPayloadForCatalogItem,
    catalogItemByKey,
    catalogItemByType,
} from './editor/block-catalog.js';
import { hiddenBlocksForCollapsedHeadings } from './editor/heading-collapse.js';
import { normalizeCopiedPlainText } from './editor/markdown-repair.js';
import { removeBlocksAndRestoreCursor } from './editor/block-operations.js';
import { blockOwnContentIsEmpty, documentHasText, noteIdFromPath } from './editor/utils.js';
import { bindImageRuntime, insertInlineImageFile, insertInlineImageNode, requestImageSource } from './editor/image-runtime.js';
import { createNoteSaveRuntime, notePayloadFingerprint } from './editor/save.js';
import { createPageSetupRuntime } from './editor/page-setup.js';
import { createReactShell } from './editor/react-shell.js';
import { createToolbarDom } from './editor/toolbar-dom.js';

const noteContext = window.APSTUDY_NOTE_CONTEXT || {};
const noteId = noteContext.noteId || noteIdFromPath();
let canEdit = noteContext.access?.can_edit === true;
const GRAMMARLY_DISABLED_ATTRS = 'data-gramm="false" data-gramm_editor="false" data-enable-grammarly="false" spellcheck="false"';

const EDITOR_CHROME_THROTTLE_MS = 120;
const VISUAL_INDENT_BLOCKS = new Set(['paragraph', 'heading']);
const LIST_BLOCK_TYPES = new Set(['bulletListItem', 'numberedListItem', 'checkListItem']);
const ATOM_BLOCK_TYPES = new Set(['divider', 'bookmark', 'image', 'video', 'audio', 'file']);
const MAX_INDENT_LEVEL = 4;
let editorInstance = null;
let historyBaselineDepths = { undo: 0, redo: 0 };
let selectedBlockAnchorId = null;
let urlBlockPopover = null;
let urlBlockResolve = null;
let editorChromeThrottleTimer = null;
let editorChromeRafId = null;
let editorChromeSyncNeedsStructure = false;
let editorChromeSyncSnapshot = null;
let lastSelectedBlockIds = new Set();
let lastHeadingCollapseSignature = '';
let latestDocumentSnapshot = null;
let notePrintReady = false;
let notePrintInProgress = false;
let editorPageDisposed = false;
let noteCollaborationEnabled = false;
let reviewPanelController = null;
let reviewPanelModulePromise = null;
let reviewPanelBootstrapCleanup = null;
let saveRuntime = null;
let pageSetupRuntime = null;
let toolbarDom = null;
let reactShell = null;

const titleInput = document.getElementById('note-title-input');
const saveStatus = document.getElementById('save-status');
const saveRetry = document.getElementById('save-retry');
const blocknoteRoot = document.getElementById('blocknote-root');
const writingToolbar = document.getElementById('notes-writing-toolbar');
const editorHint = document.getElementById('notes-editor-hint');
const editorPage = document.getElementById('editor-page');
const zoomValue = document.getElementById('notes-zoom-value');
const pageSetupPopover = document.getElementById('notes-page-setup-popover');
const shareButton = document.getElementById('notes-share-button');
const collaboratorsRoot = document.getElementById('notes-active-collaborators');
const reviewPanel = document.getElementById('notes-review-panel');
const reviewButton = document.getElementById('notes-review-button');
const historyButton = document.getElementById('notes-history-button');
const pageSetupScopeInput = document.querySelector('[data-page-setup-scope]');
const sideMarginsValue = document.getElementById('notes-side-margins-value');
const notePrintButtons = Array.from(document.querySelectorAll('[data-note-print]'));

function setEditorReadOnlyMode(readOnly) {
    canEdit = !readOnly && noteContext.access?.can_edit === true;
    document.body.dataset.noteReadOnly = readOnly ? 'true' : 'false';
    if (titleInput) {
        titleInput.readOnly = readOnly;
        titleInput.setAttribute('aria-readonly', readOnly ? 'true' : 'false');
    }
}

function bindCollaborativeTitle(session, fallbackTitle) {
    if (!session || !titleInput) return null;
    const yTitle = session.document.getText('title');
    let applyingRemoteTitle = false;
    const applyRemoteTitle = () => {
        const nextTitle = yTitle.toString();
        if (titleInput.value === nextTitle) return;
        applyingRemoteTitle = true;
        titleInput.value = nextTitle;
        applyingRemoteTitle = false;
    };
    const maybeInitializeTitle = () => {
        if (yTitle.length === 0 && fallbackTitle) {
            yTitle.insert(0, fallbackTitle);
        }
        applyRemoteTitle();
    };
    const handleTitleInput = () => {
        if (applyingRemoteTitle || !canEdit) return;
        yTitle.delete(0, yTitle.length);
        yTitle.insert(0, titleInput.value);
    };
    yTitle.observe(applyRemoteTitle);
    session.provider.on?.('synced', ({ state }) => {
        if (state) maybeInitializeTitle();
    });
    titleInput.addEventListener('input', handleTitleInput);
    maybeInitializeTitle();
    return () => {
        yTitle.unobserve(applyRemoteTitle);
        titleInput.removeEventListener('input', handleTitleInput);
    };
}

function closeToolbarMenus() {
    toolbarDom?.closeToolbarMenus();
}

function updateToolbarState() {
    toolbarDom?.updateToolbarState();
}

function setSaveStatus(status, options = {}) {
    saveRuntime?.setSaveStatus(status, options);
}

function saveNote() {
    return saveRuntime?.saveNote();
}

function triggerDebouncedSave() {
    return saveRuntime?.triggerDebouncedSave();
}

function closePageSetupPopover(options = {}) {
    pageSetupRuntime?.closePageSetupPopover(options);
}

function closePageSetupDropdowns(except = null) {
    pageSetupRuntime?.clearPageSetupDropdowns(except);
}

function effectivePageSetup() {
    return pageSetupRuntime?.effectivePageSetup() || {};
}

function applyEditorZoom(options = {}) {
    pageSetupRuntime?.applyEditorZoom(options);
}

function bindWritingToolbar() {
    toolbarDom?.bindWritingToolbar();
}

function initEditorPage() {
    return reactShell?.initEditorPage();
}

function textFromInlineContent(content) {
    if (!Array.isArray(content)) return '';
    return content.map((item) => {
        if (typeof item === 'string') return item;
        if (item?.type === 'link') return textFromInlineContent(item.content);
        return item?.text || '';
    }).join('');
}

function blockCountForDocument(documentValue) {
    return Array.isArray(documentValue) ? documentValue.length : 0;
}

function editorTopLevelBlockCount() {
    const prosemirrorBlockGroup = editorInstance?._tiptapEditor?.state?.doc?.firstChild;
    if (typeof prosemirrorBlockGroup?.childCount === 'number') {
        return prosemirrorBlockGroup.childCount;
    }
    return blockCountForDocument(latestDocumentSnapshot);
}

function invalidateDocumentSnapshot() {
    latestDocumentSnapshot = null;
}

function currentDocumentSnapshot() {
    if (!editorInstance) return [];
    if (!latestDocumentSnapshot) {
        latestDocumentSnapshot = editorInstance.document || [];
    }
    return latestDocumentSnapshot;
}

function noteUrl(value) {
    const raw = String(value || '').trim();
    if (!raw) return '';
    try {
        const parsed = new URL(raw);
        if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return '';
        return parsed.href;
    } catch (error) {
        return '';
    }
}

function updateBlockPayloadForPreservedText(block, payload) {
    if (!block || !payload) return payload;
    if (ATOM_BLOCK_TYPES.has(payload.type) || payload.type === 'table') return payload;
    return {
        ...payload,
        props: mergedPropsForBlockType(payload.type, block, payload.props),
        content: block.content,
        children: block.children || [],
    };
}

function isBlockStyleSelected(block, option) {
    if (!block || block.type !== option.type) return false;
    if (!option.props) return true;
    return Object.entries(option.props).every(([key, value]) => block.props?.[key] === value);
}

function getSelectedTextAlignment() {
    if (!editorInstance) return 'left';
    const inlineSelection = editorInstance._tiptapEditor?.state?.selection;
    if (inlineSelection?.node?.type?.name === 'inlineImage') {
        return inlineSelection.node.attrs?.alignment || 'left';
    }
    const blocks = selectedBlocks();
    const block = blocks[0];
    if (!block) return 'left';

    if (checkBlockHasDefaultProp('textAlignment', block, editorInstance)) {
        return block.props.textAlignment || 'left';
    }

    if (block.type === 'table') {
        const cellSelection = editorInstance.tableHandles?.getCellSelection();
        if (!cellSelection) return 'left';

        const alignments = cellSelection.cells.map(({ row, col }) => (
            mapTableCell(block.content.rows[row].cells[col]).props.textAlignment
        ));
        const firstAlignment = alignments[0];
        return alignments.every((alignment) => alignment === firstAlignment) ? firstAlignment || 'left' : 'left';
    }

    return 'left';
}

function applyTextAlignment(textAlignment) {
    if (!editorInstance) return;
    editorInstance.focus();

    const inlineSelection = editorInstance._tiptapEditor?.state?.selection;
    if (inlineSelection?.node?.type?.name === 'inlineImage') {
        const transaction = editorInstance._tiptapEditor.state.tr.setNodeMarkup(
            inlineSelection.from,
            undefined,
            { ...inlineSelection.node.attrs, layout: 'break', alignment: textAlignment === 'justify' ? 'left' : textAlignment }
        );
        editorInstance.dispatch(transaction);
        updateEditorChrome();
        triggerDebouncedSave();
        return;
    }

    selectedBlocks().forEach((block) => {
        if (checkBlockTypeHasDefaultProp('textAlignment', block.type, editorInstance)) {
            editorInstance.updateBlock(block, { props: { ...block.props, textAlignment } });
            return;
        }

        if (block.type !== 'table') return;
        const cellSelection = editorInstance.tableHandles?.getCellSelection();
        if (!cellSelection) return;

        const newTable = block.content.rows.map((row) => ({
            ...row,
            cells: row.cells.map((cell) => mapTableCell(cell)),
        }));

        cellSelection.cells.forEach(({ row, col }) => {
            newTable[row].cells[col].props.textAlignment = textAlignment;
        });

        editorInstance.updateBlock(block, {
            type: 'table',
            content: {
                ...block.content,
                type: 'tableContent',
                rows: newTable,
            },
        });
        editorInstance.setTextCursorPosition(block);
    });

    updateEditorChrome();
    triggerDebouncedSave();
}

function syncNotePrintControls() {
    notePrintButtons.forEach((button) => {
        const disabled = !notePrintReady || notePrintInProgress;
        button.disabled = disabled;
        button.setAttribute('aria-disabled', String(disabled));
        button.toggleAttribute('aria-busy', notePrintInProgress);
    });
}

function setNotePrintReady(ready) {
    notePrintReady = Boolean(ready);
    syncNotePrintControls();
}

async function requestCurrentNotePrint() {
    if (!notePrintReady || notePrintInProgress || !editorInstance || !titleInput) return;
    notePrintInProgress = true;
    syncNotePrintControls();
    closeToolbarMenus();
    closePageSetupPopover();

    try {
        const { printNote } = await import('./editor/print.js');
        const documentSnapshot = currentDocumentSnapshot();
        const { hidden } = hiddenBlocksForCollapsedHeadings(documentSnapshot);
        const setup = effectivePageSetup();
        await printNote({
            editor: editorInstance,
            blocks: documentSnapshot,
            hiddenBlockIds: hidden.keys(),
            title: titleInput.value,
            fontFamily: pageSetupRuntime?.getPageSetupFontFamily?.() || 'var(--font-body)',
            sideMargins: setup.sideMargins,
        });
    } catch (error) {
        console.error('Failed to prepare note for printing', error);
        window.APStudyToast?.error?.('Try again in a moment.', { title: 'Couldn’t prepare note for printing' });
    } finally {
        notePrintInProgress = false;
        syncNotePrintControls();
    }
}

function bindNotePrintControls() {
    syncNotePrintControls();
    document.addEventListener('click', handleNotePrintClick, true);
    document.addEventListener('keydown', handleNotePrintShortcut, true);
}

function handleNotePrintClick(event) {
    const button = event.target.closest?.('[data-note-print]');
    if (!button || !document.contains(button)) return;
    event.preventDefault();
    void requestCurrentNotePrint();
}

function handleNotePrintShortcut(event) {
    const isPrintShortcut = !event.defaultPrevented
        && (event.metaKey || event.ctrlKey)
        && !event.altKey
        && !event.shiftKey
        && String(event.key || '').toLowerCase() === 'p';
    if (!isPrintShortcut) return;
    event.preventDefault();
    if (notePrintReady) void requestCurrentNotePrint();
}

function bindLazyReviewPanel({ canReview, canManageReviews, canViewVersions }) {
    reviewPanelBootstrapCleanup?.();
    reviewPanelBootstrapCleanup = null;
    if (!noteId || !reviewPanel) return;

    const openPanel = async (mode) => {
        reviewButton?.setAttribute('aria-busy', 'true');
        historyButton?.setAttribute('aria-busy', 'true');
        try {
            reviewPanelModulePromise ||= import('./editor/review-panel.js');
            const { bindReviewPanel } = await reviewPanelModulePromise;
            if (editorPageDisposed) return;
            if (!reviewPanelController) {
                reviewPanelController = bindReviewPanel({
                    noteId,
                    canReview,
                    canManageReviews,
                    canViewVersions,
                    panel: reviewPanel,
                    reviewButton: null,
                    historyButton: null,
                    toast: window.APStudyToast,
                });
            }
            await reviewPanelController?.open?.(mode);
        } catch (error) {
            console.error('Failed to load note review panel', error);
            window.APStudyToast?.error?.('Try again in a moment.', { title: 'Couldn’t load review tools' });
        } finally {
            reviewButton?.removeAttribute('aria-busy');
            historyButton?.removeAttribute('aria-busy');
        }
    };
    const openReview = () => { void openPanel('review'); };
    const openHistory = () => { void openPanel('history'); };
    reviewButton?.addEventListener('click', openReview);
    historyButton?.addEventListener('click', openHistory);
    reviewPanelBootstrapCleanup = () => {
        reviewButton?.removeEventListener('click', openReview);
        historyButton?.removeEventListener('click', openHistory);
    };
}

function focusEditorBody() {
    if (!editorInstance) return;
    editorInstance.focus?.();
}

function getCurrentBlock() {
    if (!editorInstance) return null;
    return editorInstance.getTextCursorPosition?.()?.block || selectedBlocks()[0] || null;
}

function openBlockSuggestionMenu(block) {
    if (!editorInstance || !block) return;
    editorInstance.setTextCursorPosition?.(block);
    editorInstance.openSuggestionMenu?.('/');
    focusEditorBody();
}

function removeUrlBlockPopover(result = null) {
    if (!urlBlockPopover) return;
    const resolve = urlBlockResolve;
    urlBlockPopover.remove();
    urlBlockPopover = null;
    urlBlockResolve = null;
    resolve?.(result);
}

function positionUrlBlockPopover(anchorRect) {
    if (!urlBlockPopover) return;
    const rect = anchorRect || editorPage?.getBoundingClientRect() || { left: 16, bottom: 80 };
    const width = Math.min(360, window.innerWidth - 16);
    const left = Math.max(8, Math.min(rect.left || 8, window.innerWidth - width - 8));
    const top = Math.max(8, Math.min((rect.bottom || 80) + 8, window.innerHeight - 380));
    urlBlockPopover.style.width = `${width}px`;
    urlBlockPopover.style.left = `${Math.round(left)}px`;
    urlBlockPopover.style.top = `${Math.round(top)}px`;
}

function requestUrlForBlock(item, anchorRect = null) {
    removeUrlBlockPopover();
    return new Promise((resolve) => {
        urlBlockResolve = resolve;
        const label = item?.label || 'URL block';
        urlBlockPopover = document.createElement('form');
        urlBlockPopover.className = 'notes-url-block-popover';
        urlBlockPopover.setAttribute('data-gramm', 'false');
        urlBlockPopover.setAttribute('data-gramm_editor', 'false');
        urlBlockPopover.setAttribute('data-enable-grammarly', 'false');
        urlBlockPopover.setAttribute('spellcheck', 'false');
        urlBlockPopover.innerHTML = `
            <label class="notes-url-block-field">
                <span>${label} URL</span>
                <input type="url" name="url" placeholder="https://example.com" autocomplete="off" ${GRAMMARLY_DISABLED_ATTRS} required />
            </label>
            <div class="notes-url-block-error" role="alert" hidden></div>
            <div class="notes-url-block-actions">
                <button type="button" data-url-block-cancel>Cancel</button>
                <button type="submit">Insert</button>
            </div>
        `;
        document.body.appendChild(urlBlockPopover);
        positionUrlBlockPopover(anchorRect);
        const input = urlBlockPopover.querySelector('input[name="url"]');
        const error = urlBlockPopover.querySelector('.notes-url-block-error');
        input?.focus();

        urlBlockPopover.addEventListener('submit', (event) => {
            event.preventDefault();
            const url = noteUrl(input?.value);
            if (!url) {
                if (error) {
                    error.hidden = false;
                    error.textContent = 'Enter a valid http or https URL.';
                }
                return;
            }
            removeUrlBlockPopover(url);
        });
        urlBlockPopover.querySelector('[data-url-block-cancel]')?.addEventListener('click', () => {
            removeUrlBlockPopover('');
        });
    });
}

async function insertImageFromDialog(anchorRect = null, editor = editorInstance) {
    const source = await requestImageSource(anchorRect, noteUrl);
    if (!source) return null;
    closeToolbarMenus();
    if (source.file) return insertInlineImageFile(editor, source.file, { noteId, onChange: triggerDebouncedSave });
    return insertInlineImageNode(editor, { url: source.url, mediaId: '', clientId: `url-${Date.now()}`, alt: '', width: 240, layout: 'inline', alignment: 'left', status: 'ready', error: '' });
}

async function previewForBookmark(url) {
    try {
        const response = await fetch(`/api/notes/tools/link-preview?url=${encodeURIComponent(url)}`);
        if (!response.ok) throw new Error('Preview unavailable');
        return await response.json();
    } catch (error) {
        let hostname = '';
        try {
            hostname = new URL(url).hostname;
        } catch (urlError) {
            hostname = '';
        }
        return {
            url,
            title: hostname || url,
            description: '',
            image_url: '',
            site_name: hostname,
            content_type: '',
            preview_found: false,
        };
    }
}

async function payloadForCatalogItem(item, anchorRect = null) {
    if (!item) return blockPayloadForCatalogItem(catalogItemByKey('paragraph'));
    if (!item.requiresUrl) return blockPayloadForCatalogItem(item);
    const url = await requestUrlForBlock(item, anchorRect);
    if (!url) return null;
    if (item.type === 'bookmark') {
        const preview = await previewForBookmark(url);
        return blockPayloadForCatalogItem(item, {
            url: preview?.url || url,
            title: preview?.title || url,
            description: preview?.description || '',
            image_url: preview?.image_url || '',
            site_name: preview?.site_name || '',
            content_type: preview?.content_type || '',
        });
    }
    return blockPayloadForCatalogItem(item, { url });
}

function insertBlockPayload(block, editor = editorInstance) {
    if (!editor || !block) return null;
    const currentBlock = editor.getTextCursorPosition?.()?.block;
    if (!currentBlock) {
        editor.focus?.();
        return null;
    }
    let insertedOrUpdated = null;
    if (blockOwnContentIsEmpty(currentBlock)) {
        insertedOrUpdated = editor.updateBlock(currentBlock, block);
    } else {
        insertedOrUpdated = editor.insertBlocks?.([block], currentBlock, 'after')?.[0] || null;
    }

    if (!insertedOrUpdated) return null;

    if (ATOM_BLOCK_TYPES.has(insertedOrUpdated.type) || insertedOrUpdated.type === 'table') {
        const after = editor.insertBlocks?.([{ type: 'paragraph' }], insertedOrUpdated, 'after')?.[0];
        if (after) editor.setTextCursorPosition?.(after);
    } else {
        editor.setTextCursorPosition?.(insertedOrUpdated);
    }

    editor.focus?.();
    updateEditorChrome();
    triggerDebouncedSave();
    return insertedOrUpdated;
}

async function insertCatalogItem(item, anchorRect = null, editor = editorInstance) {
    if (item?.type === 'inlineImage') return insertImageFromDialog(anchorRect, editor);
    const payload = await payloadForCatalogItem(item, anchorRect);
    if (!payload) {
        focusEditorBody();
        return null;
    }
    closeToolbarMenus();
    return insertBlockPayload(payload, editor);
}

function insertBlockFromMenu(button) {
    if (!editorInstance) return;
    const item = catalogItemByKey(button?.dataset.blockKey) || catalogItemByType(button?.dataset.blockType, {
        level: Number(button?.dataset.level || 1),
    });
    void insertCatalogItem(item || catalogItemByKey('paragraph'), button?.getBoundingClientRect?.());
}

function selectedBlocks() {
    if (!editorInstance) return [];
    return editorInstance.getSelection?.()?.blocks || [editorInstance.getTextCursorPosition?.()?.block].filter(Boolean);
}

function safeSetBlockSelection(anchor, head = anchor) {
    if (!editorInstance || !anchor) return false;
    const nextHead = head || anchor;
    if (anchor.id && nextHead.id && anchor.id !== nextHead.id) {
        editorInstance.setSelection?.(anchor, nextHead);
    } else {
        editorInstance.setTextCursorPosition?.(anchor);
    }
    selectedBlockAnchorId = anchor.id || null;
    editorInstance.setForceSelectionVisible?.(true);
    return true;
}

function blockSupportsVisualIndent(block) {
    return VISUAL_INDENT_BLOCKS.has(block?.type);
}

function visualIndentLevel(block) {
    return Math.min(MAX_INDENT_LEVEL, Math.max(0, Number(block?.props?.indentLevel || 0)));
}

function mergedPropsForBlockType(type, block, props = undefined) {
    const merged = {};
    if (block?.props?.textAlignment) merged.textAlignment = block.props.textAlignment;
    if (block?.props?.textColor) merged.textColor = block.props.textColor;
    if (block?.props?.backgroundColor) merged.backgroundColor = block.props.backgroundColor;
    if (VISUAL_INDENT_BLOCKS.has(type)) merged.indentLevel = visualIndentLevel(block);
    if (type === 'heading') merged.level = Number(props?.level || block?.props?.level || 1);
    if (type === 'checkListItem') merged.checked = Boolean(block?.props?.checked);
    return { ...merged, ...(props || {}) };
}

function setSelectedBlocks(type, props = undefined) {
    if (!editorInstance) return;
    selectedBlocks().forEach((block) => {
        if (!block) return;
        editorInstance.updateBlock(block, {
            type,
            props: mergedPropsForBlockType(type, block, props),
        });
    });
    focusEditorBody();
    updateEditorChrome();
    triggerDebouncedSave();
}

function toggleBasicStyle(style) {
    if (!editorInstance) return;
    if (typeof editorInstance.toggleStyles === 'function') {
        editorInstance.toggleStyles({ [style]: true });
        focusEditorBody();
        updateEditorChrome();
        triggerDebouncedSave();
        return;
    }
    focusEditorBody();
}

function applyInlineStyle(style, value) {
    if (!editorInstance) return;
    editorInstance.focus?.();
    if (value === 'default' || value === '') {
        editorInstance.removeStyles?.({ [style]: value });
    } else {
        editorInstance.addStyles?.({ [style]: value });
    }
    updateEditorChrome();
    triggerDebouncedSave();
}

function applyTextColor(color) {
    applyInlineStyle('textColor', color);
}

function applyHighlightColor(color) {
    applyInlineStyle('backgroundColor', color);
}

function applyFontSizePreset(value) {
    const preset = FONT_SIZE_PRESETS.find((item) => item.value === value) || FONT_SIZE_PRESETS[0];
    if (preset.value === 'default') {
        editorInstance?.removeStyles?.({ fontSize: '' });
    } else {
        editorInstance?.addStyles?.({ fontSize: preset.cssValue });
    }
    focusEditorBody();
    updateEditorChrome();
    triggerDebouncedSave();
}

function deleteSelectedBlocks() {
    if (!editorInstance) return;
    const blocks = selectedBlocks();
    if (!blocks.length) return;
    removeBlocksAndRestoreCursor(editorInstance, blocks);
    selectedBlockAnchorId = null;
    focusEditorBody();
    updateEditorChrome();
    triggerDebouncedSave();
}

function duplicateSelectedBlocks() {
    if (!editorInstance) return;
    const blocks = selectedBlocks();
    if (!blocks.length) return;
    const copied = JSON.parse(JSON.stringify(blocks)).map((block) => {
        delete block.id;
        return block;
    });
    const inserted = editorInstance.insertBlocks?.(copied, blocks[blocks.length - 1], 'after') || [];
    if (inserted[0]) {
        safeSetBlockSelection(inserted[0], inserted[inserted.length - 1] || inserted[0]);
    }
    focusEditorBody();
    updateEditorChrome();
    triggerDebouncedSave();
}

async function copySelectedBlocks({ cut = false } = {}) {
    if (!editorInstance) return;
    const blocks = selectedBlocks();
    if (!blocks.length) return;
    const blockIds = blocks.map((block) => block.id).filter(Boolean);
    const [markdown, html] = await Promise.all([
        editorInstance.blocksToMarkdownLossy?.(blocks),
        editorInstance.blocksToHTMLLossy?.(blocks),
    ]);
    const copiedMarkdown = markdown || blocks.map((block) => textFromInlineContent(block.content)).join('\n\n');
    const plainText = normalizeCopiedPlainText(copiedMarkdown);
    try {
        if (window.ClipboardItem && navigator.clipboard?.write) {
            const clipboardPayload = {
                'text/plain': new Blob([plainText], { type: 'text/plain' }),
                'text/html': new Blob([html || plainText], { type: 'text/html' }),
            };
            if (window.ClipboardItem.supports?.('text/markdown')) {
                clipboardPayload['text/markdown'] = new Blob([copiedMarkdown], { type: 'text/markdown' });
            }
            await navigator.clipboard.write([
                new ClipboardItem(clipboardPayload),
            ]);
        } else {
            await navigator.clipboard?.writeText(plainText);
        }
    } catch (error) {
        try {
            await navigator.clipboard?.writeText(plainText);
        } catch (clipboardError) {
            console.warn('Unable to write selected note blocks to clipboard', clipboardError);
        }
    }
    if (cut) removeBlocksAndRestoreCursor(editorInstance, blockIds);
}

function normalizeNativeEditorCopy(event) {
    const clipboardData = event.clipboardData;
    if (!clipboardData) return;
    const plainText = clipboardData.getData('text/plain');
    if (!plainText) return;
    clipboardData.setData('text/plain', normalizeCopiedPlainText(plainText));
}

function moveSelectedBlocks(direction) {
    if (!editorInstance) return;
    if (direction === 'up') {
        editorInstance.moveBlocksUp?.();
    } else {
        editorInstance.moveBlocksDown?.();
    }
    focusEditorBody();
    updateEditorChrome({ structureChanged: true });
    triggerDebouncedSave();
}

function firstSelectedHeading() {
    return selectedBlocks().find((block) => block?.type === 'heading') || null;
}

function toggleHeadingCollapse(block = firstSelectedHeading()) {
    if (!editorInstance || !block || block.type !== 'heading') return;
    editorInstance.updateBlock(block, {
        props: {
            ...block.props,
            isCollapsed: !Boolean(block.props?.isCollapsed),
        },
    });
    focusEditorBody();
    updateEditorChrome({ structureChanged: true });
    triggerDebouncedSave();
}

function selectBlockRange(block, extend = false) {
    if (!editorInstance || !block) return;
    const anchor = extend && selectedBlockAnchorId ? editorInstance.getBlock?.(selectedBlockAnchorId) : null;
    if (anchor) {
        safeSetBlockSelection(anchor, block);
    } else {
        safeSetBlockSelection(block, block);
    }
    updateEditorChrome();
}

function selectedBlockIds() {
    return new Set(selectedBlocks().map((block) => block?.id).filter(Boolean));
}

function canRunHistoryAction(action) {
    if (!editorInstance) return false;

    const depth = historyDepth(action);
    if (typeof depth === 'number') {
        return depth > (historyBaselineDepths[action] || 0);
    }

    const commandCan = editorInstance._tiptapEditor?.can?.();
    const canAction = commandCan?.[action];
    if (typeof canAction !== 'function') return false;

    try {
        return Boolean(canAction.call(commandCan));
    } catch (error) {
        return false;
    }
}

function runHistoryAction(action) {
    if (!editorInstance || !canRunHistoryAction(action)) return;

    if (typeof editorInstance[action] === 'function') {
        editorInstance[action]();
    } else {
        editorInstance._tiptapEditor?.commands?.[action]?.();
    }

    focusEditorBody();
    updateEditorChrome();
}

function canRunIndentAction(action) {
    if (!editorInstance) return false;
    const blocks = selectedBlocks();
    if (!blocks.length) return false;
    if (blocks.some((block) => LIST_BLOCK_TYPES.has(block?.type))) {
        if (action === 'indent') return Boolean(editorInstance.canNestBlock?.());
        if (action === 'outdent') return Boolean(editorInstance.canUnnestBlock?.());
    }
    if (blocks.some(blockSupportsVisualIndent)) {
        if (action === 'indent') return blocks.some((block) => visualIndentLevel(block) < MAX_INDENT_LEVEL);
        if (action === 'outdent') return blocks.some((block) => visualIndentLevel(block) > 0);
    }
    return false;
}

function runIndentAction(action) {
    if (!editorInstance || !canRunIndentAction(action)) return;
    const blocks = selectedBlocks();
    const listMode = blocks.some((block) => LIST_BLOCK_TYPES.has(block?.type));
    if (listMode && action === 'indent') {
        editorInstance.nestBlock?.();
    } else if (listMode && action === 'outdent') {
        editorInstance.unnestBlock?.();
    } else {
        blocks.forEach((block) => {
            if (!blockSupportsVisualIndent(block)) return;
            const currentLevel = visualIndentLevel(block);
            const nextLevel = action === 'indent'
                ? Math.min(MAX_INDENT_LEVEL, currentLevel + 1)
                : Math.max(0, currentLevel - 1);
            editorInstance.updateBlock(block, {
                props: {
                    ...block.props,
                    indentLevel: nextLevel,
                },
            });
        });
    }
    focusEditorBody();
    updateEditorChrome();
    triggerDebouncedSave();
}

function applyLinkFromMenu(menu) {
    if (!editorInstance || !menu) return;
    const input = menu.querySelector('[data-link-url]');
    const url = input?.value?.trim();
    if (!url) return;

    const selectedText = editorInstance.getSelectedText?.() || '';
    editorInstance.focus();
    editorInstance.createLink?.(url, selectedText ? undefined : url);
    closeToolbarMenus();
    updateEditorChrome();
    triggerDebouncedSave();
}

function removeSelectedLink() {
    if (!editorInstance) return;
    editorInstance.focus?.();
    editorInstance._tiptapEditor?.chain?.().focus().unsetLink().run();
    closeToolbarMenus();
    updateEditorChrome();
    triggerDebouncedSave();
}

function historyDepth(action) {
    const state = editorInstance?._tiptapEditor?.state;
    if (!state?.plugins) return null;

    const historyPlugin = state.plugins.find((plugin) => String(plugin.key || '').startsWith('history$'));
    const historyState = historyPlugin?.getState?.(state);
    const branch = action === 'redo' ? historyState?.undone : historyState?.done;
    return typeof branch?.eventCount === 'number' ? branch.eventCount : null;
}

function captureHistoryBaseline() {
    historyBaselineDepths = {
        undo: historyDepth('undo') || 0,
        redo: historyDepth('redo') || 0,
    };
}

function updateEditorHint(documentSnapshot = latestDocumentSnapshot) {
    if (!editorHint || !editorInstance) return;
    if (Array.isArray(documentSnapshot)) {
        editorHint.hidden = documentHasText(documentSnapshot);
        return;
    }
    const textContent = editorInstance._tiptapEditor?.state?.doc?.textContent || '';
    editorHint.hidden = textContent.trim().length > 0;
}

function blockOuterById(id) {
    if (!id || !blocknoteRoot) return null;
    return blocknoteRoot.querySelector(`.bn-block-outer[data-id="${CSS.escape(String(id))}"]`);
}

function syncSelectedBlockClasses() {
    if (!blocknoteRoot) return;
    if (!canEdit) {
        blocknoteRoot.querySelectorAll('.notes-block-selected').forEach((element) => {
            element.classList.remove('notes-block-selected');
        });
        lastSelectedBlockIds = new Set();
        return;
    }
    const ids = selectedBlockIds();
    const toClear = [...lastSelectedBlockIds].filter((id) => !ids.has(id));
    const toSet = [...ids].filter((id) => !lastSelectedBlockIds.has(id));

    toClear.forEach((id) => {
        const element = blockOuterById(id);
        element?.classList.remove('notes-block-selected');
    });
    toSet.forEach((id) => {
        const element = blockOuterById(id);
        element?.classList.add('notes-block-selected');
    });
    lastSelectedBlockIds = ids;
}

function headingCollapseSignature(documentBlocks) {
    const { hidden, counts } = hiddenBlocksForCollapsedHeadings(documentBlocks || []);
    const collapsed = (documentBlocks || [])
        .filter((block) => block?.type === 'heading')
        .map((block) => `${block.id}:${block.props?.isCollapsed ? 1 : 0}:${counts.get(block.id) || 0}`)
        .join('|');
    const hiddenPart = [...hidden.entries()].map(([id, by]) => `${id}:${by}`).join('|');
    return `${collapsed}::${hiddenPart}`;
}

function syncHeadingCollapseChrome(force = false, documentSnapshot = null) {
    if (!editorInstance || !blocknoteRoot) return;
    const documentBlocks = documentSnapshot || currentDocumentSnapshot();
    const signature = headingCollapseSignature(documentBlocks);
    if (!force && signature === lastHeadingCollapseSignature) return;
    lastHeadingCollapseSignature = signature;

    const { hidden } = hiddenBlocksForCollapsedHeadings(documentBlocks);
    blocknoteRoot.querySelectorAll('.bn-block-outer[data-id]').forEach((element) => {
        const blockId = element.dataset.id;
        const hiddenBy = hidden.get(blockId);
        element.classList.toggle('notes-block-hidden-by-collapse', Boolean(hiddenBy));
        if (hiddenBy) element.dataset.hiddenByHeading = hiddenBy;
        else delete element.dataset.hiddenByHeading;
    });

    documentBlocks.forEach((block) => {
        if (block?.type !== 'heading') return;
        const outer = blockOuterById(block.id);
        const content = outer?.querySelector('.bn-block-content[data-content-type="heading"]');
        if (!content) return;
        content.classList.toggle('notes-heading-collapsed', Boolean(block.props?.isCollapsed));
        let button = content.querySelector(':scope > .notes-heading-collapse-toggle');
        if (!canEdit) {
            button?.remove();
            return;
        }
        if (!button) {
            button = document.createElement('button');
            button.type = 'button';
            button.className = 'notes-heading-collapse-toggle';
            button.contentEditable = 'false';
            button.setAttribute('aria-label', 'Toggle heading collapse');
            button.innerHTML = '<span class="material-symbols-outlined" aria-hidden="true">chevron_right</span>';
            content.insertBefore(button, content.firstChild);
        }
        button.setAttribute('aria-expanded', String(!block.props?.isCollapsed));
    });
}

function flushEditorChromeSync() {
    editorChromeRafId = null;
    const needsStructure = editorChromeSyncNeedsStructure;
    const documentSnapshot = editorChromeSyncSnapshot;
    editorChromeSyncNeedsStructure = false;
    editorChromeSyncSnapshot = null;
    syncSelectedBlockClasses();
    if (needsStructure) {
        syncHeadingCollapseChrome(false, documentSnapshot);
    }
}

function scheduleEditorChromeSync({ immediate = false, structureChanged = false, documentSnapshot = null } = {}) {
    editorChromeSyncNeedsStructure = editorChromeSyncNeedsStructure || structureChanged;
    if (documentSnapshot) editorChromeSyncSnapshot = documentSnapshot;

    if (immediate) {
        if (editorChromeThrottleTimer) {
            clearTimeout(editorChromeThrottleTimer);
            editorChromeThrottleTimer = null;
        }
        if (editorChromeRafId) {
            cancelAnimationFrame(editorChromeRafId);
        }
        editorChromeRafId = window.requestAnimationFrame(flushEditorChromeSync);
        return;
    }
    if (editorChromeThrottleTimer) return;
    editorChromeThrottleTimer = window.setTimeout(() => {
        editorChromeThrottleTimer = null;
        if (editorChromeRafId) return;
        editorChromeRafId = window.requestAnimationFrame(flushEditorChromeSync);
    }, EDITOR_CHROME_THROTTLE_MS);
}

function updateEditorChrome({ immediate = false, structureChanged = false, contentChanged = false, documentSnapshot = null } = {}) {
    updateToolbarState();
    if (contentChanged) {
        updateEditorHint(documentSnapshot);
    }
    if (immediate || structureChanged || contentChanged) {
        scheduleEditorChromeSync({
            immediate: immediate || structureChanged,
            structureChanged: structureChanged || contentChanged,
            documentSnapshot,
        });
        return;
    }
    scheduleEditorChromeSync();
}

function initializeEditorRuntimes() {
    if (saveRuntime) return;

    saveRuntime = createNoteSaveRuntime({
        noteId,
        titleInput,
        saveStatus,
        saveRetry,
        getCanEdit: () => canEdit,
        getEditor: () => editorInstance,
        getNoteCollaborationEnabled: () => noteCollaborationEnabled,
        getCurrentDocumentSnapshot: currentDocumentSnapshot,
        getTopLevelBlockCount: editorTopLevelBlockCount,
    });

    pageSetupRuntime = createPageSetupRuntime({
        noteId,
        editorPage,
        pageSetupPopover,
        zoomValue,
        pageSetupScopeInput,
        sideMarginsValue,
        getCanEdit: () => canEdit,
        getNoteCollaborationEnabled: () => noteCollaborationEnabled,
        setSaveStatus,
        closeToolbarMenus,
        updateToolbarState,
        refreshToolbar: () => toolbarDom?.refresh(),
    });

    toolbarDom = createToolbarDom({
        writingToolbar,
        pageSetupPopover,
        editorPage,
        getCanEdit: () => canEdit,
        getEditor: () => editorInstance,
        getSelectedBlocks: selectedBlocks,
        getSelectedTextAlignment,
        isBlockStyleSelected,
        canRunHistoryAction,
        canRunIndentAction,
        getZoomIndex: () => pageSetupRuntime.getZoomIndex(),
        getZoomLevels: () => pageSetupRuntime.getZoomLevels(),
        pageSetup: pageSetupRuntime,
        actions: {
            focusEditorBody,
            insertBlockFromMenu,
            runHistoryAction,
            toggleBasicStyle,
            applyTextColor,
            applyHighlightColor,
            applyFontSizePreset,
            removeSelectedLink,
            setSelectedBlocks,
            applyTextAlignment,
            runIndentAction,
            copySelectedBlocks,
            duplicateSelectedBlocks,
            deleteSelectedBlocks,
            moveSelectedBlocks,
            toggleHeadingCollapse,
            applyLinkFromMenu,
            hasUrlBlockPopover: () => Boolean(urlBlockPopover),
            getUrlBlockPopover: () => urlBlockPopover,
            resolveUrlBlockPopover: (result) => urlBlockResolve?.(result),
            removeUrlBlockPopover,
            getActivePageSetupTrigger: () => pageSetupRuntime.getActivePageSetupTrigger(),
        },
    });

    reactShell = createReactShell({
        noteContext,
        noteId,
        titleInput,
        blocknoteRoot,
        writingToolbar,
        editorPage,
        shareButton,
        collaboratorsRoot,
        getCanEdit: () => canEdit,
        setEditorReadOnlyMode,
        getEditor: () => editorInstance,
        setEditorInstance: (value) => { editorInstance = value; },
        setNotePrintReady,
        setNoteCollaborationEnabled: (value) => { noteCollaborationEnabled = Boolean(value); },
        setSaveStatus,
        setLastSavedPayloadFingerprint: (value) => saveRuntime?.setLastSavedPayloadFingerprint(value),
        notePayloadFingerprint,
        getEditorPageDisposed: () => editorPageDisposed,
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
        pageSetup: pageSetupRuntime,
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
    });
}

function clearEditorTimersAndFrames() {
    saveRuntime?.clearTimers();
    pageSetupRuntime?.clearTimers();
    reactShell?.clearTimers();
    if (editorChromeThrottleTimer) window.clearTimeout(editorChromeThrottleTimer);
    if (editorChromeRafId) window.cancelAnimationFrame(editorChromeRafId);
    editorChromeThrottleTimer = null;
    editorChromeRafId = null;
}

function releaseNoteEditorRuntime() {
    if (editorPageDisposed) return;
    editorPageDisposed = true;
    clearEditorTimersAndFrames();
    toolbarDom?.disconnect();
    closeToolbarMenus();
    closePageSetupPopover();
    closePageSetupDropdowns();
    removeUrlBlockPopover();
    reactShell?.release();
    reviewPanelBootstrapCleanup?.();
    reviewPanelBootstrapCleanup = null;
    reviewPanelController?.close?.();
    reviewPanelController = null;
    document.removeEventListener('click', handleNotePrintClick, true);
    document.removeEventListener('keydown', handleNotePrintShortcut, true);
    editorInstance = null;
    latestDocumentSnapshot = null;
    editorChromeSyncSnapshot = null;
    lastSelectedBlockIds.clear();
    setNotePrintReady(false);
}

const NOTES_EDITOR_RUNTIME_KEY = Symbol.for('apstudy.notes.editor.runtime');

if (!window[NOTES_EDITOR_RUNTIME_KEY]) {
    window[NOTES_EDITOR_RUNTIME_KEY] = true;
    initializeEditorRuntimes();

    window.APStudyPageLifecycle?.register?.({
        pause: releaseNoteEditorRuntime,
        resume() {
            // Browser Back can still place the editor in bfcache. Restore it from a
            // fresh document after releasing the retained BlockNote heap.
            window.location.reload();
        },
        dispose: releaseNoteEditorRuntime,
    });

    saveRetry?.addEventListener('click', () => {
        void saveNote();
    });

    bindNotePrintControls();
    pageSetupRuntime.setInitialZoom();
    initEditorPage();
}
