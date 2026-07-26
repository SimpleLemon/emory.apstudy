import {
  addDays,
  escapeHtml,
  formatEventTime,
  formatLongDate,
} from "./echo-utils.js";

function formatEventDateRange(event) {
  if (!event?.startDate) return "";
  if (!event.isAllDay) {
    const start = formatLongDate(event.startDate, { year: "numeric" });
    return `${start} · ${formatEventTime(event)}`;
  }

  const start = formatLongDate(event.startDate, { year: "numeric" });
  if (!event.endDate || event.endDate <= addDays(event.startDate, 1)) return `${start} · All day`;
  const inclusiveEnd = addDays(event.endDate, -1);
  return `${start} – ${formatLongDate(inclusiveEnd, { year: "numeric" })} · All day`;
}

function detailRow(label, value, className = "") {
  if (!String(value || "").trim()) return "";
  return `
    <div class="echo-event-detail-row${className ? ` ${className}` : ""}">
      <dt>${escapeHtml(label)}</dt>
      <dd>${escapeHtml(value)}</dd>
    </div>
  `;
}

export function buildEventDetailsHtml(event) {
  if (!event) return `<p class="echo-event-details-empty">Event details are unavailable.</p>`;
  const description = String(event.description || "").trim();
  return `
    <dl class="echo-event-detail-list">
      ${detailRow("When", formatEventDateRange(event))}
      ${detailRow("Calendar", event.calendarLabel || event.calendar_id || "Calendar")}
      ${detailRow("Course", event.course)}
      ${detailRow("Type", event.type)}
      ${detailRow("Description", description, "echo-event-detail-row--description")}
    </dl>
  `;
}

const FOCUSABLE_SELECTOR = [
  "button:not([disabled])",
  "a[href]",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

export function createEchoEventDetails({ modal, titleNode, bodyNode } = {}) {
  let opener = null;

  function isOpen() {
    return Boolean(modal && !modal.hidden);
  }

  function close({ restoreFocus = true } = {}) {
    if (!modal || modal.hidden) return;
    modal.hidden = true;
    if (restoreFocus && opener?.isConnected) opener.focus({ preventScroll: true });
    opener = null;
  }

  function open(event, trigger) {
    if (!modal || !bodyNode || !event) return;
    opener = trigger || document.activeElement;
    if (titleNode) titleNode.textContent = event.title || "Event details";
    bodyNode.innerHTML = buildEventDetailsHtml(event);
    modal.hidden = false;
    requestAnimationFrame(() => modal.querySelector("[data-echo-event-close]")?.focus({ preventScroll: true }));
  }

  function handleKeydown(event) {
    if (!isOpen()) return false;
    if (event.key === "Escape") {
      event.preventDefault();
      close();
      return true;
    }
    if (event.key !== "Tab") return false;

    const focusable = Array.from(modal.querySelectorAll(FOCUSABLE_SELECTOR));
    if (!focusable.length) {
      event.preventDefault();
      modal.focus({ preventScroll: true });
      return true;
    }
    const first = focusable[0];
    const last = focusable.at(-1);
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus({ preventScroll: true });
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus({ preventScroll: true });
    }
    return true;
  }

  modal?.addEventListener("click", (event) => {
    if (event.target.closest("[data-echo-event-close]")) close();
  });

  document.addEventListener("keydown", handleKeydown);

  return {
    close,
    handleKeydown,
    isOpen,
    open,
  };
}
