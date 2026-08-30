const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const VERIFY_PATH = path.join(__dirname, '../../static/js/courses/verify.js');
const verifySource = fs.readFileSync(VERIFY_PATH, 'utf8');

function loadVerify() {
  const context = {
    window: { AbortController },
    AbortController,
    setTimeout,
    Promise,
    Map,
    Set,
  };
  vm.createContext(context);
  vm.runInContext(verifySource, context, { filename: 'verify.js' });
  const api = context.window.APStudyCoursesVerify;
  assert.ok(api && typeof api.create === 'function', 'verify.js must expose APStudyCoursesVerify');
  return api;
}

const verifyModule = loadVerify();

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function createFetchRecorder() {
  const calls = [];
  function fetchImpl(url, options) {
    const pending = deferred();
    const call = {
      url,
      options,
      method: options.method,
      headers: options.headers,
      credentials: options.credentials,
      signal: options.signal || null,
      body: JSON.parse(options.body),
      deferred: pending,
    };
    calls.push(call);
    return pending.promise;
  }
  return { calls, fetchImpl };
}

function respondJson(call, payload) {
  call.deferred.resolve({
    ok: true,
    status: 200,
    json: () => Promise.resolve(payload),
    text: () => Promise.resolve(JSON.stringify(payload)),
  });
}

function rejectCall(call, error) {
  call.deferred.reject(error);
}

async function flush() {
  for (let i = 0; i < 3; i += 1) {
    await new Promise((resolve) => setImmediate(resolve));
  }
}

function createController(fetchImpl) {
  return verifyModule.create({ fetchImpl });
}

function plain(value) {
  return JSON.parse(JSON.stringify(value));
}

test('buildQuerySignature sorts days, ignores statuses/statusFilters, and changes with real inputs', () => {
  const { buildQuerySignature } = verifyModule;
  const base = {
    term: 'Fall_2026',
    query: ' bio ',
    days: ['Tue', 'Mon'],
    campus: ' Atlanta ',
    requirement: ' WR ',
    timeEnabled: false,
    statuses: ['Open', 'Closed'],
    statusFilters: new Set(['open']),
  };

  assert.equal(buildQuerySignature(base), buildQuerySignature({ ...base, days: ['Mon', 'Tue'] }));
  assert.equal(
    buildQuerySignature(base),
    buildQuerySignature({
      term: 'Fall_2026',
      query: 'bio',
      days: ['mon', 'tue'],
      campus: 'atlanta',
      requirement: 'wr',
      timeEnabled: false,
    }),
  );
  assert.notEqual(buildQuerySignature(base), buildQuerySignature({ ...base, query: 'chem' }));
  assert.notEqual(buildQuerySignature(base), buildQuerySignature({ ...base, days: ['Wed'] }));
  assert.notEqual(buildQuerySignature(base), buildQuerySignature({ ...base, campus: 'Oxford' }));
  assert.notEqual(buildQuerySignature(base), buildQuerySignature({ ...base, requirement: 'Q' }));
  assert.notEqual(
    buildQuerySignature(base),
    buildQuerySignature({ ...base, timeEnabled: true, timeStart: '09:00', timeEnd: '10:00' }),
  );
  assert.notEqual(
    buildQuerySignature({ ...base, timeEnabled: true, timeStart: '09:00', timeEnd: '10:00' }),
    buildQuerySignature({ ...base, timeEnabled: true, timeStart: '09:00', timeEnd: '11:00' }),
  );
});

test('_beginGeneration dedupes candidate ids and caps the list at 120', () => {
  const ids = ['dup', 'dup', ...Array.from({ length: 130 }, (_, i) => `s${i}`)];
  const controller = verifyModule.create({
    fetchImpl: () => {
      throw new Error('no fetch expected from _beginGeneration');
    },
  });

  const first = controller._beginGeneration({ sectionIds: ids, queryInput: { term: 'Fall_2026' } });
  assert.equal(first.changed, true);
  assert.equal(first.sectionIds.length, 120);
  assert.equal(new Set(first.sectionIds).size, 120);
  assert.equal(first.sectionIds[0], 'dup');
  assert.ok(first.sectionIds.includes('s118'));
  assert.ok(!first.sectionIds.includes('s119'));

  const state = controller.getState();
  assert.equal(state.phases.size, 120);
  for (const id of first.sectionIds) {
    assert.equal(state.phases.get(id), 'pending');
  }

  const second = controller._beginGeneration({ sectionIds: ids, queryInput: { term: 'Fall_2026' } });
  assert.equal(second.changed, false);
  assert.equal(second.generation, first.generation);
});

