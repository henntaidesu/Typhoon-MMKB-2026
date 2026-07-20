"""Tests for `_resolve_typhoon` — which typhoon a disaster record attaches to.

This is the decision that went wrong at scale: 604 of ~1000 disaster rows were
hung on the wrong storm, mostly cyclones from other basins. The chain has three
links (WMO id -> name -> time/space) plus two guards (a spatial sanity check on
the name match, and the `named_event` policy), and each link can silently pick a
plausible-looking wrong answer.

These run against the real KB read-only: `_resolve_typhoon` never writes, so
synthetic records can be resolved against real storms without fixtures. They
skip when the database is unreachable.

Run:  python -m unittest discover -s tests -t .        (from backend/)
"""
from __future__ import annotations

import os
import sys
import unittest
from datetime import timedelta

_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from tests._kb import requires_kb  # noqa: E402


def _rec(**kw):
    """A DisasterRec with only the matching hints that matter here."""
    from crawler.sources._shared.disaster_common import DisasterRec
    kw.setdefault("disaster_type", "flood")
    kw.setdefault("description", "test record")
    kw.setdefault("source", "TEST")
    return DisasterRec(**kw)


@requires_kb
class ResolveTyphoonTest(unittest.TestCase):
    """Anchored on Hagibis 2019 (#1919), a storm with a long, well-covered
    West-Pacific track."""

    @classmethod
    def setUpClass(cls):
        from sqlalchemy import select
        from db import SessionLocal
        from models import TrackPoint, Typhoon

        cls.session = SessionLocal()
        cls.hagibis = cls.session.scalar(select(Typhoon).where(Typhoon.intl_id == "1919"))
        assert cls.hagibis is not None, "anchor typhoon 1919 missing from the KB"
        pts = cls.session.scalars(
            select(TrackPoint).where(TrackPoint.typhoon_id == cls.hagibis.id)
            .order_by(TrackPoint.obs_time)
        ).all()
        cls.on_track = pts[len(pts) // 2]  # a point the storm demonstrably passed

    @classmethod
    def tearDownClass(cls):
        cls.session.close()

    def resolve(self, **kw):
        from crawler.load import _resolve_typhoon
        return _resolve_typhoon(self.session, _rec(**kw))

    # --- link 1: exact WMO id ---------------------------------------------
    def test_intl_id_wins_over_a_contradicting_name(self):
        got = self.resolve(intl_id="1919", typhoon_name="Katrina", season_year=2005)
        self.assertEqual(got.id, self.hagibis.id)

    # --- link 2: name, guarded by location ---------------------------------
    def test_name_and_year_resolve(self):
        got = self.resolve(typhoon_name="Hagibis", season_year=2019)
        self.assertEqual(got.id, self.hagibis.id)

    def test_name_match_accepted_when_located_on_the_track(self):
        got = self.resolve(typhoon_name="Hagibis", season_year=2019,
                           lat=self.on_track.lat, lon=self.on_track.lon,
                           event_time=self.on_track.obs_time)
        self.assertEqual(got.id, self.hagibis.id)

    def test_name_match_rejected_when_located_across_the_planet(self):
        """Cyclone names are recycled across basins within a season, so a name
        alone cannot distinguish a WP typhoon from its namesake elsewhere."""
        got = self.resolve(typhoon_name="Hagibis", season_year=2019,
                           lat=28.4, lon=-168.8,  # central North Pacific
                           event_time=self.on_track.obs_time, named_event=True)
        self.assertIsNone(got)

    def test_longitude_convention_does_not_break_the_name_guard(self):
        """Track longitudes are 0-360, feeds report -180..180. The same meridian
        expressed either way must pass the same guard."""
        west = self.on_track.lon - 360  # identical physical meridian
        got = self.resolve(typhoon_name="Hagibis", season_year=2019,
                           lat=self.on_track.lat, lon=west,
                           event_time=self.on_track.obs_time)
        self.assertEqual(got.id, self.hagibis.id)

    # --- the named_event policy -------------------------------------------
    def test_named_event_with_unknown_name_is_dropped(self):
        """The regression guard: a GDACS event naming a cyclone this KB doesn't
        carry belongs to another basin. Guessing by time/space is what filled the
        KB with Atlantic hurricanes."""
        got = self.resolve(typhoon_name="Katrina", season_year=2019,
                           lat=self.on_track.lat, lon=self.on_track.lon,
                           event_time=self.on_track.obs_time, named_event=True)
        self.assertIsNone(got)

    def test_unnamed_gdacs_designation_is_dropped(self):
        """GDACS numbers its unnamed systems ('12-20252026-26'); no name regex
        parses those, so only the named_event policy stops them."""
        got = self.resolve(typhoon_name="12-20252026-26", season_year=2019,
                           lat=-10.05, lon=58.15,  # south-west Indian Ocean
                           event_time=self.on_track.obs_time, named_event=True)
        self.assertIsNone(got)

    # --- naming a storm is itself a claim ----------------------------------
    def test_an_unresolvable_name_is_not_guessed_at(self):
        """A bulletin that names its storm has already said which one it means.
        If that name won't resolve, the answer is "unknown" — not whichever storm
        was active. 应急管理部 bulletins about 台风“巴威” were landing on Haishen
        purely because Haishen's window sat nearer their publication date."""
        got = self.resolve(typhoon_name="Katrina", season_year=2019,
                           lat=self.on_track.lat, lon=self.on_track.lon,
                           event_time=self.on_track.obs_time, named_event=False)
        self.assertIsNone(got)

    def test_a_record_naming_no_storm_may_still_be_placed_by_time_and_space(self):
        """The NMC 预警 case: no storm named, so time and place are all there is."""
        got = self.resolve(lat=self.on_track.lat, lon=self.on_track.lon,
                           event_time=self.on_track.obs_time)
        self.assertIsNotNone(got)

    # --- link 3: time/space -------------------------------------------------
    def test_a_warning_a_few_degrees_off_the_track_still_belongs(self):
        """A storm's rain shield reaches well past its centre line. The gate is
        measured from the track, so its value has to allow for that — an earlier
        version measured from the buffered corridor and a later one kept the same
        number against the bare track, silently tightening the rule."""
        from crawler.load import _MAX_MATCH_DEG
        self.assertGreaterEqual(_MAX_MATCH_DEG, 4.0)
        # ~3.5 degrees north of a mid-track fix: inland, but plainly this storm's.
        got = self.resolve(lat=self.on_track.lat + 3.5, lon=self.on_track.lon,
                           event_time=self.on_track.obs_time)
        self.assertIsNotNone(got)

    def test_time_space_rejects_a_point_far_from_every_track(self):
        got = self.resolve(lat=0.0, lon=0.0,  # Gulf of Guinea
                           event_time=self.on_track.obs_time)
        self.assertIsNone(got)

    def test_time_space_rejects_a_plausible_place_at_the_wrong_time(self):
        from datetime import datetime, timezone
        got = self.resolve(lat=self.on_track.lat, lon=self.on_track.lon,
                           event_time=datetime(1800, 1, 1, tzinfo=timezone.utc))
        self.assertIsNone(got)

    def test_disaster_may_lag_the_storm_by_days(self):
        """Floods and casualty tolls keep accruing after the storm has passed."""
        got = self.resolve(lat=self.on_track.lat, lon=self.on_track.lon,
                           event_time=self.hagibis.end_time + timedelta(days=3))
        self.assertIsNotNone(got)

    def test_disaster_long_after_the_storm_is_not_attributed(self):
        got = self.resolve(lat=self.on_track.lat, lon=self.on_track.lon,
                           event_time=self.hagibis.end_time + timedelta(days=60))
        self.assertIsNone(got)

    # --- name reuse ---------------------------------------------------------
    def test_reused_names_need_the_season_to_be_unambiguous(self):
        """Storm names recycle heavily — 'Lola' appears in 17 seasons — so a
        name without a year cannot identify a storm."""
        from sqlalchemy import func, select
        from models import Typhoon
        seasons = self.session.scalars(
            select(Typhoon.season_year).where(func.lower(Typhoon.name) == "lola")
        ).all()
        self.assertGreater(len(seasons), 1, "expected a reused name to test with")
        for year in seasons[:3]:
            got = self.resolve(typhoon_name="Lola", season_year=year)
            self.assertIsNotNone(got)
            self.assertEqual(got.season_year, year)


if __name__ == "__main__":
    unittest.main(verbosity=2)
