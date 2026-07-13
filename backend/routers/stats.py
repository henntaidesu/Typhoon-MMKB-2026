"""Geographic-impact aggregation endpoints.

Answers the knowledge-base questions the raw tracks can't:
  - which countries a typhoon affected / how many typhoons hit each country
  - how many times each region was struck by a landfall (frequency)
  - a choropleth-ready GeoJSON of landfall/impact counts per admin region

Built on the derived tables typhoon_region_impact + landfall (see
crawler/enrich.py). Aggregates are chart-friendly arrays or standard GeoJSON.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from db import get_session
from models import AdminRegion, Landfall, Typhoon, TrackPoint, TyphoonRegionImpact

router = APIRouter(prefix="/stats", tags=["stats"])

# Primary-track preference so a multi-agency storm draws ONE line (see enrich.py).
_AGENCY_PRIORITY = ["CMA", "JMA", "JTWC", "IBTrACS"]


def _landfall_counts(session, level: int, min_year=None, max_year=None) -> dict[int, int]:
    """Landfalls per admin_region.id, level-aware. Level 0 (country) matches the
    denormalized Landfall.country name; levels 1/2 (province, prefecture) count
    landfall points geometrically contained in each region — so counts are
    correct regardless of which single region the landfall was attributed to."""
    if level == 0:
        stmt = (
            select(AdminRegion.id, func.count())
            .join(Landfall, Landfall.country == AdminRegion.name)
            .where(AdminRegion.admin_level == 0)
            .group_by(AdminRegion.id)
        )
    else:  # admin-1 / admin-2 — spatial containment (GiST-backed, ~1900 points).
        stmt = (
            select(AdminRegion.id, func.count())
            .join(Landfall, func.ST_Contains(AdminRegion.geom, Landfall.geom))
            .where(AdminRegion.admin_level == level)
            .group_by(AdminRegion.id)
        )
    if min_year is not None or max_year is not None:
        stmt = stmt.join(Typhoon, Typhoon.id == Landfall.typhoon_id)
        if min_year is not None:
            stmt = stmt.where(Typhoon.season_year >= min_year)
        if max_year is not None:
            stmt = stmt.where(Typhoon.season_year <= max_year)
    return {rid: c for rid, c in session.execute(stmt).all() if rid is not None}


@router.get("/by-country")
def by_country(session: Session = Depends(get_session)):
    """Per country: how many distinct typhoons affected it, and total landfalls."""
    # distinct typhoons that affected each country (admin-0 impacts).
    impact_rows = session.execute(
        select(AdminRegion.id, AdminRegion.iso_a3, AdminRegion.name,
               func.count(func.distinct(TyphoonRegionImpact.typhoon_id)))
        .join(TyphoonRegionImpact, TyphoonRegionImpact.admin_region_id == AdminRegion.id)
        .where(AdminRegion.admin_level == 0)
        .group_by(AdminRegion.id, AdminRegion.iso_a3, AdminRegion.name)
    ).all()
    # landfalls grouped by denormalized country name.
    lf_rows = dict(session.execute(
        select(Landfall.country, func.count()).group_by(Landfall.country)
    ).all())
    out = [
        {"admin_region_id": rid, "iso_a3": iso, "country": name, "name": name,
         "typhoon_count": tc, "landfall_count": lf_rows.get(name, 0)}
        for rid, iso, name, tc in impact_rows
    ]
    out.sort(key=lambda r: (r["typhoon_count"], r["landfall_count"]), reverse=True)
    return out


@router.get("/by-region")
def by_region(
    session: Session = Depends(get_session),
    level: int = Query(1, ge=0, le=2),
    country: str | None = Query(None, description="parent country ISO-A2 or name"),
    min_year: int | None = None,
    max_year: int | None = None,
):
    """Per admin region: landfall frequency + affecting-typhoon count. Answers
    'how many times was region X hit by a typhoon landfall'."""
    lf_map = _landfall_counts(session, level, min_year, max_year)
    imp = (
        select(TyphoonRegionImpact.admin_region_id,
               func.count(func.distinct(TyphoonRegionImpact.typhoon_id)).label("impact_count"))
        .group_by(TyphoonRegionImpact.admin_region_id)
        .subquery()
    )
    stmt = (
        select(AdminRegion.id, AdminRegion.name, AdminRegion.country,
               AdminRegion.parent_name, func.coalesce(imp.c.impact_count, 0))
        .select_from(AdminRegion)
        .outerjoin(imp, imp.c.admin_region_id == AdminRegion.id)
        .where(AdminRegion.admin_level == level)
    )
    if country:
        c = country.strip()
        stmt = stmt.where(
            (func.upper(AdminRegion.iso_a2) == c.upper()) | (AdminRegion.country == c)
        )
    out = []
    for rid, name, ctry, parent, impc in session.execute(stmt).all():
        lfc = lf_map.get(rid, 0)
        if lfc or impc:  # only regions actually touched
            out.append({"admin_region_id": rid, "name": name, "country": ctry,
                        "parent_name": parent, "landfall_count": lfc, "impact_count": impc})
    out.sort(key=lambda r: (r["landfall_count"], r["impact_count"]), reverse=True)
    return out


@router.get("/landfall-geojson")
def landfall_geojson(
    session: Session = Depends(get_session),
    level: int = Query(0, ge=0, le=2),
    bbox: str | None = Query(None, description="minLon,minLat,maxLon,maxLat"),
):
    """Choropleth-ready FeatureCollection of admin polygons, each carrying its
    landfall_count / impact_count. Drives the Leaflet choropleth directly."""
    lf_map = _landfall_counts(session, level)
    imp_sub = (
        select(TyphoonRegionImpact.admin_region_id,
               func.count(func.distinct(TyphoonRegionImpact.typhoon_id)).label("impact_count"))
        .group_by(TyphoonRegionImpact.admin_region_id).subquery()
    )
    stmt = (
        select(AdminRegion.id, AdminRegion.name, AdminRegion.country,
               func.coalesce(imp_sub.c.impact_count, 0),
               func.ST_AsGeoJSON(AdminRegion.geom))
        .select_from(AdminRegion)
        .outerjoin(imp_sub, imp_sub.c.admin_region_id == AdminRegion.id)
        .where(AdminRegion.admin_level == level)
    )
    if bbox:
        minx, miny, maxx, maxy = (float(x) for x in bbox.split(","))
        env = func.ST_MakeEnvelope(minx, miny, maxx, maxy, 4326)
        stmt = stmt.where(func.ST_Intersects(AdminRegion.geom, env))

    feats = []
    max_lf = 0
    for rid, name, ctry, impc, geo in session.execute(stmt).all():
        if not geo:
            continue
        lfc = lf_map.get(rid, 0)
        # Countries (level 0) always render for context. Provinces (level 1)
        # render if touched at all (corridor or landfall). Prefectures/cities
        # (level 2) are thousands of units — render only actual landfall sites,
        # so the choropleth payload stays small and on-topic (登陆频次).
        if level == 1 and not (lfc or impc):
            continue
        if level >= 2 and not lfc:
            continue
        max_lf = max(max_lf, lfc)
        feats.append({
            "type": "Feature", "geometry": json.loads(geo),
            "properties": {"id": rid, "name": name, "country": ctry,
                           "landfall_count": lfc, "impact_count": impc},
        })
    return {"type": "FeatureCollection", "features": feats,
            "properties": {"level": level, "max_landfall_count": max_lf}}


@router.get("/region/{region_id}/tracks")
def region_tracks(
    region_id: int,
    session: Session = Depends(get_session),
    landfall_only: bool = Query(False, description="only storms that made landfall here"),
    limit: int = Query(300, le=1000),
):
    """Tracks of every typhoon that affected (or landed in) an admin region, as a
    GeoJSON LineString FeatureCollection — powers the interactive click-to-map.
    One primary-agency line per storm so multi-agency tracks don't zig-zag."""
    region = session.get(AdminRegion, region_id)
    if region is None:
        raise HTTPException(404, "admin region not found")

    if landfall_only:
        sub = select(func.distinct(Landfall.typhoon_id)).where(Landfall.admin_region_id == region_id)
    else:
        sub = select(func.distinct(TyphoonRegionImpact.typhoon_id)).where(
            TyphoonRegionImpact.admin_region_id == region_id)
    typhoons = session.scalars(
        select(Typhoon).where(Typhoon.id.in_(sub))
        .order_by(Typhoon.season_year.desc().nullslast(), Typhoon.max_wind_kt.desc().nullslast())
        .limit(limit)
    ).all()
    total = session.scalar(select(func.count()).select_from(sub.subquery())) or 0
    tids = [t.id for t in typhoons]

    feats = []
    if tids:
        rows = session.execute(
            select(TrackPoint.typhoon_id, TrackPoint.agency, TrackPoint.obs_time,
                   TrackPoint.lon, TrackPoint.lat)
            .where(TrackPoint.typhoon_id.in_(tids))
            .order_by(TrackPoint.typhoon_id, TrackPoint.obs_time)
        ).all()
        by_tid: dict[int, list] = {}
        for r in rows:
            by_tid.setdefault(r.typhoon_id, []).append(r)
        metas = {t.id: t for t in typhoons}
        for tid, pts in by_tid.items():
            agencies = {p.agency for p in pts if p.agency}
            primary = next((a for a in _AGENCY_PRIORITY if a in agencies), None) \
                or (next(iter(agencies)) if agencies else None)
            coords = [[p.lon, p.lat] for p in pts if p.agency == primary]
            if len(coords) < 2:
                continue
            t = metas[tid]
            feats.append({
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": coords},
                "properties": {
                    "typhoon_id": tid, "intl_id": t.intl_id, "name": t.name,
                    "season_year": t.season_year, "max_wind_kt": t.max_wind_kt,
                },
            })
        feats.sort(key=lambda f: f["properties"]["season_year"] or 0, reverse=True)

    return {
        "type": "FeatureCollection", "features": feats,
        "properties": {
            "region_id": region.id, "region_name": region.name,
            "admin_level": region.admin_level, "country": region.country,
            "parent_name": region.parent_name,
            "typhoon_count": total, "shown": len(feats),
        },
    }
