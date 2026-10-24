import unittest

from collector.adapters import careerpage, rmk, vendors
from collector.discovery import detect_source
from tests.fakes import FakeNet

# ---------------------------------------------------------------- silent zeros

SF_BASE = "https://werkenbij.example.nl"


def sf_row(href, title, location, date="Sep 11, 2026"):
    return (f'<tr class="data-row"><td><span class="jobTitle">'
            f'<a href="{href}" class="jobTitle-link">{title}</a></span></td>'
            f'<td><span class="jobLocation">{location}</span></td>'
            f'<td><span class="jobDate">{date}</span></td></tr>')


class SuccessFactorsLocationFallback(unittest.TestCase):
    """A Dutch-only site lists plain city names, so a country search finds nothing."""

    def search(self, query, start):
        return FakeNet.key(SF_BASE + "/search/", dict(query, startrow=start))

    def test_falls_back_to_the_unfiltered_list_when_a_country_search_is_empty(self):
        country = {"q": "", "locationsearch": "Netherlands"}
        plain = {"q": ""}
        net = FakeNet(routes={
            self.search(country, 0): "<table></table>",
            self.search(plain, 0): "<table>" + sf_row("/job/1/", "Transportplanner", "Rotterdam") + "</table>",
            self.search(plain, 25): "<table></table>",
        })
        jobs = rmk.fetch(net, {"base_url": SF_BASE}, strict=True)
        self.assertEqual([j["title"] for j in jobs], ["Transportplanner"])
        self.assertEqual(jobs[0]["url"], SF_BASE + "/job/1/")

    def test_country_search_is_kept_when_it_does_return_dutch_rows(self):
        country = {"q": "", "locationsearch": "Netherlands"}
        net = FakeNet(routes={
            self.search(country, 0): "<table>" + sf_row("/job/2/", "Rail Planner", "Venlo") + "</table>",
            self.search(country, 25): "<table></table>",
        })
        jobs = rmk.fetch(net, {"base_url": SF_BASE}, strict=True)
        self.assertEqual([j["title"] for j in jobs], ["Rail Planner"])
        self.assertNotIn(self.search({"q": ""}, 0), net.calls)

    def test_foreign_rows_are_still_dropped_from_the_fallback(self):
        plain = {"q": ""}
        net = FakeNet(routes={
            self.search({"q": "", "locationsearch": "Netherlands"}, 0): "<table></table>",
            self.search(plain, 0): "<table>" + sf_row("/job/3/", "Planner", "Hamburg") + "</table>",
            self.search(plain, 25): "<table></table>",
        })
        self.assertEqual(rmk.fetch(net, {"base_url": SF_BASE}, strict=True), [])


class PhenomCountryFallback(unittest.TestCase):
    def widget_net(self, filtered, unfiltered):
        class Net(FakeNet):
            def post_json(self, url, payload):
                batch = filtered if payload["selected_fields"] else unfiltered
                jobs = batch if payload["from"] == 0 else []
                return {"refineSearch": {"totalHits": len(batch), "data": {"jobs": jobs}}}
        return Net()

    def job(self, job_id, city, country="Netherlands"):
        return {"jobId": job_id, "title": "Transport Planner", "cityState": city,
                "country": country, "jobSeoUrl": f"/job/{job_id}"}

    def test_unfiltered_list_is_used_when_the_country_facet_matches_nothing(self):
        net = self.widget_net(filtered=[], unfiltered=[self.job("1", "Venlo"),
                                                       self.job("2", "Bonn", "Germany")])
        jobs = vendors.phenom_fetch(net, {"base_url": "https://jobs.example.com"}, strict=True)
        self.assertEqual([j["ext_id"] for j in jobs], ["1"])

    def test_country_facet_is_kept_when_it_works(self):
        net = self.widget_net(filtered=[self.job("9", "Tilburg")], unfiltered=[])
        jobs = vendors.phenom_fetch(net, {"base_url": "https://jobs.example.com"}, strict=True)
        self.assertEqual([j["ext_id"] for j in jobs], ["9"])


# ---------------------------------------------------------------- generic reader

LIST_URL = "https://transporteur.nl/vacatures"


