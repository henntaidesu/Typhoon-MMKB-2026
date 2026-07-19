"""Tests for the matching and search rules.

These cover the decisions that are easy to get subtly wrong and that nothing
else guards: how a query is classified, when the keyword arm fires, which admin
regions belong in a landfall view, and — the one that actually bit this KB —
whether a disaster record may be attached to a given typhoon.

Split in two:

* pure-logic tests, which need nothing but the source;
* data-invariant tests, which assert properties the *loaded KB* must hold. They
  are the regression net for the ingest rules: a loader change that starts
  attaching cross-basin cyclones again shows up here on the next crawl.

Run:  python -m unittest discover -s tests -t .        (from backend/)
"""
from __future__ import annotations

import os
import sys
import unittest

_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from crawler.load import (  # noqa: E402
    _MAX_NAME_MATCH_DEG, _public_title, _strip_suffix,
)
from routers.stats import _region_visible  # noqa: E402
from services.intent import detect_intent  # noqa: E402
from services.semantic import _dedup_key, _is_keyword_query  # noqa: E402


class _Rec:
    """Minimal stand-in for a DisasterRec/PublicInfoRec."""

    def __init__(self, description=None, title=None):
        self.description = description
        self.title = title


class QueryIntentTest(unittest.TestCase):
    """A bare year or storm name must never reach the embedding model — cosine
    distance has no notion of "the year 2019" and returns confident nonsense."""

    def test_bare_year_is_structured(self):
        for q in ("2019", "1955", "1899"):
            self.assertEqual(detect_intent(q)[0], "year", q)

    def test_year_params_carry_the_int(self):
        self.assertEqual(detect_intent("2019"), ("year", {"year": 2019}))

    def test_four_digit_storm_number(self):
        # A WMO number is also 4 digits; the season prefix disambiguates it from
        # a year only by range, so "2306" must read as a storm, "2023" as a year.
        self.assertEqual(detect_intent("2306")[0], "intl_id")
        self.assertEqual(detect_intent("2023")[0], "year")

    def test_bare_latin_word_is_a_name(self):
        for q in ("Hagibis", "Kong-Rey", "Nari"):
            self.assertEqual(detect_intent(q)[0], "name", q)

    def test_typhoon_prefix_is_stripped(self):
        for q in ("typhoon Hagibis", "Typhoon Hagibis", "台风 Hagibis", "台風 Hagibis"):
            intent, params = detect_intent(q)
            self.assertEqual(intent, "name", q)
            self.assertEqual(params["name"], "Hagibis", q)

    def test_the_word_typhoon_alone_is_not_a_name(self):
        self.assertEqual(detect_intent("typhoon")[0], "semantic")

    def test_descriptive_queries_stay_semantic(self):
        for q in ("severe flooding and landslides", "大雨による浸水と土砂災害",
                  "暴雨洪涝的应急响应", "storm surge damage"):
            self.assertEqual(detect_intent(q)[0], "semantic", q)

    def test_whitespace_is_tolerated(self):
        self.assertEqual(detect_intent("  2019  ")[0], "year")


class KeywordArmTest(unittest.TestCase):
    """The keyword arm exists for queries the vector search handles badly. On a
    long query its per-token AND degenerates into a bag-of-words match that hits
    almost everything, so it must stay off."""

    def test_fires_for_bare_place_names(self):
        for q in ("浙江", "甘肃", "沖縄", "滑坡", "Okinawa"):
            self.assertTrue(_is_keyword_query(q), q)

    def test_fires_for_two_word_queries(self):
        self.assertTrue(_is_keyword_query("storm surge"))

    def test_off_for_long_queries(self):
        for q in ("destructive wind damage red alert",
                  "severe flooding and landslides",
                  "大雨による浸水と土砂災害という非常に長い問い合わせ"):
            self.assertFalse(_is_keyword_query(q), q)

    def test_char_limit_boundary(self):
        self.assertTrue(_is_keyword_query("x" * 16))
        self.assertFalse(_is_keyword_query("x" * 17))


