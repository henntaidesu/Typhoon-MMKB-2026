"""公共情报 (public-information) query endpoints.

Public information — official warnings/alerts (预警·警报), evacuation & emergency
advisories, and news/media reports — is a first-class knowledge unit alongside
secondary disasters. This router exposes attribute + spatio-temporal selection
over it (semantic selection lives in /search/semantic, which now also ranks
public info).
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from db import get_session
from models import PublicInfo

router = APIRouter(prefix="/public-info", tags=["public-info"])


@router.get("")
def list_public_info(
    session: Session = Depends(get_session),
    info_type: str | None = Query(None, description="warning|advisory|evacuation|news|bulletin"),
    source: str | None = Query(None, description="JMA|HKO|NMC|MEM|GDACS…"),
    typhoon_id: int | None = None,
    bbox: str | None = Query(None, description="minLon,minLat,maxLon,maxLat"),
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    limit: int = Query(2000, le=10000),
):
    """公共情报 as a GeoJSON FeatureCollection, filtered by kind / source /
    typhoon / bounding box / time window. Attribute + 時空間 selection."""
    conds = []
    if info_type:
        conds.append(PublicInfo.info_type == info_type)
    if source:
        conds.append(PublicInfo.source == source)
    if typhoon_id is not None:
        conds.append(PublicInfo.typhoon_id == typhoon_id)
    if date_from:
        conds.append(PublicInfo.publish_time >= date_from)
    if date_to:
        conds.append(PublicInfo.publish_time <= date_to)
    if bbox:
        minx, miny, maxx, maxy = (float(x) for x in bbox.split(","))
        env = func.ST_MakeEnvelope(minx, miny, maxx, maxy, 4326)
        conds.append(func.ST_Intersects(PublicInfo.geom, env))

    stmt = select(PublicInfo)
    if conds:
        stmt = stmt.where(and_(*conds))
    stmt = stmt.order_by(PublicInfo.publish_time.desc()).limit(limit)

    feats = []
    for p in session.scalars(stmt):
        if p.lon is None or p.lat is None:
            continue
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [p.lon, p.lat]},
            "properties": {
                "id": p.id, "typhoon_id": p.typhoon_id, "info_type": p.info_type,
                "category": p.category, "agency": p.agency, "severity": p.severity,
                "title": p.title, "body": p.body, "region_name": p.region_name,
                "publish_time": p.publish_time.isoformat() if p.publish_time else None,
                "source": p.source, "source_url": p.source_url,
            },
        })
    return {"type": "FeatureCollection", "features": feats}


@router.get("/stats")
def public_info_stats(session: Session = Depends(get_session)):
    """Counts by kind and by issuing source, for the 公共情报 dashboard."""
    by_type = session.execute(
        select(PublicInfo.info_type, func.count()).group_by(PublicInfo.info_type)
    ).all()
    by_source = session.execute(
        select(PublicInfo.source, func.count()).group_by(PublicInfo.source)
    ).all()
    return {
        "by_type": [{"type": t, "count": c} for t, c in by_type],
        "by_source": [{"source": s, "count": c} for s, c in by_source],
        "total": session.scalar(select(func.count()).select_from(PublicInfo)),
    }
