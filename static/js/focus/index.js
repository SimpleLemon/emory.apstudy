import {
  focusApi,
  formPayload,
  playlistEmbedUrl,
  routineFromState,
  suggestedBreaks,
} from './data.js';
import { clockedSession, remainingSeconds } from './timer.js';
import { createFocusView } from './view.js';

const view = createFocusView({
  savePlayerPreferences: (preferences) => focusApi.savePlayerPreferences(preferences),
  notify: ({ message, type = 'error', title = '' } = {}) => toast(message, type, title),
  onRoutineSelect: (routineId) => {
    const routine = routineFromState(state, routineId);
    view.fillRoutine(routine, { updatePicker: false });
    if (routine?.spotify_url && state.spotifySource?.playlists?.some((playlist) => playlist.spotify_url === routine.spotify_url)) {
      state.spotifySource = localPlaylistSource(state.spotifySource, state.spotifySource.playlists, routine.spotify_url);
    }
    view.renderSpotify(state.spotifySource, state.playlistEntitlements);
    view.setSettingsStatus();
    view.setPlaylistStatus();
    renderSuggestions();
  },
  onRoutineCreate: () => {
    view.fillRoutine(null, { updatePicker: false });
    view.setSettingsStatus();
  },
});
const { elements } = view;
let completionEffects = null;
let completionEffectsPromise = null;
let completionPreparePromise = null;
let disposePlaylistGestures = null;
let playlistGesturesPromise = null;

async function ensureCompletionEffects() {
  if (state.disposed) return null;
  if (completionEffects) return completionEffects;
  completionEffectsPromise ||= import('./completion.js');
  const { createCompletionEffects } = await completionEffectsPromise;
  if (state.disposed) return null;
  completionEffects ||= createCompletionEffects();
  return completionEffects;
}

async function prepareCompletionEffects() {
  completionPreparePromise ||= ensureCompletionEffects().then((effects) => effects?.prepare());
  return completionPreparePromise;
}

async function playCompletionEffects(phase) {
  const effects = await ensureCompletionEffects();
  effects?.complete(phase);
}

async function ensurePlaylistGestures() {
  if (state.disposed || disposePlaylistGestures || !elements.playlistList?.children.length) return;
  playlistGesturesPromise ||= import('./playlist-gestures.js');
  const { bindPlaylistGestures } = await playlistGesturesPromise;
  if (state.disposed || !elements.playlistList?.children.length || disposePlaylistGestures) return;
  disposePlaylistGestures = bindPlaylistGestures(elements.playlistList, {
    onRemove: (url) => { void removePlaylist(url); },
    onSelect: (url) => { void selectPlaylist(url); },
  });
}
const state = {
  routines: [],
  history: [],
  recentSelections: [],
  playerPreferences: null,
  session: null,
  completedSession: null,
  spotifySource: null,
  playlistEntitlements: null,
  timerId: null,
  advanceInFlight: false,
  sessionActionInFlight: false,
  shellActive: false,
  disposed: false,
};

function toast(message, type = 'success', title = '', duration = 3500, action = null) {
  window.APStudyToast?.show?.({
    message,
    type,
    duration,
    ...(title ? { title } : {}),
    ...(action ? { action } : {}),
  });
}

function showError(error, title, fallback = 'Try again in a moment.') {
  toast(error?.message || fallback, 'error', title);
}

function showPlaylistError(error, title, fallback = 'Try again in a moment.') {
  if (error?.code === 'tier_limit') {
    toast(error.message || fallback, 'error', title || 'Playlist limit reached');
    return;
  }
  showError(error, title, fallback);
}

function spotifySourceFromLibrary(playlists = [], activeUrl = '') {
  const list = Array.isArray(playlists) ? playlists : [];
  const url = String(activeUrl || '').trim() || list[0]?.spotify_url || '';
  if (!url && !list.length) return null;
  const active = list.find((playlist) => playlist.spotify_url === url) || list[0] || {};
  const embedUrl = active.embed_url || active.spotify_embed_url || playlistEmbedUrl(url);
  return {
    spotify_url: url,
    spotify_embed_url: embedUrl,
    embed_url: embedUrl,
    playlist_provider: active.provider,
    playlists: list,
  };
}

