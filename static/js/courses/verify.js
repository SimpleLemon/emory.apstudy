/**
 * Courses verify controller.
 * State, signatures, lifecycle, status verification networking, live seat
 * details, and effective availability resolution.
 *
 * Status verification splits candidates into sequential requests that each
 * stay within the backend contract (max 120 section ids, max 24 distinct
 * term-subject groups per request). Detail requests are serialized through a
 * queue so overlapping scroll hydration never clobbers controllers or
 * promises. Detail-level failures (detail_errors_by_id, errors_by_id during a
 * detail call) and transport or HTTP failures of a detail request keep the
 * already verified status and phase: they only clear seat details, record a
 * detail error, and mark the detail attempted so it is never refetched.
 */
(function () {
  'use strict';

  var MAX_CANDIDATES = 120;
  var MAX_GROUPS_PER_REQUEST = 24;
  var MAX_DETAIL_IDS = 12;
  var DEFAULT_STATUS_ENDPOINT = '/api/atlas/sections/verify';
  var MISSING_AVAILABILITY_MESSAGE = 'Live availability was not returned by Atlas.';
  var VERIFICATION_FAILURE_MESSAGE = 'Unable to verify live availability.';
  var DETAILS_MISSING_MESSAGE = 'Live seat details were not returned by Atlas.';
  var DETAILS_FAILURE_MESSAGE = 'Unable to verify live seat details.';
  var ERROR_SNIPPET_LIMIT = 160;
  var PHASES = {
    UNVERIFIED: 'unverified',
    PENDING: 'pending',
    VERIFIED: 'verified',
    UNAVAILABLE: 'unavailable'
  };
  var STATUS_LABELS = { open: 'Open', closed: 'Closed', waitlist: 'Waitlist' };

  function normalizeText(value) {
    return typeof value === 'string' ? value.trim().toLowerCase() : '';
  }

  function normalizeEnrollmentStatus(value) {
    var text = normalizeText(value);
    if (text === 'o') return 'open';
    if (text === 'c') return 'closed';
    if (text === 'w') return 'waitlist';
    return text;
  }

  function isOpenWaitlistStatus(value) {
    var status = normalizeEnrollmentStatus(value);
    return status === 'open' || status === 'waitlist';
  }

  function statusLabel(normalized) {
    return STATUS_LABELS[normalized] || null;
  }

  function numericOrNull(value) {
    if (typeof value === 'number' && isFinite(value)) return value;
    if (typeof value === 'string' && value.trim() !== '' && isFinite(Number(value))) {
      return Number(value);
    }
    return null;
  }

  function toErrorShape(value) {
    if (!value) return null;
    if (typeof value === 'string') return { message: value };
    if (typeof value === 'object') return value;
    return { message: String(value) };
  }

  function uniqueSorted(values) {
    var seen = new Set();
    var out = [];
    (Array.isArray(values) ? values : []).forEach(function (value) {
      var normalized = normalizeText(value);
      if (!normalized || seen.has(normalized)) return;
      seen.add(normalized);
      out.push(normalized);
    });
    return out.sort();
  }

  function buildQuerySignature(input) {
    var source = input && typeof input === 'object' ? input : {};
    var timeEnabled = Boolean(source.timeEnabled);
    var signature = {
      term: normalizeText(source.term),
      query: typeof source.query === 'string' ? source.query.trim() : '',
      days: uniqueSorted(source.days),
      timeEnabled: timeEnabled,
      campus: normalizeText(source.campus),
      requirement: normalizeText(source.requirement)
    };
    if (timeEnabled) {
      signature.timeStart = normalizeText(source.timeStart);
      signature.timeEnd = normalizeText(source.timeEnd);
    }
    return JSON.stringify(signature);
  }

  function dedupeIds(ids, max) {
    var limit = typeof max === 'number' && max > 0 ? max : MAX_CANDIDATES;
    var seen = new Set();
    var out = [];
    (Array.isArray(ids) ? ids : []).some(function (id) {
      if (id === null || id === undefined || id === '' || seen.has(id)) return false;
      seen.add(id);
      out.push(id);
      return out.length >= limit;
    });
    return out;
  }

  function buildCandidateSignature(ids) {
    return JSON.stringify(dedupeIds(ids, MAX_CANDIDATES));
  }

  function clonePlain(value) {
    if (value && typeof value === 'object' && !Array.isArray(value)) {
      return Object.assign({}, value);
    }
    return value;
  }

  function hasOwnKey(source, key) {
    return Object.prototype.hasOwnProperty.call(source, key);
  }

  function idKeyedMap(value) {
    return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
  }

  function cloneRowMap(source) {
    var out = new Map();
    source.forEach(function (value, key) {
      out.set(key, clonePlain(value));
    });
    return out;
  }

  function sectionGroupKey(id) {
    var parts = String(id === null || id === undefined ? '' : id).split('|');
    if (parts.length < 2) return null;
    var term = parts[0].trim();
    var subject = parts[1].trim().toUpperCase();
    if (!term || !subject) return null;
    return term + '|' + subject;
  }

  function buildStatusChunks(ids) {
    var chunks = [];
    var currentIds = [];
    var currentGroupCount = 0;
    var seenGroups = new Set();
    ids.forEach(function (id) {
      var key = sectionGroupKey(id);
      if (key && !seenGroups.has(key) && currentGroupCount >= MAX_GROUPS_PER_REQUEST) {
        chunks.push(currentIds);
        currentIds = [];
        currentGroupCount = 0;
        seenGroups = new Set();
      }
      if (key && !seenGroups.has(key)) {
        seenGroups.add(key);
        currentGroupCount += 1;
      }
      currentIds.push(id);
    });
    if (currentIds.length) chunks.push(currentIds);
    return chunks;
  }

  function create(options) {
    options = options || {};

    var endpoints = {
      status: typeof options.statusEndpoint === 'string' ? options.statusEndpoint : '',
      details: typeof options.detailsEndpoint === 'string' ? options.detailsEndpoint : ''
    };
    var fetchImpl = typeof options.fetchImpl === 'function' ? options.fetchImpl : null;
    var onStatusProgress = typeof options.onStatusProgress === 'function' ? options.onStatusProgress : null;

    var state = {
      generation: 0,
      querySignature: '',
      candidateSignature: '',
      candidateIds: [],
      phases: new Map(),
      verified: new Map(),
      details: new Map(),
      detailErrors: new Map(),
      errors: new Map(),
      statusAbort: null,
      detailsAbort: null,
      statusPromise: null,
      detailsPromise: null,
      detailsChain: null,
      detailedIds: new Set(),
      detailInflightIds: new Set()
    };

    function abortController(controller) {
      if (controller && typeof controller.abort === 'function') controller.abort();
    }

    function abortActive() {
      abortController(state.statusAbort);
      abortController(state.detailsAbort);
      state.statusAbort = null;
      state.detailsAbort = null;
      state.statusPromise = null;
      state.detailsPromise = null;
    }

    function clearSections() {
      state.phases = new Map();
      state.verified = new Map();
      state.details = new Map();
      state.detailErrors = new Map();
      state.errors = new Map();
      state.detailedIds = new Set();
      state.detailInflightIds = new Set();
    }

    function getSectionState(id) {
      if (id === null || id === undefined || id === '') return null;
      return {
        phase: state.phases.get(id) || PHASES.UNVERIFIED,
        verified: state.verified.has(id) ? clonePlain(state.verified.get(id)) : null,
        detail: state.details.has(id) ? clonePlain(state.details.get(id)) : null,
        detailError: state.detailErrors.has(id) ? clonePlain(state.detailErrors.get(id)) : null,
        error: state.errors.has(id) ? clonePlain(state.errors.get(id)) : null
      };
    }

    function getState() {
      return {
        generation: state.generation,
        querySignature: state.querySignature,
        candidateSignature: state.candidateSignature,
        candidateIds: state.candidateIds.slice(),
        phases: new Map(state.phases),
        verified: cloneRowMap(state.verified),
        details: cloneRowMap(state.details),
        detailErrors: cloneRowMap(state.detailErrors),
        errors: cloneRowMap(state.errors),
        detailedIds: new Set(state.detailedIds),
        detailInflightIds: new Set(state.detailInflightIds)
      };
    }

    function reset() {
      abortActive();
      state.generation += 1;
      state.querySignature = '';
      state.candidateSignature = '';
      state.candidateIds = [];
      state.detailsChain = null;
      clearSections();
    }

    function beginGeneration(payload) {
      payload = payload || {};
      var sectionIds = dedupeIds(payload.sectionIds, MAX_CANDIDATES);
      var querySignature = buildQuerySignature(payload.queryInput);
      var candidateSignature = buildCandidateSignature(sectionIds);
      var changed = querySignature !== state.querySignature ||
        candidateSignature !== state.candidateSignature;

      if (!changed) {
        return { changed: false, generation: state.generation, sectionIds: state.candidateIds.slice() };
      }

      abortActive();
      state.generation += 1;
      state.querySignature = querySignature;
      state.candidateSignature = candidateSignature;
      state.candidateIds = sectionIds;
      state.detailsChain = null;
      clearSections();
      sectionIds.forEach(function (id) {
        state.phases.set(id, PHASES.PENDING);
      });

      return { changed: true, generation: state.generation, sectionIds: sectionIds.slice() };
    }

    function notifyStatusProgress() {
      if (onStatusProgress) onStatusProgress();
    }

    function markUnavailable(ids, message) {
      ids.forEach(function (id) {
        state.errors.set(id, { message: message });
        state.phases.set(id, PHASES.UNAVAILABLE);
      });
    }

    function performFetch(url, options) {
      if (fetchImpl) return fetchImpl(url, options);
      return window.fetch(url, options);
    }

    function readErrorResponse(response) {
      var read = typeof response.text === 'function' ? response.text() : Promise.resolve('');
      return Promise.resolve(read)
        .catch(function () { return ''; })
        .then(function (text) {
          var snippet = typeof text === 'string'
            ? text.replace(/\s+/g, ' ').trim().slice(0, ERROR_SNIPPET_LIMIT)
            : '';
          var error = new Error('Verification request failed with status ' + response.status +
            (snippet ? ': ' + snippet : ''));
          error.name = 'VerifyStatusHttpError';
          error.statusCode = response.status;
          throw error;
        });
    }

    function parseStatusResponse(response) {
      if (!response || typeof response !== 'object' || typeof response.json !== 'function') {
        throw new Error('Verification request returned an invalid response.');
      }
      if (!response.ok) return readErrorResponse(response);
      return response.json().then(function (payload) {
        if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
          throw new Error('Verification response was not a JSON object.');
        }
        return payload;
      });
    }

    function startQuery(payload) {
      payload = payload || {};
      var result = beginGeneration(payload);

      if (!result.changed) {
        return state.statusPromise || Promise.resolve(getState());
      }

      var sectionIds = result.sectionIds.slice();
      if (!sectionIds.length) return Promise.resolve(getState());

      var captured = {
        generation: result.generation,
        querySignature: state.querySignature,
        candidateSignature: state.candidateSignature,
        sectionIds: sectionIds
      };

      function isCurrent() {
        return state.generation === captured.generation &&
          state.querySignature === captured.querySignature &&
          state.candidateSignature === captured.candidateSignature;
      }

      function applyChunk(ids, payload) {
        if (!isCurrent()) return;
        payload = payload || {};
        var verifiedById = idKeyedMap(payload.verified_by_id);
        var errorsById = idKeyedMap(payload.errors_by_id);
        ids.forEach(function (id) {
          if (hasOwnKey(verifiedById, id) && verifiedById[id]) {
            state.verified.set(id, clonePlain(verifiedById[id]));
            state.phases.set(id, PHASES.VERIFIED);
            state.errors.delete(id);
            return;
          }
          if (hasOwnKey(errorsById, id)) {
            state.errors.set(id, clonePlain(errorsById[id]) || { message: MISSING_AVAILABILITY_MESSAGE });
            state.phases.set(id, PHASES.UNAVAILABLE);
            return;
          }
          state.errors.set(id, { message: MISSING_AVAILABILITY_MESSAGE });
          state.phases.set(id, PHASES.UNAVAILABLE);
        });
      }

      function runChunk(ids) {
        var AbortCtor = typeof window.AbortController === 'function' ? window.AbortController : null;
        var requestAbort = AbortCtor ? new AbortCtor() : null;
        var requestOptions = {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ section_ids: ids })
        };
        if (requestAbort) {
          requestOptions.signal = requestAbort.signal;
          state.statusAbort = requestAbort;
        }

        return Promise.resolve()
          .then(function () {
            return performFetch(endpoints.status || DEFAULT_STATUS_ENDPOINT, requestOptions);
          })
          .then(parseStatusResponse)
          .then(function (payload) {
            applyChunk(ids, payload);
          })
          .catch(function (error) {
            if (isCurrent() && !(error && error.name === 'AbortError')) {
              markUnavailable(ids, VERIFICATION_FAILURE_MESSAGE);
            }
          })
          .then(function () {
            if (state.statusAbort === requestAbort) state.statusAbort = null;
          });
      }

      var chunks = buildStatusChunks(sectionIds);
      var promise = chunks.reduce(function (chain, ids) {
        return chain.then(function () {
          if (!isCurrent()) return getState();
          return runChunk(ids).then(function () {
            if (isCurrent()) notifyStatusProgress();
            return getState();
          });
        });
      }, Promise.resolve()).then(function (value) {
        if (state.statusPromise === promise) state.statusPromise = null;
        return value;
      }, function (error) {
        if (state.statusPromise === promise) state.statusPromise = null;
        throw error;
      });

      state.statusPromise = promise;
      state.statusAbort = null;
      return promise;
    }

    function selectDetailCandidates(sectionIds) {
      var seen = new Set();
      var out = [];
      (Array.isArray(sectionIds) ? sectionIds : []).some(function (id) {
        if (out.length >= MAX_DETAIL_IDS) return true;
        if (id === null || id === undefined || id === '' || seen.has(id)) return false;
        seen.add(id);
        if (state.phases.get(id) !== PHASES.VERIFIED) return false;
        if (state.detailedIds.has(id) || state.detailInflightIds.has(id)) return false;
        var row = state.verified.get(id);
        if (!row || typeof row !== 'object' || Array.isArray(row)) return false;
        if (!isOpenWaitlistStatus(row.enrollment_status)) return false;
        out.push(id);
        return false;
      });
      return out;
    }

    function markDetailFailed(id, error) {
      state.details.delete(id);
      state.detailedIds.add(id);
      state.detailErrors.set(id, error || { message: DETAILS_FAILURE_MESSAGE });
    }

    function executeDetailRequest(captured, isCurrent, releaseInflight) {
      var selectedIds = captured.selectedIds;
      var AbortCtor = typeof window.AbortController === 'function' ? window.AbortController : null;
      var requestAbort = AbortCtor ? new AbortCtor() : null;
      var requestOptions = {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          section_ids: selectedIds.slice(),
          detail_ids: selectedIds.slice()
        })
      };
      if (requestAbort) {
        requestOptions.signal = requestAbort.signal;
        state.detailsAbort = requestAbort;
      }

      function applyDetailPayload(payload) {
        if (!isCurrent()) return getState();
        payload = payload || {};
        var verifiedById = idKeyedMap(payload.verified_by_id);
        var errorsById = idKeyedMap(payload.errors_by_id);
        var detailsById = idKeyedMap(payload.details_by_id);
        var detailErrorsById = idKeyedMap(payload.detail_errors_by_id);
        selectedIds.forEach(function (id) {
          if (hasOwnKey(verifiedById, id) && verifiedById[id]) {
            state.verified.set(id, clonePlain(verifiedById[id]));
            state.errors.delete(id);
            state.phases.set(id, PHASES.VERIFIED);
          } else if (hasOwnKey(errorsById, id)) {
            markDetailFailed(id, clonePlain(errorsById[id]) || { message: MISSING_AVAILABILITY_MESSAGE });
            return;
          }
          var detail = detailsById[id];
          if (hasOwnKey(detailsById, id) && detail && typeof detail === 'object' && !Array.isArray(detail)) {
            state.details.set(id, clonePlain(detail));
            state.detailErrors.delete(id);
            state.errors.delete(id);
            state.detailedIds.add(id);
            state.phases.set(id, PHASES.VERIFIED);
            return;
          }
          state.details.delete(id);
          state.detailedIds.add(id);
          if (hasOwnKey(detailErrorsById, id) && detailErrorsById[id]) {
            state.detailErrors.set(id, clonePlain(detailErrorsById[id]));
          } else {
            state.detailErrors.set(id, { message: DETAILS_MISSING_MESSAGE });
          }
          state.phases.set(id, PHASES.VERIFIED);
        });
        return getState();
      }

      function handleFailure(error) {
        if (!isCurrent()) return getState();
        if (error && error.name === 'AbortError') return getState();
        selectedIds.forEach(function (id) {
          markDetailFailed(id, { message: DETAILS_FAILURE_MESSAGE });
        });
        return getState();
      }

      return Promise.resolve()
        .then(function () {
          return performFetch(endpoints.details || DEFAULT_STATUS_ENDPOINT, requestOptions);
        })
        .then(parseStatusResponse)
        .then(applyDetailPayload)
        .catch(handleFailure)
        .then(function (value) {
          releaseInflight();
          if (state.detailsAbort === requestAbort) state.detailsAbort = null;
          return value;
        }, function (error) {
          releaseInflight();
          if (state.detailsAbort === requestAbort) state.detailsAbort = null;
          throw error;
        });
    }

    function requestDetails(sectionIds) {
      var selected = selectDetailCandidates(sectionIds);

      if (!selected.length) {
        return state.detailsPromise || Promise.resolve(getState());
      }

      selected.forEach(function (id) {
        state.detailInflightIds.add(id);
      });

      var captured = {
        generation: state.generation,
        querySignature: state.querySignature,
        candidateSignature: state.candidateSignature,
        selectedIds: selected
      };

      function isCurrent() {
        return state.generation === captured.generation &&
          state.querySignature === captured.querySignature &&
          state.candidateSignature === captured.candidateSignature;
      }

      function releaseInflight() {
        captured.selectedIds.forEach(function (id) {
          state.detailInflightIds.delete(id);
        });
      }

      var run = (state.detailsChain || Promise.resolve()).then(function () {
        if (!isCurrent()) {
          releaseInflight();
          return getState();
        }
        return executeDetailRequest(captured, isCurrent, releaseInflight);
      });

      var promise = run.then(function (value) {
        if (state.detailsPromise === promise) state.detailsPromise = null;
        return value;
      }, function (error) {
        if (state.detailsPromise === promise) state.detailsPromise = null;
        throw error;
      });

      state.detailsChain = run.then(function () {}, function () {});
      state.detailsPromise = promise;
      return promise;
    }

    function getEffectiveAvailability(section) {
      var id = section && typeof section === 'object' ? section.id : null;
      var sectionState = getSectionState(id) || {
        phase: PHASES.UNVERIFIED,
        verified: null,
        detail: null,
        detailError: null,
        error: null
      };

      if (sectionState.phase === PHASES.PENDING) {
        return {
          phase: PHASES.PENDING,
          status: 'Checking',
          seatsAvailable: null,
          waitlistTotal: null,
          capacity: null,
          current: false,
          detailsPending: false,
          error: null,
          source: null
        };
      }

      if (sectionState.phase === PHASES.UNAVAILABLE) {
        return {
          phase: PHASES.UNAVAILABLE,
          status: 'Unavailable',
          seatsAvailable: null,
          waitlistTotal: null,
          capacity: null,
          current: false,
          detailsPending: false,
          error: toErrorShape(sectionState.error),
          source: null
        };
      }

      if (sectionState.phase === PHASES.VERIFIED) {
        var row = sectionState.detail || sectionState.verified;
        var status = normalizeEnrollmentStatus(row ? row.enrollment_status : null);
        var seats = null;
        if (status === 'closed') {
          seats = 0;
        } else if (sectionState.detail) {
          seats = numericOrNull(row ? row.seats_available : null);
        }
        return {
          phase: PHASES.VERIFIED,
          status: statusLabel(status),
          seatsAvailable: seats,
          waitlistTotal: row ? numericOrNull(row.waitlist_total) : null,
          capacity: row ? numericOrNull(row.waitlist_capacity) : null,
          current: true,
          detailsPending: id !== null && state.detailInflightIds.has(id),
          error: id !== null && state.detailErrors.has(id)
            ? toErrorShape(state.detailErrors.get(id))
            : null,
          source: 'live'
        };
      }

      var catalogStatus = normalizeEnrollmentStatus(
        section && typeof section === 'object' ? section.enrollment_status : null
      );
      var catalogSeats = catalogStatus === 'closed'
        ? 0
        : numericOrNull(section && typeof section === 'object' ? section.seats_available : null);
      return {
        phase: PHASES.UNVERIFIED,
        status: statusLabel(catalogStatus),
        seatsAvailable: catalogSeats,
        waitlistTotal: section && typeof section === 'object'
          ? numericOrNull(section.waitlist_total)
          : null,
        capacity: section && typeof section === 'object'
          ? numericOrNull(section.waitlist_capacity)
          : null,
        current: false,
        detailsPending: false,
        error: null,
        source: 'catalog'
      };
    }

    return {
      getState: getState,
      getSectionState: getSectionState,
      getEffectiveAvailability: getEffectiveAvailability,
      reset: reset,
      startQuery: startQuery,
      requestDetails: requestDetails,
      _beginGeneration: beginGeneration
    };
  }

  window.APStudyCoursesVerify = {
    buildQuerySignature: buildQuerySignature,
    create: create
  };
})();
