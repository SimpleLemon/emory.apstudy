import * as React from 'react';
import { createReactInlineContentSpec } from '@blocknote/react';

export const NOTE_IMAGE_ACCEPT = 'image/jpeg,image/png,image/gif,image/webp';
export const NOTE_IMAGE_MAX_BYTES = 10 * 1024 * 1024;

export function noteImageError(file) {
    if (!file) return 'Choose an image.';
    if (!NOTE_IMAGE_ACCEPT.split(',').includes(String(file.type || '').toLowerCase())) {
        return 'Use a JPEG, PNG, GIF, or WebP image.';
    }
    if (!file.size) return 'Image file is empty.';
    if (file.size > NOTE_IMAGE_MAX_BYTES) return 'Image exceeds the 10 MiB limit.';
    return '';
}

export function clipboardImageFiles(clipboardData) {
    return Array.from(clipboardData?.items || [])
        .filter((item) => item.kind === 'file' && String(item.type || '').startsWith('image/'))
        .map((item) => item.getAsFile())
        .filter(Boolean);
}

export function droppedImageFiles(dataTransfer) {
    return Array.from(dataTransfer?.files || []).filter((file) => String(file.type || '').startsWith('image/'));
}

export function clipboardHtmlImageSources(clipboardData) {
    const html = clipboardData?.getData?.('text/html') || '';
    if (!html || typeof DOMParser === 'undefined') return [];
    const documentRef = new DOMParser().parseFromString(html, 'text/html');
    if (documentRef.body.textContent?.trim()) return [];
    return Array.from(documentRef.querySelectorAll('img[src]'))
        .map((image) => image.getAttribute('src') || '')
        .filter((src) => /^data:image\/(?:jpeg|png|gif|webp);base64,/i.test(src) || /^https?:\/\//i.test(src));
}

export async function dataImageFile(source, index = 0) {
    const response = await fetch(source);
    const blob = await response.blob();
    return new File([blob], `clipboard-image-${index + 1}.${blob.type === 'image/jpeg' ? 'jpg' : blob.type.split('/')[1] || 'png'}`, { type: blob.type });
}

export function updateInlineImage(editor, clientId, props) {
    let target = null;
    editor?._tiptapEditor?.state?.doc?.descendants?.((node, pos) => {
        if (!target && node.type?.name === 'inlineImage' && node.attrs?.clientId === clientId) {
            target = { node, pos };
            return false;
        }
        return true;
    });
    if (!target) return false;
    const transaction = editor._tiptapEditor.state.tr.setNodeMarkup(target.pos, undefined, {
        ...target.node.attrs,
        ...props,
    });
    editor.dispatch(transaction);
    return true;
}

export function removeInlineImage(editor, clientId) {
    let target = null;
    editor?._tiptapEditor?.state?.doc?.descendants?.((node, pos) => {
        if (!target && node.type?.name === 'inlineImage' && node.attrs?.clientId === clientId) {
            target = { node, pos };
            return false;
        }
        return true;
    });
    if (!target) return false;
    editor.dispatch(editor._tiptapEditor.state.tr.delete(target.pos, target.pos + target.node.nodeSize));
    return target;
}

export function restoreInlineImage(editor, removed) {
    if (!removed?.node || !Number.isFinite(removed.pos)) return false;
    const position = Math.min(removed.pos, editor?._tiptapEditor?.state?.doc?.content?.size || removed.pos);
    editor.dispatch(editor._tiptapEditor.state.tr.insert(position, removed.node));
    return true;
}

function InlineImageView({ inlineContent, updateInlineContent }) {
    const props = inlineContent.props;
    const [open, setOpen] = React.useState(false);
    const [progress, setProgress] = React.useState(0);
    const rootRef = React.useRef(null);

    React.useEffect(() => {
        const onProgress = (event) => {
            if (event.detail?.clientId === props.clientId) setProgress(Number(event.detail.progress || 0));
        };
        const onPointer = (event) => {
            if (!rootRef.current?.contains(event.target)) setOpen(false);
        };
        window.addEventListener('notes-image-upload-progress', onProgress);
        document.addEventListener('pointerdown', onPointer);
        return () => {
            window.removeEventListener('notes-image-upload-progress', onProgress);
            document.removeEventListener('pointerdown', onPointer);
        };
    }, [props.clientId]);

    const update = (nextProps) => updateInlineContent({
        type: 'inlineImage',
        props: { ...props, ...nextProps },
    });
    const ownLine = props.layout === 'break';
    const style = ownLine
        ? { width: '100%', justifyContent: props.alignment === 'right' ? 'flex-end' : props.alignment === 'center' ? 'center' : 'flex-start' }
        : undefined;

    return React.createElement(
        'span',
        {
            ref: rootRef,
            className: `notes-inline-image notes-inline-image-${props.layout} notes-inline-image-${props.status}`,
            style,
            contentEditable: 'false',
            onClick: () => setOpen(true),
        },
        props.status === 'uploading'
            ? React.createElement('span', { className: 'notes-inline-image-placeholder', style: { width: `${props.width}px` } },
                React.createElement('span', { className: 'material-symbols-outlined', 'aria-hidden': 'true' }, 'image'),
                React.createElement('span', null, progress ? `Uploading ${progress}%` : 'Uploading image…'))
            : props.status === 'error'
                ? React.createElement('span', { className: 'notes-inline-image-placeholder is-error', style: { width: `${props.width}px` } },
                    React.createElement('span', null, props.error || 'Upload failed'),
                    React.createElement('button', { type: 'button', onClick: (event) => {
                        event.stopPropagation();
                        window.dispatchEvent(new CustomEvent('notes-image-retry', { detail: { clientId: props.clientId } }));
                    } }, 'Retry'))
                : React.createElement('img', {
                    src: props.url,
                    alt: props.alt || '',
                    width: props.width,
                    className: 'notes-inline-image-element',
                    draggable: false,
                    loading: 'lazy',
                    decoding: 'async',
                }),
        open && props.status === 'ready' ? React.createElement(
            'span',
            { className: 'notes-inline-image-toolbar', role: 'toolbar', 'aria-label': 'Image formatting' },
            React.createElement('label', null, React.createElement('span', null, 'Alt text'), React.createElement('input', {
                value: props.alt,
                onChange: (event) => update({ alt: event.target.value }),
                onClick: (event) => event.stopPropagation(),
            })),
            React.createElement('label', null, React.createElement('span', null, 'Width'), React.createElement('input', {
                type: 'range', min: 48, max: 900, value: props.width,
                onChange: (event) => update({ width: Number(event.target.value) }),
                onClick: (event) => event.stopPropagation(),
            })),
            React.createElement('span', { className: 'notes-inline-image-buttons' },
                React.createElement('button', { type: 'button', className: props.layout === 'inline' ? 'is-active' : '', onClick: () => update({ layout: 'inline', alignment: 'left' }) }, 'Inline'),
                React.createElement('button', { type: 'button', className: ownLine ? 'is-active' : '', onClick: () => update({ layout: 'break' }) }, 'Own line')),
            React.createElement('span', { className: 'notes-inline-image-buttons' },
                ['left', 'center', 'right'].map((alignment) => React.createElement('button', {
                    key: alignment,
                    type: 'button',
                    title: `Align ${alignment}`,
                    className: ownLine && props.alignment === alignment ? 'is-active' : '',
                    onClick: () => update({ layout: 'break', alignment }),
                }, React.createElement('span', { className: 'material-symbols-outlined', 'aria-hidden': 'true' }, `format_align_${alignment}`)))),
            React.createElement('button', { type: 'button', onClick: () => window.dispatchEvent(new CustomEvent('notes-image-replace', { detail: { clientId: props.clientId } })) }, 'Replace'),
            React.createElement('button', { type: 'button', className: 'is-danger', onClick: () => window.dispatchEvent(new CustomEvent('notes-image-remove', { detail: { clientId: props.clientId, mediaId: props.mediaId } })) }, 'Delete')
        ) : null
    );
}

export const inlineImageSpec = createReactInlineContentSpec(
    {
        type: 'inlineImage',
        propSchema: {
            url: { default: '' },
            mediaId: { default: '' },
            clientId: { default: '' },
            alt: { default: '' },
            width: { default: 240 },
            layout: { default: 'inline', values: ['inline', 'break'] },
            alignment: { default: 'left', values: ['left', 'center', 'right'] },
            status: { default: 'ready', values: ['uploading', 'ready', 'error'] },
            error: { default: '' },
        },
        content: 'none',
    },
    { render: InlineImageView }
);
