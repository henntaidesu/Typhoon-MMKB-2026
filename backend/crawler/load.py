"""DB loading helpers: upsert parsed records into the knowledge base via ORM.

Kept separate from the source parsers so parsing can be tested without a DB.
All spatial values are written as WKT 'SRID=4326;POINT(lon lat)' which
geoalchemy2 accepts directly.
"""
from __future__ import annotations

import os
import re
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
    AdminRegion, AffectedRegion, MediaAsset, PublicInfo, SecondaryDisaster,
    Typhoon, TrackPoint,
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
    # Match the English name (lower-cased) OR the localized CN/JP name verbatim,
    # so a bulletin that quotes 台风“巴威” / 台風「マーワー」 resolves as well as an
    # English "Typhoon Bavi". The English column is the common case; the local
    # columns catch CN/JP official bulletins (NMC / MEM / 消防庁).
    orig = name.split("-")[0].strip()
    stmt = select(Typhoon).where(
        (func.lower(Typhoon.name) == base)
        | (Typhoon.name_cn == orig)
        | (Typhoon.name_jp == orig)
    )
    if year:
        stmt = stmt.where(Typhoon.season_year == year)
    return session.scalar(stmt)


# How far outside a typhoon's [start, end] window a secondary disaster can still
# be attributed to it: storms build a day or two before, and floods / landslides
# / casualty tolls keep accruing for up to a week after the storm has passed.
_PAD_BEFORE = timedelta(days=2)
_PAD_AFTER = timedelta(days=7)
# Max angular distance (degrees, ~110 km each) from a typhoon's observed track
# for a located-but-unnamed warning to be tied to it.
_MAX_MATCH_DEG = 3.0
# Looser gate applied to a record that already matched by NAME. Storm names are
# reused across basins in the same season (GDACS's East-Pacific "DORA-23" and
# the West-Pacific typhoon Dora 2308), so a name match alone can be off by half
# the planet; a report's stated location may legitimately sit a few hundred km
# off the track, but never an ocean away.
_MAX_NAME_MATCH_DEG = 10.0


def _track_distance_expr(lat: float, lon: float):
    """Angular distance (degrees) from (lat, lon) to a track_point, wrap-safe.

    Track longitudes are stored in the 0-360 convention the agencies use (a
    storm crossing the dateline continues to 185, 190...), while the disaster
    feeds report -180..180. Subtracting them raw makes a neighbouring point look
    ~360 deg away, so the query longitude is folded into 0-360 first and the
    remaining seam closed by taking the shorter way round the circle. Done in
    plain SQL arithmetic rather than PostGIS precisely because the two
    conventions cannot be mixed inside one planar geometry."""
    lon360 = lon % 360  # normalize the Python-side value into the track's frame
    raw = func.abs(TrackPoint.lon - lon360)
    dlon = func.least(raw, func.abs(360.0 - raw))  # shorter arc across the seam
    dlat = func.abs(TrackPoint.lat - lat)
    return func.sqrt(dlon * dlon + dlat * dlat)


def _nearest_track_deg(session: Session, typhoon_ids: list[int], lat, lon):
    """(typhoon_id, degrees to its closest observed fix), nearest first."""
    if not typhoon_ids or lat is None or lon is None:
        return []
    d = func.min(_track_distance_expr(lat, lon)).label("d")
    return session.execute(
        select(TrackPoint.typhoon_id, d)
        .where(TrackPoint.typhoon_id.in_(typhoon_ids))
        .group_by(TrackPoint.typhoon_id)
        .order_by(d)
    ).all()


def _near_track(session: Session, typhoon_id: int, lat, lon, max_deg: float) -> bool:
    """Whether a located record plausibly belongs to this storm at all."""
    rows = _nearest_track_deg(session, [typhoon_id], lat, lon)
    return bool(rows) and rows[0][1] is not None and rows[0][1] <= max_deg