function applyLibraryResponse(payload) {
  state.playlistEntitlements = payload.playlist_entitlements || state.playlistEntitlements;
  state.spotifySource = spotifySourceFromLibrary(
    payload.playlists,
    payload.spotify_url || payload.active_playlist_url,
  );
  view.renderSpotify(state.spotifySource, state.playlistEntitlements);
  return state.spotifySource;
}

function syncSessionPlaylistSource(session) {
  if (!session) return state.spotifySource;
  state.spotifySource = spotifySourceFromLibrary(session.playlists, session.spotify_url);
  if (state.playlistEntitlements) {
    state.playlistEntitlements = {
      ...state.playlistEntitlements,
      usage: Array.isArray(session.playlists) ? session.playlists.length : state.playlistEntitlements.usage,
    };
  }
  return state.spotifySource;
}

function playlistToast(message, action = null) {
  toast(message, 'success', '', action ? 10_000 : 1000, action);
}

function announceSessionChange(message, title, type = 'info') {
  view.announce(message);
  toast(message, type, title);
}

function announcePhaseTransition(previousPhase, nextSession) {
  if (!nextSession) {
    announceSessionChange('Focus routine complete.', 'Routine complete', 'success');
    return;
  }
  const focusEnded = previousPhase === 'focus';
  const phaseName = focusEnded ? 'Focus' : 'Break';
  const message = focusEnded
    ? (nextSession.state === 'paused' ? 'Focus complete. Your break is ready.' : 'Focus complete. Break started.')
    : (nextSession.state === 'paused' ? 'Break complete. Your next focus is ready.' : 'Break complete. Focus started.');
  announceSessionChange(message, `${phaseName} complete`, nextSession.state === 'paused' ? 'info' : 'success');
}

function selectedRoutine() {
  return routineFromState(state, elements.routineSelect?.value);
}

function userPlaylistSourceFromPayload(payload) {
  const playlists = payload?.playlists;
  if (!Array.isArray(playlists) || !playlists.length) return null;
  const activeUrl = payload.active_playlist_url
    || playlists.find((playlist) => playlist.active)?.spotify_url
    || playlists[0]?.spotify_url
    || '';
  return {
    spotify_url: activeUrl,
    playlists,
  };
}

function applyUserPlaylistResponse(response) {
  state.playlistEntitlements = response.playlist_entitlements || state.playlistEntitlements;
  state.spotifySource = response.playlists?.length
    ? { spotify_url: response.spotify_url || '', playlists: response.playlists }
    : null;
  return state.spotifySource;
}

function hideSidebarForFocus() {
  window.APSTUDY_SET_MOBILE_SIDEBAR_OPEN?.(false);
  window.APSTUDY_SET_SIDEBAR_COLLAPSED?.(true, { persist: false });
  const sidebar = document.querySelector('.sidebar-container');
  if (sidebar) {
    sidebar.setAttribute('aria-hidden', 'true');
    sidebar.inert = true;
  }
}

function focusSidebar(active) {
  if (active) {
    if (!state.shellActive) {
      state.shellActive = true;
      window.APStudyProfileStatus?.setFocusMode?.(true);
    }
    hideSidebarForFocus();
    return;
  }
  const sidebar = document.querySelector('.sidebar-container');
  if (sidebar) {
    sidebar.inert = false;
    sidebar.removeAttribute('aria-hidden');
  }
  state.shellActive = false;
  window.APStudyProfileStatus?.setFocusMode?.(false);
  window.APSTUDY_SET_MOBILE_SIDEBAR_OPEN?.(false);
}

function stopTimer() {
  window.clearTimeout(state.timerId);
  state.timerId = null;
}

function renderSuggestions() {
  view.renderSuggestions(suggestedBreaks(elements.focusMinutes.value, state.recentSelections));
  view.syncTimeSuggestionPressed();
}

function renderSetup() {
  view.renderRoutines(state.routines);
  view.renderRecent(state.recentSelections);
  renderSuggestions();
  view.syncRhythmVisibility();
}

function renderActiveSession() {
  if (!state.session) return;
  syncSessionPlaylistSource(state.session);
  view.renderSession(state.session);
  view.renderSpotify(state.spotifySource, state.playlistEntitlements);
  tick();
}

function renderCompletedSession() {
  if (!state.completedSession) return;
  stopTimer();
  view.renderSession(state.completedSession);
  view.renderTick(state.completedSession, 0);
  view.renderSpotify(state.spotifySource || spotifySourceFromLibrary([], state.completedSession.spotify_url), state.playlistEntitlements);
  document.title = 'Focus complete - Nest';
}

