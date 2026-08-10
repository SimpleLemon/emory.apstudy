import * as React from "react";
import { createPortal } from "react-dom";
import { getFloatingPosition, shouldCloseFloatingLayer } from "./task-floating.js";

const h = React.createElement;

export function AddTaskPopover({ popover, onClose, children }) {
    const popoverRef = React.useRef(null);
    const previousFocusRef = React.useRef(null);
    const onCloseRef = React.useRef(onClose);
    const floatingOwner = `task-popover-${React.useId()}`;
    const popoverKey = popover ? `${popover.type}:${popover.nonce || 0}` : "";
    const [position, setPosition] = React.useState({ key: "", top: 0, left: 0, ready: false });

    React.useEffect(() => {
        onCloseRef.current = onClose;
    }, [onClose]);

    React.useEffect(() => {
        if (!popover) return undefined;
        previousFocusRef.current = document.activeElement;
        const onPointerDown = (event) => {
            if (shouldCloseFloatingLayer(event, {
                layers: [popoverRef.current],
                owner: floatingOwner,
                triggerAttribute: "data-task-add-popover-trigger",
            })) onCloseRef.current();
        };
        const onKeyDown = (event) => {
            if (event.key === "Escape") onCloseRef.current();
        };
        const onScroll = (event) => {
            if (shouldCloseFloatingLayer(event, {
                layers: [popoverRef.current],
                owner: floatingOwner,
            })) onCloseRef.current();
        };
        const onResize = () => onCloseRef.current();
        document.addEventListener("pointerdown", onPointerDown);
        document.addEventListener("keydown", onKeyDown);
        window.addEventListener("scroll", onScroll, true);
        window.addEventListener("resize", onResize);
        window.visualViewport?.addEventListener("resize", onResize);
        return () => {
            document.removeEventListener("pointerdown", onPointerDown);
            document.removeEventListener("keydown", onKeyDown);
            window.removeEventListener("scroll", onScroll, true);
            window.removeEventListener("resize", onResize);
            window.visualViewport?.removeEventListener("resize", onResize);
            if (popoverRef.current?.contains(document.activeElement)) {
                previousFocusRef.current?.focus?.({ preventScroll: true });
            }
        };
    }, [popover]);

    React.useLayoutEffect(() => {
        if (!popover || !popoverRef.current) return;
        const reposition = () => {
            const popoverRect = popoverRef.current?.getBoundingClientRect();
            if (!popoverRect) return;
            const next = getFloatingPosition(popover.anchor, popoverRect, { align: "start", gap: 8 });
            setPosition({ key: popoverKey, ...next, ready: true });
        };
        reposition();
        if (typeof ResizeObserver === "undefined") return undefined;
        const observer = new ResizeObserver(reposition);
        observer.observe(popoverRef.current);
        return () => observer.disconnect();
    }, [popover, popoverKey]);

    const ready = Boolean(popover && position.key === popoverKey && position.ready);
    React.useEffect(() => {
        if (ready) popoverRef.current?.querySelector("button:not([disabled]), input:not([disabled]), select:not([disabled])")?.focus({ preventScroll: true });
    }, [popoverKey, ready]);

    if (!popover) return null;
    const layer = h("div", {
        ref: popoverRef,
        className: "task-add-popover",
        role: "dialog",
        "aria-label": "Task options",
        "data-task-floating-layer": "add-task-popover",
        "data-task-floating-owner": floatingOwner,
        style: {
            top: `${ready ? position.top : 0}px`,
            left: `${ready ? position.left : 0}px`,
            visibility: ready ? "visible" : "hidden",
        },
    }, typeof children === "function" ? children({ floatingOwner }) : children);
    return document.body ? createPortal(layer, document.body) : layer;
}
