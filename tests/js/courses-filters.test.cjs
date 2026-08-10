const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function loadFilters() {
  const source = fs.readFileSync(path.join(__dirname, '../../static/js/courses/filters.js'), 'utf8');
  const context = { window: {} };
  vm.createContext(context);
  vm.runInContext(source, context);
  return context.window.APStudyCoursesFilters;
}

function createFilters(statusFilters) {
  const state = {
    activeCourseView: 'search',
    sections: [
      { id: 'open', term: 'Fall_2026', enrollment_status: 'Open' },
      { id: 'closed', term: 'Fall_2026', enrollment_status: 'Closed' },
      { id: 'waitlist', term: 'Fall_2026', enrollment_status: 'Waitlist' },
    ],
    selectedTerm: 'Fall_2026',
    searchQuery: '',
    campusFilter: 'all',
    requirementFilter: 'all',
    statusFilters: new Set(statusFilters),
    dayFilters: new Set(),
    timeEnabled: false,
    savedCoursesBySection: new Map(),
    tracksBySection: new Map(),
    removedSelectedSections: new Map(),
  };
  return loadFilters().create({
    state,
    COURSE_START_MINUTES: 360,
    COURSE_END_MINUTES: 1440,
    getSection: () => null,
    rememberSection: (section) => section,
    utils: {
      buildSectionSearchBlob: () => '',
      compareCourseSections: () => 0,
      parseAtlasTimeToken: () => null,
      parseTimeInput: () => null,
    },
  });
}

test('courses availability filter supports multiple selected enrollment statuses', () => {
  const { getFilteredSections } = createFilters(['open', 'waitlist']);
  assert.deepEqual(
    getFilteredSections().map((section) => section.id),
    ['open', 'waitlist'],
  );
});

test('courses availability filter treats an empty checklist as all statuses', () => {
  const { getFilteredSections } = createFilters([]);
  assert.equal(getFilteredSections().length, 3);
});
