from datetime import date
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from app.calculation.revenue import calculate_revenue, day_detail, pair_analysis


def eeg(name="EEG", satz=8.0, negativ=False):
    return SimpleNamespace(type="eeg", name=name, verguetung_ct_kwh=satz, keine_verguetung_bei_negativen_preisen=negativ)


def dv(name="DV", abschlag=0.5, grund=0.0, einmal=0.0, abschalten=False):
    return SimpleNamespace(
        type="direktvermarktung", name=name, abschlag_ct_kwh=abschlag, grundgebuehr_eur_monat=grund,
        einmalkosten_eur=einmal, abschalten_bei_negativer_verguetung=abschalten,
    )


def june_local():
    """Kompletter Juni 2026 in Ortszeit als 15-Minuten-Raster (UTC)."""
    start = pd.Timestamp("2026-06-01", tz="Europe/Berlin").tz_convert("UTC")
    end = pd.Timestamp("2026-07-01", tz="Europe/Berlin").tz_convert("UTC")
    return pd.date_range(start, end, freq="15min", inclusive="left")


def test_eeg_revenue_is_feed_in_times_rate():
    idx = pd.date_range("2026-06-01 10:00", periods=4, freq="15min", tz="UTC")
    kwh = pd.Series([1.0, 2.0, 0.0, 1.0], index=idx)
    prices = pd.Series([50.0, -10.0, 30.0, 80.0], index=idx)
    result, _ = calculate_revenue(kwh, prices, [eeg(satz=8.0), eeg("EEG neu", satz=8.0, negativ=True)])

    by_name = {t["name"]: t for t in result["tariffs"]}
    assert by_name["EEG"]["total_eur"] == pytest.approx(4 * 0.08)
    # Viertelstunde mit -10 €/MWh und 2 kWh entfällt.
    assert by_name["EEG neu"]["total_eur"] == pytest.approx(2 * 0.08)
    assert by_name["EEG neu"]["lost_negative_eur"] == pytest.approx(2 * 0.08)
    assert result["negative_prices"] == {
        "slots": 1, "slots_with_feed_in": 1, "feed_in_kwh": 2.0, "share_percent": 50.0, "spot_value_eur": -0.02,
    }
    assert result["best_name"] == "EEG"
    assert by_name["EEG neu"]["diff_to_best_eur"] == pytest.approx(-0.16)


def test_direct_marketing_negative_prices_cost_money():
    idx = pd.date_range("2026-06-01 10:00", periods=2, freq="15min", tz="UTC")
    kwh = pd.Series([1.0, 1.0], index=idx)
    prices = pd.Series([100.0, -50.0], index=idx)  # 10 ct und -5 ct
    result, detail = calculate_revenue(kwh, prices, [dv(abschlag=1.0), eeg()])

    dv_total = result["tariffs"][0]
    # (10 - 1) ct + (-5 - 1) ct = 3 ct -- kein Abschalten in Schritt 1.
    assert dv_total["energy_revenue_eur"] == pytest.approx(0.03)
    assert detail.revenue_eur["DV"].iloc[1] == pytest.approx(-0.06)


def test_direct_marketing_curtailment_at_negative_rate():
    idx = pd.date_range("2026-06-01 10:00", periods=4, freq="15min", tz="UTC")
    kwh = pd.Series([1.0, 2.0, 1.0, 1.0], index=idx)
    # 10 ct, -5 ct, 0 ct und 2 ct: Bei 1 ct Abschlag ist die Vergütung auch bei 0 ct negativ.
    prices = pd.Series([100.0, -50.0, 0.0, 20.0], index=idx)
    result, detail = calculate_revenue(
        kwh, prices, [dv("DV aus", abschlag=1.0, grund=3.0, abschalten=True), dv(abschlag=1.0, grund=3.0)]
    )

    off, on = result["tariffs"]
    assert off["feed_in_kwh"] == 2.0
    assert off["curtailed_kwh"] == 3.0
    # (10 - 1) + (2 - 1) ct; ohne Abschalten zusätzlich 2 × (-5 - 1) + (0 - 1) ct.
    assert off["energy_revenue_eur"] == pytest.approx(0.10)
    assert on["energy_revenue_eur"] == pytest.approx(0.10 - 0.13)
    assert off["avoided_negative_eur"] == pytest.approx(0.13)
    assert on["curtailed_kwh"] == 0.0
    # Abgeschaltet: weder negative Vergütung noch Erlös, auch nicht inkl. Gebühren-Anteil.
    curtailed = detail.feed_in_kwh["DV aus"] < detail.measured_kwh
    assert (detail.rates_ct_kwh["DV aus"][curtailed] == 0).all()
    assert (detail.net_rates_ct_kwh["DV aus"][curtailed] == 0).all()
    assert (detail.revenue_eur["DV aus"][curtailed] == 0).all()
    # Gemessene Einspeisung bleibt für Tageswerte und Tagesdetail unverändert.
    assert result["total_kwh"] == 5.0
    day = day_detail(detail, date(2026, 6, 1), ["DV aus", "DV"])
    assert day["feed_in_kwh"] == pytest.approx(5.0)
    assert day["curtailed_kwh"] == {"DV aus": 3.0, "DV": 0.0}
    assert [s["curtailed"]["DV aus"] for s in day["slots"] if s["feed_in_kwh"] > 0] == [False, True, True, False]
    assert all(s["revenue_eur"]["DV aus"] >= 0 for s in day["slots"] if s["curtailed"]["DV aus"])