function renderCurrentMode() {
  const activeSession = state.session || state.completedSession;
  const active = Boolean(activeSession);
  view.showMode(active, activeSession);
  view.renderHistory(state.history);
  if (state.session) {
    focusSidebar(true);
    renderActiveSession();
  } else if (state.completedSession) {
    focusSidebar(true);
    renderCompletedSession();
  } else {
    stopTimer();
    document.title = 'Focus Mode - APStudy Nest';
    focusSidebar(false);
    renderSetup();
    view.renderSpotify(state.spotifySource, state.playlistEntitlements);
  }
}

async function loadState() {
  try {
    const payload = await focusApi.state();
    state.routines = payload.routines || [];
    state.history = payload.history || [];
    state.recentSelections = payload.recent_selections || [];
    state.playerPreferences = payload.player_preferences || state.playerPreferences;
    if (state.playerPreferences) view.applyPlayerPreferences(state.playerPreferences);
    state.playlistEntitlements = payload.playlist_entitlements || state.playlistEntitlements;
    state.spotifySource = spotifySourceFromLibrary(payload.playlists, payload.active_playlist_url);
    state.session = clockedSession(payload.active_session);
    if (state.session) state.completedSession = null;
    renderCurrentMode();
  } catch (error) {
    elements.loading.hidden = true;
    elements.setup.hidden = false;
    showError(error, 'Couldn’t load Focus Mode');
  }
}

function scheduleTick() {
  stopTimer();
  if (!state.session || state.session.state !== 'running' || state.disposed) return;
  const delay = 1000 - (Date.now() % 1000) + 20;
  state.timerId = window.setTimeout(tick, delay);
}

async function advancePhase() {
  if (!state.session || state.advanceInFlight || state.sessionActionInFlight) return;
  state.advanceInFlight = true;
  view.setSessionBusy(true);
  stopTimer();
  try {
    const previousPhase = state.session.phase;
    const previousSession = state.session;
    const payload = await focusApi.updateSession(state.session.id, 'advance');
    const next = clockedSession(payload.session);
    view.pauseSpotify();
    void playCompletionEffects(previousPhase);
    if (!payload.active) {
      view.renderTick(state.session, 0);
      await view.playEggOpening(previousPhase);
      state.session = null;
      state.completedSession = { ...previousSession, state: 'completed', remaining_seconds: 0, _clockRemaining: 0 };
      announcePhaseTransition(previousPhase, null);
      renderCurrentMode();
      await refreshHistory();
      return;
    }
    state.session = next;
    view.renderSession(next);
    view.renderTick(next, remainingSeconds(next));
    await view.playEggOpening(previousPhase);
    view.resetEgg();
    view.renderTick(next, remainingSeconds(next));
    announcePhaseTransition(previousPhase, next);
    if (next.phase === 'focus' && next.state === 'running') view.resumeSpotify();
    await refreshHistory();
    scheduleTick();
  } catch (error) {
    toast('The timer will retry when Nest reconnects.', 'error', 'Couldn’t sync this phase');
    state.timerId = window.setTimeout(advancePhase, 15000);
  } finally {
    state.advanceInFlight = false;
    view.setSessionBusy(false);
  }
}

function tick() {
  if (!state.session) return;
  const remaining = remainingSeconds(state.session);
  view.renderTick(state.session, remaining);
  if (state.session.state === 'running' && remaining <= 0) {
    void advancePhase();
    return;
  }
  scheduleTick();
}

async function refreshHistory() {
  try {
    const payload = await focusApi.state();
    state.history = payload.history || [];
    state.recentSelections = payload.recent_selections || [];
    view.renderHistory(state.history);
    view.renderRecent(state.recentSelections);
  } catch (_error) {
    // The next meaningful session action will refresh history again.
  }
}