test('startQuery reuses unchanged query and candidates with a single request', async () => {
  const recorder = createFetchRecorder();
  const controller = createController(recorder.fetchImpl);
  const payload = { sectionIds: ['a', 'b', 'a'], queryInput: { query: 'bio' } };

  const first = controller.startQuery(payload);
  const second = controller.startQuery(payload);
  assert.equal(second, first);
  await flush();
  assert.equal(recorder.calls.length, 1);

  respondJson(recorder.calls[0], {
    verified_by_id: {
      a: { enrollment_status: 'Open', seats_available: 3 },
      b: { enrollment_status: 'Closed' },
    },
  });
  await first;
  assert.equal(controller.getState().phases.get('a'), 'verified');

  await controller.startQuery(payload);
  assert.equal(recorder.calls.length, 1);
});

test('changed query aborts the old request, clears state, and stale responses cannot apply', async () => {
  const recorder = createFetchRecorder();
  const controller = createController(recorder.fetchImpl);

  const first = controller.startQuery({ sectionIds: ['a1', 'a2'], queryInput: { query: 'bio' } });
  await flush();
  assert.equal(recorder.calls.length, 1);

  controller.startQuery({ sectionIds: ['b1'], queryInput: { query: 'chem' } });
  assert.equal(recorder.calls[0].signal.aborted, true);
  await flush();

  const mid = controller.getState();
  assert.equal(mid.generation, 2);
  assert.deepEqual([...mid.candidateIds], ['b1']);
  assert.deepEqual([...mid.phases.keys()], ['b1']);
  assert.equal(mid.phases.get('b1'), 'pending');
  assert.equal(mid.verified.size, 0);

  respondJson(recorder.calls[0], {
    verified_by_id: {
      a1: { enrollment_status: 'Open', seats_available: 9 },
      a2: { enrollment_status: 'Open', seats_available: 9 },
    },
  });
  await first;
  await flush();

  const after = controller.getState();
  assert.equal(after.phases.get('a1'), undefined);
  assert.equal(after.phases.get('a2'), undefined);
  assert.equal(after.verified.has('a1'), false);
  assert.equal(after.verified.has('a2'), false);
  assert.equal(after.phases.get('b1'), 'pending');
  assert.equal(controller.getSectionState('a1').detail, null);
});

test('status partial results override catalog rows and mark errors and missing ids unavailable', async () => {
  const recorder = createFetchRecorder();
  const controller = createController(recorder.fetchImpl);

  const pending = controller.startQuery({
    sectionIds: ['closedSec', 'errSec', 'missSec'],
    queryInput: {},
  });
  await flush();
  respondJson(recorder.calls[0], {
    verified_by_id: {
      closedSec: { enrollment_status: 'Closed', seats_available: 99 },
    },
    errors_by_id: {
      errSec: { message: 'boom' },
    },
  });
  await pending;

  const closed = controller.getEffectiveAvailability({
    id: 'closedSec',
    enrollment_status: 'Open',
    seats_available: 2,
  });
  assert.deepEqual(plain(closed), {
    phase: 'verified',
    status: 'Closed',
    seatsAvailable: 0,
    waitlistTotal: null,
    capacity: null,
    current: true,
    detailsPending: false,
    error: null,
    source: 'live',
  });

  const errored = controller.getEffectiveAvailability({
    id: 'errSec',
    enrollment_status: 'Open',
    seats_available: 5,
  });
  assert.deepEqual(plain(errored), {
    phase: 'unavailable',
    status: 'Unavailable',
    seatsAvailable: null,
    waitlistTotal: null,
    capacity: null,
    current: false,
    detailsPending: false,
    error: { message: 'boom' },
    source: null,
  });

  const missing = controller.getEffectiveAvailability({
    id: 'missSec',
    enrollment_status: 'Open',
    seats_available: 7,
  });
  assert.deepEqual(plain(missing), {
    phase: 'unavailable',
    status: 'Unavailable',
    seatsAvailable: null,
    waitlistTotal: null,
    capacity: null,
    current: false,
    detailsPending: false,
    error: { message: 'Live availability was not returned by Atlas.' },
    source: null,
  });
});

test('pending and global failure never expose stale seats or status as current', async () => {
  const recorder = createFetchRecorder();
  const controller = createController(recorder.fetchImpl);
  const section = { id: 's1', enrollment_status: 'Open', seats_available: 2 };

  const pending = controller.startQuery({ sectionIds: ['s1', 's2'], queryInput: {} });
  await flush();

  assert.deepEqual(plain(controller.getEffectiveAvailability(section)), {
    phase: 'pending',
    status: 'Checking',
    seatsAvailable: null,
    waitlistTotal: null,
    capacity: null,
    current: false,
    detailsPending: false,
    error: null,
    source: null,
  });

  rejectCall(recorder.calls[0], new TypeError('network down'));
  await pending;

  const failed = controller.getEffectiveAvailability(section);
  assert.deepEqual(plain(failed), {
    phase: 'unavailable',
    status: 'Unavailable',
    seatsAvailable: null,
    waitlistTotal: null,
    capacity: null,
    current: false,
    detailsPending: false,
    error: { message: 'Unable to verify live availability.' },
    source: null,
  });
  const failedSibling = controller.getEffectiveAvailability({ id: 's2', enrollment_status: 'Open', seats_available: 4 });
  assert.equal(failedSibling.status, 'Unavailable');
  assert.equal(failedSibling.seatsAvailable, null);
  assert.equal(failedSibling.current, false);
});

