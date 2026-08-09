import { escapeHtml } from "../core/ui-primitives-module.js";

const DEFAULT_AVATAR = "data:image/svg+xml,%3Csvg%20xmlns='http://www.w3.org/2000/svg'%20viewBox='0%200%2096%2096'%3E%3Crect%20width='96'%20height='96'%20rx='24'%20fill='%23e5e7eb'/%3E%3Ccircle%20cx='48'%20cy='35'%20r='17'%20fill='%239ca3af'/%3E%3Cpath%20d='M20%2082c4-18%2017-28%2028-28s24%2010%2028%2028'%20fill='%239ca3af'/%3E%3C/svg%3E";
const MESSAGE_GROUP_WINDOW_MS = 7 * 60 * 1000;

export { escapeHtml };

export function avatarUrl(url, size = 64) {
  const candidate = url || DEFAULT_AVATAR;
  return typeof window.APSTUDY_AVATAR_URL_FOR_SIZE === "function"
    ? window.APSTUDY_AVATAR_URL_FOR_SIZE(candidate, size)
    : candidate;
}

export function avatarAttrs(url, size = 64, sizes = `${size}px`) {
  const resolved = avatarUrl(url, size);
  const src = escapeHtml(resolved);
  if (/^data:/i.test(resolved)) return `src="${src}" sizes="${escapeHtml(sizes)}" loading="lazy" decoding="async"`;
  const src2x = escapeHtml(avatarUrl(url, size * 2));
  return `src="${src}" srcset="${src} 1x, ${src2x} 2x" sizes="${escapeHtml(sizes)}" loading="lazy" decoding="async"`;
}

export function parseMessageDate(value) {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function localDateKey(date) {
  if (!date) return "";
  return [date.getFullYear(), String(date.getMonth() + 1).padStart(2, "0"), String(date.getDate()).padStart(2, "0")].join("-");
}

export function calendarDayDifference(date, now = new Date()) {
  if (!date) return 0;
  const start = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const target = new Date(date.getFullYear(), date.getMonth(), date.getDate());
  return Math.round((start - target) / 86400000);
}

export function formatMessageTimestamp(value, now = new Date()) {
  const date = parseMessageDate(value);
  if (!date) return "";
  const time = new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" }).format(date);
  const dayDifference = calendarDayDifference(date, now);
  if (dayDifference === 0) return time;
  if (dayDifference === 1) return `Yesterday at ${time}`;
  return new Intl.DateTimeFormat(undefined, { month: "numeric", day: "numeric", year: "2-digit", hour: "numeric", minute: "2-digit" }).format(date);
}

export function messageAuthorKey(message) {
  return String(message?.user_id || message?.author_username || message?.author_name || "");
}

export function shouldGroupMessage(previous, next) {
  const previousDate = parseMessageDate(previous?.created_at);
  const nextDate = parseMessageDate(next?.created_at);
  return Boolean(previousDate && nextDate
    && messageAuthorKey(previous) === messageAuthorKey(next)
    && localDateKey(previousDate) === localDateKey(nextDate)
    && nextDate - previousDate <= MESSAGE_GROUP_WINDOW_MS);
}

export function groupMessages(messages) {
  const groups = [];
  for (const message of messages || []) {
    const lastGroup = groups.at(-1);
    const previous = lastGroup?.messages.at(-1);
    if (previous && shouldGroupMessage(previous, message)) lastGroup.messages.push(message);
    else groups.push({ id: message.id, messages: [message] });
  }
  return groups;
}

export function plural(value, singular, pluralLabel) {
  const number = Number(value) || 0;
  return `${number} ${number === 1 ? singular : pluralLabel}`;
}
