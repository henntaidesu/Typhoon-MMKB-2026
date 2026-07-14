"""Shared types + helpers for the public-information (公共情报) sources.

公共情报 is what authorities *announce to the public* about a typhoon —
official warnings/alerts (预警·警报), evacuation & emergency advisories, and
news/media reports. This is distinct from 受灾情报 (`disaster_common.DisasterRec`),
which records damage that actually occurred (casualties, economic loss).

Every public-information source parses its feed into the same `PublicInfoRec`
shape so `load.load_public_info` can attach it to a KB typhoon uniformly, using
the same matching modes as disasters (intl_id / name / time-space).

The hazard-subtype classifier and typhoon-name extractor are shared with the
disaster sources (see `disaster_common`) so both layers speak one vocabulary.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

# Canonical public-information kinds (PublicInfo.info_type).
INFO_WARNING = "warning"      # 官方预警/警报 (JMA 警報, HKO signal, NMC 预警)
INFO_ADVISORY = "advisory"    # 注意报 / lower-level advisory
INFO_EVACUATION = "evacuation"  # 避难指示 / 避难所开设 / 应急响应
INFO_NEWS = "news"            # 报道 / 社交媒体速报
INFO_BULLETIN = "bulletin"    # 官方情报公告 (非灾情统计)

INFO_TYPES = frozenset({INFO_WARNING, INFO_ADVISORY, INFO_EVACUATION, INFO_NEWS, INFO_BULLETIN})


@dataclass
class PublicInfoRec:
    info_type: str            # one of INFO_TYPES
    description: str          # human-readable body / headline (stored as body)
    source: str               # issuing authority short code (JMA / HKO / NMC / ...)
    # Matching hints — at least one path (intl_id / name / time) should be set.
    intl_id: str | None = None       # WMO number "YYNN" for a direct, exact match
    typhoon_name: str | None = None
    season_year: int | None = None
    event_time: datetime | None = None  # publish/issue time
    lat: float | None = None
    lon: float | None = None
    # Descriptive fields.
    category: str | None = None      # hazard subtype (flood/wind/storm_surge/…)
    agency: str | None = None        # issuing authority display name
    severity: str | None = None      # warning level as phrased by the source
    title: str | None = None
    source_url: str | None = None
    region_name: str | None = None   # human hint, e.g. "浙江 温州" / "沖縄"
