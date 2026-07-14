"""JTWC official best-track archive (historical, ATCF b-deck).

JTWC keys storms by its OWN annual cyclone number (not the WMO number), so its
tracks can't be merged by number. Each b-deck does carry the storm NAME, so the
caller matches JTWC storms to already-known typhoons by name + year.

Source: one zip per year, bwp{YEAR}.zip, each containing per-storm bwpNNYYYY.dat
b-deck files. b-deck line (comma-separated) fields used:
  2 YYYYMMDDHH   6 lat(tenths+N/S)   7 lon(tenths+E/W)
  8 vmax(kt)     9 mslp(hPa)        10 level(DB/TD/TS/TY/ST)   27 name
Each timestamp may repeat across wind-radii rows; we keep the first per time.
"""
from __future__ import annotations

import io
import zipfile
from datetime import datetime, timezone

import httpx

from crawler.sources._shared.common import AgencyStorm, ObsPoint, num, strongest_grade

ZIP_URL = "https://www.metoc.navy.mil/jtwc/products/best-tracks/{y}/{y}s-bwp/bwp{y}.zip"
EARLIEST_YEAR = 1945
_H = {"User-Agent": "Mozilla/5.0"}
_LEVEL = {
    "DB": "LOW", "LO": "LOW", "WV": "LOW", "SD": "LOW", "ED": "LOW", "IN": "LOW",
    "TD": "TD", "SS": "TS", "TS": "TS", "TY": "TY", "TC": "TS",
    "ST": "SuperTY", "HU": "TY", "EX": "L",
}


def _coord(token: str) -> float | None:
    """'262N' -> 26.2, '1181E' -> 118.1, '90S' -> -9.0."""
    token = token.strip()
    if len(token) < 2 or token[-1] not in "NSEW":
        return None
    try:
        val = int(token[:-1]) / 10.0
    except ValueError:
        return None
    return -val if token[-1] in "SW" else val


def _parse_dat(text: str):
    """One b-deck file -> (cyclone_number, name, season_year, [ObsPoint]).

    Handles both modern files (.dat, 30+ cols, storm name at col 27) and old
    files (.txt, ~9 cols, no name/level/mslp) — only time/lat/lon/vmax are
    guaranteed, everything past that is optional."""
    number = None
    name = None
    season = None
    by_time: dict[str, ObsPoint] = {}
    for line in text.splitlines():
        f = [x.strip() for x in line.split(",")]
        if len(f) < 9:
            continue
        t = f[2]
        try:
            season = int(t[:4])
            obs = datetime(season, int(t[4:6]), int(t[6:8]), int(t[8:10]), tzinfo=timezone.utc)
        except (ValueError, IndexError):
            continue
        if number is None and f[1].isdigit():
            number = int(f[1])
        lat, lon = _coord(f[6]), _coord(f[7])
        if lat is None or lon is None:
            continue
        if len(f) > 27:
            nm = f[27]
            if nm and nm.upper() not in ("INVEST", "UNNAMED", ""):
                name = nm.title()
        if t in by_time:
            continue  # dedupe repeated wind-radii rows
        mslp = num(f[9]) if len(f) > 9 else None
        grade = _LEVEL.get(f[10].upper()) if len(f) > 10 and f[10] else None
        by_time[t] = ObsPoint(
            obs_time=obs, lat=lat, lon=lon,
            wind_kt=num(f[8]) or None, pressure_hpa=mslp if mslp else None, grade=grade,
        )
    return number, name, season, list(by_time.values())


def fetch_storms(years, emit=lambda m: None) -> list[AgencyStorm]:
    """JTWC best-track storms for the given years. intl_id is preset to the
    number-based key (year + JTWC cyclone number); the caller prefers a name
    match and falls back to this key. Recent storms also carry a name."""
    out: list[AgencyStorm] = []
    for y in years:
        emit(f"  下载 JTWC {y} 最佳路径 …")
        try:
            r = httpx.get(ZIP_URL.format(y=y), headers=_H, timeout=90.0, follow_redirects=True)
            if r.status_code != 200 or r.content[:2] != b"PK":
                emit(f"  JTWC {y} 无存档（{r.status_code}）")
                continue
            zf = zipfile.ZipFile(io.BytesIO(r.content))
        except Exception as e:  # noqa: BLE001
            emit(f"  JTWC {y} 失败（{e}）")
            continue
        for member in zf.namelist():
            if not (member.endswith(".dat") or member.endswith(".txt")):
                continue
            try:
                number, name, season, points = _parse_dat(
                    zf.read(member).decode("utf-8", "replace"))
            except Exception:  # noqa: BLE001
                continue
            if not points:
                continue
            season = season or int(y)
            iid = f"{season % 100:02d}{number:02d}" if number else ""
            out.append(AgencyStorm(
                intl_id=iid, name=name, season_year=season,
                category=strongest_grade(p.grade for p in points), points=points,
                active=False,
            ))
    return out
