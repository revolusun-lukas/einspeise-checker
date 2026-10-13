/* Chart.js-Aufbau (aus dem Dynamischen Tarif Check übernommen, auf Erlöse und Viertelstunden umgestellt). */

// Feste Reihenfolge, keine automatisch generierten Farben (CVD-Sicherheit) -- muss zu den
// --series-1..8 Custom Properties in style.css passen. Tarife bekommen ihre Farbe in der
// Reihenfolge, in der sie im Tarif-Schritt stehen.
const SERIES_VARS = ['--series-1', '--series-2', '--series-3', '--series-4', '--series-5', '--series-6', '--series-7', '--series-8'];

let dailyChartInstance = null;
let socChartInstance = null;

// Feste Breite der Y-Achsen im Tagesverlauf: Erlös- und Speicherstand-Diagramm liegen untereinander,
// ihre Zeitachsen sollen exakt übereinander stehen -- unabhängig von der Länge der Achsenbeschriftung.
const CURVE_AXIS_WIDTH_PX = 64;
const fixAxisWidth = (scale) => { scale.width = CURVE_AXIS_WIDTH_PX; };

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

/* Klickbare Legenden: Ein Eintrag blendet die zugehörige Reihe (Dataset-Index) aus und wieder ein.
   Ausgeblendete Reihen werden je Diagramm über ihr Dataset-Label gemerkt -- so bleibt die Auswahl beim
   Blättern zwischen Tagen und beim Wechsel Monat/Tag/Tagesverlauf erhalten. */
const hiddenSeries = { daily: new Set(), soc: new Set() };

const swatchMarker = (color) => `<span class="legend-swatch" style="background:${color}"></span>`;
const lineMarker = (color) => `<span class="legend-line" style="background:${color}"></span>`;

function legendToggleHtml(datasetIndex, text, marker) {
  return `<button type="button" class="legend-item legend-toggle" data-index="${datasetIndex}" aria-pressed="true" ` +
    `title="Ein-/ausblenden">${marker}${escapeHtml(text)}</button>`;
}

function bindLegendToggles(legendId, chart, key) {
  const hidden = hiddenSeries[key];
  document.getElementById(legendId).querySelectorAll('.legend-toggle').forEach((btn) => {
    const index = Number(btn.dataset.index);
    const label = chart.data.datasets[index].label;
    const apply = () => {
      const isHidden = hidden.has(label);
      chart.setDatasetVisibility(index, !isHidden);
      btn.classList.toggle('is-off', isHidden);
      btn.setAttribute('aria-pressed', String(!isHidden));
    };
    apply();
    btn.addEventListener('click', () => {
      if (hidden.has(label)) hidden.delete(label);
      else hidden.add(label);
      apply();
      chart.update();
    });
  });
  chart.update('none');
}


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
   spotValues (ct/kWh je Kategorie, null = kein Preis) blendet den Börsenpreis als Linie auf einer
   eigenen rechten Achse ein.
   Der Chart füllt die verfügbare Breite (CSS .fit-chart) -- bei vielen Kategorien werden die Balken
   schmaler, es gibt keinen horizontalen Scrollbalken. Jeder Aufruf erzeugt einen neuen Chart
   (destroy + recreate), weil sich Achsen und Datensätze je Ansicht ändern. */
const DAILY_CHART_GRANULARITY = {
  month: { axisTitle: '€ / Monat', maxBar: 32 },
  day: { axisTitle: '€ / Tag', maxBar: 16 },
  hour: { axisTitle: '€ / Stunde', maxBar: 16 },
  quarter: { axisTitle: '€ / Viertelstunde', maxBar: 8 },
};

/* Wertebereiche zweier Achsen so erweitern, dass ihre Nulllinien auf derselben Höhe liegen --
   sonst wirkt die Börsenpreis-Linie negativ, wo sie nur unter der Null der Erlös-Achse liegt.
   Liefert je Achse {min, max}. */
function alignedZeroRanges(valuesA, valuesB) {
  const bounds = (values) => {
    const finite = values.filter((v) => v !== null && Number.isFinite(v));
    let lo = Math.min(0, ...finite);
    let hi = Math.max(0, ...finite);
    if (hi === lo) hi = lo + 1;
    // Nur negative Werte: etwas Platz über der Null, damit die andere Achse ihre positiven Werte zeigen kann.
    if (hi === 0) hi = -lo * 0.05;
    return { lo, hi };
  };
  const a = bounds(valuesA);
  const b = bounds(valuesB);
  // Anteil der Achsenhöhe unterhalb der Null -- der größere gilt für beide.
  const f = Math.max(-a.lo / (a.hi - a.lo), -b.lo / (b.hi - b.lo));
  const stretch = ({ lo, hi }) => {
    if (f === 0) return { min: 0, max: hi };
    const loNeeded = (-f * hi) / (1 - f);
    return loNeeded <= lo ? { min: loNeeded, max: hi } : { min: lo, max: (-lo * (1 - f)) / f };
  };
  return [stretch(a), stretch(b)];
}

