const DEFAULT_FLOATING_MARGIN = 10;

export function composedEventPath(event) {
    if (typeof event?.composedPath === "function") return event.composedPath();
    const path = [];
    let node = event?.target || null;
    while (node) {
        path.push(node);
        node = node.parentNode || node.host || null;
    }
    if (typeof document !== "undefined" && !path.includes(document)) path.push(document);
    if (typeof window !== "undefined" && !path.includes(window)) path.push(window);
    return path;
}

function pathHasAttribute(path, attribute, value) {
    return path.some((node) => {
        if (typeof node?.getAttribute !== "function") return false;
        const attributeValue = node.getAttribute(attribute);
        return value == null ? attributeValue != null : attributeValue === value;
    });
}

export function shouldCloseFloatingLayer(event, { layers = [], owner = "", triggerAttribute = "" } = {}) {
    const path = composedEventPath(event);
    if (layers.some((layer) => layer && path.includes(layer))) return false;
    if (owner && pathHasAttribute(path, "data-task-floating-owner", owner)) return false;
    if (triggerAttribute && pathHasAttribute(path, triggerAttribute)) return false;
    return true;
}

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