test('O/C/W and lowercase enrollment statuses normalize to Open/Closed/Waitlist', async () => {
  const recorder = createFetchRecorder();
  const controller = createController(recorder.fetchImpl);

  const pending = controller.startQuery({ sectionIds: ['o1', 'c1', 'w1'], queryInput: {} });
  await flush();
  respondJson(recorder.calls[0], {
    verified_by_id: {
      o1: { enrollment_status: 'o' },
      c1: { enrollment_status: 'C' },
      w1: { enrollment_status: ' waitlist ' },
    },
  });
  await pending;

  const open = controller.getEffectiveAvailability({ id: 'o1', enrollment_status: 'Open', seats_available: 3 });
  assert.deepEqual(
    { phase: open.phase, status: open.status, seatsAvailable: open.seatsAvailable, current: open.current },
    { phase: 'verified', status: 'Open', seatsAvailable: null, current: true },
  );
  const closed = controller.getEffectiveAvailability({ id: 'c1', enrollment_status: 'Open', seats_available: 3 });
  assert.equal(closed.status, 'Closed');
  assert.equal(closed.seatsAvailable, 0);
  const waitlisted = controller.getEffectiveAvailability({ id: 'w1' });
  assert.deepEqual(
    { phase: waitlisted.phase, status: waitlisted.status, current: waitlisted.current },
    { phase: 'verified', status: 'Waitlist', current: true },
  );

  assert.equal(controller.getEffectiveAvailability({ id: 'x', enrollment_status: 'O', seats_available: 3 }).status, 'Open');
  assert.equal(controller.getEffectiveAvailability({ id: 'x', enrollment_status: 'w' }).status, 'Waitlist');
  assert.equal(controller.getEffectiveAvailability({ id: 'x', enrollment_status: 'c', seats_available: 5 }).status, 'Closed');
  assert.equal(controller.getEffectiveAvailability({ id: 'x', enrollment_status: 'open' }).status, 'Open');

  assert.equal(
    verifyModule.buildQuerySignature({ campus: ' ATLANTA ', requirement: 'Wr', query: 'BIO' }),
    verifyModule.buildQuerySignature({ campus: 'atlanta', requirement: 'wr', query: 'BIO' }),
  );
});

test('details request caps 12 eligible open/waitlist ids and sends candidate plus detail ids', async () => {
  const recorder = createFetchRecorder();
  const controller = createController(recorder.fetchImpl);
  const openIds = Array.from({ length: 14 }, (_, i) => `open${i + 1}`);
  const candidateIds = [...openIds, 'wl1', 'cl1'];

  const statusPending = controller.startQuery({ sectionIds: candidateIds, queryInput: {} });
  await flush();
  const verifiedById = {};
  openIds.forEach((id) => {
    verifiedById[id] = { enrollment_status: 'Open', seats_available: 3 };
  });
  verifiedById.wl1 = { enrollment_status: 'Waitlist' };
  verifiedById.cl1 = { enrollment_status: 'Closed' };
  respondJson(recorder.calls[0], { verified_by_id: verifiedById });
  await statusPending;

  const detailsPending = controller.requestDetails([...candidateIds, ...candidateIds]);
  await flush();

  assert.equal(recorder.calls.length, 2);
  const detailCall = recorder.calls[1];
  assert.equal(detailCall.url, '/api/atlas/sections/verify');
  assert.deepEqual(detailCall.body.detail_ids, openIds.slice(0, 12));
  assert.equal(detailCall.body.detail_ids.length, 12);
  assert.deepEqual(detailCall.body.section_ids, openIds.slice(0, 12));
  assert.ok(!detailCall.body.detail_ids.includes('wl1'));
  assert.ok(!detailCall.body.detail_ids.some((id) => id.startsWith('cl')));

  const detailsById = {};
  openIds.slice(0, 12).forEach((id) => {
    detailsById[id] = { enrollment_status: 'Open', seats_available: 7 };
  });
  respondJson(detailCall, { details_by_id: detailsById });
  await detailsPending;

  const state = controller.getState();
  assert.equal(state.detailedIds.size, 12);
  assert.ok(state.detailedIds.has('open1'));
  assert.ok(!state.detailedIds.has('open13'));

  const waitlist = controller.getEffectiveAvailability({ id: 'wl1' });
  assert.equal(waitlist.status, 'Waitlist');
  assert.equal(waitlist.current, true);
  assert.equal(waitlist.detailsPending, false);
  const closed = controller.getEffectiveAvailability({ id: 'cl1', enrollment_status: 'Open', seats_available: 2 });
  assert.equal(closed.status, 'Closed');
  assert.equal(closed.seatsAvailable, 0);
  assert.equal(closed.current, true);
});

