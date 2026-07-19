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
from models import (
    AdminRegion, Landfall, PublicInfo, SecondaryDisaster, Typhoon, TrackPoint,
    TyphoonRegionImpact,
)
from schemas import SemanticQuery, TyphoonBrief
from services.intent import detect_intent
from services.semantic import (
    DEFAULT_MAX_DISTANCE, keyword_hits, semantic_disasters, semantic_public_info,
    semantic_typhoons,
)

router = APIRouter(prefix="/search", tags=["search"])


def _bbox_env(bbox: str):
    minx, miny, maxx, maxy = (float(x) for x in bbox.split(","))
    return func.ST_MakeEnvelope(minx, miny, maxx, maxy, 4326)


def _scope_stmt(bbox: str | None, date_from, date_to):
    """Typhoon ids whose track passes the bbox inside the time window.

    Returns None when no spatio-temporal constraint was given, so callers can
    tell "no filter" apart from "filter matched nothing"."""
    conds = []
    if bbox:
        conds.append(func.ST_Intersects(TrackPoint.geom, _bbox_env(bbox)))
    if date_from:
        conds.append(TrackPoint.obs_time >= date_from)
    if date_to:
        conds.append(TrackPoint.obs_time <= date_to)
    if not conds:
        return None
    return select(TrackPoint.typhoon_id).where(and_(*conds)).distinct()


def _scope_ids(session: Session, bbox: str | None, date_from, date_to):
    """Same scope, resolved to a list of ids once.

    Kept as a materialized list rather than a subquery because the search fans
    out over three knowledge layers with two arms each — leaving it as a
    correlated subquery makes Postgres re-run the spatial scan over every track
    point six times per request (~300ms instead of ~65ms)."""
    stmt = _scope_stmt(bbox, date_from, date_to)
    if stmt is None:
        return None
    return session.scalars(stmt).all()


def _typhoon_row(t: Typhoon, distance: float | None) -> dict:
    return {**TyphoonBrief.model_validate(t).model_dump(),
            "distance": None if distance is None else round(distance, 4),
            "match": "semantic" if distance is not None else "keyword"}


def _disaster_row(d: SecondaryDisaster, distance: float | None) -> dict:
    return {"id": d.id, "typhoon_id": d.typhoon_id, "disaster_type": d.disaster_type,
            "description": d.description, "lat": d.lat, "lon": d.lon,
            "region_name": d.region_name, "source": d.source, "source_url": d.source_url,
            "distance": None if distance is None else round(distance, 4),
            "match": "semantic" if distance is not None else "keyword"}


def _public_row(p: PublicInfo, distance: float | None) -> dict:
    return {"id": p.id, "typhoon_id": p.typhoon_id, "info_type": p.info_type,
            "category": p.category, "agency": p.agency, "severity": p.severity,
            "title": _title_of(p), "description": p.body, "lat": p.lat, "lon": p.lon,
            "region_name": p.region_name, "source_url": p.source_url,
            "distance": None if distance is None else round(distance, 4),
            "match": "semantic" if distance is not None else "keyword"}


def _merge(keyword_rows: list[dict], semantic_rows: list[dict], k: int) -> list[dict]:
    """Keyword hits first — an exact substring beats a fuzzy neighbour — then the
    semantic ranking with anything already shown removed."""
    seen = {r["id"] for r in keyword_rows}
    return (keyword_rows + [r for r in semantic_rows if r["id"] not in seen])[:k]


def _title_of(p: PublicInfo) -> str | None:
    """Some feeds (NMC warnings) carry the headline in the body only — surface a
    usable label instead of letting the UI render a blank row."""
    if p.title and p.title.strip():
        return p.title
    body = (p.body or "").strip()
    return body.split("\n", 1)[0][:120] or None


