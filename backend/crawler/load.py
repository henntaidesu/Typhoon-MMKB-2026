"""DB loading helpers: upsert parsed records into the knowledge base via ORM.

Kept separate from the source parsers so parsing can be tested without a DB.
All spatial values are written as WKT 'SRID=4326;POINT(lon lat)' which
geoalchemy2 accepts directly.
"""
from __future__ import annotations

import os
import sys

from shapely.geometry import LineString, MultiPolygon
from shapely.geometry import mapping  # noqa: F401
from sqlalchemy import select
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


def load_disasters(records) -> int:
    """Match GDACS DisasterRec to KB typhoons and insert SecondaryDisaster rows."""
    n = 0
    with SessionLocal() as session:
        for r in records:
            t = _match_typhoon(session, r.typhoon_name, r.season_year)
            if t is None:
                continue  # not a WP typhoon in our KB
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


# func imported lazily to keep top clean
from sqlalchemy import func  # noqa: E402
