"""Tests for storm-name extraction and name-based attribution.

A Chinese or Japanese bulletin identifies its storm only by the local name it
quotes — 台风“巴威”. Getting that name out of the prose, and resolving it to the
right season, is the whole basis for attributing those records; when it failed
silently the KB put 应急管理部 bulletins about Bavi onto Haishen purely because
Haishen's window sat nearer their publication date.

Two properties matter and they pull against each other:

* extract a name when one is genuinely quoted, and
* extract *nothing* when the text merely contains the word 台风 (防台风 =
  "typhoon preparedness" names no storm). A junk name is worse than no name,
  because it fails to resolve and used to send the record down the guessing path.

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

from crawler.sources._shared.disaster_common import extract_typhoon_name  # noqa: E402


class ExtractTyphoonNameTest(unittest.TestCase):

    def test_quoted_name_after_the_word_typhoon(self):
        self.assertEqual(
            extract_typhoon_name('台风“巴威”两次登陆浙江沿海 中东部地区有大范围降水'), '巴威')

    def test_quoted_name_before_the_word_typhoon(self):
        """Chinese word order puts the name first as often as not; anchoring on
        台风 and reading forward produced '灾害救助工作' here."""
        self.assertEqual(
            extract_typhoon_name('派出工作组指导浙江做好“巴威”台风灾害救助工作'), '巴威')

    def test_generic_typhoon_preparedness_names_no_storm(self):
        """防台风 is 'typhoon preparedness'. Reading forward from 台风 turned this
        into a storm called 四级应急响应, which then failed to resolve and sent the
        record to the time-based guess."""
        self.assertIsNone(
            extract_typhoon_name('国家防总针对上海江苏安徽江西四川启动防汛防台风四级应急响应'))

    def test_bulletin_with_no_storm_at_all(self):
        for text in ('国家防总启动防汛Ⅲ级应急响应',
                     '国家防灾减灾救灾委员会启动国家救灾应急响应 指导甘肃做好滑坡灾害救助工作'):
            self.assertIsNone(extract_typhoon_name(text), text)

    def test_japanese_bracket_quotes(self):
        self.assertEqual(extract_typhoon_name('台風「マーワー」の接近に伴う警戒'), 'マーワー')

    def test_english_reliefweb_phrasing(self):
        self.assertEqual(
            extract_typhoon_name('Philippines: Typhoon Doksuri - Jul 2023'), 'Doksuri')

    def test_bare_quoted_name(self):
        self.assertEqual(extract_typhoon_name('台风“杜苏芮”'), '杜苏芮')

    def test_empty_and_none_are_safe(self):
        self.assertIsNone(extract_typhoon_name(''))
        self.assertIsNone(extract_typhoon_name(None))

    def test_a_long_phrase_is_not_a_name(self):
        """The length bound is what stops a run of prose being read as a name."""
        self.assertIsNone(extract_typhoon_name('台风“这是一个非常长的短语不可能是名字”'))

    def test_hyphenated_name_keeps_its_hyphen_but_drops_the_season(self):
        """'MAN-YI-13' must not come out as 'Man-Yi-' — the trailing hyphen then
        survives normalization and the name never matches its storm."""
        for text, want in (
            ('GDACS Green alert: Green Tropical Cyclone MAN-YI-13 in Japan', 'Man-Yi'),
            ('Red Tropical Cyclone KONG-REY-24 in Philippines, Taiwan', 'Kong-Rey'),
            ('Orange Tropical Cyclone IN-FA-21 in Japan, China', 'In-Fa'),
        ):
            self.assertEqual(extract_typhoon_name(text), want, text)


@requires_kb
class ChineseNameAttributionTest(unittest.TestCase):
    """The CN name is the only key these bulletins offer, so it has to be both
    present in the KB and matched within the right season."""

    @classmethod
    def setUpClass(cls):
        from db import SessionLocal
        cls.session = SessionLocal()

    @classmethod
    def tearDownClass(cls):
        cls.session.close()

    def test_chinese_names_are_populated(self):
        """`Typhoon.name_cn` was NULL for every row, which silently disabled the
        CN branch of `_match_typhoon` for the whole KB."""
        from sqlalchemy import func, select
        from models import Typhoon
        named = self.session.scalar(
            select(func.count()).select_from(Typhoon).where(Typhoon.name_cn.isnot(None)))
        self.assertGreater(named, 0, "no Chinese names loaded — CN bulletins cannot resolve")

    def test_a_chinese_name_resolves_to_its_storm(self):
        from crawler.load import _match_typhoon
        got = _match_typhoon(self.session, '巴威', 2026)
        self.assertIsNotNone(got, "台风“巴威” should resolve to a 2026 storm")
        self.assertEqual(got.name, 'Bavi')

    def test_the_season_is_required_because_names_recycle(self):
        """Storm names come round again every few years. Resolving 格美 without a
        season matched Kaemi 2006 as readily as Gaemi 2024 — which is exactly how
        a repair pass nearly moved a 2024 bulletin onto a 2006 storm."""
        from sqlalchemy import func, select
        from crawler.load import _match_typhoon
        from models import Typhoon

        reused = self.session.execute(
            select(Typhoon.name_cn, func.count())
            .where(Typhoon.name_cn.isnot(None))
            .group_by(Typhoon.name_cn).having(func.count() > 1).limit(1)
        ).first()
        if reused is None:
            self.skipTest("no recycled Chinese name in this KB")
        name = reused[0]
        seasons = self.session.scalars(
            select(Typhoon.season_year).where(Typhoon.name_cn == name)).all()
        for year in seasons:
            got = _match_typhoon(self.session, name, year)
            self.assertIsNotNone(got)
            self.assertEqual(got.season_year, year,
                             f"{name} + {year} resolved to {got.season_year}")


@requires_kb
class NamedBulletinInvariantTest(unittest.TestCase):
    """No stored bulletin may sit on a storm whose name contradicts the one its
    own text quotes. This is the regression net for the Haishen/Bavi mix-up."""

    def test_no_bulletin_contradicts_the_storm_it_names(self):
        from sqlalchemy import select
        from crawler.load import _match_typhoon
        from db import SessionLocal
        from models import PublicInfo, SecondaryDisaster, Typhoon

        with SessionLocal() as s:
            bad = []
            for model, cols in ((SecondaryDisaster, ("description",)),
                                (PublicInfo, ("title", "body"))):
                for row in s.scalars(select(model)).all():
                    text = " ".join(str(getattr(row, c) or "") for c in cols)
                    name = extract_typhoon_name(text)
                    if not name:
                        continue
                    stamp = (getattr(row, "publish_time", None)
                             or getattr(row, "event_time", None))
                    year = stamp.year if stamp else None
                    target = _match_typhoon(s, name, year)
                    if target is not None and target.id != row.typhoon_id:
                        on = s.get(Typhoon, row.typhoon_id)
                        bad.append((model.__name__, row.id, name,
                                    on.intl_id if on else None, target.intl_id))
            self.assertEqual(bad, [], f"bulletins on the wrong storm: {bad[:5]}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