test('detail rows retain seats, completed ids are not refetched, inflight requests do not duplicate', async () => {
  const recorder = createFetchRecorder();
  const controller = createController(recorder.fetchImpl);

  const statusPending = controller.startQuery({ sectionIds: ['x1', 'x2'], queryInput: {} });
  await flush();
  respondJson(recorder.calls[0], {
    verified_by_id: {
      x1: { enrollment_status: 'Open', seats_available: 1 },
      x2: { enrollment_status: 'Open', seats_available: 2 },
    },
  });
  await statusPending;

  const detailsPending = controller.requestDetails(['x1', 'x2']);
  await flush();
  assert.equal(recorder.calls.length, 2);

  const repeated = controller.requestDetails(['x1', 'x2', 'x1']);
  assert.equal(repeated, detailsPending);
  await flush();
  assert.equal(recorder.calls.length, 2);

  respondJson(recorder.calls[1], {
    details_by_id: {
      x1: { enrollment_status: 'Open', seats_available: 9 },
      x2: { enrollment_status: 'Open', seats_available: 0 },
    },
  });
  await detailsPending;

  const live = controller.getEffectiveAvailability({ id: 'x1', enrollment_status: 'Open', seats_available: 1 });
  assert.deepEqual(
    { phase: live.phase, status: live.status, seatsAvailable: live.seatsAvailable, current: live.current, detailsPending: live.detailsPending },
    { phase: 'verified', status: 'Open', seatsAvailable: 9, current: true, detailsPending: false },
  );
  assert.equal(controller.getState().details.get('x1').seats_available, 9);
  const zeroSeats = controller.getEffectiveAvailability({ id: 'x2' });
  assert.equal(zeroSeats.seatsAvailable, 0);
  assert.equal(zeroSeats.current, true);

  await controller.requestDetails(['x1', 'x2']);
  assert.equal(recorder.calls.length, 2);
});

test('detail_errors_by_id keeps the verified status and only marks seat details unavailable', async () => {
  const recorder = createFetchRecorder();
  const controller = createController(recorder.fetchImpl);

  let pending = controller.startQuery({ sectionIds: ['d1', 'd2', 'd3', 'd4'], queryInput: {} });
  await flush();
  respondJson(recorder.calls[0], {
    verified_by_id: {
      d1: { enrollment_status: 'Open', seats_available: 2 },
      d2: { enrollment_status: 'Open', seats_available: 5 },
      d3: { enrollment_status: 'Open', seats_available: 6 },
      d4: { enrollment_status: 'Open', seats_available: 7 },
    },
  });
  await pending;

  pending = controller.requestDetails(['d1', 'd2', 'd3', 'd4']);
  await flush();
  respondJson(recorder.calls[1], {
    verified_by_id: {
      d1: { enrollment_status: 'Open', seats_available: 2 },
      d2: { enrollment_status: 'Open', seats_available: 5 },
      d3: { enrollment_status: 'Open', seats_available: 6 },
    },
    details_by_id: { d1: { enrollment_status: 'Open', seats_available: 4 } },
    detail_errors_by_id: { d2: { message: 'row gone' } },
    errors_by_id: { d4: 'Live Atlas request failed' },
  });
  await pending;

  const kept = controller.getEffectiveAvailability({ id: 'd1' });
  assert.equal(kept.status, 'Open');
  assert.equal(kept.seatsAvailable, 4);
  assert.equal(kept.current, true);

  const detailErrored = controller.getEffectiveAvailability({ id: 'd2', enrollment_status: 'Open', seats_available: 5 });
  assert.deepEqual(
    plain({
      phase: detailErrored.phase,
      status: detailErrored.status,
      seatsAvailable: detailErrored.seatsAvailable,
      current: detailErrored.current,
      detailsPending: detailErrored.detailsPending,
      error: detailErrored.error,
    }),
    {
      phase: 'verified',
      status: 'Open',
      seatsAvailable: null,
      current: true,
      detailsPending: false,
      error: { message: 'row gone' },
    },
  );
  assert.ok(controller.getSectionState('d2').verified, 'detail error must keep the verified status row');
  assert.equal(controller.getSectionState('d2').detail, null);
  assert.ok(controller.getState().detailedIds.has('d2'), 'detail-errored ids must not be refetched');

  const missing = controller.getEffectiveAvailability({ id: 'd3', enrollment_status: 'Open', seats_available: 6 });
  assert.deepEqual(
    plain({
      phase: missing.phase,
      status: missing.status,
      current: missing.current,
      error: missing.error,
    }),
    {
      phase: 'verified',
      status: 'Open',
      current: true,
      error: { message: 'Live seat details were not returned by Atlas.' },
    },
  );

  const statusErrored = controller.getEffectiveAvailability({ id: 'd4', enrollment_status: 'Open', seats_available: 7 });
  assert.deepEqual(
    plain({
      phase: statusErrored.phase,
      status: statusErrored.status,
      seatsAvailable: statusErrored.seatsAvailable,
      current: statusErrored.current,
      error: statusErrored.error,
    }),
    {
      phase: 'verified',
      status: 'Open',
      seatsAvailable: null,
      current: true,
      error: { message: 'Live Atlas request failed' },
    },
  );
  assert.ok(controller.getSectionState('d4').verified, 'errors_by_id during a detail call must keep the verified status row');
  assert.equal(controller.getSectionState('d4').detail, null);
  assert.ok(controller.getState().detailedIds.has('d4'), 'detail-call errors_by_id must mark the id attempted');

  const before = controller.getState();
  await controller.requestDetails(['d2', 'd3', 'd4']);
  assert.equal(recorder.calls.length, 2, 'attempted detail ids must never be refetched');
  assert.equal(controller.getState().detailedIds.size, before.detailedIds.size);
});