async function updateSession(action) {
  if (!state.session || state.sessionActionInFlight) return;
  const previousPhase = state.session.phase;
  state.sessionActionInFlight = true;
  const button = action === 'pause' || action === 'resume' ? elements.toggle : elements.completePhase;
  view.setSessionBusy(true, button);
  if (action === 'pause') view.pauseSpotify();
  if (action === 'resume') view.resumeSpotify();
  try {
    const payload = await focusApi.updateSession(state.session.id, action);
    const previousSession = state.session;
    state.session = payload.active ? clockedSession(payload.session) : null;
    if (!state.session) {
      if (action === 'complete_phase') {
        view.renderTick(previousSession, 0);
        view.pauseSpotify();
        void playCompletionEffects(previousPhase);
        await view.playEggOpening(previousPhase);
        state.completedSession = { ...previousSession, state: 'completed', remaining_seconds: 0, _clockRemaining: 0 };
        renderCurrentMode();
        await refreshHistory();
      }
      if (action === 'complete_phase') announcePhaseTransition(previousPhase, null);
      return;
    }
    renderActiveSession();
    if (action === 'complete_phase') {
      view.pauseSpotify();
      void playCompletionEffects(previousPhase);
      await view.playEggOpening(previousPhase);
      view.resetEgg();
      renderActiveSession();
      announcePhaseTransition(previousPhase, state.session);
      if (state.session.phase === 'focus' && state.session.state === 'running') view.resumeSpotify();
      await refreshHistory();
    } else {
      const message = action === 'pause' ? 'Timer paused.' : 'Timer resumed.';
      announceSessionChange(message, action === 'pause' ? 'Timer paused' : 'Timer resumed');
    }
  } catch (error) {
    if (action === 'resume') view.pauseSpotify();
    showError(error, 'Couldn’t update the timer');
  } finally {
    state.sessionActionInFlight = false;
    view.setSessionBusy(false);
  }
}

async function confirmEndSession() {
  if (!state.session) return false;
  if (!window.APStudyConfirm?.request) return window.confirm('End this Focus Mode session?');
  return window.APStudyConfirm.request({
    title: 'End this Focus Mode session?',
    message: 'The unfinished phase will not be added to completion history.',
    acceptLabel: 'End session',
    danger: true,
  });
}

async function endSession() {
  if (!state.session || state.sessionActionInFlight) return;
  state.sessionActionInFlight = true;
  view.setSessionBusy(true, elements.end);
  try {
    if (!(await confirmEndSession())) return;
    view.pauseSpotify();
    await focusApi.updateSession(state.session.id, 'exit');
    state.session = null;
    focusSidebar(false);
    await loadState();
    announceSessionChange('Session ended. Completed phases remain in your history.', 'Focus session ended');
  } catch (error) {
    showError(error, 'Couldn’t end Focus Mode');
  } finally {
    state.sessionActionInFlight = false;
    view.setSessionBusy(false);
  }
}

function exitCompletedSession() {
  state.completedSession = null;
  renderCurrentMode();
}

function applySelection(selection) {
  if (!selection) return;
  elements.focusMinutes.value = selection.focus_minutes;
  elements.breakMinutes.value = selection.break_minutes || 0;
  elements.longBreakMinutes.value = selection.long_break_minutes || selection.break_minutes || 0;
  elements.cycles.value = selection.cycles || 1;
  view.syncRhythmVisibility();
  renderSuggestions();
  if (Object.prototype.hasOwnProperty.call(selection, 'spotify_url')) {
    const url = selection.spotify_url || '';
    if (url && state.spotifySource?.playlists?.some((playlist) => playlist.spotify_url === url)) {
      state.spotifySource = localPlaylistSource(state.spotifySource, state.spotifySource.playlists, url);
    }
    view.renderSpotify(state.spotifySource, state.playlistEntitlements);
  }
}

async function startSession(event) {
  event.preventDefault();
  void prepareCompletionEffects();
  const { routine_id: _routineId, spotify_playlists: _playlists, ...payload } = formPayload(elements.form);
  view.setBusy(true);
  try {
    const response = await focusApi.start(payload);
    state.session = clockedSession(response.session);
    state.completedSession = null;
    syncSessionPlaylistSource(state.session);
    view.setBusy(false);
    renderCurrentMode();
    void view.activateSpotify({ autoplay: true });
    void view.startCountdown();
    announceSessionChange('Focus Mode started. Nonurgent Nest notifications are muted.', 'Focus Mode started');
  } catch (error) {
    view.pauseSpotify();
    view.setBusy(false);
    showError(error, 'Couldn’t start Focus Mode');
  }
}

