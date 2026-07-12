"""IBTrACS (NOAA) West Pacific best-track ingest.

Downloads the IBTrACS CSV, filters to the West Pacific (WP) basin and the
configured seasons, groups rows by storm id (SID) into Typhoon + TrackPoint
records, derives a coarse affected-region polygon from the track, and upserts
everything into the knowledge base.

The parsing is separated from DB loading so it can be validated offline:
    python -m crawler.sources.ibtracs --preview
"""
from __future__ import annotations

import argparse
import io
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone

import sys

import httpx
import pandas as pd

# Make backend/ importable (config + ORM models live there, single source).
_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

# IBTrACS access. 'last3years' is small & fast; 'WP' is the full basin archive.
IBTRACS_URLS = {
    "last3years": "https://www.ncei.noaa.gov/data/international-best-track-archive-for-climate-stewardship-ibtracs/v04r01/access/csv/ibtracs.last3years.list.v04r01.csv",
    "WP": "https://www.ncei.noaa.gov/data/international-best-track-archive-for-climate-stewardship-ibtracs/v04r01/access/csv/ibtracs.WP.list.v04r01.csv",
    "since1980": "https://www.ncei.noaa.gov/data/international-best-track-archive-for-climate-stewardship-ibtracs/v04r01/access/csv/ibtracs.since1980.list.v04r01.csv",
}

CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", ".cache")

# Columns we care about (IBTrACS has ~170). Row 0 of the CSV body is a units row.
USECOLS = [
    "SID", "SEASON", "BASIN", "NUMBER", "NAME", "ISO_TIME", "NATURE",
    "LAT", "LON", "WMO_WIND", "WMO_PRES", "USA_WIND", "USA_PRES",
    "USA_SSHS", "STORM_SPEED", "STORM_DIR",
]


@dataclass
class TrackObs:
    obs_time: datetime
    lat: float
    lon: float
    wind_kt: float | None
    pressure_hpa: float | None
    grade: str | None
    move_dir: float | None
    move_speed: float | None


@dataclass
class TyphoonRec:
    intl_id: str          # SEASON+NUMBER, e.g. "2306"
    sid: str
    name: str | None
    season_year: int
    category: str | None
    track: list[TrackObs] = field(default_factory=list)

    @property
    def max_wind_kt(self) -> float | None:
        vals = [t.wind_kt for t in self.track if t.wind_kt is not None]
        return max(vals) if vals else None

    @property
    def min_pressure_hpa(self) -> float | None:
        vals = [t.pressure_hpa for t in self.track if t.pressure_hpa is not None]
        return min(vals) if vals else None

    @property
    def start_time(self) -> datetime | None:
        return self.track[0].obs_time if self.track else None

    @property
    def end_time(self) -> datetime | None:
        return self.track[-1].obs_time if self.track else None


def _num(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f  # drop NaN


def _first(*vals) -> float | None:
    for v in vals:
        n = _num(v)
        if n is not None:
            return n
    return None


def _sshs_category(sshs) -> str | None:
    """Map USA Saffir-Simpson code to a coarse category label."""
    c = _num(sshs)
    if c is None:
        return None
    c = int(c)
    return {
        -5: "DB", -4: "DB", -3: "DB", -2: "TD", -1: "TS",
        0: "TS", 1: "TY1", 2: "TY2", 3: "TY3", 4: "TY4", 5: "TY5",
    }.get(c)


def download_csv(source: str = "last3years", force: bool = False) -> str:
    """Download (and cache) the IBTrACS CSV, return the local path."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, f"ibtracs.{source}.csv")
    if os.path.exists(path) and not force:
        return path
    url = IBTRACS_URLS[source]
    print(f"[ibtracs] downloading {url}")
    with httpx.stream("GET", url, timeout=120.0, follow_redirects=True) as r:
        r.raise_for_status()
        with open(path, "wb") as f:
            for chunk in r.iter_bytes():
                f.write(chunk)
    print(f"[ibtracs] cached -> {path} ({os.path.getsize(path)//1024} KB)")
    return path


def parse(csv_path: str, years: list[int] | None = None) -> list[TyphoonRec]:
    """Parse the CSV into TyphoonRec objects (WP basin, optional year filter)."""
    df = pd.read_csv(csv_path, usecols=USECOLS, skiprows=[1], low_memory=False,
                     keep_default_na=True, na_values=[" ", ""])
    df = df[df["BASIN"] == "WP"]
    if years:
        df = df[df["SEASON"].astype("Int64").isin(years)]
    df = df.sort_values(["SID", "ISO_TIME"])

    records: list[TyphoonRec] = []
    for sid, g in df.groupby("SID"):
        first = g.iloc[0]
        season = int(first["SEASON"])
        number = int(first["NUMBER"]) if not pd.isna(first["NUMBER"]) else 0
        name = None if str(first["NAME"]).upper() in ("NOT_NAMED", "NAN") else str(first["NAME"]).title()
        intl_id = f"{season % 100:02d}{number:02d}"  # e.g. 2306
        rec = TyphoonRec(intl_id=intl_id, sid=str(sid), name=name,
                         season_year=season, category=None)
        cat_codes: list[str] = []
        for _, row in g.iterrows():
            lat, lon = _num(row["LAT"]), _num(row["LON"])
            if lat is None or lon is None:
                continue
            wind = _first(row["USA_WIND"], row["WMO_WIND"])
            pres = _first(row["USA_PRES"], row["WMO_PRES"])
            grade = _sshs_category(row["USA_SSHS"]) or (str(row["NATURE"]) if not pd.isna(row["NATURE"]) else None)
            if grade:
                cat_codes.append(grade)
            rec.track.append(TrackObs(
                obs_time=datetime.fromisoformat(str(row["ISO_TIME"])).replace(tzinfo=timezone.utc),
                lat=lat, lon=lon, wind_kt=wind, pressure_hpa=pres, grade=grade,
                move_dir=_num(row["STORM_DIR"]), move_speed=_num(row["STORM_SPEED"]),
            ))
        # Coarse category = strongest grade seen.
        if cat_codes:
            rec.category = max(cat_codes)
        if rec.track:
            records.append(rec)
    return records


def _preview(source: str, years: list[int] | None) -> None:
    path = download_csv(source)
    recs = parse(path, years)
    print(f"[ibtracs] parsed {len(recs)} typhoons (basin=WP, years={years or 'all'})")
    for r in recs[:8]:
        print(f"  {r.intl_id} {r.name or '(unnamed)':16s} {r.season_year} "
              f"pts={len(r.track):3d} maxwind={r.max_wind_kt} minpres={r.min_pressure_hpa} cat={r.category}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="last3years", choices=list(IBTRACS_URLS))
    ap.add_argument("--years", type=int, nargs="*", default=None)
    ap.add_argument("--preview", action="store_true", help="parse & print, no DB")
    args = ap.parse_args()
    if args.preview:
        _preview(args.source, args.years)
    else:
        print("Use --preview for offline parse; DB load runs via crawler/pipeline.py")
