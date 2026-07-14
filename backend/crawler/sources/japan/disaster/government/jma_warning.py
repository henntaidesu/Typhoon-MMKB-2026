"""気象庁 JMA 気象警報 (www.jma.go.jp/bosai/warning) — official Japan warnings.

Complements the post-event 消防庁 被害報 (fdma.py) with JMA's REAL-TIME official
warnings for the secondary hazards a typhoon drives inland: 大雨警報 / 洪水警報
(flooding), 土砂災害 (landslide) and 高潮警報 (storm surge). We poll the warning
feed for a curated set of typhoon-exposed prefectures and record any active
警報-level secondary hazard, stamped with the prefecture centroid so the loader
time/space-matches it to the responsible typhoon.

Same bosai infrastructure as the JMA track source (jma.py). Real-time (current
warnings only). Offline test:  python crawler/sources/japan/disaster/government/jma_warning.py --preview
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime

import httpx

_BACKEND = os.path.dirname(os.path.abspath(__file__))
while os.path.basename(_BACKEND) != "backend" and os.path.dirname(_BACKEND) != _BACKEND:
    _BACKEND = os.path.dirname(_BACKEND)
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from crawler.sources._shared.disaster_common import classify_type  # noqa: E402
from crawler.sources._shared.public_common import INFO_WARNING, PublicInfoRec  # noqa: E402

WARN_URL = "https://www.jma.go.jp/bosai/warning/data/warning/{office}.json"
_H = {"User-Agent": "Mozilla/5.0"}

# Typhoon-exposed prefectures: JMA office code -> (name, lat, lon centroid).
PREFECTURES: dict[str, tuple[str, float, float]] = {
    "471000": ("沖縄", 26.21, 127.68),
    "460100": ("鹿児島", 31.60, 130.56),
    "450000": ("宮崎", 31.91, 131.42),
    "430000": ("熊本", 32.79, 130.74),
    "420000": ("長崎", 32.75, 129.87),
    "390000": ("高知", 33.56, 133.53),
    "380000": ("愛媛", 33.84, 132.77),
    "360000": ("徳島", 34.07, 134.56),
    "300000": ("和歌山", 34.23, 135.17),
    "240000": ("三重", 34.73, 136.51),
    "270000": ("大阪", 34.69, 135.52),
    "220000": ("静岡", 34.98, 138.38),
    "120000": ("千葉", 35.61, 140.12),
    "130000": ("東京", 35.69, 139.69),
}

# JMA 警報-level codes for typhoon secondary hazards -> (type, JP name).
# 注意報 (advisory) codes are intentionally excluded as low-signal noise.
_WARN_CODES: dict[str, tuple[str, str]] = {
    "03": ("flood", "大雨警報"),
    "04": ("flood", "洪水警報"),
    "08": ("storm_surge", "高潮警報"),
}
# Warning entries with these statuses are not currently in force.
_INACTIVE = {"解除", "発表警報・注意報はなし", ""}
_SECONDARY_TYPES = {"flood", "landslide", "storm_surge"}


def _get_json(url: str):
    r = httpx.get(url, headers=_H, timeout=40.0, follow_redirects=True)
    r.raise_for_status()
    return r.json()


def _parse_dt(s) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None


def _active_codes(data: dict) -> set[str]:
    out: set[str] = set()
    for at in data.get("areaTypes", []) or []:
        for area in at.get("areas", []) or []:
            for w in area.get("warnings", []) or []:
                if w.get("status") not in _INACTIVE and w.get("code"):
                    out.add(str(w["code"]))
    return out


def parse_office(office: str, data: dict) -> list[PublicInfoRec]:
    name, lat, lon = PREFECTURES[office]
    ts = _parse_dt(data.get("reportDatetime"))
    headline = data.get("headlineText") or ""

    hazards: dict[str, str] = {}  # type -> label
    for code in _active_codes(data):
        if code in _WARN_CODES:
            typ, label = _WARN_CODES[code]
            hazards.setdefault(typ, label)
    # headlineText catches 土砂災害 / 特別警報 phrasing the code map omits.
    ht = classify_type(headline, default="none")
    if ht in _SECONDARY_TYPES:
        hazards.setdefault(ht, "気象警報")

    recs: list[PublicInfoRec] = []
    for typ, label in hazards.items():
        recs.append(PublicInfoRec(
            info_type=INFO_WARNING,
            category=typ,
            agency="気象庁",
            severity="特別警報" if "特別警報" in headline else "警報",
            title=label,
            event_time=ts,
            lat=lat, lon=lon,
            description=f"[気象庁 {name}] {label}"
                        + (f"：{headline}" if headline else ""),
            source="JMA",
            source_url=f"https://www.jma.go.jp/bosai/warning/#area_type=class20s&area_code={office}",
            region_name=name,
        ))
    return recs


def collect(emit=lambda m: None) -> list[PublicInfoRec]:
    recs: list[PublicInfoRec] = []
    hit = 0
    for office in PREFECTURES:
        try:
            data = _get_json(WARN_URL.format(office=office))
        except Exception as e:  # noqa: BLE001
            emit(f"  JMA 警報 {office} 跳过（{e}）")
            continue
        got = parse_office(office, data)
        recs += got
        hit += 1
    emit(f"  気象庁 気象警報: {hit}/{len(PREFECTURES)} prefectures -> {len(recs)} public-info (warning) records")
    return recs


def _preview() -> None:
    recs = collect(emit=lambda m: print(f"[jma_warning]{m}"))
    for r in recs:
        print(f"  {r.category:12s} {r.region_name} {r.event_time} | {r.description[:60]}")
    if not recs:
        print("  (no active 警報-level secondary hazards right now)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--preview", action="store_true")
    args = ap.parse_args()
    if args.preview:
        _preview()
    else:
        print("Use --preview for offline fetch; DB load runs via crawler/pipeline.py")
