/* Chart.js-Aufbau (aus dem Dynamischen Tarif Check übernommen, auf Erlöse und Viertelstunden umgestellt). */

// Feste Reihenfolge, keine automatisch generierten Farben (CVD-Sicherheit) -- muss zu den
// --series-1..8 Custom Properties in style.css passen. Tarife bekommen ihre Farbe in der
// Reihenfolge, in der sie im Tarif-Schritt stehen.
const SERIES_VARS = ['--series-1', '--series-2', '--series-3', '--series-4', '--series-5', '--series-6', '--series-7', '--series-8'];

let dailyChartInstance = null;

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function seriesColor(index) {
  return cssVar(SERIES_VARS[index % SERIES_VARS.length]);
}

function commonScaleOptions() {
  return {
    grid: { color: cssVar('--gridline'), drawTicks: false },
    ticks: { color: cssVar('--text-muted'), font: { size: 11 } },
    border: { color: cssVar('--baseline') },
  };
}

// Viertelstunden-Achsen: nur volle Stunden beschriften, sonst wird die Achse unlesbar.
function quarterHourScale() {
  const base = commonScaleOptions();
  return {
    ...base,
    grid: {
      ...base.grid,
      color(ctx) {
        const label = ctx.chart.data.labels[ctx.index] ?? '';
        return label.endsWith(':00') ? cssVar('--gridline') : 'transparent';
      },
    },
    ticks: {
      ...base.ticks,
      autoSkip: false,
      maxRotation: 0,
      callback(value) {
        const label = this.getLabelForValue(value);
        return label.endsWith(':00') && Number(label.slice(0, 2)) % 3 === 0 ? label : '';
      },
    },
  };
}

function renderLegend(elementId, names) {
  const el = document.getElementById(elementId);
  el.innerHTML = names
    .map(
      (name, i) =>
        `<span class="legend-item"><span class="legend-swatch" style="background:${seriesColor(i)}"></span>${escapeHtml(name)}</span>`
    )
    .join('');
}

const CHART_HEIGHT_PX = 320;

/* ---------- Vergleichs-Charts für zwei ausgewählte Tarife ----------
   pair: [{name, colorIndex}] -- Farben folgen dem Tarif (Index in der Tarifliste), nicht der
   Position im Paar, damit ein Tarif in allen Charts dieselbe Farbe hat. */

const pairChartInstances = {};

function recreatePairChart(canvasId, config) {
  if (pairChartInstances[canvasId]) pairChartInstances[canvasId].destroy();
  pairChartInstances[canvasId] = new Chart(document.getElementById(canvasId).getContext('2d'), config);
}

function formatEurShort(value) {
  return value.toLocaleString('de-DE', { style: 'currency', currency: 'EUR' });
}

