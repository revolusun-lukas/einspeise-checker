"""Routen für den Erlösvergleich (Preisabruf + Berechnung) sowie Tagesdetail und Paaranalyse."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.calculation.revenue import calculate_revenue, day_detail, pair_analysis
from app.pricing.prices import (
    SOURCE_AWATTAR_HOUR,
    SOURCE_SMARD_HOUR,
    SOURCE_SMARD_QUARTER_HOUR,
    PriceError,
    load_prices,
)
from app.rate_limit import limiter
from app.schemas import (
    CalculateRequest,
    CalculateResponse,
    DayDetailRequest,
    DayDetailResponse,
    PairAnalysisRequest,
    PairAnalysisResponse,
)
from app.session_store import SessionNotFoundError, store

router = APIRouter(prefix="/api", tags=["calculate"])


@router.post("/calculate", response_model=CalculateResponse)
@limiter.limit("10/minute")
async def calculate(request: Request, req: CalculateRequest) -> CalculateResponse:
    try:
        session = store.get(req.session_id)
    except SessionNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc

    if session.feed_in_kwh is None or session.feed_in_kwh.empty:
        raise HTTPException(400, "Bitte zuerst einen Import abschließen.")

    feed_in = session.feed_in_kwh
    if session.price_data is None:
        try:
            session.price_data = await load_prices(feed_in.index)
        except PriceError as exc:
            raise HTTPException(502, str(exc)) from exc
    price_data = session.price_data

    try:
        result, detail = calculate_revenue(feed_in, price_data.eur_mwh, req.tariffs)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    source_counts = price_data.source.value_counts()
    result["price_resolution"] = {
        "quarter_hour_slots": int(source_counts.get(SOURCE_SMARD_QUARTER_HOUR, 0)),
        "hour_slots": int(source_counts.get(SOURCE_SMARD_HOUR, 0)),
        "awattar_slots": int(source_counts.get(SOURCE_AWATTAR_HOUR, 0)),
        "notes": price_data.notes,
    }
    session.calculation_detail = detail
    return result


def _calculation_detail(session_id: str):
    try:
        session = store.get(session_id)
    except SessionNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    if session.calculation_detail is None:
        raise HTTPException(400, "Bitte zuerst eine Berechnung durchführen.")
    return session.calculation_detail


@router.post("/day-detail", response_model=DayDetailResponse)
@limiter.limit("60/minute")
async def get_day_detail(request: Request, req: DayDetailRequest) -> DayDetailResponse:
    detail = _calculation_detail(req.session_id)
    try:
        return day_detail(detail, req.date, req.tariff_names)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/pair-analysis", response_model=PairAnalysisResponse)
@limiter.limit("60/minute")
async def get_pair_analysis(request: Request, req: PairAnalysisRequest) -> PairAnalysisResponse:
    detail = _calculation_detail(req.session_id)
    try:
        return pair_analysis(detail, req.tariff_names)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
