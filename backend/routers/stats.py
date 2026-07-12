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

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from db import get_session
from models import AdminRegion, Landfall, Typhoon, TyphoonRegionImpact

router = APIRouter(prefix="/stats", tags=["stats"])


@router.get("/by-country")
def by_country(session: Session = Depends(get_session)):
    """Per country: how many distinct typhoons affected it, and total landfalls."""
    # distinct typhoons that affected each country (admin-0 impacts).
    impact_rows = session.execute(
        select(AdminRegion.iso_a3, AdminRegion.name,
               func.count(func.distinct(TyphoonRegionImpact.typhoon_id)))
        .join(TyphoonRegionImpact, TyphoonRegionImpact.admin_region_id == AdminRegion.id)
        .where(AdminRegion.admin_level == 0)
        .group_by(AdminRegion.iso_a3, AdminRegion.name)
    ).all()
    # landfalls grouped by denormalized country name.
    lf_rows = dict(session.execute(
        select(Landfall.country, func.count()).group_by(Landfall.country)
    ).all())
    out = [
        {"iso_a3": iso, "country": name, "name": name,
         "typhoon_count": tc, "landfall_count": lf_rows.get(name, 0)}
        for iso, name, tc in impact_rows
    ]
    out.sort(key=lambda r: (r["typhoon_count"], r["landfall_count"]), reverse=True)
    return out


@router.get("/by-region")
def by_region(
    session: Session = Depends(get_session),
    level: int = Query(1, ge=0, le=1),
    country: str | None = Query(None, description="parent country ISO-A2 or name"),
    min_year: int | None = None,
    max_year: int | None = None,
):
    """Per admin region: landfall frequency + affecting-typhoon count. Answers
    'how many times was region X hit by a typhoon landfall'."""
    # Landfall counts per region (optionally year-filtered via the typhoon season).
    lf = (
        select(Landfall.admin_region_id, func.count().label("landfall_count"))
        .group_by(Landfall.admin_region_id)
    )
    if min_year is not None or max_year is not None:
        lf = lf.join(Typhoon, Typhoon.id == Landfall.typhoon_id)
        if min_year is not None:
            lf = lf.where(Typhoon.season_year >= min_year)
        if max_year is not None:
            lf = lf.where(Typhoon.season_year <= max_year)
    lf_sub = lf.subquery()

    imp = (
        select(TyphoonRegionImpact.admin_region_id,
               func.count(func.distinct(TyphoonRegionImpact.typhoon_id)).label("impact_count"))
        .group_by(TyphoonRegionImpact.admin_region_id)
        .subquery()
    )

    stmt = (
        select(AdminRegion.id, AdminRegion.name, AdminRegion.country,
               func.coalesce(lf_sub.c.landfall_count, 0),
               func.coalesce(imp.c.impact_count, 0))
        .select_from(AdminRegion)
        .outerjoin(lf_sub, lf_sub.c.admin_region_id == AdminRegion.id)
        .outerjoin(imp, imp.c.admin_region_id == AdminRegion.id)
        .where(AdminRegion.admin_level == level)
    )
    if country:
        c = country.strip()
        stmt = stmt.where(
            (func.upper(AdminRegion.iso_a2) == c.upper()) | (AdminRegion.country == c)
        )
    rows = session.execute(stmt).all()
    out = [
        {"admin_region_id": rid, "name": name, "country": ctry,
         "landfall_count": lfc, "impact_count": impc}
        for rid, name, ctry, lfc, impc in rows
        if lfc or impc  # only regions actually touched
    ]
    out.sort(key=lambda r: (r["landfall_count"], r["impact_count"]), reverse=True)
    return out


@router.get("/landfall-geojson")
def landfall_geojson(
    session: Session = Depends(get_session),
    level: int = Query(0, ge=0, le=1),
    bbox: str | None = Query(None, description="minLon,minLat,maxLon,maxLat"),
):
    """Choropleth-ready FeatureCollection of admin polygons, each carrying its
    landfall_count / impact_count. Drives the Leaflet choropleth directly."""
    lf_sub = (
        select(Landfall.admin_region_id, func.count().label("landfall_count"))
        .group_by(Landfall.admin_region_id).subquery()
    )
    imp_sub = (
        select(TyphoonRegionImpact.admin_region_id,
               func.count(func.distinct(TyphoonRegionImpact.typhoon_id)).label("impact_count"))
        .group_by(TyphoonRegionImpact.admin_region_id).subquery()
    )
    stmt = (
        select(AdminRegion.id, AdminRegion.name, AdminRegion.country,
               func.coalesce(lf_sub.c.landfall_count, 0),
               func.coalesce(imp_sub.c.impact_count, 0),
               func.ST_AsGeoJSON(AdminRegion.geom))
        .select_from(AdminRegion)
        .outerjoin(lf_sub, lf_sub.c.admin_region_id == AdminRegion.id)
        .outerjoin(imp_sub, imp_sub.c.admin_region_id == AdminRegion.id)
        .where(AdminRegion.admin_level == level)
    )
    if bbox:
        minx, miny, maxx, maxy = (float(x) for x in bbox.split(","))
        env = func.ST_MakeEnvelope(minx, miny, maxx, maxy, 4326)
        stmt = stmt.where(func.ST_Intersects(AdminRegion.geom, env))

    feats = []
    max_lf = 0
    for rid, name, ctry, lfc, impc, geo in session.execute(stmt).all():
        if not geo:
            continue
        max_lf = max(max_lf, lfc)
        feats.append({
            "type": "Feature", "geometry": json.loads(geo),
            "properties": {"id": rid, "name": name, "country": ctry,
                           "landfall_count": lfc, "impact_count": impc},
        })
    return {"type": "FeatureCollection", "features": feats,
            "properties": {"level": level, "max_landfall_count": max_lf}}
