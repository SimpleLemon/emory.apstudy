(function registerNotesListUtils(global) {
    const { escapeHtml } = global.APStudyUIPrimitives;

    function formatCount(count, singular) {
        return `${count} ${singular}${count === 1 ? '' : 's'}`;
    }

    function formatDate(value) {
        const date = new Date(value || Date.now());
        if (Number.isNaN(date.getTime())) return 'Updated recently';
        return `Updated ${date.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })}`;
    }

    function notePreview(note) {
        if (typeof note.preview_text === 'string' && note.preview_text.trim()) {
            return note.preview_text.trim();
        }
        if (typeof note.content !== 'string' || !note.content.trim()) return 'Blank note';
        try {
            const blocks = JSON.parse(note.content);
            const blockText = (block) => {
                if (!block) return '';
                if (block.type === 'bookmark') return [block.props?.title, block.props?.description, block.props?.url].filter(Boolean).join(' ');
                if (block.type === 'image' || block.type === 'video') return [block.props?.caption, block.props?.name, block.props?.url].filter(Boolean).join(' ');
                if (block.type === 'divider' || block.type === 'pageBreak' || block.type === 'horizontalRule') return '';
                const own = Array.isArray(block.content)
                    ? block.content.map((item) => {
                        if (item?.type === 'link' && Array.isArray(item.content)) {
                            return item.content.map((child) => child?.text || '').join('');
                        }
                        return item?.text || '';
                    }).join(' ')
                    : '';
                const children = Array.isArray(block.children) ? block.children.map(blockText).join(' ') : '';
                return `${own} ${children}`.trim();
            };
            const text = (Array.isArray(blocks) ? blocks : [])
                .map(blockText)
                .join(' ')
                .replace(/\s+/g, ' ')
                .trim();
            return text || 'Blank note';
        } catch {
            return 'Blank note';
        }
    }

    function buildLoadingIndicatorHtml(label = 'Loading notes...', options = {}) {
        return global.APStudyLoader.html(label, options);
    }

    let editorBundlePreloaded = false;
    function preloadEditorBundle() {
        if (editorBundlePreloaded) return;
        editorBundlePreloaded = true;
        const scriptPath = '/static/js/notes/dist/notes-editor-v17.js';
        if (document.querySelector(`link[rel="modulepreload"][href="${scriptPath}"]`)) return;
        const link = document.createElement('link');
        link.rel = 'modulepreload';
        link.href = scriptPath;
        document.head.appendChild(link);
    }

    async function apiJson(url, options = {}) {
        return global.APStudyHttp.fetchJson(url, {
            ...options,
            pendingLabel: options.pendingLabel || "notes-save",
        });
    }

    global.APStudyNotesListUtils = {
        apiJson,
        buildLoadingIndicatorHtml,
        escapeHtml,
        formatCount,
        formatDate,
        notePreview,
        preloadEditorBundle,
    };
})(window);