function renderDailyChart(items, tariffNames, granularity, onBarClick = null, spotValues = null) {
  // Reihenfolge wie die Datasets unten: erst die Tarife, dann der Börsenpreis.
  document.getElementById('legend-daily').innerHTML =
    tariffNames.map((name, i) => legendToggleHtml(i, name, swatchMarker(seriesColor(i)))).join('') +
    (spotValues ? legendToggleHtml(tariffNames.length, 'Börsenpreis (ct/kWh, rechte Achse)', lineMarker(cssVar('--text-primary'))) : '');

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

  const ctx = document.getElementById('chart-daily').getContext('2d');

  const datasets = tariffNames.map((name, i) => ({
    label: name,
    data: items.map((it) => it.revenues[name]),
    backgroundColor: seriesColor(i),
    borderRadius: isCurve ? 2 : 4,
    maxBarThickness: cfg.maxBar,
    categoryPercentage: 0.8,
    barPercentage: 0.9,
    order: 1,
  }));
  if (spotValues) {
    // order 0 = über den Balken gezeichnet.
    datasets.push({
      type: 'line',
      label: 'Börsenpreis',
      data: spotValues,
      yAxisID: 'ySpot',
      borderColor: cssVar('--text-primary'),
      backgroundColor: cssVar('--text-primary'),
      borderWidth: 1.5,
      pointRadius: granularity === 'quarter' ? 0 : 2,
      pointHoverRadius: 4,
      tension: 0,
      spanGaps: false,
      order: 0,
    });
  }

  // Waagerechte Beschriftung; bei wenig Platz blendet Chart.js einzelne Labels aus (autoSkip).
  let xScale = { ...commonScaleOptions(), ticks: { ...commonScaleOptions().ticks, maxRotation: 0, maxTicksLimit: granularity === 'day' ? 20 : undefined } };
  if (granularity === 'quarter') xScale = quarterHourScale();

  // Mit Börsenpreis: feste Achsengrenzen mit gemeinsamer Nulllinie. Die Grenzen sind dann keine
  // runden Werte, daher die Achsenbeschriftung auf 2 Nachkommastellen begrenzen.
  let revenueRange = {};
  let spotRange = {};
  if (spotValues) {
    const revenues = tariffNames.flatMap((name) => items.map((it) => it.revenues[name]));
    [revenueRange, spotRange] = alignedZeroRanges(revenues, spotValues);
  }
  const tickLabel = (value) => value.toLocaleString('de-DE', { maximumFractionDigits: 2 });

  dailyChartInstance = new Chart(ctx, {
    type: 'bar',
    data: { labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      // Ganze Spalte im Tooltip (alle Tarife + Börsenpreis) und als Klickfläche.
      interaction: { mode: 'index', intersect: false },
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
            label: (c) => {
              if (c.dataset.yAxisID === 'ySpot') {
                return c.parsed.y === null ? 'Börsenpreis: kein Preis' : `Börsenpreis: ${formatDe(c.parsed.y, 2)} ct/kWh${granularity === 'day' ? ' (Ø des Tages)' : ''}`;
              }
              return `${c.dataset.label}: ${formatEurShort(c.parsed.y)}`;
            },
            footer: (ctxs) => {
              if (!ctxs.length) return '';
              const item = items[ctxs[0].dataIndex];
              if (isCurve) {
                // Füllstand nur für Tarife mit fiktivem Speicher (soc[name] !== null).
                const socLines = tariffNames
                  .filter((name) => item.soc && item.soc[name] != null)
                  .map((name) => `Speicher ${name}: ${formatDe(item.socPct[name], 0)} % (${formatDe(item.soc[name], 1)} kWh)`);
                return [`PV-Überschuss (gemessen): ${formatDe(item.feed_in_kwh, 3)} kWh`, ...socLines];
              }
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
          ...revenueRange,
          ticks: { ...commonScaleOptions().ticks, callback: tickLabel },
          ...(isCurve ? { afterFit: fixAxisWidth } : {}),
          title: { display: true, text: cfg.axisTitle, color: cssVar('--text-secondary') },
        },
        ...(spotValues ? {
          ySpot: {
            ...commonScaleOptions(),
            ...spotRange,
            ticks: { ...commonScaleOptions().ticks, callback: tickLabel },
            ...(isCurve ? { afterFit: fixAxisWidth } : {}),
            position: 'right',
            grid: { drawOnChartArea: false },
            title: { display: true, text: 'Börsenpreis ct/kWh', color: cssVar('--text-secondary') },
          },
        } : {}),
      },
    },
  });
  bindLegendToggles('legend-daily', dailyChartInstance, 'daily');
}

