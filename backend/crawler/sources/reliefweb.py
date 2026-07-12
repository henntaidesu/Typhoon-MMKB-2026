"""ReliefWeb (UN OCHA) — official situation reports & disaster bulletins.

ReliefWeb aggregates the authoritative humanitarian reporting (UN OCHA, IFRC,
national disaster agencies, government situation reports) for every major
disaster, exposed through a clean public JSON API. We query tropical-cyclone
disasters over the West-Pacific rim countries, then pull the linked reports and
mine their titles/bodies for the storm name and impact figures (casualties /
economic loss). Each becomes a name-matched SecondaryDisaster.

  https://api.reliefweb.int/v1/disasters   -> TC disasters (name + date)
  https://api.reliefweb.int/v1/reports     -> official reports per disaster

Offline test:  python crawler/sources/reliefweb.py --preview --years 2023
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

from crawler.sources.disaster_common import (  # noqa: E402
    DisasterRec, classify_type, extract_casualties, extract_loss_usd,
    extract_typhoon_name,
)

API = "https://api.reliefweb.int/v1"
APPNAME = "typhoon-mmkb"
# West-Pacific basin rim — where our typhoons make landfall / cause impact.
WP_COUNTRIES = ("China", "Japan", "Philippines", "Viet Nam", "Republic of Korea",
                "Taiwan", "Hong Kong", "Macau")


def _post(path: str, payload: dict) -> list[dict]:
    with httpx.Client(timeout=60.0, follow_redirects=True,
                      headers={"User-Agent": APPNAME}) as c:
        r = c.post(f"{API}/{path}", params={"appname": APPNAME}, json=payload)
        r.raise_for_status()
        return r.json().get("data", [])


def fetch_reports(year: int, limit: int = 200) -> list[dict]:
    """Official reports on tropical-cyclone disasters affecting WP countries."""
    payload = {
        "filter": {
            "operator": "AND",
            "conditions": [
                {"field": "disaster.type.name", "value": "Tropical Cyclone"},
                {"field": "primary_country.name", "value": list(WP_COUNTRIES)},
                {"field": "date.created", "value": {
                    "from": f"{year}-01-01T00:00:00+00:00",
                    "to": f"{year}-12-31T23:59:59+00:00",
                }},
            ],
        },
        "fields": {"include": [
            "title", "body", "date.created", "disaster.name",
            "primary_country.name", "url", "source.shortname",
        ]},
        "sort": ["date.created:asc"],
        "limit": limit,
    }
    return _post("reports", payload)


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def parse_reports(data: list[dict]) -> list[DisasterRec]:
    recs: list[DisasterRec] = []
    for item in data:
        f = item.get("fields", {})
        title = f.get("title") or ""
        body = f.get("body") or ""
        text = f"{title}\n{body}"
        # Prefer the storm name off the structured disaster.name, else the title.
        dis_names = f.get("disaster") or []
        dis_str = " ".join(d.get("name", "") for d in dis_names) if isinstance(dis_names, list) else ""
        name = extract_typhoon_name(dis_str) or extract_typhoon_name(title)
        if not name:
            continue  # can't tie an un-named report to a KB typhoon
        ts = _parse_dt((f.get("date") or {}).get("created"))
        country = ((f.get("primary_country") or {}).get("name")) if isinstance(f.get("primary_country"), dict) else None
        src = ((f.get("source") or [{}])[0].get("shortname")) if isinstance(f.get("source"), list) and f.get("source") else "ReliefWeb"
        recs.append(DisasterRec(
            typhoon_name=name,
            season_year=ts.year if ts else None,
            disaster_type=classify_type(text),
            event_time=ts,
            casualties=extract_casualties(text),
            economic_loss_usd=extract_loss_usd(text),
            description=f"[{src}] {title}"[:800],
            source="ReliefWeb",
            source_url=(f.get("url", {}).get("self") if isinstance(f.get("url"), dict) else None),
            region_name=country,
        ))
    return recs


def collect(years: list[int], emit=lambda m: None) -> list[DisasterRec]:
    all_recs: list[DisasterRec] = []
    for y in years:
        try:
            data = fetch_reports(y)
        except Exception as e:  # noqa: BLE001
            emit(f"  ReliefWeb {y} 获取失败（{e}）")
            continue
        recs = parse_reports(data)
        emit(f"  ReliefWeb {y}: {len(data)} reports -> {len(recs)} disaster records")
        all_recs += recs
    return all_recs


def _preview(years: list[int]) -> None:
    recs = collect(years, emit=lambda m: print(f"[reliefweb]{m}"))
    for r in recs[:12]:
        print(f"  {r.typhoon_name or '(?)':14s} {r.disaster_type:14s} "
              f"deaths={r.casualties} loss={r.economic_loss_usd} | {r.description[:70]}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, nargs="*", default=[2023])
    ap.add_argument("--preview", action="store_true")
    args = ap.parse_args()
    if args.preview:
        _preview(args.years)
    else:
        print("Use --preview for offline fetch; DB load runs via crawler/pipeline.py")
