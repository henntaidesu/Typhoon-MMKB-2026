"""GDACS (Global Disaster Alert and Coordination System) tropical-cyclone
events and their associated impacts -> SecondaryDisaster records.

GDACS exposes a public event API. We query TC (tropical cyclone) events,
match them to typhoons already in the KB by name + year, and record the
event location, alert level, affected population and any linked flood impact.

Offline test:  python crawler/sources/international/disaster/government/gdacs.py --preview --years 2023
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

import httpx

_BACKEND = os.path.dirname(os.path.abspath(__file__))
while os.path.basename(_BACKEND) != "backend" and os.path.dirname(_BACKEND) != _BACKEND:
    _BACKEND = os.path.dirname(_BACKEND)
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from crawler.sources._shared.disaster_common import DisasterRec  # noqa: E402
from crawler.sources._shared.public_common import INFO_NEWS, PublicInfoRec  # noqa: E402

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


def _affected_countries(val) -> str | None:
    """Normalize GDACS 'affectedcountries' (list of dicts / list of str / str)
    into a comma-separated country-name string, or None."""
    if not val:
        return None
    if isinstance(val, str):
        return val.strip() or None
    names: list[str] = []
    if isinstance(val, list):
        for item in val:
            if isinstance(item, dict):
                nm = item.get("countryname") or item.get("name") or item.get("iso3")
                if nm:
                    names.append(str(nm).strip())
            elif item:
                names.append(str(item).strip())
    return ", ".join(dict.fromkeys(n for n in names if n)) or None


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
        # affectedcountries is a list of {iso3, countryname} (or a string) — pull
        # the human-readable names so the affected country is preserved.
        region = _affected_countries(p.get("affectedcountries"))
        recs.append(DisasterRec(
            typhoon_name=(name.title() if name else None),
            season_year=ts.year if ts else None,
            # GDACS covers every basin. Each event IS a named cyclone, so if the
            # name doesn't resolve to a KB typhoon the event belongs to another
            # basin and must be dropped, not guessed at by time/space.
            named_event=True,
            disaster_type="storm_surge" if "surge" in str(sev.get("severitytext", "")).lower() else "wind_impact",
            lat=lat, lon=lon, event_time=ts,
            casualties=None,
            economic_loss_usd=None,
            description=(f"GDACS {p.get('alertlevel','')} alert: {p.get('htmldescription') or p.get('description') or name}. "
                         f"Severity: {sev.get('severitytext','')}."
                         + (f" Affected: {region}." if region else "")),
            source="GDACS",
            source_url=p.get("url", {}).get("report") if isinstance(p.get("url"), dict) else p.get("link"),
            region_name=region,
        ))
    return recs


def _report_url(p: dict) -> str | None:
    url = p.get("url")
    if isinstance(url, dict):
        return url.get("report") or url.get("details")
    return p.get("link")


def parse_news(features: list[dict]) -> list[PublicInfoRec]:
    """Derive 公共情报 (info_type=news) from the same GDACS TC events: each event's
    official GDACS report page — which surfaces the media coverage & situation
    reporting for that cyclone — becomes a name-matched PublicInfoRec. This is the
    *public reporting* about the storm, distinct from the impact event itself
    (parse_events, 受灾情报)."""
    recs: list[PublicInfoRec] = []
    for f in features:
        p = f.get("properties", {})
        report = _report_url(p)
        if not report:
            continue
        geom = f.get("geometry") or {}
        coords = geom.get("coordinates") or [None, None]
        lon, lat = (coords[0], coords[1]) if len(coords) >= 2 else (None, None)
        name = p.get("eventname") or p.get("name")
        try:
            ts = datetime.fromisoformat(str(p.get("fromdate")).replace("Z", "+00:00"))
        except Exception:
            ts = None
        if ts and ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        region = _affected_countries(p.get("affectedcountries"))
        alert = p.get("alertlevel", "")
        recs.append(PublicInfoRec(
            info_type=INFO_NEWS,
            category="tropical_cyclone",
            agency="GDACS",
            severity=str(alert) or None,
            title=(name.title() if name else None),
            typhoon_name=(name.title() if name else None),
            season_year=ts.year if ts else None,
            named_event=True,  # see parse_events — global feed, name is the key
            event_time=ts, lat=lat, lon=lon,
            description=(f"GDACS {alert} report: {p.get('htmldescription') or p.get('description') or name}."
                        + (f" Affected: {region}." if region else "")),
            source="GDACS",
            source_url=report,
            region_name=region,
        ))
    return recs


def _preview(years: list[int]) -> None:
    all_recs: list[DisasterRec] = []
    for y in years:
        feats = fetch_tc_events(y)
        recs = parse_events(feats)
        news = parse_news(feats)
        print(f"[gdacs] {y}: {len(feats)} TC events -> {len(recs)} disaster records, {len(news)} news")
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