class DedupKeyTest(unittest.TestCase):
    """Bulletins get re-issued as 第4報 / 第5報 with near-identical text and would
    otherwise fill a whole page with one event."""

    def test_mixed_types_do_not_raise(self):
        # typhoon_id is an int; joining it with text used to throw TypeError.
        self.assertTrue(_dedup_key(96, "some description"))

    def test_reissued_bulletins_collapse(self):
        a = "[消防庁 災害情報] 令和8年07月10日　令和8年台風第9号による被害及び消防機関等の対応状況（第4報）"
        b = "[消防庁 災害情報] 令和8年07月10日　令和8年台風第9号による被害及び消防機関等の対応状況（第5報）"
        self.assertEqual(_dedup_key(96, a), _dedup_key(96, b))

    def test_same_text_under_different_typhoons_stays_distinct(self):
        text = "identical wording from two different storms"
        self.assertNotEqual(_dedup_key(1, text), _dedup_key(2, text))

    def test_whitespace_is_normalized(self):
        self.assertEqual(_dedup_key(1, "a  b\n c"), _dedup_key(1, "a b c"))

    def test_none_fields_are_skipped(self):
        self.assertEqual(_dedup_key(1, None, "x"), _dedup_key(1, "x"))


class RegionVisibilityTest(unittest.TestCase):
    """The bar list and the choropleth must answer the same question for a given
    level; they previously diverged (4681 rows against 838 map features)."""

    def test_countries_always_render(self):
        self.assertTrue(_region_visible(0, 0, 0))

    def test_provinces_render_when_touched_at_all(self):
        self.assertTrue(_region_visible(1, 0, 1))   # corridor only
        self.assertTrue(_region_visible(1, 3, 0))   # landfall only
        self.assertFalse(_region_visible(1, 0, 0))  # untouched

    def test_level2_requires_an_actual_landfall(self):
        self.assertTrue(_region_visible(2, 1, 0))
        self.assertFalse(_region_visible(2, 0, 99))  # corridor alone is not enough


class PublicTitleTest(unittest.TestCase):
    """Feeds that leave `title` empty (中央气象台预警) would render blank rows."""

    def test_derives_headline_from_body(self):
        r = _Rec(description="[中央气象台预警]  浙江省丽水市松阳县气象台发布暴雨蓝色预警信号")
        self.assertEqual(_public_title(r), "浙江省丽水市松阳县气象台发布暴雨蓝色预警信号")

    def test_existing_title_wins(self):
        r = _Rec(description="body text", title="Real headline")
        self.assertEqual(_public_title(r), "Real headline")

    def test_blank_title_falls_back(self):
        r = _Rec(description="body text", title="   ")
        self.assertEqual(_public_title(r), "body text")

    def test_only_the_first_line_is_used(self):
        r = _Rec(description="headline\nsecond paragraph")
        self.assertEqual(_public_title(r), "headline")

    def test_empty_body_yields_none(self):
        self.assertIsNone(_public_title(_Rec(description="")))


class NameNormalizationTest(unittest.TestCase):
    def test_gdacs_suffix_is_stripped(self):
        self.assertEqual(_strip_suffix("Doksuri-23"), "doksuri")
        self.assertEqual(_strip_suffix("HAGIBIS-19"), "hagibis")

    def test_plain_name_passes_through(self):
        self.assertEqual(_strip_suffix("Nari"), "nari")


# --- Data invariants -------------------------------------------------------
def _engine_or_none():
    try:
        from db import engine
        with engine.connect() as c:
            c.exec_driver_sql("select 1")
        return engine
    except Exception:  # noqa: BLE001 — the KB is optional for the pure tests
        return None


_ENGINE = _engine_or_none()


