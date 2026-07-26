import {
  DEFAULT_COLOR,
  addDays,
  dateKey,
  endOfDay,
  escapeHtml,
  eventKeyFor,
  formatEventTime,
  formatHourLabel,
  getQuarterHourMinutes,
  getQuarterHourKey,
  mixHex,
  parseEventDate,
  sameDay,
  startOfDay,
} from "./echo-utils.js";

export const HOUR_HEIGHT_PX = 36;
export const UPCOMING_DAYS = 7;

function resolveColor(event, sourcesById) {
  if (event.color) return event.color;
  const source = sourcesById.get(String(event.calendar_id || ""));
  return source?.color_hex || DEFAULT_COLOR;
}

function resolveCalendarLabel(event, sourcesById) {
  const source = sourcesById.get(String(event.calendar_id || ""));
  return source?.display_name || source?.default_name || event.calendar_id || "Calendar";
}

export function normalizeEvents(payload) {
  const sourcesById = new Map(
    (payload?.calendar_sources || []).map((source) => [String(source.id), source]),
  );

  return (payload?.events || [])
    .filter((event) => event?.start)
    .map((event, index) => {
      const isAllDay = Boolean(event.is_all_day);
      const startDate = parseEventDate(event.start, isAllDay);
      let endDate = event.end
        ? parseEventDate(event.end, isAllDay)
        : new Date(startDate);
      if (isAllDay && endDate <= startDate) endDate = addDays(startDate, 1);
      return {
        ...event,
        eventKey: eventKeyFor(event, index),
        calendarLabel: resolveCalendarLabel(event, sourcesById),
        isAllDay,
        startDate,
        endDate,
        color: resolveColor(event, sourcesById),
      };
    })
    .sort((a, b) => a.startDate - b.startDate || String(a.title || "").localeCompare(String(b.title || "")));
}

export function eventOverlapsDay(event, day) {
  const dayStart = startOfDay(day);
  const dayEnd = endOfDay(day);
  return event.startDate <= dayEnd && event.endDate > dayStart;
}

export function pickNextEvent(events, now = new Date()) {
  const today = startOfDay(now);
  const candidates = events.filter((event) => {
    if (event.isAllDay) {
      return event.endDate > today && event.startDate <= endOfDay(now);
    }
    return event.endDate > now;
  });
  return candidates[0] || null;
}

function calendarDayDifference(later, earlier) {
  const laterUtc = Date.UTC(later.getFullYear(), later.getMonth(), later.getDate());
  const earlierUtc = Date.UTC(earlier.getFullYear(), earlier.getMonth(), earlier.getDate());
  return Math.round((laterUtc - earlierUtc) / 86400000);
}

export function formatUpcomingDayLabel(day, reference = new Date()) {
  const offset = Math.max(0, calendarDayDifference(day, reference));
  const shortDate = day.toLocaleDateString(undefined, { month: "numeric", day: "numeric" });
  if (offset === 0) return `Today (${shortDate})`;
  if (offset === 1) return `Tomorrow (${shortDate})`;
  return `In ${offset} Days (${shortDate})`;
}

export function layoutTimedEvents(events) {
  const items = events.map((event, originalIndex) => {
    const startMin = event.startDate.getHours() * 60 + event.startDate.getMinutes();
    const endDate = event.endDate || event.startDate;
    const durationMin = Math.max(30, Math.ceil((endDate - event.startDate) / 60000));
    return {
      ...event,
      layoutOriginalIndex: originalIndex,
      layoutStartMinutes: startMin,
      layoutDurationMinutes: durationMin,
      layoutLane: 0,
      layoutLaneCount: 1,
    };
  }).sort((a, b) => (
    a.layoutStartMinutes - b.layoutStartMinutes
    || b.layoutDurationMinutes - a.layoutDurationMinutes
    || a.layoutOriginalIndex - b.layoutOriginalIndex
  ));

  function assignGroupLanes(group) {
    const laneEnds = [];
    for (const item of group) {
      let lane = laneEnds.findIndex((end) => end <= item.layoutStartMinutes);
      if (lane < 0) {
        lane = laneEnds.length;
        laneEnds.push(0);
      }
      laneEnds[lane] = item.layoutStartMinutes + item.layoutDurationMinutes;
      item.layoutLane = lane;
    }
    const laneCount = Math.max(1, laneEnds.length);
    group.forEach((item) => { item.layoutLaneCount = laneCount; });
  }

  let groupStart = 0;
  let groupEnd = -Infinity;
  for (let index = 0; index < items.length; index += 1) {
    const item = items[index];
    if (index > groupStart && item.layoutStartMinutes >= groupEnd) {
      assignGroupLanes(items.slice(groupStart, index));
      groupStart = index;
      groupEnd = -Infinity;
    }
    groupEnd = Math.max(groupEnd, item.layoutStartMinutes + item.layoutDurationMinutes);
  }
  if (items.length) assignGroupLanes(items.slice(groupStart));
  return items;
}

