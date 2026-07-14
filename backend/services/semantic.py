"""Semantic associative search over the knowledge base using pgvector.

Query text -> embedding -> ORM `.cosine_distance()` ranking. This is the
'意味的選択' (semantic selection) query type; combined with spatio-temporal
filters it realizes the '意味的結合' (semantic join) required by the course.
"""
from __future__ import annotations

from sqlalchemy import Select, select, text
from sqlalchemy.orm import Session

from models import PublicInfo, SecondaryDisaster, Typhoon
from services.embedding import embed

# IVFFlat lists=100; pgvector suggests probes ≈ sqrt(lists) for good recall.
# Set per-session before each vector search so we scan enough lists to fill Top-K.
_PROBES = 10


def _tune(session: Session) -> None:
    try:
        session.execute(text(f"SET ivfflat.probes = {_PROBES}"))
    except Exception:  # noqa: BLE001 — recall tuning is best-effort
        pass


def semantic_typhoons(session: Session, query: str, k: int = 10) -> list[tuple[Typhoon, float]]:
    _tune(session)
    qvec = embed(query)
    dist = Typhoon.embedding.cosine_distance(qvec).label("distance")
    stmt: Select = (
        select(Typhoon, dist)
        .where(Typhoon.embedding.isnot(None))
        .order_by(dist)
        .limit(k)
    )
    return [(row[0], float(row[1])) for row in session.execute(stmt).all()]


def semantic_disasters(session: Session, query: str, k: int = 10) -> list[tuple[SecondaryDisaster, float]]:
    _tune(session)
    qvec = embed(query)
    dist = SecondaryDisaster.embedding.cosine_distance(qvec).label("distance")
    stmt = (
        select(SecondaryDisaster, dist)
        .where(SecondaryDisaster.embedding.isnot(None))
        .order_by(dist)
        .limit(k)
    )
    return [(row[0], float(row[1])) for row in session.execute(stmt).all()]


def semantic_public_info(session: Session, query: str, k: int = 10) -> list[tuple[PublicInfo, float]]:
    """Semantic selection over 公共情报 (warnings / advisories / news)."""
    _tune(session)
    qvec = embed(query)
    dist = PublicInfo.embedding.cosine_distance(qvec).label("distance")
    stmt = (
        select(PublicInfo, dist)
        .where(PublicInfo.embedding.isnot(None))
        .order_by(dist)
        .limit(k)
    )
    return [(row[0], float(row[1])) for row in session.execute(stmt).all()]
