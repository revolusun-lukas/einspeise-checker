import asyncio
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from app.pricing import awattar, prices, smard

UTC = timezone.utc


def _index(start: datetime, quarters: int) -> pd.DatetimeIndex:
    return pd.date_range(start, periods=quarters, freq="15min", tz="UTC")


def _patch(monkeypatch, smard_result=None, awattar_result=None):
    async def fake_smard(start, end):
        if isinstance(smard_result, Exception):
            raise smard_result
        return smard_result(start, end) if callable(smard_result) else (smard_result or {})

    async def fake_awattar(start, end):
        if isinstance(awattar_result, Exception):
            raise awattar_result
        return awattar_result or {}

    monkeypatch.setattr(smard, "fetch_prices", fake_smard)
    monkeypatch.setattr(awattar, "fetch_prices", fake_awattar)


def test_resolution_switches_at_quarter_hour_start(monkeypatch):
    idx = _index(smard.QUARTER_HOUR_START_UTC - timedelta(hours=1), 8)
    _patch(monkeypatch, smard_result={ts.to_pydatetime(): 10.0 for ts in idx})

    data = asyncio.run(prices.load_prices(idx))

    assert list(data.source[:4]) == [prices.SOURCE_SMARD_HOUR] * 4
    assert list(data.source[4:]) == [prices.SOURCE_SMARD_QUARTER_HOUR] * 4
    assert data.notes == []


def test_missing_quarters_filled_from_awattar(monkeypatch):
    start = datetime(2026, 9, 19, 10, tzinfo=UTC)
    idx = _index(start, 8)
    smard_prices = {ts.to_pydatetime(): -1.0 for ts in idx[:4]}  # zweite Stunde fehlt bei SMARD
    _patch(monkeypatch, smard_result=smard_prices, awattar_result={start + timedelta(hours=1): -1.26})

    data = asyncio.run(prices.load_prices(idx))

    assert list(data.eur_mwh[4:]) == [-1.26] * 4
    assert list(data.source[4:]) == [prices.SOURCE_AWATTAR_HOUR] * 4
    assert list(data.source[:4]) == [prices.SOURCE_SMARD_QUARTER_HOUR] * 4
    assert data.eur_mwh[:4].tolist() == [-1.0] * 4


def test_smard_down_falls_back_to_awattar(monkeypatch):
    start = datetime(2026, 9, 19, 10, tzinfo=UTC)
    idx = _index(start, 4)
    _patch(monkeypatch, smard_result=smard.SmardError("down"), awattar_result={start: 42.0})

    data = asyncio.run(prices.load_prices(idx))

    assert data.eur_mwh.tolist() == [42.0] * 4
    assert "SMARD war nicht erreichbar" in data.notes[0]


def test_no_prices_at_all_raises(monkeypatch):
    idx = _index(datetime(2026, 9, 19, 10, tzinfo=UTC), 4)
    _patch(monkeypatch, smard_result=smard.SmardError("down"), awattar_result=awattar.AwattarError("down"))
    with pytest.raises(prices.PriceError):
        asyncio.run(prices.load_prices(idx))


def test_future_quarters_without_any_price_stay_empty(monkeypatch):
    idx = _index(datetime(2026, 9, 19, 10, tzinfo=UTC), 8)
    _patch(
        monkeypatch,
        smard_result={ts.to_pydatetime(): 5.0 for ts in idx[:4]},
        awattar_result=awattar.AwattarError("keine Daten"),
    )
    data = asyncio.run(prices.load_prices(idx))
    assert data.eur_mwh.isna().sum() == 4


def test_awattar_expansion():
    hour = datetime(2026, 9, 19, 11, tzinfo=UTC)
    expanded = awattar.expand_to_quarter_hours({hour: -0.31})
    assert sorted(expanded) == [hour + timedelta(minutes=15 * i) for i in range(4)]
    assert set(expanded.values()) == {-0.31}


def test_smard_block_selection():
    week = 7 * 24 * 3600 * 1000
    index = [0, week, 2 * week]
    assert smard._blocks_for_range(index, week + 1, week + 2) == [week]
    assert smard._blocks_for_range(index, week - 1, week + 1) == [0, week]
    assert smard._blocks_for_range(index, 3 * week - 1, 3 * week) == [2 * week]
