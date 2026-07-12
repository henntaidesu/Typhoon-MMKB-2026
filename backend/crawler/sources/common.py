"""Shared types + helpers for the official real-time (实况) track sources.

Every agency source (CMA / JMA / JTWC) parses its own feed into the same
`AgencyStorm` shape so the loader can write agency-tagged actual tracks
uniformly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class ObsPoint:
    """One observed (实况) track fix from an agency."""
    obs_time: datetime
    lat: float
    lon: float
    wind_kt: float | None = None
    pressure_hpa: float | None = None
    grade: str | None = None
    move_dir: float | None = None    # degrees (0=N, 90=E), or None
    move_speed: float | None = None  # km/h


@dataclass
class AgencyStorm:
    intl_id: str                 # WMO number, e.g. "2609" (year 26, storm 09)
    name: str | None
    season_year: int | None
    category: str | None
    points: list[ObsPoint] = field(default_factory=list)
    active: bool | None = None   # still ongoing? (None = source doesn't say)


def num(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f  # drop NaN


def ms_to_kt(ms: float | None) -> float | None:
    return round(ms * 1.943844, 1) if ms is not None else None


# 16-point English compass -> degrees (used by CMA move direction).
_COMPASS = {
    "N": 0, "NNE": 22.5, "NE": 45, "ENE": 67.5, "E": 90, "ESE": 112.5,
    "SE": 135, "SSE": 157.5, "S": 180, "SSW": 202.5, "SW": 225, "WSW": 247.5,
    "W": 270, "WNW": 292.5, "NW": 315, "NNW": 337.5,
}


def compass_to_deg(v) -> float | None:
    if v is None:
        return None
    return _COMPASS.get(str(v).strip().upper())


# Grade strength ordering, weakest -> strongest, for a coarse storm category.
_GRADE_RANK = {
    "LOW": 0, "TD": 1, "TS": 2, "STS": 3, "TY": 4, "STY": 5,
    "SuperTY": 6, "SUPERTY": 6,
}


def strongest_grade(grades) -> str | None:
    best = None
    best_rank = -1
    for g in grades:
        if not g:
            continue
        r = _GRADE_RANK.get(g, _GRADE_RANK.get(str(g).upper(), 0))
        if r > best_rank:
            best_rank, best = r, g
    return best
