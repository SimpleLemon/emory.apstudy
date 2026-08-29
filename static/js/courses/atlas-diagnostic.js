(function () {
  const ATLAS_BASE_URL = "https://atlas.emory.edu/api/";
  const BODY_PREVIEW_LIMIT = 400;
  const CHALLENGE_SCAN_LIMIT = 4000;
  const DETAILS_PLACEHOLDER_KEY = "DIAGNOSTIC-PLACEHOLDER";
  const REQUEST_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
  };
  const CHALLENGE_MARKERS = [
    "just a moment",
    "attention required",
    "checking your browser",
    "cf-browser-verification",
    "cf_chl_",
    "challenge-platform",
    "cdn-cgi/challenge",
    "cf-error-details",
    "cloudflare",
  ];
  let lastReport = null;

  function diagnosticsEnabled() {
    return window.APSTUDY_ATLAS_DIAGNOSTIC_ENABLED === true;
  }

  function monotonicNow() {
    if (typeof performance !== "undefined" && typeof performance.now === "function") {
      return performance.now();
    }
    return Date.now();
  }

  function currentOrigin() {
    if (typeof location !== "undefined" && location.origin) return location.origin;
    return "";
  }

  function currentUserAgent() {
    if (typeof navigator !== "undefined" && navigator.userAgent) return navigator.userAgent;
    return "";
  }

  function resolveConfig() {
    const term = String(window.APSTUDY_COURSES_DEFAULT_TERM || "").trim();
    const srcdb = term && window.APSTUDY_ATLAS_SRCDB ? window.APSTUDY_ATLAS_SRCDB[term] : null;
    const override = window.APSTUDY_ATLAS_DIAGNOSTIC_CONFIG || {};
    const subject = String(override.subject || "CS").trim().toUpperCase();
    const key = String(override.key || "").trim();
    if (!term) return { error: "No default term configured (APSTUDY_COURSES_DEFAULT_TERM)." };
    if (!srcdb) return { error: `No Atlas srcdb mapping for term ${term}.` };
    if (!subject) return { error: "No diagnostic subject configured." };
    return {
      term,
      srcdb,
      subject,
      key,
      keyInUse: key || DETAILS_PLACEHOLDER_KEY,
      keySource: key ? "query-param" : "placeholder",
    };
  }

  function foseUrl(route) {
    const url = new URL(ATLAS_BASE_URL);
    url.searchParams.set("page", "fose");
    url.searchParams.set("route", route);
    return url.toString();
  }

  function captureHeaders(response) {
    const headers = {};
    try {
      if (response.headers && typeof response.headers.forEach === "function") {
        response.headers.forEach((value, name) => {
          headers[String(name).toLowerCase()] = String(value);
        });
      } else if (response.headers && typeof response.headers[Symbol.iterator] === "function") {
        for (const [name, value] of response.headers) {
          headers[String(name).toLowerCase()] = String(value);
        }
      }
    } catch (error) {
      return {};
    }
    return headers;
  }

  function looksLikeChallenge(text) {
    const sample = String(text || "").slice(0, CHALLENGE_SCAN_LIMIT).toLowerCase();
    return CHALLENGE_MARKERS.some((marker) => sample.includes(marker));
  }

  function inspectBody(text, contentType) {
    const trimmed = String(text || "").trim();
    if (!trimmed) return { kind: "empty", json: null };
    try {
      return { kind: "json", json: JSON.parse(trimmed) };
    } catch (error) {
      const loweredContentType = String(contentType || "").toLowerCase();
      if (loweredContentType.includes("html") || trimmed.startsWith("<")) {
        return { kind: looksLikeChallenge(trimmed) ? "html-challenge" : "html", json: null };
      }
      return { kind: "text", json: null };
    }
  }

  function normalizationProbe(term, raw) {
    const normalize = window.APStudyAtlasLive && window.APStudyAtlasLive.normalizeRawSection;
    if (typeof normalize !== "function") {
      return { attempted: false, ok: false, error: "APStudyAtlasLive.normalizeRawSection unavailable." };
    }
    try {
      const row = normalize(term, raw);
      const ok = Boolean(row && row.subject);
      return {
        attempted: true,
        ok,
        sampleId: row ? row.id : null,
        sampleSubject: row ? row.subject : null,
        sampleAtlasKey: row ? row.atlas_key : null,
        error: ok ? null : "Normalized row is missing a subject.",
      };
    } catch (error) {
      return { attempted: true, ok: false, error: String((error && error.message) || error) };
    }
  }

  function inspectJsonShape(route, json, config) {
    if (route === "search") {
      if (!json || typeof json !== "object" || Array.isArray(json)) {
        return { usable: false, summary: { shape: "unexpected-root" }, issues: ["Search body was not a JSON object."], normalization: null };
      }
      if (json.fatal) {
        return { usable: false, summary: { shape: "fatal" }, issues: [`Search body reported fatal: ${json.fatal}`], normalization: null };
      }
      if (!Array.isArray(json.results)) {
        return { usable: false, summary: { shape: "missing-results" }, issues: ["Search body had no results array."], normalization: null };
      }
      if (json.results.length === 0) {
        return { usable: false, summary: { shape: "empty-results", count: 0 }, issues: ["Search returned zero results for the probe subject."], normalization: null };
      }
      const normalization = normalizationProbe(config.term, json.results[0]);
      const issues = normalization.ok ? [] : [normalization.error || "Normalization of the first result failed."];
      return {
        usable: normalization.ok,
        summary: { shape: "results", count: json.results.length },
        issues,
        normalization,
      };
    }
    if (!json || typeof json !== "object" || Array.isArray(json)) {
      return { usable: false, summary: { shape: "unexpected-root" }, issues: ["Details body was not a JSON object."], normalization: null };
    }
    if (json.fatal) {
      return { usable: false, summary: { shape: "fatal" }, issues: [`Details body reported fatal: ${json.fatal}`], normalization: null };
    }
    const fields = {
      key: Boolean(json.key),
      crn: Boolean(json.crn),
      seats: Boolean(json.seats),
      enrlStat: Boolean(json.enrl_stat_html),
    };
    const present = Object.keys(fields).filter((name) => fields[name]).length;
    if (present === 0) {
      return { usable: false, summary: { shape: "unrecognized-details" }, issues: ["Details body had no recognizable section fields."], normalization: null };
    }
    return { usable: true, summary: { shape: "details", fields }, issues: [], normalization: null };
  }

  function classify(status, bodyKind, json, route, config) {
    if (bodyKind === "html-challenge") {
      return { code: "waf_challenge_html", detail: "Response body looks like a WAF or browser challenge page instead of API JSON." };
    }
    if (status === 403) {
      return { code: "http_403", detail: "Atlas returned 403 for a direct browser request." };
    }
    if (status < 200 || status > 299) {
      return { code: `http_${status}`, detail: `Atlas returned HTTP ${status}.` };
    }
    if (bodyKind !== "json") {
      return { code: "malformed_data", detail: `Expected a JSON body but received kind "${bodyKind}".` };
    }
    const shape = inspectJsonShape(route, json, config);
    if (!shape.usable) {
      return { code: "malformed_data", detail: shape.issues.join(" "), shape };
    }
    return { code: "usable_json", detail: null, shape };
  }

  function deriveDetailsKey(json) {
    if (!json || typeof json !== "object" || Array.isArray(json)) return null;
    if (!Array.isArray(json.results) || json.results.length === 0) return null;
    const first = json.results[0];
    if (!first || typeof first !== "object") return null;
    const key = String(first.key || first.atlas_key || "").trim();
    return key || null;
  }

  async function probe(route, body, label, config) {
    const started = monotonicNow();
    const record = {
      label,
      route,
      url: foseUrl(route),
      status: null,
      ok: null,
      contentType: null,
      headers: {},
      bodyKind: null,
      bodyPreview: null,
      jsonShape: null,
      normalization: null,
      classification: null,
      classificationDetail: null,
      durationMs: null,
      error: null,
    };
    let response;
    try {
      response = await fetch(foseUrl(route), {
        method: "POST",
        mode: "cors",
        credentials: "omit",
        headers: REQUEST_HEADERS,
        body,
      });
    } catch (error) {
      record.durationMs = Math.round(monotonicNow() - started);
      record.error = { name: (error && error.name) || "Error", message: String((error && error.message) || error) };
      record.classification = "network_or_cors_blocked";
      record.classificationDetail = "fetch() rejected before a readable response existed; typical for a CORS preflight or response block, or a network-level reset.";
      return { record, json: null };
    }
    record.status = response.status;
    record.ok = Boolean(response.ok);
    record.headers = captureHeaders(response);
    record.contentType = record.headers["content-type"] || "";
    let text = "";
    try {
      text = await response.text();
    } catch (error) {
      record.error = { name: (error && error.name) || "Error", message: String((error && error.message) || error) };
    }
    const bodyInfo = inspectBody(text, record.contentType);
    record.bodyKind = bodyInfo.kind;
    record.bodyPreview = String(text || "").slice(0, BODY_PREVIEW_LIMIT);
    const verdict = classify(response.status, bodyInfo.kind, bodyInfo.json, route, config);
    record.classification = verdict.code;
    record.classificationDetail = verdict.detail;
    if (verdict.shape) {
      record.jsonShape = verdict.shape.summary;
      record.normalization = verdict.shape.normalization;
    }
    record.durationMs = Math.round(monotonicNow() - started);
    return { record, json: bodyInfo.json };
  }

  function summarize(records) {
    const counts = {};
    records.forEach((record) => {
      counts[record.classification] = (counts[record.classification] || 0) + 1;
    });
    const codes = Object.keys(counts);
    return {
      verdict: codes.length === 1 ? codes[0] : codes.join("+"),
      counts,
      allUsable: counts.usable_json === records.length,
    };
  }

  function printReport(report) {
    const output = typeof console !== "undefined" ? console : null;
    if (!output || typeof output.group !== "function") return;
    output.group(`Atlas browser diagnostic - verdict: ${report.summary ? report.summary.verdict : "n/a"}`);
    output.log("config", report.config);
    report.requests.forEach((record) => {
      output.group(`${record.label}: ${record.classification} (HTTP ${record.status === null ? "n/a" : record.status})`);
      output.log("classification", record.classificationDetail);
      output.log("headers", record.headers);
      output.log("bodyKind", record.bodyKind);
      output.log("bodyPreview", record.bodyPreview);
      if (record.jsonShape) output.log("jsonShape", record.jsonShape);
      if (record.normalization) output.log("normalization", record.normalization);
      if (record.error) output.log("error", record.error);
      output.groupEnd();
    });
    output.log("summary", report.summary);
    output.log("Full report: window.APSTUDY_ATLAS_DIAGNOSTIC.lastReport");
    output.groupEnd();
  }

  function finishReport(report) {
    report.finishedAt = new Date().toISOString();
    report.durationMs = Date.parse(report.finishedAt) - Date.parse(report.startedAt);
    lastReport = report;
    printReport(report);
    return report;
  }

  async function run() {
    const config = resolveConfig();
    const report = {
      version: 1,
      enabled: true,
      origin: currentOrigin(),
      userAgent: currentUserAgent(),
      startedAt: new Date().toISOString(),
      config: config.error ? { error: config.error } : config,
      requests: [],
      summary: null,
      finishedAt: null,
      durationMs: null,
    };
    if (config.error) {
      report.summary = { verdict: "config_error", counts: {}, allUsable: false };
      return finishReport(report);
    }
    const searchBody = JSON.stringify({
      other: { srcdb: config.srcdb },
      criteria: [{ field: "subject", value: config.subject }],
    });
    const searchOutcome = await probe("search", searchBody, "subject-search", config);
    if (!config.key) {
      const derived = deriveDetailsKey(searchOutcome.json);
      if (derived) {
        config.keyInUse = derived;
        config.keySource = "derived-from-search";
      }
    }
    const detailsBody = JSON.stringify({
      other: { srcdb: config.srcdb },
      group: `key:${config.keyInUse}`,
    });
    const detailsOutcome = await probe("details", detailsBody, "details", config);
    report.requests = [searchOutcome.record, detailsOutcome.record];
    report.summary = summarize(report.requests);
    return finishReport(report);
  }

  window.APSTUDY_ATLAS_DIAGNOSTIC = {
    version: 1,
    run,
    get lastReport() {
      return lastReport;
    },
  };
  if (diagnosticsEnabled()) {
    run();
  }
})();
