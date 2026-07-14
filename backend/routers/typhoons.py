"""Typhoon resource endpoints. Geometry is returned as GeoJSON for Leaflet."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from db import get_session
from models import (
    AdminRegion, AffectedRegion, Landfall, PublicInfo, SecondaryDisaster, Typhoon,
    TrackPoint, TyphoonRegionImpact,
)
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


@router.get("/{tid}/public-info")
def get_public_info(
    tid: int,
    session: Session = Depends(get_session),
    info_type: str | None = Query(None, description="warning|advisory|evacuation|news|bulletin"),
):
    """公共情报 (warnings / advisories / evacuation / news) about this typhoon,
    as a GeoJSON FeatureCollection. Records without a location are returned in a
    separate `unlocated` list so the UI can still list them."""
    stmt = select(PublicInfo).where(PublicInfo.typhoon_id == tid)
    if info_type:
        stmt = stmt.where(PublicInfo.info_type == info_type)
    stmt = stmt.order_by(PublicInfo.publish_time)
    rows = session.scalars(stmt).all()
    feats, unlocated = [], []
    for p in rows:
        props = {
            "id": p.id, "info_type": p.info_type, "category": p.category,
            "agency": p.agency, "severity": p.severity, "title": p.title,
            "body": p.body, "region_name": p.region_name,
            "publish_time": p.publish_time.isoformat() if p.publish_time else None,
            "source": p.source, "source_url": p.source_url,
        }
        if p.lon is None or p.lat is None:
            unlocated.append(props)
            continue
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [p.lon, p.lat]},
            "properties": props,
        })
    return {"type": "FeatureCollection", "features": feats, "unlocated": unlocated}


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


@router.get("/{tid}/countries")
def get_countries(tid: int, session: Session = Depends(get_session)):
    """Administrative regions (countries + provinces) this typhoon affected —
    the 'which countries/regions were affected' answer. Most-impacted first."""
    rows = session.execute(
        select(TyphoonRegionImpact, AdminRegion)
        .join(AdminRegion, AdminRegion.id == TyphoonRegionImpact.admin_region_id)
        .where(TyphoonRegionImpact.typhoon_id == tid)
    ).all()
    out = [
        {"admin_region_id": ar.id, "name": ar.name, "iso_a3": ar.iso_a3,
         "admin_level": ar.admin_level, "country": ar.country,
         "passed_over": imp.passed_over, "landfall": imp.landfall,
         "within_corridor": imp.within_corridor,
         "min_distance_deg": (round(imp.min_distance_deg, 3)
                              if imp.min_distance_deg is not None else None),
         "max_wind_kt": imp.max_wind_kt,
         "landfall_time": imp.landfall_time.isoformat() if imp.landfall_time else None}
        for imp, ar in rows
    ]
    # Landfall regions first, then those the eye passed over, then by proximity.
    out.sort(key=lambda r: (not r["landfall"], not r["passed_over"],
                            r["min_distance_deg"] if r["min_distance_deg"] is not None else 1e9))
    return out


@router.get("/{tid}/landfalls")
def get_landfalls(tid: int, session: Session = Depends(get_session)):
    """Landfall events for this typhoon as a GeoJSON Point FeatureCollection."""
    rows = session.scalars(
        select(Landfall).where(Landfall.typhoon_id == tid).order_by(Landfall.landfall_time)
    ).all()
    feats = []
    for lf in rows:
        if lf.lon is None or lf.lat is None:
            continue
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lf.lon, lf.lat]},
            "properties": {
                "id": lf.id, "country": lf.country,
                "admin_region_id": lf.admin_region_id,
                "landfall_time": lf.landfall_time.isoformat() if lf.landfall_time else None,
                "wind_kt": lf.wind_kt, "pressure_hpa": lf.pressure_hpa, "grade": lf.grade,
            },
        })
    return {"type": "FeatureCollection", "features": feats}
