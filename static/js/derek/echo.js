import {
  calendarDayKey,
  centerNowMarker,
  mergeEventLists,
  normalizeEvents,
  pickNextEvent,
  renderAgendaList,
  renderNextEvent,
  renderTwoDayCalendar,
  UPCOMING_DAYS,
  updateNowMarker,
} from "./echo-calendar.js";
import { createEchoClock, isReducedMotion } from "./echo-clock.js";
import { createEchoEventDetails } from "./echo-event-details.js";
import {
  fetchSimulatedCourses,
  simulatedCoursesPayload,
} from "./echo-courses.js";
import {
  addDays,
  endOfDay,
  escapeHtml,
  getQuarterHourKey,
  startOfDay,
} from "./echo-utils.js";

const PLAYLIST_ID = "PLRuGynt4aVsqpyBgcw7Yorrque_ZVmz4x";
const VIDEO_ID = "BybOGhyJO5M";

let cachedEvents = [];
let calendarLoaded = false;
let loadedCalendarDayKey = null;
let lastQuarterHourKey = null;
let echoClock = null;
let eventDetails = null;
let ytPlayer = null;
let ytApiPromise = null;
let playlistIds = [];
let playlistTitles = {};
let activePlaylistIndex = 0;

function $(selector) {
  return document.querySelector(selector);
}

function centerCalendar(now, behavior = "auto") {
  const root = $("[data-echo-calendar]");
  if (!root) return;
  const nextBehavior = isReducedMotion() ? "auto" : behavior;
  const center = () => centerNowMarker(root, now, { behavior: nextBehavior });
  if (typeof window.requestAnimationFrame === "function") window.requestAnimationFrame(center);
  else center();
}

function openEventDetails(eventKey, trigger) {
  const event = cachedEvents.find((candidate) => candidate.eventKey === eventKey);
  if (event) eventDetails?.open(event, trigger);
}

async function loadSimulatedEvents(start, end) {
  try {
    const courses = await fetchSimulatedCourses();
    return normalizeEvents(simulatedCoursesPayload(courses, { start, end }));
  } catch (error) {
    console.error("Failed to load simulated courses:", error);
    return [];
  }
}

async function loadCalendar({ center = true } = {}) {
  const now = new Date();
  const start = startOfDay(now);
  const end = endOfDay(addDays(start, UPCOMING_DAYS - 1));
  const url = `/api/calendar/events?start=${encodeURIComponent(start.toISOString())}&end=${encodeURIComponent(end.toISOString())}`;

  try {
    const [baseEvents, simulatedEvents] = await Promise.all([
      (async () => {
        const response = await fetch(url, {
          credentials: "same-origin",
          headers: { Accept: "application/json" },
        });
        if (!response.ok) throw new Error(`Calendar request failed (${response.status})`);
        return normalizeEvents(await response.json());
      })(),
      loadSimulatedEvents(start, end),
    ]);
    cachedEvents = mergeEventLists(baseEvents, simulatedEvents);
    loadedCalendarDayKey = calendarDayKey(now);
    lastQuarterHourKey = getQuarterHourKey(now);
    calendarLoaded = true;

    renderNextEvent($("[data-echo-next]"), pickNextEvent(cachedEvents, now), now);
    renderTwoDayCalendar($("[data-echo-calendar]"), cachedEvents, {
      now,
      onEventActivate: openEventDetails,
    });
    renderAgendaList($("[data-echo-agenda]"), cachedEvents, now);
    if (center) centerCalendar(now);
  } catch (error) {
    console.error(error);
    calendarLoaded = true;
    loadedCalendarDayKey = calendarDayKey(now);
    lastQuarterHourKey = getQuarterHourKey(now);
    const next = $("[data-echo-next]");
    const calendar = $("[data-echo-calendar]");
    const agenda = $("[data-echo-agenda]");
    if (next) next.innerHTML = `<p class="echo-empty">Couldn’t load next event</p>`;
    if (calendar) calendar.innerHTML = `<p class="echo-empty">Couldn’t load calendar</p>`;
    if (agenda) agenda.innerHTML = `<p class="echo-empty">Couldn’t load calendar</p>`;
  }
}

