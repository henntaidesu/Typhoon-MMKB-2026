"""Geographic impact enrichment — derive which countries/provinces each typhoon
affected and where it made landfall, from the track + Natural Earth boundaries.

Populates two derived tables (idempotent, delete-then-recompute per typhoon,
same pattern as load._rebuild_region_from_points):
  - typhoon_region_impact : one row per (typhoon, admin_region) it affected
  - landfall              : one row per discrete sea→land crossing

Requires admin_region to be loaded first (crawler/sources/naturalearth.py +
load.load_admin_regions). Run standalone:
    python crawler/enrich.py            # incremental (skip already-enriched)
    python crawler/enrich.py --force    # recompute all
"""
from __future__ import annotations

import os
import sys

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session, aliased

_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from config import SRID  # noqa: E402
from db import SessionLocal  # noqa: E402
from models import AdminRegion, Landfall, Typhoon, TrackPoint, TyphoonRegionImpact  # noqa: E402

# Primary-track preference — a typhoon may carry several agencies' tracks; we
# enrich ONE so landfalls/impacts aren't counted 2-3×. Mirrors the same list in
# routers/typhoons.py (kept local to avoid a router import from the crawler).
_AGENCY_PRIORITY = ["CMA", "JMA", "JTWC", "IBTrACS"]

# Bounding-box padding (degrees) when prefiltering candidate regions.
_BBOX_PAD = 1.5
# Corridor half-width (degrees) — matches load._rebuild_region_from_points'
# 1° track buffer, so within_corridor ⟺ distance ≤ this.
_CORRIDOR_DEG = 1.0
# Max distance (degrees) a fix counts as "near" a region for max-wind/time stats.
_NEAR_DEG = 1.0


def _primary_agency(pts) -> str | None:
    agencies = {p.agency for p in pts if p.agency}
    for a in _AGENCY_PRIORITY:
        if a in agencies:
            return a
    return next(iter(agencies)) if agencies else None


def _line_wkt(coords: list[tuple[float, float]]) -> str | None:
    """EWKT LINESTRING from (lon,lat) pairs, dropping consecutive duplicates."""
    uniq: list[tuple[float, float]] = []
    for c in coords:
        if not uniq or uniq[-1] != c:
            uniq.append(c)
    if len(uniq) < 2:
        return None
    body = ", ".join(f"{lon} {lat}" for lon, lat in uniq)
    return f"SRID={SRID};LINESTRING({body})"


def compute_impacts_for(session: Session, typhoon: Typhoon) -> int:
    """Recompute region impacts + landfalls for one typhoon. Returns #landfalls."""
    # Idempotent: clear this typhoon's derived rows first.
    typhoon.region_impacts.clear()
    typhoon.landfalls.clear()
    session.flush()

    all_pts = list(typhoon.track_points)
    primary = _primary_agency(all_pts)
    if primary is None:
        return 0
    pts = sorted((p for p in all_pts if p.agency == primary), key=lambda p: p.obs_time)
    if len(pts) < 2:
        return 0

    line_wkt = _line_wkt([(p.lon, p.lat) for p in pts])
    if line_wkt is None:
        return 0

    # --- Bbox prefilter: candidate admin regions intersecting the track envelope
    lons = [p.lon for p in pts]
    lats = [p.lat for p in pts]
    env = func.ST_MakeEnvelope(
        min(lons) - _BBOX_PAD, min(lats) - _BBOX_PAD,
        max(lons) + _BBOX_PAD, max(lats) + _BBOX_PAD, SRID,
    )
    cand = session.execute(
        select(AdminRegion.id, AdminRegion.admin_level, AdminRegion.name, AdminRegion.country)
        .where(func.ST_Intersects(AdminRegion.geom, env))
    ).all()
    if not cand:
        return 0
    cand_ids = [c.id for c in cand]
    cand_meta = {c.id: c for c in cand}
    cand0_ids = [c.id for c in cand if c.admin_level == 0]
    cand1_ids = [c.id for c in cand if c.admin_level == 1]

    # --- Pass 1: line-based passed_over + min distance, per candidate region.
    line = func.ST_GeomFromEWKT(line_wkt)
    impacts: dict[int, dict] = {}
    for rid, crossed, dist in session.execute(
        select(AdminRegion.id,
               func.ST_Intersects(AdminRegion.geom, line),
               func.ST_Distance(AdminRegion.geom, line))
        .where(AdminRegion.id.in_(cand_ids))
    ).all():
        impacts[rid] = {
            "passed_over": bool(crossed),
            "min_distance_deg": float(dist) if dist is not None else None,
            "within_corridor": dist is not None and dist <= _CORRIDOR_DEG,
            "max_wind_kt": None, "first_time": None, "last_time": None,
            "landfall": False, "landfall_time": None,
        }

    # --- Pass 2: point-level max wind + time bounds for fixes near each region.
    tp = aliased(TrackPoint)
    for rid, maxw, tmin, tmax in session.execute(
        select(AdminRegion.id, func.max(tp.wind_kt), func.min(tp.obs_time), func.max(tp.obs_time))
        .select_from(AdminRegion)
        .join(tp, func.ST_DWithin(AdminRegion.geom, tp.geom, _NEAR_DEG))
        .where(AdminRegion.id.in_(cand_ids),
               tp.typhoon_id == typhoon.id, tp.agency == primary)
        .group_by(AdminRegion.id)
    ).all():
        if rid in impacts:
            impacts[rid]["max_wind_kt"] = float(maxw) if maxw is not None else None
            impacts[rid]["first_time"] = tmin
            impacts[rid]["last_time"] = tmax

    # --- Landfall detection: ordered fixes tagged with containing admin-0 country.
    n_landfalls = 0
    if cand0_ids:
        walk = session.execute(
            select(tp.obs_time, tp.lon, tp.lat, tp.wind_kt, tp.pressure_hpa, tp.grade,
                   AdminRegion.id)
            .select_from(tp)
            .outerjoin(AdminRegion,
                       and_(AdminRegion.admin_level == 0,
                            AdminRegion.id.in_(cand0_ids),
                            func.ST_Contains(AdminRegion.geom, tp.geom)))
            .where(tp.typhoon_id == typhoon.id, tp.agency == primary)
            .order_by(tp.obs_time)
        ).all()

        prev_country: int | None = None
        for row in walk:
            country_id = row[6]
            # outside→inside transition = a landfall in `country_id`.
            if country_id is not None and country_id != prev_country and prev_country is None:
                n_landfalls += _record_landfall(
                    session, typhoon, row, country_id, cand1_ids, cand_meta, impacts)
            prev_country = country_id

    # --- Persist impacts (only regions the storm actually affected).
    for rid, d in impacts.items():
        if not (d["passed_over"] or d["within_corridor"] or d["landfall"]):
            continue
        typhoon.region_impacts.append(TyphoonRegionImpact(
            admin_region_id=rid,
            within_corridor=d["within_corridor"], passed_over=d["passed_over"],
            landfall=d["landfall"], min_distance_deg=d["min_distance_deg"],
            max_wind_kt=d["max_wind_kt"], first_time=d["first_time"],
            last_time=d["last_time"], landfall_time=d["landfall_time"],
        ))
    session.flush()
    return n_landfalls