def _match_typhoon_by_time_space(
    session: Session, event_time, lat, lon, year: int | None
) -> Typhoon | None:
    """Attribute an unnamed official warning/bulletin to whichever typhoon was
    active near that place and time — used for feeds (NMC 预警) that don't quote a
    storm name. Prefers the storm whose track passes nearest the point; falls
    back to the one whose window is temporally closest."""
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

    # When the record is located, the spatial test is authoritative: it must sit
    # near a typhoon's observed track, else it isn't that storm's disaster (a
    # located warning far from every track is rejected, NOT matched by time).
    # Measured against the track itself rather than the derived impact corridor:
    # the corridor is a coarse buffer that some storms inflate to tens of degrees.
    if lat is not None and lon is not None:
        rows = _nearest_track_deg(session, [c.id for c in cands], lat, lon)
        if rows and rows[0][1] is not None and rows[0][1] <= _MAX_MATCH_DEG:
            return session.get(Typhoon, rows[0][0])
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
    time/space proximity.

    A name match is confirmed against the track when the record is located:
    tropical-cyclone names are recycled across basins within one season, so
    "Dora" alone cannot distinguish the West-Pacific typhoon from the
    East-Pacific hurricane of the same year. An implausible name match falls
    through to the time/space path rather than being trusted."""
    intl_id = getattr(r, "intl_id", None)
    if intl_id:
        obj = session.scalar(select(Typhoon).where(Typhoon.intl_id == intl_id))
        if obj is not None:
            return obj
    obj = _match_typhoon(session, r.typhoon_name, r.season_year)
    if obj is not None:
        if r.lat is None or r.lon is None:
            return obj
        if _near_track(session, obj.id, r.lat, r.lon, _MAX_NAME_MATCH_DEG):
            return obj
    # A self-identifying cyclone whose name we could not resolve is simply not a
    # storm in this West-Pacific KB. Guessing by time/space here is what filled
    # the KB with Atlantic and South-Pacific systems.
    if getattr(r, "named_event", False):
        return None
    return _match_typhoon_by_time_space(
        session, r.event_time, r.lat, r.lon, r.season_year
    )


def _disaster_exists(session: Session, typhoon_id: int, r) -> bool:
    """Idempotency guard so re-ingesting the same feed doesn't duplicate rows.

    A source_url identifies one real-world event, so it is matched *across*
    typhoons: the name/time-space resolver can otherwise attach the same GDACS
    report to two storms, and the KB would carry the event twice."""
    url = getattr(r, "source_url", None)
    if url:
        stmt = select(SecondaryDisaster.id).where(
            SecondaryDisaster.source == r.source,
            SecondaryDisaster.source_url == url,
        )
    else:
        stmt = select(SecondaryDisaster.id).where(
            SecondaryDisaster.typhoon_id == typhoon_id,
            SecondaryDisaster.source == r.source,
            SecondaryDisaster.description == r.description,
        )
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
                region_name=getattr(r, "region_name", None),
            ))
            n += 1
        session.commit()
    return n


def _public_info_exists(session: Session, typhoon_id: int, r) -> bool:
    """Idempotency guard so re-ingesting the same feed doesn't duplicate rows."""
    stmt = select(PublicInfo.id).where(
        PublicInfo.typhoon_id == typhoon_id,
        PublicInfo.source == r.source,
    )
    url = getattr(r, "source_url", None)
    if url:
        stmt = stmt.where(PublicInfo.source_url == url)
    else:
        stmt = stmt.where(PublicInfo.body == r.description)
    return session.scalar(stmt) is not None


# Feeds that put the headline in the body with a "[发布方]" prefix and leave the
# title empty (中央气象台预警 does this for every row).
_BODY_PREFIX = re.compile(r"^\s*\[[^\]]{1,24}\]\s*")


def _public_title(r) -> str | None:
    """A displayable headline, derived from the body when the feed omits one.

    A blank title renders as an empty row in the search results, so the first
    line of the body — which for these feeds *is* the headline — stands in."""
    title = (getattr(r, "title", None) or "").strip()
    if title:
        return title
    body = (r.description or "").strip()
    if not body:
        return None
    return _BODY_PREFIX.sub("", body.split("\n", 1)[0]).strip()[:200] or None


def load_public_info(records) -> int:
    """Match public-information records (公共情报: warnings / advisories /
    evacuation / news) to KB typhoons and insert PublicInfo rows. Uses the same
    intl_id -> name -> time/space resolution as disasters. Idempotent."""
    n = 0
    with SessionLocal() as session:
        for r in records:
            t = _resolve_typhoon(session, r)
            if t is None:
                continue  # can't tie it to a WP typhoon in our KB
            if _public_info_exists(session, t.id, r):
                continue
            geom = _wkt_point(r.lon, r.lat) if (r.lon is not None and r.lat is not None) else None
            session.add(PublicInfo(
                typhoon_id=t.id,
                info_type=r.info_type,
                category=getattr(r, "category", None),
                agency=getattr(r, "agency", None) or r.source,
                severity=getattr(r, "severity", None),
                title=_public_title(r),
                body=r.description,
                geom=geom, lat=r.lat, lon=r.lon,
                publish_time=r.event_time,
                region_name=getattr(r, "region_name", None),
                source=r.source, source_url=r.source_url,
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


# --- Reference administrative boundaries (Natural Earth) ---------------------
def load_admin_regions(records) -> int:
    """Upsert Natural Earth admin regions (countries + provinces) into the
    reference table. Keyed on ne_id, so re-running only updates. Returns count."""
    n = 0
    with SessionLocal() as session:
        for r in records:
            obj = session.scalar(select(AdminRegion).where(AdminRegion.ne_id == r.ne_id))
            if obj is None:
                obj = AdminRegion(ne_id=r.ne_id)
                session.add(obj)
            obj.name = r.name
            obj.name_local = r.name_local
            obj.iso_a2 = r.iso_a2
            obj.iso_a3 = r.iso_a3
            obj.admin_level = r.admin_level
            obj.country = r.country
            obj.parent_name = getattr(r, "parent_name", None)
            obj.geom = f"SRID={SRID};{r.wkt}"
            n += 1
        session.commit()
    return n


def admin_region_count() -> int:
    with SessionLocal() as session:
        return session.scalar(select(func.count()).select_from(AdminRegion)) or 0
