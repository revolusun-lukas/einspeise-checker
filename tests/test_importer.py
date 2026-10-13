from pathlib import Path

import pandas as pd
import pytest

from app.importer import aggregation, parsing

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE_DIR = Path(__file__).parent.parent / "static" / "sample"


def _ts(text: str) -> pd.Timestamp:
    return pd.Timestamp(text, tz="UTC")


def _df(rows: list[tuple[str, str]], header=("time", "value")) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=list(header), dtype=str)


def test_shelly_em_suggestions():
    df, meta = parsing.read_csv_raw((FIXTURES / "shelly_em_data.csv").read_bytes())
    ts_col, _ = parsing.suggest_timestamp_column(df)
    value_col, direction = parsing.suggest_feed_in_column(df, exclude=ts_col, decimal=meta["decimal"])

    assert ts_col == "Date/time UTC"
    assert value_col == "Returned energy Wh"
    assert direction == "feed_in"
    assert parsing.suggest_value_type(value_col, df[value_col], meta["decimal"]) == "energy_wh"
    assert parsing.suggest_grid_column(df, exclude=[ts_col, value_col]) == "Active energy Wh"
    assert parsing.suggest_timezone(ts_col) == "UTC"


def test_sample_data_suggestions_and_total():
    """Die mitgelieferten Beispieldaten müssen ohne Korrektur der Vorschläge durchlaufen."""
    df, meta = parsing.read_csv_raw((SAMPLE_DIR / "beispiel-einspeisung-2025.csv").read_bytes())
    ts_col, _ = parsing.suggest_timestamp_column(df)
    value_col, direction = parsing.suggest_feed_in_column(df, exclude=ts_col, decimal=meta["decimal"])
    value_type = parsing.suggest_value_type(value_col, df[value_col], meta["decimal"])

    assert (ts_col, value_col, direction, value_type) == ("Zeitstempel", "Netzeinspeisung [W]", "feed_in", "power_w")
    # "Direktverbrauch" ist Eigenverbrauch, kein Netzbezug.
    assert parsing.suggest_grid_column(df, exclude=[ts_col, value_col]) is None
    assert parsing.suggest_timezone(ts_col) == "Europe/Berlin"

    result = aggregation.build_quarter_hour_series(
        df, ts_col, value_col, value_type, direction, "Europe/Berlin", meta["decimal"]
    )
    assert result.feed_in_kwh.sum() == pytest.approx(5305.4, abs=0.1)


def test_shelly_em_feed_in_and_grid_per_quarter_hour():
    df, meta = parsing.read_csv_raw((FIXTURES / "shelly_em_data.csv").read_bytes())
    result = aggregation.build_quarter_hour_series(
        df, "Date/time UTC", "Returned energy Wh", "energy_wh", "feed_in", "UTC", meta["decimal"],
        grid_col="Active energy Wh",
    )
    feed_in = result.feed_in_kwh

    # 50 Minuten × 10 Wh Einspeisung, 10 Minuten × 5 Wh Bezug.
    assert feed_in.sum() == pytest.approx(0.5)
    assert result.grid_kwh.sum() == pytest.approx(0.05)
    assert feed_in.index.freq == "15min"
    assert str(feed_in.index.tz) == "UTC"
    assert feed_in[_ts("2026-06-01 10:00")] == pytest.approx(0.15)
    assert feed_in[_ts("2026-06-01 10:45")] == pytest.approx(0.05)
    assert result.grid_kwh[_ts("2026-06-01 09:45")] == pytest.approx(0.05)


def test_net_power_is_split_per_interval_before_summing():
    # Innerhalb EINER Viertelstunde: 5 min Bezug 600 W, 5 min Einspeisung 1200 W, 5 min Bezug 600 W.
    # Netto wäre das 0 kWh -- tatsächlich wurden 0,1 kWh eingespeist und 0,1 kWh bezogen.
    df = _df([
        ("2026-06-01 12:00:00", "0"),
        ("2026-06-01 12:05:00", "600"),
        ("2026-06-01 12:10:00", "-1200"),
        ("2026-06-01 12:15:00", "600"),
    ])
    result = aggregation.build_quarter_hour_series(df, "time", "value", "power_w", "net", "UTC", ".")

    assert result.feed_in_kwh[_ts("2026-06-01 12:00")] == pytest.approx(0.1)
    assert result.grid_kwh[_ts("2026-06-01 12:00")] == pytest.approx(0.1)


def test_interval_across_quarter_boundary_is_distributed():
    df = _df([("2026-06-01 12:10:00", "0"), ("2026-06-01 12:20:00", "-6000")])
    result = aggregation.build_quarter_hour_series(df, "time", "value", "power_w", "net", "UTC", ".")
    # 10 Minuten × 6 kW = 1 kWh, davon 5 Minuten vor und 5 Minuten nach 12:15.
    assert result.feed_in_kwh[_ts("2026-06-01 12:00")] == pytest.approx(0.5)
    assert result.feed_in_kwh[_ts("2026-06-01 12:15")] == pytest.approx(0.5)


def test_local_time_is_converted_to_utc():
    df = _df([("01.06.2026 12:00", "0"), ("01.06.2026 12:15", "250")])
    result = aggregation.build_quarter_hour_series(df, "time", "value", "energy_wh", "feed_in", "Europe/Berlin", ".")
    # Sommerzeit: 12:00 Ortszeit = 10:00 UTC.
    assert result.feed_in_kwh[_ts("2026-06-01 10:00")] == pytest.approx(0.25)


def test_feed_in_counter_with_reset():
    df = _df([
        ("2026-06-01 12:00:00", "100.0"),
        ("2026-06-01 12:15:00", "101.5"),
        ("2026-06-01 12:30:00", "0.5"),  # Zählerwechsel
        ("2026-06-01 12:45:00", "1.0"),
    ])
    result = aggregation.build_quarter_hour_series(df, "time", "value", "counter_kwh", "feed_in", "UTC", ".")
    assert result.feed_in_kwh.sum() == pytest.approx(2.0)
    assert result.grid_kwh is None
    assert any("Reset" in w for w in result.warnings)


def test_net_counter_is_rejected():
    df = _df([("2026-06-01 12:00:00", "1"), ("2026-06-01 12:15:00", "2")])
    with pytest.raises(aggregation.AggregationError):
        aggregation.build_quarter_hour_series(df, "time", "value", "counter_kwh", "net", "UTC", ".")


def test_no_feed_in_is_rejected():
    df = _df([("2026-06-01 12:00:00", "300"), ("2026-06-01 12:15:00", "300")])
    with pytest.raises(aggregation.AggregationError, match="keine Einspeisung"):
        aggregation.build_quarter_hour_series(df, "time", "value", "power_w", "net", "UTC", ".")


def test_net_column_suggested_when_values_are_negative():
    df = _df([("2026-06-01 12:00:00", "300"), ("2026-06-01 12:15:00", "-800")], header=("Zeit", "Leistung W"))
    col, direction = parsing.suggest_feed_in_column(df, exclude="Zeit", decimal=".")
    assert (col, direction) == ("Leistung W", "net")


def test_net_detected_even_if_file_starts_at_night():
    # Erst 1000 Zeilen nur Bezug (Nacht), dann Einspeisung -- die Erkennung darf nicht nur den Anfang ansehen.
    rows = [(f"2026-06-01 00:{i % 60:02d}:00", "300") for i in range(1000)] + [("2026-06-01 12:00:00", "-2000")]
    df = _df(rows, header=("Zeit", "Leistung W"))
    assert parsing.suggest_feed_in_column(df, exclude="Zeit", decimal=".") == ("Leistung W", "net")
