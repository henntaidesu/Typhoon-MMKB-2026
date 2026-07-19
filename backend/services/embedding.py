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


# Human-readable phrasing for the disaster_type vocabulary, so a storm's vector
# carries the *words* a user would search with ("flooding", "landslide") rather
# than the internal enum token.
_DISASTER_WORDS = {
    "flood": "flooding, inundation, 洪水, 浸水",
    "landslide": "landslide, mudslide, 土砂崩れ, 滑坡",
    "storm_surge": "storm surge, coastal inundation, 高潮, 风暴潮",
    "wind_impact": "destructive wind damage, 暴風被害, 大风灾害",
    "rain": "torrential rain, 大雨, 暴雨",
    "casualty": "casualties, deaths and injuries, 死傷者, 人员伤亡",
    "infrastructure": "infrastructure damage, power outage, 停電, 基础设施损毁",
    "agriculture": "crop and agricultural loss, 農業被害, 农业损失",
}


def typhoon_summary(t, ctx: dict | None = None) -> str:
    """Compose a readable multilingual description of a typhoon for embedding.

    The identity half (name / season / intensity) makes name lookups work. The
    ``ctx`` half — where it made landfall, which regions it hit, what secondary
    disasters and public warnings it produced — is what makes *damage-flavoured*
    queries ("severe flooding and landslides", "死者が多かった台風") rank
    meaningfully instead of returning near-random storms. Without it a typhoon
    vector encodes nothing a user actually searches for.

    ``ctx`` is built in bulk by crawler/embed.py (see `_typhoon_context`); when
    omitted the summary degrades to the identity half only.
    """
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

    ctx = ctx or {}
    if ctx.get("landfall_regions"):
        parts.append("Made landfall in " + ", ".join(ctx["landfall_regions"]) + ".")
    if ctx.get("regions"):
        parts.append("Affected " + ", ".join(ctx["regions"]) + ".")
    if ctx.get("disaster_types"):
        words = [_DISASTER_WORDS.get(d, d) for d in ctx["disaster_types"]]
        parts.append("Secondary disasters: " + "; ".join(words) + ".")
    if ctx.get("casualties"):
        parts.append(f"About {ctx['casualties']} casualties reported. 死傷者・人员伤亡。")
    if ctx.get("economic_loss_usd"):
        parts.append(f"Economic loss around {int(ctx['economic_loss_usd'])} USD.")
    if ctx.get("public_categories"):
        parts.append("Public warnings issued about " + ", ".join(ctx["public_categories"]) + ".")
    if ctx.get("snippets"):
        parts.append(" ".join(ctx["snippets"]))
    return " ".join(p for p in parts if p)


def disaster_summary(d) -> str:
    parts = [
        f"{d.disaster_type} secondary disaster caused by the typhoon.",
        d.description or "",
        f"{d.casualties} casualties." if d.casualties else "",
        f"Economic loss {d.economic_loss_usd} USD." if d.economic_loss_usd else "",
    ]
    return " ".join(p for p in parts if p)


def public_info_summary(p) -> str:
    """Readable description of a piece of public information (公共情报) for
    embedding — the announcement's kind, hazard, authority and text."""
    parts = [
        f"Public {p.info_type} issued about the typhoon.",
        f"{p.agency}." if getattr(p, "agency", None) else "",
        f"Hazard: {p.category}." if getattr(p, "category", None) else "",
        f"Severity {p.severity}." if getattr(p, "severity", None) else "",
        p.title or "",
        p.body or "",
        f"Area: {p.region_name}." if getattr(p, "region_name", None) else "",
    ]
    return " ".join(p_ for p_ in parts if p_)


assert EMBEDDING_DIM == 384, "config EMBEDDING_DIM must match the model output dim"
