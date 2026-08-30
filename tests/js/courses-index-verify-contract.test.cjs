const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const INDEX_PATH = path.join(__dirname, '../../static/js/courses/index.js');
const CONTROLS_PATH = path.join(__dirname, '../../static/js/courses/controls.js');
const TEMPLATE_PATH = path.join(__dirname, '../../templates/courses.html');

const indexSource = fs.readFileSync(INDEX_PATH, 'utf8');
const controlsSource = fs.readFileSync(CONTROLS_PATH, 'utf8');
const templateSource = fs.readFileSync(TEMPLATE_PATH, 'utf8');

function extractBracedBlock(source, anchor, label) {
  const anchorIndex = source.indexOf(anchor);
  assert.ok(anchorIndex !== -1, `expected to find ${label || anchor}`);
  const openBrace = source.indexOf('{', anchorIndex);
  assert.ok(openBrace !== -1, `expected a block after ${label || anchor}`);
  let depth = 0;
  for (let i = openBrace; i < source.length; i += 1) {
    if (source[i] === '{') depth += 1;
    else if (source[i] === '}') {
      depth -= 1;
      if (depth === 0) return source.slice(openBrace, i + 1);
    }
  }
  assert.fail(`unbalanced braces in ${label || anchor}`);
}

test('index.js wires exactly one availability verifier into the courses filters', () => {
  const createCalls = indexSource.match(/APStudyCoursesVerify\.create\(/g) || [];
  assert.equal(createCalls.length, 1);
  assert.match(
    indexSource,
    /const availabilityVerifier = window\.APStudyCoursesVerify\.create\(\{\s*onStatusProgress: renderCourses,\s*\}\);/,
  );
  assert.match(
    indexSource,
    /getEffectiveAvailability: \(section\) => availabilityVerifier\.getEffectiveAvailability\(section\)/,
  );
});

test('verifyCurrentAvailability hands startQuery the built query input and availability candidates', () => {
  const verifyBody = extractBracedBlock(indexSource, 'async function verifyCurrentAvailability()');
  assert.match(verifyBody, /const \{ sectionIds \} = getAvailabilityCandidates\(\);/);
  assert.match(
    verifyBody,
    /availabilityVerifier\.startQuery\(\{\s*queryInput: buildAvailabilityQueryInput\(\),\s*sectionIds,\s*\}\)/,
  );
  assert.match(verifyBody, /settledState = await queryPromise;/);
  assert.match(verifyBody, /availabilityVerifier\.getState\(\)/);
  assert.match(verifyBody, /currentState\.generation !== settledState\.generation/);
});

test('startQuery runs even when the section list is empty (no early empty return)', () => {
  const verifyBody = extractBracedBlock(indexSource, 'async function verifyCurrentAvailability()');
  const startQueryIndex = verifyBody.indexOf('availabilityVerifier.startQuery(');
  const awaitIndex = verifyBody.indexOf('settledState = await queryPromise;');
  assert.ok(startQueryIndex !== -1 && awaitIndex !== -1);
  assert.ok(startQueryIndex < awaitIndex);
  assert.doesNotMatch(verifyBody, /sectionIds\.length/);
  assert.doesNotMatch(verifyBody, /if \(!candidates\.length\)/);
});

test('buildAvailabilityQueryInput uses the exact verify query keys', () => {
  const body = extractBracedBlock(indexSource, 'function buildAvailabilityQueryInput()');
  const keys = [...body.matchAll(/^ {4}([A-Za-z_]\w*):/gm)].map((match) => match[1]);
  assert.deepEqual(keys, [
    'term',
    'query',
    'days',
    'timeEnabled',
    'timeStart',
    'timeEnd',
    'campus',
    'requirement',
  ]);
  assert.doesNotMatch(body, /statuses|statusFilters/);
});

test('index.js never sends statuses directly to the Atlas sections endpoint', () => {
  assert.doesNotMatch(indexSource, /params\.set\("statuses",/);
});

test('availability candidates ignore status filters so pending rows still verify', () => {
  const candidatesBody = extractBracedBlock(indexSource, 'function getAvailabilityCandidates()');
  assert.match(candidatesBody, /getFilteredSections\(\{ ignoreStatus: true \}\)/);
  assert.match(candidatesBody, /section\?\.id \|\| section\?\.section_id/);

  const renderCoursesBody = extractBracedBlock(indexSource, 'function renderCourses()');
  assert.match(renderCoursesBody, /getFilteredSections\(\{ ignoreStatus: true \}\)/);
  assert.match(renderCoursesBody, /state\.statusFilters\.size && isAvailabilityVerificationPending\(candidates\)/);
  assert.match(renderCoursesBody, /renderAvailabilityPendingState\(\);/);
  assert.match(renderCoursesBody, /renderPanel\(\);/);
});

test('renderCourses keeps loading, error, edit, detail, and section-loading screens ahead of verification states', () => {
  const renderCoursesBody = extractBracedBlock(indexSource, 'function renderCourses()');
  const guardMatch = renderCoursesBody.match(
    /state\.loading \|\| state\.error \|\| state\.editingSectionId \|\| state\.detailSectionId \|\| state\.sectionsLoading/,
  );
  assert.ok(guardMatch, 'renderCourses must delegate precedence states to renderPanel first');
  assert.ok(guardMatch.index < renderCoursesBody.indexOf('isAvailabilityVerificationPending'));
});

test('verification pending state uses explicit aria-live markup without a duplicate summary id', () => {
  const pendingBody = extractBracedBlock(indexSource, 'function renderAvailabilityPendingState()');
  assert.match(pendingBody, /Verifying live availability…/);
  assert.match(pendingBody, /role="status"/);
  assert.match(pendingBody, /aria-live="polite"/);
  assert.match(pendingBody, /courses-result-summary/);
  assert.match(pendingBody, /summary\.textContent = "Verifying live availability…"/);
  assert.doesNotMatch(pendingBody, /id="courses-result-summary"/);
});

test('switching the active view re-verifies candidates so status filters work in selected and tracked views', () => {
  const toggleBody = extractBracedBlock(controlsSource, '.courses-view-toggle")?.addEventListener', 'view toggle listener');
  assert.match(toggleBody, /state\.activeCourseView = nextView/);
  assert.match(toggleBody, /renderPanel\(\);/);
  assert.match(toggleBody, /void verifyCurrentAvailability\(\);/);
  assert.match(controlsSource, /verifyCurrentAvailability,\s*\n\s*meetingRemovalFocusPlan,/);
});

test('renderCourses renders the exact verifying copy while availability is pending', () => {
  const pendingBody = extractBracedBlock(indexSource, 'function renderAvailabilityPendingState()');
  assert.match(pendingBody, /Verifying live availability…/);
  assert.match(pendingBody, /courses-result-summary/);
  const copyIndex = pendingBody.indexOf('Verifying live availability…');
  assert.ok(copyIndex !== -1);
});

test('status checkboxes update statusFilters, clear detail and edit context, and rerender without reloading sections', () => {
  const listener = extractBracedBlock(
    controlsSource,
    'getElementById("courses-availability-filter")',
    'availability filter listener',
  );
  const addIndex = listener.indexOf('state.statusFilters.add(status)');
  const deleteIndex = listener.indexOf('state.statusFilters.delete(status)');
  const clearContextIndex = listener.indexOf('clearDetailReturnContext();');
  const detailIndex = listener.indexOf('state.detailSectionId = null;');
  const editIndex = listener.indexOf('state.editingSectionId = null;');
  const renderIndex = listener.indexOf('renderCourses();');
  assert.ok(addIndex !== -1, 'listener must add to statusFilters');
  assert.ok(deleteIndex !== -1, 'listener must delete from statusFilters');
  assert.ok(clearContextIndex !== -1, 'listener must clear the detail return context');
  assert.ok(detailIndex !== -1, 'listener must close the detail view');
  assert.ok(editIndex !== -1, 'listener must close the edit view');
  assert.ok(renderIndex !== -1, 'listener must call renderCourses');
  assert.ok(Math.max(addIndex, deleteIndex, clearContextIndex, detailIndex, editIndex) < renderIndex,
    'statusFilters and detail/edit clearing must happen before renderCourses');
  assert.doesNotMatch(listener, /scheduleSectionReload|loadSectionsForTerm/);
});

test('search input keeps its 500ms debounced section reload', () => {
  assert.match(controlsSource, /void loadSectionsForTerm\(state\.selectedTerm\);\s*\}, 500\);/);
  assert.doesNotMatch(controlsSource, /, 250\);/);
});

