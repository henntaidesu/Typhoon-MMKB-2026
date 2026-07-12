"""Semantic layer — turns knowledge units into 384-dim vectors.

Uses a multilingual sentence-transformer so Japanese / English / Chinese text
all map into ONE semantic space (this is the engineering realization of the
course's "意味計算 / semantic associative search").

The model is loaded lazily and cached, so importing this module is cheap.
"""
from __future__ import annotations

import os
import sys
import threading

_HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.abspath(os.path.join(_HERE, "..")))

from config import EMBEDDING_DIM, EMBEDDING_MODEL  # noqa: E402

_model = None
_lock = threading.Lock()


def get_model():
    global _model
    if _model is None:
        with _lock:
            if _model is None:
                from sentence_transformers import SentenceTransformer
                print(f"[embedding] loading {EMBEDDING_MODEL} ...")
                _model = SentenceTransformer(EMBEDDING_MODEL)
    return _model


def embed(text: str) -> list[float]:
    """Embed a single string into a normalized 384-dim vector."""
    vec = get_model().encode(text, normalize_embeddings=True)
    return vec.tolist()


def embed_many(texts: list[str]) -> list[list[float]]:
    vecs = get_model().encode(texts, normalize_embeddings=True, batch_size=32)
    return [v.tolist() for v in vecs]


def typhoon_summary(t) -> str:
    """Compose a readable multilingual description of a typhoon for embedding."""
    parts = [
        f"Typhoon {t.name or t.intl_id} ({t.intl_id}), West Pacific, {t.season_year}.",
        f"Category {t.category}." if t.category else "",
        f"Peak wind {t.max_wind_kt} kt." if t.max_wind_kt else "",
        f"Minimum pressure {t.min_pressure_hpa} hPa." if t.min_pressure_hpa else "",
    ]
    if getattr(t, "name_jp", None):
        parts.append(f"台風{t.name_jp}。")
    if getattr(t, "name_cn", None):
        parts.append(f"台风{t.name_cn}。")
    return " ".join(p for p in parts if p)


def disaster_summary(d) -> str:
    parts = [
        f"{d.disaster_type} secondary disaster caused by the typhoon.",
        d.description or "",
        f"{d.casualties} casualties." if d.casualties else "",
        f"Economic loss {d.economic_loss_usd} USD." if d.economic_loss_usd else "",
    ]
    return " ".join(p for p in parts if p)


assert EMBEDDING_DIM == 384, "config EMBEDDING_DIM must match the model output dim"
