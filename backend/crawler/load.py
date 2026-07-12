"""DB loading helpers: upsert parsed records into the knowledge base via ORM.

Kept separate from the source parsers so parsing can be tested without a DB.
All spatial values are written as WKT 'SRID=4326;POINT(lon lat)' which
geoalchemy2 accepts directly.
"""
from __future__ import annotations

import os
import sys
from datetime import timedelta

from shapely.geometry import LineString, MultiPolygon
from shapely.geometry import mapping  # noqa: F401
from sqlalchemy import func, select
from sqlalchemy.orm import Session

_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from config import SRID  # noqa: E402
from db import SessionLocal  # noqa: E402
from models import (  # noqa: E402
    AffectedRegion, MediaAsset, SecondaryDisaster, Typhoon, TrackPoint,
)


def _wkt_point(lon: float, lat: float) -> str:
    return f"SRID={SRID};POINT({lon} {lat})"


def upsert_typhoon(session: Session, rec) -> Typhoon:
    """Insert or update a Typhoon + its track points from a TyphoonRec."""
    obj = session.scalar(select(Typhoon).where(Typhoon.intl_id == rec.intl_id))
    if obj is None:
        obj = Typhoon(intl_id=rec.intl_id)
        session.add(obj)
    obj.sid = rec.sid
    obj.name = rec.name
    obj.season_year = rec.season_year
    obj.category = rec.category
    obj.max_wind_kt = rec.max_wind_kt
    obj.min_pressure_hpa = rec.min_pressure_hpa
    obj.start_time = rec.start_time
    obj.end_time = rec.end_time
    obj.source = "IBTrACS"

    # Replace track points (idempotent re-ingest).
    obj.track_points.clear()
    session.flush()
    for t in rec.track:
        obj.track_points.append(TrackPoint(
            obs_time=t.obs_time, geom=_wkt_point(t.lon, t.lat),
            lat=t.lat, lon=t.lon, wind_kt=t.wind_kt, pressure_hpa=t.pressure_hpa,
            grade=t.grade, move_dir=t.move_dir, move_speed=t.move_speed,
        ))

    _rebuild_affected_region(session, obj, rec)
    return obj


def _rebuild_affected_region(session: Session, obj: Typhoon, rec) -> None:
    """Derive a coarse impact footprint = buffer around the track line."""
    obj.regions.clear()
    session.flush()
    pts = [(t.lon, t.lat) for t in rec.track]
    if len(pts) < 2:
        return
    line = LineString(pts)
    # ~1 degree buffer (~100 km) as a coarse wind-affected corridor.
    poly = line.buffer(1.0)
    mp = poly if isinstance(poly, MultiPolygon) else MultiPolygon([poly])
    obj.regions.append(AffectedRegion(
        region_name=f"{rec.name or rec.intl_id} corridor",
        geom=f"SRID={SRID};{mp.wkt}",
        impact_type="wind_corridor", source="derived",
    ))


def load_typhoons(records) -> int:
    """Upsert a list of TyphoonRec; returns count."""
    n = 0
    with SessionLocal() as session:
        for rec in records:
            upsert_typhoon(session, rec)
            n += 1
        session.commit()
    return n


def _strip_suffix(name: str) -> str:
    """'Doksuri-23' -> 'doksuri' for matching GDACS names to KB typhoons."""
    return name.split("-")[0].strip().lower()


def _match_typhoon(session: Session, name: str | None, year: int | None) -> Typhoon | None:
    if not name:
        return None
    base = _strip_suffix(name)
    stmt = select(Typhoon).where(func.lower(Typhoon.name) == base)
    if year:
        stmt = stmt.where(Typhoon.season_year == year)
    return session.scalar(stmt)


# How far outside a typhoon's [start, end] window a secondary disaster can still
# be attributed to it: storms build a day or two before, and floods / landslides
# / casualty tolls keep accruing for up to a week after the storm has passed.
_PAD_BEFORE = timedelta(days=2)
_PAD_AFTER = timedelta(days=7)
# Max distance (degrees, ~110 km) from a typhoon's impact corridor for a
# located-but-unnamed warning to be tied to it.
_MAX_MATCH_DEG = 3.0


