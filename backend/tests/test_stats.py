"""Tests for the landfall-frequency aggregations behind the 统计 page.

"How many times was region X hit" is counted three different ways in this
codebase — by denormalized country name at level 0, by spatial containment at
levels 1/2, and by a separate group-by inside `by_country` — so the risk is not
that one query is wrong but that the three quietly disagree.

Read-only against the real KB; skipped when the database is unreachable.

Run:  python -m unittest discover -s tests -t .        (from backend/)
"""
from __future__ import annotations

import os
import sys
import unittest

_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from tests._kb import requires_kb  # noqa: E402


@requires_kb
class LandfallCountTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from sqlalchemy import func, select
        from db import SessionLocal
        from models import Landfall

        cls.session = SessionLocal()
        cls.total = cls.session.scalar(select(func.count()).select_from(Landfall))

    @classmethod
    def tearDownClass(cls):
        cls.session.close()

    def counts(self, level, **kw):
        from routers.stats import _landfall_counts
        return _landfall_counts(self.session, level, **kw)

    def test_every_landfall_is_attributed_to_a_country(self):
        """Level 0 joins on the denormalized country *name*, so a value that
        doesn't match an admin-0 region would silently vanish from the map."""
        self.assertEqual(sum(self.counts(0).values()), self.total)

    def test_no_landfall_country_is_orphaned(self):
        from sqlalchemy import select
        from models import AdminRegion, Landfall
        names = {n for (n,) in self.session.execute(
            select(AdminRegion.name).where(AdminRegion.admin_level == 0))}
        used = {c for (c,) in self.session.execute(
            select(Landfall.country).distinct())}
        self.assertEqual(used - names - {None}, set())

    def test_finer_levels_never_invent_landfalls(self):
        """A landfall point may fall outside every province/prefecture polygon
        (coastal precision, or a country with no admin-2 coverage), so finer
        levels may under-count — but they must never exceed the coarser total."""
        by_level = [sum(self.counts(lv).values()) for lv in (0, 1, 2)]
        self.assertEqual(by_level[0], self.total)
        for finer, coarser in zip(by_level[1:], by_level[:-1]):
            self.assertLessEqual(finer, coarser, f"level totals not monotonic: {by_level}")

    def test_a_landfall_is_counted_once_per_region(self):
        """Spatial containment could double-count a point sitting in overlapping
        polygons; at each level the per-region counts must still sum to no more
        than the number of landfall points."""
        for level in (1, 2):
            self.assertLessEqual(sum(self.counts(level).values()), self.total)

    # --- year filtering -----------------------------------------------------
    def test_year_window_narrows_the_result(self):
        narrow = sum(self.counts(0, min_year=2019, max_year=2019).values())
        self.assertGreater(narrow, 0)
        self.assertLess(narrow, self.total)

    def test_an_all_encompassing_window_matches_the_unfiltered_total(self):
        from sqlalchemy import func, select
        from models import Typhoon
        lo, hi = self.session.execute(
            select(func.min(Typhoon.season_year), func.max(Typhoon.season_year))).first()
        self.assertEqual(
            sum(self.counts(0, min_year=lo, max_year=hi).values()),
            sum(self.counts(0).values()))

    def test_disjoint_windows_partition_the_landfalls(self):
        """Splitting the timeline in two must account for every landfall exactly
        once — an off-by-one on the boundary would double-count or drop a year."""
        from sqlalchemy import func, select
        from models import Typhoon
        lo, hi = self.session.execute(
            select(func.min(Typhoon.season_year), func.max(Typhoon.season_year))).first()
        split = (lo + hi) // 2
        early = sum(self.counts(0, min_year=lo, max_year=split).values())
        late = sum(self.counts(0, min_year=split + 1, max_year=hi).values())
        self.assertEqual(early + late, sum(self.counts(0).values()))

    def test_empty_window_yields_nothing(self):
        self.assertEqual(self.counts(0, min_year=1800, max_year=1801), {})


@requires_kb
class StatsEndpointAgreementTest(unittest.TestCase):
    """The bar list, the choropleth and the country table must not answer the
    same question differently — they previously did at level 2."""

    @classmethod
    def setUpClass(cls):
        from db import SessionLocal
        cls.session = SessionLocal()

    @classmethod
    def tearDownClass(cls):
        cls.session.close()

    def test_by_country_landfalls_match_the_shared_counter(self):
        """`by_country` computes landfalls with its own group-by rather than
        `_landfall_counts`; the two must agree or the 统计 page contradicts the
        map."""
        from routers.stats import _landfall_counts, by_country
        shared = _landfall_counts(self.session, 0)
        for row in by_country(session=self.session):
            self.assertEqual(
                row["landfall_count"], shared.get(row["admin_region_id"], 0),
                f"{row['country']}: table and map disagree")

    # These handlers declare optional params as `Query(None)`, which FastAPI
    # resolves per request — calling them directly leaves the sentinel object in
    # place, so every optional argument has to be passed explicitly here.
    def _bars(self, level):
        from routers.stats import by_region
        return by_region(session=self.session, level=level,
                         country=None, min_year=None, max_year=None)

    def _map(self, level):
        from routers.stats import landfall_geojson
        return landfall_geojson(session=self.session, level=level, bbox=None)

    def test_bar_list_and_choropleth_cover_the_same_regions(self):
        for level in (0, 1, 2):
            bars = {r["admin_region_id"] for r in self._bars(level)}
            pins = {f["properties"]["id"] for f in self._map(level)["features"]}
            self.assertEqual(bars, pins, f"level {level}: bar list and map disagree")

    def test_the_landfall_flag_agrees_with_the_geometry_at_every_level(self):
        """A landfall in Wenzhou is a landfall in Zhejiang and in China. Flagging
        only the most-specific region plus its country left the middle tier blank,
        which the detail panel renders as 「China 登陆 / Zhejiang — / Wenzhou 登陆」.
        Stated both ways so neither over- nor under-flagging can creep back."""
        from sqlalchemy import text
        missing = self.session.scalar(text("""
            SELECT count(*) FROM typhoon_region_impact i
              JOIN landfall l ON l.typhoon_id = i.typhoon_id AND l.geom IS NOT NULL
              JOIN admin_region a ON a.id = i.admin_region_id
                                 AND ST_Contains(a.geom, l.geom)
             WHERE COALESCE(i.landfall, false) = false
        """))
        self.assertEqual(missing, 0, f"{missing} regions contain a landfall but are not flagged")

        spurious = self.session.scalar(text("""
            SELECT count(*) FROM typhoon_region_impact i
              JOIN admin_region a ON a.id = i.admin_region_id
             WHERE i.landfall IS true
               AND NOT EXISTS (SELECT 1 FROM landfall l
                                WHERE l.typhoon_id = i.typhoon_id
                                  AND l.geom IS NOT NULL
                                  AND ST_Contains(a.geom, l.geom))
        """))
        self.assertEqual(spurious, 0, f"{spurious} regions flagged without a landfall inside")

    def test_level2_rows_all_have_an_actual_landfall(self):
        """The chart is titled 'most landfalls by region'; corridor-only regions
        would pad it with thousands of zero-length bars."""
        rows = self._bars(2)
        self.assertTrue(rows)
        self.assertTrue(all(r["landfall_count"] > 0 for r in rows))


if __name__ == "__main__":
    unittest.main(verbosity=2)
