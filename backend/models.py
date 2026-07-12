"""Typhoon MMKB — knowledge base data structure defined entirely as
SQLAlchemy ORM database objects (no .sql files).

Spatial columns use geoalchemy2.Geometry (PostGIS); semantic columns use
pgvector.sqlalchemy.Vector. Indexes are declared as SQLAlchemy Index objects,
so `Base.metadata.create_all()` builds the full schema including GiST spatial
indexes and IVFFlat vector indexes.
"""
from __future__ import annotations

from datetime import datetime

from geoalchemy2 import Geometry
from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from config import EMBEDDING_DIM, SRID


class Base(DeclarativeBase):
    pass


class Typhoon(Base):
    """A single tropical cyclone (West Pacific basin), the top knowledge unit."""

    __tablename__ = "typhoon"

    id: Mapped[int] = mapped_column(primary_key=True)
    intl_id: Mapped[str] = mapped_column(String(16), unique=True, index=True)  # e.g. "2306"
    sid: Mapped[str | None] = mapped_column(String(32), index=True)  # IBTrACS storm id
    name: Mapped[str | None] = mapped_column(String(64))
    name_jp: Mapped[str | None] = mapped_column(String(64))
    name_cn: Mapped[str | None] = mapped_column(String(64))
    season_year: Mapped[int | None] = mapped_column(Integer, index=True)
    category: Mapped[str | None] = mapped_column(String(32))  # e.g. "TY", "STS"
    max_wind_kt: Mapped[float | None] = mapped_column(Float)
    min_pressure_hpa: Mapped[float | None] = mapped_column(Float)
    start_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source: Mapped[str | None] = mapped_column(String(32))
    # Whether the typhoon is still ongoing (from CMA start/stop status, or any
    # agency still listing it as an active storm). None = unknown / not crawled.
    is_active: Mapped[bool | None] = mapped_column(Boolean, index=True)

    # Semantic layer: a readable multilingual summary and its embedding.
    summary_text: Mapped[str | None] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))

    track_points: Mapped[list["TrackPoint"]] = relationship(
        back_populates="typhoon", cascade="all, delete-orphan"
    )
    disasters: Mapped[list["SecondaryDisaster"]] = relationship(
        back_populates="typhoon", cascade="all, delete-orphan"
    )
    regions: Mapped[list["AffectedRegion"]] = relationship(
        back_populates="typhoon", cascade="all, delete-orphan"
    )
    media: Mapped[list["MediaAsset"]] = relationship(
        back_populates="typhoon", cascade="all, delete-orphan"
    )
    # Derived geographic impact (populated by crawler/enrich.py from the track +
    # the admin_region reference boundaries).
    region_impacts: Mapped[list["TyphoonRegionImpact"]] = relationship(
        back_populates="typhoon", cascade="all, delete-orphan"
    )
    landfalls: Mapped[list["Landfall"]] = relationship(
        back_populates="typhoon", cascade="all, delete-orphan"
    )

    __table_args__ = (
        # IVFFlat index for fast cosine similarity (semantic associative search).
        Index(
            "ix_typhoon_embedding",
            "embedding",
            postgresql_using="ivfflat",
            postgresql_with={"lists": 100},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


class TrackPoint(Base):
    """One best-track observation — the spatio-temporal core of the KB."""

    __tablename__ = "track_point"

    id: Mapped[int] = mapped_column(primary_key=True)
    typhoon_id: Mapped[int] = mapped_column(
        ForeignKey("typhoon.id", ondelete="CASCADE"), index=True
    )
    # Which official agency observed this point (CMA / JMA / JTWC / IBTrACS).
    # Lets multiple agencies' actual (实况) tracks coexist for one typhoon.
    agency: Mapped[str | None] = mapped_column(String(16), index=True)
    obs_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    geom: Mapped[object] = mapped_column(Geometry("POINT", srid=SRID, spatial_index=False))
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    wind_kt: Mapped[float | None] = mapped_column(Float)
    pressure_hpa: Mapped[float | None] = mapped_column(Float)
    grade: Mapped[str | None] = mapped_column(String(32))
    rmw_km: Mapped[float | None] = mapped_column(Float)
    move_dir: Mapped[float | None] = mapped_column(Float)
    move_speed: Mapped[float | None] = mapped_column(Float)

    typhoon: Mapped["Typhoon"] = relationship(back_populates="track_points")

    __table_args__ = (
        Index("ix_track_point_geom", "geom", postgresql_using="gist"),
    )


class AffectedRegion(Base):
    """Approximate impact footprint (polygon) of a typhoon."""

    __tablename__ = "affected_region"

    id: Mapped[int] = mapped_column(primary_key=True)
    typhoon_id: Mapped[int] = mapped_column(
        ForeignKey("typhoon.id", ondelete="CASCADE"), index=True
    )
    region_name: Mapped[str | None] = mapped_column(String(128))
    geom: Mapped[object] = mapped_column(Geometry("MULTIPOLYGON", srid=SRID, spatial_index=False))
    impact_type: Mapped[str | None] = mapped_column(String(64))
    source: Mapped[str | None] = mapped_column(String(32))

    typhoon: Mapped["Typhoon"] = relationship(back_populates="regions")

    __table_args__ = (
        Index("ix_affected_region_geom", "geom", postgresql_using="gist"),
    )


class SecondaryDisaster(Base):
    """A secondary disaster event triggered by the typhoon (flood, landslide,
    storm surge, casualty, infrastructure). Carries its own embedding so
    disasters are semantically searchable too."""

    __tablename__ = "secondary_disaster"

    id: Mapped[int] = mapped_column(primary_key=True)
    typhoon_id: Mapped[int] = mapped_column(
        ForeignKey("typhoon.id", ondelete="CASCADE"), index=True
    )
    disaster_type: Mapped[str] = mapped_column(String(32), index=True)
    geom: Mapped[object | None] = mapped_column(Geometry("POINT", srid=SRID, spatial_index=False))
    lat: Mapped[float | None] = mapped_column(Float)
    lon: Mapped[float | None] = mapped_column(Float)
    event_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    casualties: Mapped[int | None] = mapped_column(Integer)
    economic_loss_usd: Mapped[float | None] = mapped_column(Float)
    description: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(String(32))
    source_url: Mapped[str | None] = mapped_column(Text)
    # Affected country/province as reported by the source feed (GDACS country,
    # ReliefWeb primary_country, NMC province, JMA prefecture, HKO "Hong Kong").
    # Human-readable hint; the structured geographic attribution lives in
    # typhoon_region_impact / landfall.
    region_name: Mapped[str | None] = mapped_column(String(128))
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))

    typhoon: Mapped["Typhoon"] = relationship(back_populates="disasters")

    __table_args__ = (
        Index("ix_disaster_geom", "geom", postgresql_using="gist"),
        Index(
            "ix_disaster_embedding",
            "embedding",
            postgresql_using="ivfflat",
            postgresql_with={"lists": 100},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


class MediaAsset(Base):
    """Multimedia metadata: satellite imagery / photos / maps linked to a typhoon."""

    __tablename__ = "media_asset"

    id: Mapped[int] = mapped_column(primary_key=True)
    typhoon_id: Mapped[int] = mapped_column(
        ForeignKey("typhoon.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(32))  # satellite | photo | map
    url: Mapped[str] = mapped_column(Text)
    caption: Mapped[str | None] = mapped_column(Text)
    captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    typhoon: Mapped["Typhoon"] = relationship(back_populates="media")


class AdminRegion(Base):
    """Reference administrative boundary (country / province) from Natural Earth.

    This is a *reference* table (not owned by any typhoon): the derived impact
    and landfall facts point into it. Loaded once via crawler/sources/
    naturalearth.py + load.load_admin_regions."""

    __tablename__ = "admin_region"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Natural Earth stable feature id — the idempotency key for upserts.
    ne_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str | None] = mapped_column(String(128))
    name_local: Mapped[str | None] = mapped_column(String(128))
    iso_a2: Mapped[str | None] = mapped_column(String(2), index=True)
    iso_a3: Mapped[str | None] = mapped_column(String(3), index=True)
    admin_level: Mapped[int | None] = mapped_column(Integer, index=True)  # 0 country, 1 province, 2 prefecture/city
    country: Mapped[str | None] = mapped_column(String(128), index=True)  # parent country (for grouping)
    parent_name: Mapped[str | None] = mapped_column(String(128), index=True)  # parent admin-1 name (for admin-2 grouping)
    geom: Mapped[object] = mapped_column(Geometry("MULTIPOLYGON", srid=SRID, spatial_index=False))

    __table_args__ = (
        Index("ix_admin_region_geom", "geom", postgresql_using="gist"),
    )


class TyphoonRegionImpact(Base):
    """Which administrative regions a typhoon affected (derived, one row per
    typhoon × region). Answers 'which countries / provinces were affected'."""

    __tablename__ = "typhoon_region_impact"

    id: Mapped[int] = mapped_column(primary_key=True)
    typhoon_id: Mapped[int] = mapped_column(
        ForeignKey("typhoon.id", ondelete="CASCADE"), index=True
    )
    admin_region_id: Mapped[int] = mapped_column(
        ForeignKey("admin_region.id", ondelete="CASCADE"), index=True
    )
    within_corridor: Mapped[bool | None] = mapped_column(Boolean)  # region ∩ wind corridor
    passed_over: Mapped[bool | None] = mapped_column(Boolean)      # track line crossed the polygon
    landfall: Mapped[bool | None] = mapped_column(Boolean)         # ≥1 landfall event in this region
    min_distance_deg: Mapped[float | None] = mapped_column(Float)  # track → region distance (degrees)
    max_wind_kt: Mapped[float | None] = mapped_column(Float)       # strongest fix over/near the region
    first_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    landfall_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    typhoon: Mapped["Typhoon"] = relationship(back_populates="region_impacts")
    admin_region: Mapped["AdminRegion"] = relationship()

    __table_args__ = (
        UniqueConstraint("typhoon_id", "admin_region_id", name="uq_typhoon_region"),
    )


class Landfall(Base):
    """A discrete landfall event — where the track crossed from sea to land.
    Supports 'how many times region X was hit by a typhoon landfall'."""

    __tablename__ = "landfall"

    id: Mapped[int] = mapped_column(primary_key=True)
    typhoon_id: Mapped[int] = mapped_column(
        ForeignKey("typhoon.id", ondelete="CASCADE"), index=True
    )
    # Most-specific region at the crossing (admin-1 if resolvable, else admin-0).
    admin_region_id: Mapped[int | None] = mapped_column(
        ForeignKey("admin_region.id", ondelete="SET NULL"), index=True
    )
    country: Mapped[str | None] = mapped_column(String(128), index=True)  # denormalized for fast grouping
    landfall_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    lat: Mapped[float | None] = mapped_column(Float)
    lon: Mapped[float | None] = mapped_column(Float)
    wind_kt: Mapped[float | None] = mapped_column(Float)
    pressure_hpa: Mapped[float | None] = mapped_column(Float)
    grade: Mapped[str | None] = mapped_column(String(32))
    geom: Mapped[object | None] = mapped_column(Geometry("POINT", srid=SRID, spatial_index=False))

    typhoon: Mapped["Typhoon"] = relationship(back_populates="landfalls")
    admin_region: Mapped["AdminRegion"] = relationship()

    __table_args__ = (
        Index("ix_landfall_geom", "geom", postgresql_using="gist"),
    )
