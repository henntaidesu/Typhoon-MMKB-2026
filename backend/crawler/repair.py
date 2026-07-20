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
    _BODY_PREFIX, _MAX_NAME_MATCH_DEG, _match_typhoon, _track_distance_expr,
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


def backfill_chinese_names(emit=print) -> int:
    """Fill `Typhoon.name_cn` from the CMA season rosters.

    CMA publishes the Chinese name next to the English one, but the crawler only
    ever read the English column, so every row landed with name_cn NULL — which
    silently disabled the CN branch of `_match_typhoon` for the whole KB. New
    crawls carry it now; this brings the existing rows up to date.

    Rosters only (no tracks), one cheap request per season."""
    from sqlalchemy import func

    from crawler.sources.china.typhoon import cma

    n = 0
    with SessionLocal() as s:
        years = [y for (y,) in s.execute(
            select(Typhoon.season_year).where(Typhoon.season_year.isnot(None))
            .distinct().order_by(Typhoon.season_year.desc())).all()]
        current = s.scalar(select(func.max(Typhoon.season_year)))
        for y in years:
            # The current season lives on list_default; past ones on list_{year}.
            roster = (cma.fetch_current_roster(emit=lambda m: None) if y == current
                      else cma.fetch_year_roster(y, emit=lambda m: None))
            by_id = {e["intl_id"]: e.get("name_cn") for e in roster if e.get("name_cn")}
            if not by_id:
                continue
            for t in s.scalars(select(Typhoon).where(Typhoon.season_year == y)):
                cn = by_id.get(t.intl_id)
                if cn and t.name_cn != cn:
                    t.name_cn = cn
                    n += 1
            s.commit()
            emit(f"  {y}: {len(by_id)} named storms in roster, {n} filled so far")
    return n


def reattribute_named_bulletins(dry_run: bool = False) -> dict[str, int]:
    """Re-resolve records whose text names a storm, now that names can resolve.

    Bulletins from 应急管理部 / 中央气象台 carry no coordinates, so before the name
    could be read they fell through to "whichever storm was active nearest this
    date" — which put 台风“巴威” bulletins on Haishen. With the quoted name now
    extracted and `name_cn` populated, each such record can be re-pointed at the
    storm it actually names; ones that still don't resolve are detached rather
    than left on a storm they demonstrably do not describe."""
    from crawler.sources._shared.disaster_common import extract_typhoon_name

    out = {"moved": 0, "detached": 0, "unchanged": 0}
    with SessionLocal() as s:
        for model, text_cols in ((SecondaryDisaster, ("description",)),
                                 (PublicInfo, ("title", "body"))):
            rows = s.scalars(
                select(model).where(model.lat.is_(None), model.source.in_(("MEM", "NMC")))
            ).all()
            for row in rows:
                text = " ".join(str(getattr(row, c) or "") for c in text_cols)
                name = extract_typhoon_name(text)
                if not name:
                    out["unchanged"] += 1
                    continue
                # The season is essential: storm names are recycled every few
                # years, so 台风“格美” alone matches Kaemi 2006 as readily as
                # Gaemi 2024. The bulletin's own timestamp decides; the storm it
                # currently hangs on is the fallback, since the time-based guess
                # that put it there at least landed in the right season.
                stamp = getattr(row, "publish_time", None) or getattr(row, "event_time", None)
                year = stamp.year if stamp else None
                if year is None:
                    year = s.scalar(select(Typhoon.season_year)
                                    .where(Typhoon.id == row.typhoon_id))
                target = _match_typhoon(s, name, year)
                if target is None:
                    if not dry_run:
                        s.delete(row)
                    out["detached"] += 1
                elif target.id != row.typhoon_id:
                    if not dry_run:
                        row.typhoon_id = target.id
                    out["moved"] += 1
                else:
                    out["unchanged"] += 1
        if not dry_run:
            s.commit()
    return out


def flag_landfall_at_every_level(dry_run: bool = False) -> int:
    """Set `landfall` on impact rows whose region contains a landfall point.

    The enricher used to flag only the most-specific region plus its country, so
    when a landfall resolved to an admin-2 city the province in between was left
    unflagged — the detail panel showed 「China 登陆 / Zhejiang — / Wenzhou 登陆」.
    enrich.py now flags every containing level; this repairs what is already
    stored without re-deriving all 1900-odd landfalls.
    """
    from sqlalchemy import text
    sql = text("""
        UPDATE typhoon_region_impact i
           SET landfall = true,
               landfall_time = COALESCE(i.landfall_time, lf.t)
          FROM (SELECT l.typhoon_id, a.id AS region_id, MIN(l.landfall_time) AS t
                  FROM landfall l
                  JOIN admin_region a ON ST_Contains(a.geom, l.geom)
                 WHERE l.geom IS NOT NULL
                 GROUP BY l.typhoon_id, a.id) lf
         WHERE i.typhoon_id = lf.typhoon_id
           AND i.admin_region_id = lf.region_id
           AND COALESCE(i.landfall, false) = false
    """)
    count_sql = text("""
        SELECT count(*) FROM typhoon_region_impact i
          JOIN landfall l ON l.typhoon_id = i.typhoon_id AND l.geom IS NOT NULL
          JOIN admin_region a ON a.id = i.admin_region_id AND ST_Contains(a.geom, l.geom)
         WHERE COALESCE(i.landfall, false) = false
    """)
    with SessionLocal() as s:
        n = s.scalar(count_sql)
        if not dry_run:
            s.execute(sql)
            s.commit()
    return n or 0


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    if "--landfall-levels" in sys.argv:
        n = flag_landfall_at_every_level(dry_run=dry)
        print(f"[repair] impact rows to flag as landfall{' (dry run)' if dry else ''}: {n}")
        sys.exit(0)
    if "--names" in sys.argv:
        print(f"[repair] filled {backfill_chinese_names()} Chinese names")
        print(f"[repair] re-attribution: {reattribute_named_bulletins(dry_run=dry)}")
        sys.exit(0)
    offbasin = drop_offbasin_records(dry_run=dry)
    print(f"[repair] off-basin rows{' (dry run)' if dry else ''}: {offbasin}")
    if dry:
        sys.exit(0)
    titles = fix_public_titles()
    dups = drop_duplicate_disasters()
    print(f"[repair] filled {titles} public-info titles, removed {dups} duplicate disasters")
