"""JTWC (US Joint Typhoon Warning Center) — official real-time WP warnings.

JTWC does not publish a clean live-track JSON; its real-time product is the
per-storm warning text. This source is therefore best-effort: it reads the JTWC
RSS to find active West-Pacific storms, then parses each storm's warning text
for the current WARNING POSITION (one observed fix). When JTWC has no active WP
storm (or a storm just went to Final Warning and its text is gone), this simply
returns fewer / no points — that is expected, not an error.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

import httpx

from crawler.sources.common import AgencyStorm, ObsPoint, num

RSS_URL = "https://www.metoc.navy.mil/jtwc/rss/jtwc.rss"
WARN_URL = "https://www.metoc.navy.mil/jtwc/products/wp{num}{yy}web.txt"
_H = {"User-Agent": "Mozilla/5.0"}

_STORM_RE = re.compile(
    r"(Super Typhoon|Typhoon|Tropical Storm|Tropical Depression)\s+(\d\dW)\s*\(([A-Za-z\-]+)\)",
    re.I,
)
_GRADE = {
    "tropical depression": "TD", "tropical storm": "TS",
    "typhoon": "TY", "super typhoon": "SuperTY",
}
_POS_RE = re.compile(r"(\d{6})Z\s*---\s*NEAR\s+(\d+\.?\d*)([NS])\s+(\d+\.?\d*)([EW])", re.I)
_WIND_RE = re.compile(r"MAX\s+SUSTAINED\s+WINDS?\s*[-:]\s*(\d+)\s*(?:KT|KTS)", re.I)


def _get(url: str) -> httpx.Response:
    return httpx.get(url, headers=_H, timeout=40.0, follow_redirects=True)


def _obs_time(ddhhmm: str) -> datetime | None:
    """JTWC 'DDHHMM' in UTC -> datetime, using the current year/month."""
    try:
        now = datetime.now(timezone.utc)
        dd, hh, mm = int(ddhhmm[:2]), int(ddhhmm[2:4]), int(ddhhmm[4:6])
        dt = datetime(now.year, now.month, dd, hh, mm, tzinfo=timezone.utc)
        # Handle a month-boundary warning (e.g. now=Jul-01, fix=Jun-30).
        if (dt - now).days > 2:
            month = now.month - 1 or 12
            year = now.year if now.month > 1 else now.year - 1
            dt = datetime(year, month, dd, hh, mm, tzinfo=timezone.utc)
        return dt
    except (ValueError, IndexError):
        return None


def fetch_storms(emit=lambda m: None) -> list[AgencyStorm]:
    try:
        rss = _get(RSS_URL).text
    except Exception as e:  # noqa: BLE001
        emit(f"  JTWC RSS 获取失败（{e}）")
        return []

    yy = f"{datetime.now(timezone.utc).year % 100:02d}"
    seen: set[str] = set()
    storms: list[AgencyStorm] = []
    for kind, wid, name in _STORM_RE.findall(rss):
        if wid in seen:
            continue
        seen.add(wid)
        stormno = wid[:2]                 # "09"
        intl_id = f"{yy}{stormno}"        # "2609"
        emit(f"  JTWC {wid} ({name}) 拉取警报 …")
        try:
            resp = _get(WARN_URL.format(num=stormno, yy=yy))
            if resp.status_code != 200:
                emit(f"  JTWC {wid} 无警报文本（{resp.status_code}），跳过")
                continue
            text = resp.text
        except Exception as e:  # noqa: BLE001
            emit(f"  JTWC {wid} 跳过（{e}）")
            continue

        m = _POS_RE.search(text)
        if not m:
            emit(f"  JTWC {wid} 未解析到定位，跳过")
            continue
        ddhhmm, lat_s, ns, lon_s, ew = m.groups()
        lat = float(lat_s) * (-1 if ns.upper() == "S" else 1)
        lon = float(lon_s) * (-1 if ew.upper() == "W" else 1)
        obs = _obs_time(ddhhmm)
        if obs is None:
            continue
        wm = _WIND_RE.search(text)
        grade = _GRADE.get(kind.lower())
        points = [ObsPoint(obs_time=obs, lat=lat, lon=lon,
                           wind_kt=num(wm.group(1)) if wm else None,
                           grade=grade)]
        storms.append(AgencyStorm(
            intl_id=intl_id, name=name.title(),
            season_year=2000 + int(yy), category=grade, points=points,
        ))
    if not storms:
        emit("  JTWC 当前无可解析的活跃西太平洋台风")
    return storms
