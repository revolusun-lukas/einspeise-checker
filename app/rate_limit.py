"""Gemeinsame Rate-Limiter-Instanz (IP-basiert, In-Memory).

In-Memory reicht, solange die App als einzelner Worker-Prozess läuft (Render setzt
WEB_CONCURRENCY=1). Bei mehreren parallelen Instanzen wäre das Limit pro Instanz statt
global -- für den aktuellen Betrieb nicht relevant.
"""
from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)


async def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={"detail": f"Zu viele Anfragen (Limit: {exc.detail}). Bitte später erneut versuchen."},
    )
