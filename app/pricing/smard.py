"""Client für die Day-Ahead-Preise von SMARD (Bundesnetzagentur), Gebotszone DE-LU.

Filter 4169 = Großhandelspreis DE-LU in €/MWh, netto. SMARD liefert die Daten in Wochenblöcken
(Montag 00:00 Ortszeit), Zeitstempel in Millisekunden UTC:
  Index:  /app/chart_data/4169/DE/index_quarterhour.json  -> {"timestamps": [Blockanfang, ...]}
  Block:  /app/chart_data/4169/DE/4169_DE_quarterhour_{ts}.json -> {"series": [[ts, preis|null], ...]}

Seit dem 01.10.2025 (Lieferzeitraum, = 30.09.2025 22:00 UTC) gibt es echte Viertelstundenpreise.
Davor enthält dieselbe Viertelstunden-Reihe den Stundenpreis viermal hintereinander -- geprüft:
identisch mit index_hour.json / 4169_DE_hour_{ts}.json. Deshalb genügt die Viertelstunden-Reihe für
den gesamten Zeitraum; Viertelstunden vor dem Stichtag werden nur als "stündlich" gekennzeichnet.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

import httpx

SMARD_BASE_URL = "https://www.smard.de/app/chart_data/4169/DE"
INDEX_URL = f"{SMARD_BASE_URL}/index_quarterhour.json"
BLOCK_URL = SMARD_BASE_URL + "/4169_DE_quarterhour_{ts}.json"

# Erster Liefertag mit Viertelstundenauktion: 01.10.2025 00:00 Ortszeit.
QUARTER_HOUR_START_UTC = datetime(2025, 9, 30, 22, 0, tzinfo=timezone.utc)

INDEX_TTL_SECONDS = 60 * 60
# Blöcke, die noch leere Werte enthalten (laufende Woche), werden nach einer Stunde neu geladen;
# vollständige Blöcke ändern sich nicht mehr und bleiben im Speicher (je Block ~672 Werte).
OPEN_BLOCK_TTL_SECONDS = 60 * 60
MAX_PARALLEL_REQUESTS = 6


class SmardError(Exception):
    pass


_index_cache: tuple[float, list[int]] | None = None
_block_cache: dict[int, tuple[float, bool, dict[int, float]]] = {}  # ts -> (geladen, vollständig, werte)


async def _get_json(client: httpx.AsyncClient, url: str) -> dict:
    try:
        response = await client.get(url)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPError as exc:
        raise SmardError(f"SMARD nicht erreichbar: {exc}") from exc
    except ValueError as exc:
        raise SmardError("SMARD lieferte eine ungültige Antwort (kein JSON).") from exc


async def _block_index(client: httpx.AsyncClient) -> list[int]:
    global _index_cache
    if _index_cache and time.time() - _index_cache[0] < INDEX_TTL_SECONDS:
        return _index_cache[1]
    payload = await _get_json(client, INDEX_URL)
    timestamps = sorted(int(ts) for ts in payload.get("timestamps", []))
    if not timestamps:
        raise SmardError("SMARD-Index enthält keine Datenblöcke.")
    _index_cache = (time.time(), timestamps)
    return timestamps


async def _block(client: httpx.AsyncClient, block_ts: int, semaphore: asyncio.Semaphore) -> dict[int, float]:
    cached = _block_cache.get(block_ts)
    if cached and (cached[1] or time.time() - cached[0] < OPEN_BLOCK_TTL_SECONDS):
        return cached[2]
    async with semaphore:
        payload = await _get_json(client, BLOCK_URL.format(ts=block_ts))
    series = payload.get("series", [])
    values = {int(ts): float(v) for ts, v in series if v is not None}
    complete = bool(series) and len(values) == len(series)
    _block_cache[block_ts] = (time.time(), complete, values)
    return values


def _blocks_for_range(index: list[int], start_ms: int, end_ms: int) -> list[int]:
    """Blöcke, die [start_ms, end_ms) überlappen. Block i reicht bis zum Beginn von Block i+1."""
    selected = []
    for i, block_start in enumerate(index):
        block_end = index[i + 1] if i + 1 < len(index) else block_start + 8 * 24 * 3600 * 1000
        if block_start < end_ms and block_end > start_ms:
            selected.append(block_start)
    return selected


async def fetch_prices(start_utc: datetime, end_utc: datetime) -> dict[datetime, float]:
    """Day-Ahead-Preise (€/MWh) je Viertelstundenbeginn (UTC) für [start_utc, end_utc).

    Viertelstunden ohne Preis (z.B. Zukunft) fehlen im Ergebnis.
    """
    start_ms = int(start_utc.timestamp() * 1000)
    end_ms = int(end_utc.timestamp() * 1000)
    semaphore = asyncio.Semaphore(MAX_PARALLEL_REQUESTS)
    async with httpx.AsyncClient(timeout=20.0) as client:
        index = await _block_index(client)
        blocks = _blocks_for_range(index, start_ms, end_ms)
        results = await asyncio.gather(*(_block(client, ts, semaphore) for ts in blocks))

    prices: dict[datetime, float] = {}
    for values in results:
        for ts, value in values.items():
            if start_ms <= ts < end_ms:
                prices[datetime.fromtimestamp(ts / 1000, tz=timezone.utc)] = value
    return prices
