"""Börsenpreise je Viertelstunde: SMARD als Hauptquelle, aWATTar als Fallback für Lücken."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

from app.pricing import awattar, smard

# Herkunft je Viertelstunde -- wird im Ergebnis als verwendete Auflösung ausgewiesen.
SOURCE_SMARD_QUARTER_HOUR = "smard_qh"  # echter Viertelstundenpreis (ab 01.10.2025)
SOURCE_SMARD_HOUR = "smard_hour"  # Stundenpreis, auf 4 Viertelstunden übertragen (vor 01.10.2025)
SOURCE_AWATTAR_HOUR = "awattar_hour"  # Fallback: aWATTar-Stundenpreis, auf 4 Viertelstunden übertragen


class PriceError(Exception):
    pass


@dataclass
class PriceData:
    eur_mwh: pd.Series  # 15-Minuten-Raster (UTC), NaN = kein Preis
    source: pd.Series  # Herkunft je Viertelstunde (siehe SOURCE_*), None = kein Preis
    notes: list[str] = field(default_factory=list)


def _assign(eur: pd.Series, source: pd.Series, prices: dict[datetime, float], label, only_missing: bool) -> None:
    if not prices:
        return
    found = pd.Series(prices, dtype=float)
    found.index = pd.DatetimeIndex(found.index).tz_convert("UTC")
    found = found.reindex(eur.index)
    mask = found.notna()
    if only_missing:
        mask &= eur.isna()
    eur[mask] = found[mask]
    source[mask] = label[mask] if isinstance(label, pd.Series) else label


async def load_prices(index: pd.DatetimeIndex) -> PriceData:
    """Lädt die Preise für alle Viertelstunden in `index` (lückenloses 15-Minuten-Raster, UTC)."""
    start = index.min().to_pydatetime()
    end = (index.max() + pd.Timedelta(minutes=15)).to_pydatetime()
    eur = pd.Series(float("nan"), index=index)
    source = pd.Series(None, index=index, dtype=object)
    notes: list[str] = []

    try:
        smard_prices = await smard.fetch_prices(start, end)
        label = pd.Series(
            [SOURCE_SMARD_QUARTER_HOUR if ts >= smard.QUARTER_HOUR_START_UTC else SOURCE_SMARD_HOUR for ts in index],
            index=index,
        )
        _assign(eur, source, smard_prices, label, only_missing=False)
    except smard.SmardError as exc:
        notes.append(f"SMARD war nicht erreichbar ({exc}); es wurden ersatzweise aWATTar-Stundenpreise verwendet.")

    missing = eur.isna()
    if missing.any():
        gap_start = index[missing].min().to_pydatetime()
        gap_end = (index[missing].max() + pd.Timedelta(minutes=15)).to_pydatetime()
        try:
            hourly = await awattar.fetch_prices(gap_start.replace(minute=0), gap_end)
            before = int(eur.notna().sum())
            _assign(eur, source, awattar.expand_to_quarter_hours(hourly), SOURCE_AWATTAR_HOUR, only_missing=True)
            filled = int(eur.notna().sum()) - before
            if filled and not notes:
                notes.append(f"{filled} Viertelstunden ohne SMARD-Preis wurden mit aWATTar-Stundenpreisen ergänzt.")
        except awattar.AwattarError as exc:
            if eur.notna().sum() == 0:
                raise PriceError(f"Börsenpreise konnten nicht geladen werden: {exc}") from exc

    if eur.notna().sum() == 0:
        raise PriceError("Für den Zeitraum der Messdaten liegen (noch) keine Börsenpreise vor.")
    return PriceData(eur_mwh=eur, source=source, notes=notes)
