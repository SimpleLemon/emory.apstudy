import {
  dateKey,
  formatLongDate,
  getQuarterHourKey,
} from "./echo-utils.js";

export const CLOCK_DIGIT_KEYS = ["h1", "h2", "m1", "m2"];

export function formatEchoDigits(date) {
  let hours = date.getHours() % 12;
  if (hours === 0) hours = 12;
  const hourStr = String(hours).padStart(2, "0");
  const minuteStr = String(date.getMinutes()).padStart(2, "0");
  return {
    h1: hourStr[0],
    h2: hourStr[1],
    m1: minuteStr[0],
    m2: minuteStr[1],
  };
}

export function formatEchoAccessibleTime(date) {
  return date.toLocaleTimeString(undefined, {
    hour: "numeric",
    minute: "2-digit",
  });
}

export function isReducedMotion() {
  return Boolean(window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches);
}

export function fitEchoDateText(dateNode, root = dateNode?.closest("[data-echo-clock]")) {
  if (!dateNode || !root || !root.clientWidth) return 0;

  dateNode.style.fontSize = "";
  const width = root.clientWidth;
  const height = root.clientHeight || width;
  const safeWidth = Math.max(0, width - 10);
  let size = Math.min(96, Math.max(18, width * 0.16, height * 0.22));
  size = Math.min(size, Math.max(18, height * 0.25));
  dateNode.style.fontSize = `${size}px`;

  while (size > 18 && dateNode.scrollWidth > safeWidth) {
    size -= 1;
    dateNode.style.fontSize = `${size}px`;
  }
  return size;
}

function setSlotValue(slot, value) {
  slot.querySelector("[data-echo-flip-under]").textContent = value;
  slot.querySelector("[data-echo-flip-top]").textContent = value;
  slot.querySelector("[data-echo-flip-bottom]").textContent = value;
  slot.dataset.echoCurrent = value;
}

function finishFlip(slot, value) {
  if (!slot.classList.contains("is-flipping")) return;
  setSlotValue(slot, value);
  slot.classList.remove("is-flipping");
}

function updateSlot(slot, value, animate) {
  const current = slot.dataset.echoCurrent;
  if (current === value) return;
  if (!current || !animate || isReducedMotion()) {
    slot.classList.remove("is-flipping");
    setSlotValue(slot, value);
    return;
  }

  const under = slot.querySelector("[data-echo-flip-under]");
  const top = slot.querySelector("[data-echo-flip-top]");
  const bottom = slot.querySelector("[data-echo-flip-bottom]");
  under.textContent = value;
  top.textContent = current;
  bottom.textContent = value;

  slot.classList.remove("is-flipping");
  void slot.offsetWidth;
  slot.classList.add("is-flipping");

  let finished = false;
  const finish = () => {
    if (finished) return;
    finished = true;
    finishFlip(slot, value);
  };
  top.addEventListener("animationend", finish, { once: true });
  window.setTimeout(finish, 520);
}

export function createEchoClock({
  root,
  dateNode = root?.querySelector("[data-echo-date]"),
  getNow = () => new Date(),
  onTick,
} = {}) {
  const labelNode = root?.querySelector("[data-echo-clock-label]");
  const slots = new Map(
    Array.from(root?.querySelectorAll("[data-echo-digit-slot]") || [])
      .map((slot) => [slot.dataset.echoDigitSlot, slot]),
  );
  let previousNow = null;
  let timeoutId = null;
  let resizeObserver = null;
  let resizeHandler = null;

  function update(now = getNow(), { animate = true } = {}) {
    const digits = formatEchoDigits(now);
    const shouldAnimate = Boolean(previousNow) && animate;
    CLOCK_DIGIT_KEYS.forEach((key) => {
      const slot = slots.get(key);
      if (slot) updateSlot(slot, digits[key], shouldAnimate);
    });

    const accessibleTime = formatEchoAccessibleTime(now);
    const dateText = formatLongDate(now);
    if (dateNode) {
      dateNode.textContent = dateText;
      fitEchoDateText(dateNode, root);
    }
    if (labelNode) labelNode.textContent = `${accessibleTime}, ${dateText}`;
    root?.setAttribute("aria-label", `Clock: ${accessibleTime}`);

    const prior = previousNow;
    previousNow = now;
    onTick?.(now, prior, {
      dayKey: dateKey(now),
      quarterHourKey: getQuarterHourKey(now),
    });
  }

  function schedule() {
    const delay = 60000 - (Date.now() % 60000) + 50;
    timeoutId = window.setTimeout(() => {
      update(getNow(), { animate: true });
      schedule();
    }, delay);
  }

  function start() {
    update(getNow(), { animate: false });
    schedule();
  }

  function stop() {
    if (timeoutId) window.clearTimeout(timeoutId);
    timeoutId = null;
    resizeObserver?.disconnect();
    if (resizeHandler) window.removeEventListener("resize", resizeHandler);
  }

  function fitDate() {
    return fitEchoDateText(dateNode, root);
  }

  if (root && typeof window.ResizeObserver === "function") {
    resizeObserver = new window.ResizeObserver(fitDate);
    resizeObserver.observe(root);
  } else if (root) {
    resizeHandler = fitDate;
    window.addEventListener("resize", resizeHandler, { passive: true });
  }

  return {
    fitDate,
    start,
    stop,
    update,
  };
}
