import "../core/ui-primitives-module.js?v=32eec5b276367ae3c9bbbb502fe8b544ffa3e5147a1b831249095e4f15570cb2";
import "./utils.js?v=32eec5b276367ae3c9bbbb502fe8b544ffa3e5147a1b831249095e4f15570cb2";
import "./state.js?v=32eec5b276367ae3c9bbbb502fe8b544ffa3e5147a1b831249095e4f15570cb2";
import "./core.js?v=32eec5b276367ae3c9bbbb502fe8b544ffa3e5147a1b831249095e4f15570cb2";
import "./integrations/course-modal.js?v=32eec5b276367ae3c9bbbb502fe8b544ffa3e5147a1b831249095e4f15570cb2";
import "./integrations/courses.js?v=32eec5b276367ae3c9bbbb502fe8b544ffa3e5147a1b831249095e4f15570cb2";
import "./menu.js?v=32eec5b276367ae3c9bbbb502fe8b544ffa3e5147a1b831249095e4f15570cb2";
import "./preferences.js?v=32eec5b276367ae3c9bbbb502fe8b544ffa3e5147a1b831249095e4f15570cb2";
import "./integrations/data.js?v=32eec5b276367ae3c9bbbb502fe8b544ffa3e5147a1b831249095e4f15570cb2";
import "./views/event-render.js?v=32eec5b276367ae3c9bbbb502fe8b544ffa3e5147a1b831249095e4f15570cb2";
import "./events/ui-actions.js?v=32eec5b276367ae3c9bbbb502fe8b544ffa3e5147a1b831249095e4f15570cb2";
import "./views/agenda.js?v=32eec5b276367ae3c9bbbb502fe8b544ffa3e5147a1b831249095e4f15570cb2";
import "./views/month-view.js?v=32eec5b276367ae3c9bbbb502fe8b544ffa3e5147a1b831249095e4f15570cb2";
import "./views/week-view.js?v=32eec5b276367ae3c9bbbb502fe8b544ffa3e5147a1b831249095e4f15570cb2";
import "./views/render-shell.js?v=32eec5b276367ae3c9bbbb502fe8b544ffa3e5147a1b831249095e4f15570cb2";
import "./integrations/sources.js?v=32eec5b276367ae3c9bbbb502fe8b544ffa3e5147a1b831249095e4f15570cb2";
import "./integrations/share.js?v=32eec5b276367ae3c9bbbb502fe8b544ffa3e5147a1b831249095e4f15570cb2";
import "./controls.js?v=32eec5b276367ae3c9bbbb502fe8b544ffa3e5147a1b831249095e4f15570cb2";
import "./bootstrap.js?v=32eec5b276367ae3c9bbbb502fe8b544ffa3e5147a1b831249095e4f15570cb2";
import "./events/context-menu.js?v=32eec5b276367ae3c9bbbb502fe8b544ffa3e5147a1b831249095e4f15570cb2";
import "./events/event-form.js?v=32eec5b276367ae3c9bbbb502fe8b544ffa3e5147a1b831249095e4f15570cb2";
import { createCalendarLifecycle } from "./lifecycle.js?v=32eec5b276367ae3c9bbbb502fe8b544ffa3e5147a1b831249095e4f15570cb2";
import { mountCalendar } from "./index.js?v=32eec5b276367ae3c9bbbb502fe8b544ffa3e5147a1b831249095e4f15570cb2";
import { createCalendarDataAdapter } from "./adapter.js?v=32eec5b276367ae3c9bbbb502fe8b544ffa3e5147a1b831249095e4f15570cb2";

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
