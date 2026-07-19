"""Local gazetteer geocoder — resolve a place name mentioned in free text to a
coordinate, using ONLY the admin_region reference table already in the KB
(Natural Earth admin-0/1 + GADM admin-2 / 地级市).

This exists so news / bulletin text ("温州", "鹿児島", "Quang Ngai province") can
be pinned on the map without any external geocoding service: every candidate is
matched against the country / province / prefecture names we already loaded, and
the winning region's centroid is returned. Fully offline, no API key, consistent
with the PostGIS boundaries the rest of the app draws.

Matching rules:
  * both the English `name` and the localized `name_local` (中文/日本語) are tried,
    plus a suffix-stripped variant (温州市→温州, 鹿児島県→鹿児島, ...省/州/都/府...),
    so a headline that writes the bare stem still matches.
  * the MOST SPECIFIC hit wins: a 地级市 (admin_level 2) beats a province (1) beats
    a country (0); ties break on the longer matched string (more specific name).

The gazetteer is built once from the DB and cached in-process (rebuild with
`reload()` after loading new admin regions).
"""
from __future__ import annotations

import os
import sys
import threading
from dataclasses import dataclass

from sqlalchemy import func, select

_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from db import SessionLocal  # noqa: E402
from models import AdminRegion  # noqa: E402


# Administrative suffixes stripped to expose the bare stem for matching. Chinese
# / Japanese / Korean units first, then English words. Order matters only for the
# English multi-word forms (longest first).
_CJK_SUFFIXES = (
    "特别行政区", "自治州", "自治区", "自治县", "地区",
    "省", "市", "县", "區", "区", "州", "都", "府", "県", "縣", "道", "郡",
    "시", "군", "도",
)
_EN_SUFFIXES = (" Province", " Prefecture", " Special Administrative Region",
                " City", " County", " District", " Region")


def _stem(name: str) -> str | None:
    """Bare stem of an admin name, or None if nothing usable remains.

    温州市→温州, 鹿児島県→鹿児島, Zhejiang Province→Zhejiang. Very short stems
    (single CJK char) are dropped: they cause false positives."""
    if not name:
        return None
    s = name.strip()
    for suf in _EN_SUFFIXES:
        if s.endswith(suf):
            s = s[: -len(suf)].strip()
            break
    for suf in _CJK_SUFFIXES:
        if s.endswith(suf) and len(s) > len(suf):
            s = s[: -len(suf)]
            break
    return s or None


def _is_cjk(s: str) -> bool:
    return any("㐀" <= ch <= "鿿" or "가" <= ch <= "힣" for ch in s)


def _min_len_ok(alias: str) -> bool:
    """Reject aliases too short to match safely: CJK needs ≥2 chars, Latin ≥4."""
    return len(alias) >= (2 if _is_cjk(alias) else 4)


@dataclass
class _Entry:
    region_id: int
    label: str          # human-readable region name (English)
    admin_level: int
    country: str | None
    lon: float
    lat: float
    aliases: tuple[str, ...]


_gazetteer: list[_Entry] | None = None
_lock = threading.Lock()


def _build() -> list[_Entry]:
    entries: list[_Entry] = []
    with SessionLocal() as s:
        rows = s.execute(
            select(
                AdminRegion.id, AdminRegion.name, AdminRegion.name_local,
                AdminRegion.admin_level, AdminRegion.country, AdminRegion.parent_name,
                func.ST_X(func.ST_Centroid(AdminRegion.geom)),
                func.ST_Y(func.ST_Centroid(AdminRegion.geom)),
            )
        ).all()
    for rid, name, name_local, level, country, parent, lon, lat in rows:
        if lon is None or lat is None:
            continue
        aliases: set[str] = set()
        for raw in (name, name_local):
            if not raw:
                continue
            aliases.add(raw.strip())
            stem = _stem(raw)
            if stem:
                aliases.add(stem)
        aliases = {a for a in aliases if _min_len_ok(a)}
        if not aliases:
            continue
        label = " / ".join(p for p in (country, parent, name) if p) or (name or "")
        entries.append(_Entry(
            region_id=rid, label=label, admin_level=level or 0, country=country,
            lon=float(lon), lat=float(lat), aliases=tuple(aliases),
        ))
    # Most specific first so, on equal-length matches, the finer region wins.
    entries.sort(key=lambda e: e.admin_level, reverse=True)
    return entries


def gazetteer() -> list[_Entry]:
    global _gazetteer
    if _gazetteer is None:
        with _lock:
            if _gazetteer is None:
                _gazetteer = _build()
    return _gazetteer


def reload() -> int:
    """Force a rebuild (call after loading new admin regions). Returns entry count."""
    global _gazetteer
    with _lock:
        _gazetteer = _build()
    return len(_gazetteer)


@dataclass
class GeoHit:
    lat: float
    lon: float
    region_name: str
    admin_level: int
    matched: str


def geocode(text: str, country_hint: str | None = None) -> GeoHit | None:
    """Best place match in `text`, or None. Prefers the most specific admin level,
    then the longest matched name. `country_hint` (a country name) is a soft
    tie-breaker that favors regions in that country."""
    if not text:
        return None
    best: _Entry | None = None
    best_key = (-1, -1, 0)  # (admin_level, len(matched), country_bonus)
    best_matched = ""
    for e in gazetteer():
        hit = next((a for a in e.aliases if a in text), None)
        if hit is None:
            continue
        bonus = 1 if (country_hint and e.country and country_hint in e.country) else 0
        key = (e.admin_level, len(hit), bonus)
        if key > best_key:
            best_key, best, best_matched = key, e, hit
    if best is None:
        return None
    return GeoHit(lat=best.lat, lon=best.lon, region_name=best.label,
                 admin_level=best.admin_level, matched=best_matched)