class CareerPageReader(unittest.TestCase):
    def test_reads_vacancies_listed_as_plain_links(self):
        page = ('<a href="/vacatures/transportplanner-internationaal">Transportplanner Internationaal</a>'
                '<a href="/vacatures/logistiek-medewerker">Logistiek medewerker</a>')
        net = FakeNet(routes={LIST_URL: page})
        jobs = careerpage.fetch(net, {"url": LIST_URL}, strict=False)
        self.assertEqual([j["title"] for j in jobs],
                         ["Transportplanner Internationaal", "Logistiek medewerker"])
        self.assertEqual(jobs[0]["url"], "https://transporteur.nl/vacatures/transportplanner-internationaal")

    def test_navigation_and_other_sites_are_not_mistaken_for_vacancies(self):
        page = ('<a href="/vacatures">Alle vacatures</a>'
                '<a href="/vacatures/">Vacatures</a>'
                '<a href="/vacatures/categorie/planning">Planning</a>'
                '<a href="/vacatures/page/2">2</a>'
                '<a href="https://linkedin.com/jobs/planner-rotterdam">Planner Rotterdam</a>'
                '<a href="/vacatures/open-sollicitatie">Open sollicitatie</a>'
                '<a href="/over-ons">Transportplanner Internationaal</a>')
        net = FakeNet(routes={LIST_URL: page})
        with self.assertRaises(RuntimeError):
            careerpage.fetch(net, {"url": LIST_URL}, strict=False)

    def test_describe_reads_the_location_from_the_posting(self):
        detail = "<html><body><p>Standplaats: Tilburg</p><p>Je plant ritten.</p></body></html>"
        net = FakeNet(routes={"https://transporteur.nl/vacatures/planner": detail})
        found = careerpage.describe(net, {}, {"url": "https://transporteur.nl/vacatures/planner"})
        self.assertEqual(found["location"], "Tilburg")
        self.assertIn("Je plant ritten", found["description"])

    def test_a_company_site_with_no_vacancy_links_raises(self):
        net = FakeNet(routes={LIST_URL: "<p>Geen vacatures op dit moment.</p>"})
        with self.assertRaises(RuntimeError):
            careerpage.fetch(net, {"url": LIST_URL}, strict=False)


# ---------------------------------------------------------------- detection

class BrandedAndFallbackDetection(unittest.TestCase):
    def test_a_brand_folder_on_a_shared_host_is_kept(self):
        """CEVA and CMA CGM share one host; the brand path tells them apart."""
        markup = '<a href="/CEVALogistics/search/">Alle vacatures</a>' \
                 '<link href="https://rmkcdn.successfactors.com/x.css">'
        source, _ = detect_source(markup, "https://jobs.cmacgm-group.com/CEVALogistics/?locale=en_GB")
        self.assertEqual(source, {"type": "rmk",
                                  "base_url": "https://jobs.cmacgm-group.com/CEVALogistics"})

    def test_a_plain_vacancy_page_falls_back_to_the_generic_reader(self):
        markup = ('<a href="/vacatures/transportplanner-venlo">Transportplanner Venlo</a>'
                  '<a href="/vacatures/wagenparkbeheerder">Wagenparkbeheerder Oost</a>')
        self.assertEqual(detect_source(markup, "https://transporteur.nl/vacatures")[0],
                         {"type": "careerpage", "url": "https://transporteur.nl/vacatures"})

    def test_a_homepage_is_never_treated_as_a_vacancy_list(self):
        markup = ('<a href="/vacatures/transportplanner-venlo">Transportplanner Venlo</a>'
                  '<a href="/vacatures/wagenparkbeheerder">Wagenparkbeheerder Oost</a>')
        self.assertIsNone(detect_source(markup, "https://transporteur.nl/")[0])

    def test_a_real_recruitment_system_still_wins_over_the_generic_reader(self):
        markup = ('<a href="/vacatures/transportplanner">Transportplanner Venlo</a>'
                  '<a href="/vacatures/planner-rail">Planner Rail Oost</a>'
                  '<script src="https://acme.recruitee.com/embed.js"></script>')
        self.assertEqual(detect_source(markup, "https://acme.nl/vacatures")[0],
                         {"type": "recruitee", "subdomain": "acme"})


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------- self-healing

import json as _json
import tempfile
from pathlib import Path
from unittest import mock

from collector import run