test('courses template loads verify.js before index.js and drops the dead atlas-live script', () => {
  const verifyScriptIndex = templateSource.indexOf('js/courses/verify.js');
  const indexScriptIndex = templateSource.indexOf('js/courses/index.js');
  assert.ok(verifyScriptIndex !== -1, 'courses template must load js/courses/verify.js');
  assert.ok(indexScriptIndex !== -1, 'courses template must load js/courses/index.js');
  assert.ok(verifyScriptIndex < indexScriptIndex, 'courses template must load verify.js before index.js');
  assert.doesNotMatch(templateSource, /js\/courses\/atlas-live\.js/);
});

test('isTrackable only accepts current verified Closed sections or zero live seats', () => {
  const body = extractBracedBlock(indexSource, 'function isTrackable(section)');
  assert.match(body, /if \(section\?\.is_cancelled\) return false;/);
  assert.match(body, /availabilityVerifier\.getEffectiveAvailability\(section\)/);
  assert.match(body, /availability\.phase !== "verified" \|\| availability\.current !== true/);
  assert.match(body, /String\(availability\.status \|\| ""\)\.toLowerCase\(\) === "closed"\) return true;/);
  assert.match(body, /return availability\.seatsAvailable === 0;/);
  assert.doesNotMatch(body, /enrollment_status|seats_available/);
});

test('visible detail hydration hydrates through the verifier with overscan and a current-generation rerender', () => {
  const hydrateBody = extractBracedBlock(indexSource, 'async function hydrateVisibleLiveSections()');
  assert.match(hydrateBody, /availabilityVerifier\.requestDetails\(sectionIds\)/);
  assert.match(hydrateBody, /beforeState\.generation !== currentState\.generation/);
  assert.match(hydrateBody, /currentState\.generation !== settledState\.generation/);
  assert.match(hydrateBody, /currentState\.detailErrors\.size !== beforeState\.detailErrors\.size/);
  assert.match(hydrateBody, /rerenderAfterLiveHydration\(\)/);
  assert.doesNotMatch(hydrateBody, /fetchJson\(/);

  const visibleBody = extractBracedBlock(indexSource, 'function visibleHydrationSectionIds()');
  assert.match(visibleBody, /COURSE_LIVE_HYDRATION_OVERSCAN/);

  assert.doesNotMatch(indexSource, /section-status\/batch/);
  assert.doesNotMatch(indexSource, /liveHydrationInFlight|liveHydrationFailures/);
});
