import {
    BLOCK_CATALOG,
    FONT_SIZE_PRESETS,
    FORMAT_COLORS,
    blockIconClass,
    filterBlockCatalog,
} from './block-catalog.js';
import { claimElementBinding, handlePageSetupToolbarClick, floatingPopoverPosition } from './utils.js';

export function createToolbarDom({
    writingToolbar,
    pageSetupPopover,
    editorPage,
    getCanEdit,
    getEditor,
    getSelectedBlocks,
    getSelectedTextAlignment,
    isBlockStyleSelected,
    canRunHistoryAction,
    canRunIndentAction,
    getZoomIndex,
    getZoomLevels,
    pageSetup,
    actions,
}) {
    let activeToolbarMenu = null;
    let toolbarOverflowController = null;
    let addBlockActiveIndex = 0;

    function iconHtml(icon) {
        if (!icon) return '';
        if (icon.startsWith?.('H')) {
            return icon;
        }
        return icon;
    }

    function closeToolbarMenus() {
        if (!writingToolbar) return;
        activeToolbarMenu = null;
        writingToolbar.querySelectorAll('[data-toolbar-menu]').forEach((menu) => {
            menu.hidden = true;
        });
        writingToolbar.querySelectorAll('[data-toolbar-menu-trigger]').forEach((trigger) => {
            trigger.setAttribute('aria-expanded', 'false');
        });
    }

    function positionFloatingElement(trigger, element, { triggerRectOverride = null, boundaryRect = null } = {}) {
        if (!trigger || !element) return;
        const triggerRect = triggerRectOverride || trigger.getBoundingClientRect();
        element.style.left = '0px';
        element.style.top = '0px';
        element.style.transform = 'none';

        const originRect = element.getBoundingClientRect();
        const position = floatingPopoverPosition({
            triggerRect,
            popoverRect: originRect,
            boundaryRect,
        });

        element.style.left = `${Math.round(position.left - originRect.left)}px`;
        element.style.top = `${Math.round(position.top - originRect.top)}px`;
    }

    function positionToolbarMenu(trigger, menu, triggerRectOverride = null) {
        if (!trigger || !menu) return;
        const triggerRect = triggerRectOverride || trigger.getBoundingClientRect();
        const isOverflowToolbar = menu.classList.contains('notes-toolbar-overflow-menu');
        menu.style.minWidth = isOverflowToolbar ? '0px' : `${Math.max(triggerRect.width, 150)}px`;

        const editorRect = editorPage?.getBoundingClientRect();
        menu.style.maxWidth = isOverflowToolbar
            ? `${Math.max(0, Math.min(window.innerWidth, editorRect?.width || window.innerWidth) - 16)}px`
            : '';
        positionFloatingElement(trigger, menu, {
            triggerRectOverride: triggerRect,
            boundaryRect: editorRect,
        });
    }

    function renderFontSizeMenu(menu) {
        if (!menu || menu.dataset.rendered === 'font-size') return;
        menu.dataset.rendered = 'font-size';
        menu.innerHTML = FONT_SIZE_PRESETS.map((item) => `
        <button type="button" class="notes-toolbar-menu-item" data-editor-action="font-size" data-font-size="${item.value}" data-menu-check="font-size-${item.value}">
            <span class="material-symbols-outlined" aria-hidden="true">format_size</span>
            <span>${item.label}</span>
        </button>
    `).join('');
    }

    function renderColorMenu(menu, style) {
        if (!menu || menu.dataset.rendered === style) return;
        menu.dataset.rendered = style;
        const action = style === 'backgroundColor' ? 'highlight-color' : 'text-color';
        menu.innerHTML = FORMAT_COLORS.map((item) => `
        <button type="button" class="notes-toolbar-menu-item notes-color-menu-item" data-editor-action="${action}" data-color="${item.value}" data-menu-check="${action}-${item.value}">
            <span class="notes-format-swatch" data-${style === 'backgroundColor' ? 'background' : 'text'}-color="${item.value}" aria-hidden="true"></span>
            <span>${item.label}</span>
        </button>
    `).join('');
    }

    function visibleAddBlockItems(menu) {
        return Array.from(menu?.querySelectorAll('.notes-add-block-item:not([hidden])') || []);
    }

    function setActiveAddBlockItem(menu, index) {
        const items = visibleAddBlockItems(menu);
        if (!items.length) return;
        addBlockActiveIndex = Math.min(items.length - 1, Math.max(0, index));
        items.forEach((item, itemIndex) => {
            const active = itemIndex === addBlockActiveIndex;
            item.classList.toggle('is-active', active);
            item.setAttribute('aria-selected', String(active));
            if (active) item.scrollIntoView({ block: 'nearest' });
        });
    }

    function filterAddBlockMenu(menu) {
        const input = menu?.querySelector('[data-add-block-search]');
        const query = (input?.value || '').trim().toLowerCase();
        const items = filterBlockCatalog(query);
        const visibleKeys = new Set(items.map((item) => item.key));
        menu?.querySelectorAll('.notes-add-block-item').forEach((item) => {
            item.hidden = !visibleKeys.has(item.dataset.blockKey);
        });
        setActiveAddBlockItem(menu, 0);
    }

    function renderAddBlockMenu(menu) {
        const list = menu?.querySelector('[data-add-block-list]');
        if (!list || list.dataset.rendered === 'catalog') return;
        list.dataset.rendered = 'catalog';
        list.innerHTML = BLOCK_CATALOG.map((item) => `
        <button type="button" class="notes-add-block-item" data-editor-action="insert-block" data-block-key="${item.key}" data-block-type="${item.type}">
            <span class="${blockIconClass(item.icon)}" aria-hidden="true">${iconHtml(item.icon)}</span>
            <span><strong>${item.label}</strong><small>${item.description}</small></span>
        </button>
    `).join('');
    }

    function prepareAddBlockMenu(menu) {
        renderAddBlockMenu(menu);
        const input = menu?.querySelector('[data-add-block-search]');
        if (input) {
            input.value = '';
            window.setTimeout(() => input.focus(), 0);
        }
        filterAddBlockMenu(menu);
    }

    function openToolbarMenu(name, trigger) {
        if (!writingToolbar) return;
        const menu = writingToolbar.querySelector(`[data-toolbar-menu="${name}"]`);
        if (!menu) return;

        const openingSameMenu = activeToolbarMenu === name && !menu.hidden;
        const triggerRect = trigger?.getBoundingClientRect() || null;
        pageSetup?.closePageSetupPopover?.();
        closeToolbarMenus();
        if (openingSameMenu) return;

        activeToolbarMenu = name;
        menu.hidden = false;
        trigger?.setAttribute('aria-expanded', 'true');

        if (name === 'link') {
            const input = menu.querySelector('[data-link-url]');
            if (input) {
                input.value = getEditor()?.getSelectedLinkUrl?.() || '';
                window.setTimeout(() => input.focus(), 0);
            }
        } else if (name === 'add-block') {
            prepareAddBlockMenu(menu);
        } else if (name === 'font-size') {
            renderFontSizeMenu(menu);
        } else if (name === 'text-color' || name === 'highlight-color') {
            renderColorMenu(menu, name === 'highlight-color' ? 'backgroundColor' : 'textColor');
        }

        positionToolbarMenu(trigger, menu, triggerRect);
    }

    function createToolbarOverflowController(toolbar) {
        const main = toolbar?.querySelector('[data-toolbar-main]');
        const more = toolbar?.querySelector('[data-toolbar-more]');
        const menu = toolbar?.querySelector('[data-toolbar-menu="overflow"]');
        if (!main || !more || !menu) return null;

        const items = Array.from(main.querySelectorAll('[data-toolbar-item]')).map((element, index) => ({
            element,
            index,
            priority: Number(element.dataset.overflowPriority || 100),
        }));

        const restoreItems = () => {
            items
                .slice()
                .sort((a, b) => a.index - b.index)
                .forEach(({ element }) => {
                    main.insertBefore(element, more);
                });
        };

        const refresh = () => {
            restoreItems();
            more.hidden = true;
            menu.hidden = true;
            closeToolbarMenus();

            window.requestAnimationFrame(() => {
                restoreItems();
                const moved = [];
                const ordered = items.slice().sort((a, b) => b.priority - a.priority || b.index - a.index);

                more.hidden = false;
                for (const item of ordered) {
                    if (main.scrollWidth <= main.clientWidth + 1) break;
                    menu.insertBefore(item.element, menu.firstChild);
                    moved.push(item);
                }

                more.hidden = moved.length === 0;
                if (!moved.length) menu.hidden = true;
            });
        };

        const observer = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(refresh) : null;
        observer?.observe(main);
        window.addEventListener('resize', refresh);

        return {
            refresh,
            disconnect() {
                observer?.disconnect();
                window.removeEventListener('resize', refresh);
            },
        };
    }

    function updateToolbarState() {
        if (!writingToolbar || !getEditor()) return;
        const blocks = getSelectedBlocks();
        const block = blocks[0];
        const activeStyles = typeof getEditor().getActiveStyles === 'function'
            ? getEditor().getActiveStyles()
            : {};
        const activeFontPreset = FONT_SIZE_PRESETS.find((item) => item.cssValue && item.cssValue === activeStyles?.fontSize) || FONT_SIZE_PRESETS[0];
        const textBlockOptions = [
            { label: 'Paragraph', type: 'paragraph', icon: 'subject', key: 'paragraph' },
            { label: 'Heading 1', type: 'heading', props: { level: 1 }, icon: 'H1', key: 'heading-1' },
            { label: 'Heading 2', type: 'heading', props: { level: 2 }, icon: 'H2', key: 'heading-2' },
            { label: 'Heading 3', type: 'heading', props: { level: 3 }, icon: 'H3', key: 'heading-3' },
            { label: 'Quote', type: 'quote', icon: 'format_quote', key: 'quote' },
            { label: 'Code block', type: 'codeBlock', icon: 'code_blocks', key: 'codeBlock' },
            { label: 'Callout', type: 'callout', icon: 'lightbulb', key: 'callout' },
        ];
        const listStyleOptions = [
            { label: 'Bulleted list', type: 'bulletListItem', icon: 'format_list_bulleted', key: 'bulletListItem' },
            { label: 'Numbered list', type: 'numberedListItem', icon: 'format_list_numbered', key: 'numberedListItem' },
            { label: 'Checklist', type: 'checkListItem', icon: 'checklist', key: 'checkListItem' },
        ];
        const alignmentOptions = [
            { label: 'Align left', value: 'left', icon: 'format_align_left', key: 'align-left' },
            { label: 'Align center', value: 'center', icon: 'format_align_center', key: 'align-center' },
            { label: 'Align right', value: 'right', icon: 'format_align_right', key: 'align-right' },
            { label: 'Justify', value: 'justify', icon: 'format_align_justify', key: 'align-justify' },
        ];
        const activeBlockOption = textBlockOptions.find((option) => isBlockStyleSelected(block, option)) || textBlockOptions[0];
        const activeListOption = listStyleOptions.find((option) => isBlockStyleSelected(block, option));
        const currentAlignment = getSelectedTextAlignment();
        const activeAlignment = alignmentOptions.find((option) => option.value === currentAlignment) || alignmentOptions[0];

        const blockIcon = writingToolbar.querySelector('[data-current-block-icon]');
        const blockLabel = writingToolbar.querySelector('[data-current-block-label]');
        if (blockIcon) {
            blockIcon.classList.toggle('notes-toolbar-text-icon', activeBlockOption.icon.startsWith('H'));
            blockIcon.classList.toggle('material-symbols-outlined', !activeBlockOption.icon.startsWith('H'));
            blockIcon.textContent = iconHtml(activeBlockOption.icon);
        }
        if (blockLabel) blockLabel.textContent = activeBlockOption.label;

        const listIcon = writingToolbar.querySelector('[data-current-list-icon]');
        if (listIcon) listIcon.textContent = activeListOption?.icon || 'format_list_bulleted';

        const alignIcon = writingToolbar.querySelector('[data-current-align-icon]');
        if (alignIcon) alignIcon.textContent = activeAlignment.icon;

        const collapseIcon = writingToolbar.querySelector('[data-heading-collapse-icon]');
        const collapseLabel = writingToolbar.querySelector('[data-heading-collapse-label]');
        if (collapseIcon) collapseIcon.textContent = block?.props?.isCollapsed ? 'unfold_more' : 'unfold_less';
        if (collapseLabel) collapseLabel.textContent = block?.props?.isCollapsed ? 'Expand heading' : 'Collapse heading';

        writingToolbar.querySelectorAll('button[data-editor-action]').forEach((button) => {
            const action = button.dataset.editorAction;
            let active = false;
            let disabled = false;
            if (action === 'set-block') {
                const blockType = button.dataset.blockType;
                active = blockType === 'heading'
                    ? block?.type === 'heading' && Number(block?.props?.level) === Number(button.dataset.level)
                    : block?.type === blockType;
            } else if (action === 'align') {
                active = button.dataset.align === currentAlignment;
            } else if (action === 'basic-style') {
                active = Boolean(activeStyles?.[button.dataset.style]);
            } else if (action === 'text-color') {
                active = (activeStyles?.textColor || 'default') === button.dataset.color;
            } else if (action === 'highlight-color') {
                active = (activeStyles?.backgroundColor || 'default') === button.dataset.color;
            } else if (action === 'font-size') {
                active = activeFontPreset.value === button.dataset.fontSize;
            } else if (action === 'undo' || action === 'redo') {
                disabled = !canRunHistoryAction(action);
            } else if (action === 'indent' || action === 'outdent') {
                disabled = !canRunIndentAction(action);
            } else if (action === 'toggle-heading-collapse') {
                disabled = block?.type !== 'heading';
            } else if (['copy-blocks', 'cut-blocks', 'duplicate-blocks', 'delete-blocks', 'move-blocks-up', 'move-blocks-down'].includes(action)) {
                disabled = blocks.length === 0;
            } else if (action === 'zoom-out') {
                disabled = getZoomIndex() === 0;
            } else if (action === 'zoom-in') {
                disabled = getZoomIndex() === getZoomLevels().length - 1;
            }
            button.classList.toggle('is-active', active);
            if (action === 'basic-style') {
                button.setAttribute('aria-pressed', String(active));
            } else {
                button.removeAttribute('aria-pressed');
            }
            button.disabled = disabled;
            button.setAttribute('aria-disabled', String(disabled));
        });

        writingToolbar.querySelectorAll('[data-menu-check]').forEach((item) => {
            const key = item.dataset.menuCheck;
            const checked = key === activeBlockOption.key
                || key === activeListOption?.key
                || key === activeAlignment.key
                || key === `text-color-${activeStyles?.textColor || 'default'}`
                || key === `highlight-color-${activeStyles?.backgroundColor || 'default'}`
                || key === `font-size-${activeFontPreset.value}`;
            item.classList.toggle('is-active', checked);
            item.setAttribute('aria-checked', String(checked));
        });
    }

    function bindWritingToolbar() {
        if (!getCanEdit() || !writingToolbar) return;
        if (!claimElementBinding(writingToolbar, 'notesEditorToolbarBound')) {
            toolbarOverflowController?.refresh();
            return;
        }
        pageSetup?.bind?.();
        writingToolbar.hidden = false;
        toolbarOverflowController?.disconnect();
        toolbarOverflowController = createToolbarOverflowController(writingToolbar);

        writingToolbar.addEventListener('click', (event) => {
            const closeButton = event.target.closest('[data-toolbar-menu-close]');
            if (closeButton) {
                event.preventDefault();
                closeToolbarMenus();
                return;
            }

            const menuTrigger = event.target.closest('[data-toolbar-menu-trigger]');
            if (menuTrigger && writingToolbar.contains(menuTrigger)) {
                event.preventDefault();
                openToolbarMenu(menuTrigger.dataset.toolbarMenuTrigger, menuTrigger);
                return;
            }

            if (handlePageSetupToolbarClick(
                event,
                writingToolbar,
                pageSetupPopover,
                pageSetup?.openPageSetupPopover,
                pageSetup?.closePageSetupPopover
            )) return;

            const actionButton = event.target.closest('button[data-editor-action]');
            if (!actionButton || !writingToolbar.contains(actionButton)) return;
            event.preventDefault();

            const action = actionButton.dataset.editorAction;
            if (action === 'focus-body') {
                actions.focusEditorBody();
            } else if (action === 'insert-block') {
                actions.insertBlockFromMenu(actionButton);
            } else if (action === 'undo' || action === 'redo') {
                actions.runHistoryAction(action);
            } else if (action === 'zoom-out') {
                pageSetup?.setZoomIndex(getZoomIndex() - 1);
            } else if (action === 'zoom-in') {
                pageSetup?.setZoomIndex(getZoomIndex() + 1);
            } else if (action === 'basic-style') {
                actions.toggleBasicStyle(actionButton.dataset.style);
            } else if (action === 'text-color') {
                actions.applyTextColor(actionButton.dataset.color || 'default');
                closeToolbarMenus();
            } else if (action === 'highlight-color') {
                actions.applyHighlightColor(actionButton.dataset.color || 'default');
                closeToolbarMenus();
            } else if (action === 'font-size') {
                actions.applyFontSizePreset(actionButton.dataset.fontSize || 'default');
                closeToolbarMenus();
            } else if (action === 'remove-link') {
                actions.removeSelectedLink();
            } else if (action === 'set-block') {
                const blockType = actionButton.dataset.blockType;
                const level = Number(actionButton.dataset.level);
                actions.setSelectedBlocks(blockType, blockType === 'heading' ? { level: level || 1 } : undefined);
                closeToolbarMenus();
            } else if (action === 'align') {
                actions.applyTextAlignment(actionButton.dataset.align || 'left');
                closeToolbarMenus();
            } else if (action === 'indent' || action === 'outdent') {
                actions.runIndentAction(action);
            } else if (action === 'copy-blocks') {
                void actions.copySelectedBlocks();
                closeToolbarMenus();
            } else if (action === 'cut-blocks') {
                void actions.copySelectedBlocks({ cut: true });
                closeToolbarMenus();
            } else if (action === 'duplicate-blocks') {
                actions.duplicateSelectedBlocks();
                closeToolbarMenus();
            } else if (action === 'delete-blocks') {
                actions.deleteSelectedBlocks();
                closeToolbarMenus();
            } else if (action === 'move-blocks-up') {
                actions.moveSelectedBlocks('up');
                closeToolbarMenus();
            } else if (action === 'move-blocks-down') {
                actions.moveSelectedBlocks('down');
                closeToolbarMenus();
            } else if (action === 'toggle-heading-collapse') {
                actions.toggleHeadingCollapse();
                closeToolbarMenus();
            }
        });

        writingToolbar.addEventListener('input', (event) => {
            const input = event.target.closest('[data-add-block-search]');
            if (!input) return;
            filterAddBlockMenu(input.closest('[data-toolbar-menu="add-block"]'));
        });

        writingToolbar.addEventListener('keydown', (event) => {
            const menu = event.target.closest('[data-toolbar-menu="add-block"]');
            if (!menu || menu.hidden) return;
            const items = visibleAddBlockItems(menu);
            if (!items.length) return;

            if (event.key === 'ArrowDown') {
                event.preventDefault();
                setActiveAddBlockItem(menu, addBlockActiveIndex + 1);
            } else if (event.key === 'ArrowUp') {
                event.preventDefault();
                setActiveAddBlockItem(menu, addBlockActiveIndex - 1);
            } else if (event.key === 'Enter') {
                event.preventDefault();
                actions.insertBlockFromMenu(items[addBlockActiveIndex] || items[0]);
            }
        });

        writingToolbar.addEventListener('submit', (event) => {
            const menu = event.target.closest('[data-toolbar-menu="link"]');
            if (!menu) return;
            event.preventDefault();
            actions.applyLinkFromMenu(menu);
        });

        document.addEventListener('click', (event) => {
            if (activeToolbarMenu && !writingToolbar.contains(event.target)) {
                closeToolbarMenus();
            }
            if (actions.hasUrlBlockPopover() && !actions.getUrlBlockPopover().contains(event.target) && !writingToolbar?.contains(event.target)) {
                actions.resolveUrlBlockPopover('');
                actions.removeUrlBlockPopover();
            }
            if (!pageSetupPopover || pageSetupPopover.hidden) return;
            if (pageSetupPopover.contains(event.target) || actions.getActivePageSetupTrigger()?.contains(event.target)) return;
            pageSetup?.clearPageSetupDropdowns?.();
            pageSetup?.closePageSetupPopover?.();
        });

        document.addEventListener('keydown', (event) => {
            if (event.key === 'Escape') {
                if (activeToolbarMenu) closeToolbarMenus();
                if (pageSetupPopover && !pageSetupPopover.hidden) pageSetup?.closePageSetupPopover?.({ restoreFocus: true });
                if (actions.hasUrlBlockPopover()) {
                    actions.resolveUrlBlockPopover('');
                    actions.removeUrlBlockPopover();
                }
                return;
            }
            if (!getEditor()) return;
            const mod = event.metaKey || event.ctrlKey;
            if (mod && event.key.toLowerCase() === 'k') {
                event.preventDefault();
                const trigger = writingToolbar.querySelector('[data-toolbar-menu-trigger="link"]');
                openToolbarMenu('link', trigger);
            } else if (mod && event.shiftKey && event.key.toLowerCase() === 'l') {
                event.preventDefault();
                const trigger = writingToolbar.querySelector('[data-toolbar-menu-trigger="text-color"]');
                openToolbarMenu('text-color', trigger);
            } else if (mod && event.shiftKey && event.key.toLowerCase() === 'h') {
                event.preventDefault();
                const trigger = writingToolbar.querySelector('[data-toolbar-menu-trigger="highlight-color"]');
                openToolbarMenu('highlight-color', trigger);
            } else if (mod && event.altKey && event.key.toLowerCase() === 'h') {
                event.preventDefault();
                actions.toggleHeadingCollapse();
            } else if (event.altKey && !event.shiftKey && !event.metaKey && !event.ctrlKey && event.key === 'ArrowUp') {
                event.preventDefault();
                actions.moveSelectedBlocks('up');
            } else if (event.altKey && !event.shiftKey && !event.metaKey && !event.ctrlKey && event.key === 'ArrowDown') {
                event.preventDefault();
                actions.moveSelectedBlocks('down');
            } else if ((event.key === 'Delete' || event.key === 'Backspace') && getSelectedBlocks().length > 1) {
                event.preventDefault();
                actions.deleteSelectedBlocks();
            }
        });

        toolbarOverflowController?.refresh();
    }

    return {
        bindWritingToolbar,
        closeToolbarMenus,
        disconnect() {
            toolbarOverflowController?.disconnect();
            toolbarOverflowController = null;
        },
        refresh() {
            toolbarOverflowController?.refresh();
        },
        updateToolbarState,
    };
}
