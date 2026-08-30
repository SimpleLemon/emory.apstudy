const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const PANEL_PATH = path.join(__dirname, '../../static/js/courses/panel.js');
const panelSource = fs.readFileSync(PANEL_PATH, 'utf8');

const UNAVAILABLE_SEATS_TITLE = 'Live availability could not be verified from Atlas just now.';

const UTILS = {
  cssEscape: (value) => String(value),
  escapeHtml: (value) => String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;'),
  formatCourseCardSchedule: () => 'Mon/Wed 9:00-9:50',
  formatDateRange: () => 'Aug 26 - Dec 16',
  formatDateTime: () => 'Aug 29, 2026',
  formatSeats: () => '12 seats',
  formatTermLabel: (term) => String(term || '').replace(/_/g, ' '),
  normalizeScheduleDisplay: (value) => value,
  parseAtlasTimeToken: () => null,
  parseTimeInput: () => null,
};

function baseState() {
  return {
    loading: false,
    error: '',
    sectionsLoading: false,
    editingSectionId: null,
    detailSectionId: null,
    activeCourseView: 'search',
    selectedTerm: 'Fall_2026',
    searchQuery: '',
    dayFilters: new Set(),
    statusFilters: new Set(),
    filtersOpen: false,
    timeEnabled: false,
    campusFilter: 'all',
    requirementFilter: 'all',
    savingIds: new Set(),
    trackingIds: new Set(),
    savedCoursesBySection: new Map(),
    tracksBySection: new Map(),
    removedSelectedSections: new Map(),
    allowedTrackIntervals: [30],
    trackingTier: { key: 'free', label: 'Free' },
    trackingUsage: 0,
    trackingLimit: null,
    detailLoading: false,
    detailLiveError: '',
  };
}

function chips(html) {
  return [...html.matchAll(/<span class="course-chip([^"]*)"[^>]*>([^<]*)<\/span>/g)]
    .map((match) => ({ class: match[1].trim(), text: match[2] }));
}

function renderCard({ section, availability, withResolver = true }) {
  const summary = { textContent: '' };
  const content = { innerHTML: '', scrollTop: 0 };
  const documentStub = {
    getElementById: (id) => {
      if (id === 'courses-result-summary') return summary;
      if (id === 'courses-panel-content') return content;
      return null;
    },
    querySelector: () => null,
    querySelectorAll: () => [],
  };
  const context = { window: {}, document: documentStub };
  vm.createContext(context);
  vm.runInContext(panelSource, context, { filename: 'panel.js' });

  const options = {
    state: baseState(),
    COURSE_COLOR_PALETTE: [{ key: 'course-color-01' }],
    COURSE_DAYS: [{ key: 'Mon', index: 1 }],
    COURSE_RESULT_LIMIT: 100,
    getFilteredSections: () => [section],
    getSection: () => section,
    isTrackable: () => false,
    utils: UTILS,
  };
  if (withResolver) options.getEffectiveAvailability = () => availability;
  context.window.APStudyCoursesPanel.create(options).renderPanel();
  return content.innerHTML;
}

test('pending cards render exact Checking chips with loading classes and no title', () => {
  const html = renderCard({
    section: { id: 's1', course_code: 'BIO 201', enrollment_status: 'Open', seats_available: 12 },
    availability: {
      phase: 'pending',
      status: 'Checking',
      seatsAvailable: null,
      waitlistTotal: null,
      capacity: null,
      current: false,
      detailsPending: false,
      error: null,
      source: null,
    },
  });
  const [status, seats] = chips(html);
  assert.deepEqual(status, { class: 'is-loading', text: 'Checking' });
  assert.deepEqual(seats, { class: 'is-loading', text: 'Checking live seats' });
  assert.ok(!html.includes('title='), 'pending chips must not carry a tooltip');
  assert.ok(!html.includes('12'), 'pending cards must not leak catalog seat counts');
});