def _record_landfall(session, typhoon, row, country_id, cand1_ids, cand_meta, impacts) -> int:
    """Insert one Landfall for the crossing at track fix `row` into `country_id`;
    resolve the most-specific admin-1 province and snap the point to the coast."""
    obs_time, lon, lat, wind, pres, grade, _ = row
    b_pt = func.ST_GeomFromEWKT(f"SRID={SRID};POINT({lon} {lat})")

    # Snap the landfall marker to the country's coastline near the first land fix.
    snapped = session.execute(
        select(func.ST_X(func.ST_ClosestPoint(func.ST_Boundary(AdminRegion.geom), b_pt)),
               func.ST_Y(func.ST_ClosestPoint(func.ST_Boundary(AdminRegion.geom), b_pt)))
        .where(AdminRegion.id == country_id)
    ).first()
    lf_lon, lf_lat = (snapped[0], snapped[1]) if snapped and snapped[0] is not None else (lon, lat)

    # Most-specific region: an admin-1 province containing the fix, else the country.
    region_id = country_id
    if cand1_ids:
        a1 = session.scalar(
            select(AdminRegion.id)
            .where(AdminRegion.admin_level == 1, AdminRegion.id.in_(cand1_ids),
                   func.ST_Contains(AdminRegion.geom, b_pt))
            .limit(1)
        )
        if a1 is not None:
            region_id = a1

    meta = cand_meta.get(region_id)
    country_meta = cand_meta.get(country_id)
    country_name = (country_meta.country or country_meta.name) if country_meta else None

    typhoon.landfalls.append(Landfall(
        admin_region_id=region_id, country=country_name,
        landfall_time=obs_time, lat=lf_lat, lon=lf_lon,
        wind_kt=wind, pressure_hpa=pres, grade=grade,
        geom=f"SRID={SRID};POINT({lf_lon} {lf_lat})",
    ))
    # Flag the resolved region (and its country) as a landfall region.
    for rid in {region_id, country_id}:
        if rid in impacts:
            impacts[rid]["landfall"] = True
            if impacts[rid]["landfall_time"] is None:
                impacts[rid]["landfall_time"] = obs_time
    return 1


def backfill(force: bool = False) -> tuple[int, int]:
    """Enrich every typhoon (incrementally unless force). Returns
    (typhoons_processed, landfalls_written)."""
    n_ty = n_lf = 0
    with SessionLocal() as session:
        if session.scalar(select(func.count()).select_from(AdminRegion)) == 0:
            raise RuntimeError("admin_region 为空：请先运行 naturalearth 加载行政边界。")

        stmt = select(Typhoon.id)
        if not force:
            already = select(TyphoonRegionImpact.typhoon_id).distinct()
            stmt = stmt.where(Typhoon.id.notin_(already))
        ids = [r[0] for r in session.execute(stmt).all()]

        for tid in ids:
            typhoon = session.get(Typhoon, tid)
            if typhoon is None:
                continue
            n_lf += compute_impacts_for(session, typhoon)
            n_ty += 1
            if n_ty % 50 == 0:
                session.commit()
                print(f"[enrich] processed {n_ty}/{len(ids)} typhoons, {n_lf} landfalls")
        session.commit()
    return n_ty, n_lf


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="recompute all typhoons")
    args = ap.parse_args()
    nt, nlf = backfill(force=args.force)
    print(f"[enrich] done: {nt} typhoons, {nlf} landfalls")
