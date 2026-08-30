import "../core/ui-primitives-module.js?v=9c86ecb81c990de80a6ac1e903018413ee8cf0355279ce8295614463605110ea";
import "./utils.js?v=9c86ecb81c990de80a6ac1e903018413ee8cf0355279ce8295614463605110ea";
import "./state.js?v=9c86ecb81c990de80a6ac1e903018413ee8cf0355279ce8295614463605110ea";
import "./core.js?v=9c86ecb81c990de80a6ac1e903018413ee8cf0355279ce8295614463605110ea";
import "./integrations/course-modal.js?v=9c86ecb81c990de80a6ac1e903018413ee8cf0355279ce8295614463605110ea";
import "./integrations/courses.js?v=9c86ecb81c990de80a6ac1e903018413ee8cf0355279ce8295614463605110ea";
import "./menu.js?v=9c86ecb81c990de80a6ac1e903018413ee8cf0355279ce8295614463605110ea";
import "./preferences.js?v=9c86ecb81c990de80a6ac1e903018413ee8cf0355279ce8295614463605110ea";
import "./integrations/data.js?v=9c86ecb81c990de80a6ac1e903018413ee8cf0355279ce8295614463605110ea";
import "./views/event-render.js?v=9c86ecb81c990de80a6ac1e903018413ee8cf0355279ce8295614463605110ea";
import "./events/ui-actions.js?v=9c86ecb81c990de80a6ac1e903018413ee8cf0355279ce8295614463605110ea";
import "./views/agenda.js?v=9c86ecb81c990de80a6ac1e903018413ee8cf0355279ce8295614463605110ea";
import "./views/month-view.js?v=9c86ecb81c990de80a6ac1e903018413ee8cf0355279ce8295614463605110ea";
import "./views/week-view.js?v=9c86ecb81c990de80a6ac1e903018413ee8cf0355279ce8295614463605110ea";
import "./views/render-shell.js?v=9c86ecb81c990de80a6ac1e903018413ee8cf0355279ce8295614463605110ea";
import "./integrations/sources.js?v=9c86ecb81c990de80a6ac1e903018413ee8cf0355279ce8295614463605110ea";
import "./integrations/share.js?v=9c86ecb81c990de80a6ac1e903018413ee8cf0355279ce8295614463605110ea";
import "./controls.js?v=9c86ecb81c990de80a6ac1e903018413ee8cf0355279ce8295614463605110ea";
import "./bootstrap.js?v=9c86ecb81c990de80a6ac1e903018413ee8cf0355279ce8295614463605110ea";
import "./events/context-menu.js?v=9c86ecb81c990de80a6ac1e903018413ee8cf0355279ce8295614463605110ea";
import "./events/event-form.js?v=9c86ecb81c990de80a6ac1e903018413ee8cf0355279ce8295614463605110ea";
import { createCalendarLifecycle } from "./lifecycle.js?v=9c86ecb81c990de80a6ac1e903018413ee8cf0355279ce8295614463605110ea";
import { mountCalendar } from "./index.js?v=9c86ecb81c990de80a6ac1e903018413ee8cf0355279ce8295614463605110ea";
import { createCalendarDataAdapter } from "./adapter.js?v=9c86ecb81c990de80a6ac1e903018413ee8cf0355279ce8295614463605110ea";

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