function formatDe(value, digits) {
  return value.toLocaleString('de-DE', { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

function renderPairLegend(elementId, pair, suffix = '') {
  document.getElementById(elementId).innerHTML = pair
    .map((p) => `<span class="legend-item"><span class="legend-swatch" style="background:${seriesColor(p.colorIndex)}"></span>${escapeHtml(p.name)}${suffix}</span>`)
    .join('');
}

// Bilanz-Balken: diffs[i] = Erlös A − Erlös B, positiv = A besser. Die Farbe zeigt den jeweils
// besseren Tarif.
function renderAdvantageChart(canvasId, legendId, labels, diffs, pair, xScale = commonScaleOptions()) {
  const [a, b] = pair;
  renderPairLegend(legendId, pair, ' mehr Erlös');
  recreatePairChart(canvasId, {
    type: 'bar',
    data: {
      labels,
      datasets: [{
        data: diffs,
        backgroundColor: diffs.map((d) => seriesColor(d >= 0 ? a.colorIndex : b.colorIndex)),
        borderRadius: 3,
        maxBarThickness: 32,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: (ctx) => {
              const d = ctx.parsed.y;
              return `${d >= 0 ? a.name : b.name} ${formatEurShort(Math.abs(d))} mehr Erlös`;
            },
          },
        },
      },
      scales: {
        x: xScale,
        y: {
          ...commonScaleOptions(),
          title: { display: true, text: `€ (positiv = ${a.name} besser)`, color: cssVar('--text-secondary') },
        },
      },
    },
  });
}

// Vergütungslinien je Viertelstunde. ratesByName: {name: [ct/kWh | null, ...]}.
function renderRateLinesChart(canvasId, legendId, labels, ratesByName, pair, yTitle) {
  renderPairLegend(legendId, pair);
  recreatePairChart(canvasId, {
    type: 'line',
    data: {
      labels,
      datasets: pair.map((p) => ({
        label: p.name,
        data: ratesByName[p.name],
        borderColor: seriesColor(p.colorIndex),
        backgroundColor: seriesColor(p.colorIndex),
        borderWidth: 2,
        pointRadius: 0,
        pointHoverRadius: 5,
        tension: 0.2,
        spanGaps: false,
      })),
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: (ctx) => (ctx.parsed.y === null ? `${ctx.dataset.label}: kein Preis` : `${ctx.dataset.label}: ${formatDe(ctx.parsed.y, 2)} ct/kWh`),
          },
        },
      },
      scales: {
        x: quarterHourScale(),
        y: { ...commonScaleOptions(), title: { display: true, text: yTitle, color: cssVar('--text-secondary') } },
      },
    },
  });
}

// Einzelne Einspeise-Reihe (neutrale Farbe, keine Legende nötig -- die Überschrift benennt sie).
function renderFeedInChart(canvasId, labels, values, yTitle, tooltipLabel) {
  recreatePairChart(canvasId, {
    type: 'bar',
    data: {
      labels,
      datasets: [{
        data: values,
        backgroundColor: cssVar('--text-muted'),
        borderRadius: 2,
        categoryPercentage: 0.9,
        barPercentage: 0.9,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: (ctx) => tooltipLabel(ctx.dataIndex, ctx.parsed.y) } },
      },
      scales: {
        x: quarterHourScale(),
        y: { ...commonScaleOptions(), beginAtZero: true, title: { display: true, text: yTitle, color: cssVar('--text-secondary') } },
      },
    },
  });
}

/* Monatsbilanz des gewählten Tarifpaars. */

function renderPairMonthsChart(months, pair) {
  const [a, b] = pair;
  renderAdvantageChart(
    'chart-pair-months',
    'legend-pair-months',
    months.map((m) => formatMonthLabel(m.date)),
    months.map((m) => m.revenues[a.name] - m.revenues[b.name]),
    pair,
  );
}

/* Viertelstunden-Analyse: dieselben Charts für einen einzelnen Tag (Antwort von /api/day-detail). */

function renderDayCharts(detail, pair) {
  const [a, b] = pair;
  const labels = detail.slots.map((s) => s.time);
  renderAdvantageChart(
    'chart-day-advantage',
    'legend-day-advantage',
    labels,
    detail.slots.map((s) => s.revenue_eur[a.name] - s.revenue_eur[b.name]),
    pair,
    quarterHourScale(),
  );
  const rates = Object.fromEntries(pair.map((p) => [p.name, detail.slots.map((s) => s.rates_ct_kwh[p.name])]));
  renderRateLinesChart('chart-day-prices', 'legend-day-prices', labels, rates, pair, 'ct/kWh');
  renderFeedInChart(
    'chart-day-feed-in',
    labels,
    detail.slots.map((s) => s.feed_in_kwh),
    'kWh',
    (i, v) => `${formatDe(v, 3)} kWh`,
  );
}

function formatMonthLabel(monthKey) {
  const [year, month] = monthKey.split('-').map(Number);
  return new Date(year, month - 1, 1).toLocaleDateString('de-DE', { month: 'short', year: 'numeric' });
}

