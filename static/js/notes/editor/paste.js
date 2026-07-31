import {
    clipboardHtmlImageSources,
    clipboardImageFiles,
    dataImageFile,
} from './images.js';
import { normalizeClipboardMarkdown, normalizeClipboardText, clipboardTextLooksStructured } from './markdown-repair.js';
import { insertInlineImageFile, insertInlineImageNode } from './image-runtime.js';

export function handleNotesPaste({ event, editor, defaultPasteHandler, noteId, onChange }) {
    const clipboardData = event.clipboardData;
    const imageFiles = clipboardImageFiles(clipboardData);
    if (imageFiles.length) {
        event.preventDefault();
        imageFiles.forEach((file) => insertInlineImageFile(editor, file, { noteId, onChange }));
        return true;
    }
    const htmlImages = clipboardHtmlImageSources(clipboardData);
    if (htmlImages.length) {
        event.preventDefault();
        htmlImages.forEach((source, index) => {
            if (/^data:/i.test(source)) {
                void dataImageFile(source, index).then((file) => insertInlineImageFile(editor, file, { noteId, onChange }));
            } else {
                insertInlineImageNode(editor, { url: source, mediaId: '', clientId: `url-${Date.now()}-${index}`, alt: '', width: 240, layout: 'inline', alignment: 'left', status: 'ready', error: '' });
            }
        });
        return true;
    }
    const rawPlainText = clipboardData?.getData('text/plain') || '';
    if (clipboardData?.getData('blocknote/html')) {
        return defaultPasteHandler({
            prioritizeMarkdownOverHTML: false,
            plainTextAsMarkdown: false,
        });
    }

    const explicitMarkdown = clipboardData?.getData('text/markdown') || '';
    if (explicitMarkdown) {
        void editor.pasteMarkdown?.(normalizeClipboardMarkdown(explicitMarkdown));
        return true;
    }

    const plainText = normalizeClipboardText(rawPlainText);
    const looksStructured = clipboardTextLooksStructured(plainText);
    if (looksStructured) {
        void editor.pasteMarkdown?.(normalizeClipboardMarkdown(plainText));
        return true;
    }

    return defaultPasteHandler({
        prioritizeMarkdownOverHTML: false,
        plainTextAsMarkdown: false,
    });
}
