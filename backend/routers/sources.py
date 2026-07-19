"""Data-source endpoints — power the '数据源' page.

  GET  /sources             list source cards + live status
  GET  /sources/status      lightweight status poll (all sources)
  POST /sources/{key}/crawl start a crawl for one source
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services import ingest

router = APIRouter(prefix="/sources", tags=["sources"])


class CrawlParams(BaseModel):
    variant: str | None = None
    years: list[int] | None = None
    mode: str | None = None  # 'new' (current season) | 'history' (past seasons); temporal sources only
    intl_id: str | None = None      # selected typhoon (gdelt_news web search)
    maxrecords: str | None = None   # max articles to fetch (gdelt_news)


@router.get("")
def list_sources():
    return {
        "active": ingest.active(),
        "categories": ingest.list_categories(),
        "sources": ingest.list_sources(),
    }


@router.get("/status")
def status():
    return {"active": ingest.active(), "status": ingest.all_status()}


@router.post("/{key}/crawl")
def start_crawl(key: str, params: CrawlParams | None = None):
    try:
        ingest.start(key, params.model_dump() if params else {})
    except KeyError:
        raise HTTPException(404, f"未知数据源：{key}")
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    return {"ok": True, "key": key, "state": ingest.get_status(key)}
