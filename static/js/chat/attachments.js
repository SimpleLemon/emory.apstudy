import { escapeHtml } from "../core/ui-primitives-module.js";

const ALLOWED_EXTENSIONS = new Set([
  "jpg", "jpeg", "png", "webp", "gif", "pdf", "txt", "md", "markdown", "csv", "json",
  "docx", "xlsx", "pptx", "odt", "ods", "odp", "zip",
]);
const COMPRESSIBLE_EXTENSIONS = new Set(["txt", "md", "markdown", "csv", "json"]);

function formatBytes(value) {
  const size = Number(value || 0);
  if (size < 1024) return `${size} B`;
  if (size < 1024 ** 2) return `${(size / 1024).toFixed(1)} KiB`;
  return `${(size / 1024 ** 2).toFixed(1)} MiB`;
}

async function compressedUpload(file) {
  const extension = file.name.split(".").pop()?.toLowerCase() || "";
  if (!("CompressionStream" in window) || !COMPRESSIBLE_EXTENSIONS.has(extension)) {
    return { body: file, encoding: "identity" };
  }
  try {
    const stream = file.stream().pipeThrough(new CompressionStream("gzip"));
    const compressed = await new Response(stream).blob();
    return compressed.size < file.size ? { body: compressed, encoding: "gzip" } : { body: file, encoding: "identity" };
  } catch (_) {
    return { body: file, encoding: "identity" };
  }
}