test('detail transport failure keeps the verified status and marks only seat details unavailable', async () => {
  const recorder = createFetchRecorder();
  const controller = createController(recorder.fetchImpl);

  let pending = controller.startQuery({ sectionIds: ['g1'], queryInput: {} });
  await flush();
  respondJson(recorder.calls[0], {
    verified_by_id: { g1: { enrollment_status: 'Open', seats_available: 8 } },
  });
  await pending;
  pending = controller.requestDetails(['g1']);
  await flush();
  rejectCall(recorder.calls[1], new Error('boom'));
  await pending;

  const failed = controller.getEffectiveAvailability({ id: 'g1', enrollment_status: 'Open', seats_available: 8 });
  assert.deepEqual(
    plain({ phase: failed.phase, status: failed.status, seatsAvailable: failed.seatsAvailable, current: failed.current, error: failed.error }),
    {
      phase: 'verified',
      status: 'Open',
      seatsAvailable: null,
      current: true,
      error: { message: 'Unable to verify live seat details.' },
    },
  );
  const sectionState = controller.getSectionState('g1');
  assert.ok(sectionState.verified, 'transport failure must keep the verified status row');
  assert.equal(sectionState.verified.enrollment_status, 'Open');
  assert.equal(sectionState.detail, null);
  assert.ok(controller.getState().detailedIds.has('g1'), 'failed detail ids must be marked attempted');
  assert.equal(controller.getState().detailInflightIds.size, 0);

  await controller.requestDetails(['g1']);
  assert.equal(recorder.calls.length, 2, 'attempted detail ids must never be refetched');
});

test('detail HTTP failure keeps the verified status and never refetches the id', async () => {
  const recorder = createFetchRecorder();
  const controller = createController(recorder.fetchImpl);

  let pending = controller.startQuery({ sectionIds: ['h1'], queryInput: {} });
  await flush();
  respondJson(recorder.calls[0], {
    verified_by_id: { h1: { enrollment_status: 'Waitlist' } },
  });
  await pending;
  pending = controller.requestDetails(['h1']);
  await flush();
  recorder.calls[1].deferred.resolve({
    ok: false,
    status: 500,
    json: () => Promise.resolve({}),
    text: () => Promise.resolve('server exploded'),
  });
  await pending;

  const failed = controller.getEffectiveAvailability({ id: 'h1' });
  assert.deepEqual(
    plain({ phase: failed.phase, status: failed.status, seatsAvailable: failed.seatsAvailable, current: failed.current, error: failed.error }),
    {
      phase: 'verified',
      status: 'Waitlist',
      seatsAvailable: null,
      current: true,
      error: { message: 'Unable to verify live seat details.' },
    },
  );
  assert.ok(controller.getSectionState('h1').verified, 'HTTP failure must keep the verified status row');
  assert.equal(controller.getSectionState('h1').detail, null);
  assert.ok(controller.getState().detailedIds.has('h1'), 'HTTP-failed detail ids must be marked attempted');

  await controller.requestDetails(['h1']);
  assert.equal(recorder.calls.length, 2, 'attempted detail ids must never be refetched');
});

