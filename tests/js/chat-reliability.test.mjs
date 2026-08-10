/* global Buffer, URL */

import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const dataUrl = (source) => `data:text/javascript;base64,${Buffer.from(source).toString("base64")}`;
const escapeHtmlSource = `
  const entities = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
  export const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (character) => entities[character]);
`;

const roomsSource = await readFile(new URL("../../static/js/chat/rooms.js", import.meta.url), "utf8");
const composerSource = await readFile(new URL("../../static/js/chat/composer.js", import.meta.url), "utf8");
const messagesDomSource = await readFile(new URL("../../static/js/chat/messages-dom.js", import.meta.url), "utf8");
const realtimeSource = await readFile(new URL("../../static/js/chat/realtime.js", import.meta.url), "utf8");

const presentationBridge = dataUrl(`
  ${escapeHtmlSource}
  export const avatarAttrs = () => "";
  export const formatMessageTimestamp = () => "formatted-time";
  export const groupMessages = (messages) => (messages || []).map((message) => ({ id: message.id, messages: [message] }));
  export const localDateKey = () => "2026-08-09";
  export const parseMessageDate = (value) => new Date(value);
  export const shouldGroupMessage = () => false;
`);
const cacheBridge = dataUrl(`
  export const deltaLoadParams = (cache) => cache?.latestCursor ? { after: cache.latestCursor } : {};
  export const mergeMessages = (existing, incoming) => [...(existing || []), ...(incoming || [])];
  export const updateCacheCursors = () => {};
`);

const { createChatRooms, createRoomSelectionCoordinator } = await import(
  dataUrl(roomsSource.replace("./presentation.js", presentationBridge))
);
const { createChatComposer } = await import(
  dataUrl(composerSource.replace("./presentation.js", presentationBridge))
);
const { dedupeIncomingMessages, messageBodyMarkup, messageTimestampMarkup } = await import(
  dataUrl(messagesDomSource
    .replace("./cache.js", cacheBridge)
    .replace("./presentation.js", presentationBridge))
);
const { createChatRealtime } = await import(
  dataUrl(realtimeSource.replace("./cache.js", cacheBridge))
);

test("room selection tokens accept the current load and reject replaced loads", () => {
  const firstRoom = { type: "channel", id: "first" };
  const secondRoom = { type: "channel", id: "second" };
  let activeRoom = firstRoom;
  const coordinator = createRoomSelectionCoordinator();
  const first = coordinator.begin(firstRoom);

  assert.equal(first.isCurrent(activeRoom), true);

  const second = coordinator.begin(secondRoom);
  activeRoom = secondRoom;
  assert.equal(first.signal.aborted, true);
  assert.equal(first.isCurrent(activeRoom), false);
  assert.equal(second.isCurrent(activeRoom), true);
});

