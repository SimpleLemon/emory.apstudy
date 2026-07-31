import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const source = (file) => readFile(path.join(root, file), "utf8");

test("chat entry point keeps composer responsibilities in adjacent modules", async () => {
  const index = await source("static/js/chat/index.js");
  assert.match(index, /attachments\.js/);
  assert.match(index, /media-picker\.js/);
  assert.match(index, /message-media\.js/);
  assert.match(index, /runtime\.js/);
});

test("chat attachments support progress, retry, cancellation, and browser gzip", async () => {
  const attachments = await source("static/js/chat/attachments.js");
  assert.match(attachments, /CompressionStream\("gzip"\)/);
  assert.match(attachments, /xhr\.upload\.addEventListener\("progress"/);
  assert.match(attachments, /data-upload-retry/);
  assert.match(attachments, /method: "DELETE"/);
  assert.match(attachments, /File attachments are temporarily unavailable/);
  assert.doesNotMatch(attachments, /els\.attach\.disabled/);
});

test("media picker has emoji navigation and an accessible roving tab interface", async () => {
  const picker = await source("static/js/chat/media-picker.js");
  const template = await source("templates/chat.html");
  assert.match(picker, /ArrowRight/);
  assert.match(picker, /apstudy-chat-recent-emoji/);
  assert.match(picker, /capabilities\.giphy\?\.available/);
  assert.match(picker, /handleTabKeydown/);
  assert.match(picker, /aria-disabled/);
  assert.match(picker, /returnFocusEl/);
  assert.match(template, /Powered by GIPHY/);
  assert.match(picker, /analytics\?\.onsent/);
  assert.match(picker, /chat-emoji-categories/);
  assert.match(template, /id="chat-emoji-categories"/);
  assert.match(template, /gif_box/);
  assert.match(template, /id="chat-gif-tab"[^>]*tabindex="-1"/);
  assert.match(template, /id="chat-gif-unavailable"/);
});

test("emoji trigger rotates through five colored smile reactions on hover", async () => {
  const picker = await source("static/js/chat/media-picker.js");
  const styles = await source("static/css/chat.css");
  assert.match(picker, /HOVER_SMILES = \["😀", "😄", "😊", "🤩", "🥳"\]/);
  assert.match(picker, /addEventListener\("pointerenter", showHoverSmile\)/);
  assert.match(picker, /addEventListener\("pointerleave", restoreHoverSmile\)/);
  assert.match(styles, /@keyframes chat-smile-pop/);
  assert.match(styles, /prefers-reduced-motion: reduce[\s\S]*\.chat-media-button \.chat-hover-smile/);
});

test("chat send stays disabled until the composer has sendable content", async () => {
  const runtime = await source("static/js/chat/composer.js");
  const template = await source("templates/chat.html");
  const styles = await source("static/css/chat.css");
  assert.match(runtime, /function updateComposerSubmitState/);
  assert.match(runtime, /!hasText && !hasAttachment && !hasGif/);
  assert.match(runtime, /attachmentsBusy/);
  assert.match(template, /class="chat-send-button"[^>]*disabled/);
  assert.match(styles, /\.chat-send-button:disabled/);
});

test("every non-image card opens the advisory download warning", async () => {
  const media = await source("static/js/chat/message-media.js");
  const template = await source("templates/chat.html");
  assert.match(media, /data-chat-download/);
  assert.match(media, /showModal/);
  assert.match(template, /Nest never submits files automatically/);
  assert.match(template, /VirusTotal/);
});