@unittest.skipIf(_ENGINE is None, "knowledge base not reachable")
class KnowledgeBaseInvariantTest(unittest.TestCase):
    """Properties the loaded KB must hold. These are the regression net for the
    ingest rules — they fail on the next crawl if a loader change reintroduces
    cross-basin attribution."""

    def test_every_gdacs_row_is_attributable_to_its_typhoon(self):
        """Either the cyclone names agree, or the report is close enough to the
        track. Neither test alone is right — see `gdacs_row_is_attributable`."""
        from sqlalchemy import func, select
        from crawler.load import _track_distance_expr
        from crawler.repair import gdacs_event_name, gdacs_row_is_attributable
        from db import SessionLocal
        from models import PublicInfo, SecondaryDisaster, TrackPoint, Typhoon

        with SessionLocal() as s:
            for model, col in ((SecondaryDisaster, SecondaryDisaster.description),
                               (PublicInfo, PublicInfo.body)):
                bad = []
                for rid, tid, lat, lon, body in s.execute(
                    select(model.id, model.typhoon_id, model.lat, model.lon, col)
                    .where(model.source == "GDACS")
                ).all():
                    dist = None
                    if lat is not None and lon is not None:
                        dist = s.scalar(select(func.min(_track_distance_expr(lat, lon)))
                                        .where(TrackPoint.typhoon_id == tid))
                    name = s.scalar(select(Typhoon.name).where(Typhoon.id == tid))
                    if not gdacs_row_is_attributable(gdacs_event_name(body), name, dist):
                        bad.append((rid, name, round(dist or -1, 1)))
                self.assertEqual(bad, [], f"{model.__name__}: unattributable rows {bad[:5]}")

    def test_non_gdacs_located_rows_sit_near_their_track(self):
        """Feeds that don't name their storm (NMC 预警 etc.) are attached purely
        by time and place, so proximity is the whole basis for the link."""
        from sqlalchemy import func, select
        from crawler.load import _track_distance_expr
        from db import SessionLocal
        from models import PublicInfo, SecondaryDisaster, TrackPoint

        with SessionLocal() as s:
            for model in (SecondaryDisaster, PublicInfo):
                worst = 0.0
                for tid, lat, lon in s.execute(
                    select(model.typhoon_id, model.lat, model.lon)
                    .where(model.lat.isnot(None), model.lon.isnot(None),
                           model.source != "GDACS")
                ).all():
                    d = s.scalar(select(func.min(_track_distance_expr(lat, lon)))
                                 .where(TrackPoint.typhoon_id == tid))
                    worst = max(worst, d or 0.0)
                self.assertLessEqual(
                    worst, _MAX_NAME_MATCH_DEG,
                    f"{model.__name__}: a record sits {worst:.1f} deg from its track")

    def test_track_distance_is_longitude_wrap_safe(self):
        """track_point.lon uses the agencies' 0-360 convention; the feeds report
        -180..180. The same physical point must measure the same either way."""
        from sqlalchemy import func, select
        from crawler.load import _nearest_track_deg, _track_distance_expr
        from db import SessionLocal
        from models import TrackPoint, Typhoon

        with SessionLocal() as s:
            # A storm whose track crosses the dateline, so the two conventions
            # genuinely disagree over its points.
            tid = s.scalar(
                select(Typhoon.id)
                .join(TrackPoint, TrackPoint.typhoon_id == Typhoon.id)
                .group_by(Typhoon.id)
                .having(func.max(TrackPoint.lon) > 190)
                .limit(1)
            )
            self.assertIsNotNone(tid, "no dateline-crossing track to test against")

            lat = 20.0
            for east, west in ((200.0, -160.0), (185.0, -175.0), (359.0, -1.0)):
                de = s.scalar(select(func.min(_track_distance_expr(lat, east)))
                              .where(TrackPoint.typhoon_id == tid))
                dw = s.scalar(select(func.min(_track_distance_expr(lat, west)))
                              .where(TrackPoint.typhoon_id == tid))
                self.assertAlmostEqual(
                    de, dw, places=6,
                    msg=f"lon {east} and {west} are the same meridian but measured differently")

            # And a point actually on the far side of the dateline must come out
            # near the track, not ~360 deg away.
            near = _nearest_track_deg(s, [tid], lat=20.0, lon=-160.0)
            self.assertTrue(near and near[0][1] < 180,
                            "a dateline-side point measured as if half a planet away")


if __name__ == "__main__":
    unittest.main(verbosity=2)
