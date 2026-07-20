"""CMA 中央气象台 (typhoon.nmc.cn) — official Chinese real-time typhoon feed.

Provides the '实况路径' (actual observed track), for the current season (live,
via list_default) and for any archived year (list_{year}, back to 1949). This
is the richest official live-track source for the West Pacific.

The feed is JSONP; list endpoints are GBK-encoded, the view endpoint UTF-8, so
decoding is auto-detected. Access is split into a cheap *roster* (one request
per year -> which storms exist + status) and an expensive *view* (one request
per storm -> its full track). This split lets the caller fetch rosters to plan
an incremental/batched backfill and only pull views for the storms it wants.

Position array layout (index -> meaning):
  0 pointId  1 timeStr  2 epochMs  3 grade  4 lon  5 lat
  6 pressure(hPa)  7 wind(m/s)  8 moveDir  9 moveSpeed(km/h)  10 windRadii  11 forecasts
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import httpx

from crawler.sources._shared.common import (
    AgencyStorm, ObsPoint, compass_to_deg, ms_to_kt, num, season_of, strongest_grade,
)

LIST_URL = "http://typhoon.nmc.cn/weatherservice/typhoon/jsons/list_default"
LIST_YEAR_URL = "http://typhoon.nmc.cn/weatherservice/typhoon/jsons/list_{year}"
VIEW_URL = "http://typhoon.nmc.cn/weatherservice/typhoon/jsons/view_{id}"
EARLIEST_YEAR = 1949  # CMA archive reaches back to 1949
_H = {"User-Agent": "Mozilla/5.0", "Referer": "http://typhoon.nmc.cn/"}
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


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


# --- Roster (cheap: storm list + status, no track) --------------------------
def _name_of(row) -> str | None:
    name_en = row[1] if len(row) > 1 else None
    return None if not name_en or str(name_en).lower() in ("nameless", "unnamed") \
        else str(name_en).title()


def _name_cn_of(row) -> str | None:
    """CMA publishes the Chinese name beside the English one (row[2]: "BAVI" ->
    "巴威"). Chinese official bulletins quote storms by this name and nothing
    else, so it is the only key that can tie 台风“巴威” to storm 2609."""
    cn = row[2] if len(row) > 2 else None
    cn = str(cn).strip() if cn else ""
    return cn or None


def _parse_roster(rows, year: int | None = None) -> list[dict]:
    """Rows -> roster entries. Storms with a real WMO 编号 (4-digit YYNN) key on
    it. Pre-1963 storms carry the "1900" no-number sentinel; when a query year is
    known we still ingest them under a synthesized YYNN key (year + chronological
    rank from the global sequence field row[5]) so no old history is lost."""
    out = []
    unnumbered = []
    for row in rows:
        tfbh = str(row[3]) if len(row) > 3 and row[3] is not None else ""
        if tfbh.isdigit() and len(tfbh) == 4 and int(tfbh[2:]) >= 1:
            status = str(row[7]).lower() if len(row) > 7 and row[7] is not None else ""
            out.append({
                "intl_id": tfbh, "internal_id": row[0], "name": _name_of(row),
                "name_cn": _name_cn_of(row),
                "season": season_of(tfbh), "active": status == "start",
            })
        elif year is not None and tfbh == "1900":
            unnumbered.append(row)  # old storm, no WMO number -> synthesize below
        # else: 8-digit placeholder (unnamed depression) -> skip

    if unnumbered and year is not None:
        yy = year % 100
        # row[5] is a global running sequence; ascending = chronological in-year.
        unnumbered.sort(key=lambda r: r[5] if len(r) > 5 and isinstance(r[5], int) else r[0])
        for i, row in enumerate(unnumbered, 1):
            out.append({
                "intl_id": f"{yy:02d}{i:02d}", "internal_id": row[0], "name": _name_of(row),
                "name_cn": _name_cn_of(row),
                "season": year, "active": False,
            })
    return out


def fetch_current_roster(emit=lambda m: None) -> list[dict]:
    """Current-season roster with live start/stop status (list_default)."""
    try:
        rows = _jsonp(_get(LIST_URL)).get("typhoonList", [])
    except Exception as e:  # noqa: BLE001
        emit(f"  当季列表获取失败（{e}）")
        return []
    return _parse_roster(rows)


def fetch_year_roster(year: int, emit=lambda m: None) -> list[dict]:
    """Archived roster for one year (list_{year}); all storms are ended."""
    try:
        rows = _jsonp(_get(LIST_YEAR_URL.format(year=int(year)))).get("typhoonList", [])
    except Exception as e:  # noqa: BLE001
        emit(f"  {year} 年列表获取失败（{e}）")
        return []
    return _parse_roster(rows, year=int(year))


# --- View (expensive: one storm's full observed track) ----------------------
def _points(raw) -> list[ObsPoint]:
    points: list[ObsPoint] = []
    for p in raw:
        try:
            # Epoch + timedelta is OS-independent; datetime.fromtimestamp raises
            # OSError [Errno 22] on Windows for out-of-range/garbage epoch values.
            obs = _EPOCH + timedelta(milliseconds=int(p[2]))
            lon, lat = float(p[4]), float(p[5])
        except (TypeError, ValueError, IndexError, OverflowError, OSError):
            continue
        points.append(ObsPoint(
            obs_time=obs, lat=lat, lon=lon,
            wind_kt=ms_to_kt(num(p[7])) if len(p) > 7 else None,
            pressure_hpa=num(p[6]) if len(p) > 6 else None,
            grade=p[3] if len(p) > 3 else None,
            move_dir=compass_to_deg(p[8]) if len(p) > 8 else None,
            move_speed=num(p[9]) if len(p) > 9 else None,
        ))
    return points


def fetch_view(entry: dict) -> AgencyStorm | None:
    """Fetch one roster entry's full observed track -> AgencyStorm."""
    try:
        vd = _jsonp(_get(VIEW_URL.format(id=entry["internal_id"])))
        arr = vd["typhoon"]
        raw = arr[8] if len(arr) > 8 and isinstance(arr[8], list) else []
    except Exception:  # noqa: BLE001
        return None
    points = _points(raw)
    if not points:
        return None
    return AgencyStorm(
        intl_id=entry["intl_id"], name=entry["name"],
        name_cn=entry.get("name_cn"), season_year=entry["season"],
        category=strongest_grade(pt.grade for pt in points), points=points,
        active=entry["active"],
    )


def fetch_storms(years=None, emit=lambda m: None) -> list[AgencyStorm]:
    """Convenience: fetch full tracks for a scope in one shot (no batching).
    `years` empty -> current season; else each archived year."""
    rosters = ([("cur", fetch_current_roster(emit))] if not years
               else [(y, fetch_year_roster(y, emit)) for y in years])
    storms: list[AgencyStorm] = []
    for _, roster in rosters:
        for entry in roster:
            st = fetch_view(entry)
            if st:
                storms.append(st)
    return storms
