const DEFAULT_FLOATING_MARGIN = 10;

function viewportSize() {
    return {
        width: Math.max(0, Number(window.innerWidth) || document.documentElement.clientWidth || 0),
        height: Math.max(0, Number(window.innerHeight) || document.documentElement.clientHeight || 0),
    };
}

export function getFloatingPosition(anchor, floatingRect, { align = "end", gap = 8, margin = DEFAULT_FLOATING_MARGIN } = {}) {
    const viewport = viewportSize();
    const width = Math.max(0, floatingRect?.width || 0);
    const height = Math.max(0, floatingRect?.height || 0);
    const safeMargin = Math.min(margin, Math.max(0, Math.floor(Math.min(viewport.width, viewport.height) / 2)));
    const source = anchor || { top: safeMargin, right: safeMargin, bottom: safeMargin, left: safeMargin };
    const maxLeft = Math.max(safeMargin, viewport.width - width - safeMargin);
    const preferredLeft = align === "start" ? source.left : source.right - width;
    const alternateLeft = align === "start" ? source.right - width : source.left;
    let left = preferredLeft;

    if (left < safeMargin || left > maxLeft) left = alternateLeft;
    left = Math.max(safeMargin, Math.min(maxLeft, left));

    const maxTop = Math.max(safeMargin, viewport.height - height - safeMargin);
    const belowTop = source.bottom + gap;
    const aboveTop = source.top - height - gap;
    let top = belowTop;
    if (belowTop > maxTop && aboveTop >= safeMargin) top = aboveTop;
    top = Math.max(safeMargin, Math.min(maxTop, top));

    return { top, left };
}
