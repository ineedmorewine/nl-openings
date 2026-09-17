import json
import unittest
from datetime import date
from pathlib import Path

from collector.filters import is_air_or_ocean_only, is_suitable
from collector.text import (dutch_required, keep_location, location_status, normalize_date,
                            parse_relative_posted, strip_html, written_in_dutch)

ROOT = Path(__file__).resolve().parent.parent
FILTERS = json.loads((ROOT / "docs/data/filters.json").read_text())
CASES = json.loads((ROOT / "tests/filter_cases.json").read_text())

DUTCH_AD = ("Als transportplanner ben jij verantwoordelijk voor de planning van onze internationale "
            "ritten. Je werkt samen met het team van de afdeling en je hebt contact met onze klanten "
            "en charters. Wij zoeken iemand die zelfstandig werkt en die ook in een druk team goed "
            "blijft plannen. Je hebt een uitstekende beheersing van de Nederlandse taal in woord en "
            "geschrift en ervaring met een TMS. Wat bieden wij? Een goed salaris en een fijne werkplek.")
ENGLISH_AD = ("As an intermodal planner you will be responsible for the planning of our rail "
              "connections between the Netherlands and Italy. You work with carriers and our "
              "customers, and you are the first point of contact for the terminal. We are looking "
              "for someone with experience in rail or road operations. Fluent English is required "
              "and Dutch is a plus. This is a full time role in our Venlo office with a good package.")


class FilterCases(unittest.TestCase):
    def test_shared_cases_match_expected_outcome(self):
        for case in CASES:
            with self.subTest(case["label"]):
                self.assertEqual(is_suitable(case["title"], FILTERS), case["suitable"])
                self.assertEqual(is_air_or_ocean_only(case["title"], FILTERS), case["air_ocean_only"])

    def test_company_extra_keywords_widen_include_list(self):
        self.assertFalse(is_suitable("Implementation Consultant", FILTERS))
        self.assertTrue(is_suitable("Implementation Consultant", FILTERS, ["implementation"]))


class Locations(unittest.TestCase):
    def test_dutch_city_and_country_are_recognised(self):
        self.assertEqual(location_status("Venlo"), "nl")
        self.assertEqual(location_status("Rotterdam, Zuid-Holland, Netherlands"), "nl")
        self.assertEqual(location_status("'s-Hertogenbosch"), "nl")
        self.assertEqual(location_status("Moerdijk, NL"), "nl")

    def test_foreign_locations_are_recognised(self):
        self.assertEqual(location_status("Hamburg, Germany"), "foreign")
        self.assertEqual(location_status("Antwerp"), "foreign")
        self.assertEqual(location_status("Lyon, FR"), "foreign")

    def test_unknown_location_depends_on_strictness(self):
        self.assertEqual(location_status("Hub 4"), "unknown")
        self.assertTrue(keep_location("Hub 4", strict=False))
        self.assertFalse(keep_location("Hub 4", strict=True))

    def test_city_names_need_word_boundaries(self):
        self.assertEqual(location_status("Bossche Broek Industrial"), "unknown")


class Language(unittest.TestCase):
    def test_dutch_ad_is_labelled(self):
        self.assertTrue(written_in_dutch(DUTCH_AD))
        self.assertTrue(dutch_required(DUTCH_AD))

    def test_english_ad_with_dutch_as_plus_is_not_required(self):
        self.assertFalse(written_in_dutch(ENGLISH_AD))
        self.assertFalse(dutch_required(ENGLISH_AD))

    def test_explicit_english_requirement_detected(self):
        text = ENGLISH_AD.replace("Dutch is a plus", "fluent Dutch and English are essential")
        self.assertTrue(dutch_required(text))

    def test_short_text_gives_no_verdict(self):
        self.assertIsNone(written_in_dutch("Planner"))
        self.assertIsNone(dutch_required("Planner"))


class Dates(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(normalize_date("2026-09-12T08:00:00Z"), "2026-09-12")
        self.assertEqual(normalize_date("2026-09-12 10:00:00 UTC"), "2026-09-12")
        self.assertEqual(normalize_date(1789200000000), "2026-09-12")
        self.assertEqual(normalize_date("Sat, 12 Sep 2026 08:00:00 +0000"), "2026-09-12")
        self.assertEqual(normalize_date("September 12, 2026"), "2026-09-12")
        self.assertIsNone(normalize_date("soon"))

    def test_workday_relative_dates(self):
        today = date(2026, 9, 15)
        self.assertEqual(parse_relative_posted("Posted Today", today), "2026-09-15")
        self.assertEqual(parse_relative_posted("Posted Yesterday", today), "2026-09-14")
        self.assertEqual(parse_relative_posted("Posted 3 Days Ago", today), "2026-09-12")
        self.assertIsNone(parse_relative_posted("Posted 30+ Days Ago", today))

    def test_strip_html_handles_escaped_markup(self):
        self.assertEqual(strip_html("&lt;p&gt;Rail &amp;amp; road&lt;/p&gt;"), "Rail & road")
        self.assertEqual(strip_html("<p>One</p><p>Two</p><script>x()</script>"), "One Two")


if __name__ == "__main__":
    unittest.main()
