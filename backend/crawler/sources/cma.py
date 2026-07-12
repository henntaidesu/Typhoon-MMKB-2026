"""CMA 中央气象台 (typhoon.nmc.cn) — official Chinese real-time typhoon feed.

Provides the '实况路径' (actual observed track) for the current season, including
typhoons that are still active. This is the richest official live-track source
for the West Pacific (full multi-hour observed track per storm).

Feed is JSONP; the list endpoint is GBK-encoded, the view endpoint UTF-8, so
decoding is auto-detected. Position array layout (index -> meaning):
  0 pointId  1 timeStr  2 epochMs  3 grade  4 lon  5 lat
  6 pressure(hPa)  7 wind(m/s)  8 moveDir  9 moveSpeed(km/h)  10 windRadii  11 forecasts
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx

from crawler.sources.common import (
    AgencyStorm, ObsPoint, compass_to_deg, ms_to_kt, num, strongest_grade,
)

LIST_URL = "http://typhoon.nmc.cn/weatherservice/typhoon/jsons/list_default"
VIEW_URL = "http://typhoon.nmc.cn/weatherservice/typhoon/jsons/view_{id}"
_H = {"User-Agent": "Mozilla/5.0", "Referer": "http://typhoon.nmc.cn/"}


def _get(url: str) -> str:
    r = httpx.get(url, headers=_H, timeout=45.0, follow_redirects=True)
    r.raise_for_status()
    b = r.content
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        return b.decode("gbk", "replace")


def _jsonp(text: str):
    """Strip the JSONP wrapper and parse the JSON inside. The list endpoint uses
    a double-paren wrapper `callback(( ... ))` and the view endpoint a single
    one, so we slice to the real JSON delimiters instead of counting parens."""
    starts = [i for i in (text.find("{"), text.find("[")) if i >= 0]
    ends = [i for i in (text.rfind("}"), text.rfind("]")) if i >= 0]
    if not starts or not ends:
        raise ValueError("no JSON body in JSONP response")
    return json.loads(text[min(starts):max(ends) + 1])


def fetch_storms(year: int | None = None, emit=lambda m: None) -> list[AgencyStorm]:
    data = _jsonp(_get(LIST_URL))
    rows = data.get("typhoonList", [])
    storms: list[AgencyStorm] = []
    for row in rows:
        internal_id = row[0]
        name_en = row[1] if len(row) > 1 else None
        tfbh = str(row[3]) if len(row) > 3 and row[3] is not None else ""
        # Real WMO 编号 is 4 digits (YYNN). Unnamed depressions carry an 8-digit
        # internal placeholder instead — skip those.
        if not tfbh.isdigit() or len(tfbh) != 4:
            continue
        season = 2000 + int(tfbh[:2])
        if year and season != year:
            continue

        emit(f"  {tfbh} {name_en} 拉取实况路径 …")
        try:
            vd = _jsonp(_get(VIEW_URL.format(id=internal_id)))
            arr = vd["typhoon"]
            raw = arr[8] if len(arr) > 8 and isinstance(arr[8], list) else []
        except Exception as e:  # noqa: BLE001
            emit(f"  {tfbh} 跳过（{e}）")
            continue

        points: list[ObsPoint] = []
        for p in raw:
            try:
                obs = datetime.fromtimestamp(p[2] / 1000, tz=timezone.utc)
                lon, lat = float(p[4]), float(p[5])
            except (TypeError, ValueError, IndexError):
                continue
            points.append(ObsPoint(
                obs_time=obs, lat=lat, lon=lon,
                wind_kt=ms_to_kt(num(p[7])) if len(p) > 7 else None,
                pressure_hpa=num(p[6]) if len(p) > 6 else None,
                grade=p[3] if len(p) > 3 else None,
                move_dir=compass_to_deg(p[8]) if len(p) > 8 else None,
                move_speed=num(p[9]) if len(p) > 9 else None,
            ))
        if not points:
            continue
        name = None if not name_en or name_en.lower() in ("nameless", "unnamed") else name_en.title()
        storms.append(AgencyStorm(
            intl_id=tfbh, name=name, season_year=season,
            category=strongest_grade(pt.grade for pt in points), points=points,
        ))
    return storms
