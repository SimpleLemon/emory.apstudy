(() => {
  const { escapeHtml } = window.APStudyUIPrimitives;
  const RANGE_KEYS = new Set(["24h", "7d", "14d", "30d", "60d", "all"]);
  let googleChartsRequested = false;
  let googleChartsReady = false;
  const googleChartsCallbacks = [];
  let geoChartThemeObserver = null;
  let geoChartRedraw = null;
  const METRIC_CONFIG = {
    totalUsers: {
      label: "Total Users",
      title: "Total Users",
      description: "Cumulative user growth from the first joined account.",
      type: "line",
      tone: "primary",
      fillArea: true,
      seriesPath: ["series", "totalUsers"],
      value: (payload) => payload?.cards?.totalUsers,
    },
    activeUsers: {
      label: "Users Active",
      title: "Users Active",
      description: "Distinct signed-in users who opened Nest in each bucket.",
      type: "line",
      tone: "primary",
      seriesPath: ["series", "activeUsers"],
      value: (payload) => payload?.cards?.activeUsers,
    },
    pageViews: {
      label: "Views",
      title: "Views",
      description: "Google Analytics page views across the selected range.",
      type: "line",
      tone: "primary",
      seriesPath: ["series", "pageViews"],
      value: (payload) => payload?.cards?.pageViews,
    },
    oauth: {
      label: "OAuth",
      title: "OAuth",
      description: "Cumulative signups by provider across the selected range.",
      type: "multiLine",
      tone: "primary",
      seriesPath: ["series", "oauth"],
      value: (payload) => payload?.cards?.oauth ?? sumMultiSeriesIncrease(payload?.series?.oauth),
    },
    uniType: {
      label: "Uni Type",
      title: "Uni Type",
      description: "Cumulative signups by education type across the selected range.",
      type: "multiLine",
      tone: "secondary",
      seriesPath: ["series", "uniType"],
      value: (payload) => payload?.cards?.uniType ?? sumMultiSeriesIncrease(payload?.series?.uniType),
    },
  };

  function getBrowserTimezone() {
    try {
      return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
    } catch {
      return "UTC";
    }
  }

  function normalizeRange(value, fallback = "30d") {
    return RANGE_KEYS.has(value) ? value : fallback;
  }

  function buildAnalyticsUrl(range, timezone) {
    const url = new URL("/admin/analytics/data", window.location.origin);
    url.searchParams.set("range", normalizeRange(range));
    url.searchParams.set("tz", timezone || getBrowserTimezone());
    return url.toString();
  }

  function normalizeSeries(points) {
    return Array.isArray(points)
      ? points.map((point) => ({
          key: String(point?.key || ""),
          label: String(point?.label || point?.key || ""),
          value: Number(point?.value || 0),
        }))
      : [];
  }

  function sumSeries(points) {
    return normalizeSeries(points).reduce((total, point) => total + point.value, 0);
  }

  function seriesIntervalIncrease(points) {
    const series = normalizeSeries(points);
    if (!series.length) return 0;
    if (series.length === 1) return series[0].value;
    return series[series.length - 1].value - series[0].value;
  }

  function sumMultiSeriesIncrease(groups) {
    return (Array.isArray(groups) ? groups : []).reduce(
      (total, group) => total + seriesIntervalIncrease(group?.points),
      0,
    );
  }

  function stableSeriesColor(key, index = 0) {
    let hash = 0;
    const text = String(key ?? index);
    for (let i = 0; i < text.length; i += 1) {
      hash = ((hash << 5) - hash) + text.charCodeAt(i);
      hash |= 0;
    }
    const hue = Math.abs(hash) % 360;
    return `hsl(${hue} 62% 48%)`;
  }

  function formatNumber(value) {
    const number = Number(value || 0);
    return new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 }).format(number);
  }

  function formatPercent(value) {
    const number = Number(value);
    if (!Number.isFinite(number)) return "";
    return `${Math.abs(number).toFixed(1)}%`;
  }

  const TREND_UP_SVG = '<svg xmlns="http://www.w3.org/2000/svg" aria-hidden="true" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5"><path d="m22 7-7.38 7.335c-.997.991-1.496 1.487-2.115 1.487s-1.117-.496-2.115-1.488l-.24-.238c-.997-.992-1.497-1.489-2.116-1.489s-1.118.497-2.115 1.49L2 18" opacity=".5"/><path d="M22 12.546V7h-5.582"/></svg>';
  const TREND_DOWN_SVG = '<svg xmlns="http://www.w3.org/2000/svg" aria-hidden="true" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5"><path d="m22 18-7.38-7.335c-.997-.991-1.496-1.487-2.115-1.487s-1.117.496-2.115 1.488l-.24.238c-.997.992-1.497 1.489-2.116 1.489s-1.118-.497-2.115-1.49L2 7" opacity=".5"/><path d="M22 12.454V18h-5.582"/></svg>';
  const TREND_NEUTRAL_SVG = '<svg xmlns="http://www.w3.org/2000/svg" aria-hidden="true" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-width="1.5"><path d="M5 12h14"/></svg>';

  function comparisonForMetric(payload, key) {
    return payload?.comparison?.metrics?.[key] || null;
  }

  function deltaLabel(comparison) {
    if (!comparison?.available) return "";
    if (comparison.label) return String(comparison.label);
    if (comparison.percentChange == null) return "";
    const number = Number(comparison.percentChange);
    if (!Number.isFinite(number)) return "";
    const prefix = number > 0 ? "+" : number < 0 ? "-" : "";
    return `${prefix}${formatPercent(number)}`;
  }

  function deltaBadgeClass(comparison) {
    const direction = comparison?.direction === "up" ? "up" : comparison?.direction === "down" ? "down" : "neutral";
    return `admin-stat-delta admin-stat-delta--${direction}`;
  }

  function deltaTrendSvg(comparison) {
    if (comparison?.direction === "down") return TREND_DOWN_SVG;
    if (comparison?.direction === "up") return TREND_UP_SVG;
    return TREND_NEUTRAL_SVG;
  }

  function deltaClass(comparison) {
    return deltaBadgeClass(comparison);
  }

  function deltaMarkup(comparison) {
    const label = deltaLabel(comparison);
    if (!label) return "";
    return `<span class="${deltaBadgeClass(comparison)}" title="Compared with the previous period" aria-label="${escapeHtml(label)} compared with the previous period"><span>${escapeHtml(label)}${deltaTrendSvg(comparison)}</span></span>`;
  }

  function chartEmpty(label = "No data yet") {
    return `<div class="admin-analytics-empty">${escapeHtml(label)}</div>`;
  }

  function clamp(value, min, max) {
    return Math.min(Math.max(value, min), max);
  }

  function truncateLabel(label, limit = 16) {
    const text = String(label || "");
    return text.length > limit ? `${text.slice(0, Math.max(0, limit - 1))}...` : text;
  }

  function cleanPageTitle(title, path) {
    const pagePath = String(path || "").trim();
    if (pagePath === "/") return "Landing Page";
    let text = String(title || "").trim();
    [" - APStudy Nest", " | APStudy Nest", " - APStudy", " | APStudy"].some((suffix) => {
      if (!text.endsWith(suffix)) return false;
      text = text.slice(0, -suffix.length).trim();
      return true;
    });
    return text || pagePath || "Untitled Page";
  }

  function valueAtPath(payload, path) {
    return path.reduce((current, key) => current?.[key], payload);
  }

  function niceAxis(maxValue) {
    const max = Math.max(0, Number(maxValue || 0));
    if (max <= 0) {
      return { max: 10, ticks: [0, 2, 4, 6, 8, 10] };
    }
    const rawStep = max / 5;
    const magnitude = 10 ** Math.floor(Math.log10(rawStep || 1));
    const residual = rawStep / magnitude;
    const niceResidual = residual <= 1 ? 1 : residual <= 2 ? 2 : residual <= 5 ? 5 : 10;
    let axisMax = niceResidual * magnitude * 5;
    if (axisMax <= max) axisMax += niceResidual * magnitude;
    if (axisMax < 10) axisMax = 10;
    axisMax = Math.ceil(axisMax / 10) * 10;
    const step = axisMax / 5;
    return {
      max: axisMax,
      ticks: Array.from({ length: 6 }, (_, index) => Number((step * index).toFixed(6))),
    };
  }

  function renderMultiTooltip(bucketLabel, entries, x, y, bounds = { width: 760, height: 300 }) {
    const title = String(bucketLabel || "");
    const rowMarkup = entries.map((entry, index) => `
      <text x="12" y="${34 + index * 16}">
        <tspan fill="${entry.color}">●</tspan>
        <tspan> ${escapeHtml(truncateLabel(entry.label, 18))}: ${escapeHtml(formatNumber(entry.value))}</tspan>
      </text>
    `).join("");
    const boxWidth = clamp(Math.max(160, title.length * 7 + 34, ...entries.map((entry) => entry.label.length * 7 + 48)), 160, 260);
    const boxHeight = 28 + entries.length * 16;
    const boxX = clamp(x - boxWidth / 2, 8, bounds.width - boxWidth - 8);
    const boxY = y - boxHeight - 14 < 8 ? y + 16 : y - boxHeight - 14;
    return `
      <g class="admin-analytics-tooltip" role="tooltip" transform="translate(${boxX.toFixed(1)} ${boxY.toFixed(1)})">
        <rect width="${boxWidth.toFixed(1)}" height="${boxHeight}" rx="8"></rect>
        <text x="12" y="18">${escapeHtml(truncateLabel(title, 28))}</text>
        ${rowMarkup}
      </g>
    `;
  }

  function renderMultiLineChart(seriesGroups) {
    const groups = (Array.isArray(seriesGroups) ? seriesGroups : []).map((group, index) => ({
      key: String(group?.key || index),
      label: String(group?.label || group?.key || ""),
      points: normalizeSeries(group?.points),
      color: stableSeriesColor(group?.key, index),
      increase: seriesIntervalIncrease(group?.points),
    })).filter((group) => group.points.length && (group.increase > 0 || group.points.some((point) => point.value > 0)));
    if (!groups.length) return chartEmpty();

    const width = 1200;
    const height = 300;
    const padLeft = 28;
    const padRight = 58;
    const padTop = 28;
    const padBottom = 42;
    const chartWidth = width - padLeft - padRight;
    const chartHeight = height - padTop - padBottom;
    const bucketCount = groups[0].points.length;
    const axis = niceAxis(Math.max(0, ...groups.flatMap((group) => group.points.map((point) => point.value))));
    const step = bucketCount > 1 ? chartWidth / (bucketCount - 1) : 0;
    const labelEvery = Math.max(1, Math.ceil(bucketCount / 6));

    const plotted = groups.map((group) => ({
      ...group,
      coords: group.points.map((point, index) => {
        const x = bucketCount > 1 ? padLeft + index * step : padLeft + chartWidth / 2;
        const y = padTop + chartHeight - (point.value / axis.max) * chartHeight;
        return { ...point, x, y };
      }),
    }));

    const hoverBuckets = Array.from({ length: bucketCount }, (_, index) => ({
      label: plotted[0].coords[index]?.label || "",
      x: plotted[0].coords[index]?.x || padLeft,
      entries: plotted.map((group) => ({
        label: group.label,
        value: group.coords[index]?.value || 0,
        color: group.color,
      })),
    }));

    const legend = plotted.map((group) => `
      <div class="admin-analytics-legend-item" role="listitem">
        <span class="admin-analytics-legend-swatch" style="background:${group.color}"></span>
        <span class="admin-analytics-legend-label">${escapeHtml(group.label)}</span>
        <strong class="admin-analytics-legend-value">+${formatNumber(group.increase)}</strong>
      </div>
    `).join("");

    return `
      <div class="admin-analytics-multiline">
        <svg class="admin-analytics-svg admin-analytics-svg--multiline" viewBox="0 0 ${width} ${height}" role="img" aria-label="Multi-series line chart">
          ${axis.ticks.map((tick) => {
            const y = padTop + chartHeight - (tick / axis.max) * chartHeight;
            return `
              <line class="admin-analytics-grid-line" x1="${padLeft}" y1="${y.toFixed(1)}" x2="${width - padRight}" y2="${y.toFixed(1)}"></line>
              <text class="admin-analytics-y-label" x="${width - padRight + 14}" y="${(y + 4).toFixed(1)}">${formatNumber(tick)}</text>
            `;
          }).join("")}
          <line class="admin-analytics-axis" x1="${padLeft}" y1="${height - padBottom}" x2="${width - padRight}" y2="${height - padBottom}"></line>
          ${plotted.map((group) => {
            const path = group.coords.map((point, index) => `${index === 0 ? "M" : "L"}${point.x.toFixed(1)} ${point.y.toFixed(1)}`).join(" ");
            return `<path class="admin-analytics-line admin-analytics-series-line" d="${path}" style="stroke:${group.color}"></path>`;
          }).join("")}
          ${hoverBuckets.map((bucket, bucketIndex) => {
            const hitWidth = Math.max(36, step || 36);
            const hitX = clamp(bucket.x - hitWidth / 2, padLeft, width - padRight - hitWidth);
            const summary = bucket.entries.map((entry) => `${entry.label}: ${formatNumber(entry.value)}`).join(", ");
            const anchorY = bucket.entries.reduce((maxY, entry) => {
              const y = padTop + chartHeight - (entry.value / axis.max) * chartHeight;
              return Math.min(maxY, y);
            }, padTop + chartHeight);
            return `
              <g class="admin-analytics-hover-target" tabindex="0" role="listitem" aria-label="${escapeHtml(bucket.label)}: ${escapeHtml(summary)}">
                <rect class="admin-analytics-hover-band" x="${hitX.toFixed(1)}" y="${padTop}" width="${hitWidth.toFixed(1)}" height="${chartHeight}" rx="4"></rect>
                <rect class="admin-analytics-hit-area" x="${hitX.toFixed(1)}" y="${padTop}" width="${hitWidth.toFixed(1)}" height="${chartHeight}" rx="4"></rect>
                <line class="admin-analytics-hover-line admin-analytics-series-hover-line" x1="${bucket.x.toFixed(1)}" y1="${padTop}" x2="${bucket.x.toFixed(1)}" y2="${height - padBottom}"></line>
                ${plotted.map((group) => {
                  const point = group.coords[bucketIndex];
                  return `<circle class="admin-analytics-dot admin-analytics-series-dot" cx="${point.x.toFixed(1)}" cy="${point.y.toFixed(1)}" r="4" style="fill:${group.color};stroke:${group.color}"></circle>`;
                }).join("")}
                ${renderMultiTooltip(bucket.label, bucket.entries, bucket.x, anchorY, { width, height })}
              </g>
            `;
          }).join("")}
          ${plotted[0].coords.filter((_, index) => index % labelEvery === 0 || index === plotted[0].coords.length - 1).map((point) => `<text class="admin-analytics-x-label" x="${point.x.toFixed(1)}" y="${height - 12}" text-anchor="middle">${escapeHtml(point.label)}</text>`).join("")}
        </svg>
        <div class="admin-analytics-legend" role="list" aria-label="Series legend">${legend}</div>
      </div>
    `;
  }

  function renderTooltip(label, value, x, y, bounds = { width: 760, height: 300 }) {
    const valueText = formatNumber(value);
    const title = String(label || "");
    const boxWidth = clamp(Math.max(126, title.length * 7 + 34, valueText.length * 13 + 34), 126, 240);
    const boxHeight = 46;
    const boxX = clamp(x - boxWidth / 2, 8, bounds.width - boxWidth - 8);
    const boxY = y - boxHeight - 14 < 8 ? y + 16 : y - boxHeight - 14;
    return `
      <g class="admin-analytics-tooltip" role="tooltip" transform="translate(${boxX.toFixed(1)} ${boxY.toFixed(1)})">
        <rect width="${boxWidth.toFixed(1)}" height="${boxHeight}" rx="8"></rect>
        <text x="12" y="18">${escapeHtml(truncateLabel(title, 28))}</text>
        <text class="admin-analytics-tooltip-value" x="12" y="36">${escapeHtml(valueText)}</text>
      </g>
    `;
  }

  function renderLineChart(points, { tone = "primary", fillArea = false } = {}) {
    const series = normalizeSeries(points);
    if (!series.length) return chartEmpty();
    const width = 1200;
    const height = 300;
    const padLeft = 28;
    const padRight = 58;
    const padTop = 28;
    const padBottom = 42;
    const chartWidth = width - padLeft - padRight;
    const chartHeight = height - padTop - padBottom;
    const axis = niceAxis(Math.max(0, ...series.map((point) => point.value)));
    const step = series.length > 1 ? chartWidth / (series.length - 1) : 0;
    const coords = series.map((point, index) => {
      const x = series.length > 1 ? padLeft + index * step : padLeft + chartWidth / 2;
      const y = padTop + chartHeight - (point.value / axis.max) * chartHeight;
      return { ...point, x, y };
    });
    const path = coords.map((point, index) => `${index === 0 ? "M" : "L"}${point.x.toFixed(1)} ${point.y.toFixed(1)}`).join(" ");
    const area = `${path} L${coords[coords.length - 1].x.toFixed(1)} ${height - padBottom} L${coords[0].x.toFixed(1)} ${height - padBottom} Z`;
    const labelEvery = Math.max(1, Math.ceil(series.length / 6));

    return `
      <svg class="admin-analytics-svg admin-analytics-svg--${tone}" viewBox="0 0 ${width} ${height}" role="list">
        ${axis.ticks.map((tick) => {
          const y = padTop + chartHeight - (tick / axis.max) * chartHeight;
          return `
            <line class="admin-analytics-grid-line" x1="${padLeft}" y1="${y.toFixed(1)}" x2="${width - padRight}" y2="${y.toFixed(1)}"></line>
            <text class="admin-analytics-y-label" x="${width - padRight + 14}" y="${(y + 4).toFixed(1)}">${formatNumber(tick)}</text>
          `;
        }).join("")}
        <line class="admin-analytics-axis" x1="${padLeft}" y1="${height - padBottom}" x2="${width - padRight}" y2="${height - padBottom}"></line>
        ${fillArea ? `<path class="admin-analytics-area" d="${area}"></path>` : ""}
        <path class="admin-analytics-line" d="${path}"></path>
        ${coords.map((point) => {
          const hitWidth = Math.max(36, step || 36);
          const hitX = clamp(point.x - hitWidth / 2, padLeft, width - padRight - hitWidth);
          return `
          <g class="admin-analytics-hover-target" tabindex="0" role="listitem" aria-label="${escapeHtml(point.label)}: ${formatNumber(point.value)}">
            <rect class="admin-analytics-hover-band" x="${hitX.toFixed(1)}" y="${padTop}" width="${hitWidth.toFixed(1)}" height="${chartHeight}" rx="4"></rect>
            <rect class="admin-analytics-hit-area" x="${hitX.toFixed(1)}" y="${padTop}" width="${hitWidth.toFixed(1)}" height="${chartHeight}" rx="4"></rect>
            <line class="admin-analytics-hover-line" x1="${point.x.toFixed(1)}" y1="${padTop}" x2="${point.x.toFixed(1)}" y2="${height - padBottom}"></line>
            <circle class="admin-analytics-dot" cx="${point.x.toFixed(1)}" cy="${point.y.toFixed(1)}" r="4"></circle>
            ${renderTooltip(point.label, point.value, point.x, point.y, { width, height })}
          </g>
        `;
        }).join("")}
        ${coords.filter((_, index) => index % labelEvery === 0 || index === coords.length - 1).map((point) => `<text class="admin-analytics-x-label" x="${point.x.toFixed(1)}" y="${height - 12}" text-anchor="middle">${escapeHtml(point.label)}</text>`).join("")}
      </svg>
    `;
  }

  function renderVerticalBarChart(items, { tone = "primary" } = {}) {
    const rows = normalizeSeries(items);
    if (!rows.length) return chartEmpty();
    const width = 760;
    const height = 300;
    const padLeft = 28;
    const padRight = 58;
    const padTop = 28;
    const padBottom = 58;
    const chartWidth = width - padLeft - padRight;
    const chartHeight = height - padTop - padBottom;
    const axis = niceAxis(Math.max(0, ...rows.map((row) => row.value)));
    const slot = chartWidth / Math.max(1, rows.length);
    const barWidth = Math.min(74, slot * 0.58);

    return `
      <svg class="admin-analytics-svg admin-analytics-svg--${tone}" viewBox="0 0 ${width} ${height}" role="list">
        ${axis.ticks.map((tick) => {
          const y = padTop + chartHeight - (tick / axis.max) * chartHeight;
          return `
            <line class="admin-analytics-grid-line" x1="${padLeft}" y1="${y.toFixed(1)}" x2="${width - padRight}" y2="${y.toFixed(1)}"></line>
            <text class="admin-analytics-y-label" x="${width - padRight + 14}" y="${(y + 4).toFixed(1)}">${formatNumber(tick)}</text>
          `;
        }).join("")}
        <line class="admin-analytics-axis" x1="${padLeft}" y1="${height - padBottom}" x2="${width - padRight}" y2="${height - padBottom}"></line>
        ${rows.map((row, index) => {
          const barHeight = (row.value / axis.max) * chartHeight;
          const x = padLeft + index * slot + (slot - barWidth) / 2;
          const y = padTop + chartHeight - barHeight;
          return `
            <g class="admin-analytics-hover-target admin-analytics-bar-target" tabindex="0" role="listitem" aria-label="${escapeHtml(row.label)}: ${formatNumber(row.value)}">
              <rect class="admin-analytics-bar-column" x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${barWidth.toFixed(1)}" height="${Math.max(2, barHeight).toFixed(1)}" rx="5"></rect>
              ${renderTooltip(row.label, row.value, x + barWidth / 2, y, { width, height })}
            </g>
            <text class="admin-analytics-x-label" x="${(padLeft + index * slot + slot / 2).toFixed(1)}" y="${height - 22}" text-anchor="middle">${escapeHtml(truncateLabel(row.label, 18))}</text>
          `;
        }).join("")}
      </svg>
    `;
  }

  function renderBarList(items) {
    const rows = normalizeSeries(items);
    if (!rows.length) return chartEmpty();
    const maxValue = Math.max(1, ...rows.map((row) => row.value));
    return `
      <div class="admin-analytics-bars">
        ${rows.map((row) => `
          <div class="admin-analytics-bar-row">
            <div class="admin-analytics-bar-meta">
              <span>${escapeHtml(row.label)}</span>
              <strong>${formatNumber(row.value)}</strong>
            </div>
            <div class="admin-analytics-bar-track" aria-hidden="true">
              <span style="width:${Math.max(3, (row.value / maxValue) * 100).toFixed(1)}%"></span>
            </div>
          </div>
        `).join("")}
      </div>
    `;
  }

  function renderList(root, title, items) {
    if (!root) return;
    const rows = normalizeSeries(items);
    root.innerHTML = `
      <h3>${escapeHtml(title)}</h3>
      ${rows.length ? rows.map((row) => `
        <div class="admin-analytics-list-row">
          <span>${escapeHtml(row.label)}</span>
          <strong>${formatNumber(row.value)}</strong>
        </div>
      `).join("") : chartEmpty()}
    `;
  }

  function normalizeDetailRows(items) {
    return Array.isArray(items)
      ? items.map((item) => {
          const path = String(item?.path || "");
          const title = cleanPageTitle(item?.title || item?.label, path);
          return {
            key: String(item?.key || item?.countryId || path || item?.label || ""),
            countryId: String(item?.countryId || ""),
            label: String(item?.countryId ? (item?.label || item?.countryId) : title),
            title,
            path,
            value: Number(item?.value || 0),
            comparison: item?.comparison || null,
          };
        }).filter((item) => item.label && item.value > 0)
      : [];
  }

  function renderDetailRows(root, items, { type = "countries" } = {}) {
    if (!root) return;
    const rows = normalizeDetailRows(items);
    const maxValue = Math.max(1, ...rows.map((row) => row.value));
    const primaryHeader = type === "pages" ? "Page title and screen path" : "Country";
    const valueHeaderMarkup = type === "pages"
      ? `<span>${escapeHtml("Views")}</span>`
      : `<span aria-label="Active users">Users</span>`;
    root.innerHTML = `
      <div class="admin-analytics-rank-head">
        <span>${escapeHtml(primaryHeader)}</span>
        ${valueHeaderMarkup}
      </div>
      ${rows.length ? rows.map((row) => {
        const labelText = type === "pages" ? row.title : row.label;
        const labelTitle = labelText ? ` title="${escapeHtml(labelText)}"` : "";
        const pathTitle = row.path ? ` title="${escapeHtml(row.path)}"` : "";
        return `
        <div class="admin-analytics-rank-row admin-analytics-rank-row--compact">
          <div class="admin-analytics-rank-label">
            <strong${labelTitle}>${escapeHtml(labelText)}</strong>
            ${type === "pages" && row.path ? `<span${pathTitle}>${escapeHtml(row.path)}</span>` : ""}
          </div>
          <div class="admin-analytics-rank-value">
            <strong>${formatNumber(row.value)}</strong>
            ${deltaMarkup(row.comparison)}
          </div>
          <div class="admin-analytics-bar-track" aria-hidden="true">
            <span style="width:${Math.max(3, (row.value / maxValue) * 100).toFixed(1)}%"></span>
          </div>
        </div>
      `;
      }).join("") : chartEmpty()}
    `;
  }

  function setMetricDeltas(root, payload) {
    Object.keys(METRIC_CONFIG).forEach((key) => {
      const el = root.querySelector(`[data-analytics-delta="${key}"]`);
      if (!el) return;
      const comparison = comparisonForMetric(payload, key);
      const label = deltaLabel(comparison);
      el.hidden = !label;
      if (label) {
        el.className = deltaBadgeClass(comparison);
        el.title = "Compared with the previous period";
        el.setAttribute?.("aria-label", `${label} compared with the previous period`);
        el.innerHTML = `<span>${escapeHtml(label)}${deltaTrendSvg(comparison)}</span>`;
      } else {
        el.className = "admin-stat-delta";
        el.removeAttribute?.("title");
        el.removeAttribute?.("aria-label");
        el.innerHTML = "";
      }
    });
  }

  function syncPeriodLabels(root, label) {
    const text = String(label || "").trim();
    if (!text) return;
    root.querySelectorAll("[data-analytics-period-label]").forEach((el) => {
      el.textContent = text;
    });
  }

  function runGoogleChartsCallback(callback) {
    if (googleChartsReady) {
      callback();
      return true;
    }
    const charts = window.google?.charts;
    if (!charts?.load || !charts?.setOnLoadCallback) {
      return false;
    }
    googleChartsCallbacks.push(callback);
    if (!googleChartsRequested) {
      googleChartsRequested = true;
      charts.load("current", { packages: ["geochart"] });
      charts.setOnLoadCallback(() => {
        googleChartsReady = true;
        while (googleChartsCallbacks.length) {
          googleChartsCallbacks.shift()?.();
        }
      });
    }
    return true;
  }

  function geoChartThemeOptions() {
    const isDark = document.documentElement?.classList?.contains("dark") ?? false;
    return isDark
      ? {
          colorAxis: { colors: ["#1e3a5f", "#60a5fa"] },
          datalessRegionColor: "#2a2d35",
        }
      : {
          colorAxis: { colors: ["#dbeafe", "#2563eb"] },
          datalessRegionColor: "#eef2f7",
        };
  }

  function ensureGeoChartThemeObserver(redraw) {
    geoChartRedraw = redraw;
    if (geoChartThemeObserver || typeof MutationObserver !== "function") return;
    geoChartThemeObserver = new MutationObserver(() => {
      geoChartRedraw?.();
    });
    geoChartThemeObserver.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["class", "data-theme"],
    });
  }

  function renderCountryMapFallback(root, countries) {
    const rows = normalizeDetailRows(countries).slice(0, 6);
    root.innerHTML = rows.length ? renderBarList(rows) : chartEmpty();
  }

  function renderCountryMap(root, countries) {
    if (!root) return;
    const rows = normalizeDetailRows(countries).filter((row) => row.countryId || row.label);
    if (!rows.length) {
      root.innerHTML = chartEmpty();
      return;
    }
    const draw = () => {
      try {
        const table = [["Country", "Active users"], ...rows.map((row) => [row.countryId || row.label, row.value])];
        const data = window.google.visualization.arrayToDataTable(table);
        const chart = new window.google.visualization.GeoChart(root);
        const themeOptions = geoChartThemeOptions();
        chart.draw(data, {
          backgroundColor: "transparent",
          colorAxis: themeOptions.colorAxis,
          datalessRegionColor: themeOptions.datalessRegionColor,
          legend: "none",
          tooltip: { textStyle: { fontName: "Inter" } },
        });
        ensureGeoChartThemeObserver(draw);
      } catch {
        renderCountryMapFallback(root, rows);
      }
    };
    if (!runGoogleChartsCallback(draw)) {
      renderCountryMapFallback(root, rows);
    }
  }

  function renderGaDetailPanel(root, payload, type) {
    if (!root) return;
    const countries = payload?.gaDetails?.countries || [];
    const pages = payload?.gaDetails?.pages || [];
    const traffic = payload?.sources?.traffic?.status === "ok"
      ? payload.sources.traffic.label
      : "Google Analytics unavailable";
    const sourceText = `Source: ${traffic}`;
    if (type === "countries") {
      const gaSource = root.querySelector("[data-analytics-ga-details-source]");
      if (gaSource) gaSource.textContent = sourceText;
      renderCountryMap(root.querySelector("[data-analytics-country-map]"), countries);
      renderDetailRows(root.querySelector('[data-analytics-detail-list="countries"]'), countries, { type: "countries" });
      return;
    }
    const pageSource = root.querySelector("[data-analytics-page-details-source]");
    if (pageSource) pageSource.textContent = sourceText;
    renderDetailRows(root.querySelector('[data-analytics-detail-list="pages"]'), pages, { type: "pages" });
  }

  function renderGaDetails(root, payload) {
    renderGaDetailPanel(root, payload, "countries");
    renderGaDetailPanel(root, payload, "pages");
  }

  function setCard(root, key, value, suffix = "") {
    const el = root.querySelector(`[data-analytics-card="${key}"]`);
    if (el) el.textContent = `${formatNumber(value)}${suffix}`;
  }

  function setMetricCards(root, payload) {
    Object.entries(METRIC_CONFIG).forEach(([key, config]) => {
      setCard(root, key, config.value(payload));
    });
  }

  function renderMainMetric(root, payload, metricKey) {
    const key = METRIC_CONFIG[metricKey] ? metricKey : "totalUsers";
    const config = METRIC_CONFIG[key];
    const chart = root.querySelector('[data-chart="main"]');
    const title = root.querySelector("[data-analytics-main-title]");
    const description = root.querySelector("[data-analytics-main-description]");
    const source = root.querySelector("[data-analytics-main-source]");
    const tabs = Array.from(root.querySelectorAll("[data-analytics-metric]"));
    tabs.forEach((tab) => {
      const selected = tab.dataset.analyticsMetric === key;
      tab.classList.toggle("is-active", selected);
      tab.classList.toggle("admin-stat-cell--active", selected);
      tab.setAttribute("aria-selected", selected ? "true" : "false");
    });
    if (title) title.textContent = config.title;
    if (description) description.textContent = config.description;
    if (source) source.textContent = sourceTextForMetric(payload, key);
    if (!chart) return;
    const data = valueAtPath(payload, config.seriesPath);
    chart.setAttribute("aria-label", `${config.label} analytics chart`);
    chart.innerHTML = config.type === "multiLine"
      ? renderMultiLineChart(data)
      : config.type === "bar"
        ? renderVerticalBarChart(data, { tone: config.tone })
        : renderLineChart(data, { tone: config.tone, fillArea: config.fillArea });
  }

  function sourceTextForMetric(payload, metricKey) {
    const traffic = payload?.sources?.traffic?.status === "ok"
      ? payload.sources.traffic.label
      : "Google Analytics unavailable";
    const featureUsage = payload?.sources?.featureUsage?.label || "Nest database";
    if (metricKey === "pageViews") {
      return `Source: ${traffic}`;
    }
    return `Source: ${featureUsage}`;
  }

  function renderDashboard(root, payload) {
    setMetricCards(root, payload);
    setCard(root, "onboardingRate", payload?.cards?.onboardingRate, "%");
    setMetricDeltas(root, payload);
    const rangeLabel = root.querySelector("[data-analytics-range-dropdown] [data-analytics-range-label]")?.textContent;
    syncPeriodLabels(root, rangeLabel);
    renderMainMetric(root, payload, root.dataset.activeMetric || "totalUsers");
  }

  function setNotice(root, message, isError = false) {
    const notice = root.querySelector("[data-analytics-notice]");
    if (!notice) return;
    notice.hidden = !message;
    notice.textContent = message || "";
    notice.classList.toggle("is-error", isError);
  }

  function initAnalyticsRangeDropdown(root, { defaultRange = "30d", onChange } = {}) {
    let dropdown = root.matches?.("[data-analytics-range-dropdown], [data-analytics-detail-range-dropdown]") ? root : null;
    if (!dropdown) {
      dropdown = root.querySelector("[data-analytics-range-dropdown]")
        || root.querySelector("[data-analytics-detail-range-dropdown]");
    }
    if (!dropdown) return null;

    const trigger = dropdown.querySelector("[data-analytics-range-trigger]");
    const menu = dropdown.querySelector("[data-analytics-range-menu]");
    const labelEl = dropdown.querySelector("[data-analytics-range-label]");
    const options = Array.from(dropdown.querySelectorAll("[data-analytics-range]"));
    let activeRange = normalizeRange(defaultRange);
    let open = false;
    let focusedIndex = -1;

    const updateMenuPlacement = () => {
      if (!trigger || !menu || menu.hidden) return;
      const triggerRect = trigger.getBoundingClientRect();
      const menuHeight = menu.offsetHeight || 240;
      const spaceBelow = window.innerHeight - triggerRect.bottom;
      const spaceAbove = triggerRect.top;
      const shouldFlip = spaceBelow < menuHeight + 12 && spaceAbove > spaceBelow;
      dropdown.classList.toggle("is-flipped", shouldFlip);
    };

    const syncActiveDescendant = () => {
      const focused = options[focusedIndex];
      if (focused?.id) {
        trigger?.setAttribute("aria-activedescendant", focused.id);
      } else {
        trigger?.removeAttribute("aria-activedescendant");
      }
    };

    const focusOption = (index) => {
      if (!options.length) return;
      focusedIndex = clamp(index, 0, options.length - 1);
      options[focusedIndex]?.focus();
      syncActiveDescendant();
    };

    const setOpen = (next) => {
      const willOpen = Boolean(next);
      if (willOpen === open) return;
      open = willOpen;
      dropdown.classList.toggle("is-open", open);
      if (menu) menu.hidden = !open;
      trigger?.setAttribute("aria-expanded", open ? "true" : "false");
      if (open) {
        focusedIndex = Math.max(0, options.findIndex((option) => option.dataset.analyticsRange === activeRange));
        requestAnimationFrame(() => {
          updateMenuPlacement();
          focusOption(focusedIndex);
        });
        window.addEventListener("resize", updateMenuPlacement);
      } else {
        focusedIndex = -1;
        trigger?.removeAttribute("aria-activedescendant");
        dropdown.classList.remove("is-flipped");
        window.removeEventListener("resize", updateMenuPlacement);
        trigger?.focus();
      }
    };

    const syncSelection = () => {
      options.forEach((option) => {
        const selected = option.dataset.analyticsRange === activeRange;
        option.classList.toggle("is-selected", selected);
        option.setAttribute("aria-selected", selected ? "true" : "false");
      });
      const activeOption = options.find((option) => option.dataset.analyticsRange === activeRange);
      if (labelEl && activeOption) labelEl.textContent = activeOption.textContent.trim();
    };

    const selectRange = (range) => {
      const nextRange = normalizeRange(range, defaultRange);
      if (nextRange === activeRange) {
        setOpen(false);
        return;
      }
      activeRange = nextRange;
      syncSelection();
      setOpen(false);
      onChange?.(activeRange);
    };

    trigger?.addEventListener("click", (event) => {
      event.stopPropagation();
      setOpen(!open);
    });

    menu?.addEventListener("click", (event) => {
      event.stopPropagation();
    });

    options.forEach((option, index) => {
      option.addEventListener("click", (event) => {
        event.stopPropagation();
        selectRange(option.dataset.analyticsRange);
      });
      option.addEventListener("focus", () => {
        focusedIndex = index;
        syncActiveDescendant();
      });
    });

    const handleDocumentClick = (event) => {
      if (!open) return;
      if (!dropdown.contains(event.target)) setOpen(false);
    };

    const handleDocumentKeydown = (event) => {
      if (!open) return;
      if (event.key === "Escape") {
        event.preventDefault();
        setOpen(false);
        return;
      }
      if (event.key === "ArrowDown") {
        event.preventDefault();
        focusOption(focusedIndex < 0 ? 0 : focusedIndex + 1);
        return;
      }
      if (event.key === "ArrowUp") {
        event.preventDefault();
        focusOption(focusedIndex < 0 ? options.length - 1 : focusedIndex - 1);
        return;
      }
      if (event.key === "Enter" || event.key === " ") {
        const active = document.activeElement;
        if (active && options.includes(active)) {
          event.preventDefault();
          selectRange(active.dataset.analyticsRange);
        }
      }
    };

    document.addEventListener("click", handleDocumentClick);
    document.addEventListener("keydown", handleDocumentKeydown);

    if (menu) menu.hidden = true;
    trigger?.setAttribute("aria-expanded", "false");
    dropdown.classList.remove("is-open", "is-flipped");
    syncSelection();

    return {
      destroy() {
        document.removeEventListener("click", handleDocumentClick);
        document.removeEventListener("keydown", handleDocumentKeydown);
        window.removeEventListener("resize", updateMenuPlacement);
      },
      getActiveRange: () => activeRange,
      isOpen: () => open,
      selectRange,
      setActiveRange(range) {
        activeRange = normalizeRange(range, defaultRange);
        syncSelection();
      },
      setOpen,
      updateMenuPlacement,
    };
  }

  function initAdminAnalytics(root = document) {
    const shell = root.querySelector("[data-admin-analytics]");
    if (!shell) return;
    const metricTabs = Array.from(shell.querySelectorAll("[data-analytics-metric]"));
    const defaultRange = normalizeRange(shell.dataset.defaultRange || "30d");
    let activeRange = defaultRange;
    let activeMetric = "totalUsers";
    let latestPayload = null;
    let token = 0;
    const detailTokens = { countries: 0, pages: 0 };
    const timezone = getBrowserTimezone();

    const rangeDropdown = initAnalyticsRangeDropdown(shell, {
      defaultRange,
      onChange: (range) => {
        load(range);
      },
    });

    const load = async (range) => {
      activeRange = normalizeRange(range, defaultRange);
      rangeDropdown?.setActiveRange(activeRange);
      setNotice(shell, "Loading analytics...");
      const requestToken = ++token;
      try {
        const response = await fetch(buildAnalyticsUrl(activeRange, timezone), {
          credentials: "same-origin",
          headers: { Accept: "application/json" },
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
          throw new Error(payload.error || "Unable to load analytics.");
        }
        if (requestToken !== token) return;
        latestPayload = payload;
        shell.dataset.activeMetric = activeMetric;
        renderDashboard(shell, payload);
        setNotice(shell, "");
      } catch (error) {
        if (requestToken !== token) return;
        setNotice(shell, error.message || "Unable to load analytics.", true);
      }
    };

    const loadDetailPanel = async (type, range) => {
      const dropdown = shell.querySelector(`[data-analytics-detail-range-dropdown="${type}"]`);
      const panel = dropdown?.closest(".admin-analytics-ga-panel") || dropdown?.closest(".admin-analytics-ga-detail-card");
      if (!panel) return;
      const detailRange = normalizeRange(range, defaultRange);
      const requestToken = ++detailTokens[type];
      try {
        const response = await fetch(buildAnalyticsUrl(detailRange, timezone), {
          credentials: "same-origin",
          headers: { Accept: "application/json" },
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(payload.error || "Unable to load analytics.");
        if (requestToken !== detailTokens[type]) return;
        renderGaDetailPanel(panel, payload, type);
      } catch {
        if (requestToken !== detailTokens[type]) return;
        renderGaDetailPanel(panel, {
          gaDetails: { countries: [], pages: [] },
          sources: { traffic: { label: "Google Analytics unavailable", status: "error" } },
        }, type);
      }
    };

    shell.querySelectorAll("[data-analytics-detail-range-dropdown]").forEach((dropdown) => {
      const type = dropdown.dataset.analyticsDetailRangeDropdown;
      if (type !== "countries" && type !== "pages") return;
      initAnalyticsRangeDropdown(dropdown, {
        defaultRange,
        onChange: (range) => {
          loadDetailPanel(type, range);
        },
      });
      loadDetailPanel(type, defaultRange);
    });

    metricTabs.forEach((tab) => {
      tab.addEventListener("click", () => {
        activeMetric = METRIC_CONFIG[tab.dataset.analyticsMetric] ? tab.dataset.analyticsMetric : "totalUsers";
        shell.dataset.activeMetric = activeMetric;
        if (latestPayload) renderMainMetric(shell, latestPayload, activeMetric);
      });
    });

    load(activeRange);
  }

  window.AdminAnalytics = {
    buildAnalyticsUrl,
    getBrowserTimezone,
    initAnalyticsRangeDropdown,
    niceAxis,
    normalizeRange,
    normalizeSeries,
    renderDashboard,
    renderBarList,
    renderDetailRows,
    renderGaDetailPanel,
    renderGaDetails,
    renderVerticalBarChart,
    renderLineChart,
    renderMultiLineChart,
    seriesIntervalIncrease,
    sumMultiSeriesIncrease,
    stableSeriesColor,
    sumSeries,
  };

  initAdminAnalytics();
})();
