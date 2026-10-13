"""Client für die aWATTar Day-Ahead-Preis-API (Deutschland) -- Fallback, falls SMARD ausfällt.

aWATTar liefert nur Stundenwerte. Geprüft (u.a. am 19.09.2026 mit negativen Mittagspreisen):
der Stundenwert ist exakt der Mittelwert der vier SMARD-Viertelstundenpreise (auf 0,01 €/MWh
gerundet). Für die Viertelstunden-Rechnung wird er auf alle vier Viertelstunden übertragen.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx

AWATTAR_URL = "https://api.awattar.de/v1/marketdata"


class AwattarError(Exception):
    pass


async def fetch_prices(start_utc: datetime, end_utc: datetime) -> dict[datetime, float]:
    """Lädt Day-Ahead-Preise (EUR/MWh) für [start_utc, end_utc) und liefert sie je Stundenbeginn (UTC)."""
    start_ms = int(start_utc.timestamp() * 1000)
    end_ms = int(end_utc.timestamp() * 1000)

    async with httpx.AsyncClient(timeout=20.0) as client:
        try:
            response = await client.get(AWATTAR_URL, params={"start": start_ms, "end": end_ms})
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise AwattarError(f"aWATTar-API nicht erreichbar: {exc}") from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise AwattarError("aWATTar-API lieferte eine ungültige Antwort (kein JSON).") from exc

    entries = payload.get("data", [])
    if not entries:
        raise AwattarError("aWATTar hat keine Preisdaten für den angefragten Zeitraum geliefert.")

    prices: dict[datetime, float] = {}
    for entry in entries:
        hour_start = datetime.fromtimestamp(entry["start_timestamp"] / 1000, tz=timezone.utc)
        prices[hour_start] = float(entry["marketprice"])
    return prices


def expand_to_quarter_hours(hourly: dict[datetime, float]) -> dict[datetime, float]:
    """Überträgt jeden Stundenpreis auf die vier Viertelstunden der Stunde."""
    return {hour + timedelta(minutes=15 * i): price for hour, price in hourly.items() for i in range(4)}
