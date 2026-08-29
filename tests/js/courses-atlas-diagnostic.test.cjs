const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const ROOT = path.join(__dirname, '../..');

function buildContext(fetchImpl, globals = {}) {
  const context = {
    console,
    URL,
    fetch: fetchImpl,
    Date,
    performance: { now: () => Date.now() },
    window: {
      APSTUDY_ATLAS_SRCDB: { Fall_2026: '5269' },
      APSTUDY_COURSES_DEFAULT_TERM: 'Fall_2026',
      APSTUDY_ATLAS_DIAGNOSTIC_ENABLED: false,
      APSTUDY_ATLAS_DIAGNOSTIC_CONFIG: {},
      ...globals,
    },
  };
  context.window.console = console;
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(path.join(ROOT, 'static/js/courses/atlas-live.js'), 'utf8'), context);
  vm.runInContext(fs.readFileSync(path.join(ROOT, 'static/js/courses/atlas-diagnostic.js'), 'utf8'), context);
  return context;
}

function jsonResponse(status, body, contentType = 'application/json') {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Map([['content-type', contentType]]),
    text: async () => (typeof body === 'string' ? body : JSON.stringify(body)),
  };
}

function recordingFetch(responses) {
  const calls = [];
  const impl = async (url, options) => {
    calls.push({ url, options });
    const index = calls.length - 1;
    const responder = responses[index];
    if (!responder) throw new Error(`Unexpected request ${index}: ${url}`);
    return responder();
  };
  impl.calls = calls;
  return impl;
}