async function applyPlaylist() {
  const normalized = view.syncPlaylistControls({ clearStatus: true });
  if (!normalized) {
    showError(null, 'Couldn’t add playlist', 'Use a Spotify, YouTube, or YouTube Music playlist URL.');
    elements.spotifyUrl?.focus();
    return;
  }
  view.setPlaylistBusy(true);
  try {
    if (state.session) {
      const response = await focusApi.setPlaylist(state.session.id, normalized);
      state.session = clockedSession(response.session);
      syncSessionPlaylistSource(state.session);
      view.renderSpotify(state.spotifySource, state.playlistEntitlements);
      void view.activateSpotify({ autoplay: state.session.phase === 'focus' && state.session.state === 'running' });
      playlistToast('Playlist added to this session.');
      return;
    }
    const existing = (state.spotifySource?.playlists || []).find((playlist) => playlist.spotify_url === normalized);
    let response = existing
      ? await focusApi.setActivePlaylist(normalized)
      : await focusApi.addPlaylist(normalized);
    if (!existing && response.spotify_url !== normalized) {
      response = await focusApi.setActivePlaylist(normalized);
    }
    applyLibraryResponse(response);
    void view.activateSpotify({ autoplay: false });
    playlistToast(existing ? 'Playlist selected.' : 'Playlist added.');
  } catch (error) {
    showPlaylistError(error, 'Couldn’t add playlist');
  } finally {
    view.setPlaylistBusy(false);
  }
}

function playlistByUrl(source, url) {
  return (source?.playlists || []).find((playlist) => playlist.spotify_url === url) || null;
}

function localPlaylistSource(source, playlists, activeUrl) {
  if (!playlists.length) return null;
  const active = playlistByUrl({ playlists }, activeUrl) || playlists[0];
  return {
    ...source,
    spotify_url: active.spotify_url,
    spotify_embed_url: active.embed_url || active.spotify_embed_url || playlistEmbedUrl(active.spotify_url),
    embed_url: active.embed_url || active.spotify_embed_url || playlistEmbedUrl(active.spotify_url),
    playlist_provider: active.provider,
    playlists,
  };
}

async function restorePlaylist(record) {
  view.setPlaylistBusy(true);
  try {
    if (state.session) {
      const response = await focusApi.restorePlaylist(state.session.id, record.playlist.spotify_url, record.activeUrl);
      state.session = clockedSession(response.session);
      syncSessionPlaylistSource(state.session);
      view.renderSpotify(state.spotifySource, state.playlistEntitlements);
      void view.activateSpotify({ autoplay: state.session.phase === 'focus' && state.session.state === 'running' });
    } else {
      let response = await focusApi.addPlaylist(record.playlist.spotify_url);
      if (record.activeUrl && response.spotify_url !== record.activeUrl) {
        response = await focusApi.setActivePlaylist(record.activeUrl);
      }
      applyLibraryResponse(response);
    }
    playlistToast('Playlist restored.');
  } catch (error) {
    showPlaylistError(error, 'Couldn’t restore playlist');
  } finally {
    view.setPlaylistBusy(false);
  }
}

async function removePlaylist(playlistUrl = '') {
  const source = state.spotifySource || state.session || {};
  const targetUrl = playlistUrl || source.spotify_url;
  const playlists = Array.isArray(source.playlists) ? source.playlists : [];
  const index = playlists.findIndex((playlist) => playlist.spotify_url === targetUrl);
  const playlist = index >= 0 ? playlists[index] : {
    spotify_url: targetUrl,
    title: 'Playlist',
    creator: 'Music',
  };
  if (!targetUrl) return;
  const record = { playlist, index: Math.max(0, index), activeUrl: source.spotify_url, source };
  view.setPlaylistBusy(true);
  try {
    if (state.session) {
      const response = await focusApi.removeSessionPlaylist(state.session.id, targetUrl);
      state.session = clockedSession(response.session);
      syncSessionPlaylistSource(state.session);
      view.renderSpotify(state.spotifySource, state.playlistEntitlements);
    } else {
      const response = await focusApi.removePlaylist(targetUrl);
      applyLibraryResponse(response);
    }
    playlistToast(`${playlist.title || 'Playlist'} removed.`, {
      label: 'Undo',
      onClick: () => { void restorePlaylist(record); },
    });
  } catch (error) {
    showPlaylistError(error, 'Couldn’t remove playlist');
  } finally {
    view.setPlaylistBusy(false);
  }
}

