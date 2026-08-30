import "../core/ui-primitives-module.js?v=9e869432dca0b68820513c16fc20f84294c53573be227500124b39abb06e13ee";
import "./utils.js?v=9e869432dca0b68820513c16fc20f84294c53573be227500124b39abb06e13ee";
import "./state.js?v=9e869432dca0b68820513c16fc20f84294c53573be227500124b39abb06e13ee";
import "./core.js?v=9e869432dca0b68820513c16fc20f84294c53573be227500124b39abb06e13ee";
import "./integrations/course-modal.js?v=9e869432dca0b68820513c16fc20f84294c53573be227500124b39abb06e13ee";
import "./integrations/courses.js?v=9e869432dca0b68820513c16fc20f84294c53573be227500124b39abb06e13ee";
import "./menu.js?v=9e869432dca0b68820513c16fc20f84294c53573be227500124b39abb06e13ee";
import "./preferences.js?v=9e869432dca0b68820513c16fc20f84294c53573be227500124b39abb06e13ee";
import "./integrations/data.js?v=9e869432dca0b68820513c16fc20f84294c53573be227500124b39abb06e13ee";
import "./views/event-render.js?v=9e869432dca0b68820513c16fc20f84294c53573be227500124b39abb06e13ee";
import "./events/ui-actions.js?v=9e869432dca0b68820513c16fc20f84294c53573be227500124b39abb06e13ee";
import "./views/agenda.js?v=9e869432dca0b68820513c16fc20f84294c53573be227500124b39abb06e13ee";
import "./views/month-view.js?v=9e869432dca0b68820513c16fc20f84294c53573be227500124b39abb06e13ee";
import "./views/week-view.js?v=9e869432dca0b68820513c16fc20f84294c53573be227500124b39abb06e13ee";
import "./views/render-shell.js?v=9e869432dca0b68820513c16fc20f84294c53573be227500124b39abb06e13ee";
import "./integrations/sources.js?v=9e869432dca0b68820513c16fc20f84294c53573be227500124b39abb06e13ee";
import "./integrations/share.js?v=9e869432dca0b68820513c16fc20f84294c53573be227500124b39abb06e13ee";
import "./controls.js?v=9e869432dca0b68820513c16fc20f84294c53573be227500124b39abb06e13ee";
import "./bootstrap.js?v=9e869432dca0b68820513c16fc20f84294c53573be227500124b39abb06e13ee";
import "./events/context-menu.js?v=9e869432dca0b68820513c16fc20f84294c53573be227500124b39abb06e13ee";
import "./events/event-form.js?v=9e869432dca0b68820513c16fc20f84294c53573be227500124b39abb06e13ee";
import { createCalendarLifecycle } from "./lifecycle.js?v=9e869432dca0b68820513c16fc20f84294c53573be227500124b39abb06e13ee";
import { mountCalendar } from "./index.js?v=9e869432dca0b68820513c16fc20f84294c53573be227500124b39abb06e13ee";
import { createCalendarDataAdapter } from "./adapter.js?v=9e869432dca0b68820513c16fc20f84294c53573be227500124b39abb06e13ee";

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
