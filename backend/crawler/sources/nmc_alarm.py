"""CMA 中央气象台 预警 (nmc.cn) — official Chinese meteorological warnings.

The NMC (same authority as the typhoon.nmc.cn live-track feed already used) also
publishes real-time 预警信号 for the secondary hazards a typhoon spawns inland:
暴雨 (heavy rain / flooding), 地质灾害气象风险 (landslide risk), 风暴潮 (storm
surge), 大风 (gale). These warnings are region + time stamped but DON'T name the
storm, so the loader ties each to whichever typhoon was active there & then
(time/space match).

  http://www.nmc.cn/rest/findAlarm  -> current national warning list (JSON)

This is a real-time feed (current warnings only), matching the live nature of the
CMA/JMA/JTWC track sources. Offline test:
  python crawler/sources/nmc_alarm.py --preview
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

from crawler.sources.disaster_common import DisasterRec, classify_type  # noqa: E402

ALARM_URL = "http://www.nmc.cn/rest/findAlarm"
_H = {"User-Agent": "Mozilla/5.0", "Referer": "http://www.nmc.cn/"}

# Only warnings whose hazard is plausibly a typhoon secondary disaster. Typhoon
# (台风) itself is a primary-track warning, so it's excluded here.
_SECONDARY_SIGNALS = ("暴雨", "地质灾害", "山洪", "风暴潮", "海浪", "大风", "洪水")


def _get_json(url: str, params: dict | None = None):
    r = httpx.get(url, headers=_H, params=params, timeout=45.0, follow_redirects=True)
    r.raise_for_status()
    # Endpoint is UTF-8 JSON; fall back to GBK just in case.
    try:
        return r.json()
    except Exception:  # noqa: BLE001
        return httpx.Response(200, content=r.content.decode("gbk", "replace").encode()).json()


def _as_list(data) -> list[dict]:
    """findAlarm has returned a few shapes over time; normalise to a row list."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("data", "list", "rows"):
            v = data.get(key)
            if isinstance(v, list):
                return v
            if isinstance(v, dict) and isinstance(v.get("list"), list):
                return v["list"]
    return []


def _parse_dt(s) -> datetime | None:
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(str(s)[:19], fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None


def fetch_alarms(page_size: int = 100, emit=lambda m: None) -> list[dict]:
    try:
        data = _get_json(ALARM_URL, {"pageNo": 1, "pageSize": page_size})
    except Exception as e:  # noqa: BLE001
        emit(f"  NMC 预警获取失败（{e}）")
        return []
    return _as_list(data)


def _pick(row: dict, *keys):
    for k in keys:
        v = row.get(k)
        if v:
            return v
    return None


def parse_alarms(rows: list[dict]) -> list[DisasterRec]:
    recs: list[DisasterRec] = []
    for row in rows:
        title = _pick(row, "title", "headline", "name") or ""
        signal = _pick(row, "signaltype", "signalType", "type") or title
        if not any(sig in str(signal) or sig in title for sig in _SECONDARY_SIGNALS):
            continue
        ts = _parse_dt(_pick(row, "issuetime", "issueTime", "sendTime", "effective"))
        province = _pick(row, "province", "area", "region")
        level = _pick(row, "signallevel", "signalLevel", "level") or ""
        url = _pick(row, "url", "link")
        if url and str(url).startswith("/"):
            url = "http://www.nmc.cn" + url
        recs.append(DisasterRec(
            disaster_type=classify_type(f"{signal} {title}"),
            event_time=ts,
            description=f"[中央气象台预警] {level} {title}".strip()[:800],
            source="NMC",
            source_url=url,
            region_name=province,
        ))
    return recs


def collect(emit=lambda m: None) -> list[DisasterRec]:
    rows = fetch_alarms(emit=emit)
    recs = parse_alarms(rows)
    emit(f"  NMC 预警: {len(rows)} alarms -> {len(recs)} secondary-hazard records")
    return recs


def _preview() -> None:
    recs = collect(emit=lambda m: print(f"[nmc_alarm]{m}"))
    for r in recs[:15]:
        print(f"  {r.disaster_type:14s} {r.region_name or '(?)':10s} "
              f"{r.event_time} | {r.description[:60]}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--preview", action="store_true")
    args = ap.parse_args()
    if args.preview:
        _preview()
    else:
        print("Use --preview for offline fetch; DB load runs via crawler/pipeline.py")