function formatDayLabel(dateStr) {
  const d = new Date(dateStr + 'T00:00:00');
  return d.toLocaleDateString('de-DE', { day: '2-digit', month: '2-digit' });
}

/* items: [{date, revenues: {tarifname: betrag}}] für 'month'/'day', [{label, feed_in_kwh, revenues}] für
   den Tagesverlauf ('quarter'/'hour'). granularity steuert Label-Format, Balkenbreite und Achsentitel.
   onBarClick(index) macht die Balken anklickbar (Drill-down Monat -> Tag -> Tagesverlauf).
   Erzeugt bei jedem Aufruf einen komplett neuen Chart (destroy + recreate), damit Breite/Höhe immer
   zur aktuellen Datenmenge passen. */
const DAILY_CHART_GRANULARITY = {
  month: { axisTitle: '€ / Monat', perCategory: (n) => Math.max(70, n * 26), maxBar: 32 },
  day: { axisTitle: '€ / Tag', perCategory: (n) => Math.max(18, n * 8), maxBar: 16 },
  hour: { axisTitle: '€ / Stunde', perCategory: (n) => Math.max(30, n * 10), maxBar: 16 },
  quarter: { axisTitle: '€ / Viertelstunde', perCategory: (n) => Math.max(10, n * 5), maxBar: 8 },
};

function renderDailyChart(items, tariffNames, granularity, onBarClick = null) {
  renderLegend('legend-daily', tariffNames);

  if (dailyChartInstance) {
    dailyChartInstance.destroy();
    dailyChartInstance = null;
  }

  const cfg = DAILY_CHART_GRANULARITY[granularity];
  const isCurve = granularity === 'quarter' || granularity === 'hour';
  const labels = items.map((it) => {
    if (isCurve) return it.label;
    return granularity === 'day' ? formatDayLabel(it.date) : formatMonthLabel(it.date);
  });

  // Größe auf dem Wrapper-Div, Chart.js per responsive:true/maintainAspectRatio:false darauf
  // angesetzt -- so bleibt der Graph inkl. devicePixelRatio scharf.
  const width = Math.max(480, items.length * cfg.perCategory(tariffNames.length));
  const wrap = document.getElementById('chart-daily-wrap');
  wrap.style.width = width + 'px';
  wrap.style.height = CHART_HEIGHT_PX + 'px';

  const ctx = document.getElementById('chart-daily').getContext('2d');

  const datasets = tariffNames.map((name, i) => ({
    label: name,
    data: items.map((it) => it.revenues[name]),
    backgroundColor: seriesColor(i),
    borderRadius: isCurve ? 2 : 4,
    maxBarThickness: cfg.maxBar,
    categoryPercentage: 0.8,
    barPercentage: 0.9,
  }));

  let xScale = { ...commonScaleOptions(), ticks: { ...commonScaleOptions().ticks, maxTicksLimit: granularity === 'day' ? 20 : undefined } };
  if (granularity === 'quarter') xScale = quarterHourScale();

  dailyChartInstance = new Chart(ctx, {
    type: 'bar',
    data: { labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      onClick: onBarClick
        ? (event, elements) => { if (elements.length) onBarClick(elements[0].index); }
        : undefined,
      onHover: (event, elements) => {
        event.native.target.style.cursor = onBarClick && elements.length ? 'pointer' : 'default';
      },
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: (c) => `${c.dataset.label}: ${formatEurShort(c.parsed.y)}`,
            footer: (ctxs) => {
              if (!ctxs.length) return '';
              const item = items[ctxs[0].dataIndex];
              if (isCurve) return `Einspeisung: ${formatDe(item.feed_in_kwh, 3)} kWh`;
              return onBarClick ? 'Klicken für Details' : '';
            },
          },
        },
      },
      scales: {
        x: xScale,
        y: {
          ...commonScaleOptions(),
          beginAtZero: true,
          title: { display: true, text: cfg.axisTitle, color: cssVar('--text-secondary') },
        },
      },
    },
  });
}