function handleClockTick(now, previousNow, { quarterHourKey } = {}) {
  if (!calendarLoaded) return;
  const root = $("[data-echo-calendar]");
  updateNowMarker(root, now);

  const currentDayKey = calendarDayKey(now);
  if (currentDayKey !== loadedCalendarDayKey) {
    calendarLoaded = false;
    void loadCalendar({ center: true });
    return;
  }

  if (previousNow && quarterHourKey !== lastQuarterHourKey) {
    lastQuarterHourKey = quarterHourKey;
    centerCalendar(now, "smooth");
  }
}

function loadYouTubeApi() {
  if (window.YT?.Player) return Promise.resolve(window.YT);
  if (ytApiPromise) return ytApiPromise;

  ytApiPromise = new Promise((resolve, reject) => {
    const previous = window.onYouTubeIframeAPIReady;
    window.onYouTubeIframeAPIReady = () => {
      if (typeof previous === "function") previous();
      resolve(window.YT);
    };
    const existing = document.querySelector("script[data-echo-youtube-api]");
    if (existing) return;
    const script = document.createElement("script");
    script.src = "https://www.youtube.com/iframe_api";
    script.async = true;
    script.dataset.echoYoutubeApi = "true";
    script.onerror = () => reject(new Error("Failed to load YouTube API"));
    document.head.appendChild(script);
  });

  return ytApiPromise;
}

async function fetchVideoTitle(videoId) {
  if (playlistTitles[videoId]) return playlistTitles[videoId];
  try {
    const response = await fetch(
      `https://www.youtube.com/oembed?url=${encodeURIComponent(`https://www.youtube.com/watch?v=${videoId}`)}&format=json`,
    );
    if (!response.ok) throw new Error("oEmbed failed");
    const payload = await response.json();
    playlistTitles[videoId] = payload.title || `Video ${videoId}`;
  } catch {
    playlistTitles[videoId] = `Video ${videoId}`;
  }
  return playlistTitles[videoId];
}

function renderPlaylistList() {
  const root = $("[data-echo-playlist-list]");
  if (!root) return;
  if (!playlistIds.length) {
    root.innerHTML = `<p class="echo-empty">Playlist is still loading…</p>`;
    return;
  }

  root.innerHTML = playlistIds.map((videoId, index) => {
    const title = playlistTitles[videoId] || `Track ${index + 1}`;
    const active = index === activePlaylistIndex ? " is-active" : "";
    return `
      <button type="button" class="echo-playlist-item${active}" data-echo-playlist-index="${index}">
        <span class="echo-playlist-index">${index + 1}</span>
        <p class="echo-playlist-title">${escapeHtml(title)}</p>
      </button>
    `;
  }).join("");
}

async function refreshPlaylistMetadata(ids) {
  playlistIds = ids.slice();
  renderPlaylistList();
  await Promise.all(playlistIds.map((id) => fetchVideoTitle(id)));
  renderPlaylistList();
}

function ensureYouTubePlayer() {
  return loadYouTubeApi().then((YT) => {
    if (ytPlayer) return ytPlayer;

    return new Promise((resolve) => {
      ytPlayer = new YT.Player("echo-yt-player", {
        width: "100%",
        height: "100%",
        videoId: VIDEO_ID,
        playerVars: {
          listType: "playlist",
          list: PLAYLIST_ID,
          autoplay: 1,
          playsinline: 1,
          rel: 0,
          modestbranding: 1,
          controls: 1,
          fs: 1,
        },
        events: {
          onReady: (event) => {
            const loadIds = (attempt = 0) => {
              const ids = event.target.getPlaylist?.() || [];
              if (ids.length) {
                refreshPlaylistMetadata(ids);
                return;
              }
              if (attempt < 6) {
                window.setTimeout(() => loadIds(attempt + 1), 400);
                return;
              }
              refreshPlaylistMetadata([VIDEO_ID]);
            };
            loadIds();
            try {
              event.target.setPlaybackQuality?.("hd720");
            } catch {
              /* best-effort */
            }
            resolve(ytPlayer);
          },
          onStateChange: (event) => {
            const index = event.target.getPlaylistIndex?.();
            if (typeof index === "number" && index >= 0) {
              activePlaylistIndex = index;
              renderPlaylistList();
            }
          },
        },
      });
    });
  });
}

