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
import "./extension.css";
import { CALENDAR_EXTENSION_CONTRACT_VERSION } from "./capabilities.js";
import { mountCalendar } from "./index.js";
import { createCalendarDataAdapter } from "./adapter.js";

export { CALENDAR_EXTENSION_CONTRACT_VERSION, createCalendarDataAdapter, mountCalendar };

const extensionApi = {
    contractVersion: CALENDAR_EXTENSION_CONTRACT_VERSION,
    mountCalendar,
    createCalendarDataAdapter,
};

if (typeof globalThis !== "undefined") {
    globalThis.APStudyCalendarExtension = extensionApi;
}