test('query generation change while details are delayed discards the stale details', async () => {
  const recorder = createFetchRecorder();
  const controller = createController(recorder.fetchImpl);

  const statusPending = controller.startQuery({ sectionIds: ['s1', 's2'], queryInput: { query: 'bio' } });
  await flush();
  respondJson(recorder.calls[0], {
    verified_by_id: {
      s1: { enrollment_status: 'Open', seats_available: 3 },
      s2: { enrollment_status: 'Open', seats_available: 3 },
    },
  });
  await statusPending;

  const detailsPending = controller.requestDetails(['s1', 's2']);
  await flush();
  assert.equal(recorder.calls.length, 2);
  const generationBefore = controller.getState().generation;

  controller.startQuery({ sectionIds: ['s1', 's2'], queryInput: { query: 'chem' } });
  await flush();

  assert.equal(recorder.calls[1].signal.aborted, true);
  const mid = controller.getState();
  assert.equal(mid.generation, generationBefore + 1);
  assert.equal(mid.details.size, 0);
  assert.equal(mid.phases.get('s1'), 'pending');

  respondJson(recorder.calls[1], {
    details_by_id: { s1: { enrollment_status: 'Open', seats_available: 42 } },
  });
  await detailsPending;

  const after = controller.getState();
  assert.equal(after.details.size, 0);
  assert.equal(after.phases.get('s1'), 'pending');
  assert.equal(controller.getSectionState('s1').detail, null);

  const effective = controller.getEffectiveAvailability({ id: 's1', enrollment_status: 'Open', seats_available: 3 });
  assert.equal(effective.phase, 'pending');
  assert.equal(effective.status, 'Checking');
  assert.equal(effective.seatsAvailable, null);
  assert.equal(effective.current, false);
});

test('status verification splits candidates beyond 24 term-subject groups into sequential requests and aggregates results', async () => {
  const recorder = createFetchRecorder();
  const controller = createController(recorder.fetchImpl);
  const groupCount = 30;
  const ids = [];
  const verifiedById = {};
  for (let group = 1; group <= groupCount; group += 1) {
    const subject = `S${String(group).padStart(2, '0')}`;
    for (let row = 1; row <= 2; row += 1) {
      const id = `Fall_2026|${subject}|101|${group}${row}0|${row}`;
      ids.push(id);
      verifiedById[id] = { enrollment_status: 'Open', seats_available: row };
    }
  }
  assert.equal(new Set(ids.map((id) => id.split('|').slice(0, 2).join('|'))).size, groupCount);

  const pending = controller.startQuery({ sectionIds: ids, queryInput: { term: 'Fall_2026' } });
  await flush();

  assert.equal(recorder.calls.length, 1, 'next chunk must wait for the previous request to settle');
  const firstIds = recorder.calls[0].body.section_ids;
  assert.equal(firstIds.length, 48);
  assert.equal(new Set(firstIds.map((id) => id.split('|').slice(0, 2).join('|'))).size, 24);
  respondJson(recorder.calls[0], { verified_by_id: subsetOf(verifiedById, firstIds) });
  await new Promise((resolve) => setImmediate(resolve));
  await flush();

  assert.equal(recorder.calls.length, 2, 'second chunk fetches only after the first responds');
  const secondIds = recorder.calls[1].body.section_ids;
  assert.equal(secondIds.length, 12);
  assert.equal(new Set(secondIds.map((id) => id.split('|').slice(0, 2).join('|'))).size, 6);
  assert.deepEqual([...firstIds, ...secondIds], ids, 'chunks preserve candidate order and coverage');
  respondJson(recorder.calls[1], { verified_by_id: subsetOf(verifiedById, secondIds) });
  await pending;

  const state = controller.getState();
  assert.equal(state.verified.size, ids.length);
  assert.equal(state.errors.size, 0);
  for (const id of ids) {
    assert.equal(state.phases.get(id), 'verified');
  }
  const first = controller.getEffectiveAvailability({ id: ids[0] });
  assert.equal(first.phase, 'verified');
  assert.equal(first.current, true);
});

test('a chunk failure marks only its own ids unavailable and later chunks still verify', async () => {
  const recorder = createFetchRecorder();
  const controller = createController(recorder.fetchImpl);
  const ids = [];
  for (let group = 1; group <= 26; group += 1) {
    ids.push(`Fall_2026|S${String(group).padStart(2, '0')}|101|${group}|1`);
  }

  const pending = controller.startQuery({ sectionIds: ids, queryInput: {} });
  await flush();
  assert.equal(recorder.calls.length, 1);
  rejectCall(recorder.calls[0], new TypeError('network down'));
  await flush();

  assert.equal(recorder.calls.length, 2, 'a failed chunk must not stop the remaining chunks');
  respondJson(recorder.calls[1], {
    verified_by_id: { [ids[24]]: { enrollment_status: 'Open', seats_available: 1 } },
  });
  await pending;

  const state = controller.getState();
  for (const id of ids.slice(0, 24)) {
    assert.equal(state.phases.get(id), 'unavailable');
    assert.equal(controller.getEffectiveAvailability({ id, enrollment_status: 'Open' }).error.message, 'Unable to verify live availability.');
  }
  assert.equal(state.phases.get(ids[24]), 'verified');
  assert.equal(state.phases.get(ids[25]), 'unavailable');
  assert.equal(controller.getEffectiveAvailability({ id: ids[25], enrollment_status: 'Open' }).error.message, 'Live availability was not returned by Atlas.');
});

