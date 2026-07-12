"""JMA / RSMC Tokyo (www.jma.go.jp/bosai) — official West-Pacific RSMC feed.

JMA is the WMO-designated RSMC for the West Pacific. Its real-time 'bosai'
JSON exposes each active typhoon's 実況 (Analysis) fix plus forecasts. We take
the Analysis part only (the actual observed position), so each active typhoon
contributes JMA's official current fix as an agency-tagged track point.

  targetTc.json            -> list of current TCs (tropicalCyclone id, number)
  {tc}/specifications.json -> parts: title / 実況(Analysis) / 予報(forecasts)
"""
from __future__ import annotations

from datetime import datetime

import httpx

from crawler.sources.common import AgencyStorm, ObsPoint, num

LIST_URL = "https://www.jma.go.jp/bosai/typhoon/data/targetTc.json"
SPEC_URL = "https://www.jma.go.jp/bosai/typhoon/data/{tc}/specifications.json"
_H = {"User-Agent": "Mozilla/5.0"}


def _get_json(url: str):
    r = httpx.get(url, headers=_H, timeout=40.0, follow_redirects=True)
    r.raise_for_status()
    return r.json()


def fetch_storms(emit=lambda m: None) -> list[AgencyStorm]:
    try:
        tcs = _get_json(LIST_URL)
    except Exception as e:  # noqa: BLE001
        emit(f"  JMA 列表获取失败（{e}）")
        return []

    storms: list[AgencyStorm] = []
    for tc in tcs:
        tcid = tc.get("tropicalCyclone")
        number = str(tc.get("typhoonNumber") or "").strip()
        if not tcid or len(number) < 3:
            continue
        emit(f"  JMA {number} 拉取实况 …")
        try:
            spec = _get_json(SPEC_URL.format(tc=tcid))
        except Exception as e:  # noqa: BLE001
            emit(f"  JMA {number} 跳过（{e}）")
            continue

        name = None
        for part in spec:
            if part.get("part") == "title":
                name = (part.get("name") or {}).get("en")
                break

        points: list[ObsPoint] = []
        category = None
        for part in spec:
            pt = part.get("part")
            if not (isinstance(pt, dict) and pt.get("en") == "Analysis"):
                continue
            pos = (part.get("position") or {}).get("deg") or []
            if len(pos) < 2:
                continue
            lat, lon = num(pos[0]), num(pos[1])
            if lat is None or lon is None:
                continue
            iso = (part.get("validtime") or {}).get("UTC")
            try:
                obs = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
            except Exception:  # noqa: BLE001
                continue
            kt = ((part.get("maximumWind") or {}).get("sustained") or {}).get("kt")
            category = (part.get("category") or {}).get("en")
            points.append(ObsPoint(
                obs_time=obs, lat=lat, lon=lon,
                wind_kt=num(kt), pressure_hpa=num(part.get("pressure")),
                grade=category,
                move_speed=num(((part.get("speed") or {}).get("km/h"))),
            ))
        if not points:
            continue
        season = 2000 + int(number[:2])
        storms.append(AgencyStorm(
            intl_id=number, name=(name.title() if name else None),
            season_year=season, category=category, points=points,
        ))
    return storms
