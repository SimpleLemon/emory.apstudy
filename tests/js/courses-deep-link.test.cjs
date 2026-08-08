const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function loadCoursesUtils() {
  const source = fs.readFileSync(path.join(__dirname, '../../static/js/courses/utils.js'), 'utf8');
  const context = {
    console,
    URLSearchParams,
    window: { APStudyUIPrimitives: { escapeHtml: String } },
  };
  vm.createContext(context);
  vm.runInContext(source, context);
  return context.window.APStudyCoursesUtils;
}

test('parseCoursesSectionDeepLink reads hash section param', () => {
  const { parseCoursesSectionDeepLink } = loadCoursesUtils();
  const sectionId = 'Spring_2026|JPN|101|1234|1';
  const encoded = encodeURIComponent(sectionId);
  const parsed = parseCoursesSectionDeepLink({
    hash: `#section=${encoded}`,
    search: '',
  });
  assert.equal(parsed, sectionId);
});

test('parseCoursesSectionDeepLink falls back to query section param', () => {
  const { parseCoursesSectionDeepLink } = loadCoursesUtils();
  const sectionId = 'Spring_2026|JPN|101|1234|1';
  const encoded = encodeURIComponent(sectionId);
  const parsed = parseCoursesSectionDeepLink({
    hash: '',
    search: `?section=${encoded}`,
  });
  assert.equal(parsed, sectionId);
});

test('parseCoursesSectionDeepLink returns null when missing', () => {
  const { parseCoursesSectionDeepLink } = loadCoursesUtils();
  assert.equal(parseCoursesSectionDeepLink({ hash: '', search: '' }), null);
});