export function renderNextEvent(root, event, now = new Date()) {
  if (!root) return;
  if (!event) {
    root.innerHTML = `<p class="echo-empty">No upcoming events</p>`;
    root.setAttribute("aria-label", "Show upcoming events");
    return;
  }
  const dayLabel = formatUpcomingDayLabel(event.startDate, now);
  root.setAttribute("aria-label", `Show upcoming events. ${event.title || "Untitled"}, ${dayLabel}`);
  root.innerHTML = `
    <article class="echo-next-row">
      <span class="echo-next-color" style="background-color:${escapeHtml(event.color)}"></span>
      <p class="echo-next-day">${escapeHtml(dayLabel)}</p>
      <p class="echo-next-time">${escapeHtml(formatEventTime(event))}</p>
      <h3 class="echo-next-title">${escapeHtml(event.title || "Untitled")}</h3>
    </article>
  `;
}

function renderDayGroup(label, events, { isToday = false } = {}) {
  if (!events.length) return "";
  const chips = events.map((event) => {
    const accent = event.color || DEFAULT_COLOR;
    const background = mixHex(accent, "#1a1a1a", 0.28);
    const border = mixHex(accent, "#ffffff", 0.35);
    return `
      <article class="echo-chip" style="background:${escapeHtml(background)};color:#fff;border-color:${escapeHtml(border)}">
        <span class="echo-chip-bar" style="background:${escapeHtml(accent)}"></span>
        <div class="echo-chip-copy">
          <p class="echo-chip-title">${escapeHtml(event.title || "Untitled")}</p>
          <p class="echo-chip-time">${escapeHtml(formatEventTime(event))}</p>
        </div>
      </article>
    `;
  }).join("");
  return `
    <section class="echo-day">
      <h3 class="echo-day-label${isToday ? " is-today" : ""}">${escapeHtml(label)}</h3>
      ${chips}
    </section>
  `;
}

export function renderAgendaList(root, events, now = new Date()) {
  if (!root) return;
  const today = startOfDay(now);
  const days = Array.from({ length: UPCOMING_DAYS }, (_, index) => addDays(today, index));
  const sections = days.map((day, index) => renderDayGroup(
    formatUpcomingDayLabel(day, today),
    events.filter((event) => eventOverlapsDay(event, day)),
    { isToday: index === 0 },
  )).filter(Boolean);

  if (!sections.length) {
    root.innerHTML = `<p class="echo-empty">Nothing on the calendar for the next 7 days.</p>`;
    return;
  }

  root.innerHTML = sections.join("");
}

function eventAriaLabel(event) {
  return `${event.title || "Untitled"}, ${formatEventTime(event)}. View event details.`;
}

function renderTimedEvent(event) {
  const topPx = (event.layoutStartMinutes / 60) * HOUR_HEIGHT_PX;
  const heightPx = Math.max((event.layoutDurationMinutes / 60) * HOUR_HEIGHT_PX, 20);
  const leftPct = (event.layoutLane / event.layoutLaneCount) * 100;
  const widthPct = 100 / event.layoutLaneCount;
  const accent = event.color || DEFAULT_COLOR;
  const background = mixHex(accent, "#1a1a1a", 0.32);
  const border = mixHex(accent, "#ffffff", 0.35);
  const showTime = heightPx >= 36;
  return `
    <button type="button" class="echo-week-event" data-echo-event-key="${escapeHtml(event.eventKey)}"
      aria-haspopup="dialog"
      aria-label="${escapeHtml(eventAriaLabel(event))}"
      style="top:${topPx}px;left:${leftPct}%;width:calc(${widthPct}% - 0.15rem);height:${heightPx}px">
      <span class="echo-week-event-inner" style="background:${escapeHtml(background)};border-color:${escapeHtml(border)}">
        <span class="echo-week-event-title">${escapeHtml(event.title || "Untitled")}</span>
        ${showTime ? `<span class="echo-week-event-time">${escapeHtml(formatEventTime(event))}</span>` : ""}
      </span>
    </button>
  `;
}

function renderAllDayEvent(event) {
  const accent = event.color || DEFAULT_COLOR;
  const background = mixHex(accent, "#1a1a1a", 0.28);
  const border = mixHex(accent, "#ffffff", 0.35);
  return `
    <button type="button" class="echo-week-allday-chip" data-echo-event-key="${escapeHtml(event.eventKey)}"
      aria-haspopup="dialog"
      aria-label="${escapeHtml(eventAriaLabel(event))}"
      style="background:${escapeHtml(background)};border-color:${escapeHtml(border)}">
      ${escapeHtml(event.title || "Untitled")}
    </button>
  `;
}

