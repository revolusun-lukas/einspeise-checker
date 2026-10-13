/* Wizard-Logik: Upload -> Mapping bestätigen -> Tarife -> Ergebnis.
   Aufbau aus dem Dynamischen Tarif Check übernommen; hier Erlös (mehr = besser) statt Kosten. */

const state = {
  sessionId: null,
  comparePair: [], // die zwei im Ergebnis angeklickten Tarifnamen für Paar- und Tagesvergleich
  compareDays: { a: null, b: null }, // Datum des besten Tages je Tarif des Paars
};

const el = (id) => document.getElementById(id);

// Escaped Werte, die per Template-Literal in innerHTML landen (CSV-Inhalte, Tarifnamen).
function escapeHtml(value) {
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

/* ---------- Wizard-Schritte (alle direkt auf der Seite, kein Popup -- iframe-tauglich) ---------- */

const STEP_IDS = ['step-entry', 'modal-help', 'modal-mapping', 'modal-tariffs', 'modal-results', 'modal-day-detail'];

function showStep(id) {
  STEP_IDS.forEach((s) => { el(s).hidden = (s !== id); });
  const target = el(id);
  target.focus({ preventScroll: true });
  target.scrollIntoView({ behavior: 'smooth', block: 'start' });
  // scrollIntoView wirkt nur im eigenen Dokument -- eingebettet in ein iframe ohne eigenen
  // Scrollbereich kann die Elternseite auf diese Nachricht reagieren.
  if (window.parent !== window) {
    window.parent.postMessage({ source: 'einspeise-checker', action: 'scrollIntoView' }, '*');
  }
}

document.querySelectorAll('[data-back-to]').forEach((btn) => {
  btn.addEventListener('click', () => showStep(btn.dataset.backTo));
});

el('agb-consent').addEventListener('change', () => {
  const accepted = el('agb-consent').checked;
  el('csv-file').disabled = !accepted;
  el('btn-upload').disabled = !accepted;
  el('btn-upload').title = accepted ? '' : 'Bitte zuerst der Datennutzung zustimmen';
});

el('btn-open-help').addEventListener('click', () => showStep('modal-help'));

function showError(message) {
  const banner = el('error-banner');
  banner.textContent = message;
  banner.hidden = false;
  banner.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

function clearError() {
  const banner = el('error-banner');
  banner.hidden = true;
  banner.textContent = '';
}

// Lade-Spinner + Text im Button, solange eine Anfrage läuft (Preisabruf kann ein paar Sekunden dauern).
function setButtonLoading(button, isLoading, loadingText) {
  if (isLoading) {
    if (button.dataset.originalText === undefined) {
      button.dataset.originalText = button.textContent;
    }
    button.disabled = true;
    button.innerHTML = `<span class="btn-spinner"></span>${loadingText}`;
  } else {
    button.disabled = false;
    button.textContent = button.dataset.originalText ?? button.textContent;
  }
}

async function apiRequest(url, options) {
  let response;
  try {
    response = await fetch(url, options);
  } catch (networkErr) {
    throw new Error('Server nicht erreichbar. Läuft die Anwendung noch?');
  }
  if (!response.ok) {
    let detail = `Fehler (${response.status})`;
    try {
      const body = await response.json();
      if (body && typeof body.detail === 'string') detail = body.detail;
      else if (body && Array.isArray(body.detail)) detail = body.detail.map((d) => d.msg).join(' · ');
    } catch (_) {
      /* ignore parse failure, keep generic message */
    }
    throw new Error(detail);
  }
  return response.json();
}

function postJson(url, payload) {
  return apiRequest(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
}

/* ---------- Schritt 1: Upload ---------- */

el('btn-upload').addEventListener('click', async () => {
  clearError();
  const fileInput = el('csv-file');
  if (!fileInput.files.length) {
    showError('Bitte zuerst eine CSV-Datei auswählen.');
    return;
  }
  const formData = new FormData();
  formData.append('file', fileInput.files[0]);

  const btn = el('btn-upload');
  setButtonLoading(btn, true, 'Wird hochgeladen und analysiert…');
  try {
    const data = await apiRequest('/api/import/upload', { method: 'POST', body: formData });
    state.sessionId = data.session_id;
    populateMappingStep(data);
    showStep('modal-mapping');
  } catch (err) {
    showError(err.message);
  } finally {
    setButtonLoading(btn, false);
  }
});

/* ---------- Schritt 2: Mapping ---------- */

function populateMappingStep(data) {
  const tsSelect = el('select-timestamp-col');
  const valSelect = el('select-value-col');
  const gridSelect = el('select-grid-col');
  tsSelect.innerHTML = '';
  valSelect.innerHTML = '';
  gridSelect.innerHTML = '';
  gridSelect.appendChild(new Option('– keine –', ''));
  data.columns.forEach((col) => {
    tsSelect.appendChild(new Option(col, col));
    valSelect.appendChild(new Option(col, col));
    gridSelect.appendChild(new Option(col, col));
  });

  if (data.suggested_timestamp_column) tsSelect.value = data.suggested_timestamp_column;
  if (data.suggested_value_column) valSelect.value = data.suggested_value_column;
  if (data.suggested_value_type) el('select-value-type').value = data.suggested_value_type;
  el('select-direction').value = data.suggested_direction || 'feed_in';
  gridSelect.value = data.suggested_grid_column || '';
  el('select-timezone').value = data.suggested_timezone || 'Europe/Berlin';
  updateGridColumnState();

  const warningsBox = el('mapping-warnings');
  if (data.warnings && data.warnings.length) {
    warningsBox.hidden = false;
    warningsBox.innerHTML = '<strong>Hinweise:</strong><ul>' + data.warnings.map((w) => `<li>${escapeHtml(w)}</li>`).join('') + '</ul>';
  } else {
    warningsBox.hidden = true;
    warningsBox.innerHTML = '';
  }

  renderPreviewTable(data.columns, data.preview_rows);
}

// Bei Netto-Werten steckt der Bezug schon in der Einspeise-Spalte -- eine eigene Bezugsspalte entfällt.
function updateGridColumnState() {
  const net = el('select-direction').value === 'net';
  el('select-grid-col').disabled = net;
  el('grid-col-label').classList.toggle('is-disabled', net);
  if (net) el('select-grid-col').value = '';
}
el('select-direction').addEventListener('change', updateGridColumnState);

function renderPreviewTable(columns, rows) {
  const table = el('preview-table');
  const thead = '<thead><tr>' + columns.map((c) => `<th>${escapeHtml(c)}</th>`).join('') + '</tr></thead>';
  const tbody =
    '<tbody>' +
    rows
      .map((row) => '<tr>' + columns.map((c) => `<td>${escapeHtml(row[c] ?? '')}</td>`).join('') + '</tr>')
      .join('') +
    '</tbody>';
  table.innerHTML = thead + tbody;
}

el('btn-confirm-mapping').addEventListener('click', async () => {
  clearError();
  const payload = {
    session_id: state.sessionId,
    timestamp_column: el('select-timestamp-col').value,
    value_column: el('select-value-col').value,
    value_type: el('select-value-type').value,
    direction: el('select-direction').value,
    grid_column: el('select-grid-col').value || null,
    timezone: el('select-timezone').value,
  };

  const btn = el('btn-confirm-mapping');
  setButtonLoading(btn, true, 'Viertelstundenwerte werden berechnet…');
  try {
    const data = await postJson('/api/import/confirm', payload);
    showImportSummary(data);
    showStep('modal-tariffs');
  } catch (err) {
    showError(err.message);
  } finally {
    setButtonLoading(btn, false);
  }
});

function showImportSummary(data) {
  el('summary-total-kwh').textContent = `${formatNum(data.total_feed_in_kwh, 1)} kWh`;
  const grid = data.total_grid_kwh == null ? '' : ` · Netzbezug ${formatNum(data.total_grid_kwh, 1)} kWh`;
  el('summary-meta').textContent =
    `Zeitraum ${formatDateTime(data.start_date)} – ${formatDateTime(data.end_date)} (${formatNum(data.slots_count, 0)} Viertelstunden)${grid}`;

  const warningsBox = el('summary-warnings');
  warningsBox.innerHTML = data.warnings && data.warnings.length
    ? '<ul>' + data.warnings.map((w) => `<li>${escapeHtml(w)}</li>`).join('') + '</ul>'
    : '';
}

function formatDateTime(isoString) {
  return new Date(isoString).toLocaleString('de-DE', { dateStyle: 'medium', timeStyle: 'short' });
}

/* ---------- Schritt 3: Tarife ---------- */

// 8 = Anzahl der Kategorial-Farben in style.css (--series-1..--series-8).
const MAX_TARIFFS = 8;
// Muss zu SafeName in app/schemas.py passen (max_length + verbotene Zeichen).
const TARIFF_NAME_MAX_LENGTH = 40;
const UNSAFE_NAME_CHARS_RE = /[<>"'`]/;
const DEFAULT_TARIFF_NAMES = { eeg: 'EEG-Vergütung', direktvermarktung: 'Direktvermarktung' };
const TYPE_LABELS = { eeg: 'EEG', direktvermarktung: 'Direktvermarktung' };

// autoName = Name ist noch ein vom Programm vergebener Standardname und darf beim Typwechsel
// mitwandern. Sobald der Nutzer den Namen selbst editiert, bleibt er fest.
// Startwerte sind Beispiele (netto) und sollen durch eigene Werte ersetzt werden.
let tariffs = [
  { uid: 1, type: 'eeg', name: 'EEG-Vergütung', autoName: true, verguetung_ct_kwh: 7.86, keine_verguetung_bei_negativen_preisen: true },
  { uid: 2, type: 'direktvermarktung', name: 'Direktvermarktung', autoName: true,
    abschlag_ct_kwh: 0.5, grundgebuehr_eur_monat: 5, einmalkosten_eur: 100 },
];
let tariffUidCounter = Math.max(...tariffs.map((t) => t.uid));

function normalizeTariffName(name) {
  return String(name ?? '').trim().toLowerCase();
}

// Liefert base, "base 2", "base 3", ... -- den ersten Namen, den kein anderer Tarif nutzt.
function uniqueTariffName(base, ownUid = null) {
  const taken = new Set(tariffs.filter((t) => t.uid !== ownUid).map((t) => normalizeTariffName(t.name)));
  if (!taken.has(normalizeTariffName(base))) return base;
  for (let i = 2; ; i += 1) {
    const candidate = `${base} ${i}`;
    if (!taken.has(normalizeTariffName(candidate))) return candidate;
  }
}

// Gibt eine Fehlermeldung zurück oder null, wenn der Name gültig ist.
function tariffNameError(t) {
  const name = String(t.name ?? '').trim();
  if (!name) return 'Bitte einen Namen vergeben.';
  if (name.length > TARIFF_NAME_MAX_LENGTH) return `Maximal ${TARIFF_NAME_MAX_LENGTH} Zeichen.`;
  if (UNSAFE_NAME_CHARS_RE.test(name)) return 'Die Zeichen < > " \' ` sind nicht erlaubt.';
  const duplicate = tariffs.some(
    (other) => other.uid !== t.uid && normalizeTariffName(other.name) === normalizeTariffName(name),
  );
  if (duplicate) return 'Dieser Name wird schon von einem anderen Tarif verwendet.';
  return null;
}

function updateTariffNameErrors() {
  let first = null;
  tariffs.forEach((t) => {
    const message = tariffNameError(t);
    const errorEl = el(`tariff-${t.uid}-name-error`);
    errorEl.textContent = message ?? '';
    errorEl.hidden = !message;
    el(`tariff-${t.uid}-name`).setAttribute('aria-invalid', message ? 'true' : 'false');
    if (message && !first) first = `Tarif „${String(t.name).trim() || '(ohne Namen)'}“: ${message}`;
  });
  return first;
}

function round2(v) {
  return Math.round(v * 100) / 100;
}

function emptyAsZero(v) {
  return v === '' || v == null ? 0 : parseFloat(v);
}

function formatCt(value) {
  return `${value.toLocaleString('de-DE', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ct/kWh`;
}

function numberInput(id, value, { step = '0.01' } = {}) {
  return `<input type="number" id="${id}" step="${step}" min="0" value="${escapeHtml(value ?? '')}">`;
}

// Grundgebühr wahlweise als Monats- oder Jahresbetrag. Intern wird immer der Monatsbetrag geführt
// und exakt (ungerundet) ÷ 12 gerechnet (wie im Tarifcheck).
function grundgebuehrFieldHtml(t) {
  const yearly = Boolean(t.grundgebuehrJaehrlich);
  const monthly = parseFloat(t.grundgebuehr_eur_monat);
  const shown = Number.isFinite(monthly) ? round2(yearly ? monthly * 12 : monthly) : '';
  return `
    <label>Grundgebühr netto (${yearly ? '€/Jahr' : '€/Monat'})
      ${numberInput(`tariff-${t.uid}-grundgebuehr`, shown)}
      <span class="tariff-inline-check">
        <input type="checkbox" id="tariff-${t.uid}-grund-jaehrlich" ${yearly ? 'checked' : ''}>
        <span>Jahresbetrag</span>
      </span>
      <span class="field-hint" id="tariff-${t.uid}-grund-hint">${grundgebuehrHint(t)}</span>
    </label>`;
}

function grundgebuehrHint(t) {
  const monthly = parseFloat(t.grundgebuehr_eur_monat);
  if (t.grundgebuehrJaehrlich && Number.isFinite(monthly)) return `= ${formatEur(monthly)} pro Monat`;
  return 'Feste Gebühr des Direktvermarkters, wird je Kalendermonat abgezogen.';
}

function readGrundgebuehr(t) {
  const raw = el(`tariff-${t.uid}-grundgebuehr`).value;
  const value = parseFloat(raw);
  if (!Number.isFinite(value)) return raw; // leer/ungültig -> Validierung beim Berechnen
  // Unveränderter (nur gerundet angezeigter) Wert: exakten Monatsbetrag behalten.
  const current = parseFloat(t.grundgebuehr_eur_monat);
  if (Number.isFinite(current) && round2(t.grundgebuehrJaehrlich ? current * 12 : current) === value) return current;
  return t.grundgebuehrJaehrlich ? value / 12 : value;
}

function eegFieldsHtml(t) {
  return `
    <label>Vergütungssatz netto (ct/kWh)
      ${numberInput(`tariff-${t.uid}-verguetung`, t.verguetung_ct_kwh)}
      <span class="field-hint">Hängt vom Inbetriebnahmedatum und der Anlagengröße ab – steht in der
        Abrechnung des Netzbetreibers. Vorbelegt: Beispielwert Teileinspeisung bis 10 kWp.</span>
    </label>
    <label class="tariff-check">
      <span class="tariff-inline-check">
        <input type="checkbox" id="tariff-${t.uid}-negativ" ${t.keine_verguetung_bei_negativen_preisen ? 'checked' : ''}>
        <span>Keine Vergütung bei negativen Börsenpreisen</span>
      </span>
      <span class="field-hint">Gesetzliche Regel für neuere Anlagen (Solarspitzengesetz, Inbetriebnahme ab
        25.02.2025): In Viertelstunden mit negativem Börsenpreis entfällt die Vergütung.</span>
    </label>`;
}

function dvFieldsHtml(t) {
  return `
    <label>Abschlag netto (ct/kWh)
      ${numberInput(`tariff-${t.uid}-abschlag`, t.abschlag_ct_kwh)}
      <span class="field-hint">Vermarktungsentgelt: wird je kWh vom Börsenpreis abgezogen.</span>
    </label>
    ${grundgebuehrFieldHtml(t)}
    <label>Einmalkosten netto (€)
      ${numberInput(`tariff-${t.uid}-einmalkosten`, t.einmalkosten_eur ?? 0)}
      <span class="field-hint">Z.B. Smart Meter / Steuerbox. Wird wie im Tarifcheck aufs erste Jahr verteilt
        (1/12 je Monat); bei kürzerem Zeitraum anteilig.</span>
    </label>
    <p class="tariff-preview">Erlös je Viertelstunde = Einspeisung × (Börsenpreis − Abschlag).
      Negative Börsenpreise kosten Geld – Abschalten wird (noch) nicht simuliert.</p>`;
}

function tariffRowHtml(t) {
  const canRemove = tariffs.length > 2;
  return `
    <fieldset class="tariff-box" data-uid="${t.uid}">
      <legend>
        <input type="text" id="tariff-${t.uid}-name" class="tariff-name-input" value="${escapeHtml(t.name)}"
          maxlength="${TARIFF_NAME_MAX_LENGTH}" aria-label="Tarifname" aria-describedby="tariff-${t.uid}-name-error">
        ${canRemove ? `<button type="button" id="tariff-${t.uid}-remove" class="btn-remove-tariff" title="Tarif entfernen">✕</button>` : ''}
      </legend>
      <p class="field-error" id="tariff-${t.uid}-name-error" role="alert" hidden></p>
      <label>Typ
        <select id="tariff-${t.uid}-type">
          <option value="eeg" ${t.type === 'eeg' ? 'selected' : ''}>EEG-Vergütung</option>
          <option value="direktvermarktung" ${t.type === 'direktvermarktung' ? 'selected' : ''}>Direktvermarktung</option>
        </select>
      </label>
      ${t.type === 'eeg' ? eegFieldsHtml(t) : dvFieldsHtml(t)}
    </fieldset>`;
}

// Die Eingabefelder im DOM sind die "Wahrheit" während der Bearbeitung. Vor jedem strukturellen
// Rerender müssen die eingegebenen Werte zurück ins tariffs-Array, sonst gehen sie verloren.
function syncTariffFromDom(t) {
  t.name = el(`tariff-${t.uid}-name`).value;
  if (t.type === 'eeg') {
    t.verguetung_ct_kwh = el(`tariff-${t.uid}-verguetung`).value;
    t.keine_verguetung_bei_negativen_preisen = el(`tariff-${t.uid}-negativ`).checked;
    return;
  }
  t.abschlag_ct_kwh = el(`tariff-${t.uid}-abschlag`).value;
  t.grundgebuehr_eur_monat = readGrundgebuehr(t);
  t.einmalkosten_eur = el(`tariff-${t.uid}-einmalkosten`).value;
}

function syncTariffsFromDom() {
  tariffs.forEach(syncTariffFromDom);
}

// Fehlende Werte eines neuen oder umgestellten Tarifs ergänzen. Gebühren einer Direktvermarktung
// kommen vom ersten anderen Direktvermarktungs-Tarif, damit meist nur der Abschlag anzupassen ist.
function ensureDefaults(t) {
  if (t.type === 'eeg') {
    t.verguetung_ct_kwh ??= 7.86;
    t.keine_verguetung_bei_negativen_preisen ??= true;
    return;
  }
  const template = tariffs.find((o) => o.type === 'direktvermarktung' && o.uid !== t.uid);
  t.abschlag_ct_kwh ??= template ? template.abschlag_ct_kwh : 0.5;
  if (t.grundgebuehr_eur_monat == null || t.grundgebuehr_eur_monat === '') {
    t.grundgebuehr_eur_monat = template ? template.grundgebuehr_eur_monat : 5;
    t.grundgebuehrJaehrlich = template ? Boolean(template.grundgebuehrJaehrlich) : false;
  }
  t.einmalkosten_eur ??= template ? template.einmalkosten_eur : 100;
}

function renderTariffList() {
  el('tariff-list').innerHTML = tariffs.map(tariffRowHtml).join('');

  tariffs.forEach((t) => {
    el(`tariff-${t.uid}-name`).addEventListener('input', (e) => {
      t.name = e.target.value;
      t.autoName = false;
      updateTariffNameErrors();
    });
    el(`tariff-${t.uid}-type`).addEventListener('change', (e) => {
      syncTariffsFromDom();
      t.type = e.target.value;
      ensureDefaults(t);
      if (t.autoName) t.name = uniqueTariffName(DEFAULT_TARIFF_NAMES[t.type], t.uid);
      renderTariffList();
    });
    if (t.type === 'direktvermarktung') {
      el(`tariff-${t.uid}-grund-jaehrlich`).addEventListener('change', (e) => {
        syncTariffsFromDom();
        t.grundgebuehrJaehrlich = e.target.checked;
        renderTariffList();
      });
      el(`tariff-${t.uid}-grundgebuehr`).addEventListener('input', () => {
        t.grundgebuehr_eur_monat = readGrundgebuehr(t);
        el(`tariff-${t.uid}-grund-hint`).textContent = grundgebuehrHint(t);
      });
    }
    const removeBtn = document.getElementById(`tariff-${t.uid}-remove`);
    if (removeBtn) {
      removeBtn.addEventListener('click', () => {
        syncTariffsFromDom();
        tariffs = tariffs.filter((row) => row.uid !== t.uid);
        renderTariffList();
      });
    }
  });

  el('btn-add-tariff').disabled = tariffs.length >= MAX_TARIFFS;
  updateTariffNameErrors();
}

el('btn-add-tariff').addEventListener('click', () => {
  if (tariffs.length >= MAX_TARIFFS) return;
  syncTariffsFromDom();
  tariffUidCounter += 1;
  const newTariff = {
    uid: tariffUidCounter,
    type: 'direktvermarktung',
    name: uniqueTariffName(DEFAULT_TARIFF_NAMES.direktvermarktung),
    autoName: true,
  };
  ensureDefaults(newTariff);
  tariffs.push(newTariff);
  renderTariffList();
});

renderTariffList();

function tariffPayload(t) {
  const name = t.name.trim();
  if (t.type === 'eeg') {
    return {
      type: 'eeg',
      name,
      verguetung_ct_kwh: parseFloat(t.verguetung_ct_kwh),
      keine_verguetung_bei_negativen_preisen: Boolean(t.keine_verguetung_bei_negativen_preisen),
    };
  }
  return {
    type: 'direktvermarktung',
    name,
    abschlag_ct_kwh: emptyAsZero(t.abschlag_ct_kwh),
    grundgebuehr_eur_monat: emptyAsZero(t.grundgebuehr_eur_monat),
    einmalkosten_eur: emptyAsZero(t.einmalkosten_eur),
  };
}

el('btn-calculate').addEventListener('click', async () => {
  clearError();
  syncTariffsFromDom();

  const nameError = updateTariffNameErrors();
  if (nameError) {
    showError(nameError);
    return;
  }

  const payloadTariffs = [];
  for (const t of tariffs) {
    const entry = tariffPayload(t);
    const numbers = Object.values(entry).filter((v) => typeof v === 'number');
    if (numbers.some(Number.isNaN)) {
      showError(`Bitte alle Felder von "${entry.name}" mit gültigen Zahlen ausfüllen.`);
      return;
    }
    if (numbers.some((v) => v < 0)) {
      showError(`"${entry.name}": Werte dürfen nicht negativ sein.`);
      return;
    }
    payloadTariffs.push(entry);
  }

  const btn = el('btn-calculate');
  setButtonLoading(btn, true, 'Börsenpreise werden abgerufen und Erlöse berechnet…');
  try {
    const data = await postJson('/api/calculate', { session_id: state.sessionId, tariffs: payloadTariffs });
    // Erst sichtbar machen, dann rendern: Chart.js misst beim Aufbau die Containergröße.
    showStep('modal-results');
    renderResults(data);
  } catch (err) {
    showError(err.message);
  } finally {
    setButtonLoading(btn, false);
  }
});

/* ---------- Schritt 4: Ergebnis ---------- */

function formatEur(value) {
  return value.toLocaleString('de-DE', { style: 'currency', currency: 'EUR' });
}

function formatSignedEur(value) {
  return `${value > 0 ? '+' : ''}${formatEur(value)}`;
}

function formatDateOnly(isoDate) {
  return new Date(isoDate + 'T00:00:00').toLocaleDateString('de-DE', { dateStyle: 'medium' });
}

function formatNum(value, digits) {
  return value.toLocaleString('de-DE', { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

function formatOptionalNum(value, digits) {
  return value === null || value === undefined ? '–' : formatNum(value, digits);
}

// Rohdaten der letzten Berechnung, damit der Erlös-Chart lokal zwischen Monat/Tag umschalten kann.
let resultDailyRaw = [];
let resultTariffNames = [];
let resultTariffTypes = {}; // Name -> 'eeg' | 'direktvermarktung'
let resultTariffs = [];
let resultTotalKwh = 0;
let chartGranularity = 'month';
let selectedMonthKey = null; // nur relevant, wenn chartGranularity === 'day'

function getAvailableMonths(daily) {
  return Array.from(new Set(daily.map((d) => d.date.slice(0, 7)))).sort();
}

function renderMonthTabs(months, selected) {
  const container = el('chart-month-tabs');
  container.innerHTML = months
    .map((m) => `<button type="button" class="month-tab-btn ${m === selected ? 'active' : ''}" data-month="${m}">${formatMonthLabel(m)}</button>`)
    .join('');
  container.querySelectorAll('.month-tab-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      if (btn.classList.contains('active')) return;
      selectedMonthKey = btn.dataset.month;
      renderDailyChartForGranularity();
    });
  });
}

function renderDailyChartForGranularity() {
  const tabsContainer = el('chart-month-tabs');
  if (chartGranularity === 'day') {
    const months = getAvailableMonths(resultDailyRaw);
    if (!selectedMonthKey || !months.includes(selectedMonthKey)) selectedMonthKey = months[0];
    renderMonthTabs(months, selectedMonthKey);
    tabsContainer.hidden = false;
    renderDailyChart(resultDailyRaw.filter((d) => d.date.slice(0, 7) === selectedMonthKey), resultTariffNames, 'day');
  } else {
    tabsContainer.hidden = true;
    renderDailyChart(aggregateByMonth(resultDailyRaw, resultTariffNames), resultTariffNames, 'month');
  }
}

document.querySelectorAll('#chart-granularity-toggle .granularity-btn').forEach((btn) => {
  btn.addEventListener('click', () => {
    if (btn.classList.contains('active')) return;
    document.querySelectorAll('#chart-granularity-toggle .granularity-btn').forEach((b) => b.classList.remove('active'));
    btn.classList.add('active');
    chartGranularity = btn.dataset.granularity;
    renderDailyChartForGranularity();
  });
});

function tariffBreakdownHtml(t) {
  if (t.type === 'eeg') {
    const lost = t.lost_negative_eur > 0
      ? `<br>entfallen bei negativen Preisen: ${formatEur(t.lost_negative_eur)}` : '';
    return `Ø ${formatCt(t.avg_ct_kwh)}${lost}`;
  }
  return `Ø ${formatCt(t.avg_ct_kwh)} nach Gebühren<br>` +
    `Börse ${formatEur(t.energy_revenue_eur)} − Grundgebühr ${formatEur(t.base_fee_eur)}` +
    (t.setup_eur > 0 ? ` − Einmalkosten ${formatEur(t.setup_eur)}` : '');
}

function renderResults(data) {
  const names = data.tariffs.map((t) => t.name);

  el('stat-grid-tariffs').innerHTML = data.tariffs
    .map((t, i) => `
      <button type="button" class="stat-tile tariff-pick" data-index="${i}" aria-pressed="false">
        <span class="pick-badge">✓ im Vergleich</span>
        <div class="stat-label">${escapeHtml(t.name)} · ${TYPE_LABELS[t.type]}</div>
        <div class="stat-value">${formatEur(t.total_eur)}</div>
        <div class="stat-delta ${t.diff_to_best_eur < 0 ? 'negative' : 'positive'}">${
          t.name === data.best_name ? 'bester Tarif' : `${formatEur(t.diff_to_best_eur)} ggü. ${escapeHtml(data.best_name)}`}</div>
        <div class="stat-breakdown">${tariffBreakdownHtml(t)}</div>
      </button>`)
    .join('');
  el('stat-grid-tariffs').querySelectorAll('.tariff-pick').forEach((tile) => {
    tile.addEventListener('click', () => selectCompareTariff(names[Number(tile.dataset.index)]));
  });

  el('stat-best').textContent = data.best_name;
  el('stat-advantage').textContent = formatEur(data.best_vs_worst_eur);
  el('stat-advantage-detail').textContent = `mehr als ${data.worst_name}`;
  el('stat-feed-in').textContent = `${formatNum(data.total_kwh, 0)} kWh`;
  el('stat-period').textContent = `in ${formatNum(Math.round(data.period_days), 0)} Tagen`;
  el('stat-market-value').textContent = data.spot.feed_in_weighted_ct_kwh == null ? '–' : formatCt(data.spot.feed_in_weighted_ct_kwh);
  el('stat-market-value-detail').textContent = data.spot.time_avg_ct_kwh == null ? '' :
    `Ø Börsenpreis je eingespeister kWh; zeitlicher Ø im Zeitraum: ${formatCt(data.spot.time_avg_ct_kwh)}`;

  const missingBox = el('missing-price-note');
  missingBox.hidden = data.slots_missing_price === 0;
  missingBox.textContent = data.slots_missing_price === 0 ? '' :
    `${formatNum(data.slots_missing_price, 0)} von ${formatNum(data.slots_total, 0)} Viertelstunden hatten keinen Börsenpreis ` +
    `(${formatNum(data.kwh_missing_price, 1)} kWh Einspeisung) und wurden bei allen Tarifen aus dem Vergleich ausgeschlossen.`;

  renderPriceSourceNote(data.price_resolution);
  renderNegativePriceNote(data.negative_prices);

  resultDailyRaw = data.daily;
  resultTariffNames = names;
  resultTariffTypes = Object.fromEntries(data.tariffs.map((t) => [t.name, t.type]));
  resultTariffs = data.tariffs;
  resultTotalKwh = data.total_kwh;
  state.comparePair = [names[0], names[1]];
  renderComparePair();
  renderDailyChartForGranularity();
}

function renderPriceSourceNote(res) {
  const parts = [];
  if (res.quarter_hour_slots) parts.push(`${formatNum(res.quarter_hour_slots, 0)} Viertelstunden mit echten Viertelstundenpreisen (ab 01.10.2025)`);
  if (res.hour_slots) parts.push(`${formatNum(res.hour_slots, 0)} Viertelstunden mit Stundenpreisen (vor 01.10.2025, auf 4 Viertelstunden übertragen)`);
  if (res.awattar_slots) parts.push(`${formatNum(res.awattar_slots, 0)} Viertelstunden mit aWATTar-Stundenpreisen (Ersatzquelle)`);
  el('price-source-note').innerHTML =
    `<strong>Börsenpreise:</strong> Day-Ahead DE-LU, SMARD (Bundesnetzagentur), netto. ${escapeHtml(parts.join(' · '))}.` +
    res.notes.map((n) => `<br>${escapeHtml(n)}`).join('');
}

function renderNegativePriceNote(neg) {
  const box = el('negative-price-note');
  box.hidden = neg.slots === 0;
  if (neg.slots === 0) return;
  box.innerHTML =
    `<strong>Negative Börsenpreise:</strong> ${formatNum(neg.slots, 0)} Viertelstunden im Zeitraum, in ` +
    `${formatNum(neg.slots_with_feed_in, 0)} davon hast du eingespeist – insgesamt ${formatNum(neg.feed_in_kwh, 1)} kWh ` +
    `(${formatNum(neg.share_percent, 1)} % deiner Einspeisung). In der Direktvermarktung hat diese Menge zum Börsenpreis ` +
    `${formatEur(neg.spot_value_eur)} gekostet (zzgl. Abschlag). Mit EEG-Regel für neuere Anlagen gibt es dafür keine Vergütung.`;
}

function aggregateByMonth(daily, names) {
  const buckets = new Map();
  daily.forEach((d) => {
    const monthKey = d.date.slice(0, 7);
    if (!buckets.has(monthKey)) buckets.set(monthKey, Object.fromEntries(names.map((n) => [n, 0])));
    const bucket = buckets.get(monthKey);
    names.forEach((n) => { bucket[n] += d.revenues[n] ?? 0; });
  });
  return Array.from(buckets.entries())
    .sort(([a], [b]) => (a < b ? -1 : 1))
    .map(([monthKey, revenues]) => ({ date: monthKey, revenues }));
}

/* ---------- Tagesvergleich zweier frei gewählter Tarife ----------
   Für das gewählte Paar A/B wird je Tarif der Tag gesucht, an dem er gegenüber dem anderen
   den größten Mehrerlös hatte. */

function selectCompareTariff(name) {
  if (state.comparePair.includes(name)) return;
  state.comparePair = [state.comparePair[1], name];
  renderComparePair();
}

function bestDayFor(name, other) {
  return resultDailyRaw.reduce((best, d) => {
    const advantage = d.revenues[name] - d.revenues[other];
    return !best || advantage > best.advantage ? { date: d.date, advantage, revenues: d.revenues } : best;
  }, null);
}

function renderDayCompareItem(slot, name, other) {
  const day = bestDayFor(name, other);
  state.compareDays[slot] = day.date;
  const nameSafe = escapeHtml(name);
  const otherSafe = escapeHtml(other);

  el(`day-${slot}-heading`).innerHTML = `Bester Tag für ${nameSafe}`;
  // War der Tarif an keinem Tag (mind. 1 Cent) besser, gibt es keinen "besten Tag".
  const neverBetter = day.advantage < 0.005;
  el(`btn-day-${slot}-detail`).hidden = neverBetter;
  if (neverBetter) {
    el(`day-${slot}-title`).innerHTML = `${nameSafe} hat an keinem Tag mehr erlöst als ${otherSafe}.`;
    return;
  }
  el(`day-${slot}-title`).innerHTML =
    `${formatDateOnly(day.date)}: ${nameSafe} hat <span class="positive">${formatEur(day.advantage)} mehr</span> erlöst ` +
    `als ${otherSafe} (${nameSafe}: ${formatEur(day.revenues[name])} · ${otherSafe}: ${formatEur(day.revenues[other])}, jeweils inkl. Gebühren)`;
}

function renderComparePair() {
  const [a, b] = state.comparePair;
  document.querySelectorAll('#stat-grid-tariffs .tariff-pick').forEach((tile) => {
    const selected = state.comparePair.includes(resultTariffNames[Number(tile.dataset.index)]);
    tile.classList.toggle('selected', selected);
    tile.setAttribute('aria-pressed', String(selected));
  });
  el('day-compare-pair').innerHTML = pairBannerHtml(a, b);
  renderDayCompareItem('a', a, b);
  renderDayCompareItem('b', b, a);
  renderPairAnalysis();
}

/* ---------- Paarvergleich über den gesamten Zeitraum ---------- */

function countWins(items, a, b) {
  return items.reduce(
    (acc, it) => {
      const diff = it.revenues[a] - it.revenues[b];
      if (Math.abs(diff) < 0.005) acc.equal += 1;
      else if (diff > 0) acc.a += 1;
      else acc.b += 1;
      return acc;
    },
    { a: 0, b: 0, equal: 0 },
  );
}

function statTileHtml(label, value, deltaHtml = '', id = '') {
  return `<div class="stat-tile"${id ? ` id="${id}"` : ''}><div class="stat-label">${label}</div>` +
    `<div class="stat-value">${value}</div>${deltaHtml ? `<div class="stat-delta">${deltaHtml}</div>` : ''}</div>`;
}

function winsText(wins, a, b, unit) {
  const parts = [`${escapeHtml(a)}: ${wins.a}`, `${escapeHtml(b)}: ${wins.b}`];
  if (wins.equal) parts.push(`gleich: ${wins.equal}`);
  return `${parts.join(' · ')} ${unit}`;
}

let pairAnalysisRequestId = 0;

// Vergütungsverläufe sind nur mit Direktvermarktung aussagekräftig -- EEG ist eine flache Linie.
function pairHasMarket(names) {
  return names.some((n) => resultTariffTypes[n] === 'direktvermarktung');
}

function chartPair(names) {
  return names.map((name) => ({ name, colorIndex: resultTariffNames.indexOf(name) }));
}

function pairBannerHtml(a, b) {
  const chip = (name) => {
    const t = resultTariffs.find((row) => row.name === name);
    const color = seriesColor(resultTariffNames.indexOf(name));
    return `<span class="pair-chip" style="border-color:${color}">` +
      `<span class="legend-swatch" style="background:${color}"></span>` +
      `<span class="pair-chip-name">${escapeHtml(name)}</span>` +
      `<span class="pair-chip-meta">${TYPE_LABELS[t.type]} · ${formatEur(t.total_eur)}</span></span>`;
  };
  return `${chip(a)}<span class="pair-vs">vs.</span>${chip(b)}`;
}

async function renderPairAnalysis() {
  const [a, b] = state.comparePair;
  const ta = resultTariffs.find((t) => t.name === a);
  const tb = resultTariffs.find((t) => t.name === b);
  const [winner, loser] = ta.total_eur >= tb.total_eur ? [ta, tb] : [tb, ta];
  const diff = winner.total_eur - loser.total_eur;
  const diffPerKwh = resultTotalKwh ? (diff / resultTotalKwh) * 100 : 0;
  const months = aggregateByMonth(resultDailyRaw, [a, b]);
  const dayWins = countWins(resultDailyRaw, a, b);
  const monthWins = countWins(months, a, b);
  const winnerWins = (wins) => (winner.name === a ? wins.a : wins.b);

  el('pair-title').innerHTML = pairBannerHtml(a, b);
  el('pair-kpis').innerHTML =
    statTileHtml(
      'Mehr Erlös im Gesamtzeitraum',
      escapeHtml(winner.name),
      `<span class="positive">${formatSignedEur(diff)}</span> ggü. ${escapeHtml(loser.name)}`,
    ) +
    statTileHtml(
      'Differenz je kWh',
      formatCt(diffPerKwh),
      `So viel müsste ${escapeHtml(loser.name)} je eingespeister kWh mehr bringen, um gleichzuziehen`,
    ) +
    statTileHtml(`Tage, an denen ${escapeHtml(winner.name)} besser war`, `${winnerWins(dayWins)} von ${resultDailyRaw.length}`, winsText(dayWins, a, b, 'Tage')) +
    statTileHtml(`Monate, in denen ${escapeHtml(winner.name)} besser war`, `${winnerWins(monthWins)} von ${months.length}`, winsText(monthWins, a, b, 'Monate')) +
    [a, b]
      .filter((n) => resultTariffTypes[n] === 'direktvermarktung')
      .map((n, i) => statTileHtml(`Profilfaktor ${escapeHtml(n)}`, '…', 'wird berechnet', `pair-profile-${i}`))
      .join('');

  const first = resultDailyRaw[0].date;
  const last = resultDailyRaw[resultDailyRaw.length - 1].date;
  el('pair-period').textContent = `über ${resultDailyRaw.length} Tage (${formatDateOnly(first)} – ${formatDateOnly(last)})`;

  const pair = chartPair([a, b]);
  renderPairMonthsChart(months, pair);

  // Bei schnellem Umklicken nur die Antwort der letzten Anfrage rendern.
  const requestId = ++pairAnalysisRequestId;
  try {
    const analysis = await postJson('/api/pair-analysis', { session_id: state.sessionId, tariff_names: [a, b] });
    if (requestId !== pairAnalysisRequestId) return;
    // Erst ein-/ausblenden, dann rendern (Chart.js misst die Containergröße).
    el('pair-prices-section').hidden = !pairHasMarket([a, b]);
    renderPairProfileCharts(analysis.slots, pair);
    [a, b]
      .filter((n) => resultTariffTypes[n] === 'direktvermarktung')
      .forEach((n, i) => {
        const tile = el(`pair-profile-${i}`);
        const p = analysis.profile[n];
        if (p.profile_factor_ct_kwh === null) return;
        const good = p.profile_factor_ct_kwh >= 0;
        tile.querySelector('.stat-value').textContent = `${p.profile_factor_ct_kwh > 0 ? '+' : ''}${formatCt(p.profile_factor_ct_kwh)}`;
        tile.querySelector('.stat-delta').innerHTML =
          `Ø erlöst ${formatCt(p.weighted_avg_ct_kwh)} vs. zeitlicher Ø ${formatCt(p.time_avg_ct_kwh)} — ` +
          `<span class="${good ? 'positive' : 'negative'}">Einspeisung liegt eher in ${good ? 'teuren' : 'günstigen'} Viertelstunden</span>`;
      });
  } catch (err) {
    if (requestId === pairAnalysisRequestId) showError(err.message);
  }
}

/* ---------- Viertelstunden-Analyse eines Tages ---------- */

function renderDayDetailTable(detail, [a, b], titleHtml) {
  const aSafe = escapeHtml(a);
  const bSafe = escapeHtml(b);
  el('day-detail-title').innerHTML = titleHtml;

  // Je Tarif eine Spaltengruppe: Vergütung (ohne Gebühren), inkl. Gebühren-Anteil, Erlös.
  const groups = [a, b].map((name) => ({ name, safe: escapeHtml(name), subtitle: TYPE_LABELS[resultTariffTypes[name]] }));
  // Bei der Zeitumstellung gibt es eine Uhrzeit doppelt -- dann Sommer-/Winterzeit dazuschreiben.
  const counts = detail.slots.reduce((acc, s) => ({ ...acc, [s.time]: (acc[s.time] || 0) + 1 }), {});
  const timeLabel = (s) => (counts[s.time] > 1 ? `${s.time} (${s.utc_offset_hours === 2 ? 'MESZ' : 'MEZ'})` : s.time);

  const header =
    '<thead><tr><th rowspan="2">Zeit</th><th rowspan="2">Einspeisung</th><th rowspan="2">Börsenpreis<br>ct/kWh</th>' +
    groups.map((g) => `<th colspan="3" class="group-head">${g.safe}<span class="group-sub">${g.subtitle}</span></th>`).join('') +
    `<th rowspan="2">Vorteil<br>${aSafe}</th></tr><tr>` +
    groups.map(() => '<th>Vergütung<br>ct/kWh</th><th>inkl. Gebühren*<br>ct/kWh</th><th>Erlös<br>€</th>').join('') +
    '</tr></thead>';

  const body =
    '<tbody>' +
    detail.slots
      .map((s) => {
        const advantage = s.revenue_eur[a] - s.revenue_eur[b];
        const cells = groups.map((g) =>
          `<td>${formatOptionalNum(s.rates_ct_kwh[g.name], 2)}</td>` +
          `<td>${formatOptionalNum(s.net_rates_ct_kwh[g.name], 2)}</td><td>${formatNum(s.revenue_eur[g.name], 4)}</td>`,
        ).join('');
        const spotClass = s.spot_ct_kwh !== null && s.spot_ct_kwh < 0 ? ' class="negative"' : '';
        return `<tr><td>${timeLabel(s)}</td><td>${formatNum(s.feed_in_kwh, 3)} kWh</td>` +
          `<td${spotClass}>${formatOptionalNum(s.spot_ct_kwh, 2)}</td>${cells}` +
          `<td class="${advantage >= 0 ? 'positive' : 'negative'}">${formatNum(advantage, 4)}</td></tr>`;
      })
      .join('') +
    '</tbody>';

  // Summenzeile: Vergütung als einspeisegewichteter Tagesdurchschnitt (Ø), Erlöse als Summe.
  const kwh = detail.feed_in_kwh;
  const weightedAvg = (key, name) => {
    const slots = detail.slots.filter((s) => s[key][name] != null);
    const slotKwh = slots.reduce((sum, s) => sum + s.feed_in_kwh, 0);
    return slotKwh > 0 ? slots.reduce((sum, s) => sum + s[key][name] * s.feed_in_kwh, 0) / slotKwh : null;
  };
  const spotSlots = detail.slots.filter((s) => s.spot_ct_kwh != null);
  const spotKwh = spotSlots.reduce((sum, s) => sum + s.feed_in_kwh, 0);
  const spotAvg = spotKwh > 0 ? spotSlots.reduce((sum, s) => sum + s.spot_ct_kwh * s.feed_in_kwh, 0) / spotKwh : null;
  const dayAdvantage = detail.totals_eur[a] - detail.totals_eur[b];
  const footer =
    `<tfoot><tr class="detail-sum"><td>Summe</td><td>${formatNum(kwh, 3)} kWh</td><td>Ø ${formatOptionalNum(spotAvg, 2)}</td>` +
    groups.map((g) =>
      `<td>Ø ${formatOptionalNum(weightedAvg('rates_ct_kwh', g.name), 2)}</td>` +
      `<td>Ø ${formatOptionalNum(kwh > 0 ? (detail.totals_eur[g.name] / kwh) * 100 : null, 2)}</td>` +
      `<td>${formatEur(detail.totals_eur[g.name])}</td>`,
    ).join('') +
    `<td class="${dayAdvantage >= 0 ? 'positive' : 'negative'}">${formatEur(dayAdvantage)}</td></tr></tfoot>`;

  el('table-day-detail').innerHTML = header + body + footer;
  el('day-detail-note').innerHTML =
    '<strong>Vergütung</strong>: EEG-Satz bzw. Börsenpreis − Abschlag, ohne Grundgebühr. ' +
    '<strong>* inkl. Gebühren</strong>: Vergütung abzüglich Grundgebühr- und Einmalkosten-Anteil (Monatsgebühr ÷ Monatseinspeisung). ' +
    '<strong>Erlös</strong>: Erlös der Viertelstunde inkl. Gebühren-Anteil. ' +
    `<strong>Vorteil ${aSafe}</strong>: Erlös ${aSafe} − Erlös ${bSafe} (positiv = ${aSafe} besser). Ø-Werte einspeisegewichtet.`;
}

async function openDayDetail(slot) {
  // Der Tarif, dessen bester Tag angezeigt wird, steht in der Tabelle vorne.
  const [a, b] = state.comparePair;
  const pair = slot === 'a' ? [a, b] : [b, a];
  const btn = el(`btn-day-${slot}-detail`);
  const titleHtml = el(`day-${slot}-title`).innerHTML;
  clearError();
  btn.disabled = true;
  try {
    const detail = await postJson('/api/day-detail', { session_id: state.sessionId, date: state.compareDays[slot], tariff_names: pair });
    renderDayDetailTable(detail, pair, titleHtml);
    // Erst sichtbar machen, dann Charts bauen (Chart.js misst die Containergröße).
    showStep('modal-day-detail');
    renderDayCharts(detail, chartPair(pair));
  } catch (err) {
    showError(err.message);
  } finally {
    btn.disabled = false;
  }
}

el('btn-day-a-detail').addEventListener('click', () => openDayDetail('a'));
el('btn-day-b-detail').addEventListener('click', () => openDayDetail('b'));

el('btn-restart').addEventListener('click', () => {
  window.location.reload();
});

/* ---------- Iframe-Einbettung: Höhe an die Elternseite melden, damit das iframe dort
   automatisch mitwächst/-schrumpft statt eine feste Höhe mit Scrollbalken zu brauchen. ---------- */

if (window.parent !== window) {
  let lastReportedHeight = 0;
  const reportHeight = () => {
    const height = Math.ceil(document.body.getBoundingClientRect().height);
    if (height !== lastReportedHeight) {
      lastReportedHeight = height;
      window.parent.postMessage({ source: 'einspeise-checker', height }, '*');
    }
  };
  new ResizeObserver(reportHeight).observe(document.body);
  reportHeight();
}
