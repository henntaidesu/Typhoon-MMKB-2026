"""One-off repairs for rows loaded before the ingest guards existed.

New crawls are already correct (see `_public_title` and `_disaster_exists` in
crawler/load.py); this brings the existing KB in line so search results don't
show blank headlines or the same event twice.

Run:  python crawler/repair.py
"""
from __future__ import annotations

import os
import re
import sys

from sqlalchemy import delete, func, select

_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from crawler.load import (  # noqa: E402
    _BODY_PREFIX, _MAX_NAME_MATCH_DEG, _track_distance_expr,
)
from db import SessionLocal  # noqa: E402
from models import PublicInfo, SecondaryDisaster, TrackPoint, Typhoon  # noqa: E402


def fix_public_titles() -> int:
    """Derive a headline from the body for rows whose feed left title empty."""
    n = 0
    with SessionLocal() as s:
        rows = s.scalars(
            select(PublicInfo).where(
                func.coalesce(PublicInfo.title, "") == "",
                PublicInfo.body.isnot(None),
            )
        ).all()
        for p in rows:
            head = _BODY_PREFIX.sub("", p.body.strip().split("\n", 1)[0]).strip()[:200]
            if head:
                p.title = head
                n += 1
        s.commit()
    return n


def drop_duplicate_disasters() -> int:
    """Delete repeats of one real-world event kept under a second typhoon.

    Keyed on (source, source_url) — one report describes one event, so extra
    rows mean the name/time-space resolver attached it to two storms. The
    lowest id wins; the loser also loses its (stale) embedding with it."""
    n = 0
    with SessionLocal() as s:
        dup_keys = s.execute(
            select(SecondaryDisaster.source, SecondaryDisaster.source_url)
            .where(SecondaryDisaster.source_url.isnot(None))
            .group_by(SecondaryDisaster.source, SecondaryDisaster.source_url)
            .having(func.count() > 1)
        ).all()
        for source, url in dup_keys:
            rows = s.scalars(
                select(SecondaryDisaster)
                .where(SecondaryDisaster.source == source,
                       SecondaryDisaster.source_url == url)
                .order_by(SecondaryDisaster.id)
            ).all()
            for extra in rows[1:]:
                s.delete(extra)
                n += 1
        s.commit()
    return n


# GDACS phrases every event as "... Tropical Cyclone SOUDELOR-15 in China ...".
# The cyclone's own name is the ground truth for which storm the row describes.
# Unnamed systems get a numeric designation instead ("12-20252026-26"), which is
# why the name alone cannot decide every row — see `gdacs_row_is_attributable`.
_GDACS_EVENT = re.compile(r"Tropical Cyclone\s+([A-Za-z][A-Za-z\-']*)-\d{2}\b")


def gdacs_event_name(body: str | None) -> str | None:
    """The cyclone name GDACS gives the event, or None if it is unnamed."""
    m = _GDACS_EVENT.search(body or "")
    return m.group(1).lower() if m else None


def gdacs_row_is_attributable(event_name: str | None, typhoon_name: str | None,
                              distance_deg: float | None) -> bool:
    """Whether a GDACS row may hang on this typhoon: the names agree, OR the
    report sits close enough to the track to be about this storm.

    Neither half suffices alone. Name-only would reject Matmo 2019, which really
    did cross into the Bay of Bengal and get renamed Bulbul — its report is 15
    degrees off the West-Pacific track segment yet genuinely belongs to it.
    Distance-only would accept an East-Pacific hurricane that happens to pass
    near a typhoon. Requiring *either* keeps the basin-crossers and still drops
    the unnamed South-Indian-Ocean systems that arrived with no name to check.
    """
    if event_name and typhoon_name and event_name == typhoon_name.lower():
        return True
    return distance_deg is not None and distance_deg <= _MAX_NAME_MATCH_DEG


def drop_offbasin_records(dry_run: bool = False) -> dict[str, int]:
    """Delete records attached to a typhoon they cannot possibly describe.

    GDACS publishes tropical cyclones worldwide, and an earlier resolver hung
    each event on whichever West-Pacific storm happened to be active that week —
    so the KB carries Atlantic hurricanes and South-Pacific cyclones as
    "secondary disasters" of typhoons half a planet away (a WP storm whose
    summary claims it struck Canada). That poisons the embeddings.

    Two rules, matching what `_resolve_typhoon` now enforces at ingest:

    * GDACS rows must name the same cyclone as the typhoon they hang on. This is
      exact, unlike distance: an East-Pacific hurricane can pass within a few
      degrees of a WP typhoon's track and still be a different storm.
    * any other located row must sit within the loosest spatial gate.
    """
    out = {"gdacs_unattributable": 0, "off_track": 0}
    with SessionLocal() as s:

        def track_distance(tid, lat, lon):
            if lat is None or lon is None:
                return None
            return s.scalar(select(func.min(_track_distance_expr(lat, lon)))
                            .where(TrackPoint.typhoon_id == tid))

        for model, text_col in ((SecondaryDisaster, SecondaryDisaster.description),
                                (PublicInfo, PublicInfo.body)):
            doomed: list[int] = []
            for rid, tid, lat, lon, body in s.execute(
                select(model.id, model.typhoon_id, model.lat, model.lon, text_col)
                .where(model.source == "GDACS")
            ).all():
                typhoon_name = s.scalar(select(Typhoon.name).where(Typhoon.id == tid))
                if not gdacs_row_is_attributable(
                        gdacs_event_name(body), typhoon_name,
                        track_distance(tid, lat, lon)):
                    doomed.append(rid)
            out["gdacs_unattributable"] += len(doomed)

            off_track: list[int] = []
            for rid, tid, lat, lon in s.execute(
                select(model.id, model.typhoon_id, model.lat, model.lon)
                .where(model.lat.isnot(None), model.lon.isnot(None),
                       model.source != "GDACS")
            ).all():
                d = track_distance(tid, lat, lon)
                if d is not None and d > _MAX_NAME_MATCH_DEG:
                    off_track.append(rid)
            out["off_track"] += len(off_track)

            if not dry_run and (doomed or off_track):
                s.execute(delete(model).where(model.id.in_(doomed + off_track)))
        if not dry_run:
            s.commit()
    return out


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    offbasin = drop_offbasin_records(dry_run=dry)
    print(f"[repair] off-basin rows{' (dry run)' if dry else ''}: {offbasin}")
    if dry:
        sys.exit(0)
    titles = fix_public_titles()
    dups = drop_duplicate_disasters()
    print(f"[repair] filled {titles} public-info titles, removed {dups} duplicate disasters")