def _match_typhoon_by_time_space(
    session: Session, event_time, lat, lon, year: int | None
) -> Typhoon | None:
    """Attribute an unnamed official warning/bulletin to whichever typhoon was
    active near that place and time — used for feeds (NMC 预警) that don't quote a
    storm name. Prefers the storm whose impact corridor is nearest the point;
    falls back to the one whose window is temporally closest."""
    if event_time is None:
        return None
    stmt = (
        select(Typhoon)
        .where(
            Typhoon.start_time.isnot(None),
            Typhoon.end_time.isnot(None),
            Typhoon.start_time <= event_time + _PAD_BEFORE,
            Typhoon.end_time >= event_time - _PAD_AFTER,
        )
    )
    if year:
        stmt = stmt.where(Typhoon.season_year == year)
    cands = session.scalars(stmt).all()
    if not cands:
        return None

    # When the record is located, the spatial test is authoritative: it must fall
    # within a typhoon's impact corridor, else it isn't that storm's disaster (a
    # located warning far from every corridor is rejected, NOT matched by time).
    if lat is not None and lon is not None:
        ids = [c.id for c in cands]
        pt = func.ST_GeomFromEWKT(_wkt_point(lon, lat))
        row = session.execute(
            select(AffectedRegion.typhoon_id,
                   func.ST_Distance(AffectedRegion.geom, pt).label("d"))
            .where(AffectedRegion.typhoon_id.in_(ids))
            .order_by("d").limit(1)
        ).first()
        if row is not None and row.d is not None and row.d <= _MAX_MATCH_DEG:
            return session.get(Typhoon, row.typhoon_id)
        return None

    # No location — a single active storm gets it; otherwise the temporally
    # closest one.
    if len(cands) == 1:
        return cands[0]
    return min(cands, key=lambda t: min(
        abs((event_time - t.start_time).total_seconds()),
        abs((event_time - t.end_time).total_seconds()),
    ))


def _resolve_typhoon(session: Session, r) -> Typhoon | None:
    """Try the strongest available link in order: exact WMO id -> storm name ->
    time/space proximity."""
    intl_id = getattr(r, "intl_id", None)
    if intl_id:
        obj = session.scalar(select(Typhoon).where(Typhoon.intl_id == intl_id))
        if obj is not None:
            return obj
    obj = _match_typhoon(session, r.typhoon_name, r.season_year)
    if obj is not None:
        return obj
    return _match_typhoon_by_time_space(
        session, r.event_time, r.lat, r.lon, r.season_year
    )


def _disaster_exists(session: Session, typhoon_id: int, r) -> bool:
    """Idempotency guard so re-ingesting the same feed doesn't duplicate rows."""
    stmt = select(SecondaryDisaster.id).where(
        SecondaryDisaster.typhoon_id == typhoon_id,
        SecondaryDisaster.source == r.source,
    )
    url = getattr(r, "source_url", None)
    if url:
        stmt = stmt.where(SecondaryDisaster.source_url == url)
    else:
        stmt = stmt.where(SecondaryDisaster.description == r.description)
    return session.scalar(stmt) is not None


def load_disasters(records) -> int:
    """Match secondary-disaster records (GDACS / ReliefWeb / NMC / MEM / FDMA /
    HKO / JMA) to KB typhoons and insert SecondaryDisaster rows. Idempotent."""
    n = 0
    with SessionLocal() as session:
        for r in records:
            t = _resolve_typhoon(session, r)
            if t is None:
                continue  # can't tie it to a WP typhoon in our KB
            if _disaster_exists(session, t.id, r):
                continue
            geom = _wkt_point(r.lon, r.lat) if (r.lon is not None and r.lat is not None) else None
            session.add(SecondaryDisaster(
                typhoon_id=t.id, disaster_type=r.disaster_type, geom=geom,
                lat=r.lat, lon=r.lon, event_time=r.event_time,
                casualties=r.casualties, economic_loss_usd=r.economic_loss_usd,
                description=r.description, source=r.source, source_url=r.source_url,
            ))
            n += 1
        session.commit()
    return n


def load_media_and_damage(intl_id: str, dt_result) -> None:
    """Attach a Digital Typhoon satellite image + optional damage record."""
    with SessionLocal() as session:
        t = session.scalar(select(Typhoon).where(Typhoon.intl_id == intl_id))
        if t is None:
            return
        if dt_result.image_url:
            exists = session.scalar(
                select(MediaAsset).where(MediaAsset.typhoon_id == t.id,
                                         MediaAsset.url == dt_result.image_url)
            )
            if not exists:
                session.add(MediaAsset(typhoon_id=t.id, kind="satellite",
                                       url=dt_result.image_url,
                                       caption=dt_result.title or "Digital Typhoon"))
        if dt_result.damage_text or dt_result.casualties:
            session.add(SecondaryDisaster(
                typhoon_id=t.id, disaster_type="casualty",
                event_time=t.end_time, casualties=dt_result.casualties,
                description=(dt_result.damage_text or "Damage reported"),
                source="DigitalTyphoon",
                source_url=f"http://agora.ex.nii.ac.jp/digital-typhoon/summary/wnp/s/{dt_result.dtid}.html.en",
            ))
        session.commit()


# --- Multi-agency actual-track loading (实况路径) -----------------------------
def _rebuild_region_from_points(session: Session, obj: Typhoon) -> None:
    """Coarse impact corridor derived from ALL observed points of the typhoon
    (any agency), ordered by time. Buffer ~1 degree (~100 km)."""
    obj.regions.clear()
    session.flush()
    pts = sorted(obj.track_points, key=lambda t: t.obs_time)
    coords = [(t.lon, t.lat) for t in pts]
    if len(coords) < 2:
        return
    poly = LineString(coords).buffer(1.0)
    mp = poly if isinstance(poly, MultiPolygon) else MultiPolygon([poly])
    obj.regions.append(AffectedRegion(
        region_name=f"{obj.name or obj.intl_id} corridor",
        geom=f"SRID={SRID};{mp.wkt}", impact_type="wind_corridor", source="derived",
    ))


