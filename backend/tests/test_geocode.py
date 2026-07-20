"""Tests for the news geocoder (services/geocode.py).

It resolves a place out of a GDELT headline by matching the admin-region
gazetteer against the text. Substring matching is the whole mechanism, so the
failure mode is false positives, and the gazetteer is full of traps: Hong Kong
has districts named Eastern / Central / Southern, Myanmar a state named Chin,
and Kagoshima a town romanized "China".

Nothing has exercised this path yet — GDELT has contributed no rows — so these
are the first checks it has had.

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

from services.geocode import _alias_in, _min_len_ok, _stem  # noqa: E402


class AliasMatchTest(unittest.TestCase):
    """Pure matching rules — no gazetteer needed."""

    def test_latin_needs_a_word_boundary(self):
        self.assertFalse(_alias_in("Chin", "Chinese authorities issued an alert"))
        self.assertTrue(_alias_in("Chin", "flooding across Chin State"))

    def test_latin_is_case_sensitive(self):
        """English capitalizes proper nouns; that is the only thing separating
        Hong Kong's Eastern District from the word "eastern"."""
        self.assertFalse(_alias_in("Eastern", "landfall in eastern China"))
        self.assertTrue(_alias_in("Eastern", "evacuations in Eastern District"))

    def test_cjk_matches_as_a_plain_substring(self):
        """No spaces and no case to work with, so substring is all there is."""
        self.assertTrue(_alias_in("温州", "台风在浙江温州登陆"))
        self.assertFalse(_alias_in("温州", "台风在福建登陆"))

    def test_punctuation_in_a_name_is_not_a_regex(self):
        self.assertTrue(_alias_in("Ho Chi Minh", "damage in Ho Chi Minh City"))


class StemAndLengthTest(unittest.TestCase):

    def test_administrative_suffixes_are_stripped(self):
        self.assertEqual(_stem("温州市"), "温州")
        self.assertEqual(_stem("鹿児島県"), "鹿児島")
        self.assertEqual(_stem("Zhejiang Province"), "Zhejiang")

    def test_a_name_that_is_only_a_suffix_survives(self):
        # 市 alone must not be stemmed to nothing and then matched everywhere.
        self.assertEqual(_stem("市"), "市")

    def test_short_aliases_are_rejected(self):
        self.assertFalse(_min_len_ok("市"))      # 1 CJK char
        self.assertTrue(_min_len_ok("温州"))     # 2 CJK chars
        self.assertFalse(_min_len_ok("Ube"))    # 3 latin chars
        self.assertTrue(_min_len_ok("Chin"))    # 4 latin chars


@requires_kb
class GeocodeAgainstGazetteerTest(unittest.TestCase):
    """End-to-end against the loaded admin_region gazetteer."""

    def geocode(self, text):
        from services.geocode import geocode
        return geocode(text)

    def test_a_bare_country_name_resolves_to_the_country(self):
        """Kagoshima has a town romanized "China". Ranking purely by specificity
        sent every English story about China to that town."""
        hit = self.geocode("Typhoon Bavi makes landfall in eastern China")
        self.assertIsNotNone(hit)
        self.assertEqual(hit.admin_level, 0)
        self.assertEqual(hit.matched, "China")

    def test_a_chinese_city_resolves_to_the_city(self):
        hit = self.geocode("台风“巴威”在浙江温州登陆")
        self.assertIsNotNone(hit)
        self.assertEqual(hit.admin_level, 2)
        self.assertIn("Wenzhou", hit.region_name)

    def test_prose_with_no_place_resolves_to_nothing(self):
        for text in ("A sandwich shop reopened after the storm",
                     "Storm damage reported across the region",
                     "大学生志愿者参与救灾",
                     "Chinese authorities issued a red alert"):
            self.assertIsNone(self.geocode(text), text)

    def test_known_limitation_cjk_names_inside_longer_words(self):
        """中山大学 (Sun Yat-sen University) still resolves to 中山市. CJK offers
        neither spaces nor case, so the mechanisms that fix the Latin cases do
        not apply. Recorded so the behaviour is a known limit, not a surprise."""
        hit = self.geocode("中山大学的研究团队发布报告")
        self.assertIsNotNone(hit)
        self.assertEqual(hit.matched, "中山")


if __name__ == "__main__":
    unittest.main(verbosity=2)
