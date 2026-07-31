import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");

async function sourceFor(relativePath) {
  if (relativePath === "static/js/chat.js") {
    const paths = [
      "static/js/chat/runtime.js",
      "static/js/chat/realtime.js",
      "static/js/chat/presence.js",
      "static/js/chat/messages-dom.js",
    ];
    return Promise.all(paths.map((sourcePath) => readFile(path.join(repoRoot, sourcePath), "utf8")))
      .then((sources) => sources.join("\n"));
  }
  return readFile(path.join(repoRoot, relativePath), "utf8");
}

function cssBlock(source, selector) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = source.match(new RegExp(`${escaped}\\s*\\{[^}]*\\}`));
  assert.ok(match, `Missing CSS block for ${selector}`);
  return match[0];
}

test("chat uses realtime event signals instead of message polling", async () => {
  const source = await sourceFor("static/js/chat/realtime.js");

  assert.doesNotMatch(source, /pollTimer/);
  assert.match(source, /pollActiveRoomMessages/);
  assert.match(source, /startRealtimeHeartbeat/);
  assert.match(source, /\/api\/chat\/events\/stream/);
  assert.match(source, /new EventSource/);
  assert.match(source, /initializeChatEventStream/);
  assert.match(source, /handleRealtimePayload/);
  assert.match(source, /normalizeChatEvent/);
  assert.match(source, /startRealtimeServices\(\)/);
  assert.match(source, /message_updated/);
  assert.doesNotMatch(source, /realtimeChannelName/);
  assert.doesNotMatch(source, /chatEventsTableId/);
  assert.doesNotMatch(source, /ensureAppwriteRealtimeAuth/);
  assert.doesNotMatch(source, /\/api\/chat\/realtime-token/);
});

test("chat uses local presence APIs for online and typing state", async () => {
  const script = await sourceFor("static/js/chat.js");
  const appwrite = await sourceFor("static/js/core/appwrite.js");
  const global = await sourceFor("static/js/core/global.js");
  const template = await sourceFor("templates/chat.html");

  assert.doesNotMatch(template, /appwrite@25\.0\.0|js\/core\/appwrite\.js/);
  assert.doesNotMatch(appwrite, /Appwrite\.Presences/);
  assert.doesNotMatch(appwrite, /Appwrite\.Realtime/);
  assert.doesNotMatch(appwrite, /window\.presences/);
  assert.doesNotMatch(appwrite, /window\.realtime/);
  assert.doesNotMatch(template, /data-appwrite-database-id/);
  assert.match(global, /initializePresenceHeartbeat/);
  assert.match(global, /\/api\/presence\/heartbeat/);
  assert.match(global, /scope_type: "chat"/);
  assert.match(global, /scope_type: "site"/);
  assert.match(global, /const siteHeartbeatMs = 60000/);
  assert.match(global, /const chatHeartbeatMs = 5000/);
  assert.match(global, /setChatRoom/);
  assert.match(global, /apstudy-presence-tab-id/);
  assert.match(script, /\/api\/presence\/online/);
  assert.match(script, /\/api\/presence\/statuses/);
  assert.match(script, /\/api\/presence\/room/);
  assert.match(script, /\/api\/presence\/heartbeat/);
  assert.match(script, /typing_channel/);
  assert.match(script, /typing_thread/);
  assert.match(script, /const PRESENCE_REFRESH_MS = 5000/);
  assert.match(script, /const TYPING_PRESENCE_TTL_MS = 8000/);
  assert.match(script, /tab_id: currentTabId\(\)/);
  assert.match(script, /function refreshTargetedPresences/);
  assert.match(script, /function renderTypingIndicator/);
  assert.match(script, /Several people are typing\.\.\./);
  assert.match(script, /renderPresenceDrivenUi\(\)/);
  assert.match(script, /presenceStatusLabel/);
  assert.match(script, /Online/);
  assert.match(script, /Busy/);
  assert.match(script, /online_users/);
  assert.doesNotMatch(script, /apstudy-chat-tab-id/);
});

