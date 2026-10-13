"""Erlösvergleich beliebig vieler Einspeise-"Tarife" (EEG-Vergütung, Direktvermarktung) auf Basis
der Einspeisung je Viertelstunde und der Day-Ahead-Börsenpreise.

Alle Beträge netto: Der Börsenpreis ist netto, private Anlagenbetreiber sind i.d.R.
Kleinunternehmer bzw. die Einspeisevergütung ist ohnehin umsatzsteuerlich neutral.

Rechnung je Viertelstunde q:
  EEG:               Erlös(q) = Einspeisung(q) × Vergütungssatz
                     (bei "keine Vergütung bei negativen Preisen": 0, wenn Börsenpreis(q) < 0)
  Direktvermarktung: Erlös(q) = Einspeisung(q) × (Börsenpreis(q) − Abschlag)
                                − Grundgebühr-Anteil(q) − Einmalkosten-Anteil(q)
                     Ohne Abschalten kostet Einspeisung Geld, wenn Börsenpreis − Abschlag < 0; mit
                     "Abschalten bei negativer Vergütung" sind Einspeisung und Vergütung dann 0.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

LOCAL_TZ = "Europe/Berlin"
SLOTS_PER_HOUR = 4


@dataclass
class CalculationDetail:
    """Viertelstundengenaue Zwischenergebnisse der letzten Berechnung. Wird in der Session abgelegt,
    damit Tagesdetail und Paaranalyse nachgeladen werden können, ohne neu zu rechnen."""

    local_index: pd.DatetimeIndex
    spot_ct_kwh: pd.Series  # Börsenpreis netto, NaN = kein Preis
    measured_kwh: pd.Series  # gemessene Einspeisung (Viertelstunden ohne Preis = 0)
    # Je Tarif die tatsächlich eingespeiste Menge: gemessene Einspeisung, bei "Abschalten bei
    # negativer Vergütung" ohne die abgeschalteten Viertelstunden (später auch HEMS).
    feed_in_kwh: dict[str, pd.Series]
    rates_ct_kwh: dict[str, pd.Series]  # Vergütung je kWh ohne Grundgebühr, NaN = kein Preis
    net_rates_ct_kwh: dict[str, pd.Series]  # Vergütung je kWh abzüglich Grundgebühr-/Einmalkosten-Anteil
    revenue_eur: dict[str, pd.Series]  # Erlös inkl. Abzug Grundgebühr-/Einmalkosten-Anteil


def _month_keys(local_index: pd.DatetimeIndex) -> np.ndarray:
    return np.asarray(local_index.year * 100 + local_index.month)


def _slots_in_month(key: int) -> float:
    year, month = divmod(int(key), 100)
    start = pd.Timestamp(year=year, month=month, day=1, tz=LOCAL_TZ)
    end = start + pd.offsets.MonthBegin(1)
    return (end - start).total_seconds() / 3600 * SLOTS_PER_HOUR


def _base_fee_weights(full_index: pd.DatetimeIndex, local_index: pd.DatetimeIndex, kwh_full: pd.Series):
    """Verteilt je Kalendermonat die Grundgebühr proportional zur Einspeisung des Monats
    (aus dem Dynamischen Tarif Check, dort Stunden/Verbrauch, hier Viertelstunden/Einspeisung).

    Liefert (weight, per_kwh): weight[q] * Monatsgrundgebühr = Grundgebühr-Anteil der Viertelstunde q;
    per_kwh[q] * Monatsgrundgebühr = Grundgebühr-Anteil je kWh in ct (im Monat konstant).
    Angebrochene Monate am Anfang/Ende zahlen nur ihren zeitlichen Anteil. Monate ganz ohne
    Einspeisung verteilen ihren Anteil gleichmäßig (die Gebühr fällt trotzdem an).
    """
    months = pd.Series(_month_keys(local_index), index=full_index)
    covered_slots = months.map(months.value_counts())
    month_fraction = months.map({k: n / _slots_in_month(k) for k, n in months.value_counts().items()})
    kwh_month = kwh_full.groupby(months).transform("sum")

    has_kwh = kwh_month > 0
    weight = pd.Series(
        np.where(has_kwh, kwh_full / kwh_month.where(has_kwh, 1.0), 1.0 / covered_slots),
        index=full_index,
    ) * month_fraction
    per_kwh = (month_fraction / kwh_month.where(has_kwh) * 100).fillna(0.0)
    return weight, per_kwh


def _first_year_share(base_weight: pd.Series) -> float:
    """Anteil je Monat für Einmalbeträge fürs erste Jahr (wie Bonus/Smart-Meter-Einbau im Tarifcheck):
    1/12 je Monat; kürzere Zeiträume tragen sie anteilig, längere höchstens einmal."""
    months_in_period = float(base_weight.sum())
    return min(1.0, 12 / months_in_period) / 12 if months_in_period > 0 else 0.0


def _round_or_none(value, digits: int):
    return None if value is None or pd.isna(value) else round(float(value), digits)


def calculate_revenue(feed_in_kwh: pd.Series, prices_eur_mwh: pd.Series, tariffs: list) -> tuple[dict, CalculationDetail]:
    """feed_in_kwh: Einspeisung je Viertelstunde (UTC). prices_eur_mwh: Börsenpreis je Viertelstunde
    (UTC, NaN = kein Preis). Viertelstunden ohne Preis gehen bei allen Tarifen mit 0 kWh ein."""
    full_index = pd.date_range(feed_in_kwh.index.min(), feed_in_kwh.index.max(), freq="15min")
    kwh_raw = feed_in_kwh.reindex(full_index, fill_value=0.0).clip(lower=0)
    price = prices_eur_mwh.reindex(full_index)
    has_price = price.notna()
    if not has_price.any():
        raise ValueError("Keine Überschneidung zwischen Einspeisedaten und Börsenpreisen gefunden.")

    kwh = kwh_raw.where(has_price, 0.0)
    total_kwh = float(kwh.sum())
    slots_missing = int((~has_price).sum())
    kwh_missing = float(kwh_raw[~has_price].sum())

    local_index = full_index.tz_convert(LOCAL_TZ)
    spot_ct = price / 10
    negative = has_price & (price < 0)
    # Die Gewichte summieren sich je Monat zum zeitlichen Monatsanteil -- unabhängig von der
    # Einspeisung, daher für alle Tarife derselbe Einmalkosten-Anteil.
    first_year_share = _first_year_share(_base_fee_weights(full_index, local_index, kwh)[0])

    names: list[str] = []
    types: dict[str, str] = {}
    feed_in: dict[str, pd.Series] = {}
    rates: dict[str, pd.Series] = {}
    net_rates: dict[str, pd.Series] = {}
    revenue: dict[str, pd.Series] = {}
    totals: dict[str, dict] = {}

    for tariff in tariffs:
        name = tariff.name
        if name in types:
            raise ValueError(f"Tarifname '{name}' wird mehrfach verwendet. Bitte eindeutige Namen vergeben.")

        tariff_kwh = kwh
        curtailed = pd.Series(False, index=full_index)
        lost_negative = 0.0
        curtailed_kwh = 0.0
        avoided_negative = 0.0
        if tariff.type == "eeg":
            rate = pd.Series(float(tariff.verguetung_ct_kwh), index=full_index)
            if tariff.keine_verguetung_bei_negativen_preisen:
                lost_negative = float((tariff_kwh[negative] * rate[negative]).sum() / 100)
                rate = rate.mask(negative, 0.0)
            fee_per_month = 0.0
            setup_per_month = 0.0
        else:
            rate = spot_ct - float(tariff.abschlag_ct_kwh)
            if tariff.abschalten_bei_negativer_verguetung:
                # Abgeschaltet wird, sobald Börsenpreis − Abschlag < 0 -- also auch bei Börsenpreisen
                # zwischen 0 und dem Abschlag, nicht erst bei negativem Börsenpreis.
                curtailed = has_price & (rate < 0)
                curtailed_kwh = float(kwh[curtailed].sum())
                # Was die Einspeisung in diesen Viertelstunden gekostet hätte.
                avoided_negative = -float((kwh[curtailed] * rate[curtailed]).sum() / 100)
                tariff_kwh = kwh.mask(curtailed, 0.0)
                # Ausgezahlt wird dort nichts -- Vergütung 0 statt des negativen Werts.
                rate = rate.mask(curtailed, 0.0)
            fee_per_month = float(tariff.grundgebuehr_eur_monat)
            setup_per_month = float(tariff.einmalkosten_eur) * first_year_share
        rate = rate.where(has_price)
        # Grundgebühr je Tarif auf dessen eigene Einspeisung verteilen (abgeschaltete Viertelstunden
        # tragen keinen Anteil).
        base_weight, base_per_kwh = _base_fee_weights(full_index, local_index, tariff_kwh)

        energy = (tariff_kwh * rate / 100).fillna(0.0)
        base = base_weight * fee_per_month
        setup = base_weight * setup_per_month

        names.append(name)
        types[name] = tariff.type
        feed_in[name] = tariff_kwh
        rates[name] = rate
        # Abgeschaltete Viertelstunden tragen keinen Gebühren-Anteil.
        net_rates[name] = (rate - base_per_kwh * (fee_per_month + setup_per_month)).mask(curtailed, 0.0)
        revenue[name] = energy - base - setup
        energy_total = float(energy.sum())
        tariff_kwh_total = float(tariff_kwh.sum())
        total = energy_total - float(base.sum()) - float(setup.sum())
        totals[name] = {
            "total": total,
            "energy": energy_total,
            "base": float(base.sum()),
            "setup": float(setup.sum()),
            "lost_negative": lost_negative,
            "curtailed_kwh": curtailed_kwh,
            "avoided_negative": avoided_negative,
            "kwh": tariff_kwh_total,
            "avg": total / tariff_kwh_total * 100 if tariff_kwh_total else 0.0,
            "energy_avg": energy_total / tariff_kwh_total * 100 if tariff_kwh_total else 0.0,
        }

    best_name = max(names, key=lambda n: totals[n]["total"])
    worst_name = min(names, key=lambda n: totals[n]["total"])
    best_total = totals[best_name]["total"]

    # Tageswerte inkl. Grundgebühr-Anteil -- summieren sich exakt zum Gesamterlös.
    daily = pd.DataFrame(revenue).groupby(local_index.date).sum().sort_index()
    daily_kwh = kwh.groupby(local_index.date).sum()
    daily_out = [
        {
            "date": day.isoformat(),
            "feed_in_kwh": round(float(daily_kwh.get(day, 0.0)), 3),
            "revenues": {n: round(float(row[n]), 4) for n in names},
        }
        for day, row in daily.iterrows()
    ]

    priced_spot = spot_ct[has_price]
    weighted_spot = float((priced_spot * kwh[has_price]).sum() / total_kwh) if total_kwh else None

    result = {
        "tariffs": [
            {
                "name": n,
                "type": types[n],
                "total_eur": round(totals[n]["total"], 2),
                "energy_revenue_eur": round(totals[n]["energy"], 2),
                "base_fee_eur": round(totals[n]["base"], 2),
                "setup_eur": round(totals[n]["setup"], 2),
                "lost_negative_eur": round(totals[n]["lost_negative"], 2),
                "curtailed_kwh": round(totals[n]["curtailed_kwh"], 2),
                "avoided_negative_eur": round(totals[n]["avoided_negative"], 2),
                "feed_in_kwh": round(totals[n]["kwh"], 1),
                # Ø-Erlös je eingespeister kWh nach Abzug aller Gebühren bzw. nur aus der Energie.
                "avg_ct_kwh": round(totals[n]["avg"], 2),
                "energy_avg_ct_kwh": round(totals[n]["energy_avg"], 2),
                "diff_to_best_eur": round(totals[n]["total"] - best_total, 2),
            }
            for n in names
        ],
        "best_name": best_name,
        "worst_name": worst_name,
        "best_vs_worst_eur": round(best_total - totals[worst_name]["total"], 2),
        "total_kwh": round(total_kwh, 1),
        "period_days": round(len(full_index) / (24 * SLOTS_PER_HOUR), 2),
        "slots_total": int(len(full_index)),
        "slots_missing_price": slots_missing,
        "kwh_missing_price": round(kwh_missing, 1),
        "spot": {
            "time_avg_ct_kwh": _round_or_none(priced_spot.mean(), 2),
            # Einspeisegewichteter Ø-Börsenpreis ("Marktwert" der eigenen Einspeisung).
            "feed_in_weighted_ct_kwh": _round_or_none(weighted_spot, 2),
        },
        "negative_prices": {
            "slots": int(negative.sum()),
            "slots_with_feed_in": int((negative & (kwh > 0)).sum()),
            "feed_in_kwh": round(float(kwh[negative].sum()), 2),
            "share_percent": round(float(kwh[negative].sum()) / total_kwh * 100, 2) if total_kwh else 0.0,
            # Was die Einspeisung bei negativen Preisen zum Börsenpreis gekostet hat (ohne Abschlag).
            "spot_value_eur": round(float((kwh[negative] * spot_ct[negative]).sum() / 100), 2),
        },
        "daily": daily_out,
    }
    detail = CalculationDetail(
        local_index=local_index,
        spot_ct_kwh=spot_ct,
        measured_kwh=kwh,
        feed_in_kwh=feed_in,
        rates_ct_kwh=rates,
        net_rates_ct_kwh=net_rates,
        revenue_eur=revenue,
    )
    return result, detail


def _check_names(detail: CalculationDetail, names: list[str]) -> None:
    for n in names:
        if n not in detail.revenue_eur:
            raise ValueError(f"Tarif '{n}' ist in der letzten Berechnung nicht enthalten.")


def pair_analysis(detail: CalculationDetail, names: list[str]) -> dict:
    """Profilfaktor je Tarif eines Paars über den gesamten Zeitraum.

    Profilfaktor = einspeisegewichtete Ø-Vergütung − zeitliche Ø-Vergütung. Negativ heißt: Die
    Einspeisung fällt überdurchschnittlich in Viertelstunden mit niedriger Vergütung (typisch für PV
    in der Direktvermarktung, weil mittags viel Solarstrom den Börsenpreis drückt).
    """
    _check_names(detail, names)
    profile: dict[str, dict] = {}
    for n in names:
        rate = detail.rates_ct_kwh[n]
        has_rate = rate.notna()
        kwh_priced = detail.feed_in_kwh[n][has_rate]
        weighted = float((rate[has_rate] * kwh_priced).sum() / kwh_priced.sum()) if kwh_priced.sum() > 0 else None
        time_avg = float(rate[has_rate].mean()) if has_rate.any() else None
        profile[n] = {
            "weighted_avg_ct_kwh": _round_or_none(weighted, 2),
            "time_avg_ct_kwh": _round_or_none(time_avg, 2),
            "profile_factor_ct_kwh": _round_or_none(
                weighted - time_avg if weighted is not None and time_avg is not None else None, 2
            ),
        }
    return {"profile": profile}


def day_detail(detail: CalculationDetail, day: date, names: list[str]) -> dict:
    """Viertelstundenwerte eines Tages für die gewählten Tarife (Erlös inkl. Gebühren-Anteil)."""
    _check_names(detail, names)
    mask = detail.local_index.date == day
    if not mask.any():
        raise ValueError("Für diesen Tag liegen keine Daten vor.")

    slots = detail.local_index[mask]
    spot = detail.spot_ct_kwh.values[mask]
    measured = detail.measured_kwh.values[mask]
    kwh = {n: detail.feed_in_kwh[n].values[mask] for n in names}
    rates = {n: detail.rates_ct_kwh[n].values[mask] for n in names}
    net_rates = {n: detail.net_rates_ct_kwh[n].values[mask] for n in names}
    revenue = {n: detail.revenue_eur[n].values[mask] for n in names}

    return {
        "date": day.isoformat(),
        "feed_in_kwh": round(float(measured.sum()), 3),
        # Je Tarif durch Abschalten nicht eingespeiste Menge an diesem Tag.
        "curtailed_kwh": {n: round(float((measured - kwh[n]).sum()), 3) for n in names},
        # 4 Nachkommastellen wie bei den Tageswerten, damit die im Frontend gebildete
        # Differenz nicht durch doppeltes Runden um einen Cent abweicht.
        "totals_eur": {n: round(float(revenue[n].sum()), 4) for n in names},
        "slots": [
            {
                # UTC-Offset dazu, damit die doppelte Stunde bei der Zeitumstellung eindeutig ist.
                "time": ts.strftime("%H:%M"),
                "utc_offset_hours": ts.utcoffset().total_seconds() / 3600,
                "feed_in_kwh": round(float(measured[i]), 4),
                "curtailed": {n: bool(kwh[n][i] < measured[i]) for n in names},
                "spot_ct_kwh": _round_or_none(spot[i], 3),
                "rates_ct_kwh": {n: _round_or_none(rates[n][i], 3) for n in names},
                "net_rates_ct_kwh": {n: _round_or_none(net_rates[n][i], 3) for n in names},
                "revenue_eur": {n: round(float(revenue[n][i]), 5) for n in names},
            }
            for i, ts in enumerate(slots)
        ],
    }
