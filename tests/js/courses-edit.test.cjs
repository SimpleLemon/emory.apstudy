const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function loadEditHelpers() {
  const source = fs.readFileSync(path.join(__dirname, '../../static/js/courses/edit.js'), 'utf8');
  const context = { window: {} };
  vm.createContext(context);
  vm.runInContext(source, context);
  return context.window.APStudyCoursesEdit;
}

function parseAtlasTimeToken(value) {
  const [hour, minute] = String(value).match(/^(\d{2})(\d{2})$/)?.slice(1) || [];
  return hour === undefined ? null : Number(hour) * 60 + Number(minute);
}

function timeInputToAtlasToken(value) {
  return /^\d{2}:\d{2}$/.test(String(value)) ? String(value).replace(":", "") : "";
}

test('meeting overrides preserve multiple same-day meetings in editor order', () => {
  const { collectMeetingOverrides } = loadEditHelpers();
  const meetings = collectMeetingOverrides([
    { day: 'Thu', start: '16:00', end: '17:15' },
    { day: 'Thu', start: '17:15', end: '19:20' },
  ], {
    COURSE_DAYS: [{ key: 'Mon' }, { key: 'Tue' }, { key: 'Wed' }, { key: 'Thu' }, { key: 'Fri' }],
    parseAtlasTimeToken,
    timeInputToAtlasToken,
  });

  assert.deepEqual(JSON.parse(JSON.stringify(meetings)), [
    { day: 'Thu', start: '1600', end: '1715' },
    { day: 'Thu', start: '1715', end: '1920' },
  ]);
});

test('meeting overrides omit invalid rows without dropping valid meetings', () => {
  const { collectMeetingOverrides } = loadEditHelpers();
  const meetings = collectMeetingOverrides([
    { day: 'Thu', start: '16:00', end: '17:15' },
    { day: 'Thu', start: '19:20', end: '17:15' },
    { day: 'Invalid', start: '09:00', end: '09:50' },
  ], {
    COURSE_DAYS: [{ key: 'Thu' }],
    parseAtlasTimeToken,
    timeInputToAtlasToken,
  });

  assert.deepEqual(JSON.parse(JSON.stringify(meetings)), [{ day: 'Thu', start: '1600', end: '1715' }]);
});

test('meeting removal focus advances, falls back, or returns to Add meeting', () => {
  const { meetingRemovalFocusPlan } = loadEditHelpers();

  assert.deepEqual(JSON.parse(JSON.stringify(meetingRemovalFocusPlan(0, 3))), { rowIndex: 1 });
  assert.deepEqual(JSON.parse(JSON.stringify(meetingRemovalFocusPlan(2, 3))), { rowIndex: 1 });
  assert.deepEqual(JSON.parse(JSON.stringify(meetingRemovalFocusPlan(0, 1))), { focusAddButton: true });
});