def test_curtailed_slots_carry_no_base_fee():
    idx = june_local()
    local = idx.tz_convert("Europe/Berlin")
    kwh = pd.Series(np.where(local.hour == 12, 1.0, 0.0), index=idx)
    prices = pd.Series(np.where(local.minute == 0, -10.0, 50.0), index=idx)
    result, detail = calculate_revenue(kwh, prices, [dv(abschalten=True, grund=6.0), eeg()])

    assert result["tariffs"][0]["base_fee_eur"] == pytest.approx(6.0)
    curtailed = detail.feed_in_kwh["DV"] == 0
    assert detail.revenue_eur["DV"][curtailed].abs().sum() == pytest.approx(0.0)


def test_direct_marketing_monthly_fee_and_one_off_costs():
    idx = june_local()
    kwh = pd.Series(0.0, index=idx)
    kwh[idx.tz_convert("Europe/Berlin").hour == 12] = 0.5
    prices = pd.Series(80.0, index=idx)
    result, detail = calculate_revenue(kwh, prices, [dv(abschlag=0.0, grund=6.0, einmal=120.0), eeg()])

    t = result["tariffs"][0]
    total_kwh = float(kwh.sum())
    assert t["energy_revenue_eur"] == pytest.approx(total_kwh * 0.08)
    assert t["base_fee_eur"] == pytest.approx(6.0)  # genau ein Kalendermonat
    assert t["setup_eur"] == pytest.approx(10.0)  # 120 € aufs erste Jahr: 1/12
    assert t["total_eur"] == pytest.approx(total_kwh * 0.08 - 16.0, abs=0.01)
    # Gebühren nur dort, wo eingespeist wird; Viertelstundenwerte summieren sich zum Gesamterlös.
    assert detail.revenue_eur["DV"][kwh == 0].abs().sum() == pytest.approx(0.0)
    assert sum(d["revenues"]["DV"] for d in result["daily"]) == pytest.approx(t["total_eur"], abs=0.01)


def test_partial_month_pays_time_share_of_fee():
    idx = june_local()[: 15 * 96]  # 1.–15. Juni = halber Monat
    kwh = pd.Series(0.1, index=idx)
    prices = pd.Series(50.0, index=idx)
    result, _ = calculate_revenue(kwh, prices, [dv(grund=10.0), eeg()])
    assert result["tariffs"][0]["base_fee_eur"] == pytest.approx(5.0)


def test_slots_without_price_are_excluded_for_all_tariffs():
    idx = pd.date_range("2026-06-01 10:00", periods=4, freq="15min", tz="UTC")
    kwh = pd.Series(1.0, index=idx)
    prices = pd.Series([50.0, np.nan, 50.0, np.nan], index=idx)
    result, _ = calculate_revenue(kwh, prices, [eeg(), dv(abschlag=0.0)])

    assert result["total_kwh"] == 2.0
    assert result["slots_missing_price"] == 2
    assert result["kwh_missing_price"] == 2.0
    assert result["tariffs"][0]["total_eur"] == pytest.approx(0.16)
    assert result["tariffs"][1]["total_eur"] == pytest.approx(0.10)


def test_daily_spot_average_ignores_slots_without_price():
    idx = pd.date_range("2026-06-01 10:00", periods=4, freq="15min", tz="UTC")
    prices = pd.Series([40.0, np.nan, 80.0, -20.0], index=idx)
    result, _ = calculate_revenue(pd.Series(1.0, index=idx), prices, [eeg(), dv()])
    # (4 + 8 − 2) ct ÷ 3 Viertelstunden mit Preis
    assert result["daily"][0]["spot_avg_ct_kwh"] == pytest.approx(10 / 3, abs=0.001)


def test_duplicate_names_rejected():
    idx = pd.date_range("2026-06-01 10:00", periods=2, freq="15min", tz="UTC")
    with pytest.raises(ValueError):
        calculate_revenue(pd.Series(1.0, index=idx), pd.Series(1.0, index=idx), [eeg("A"), eeg("A")])


def test_pair_analysis_and_day_detail():
    idx = june_local()[:96 * 2]
    local = idx.tz_convert("Europe/Berlin")
    kwh = pd.Series(np.where((local.hour >= 10) & (local.hour < 14), 0.4, 0.0), index=idx)
    # Mittags billig, sonst teuer -- PV-Einspeisung liegt in den billigen Viertelstunden.
    prices = pd.Series(np.where((local.hour >= 10) & (local.hour < 14), 20.0, 120.0), index=idx)
    _, detail = calculate_revenue(kwh, prices, [eeg(), dv(abschlag=0.0)])

    analysis = pair_analysis(detail, ["EEG", "DV"])
    assert analysis["profile"]["EEG"]["profile_factor_ct_kwh"] == 0.0
    assert analysis["profile"]["DV"]["profile_factor_ct_kwh"] < 0

    day = day_detail(detail, date(2026, 6, 1), ["DV", "EEG"])
    assert len(day["slots"]) == 96
    assert day["feed_in_kwh"] == pytest.approx(16 * 0.4)
    assert day["slots"][48]["spot_ct_kwh"] == 2.0
    assert day["totals_eur"]["EEG"] == pytest.approx(16 * 0.4 * 0.08)