async function selectPlaylist(spotifyUrl) {
  const current = state.session || state.spotifySource;
  if (!current || current.spotify_url === spotifyUrl) return;
  view.setPlaylistBusy(true);
  try {
    if (state.session) {
      const response = await focusApi.setPlaylist(state.session.id, spotifyUrl);
      state.session = clockedSession(response.session);
      syncSessionPlaylistSource(state.session);
      view.renderSpotify(state.spotifySource, state.playlistEntitlements);
      void view.activateSpotify({ autoplay: state.session.phase === 'focus' && state.session.state === 'running' });
    } else {
      const response = await focusApi.setActivePlaylist(spotifyUrl);
      applyLibraryResponse(response);
    }
    playlistToast('Playlist selected.');
  } catch (error) {
    showPlaylistError(error, 'Couldn’t select playlist');
  } finally {
    view.setPlaylistBusy(false);
  }
}

function payloadFromRoutine(routine) {
  return {
    routine_id: routine.id,
    name: routine.name,
    focus_minutes: routine.focus_minutes,
    break_minutes: routine.break_minutes || 0,
    long_break_minutes: routine.long_break_minutes || routine.break_minutes || 0,
    cycles: routine.cycles || 1,
    spotify_url: routine.spotify_url || '',
  };
}

async function undoRoutineSave(record) {
  try {
    if (record.previous) {
      const response = await focusApi.saveRoutine(payloadFromRoutine(record.previous), record.saved.id);
      const index = state.routines.findIndex((routine) => routine.id === record.saved.id);
      if (index >= 0) state.routines[index] = response.routine;
      view.renderRoutines(state.routines, response.routine.id);
      view.fillRoutine(response.routine);
      if (response.routine.spotify_url && state.spotifySource?.playlists?.some((playlist) => playlist.spotify_url === response.routine.spotify_url)) {
        state.spotifySource = localPlaylistSource(state.spotifySource, state.spotifySource.playlists, response.routine.spotify_url);
      }
      view.renderSpotify(state.spotifySource, state.playlistEntitlements);
    } else {
      await focusApi.deleteRoutine(record.saved.id);
      state.routines = state.routines.filter((routine) => routine.id !== record.saved.id);
      view.renderRoutines(state.routines);
      view.fillRoutine(null);
      state.spotifySource = null;
      view.renderSpotify(null, state.playlistEntitlements);
      renderSuggestions();
    }
    view.setSettingsStatus('Saved setup restored.', 'success');
    playlistToast('Focus setup restored.');
  } catch (error) {
    showError(error, 'Couldn’t undo the save');
  }
}

async function saveRoutine() {
  const { routine_id: _routineId, spotify_playlists: _playlists, ...payload } = formPayload(elements.form);
  const selectedId = elements.routineSelect.value;
  const creating = !selectedId;
  if (creating && !elements.routineName.value.trim()) {
    showError(null, 'Couldn’t save focus setup', 'Enter a setup name before saving.');
    elements.routineName.focus();
    return;
  }
  if (!creating) {
    const routine = selectedRoutine();
    if (routine) elements.routineName.value = routine.name;
  }
  elements.saveRoutines.forEach((button) => { button.disabled = true; });
  try {
    const previous = selectedId ? selectedRoutine() : null;
    const previousSnapshot = previous ? {
      ...previous,
      playlists: (previous.playlists || []).map((playlist) => ({ ...playlist })),
    } : null;
    const response = await focusApi.saveRoutine(payload, selectedId);
    const index = state.routines.findIndex((routine) => routine.id === response.routine.id);
    if (index >= 0) state.routines[index] = response.routine;
    else state.routines.unshift(response.routine);
    view.renderRoutines(state.routines, response.routine.id);
    if (response.routine.spotify_url && state.spotifySource?.playlists?.some((playlist) => playlist.spotify_url === response.routine.spotify_url)) {
      state.spotifySource = localPlaylistSource(state.spotifySource, state.spotifySource.playlists, response.routine.spotify_url);
    }
    view.renderSpotify(state.spotifySource, state.playlistEntitlements);
    const setupName = response.routine.name;
    view.setSettingsStatus(
      previousSnapshot ? `Changes saved to “${setupName}”.` : `“${setupName}” saved as a new setup.`,
      'success',
    );
    toast(
      previousSnapshot ? 'This focus setup was updated.' : 'A new focus setup was created.',
      'success',
      previousSnapshot ? `Changes saved to “${setupName}”` : `“${setupName}” saved`,
      10_000,
      { label: 'Undo', onClick: () => { void undoRoutineSave({ previous: previousSnapshot, saved: response.routine }); } },
    );
  } catch (error) {
    showError(error, 'Couldn’t save focus setup');
  } finally {
    elements.saveRoutines.forEach((button) => { button.disabled = false; });
  }
}

