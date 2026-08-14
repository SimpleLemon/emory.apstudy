export function createCalendarLifecycle({ view = globalThis.window || globalThis } = {}) {
        const cleanups = new Set();
        const controllers = new Set();
        const observers = new Set();
        let disposed = false;

        function addCleanup(cleanup) {
            if (typeof cleanup !== "function") return cleanup;
            if (disposed) {
                cleanup();
                return cleanup;
            }
            cleanups.add(cleanup);
            return cleanup;
        }

        function addEventListener(target, type, listener, options) {
            if (!target?.addEventListener || typeof listener !== "function") return () => {};
            target.addEventListener(type, listener, options);
            return addCleanup(() => target.removeEventListener(type, listener, options));
        }

        function setTimeoutTracked(callback, delay, ...args) {
            const timer = (view.setTimeout || setTimeout).call(view, () => {
                cleanups.delete(cancel);
                if (disposed) return;
                callback(...args);
            }, delay);
            const cancel = () => (view.clearTimeout || clearTimeout).call(view, timer);
            addCleanup(cancel);
            return timer;
        }

        function clearTimeoutTracked(timer) {
            if (timer == null) return;
            (view.clearTimeout || clearTimeout).call(view, timer);
        }

        function requestAnimationFrameTracked(callback) {
            if (typeof view.requestAnimationFrame !== "function") return setTimeoutTracked(callback, 0);
            const frame = view.requestAnimationFrame(() => {
                cleanups.delete(cancel);
                if (disposed) return;
                callback();
            });
            const cancel = () => view.cancelAnimationFrame?.(frame);
            addCleanup(cancel);
            return frame;
        }

        function trackAbortController(controller) {
            const AbortControllerConstructor = view.AbortController || globalThis.AbortController;
            controller ||= new AbortControllerConstructor();
            if (disposed) {
                controller.abort();
                return controller;
            }
            controllers.add(controller);
            addCleanup(() => controllers.delete(controller));
            return controller;
        }

        function releaseAbortController(controller) {
            controllers.delete(controller);
        }

        function trackObserver(observer) {
            if (!observer || typeof observer.disconnect !== "function") return observer;
            observers.add(observer);
            return addCleanup(() => {
                if (observers.delete(observer)) observer.disconnect();
            });
        }

        function trackNode(node) {
            if (!node) return node;
            addCleanup(() => node.remove?.());
            return node;
        }

        function dispose() {
            if (disposed) return;
            disposed = true;
            for (const controller of controllers) controller.abort();
            controllers.clear();
            for (const observer of observers) observer.disconnect();
            observers.clear();
            for (const cleanup of Array.from(cleanups).reverse()) {
                try {
                    cleanup();
                } catch (error) {
                    console.warn("Calendar cleanup failed:", error);
                }
            }
            cleanups.clear();
        }

        return {
            addCleanup,
            addEventListener,
            clearTimeout: clearTimeoutTracked,
            dispose,
            isDisposed: () => disposed,
            requestAnimationFrame: requestAnimationFrameTracked,
            releaseAbortController,
            setTimeout: setTimeoutTracked,
            trackAbortController,
            trackObserver,
            trackNode,
        };
}

if (typeof window !== "undefined") {
    window.APStudyCalendarLifecycle = {
        ...(window.APStudyCalendarLifecycle || {}),
        createCalendarLifecycle,
    };
}