test('query generation change during a chunk sequence cancels remaining chunks and stale results never apply', async () => {
  const recorder = createFetchRecorder();
  const controller = createController(recorder.fetchImpl);
  const gen1Ids = [];
  for (let group = 1; group <= 26; group += 1) {
    gen1Ids.push(`Fall_2026|S${String(group).padStart(2, '0')}|101|${group}|1`);
  }

  const first = controller.startQuery({ sectionIds: gen1Ids, queryInput: { query: 'bio' } });
  await flush();
  assert.equal(recorder.calls.length, 1);

  const second = controller.startQuery({ sectionIds: ['Fall_2026|CH|101|1|1'], queryInput: { query: 'chem' } });
  assert.equal(recorder.calls[0].signal.aborted, true, 'in-flight chunk must abort on a new generation');
  await flush();
  assert.equal(recorder.calls.length, 2, 'the new generation owns the next fetch');

  respondJson(recorder.calls[0], {
    verified_by_id: { [gen1Ids[0]]: { enrollment_status: 'Open', seats_available: 9 } },
  });
  await first;
  await flush();

  assert.equal(recorder.calls.length, 2, 'stale sequence must not issue its remaining chunks');
  let mid = controller.getState();
  assert.equal(mid.verified.size, 0, 'stale chunk results must not apply');
  assert.equal(mid.phases.get('Fall_2026|CH|101|1|1'), 'pending');

  respondJson(recorder.calls[1], {
    verified_by_id: { 'Fall_2026|CH|101|1|1': { enrollment_status: 'Closed' } },
  });
  await second;
  mid = controller.getState();
  assert.equal(mid.phases.get('Fall_2026|CH|101|1|1'), 'verified');
  assert.equal(mid.verified.size, 1);
});

test('overlapping detail requests serialize so controllers and promises never clobber', async () => {
  const recorder = createFetchRecorder();
  const controller = createController(recorder.fetchImpl);

  const statusPending = controller.startQuery({
    sectionIds: ['s1', 's2', 's3', 's4'],
    queryInput: {},
  });
  await flush();
  const verifiedById = {};
  for (const id of ['s1', 's2', 's3', 's4']) {
    verifiedById[id] = { enrollment_status: 'Open', seats_available: 3 };
  }
  respondJson(recorder.calls[0], { verified_by_id: verifiedById });
  await statusPending;

  const first = controller.requestDetails(['s1', 's2']);
  await flush();
  const second = controller.requestDetails(['s3', 's4']);
  await flush();

  assert.notEqual(first, second, 'overlapping callers get distinct promises');
  assert.equal(recorder.calls.length, 2, 'queued detail request must wait for the executing one to settle');
  assert.deepEqual(recorder.calls[1].body.detail_ids, ['s1', 's2']);
  assert.equal(recorder.calls[1].signal.aborted, false, 'the executing controller must stay reachable');

  respondJson(recorder.calls[1], {
    details_by_id: { s1: { enrollment_status: 'Open', seats_available: 9 }, s2: { enrollment_status: 'Open', seats_available: 8 } },
  });
  await first;
  await flush();
  assert.equal(recorder.calls.length, 3, 'queued detail request fetches only after its turn');
  assert.deepEqual(recorder.calls[2].body.detail_ids, ['s3', 's4']);

  respondJson(recorder.calls[2], {
    details_by_id: { s3: { enrollment_status: 'Open', seats_available: 7 }, s4: { enrollment_status: 'Open', seats_available: 6 } },
  });
  await second;

  const state = controller.getState();
  assert.equal(state.details.size, 4);
  assert.equal(state.detailInflightIds.size, 0, 'serialized requests clean up their inflight ids');
  assert.equal(controller.getEffectiveAvailability({ id: 's3' }).seatsAvailable, 7);
});

