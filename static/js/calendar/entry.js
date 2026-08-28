import "../core/ui-primitives-module.js?v=e618f7039349a94f81f18f8ce31a6d986c7ae808a3b1069594faec0180fa9a41";
import "./utils.js?v=e618f7039349a94f81f18f8ce31a6d986c7ae808a3b1069594faec0180fa9a41";
import "./state.js?v=e618f7039349a94f81f18f8ce31a6d986c7ae808a3b1069594faec0180fa9a41";
import "./core.js?v=e618f7039349a94f81f18f8ce31a6d986c7ae808a3b1069594faec0180fa9a41";
import "./integrations/course-modal.js?v=e618f7039349a94f81f18f8ce31a6d986c7ae808a3b1069594faec0180fa9a41";
import "./integrations/courses.js?v=e618f7039349a94f81f18f8ce31a6d986c7ae808a3b1069594faec0180fa9a41";
import "./menu.js?v=e618f7039349a94f81f18f8ce31a6d986c7ae808a3b1069594faec0180fa9a41";
import "./preferences.js?v=e618f7039349a94f81f18f8ce31a6d986c7ae808a3b1069594faec0180fa9a41";
import "./integrations/data.js?v=e618f7039349a94f81f18f8ce31a6d986c7ae808a3b1069594faec0180fa9a41";
import "./views/event-render.js?v=e618f7039349a94f81f18f8ce31a6d986c7ae808a3b1069594faec0180fa9a41";
import "./events/ui-actions.js?v=e618f7039349a94f81f18f8ce31a6d986c7ae808a3b1069594faec0180fa9a41";
import "./views/agenda.js?v=e618f7039349a94f81f18f8ce31a6d986c7ae808a3b1069594faec0180fa9a41";
import "./views/month-view.js?v=e618f7039349a94f81f18f8ce31a6d986c7ae808a3b1069594faec0180fa9a41";
import "./views/week-view.js?v=e618f7039349a94f81f18f8ce31a6d986c7ae808a3b1069594faec0180fa9a41";
import "./views/render-shell.js?v=e618f7039349a94f81f18f8ce31a6d986c7ae808a3b1069594faec0180fa9a41";
import "./integrations/sources.js?v=e618f7039349a94f81f18f8ce31a6d986c7ae808a3b1069594faec0180fa9a41";
import "./integrations/share.js?v=e618f7039349a94f81f18f8ce31a6d986c7ae808a3b1069594faec0180fa9a41";
import "./controls.js?v=e618f7039349a94f81f18f8ce31a6d986c7ae808a3b1069594faec0180fa9a41";
import "./bootstrap.js?v=e618f7039349a94f81f18f8ce31a6d986c7ae808a3b1069594faec0180fa9a41";
import "./events/context-menu.js?v=e618f7039349a94f81f18f8ce31a6d986c7ae808a3b1069594faec0180fa9a41";
import "./events/event-form.js?v=e618f7039349a94f81f18f8ce31a6d986c7ae808a3b1069594faec0180fa9a41";
import { createCalendarLifecycle } from "./lifecycle.js?v=e618f7039349a94f81f18f8ce31a6d986c7ae808a3b1069594faec0180fa9a41";
import { mountCalendar } from "./index.js?v=e618f7039349a94f81f18f8ce31a6d986c7ae808a3b1069594faec0180fa9a41";
import { createCalendarDataAdapter } from "./adapter.js?v=e618f7039349a94f81f18f8ce31a6d986c7ae808a3b1069594faec0180fa9a41";

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
