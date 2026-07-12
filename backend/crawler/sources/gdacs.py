"""GDACS (Global Disaster Alert and Coordination System) tropical-cyclone
events and their associated impacts -> SecondaryDisaster records.

GDACS exposes a public event API. We query TC (tropical cyclone) events,
match them to typhoons already in the KB by name + year, and record the
event location, alert level, affected population and any linked flood impact.

Offline test:  python crawler/sources/gdacs.py --preview --years 2023
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

import httpx

_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from crawler.sources.disaster_common import DisasterRec  # noqa: E402

# GDACS public geo-event feed (GeoJSON). eventtype=TC for tropical cyclones.
GDACS_SEARCH = "https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH"


def fetch_tc_events(year: int) -> list[dict]:
    params = {
        "eventlist": "TC",
        "fromDate": f"{year}-01-01",
        "toDate": f"{year}-12-31",
        "alertlevel": "Green;Orange;Red",
    }
    with httpx.Client(timeout=60.0, follow_redirects=True,
                      headers={"User-Agent": "typhoon-mmkb/0.1"}) as c:
        r = c.get(GDACS_SEARCH, params=params)
        r.raise_for_status()
        data = r.json()
    return data.get("features", [])


def parse_events(features: list[dict]) -> list[DisasterRec]:
    recs: list[DisasterRec] = []
    for f in features:
        p = f.get("properties", {})
        geom = f.get("geometry") or {}
        coords = geom.get("coordinates") or [None, None]
        lon, lat = (coords[0], coords[1]) if len(coords) >= 2 else (None, None)
        name = p.get("eventname") or p.get("name")
        sev = p.get("severitydata", {}) or {}
        try:
            ts = datetime.fromisoformat(str(p.get("fromdate")).replace("Z", "+00:00"))
        except Exception:
            ts = None
        if ts and ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        pop = p.get("affectedcountries")
        recs.append(DisasterRec(
            typhoon_name=(name.title() if name else None),
            season_year=ts.year if ts else None,
            disaster_type="storm_surge" if "surge" in str(sev.get("severitytext", "")).lower() else "wind_impact",
            lat=lat, lon=lon, event_time=ts,
            casualties=None,
            economic_loss_usd=None,
            description=(f"GDACS {p.get('alertlevel','')} alert: {p.get('htmldescription') or p.get('description') or name}. "
                         f"Severity: {sev.get('severitytext','')}."),
            source="GDACS",
            source_url=p.get("url", {}).get("report") if isinstance(p.get("url"), dict) else p.get("link"),
        ))
    return recs


def _preview(years: list[int]) -> None:
    all_recs: list[DisasterRec] = []
    for y in years:
        feats = fetch_tc_events(y)
        recs = parse_events(feats)
        print(f"[gdacs] {y}: {len(feats)} TC events -> {len(recs)} disaster records")
        all_recs += recs
    for r in all_recs[:10]:
        print(f"  {r.typhoon_name or '(?)':16s} {r.disaster_type:12s} "
              f"@({r.lat},{r.lon}) {r.event_time} | {r.description[:60]}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, nargs="*", default=[2023])
    ap.add_argument("--preview", action="store_true")
    args = ap.parse_args()
    if args.preview:
        _preview(args.years)
    else:
        print("Use --preview for offline fetch; DB load runs via crawler/pipeline.py")
