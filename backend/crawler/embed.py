"""Backfill semantic embeddings for typhoons and disasters that lack one.

Builds a readable multilingual summary per knowledge unit, embeds it with the
shared multilingual model, and writes the 384-dim vector back via ORM.

Run:  python crawler/embed.py
"""
from __future__ import annotations

import os
import sys

from sqlalchemy import select

_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from sqlalchemy import text  # noqa: E402

from db import SessionLocal, engine  # noqa: E402
from models import PublicInfo, SecondaryDisaster, Typhoon  # noqa: E402
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


def backfill(batch: int = 64) -> tuple[int, int, int]:
    nt = nd = np = 0
    with SessionLocal() as s:
        typhoons = s.scalars(select(Typhoon).where(Typhoon.embedding.is_(None))).all()
        for i in range(0, len(typhoons), batch):
            chunk = typhoons[i:i + batch]
            for t in chunk:
                t.summary_text = typhoon_summary(t)
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
    nt, nd, np_ = backfill()
    print(f"[embed] embedded {nt} typhoons, {nd} disasters, {np_} public-info")
