"""Shared types + helpers for the secondary-disaster (次生灾害) sources.

Every disaster source (GDACS / ReliefWeb / NMC 预警 / 应急管理部 / 気象庁·消防庁)
parses its own feed into the same `DisasterRec` shape so `load.load_disasters`
can attach it to a KB typhoon uniformly.

Two matching modes are supported by the loader, driven by what a record carries:
  - name match  : `typhoon_name` (+ optional `season_year`) — international
                  reports that mention the storm by name (GDACS, ReliefWeb).
  - time/space  : `event_time` (+ optional lat/lon) — official warnings /
                  bulletins that DON'T name the storm (NMC 预警, some 通报); the
                  loader ties them to whichever typhoon was active there & then.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime


@dataclass
class DisasterRec:
    disaster_type: str
    description: str
    source: str
    # Matching hints — at least one path (intl_id / name / time) should be set.
    intl_id: str | None = None  # WMO number "YYNN" for a direct, exact match
    typhoon_name: str | None = None
    season_year: int | None = None
    # True when the record describes a cyclone that identifies ITSELF by name
    # (a GDACS TC event), as opposed to a bulletin that merely happened near a
    # storm (NMC 预警). For these, a failed name match means the cyclone is not
    # in this KB — usually another basin — and the loader must NOT fall back to
    # time/space attribution, which would hang an Atlantic hurricane on whatever
    # typhoon was active that week.
    named_event: bool = False
    event_time: datetime | None = None
    lat: float | None = None
    lon: float | None = None
    # Impact figures (best-effort; parsed from official text where present).
    casualties: int | None = None
    economic_loss_usd: float | None = None
    source_url: str | None = None
    region_name: str | None = None  # human hint, e.g. "浙江 温州" / "Kagoshima"


# --- Rough FX for converting official loss figures to the stored USD field ----
# These are coarse (annual-average order) constants; the exact rate on the event
# date is not tracked. The original figure is preserved in the description.
CNY_USD = 0.14
JPY_USD = 0.0067

_YI = 10 ** 8  # 亿 / 億


# --- Impact extraction from Chinese / Japanese official bulletin text ---------
_RE_DEAD_CN = re.compile(r"(\d+)\s*(?:人|名)?(?:死亡|遇难|罹难|死者)")
_RE_DEAD_JP = re.compile(r"死者\s*(\d+)\s*(?:人|名)")
_RE_MISSING_CN = re.compile(r"(\d+)\s*(?:人|名)?(?:失踪|下落不明)")
_RE_MISSING_JP = re.compile(r"行方不明(?:者)?\s*(\d+)\s*(?:人|名)")
# 直接经济损失 12.3 亿元 / 経済損失 45 億円
_RE_LOSS_CN = re.compile(r"(?:直接)?经济损失[^\d]{0,8}([\d.]+)\s*亿元")
_RE_LOSS_JP = re.compile(r"(?:被害|経済損失)[額]?[^\d]{0,8}([\d.]+)\s*億円")


def _first_int(pat: re.Pattern, text: str) -> int | None:
    m = pat.search(text or "")
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def extract_casualties(text: str) -> int | None:
    """Deaths (+ missing) reported in an official CN/JP bulletin, if any."""
    dead = _first_int(_RE_DEAD_CN, text) or _first_int(_RE_DEAD_JP, text)
    missing = _first_int(_RE_MISSING_CN, text) or _first_int(_RE_MISSING_JP, text)
    if dead is None and missing is None:
        return None
    return (dead or 0) + (missing or 0)


def extract_loss_usd(text: str) -> float | None:
    """Direct economic loss (亿元 / 億円) converted to a rough USD figure."""
    m = _RE_LOSS_CN.search(text or "")
    if m:
        try:
            return round(float(m.group(1)) * _YI * CNY_USD, 0)
        except ValueError:
            pass
    m = _RE_LOSS_JP.search(text or "")
    if m:
        try:
            return round(float(m.group(1)) * _YI * JPY_USD, 0)
        except ValueError:
            pass
    return None


# --- Disaster-type classification from free text ------------------------------
# Maps the canonical SecondaryDisaster.disaster_type values used across the KB.
_TYPE_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("storm_surge",  ("风暴潮", "storm surge", "高潮", "surge")),
    ("flood",        ("洪涝", "洪水", "山洪", "内涝", "flood", "inundation", "浸水", "大雨",
                      "暴雨", "heavy rain", "rainstorm", "torrential")),
    ("landslide",    ("滑坡", "泥石流", "地质灾害", "山体", "landslide", "landslip",
                      "mudslide", "debris flow", "土砂", "崖崩")),
    ("infrastructure", ("停电", "断电", "倒塌", "基础设施", "power outage", "infrastructure", "停電", "倒壊")),
    ("casualty",     ("死亡", "遇难", "伤亡", "casualt", "死者", "行方不明", "被災")),
    ("wind_impact",  ("大风", "狂风", "强风", "wind", "暴风")),
]


def classify_type(text: str, default: str = "wind_impact") -> str:
    t = (text or "").lower()
    orig = text or ""
    for label, kws in _TYPE_KEYWORDS:
        for kw in kws:
            if kw in orig or kw.lower() in t:
                return label
    return default


# --- Typhoon-name extraction from a report title/disaster name ----------------
# ReliefWeb disaster names look like "Philippines: Typhoon Doksuri - Jul 2023";
# CN bulletins like "台风“杜苏芮”".
# The trailing [A-Za-z] stops the capture eating the hyphen that separates a
# GDACS name from its season ("MAN-YI-13" would otherwise yield "Man-Yi-").
_RE_NAME_EN = re.compile(
    r"(?:typhoon|super typhoon|tropical storm|cyclone)\s+([A-Za-z](?:[A-Za-z\-]*[A-Za-z])?)", re.I)

# The Chinese name must come from inside the quotes, never from the text next to
# the word 台风. Anchoring on 台风 and taking the characters that follow reads
# 防台风四级应急响应 as a storm called 四级应急响应, and 指导浙江做好“巴威”台风灾害救助工作
# as one called 灾害救助工作 — both of which then fail to resolve and send the
# record down the guess-by-time path. Bulletins always quote the name, and it
# may sit on either side of 台风, so match the quotes and accept both orders.
_CJK = r"一-鿿぀-ヿ"
_QUOTED = r"[“\"'「『]([" + _CJK + r"A-Za-z]{2,6})[”\"'」』]"
_RE_NAME_CN = re.compile(r"(?:台风|台風|颱風)\s*" + _QUOTED + r"|" + _QUOTED + r"\s*(?:台风|台風|颱風)")


def extract_typhoon_name(text: str) -> str | None:
    m = _RE_NAME_EN.search(text or "")
    if m:
        return m.group(1).strip().title()
    m = _RE_NAME_CN.search(text or "")
    if m:
        # One alternative captures before 台风, the other after; exactly one fires.
        return (m.group(1) or m.group(2)).strip()
    return None