export function createAttachmentManager() {
  const items = [];
  let state;
  let setStatus;
  let onComposerChange;
  let capabilities = {};
  const els = {};

  function room() {
    return state?.activeRoom || null;
  }

  function render() {
    if (!els.pending || !els.list) return;
    els.list.innerHTML = items.map((item) => `
      <article class="chat-upload-chip ${item.status === "error" ? "is-error" : ""}" data-upload-id="${item.localId}">
        <span class="material-symbols-outlined" aria-hidden="true">${item.status === "uploaded" ? "draft" : item.status === "error" ? "error" : "upload"}</span>
        <span class="chat-upload-copy">
          <strong>${escapeHtml(item.file.name)}</strong>
          <small>${item.status === "uploading" ? `Uploading ${item.progress}%` : item.status === "error" ? escapeHtml(item.error) : `${formatBytes(item.file.size)} · Ready`}</small>
          ${item.status === "uploading" ? `<span class="chat-upload-progress"><span style="--chat-upload-progress:${Math.max(0, Math.min(100, item.progress)) / 100}"></span></span>` : ""}
        </span>
        ${item.status === "error" ? `<button type="button" data-upload-retry="${item.localId}" aria-label="Retry ${escapeHtml(item.file.name)}"><span class="material-symbols-outlined" aria-hidden="true">refresh</span></button>` : ""}
        <button type="button" data-upload-remove="${item.localId}" aria-label="Remove ${escapeHtml(item.file.name)}"><span class="material-symbols-outlined" aria-hidden="true">close</span></button>
      </article>
    `).join("");
    els.pending.hidden = items.length === 0 && !document.getElementById("chat-selected-gif");
    onComposerChange?.();
  }

  function validate(file) {
    const extension = file.name.split(".").pop()?.toLowerCase() || "";
    if (!ALLOWED_EXTENSIONS.has(extension)) return "This file type is not allowed in chat.";
    const limit = Number(capabilities.max_attachment_size_bytes || 0);
    if (limit && file.size > limit) return `This file exceeds your ${formatBytes(limit)} chat limit.`;
    return "";
  }

  async function upload(item) {
    const activeRoom = room();
    if (!activeRoom) return;
    item.status = "uploading";
    item.error = "";
    item.progress = 0;
    render();
    const prepared = await compressedUpload(item.file);
    if (!items.includes(item)) return;
    const form = new FormData();
    form.append("file", prepared.body, item.file.name);
    form.append("scope_type", activeRoom.type);
    form.append("scope_id", activeRoom.id);
    form.append("original_size_bytes", String(item.file.size));
    form.append("content_encoding", prepared.encoding);
    const xhr = new XMLHttpRequest();
    item.xhr = xhr;
    xhr.open("POST", "/api/chat/attachments");
    xhr.responseType = "json";
    xhr.upload.addEventListener("progress", (event) => {
      if (!event.lengthComputable) return;
      item.progress = Math.max(1, Math.round((event.loaded / event.total) * 100));
      render();
    });
    xhr.addEventListener("load", () => {
      const payload = xhr.response || {};
      if (xhr.status < 200 || xhr.status >= 300 || !payload.attachment) {
        item.status = "error";
        item.error = payload.error || "Upload failed. Try again.";
      } else {
        item.status = "uploaded";
        item.attachment = payload.attachment;
        item.progress = 100;
      }
      render();
    });
    xhr.addEventListener("error", () => {
      item.status = "error";
      item.error = navigator.onLine ? "Upload failed. Try again." : "You are offline.";
      render();
    });
    xhr.addEventListener("abort", () => {
      if (items.includes(item)) {
        item.status = "error";
        item.error = "Upload cancelled.";
        render();
      }
    });
    xhr.send(form);
  }

  function addFiles(fileList) {
    if (!capabilities.attachments) {
      setStatus?.("Attachments are not configured yet.", "error");
      return;
    }
    const available = Math.max(0, Number(capabilities.max_attachments_per_message || 5) - items.length);
    const incoming = Array.from(fileList || []).slice(0, available);
    if (Array.from(fileList || []).length > available) setStatus?.("A message can include at most five attachments.", "error");
    for (const file of incoming) {
      const error = validate(file);
      const item = { localId: crypto.randomUUID(), file, status: error ? "error" : "queued", error, progress: 0 };
      items.push(item);
      if (!error) void upload(item);
    }
    render();
  }

  async function remove(localId, options = {}) {
    const index = items.findIndex((item) => item.localId === localId);
    if (index < 0) return;
    const [item] = items.splice(index, 1);
    item.xhr?.abort();
    render();
    const commit = ({ reason } = {}) => {
      if (!item.attachment?.id) return Promise.resolve();
      return fetch(`/api/chat/attachments/${encodeURIComponent(item.attachment.id)}`, {
        method: "DELETE",
        keepalive: reason === "pagehide",
      }).then((response) => {
        if (!response.ok) throw new Error("Unable to remove attachment.");
      });
    };
    if (options.notify !== false && window.APStudyUndo?.stage) {
      window.APStudyUndo.stage({
        message: `${item.file.name} removed from this message.`,
        commit,
        restore: () => {
          items.splice(Math.min(index, items.length), 0, item);
          if (!item.attachment && ["queued", "uploading"].includes(item.status)) {
            item.status = "queued";
            item.progress = 0;
            void upload(item);
          } else {
            render();
          }
        },
        errorTitle: "Couldn’t remove attachment",
      });
      return;
    }
    await commit();
  }

  return {
    init(context) {
      state = context.state;
      setStatus = context.setStatus;
      onComposerChange = context.onComposerChange;
      els.pending = document.getElementById("chat-pending-files");
      els.list = document.getElementById("chat-upload-list");
      els.fileInput = document.getElementById("chat-file-input");
      els.attach = document.getElementById("chat-attach-button");
      els.composer = document.getElementById("chat-composer");
      els.attach?.addEventListener("click", () => {
        if (!capabilities.attachments) {
          setStatus?.("File attachments are temporarily unavailable. Try refreshing the page.", "error");
          return;
        }
        els.fileInput?.click();
      });
      els.fileInput?.addEventListener("change", () => {
        addFiles(els.fileInput.files);
        els.fileInput.value = "";
      });
      els.list?.addEventListener("click", (event) => {
        const removeButton = event.target.closest("[data-upload-remove]");
        if (removeButton) void remove(removeButton.dataset.uploadRemove);
        const retryButton = event.target.closest("[data-upload-retry]");
        if (retryButton) {
          const item = items.find((row) => row.localId === retryButton.dataset.uploadRetry);
          if (item) void upload(item);
        }
      });
      ["dragenter", "dragover"].forEach((name) => els.composer?.addEventListener(name, (event) => {
        event.preventDefault();
        els.composer.classList.add("is-dragging");
      }));
      ["dragleave", "drop"].forEach((name) => els.composer?.addEventListener(name, (event) => {
        event.preventDefault();
        els.composer.classList.remove("is-dragging");
        if (name === "drop") addFiles(event.dataTransfer?.files);
      }));
      document.getElementById("chat-message-input")?.addEventListener("paste", (event) => {
        const files = Array.from(event.clipboardData?.files || []);
        if (files.length) addFiles(files);
      });
    },
    configure(value) {
      capabilities = value || {};
      if (els.attach) {
        els.attach.setAttribute("aria-disabled", String(!capabilities.attachments));
        els.attach.title = capabilities.attachments ? "Attach files" : "File attachments are temporarily unavailable";
      }
      onComposerChange?.();
    },
    readyIds() {
      return items.filter((item) => item.status === "uploaded").map((item) => item.attachment.id);
    },
    hasContent() { return items.length > 0; },
    isBusy() { return items.some((item) => item.status !== "uploaded"); },
    clear() { items.splice(0); render(); },
    resetForRoom() { items.slice().forEach((item) => void remove(item.localId, { notify: false })); },
  };
}