async function deleteRoutine() {
  const routine = selectedRoutine();
  if (!routine) return;
  const accepted = window.APStudyConfirm?.request
    ? await window.APStudyConfirm.request({
      title: `Delete “${routine.name}”?`,
      message: 'Completion history will stay intact.',
      acceptLabel: 'Delete routine',
      danger: true,
    })
    : window.confirm(`Delete “${routine.name}”?`);
  if (!accepted) return;
  const routineIndex = state.routines.findIndex((item) => item.id === routine.id);
  const previousSpotifySource = state.spotifySource;
  state.routines = state.routines.filter((item) => item.id !== routine.id);
  view.renderRoutines(state.routines);
  view.fillRoutine(null);
  view.renderSpotify(null, state.playlistEntitlements);
  state.spotifySource = null;
  renderSuggestions();
  view.setSettingsStatus('Routine deleted.');
  if (window.APStudyUndo?.stage) {
    window.APStudyUndo.stage({
      message: `“${routine.name}” deleted.`,
      commit: ({ reason }) => focusApi.deleteRoutine(routine.id, { keepalive: reason === 'pagehide' }),
      restore: () => {
        if (!state.routines.some((item) => item.id === routine.id)) {
          state.routines.splice(Math.min(Math.max(0, routineIndex), state.routines.length), 0, routine);
        }
        if (!state.spotifySource) state.spotifySource = previousSpotifySource;
        view.renderRoutines(state.routines, routine.id);
        view.fillRoutine(routine);
        view.renderSpotify(state.spotifySource, state.playlistEntitlements);
        renderSuggestions();
        view.setSettingsStatus('Routine restored.', 'success');
      },
      errorTitle: 'Couldn’t delete focus setup',
    });
    return;
  }
  try {
    await focusApi.deleteRoutine(routine.id);
  } catch (error) {
    if (!state.routines.some((item) => item.id === routine.id)) {
      state.routines.splice(Math.min(Math.max(0, routineIndex), state.routines.length), 0, routine);
    }
    if (!state.spotifySource) state.spotifySource = previousSpotifySource;
    view.renderRoutines(state.routines, routine.id);
    view.fillRoutine(routine);
    view.renderSpotify(state.spotifySource, state.playlistEntitlements);
    renderSuggestions();
    showError(error, 'Couldn’t delete focus setup');
  }
}