@router.post("/semantic")
def search_semantic(body: SemanticQuery, session: Session = Depends(get_session)):
    """The search box's single entry point.

    Three behaviours, chosen from the query itself:

    * a structured lookup ("2019", "2306", "Hagibis") answers from the indexed
      columns — the embedding model has no useful opinion about a bare year;
    * everything else is ranked semantically over all three knowledge layers
      (typhoon / 受灾情报 / 公共情报) with a relevance cutoff;
    * passing bbox/date turns that ranking into a spatio-temporal semantic join.
    """
    intent, params = detect_intent(body.q)
    scope = _scope_ids(session, body.bbox, body.date_from, body.date_to)
    max_dist = DEFAULT_MAX_DISTANCE if body.max_distance is None else body.max_distance

    structured: list[dict] = []
    if intent != "semantic":
        stmt = select(Typhoon)
        if intent == "year":
            stmt = stmt.where(Typhoon.season_year == params["year"])
        elif intent == "intl_id":
            stmt = stmt.where(Typhoon.intl_id == params["intl_id"])
        elif intent == "name":
            like = f"%{params['name']}%"
            stmt = stmt.where(Typhoon.name.ilike(like) | Typhoon.name_cn.ilike(like)
                              | Typhoon.name_jp.ilike(like))
        if scope is not None:
            stmt = stmt.where(Typhoon.id.in_(scope))
        stmt = stmt.order_by(Typhoon.season_year.desc(), Typhoon.intl_id)
        structured = [{**TyphoonBrief.model_validate(t).model_dump(),
                       "distance": None, "match": "exact"}
                      for t in session.scalars(stmt)]

    # A structured lookup that found something is the complete answer; a bare
    # year or storm name is also too short to rank meaningfully, so the vector
    # search would only append unrelated storms under a confident heading. It
    # stays as the fallback for a name the KB doesn't carry ("Katrina").
    run_semantic = intent == "semantic" or not structured
    if run_semantic:
        # Two arms per layer: exact substring, then vector similarity. The
        # keyword arm is what makes a bare place name ("浙江") findable at all —
        # short queries sit past the cosine cutoff even when the KB holds rows
        # containing that exact string.
        typhoons = _merge(
            [_typhoon_row(t, None) for t in keyword_hits(
                session, Typhoon, body.q, body.k,
                [Typhoon.name, Typhoon.name_cn, Typhoon.name_jp, Typhoon.summary_text],
                scope, Typhoon.id)],
            [_typhoon_row(t, d) for t, d in
             semantic_typhoons(session, body.q, body.k, max_dist, scope)],
            body.k)
        disasters = _merge(
            [_disaster_row(d, None) for d in keyword_hits(
                session, SecondaryDisaster, body.q, body.k,
                [SecondaryDisaster.description, SecondaryDisaster.region_name], scope)],
            [_disaster_row(d, dist) for d, dist in
             semantic_disasters(session, body.q, body.k, max_dist, scope)],
            body.k)
        public_info = _merge(
            [_public_row(p, None) for p in keyword_hits(
                session, PublicInfo, body.q, body.k,
                [PublicInfo.title, PublicInfo.body, PublicInfo.region_name], scope)],
            [_public_row(p, dist) for p, dist in
             semantic_public_info(session, body.q, body.k, max_dist, scope)],
            body.k)
    else:
        typhoons = disasters = public_info = []

    # De-dupe the structured hits out of the semantic list so a name lookup
    # doesn't show the same storm twice.
    known = {t["id"] for t in structured}
    typhoons = [t for t in typhoons if t["id"] not in known]

    return {"query": body.q, "intent": intent,
            "scoped": scope is not None, "max_distance": max_dist,
            "structured": structured, "typhoons": typhoons,
            "disasters": disasters, "public_info": public_info}


@router.get("/spatiotemporal")
def search_spatiotemporal(
    session: Session = Depends(get_session),
    bbox: str = Query(..., description="minLon,minLat,maxLon,maxLat"),
    date_from: datetime | None = None,
    date_to: datetime | None = None,
):
    """Typhoons whose track passes through the bbox within the time window."""
    stmt = (
        select(Typhoon)
        .where(Typhoon.id.in_(_scope_stmt(bbox, date_from, date_to)))
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
    """Spatio-temporal selection first, then semantic ranking (semantic join).

    Same computation the POST /search/semantic endpoint performs when given a
    bbox; kept as a standalone GET so the join can be exercised on its own."""
    scope = _scope_ids(session, bbox, date_from, date_to)
    return [
        {**TyphoonBrief.model_validate(t).model_dump(), "distance": round(d, 4)}
        for t, d in semantic_typhoons(session, q, k, DEFAULT_MAX_DISTANCE, scope)
    ]


@router.get("/stats")
def stats(session: Session = Depends(get_session)):
    by_year = session.execute(
        select(Typhoon.season_year, func.count()).group_by(Typhoon.season_year).order_by(Typhoon.season_year)
    ).all()
    by_type = session.execute(
        select(SecondaryDisaster.disaster_type, func.count()).group_by(SecondaryDisaster.disaster_type)
    ).all()
    public_by_type = session.execute(
        select(PublicInfo.info_type, func.count()).group_by(PublicInfo.info_type)
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
        "public_info_by_type": [{"type": t, "count": c} for t, c in public_by_type],
        "top_countries": [{"country": n, "count": c} for n, c in top_countries],
        "total_typhoons": session.scalar(select(func.count()).select_from(Typhoon)),
        "total_disasters": session.scalar(select(func.count()).select_from(SecondaryDisaster)),
        "total_public_info": session.scalar(select(func.count()).select_from(PublicInfo)),
        "total_landfalls": session.scalar(select(func.count()).select_from(Landfall)),
    }
