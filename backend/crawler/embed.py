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

from db import SessionLocal  # noqa: E402
from models import SecondaryDisaster, Typhoon  # noqa: E402
from services.embedding import (  # noqa: E402
    disaster_summary, embed_many, typhoon_summary,
)


def backfill(batch: int = 64) -> tuple[int, int]:
    nt = nd = 0
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
    return nt, nd


if __name__ == "__main__":
    nt, nd = backfill()
    print(f"[embed] embedded {nt} typhoons, {nd} disasters")
