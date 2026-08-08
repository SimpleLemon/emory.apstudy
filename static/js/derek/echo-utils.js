import { escapeHtml } from "../core/ui-primitives-module.js";

export const DEFAULT_COLOR = "#6366f1";

export { escapeHtml };

function parseHex(hex) {
  const clean = String(hex || "").replace("#", "");
  if (clean.length !== 6) return null;
  return {
    r: parseInt(clean.slice(0, 2), 16),
    g: parseInt(clean.slice(2, 4), 16),
    b: parseInt(clean.slice(4, 6), 16),
  };
}

export function mixHex(accent, surface, amount) {
  const a = parseHex(accent);
  const s = parseHex(surface);
  if (!a || !s) return accent || DEFAULT_COLOR;
  const mix = (x, y) => Math.round(x + (y - x) * amount);
  const toHex = (n) => n.toString(16).padStart(2, "0");
  return `#${toHex(mix(s.r, a.r))}${toHex(mix(s.g, a.g))}${toHex(mix(s.b, a.b))}`;
}

export function startOfDay(date) {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate());
}

export function endOfDay(date) {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate(), 23, 59, 59, 999);
}

export function addDays(date, days) {
  const next = new Date(date);
  next.setDate(next.getDate() + days);
  return next;
}

export function sameDay(a, b) {
  return a.getFullYear() === b.getFullYear()
    && a.getMonth() === b.getMonth()
    && a.getDate() === b.getDate();
}

export function dateKey(date) {
  return `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}`;
}

export function getQuarterHourMinutes(date) {
  return Math.floor(date.getMinutes() / 15) * 15;
}

export function getQuarterHourKey(date) {
  return `${dateKey(date)}-${date.getHours()}-${getQuarterHourMinutes(date)}`;
}

export function parseEventDate(dateStr, isAllDay) {
  if (!dateStr) return new Date();
  if (isAllDay && /^\d{4}-\d{2}-\d{2}$/.test(dateStr)) {
    const [year, month, day] = dateStr.split("-").map((part) => parseInt(part, 10));
    return new Date(year, month - 1, day, 0, 0, 0, 0);
  }
  return new Date(dateStr);
}

export function formatTimeOnly(date) {
  return date.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}

export function formatHourLabel(hour) {
  if (hour === 0) return "12a";
  if (hour < 12) return `${hour}a`;
  if (hour === 12) return "12p";
  return `${hour - 12}p`;
}

export function formatLongDate(date, options = {}) {
  return date.toLocaleDateString(undefined, {
    weekday: "long",
    month: "long",
    day: "numeric",
    ...options,
  });
}

export function formatEventTime(event) {
  if (event.isAllDay) return "All day";
  const start = event.startDate;
  const end = event.endDate || event.startDate;
  return `${formatTimeOnly(start)} – ${formatTimeOnly(end)}`;
}

export function eventKeyFor(event, index = 0) {
  const key = event?.event_ref || event?.id || event?.uid;
  return key ? String(key) : `event-${index}`;
}
