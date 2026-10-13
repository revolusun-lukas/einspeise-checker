"""FastAPI-Anwendung: Routen-Einbindung + Ausliefern des statischen Frontends."""
from __future__ import annotations

import re
import time
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.rate_limit import limiter, rate_limit_exceeded_handler
from app.routes import calculate_routes, import_routes

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"


class RevalidateStaticFiles(StaticFiles):
    """Erzwingt, dass der Browser /static/-Dateien bei jedem Laden neu validiert (ETag/Last-Modified),
    statt sie ungefragt aus dem lokalen Cache zu übernehmen -- sonst kann altes app.js zu neuem
    index.html gecacht werden (siehe Dynamischer Tarif Check)."""

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


app = FastAPI(title="Einspeise-Checker")

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)
# Komprimiert Responses verlustfrei für die Übertragung (Content-Encoding: gzip).
app.add_middleware(GZipMiddleware, minimum_size=1000)

app.include_router(import_routes.router)
app.include_router(calculate_routes.router)

app.mount("/static", RevalidateStaticFiles(directory=STATIC_DIR), name="static")

# Ändert sich bei jedem Serverstart -- hängt als ?v=... an alle /static/-Referenzen im HTML, damit
# Browser nach einem Update garantiert die neue Datei abrufen.
ASSET_VERSION = str(int(time.time()))
_STATIC_ASSET_RE = re.compile(r'(href|src)="(/static/[^"]+)"')


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    html = _STATIC_ASSET_RE.sub(rf'\1="\2?v={ASSET_VERSION}"', html)
    return HTMLResponse(html)


@app.get("/healthz", include_in_schema=False)
async def healthz() -> dict:
    return {"status": "ok"}


if __name__ == "__main__":
    # Fallback-Start für Hosting-Umgebungen, die "python app/main.py" ausführen und den Port über $PORT vorgeben.
    import os

    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
