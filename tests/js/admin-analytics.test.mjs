import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");

async function runBrowserScript(relativePath, extra = {}) {
  const source = await readFile(path.join(repoRoot, relativePath), "utf8");
  const sandbox = {
    URL,
    Intl,
    Date,
    Number,
    Math,
    String,
    Array,
    Set,
    window: {
      APStudyUIPrimitives: { escapeHtml: String },
      location: { origin: "https://example.test" },
      ...extra.window,
    },
    document: {
      readyState: "complete",
      querySelector: () => null,
      querySelectorAll: () => [],
      addEventListener: () => {},
      ...extra.document,
    },
    ...extra.globals,
  };
  sandbox.window.window = sandbox.window;
  sandbox.window.document = sandbox.document;
  vm.runInNewContext(source, sandbox, { filename: relativePath });
  return sandbox.window;
}

test("admin analytics helpers normalize range, timezone URLs, and series data", async () => {
  const window = await runBrowserScript("static/js/admin-analytics.js", {
    window: {
      Intl,
    },
  });

  assert.equal(window.AdminAnalytics.normalizeRange("bogus"), "30d");
  assert.equal(window.AdminAnalytics.normalizeRange("7d"), "7d");
  assert.equal(
    window.AdminAnalytics.buildAnalyticsUrl("24h", "America/Phoenix"),
    "https://example.test/admin/analytics/data?range=24h&tz=America%2FPhoenix",
  );
  assert.deepEqual(JSON.parse(JSON.stringify(window.AdminAnalytics.normalizeSeries([{ key: "a", label: "A", value: "2" }, { key: "b" }]))), [
    { key: "a", label: "A", value: 2 },
    { key: "b", label: "b", value: 0 },
  ]);
});

test("admin analytics charts use nice axes, hover details, and vertical category bars", async () => {
  const window = await runBrowserScript("static/js/admin-analytics.js", {
    window: {
      Intl,
    },
  });

  const axis = window.AdminAnalytics.niceAxis(87);
  assert.ok(axis.max > 87);
  assert.equal(String(axis.max).endsWith("0"), true);
  assert.equal(axis.ticks.length, 6);

  const line = window.AdminAnalytics.renderLineChart([
    { label: "Jun 1", value: 14 },
    { label: "Jun 2", value: 87 },
  ]);
  assert.match(line, /admin-analytics-tooltip/);
  assert.match(line, /admin-analytics-grid-line/);
  assert.match(line, /admin-analytics-hover-band/);
  assert.match(line, /role="listitem"/);
  assert.doesNotMatch(line, /admin-analytics-area/);

  const filledLine = window.AdminAnalytics.renderLineChart([
    { label: "Jun 1", value: 14 },
    { label: "Jun 2", value: 87 },
  ], { fillArea: true });
  assert.match(filledLine, /admin-analytics-area/);

  const bars = window.AdminAnalytics.renderVerticalBarChart([
    { label: "Google", value: 4 },
    { label: "Emory (Main/Oxford)", value: 2 },
  ]);
  assert.match(bars, /admin-analytics-bar-column/);
  assert.match(bars, /admin-analytics-tooltip/);
  assert.doesNotMatch(bars, /admin-analytics-bars/);
});

test("admin analytics detail rows include compact layout, tooltips, and shortened country header", async () => {
  const window = await runBrowserScript("static/js/admin-analytics.js", {
    window: {
      Intl,
    },
  });

  const countryList = { innerHTML: "" };
  window.AdminAnalytics.renderDetailRows(countryList, [
    { label: "United States", countryId: "US", value: 42 },
  ], { type: "countries" });
  assert.match(countryList.innerHTML, /admin-analytics-rank-row--compact/);
  assert.match(countryList.innerHTML, /title="United States"/);
  assert.match(countryList.innerHTML, /aria-label="Active users">Users</);

  const pageList = { innerHTML: "" };
  window.AdminAnalytics.renderDetailRows(pageList, [
    { title: "Calendar", path: "/calendar", value: 21 },
  ], { type: "pages" });
  assert.match(pageList.innerHTML, /title="Calendar"/);
  assert.match(pageList.innerHTML, /title="\/calendar"/);
});

