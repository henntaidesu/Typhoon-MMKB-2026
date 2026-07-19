"""Backfill semantic embeddings for typhoons and disasters that lack one.

Builds a readable multilingual summary per knowledge unit, embeds it with the
shared multilingual model, and writes the 384-dim vector back via ORM.

Run:  python crawler/embed.py
"""
from __future__ import annotations

import os
import sys

from sqlalchemy import func, select

_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from sqlalchemy import text  # noqa: E402

from sqlalchemy.orm import Session  # noqa: E402

from db import SessionLocal, engine  # noqa: E402
from models import (  # noqa: E402
    AdminRegion, Landfall, PublicInfo, SecondaryDisaster, Typhoon,
    TyphoonRegionImpact,
)
from services.embedding import (  # noqa: E402
    disaster_summary, embed_many, public_info_summary, typhoon_summary,
)

# IVFFlat indexes created by create_all() on an empty table are degenerate (no
# centroids → vector queries return 0 rows). Rebuilding them once data exists
# fixes recall. Cheap for our row counts, so we do it after every backfill.
_VECTOR_INDEXES = ("ix_typhoon_embedding", "ix_disaster_embedding", "ix_public_info_embedding")


def _reindex_vectors() -> None:
    for ix in _VECTOR_INDEXES:
        try:
            with engine.begin() as c:
                c.execute(text(f"REINDEX INDEX {ix}"))
        except Exception as e:  # noqa: BLE001 — best-effort; never abort a load
            print(f"[embed] reindex {ix} skipped: {e}")


# How much narrative text from a typhoon's own disaster/public-info rows gets
# folded into its summary. A few representative snippets is enough to colour the
# vector; dumping every bulletin would drown out the identity half.
_MAX_SNIPPETS = 6
_SNIPPET_CHARS = 160


def _typhoon_context(s: Session) -> dict[int, dict]:
    """Bulk-build the damage context for every typhoon in one pass.

    Done as a handful of grouped queries rather than per-typhoon lazy loads —
    with ~2k typhoons the ORM-relationship route would issue ~10k round trips.
    """
    ctx: dict[int, dict] = {}

    def slot(tid: int) -> dict:
        return ctx.setdefault(tid, {})

    # Landfall + affected admin regions (names only; the geometry is irrelevant here).
    for tid, name in s.execute(
        select(Landfall.typhoon_id, AdminRegion.name)
        .join(AdminRegion, Landfall.admin_region_id == AdminRegion.id)
        .distinct()
    ):
        if name:
            slot(tid).setdefault("landfall_regions", []).append(name)
    for tid, name in s.execute(
        select(TyphoonRegionImpact.typhoon_id, AdminRegion.name)
        .join(AdminRegion, TyphoonRegionImpact.admin_region_id == AdminRegion.id)
        .where(AdminRegion.admin_level == 0)
        .distinct()
    ):
        if name:
            slot(tid).setdefault("regions", []).append(name)

    # Secondary-disaster aggregates: which hazard kinds, how bad.
    for tid, dtype in s.execute(
        select(SecondaryDisaster.typhoon_id, SecondaryDisaster.disaster_type).distinct()
    ):
        if dtype:
            slot(tid).setdefault("disaster_types", []).append(dtype)
    for tid, cas, loss in s.execute(
        select(SecondaryDisaster.typhoon_id,
               func.sum(SecondaryDisaster.casualties),
               func.sum(SecondaryDisaster.economic_loss_usd))
        .group_by(SecondaryDisaster.typhoon_id)
    ):
        if cas:
            slot(tid)["casualties"] = int(cas)
        if loss:
            slot(tid)["economic_loss_usd"] = float(loss)

    # Public-information hazard categories (what authorities warned about).
    for tid, cat in s.execute(
        select(PublicInfo.typhoon_id, PublicInfo.category).distinct()
    ):
        if cat:
            slot(tid).setdefault("public_categories", []).append(cat)

    # A few narrative snippets so region/《被害》 wording reaches the vector.
    for tid, desc in s.execute(
        select(SecondaryDisaster.typhoon_id, SecondaryDisaster.description)
        .where(SecondaryDisaster.description.isnot(None))
        .order_by(SecondaryDisaster.typhoon_id, SecondaryDisaster.id)
    ):
        snips = slot(tid).setdefault("snippets", [])
        if len(snips) < _MAX_SNIPPETS:
            snips.append(desc[:_SNIPPET_CHARS])

    return ctx


def backfill(batch: int = 64, force: bool = False) -> tuple[int, int, int]:
    """Embed knowledge units that lack a vector.

    ``force=True`` re-embeds *every* typhoon instead of only the null ones —
    needed whenever `typhoon_summary`'s composition changes, since existing
    vectors would otherwise keep encoding the old (identity-only) text.
    """
    nt = nd = np = 0
    with SessionLocal() as s:
        tstmt = select(Typhoon)
        if not force:
            tstmt = tstmt.where(Typhoon.embedding.is_(None))
        typhoons = s.scalars(tstmt).all()
        tctx = _typhoon_context(s) if typhoons else {}
        for i in range(0, len(typhoons), batch):
            chunk = typhoons[i:i + batch]
            for t in chunk:
                t.summary_text = typhoon_summary(t, tctx.get(t.id))
            vecs = embed_many([t.summary_text for t in chunk])
            for t, v in zip(chunk, vecs):
                t.embedding = v
                nt += 1
            s.commit()

        disasters = s.scalars(
            select(SecondaryDisaster).where(SecondaryDisaster.embedding.is_(None))
        ).all()
        for i in range(0, len(disasters), batch):
            chunk = disasters[i:i + batch]
            vecs = embed_many([disaster_summary(d) for d in chunk])
            for d, v in zip(chunk, vecs):
                d.embedding = v
                nd += 1
            s.commit()

        infos = s.scalars(
            select(PublicInfo).where(PublicInfo.embedding.is_(None))
        ).all()
        for i in range(0, len(infos), batch):
            chunk = infos[i:i + batch]
            vecs = embed_many([public_info_summary(p) for p in chunk])
            for p, v in zip(chunk, vecs):
                p.embedding = v
                np += 1
            s.commit()

    if nt or nd or np:
        _reindex_vectors()  # keep IVFFlat indexes healthy after new vectors
    return nt, nd, np


if __name__ == "__main__":
    force = "--all" in sys.argv  # re-embed every typhoon (summary format changed)
    nt, nd, np_ = backfill(force=force)
    print(f"[embed] embedded {nt} typhoons, {nd} disasters, {np_} public-info")
