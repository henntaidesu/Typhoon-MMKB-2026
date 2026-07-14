"""CMA 中央气象台 预警 (nmc.cn) — official Chinese meteorological warnings.

These are 公共情报 (public information the authority *announces*), NOT 受灾情报
(damage that occurred): the NMC publishes real-time 预警信号 for the secondary
hazards a typhoon spawns inland — 暴雨 (heavy rain / flooding), 地质灾害气象风险
(landslide risk), 风暴潮 (storm surge), 大风 (gale). Each becomes a PublicInfoRec
(info_type=warning). They are region + time stamped but DON'T name the storm, so
the loader ties each to whichever typhoon was active there & then (time/space
match).

  http://www.nmc.cn/rest/findAlarm  -> current national warning list (JSON)

This is a real-time feed (current warnings only), matching the live nature of the
CMA/JMA/JTWC track sources. Offline test:
  python crawler/sources/china/disaster/government/nmc_alarm.py --preview
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

from crawler.sources._shared.disaster_common import classify_type  # noqa: E402
from crawler.sources._shared.public_common import INFO_WARNING, PublicInfoRec  # noqa: E402

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
    """Normalise findAlarm's response to the alarm row list. Current shape is
    {"data": {"page": {"list": [...]}}}; older/simpler shapes are handled too."""
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    # Descend common wrappers until we find a list named "list"/"rows".
    for node in (data, data.get("data"), (data.get("data") or {}).get("page")
                 if isinstance(data.get("data"), dict) else None):
        if isinstance(node, dict):
            for key in ("list", "rows"):
                if isinstance(node.get(key), list):
                    return node[key]
        if isinstance(node, list):
            return node
    return []


# CN province -> approximate centroid (lat, lon). NMC alarm titles begin with the
# issuing locality ("江西省抚州市…"), so we geocode by the leading province name.
# This lets the loader spatially match a warning to a nearby typhoon corridor
# instead of tying every current alarm to any active storm.
_PROVINCE_LL: dict[str, tuple[float, float]] = {
    "北京": (39.90, 116.41), "天津": (39.13, 117.20), "河北": (38.04, 114.51),
    "山西": (37.87, 112.55), "内蒙古": (40.82, 111.75), "辽宁": (41.84, 123.43),
    "吉林": (43.90, 125.33), "黑龙江": (45.80, 126.64), "上海": (31.23, 121.47),
    "江苏": (32.06, 118.80), "浙江": (30.27, 120.15), "安徽": (31.86, 117.28),
    "福建": (26.08, 119.30), "江西": (28.68, 115.89), "山东": (36.67, 117.02),
    "河南": (34.76, 113.65), "湖北": (30.55, 114.34), "湖南": (28.23, 112.94),
    "广东": (23.13, 113.27), "广西": (22.82, 108.32), "海南": (20.02, 110.35),
    "重庆": (29.56, 106.55), "四川": (30.65, 104.08), "贵州": (26.65, 106.63),
    "云南": (25.04, 102.71), "西藏": (29.65, 91.13), "陕西": (34.26, 108.95),
    "甘肃": (36.06, 103.83), "青海": (36.62, 101.78), "宁夏": (38.49, 106.23),
    "新疆": (43.83, 87.62), "香港": (22.30, 114.17), "澳门": (22.20, 113.55),
    "台湾": (23.70, 120.96),
}


def _province_ll(title: str) -> tuple[str | None, float | None, float | None]:
    for name, (lat, lon) in _PROVINCE_LL.items():
        if title.startswith(name):
            return name, lat, lon
    return None, None, None


def _parse_dt(s) -> datetime | None:
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S",
                "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(str(s).strip(), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None


def fetch_alarms(page_size: int = 300, emit=lambda m: None) -> list[dict]:
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


def parse_alarms(rows: list[dict]) -> list[PublicInfoRec]:
    recs: list[PublicInfoRec] = []
    for row in rows:
        # Hazard type is embedded in the title (e.g. "…发布暴雨黄色预警信号");
        # there is no separate signaltype field in the current feed.
        title = _pick(row, "title", "headline", "name") or ""
        signal = _pick(row, "signaltype", "signalType", "type") or title
        if not any(sig in str(signal) or sig in title for sig in _SECONDARY_SIGNALS):
            continue
        ts = _parse_dt(_pick(row, "issuetime", "issueTime", "sendTime", "effective"))
        province, lat, lon = _province_ll(title)
        level = _pick(row, "signallevel", "signalLevel", "level") or ""
        url = _pick(row, "url", "link")
        if url and str(url).startswith("/"):
            url = "http://www.nmc.cn" + url
        recs.append(PublicInfoRec(
            info_type=INFO_WARNING,
            category=classify_type(f"{signal} {title}"),
            agency="中央气象台",
            severity=str(level) or None,
            title=title[:400] or None,
            event_time=ts,
            lat=lat, lon=lon,
            description=f"[中央气象台预警] {level} {title}".strip()[:800],
            source="NMC",
            source_url=url,
            region_name=province,
        ))
    return recs


def collect(emit=lambda m: None) -> list[PublicInfoRec]:
    rows = fetch_alarms(emit=emit)
    recs = parse_alarms(rows)
    emit(f"  NMC 预警: {len(rows)} alarms -> {len(recs)} public-info (warning) records")
    return recs


def _preview() -> None:
    recs = collect(emit=lambda m: print(f"[nmc_alarm]{m}"))
    for r in recs[:15]:
        print(f"  {r.info_type:8s} {r.category or '':10s} {r.region_name or '(?)':10s} "
              f"{r.event_time} | {r.description[:50]}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--preview", action="store_true")
    args = ap.parse_args()
    if args.preview:
        _preview()
    else:
        print("Use --preview for offline fetch; DB load runs via crawler/pipeline.py")
