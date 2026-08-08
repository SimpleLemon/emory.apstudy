import { escapeHtml } from "../core/ui-primitives-module.js";

function formatBytes(value) {
  const size = Number(value || 0);
  return size >= 1024 ** 2 ? `${(size / 1024 ** 2).toFixed(1)} MiB` : `${Math.max(1, Math.round(size / 1024))} KiB`;
}

export function createMessageMedia() {
  function renderAttachments(attachments = []) {
    if (!attachments.length) return "";
    return `<div class="chat-attachments">${attachments.map((attachment) => {
      if (attachment.kind === "image") {
        return `<a class="chat-attachment-image" href="${escapeHtml(attachment.download_url)}" download>
          <img src="${escapeHtml(attachment.preview_url)}" alt="${escapeHtml(attachment.filename)}" loading="lazy" decoding="async">
        </a>`;
      }
      const preview = attachment.kind === "pdf" && attachment.preview_url
        ? `<img src="${escapeHtml(attachment.preview_url)}" alt="First page preview" loading="lazy" decoding="async">`
        : `<span class="material-symbols-outlined" aria-hidden="true">${attachment.kind === "pdf" ? "picture_as_pdf" : "draft"}</span>`;
      return `<button class="chat-file-card" type="button" data-chat-download="${escapeHtml(attachment.download_url)}" data-chat-filename="${escapeHtml(attachment.filename)}" data-virus-total="${escapeHtml(attachment.virus_total_url || "")}">
        ${preview}<span><strong>${escapeHtml(attachment.filename)}</strong><small>${formatBytes(attachment.size_bytes)} · Check before downloading</small></span>
        <span class="material-symbols-outlined" aria-hidden="true">download</span>
      </button>`;
    }).join("")}</div>`;
  }

  function renderGif(gif) {
    if (!gif?.url) return "";
    return `<figure class="chat-message-gif"><img src="${escapeHtml(gif.url)}" alt="${escapeHtml(gif.title || "GIF")}" loading="lazy" decoding="async"><figcaption>via GIPHY</figcaption></figure>`;
  }

  function init() {
    const dialog = document.getElementById("chat-download-warning");
    const confirm = document.getElementById("chat-download-confirm");
    const report = document.getElementById("chat-virus-total-link");
    document.getElementById("chat-messages")?.addEventListener("click", (event) => {
      const button = event.target.closest("[data-chat-download]");
      if (!button) return;
      event.preventDefault();
      confirm.href = button.dataset.chatDownload;
      confirm.setAttribute("download", button.dataset.chatFilename || "attachment");
      report.href = button.dataset.virusTotal || "https://www.virustotal.com/gui/home/search";
      dialog?.showModal();
    });
    confirm?.addEventListener("click", () => dialog?.close());
  }

  return { init, renderAttachments, renderGif };
}
