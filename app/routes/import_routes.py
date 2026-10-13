"""Routen für den Import der Einspeisedaten (Upload + Spaltenzuordnung bestätigen)."""
from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, Request, UploadFile

from app.importer import aggregation, parsing
from app.rate_limit import limiter
from app.schemas import ImportConfirmRequest, ImportConfirmResponse, ImportUploadResponse
from app.session_store import SessionNotFoundError, store

router = APIRouter(prefix="/api/import", tags=["import"])

MAX_PREVIEW_ROWS = 15
MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MB -- schützt einen öffentlich erreichbaren Server vor Missbrauch


@router.post("/upload", response_model=ImportUploadResponse)
@limiter.limit("10/minute")
async def upload_csv(request: Request, file: UploadFile = File(...)) -> ImportUploadResponse:
    content = await file.read()
    if not content:
        raise HTTPException(400, "Die Datei ist leer.")
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"Die Datei ist zu groß (Limit: {MAX_UPLOAD_BYTES // (1024 * 1024)} MB).")

    try:
        df, parse_meta = parsing.read_csv_raw(content)
    except parsing.ParsingError as exc:
        raise HTTPException(400, str(exc)) from exc

    if df.shape[1] < 2:
        raise HTTPException(400, "Es wurden weniger als 2 Spalten erkannt. Bitte Trennzeichen der CSV prüfen.")
    if df.empty:
        raise HTTPException(400, "Die CSV enthält keine Datenzeilen.")

    decimal = parse_meta["decimal"]
    ts_col, ts_warnings = parsing.suggest_timestamp_column(df)
    value_col, direction = parsing.suggest_feed_in_column(df, exclude=ts_col, decimal=decimal)
    value_type = parsing.suggest_value_type(value_col, df[value_col], decimal) if value_col else None
    grid_col = None
    if value_col and direction == "feed_in":
        grid_col = parsing.suggest_grid_column(df, exclude=[c for c in (ts_col, value_col) if c])

    session_id, session = store.create()
    session.raw_df = df
    session.parse_meta = parse_meta

    preview_rows = df.head(MAX_PREVIEW_ROWS).to_dict(orient="records")

    # Nur Spaltennamen + tatsächlich angezeigte Vorschau-Werte prüfen (das ist exakt der
    # Bereich, der im Frontend gerendert wird) -- nicht die komplette Datei.
    suspicious_values = parsing.find_html_like_values(list(df.columns))
    for row in preview_rows:
        suspicious_values += parsing.find_html_like_values([str(v) for v in row.values()])
    warnings = list(ts_warnings)
    if value_col is None:
        warnings.append("Es konnte keine Werte-Spalte automatisch erkannt werden. Bitte manuell auswählen.")
    if suspicious_values:
        sample = ", ".join(f"„{v}“" for v in suspicious_values[:3])
        warnings.append(
            f"Achtung: Diese Datei enthält Zeichen, die wie HTML-Code aussehen (z.B. {sample}). "
            "Bitte die Herkunft der Datei prüfen, bevor du fortfährst."
        )

    return ImportUploadResponse(
        session_id=session_id,
        columns=list(df.columns),
        preview_rows=preview_rows,
        suggested_timestamp_column=ts_col,
        suggested_value_column=value_col,
        suggested_value_type=value_type,
        suggested_direction=direction,
        suggested_grid_column=grid_col,
        suggested_timezone=parsing.suggest_timezone(ts_col),
        row_count=len(df),
        warnings=warnings,
    )


@router.post("/confirm", response_model=ImportConfirmResponse)
@limiter.limit("10/minute")
async def confirm_import(request: Request, req: ImportConfirmRequest) -> ImportConfirmResponse:
    try:
        session = store.get(req.session_id)
    except SessionNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc

    if session.raw_df is None:
        raise HTTPException(400, "Kein Import-Vorgang für diese Session gefunden. Bitte CSV erneut hochladen.")

    df = session.raw_df
    columns = [req.timestamp_column, req.value_column] + ([req.grid_column] if req.grid_column else [])
    if any(c not in df.columns for c in columns):
        raise HTTPException(400, "Ungültige Spaltenauswahl.")
    if len(set(columns)) != len(columns):
        raise HTTPException(400, "Zeitstempel-, Einspeise- und Bezugsspalte müssen verschieden sein.")

    try:
        result = aggregation.build_quarter_hour_series(
            df,
            timestamp_col=req.timestamp_column,
            value_col=req.value_column,
            value_type=req.value_type,
            direction=req.direction,
            timezone_name=req.timezone,
            decimal=session.parse_meta.get("decimal", "."),
            grid_col=req.grid_column,
        )
    except aggregation.AggregationError as exc:
        raise HTTPException(400, str(exc)) from exc

    session.feed_in_kwh = result.feed_in_kwh
    session.grid_kwh = result.grid_kwh
    session.price_data = None
    session.calculation_detail = None

    feed_in = result.feed_in_kwh
    return ImportConfirmResponse(
        session_id=req.session_id,
        start_date=feed_in.index.min().isoformat(),
        end_date=(feed_in.index.max() + aggregation.SLOT).isoformat(),
        total_feed_in_kwh=round(float(feed_in.sum()), 3),
        total_grid_kwh=round(float(result.grid_kwh.sum()), 3) if result.grid_kwh is not None else None,
        slots_count=int(len(feed_in)),
        warnings=result.warnings,
    )
