import "../core/ui-primitives-module.js";
import "./utils.js";
import "./state.js";
import "./core.js";
import "./integrations/course-modal.js";
import "./integrations/courses.js";
import "./menu.js";
import "./preferences.js";
import "./integrations/data.js";
import "./views/event-render.js";
import "./events/ui-actions.js";
import "./views/agenda.js";
import "./views/month-view.js";
import "./views/week-view.js";
import "./views/render-shell.js";
import "./integrations/sources.js";
import "./integrations/share.js";
import "./controls.js";
import "./bootstrap.js";
import "./events/context-menu.js";
import "./events/event-form.js";
import { createCalendarLifecycle } from "./lifecycle.js";
import { mountCalendar } from "./index.js";
import { createCalendarDataAdapter } from "./adapter.js";

let activeDispose = null;

export function bootCalendar(root = document.querySelector("#calendar-view-root"), capabilities = {}) {
    activeDispose?.();
    activeDispose = null;
    if (!root || root.nodeType !== 1) return () => {};

    const pageRoot = capabilities.pageRoot?.nodeType === 1
        ? capabilities.pageRoot
        : root.closest?.("#calendar-app-root") || root;
    const lifecycle = capabilities.lifecycle || createCalendarLifecycle({
        view: capabilities.view || root.ownerDocument?.defaultView || globalThis,
    });
    const handle = mountCalendar(root, createCalendarDataAdapter(capabilities.adapterOverrides), {
        ...capabilities,
        lifecycle,
        pageRoot,
    });
    let disposed = false;
    const dispose = () => {
        if (disposed) return;
        disposed = true;
        handle();
        if (activeDispose === dispose) activeDispose = null;
    };
    activeDispose = dispose;
    return dispose;
}

if (typeof document !== "undefined") bootCalendar();