test('unavailable cards render exact Unavailable chips with the verification-failure title', () => {
  const html = renderCard({
    section: { id: 's1', course_code: 'BIO 201', enrollment_status: 'Open', seats_available: 12 },
    availability: {
      phase: 'unavailable',
      status: 'Unavailable',
      seatsAvailable: null,
      waitlistTotal: null,
      capacity: null,
      current: false,
      detailsPending: false,
      error: { message: 'Unable to verify live availability.' },
      source: null,
    },
  });
  const [status, seats] = chips(html);
  assert.deepEqual(status, { class: '', text: 'Unavailable' });
  assert.deepEqual(seats, { class: '', text: 'Unavailable' });
  assert.ok(html.includes(`title="${UNAVAILABLE_SEATS_TITLE}"`));
  assert.ok(!html.includes('12'), 'unavailable cards must not leak stale seat counts');
});

test('verified Closed status-only cards show zero seats from enrollment capacity without detail hydration', () => {
  const html = renderCard({
    section: { id: 's1', course_code: 'BIO 201', enrollment_status: 'Open', enrollment_capacity: 30 },
    availability: {
      phase: 'verified',
      status: 'Closed',
      seatsAvailable: 0,
      waitlistTotal: null,
      capacity: null,
      current: true,
      detailsPending: false,
      error: null,
      source: 'live',
    },
  });
  const [status, seats] = chips(html);
  assert.deepEqual(status, { class: 'is-closed', text: 'Closed' });
  assert.deepEqual(seats, { class: '', text: '0 of 30 seats' });
  assert.ok(!html.includes('title='));
});

test('verified Closed cards without a catalog capacity fall back to a plain zero-seat claim', () => {
  const html = renderCard({
    section: { id: 's1', course_code: 'BIO 201', enrollment_status: 'Open' },
    availability: {
      phase: 'verified',
      status: 'Closed',
      seatsAvailable: 0,
      waitlistTotal: null,
      capacity: null,
      current: true,
      detailsPending: false,
      error: null,
      source: 'live',
    },
  });
  const [, seats] = chips(html);
  assert.deepEqual(seats, { class: '', text: '0 seats available' });
});

test('verified detailed cards show the hydrated live seat count', () => {
  const withCapacity = renderCard({
    section: { id: 's1', course_code: 'BIO 201', enrollment_status: 'Open', enrollment_capacity: 30 },
    availability: {
      phase: 'verified',
      status: 'Open',
      seatsAvailable: 7,
      waitlistTotal: 0,
      capacity: 5,
      current: true,
      detailsPending: false,
      error: null,
      source: 'live',
    },
  });
  const [status, seats] = chips(withCapacity);
  assert.deepEqual(status, { class: 'is-open', text: 'Open' });
  assert.deepEqual(seats, { class: '', text: '7 of 30 seats' });

  const singular = renderCard({
    section: { id: 's1', course_code: 'BIO 201', enrollment_status: 'Open' },
    availability: {
      phase: 'verified',
      status: 'Open',
      seatsAvailable: 1,
      waitlistTotal: null,
      capacity: null,
      current: true,
      detailsPending: false,
      error: null,
      source: 'live',
    },
  });
  const [, singularSeats] = chips(singular);
  assert.deepEqual(singularSeats, { class: '', text: '1 seat' });
});

test('verified Open cards awaiting detail hydration show a loading seats chip instead of a guess', () => {
  const html = renderCard({
    section: { id: 's1', course_code: 'BIO 201', enrollment_status: 'Open', seats_available: 12 },
    availability: {
      phase: 'verified',
      status: 'Open',
      seatsAvailable: null,
      waitlistTotal: null,
      capacity: null,
      current: true,
      detailsPending: true,
      error: null,
      source: 'live',
    },
  });
  const [status, seats] = chips(html);
  assert.deepEqual(status, { class: 'is-open', text: 'Open' });
  assert.deepEqual(seats, { class: 'is-loading', text: 'Loading seats' });
  assert.ok(!html.includes('12'), 'unhydrated verified cards must not show catalog seats');
});

