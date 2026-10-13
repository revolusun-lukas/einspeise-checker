from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from datetime import date

from app.calculation.revenue import calculate_revenue, day_detail
from app.calculation.storage import C_RATE, ETA_CHARGE, ETA_DISCHARGE, optimize_storage

ETA = ETA_CHARGE * ETA_DISCHARGE


def dv(name="DV", abschlag=0.0, abschalten=True, speicher=0.0):
    return SimpleNamespace(
        type="direktvermarktung", name=name, abschlag_ct_kwh=abschlag, grundgebuehr_eur_monat=0.0,
        einmalkosten_eur=0.0, abschalten_bei_negativer_verguetung=abschalten, speicher_kwh=speicher,
    )


def eeg(name="EEG", regel=True, speicher=0.0):
    return SimpleNamespace(
        type="eeg", name=name, verguetung_ct_kwh=8.0, keine_verguetung_bei_negativen_preisen=regel, speicher_kwh=speicher,
    )


def test_shifts_feed_in_from_negative_to_expensive_slot():
    # Kapazität 4 kWh -> 2 kW -> höchstens 0,5 kWh je Viertelstunde laden/entladen.
    surplus = np.array([1.0, 0.0, 0.0])
    rate = np.array([-5.0, 10.0, 3.0])
    res = optimize_storage(surplus, rate, capacity_kwh=4.0, allow_curtail=True)

    step = C_RATE * 4.0 * 0.25
    assert res.charge_kwh[0] == pytest.approx(step)
    assert res.curtail_kwh[0] == pytest.approx(1.0 - step)  # Rest wird abgeregelt, nicht eingespeist
    assert res.feed_in_kwh[0] == pytest.approx(0.0, abs=1e-9)
    # Ausgespeichert im teuersten Slot, abzüglich Wirkungsgrad.
    assert res.discharge_kwh[1] == pytest.approx(step * ETA)
    assert res.feed_in_kwh[2] == pytest.approx(0.0, abs=1e-9)


def test_operating_limits_and_energy_balance():
    rng = np.random.default_rng(1)
    n = 96 * 3
    surplus = np.clip(rng.normal(0.8, 0.6, n), 0, None)
    rate = rng.normal(4.0, 6.0, n)
    rate[5] = np.nan  # Viertelstunde ohne Preis
    cap = 6.0
    res = optimize_storage(surplus, rate, capacity_kwh=cap, allow_curtail=False)

    step = C_RATE * cap * 0.25
    assert res.soc_kwh.max() <= cap + 1e-6
    assert res.charge_kwh.max() <= step + 1e-6 and res.discharge_kwh.max() <= step + 1e-6
    assert np.all(res.charge_kwh <= surplus + 1e-6)
    assert res.curtail_kwh.sum() == pytest.approx(0.0, abs=1e-9)  # Abregeln nicht erlaubt
    # Nie gleichzeitig laden und entladen; nie bei Vergütung <= 0 oder ohne Preis entladen.
    assert not np.any((res.charge_kwh > 1e-6) & (res.discharge_kwh > 1e-6))
    assert np.all(res.discharge_kwh[~(np.nan_to_num(rate, nan=-1) > 0)] < 1e-6)
    assert res.charge_kwh[5] == 0 and res.discharge_kwh[5] == 0
    # Energiebilanz: Überschuss = Einspeisung + Verluste + Rest im Speicher
    losses = res.charge_kwh.sum() * (1 - ETA_CHARGE) + res.discharge_kwh.sum() * (1 / ETA_DISCHARGE - 1)
    assert surplus.sum() == pytest.approx(res.feed_in_kwh.sum() + losses + res.soc_kwh[-1], abs=1e-6)


def test_storage_in_revenue_calculation():
    idx = pd.date_range("2026-06-01 10:00", periods=4, freq="15min", tz="UTC")
    kwh = pd.Series([1.0, 1.0, 0.0, 0.0], index=idx)
    prices = pd.Series([-50.0, 20.0, 150.0, 100.0], index=idx)  # -5, 2, 15, 10 ct
    result, detail = calculate_revenue(kwh, prices, [dv("DV mit", speicher=8.0), dv("DV ohne")])
    mit, ohne = result["tariffs"]

    # Ohne Speicher: Slot 1 abgeregelt, Slot 2 zu 2 ct.
    assert ohne["energy_revenue_eur"] == pytest.approx(0.02)
    assert ohne["storage_gain_eur"] == 0.0 and ohne["storage_kwh"] == 0.0
    # Mit 8 kWh (höchstens 1 kWh je Viertelstunde): beide kWh einspeichern (die bei 2 ct auch, weil
    # später 10-15 ct winken). Ausgespeichert 1 kWh bei 15 ct, der Rest (2·η − 1 = 0,8 kWh) bei 10 ct.
    assert mit["storage_charged_kwh"] == pytest.approx(2.0, abs=0.05)
    assert mit["energy_revenue_eur"] == pytest.approx(0.15 + 0.10 * (2 * ETA - 1), abs=0.005)
    assert mit["storage_gain_eur"] == pytest.approx(mit["energy_revenue_eur"] - ohne["energy_revenue_eur"], abs=0.01)
    assert mit["storage_cycles"] == pytest.approx(mit["storage_discharged_kwh"] / 8.0, abs=0.05)
    assert mit["curtailed_kwh"] == pytest.approx(0.0, abs=0.01)  # nichts abgeregelt, alles gespeichert
    # Tageswerte summieren sich weiter zum Gesamterlös; Füllstand liegt im Detail vor.
    assert sum(d["revenues"]["DV mit"] for d in result["daily"]) == pytest.approx(mit["total_eur"], abs=0.01)
    assert detail.soc_kwh["DV ohne"] is None and detail.soc_kwh["DV mit"].max() <= 8.0 + 1e-6
    # Tagesdetail: gemessener Überschuss bleibt, Netzeinspeisung je Tarif ist verschoben.
    day = day_detail(detail, date(2026, 6, 1), ["DV mit", "DV ohne"])
    assert [s["feed_in_kwh"] for s in day["slots"]] == [1.0, 1.0, 0.0, 0.0]
    assert [s["grid_feed_in_kwh"]["DV ohne"] for s in day["slots"]] == [0.0, 1.0, 0.0, 0.0]
    assert [s["grid_feed_in_kwh"]["DV mit"] for s in day["slots"]] == pytest.approx([0.0, 0.0, 1.0, 2 * ETA - 1], abs=1e-3)


def test_eeg_storage_only_pays_off_with_negative_price_rule():
    idx = pd.date_range("2026-06-01 10:00", periods=3, freq="15min", tz="UTC")
    kwh = pd.Series([1.0, 0.0, 0.0], index=idx)
    prices = pd.Series([-10.0, 50.0, 50.0], index=idx)
    result, _ = calculate_revenue(kwh, prices, [eeg("mit Regel", speicher=8.0), eeg("ohne Regel", regel=False, speicher=8.0)])
    mit_regel, ohne_regel = result["tariffs"]

    # Mit Regel: 1 kWh wäre unvergütet -- der Speicher verschiebt sie in eine positive Viertelstunde.
    assert mit_regel["storage_gain_eur"] == pytest.approx(0.08 * ETA, abs=0.005)  # Ergebnis auf Cent gerundet
    assert mit_regel["lost_negative_eur"] == pytest.approx(0.0, abs=1e-6)
    # Ohne Regel ist der Satz immer gleich: Speichern brächte nur Verluste, also bleibt er leer.
    assert ohne_regel["storage_charged_kwh"] == 0.0
    assert ohne_regel["storage_gain_eur"] == 0.0