async function waitFor(predicate, timeoutMs = 2000) {
  const startedAt = Date.now();
  while (Date.now() - startedAt < timeoutMs) {
    const value = predicate();
    if (value) return value;
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  return null;
}

test('diagnostic issues exactly one subject search and one details request with the FOSE contract', async () => {
  const fetchImpl = recordingFetch([
    () => jsonResponse(200, {
      results: [{
        code: 'CS 170',
        title: 'Intro to Computer Science',
        crn: '11111',
        no: '1',
        enrl_stat: 'O',
        key: 'atlas-key-1',
      }],
    }),
    () => jsonResponse(200, {
      key: 'atlas-key-1',
      crn: '11111',
      seats: '<div>Maximum Enrollment: 30 Seats Avail: 12</div>',
      enrl_stat_html: '<span>Open</span>',
    }),
  ]);
  const context = buildContext(fetchImpl);
  const report = await context.window.APSTUDY_ATLAS_DIAGNOSTIC.run();

  assert.equal(fetchImpl.calls.length, 2);
  assert.match(fetchImpl.calls[0].url, /page=fose&route=search/);
  assert.match(fetchImpl.calls[1].url, /page=fose&route=details/);

  for (const call of fetchImpl.calls) {
    assert.equal(call.options.method, 'POST');
    assert.equal(call.options.mode, 'cors');
    assert.equal(call.options.credentials, 'omit');
    assert.equal(call.options.headers['Content-Type'], 'application/json');
    assert.equal(call.options.headers['X-Requested-With'], 'XMLHttpRequest');
  }
  const searchBody = JSON.parse(fetchImpl.calls[0].options.body);
  assert.deepEqual(searchBody.other, { srcdb: '5269' });
  assert.deepEqual(searchBody.criteria, [{ field: 'subject', value: 'CS' }]);
  const detailsBody = JSON.parse(fetchImpl.calls[1].options.body);
  assert.equal(detailsBody.group, 'key:atlas-key-1');
  assert.equal(detailsBody.other.srcdb, '5269');
  assert.equal(report.config.keyInUse, 'atlas-key-1');
  assert.equal(report.config.keySource, 'derived-from-search');

  const [searchRecord, detailsRecord] = report.requests;
  assert.equal(searchRecord.label, 'subject-search');
  assert.equal(searchRecord.classification, 'usable_json');
  assert.equal(detailsRecord.classification, 'usable_json');
  assert.equal(searchRecord.headers['content-type'], 'application/json');
  assert.equal(searchRecord.normalization.attempted, true);
  assert.equal(searchRecord.normalization.ok, true);
  assert.equal(searchRecord.normalization.sampleId, 'Fall_2026|CS|170|11111|1');
  assert.equal(searchRecord.normalization.sampleAtlasKey, 'atlas-key-1');
  assert.equal(detailsRecord.jsonShape.fields.key, true);
  assert.equal(detailsRecord.jsonShape.fields.crn, true);
  assert.equal(detailsRecord.jsonShape.fields.seats, true);
  assert.equal(report.summary.verdict, 'usable_json');
  assert.equal(report.summary.allUsable, true);
  assert.equal(context.window.APSTUDY_ATLAS_DIAGNOSTIC.lastReport, report);
});

test('diagnostic auto-runs once on load when enabled', async () => {
  const fetchImpl = recordingFetch([
    () => jsonResponse(200, { results: [{ code: 'CS 170', crn: '1', no: '1', enrl_stat: 'O' }] }),
    () => jsonResponse(200, { key: 'k', crn: '1', seats: 'x' }),
  ]);
  const context = buildContext(fetchImpl, { APSTUDY_ATLAS_DIAGNOSTIC_ENABLED: true });
  const report = await waitFor(() => context.window.APSTUDY_ATLAS_DIAGNOSTIC.lastReport);
  assert.ok(report, 'expected an auto-run report');
  assert.equal(fetchImpl.calls.length, 2);
});

test('diagnostic does not auto-run when the gate is disabled', async () => {
  const fetchImpl = recordingFetch([]);
  const context = buildContext(fetchImpl, { APSTUDY_ATLAS_DIAGNOSTIC_ENABLED: false });
  assert.ok(context.window.APSTUDY_ATLAS_DIAGNOSTIC, 'console handle should still exist');
  await new Promise((resolve) => setTimeout(resolve, 60));
  assert.equal(fetchImpl.calls.length, 0);
  assert.equal(context.window.APSTUDY_ATLAS_DIAGNOSTIC.lastReport, null);
});

test('WAF 202 challenge HTML classifies as waf_challenge_html', async () => {
  const challengeHtml = '<html><head><title>Just a moment...</title></head>'
    + '<body><div id="challenge-platform">Checking your browser before accessing</div></body></html>';
  const fetchImpl = recordingFetch([
    () => jsonResponse(202, challengeHtml, 'text/html'),
    () => jsonResponse(200, { key: 'k', crn: '1', seats: 'x' }),
  ]);
  const context = buildContext(fetchImpl);
  const report = await context.window.APSTUDY_ATLAS_DIAGNOSTIC.run();
  const [searchRecord] = report.requests;
  assert.equal(searchRecord.status, 202);
  assert.equal(searchRecord.bodyKind, 'html-challenge');
  assert.equal(searchRecord.classification, 'waf_challenge_html');
  assert.match(searchRecord.classificationDetail, /WAF or browser challenge/);
  assert.equal(report.summary.verdict, 'waf_challenge_html+usable_json');
  assert.equal(report.summary.allUsable, false);
});

test('403 responses classify as http_403 and keep the body preview', async () => {
  const fetchImpl = recordingFetch([
    () => jsonResponse(403, 'Forbidden', 'text/plain'),
    () => jsonResponse(403, '<html>blocked</html>', 'text/html'),
  ]);
  const context = buildContext(fetchImpl);
  const report = await context.window.APSTUDY_ATLAS_DIAGNOSTIC.run();
  assert.equal(report.requests[0].classification, 'http_403');
  assert.equal(report.requests[0].bodyKind, 'text');
  assert.equal(report.requests[0].bodyPreview, 'Forbidden');
  assert.equal(report.requests[1].classification, 'http_403');
  assert.equal(report.requests[1].bodyKind, 'html');
  assert.equal(report.summary.verdict, 'http_403');
});

test('non-JSON 200 bodies and unusable JSON classify as malformed_data', async () => {
  const fetchImpl = recordingFetch([
    () => jsonResponse(200, '<html><body>Not API data</body></html>', 'text/html'),
    () => jsonResponse(200, { unexpected: true }),
  ]);
  const context = buildContext(fetchImpl);
  const report = await context.window.APSTUDY_ATLAS_DIAGNOSTIC.run();
  assert.equal(report.requests[0].classification, 'malformed_data');
  assert.equal(report.requests[0].bodyKind, 'html');
  assert.equal(report.requests[1].classification, 'malformed_data');
  assert.equal(report.requests[1].bodyKind, 'json');
  assert.equal(report.requests[1].jsonShape.shape, 'unrecognized-details');
  assert.equal(report.summary.verdict, 'malformed_data');
});

test('empty results and fatal payloads classify as malformed_data', async () => {
  const fetchImpl = recordingFetch([
    () => jsonResponse(200, { fatal: null, results: [] }),
    () => jsonResponse(200, { fatal: 'No section found.' }),
  ]);
  const context = buildContext(fetchImpl);
  const report = await context.window.APSTUDY_ATLAS_DIAGNOSTIC.run();
  assert.equal(report.requests[0].classification, 'malformed_data');
  assert.equal(report.requests[0].jsonShape.shape, 'empty-results');
  assert.equal(report.requests[1].classification, 'malformed_data');
  assert.match(report.requests[1].classificationDetail, /fatal/);
  const detailsBody = JSON.parse(fetchImpl.calls[1].options.body);
  assert.equal(detailsBody.group, 'key:DIAGNOSTIC-PLACEHOLDER');
});

test('rejected fetches classify as network_or_cors_blocked without a status', async () => {
  const calls = [];
  const impl = async (url, options) => {
    calls.push({ url, options });
    throw new TypeError('Failed to fetch');
  };
  impl.calls = calls;
  const context = buildContext(impl);
  const report = await context.window.APSTUDY_ATLAS_DIAGNOSTIC.run();
  assert.equal(impl.calls.length, 2);
  for (const record of report.requests) {
    assert.equal(record.status, null);
    assert.equal(record.classification, 'network_or_cors_blocked');
    assert.equal(record.error.name, 'TypeError');
    assert.equal(record.error.message, 'Failed to fetch');
  }
  assert.equal(report.summary.verdict, 'network_or_cors_blocked');
});

test('diagnostic config overrides subject and details key', async () => {
  const fetchImpl = recordingFetch([
    () => jsonResponse(200, { results: [{ code: 'CHEM 150', crn: '22222', no: '1', enrl_stat: 'O', key: 'atlas-key-1' }] }),
    () => jsonResponse(200, { key: 'real-key', crn: '22222', seats: 'x' }),
  ]);
  const context = buildContext(fetchImpl, {
    APSTUDY_ATLAS_DIAGNOSTIC_CONFIG: { subject: 'chem', key: 'real-key' },
  });
  await context.window.APSTUDY_ATLAS_DIAGNOSTIC.run();
  const searchBody = JSON.parse(fetchImpl.calls[0].options.body);
  assert.deepEqual(searchBody.criteria, [{ field: 'subject', value: 'CHEM' }]);
  const detailsBody = JSON.parse(fetchImpl.calls[1].options.body);
  assert.equal(detailsBody.group, 'key:real-key');
  assert.equal(context.window.APSTUDY_ATLAS_DIAGNOSTIC.lastReport.config.keySource, 'query-param');
  assert.equal(context.window.APSTUDY_ATLAS_DIAGNOSTIC.lastReport.config.keyInUse, 'real-key');
});

test('details key derives from the first search result atlas_key field', async () => {
  const fetchImpl = recordingFetch([
    () => jsonResponse(200, { results: [{ code: 'CS 170', atlas_key: 'k-atlas-field' }] }),
    () => jsonResponse(200, { key: 'k-atlas-field', crn: '1', seats: 'x' }),
  ]);
  const context = buildContext(fetchImpl);
  const report = await context.window.APSTUDY_ATLAS_DIAGNOSTIC.run();
  const detailsBody = JSON.parse(fetchImpl.calls[1].options.body);
  assert.equal(detailsBody.group, 'key:k-atlas-field');
  assert.equal(report.config.keySource, 'derived-from-search');
  assert.equal(report.requests.length, 2);
});

test('details key falls back to placeholder when the first search result has no key', async () => {
  const fetchImpl = recordingFetch([
    () => jsonResponse(200, { results: [{ code: 'CS 170', crn: '1' }, { key: 'k-second' }] }),
    () => jsonResponse(200, { fatal: 'No section found.' }),
  ]);
  const context = buildContext(fetchImpl);
  const report = await context.window.APSTUDY_ATLAS_DIAGNOSTIC.run();
  const detailsBody = JSON.parse(fetchImpl.calls[1].options.body);
  assert.equal(detailsBody.group, 'key:DIAGNOSTIC-PLACEHOLDER');
  assert.equal(report.config.keySource, 'placeholder');
  assert.equal(report.requests.length, 2);
});

test('missing term mapping reports a config error without any request', async () => {
  const fetchImpl = recordingFetch([]);
  const context = buildContext(fetchImpl, {
    APSTUDY_COURSES_DEFAULT_TERM: 'Spring_2099',
  });
  const report = await context.window.APSTUDY_ATLAS_DIAGNOSTIC.run();
  assert.equal(fetchImpl.calls.length, 0);
  assert.equal(report.summary.verdict, 'config_error');
  assert.equal(report.requests.length, 0);
  assert.match(report.config.error, /srcdb mapping/);
});
