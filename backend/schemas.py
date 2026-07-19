"""Pydantic response models."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class TyphoonBrief(BaseModel):
    id: int
    intl_id: str
    name: str | None
    season_year: int | None
    category: str | None
    max_wind_kt: float | None
    min_pressure_hpa: float | None
    start_time: datetime | None
    end_time: datetime | None
    is_active: bool | None = None
    disaster_count: int | None = None
    distance: float | None = None  # semantic distance when returned from search

    class Config:
        from_attributes = True


class TrackPointOut(BaseModel):
    obs_time: datetime
    lat: float
    lon: float
    wind_kt: float | None
    pressure_hpa: float | None
    grade: str | None

    class Config:
        from_attributes = True


class SemanticQuery(BaseModel):
    q: str
    k: int = 10
    # Optional spatio-temporal narrowing. When any of these is set the search
    # becomes a semantic *join* (時空間 x 意味): candidates are first restricted
    # to typhoons whose track falls in the window, then ranked semantically.
    bbox: str | None = None  # "minLon,minLat,maxLon,maxLat"
    date_from: datetime | None = None
    date_to: datetime | None = None
    # Cosine-distance cutoff; None disables it and restores raw Top-K behaviour.
    max_distance: float | None = None