export function renderTwoDayCalendar(root, events, { now = new Date(), onEventActivate } = {}) {
  if (!root) return;
  const today = startOfDay(now);
  const tomorrow = addDays(today, 1);
  const days = [today, tomorrow];

  const headers = days.map((day, index) => {
    const isCurrent = index === 0;
    const label = isCurrent
      ? `${day.toLocaleDateString(undefined, { weekday: "long" })} (Today)`
      : day.toLocaleDateString(undefined, { weekday: "long" });
    return `
      <div class="echo-week-dayhead${isCurrent ? " is-today" : ""}">
        <span class="echo-week-weekday">${escapeHtml(label)}</span>
      </div>
    `;
  }).join("");

  const allDayBlocks = days.map((day) => {
    const dayEvents = events.filter((event) => event.isAllDay && eventOverlapsDay(event, day));
    return dayEvents.length ? `<div>${dayEvents.map(renderAllDayEvent).join("")}</div>` : "<div></div>";
  }).join("");
  const hasAllDay = days.some((day) => events.some((event) => event.isAllDay && eventOverlapsDay(event, day)));

  const axisHours = Array.from({ length: 24 }, (_, hour) => `
    <div class="echo-week-hour">
      ${hour === 0 ? "" : `<span class="echo-week-hour-label">${formatHourLabel(hour)}</span>`}
    </div>
  `).join("");

  const columns = days.map((day, index) => {
    const timed = layoutTimedEvents(
      events.filter((event) => !event.isAllDay && eventOverlapsDay(event, day)),
    );
    const lines = Array.from({ length: 24 }, () => `<div class="echo-week-hour"></div>`).join("");
    return `
      <div class="echo-week-col${index === 0 ? " is-today" : ""}">
        <div class="echo-week-events" aria-hidden="true">${lines}</div>
        <div class="echo-week-events">${timed.map(renderTimedEvent).join("")}</div>
      </div>
    `;
  }).join("");

  root.innerHTML = `
    <div class="echo-week-shell">
      <div class="echo-week-header">
        <div class="echo-week-corner"></div>
        ${headers}
      </div>
      ${hasAllDay ? `
        <div class="echo-week-allday">
          <div class="echo-week-allday-label">All day</div>
          ${allDayBlocks}
        </div>
      ` : ""}
      <div class="echo-week-scroll" data-echo-week-scroll>
        <div class="echo-week-grid">
          <div class="echo-week-axis">${axisHours}</div>
          ${columns}
          <div class="echo-week-now-marker" data-echo-now-marker aria-hidden="true">
            <span class="echo-week-now-dot"></span>
            <span class="echo-week-now-line"></span>
          </div>
        </div>
      </div>
    </div>
  `;

  root.querySelectorAll("[data-echo-event-key]").forEach((eventNode) => {
    eventNode.addEventListener("click", () => {
      onEventActivate?.(eventNode.dataset.echoEventKey, eventNode);
    });
  });
  updateNowMarker(root, now);
}

export function getNowMarkerPosition(now = new Date()) {
  const quarterMinutes = now.getHours() * 60 + getQuarterHourMinutes(now);
  return {
    quarterHourKey: getQuarterHourKey(now),
    topPx: (quarterMinutes / 60) * HOUR_HEIGHT_PX,
  };
}

export function updateNowMarker(root, now = new Date()) {
  const marker = root?.querySelector("[data-echo-now-marker]");
  if (!marker) return null;
  const position = getNowMarkerPosition(now);
  marker.style.transform = `translateY(${position.topPx}px)`;
  marker.dataset.quarterHourKey = position.quarterHourKey;
  return position;
}

export function centerNowMarker(root, now = new Date(), { behavior = "auto" } = {}) {
  const scroller = root?.querySelector("[data-echo-week-scroll]");
  if (!scroller) return null;
  const position = getNowMarkerPosition(now);
  const target = Math.min(
    Math.max(0, scroller.scrollHeight - scroller.clientHeight),
    Math.max(0, position.topPx - (scroller.clientHeight / 2)),
  );
  try {
    scroller.scrollTo({ top: target, behavior });
  } catch {
    scroller.scrollTop = target;
  }
  if (scroller.scrollTop !== target && behavior === "auto") scroller.scrollTop = target;
  return target;
}

export function calendarDayKey(now = new Date()) {
  return dateKey(now);
}

export { addDays, endOfDay, sameDay, startOfDay };