test('a queued detail request is skipped without fetching after a generation change', async () => {
  const recorder = createFetchRecorder();
  const controller = createController(recorder.fetchImpl);

  const statusPending = controller.startQuery({ sectionIds: ['s1', 's2'], queryInput: {} });
  await flush();
  respondJson(recorder.calls[0], {
    verified_by_id: {
      s1: { enrollment_status: 'Open', seats_available: 3 },
      s2: { enrollment_status: 'Open', seats_available: 3 },
    },
  });
  await statusPending;

  const first = controller.requestDetails(['s1']);
  await flush();
  const queued = controller.requestDetails(['s2']);
  await flush();
  assert.notEqual(queued, first);

  controller.startQuery({ sectionIds: ['s1', 's2'], queryInput: { query: 'chem' } });
  assert.equal(recorder.calls[1].signal.aborted, true);
  await flush();
  assert.equal(recorder.calls.length, 3, 'only the new generation status chunk plus the aborted detail call exist');
  assert.deepEqual(recorder.calls[2].body.section_ids, ['s1', 's2']);

  respondJson(recorder.calls[1], {
    details_by_id: { s1: { enrollment_status: 'Open', seats_available: 42 } },
  });
  await first;
  await queued;
  await flush();

  assert.equal(recorder.calls.length, 3, 'stale queued detail request must not fetch');
  assert.equal(controller.getState().details.size, 0);
  assert.equal(controller.getState().detailInflightIds.size, 0);
});

function subsetOf(source, ids) {
  const out = {};
  ids.forEach((id) => {
    out[id] = source[id];
  });
  return out;
}

test('reset aborts active requests and returns sections to unverified', async () => {
  const recorder = createFetchRecorder();
  const controller = createController(recorder.fetchImpl);

  const statusPending = controller.startQuery({ sectionIds: ['s1'], queryInput: {} });
  await flush();
  const statusSignal = recorder.calls[0].signal;
  controller.reset();
  assert.equal(statusSignal.aborted, true);

  const cleared = controller.getState();
  assert.equal(cleared.phases.size, 0);
  assert.equal(cleared.verified.size, 0);
  assert.equal(cleared.candidateIds.length, 0);
  assert.equal(cleared.querySignature, '');
  assert.equal(controller.getSectionState('s1').phase, 'unverified');
  assert.deepEqual(plain(controller.getEffectiveAvailability({ id: 's1', enrollment_status: 'Open', seats_available: 3 })), {
    phase: 'unverified',
    status: 'Open',
    seatsAvailable: 3,
    waitlistTotal: null,
    capacity: null,
    current: false,
    detailsPending: false,
    error: null,
    source: 'catalog',
  });

  const generationAfterFirstReset = controller.getState().generation;
  const next = controller.startQuery({ sectionIds: ['s1'], queryInput: {} });
  await flush();
  respondJson(recorder.calls[1], {
    verified_by_id: { s1: { enrollment_status: 'Open', seats_available: 3 } },
  });
  await next;
  const detailsPending = controller.requestDetails(['s1']);
  await flush();
  const detailsSignal = recorder.calls[2].signal;
  controller.reset();
  assert.equal(detailsSignal.aborted, true);
  assert.equal(controller.getState().details.size, 0);
  assert.ok(controller.getState().generation > generationAfterFirstReset);
});

test('verify requests hit the same-origin endpoint with credentials, JSON body, and correct ids', async () => {
  const recorder = createFetchRecorder();
  const controller = createController(recorder.fetchImpl);

  const statusPending = controller.startQuery({ sectionIds: ['a', 'b'], queryInput: {} });
  await flush();
  const statusCall = recorder.calls[0];
  assert.equal(statusCall.url, '/api/atlas/sections/verify');
  assert.equal(statusCall.method, 'POST');
  assert.equal(statusCall.credentials, 'same-origin');
  assert.equal(statusCall.headers['Content-Type'], 'application/json');
  assert.deepEqual(statusCall.body, { section_ids: ['a', 'b'] });
  assert.ok(statusCall.signal);
  assert.equal(statusCall.signal.aborted, false);

  respondJson(statusCall, {
    verified_by_id: {
      a: { enrollment_status: 'Open', seats_available: 1 },
      b: { enrollment_status: 'Open', seats_available: 2 },
    },
  });
  await statusPending;

  const detailsPending = controller.requestDetails(['a']);
  await flush();
  const detailCall = recorder.calls[1];
  assert.equal(detailCall.url, '/api/atlas/sections/verify');
  assert.equal(detailCall.method, 'POST');
  assert.equal(detailCall.credentials, 'same-origin');
  assert.equal(detailCall.headers['Content-Type'], 'application/json');
  assert.deepEqual(detailCall.body, { section_ids: ['a'], detail_ids: ['a'] });

  respondJson(detailCall, {
    details_by_id: { a: { enrollment_status: 'Open', seats_available: 5 } },
  });
  await detailsPending;

  assert.doesNotMatch(verifySource, /atlas\.emory\.edu/);
});
