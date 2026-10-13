"""Pydantic-Modelle für die API-Requests/-Responses."""
from __future__ import annotations

import re
from datetime import date
from typing import Annotated, Literal, Optional, Union

from pydantic import AfterValidator, BaseModel, Field, StringConstraints

ValueType = Literal["power_w", "power_kw", "energy_wh", "energy_kwh", "counter_kwh"]
Direction = Literal["feed_in", "net"]
TimezoneMode = Literal["Europe/Berlin", "UTC"]

# Tarifnamen landen in mehreren innerHTML-Sinks im Frontend (Ergebnis-Kacheln, Tabellen-Header,
# Chart-Legende) -- da ein Tarifname keinen legitimen Grund hat, HTML-Sonderzeichen zu enthalten,
# wird hier serverseitig hart abgelehnt statt nur escaped. Das Frontend escaped zusätzlich.
_UNSAFE_NAME_CHARS_RE = re.compile(r'[<>"\'`]')


def _reject_html_like_chars(value: str) -> str:
    if _UNSAFE_NAME_CHARS_RE.search(value):
        raise ValueError("Der Name darf keine der Zeichen < > \" ' ` enthalten.")
    return value


# strip_whitespace, damit "EEG" und "EEG " nicht als zwei verschiedene Tarife durchgehen
# und ein Name nur aus Leerzeichen an min_length scheitert.
SafeName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=40),
    AfterValidator(_reject_html_like_chars),
]


class ImportUploadResponse(BaseModel):
    session_id: str
    columns: list[str]
    preview_rows: list[dict]
    suggested_timestamp_column: Optional[str]
    suggested_value_column: Optional[str]
    suggested_value_type: Optional[ValueType]
    suggested_direction: Direction
    suggested_grid_column: Optional[str]
    suggested_timezone: TimezoneMode
    row_count: int
    warnings: list[str]


class ImportConfirmRequest(BaseModel):
    session_id: str
    timestamp_column: str
    value_column: str
    value_type: ValueType
    direction: Direction = "feed_in"
    # Optional: Netzbezug (gleicher Werte-Typ) -- für die spätere HEMS-/Speicher-Simulation.
    grid_column: Optional[str] = None
    timezone: TimezoneMode = "Europe/Berlin"


class ImportConfirmResponse(BaseModel):
    session_id: str
    start_date: str
    end_date: str
    total_feed_in_kwh: float
    total_grid_kwh: Optional[float]
    slots_count: int
    warnings: list[str]


class EegTariffInput(BaseModel):
    type: Literal["eeg"] = "eeg"
    name: SafeName
    verguetung_ct_kwh: float = Field(ge=0, le=100)
    # Gesetzliche Regel für neuere Anlagen: keine Vergütung in Viertelstunden mit negativem Börsenpreis.
    keine_verguetung_bei_negativen_preisen: bool = False


class DirectMarketingTariffInput(BaseModel):
    type: Literal["direktvermarktung"] = "direktvermarktung"
    name: SafeName
    abschlag_ct_kwh: float = Field(ge=0, le=50)
    grundgebuehr_eur_monat: float = Field(ge=0, le=500)
    # Einmalig (z.B. Smart Meter), wird wie im Tarifcheck aufs erste Jahr verteilt.
    einmalkosten_eur: float = Field(ge=0, le=10000, default=0)


TariffInput = Annotated[Union[EegTariffInput, DirectMarketingTariffInput], Field(discriminator="type")]


class CalculateRequest(BaseModel):
    session_id: str
    tariffs: list[TariffInput] = Field(min_length=2, max_length=8)


class TariffTotal(BaseModel):
    name: str
    type: Literal["eeg", "direktvermarktung"]
    total_eur: float
    energy_revenue_eur: float
    base_fee_eur: float
    setup_eur: float
    lost_negative_eur: float
    feed_in_kwh: float
    avg_ct_kwh: float
    energy_avg_ct_kwh: float
    diff_to_best_eur: float


class DailyRevenue(BaseModel):
    date: str
    feed_in_kwh: float
    revenues: dict[str, float]


class SpotStats(BaseModel):
    time_avg_ct_kwh: Optional[float]
    feed_in_weighted_ct_kwh: Optional[float]


class NegativePriceStats(BaseModel):
    slots: int
    slots_with_feed_in: int
    feed_in_kwh: float
    share_percent: float
    spot_value_eur: float


class PriceResolution(BaseModel):
    quarter_hour_slots: int  # echte Viertelstundenpreise (SMARD, ab 01.10.2025)
    hour_slots: int  # Stundenpreise von SMARD, auf 4 Viertelstunden übertragen
    awattar_slots: int  # Fallback aWATTar (Stundenpreise, übertragen)
    notes: list[str]


class CalculateResponse(BaseModel):
    tariffs: list[TariffTotal]
    best_name: str
    worst_name: str
    best_vs_worst_eur: float
    total_kwh: float
    period_days: float
    slots_total: int
    slots_missing_price: int
    kwh_missing_price: float
    spot: SpotStats
    negative_prices: NegativePriceStats
    price_resolution: PriceResolution
    daily: list[DailyRevenue]


class DayDetailRequest(BaseModel):
    session_id: str
    date: date
    tariff_names: list[str] = Field(min_length=2, max_length=2)


class DaySlotDetail(BaseModel):
    time: str
    utc_offset_hours: float
    feed_in_kwh: float
    spot_ct_kwh: Optional[float]  # None = kein Börsenpreis
    rates_ct_kwh: dict[str, Optional[float]]
    net_rates_ct_kwh: dict[str, Optional[float]]
    revenue_eur: dict[str, float]


class DayDetailResponse(BaseModel):
    date: str
    feed_in_kwh: float
    totals_eur: dict[str, float]
    slots: list[DaySlotDetail]


class PairAnalysisRequest(BaseModel):
    session_id: str
    tariff_names: list[str] = Field(min_length=2, max_length=2)


class TariffProfile(BaseModel):
    weighted_avg_ct_kwh: Optional[float]
    time_avg_ct_kwh: Optional[float]
    profile_factor_ct_kwh: Optional[float]


class SlotOfDayStats(BaseModel):
    time: str
    feed_in_kwh: float
    avg_feed_in_kwh: float
    feed_in_share: float
    avg_rate_ct_kwh: dict[str, Optional[float]]
    revenue_eur: dict[str, float]


class PairAnalysisResponse(BaseModel):
    profile: dict[str, TariffProfile]
    slots: list[SlotOfDayStats]