test("room switching replaces the old pane before the new room load settles", async () => {
  const previousWindow = globalThis.window;
  const previousDocument = globalThis.document;
  let loaderCalls = 0;
  let loadOptions = null;
  let resolveLoad;
  const oldRoom = { type: "channel", id: "old" };
  const newRoom = { type: "channel", id: "new" };
  const makeElement = () => ({
    addEventListener: () => {},
    appendChild: () => {},
    classList: { add: () => {}, remove: () => {} },
    dataset: {},
    setAttribute: () => {},
  });
  globalThis.window = { innerWidth: 1280 };
  globalThis.document = {
    createElement: makeElement,
    getElementById: () => null,
    querySelector: () => null,
    visibilityState: "visible",
  };
  const els = {
    channelList: makeElement(),
    dmList: makeElement(),
    roomSymbol: { classList: { add: () => {}, remove: () => {} }, textContent: "#", innerHTML: "" },
    roomName: { textContent: "" },
    roomMeta: { textContent: "" },
  };
  const cache = { messages: [], loaded: false, stale: false, latestCursor: null };
  const state = {
    activeRoom: oldRoom,
    activeProfile: null,
    channels: [{ id: "new", label: "New room", approved: true, read_only: false }],
    threads: [],
    roomUnread: new Map(),
    clearedReadRooms: new Set(),
  };
  const actions = {
    cacheFor: () => cache,
    channelIsWritable: () => true,
    closeInlineProfilePopover: () => {},
    deltaLoadParams: () => ({}),
    focusComposerSoon: () => {},
    handleActiveRoomPresenceChange: () => {},
    hydrateRoomFromPersistentCache: async () => {},
    latestMessageForRead: () => null,
    loadMessages: (options) => {
      loadOptions = options;
      return new Promise((resolve) => { resolveLoad = resolve; });
    },
    markRoomRead: () => {},
    renderCachedRoom: () => false,
    renderDmProfile: () => {},
    renderMembers: () => {},
    renderMessageLoader: () => { loaderCalls += 1; },
    renderPresenceDrivenUi: () => {},
    roomKey: (room) => `${room.type}:${room.id}`,
    saveActiveScroll: () => {},
    schedulePersistentBootstrapSave: () => {},
    setComposer: () => {},
    setHistoryBanner: () => {},
    setStatus: () => {},
  };
  const rooms = createChatRooms({
    root: { dataset: {} },
    state,
    els,
    extensions: {},
    config: { ANNOUNCEMENTS_CHANNEL_ID: "announcements", GRAMMARLY_DISABLED_ATTRS: "" },
    actions,
    fetchJson: async () => ({}),
  });

  try {
    const selection = rooms.selectRoom(newRoom);
    for (let index = 0; index < 4 && !loadOptions; index += 1) await Promise.resolve();
    assert.equal(state.activeRoom, newRoom);
    assert.equal(loaderCalls, 1);
    assert.ok(loadOptions?.roomSelection);
    assert.equal(loadOptions.roomSelection.isCurrent(state.activeRoom), true);
    resolveLoad([]);
    await selection;
  } finally {
    globalThis.window = previousWindow;
    globalThis.document = previousDocument;
  }
});

test("composer clears its in-flight state when optimistic rendering throws", async () => {
  const state = {
    activeRoom: { type: "channel", id: "general" },
    messageSendInFlight: false,
    user: { id: "user-1", name: "Test User" },
    failedMessages: new Map(),
  };
  const els = {
    composer: { hidden: false },
    input: { value: "quoted <json>: {\"key\": \"value\"}", style: {}, scrollHeight: 24 },
    sendButton: { disabled: false },
  };
  const channel = { id: "general", approved: true, read_only: false };
  const cache = { messages: [] };
  const statuses = [];
  const actions = {
    activeChannel: () => channel,
    activeThread: () => null,
    applyIncomingMessages: () => { throw new Error("optimistic render failed"); },
    cacheFor: () => cache,
    channelIsWritable: () => true,
    clearTypingPresence: () => {},
    currentRoomUrl: () => "/messages",
    isNearBottom: () => true,
    removeMessageFromCaches: () => {},
    schedulePersistentBootstrapSave: () => {},
    schedulePersistentRoomSave: () => {},
    setStatus: (message) => statuses.push(message),
    roomKey: (room) => `${room.type}:${room.id}`,
  };
  const extensions = {
    attachments: { readyIds: () => [], isBusy: () => false, clear: () => {} },
    mediaPicker: { selection: () => ({}), hasSelection: () => false, clear: () => {} },
  };
  const composer = createChatComposer({ state, els, extensions, actions, fetchJson: async () => ({}) });

  await composer.sendActiveMessage({ preventDefault() {} });

  assert.equal(state.messageSendInFlight, false);
  assert.equal(els.sendButton.disabled, false);
  assert.deepEqual(statuses, ["optimistic render failed"]);
});