test('unverified cards keep the catalog status chip but never claim raw catalog seats', () => {
  const html = renderCard({
    section: { id: 's1', course_code: 'BIO 201', enrollment_status: 'Open', seats_available: 12 },
    availability: {
      phase: 'unverified',
      status: 'Open',
      seatsAvailable: 12,
      waitlistTotal: null,
      capacity: null,
      current: false,
      detailsPending: false,
      error: null,
      source: 'catalog',
    },
  });
  const [status, seats] = chips(html);
  assert.deepEqual(status, { class: 'is-open', text: 'Open' });
  assert.deepEqual(seats, { class: '', text: 'Not verified' });
  assert.ok(!/[>"]12\b/.test(html), 'catalog seat counts must never render as availability');
});

test('without an injected resolver the panel still refuses to claim catalog seats', () => {
  const html = renderCard({
    section: { id: 's1', course_code: 'BIO 201', enrollment_status: 'Closed', seats_available: 0 },
    withResolver: false,
  });
  const [status, seats] = chips(html);
  assert.deepEqual(status, { class: 'is-closed', text: 'Closed' });
  assert.deepEqual(seats, { class: '', text: 'Not verified' });
});

test('verified rows awaiting nothing show Seats unavailable instead of a loading guess', () => {
  const html = renderCard({
    section: { id: 's1', course_code: 'BIO 201', enrollment_status: 'Open', seats_available: 12 },
    availability: {
      phase: 'verified',
      status: 'Open',
      seatsAvailable: null,
      waitlistTotal: null,
      capacity: null,
      current: true,
      detailsPending: false,
      error: { message: 'Live Atlas details request failed: timeout' },
      source: 'live',
    },
  });
  const [, seats] = chips(html);
  assert.deepEqual(seats, { class: '', text: 'Seats unavailable' });
  assert.ok(html.includes('title="Live Atlas details request failed: timeout"'));
  assert.ok(!html.includes('12'), 'unhydrated verified cards must never show catalog seats');
});

test('verified rows without a detail error still get a neutral seats-unavailable tooltip', () => {
  const html = renderCard({
    section: { id: 's1', course_code: 'BIO 201' },
    availability: {
      phase: 'verified',
      status: 'Waitlist',
      seatsAvailable: null,
      waitlistTotal: null,
      capacity: null,
      current: true,
      detailsPending: false,
      error: null,
      source: 'live',
    },
  });
  const [status, seats] = chips(html);
  assert.deepEqual(status, { class: '', text: 'Waitlist' });
  assert.deepEqual(seats, { class: '', text: 'Seats unavailable' });
  assert.ok(html.includes('title="Live seat details are unavailable for this section."'));
});

test('verified rows mid-hydration keep the Loading seats chip', () => {
  const html = renderCard({
    section: { id: 's1', course_code: 'BIO 201' },
    availability: {
      phase: 'verified',
      status: 'Open',
      seatsAvailable: null,
      waitlistTotal: null,
      capacity: null,
      current: true,
      detailsPending: true,
      error: null,
      source: 'live',
    },
  });
  const [, seats] = chips(html);
  assert.deepEqual(seats, { class: 'is-loading', text: 'Loading seats' });
});

test('active status filters with global verification failure avoid a misleading no-matches claim', () => {
  const candidates = [
    { id: 'a', course_code: 'BIO 201' },
    { id: 'b', course_code: 'CHEM 202' },
  ];
  const unavailable = {
    phase: 'unavailable',
    status: 'Unavailable',
    seatsAvailable: null,
    waitlistTotal: null,
    capacity: null,
    current: false,
    detailsPending: false,
    error: { message: 'Unable to verify live availability.' },
    source: null,
  };
  const { html } = renderEmptyPanel({ candidates, availability: { a: unavailable, b: unavailable } });

  assert.match(html, /role="status"/);
  assert.match(html, /aria-live="polite"/);
  assert.match(html, /Live availability couldn&#39;t be verified, so status filters can&#39;t be applied yet\./);
  assert.doesNotMatch(html, /No sections match your filters\./);
  assert.doesNotMatch(html, /No selected courses match your filters\./);
});

test('partial unavailability with verified candidates still refuses to claim no matches', () => {
  const candidates = [
    { id: 'a', course_code: 'BIO 201' },
    { id: 'b', course_code: 'CHEM 202' },
  ];
  const { html } = renderEmptyPanel({ candidates, availability: {
    a: { phase: 'verified', status: 'Closed', seatsAvailable: 0, waitlistTotal: null, capacity: null, current: true, detailsPending: false, error: null, source: 'live' },
    b: { phase: 'unavailable', status: 'Unavailable', seatsAvailable: null, waitlistTotal: null, capacity: null, current: false, detailsPending: false, error: { message: 'boom' }, source: null },
  } });
  assert.match(html, /role="status"/);
  assert.match(html, /aria-live="polite"/);
  assert.match(html, /Live availability couldn&#39;t be verified, so status filters can&#39;t be applied yet\./);
  assert.doesNotMatch(html, /No sections match your filters\./);
});

test('unverified candidates also block a no-matches claim while status filters are active', () => {
  const candidates = [
    { id: 'a', course_code: 'BIO 201' },
    { id: 'b', course_code: 'CHEM 202' },
  ];
  const { html } = renderEmptyPanel({ candidates, availability: {
    a: { phase: 'verified', status: 'Closed', seatsAvailable: 0, waitlistTotal: null, capacity: null, current: true, detailsPending: false, error: null, source: 'live' },
    b: { phase: 'unverified', status: 'Open', seatsAvailable: 3, waitlistTotal: null, capacity: null, current: false, detailsPending: false, error: null, source: 'catalog' },
  } });
  assert.match(html, /role="status"/);
  assert.match(html, /aria-live="polite"/);
  assert.match(html, /Live availability couldn&#39;t be verified, so status filters can&#39;t be applied yet\./);
  assert.doesNotMatch(html, /No sections match your filters\./);
});

test('active status filters with pending verification explain the delay instead of no matches', () => {
  const candidates = [{ id: 'a', course_code: 'BIO 201' }];
  const { html, summary } = renderEmptyPanel({ candidates, availability: {
    a: { phase: 'pending', status: 'Checking', seatsAvailable: null, waitlistTotal: null, capacity: null, current: false, detailsPending: false, error: null, source: null },
  } });
  assert.match(html, /role="status"/);
  assert.match(html, /aria-live="polite"/);
  assert.match(html, /Live availability is still verifying\. Status filters will apply once checks finish\./);
  assert.doesNotMatch(html, /No sections match your filters\./);
  assert.ok(summary.textContent.includes('0'));
});

test('genuine no-match results with verified candidates keep the standard empty state', () => {
  const candidates = [
    { id: 'a', course_code: 'BIO 201' },
    { id: 'b', course_code: 'CHEM 202' },
  ];
  const { html } = renderEmptyPanel({ candidates, statusFilters: ['closed'], availability: {
    a: { phase: 'verified', status: 'Open', seatsAvailable: 1, waitlistTotal: null, capacity: null, current: true, detailsPending: false, error: null, source: 'live' },
    b: { phase: 'verified', status: 'Open', seatsAvailable: 2, waitlistTotal: null, capacity: null, current: true, detailsPending: false, error: null, source: 'live' },
  } });
  assert.doesNotMatch(html, /role="status"/);
  assert.match(html, /No sections match your filters\./);
});

function renderEmptyPanel({ candidates, availability, statusFilters = ['open'] }) {
  const summary = { textContent: '' };
  const content = { innerHTML: '', scrollTop: 0 };
  const documentStub = {
    getElementById: (id) => {
      if (id === 'courses-result-summary') return summary;
      if (id === 'courses-panel-content') return content;
      return null;
    },
    querySelector: () => null,
    querySelectorAll: () => [],
  };
  const context = { window: {}, document: documentStub };
  vm.createContext(context);
  vm.runInContext(panelSource, context, { filename: 'panel.js' });
  context.window.APStudyCoursesPanel.create({
    state: { ...baseState(), statusFilters: new Set(statusFilters) },
    COURSE_COLOR_PALETTE: [{ key: 'course-color-01' }],
    COURSE_DAYS: [{ key: 'Mon', index: 1 }],
    COURSE_RESULT_LIMIT: 100,
    getFilteredSections: (options) => (options && options.ignoreStatus ? candidates : []),
    getSection: () => null,
    isTrackable: () => false,
    getEffectiveAvailability: (section) => availability[section.id],
    utils: UTILS,
  }).renderPanel();
  return { html: content.innerHTML, summary };
}
