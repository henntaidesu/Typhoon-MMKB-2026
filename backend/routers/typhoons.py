"""Typhoon resource endpoints. Geometry is returned as GeoJSON for Leaflet."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from db import get_session
from models import AffectedRegion, SecondaryDisaster, Typhoon, TrackPoint
from schemas import TyphoonBrief

router = APIRouter(prefix="/typhoons", tags=["typhoons"])


@router.get("", response_model=list[TyphoonBrief])
def list_typhoons(
    session: Session = Depends(get_session),
    year: int | None = None,
    name: str | None = None,
    min_wind: float | None = None,
    limit: int = Query(5000, le=20000),
):
    """Attribute query — filter typhoons by year / name / intensity."""
    stmt = select(Typhoon)
    if year is not None:
        stmt = stmt.where(Typhoon.season_year == year)
    if name:
        stmt = stmt.where(Typhoon.name.ilike(f"%{name}%"))
    if min_wind is not None:
        stmt = stmt.where(Typhoon.max_wind_kt >= min_wind)
    stmt = stmt.order_by(Typhoon.start_time.desc()).limit(limit)
    return [TyphoonBrief.model_validate(t) for t in session.scalars(stmt)]


@router.get("/{tid}", response_model=TyphoonBrief)
def get_typhoon(tid: int, session: Session = Depends(get_session)):
    t = session.get(Typhoon, tid)
    if not t:
        raise HTTPException(404, "typhoon not found")
    dc = session.scalar(
        select(func.count()).select_from(SecondaryDisaster).where(SecondaryDisaster.typhoon_id == tid)
    )
    out = TyphoonBrief.model_validate(t)
    out.disaster_count = dc
    return out


# Which agency's track to draw as the main line, in order of preference.
_AGENCY_PRIORITY = ["CMA", "JMA", "JTWC"]


@router.get("/{tid}/track")
def get_track(tid: int, session: Session = Depends(get_session)):
    """Track as a GeoJSON Feature (LineString) with per-point intensity props.

    A typhoon may carry several agencies' tracks (CMA/JMA/JTWC). Drawing them all
    as one time-sorted line would zig-zag between agencies, so we return a single
    primary agency's track (CMA preferred) and list the other available agencies
    in the properties for optional overlay."""
    pts = session.scalars(
        select(TrackPoint).where(TrackPoint.typhoon_id == tid).order_by(TrackPoint.obs_time)
    ).all()
    if not pts:
        raise HTTPException(404, "no track points")

    agencies = {p.agency for p in pts}
    primary = next((a for a in _AGENCY_PRIORITY if a in agencies), None)
    if primary is not None or agencies:
        chosen = primary if primary is not None else next(iter(agencies))
        pts = [p for p in pts if p.agency == chosen]
    else:
        chosen = None

    coords = [[p.lon, p.lat] for p in pts]
    props = [
        {"time": p.obs_time.isoformat(), "wind_kt": p.wind_kt,
         "pressure_hpa": p.pressure_hpa, "grade": p.grade}
        for p in pts
    ]
    return {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": coords},
        "properties": {
            "typhoon_id": tid, "points": props,
            "agency": chosen,
            "agencies": sorted(a for a in agencies if a),
        },
    }


@router.get("/{tid}/disasters")
def get_disasters(tid: int, session: Session = Depends(get_session)):
    """Secondary disasters as a GeoJSON FeatureCollection (points)."""
    rows = session.scalars(
        select(SecondaryDisaster).where(SecondaryDisaster.typhoon_id == tid)
    ).all()
    feats = []
    for d in rows:
        if d.lon is None or d.lat is None:
            continue
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [d.lon, d.lat]},
            "properties": {
                "id": d.id, "disaster_type": d.disaster_type,
                "event_time": d.event_time.isoformat() if d.event_time else None,
                "casualties": d.casualties, "economic_loss_usd": d.economic_loss_usd,
                "description": d.description, "source": d.source, "source_url": d.source_url,
            },
        })
    return {"type": "FeatureCollection", "features": feats}


@router.get("/{tid}/affected-regions")
def get_regions(tid: int, session: Session = Depends(get_session)):
    """Affected-region polygons via PostGIS ST_AsGeoJSON."""
    rows = session.execute(
        select(AffectedRegion.id, AffectedRegion.region_name, AffectedRegion.impact_type,
               func.ST_AsGeoJSON(AffectedRegion.geom))
        .where(AffectedRegion.typhoon_id == tid)
    ).all()
    import json
    feats = [
        {"type": "Feature", "geometry": json.loads(geo),
         "properties": {"id": rid, "region_name": rn, "impact_type": it}}
        for rid, rn, it, geo in rows if geo
    ]
    return {"type": "FeatureCollection", "features": feats}
