"""Umrechnung der fünf Werte-Typen in Energie pro Messintervall und Verteilung auf ein
15-Minuten-Raster (UTC). Übernommen aus dem Dynamischen Tarif Check (dort Stundenraster).

Wichtig bei Netto-Daten (positiv = Bezug, negativ = Einspeisung): Einspeisung und Bezug werden
JE MESSINTERVALL getrennt und erst danach auf Viertelstunden summiert. Würde man zuerst auf
15 Minuten netto verrechnen, verschwände z.B. eine Minute Einspeisung hinter einer Minute Bezug
in derselben Viertelstunde -- abgerechnet wird aber jede Richtung für sich.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

import pandas as pd

from app.importer.parsing import Direction, ValueType, parse_numeric, parse_timestamp_value

SLOT = timedelta(minutes=15)
SLOT_FREQ = "15min"


class AggregationError(Exception):
    pass


@dataclass
class ImportResult:
    feed_in_kwh: pd.Series  # Einspeisung je Viertelstunde (UTC-Index, kWh, >= 0)
    grid_kwh: Optional[pd.Series]  # Netzbezug je Viertelstunde, falls in den Daten enthalten (für HEMS später)
    warnings: list[str]


def _slot_start(ts: datetime) -> datetime:
    return ts.replace(minute=ts.minute - ts.minute % 15, second=0, microsecond=0)


def _distribute_to_slots(start: datetime, end: datetime, energy_kwh: float) -> dict[datetime, float]:
    """Verteilt die Energie eines Intervalls (start, end] anteilig auf die überlappenden Viertelstunden."""
    if end <= start or energy_kwh == 0:
        return {}

    total_seconds = (end - start).total_seconds()
    result: dict[datetime, float] = {}
    cursor = start
    while cursor < end:
        slot_start = _slot_start(cursor)
        segment_end = min(slot_start + SLOT, end)
        share = (segment_end - cursor).total_seconds() / total_seconds
        result[slot_start] = result.get(slot_start, 0.0) + energy_kwh * share
        cursor = segment_end
    return result


def _add(target: dict[datetime, float], parts: dict[datetime, float]) -> None:
    for slot, kwh in parts.items():
        target[slot] = target.get(slot, 0.0) + kwh


def _parse_rows(
    df: pd.DataFrame, timestamp_col: str, value_col: str, tz: ZoneInfo, decimal: str
) -> tuple[list[datetime], list[float], int]:
    timestamps = [parse_timestamp_value(v, tz) for v in df[timestamp_col].tolist()]
    raw_values = [parse_numeric(v, decimal) for v in df[value_col].tolist()]
    rows = sorted(
        ((ts.astimezone(timezone.utc), val) for ts, val in zip(timestamps, raw_values) if ts is not None and val is not None),
        key=lambda r: r[0],
    )
    return [r[0] for r in rows], [r[1] for r in rows], len(timestamps) - len(rows)


def _median_delta(ts_utc: list[datetime]) -> timedelta:
    deltas = sorted((t2 - t1 for t1, t2 in zip(ts_utc, ts_utc[1:]) if t2 > t1), key=lambda d: d.total_seconds())
    return deltas[len(deltas) // 2] if deltas else timedelta(minutes=15)


def _gap_warning(ts_utc: list[datetime], median_delta: timedelta, tz: ZoneInfo) -> Optional[str]:
    # Datenlücken werden im Raster still mit 0 kWh aufgefüllt -- das würde die Einspeisung sonst
    # unbemerkt unterschätzen. Schwelle deutlich über dem Messintervall, damit Jitter nicht zählt.
    gap_threshold = max(median_delta * 6, timedelta(hours=2))
    gaps = [(t1, t2) for t1, t2 in zip(ts_utc, ts_utc[1:]) if (t2 - t1) > gap_threshold]
    if not gaps:
        return None
    total_gap = sum((t2 - t1 for t1, t2 in gaps), timedelta())
    examples = "; ".join(
        f"{t1.astimezone(tz).strftime('%d.%m.%Y %H:%M')} bis {t2.astimezone(tz).strftime('%d.%m.%Y %H:%M')}"
        for t1, t2 in gaps[:3]
    )
    more = f" und {len(gaps) - 3} weitere" if len(gaps) > 3 else ""
    return (
        f"{len(gaps)} Datenlücke(n) mit insgesamt {total_gap.total_seconds() / 3600:.1f} Stunden "
        f"gefunden (z. B. {examples}{more}). Für diese Zeiträume wird 0 kWh angenommen, "
        "die berechnete Einspeisung kann dadurch niedriger ausfallen als die tatsächliche."
    )


def _interval_energies(
    ts_utc: list[datetime], values: list[float], value_type: ValueType
) -> tuple[list[tuple[datetime, datetime, float]], int]:
    """Liefert je Messintervall (start, end, kWh mit Vorzeichen) und die Anzahl Zähler-Resets.

    Konvention: der Zeitstempel einer Zeile markiert das ENDE ihres Messintervalls.
    """
    intervals: list[tuple[datetime, datetime, float]] = []
    resets = 0
    if value_type == "counter_kwh":
        for i in range(1, len(values)):
            diff = values[i] - values[i - 1]
            if diff < 0:
                resets += 1
                diff = 0.0
            intervals.append((ts_utc[i - 1], ts_utc[i], diff))
        return intervals, resets

    # Median statt Mittelwert, damit einzelne große Lücken die Intervalllänge der ersten Zeile
    # (die keinen Vorgänger hat) nicht verzerren.
    median_delta = _median_delta(ts_utc)
    for i, value in enumerate(values):
        start = ts_utc[i - 1] if i > 0 else ts_utc[i] - median_delta
        end = ts_utc[i]
        hours = max((end - start).total_seconds() / 3600, 1e-9)
        if value_type == "power_w":
            kwh = value * hours / 1000.0
        elif value_type == "power_kw":
            kwh = value * hours
        elif value_type == "energy_wh":
            kwh = value / 1000.0
        elif value_type == "energy_kwh":
            kwh = value
        else:  # pragma: no cover - exhaustive ValueType
            raise AggregationError(f"Unbekannter Werte-Typ: {value_type}")
        intervals.append((start, end, kwh))
    return intervals, 0


def _positive_slots(
    intervals: list[tuple[datetime, datetime, float]], sign: int
) -> tuple[dict[datetime, float], int]:
    """Summiert je Viertelstunde nur die Intervall-Anteile mit dem gewünschten Vorzeichen
    (sign=+1: positive Werte, sign=-1: Betrag der negativen Werte)."""
    slots: dict[datetime, float] = {}
    skipped = 0
    for start, end, kwh in intervals:
        directed = kwh * sign
        if directed < 0:
            skipped += 1
            continue
        _add(slots, _distribute_to_slots(start, end, directed))
    return slots, skipped


def _to_series(slots: dict[datetime, float], index: pd.DatetimeIndex) -> pd.Series:
    series = pd.Series(slots, dtype=float)
    if not series.empty:
        series.index = pd.DatetimeIndex(series.index).tz_convert("UTC")
    return series.reindex(index, fill_value=0.0)


def build_quarter_hour_series(
    df: pd.DataFrame,
    timestamp_col: str,
    value_col: str,
    value_type: ValueType,
    direction: Direction,
    timezone_name: str,
    decimal: str,
    grid_col: Optional[str] = None,
) -> ImportResult:
    """Baut aus den Rohzeilen die Einspeisung je Viertelstunde (UTC, kWh).

    direction="feed_in": value_col enthält nur die Einspeisung (positiv), optional liefert
    grid_col (gleicher Werte-Typ) den Netzbezug. direction="net": value_col enthält Netto-Werte,
    negativ = Einspeisung, positiv = Bezug -- beide Richtungen werden je Intervall getrennt.
    """
    if direction == "net" and value_type == "counter_kwh":
        raise AggregationError(
            "Ein Zählerstand kann nicht netto sein. Bitte die Spalte mit dem Einspeisezähler "
            "(z.B. „Returned energy“) wählen und als reine Einspeisung kennzeichnen."
        )
    if direction == "net" and grid_col:
        raise AggregationError("Bei Netto-Werten steckt der Bezug schon in derselben Spalte -- bitte keine Bezugsspalte wählen.")

    tz = ZoneInfo(timezone_name)
    warnings: list[str] = []

    ts_utc, values, dropped = _parse_rows(df, timestamp_col, value_col, tz, decimal)
    if dropped:
        warnings.append(f"{dropped} Zeile(n) wurden übersprungen (Zeitstempel oder Wert nicht lesbar).")
    if len(ts_utc) < 2:
        raise AggregationError(
            "Nach dem Parsen blieben weniger als 2 gültige Datenpunkte übrig. "
            "Bitte Spaltenzuordnung, Werte-Typ und Zeitzone prüfen."
        )
    gap = _gap_warning(ts_utc, _median_delta(ts_utc), tz)
    if gap:
        warnings.append(gap)

    intervals, resets = _interval_energies(ts_utc, values, value_type)
    if resets:
        warnings.append(f"{resets} Zählerstand-Reset(s)/negative Sprünge wurden ignoriert (auf 0 gesetzt).")

    grid_slots: Optional[dict[datetime, float]] = None
    if direction == "net":
        feed_slots, _ = _positive_slots(intervals, sign=-1)
        grid_slots, _ = _positive_slots(intervals, sign=+1)
    else:
        feed_slots, negative = _positive_slots(intervals, sign=+1)
        if negative:
            warnings.append(
                f"{negative} Intervall(e) mit negativer Einspeisung wurden ignoriert. Enthält die Spalte "
                "Netto-Werte (negativ = Einspeisung)? Dann oben „Netto“ wählen."
            )
        if grid_col:
            g_ts, g_values, g_dropped = _parse_rows(df, timestamp_col, grid_col, tz, decimal)
            if len(g_ts) < 2:
                raise AggregationError("Die Bezugsspalte enthält keine lesbaren Werte.")
            if g_dropped:
                warnings.append(f"Bezugsspalte: {g_dropped} Zeile(n) übersprungen (Wert nicht lesbar).")
            g_intervals, g_resets = _interval_energies(g_ts, g_values, value_type)
            if g_resets:
                warnings.append(f"Bezugsspalte: {g_resets} Zählerstand-Reset(s) ignoriert.")
            grid_slots, _ = _positive_slots(g_intervals, sign=+1)

    all_slots = list(feed_slots) + list(grid_slots or {})
    if not all_slots:
        raise AggregationError("Es konnten keine Viertelstundenwerte berechnet werden. Bitte Daten und Zuordnung prüfen.")

    index = pd.date_range(min(all_slots), max(all_slots), freq=SLOT_FREQ, tz="UTC", name="slot_utc")
    feed_in = _to_series(feed_slots, index)
    if float(feed_in.sum()) <= 0:
        raise AggregationError(
            "In den Daten wurde keine Einspeisung gefunden. Bitte die Spalte und die Richtung "
            "(reine Einspeisung oder Netto mit negativen Werten) prüfen."
        )
    grid = _to_series(grid_slots, index) if grid_slots is not None else None
    return ImportResult(feed_in_kwh=feed_in, grid_kwh=grid, warnings=warnings)