test("message body text escapes quotes, JSON-like punctuation, and HTML-like text", () => {
  const content = `He said "<script>alert('x')</script>" & {"safe": true}`;
  assert.equal(
    messageBodyMarkup({ content }),
    "He said &quot;&lt;script&gt;alert(&#39;x&#39;)&lt;/script&gt;&quot; &amp; {&quot;safe&quot;: true}",
  );
});

test("continuation timestamps use the shared message timestamp formatter", () => {
  assert.equal(messageTimestampMarkup("2026-08-09T12:00:00Z"), "formatted-time");
});

test("delta insertion deduplicates repeated message IDs without replacing existing nodes", () => {
  const incoming = dedupeIncomingMessages(
    [{ id: "existing", content: "old" }],
    [
      { id: "new", content: "one" },
      { id: "new", content: "duplicate" },
      { id: "existing", content: "updated" },
    ],
  );
  assert.deepEqual(incoming.map((message) => message.id), ["new"]);
});

test("old-room incoming messages update their cache without touching the active pane", async () => {
  const oldRoom = { type: "channel", id: "old" };
  const activeRoom = { type: "channel", id: "active" };
  const oldCache = { messages: [], loaded: false, stale: false };
  let paneWrites = 0;
  const pane = {
    get scrollHeight() { return 0; },
    get scrollTop() { return 0; },
    set innerHTML(value) {
      void value;
      paneWrites += 1;
    },
    querySelector: () => null,
  };
  const dom = (await import(
    dataUrl(messagesDomSource
      .replace("./cache.js", cacheBridge)
      .replace("./presentation.js", presentationBridge)))
  ).createChatMessagesDom({
    root: { dataset: {} },
    state: { activeRoom },
    els: { messages: pane, newMessages: null },
    extensions: {},
    config: { ANNOUNCEMENTS_CHANNEL_ID: "announcements", GRAMMARLY_DISABLED_ATTRS: "" },
    actions: {
      cacheFor: () => oldCache,
      isNearBottom: () => true,
      markRoomRead: () => {},
      roomKey: (room) => `${room.type}:${room.id}`,
      schedulePersistentRoomSave: () => {},
      updateCacheCursors: () => {},
      clearRoomUnread: () => {},
      latestMessageForRead: () => null,
      activeChannel: () => null,
      scheduleTransientFrame: () => {},
      setStatus: () => {},
    },
  });

  dom.applyIncomingMessages(oldRoom, [{ id: "old-message", content: "old" }]);

  assert.equal(oldCache.messages.length, 1);
  assert.equal(paneWrites, 0);
});

