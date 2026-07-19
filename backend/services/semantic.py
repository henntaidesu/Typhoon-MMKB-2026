"""Semantic associative search over the knowledge base using pgvector.

Query text -> embedding -> ORM `.cosine_distance()` ranking. This is the
'意味的選択' (semantic selection) query type; combined with spatio-temporal
filters it realizes the '意味的結合' (semantic join) required by the course.

Three things make the raw ranking usable rather than merely non-empty:

* **a relevance cutoff** — cosine distance has no floor, so an unfiltered Top-K
  always returns K rows even when nothing matches. Anything past
  ``DEFAULT_MAX_DISTANCE`` is noise and is dropped, so "no good match" is an
  answer the UI can show.
* **near-duplicate collapsing** — official bulletins are re-issued as 第4報 /
  第5報 with near-identical text, which otherwise fills the whole Top-K with one
  event.
* **an optional spatio-temporal scope** — the typhoon-id subquery from
  routers/search.py, which turns semantic selection into a semantic *join*.
"""
from __future__ import annotations

import re

from sqlalchemy import Select, and_, or_, select, text
from sqlalchemy.orm import Session

from models import PublicInfo, SecondaryDisaster, Typhoon
from services.embedding import embed

# IVFFlat lists=100; pgvector suggests probes ≈ sqrt(lists) for good recall.
# Set per-session before each vector search so we scan enough lists to fill Top-K.
_PROBES = 10

# Cosine-distance cutoff, calibrated by probing this KB with deliberately
# meaningful and deliberately irrelevant queries: meaningful ones top out around
# 0.55, irrelevant ones ("recipe for chocolate cake") bottom out around 0.75.
# 0.60 sits in that gap with headroom on both sides. Re-measure after any change
# to what goes into the embedded text.
DEFAULT_MAX_DISTANCE = 0.60

# Over-fetch factor before de-duplication, so collapsing near-identical bulletins
# still leaves a full page of results.
_OVERFETCH = 4


def _tune(session: Session) -> None:
    try:
        session.execute(text(f"SET ivfflat.probes = {_PROBES}"))
    except Exception:  # noqa: BLE001 — recall tuning is best-effort
        pass


def _dedup_key(*fields) -> str:
    """Collapse re-issued bulletins: whitespace-normalized head of the text.

    Fields are mixed-type on purpose — the typhoon id scopes the key so two
    storms' identically-worded bulletins don't collapse into one."""
    joined = " ".join(str(f) for f in fields if f is not None)
    return re.sub(r"\s+", " ", joined).strip()[:48].lower()


def _run(session: Session, model, query: str, k: int,
         max_distance: float | None, scope: Select | None,
         key_fields) -> list[tuple[object, float]]:
    """Shared Top-K vector scan with cutoff, scope and near-duplicate collapsing."""
    _tune(session)
    qvec = embed(query)
    dist = model.embedding.cosine_distance(qvec).label("distance")
    stmt: Select = select(model, dist).where(model.embedding.isnot(None))
    if scope is not None:
        stmt = stmt.where(model.typhoon_id.in_(scope))
    if max_distance is not None:
        stmt = stmt.where(model.embedding.cosine_distance(qvec) <= max_distance)
    stmt = stmt.order_by(dist).limit(k * _OVERFETCH)

    out: list[tuple[object, float]] = []
    seen: set[str] = set()
    for row in session.execute(stmt).all():
        obj = row[0]
        key = _dedup_key(*(getattr(obj, f, None) for f in key_fields))
        if key and key in seen:
            continue
        seen.add(key)
        out.append((obj, float(row[1])))
        if len(out) >= k:
            break
    return out


# --- Keyword arm -------------------------------------------------------------
# Embeddings are weakest exactly where users are most literal: a bare place name
# ("浙江", "甘肃") lands ~0.6 from everything and gets cut, even though the KB
# holds rows containing that very string. A substring match is strong, cheap
# evidence, so it runs alongside the vector scan and its hits rank first.
#
# It fires only for SHORT queries. On a long one the per-token AND degenerates
# into a bag-of-words match that hits almost everything — "destructive wind
# damage red alert" matches every storm whose summary carries the generic hazard
# vocabulary — and it would crowd out the vector ranking, which is precisely
# what handles long queries well.
#
# Row counts here are in the thousands, so a plain ILIKE scan is well under a
# millisecond; this would want a pg_trgm index at a larger scale.
_KEYWORD_MAX_TOKENS = 2
_KEYWORD_MAX_CHARS = 16
# Keyword hits never take more than this share of a page, so an over-matching
# substring cannot push the semantic ranking off the results entirely.
_KEYWORD_SHARE = 0.5


def _is_keyword_query(q: str) -> bool:
    return len(q) <= _KEYWORD_MAX_CHARS and len(q.split()) <= _KEYWORD_MAX_TOKENS


def _keyword_filter(query: str, cols):
    """ILIKE predicate: the whole query as a substring, or (for a two-word
    query) both tokens present somewhere in the row."""
    parts = query.split()
    terms = [query] if len(parts) <= 1 else parts
    return and_(*[or_(*[c.ilike(f"%{term}%") for c in cols]) for term in terms])


def keyword_hits(session: Session, model, query: str, k: int, cols,
                 scope: Select | None = None, id_col=None) -> list:
    q = query.strip()
    if not q or not _is_keyword_query(q):
        return []
    k = max(1, int(k * _KEYWORD_SHARE))
    stmt = select(model).where(_keyword_filter(q, cols))
    if scope is not None:
        stmt = stmt.where((id_col if id_col is not None else model.typhoon_id).in_(scope))
    out, seen = [], set()
    for obj in session.scalars(stmt.limit(k * _OVERFETCH)):
        key = _dedup_key(*(getattr(obj, c.key, None) for c in cols))
        if key and key in seen:
            continue
        seen.add(key)
        out.append(obj)
        if len(out) >= k:
            break
    return out


def semantic_typhoons(session: Session, query: str, k: int = 10,
                      max_distance: float | None = DEFAULT_MAX_DISTANCE,
                      scope: Select | None = None) -> list[tuple[Typhoon, float]]:
    _tune(session)
    qvec = embed(query)
    dist = Typhoon.embedding.cosine_distance(qvec).label("distance")
    stmt: Select = select(Typhoon, dist).where(Typhoon.embedding.isnot(None))
    if scope is not None:
        stmt = stmt.where(Typhoon.id.in_(scope))
    if max_distance is not None:
        stmt = stmt.where(Typhoon.embedding.cosine_distance(qvec) <= max_distance)
    stmt = stmt.order_by(dist).limit(k)
    return [(row[0], float(row[1])) for row in session.execute(stmt).all()]


def semantic_disasters(session: Session, query: str, k: int = 10,
                       max_distance: float | None = DEFAULT_MAX_DISTANCE,
                       scope: Select | None = None,
                       ) -> list[tuple[SecondaryDisaster, float]]:
    return _run(session, SecondaryDisaster, query, k,
                max_distance, scope, ("typhoon_id", "description"))


def semantic_public_info(session: Session, query: str, k: int = 10,
                         max_distance: float | None = DEFAULT_MAX_DISTANCE,
                         scope: Select | None = None,
                         ) -> list[tuple[PublicInfo, float]]:
    """Semantic selection over 公共情报 (warnings / advisories / news)."""
    return _run(session, PublicInfo, query, k,
                max_distance, scope, ("typhoon_id", "title", "body"))
