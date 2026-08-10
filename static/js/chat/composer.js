import { escapeHtml } from "./presentation.js";

export function createChatComposer(context) {
  const {
    state,
    els,
    extensions,
    actions,
    fetchJson,
  } = context;

  function composerIsWritable() {
    const channel = actions.activeChannel();
    if (channel) return actions.channelIsWritable(channel);
    const thread = actions.activeThread();
    return Boolean(thread && !thread.blocked);
  }

  function isActiveRoom(room) {
    return Boolean(
      room
      && state.activeRoom
      && actions.roomKey(state.activeRoom) === actions.roomKey(room)
    );
  }

  function updateComposerSubmitState() {
    if (!els.sendButton) return;
    const hasText = Boolean(els.input?.value.trim());
    const hasAttachment = Boolean(extensions.attachments?.readyIds?.().length);
    const hasGif = Boolean(extensions.mediaPicker?.hasSelection?.());
    const attachmentsBusy = Boolean(extensions.attachments?.isBusy?.());
    els.sendButton.disabled = !composerIsWritable()
      || (!hasText && !hasAttachment && !hasGif)
      || attachmentsBusy
      || state.messageSendInFlight;
  }

  function autosizeComposer() {
    if (!els.input) return;
    els.input.style.height = "auto";
    const nextHeight = Math.min(112, Math.max(24, els.input.scrollHeight));
    els.input.style.height = `${nextHeight}px`;
  }

  function setComposer(enabled, placeholder) {
    if (!els.composer || !els.input || !els.sendButton) return;
    els.composer.hidden = false;
    els.input.disabled = !enabled;
    els.input.placeholder = placeholder || "Message";
    autosizeComposer();
    updateComposerSubmitState();
  }

  async function sendActiveMessage(event) {
    event.preventDefault();
    const room = state.activeRoom;
    if (!room || !els.input) return;
    const content = els.input.value.trim();
    const attachmentIds = extensions.attachments?.readyIds?.() || [];
    const gifSelection = extensions.mediaPicker?.selection?.() || {};
    if (!content && !attachmentIds.length && !gifSelection.gif_id) return;
    if (extensions.attachments?.isBusy?.()) {
      actions.setStatus("Wait for attachments to finish uploading before sending.", "error");
      return;
    }
    if (state.messageSendInFlight) return;
    const channel = actions.activeChannel();
    const thread = actions.activeThread();
    if (channel && !actions.channelIsWritable(channel)) return;
    if (thread?.blocked) return;

    state.messageSendInFlight = true;
    els.sendButton.disabled = true;
    actions.clearTypingPresence(room);
    const localId = `pending-${crypto.randomUUID()}`;
    const payloadBody = { content, attachment_ids: attachmentIds, ...gifSelection };
    try {
      const optimistic = {
        id: localId,
        user_id: state.user?.id,
        author_name: state.user?.name || state.user?.username || "You",
        author_username: state.user?.username || "",
        author_avatar_url: state.user?.picture_url || state.user?.picture || "",
        content: content || (gifSelection.gif_id ? "GIF" : "Attachment"),
        rendered_html: escapeHtml(content || ""),
        created_at: new Date().toISOString(),
        delivery_state: "sending",
        can_delete: false,
        attachments: [],
      };
      actions.applyIncomingMessages(room, [optimistic], { toBottom: true, markRead: false });
      const url = actions.currentRoomUrl(room);
      const payload = await fetchJson(url, {
        method: "POST",
        body: JSON.stringify(payloadBody),
      });
      actions.removeMessageFromCaches(localId);
      if (isActiveRoom(room)) {
        els.input.value = "";
        autosizeComposer();
        extensions.attachments?.clear?.();
        extensions.mediaPicker?.clear?.(true);
      }
      const cache = actions.cacheFor(room);
      if (cache && payload.message) {
        actions.applyIncomingMessages(room, [payload.message], { toBottom: true });
      }
      if (isActiveRoom(room)) actions.refreshViewingPresence();
      actions.schedulePersistentBootstrapSave();
    } catch (error) {
      const cache = actions.cacheFor(room);
      const failed = cache?.messages?.find((message) => message.id === localId);
      if (failed) {
        failed.delivery_state = "failed";
        state.failedMessages.set(localId, { room, payload: payloadBody });
        if (isActiveRoom(room)) actions.patchMessageInDom(failed);
        actions.schedulePersistentRoomSave(room);
      }
      actions.setStatus(error.message || "Unable to send message.", "error");
    } finally {
      state.messageSendInFlight = false;
      updateComposerSubmitState();
    }
  }

  async function retryMessage(messageId) {
    const failed = state.failedMessages.get(messageId);
    if (!failed || state.messageSendInFlight) return;
    const cache = actions.cacheFor(failed.room);
    const message = cache?.messages?.find((row) => row.id === messageId);
    if (message) {
      message.delivery_state = "sending";
      actions.patchMessageInDom(message);
    }
    state.messageSendInFlight = true;
    els.sendButton.disabled = true;
    try {
      const response = await fetchJson(actions.currentRoomUrl(failed.room), {
        method: "POST",
        body: JSON.stringify(failed.payload),
      });
      state.failedMessages.delete(messageId);
      actions.removeMessageFromCaches(messageId);
      if (response.message) {
        actions.applyIncomingMessages(failed.room, [response.message], { toBottom: true });
      }
      if (isActiveRoom(failed.room)) {
        extensions.attachments?.clear?.();
        extensions.mediaPicker?.clear?.(true);
      }
    } catch (error) {
      if (message) {
        message.delivery_state = "failed";
        if (isActiveRoom(failed.room)) actions.patchMessageInDom(message);
      }
      actions.setStatus(error.message || "Unable to send message.", "error");
    } finally {
      state.messageSendInFlight = false;
      updateComposerSubmitState();
    }
  }

  function handleComposerKeydown(event) {
    if (event.key !== "Enter" || event.isComposing) return;
    if (event.shiftKey) {
      actions.scheduleTransientTimeout(() => {
        autosizeComposer();
        actions.scheduleTypingPresence();
      }, 0);
      return;
    }
    event.preventDefault();
    if (state.messageSendInFlight) return;
    if (els.composer?.requestSubmit) {
      els.composer.requestSubmit();
    } else {
      void sendActiveMessage(event);
    }
  }

  function bindEvents() {
    els.composer?.addEventListener("submit", sendActiveMessage);
    els.input?.addEventListener("keydown", handleComposerKeydown);
    els.input?.addEventListener("input", () => {
      autosizeComposer();
      actions.scheduleTypingPresence();
      updateComposerSubmitState();
    });
  }

  return {
    autosizeComposer,
    bindEvents,
    retryMessage,
    sendActiveMessage,
    setComposer,
    updateComposerSubmitState,
  };
}