test("chat keeps a page lifetime room cache with delta loading", async () => {
  const source = await sourceFor("static/js/chat.js");
  const cacheSource = await sourceFor("static/js/chat/cache.js");

  assert.match(source, /roomCache: new Map\(\)/);
  assert.match(source, /function cacheFor\(room\)/);
  assert.match(source, /latestCursor/);
  assert.match(cacheSource, /after: cache\.latestCursor/);
  assert.match(source, /removeMessageFromCaches/);
});

test("chat persists user-scoped IndexedDB cache until logout", async () => {
  const script = await sourceFor("static/js/chat.js");
  const cacheScript = await sourceFor("static/js/chat/cache.js");
  const template = await sourceFor("templates/chat.html");
  const globalScript = await sourceFor("static/js/core/global.js");

  assert.match(template, /data-current-user-id="\{\{ user\.id or '' \}\}"/);
  assert.match(cacheScript, /const CHAT_CACHE_DB_NAME = "apstudy-chat-cache"/);
  assert.match(cacheScript, /const CHAT_CACHE_SCHEMA = "v2"/);
  assert.match(script, /function persistentCacheKey\(suffix\)/);
  assert.match(script, /\$\{CHAT_CACHE_SCHEMA\}:user:\$\{userId\}:\$\{suffix\}/);
  assert.match(cacheScript, /indexedDB\.open\(CHAT_CACHE_DB_NAME, CHAT_CACHE_DB_VERSION\)/);
  assert.match(globalScript, /indexedDB\.deleteDatabase\("apstudy-chat-cache"\)/);
});

