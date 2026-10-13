# Einspeise-Checker

Webanwendung, die auf Basis **echter Messdaten** historisch berechnet, was die PV-Einspeisung
mit verschiedenen Einspeise-„Tarifen“ erlöst hätte: **EEG-Vergütung** (fester Satz) gegenüber
**Direktvermarktung** (Börsenpreis minus Gebühren). Gerechnet wird je Viertelstunde mit den echten
Day-Ahead-Börsenpreisen. Annahmen gibt es keine: Verwendet werden nur die gemessene Einspeisung und
die eingegebenen Tarife.

Aufbau wie beim [Dynamischen Tarif Check](https://github.com/revolusun-lukas/dynamischer-tarif-check):
FastAPI und statisches Frontend mit Chart.js. Alle Daten (CSV-Upload, Zwischenergebnisse) liegen nur
im Arbeitsspeicher des Servers und werden nirgends dauerhaft gespeichert. Sessions laufen nach
2 Stunden ab, ein Neustart löscht alles.

## Installation und Start

Voraussetzung ist Python 3.11 oder neuer. Für den Abruf der Börsenpreise braucht der Server Internetzugang.

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
source .venv/bin/activate     # macOS/Linux
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Danach **http://localhost:8000** öffnen.

Tests:

```bash
pip install -r requirements-dev.txt
pytest
```

## Bedienung

### 1. Einspeisedaten hochladen

Gebraucht wird ein CSV-Export eines Smartmeters oder Energiemessgeräts. Vor dem Upload muss der
Datennutzung zugestimmt werden.

Ohne eigene Daten geht es mit **„Mit Beispieldaten ausprobieren“**: Netzeinspeisung einer PV-Anlage mit
Batteriespeicher für das ganze Jahr 2025 in Viertelstunden (`static/sample/beispiel-einspeisung-2025.csv`,
rund 5.300 kWh). Die Datei durchläuft denselben Import wie ein Upload. Die automatischen Vorschläge passen
(geprüft in `tests/test_importer.py`), deshalb geht es direkt weiter zu den Tarifen.

### 2. Spaltenzuordnung prüfen

Die Anwendung schlägt Zeitstempel-Spalte, Einspeise-Spalte, Richtung, Bedeutung und Zeitzone
automatisch vor. Alles lässt sich korrigieren.

- **Einspeise-Spalte**: Bevorzugt werden Spalten, deren Name auf Einspeisung hindeutet, z. B.
  Shelly EM „Returned energy Wh“, Shelly Pro 3EM „total_act_ret_energy“ oder „Einspeisung“.
- **Richtung**:
  - *Nur Einspeisung*: Die Spalte enthält positive Einspeisewerte.
  - *Netto*: Negative Werte sind Einspeisung, positive Werte Bezug (z. B. die Momentanleistung
    eines Zweirichtungszählers). Je **Messintervall** wird nur der negative Anteil als Einspeisung
    gezählt, **erst danach** wird auf Viertelstunden summiert. Eine Minute Einspeisung verschwindet
    also nicht hinter einer Minute Bezug in derselben Viertelstunde.
- **Bedeutung**: Momentanleistung (W/kW), Energie pro Intervall (Wh/kWh) oder kumulierter
  Zählerstand (kWh, nur bei „nur Einspeisung“; Resets werden ignoriert).
- **Bezugsspalte (optional)**: z. B. Shelly „Active energy Wh“. Sie wird mit eingelesen und für
  spätere Speicher-/HEMS-Simulationen vorgehalten. Bei Netto-Daten ergibt sich der Bezug automatisch.
- **Zeitzone**: Europe/Berlin oder UTC. Steht „UTC“ im Spaltennamen, wird UTC vorgeschlagen.

Die Messwerte werden anteilig auf ein **15-Minuten-Raster (UTC)** verteilt. Danach wird die
ermittelte Einspeisung angezeigt. Diesen Wert mit Zähler bzw. Abrechnung vergleichen.

### 3. Tarife eingeben (2–8, eindeutige Namen)

**Alle Werte netto.** Der Börsenpreis ist netto, und private Anlagenbetreiber sind in der Regel
Kleinunternehmer.

- **EEG-Vergütung**: Vergütungssatz (ct/kWh). Optional „Keine Vergütung bei negativen
  Börsenpreisen“: die gesetzliche Regel für neuere Anlagen (Solarspitzengesetz, Inbetriebnahme ab
  25.02.2025), angewendet je Viertelstunde.
- **Direktvermarktung**: Abschlag (ct/kWh), Grundgebühr (€/Monat, wahlweise als Jahresbetrag
  eingegeben) und Einmalkosten (€, z. B. Smart Meter). Optional „Abschalten bei negativer
  Vergütung“: Nicht einspeisen, sobald Börsenpreis − Abschlag unter 0 liegt.
- **Fiktiver Speicher** (beide Tarifarten, kWh, 0 = ohne): wird je Tarif optimal gefahren, siehe unten.

Die vorbelegten Werte sind Beispiele und sollten durch das eigene Angebot bzw. die eigene Abrechnung
ersetzt werden.

### 4. Ergebnis

- Erlös je Tarif nach Abzug aller Gebühren, Differenz zum besten Tarif, Ø-Erlös in ct/kWh und Einspeisemenge.
  Mit Speicher zusätzlich Mehrerlös ggü. demselben Tarif ohne Speicher, verschobene kWh und Vollzyklen;
  im Tagesverlauf darunter ein Diagramm „Speicherstand“ (% der Kapazität je Tarif, Zeitachse bündig mit
  dem Erlös-Diagramm); der Tooltip nennt den Stand in % und kWh.
- „Marktwert“ der Einspeisung: der einspeisegewichtete Ø-Börsenpreis im Vergleich zum zeitlichen Ø.
- Hinweis auf negative Preise: Anzahl der Viertelstunden, wie viele davon mit Einspeisung, kWh und Anteil.
- Angabe, welche Preisauflösung verwendet wurde (siehe unten).
- Erlös-Säulendiagramm mit Drill-down: Monate → Klick → Tage des Monats → Klick → Tagesverlauf aller Tarife
  je Viertelstunde oder Stunde (mit Tageseinspeisung, Tag vor/zurück blättern). In der Tagesansicht und im
  Tagesverlauf zusätzlich der Börsenpreis als Linie (Tagesansicht: zeitlicher Ø des Tages).
- Paarvergleich zweier anklickbarer Tarife: Mehrerlös, Differenz je kWh, gewonnene Tage/Monate, Profilfaktor.
- Monatsbilanz des Paars: Differenz der Monatserlöse.

## Rechenmodell (`app/calculation/revenue.py`)

Für jede Viertelstunde q gilt:

```
EEG:               Erlös(q) = Einspeisung(q) × Satz
                   (mit Regel: 0, wenn Börsenpreis(q) < 0)
Direktvermarktung: Erlös(q) = Einspeisung(q) × (Börsenpreis(q) − Abschlag)
                              − Grundgebühr-Anteil(q) − Einmalkosten-Anteil(q)
```

- Ohne Abschalten kostet die Einspeisung in der Direktvermarktung Geld, sobald der Börsenpreis unter dem
  Abschlag liegt. Mit **Abschalten bei negativer Vergütung** sind Einspeisung und Vergütung des Tarifs
  dann 0 (Bedingung: Börsenpreis − Abschlag < 0, also auch bei Börsenpreisen knapp über 0). Abgeschaltete
  Viertelstunden tragen keinen Gebühren-Anteil; ausgewiesen werden die abgeschaltete Menge und die
  dadurch vermiedenen Kosten. Die gemessene
  Einspeisung (Tageswerte, Tagesdetail) bleibt unverändert.
- **Fiktiver Speicher** (`app/calculation/storage.py`): Er sitzt zwischen PV-Überschuss (= gemessene
  Einspeisung) und Netz, lädt nur aus dem Überschuss, nie aus dem Netz, und speist später ein. Je Tarif
  wird die Fahrweise als lineares Programm optimiert (HiGHS über `scipy.optimize.linprog`):

  ```
  Einspeisung(q) = Überschuss(q) − laden(q) − abregeln(q) + entladen(q)
  Füllstand(q)   = Füllstand(q−1) + η · laden(q) − entladen(q) / η          (Start leer, η = √0,9)
  0 ≤ Füllstand ≤ Kapazität,  laden, entladen ≤ Kapazität × 0,5 kW/kWh × 0,25 h
  Ziel: Σ Einspeisung(q) × Vergütung(q) maximal
  ```

  Feste Annahmen: Leistung = halbe Kapazität je Stunde, 90 % Wirkungsgrad je Lade-/Entladezyklus.
  Abregeln ist nur mit „Abschalten bei negativer Vergütung“ erlaubt. Entladen nur bei positiver
  Vergütung (sonst würde das LP Energie über die Verluste „vernichten“). Die Optimierung kennt alle
  Preise und den PV-Verlauf im Voraus: Das Ergebnis ist der **höchstmögliche** Mehrerlös. Bei festem
  EEG-Satz lohnt der Speicher nur mit der Regel „keine Vergütung bei negativen Preisen“. Rechenzeit:
  etwa 2–3 s je Tarif mit Speicher für ein Jahr; die Berechnung läuft im Threadpool.
  Der Speicher kommt zu einem vorhandenen hinzu (dessen Wirkung steckt schon in der Messung) und
  verschiebt nur Einspeisung, deckt keinen Eigenverbrauch (dafür bräuchte es Verbrauchsdaten, siehe unten).
- Die **Grundgebühr** wird je Kalendermonat proportional zur Einspeisung des Monats verteilt. Das ist
  `_base_fee_weights` aus dem Tarifcheck. Angebrochene Monate zahlen nur ihren zeitlichen Anteil,
  Monate ohne Einspeisung verteilen den Betrag gleichmäßig.
- **Einmalkosten** werden wie beim Tarifcheck aufs erste Jahr verteilt: 1/12 je Monat, bei kürzerem
  Zeitraum anteilig, bei längerem höchstens einmal.
- Viertelstunden **ohne Börsenpreis** gehen bei allen Tarifen mit 0 kWh ein und werden ausgewiesen.
- Viertelstunden-, Tages- und Monatswerte summieren sich exakt zum Gesamterlös.

## Datenquellen Börsenpreise (`app/pricing/`)

- **SMARD** (Bundesnetzagentur), Großhandelspreis Day-Ahead DE-LU, Filter 4169, €/MWh netto, Zeitstempel in ms UTC:
  - Index: `https://www.smard.de/app/chart_data/4169/DE/index_quarterhour.json`
  - Wochenblöcke: `https://www.smard.de/app/chart_data/4169/DE/4169_DE_quarterhour_{timestamp}.json`

  Seit dem **01.10.2025** gibt es echte Viertelstundenpreise. Für ältere Zeiträume enthält dieselbe
  Viertelstunden-Reihe den Stundenpreis viermal hintereinander. Geprüft wurde das gegen
  `index_hour.json`/`4169_DE_hour_{ts}.json`: identisch. Die Viertelstunden-Reihe deckt deshalb den
  ganzen Zeitraum ab, ältere Viertelstunden werden im Ergebnis als „Stundenpreise“ ausgewiesen.
  Blöcke werden parallel geladen und im Speicher gecacht. Vollständige Blöcke bleiben dauerhaft im
  Cache, die laufende Woche wird nach einer Stunde neu geladen.
- **aWATTar** (`api.awattar.de/v1/marketdata`) dient als Fallback für Lücken oder wenn SMARD nicht
  erreichbar ist. aWATTar liefert nur Stundenwerte, die auf 4 Viertelstunden übertragen werden.
  Geprüft am 19.09.2026, einem Tag mit 52 negativen Viertelstunden: Der aWATTar-Stundenwert ist der
  Mittelwert der 4 SMARD-Viertelstunden (Abweichung ≤ 0,005 €/MWh, Rundung). Ein Beispiel um
  13:00 Uhr: −1,01 / −1,06 / −1,34 / −1,62 ergibt im Mittel −1,258, aWATTar liefert −1,26.

## Projektstruktur

```
app/
  main.py                FastAPI-App, Router, statisches Frontend, /healthz
  schemas.py             Pydantic-Modelle der API
  session_store.py       In-Memory-Sessions (TTL 2 h): Einspeisung, Bezug, Preise, letzte Berechnung
  rate_limit.py          IP-basiertes Rate-Limit (slowapi)
  importer/
    parsing.py           CSV lesen, Spalten-/Typ-/Richtungserkennung
    aggregation.py       Messintervalle → Einspeisung (und Bezug) je Viertelstunde
  pricing/
    smard.py             SMARD-Client (Index + Wochenblöcke, Cache)
    awattar.py           aWATTar-Client (Fallback, Stundenwerte → Viertelstunden)
    prices.py            Zusammenführung inkl. Herkunft je Viertelstunde
  calculation/
    revenue.py           Erlösvergleich, Paaranalyse, Tagesdetail
    storage.py           Fiktiver Speicher: optimale Fahrweise (lineares Programm, scipy/HiGHS)
  routes/
    import_routes.py     POST /api/import/upload, /api/import/confirm
    calculate_routes.py  POST /api/calculate, /api/day-detail, /api/pair-analysis
static/                  index.html, css/style.css, js/app.js, js/charts.js, vendor/chart.umd.min.js
  sample/                Beispieldaten (Einspeisung 2025) für „Mit Beispieldaten ausprobieren“
tests/                   pytest, inkl. Shelly-EM-Beispiel-CSV (synthetisch) unter tests/fixtures/
Dockerfile, render.yaml  Deployment auf Render
```

## Deployment (Render) und Einbindung in die Webseite

Render erkennt das `Dockerfile` und `render.yaml` (Blueprint). Ein Worker-Prozess
(`WEB_CONCURRENCY=1`) ist nötig, weil Sessions, Preis-Cache und Rate-Limit im Arbeitsspeicher liegen.
Der Health-Check läuft über `/healthz`.

Die Einbindung in WordPress funktioniert wie beim Tarifcheck per `<iframe>`. Die App meldet ihre
Höhe per `postMessage` mit `source: 'einspeise-checker'`:

```html
<iframe id="einspeise-iframe" src="https://DEINE-RENDER-ADRESSE"
  style="width:100%; max-width:960px; border:0;" loading="lazy" title="Einspeise-Checker"></iframe>
<script>
  window.addEventListener('message', function (event) {
    if (!event.data || event.data.source !== 'einspeise-checker') return;
    var iframe = document.getElementById('einspeise-iframe');
    if (event.data.height) iframe.style.height = event.data.height + 'px';
    if (event.data.action === 'scrollIntoView') iframe.scrollIntoView({ behavior: 'smooth', block: 'start' });
  });
</script>
```

## Später (vorbereitet, noch nicht umgesetzt)

- **HEMS / Speicher mit Eigenverbrauch**: Der fiktive Speicher verschiebt heute nur Einspeisung. Soll er
  auch den eigenen Verbrauch decken, werden Bezug und Einspeisung gebraucht. Der Importer liest den
  Bezug schon optional ein (`Session.grid_kwh`).
