"""JMA / RSMC Tokyo official best-track archive (historical).

RSMC Tokyo assigns the international (WMO) number, so its best-track keys by the
same YYNN number as CMA — the tracks merge cleanly onto the same typhoon.

Source: the all-history archive bst_all.zip (one text file, 1951→present), and a
current-year bst{YEAR}.txt. Format (fixed-ish, whitespace-splittable):

  header:  66666 <intlNo> <nLines> <tcNo> <flag> <code> <NAME> <revDate>
  data:    <YYMMDDHH> 002 <grade> <lat*0.1> <lon*0.1> <pressure> <maxWindKt> ...

grade: 2=TD 3=TS 4=STS 5=TY 6=extratropical(L). Max wind is 0 for weak systems.
"""
from __future__ import annotations

import io
import re
import zipfile
from datetime import datetime, timezone

import httpx

from crawler.sources._shared.common import AgencyStorm, ObsPoint, num, season_of, strongest_grade

BASE = "https://www.jma.go.jp/jma/jma-eng/jma-center/rsmc-hp-pub-eg/Besttracks/"
ALL_ZIP = BASE + "bst_all.zip"
EARLIEST_YEAR = 1951
_H = {"User-Agent": "Mozilla/5.0"}
_GRADE = {"2": "TD", "3": "TS", "4": "STS", "5": "TY", "6": "L", "7": "TD", "9": "TD"}


def _download_all(emit=lambda m: None) -> str:
    r = httpx.get(ALL_ZIP, headers=_H, timeout=90.0, follow_redirects=True)
    r.raise_for_status()
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    return zf.read(zf.namelist()[0]).decode("utf-8", "replace")


def _parse_data(line: str) -> ObsPoint | None:
    parts = line.split()
    if len(parts) < 6:
        return None
    t = parts[0]
    try:
        yy = int(t[:2])
        year = (1900 if yy >= 49 else 2000) + yy
        obs = datetime(year, int(t[2:4]), int(t[4:6]), int(t[6:8]), tzinfo=timezone.utc)
        lat = num(parts[3]) / 10.0
        lon = num(parts[4]) / 10.0
    except (ValueError, TypeError, IndexError):
        return None
    wind = num(parts[6]) if len(parts) > 6 else None
    return ObsPoint(
        obs_time=obs, lat=lat, lon=lon,
        wind_kt=wind if wind else None,   # 0 kt = not estimated
        pressure_hpa=num(parts[5]), grade=_GRADE.get(parts[2]),
    )


def _parse_text(text: str) -> list[AgencyStorm]:
    storms: list[AgencyStorm] = []
    cur: AgencyStorm | None = None
    for line in text.splitlines():
        if line.startswith("66666"):
            if cur and cur.points:
                storms.append(cur)
            parts = line.split()
            intl_id = parts[1] if len(parts) > 1 else ""
            m = re.search(r"[A-Z][A-Z\-]{1,}", line[20:])  # name sits after the numeric fields
            name = m.group(0).title() if m else None
            cur = AgencyStorm(
                intl_id=intl_id,
                name=(name if name and name.upper() != "UNNAMED" else None),
                season_year=season_of(intl_id) if intl_id else None,
                category=None, points=[], active=False,
            )
        elif cur is not None:
            p = _parse_data(line)
            if p:
                cur.points.append(p)
    if cur and cur.points:
        storms.append(cur)
    for s in storms:
        s.category = strongest_grade(pt.grade for pt in s.points)
    return storms


def fetch_storms(years=None, emit=lambda m: None) -> list[AgencyStorm]:
    """All JMA best-track storms (1951→present), optionally filtered to `years`."""
    emit("下载 JMA 最佳路径存档（bst_all.zip）…")
    storms = _parse_text(_download_all(emit))
    if years:
        wanted = {int(y) for y in years}
        storms = [s for s in storms if s.season_year in wanted]
    emit(f"JMA 最佳路径解析到 {len(storms)} 个台风")
    return storms
