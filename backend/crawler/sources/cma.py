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
LIST_YEAR_URL = "http://typhoon.nmc.cn/weatherservice/typhoon/jsons/list_{year}"
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


def _parse_row(row, emit) -> AgencyStorm | None:
    """One typhoonList row -> AgencyStorm (fetches its view for the track)."""
    internal_id = row[0]
    name_en = row[1] if len(row) > 1 else None
    tfbh = str(row[3]) if len(row) > 3 and row[3] is not None else ""
    # Real WMO 编号 is 4 digits YYNN with storm-number >= 1. Unnamed systems use
    # an 8-digit placeholder (default list) or the "2000" sentinel (yearly list)
    # — both are filtered out here.
    if not tfbh.isdigit() or len(tfbh) != 4 or int(tfbh[2:]) < 1:
        return None
    season = 2000 + int(tfbh[:2])

    emit(f"  {tfbh} {name_en} 拉取实况路径 …")
    try:
        vd = _jsonp(_get(VIEW_URL.format(id=internal_id)))
        arr = vd["typhoon"]
        raw = arr[8] if len(arr) > 8 and isinstance(arr[8], list) else []
    except Exception as e:  # noqa: BLE001
        emit(f"  {tfbh} 跳过（{e}）")
        return None

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
        return None
    name = None if not name_en or name_en.lower() in ("nameless", "unnamed") else name_en.title()
    status = str(row[7]).lower() if len(row) > 7 and row[7] is not None else ""
    return AgencyStorm(
        intl_id=tfbh, name=name, season_year=season,
        category=strongest_grade(pt.grade for pt in points), points=points,
        active=(status == "start"),  # CMA: 'start' = ongoing, 'stop' = ended
    )


def fetch_storms(years=None, emit=lambda m: None) -> list[AgencyStorm]:
    """Collect CMA actual tracks. `years` empty/None -> current season (live,
    via list_default). Otherwise iterate the per-year archive list_{year} for
    each requested year, so historical seasons can be ingested too."""
    if years:
        catalogs = [(y, LIST_YEAR_URL.format(year=int(y))) for y in years]
    else:
        catalogs = [(None, LIST_URL)]

    storms: list[AgencyStorm] = []
    for yr, url in catalogs:
        emit(f"获取 CMA 台风列表（{yr or '本季实况'}）…")
        try:
            rows = _jsonp(_get(url)).get("typhoonList", [])
        except Exception as e:  # noqa: BLE001
            emit(f"  列表 {yr} 获取失败（{e}），跳过")
            continue
        n0 = len(storms)
        for row in rows:
            st = _parse_row(row, emit)
            if st:
                storms.append(st)
        emit(f"  {yr or '本季'}: 收集到 {len(storms) - n0} 个台风")
    return storms

