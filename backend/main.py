"""Typhoon MMKB — FastAPI application entry point.

Run:  conda activate MMKB && uvicorn main:app --reload --port 8000
(from the backend/ directory)
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routers import public_info, search, sources, stats, typhoons

log = logging.getLogger("mmkb.startup")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Auto-create the database, extensions and tables on startup so a fresh
    server needs no manual init step. Kept resilient: if the DB is unreachable
    (or pgvector isn't deployed yet) the app still boots and /health reports it,
    instead of every request crashing with a raw traceback."""
    try:
        from init_db import initialize
        engine = initialize(verbose=True)
        engine.dispose()
        log.info("[startup] database schema ready")
    except Exception as e:  # noqa: BLE001
        from init_db import _decode
        log.error("[startup] auto-init skipped: %s", _decode(e))
        log.error("[startup] server is up but the DB is not ready — see /health")
    yield


app = FastAPI(
    title="Typhoon MMKB API",
    description="West Pacific typhoon multimedia knowledge base — "
                "spatio-temporal (PostGIS) + semantic (pgvector) computing.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(typhoons.router)
app.include_router(search.router)
app.include_router(sources.router)
app.include_router(stats.router)
app.include_router(public_info.router)


@app.get("/")
def root():
    return {"service": "typhoon-mmkb", "docs": "/docs"}


@app.get("/health")
def health():
    from sqlalchemy import text
    from db import engine
    try:
        with engine.connect() as c:
            c.execute(text("SELECT 1"))
        return {"status": "ok", "db": "up"}
    except Exception as e:  # noqa: BLE001
        return {"status": "degraded", "db": "down", "error": str(e)[:120]}