test("admin analytics multi-line charts render dynamic series colors and interval growth", async () => {
  const window = await runBrowserScript("static/js/admin-analytics.js", {
    window: {
      Intl,
    },
  });

  const chart = window.AdminAnalytics.renderMultiLineChart([
    {
      key: "google",
      label: "Google",
      points: [{ label: "May 1", value: 2 }, { label: "May 2", value: 5 }],
    },
    {
      key: "discord",
      label: "Discord",
      points: [{ label: "May 1", value: 1 }, { label: "May 2", value: 2 }],
    },
  ]);

  assert.match(chart, /admin-analytics-series-line/);
  assert.match(chart, /admin-analytics-legend-item/);
  assert.match(chart, /\+3/);
  assert.match(chart, /\+1/);
  assert.match(chart, /hsl\(/);
});

test("admin analytics main card switches to selected metric graph", async () => {
  const window = await runBrowserScript("static/js/admin-analytics.js", {
    window: {
      Intl,
    },
  });

  const cards = new Map();
  const deltas = new Map();
  const nodes = {
    main: {
      innerHTML: "",
      attrs: {},
      setAttribute(name, value) {
        this.attrs[name] = value;
      },
    },
    source: { textContent: "" },
    countryMap: { innerHTML: "" },
    countryList: { innerHTML: "" },
    pageList: { innerHTML: "" },
    gaSource: { textContent: "" },
    pageSource: { textContent: "" },
  };
  const tabs = ["totalUsers", "activeUsers", "pageViews", "oauth", "uniType"].map((metric) => ({
    dataset: { analyticsMetric: metric },
    active: false,
    attrs: {},
    classList: {
      toggle(name, selected) {
        if (name === "is-active") this.owner.active = selected;
      },
    },
    setAttribute(name, value) {
      this.attrs[name] = value;
    },
  }));
  tabs.forEach((tab) => {
    tab.classList.owner = tab;
  });
  const root = {
    dataset: { activeMetric: "pageViews" },
    querySelector(selector) {
      const card = selector.match(/\[data-analytics-card="([^"]+)"\]/)?.[1];
      if (card) {
        if (!cards.has(card)) cards.set(card, { textContent: "" });
        return cards.get(card);
      }
      const delta = selector.match(/\[data-analytics-delta="([^"]+)"\]/)?.[1];
      if (delta) {
        if (!deltas.has(delta)) deltas.set(delta, { textContent: "", innerHTML: "", hidden: true, className: "" });
        return deltas.get(delta);
      }
      if (selector === '[data-chart="main"]') return nodes.main;
      if (selector === "[data-analytics-main-source]") return nodes.source;
      if (selector === "[data-analytics-country-map]") return nodes.countryMap;
      if (selector === '[data-analytics-detail-list="countries"]') return nodes.countryList;
      if (selector === '[data-analytics-detail-list="pages"]') return nodes.pageList;
      if (selector === "[data-analytics-ga-details-source]") return nodes.gaSource;
      if (selector === "[data-analytics-page-details-source]") return nodes.pageSource;
      return null;
    },
    querySelectorAll(selector) {
      return selector === "[data-analytics-metric]" ? tabs : [];
    },
  };

  window.AdminAnalytics.renderDashboard(root, {
    cards: { totalUsers: 8, activeUsers: 3, pageViews: 12, onboardingRate: 0 },
    series: {
      totalUsers: [],
      activeUsers: [{ label: "May 1", value: 1 }, { label: "May 2", value: 3 }],
      pageViews: [{ label: "May 1", value: 4 }, { label: "May 2", value: 12 }],
      oauth: [
        { key: "google", label: "Google", points: [{ label: "May 1", value: 1 }, { label: "May 2", value: 3 }] },
        { key: "discord", label: "Discord", points: [{ label: "May 1", value: 0 }, { label: "May 2", value: 2 }] },
      ],
      uniType: [],
    },
    breakdowns: {
      oauth: [{ label: "Google", value: 5 }, { label: "Discord", value: 3 }],
      uniType: [],
    },
    engagement: {
      topPages: [{ label: "/dashboard", value: 12 }],
      featureUsage: [{ label: "Tasks", value: 3 }],
    },
    comparison: {
      enabled: true,
      metrics: {
        totalUsers: { available: true, percentChange: 14.3, direction: "up" },
        activeUsers: { available: true, percentChange: -20, direction: "down" },
        pageViews: { available: true, percentChange: 50, direction: "up" },
        oauth: { available: true, percentChange: 25, direction: "up" },
        uniType: { available: true, percentChange: 25, direction: "up" },
      },
    },
    gaDetails: {
      countries: [{ countryId: "US", label: "United States", value: 7, comparison: { available: true, percentChange: 40, direction: "up" } }],
      pages: [{ title: "Dashboard | APStudy Nest", path: "/dashboard", label: "Dashboard | APStudy Nest", value: 12, comparison: { available: true, percentChange: -25, direction: "down" } }],
    },
    sources: {
      traffic: { label: "Google Analytics", status: "ok" },
      featureUsage: { label: "Nest database", status: "ok" },
    },
    ga4: { configured: true, status: "ok" },
  });

  assert.equal(nodes.source.textContent, "Source: Google Analytics");
  assert.match(nodes.main.innerHTML, /admin-analytics-svg--primary/);
  assert.equal(cards.get("pageViews").textContent, "12");
  assert.match(deltas.get("activeUsers").innerHTML, /-20\.0%/);
  assert.match(deltas.get("pageViews").innerHTML, /\+50\.0%/);
  assert.match(deltas.get("activeUsers").className, /admin-stat-delta--down/);
  assert.match(deltas.get("totalUsers").innerHTML, /\+14\.3%/);
  assert.match(deltas.get("oauth").innerHTML, /\+25\.0%/);
  assert.match(deltas.get("uniType").innerHTML, /\+25\.0%/);
  assert.equal(nodes.countryMap.innerHTML, "");
  assert.equal(nodes.countryList.innerHTML, "");
  assert.equal(nodes.pageList.innerHTML, "");
  assert.equal(tabs.find((tab) => tab.dataset.analyticsMetric === "pageViews").attrs["aria-selected"], "true");

  root.dataset.activeMetric = "activeUsers";
  window.AdminAnalytics.renderDashboard(root, {
    ...{
      cards: { totalUsers: 8, activeUsers: 3, pageViews: 12, oauth: 2, uniType: 2 },
      series: {
        totalUsers: [],
        activeUsers: [{ label: "May 1", value: 1 }, { label: "May 2", value: 3 }],
        pageViews: [],
        oauth: [],
        uniType: [],
      },
      comparison: { enabled: true, metrics: {} },
      sources: {
        traffic: { label: "Google Analytics", status: "ok" },
        featureUsage: { label: "Nest database", status: "ok" },
      },
    },
  });
  assert.equal(nodes.source.textContent, "Source: Nest database");
  assert.match(nodes.main.innerHTML, /admin-analytics-svg--primary/);
  assert.doesNotMatch(nodes.main.innerHTML, /admin-analytics-area/);
});

test("admin analytics GA detail panels render independently with cleaned page labels", async () => {
  const window = await runBrowserScript("static/js/admin-analytics.js", {
    window: {
      Intl,
    },
  });

  const countryPanel = {
    querySelector(selector) {
      if (selector === "[data-analytics-country-map]") return this.countryMap;
      if (selector === '[data-analytics-detail-list="countries"]') return this.countryList;
      if (selector === "[data-analytics-ga-details-source]") return this.gaSource;
      return null;
    },
    countryMap: { innerHTML: "" },
    countryList: { innerHTML: "" },
    gaSource: { textContent: "" },
  };
  const pagePanel = {
    querySelector(selector) {
      if (selector === '[data-analytics-detail-list="pages"]') return this.pageList;
      if (selector === "[data-analytics-page-details-source]") return this.pageSource;
      return null;
    },
    pageList: { innerHTML: "" },
    pageSource: { textContent: "" },
  };
  const payload = {
    gaDetails: {
      countries: [{ countryId: "US", label: "United States", value: 7, comparison: { available: true, percentChange: 40, direction: "up" } }],
      pages: [
        { title: "Dashboard - APStudy Nest", path: "/dashboard", label: "Dashboard - APStudy Nest", value: 12, comparison: { available: true, percentChange: -25, direction: "down" } },
        { title: "APStudy Nest", path: "/", label: "APStudy Nest", value: 10, comparison: { available: true, percentChange: 100, direction: "up" } },
      ],
    },
    sources: { traffic: { label: "Google Analytics", status: "ok" } },
  };

  window.AdminAnalytics.renderGaDetailPanel(countryPanel, payload, "countries");
  window.AdminAnalytics.renderGaDetailPanel(pagePanel, payload, "pages");

  assert.match(countryPanel.countryMap.innerHTML, /admin-analytics-bars/);
  assert.match(countryPanel.countryList.innerHTML, /United States/);
  assert.equal(countryPanel.gaSource.textContent, "Source: Google Analytics");
  assert.match(pagePanel.pageList.innerHTML, /Dashboard/);
  assert.doesNotMatch(pagePanel.pageList.innerHTML, /APStudy Nest/);
  assert.match(pagePanel.pageList.innerHTML, /Landing Page/);
  assert.match(pagePanel.pageList.innerHTML, /\/dashboard/);
  assert.match(pagePanel.pageList.innerHTML, /-25\.0%/);
});

test("admin analytics GA details draw a Google GeoChart when available", async () => {
  const chartCalls = [];
  const window = await runBrowserScript("static/js/admin-analytics.js", {
    window: {
      Intl,
      google: {
        charts: {
          load(version, options) {
            chartCalls.push({ type: "load", version, options });
          },
          setOnLoadCallback(callback) {
            callback();
          },
        },
        visualization: {
          arrayToDataTable(rows) {
            chartCalls.push({ type: "data", rows });
            return rows;
          },
          GeoChart: class {
            constructor(root) {
              this.root = root;
            }
            draw(data, options) {
              this.root.drawn = { data, options };
              chartCalls.push({ type: "draw", data, options });
            }
          },
        },
      },
    },
  });

  const nodes = {
    countryMap: { innerHTML: "", drawn: null },
    countryList: { innerHTML: "" },
    pageList: { innerHTML: "" },
    gaSource: { textContent: "" },
    pageSource: { textContent: "" },
  };
  const root = {
    querySelector(selector) {
      if (selector === "[data-analytics-country-map]") return nodes.countryMap;
      if (selector === '[data-analytics-detail-list="countries"]') return nodes.countryList;
      if (selector === '[data-analytics-detail-list="pages"]') return nodes.pageList;
      if (selector === "[data-analytics-ga-details-source]") return nodes.gaSource;
      if (selector === "[data-analytics-page-details-source]") return nodes.pageSource;
      return null;
    },
  };

  window.AdminAnalytics.renderGaDetails(root, {
    gaDetails: {
      countries: [{ countryId: "US", label: "United States", value: 7, comparison: { available: true, percentChange: 40, direction: "up" } }],
      pages: [{ title: "Dashboard | APStudy", path: "/dashboard", label: "Dashboard | APStudy", value: 12, comparison: { available: true, percentChange: -25, direction: "down" } }],
    },
    sources: { traffic: { label: "Google Analytics", status: "ok" } },
  });

  assert.deepEqual(Array.from(chartCalls.find((call) => call.type === "load").options.packages), ["geochart"]);
  assert.deepEqual(JSON.parse(JSON.stringify(nodes.countryMap.drawn.data)), [["Country", "Active users"], ["US", 7]]);
  assert.equal(nodes.gaSource.textContent, "Source: Google Analytics");
  assert.match(nodes.countryList.innerHTML, /\+40\.0%/);
  assert.match(nodes.pageList.innerHTML, /-25\.0%/);
});

test("admin analytics range dropdown opens, selects, and closes on outside click", async () => {
  const documentListeners = new Map();
  const windowListeners = new Map();
  const window = await runBrowserScript("static/js/admin-analytics.js", {
    document: {
      readyState: "complete",
      querySelector: () => null,
      querySelectorAll: () => [],
      activeElement: null,
      addEventListener: (type, handler) => {
        const handlers = documentListeners.get(type) || [];
        handlers.push(handler);
        documentListeners.set(type, handlers);
      },
      removeEventListener: (type, handler) => {
        const handlers = (documentListeners.get(type) || []).filter((item) => item !== handler);
        documentListeners.set(type, handlers);
      },
    },
    globals: {
      requestAnimationFrame: (callback) => {
        callback();
        return 1;
      },
    },
    window: {
      Intl,
      location: { origin: "https://example.test" },
      innerHeight: 800,
      addEventListener: (type, handler) => {
        const handlers = windowListeners.get(type) || [];
        handlers.push(handler);
        windowListeners.set(type, handlers);
      },
      removeEventListener: (type, handler) => {
        const handlers = (windowListeners.get(type) || []).filter((item) => item !== handler);
        windowListeners.set(type, handlers);
      },
    },
  });

  const menu = {
    hidden: true,
    offsetHeight: 220,
    listeners: {},
    addEventListener(type, handler) {
      this.listeners[type] = handler;
    },
  };
  const label = { textContent: "Last 30 days" };
  const trigger = {
    attrs: {},
    listeners: {},
    getBoundingClientRect() {
      return { top: 600, bottom: 644, left: 0, right: 180 };
    },
    addEventListener(type, handler) {
      this.listeners[type] = handler;
    },
    setAttribute(name, value) {
      this.attrs[name] = value;
    },
    removeAttribute(name) {
      delete this.attrs[name];
    },
    focus() {},
  };
  const options = ["30d", "7d"].map((range) => ({
    id: `admin-analytics-range-option-${range}`,
    dataset: { analyticsRange: range },
    textContent: range === "30d" ? "Last 30 days" : "Last 7 days",
    classList: { values: new Set(), toggle(name, on) { if (on) this.values.add(name); else this.values.delete(name); } },
    attrs: {},
    listeners: {},
    addEventListener(type, handler) {
      this.listeners[type] = handler;
    },
    setAttribute(name, value) {
      this.attrs[name] = value;
    },
    focus() {
      window.document.activeElement = this;
    },
  }));
  const dropdown = {
    classList: {
      values: new Set(),
      toggle(name, on) {
        if (on) this.values.add(name);
        else this.values.delete(name);
      },
      remove(...names) {
        names.forEach((name) => this.values.delete(name));
      },
    },
    contains(target) {
      return target === dropdown || target === trigger || target === menu || options.includes(target);
    },
    querySelector(selector) {
      if (selector === "[data-analytics-range-trigger]") return trigger;
      if (selector === "[data-analytics-range-menu]") return menu;
      if (selector === "[data-analytics-range-label]") return label;
      return null;
    },
    querySelectorAll(selector) {
      return selector === "[data-analytics-range]" ? options : [];
    },
  };
  const root = {
    querySelector(selector) {
      return selector === "[data-analytics-range-dropdown]" ? dropdown : null;
    },
  };

  const changes = [];
  const controller = window.AdminAnalytics.initAnalyticsRangeDropdown(root, {
    defaultRange: "30d",
    onChange: (range) => changes.push(range),
  });

  assert.equal(controller.getActiveRange(), "30d");
  assert.equal(menu.hidden, true);
  assert.equal(trigger.attrs["aria-expanded"], "false");
  assert.equal(dropdown.classList.values.has("is-open"), false);

  controller.setOpen(true);
  assert.equal(controller.isOpen(), true);
  assert.equal(menu.hidden, false);
  assert.equal(trigger.attrs["aria-expanded"], "true");
  assert.equal(dropdown.classList.values.has("is-flipped"), true);

  controller.selectRange("7d");
  assert.equal(controller.isOpen(), false);
  assert.equal(menu.hidden, true);
  assert.equal(controller.getActiveRange(), "7d");
  assert.equal(label.textContent, "Last 7 days");
  assert.deepEqual(changes, ["7d"]);

  controller.setOpen(true);
  documentListeners.get("click")?.[0]?.({ target: {} });
  assert.equal(controller.isOpen(), false);

  controller.setOpen(true);
  documentListeners.get("keydown")?.[0]?.({ key: "Escape", preventDefault() {} });
  assert.equal(controller.isOpen(), false);

  controller.destroy();
});

test("admin analytics range dropdown flips down when space below is sufficient", async () => {
  const window = await runBrowserScript("static/js/admin-analytics.js", {
    document: {
      readyState: "complete",
      querySelector: () => null,
      querySelectorAll: () => [],
      activeElement: null,
      addEventListener: () => {},
      removeEventListener: () => {},
    },
    globals: {
      requestAnimationFrame: (callback) => {
        callback();
        return 1;
      },
    },
    window: {
      Intl,
      location: { origin: "https://example.test" },
      innerHeight: 1200,
      addEventListener: () => {},
      removeEventListener: () => {},
    },
  });

  const menu = { hidden: true, offsetHeight: 220, addEventListener() {} };
  const trigger = {
    attrs: {},
    getBoundingClientRect() {
      return { top: 120, bottom: 164, left: 0, right: 180 };
    },
    addEventListener() {},
    setAttribute(name, value) {
      this.attrs[name] = value;
    },
    removeAttribute(name) {
      delete this.attrs[name];
    },
    focus() {},
  };
  const options = [{
    id: "admin-analytics-range-option-30d",
    dataset: { analyticsRange: "30d" },
    textContent: "Last 30 days",
    classList: { toggle() {} },
    attrs: {},
    addEventListener() {},
    setAttribute(name, value) {
      this.attrs[name] = value;
    },
    focus() {},
  }];
  const dropdown = {
    classList: {
      values: new Set(),
      toggle(name, on) {
        if (on) this.values.add(name);
        else this.values.delete(name);
      },
      remove(...names) {
        names.forEach((name) => this.values.delete(name));
      },
    },
    contains: () => true,
    querySelector(selector) {
      if (selector === "[data-analytics-range-trigger]") return trigger;
      if (selector === "[data-analytics-range-menu]") return menu;
      if (selector === "[data-analytics-range-label]") return { textContent: "Last 30 days" };
      return null;
    },
    querySelectorAll(selector) {
      return selector === "[data-analytics-range]" ? options : [];
    },
  };
  const controller = window.AdminAnalytics.initAnalyticsRangeDropdown({
    querySelector: () => dropdown,
  }, { defaultRange: "30d" });

  controller.setOpen(true);
  assert.equal(dropdown.classList.values.has("is-flipped"), false);
  controller.updateMenuPlacement();
  assert.equal(dropdown.classList.values.has("is-flipped"), false);
  controller.destroy();
});

test("admin timezone helper formats timestamps and exposes browser timezone", async () => {
  const window = await runBrowserScript("static/js/admin-timezone.js", {
    window: {
      Intl,
    },
  });

  assert.equal(typeof window.AdminTimezone.adminTimezone(), "string");
  const formatted = window.AdminTimezone.formatAdminTimestamp("2026-05-25T01:00:00Z", {
    timeZone: "America/Phoenix",
  });
  assert.match(formatted, /May|5\/24|5\/25/);
  assert.equal(window.AdminTimezone.formatAdminTimestamp(""), "--");
});