function openMusic() {
  const dash = $("[data-echo-dash]");
  const frame = $("[data-echo-music-frame]");
  const playlistBtn = $("[data-echo-playlist-open]");
  if (!dash || !frame) return;

  frame.hidden = false;
  playlistBtn?.removeAttribute("hidden");
  dash.dataset.mode = "music";

  const clock = $("[data-echo-clock]");
  if (clock) {
    clock.setAttribute("role", "button");
    clock.setAttribute("tabindex", "0");
    clock.setAttribute("aria-label", "Return to home view");
  }

  echoClock?.fitDate();
  ensureYouTubePlayer().catch((error) => {
    console.error(error);
    frame.innerHTML = `<p class="echo-empty">Couldn’t load YouTube player</p>`;
  });
}

function openPlaylistModal() {
  const modal = $("[data-echo-playlist-modal]");
  if (!modal) return;
  renderPlaylistList();
  modal.hidden = false;

  if (ytPlayer?.getPlaylist) {
    const ids = ytPlayer.getPlaylist() || [];
    if (ids.length) refreshPlaylistMetadata(ids);
    const index = ytPlayer.getPlaylistIndex?.();
    if (typeof index === "number" && index >= 0) activePlaylistIndex = index;
  }
}

function closePlaylistModal() {
  const modal = $("[data-echo-playlist-modal]");
  if (modal) modal.hidden = true;
}

function playPlaylistIndex(index) {
  if (!ytPlayer?.playVideoAt) return;
  activePlaylistIndex = index;
  ytPlayer.playVideoAt(index);
  renderPlaylistList();
  closePlaylistModal();
}

function openAgendaModal() {
  const modal = $("[data-echo-agenda-modal]");
  if (!modal) return;
  renderAgendaList($("[data-echo-agenda]"), cachedEvents);
  modal.hidden = false;
}

function closeAgendaModal() {
  const modal = $("[data-echo-agenda-modal]");
  if (modal) modal.hidden = true;
}

function bindInteractions() {
  eventDetails = createEchoEventDetails({
    modal: $("[data-echo-event-modal]"),
    titleNode: $("[data-echo-event-title]"),
    bodyNode: $("[data-echo-event-details]"),
  });

  $("[data-echo-music-open]")?.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    openMusic();
  });

  $("[data-echo-playlist-open]")?.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    openPlaylistModal();
  });

  $("[data-echo-playlist-list]")?.addEventListener("click", (event) => {
    const button = event.target.closest("[data-echo-playlist-index]");
    if (!button) return;
    event.preventDefault();
    playPlaylistIndex(Number(button.dataset.echoPlaylistIndex));
  });

  $("[data-echo-agenda-open]")?.addEventListener("click", (event) => {
    event.preventDefault();
    openAgendaModal();
  });

  document.querySelectorAll("[data-echo-agenda-close]").forEach((node) => {
    node.addEventListener("click", (event) => {
      event.preventDefault();
      closeAgendaModal();
    });
  });

  document.querySelectorAll("[data-echo-playlist-close]").forEach((node) => {
    node.addEventListener("click", (event) => {
      event.preventDefault();
      closePlaylistModal();
    });
  });

  document.addEventListener("keydown", (event) => {
    if (event.defaultPrevented) return;
    if (event.key !== "Escape") return;
    closeAgendaModal();
    closePlaylistModal();
  });

  const clock = $("[data-echo-clock]");
  const reloadHome = () => {
    const dash = $("[data-echo-dash]");
    if (dash?.dataset.mode !== "music") return;
    window.location.reload();
  };
  clock?.addEventListener("click", reloadHome);
  clock?.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      reloadHome();
    }
  });
}

function startEcho() {
  const clockRoot = $("[data-echo-clock]");
  echoClock = createEchoClock({
    root: clockRoot,
    onTick: handleClockTick,
  });
  bindInteractions();
  echoClock.start();
  void loadCalendar();
}

startEcho();