test("healthy realtime avoids active-room polling and recovery leaves one fallback loop", async () => {
  const previousWindow = globalThis.window;
  const previousDocument = globalThis.document;
  const previousEventSource = globalThis.EventSource;
  const sources = [];
  const intervals = new Set();
  const timeouts = [];
  let pollCalls = 0;
  let fetchedMessageCalls = 0;
  let appliedMessages = 0;
  class FakeEventSource {
    constructor(url) {
      this.url = url;
      this.closed = false;
      sources.push(this);
    }

    close() {
      this.closed = true;
    }
  }
  globalThis.window = {
    EventSource: FakeEventSource,
    setInterval(callback) {
      intervals.add(callback);
      return callback;
    },
    clearInterval(callback) {
      intervals.delete(callback);
    },
    setTimeout(callback) {
      timeouts.push(callback);
      return callback;
    },
    clearTimeout(callback) {
      const index = timeouts.indexOf(callback);
      if (index >= 0) timeouts.splice(index, 1);
    },
  };
  globalThis.EventSource = FakeEventSource;
  globalThis.document = { visibilityState: "visible" };
  const state = {
    activeRoom: { type: "channel", id: "general" },
    channels: [{ id: "general" }],
    threads: [],
    university: null,
    realtimeReady: false,
    realtimeConnecting: false,
    realtimeUnsubscribe: null,
  };
  const actions = {
    applyIncomingMessages: () => { appliedMessages += 1; },
    cacheFor: () => ({ messages: [] }),
    loadMessages: async () => { pollCalls += 1; return []; },
    loadInitialPresences: async () => {},
    mergeMessages: (existing, incoming) => [...existing, ...incoming],
    playChatSound: () => {},
    refreshChatSummary: async () => {},
    roomKey: (room) => `${room.type}:${room.id}`,
  };
  const realtime = createChatRealtime({
    state,
    actions,
    config: { REALTIME_FALLBACK_MS: 3_000, REALTIME_HEARTBEAT_MS: 8_000, REALTIME_RECONNECT_MS: 1_500 },
    lifecycle: { paused: false, disposed: false },
    fetchJson: async (url) => {
      if (url.startsWith("/api/chat/messages/")) {
        fetchedMessageCalls += 1;
        return { message: { id: "message-1", created_at: "2026-08-09T12:00:00Z" } };
      }
      return {};
    },
  });

  try {
    await realtime.startRealtimeServices();
    const firstSource = sources[0];
    firstSource.onopen();
    assert.equal(pollCalls, 0);
    assert.equal(intervals.size, 0);

    firstSource.onerror();
    assert.equal(intervals.size, 1);
    assert.equal(timeouts.length, 1);
    timeouts.shift()();
    const recoveredSource = sources[1];
    recoveredSource.onopen();
    assert.equal(intervals.size, 0);
    assert.equal(pollCalls, 0);

    const event = {
      payload: {
        $id: "event-1",
        event_type: "message_created",
        scope_type: "channel",
        scope_id: "general",
        message_id: "message-1",
      },
    };
    await realtime.handleRealtimePayload(event);
    await realtime.handleRealtimePayload({
      payload: { ...event.payload, $id: "event-2" },
    });
    await realtime.handleRealtimePayload(event);
    assert.equal(fetchedMessageCalls, 1);
    assert.equal(appliedMessages, 1);
  } finally {
    realtime.resetRealtimeConnection();
    realtime.stopRealtimeFallback();
    globalThis.window = previousWindow;
    globalThis.document = previousDocument;
    globalThis.EventSource = previousEventSource;
  }
});

test("realtime thread discovery cannot apply an old-room event after the user switches rooms", async () => {
  let resolveThread;
  let appliedMessages = 0;
  const staleRooms = [];
  const threadPromise = new Promise((resolve) => { resolveThread = resolve; });
  const state = {
    activeRoom: { type: "thread", id: "thread-old" },
    channels: [],
    threads: [],
    university: null,
  };
  const actions = {
    applyIncomingMessages: () => { appliedMessages += 1; },
    bootstrap: async () => {},
    cacheFor: () => ({ messages: [] }),
    fetchThread: () => threadPromise,
    loadMessages: async () => [],
    markRoomStale: (room) => staleRooms.push(`${room.type}:${room.id}`),
    playChatSound: () => {},
    refreshChatSummary: async () => {},
    roomKey: (room) => `${room.type}:${room.id}`,
    scheduleUnreadSummaryRefresh: () => {},
    threadExists: () => false,
  };
  const realtime = createChatRealtime({
    state,
    actions,
    config: { REALTIME_FALLBACK_MS: 3_000, REALTIME_HEARTBEAT_MS: 8_000, REALTIME_RECONNECT_MS: 1_500 },
    lifecycle: { paused: false, disposed: false },
    fetchJson: async () => ({ message: { id: "message-old" } }),
  });

  const pending = realtime.handleRealtimePayload({
    payload: {
      $id: "event-old",
      event_type: "message_created",
      scope_type: "thread",
      scope_id: "thread-old",
      thread_id: "thread-old",
      message_id: "message-old",
    },
  });
  state.activeRoom = { type: "thread", id: "thread-new" };
  resolveThread({ id: "thread-old" });
  await pending;

  assert.equal(appliedMessages, 0);
  assert.deepEqual(staleRooms, ["thread:thread-old"]);
});
