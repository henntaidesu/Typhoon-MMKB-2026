"""Semantic associative search over the knowledge base using pgvector.

Query text -> embedding -> ORM `.cosine_distance()` ranking. This is the
'意味的選択' (semantic selection) query type; combined with spatio-temporal
filters it realizes the '意味的結合' (semantic join) required by the course.
"""
from __future__ import annotations

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from models import SecondaryDisaster, Typhoon
from services.embedding import embed


def semantic_typhoons(session: Session, query: str, k: int = 10) -> list[tuple[Typhoon, float]]:
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
    qvec = embed(query)
    dist = SecondaryDisaster.embedding.cosine_distance(qvec).label("distance")
    stmt = (
        select(SecondaryDisaster, dist)
        .where(SecondaryDisaster.embedding.isnot(None))
        .order_by(dist)
        .limit(k)
    )
    return [(row[0], float(row[1])) for row in session.execute(stmt).all()]