def _apply_meta(obj: Typhoon, st, agency: str, authoritative: bool) -> None:
    """Fill typhoon core fields. Authoritative source (CMA) overwrites; others
    only fill values that are still missing, so they enrich without clobbering."""
    winds = [p.wind_kt for p in st.points if p.wind_kt is not None]
    press = [p.pressure_hpa for p in st.points if p.pressure_hpa is not None]
    times = [p.obs_time for p in st.points]
    maxw = max(winds) if winds else None
    minp = min(press) if press else None

    def put(attr, value):
        if value is None:
            return
        if authoritative or getattr(obj, attr) is None:
            setattr(obj, attr, value)

    put("name", st.name)
    put("season_year", st.season_year)
    put("category", st.category)
    put("max_wind_kt", maxw)
    put("min_pressure_hpa", minp)
    put("start_time", min(times) if times else None)
    put("end_time", max(times) if times else None)
    if authoritative or obj.source is None:
        obj.source = agency

    # Active state: the authoritative source (CMA) sets it definitively; a
    # non-authoritative agency may only upgrade an unknown/ended storm to active
    # (it still lists it), never override CMA's 'ended'.
    if st.active is not None:
        if authoritative:
            obj.is_active = st.active
        elif st.active and obj.is_active is not True:
            obj.is_active = True
        elif obj.is_active is None:
            obj.is_active = st.active


def agency_status_map(agency: str) -> dict[str, bool | None]:
    """intl_id -> is_active, for every typhoon that already has this agency's
    track points. Used to plan incremental crawls: entries present are already
    fetched; an is_active=True entry should still be refreshed."""
    with SessionLocal() as session:
        rows = session.execute(
            select(Typhoon.intl_id, Typhoon.is_active).where(
                Typhoon.id.in_(
                    select(TrackPoint.typhoon_id).where(TrackPoint.agency == agency).distinct()
                )
            )
        ).all()
    return {intl_id: is_active for intl_id, is_active in rows}


def cma_status_map() -> dict[str, bool | None]:
    return agency_status_map("CMA")


def agency_seasons(agency: str) -> set[int]:
    """Season years that already have at least one of this agency's points."""
    with SessionLocal() as session:
        rows = session.execute(
            select(Typhoon.season_year).where(
                Typhoon.id.in_(
                    select(TrackPoint.typhoon_id).where(TrackPoint.agency == agency).distinct()
                )
            ).distinct()
        ).all()
    return {r[0] for r in rows if r[0] is not None}


def resolve_intl_id(name: str | None, year: int | None) -> str | None:
    """Find an existing typhoon by name + season and return its WMO intl_id.
    Used to attach JTWC best-track (keyed by JTWC's own number) to the right
    storm by name."""
    if not name:
        return None
    with SessionLocal() as session:
        stmt = select(Typhoon.intl_id).where(func.lower(Typhoon.name) == name.lower())
        if year:
            stmt = stmt.where(Typhoon.season_year == year)
        return session.scalar(stmt)


def typhoon_exists(intl_id: str) -> bool:
    """Whether a typhoon with this intl_id is already in the KB. Used as the
    number-based fallback for old JTWC storms that carry no name."""
    if not intl_id:
        return False
    with SessionLocal() as session:
        return session.scalar(select(Typhoon.id).where(Typhoon.intl_id == intl_id)) is not None


def load_agency_storms(storms, agency: str, authoritative: bool = False) -> tuple[int, int]:
    """Upsert actual (实况) tracks from one agency. Only that agency's points are
    replaced, so multiple agencies' tracks coexist on the same typhoon.
    Returns (typhoons_touched, points_written)."""
    n_ty = n_pt = 0
    with SessionLocal() as session:
        for st in storms:
            obj = session.scalar(select(Typhoon).where(Typhoon.intl_id == st.intl_id))
            if obj is None:
                obj = Typhoon(intl_id=st.intl_id)
                session.add(obj)
            _apply_meta(obj, st, agency, authoritative)
            session.flush()

            # Replace only this agency's existing points (idempotent re-ingest).
            for tp in [t for t in obj.track_points if t.agency == agency]:
                obj.track_points.remove(tp)
            session.flush()
            for p in st.points:
                obj.track_points.append(TrackPoint(
                    agency=agency, obs_time=p.obs_time, geom=_wkt_point(p.lon, p.lat),
                    lat=p.lat, lon=p.lon, wind_kt=p.wind_kt, pressure_hpa=p.pressure_hpa,
                    grade=p.grade, move_dir=p.move_dir, move_speed=p.move_speed,
                ))
                n_pt += 1
            session.flush()
            _rebuild_region_from_points(session, obj)
            n_ty += 1
        session.commit()
    return n_ty, n_pt
