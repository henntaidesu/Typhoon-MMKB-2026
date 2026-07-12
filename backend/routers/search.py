"""Knowledge-processing query endpoints (>=3 query types, per rubric S1-4).

  1. /search/semantic        意味的選択  (semantic associative search)
  2. /search/spatiotemporal  時空間的選択 (PostGIS ST_MakeEnvelope + time filter)
  3. /search/hybrid          時空間 x 意味 結合 (spatio-temporal then semantic rank)
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from db import get_session
from models import AdminRegion, Landfall, SecondaryDisaster, Typhoon, TrackPoint, TyphoonRegionImpact
from schemas import SemanticQuery, TyphoonBrief
from services.semantic import semantic_disasters, semantic_typhoons

router = APIRouter(prefix="/search", tags=["search"])


@router.post("/semantic")
def search_semantic(body: SemanticQuery, session: Session = Depends(get_session)):
    """Natural-language -> embedding -> pgvector cosine Top-K."""
    typhoons = [
        {**TyphoonBrief.model_validate(t).model_dump(), "distance": round(d, 4)}
        for t, d in semantic_typhoons(session, body.q, body.k)
    ]
    disasters = [
        {"id": d.id, "typhoon_id": d.typhoon_id, "disaster_type": d.disaster_type,
         "description": d.description, "lat": d.lat, "lon": d.lon,
         "distance": round(dist, 4)}
        for d, dist in semantic_disasters(session, body.q, body.k)
    ]
    return {"query": body.q, "typhoons": typhoons, "disasters": disasters}


def _bbox_env(bbox: str):
    minx, miny, maxx, maxy = (float(x) for x in bbox.split(","))
    return func.ST_MakeEnvelope(minx, miny, maxx, maxy, 4326)


@router.get("/spatiotemporal")
def search_spatiotemporal(
    session: Session = Depends(get_session),
    bbox: str = Query(..., description="minLon,minLat,maxLon,maxLat"),
    date_from: datetime | None = None,
    date_to: datetime | None = None,
):
    """Typhoons whose track passes through the bbox within the time window."""
    env = _bbox_env(bbox)
    conds = [func.ST_Intersects(TrackPoint.geom, env)]
    if date_from:
        conds.append(TrackPoint.obs_time >= date_from)
    if date_to:
        conds.append(TrackPoint.obs_time <= date_to)
    stmt = (
        select(Typhoon)
        .where(Typhoon.id.in_(select(TrackPoint.typhoon_id).where(and_(*conds))))
        .order_by(Typhoon.start_time.desc())
    )
    return [TyphoonBrief.model_validate(t).model_dump() for t in session.scalars(stmt)]


@router.get("/hybrid")
def search_hybrid(
    session: Session = Depends(get_session),
    q: str = Query(..., description="semantic query text"),
    bbox: str = Query(..., description="minLon,minLat,maxLon,maxLat"),
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    k: int = 10,
):
    """Spatio-temporal selection first, then semantic ranking (semantic join)."""
    from services.embedding import embed

    env = _bbox_env(bbox)
    conds = [func.ST_Intersects(TrackPoint.geom, env)]
    if date_from:
        conds.append(TrackPoint.obs_time >= date_from)
    if date_to:
        conds.append(TrackPoint.obs_time <= date_to)
    candidate_ids = select(TrackPoint.typhoon_id).where(and_(*conds))

    qvec = embed(q)
    dist = Typhoon.embedding.cosine_distance(qvec).label("distance")
    stmt = (
        select(Typhoon, dist)
        .where(Typhoon.id.in_(candidate_ids), Typhoon.embedding.isnot(None))
        .order_by(dist)
        .limit(k)
    )
    return [
        {**TyphoonBrief.model_validate(t).model_dump(), "distance": round(float(d), 4)}
        for t, d in session.execute(stmt).all()
    ]


@router.get("/stats")
def stats(session: Session = Depends(get_session)):
    by_year = session.execute(
        select(Typhoon.season_year, func.count()).group_by(Typhoon.season_year).order_by(Typhoon.season_year)
    ).all()
    by_type = session.execute(
        select(SecondaryDisaster.disaster_type, func.count()).group_by(SecondaryDisaster.disaster_type)
    ).all()
    # Top affected countries by distinct typhoons (admin-0 impacts).
    top_countries = session.execute(
        select(AdminRegion.name,
               func.count(func.distinct(TyphoonRegionImpact.typhoon_id)).label("count"))
        .join(TyphoonRegionImpact, TyphoonRegionImpact.admin_region_id == AdminRegion.id)
        .where(AdminRegion.admin_level == 0)
        .group_by(AdminRegion.name).order_by(func.count(
            func.distinct(TyphoonRegionImpact.typhoon_id)).desc()).limit(10)
    ).all()
    return {
        "typhoons_by_year": [{"year": y, "count": c} for y, c in by_year],
        "disasters_by_type": [{"type": t, "count": c} for t, c in by_type],
        "top_countries": [{"country": n, "count": c} for n, c in top_countries],
        "total_typhoons": session.scalar(select(func.count()).select_from(Typhoon)),
        "total_disasters": session.scalar(select(func.count()).select_from(SecondaryDisaster)),
        "total_landfalls": session.scalar(select(func.count()).select_from(Landfall)),
    }
