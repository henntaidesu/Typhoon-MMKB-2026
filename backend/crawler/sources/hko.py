"""香港天文台 HKO (data.weather.gov.hk) — official Hong Kong warnings.

The Hong Kong Observatory's Open Data API publishes real-time official warning
signals. During a typhoon the relevant secondary hazards are the Rainstorm
Warning (暴雨 → flooding), the Landslip Warning (山泥倾泻 → landslide) and the
special flooding announcement — all authoritative government warnings.

HKO warnings carry no coordinates, so each is stamped with Hong Kong's location
and time/space-matched to whichever typhoon was active nearby (like NMC 预警).
Real-time feed (current signals only).

  .../weather.php?dataType=warnsum -> {code: {name, issueTime, ...}}

Offline test:  python crawler/sources/hko.py --preview
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime

import httpx

_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from crawler.sources.disaster_common import DisasterRec, classify_type  # noqa: E402

WARNSUM_URL = "https://data.weather.gov.hk/weatherAPI/opendata/weather.php?dataType=warnsum&lang=en"
_H = {"User-Agent": "Mozilla/5.0"}

# Hong Kong reference point (Victoria Harbour) for spatial matching.
HK_LAT, HK_LON = 22.30, 114.17

# HKO warning codes that represent a typhoon secondary hazard -> disaster_type.
# (TC signals themselves are the storm, not a secondary disaster, so excluded.)
_SECONDARY = {
    "WRAIN": "flood",       # 暴雨警告 Rainstorm Warning (Amber/Red/Black)
    "WL": "landslide",      # 山泥倾泻警告 Landslip Warning
    "WFNTSA": "flood",      # 新界北部水浸特别报告 Flooding in northern New Territories
    "WFLOOD": "flood",      # Flood-related special announcement
}


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


def fetch_warnings(emit=lambda m: None) -> dict:
    try:
        return _get_json(WARNSUM_URL) or {}
    except Exception as e:  # noqa: BLE001
        emit(f"  HKO 警告获取失败（{e}）")
        return {}


def parse_warnings(warnsum: dict) -> list[DisasterRec]:
    recs: list[DisasterRec] = []
    for code, w in (warnsum or {}).items():
        if code not in _SECONDARY:
            continue
        if not isinstance(w, dict):
            continue
        # An expired/cancelled signal carries actionCode CANCEL.
        if str(w.get("actionCode", "")).upper() == "CANCEL":
            continue
        name = w.get("name") or code
        ts = _parse_dt(w.get("issueTime") or w.get("updateTime"))
        recs.append(DisasterRec(
            disaster_type=_SECONDARY.get(code) or classify_type(name),
            event_time=ts,
            lat=HK_LAT, lon=HK_LON,
            description=f"[香港天文台] {name} ({w.get('actionCode','')})".strip()[:800],
            source="HKO",
            source_url="https://www.hko.gov.hk/en/wservice/warning.htm",
            region_name="Hong Kong",
        ))
    return recs


def collect(emit=lambda m: None) -> list[DisasterRec]:
    warnsum = fetch_warnings(emit=emit)
    recs = parse_warnings(warnsum)
    emit(f"  香港天文台: {len(warnsum)} signals -> {len(recs)} secondary-hazard records")
    return recs


def _preview() -> None:
    recs = collect(emit=lambda m: print(f"[hko]{m}"))
    for r in recs:
        print(f"  {r.disaster_type:12s} {r.event_time} | {r.description[:70]}")
    if not recs:
        print("  (no active secondary-hazard warnings right now)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--preview", action="store_true")
    args = ap.parse_args()
    if args.preview:
        _preview()
    else:
        print("Use --preview for offline fetch; DB load runs via crawler/pipeline.py")