class GuessedSourceIsReconsidered(unittest.TestCase):
    """A guessed careers system that finds nothing must not stay cached forever.

    This is what kept CEVA reading the wrong company's job list: the guess was
    stored on the first run and never looked at again.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        (base / "data").mkdir()
        self.paths = {
            "COMPANIES_FILE": base / "companies.json",
            "JOBS_FILE": base / "data/jobs.json",
            "SOURCES_FILE": base / "data/sources.json",
            "DISCOVERY_FILE": base / "data/discovery.json",
            "FILTERS_FILE": Path(run.FILTERS_FILE),
        }
        self.paths["COMPANIES_FILE"].write_text(_json.dumps({"companies": [
            {"id": "guessed", "name": "Guessed", "group": "road_rail", "domain": "guessed.nl"},
            {"id": "pinned", "name": "Pinned", "group": "road_rail", "domain": "pinned.nl",
             "source": {"type": "recruitee", "subdomain": "pinned"}},
        ]}))
        self.cache = {
            "guessed": {"source": {"type": "recruitee", "subdomain": "guessed"}},
        }
        self.paths["DISCOVERY_FILE"].write_text(_json.dumps(self.cache))

    def tearDown(self):
        self.tmp.cleanup()

    def run_collector(self):
        routes = {"https://guessed.recruitee.com/api/offers/": {"offers": []},
                  "https://pinned.recruitee.com/api/offers/": {"offers": []}}
        with mock.patch.multiple(run, **self.paths), \
                mock.patch.object(run, "Net", lambda: FakeNet(routes)):
            run.main([])
        return _json.loads(self.paths["DISCOVERY_FILE"].read_text())

    def test_a_guess_that_finds_nothing_is_forgotten(self):
        cache = self.run_collector()
        self.assertNotIn("guessed", cache)

    def test_a_hand_pinned_source_is_left_alone(self):
        self.run_collector()
        sources = _json.loads(self.paths["SOURCES_FILE"].read_text())["sources"]
        pinned = [s for s in sources if s["id"] == "pinned"][0]
        self.assertEqual(pinned["status"], "ok")
        self.assertNotIn("retry_discovery", pinned)


class DutchVacancyLinkShapes(unittest.TestCase):
    """Dutch sites spell posting links two ways; both must be recognised."""

    def test_hyphen_joined_links_are_found(self):
        page = ('<a href="/vacature-transportplanner-venlo">Transportplanner Venlo</a>'
                '<a href="/vacatures/rail-coordinator">Rail Coordinator Oost</a>')
        found = careerpage.find_posting_links(page, "https://transporteur.nl/werken-bij")
        self.assertEqual([t for _, t in found],
                         ["Transportplanner Venlo", "Rail Coordinator Oost"])

    def test_the_overview_page_itself_is_not_counted_as_a_posting(self):
        page = '<a href="/vacatures">Vacatures</a><a href="/vacatures/">Onze vacatures</a>'
        self.assertEqual(careerpage.find_posting_links(page, "https://transporteur.nl/"), [])


class SecondOpinionOnZero(unittest.TestCase):
    """A recruitment system that lists nothing may just have an unreadable layout.

    CEVA's site showed jobs to a browser while the structured reader saw none, and
    the run reported "ok, 0 jobs". Reading the page as plain links catches that.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        (base / "data").mkdir()
        self.paths = {
            "COMPANIES_FILE": base / "companies.json",
            "JOBS_FILE": base / "data/jobs.json",
            "SOURCES_FILE": base / "data/sources.json",
            "DISCOVERY_FILE": base / "data/discovery.json",
            "FILTERS_FILE": Path(run.FILTERS_FILE),
        }
        self.paths["COMPANIES_FILE"].write_text(_json.dumps({"companies": [{
            "id": "acme", "name": "Acme", "group": "road_rail", "domain": "acme.nl",
            "careers_url": "https://jobs.acme.com/Acme/search/",
            "source": {"type": "recruitee", "subdomain": "acme"},
        }]}))
        self.paths["DISCOVERY_FILE"].write_text("{}")

    def tearDown(self):
        self.tmp.cleanup()

    def collect(self, careers_markup):
        routes = {"https://acme.recruitee.com/api/offers/": {"offers": []},
                  "https://jobs.acme.com/Acme/search/": careers_markup}
        with mock.patch.multiple(run, **self.paths), \
                mock.patch.object(run, "Net", lambda: FakeNet(routes)):
            run.main([])
        sources = _json.loads(self.paths["SOURCES_FILE"].read_text())["sources"]
        jobs = _json.loads(self.paths["JOBS_FILE"].read_text())["jobs"]
        return sources[0], jobs

    def test_jobs_listed_as_links_are_picked_up_after_an_empty_answer(self):
        markup = ('<a href="/job/Rotterdam-Transport-Planner/12345/">Transport Planner Rotterdam</a>'
                  '<a href="/job/Venlo-Rail-Coordinator/12346/">Rail Coordinator Venlo</a>')
        source, jobs = self.collect(markup)
        self.assertEqual(source["status"], "ok")
        self.assertEqual(source["nl_jobs"], 2)
        self.assertEqual(source["method"], "recruitee+careerpage")
        self.assertEqual(sorted(j["title"] for j in jobs),
                         ["Rail Coordinator Venlo", "Transport Planner Rotterdam"])

    def test_a_company_with_genuinely_nothing_open_still_reports_zero(self):
        source, jobs = self.collect("<p>Geen vacatures op dit moment.</p>")
        self.assertEqual(source["status"], "ok")
        self.assertEqual(source["nl_jobs"], 0)
        self.assertEqual(source["method"], "recruitee")
        self.assertEqual(jobs, [])
