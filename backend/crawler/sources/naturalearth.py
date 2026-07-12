"""Natural Earth administrative boundaries (public domain) — country + province.

Provides the *reference* geometry the knowledge base needs to attribute each
typhoon's track to real places: which countries / provinces it passed over and
made landfall in. Loaded once into the `admin_region` table via
`load.load_admin_regions`; enrichment (crawler/enrich.py) then spatially joins
tracks against it.

We fetch the GeoJSON builds from the `nvkelso/natural-earth-vector` repo so the
existing `httpx` + stdlib `json` + `shapely` stack is enough — no geopandas /
GDAL. Two layers:
  - admin-0 countries (10m, accurate coastlines) -> landfall (sea→land) detection
  - admin-1 states/provinces (10m) -> per-province landfall frequency

Only West-Pacific-rim countries are kept (attribute filter on ISO code), so the
table stays small. Offline test:
    python -m crawler.sources.naturalearth --preview
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass

import httpx
from shapely.geometry import MultiPolygon, shape

_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

# Natural Earth GeoJSON (public domain). 10m for coastline accuracy.
_RAW = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson"
ADMIN0_URL = f"{_RAW}/ne_10m_admin_0_countries.geojson"
ADMIN1_URL = f"{_RAW}/ne_10m_admin_1_states_provinces.geojson"

CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", ".cache")

# West-Pacific-rim countries whose coasts our typhoons strike. ISO A2 codes.
WP_ISO2 = {
    "CN",  # China
    "JP",  # Japan
    "PH",  # Philippines
    "TW",  # Taiwan
    "KR",  # South Korea
    "KP",  # North Korea
    "VN",  # Vietnam
    "LA",  # Laos
    "KH",  # Cambodia
    "TH",  # Thailand
    "MM",  # Myanmar
    "MY",  # Malaysia
    "ID",  # Indonesia
    "HK",  # Hong Kong
    "MO",  # Macau
    "US",  # Guam / Northern Mariana Islands (US WP territories)
    "FM",  # Micronesia
    "MH",  # Marshall Islands
    "PW",  # Palau
    "MP",  # Northern Mariana Islands (if coded separately)
    "GU",  # Guam (if coded separately)
    "BN",  # Brunei
}

# Simplify tolerance (degrees) to cap geometry size; ~1 km at these latitudes.
_SIMPLIFY_DEG = 0.01


@dataclass
class AdminRec:
    ne_id: str
    name: str | None
    name_local: str | None
    iso_a2: str | None
    iso_a3: str | None
    admin_level: int  # 0 country, 1 province
    country: str | None  # parent country name (for admin-1 grouping)
    wkt: str


def download(url: str, force: bool = False) -> str:
    """Download (and cache) a Natural Earth GeoJSON, return the local path."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, os.path.basename(url))
    if os.path.exists(path) and not force:
        return path
    print(f"[naturalearth] downloading {url}")
    with httpx.stream("GET", url, timeout=180.0, follow_redirects=True,
                      headers={"User-Agent": "typhoon-mmkb/0.1"}) as r:
        r.raise_for_status()
        with open(path, "wb") as f:
            for chunk in r.iter_bytes():
                f.write(chunk)
    print(f"[naturalearth] cached -> {path} ({os.path.getsize(path)//1024} KB)")
    return path


def _prop(props: dict, *keys: str):
    """First non-empty property among candidate keys (NE mixes upper/lower case)."""
    for k in keys:
        v = props.get(k)
        if v not in (None, "", -99, "-99"):
            return v
    return None


def _to_multipolygon_wkt(geom_json: dict) -> str | None:
    """shapely geometry -> simplified MultiPolygon WKT (or None if not areal)."""
    try:
        g = shape(geom_json)
    except Exception:  # noqa: BLE001 — skip a malformed feature, never abort the load
        return None
    if g.is_empty:
        return None
    if _SIMPLIFY_DEG:
        g = g.simplify(_SIMPLIFY_DEG, preserve_topology=True)
        if g.is_empty:
            g = shape(geom_json)  # simplification collapsed it; keep the original
    if g.geom_type == "Polygon":
        g = MultiPolygon([g])
    elif g.geom_type != "MultiPolygon":
        return None
    return g.wkt


def _parse_admin0(path: str) -> list[AdminRec]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    out: list[AdminRec] = []
    for feat in data.get("features", []):
        p = feat.get("properties", {}) or {}
        iso2 = _prop(p, "ISO_A2", "ISO_A2_EH", "iso_a2")
        iso3 = _prop(p, "ISO_A3", "ISO_A3_EH", "adm0_a3", "ADM0_A3")
        if str(iso2 or "").upper() not in WP_ISO2:
            continue
        wkt = _to_multipolygon_wkt(feat.get("geometry") or {})
        if not wkt:
            continue
        name = _prop(p, "NAME", "ADMIN", "NAME_LONG", "name")
        ne_id = str(_prop(p, "NE_ID", "ne_id") or f"a0-{iso3 or name}")
        out.append(AdminRec(
            ne_id=f"ne{ne_id}", name=name, name_local=_prop(p, "NAME_ZH", "NAME_JA", "FORMAL_EN"),
            iso_a2=(str(iso2).upper() if iso2 else None),
            iso_a3=(str(iso3).upper() if iso3 else None),
            admin_level=0, country=name, wkt=wkt,
        ))
    return out


def _parse_admin1(path: str) -> list[AdminRec]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    out: list[AdminRec] = []
    for feat in data.get("features", []):
        p = feat.get("properties", {}) or {}
        iso2 = _prop(p, "iso_a2", "ISO_A2")
        # admin-1 files store the parent country ISO in various keys.
        parent_iso2 = str(iso2 or "").upper()
        if parent_iso2 not in WP_ISO2:
            continue
        wkt = _to_multipolygon_wkt(feat.get("geometry") or {})
        if not wkt:
            continue
        name = _prop(p, "name", "NAME", "name_en", "gn_name")
        country = _prop(p, "admin", "geonunit", "gu_a3")
        iso3 = _prop(p, "adm0_a3", "ADM0_A3", "iso_3166_2")
        ne_id = str(_prop(p, "ne_id", "NE_ID", "adm1_code") or f"a1-{name}-{country}")
        out.append(AdminRec(
            ne_id=f"ne{ne_id}", name=name, name_local=_prop(p, "name_local", "name_zh", "woe_name"),
            iso_a2=parent_iso2,
            iso_a3=(str(iso3).upper()[:3] if iso3 else None),
            admin_level=1, country=country, wkt=wkt,
        ))
    return out


def parse(force: bool = False) -> list[AdminRec]:
    """All West-Pacific admin-0 + admin-1 regions as AdminRec (for load)."""
    recs = _parse_admin0(download(ADMIN0_URL, force))
    recs += _parse_admin1(download(ADMIN1_URL, force))
    return recs


def _preview() -> None:
    recs = parse()
    n0 = sum(1 for r in recs if r.admin_level == 0)
    n1 = sum(1 for r in recs if r.admin_level == 1)
    print(f"[naturalearth] parsed {len(recs)} regions: {n0} countries, {n1} provinces")
    for r in recs[:8]:
        lvl = "国" if r.admin_level == 0 else "省"
        print(f"  [{lvl}] {r.name} ({r.iso_a2}/{r.iso_a3}) parent={r.country} wkt={len(r.wkt)} chars")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--preview", action="store_true")
    ap.add_argument("--force", action="store_true", help="re-download, ignore cache")
    args = ap.parse_args()
    if args.preview:
        _preview()
    else:
        print("Use --preview for offline fetch; DB load runs via load.load_admin_regions")