test("chat hydrates cached rooms before silent refresh and limits persisted messages", async () => {
  const script = await sourceFor("static/js/chat.js");
  const cacheScript = await sourceFor("static/js/chat/cache.js");

  assert.match(cacheScript, /const CHAT_CACHE_MESSAGE_LIMIT = 50/);
  assert.match(cacheScript, /\.slice\(-limit\)/);
  assert.match(cacheScript, /function normalizeCachedMessage\(message/);
  assert.match(cacheScript, /normalized\.can_delete = false/);
  assert.match(script, /function hydrateFromPersistentCache\(\)/);
  assert.match(script, /await hydrateRoomFromPersistentCache\(room\)/);
  assert.match(script, /await selectRoom\(room, \{ fromCacheHydration: true, quiet: true \}\)/);
  assert.match(script, /if \(renderCachedRoom\(room\)\)/);
  assert.match(script, /scheduleRoomPrefetches/);
  assert.match(script, /requestIdleCallback/);
});

test("chat marks selected cached rooms and sent messages as read", async () => {
  const script = await sourceFor("static/js/chat.js");

  assert.match(script, /function markRoomRead\(room, cache = cacheFor\(room\), \{ force = false \} = \{\}\)/);
  assert.match(script, /fetchJson\("\/api\/chat\/read"/);
  assert.match(script, /if \(!force && latest\?\.id\) body\.message_id = latest\.id/);
  assert.match(script, /clearRoomUnread\(room\)/);
  assert.match(script, /if \(!cache\.stale \|\| latestMessageForRead\(cache\)\) \{\s*markRoomRead\(room, cache\)/);
  assert.match(script, /applyIncomingMessages\(room, \[payload\.message\], \{ toBottom: true \}\)/);
  assert.match(script, /refreshViewingPresence\(\)/);
  assert.match(script, /\.finally\(\(\) => \{\s*actions\.markRoomRead\(room, cache\);\s*void actions\.refreshChatSummary\(\);\s*\}\)/);
});

test("chat keeps and renders per-room unread state", async () => {
  const script = await sourceFor("static/js/chat.js");
  const styles = await sourceFor("static/css/chat.css");

  assert.match(script, /roomUnread: new Map\(\)/);
  assert.match(script, /function unreadKey\(type, id\)/);
  assert.match(script, /function applyChatSummary\(payload = \{\}\)/);
  assert.match(script, /state\.roomUnread = nextUnread/);
  assert.match(script, /function chatSummaryPayloadFromUnreadMap\(payload = \{\}\)/);
  assert.match(script, /total_unread: Math\.min\(totalUnread, 99\)/);
  assert.match(script, /window\.dispatchEvent\(new CustomEvent\("apstudy-chat-summary"/);
  assert.match(script, /function unreadBadgeMarkup\(type, id\)/);
  assert.match(script, /class="chat-room-unread-badge"/);
  assert.match(script, /hasUnread \? "has-unread" : ""/);
  assert.match(styles, /grid-template-columns: 30px minmax\(0, 1fr\) auto/);
  assert.match(styles, /\.chat-list-button\.has-unread/);
  assert.match(styles, /\.chat-room-unread-badge/);
});

test("chat room context menu supports mark read", async () => {
  const script = await sourceFor("static/js/chat.js");
  const styles = await sourceFor("static/css/chat.css");

  assert.match(script, /contextMenuRoom: null/);
  assert.match(script, /function ensureRoomContextMenu\(\)/);
  assert.match(script, /id = "chat-room-context-menu"/);
  assert.match(script, /class="chat-room-context-action"/);
  assert.match(script, /data-chat-room-action="read"/);
  assert.doesNotMatch(script, /data-chat-room-action="unread"/);
  assert.doesNotMatch(script, /function markRoomUnread\(/);
  assert.match(script, /addEventListener\("contextmenu", \(event\) => openRoomContextMenu/);
  assert.match(script, /event\.key !== "ContextMenu" && !\(event\.shiftKey && event\.key === "F10"\)/);
  assert.match(script, /markRoomRead\(room, cacheFor\(room\), \{ force: true \}\)/);
  assert.match(script, /clearedReadRooms/);
  assert.match(script, /cancelUnreadSummaryRefresh\(\)/);
  assert.match(script, /function markRoomRead\(room, cache = cacheFor\(room\), \{ force = false \} = \{\}\)/);
  assert.match(script, /closeRoomContextMenu\(\)/);
  assert.match(styles, /\.chat-room-context-menu/);
  assert.match(styles, /\.chat-room-context-menu\[hidden\]/);
  assert.match(styles, /\.chat-room-context-action/);
  assert.match(styles, /background: transparent/);
});

test("chat refreshes and updates unread state across realtime and visibility", async () => {
  const script = await sourceFor("static/js/chat.js");

  assert.match(script, /async function refreshChatSummary\(\)/);
  assert.match(script, /fetchJson\("\/api\/chat\/summary"/);
  assert.match(script, /await refreshChatSummary\(\)/);
  assert.match(script, /scheduleUnreadSummaryRefresh\(\)/);
  assert.match(script, /actions\.scheduleUnreadSummaryRefresh\(\);\s*actions\.playChatSound\(event\.actor_id\)/);
  assert.match(script, /message_deleted"[\s\S]*void actions\.refreshChatSummary\(\)/);
  assert.match(script, /document\.visibilityState === "visible"[\s\S]*void actions\.refreshChatSummary\(\)/);
  assert.match(script, /setRoomUnread\(\{ type: "thread", id: payload\.thread\.id \}, \{ unread_count: 0, has_unread: false \}\)/);
});

test("chat fetches new DM threads directly from realtime events", async () => {
  const script = await sourceFor("static/js/chat.js");
  const api = await sourceFor("blueprints/chat_api.py");

  assert.match(script, /function threadExists\(threadId\)/);
  assert.match(script, /async function fetchThread\(threadId\)/);
  assert.match(script, /fetchJson\(`\/api\/chat\/dm\/threads\/\$\{encodeURIComponent\(threadId\)\}`\)/);
  assert.match(script, /eventRoom\?\.type === "thread" && !actions\.threadExists\(eventRoom\.id\)/);
  assert.match(script, /event\.event_type === "thread_updated" && eventRoom\?\.type === "thread"/);
  assert.match(script, /const thread = await actions\.fetchThread\(eventRoom\.id\)/);
  assert.match(api, /@chat_api_bp\.route\("\/api\/chat\/dm\/threads\/<thread_id>"\)/);
  assert.match(api, /def dm_thread\(thread_id\):/);
  assert.match(api, /return jsonify\(\{"thread": payload\}\)/);
});

test("chat supports direct channel and thread URL selection", async () => {
  const script = await sourceFor("static/js/chat.js");
  const dashboard = await sourceFor("services/dashboard_summary.py");

  assert.match(script, /function requestedRoomFromLocation\(\)/);
  assert.match(script, /new URLSearchParams\(window\.location\.search \|\| ""\)/);
  assert.match(script, /params\.get\("channel"\)/);
  assert.match(script, /params\.get\("thread"\)/);
  assert.match(script, /const requestedRoom = requestedRoomFromLocation\(\)/);
  assert.match(script, /await selectRoom\(requestedRoom, \{ suppressFocus: preserveActive \}\)/);
  assert.match(dashboard, /url_for\("dashboard\.chat", channel=room_id\)/);
  assert.match(dashboard, /url_for\("dashboard\.chat", thread=room_id\)/);
});

test("chat uses message-pane loader and profile toggle tooltip polish", async () => {
  const script = await sourceFor("static/js/chat.js");
  const styles = await sourceFor("static/css/chat.css");
  const template = await sourceFor("templates/chat.html");

  assert.match(script, /function renderMessageLoader/);
  assert.match(script, /APStudyLoader\.html\(label, \{ sizePx: 46, textToneClass: "text-on-surface" \}\)/);
  assert.match(styles, /\.chat-message-loader/);
  assert.match(template, /title="Show user profile"/);
  assert.match(script, /const label = state\.membersCollapsed \? "Show user profile" : "Hide user profile"/);
  assert.match(script, /setAttribute\("title", label\)/);
  assert.match(styles, /#chat-room-symbol\.is-avatar\s*\{[\s\S]*border-radius: var\(--radius-avatar\)/);
  assert.match(styles, /#chat-room-symbol img\s*\{[\s\S]*object-fit: cover/);
});

test("chat renders university pending state in the main panel only", async () => {
  const script = await sourceFor("static/js/chat.js");
  const template = await sourceFor("templates/chat.html");

  assert.match(script, /function renderApprovalNotice/);
  assert.match(script, /Waiting Admin Approval\./);
  assert.doesNotMatch(template, /chat-university-pending/);
  assert.doesNotMatch(template, /chat-pending-card/);
});

test("chat direct messages render presence dots and profile-only side pane", async () => {
  const script = await sourceFor("static/js/chat.js");
  const styles = await sourceFor("static/css/chat.css");

  assert.match(script, /dmPresenceMarkup/);
  assert.match(script, /chat-presence-dot/);
  assert.doesNotMatch(script, /function dmPresenceMarkup\(status\)[\s\S]*chat-presence-dot[\s\S]*function renderThreads/);
  assert.doesNotMatch(script, /Direct message/);
  assert.match(styles, /chat-presence-dot\.is-active/);
  assert.match(styles, /chat-presence-dot\.is-busy/);
  assert.match(styles, /chat-presence-dot\.is-offline/);
  assert.match(script, /is-dm-profile/);
  assert.match(script, /className: "chat-dm-button"/);
  assert.match(styles, /\.chat-dm-button \.chat-list-copy\s*\{[^}]*justify-content: center;/s);
});

test("chat groups close same-author messages and formats timestamps compactly", async () => {
  const source = await sourceFor("static/js/chat.js");
  const presentation = await sourceFor("static/js/chat/presentation.js");

  assert.match(presentation, /const MESSAGE_GROUP_WINDOW_MS = 7 \* 60 \* 1000/);
  assert.match(presentation, /function groupMessages\(messages\)/);
  assert.match(presentation, /function shouldGroupMessage\(previous, next\)/);
  assert.match(presentation, /localDateKey\(previousDate\) === localDateKey\(nextDate\)/);
  assert.match(presentation, /Yesterday at \$\{time\}/);
  assert.match(source, /chat-message-continuation-time/);
});

test("chat keeps the composer compact and message stack bottom aligned", async () => {
  const styles = await sourceFor("static/css/chat.css");
  const composer = cssBlock(styles, ".chat-composer");

  assert.match(styles, /\.chat-main-panel\s*\{[\s\S]*display: flex[\s\S]*flex-direction: column/);
  assert.match(styles, /\.chat-messages\s*\{[\s\S]*flex: 1 1 auto[\s\S]*min-height: 0[\s\S]*overflow-y: auto/);
  assert.match(styles, /\.chat-message-stack\s*\{[^}]*margin-top: auto/s);
  assert.match(composer, /flex: 0 0 auto/);
  assert.match(composer, /align-self: stretch/);
  assert.doesNotMatch(composer, /align-self: end/);
  assert.doesNotMatch(composer, /position: sticky/);
  assert.doesNotMatch(composer, /position: absolute/);
  assert.match(styles, /\.chat-typing-indicator/);
});

test("chat renders discord image attachments as plain lazy images", async () => {
  const script = await sourceFor("static/js/chat.js");
  const styles = await sourceFor("static/css/chat.css");

  assert.match(script, /function renderImages\(images\)/);
  assert.match(script, /renderImages\(message\.images \|\| \[\]\)/);
  assert.match(script, /class="chat-message-image"/);
  assert.match(script, /loading="lazy"/);
  assert.match(styles, /\.chat-message-images/);
  assert.match(styles, /\.chat-message-image/);
});

test("chat styles discord custom emojis as inline lazy images", async () => {
  const styles = await sourceFor("static/css/chat.css");

  assert.match(styles, /\.chat-custom-emoji\s*\{/);
  assert.match(styles, /width: 1\.375em/);
  assert.match(styles, /height: 1\.375em/);
  assert.match(styles, /object-fit: contain/);
  assert.match(styles, /vertical-align: -0\.32em/);
});

test("scheduler uses discord gateway with slow reconciliation", async () => {
  const scheduler = await sourceFor("services/scheduler.py");
  const api = await sourceFor("blueprints/chat_api.py");
  const gateway = await sourceFor("services/discord_gateway.py");

  assert.match(scheduler, /def _reconcile_discord_chat\(app\):/);
  assert.match(scheduler, /sync_discord_channels\(emit_events=False, emit_delete_events=True\)/);
  assert.match(scheduler, /DISCORD_CHAT_RECONCILE_SECONDS/);
  assert.match(scheduler, /id="reconcile_discord_chat"/);
  assert.match(scheduler, /start_discord_gateway\(app\)/);
  assert.match(gateway, /discord\.Client\(intents=intents\)/);
  assert.match(gateway, /on_message/);
  assert.match(gateway, /on_raw_message_edit/);
  assert.match(gateway, /on_raw_message_delete/);
  assert.match(gateway, /sync_discord_channels\(emit_events=False, emit_delete_events=True\)/);
  assert.match(api, /def sync_discord_channels\(emit_events=True, emit_delete_events=None\):/);
  assert.match(api, /_sync_discord_channel\(\s*channel,\s*emit_events=emit_events,\s*emit_delete_events=emit_delete_events,/);
  assert.match(api, /_upsert_discord_message\(channel, message, emit_event=emit_events\)/);
  assert.match(api, /emit_chat_event\(\s*"channel",\s*channel_id,\s*"message_created"/);
  assert.match(api, /"message_updated"/);
  assert.match(api, /@chat_api_bp\.route\("\/api\/chat\/events\/stream"\)/);
  assert.match(api, /text\/event-stream/);
  assert.match(api, /def _event_visible_for_user/);
  assert.match(api, /@chat_api_bp\.route\("\/api\/presence\/heartbeat", methods=\["POST"\]\)/);
  assert.match(api, /@chat_api_bp\.route\("\/api\/presence\/online"\)/);
  assert.match(api, /@chat_api_bp\.route\("\/api\/presence\/statuses", methods=\["POST"\]\)/);
  assert.match(api, /@chat_api_bp\.route\("\/api\/presence\/room", methods=\["POST"\]\)/);
  assert.match(api, /PRESENCE_CHAT_FRESH_SECONDS/);
  assert.match(api, /PRESENCE_SITE_FRESH_SECONDS/);
  assert.match(api, /PRESENCE_TYPING_FRESH_SECONDS/);
  assert.doesNotMatch(api, /create_jwt/);
  assert.match(api, /@chat_api_bp\.route\("\/api\/chat\/discord\/messages", methods=\["POST"\]\)/);
  assert.match(api, /def discord_message_ingest\(\):/);
  assert.match(api, /_valid_discord_ingest_request\(\)/);
});

test("global sidebar chat badge uses summary polling and shared chat summary events", async () => {
  const sidebar = await sourceFor("static/js/core/sidebar.js");

  assert.match(sidebar, /data-chat-unread-badge/);
  assert.match(sidebar, /\/api\/chat\/summary/);
  assert.match(sidebar, /const pollMs = 120000/);
  assert.match(sidebar, /document\.visibilityState === 'hidden'/);
  assert.match(sidebar, /apstudy-chat-summary/);
  assert.match(sidebar, /payload\.rooms\.reduce/);
  assert.match(sidebar, /renderBadge\(event\.detail \|\| \{\}\)/);
  assert.doesNotMatch(sidebar, /window\.location\.pathname === '\/chat'/);
  assert.doesNotMatch(sidebar, /client\.subscribe/);
  assert.doesNotMatch(sidebar, /\/api\/chat\/channels\/.*messages/);
});

test("chat textarea enter sends and shift enter keeps multiline input", async () => {
  const script = await sourceFor("static/js/chat.js");

  assert.match(script, /function handleComposerKeydown\(event\)/);
  assert.match(script, /event\.key !== "Enter"/);
  assert.match(script, /event\.shiftKey/);
  assert.match(script, /event\.preventDefault\(\)/);
  assert.match(script, /if \(state\.messageSendInFlight\) return;/);
  assert.match(script, /els\.composer\.requestSubmit\(\)/);
  assert.match(script, /addEventListener\("keydown", handleComposerKeydown\)/);
});

test("chat composer ignores duplicate sends while a message is in flight", async () => {
  const script = await sourceFor("static/js/chat.js");

  assert.match(script, /messageSendInFlight: false/);
  assert.match(script, /if \(state\.messageSendInFlight\) return;\s+const channel = activeChannel\(\)/);
  assert.match(script, /state\.messageSendInFlight = true;\s+els\.sendButton\.disabled = true/);
  assert.match(script, /finally \{\s+state\.messageSendInFlight = false;/);
});

test("chat renders discord mention pills and scalable message avatars", async () => {
  const styles = await sourceFor("static/css/chat.css");

  assert.match(styles, /--chat-message-avatar-size: 42px/);
  assert.match(styles, /grid-template-columns: var\(--chat-message-avatar-size\) minmax\(0, 1fr\)/);
  assert.match(styles, /\.chat-message-avatar\s*\{[\s\S]*width: 100%[\s\S]*aspect-ratio: 1/);
  assert.match(styles, /--chat-message-avatar-size: 34px/);
  assert.match(styles, /\.chat-mention\s*\{/);
  assert.match(styles, /\.chat-mention-role\s*\{/);
  assert.match(styles, /font-weight: 650/);
});

test("chat history banner has no close control and follows scroll-top visibility", async () => {
  const script = await sourceFor("static/js/chat.js");
  const template = await sourceFor("templates/chat.html");
  const styles = await sourceFor("static/css/chat.css");

  assert.doesNotMatch(script, /closedHistoryBanners/);
  assert.match(script, /function updateHistoryBannerVisibility/);
  assert.match(script, /channel\.history_limited === true/);
  assert.match(script, /messagePaneIsScrollable/);
  assert.match(script, /scrollTop <= 16/);
  assert.doesNotMatch(template, /chat-history-close/);
  assert.doesNotMatch(styles, /chat-history-close/);
  assert.match(template, /Older Discord history lives in the server\./);
});

test("chat starts realtime fallback refresh after websocket failure", async () => {
  const script = await sourceFor("static/js/chat.js");

  assert.doesNotMatch(script, /pollTimer/);
  assert.match(script, /function startRealtimeFallback/);
  assert.match(script, /function stopRealtimeFallback/);
  assert.match(script, /document\.visibilityState === "visible"/);
  assert.match(script, /void actions\.refreshChatSummary\(\)/);
  assert.match(script, /startRealtimeFallback\(\)/);
  assert.match(script, /stopRealtimeFallback\(\)/);
});

test("chat lifecycle stops realtime resources and resumes one runtime", async () => {
  const script = await sourceFor("static/js/chat.js");

  assert.match(script, /function stopChatRuntime\(\{ dispose = false \} = \{\}\)/);
  assert.match(script, /chatRequestController\.abort\(\)/);
  assert.match(script, /resetRealtimeConnection\(\)/);
  assert.match(script, /stopPresenceRefreshTimer\(\)/);
  assert.match(script, /function clearReconnectTimer\(\)[\s\S]*window\.clearTimeout\(realtimeReconnectTimer\)/);
  assert.match(script, /cancelUnreadSummaryRefresh\(\)/);
  assert.match(script, /clearTransientWork\(\)/);
  assert.match(script, /function resumeChatRuntime\(\)/);
  assert.match(script, /chatRequestController = new AbortController\(\)/);
  assert.match(script, /window\.APStudyPageLifecycle\.register\(\{/);
  assert.doesNotMatch(script, /window\.addEventListener\("beforeunload"/);
});

test("chat author names and avatars open inline profile popovers", async () => {
  const script = await sourceFor("static/js/chat.js");
  const styles = await sourceFor("static/css/chat.css");

  assert.match(script, /class="chat-author-button"/);
  assert.match(script, /class="chat-message-avatar-button/);
  assert.match(script, /function openInlineProfileForMessage/);
  assert.match(script, /function positionInlineProfilePopover/);
  assert.match(script, /function closeInlineProfilePopover/);
  assert.match(script, /author_profile/);
  assert.match(script, /is-limited/);
  assert.match(styles, /\.chat-inline-profile-popover/);
  assert.match(styles, /\.chat-author-button/);
  assert.match(styles, /\.chat-message-avatar-button/);
});

test("chat announcement unread banner auto-reads small ranges and marks read on close", async () => {
  const script = await sourceFor("static/js/chat.js");
  const template = await sourceFor("templates/chat.html");

  assert.match(template, /id="chat-announcements-unread"/);
  assert.match(template, /Unread announcements/);
  assert.match(template, /aria-label="Mark announcements read"/);
  assert.match(script, /function updateAnnouncementsUnreadBanner/);
  assert.match(script, /function markAnnouncementsRead/);
  assert.match(script, /const ANNOUNCEMENTS_CHANNEL_ID = "nest_announcements"/);
  assert.match(script, /function isAnnouncementsChannel/);
  assert.match(script, /if \(isAnnouncementsChannel\(channel\)\) return ""/);
  assert.match(script, /fetchJson\("\/api\/chat\/read"/);
  assert.match(script, /read_state/);
});

test("chat profile views mirror full profiles and keep presence on the avatar", async () => {
  const script = await sourceFor("static/js/chat.js");
  const styles = await sourceFor("static/css/chat.css");
  const profileStyles = await sourceFor("static/css/user-profile.css");
  const profileTemplate = await sourceFor("templates/user_profile.html");

  for (const marker of ["profile-tile", "profile-tile-banner", "profile-tile-details", "Member Since"]) {
    assert.match(script, new RegExp(marker));
  }
  assert.match(script, /profileDetail\("Education"/);
  assert.match(script, /function tierBadgeMarkup/);
  assert.match(script, /function memberTierBadgeMarkup/);
  assert.match(script, /function showMemberProfile/);
  assert.match(script, /profile-tile-detail-emory/);
  assert.match(script, /profile-tile-detail-early-member/);
  assert.match(styles, /\.chat-profile-card \.profile-tile/);
  assert.match(styles, /\.chat-profile-panel\s*\{[\s\S]*overflow-y: auto/);
  assert.match(styles, /\.chat-inline-profile-popover\s*\{[\s\S]*overflow: visible/);
  assert.match(styles, /\.chat-profile-card \.profile-tile-details dt/);
  assert.match(styles, /\.chat-profile-card \.profile-tile-detail-early-member dd/);
  assert.match(script, /chat-presence-dot chat-presence-overlay is-\$\{status\}/);
  assert.match(script, /chat-profile-presence-label/);
  assert.doesNotMatch(script, /chat-presence-line chat-profile-presence/);
  assert.match(styles, /\.chat-profile-card \.profile-tile-details div\s*\{[\s\S]*border-radius:/);
  assert.match(profileStyles, /\.user-profile-card \.profile-tile\s*\{[\s\S]*aspect-ratio: auto/);
  assert.doesNotMatch(profileStyles, /\.user-profile-card \.tier-badge\s*\{[\s\S]*transform: scale\(1\.5\)/);
  assert.match(styles, /\.chat-profile-card \.profile-tile-heading \.tier-badge-trigger\s*\{[\s\S]*margin-top: 4px/);
  assert.match(profileTemplate, /profile\.tier_badge/);
});

test("chat online users pane has desktop collapse controls", async () => {
  const script = await sourceFor("static/js/chat.js");
  const styles = await sourceFor("static/css/chat.css");
  const template = await sourceFor("templates/chat.html");

  assert.match(template, /data-toggle-members/);
  assert.match(template, /chat-profile-toggle/);
  assert.doesNotMatch(template, /data-collapse-members/);
  assert.match(script, /profileToggle/);
  assert.match(template, /data-restore-members/);
  assert.match(template, /data-profile-back/);
  assert.match(template, /chat-members-context/);
  assert.match(template, /Online/);
  assert.match(template, /Online users and profile/);
  assert.match(script, /setMembersCollapsed/);
  assert.match(styles, /members-collapsed/);
  assert.match(template, /chat-conversation-body[\s\S]*chat-conversation-pane[\s\S]*chat-members/);
  assert.match(styles, /\.chat-conversation-body\s*\{[\s\S]*grid-template-columns:/);
  assert.match(styles, /\.chat-members\.is-dm-profile \.chat-members-head\s*\{[\s\S]*display: none/);
});

test("chat DM header uses avatar status dot without online text and focuses composer", async () => {
  const script = await sourceFor("static/js/chat.js");
  const styles = await sourceFor("static/css/chat.css");

  assert.match(script, /chat-room-avatar-wrap/);
  assert.match(script, /els\.roomMeta\.textContent = ""/);
  assert.match(script, /els\.input\?\.focus\(\{ preventScroll: true \}\)/);
  assert.match(styles, /\.chat-rail-head,[\s\S]*\.chat-topbar\s*\{[\s\S]*height: 52px/);
  assert.match(styles, /#chat-room-symbol\.is-avatar\s*\{[\s\S]*overflow: visible/);
});
