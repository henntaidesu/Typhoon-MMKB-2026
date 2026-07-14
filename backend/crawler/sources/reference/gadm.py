"""GADM level-2 administrative boundaries — prefecture / city (地级市) level.

Natural Earth only reaches admin-1 (provinces). To answer questions at the
finer 地级市 (Chinese prefecture-level city) granularity, we load GADM 4.1
level-2 units for the West-Pacific landfall countries and store them as
`admin_region` rows with admin_level=2.

GADM level 2 means (country-specific): China = 地级市 (prefecture), Japan =
市/郡, Philippines = province, Vietnam = district, Taiwan/Korea = county/city.
For China this is exactly the 地级市 the KB needs.

Data: https://gadm.org — free for academic / non-commercial use (this course
project qualifies). Per-country GeoJSON zips are small (China L2 ≈ 1.4 MB).

Offline test:  python -m crawler.sources.reference.gadm --preview --countries CHN
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import zipfile

import httpx

_BACKEND = os.path.dirname(os.path.abspath(__file__))
while os.path.basename(_BACKEND) != "backend" and os.path.dirname(_BACKEND) != _BACKEND:
    _BACKEND = os.path.dirname(_BACKEND)
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from crawler.sources.reference.naturalearth import AdminRec, CACHE_DIR, _to_multipolygon_wkt  # noqa: E402

GADM_URL = "https://geodata.ucdavis.edu/gadm/gadm4.1/json/gadm41_{iso3}_2.json.zip"

# West-Pacific landfall countries to fetch at prefecture/city (L2) resolution.
# China is the primary target (地级市); the rest give finer coverage where the
# storms actually strike. ISO-3 -> ISO-2.
DEFAULT_COUNTRIES = {
    "CHN": "CN",  # 地级市
    "TWN": "TW",  # 縣/市
    "PHL": "PH",  # provinces
    "VNM": "VN",  # districts
    "JPN": "JP",  # 市/郡
    "KOR": "KR",  # 시/군
}


def download(iso3: str, force: bool = False) -> str:
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, f"gadm41_{iso3}_2.json.zip")
    if os.path.exists(path) and not force:
        return path
    url = GADM_URL.format(iso3=iso3)
    print(f"[gadm] downloading {url}")
    with httpx.stream("GET", url, timeout=180.0, follow_redirects=True,
                      headers={"User-Agent": "typhoon-mmkb/0.1"}) as r:
        r.raise_for_status()
        with open(path, "wb") as f:
            for chunk in r.iter_bytes():
                f.write(chunk)
    print(f"[gadm] cached -> {path} ({os.path.getsize(path)//1024} KB)")
    return path


def _parse_country(iso3: str, iso2: str) -> list[AdminRec]:
    path = download(iso3)
    with zipfile.ZipFile(path) as z:
        data = json.loads(z.read(z.namelist()[0]))
    out: list[AdminRec] = []
    for feat in data.get("features", []):
        p = feat.get("properties", {}) or {}
        name = p.get("NAME_2")
        if not name:
            continue
        wkt = _to_multipolygon_wkt(feat.get("geometry") or {})
        if not wkt:
            continue
        gid = p.get("GID_2") or f"{iso3}-{p.get('NAME_1')}-{name}"
        out.append(AdminRec(
            ne_id=f"gadm{gid}", name=name,
            name_local=p.get("NL_NAME_2") or None,
            iso_a2=iso2, iso_a3=iso3,
            admin_level=2, country=p.get("COUNTRY"),
            wkt=wkt, parent_name=p.get("NAME_1"),
        ))
    return out


def parse(countries: dict[str, str] | None = None) -> list[AdminRec]:
    """Admin-2 regions for the given {iso3: iso2} countries (default WP set).
    Each country is best-effort: one failing never blocks the others."""
    countries = countries or DEFAULT_COUNTRIES
    out: list[AdminRec] = []
    for iso3, iso2 in countries.items():
        try:
            recs = _parse_country(iso3, iso2)
            out += recs
            print(f"[gadm] {iso3}: {len(recs)} level-2 regions")
        except Exception as e:  # noqa: BLE001 — skip a country that 404s / changes
            print(f"[gadm] {iso3} skipped: {e}")
    return out


def _preview(countries: list[str] | None) -> None:
    sub = {k: DEFAULT_COUNTRIES[k] for k in countries if k in DEFAULT_COUNTRIES} \
        if countries else DEFAULT_COUNTRIES
    recs = parse(sub)
    print(f"[gadm] parsed {len(recs)} level-2 regions total")
    for r in recs[:8]:
        print(f"  {r.country} / {r.parent_name} / {r.name} ({r.name_local or '-'})")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--preview", action="store_true")
    ap.add_argument("--countries", nargs="*", help="ISO3 codes, e.g. CHN JPN")
    args = ap.parse_args()
    if args.preview:
        _preview(args.countries)
    else:
        print("Use --preview for offline fetch; DB load runs via load.load_admin_regions")