function bindEvents() {
  const eventController = new AbortController();
  const listenerOptions = { signal: eventController.signal };
  elements.form.addEventListener('submit', startSession, listenerOptions);
  elements.form.querySelector('button[type="submit"]')?.addEventListener('pointerdown', () => {
    void prepareCompletionEffects();
  }, listenerOptions);
  elements.optionsOpen.forEach((button) => button.addEventListener('click', () => view.openOptions(button), listenerOptions));
  elements.focusMinutes.addEventListener('input', renderSuggestions, listenerOptions);
  elements.cycles.addEventListener('input', () => {
    renderSuggestions();
    view.syncRhythmVisibility();
  }, listenerOptions);
  elements.form.addEventListener('click', (event) => {
    const preset = event.target.closest('[data-focus-preset]');
    if (!preset) return;
    const focusMinutes = Number(preset.dataset.focus);
    const breakMinutes = Number(preset.dataset.break) || 0;
    const cycles = Number(preset.dataset.cycles) || 1;
    const recentMatch = state.recentSelections.find((selection) => (
      Number(selection.focus_minutes) === focusMinutes
      && (Number(selection.break_minutes) || 0) === breakMinutes
      && (Number(selection.cycles) || 1) === cycles
    ));
    applySelection(recentMatch || {
      focus_minutes: focusMinutes,
      break_minutes: breakMinutes,
      long_break_minutes: breakMinutes,
      cycles,
    });
  }, listenerOptions);
  elements.suggestions?.addEventListener('click', (event) => {
    const button = event.target.closest('[data-break-suggestion]');
    if (!button) return;
    elements.breakMinutes.value = button.dataset.breakSuggestion;
    if (!Number(elements.longBreakMinutes.value)) elements.longBreakMinutes.value = button.dataset.breakSuggestion;
  }, listenerOptions);
  elements.layoutInputs.forEach((input) => input.addEventListener('change', () => {
    if (input.checked) view.setSpotifyLayout(input.value);
  }, listenerOptions));
  elements.spotifyUrl?.addEventListener('input', () => view.syncPlaylistControls({ clearStatus: true }), listenerOptions);
  elements.playlistToggle?.addEventListener('click', () => {
    const open = elements.playlistToggle.getAttribute('aria-expanded') !== 'true';
    view.setPlaylistEditor(open);
    if (!open) view.setPlaylistStatus();
  }, listenerOptions);
  elements.playlistApply?.addEventListener('click', () => { void applyPlaylist(); }, listenerOptions);
  elements.playlistRemove?.addEventListener('click', (event) => {
    if (document.body.classList.contains('focus-session-active')) return;
    const coarsePointer = window.matchMedia?.('(hover: none), (pointer: coarse)').matches;
    if (event.detail > 0 && coarsePointer && !elements.playerFrame?.classList.contains('is-actions-visible')) {
      elements.playerFrame?.classList.add('is-actions-visible');
      return;
    }
    void removePlaylist();
  }, listenerOptions);
  elements.spotifyEmbed?.addEventListener('click', (event) => {
    if (!event.target.closest('[data-focus-player-load]')) return;
    void view.activateSpotify({ autoplay: false });
  }, listenerOptions);
  elements.spotifyUrl?.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') {
      event.preventDefault();
      view.setPlaylistEditor(false);
      if (elements.playlistToggle?.getAttribute('aria-expanded') !== 'true') {
        elements.playlistToggle.focus();
      }
      return;
    }
    if (event.key !== 'Enter') return;
    event.preventDefault();
    if (!elements.playlistApply.disabled) void applyPlaylist();
  }, listenerOptions);
  document.addEventListener('pointerdown', (event) => {
    if (elements.playlistToggle?.getAttribute('aria-expanded') === 'true'
        && !elements.playlistComposer?.contains(event.target)) {
      view.setPlaylistEditor(false);
    }
    if (!elements.playerFrame?.contains(event.target)) {
      elements.playerFrame?.classList.remove('is-actions-visible');
    }
  }, listenerOptions);
  elements.historyRegion?.addEventListener('toggle', () => {
    if (elements.historyRegion.open) void view.mountHistory();
  }, listenerOptions);
  elements.saveRoutines.forEach((button) => button.addEventListener('click', saveRoutine, listenerOptions));
  elements.deleteRoutine.addEventListener('click', deleteRoutine, listenerOptions);
  elements.toggle.addEventListener('click', () => updateSession(state.session?.state === 'paused' ? 'resume' : 'pause'), listenerOptions);
  elements.completePhase.addEventListener('click', () => updateSession('complete_phase'), listenerOptions);
  elements.end.addEventListener('click', () => {
    if (state.completedSession) exitCompletedSession();
    else void endSession();
  }, listenerOptions);
  document.addEventListener('focus:playlist-list-rendered', (event) => {
    if (event.detail?.hasItems) void ensurePlaylistGestures();
  }, listenerOptions);
  document.addEventListener('apstudy-sidebar-state-change', (event) => {
    if (state.session && event.detail?.collapsed === false) queueMicrotask(hideSidebarForFocus);
  }, listenerOptions);
  document.addEventListener('apstudy-mobile-sidebar-toggle', () => {
    if (state.session) queueMicrotask(hideSidebarForFocus);
  }, listenerOptions);
  document.addEventListener('visibilitychange', () => {
    if (state.completedSession) return;
    if (document.hidden) tick();
    else void loadState();
  }, listenerOptions);
  window.addEventListener('online', () => {
    if (state.session && remainingSeconds(state.session) <= 0) void advancePhase();
  }, listenerOptions);
  window.APStudyPageLifecycle?.register?.({
    pause: stopTimer,
    resume: tick,
    dispose: () => {
      state.disposed = true;
      stopTimer();
      eventController.abort();
      disposePlaylistGestures?.();
      disposePlaylistGestures = null;
      completionEffects?.dispose?.();
      view.dispose();
    },
  });
}

bindEvents();
void loadState();