function hideSocChart() {
  if (socChartInstance) {
    socChartInstance.destroy();
    socChartInstance = null;
  }
  document.getElementById('chart-soc-block').hidden = true;
}

/* Speicherstand und Netzeinspeisung im Tagesverlauf, nur für Tarife mit fiktivem Speicher.
   items wie beim Erlös-Diagramm ({label, feed_in_kwh, socPct: {tarif: %}, grid: {tarif: kWh}});
   colorIndex je Tarif aus der Tarifliste, damit Linie und Balken dieselbe Farbe haben wie die Erlös-Balken.
   Linie = Speicherstand in % (links), Balken = gemessener PV-Überschuss (grau) und Netzeinspeisung des
   Tarifs (farbig) in kWh (rechts). */
function renderSocChart(items, storageNames, tariffNames, granularity) {
  if (!storageNames.length) {
    hideSocChart();
    return;
  }
  document.getElementById('chart-soc-block').hidden = false;
  const color = (name) => seriesColor(tariffNames.indexOf(name));
  // Balken halbtransparent, damit die Speicherstand-Linie davor lesbar bleibt (#rrggbb + Alpha).
  const barColor = (name) => `${color(name)}99`;
  // Reihenfolge wie die Datasets unten: Speicherstand-Linien, PV-Überschuss, Netzeinspeisung je Tarif.
  const k = storageNames.length;
  document.getElementById('legend-soc').innerHTML =
    storageNames.map((name, i) => legendToggleHtml(i, `Speicherstand ${name}`, lineMarker(color(name)))).join('') +
    legendToggleHtml(k, 'PV-Überschuss (gemessen)', swatchMarker(cssVar('--text-muted'))) +
    storageNames.map((name, i) => legendToggleHtml(k + 1 + i, `Netzeinspeisung ${name}`, swatchMarker(barColor(name)))).join('');
  if (socChartInstance) socChartInstance.destroy();

  const unit = granularity === 'quarter' ? 'kWh / Viertelstunde' : 'kWh / Stunde';
  const xScale = granularity === 'quarter'
    ? quarterHourScale()
    : { ...commonScaleOptions(), ticks: { ...commonScaleOptions().ticks, maxRotation: 0 } };
  const datasets = [
    // order 0 = vorne: Linien über den Balken.
    ...storageNames.map((name) => ({
      type: 'line',
      label: `Speicherstand ${name}`,
      data: items.map((it) => it.socPct[name]),
      yAxisID: 'y',
      borderColor: color(name),
      backgroundColor: color(name),
      borderWidth: 2,
      pointRadius: 0,
      pointHoverRadius: 4,
      tension: 0,
      order: 0,
    })),
    {
      type: 'bar',
      label: 'PV-Überschuss (gemessen)',
      data: items.map((it) => it.feed_in_kwh),
      yAxisID: 'yKwh',
      backgroundColor: cssVar('--text-muted'),
      categoryPercentage: 0.9,
      barPercentage: 0.9,
      order: 1,
    },
    ...storageNames.map((name) => ({
      type: 'bar',
      label: `Netzeinspeisung ${name}`,
      data: items.map((it) => it.grid[name]),
      yAxisID: 'yKwh',
      backgroundColor: barColor(name),
      categoryPercentage: 0.9,
      barPercentage: 0.9,
      order: 1,
    })),
  ];

  socChartInstance = new Chart(document.getElementById('chart-soc').getContext('2d'), {
    type: 'bar',
    data: { labels: items.map((it) => it.label), datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: (c) => (c.dataset.yAxisID === 'y'
              ? `${c.dataset.label}: ${formatDe(c.parsed.y, 0)} %`
              : `${c.dataset.label}: ${formatDe(c.parsed.y, 3)} kWh`),
          },
        },
      },
      scales: {
        x: xScale,
        y: {
          ...commonScaleOptions(),
          min: 0,
          max: 100,
          ticks: { ...commonScaleOptions().ticks, stepSize: 50, callback: (v) => `${v} %` },
          afterFit: fixAxisWidth,
        },
        // Rechts in derselben Breite wie die Börsenpreis-Achse darüber -- Zeitachsen bleiben bündig.
        yKwh: {
          ...commonScaleOptions(),
          position: 'right',
          beginAtZero: true,
          grid: { drawOnChartArea: false },
          ticks: { ...commonScaleOptions().ticks, callback: (v) => v.toLocaleString('de-DE', { maximumFractionDigits: 2 }) },
          title: { display: true, text: unit, color: cssVar('--text-secondary') },
          afterFit: fixAxisWidth,
        },
      },
    },
  });
  bindLegendToggles('legend-soc', socChartInstance, 'soc');
}
