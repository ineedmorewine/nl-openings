import unittest

from collector.adapters import careerpage, rmk, vendors
from collector.adapters.careerpage import _looks_like_a_posting
from collector.adapters import sitemap
from collector.discovery import detect_source, discover, find_domain
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

from collector import discovery, run


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


class SecondOpinionPageChoice(unittest.TestCase):
    """Which pages get read as links when a recruitment system reports nothing."""

    company = {"id": "ceva", "domain": "cevalogistics.com",
               "careers_url": "https://jobs.example.com/Brand/?locale=en_GB"}

    def test_the_search_page_is_tried_before_the_brand_landing_page(self):
        """A brand landing page lists no jobs; its search page does."""
        pages = run.second_opinion_pages(
            self.company, {}, {"type": "rmk", "base_url": "https://jobs.example.com/Brand"})
        self.assertEqual(pages[0], "https://jobs.example.com/Brand/search/")
        self.assertIn("https://jobs.example.com/Brand/?locale=en_GB", pages)

    def test_the_front_page_is_not_offered_as_a_careers_page(self):
        """Both spellings of the front page must count as the front page."""
        cache = {"kn": {"checked_pages": ["https://www.kuehne-nagel.com/",
                                          "https://kuehne-nagel.com/",
                                          "https://jobs.kuehne-nagel.com/"]}}
        link = run.careers_link({"id": "kn", "domain": "kuehne-nagel.com"}, cache)
        self.assertEqual(link, "https://jobs.kuehne-nagel.com/")

    def test_a_company_with_only_a_front_page_still_gets_a_link(self):
        link = run.careers_link({"id": "x", "domain": "acme.nl"},
                                {"x": {"checked_pages": ["https://acme.nl/"]}})
        self.assertIn(link, ("https://acme.nl/", "https://www.acme.nl/"))


class GenericLinkLabels(unittest.TestCase):
    """Dutch careers pages often label every vacancy link the same way.

    Neele-Vat lists 29 vacancies whose links all read "View the vacancy", so
    using the link text as the title produced 29 identical, useless entries.
    """

    page_url = "https://werkenbijneelevat.com/vacancies/"

    def titles(self, markup):
        return [t for _, t in careerpage.find_posting_links(markup, self.page_url)]

    def test_a_repeated_label_gives_way_to_the_title_in_the_address(self):
        markup = ('<a href="/vacancies/transportplanner-ftl/">View the vacancy</a>'
                  '<a href="/vacancies/freight-forwarder-road-transport-uk/">Bekijk de vacature</a>')
        self.assertEqual(self.titles(markup),
                         ["Transportplanner Ftl", "Freight Forwarder Road Transport Uk"])

    def test_a_real_title_in_the_link_is_kept(self):
        markup = '<a href="/vacancies/container-planner/">Container Planner Rotterdam Pernis</a>'
        self.assertEqual(self.titles(markup), ["Container Planner Rotterdam Pernis"])

    def test_a_repeated_slug_loses_its_trailing_number(self):
        markup = '<a href="/vacancies/export-forwarder-3/">Lees meer</a>'
        self.assertEqual(self.titles(markup), ["Export Forwarder"])

    def test_navigation_is_still_not_a_vacancy(self):
        markup = ('<a href="/vacancies/">View the vacancy</a>'
                  '<a href="/vacancies/page/2">Read more</a>')
        self.assertEqual(self.titles(markup), [])


class NoRecruitmentSystemStillGetsRead(unittest.TestCase):
    """A company without a recognised system must still be read, not skipped.

    Most of the list was never read at all because discovery found no known
    recruitment system and collection stopped there, however plainly the
    company published its vacancies.
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
            "id": "vervoerder", "name": "Vervoerder", "group": "road_rail",
            "domain": "vervoerder.nl",
        }]}))
        self.paths["DISCOVERY_FILE"].write_text("{}")

    def tearDown(self):
        self.tmp.cleanup()

    def collect(self, vacancy_page):
        routes = {
            "https://vervoerder.nl/": '<a href="/vacatures">Werken bij ons</a>',
            "https://www.vervoerder.nl/": '<a href="/vacatures">Werken bij ons</a>',
            "https://vervoerder.nl/vacatures": vacancy_page,
        }
        with mock.patch.multiple(run, **self.paths), \
                mock.patch.object(run, "Net", lambda: FakeNet(routes)):
            run.main([])
        sources = _json.loads(self.paths["SOURCES_FILE"].read_text())["sources"]
        jobs = _json.loads(self.paths["JOBS_FILE"].read_text())["jobs"]
        return sources[0], jobs

    def test_vacancies_published_as_plain_links_are_collected(self):
        page = ('<a href="/vacatures/transportplanner-venlo">Transportplanner Venlo</a>'
                '<a href="/vacatures/rail-coordinator-oost">Rail Coordinator Oost</a>')
        source, jobs = self.collect(page)
        self.assertEqual(source["status"], "ok")
        self.assertEqual(source["method"], "careerpage")
        self.assertEqual(sorted(j["title"] for j in jobs),
                         ["Rail Coordinator Oost", "Transportplanner Venlo"])

    def test_a_company_publishing_nothing_is_still_reported_honestly(self):
        source, jobs = self.collect("<p>Op dit moment geen vacatures.</p>")
        self.assertEqual(source["status"], "no_careers_system_found")
        self.assertEqual(jobs, [])


class ForeignJobsAreDroppedOnceTheirLocationIsKnown(unittest.TestCase):
    """A link reader learns the location only by opening the posting.

    A Henkel vacancy in Bangkok reached the page because the location arrived
    after the job had already been kept.
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
            "id": "henkel", "name": "Henkel", "group": "in_house", "domain": "henkel.com",
            "careers_url": "https://henkel.com/careers",
        }]}))
        self.paths["DISCOVERY_FILE"].write_text("{}")

    def tearDown(self):
        self.tmp.cleanup()

    def test_only_the_dutch_posting_is_kept(self):
        listing = ('<a href="/careers/demand-planner-bangkok">Demand Planner Bangkok</a>'
                   '<a href="/careers/transport-planner-venlo">Transport Planner Venlo</a>')
        def posting(place):
            return f"<html><body><p>Location: {place}</p><p>" + ("Plan shipments. " * 20) + "</p></body></html>"
        routes = {
            "https://henkel.com/careers": listing,
            "https://henkel.com/careers/demand-planner-bangkok": posting("Bangkok"),
            "https://henkel.com/careers/transport-planner-venlo": posting("Venlo"),
        }
        with mock.patch.multiple(run, **self.paths), \
                mock.patch.object(run, "Net", lambda: FakeNet(routes)):
            run.main([])
        jobs = _json.loads(self.paths["JOBS_FILE"].read_text())["jobs"]
        self.assertEqual([j["title"] for j in jobs], ["Transport Planner Venlo"])


class PlatformsHiddenBehindJavaScript(unittest.TestCase):
    """A careers page that builds its list in the browser looks empty to us.

    Many such pages still run on a platform we can read, and its address
    answers directly even when the page gives nothing away.
    """

    def company(self):
        return {"id": "einride", "domain": "einride.tech"}

    def routes(self, extra):
        base = {
            "https://einride.tech/": '<a href="https://careers.einride.tech">Careers</a>',
            "https://www.einride.tech/": '<a href="https://careers.einride.tech">Careers</a>',
            "https://careers.einride.tech": "<html><body><div id=app></div></body></html>",
        }
        base.update(extra)
        return base

    def test_a_feed_answering_on_the_careers_host_identifies_the_platform(self):
        feed = ('<?xml version="1.0"?><rss><channel><item><title>Planner</title>'
                '<link>https://careers.einride.tech/jobs/1</link></item></channel></rss>')
        net = FakeNet(routes=self.routes({"https://careers.einride.tech/jobs.rss": feed}))
        found = discovery.discover(net, self.company())
        self.assertEqual(found["source"],
                         {"type": "teamtailor", "base_url": "https://careers.einride.tech"})

    def test_a_recruitee_account_is_found_the_same_way(self):
        net = FakeNet(routes=self.routes(
            {"https://careers.einride.tech/api/offers/": {"offers": [{"id": 1}]}}))
        found = discovery.discover(net, self.company())
        self.assertEqual(found["source"]["type"], "recruitee")

    def test_the_company_front_page_is_never_probed(self):
        """Probing the front page would pick up unrelated feeds."""
        net = FakeNet(routes=self.routes({"https://einride.tech/jobs.rss": "<item>news</item>"}))
        found = discovery.discover(net, self.company())
        self.assertIsNone(found["source"])

    def test_an_empty_page_with_no_platform_behind_it_still_reports_nothing(self):
        found = discovery.discover(FakeNet(routes=self.routes({})), self.company())
        self.assertIsNone(found["source"])
        self.assertEqual(found["reason"], "no_careers_system_found")


class LinkReadJobsNeedAConfirmedDutchLocation(unittest.TestCase):
    """A global company lists its foreign vacancies beside its Dutch ones.

    Reading that page as links says nothing about where a job is, so a job we
    cannot place must not be presented as a Dutch opening. A Dutch-only site
    needs no such proof.
    """

    listing = ('<a href="/careers/transport-planner">Transport Planner</a>'
               '<a href="/careers/demand-planner">Demand Planner</a>')

    def build(self, domain):
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
            "id": "acme", "name": "Acme", "group": "road_rail", "domain": domain,
            "careers_url": f"https://{domain}/careers",
        }]}))
        self.paths["DISCOVERY_FILE"].write_text("{}")

    def collect(self, domain, postings):
        self.build(domain)
        routes = {f"https://{domain}/careers": self.listing}
        routes.update(postings)
        with mock.patch.multiple(run, **self.paths), \
                mock.patch.object(run, "Net", lambda: FakeNet(routes)):
            run.main([])
        jobs = _json.loads(self.paths["JOBS_FILE"].read_text())["jobs"]
        self.tmp.cleanup()
        return sorted(j["title"] for j in jobs)

    def test_a_global_company_keeps_only_what_we_can_place_in_the_netherlands(self):
        body = lambda place: f"<html><body><p>Location: {place}</p></body></html>"
        titles = self.collect("acme.com", {
            "https://acme.com/careers/transport-planner": body("Venlo"),
            "https://acme.com/careers/demand-planner": body("Bangkok"),
        })
        self.assertEqual(titles, ["Transport Planner"])

    def test_a_global_company_drops_a_job_it_cannot_place_at_all(self):
        blank = "<html><body><p>Join our team.</p></body></html>"
        titles = self.collect("acme.com", {
            "https://acme.com/careers/transport-planner": blank,
            "https://acme.com/careers/demand-planner": blank,
        })
        self.assertEqual(titles, [])

    def test_a_dutch_only_site_keeps_its_jobs_without_proof(self):
        blank = "<html><body><p>Kom bij ons werken.</p></body></html>"
        titles = self.collect("acme.nl", {
            "https://acme.nl/careers/transport-planner": blank,
            "https://acme.nl/careers/demand-planner": blank,
        })
        self.assertEqual(titles, ["Demand Planner", "Transport Planner"])


class WhatAnEarlierRunLearnedIsKept(unittest.TestCase):
    """A job seen before is not opened again, so its location must carry over.

    Losing it discarded the job on the next run as one we could not place,
    which silently emptied Neele-Vat of all 29 vacancies.
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
            "id": "neelevat", "name": "Neele-Vat", "group": "forwarders",
            "domain": "neelevat.com", "careers_url": "https://werkenbij.example.com/vacancies/",
        }]}))
        self.paths["DISCOVERY_FILE"].write_text("{}")

    def tearDown(self):
        self.tmp.cleanup()

    def collect(self, posting):
        routes = {
            "https://werkenbij.example.com/vacancies/":
                '<a href="/vacancies/transportplanner-ftl/">View the vacancy</a>',
            "https://werkenbij.example.com/vacancies/transportplanner-ftl/": posting,
        }
        with mock.patch.multiple(run, **self.paths), \
                mock.patch.object(run, "Net", lambda: FakeNet(routes)):
            run.main([])
        return _json.loads(self.paths["JOBS_FILE"].read_text())["jobs"]

    def test_the_job_survives_a_second_run_that_does_not_reopen_it(self):
        posting = "<html><body><p>Standplaats: Rotterdam</p><p>" + ("Je plant ritten. " * 20) + "</p></body></html>"
        first = self.collect(posting)
        self.assertEqual([j["location"] for j in first], ["Rotterdam"])

        # Second run: the posting is not opened again because the labels are known.
        second = self.collect(posting)
        self.assertEqual([j["title"] for j in second], ["Transportplanner Ftl"])
        self.assertEqual([j["location"] for j in second], ["Rotterdam"])


class RenderingAsTheLastResort(unittest.TestCase):
    """A careers page that builds its list after loading is empty when downloaded.

    Opening it in a browser is the only way to see those vacancies, so it runs
    for companies that would otherwise be reported as unreadable.
    """

    rendered = ('<a href="/vacatures/transportplanner-venlo">Transportplanner Venlo</a>'
                '<a href="/vacatures/rail-coordinator">Rail Coordinator Oost</a>')

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
            "id": "barsan", "name": "Barsan", "group": "road_rail", "domain": "barsan.nl",
            "careers_url": "https://barsan.nl/jobs",
        }]}))
        self.paths["DISCOVERY_FILE"].write_text("{}")

    def tearDown(self):
        self.tmp.cleanup()

    def collect(self, renderer):
        # Downloading the page gives an empty shell, as it does on the real site.
        routes = {"https://barsan.nl/jobs": "<html><body><div id=app></div></body></html>",
                  "https://barsan.nl/": "<a href='/jobs'>Jobs</a>",
                  "https://www.barsan.nl/": "<a href='/jobs'>Jobs</a>"}
        with mock.patch.multiple(run, **self.paths), \
                mock.patch.object(run, "Net", lambda: FakeNet(routes)), \
                mock.patch.object(run, "PageRenderer", lambda budget: renderer):
            run.main([])
        sources = _json.loads(self.paths["SOURCES_FILE"].read_text())["sources"]
        jobs = _json.loads(self.paths["JOBS_FILE"].read_text())["jobs"]
        return sources[0], jobs

    def fake_renderer(self, html=None, error=None, budget=5):
        class Renderer:
            def __init__(self):
                self.budget = budget
                self.rendered = []
                self.last_error = None

            @property
            def exhausted(self):
                return self.budget <= 0

            def render(self, url):
                self.budget -= 1
                self.rendered.append(url)
                if error:
                    raise error
                return html

            def close(self):
                pass
        return Renderer()

    def test_vacancies_that_only_appear_in_a_browser_are_collected(self):
        renderer = self.fake_renderer(html=self.rendered)
        source, jobs = self.collect(renderer)
        self.assertEqual(source["status"], "ok")
        self.assertEqual(source["method"], "careerpage (rendered)")
        self.assertEqual(sorted(j["title"] for j in jobs),
                         ["Rail Coordinator Oost", "Transportplanner Venlo"])

    def test_without_a_browser_the_company_is_reported_unreadable_as_before(self):
        renderer = self.fake_renderer(error=run.BrowserUnavailable("no browser"))
        source, jobs = self.collect(renderer)
        self.assertEqual(source["status"], "no_careers_system_found")
        self.assertEqual(jobs, [])

    def test_rendering_is_not_attempted_once_the_budget_is_spent(self):
        renderer = self.fake_renderer(html=self.rendered, budget=0)
        source, _ = self.collect(renderer)
        self.assertEqual(source["status"], "no_careers_system_found")
        self.assertEqual(renderer.rendered, [])


class RenderingReportsWhatHappened(unittest.TestCase):
    """Rendering rescued nobody on its first run and the result said nothing.

    A company that reaches the browser now records what the attempt did, so a
    browser that will not start is told apart from pages that list nothing.
    """

    def pages(self):
        return ["https://acme.nl/vacatures"]

    def attempt(self, renderer):
        with mock.patch.object(run, "second_opinion_pages", lambda *a, **k: self.pages()):
            return run.rendered_second_opinion(renderer, {"id": "acme"}, {})

    def test_a_browser_that_will_not_start_is_named(self):
        class Renderer:
            exhausted = False
            last_error = None

            def render(self, url):
                raise run.BrowserUnavailable("Executable doesn't exist")
        report = self.attempt(Renderer())["report"]
        self.assertIn("no browser", report)
        self.assertIn("Executable", report)

    def test_pages_that_render_but_list_nothing_say_so(self):
        class Renderer:
            exhausted = False
            last_error = None

            def render(self, url):
                return "<html><body><p>Geen vacatures.</p></body></html>"
        attempt = self.attempt(Renderer())
        self.assertEqual(attempt["jobs"], [])
        self.assertEqual(attempt["report"],
                         "rendered ['https://acme.nl/vacatures'], found 0 link(s)")

    def test_a_spent_budget_is_distinguished_from_a_failure(self):
        class Renderer:
            exhausted = True
            last_error = None
        self.assertIn("budget spent", self.attempt(Renderer())["report"])


class SampleLinksExplainAnEmptyRender(unittest.TestCase):
    """Fifty pages rendered and none looked like a vacancy, with no clue why."""

    def test_the_links_a_page_did_offer_are_recorded(self):
        markup = ('<a href="/werken-bij/functie/transportplanner">Transportplanner</a>'
                  '<a href="https://ats.example.com/go/1234">Apply</a>'
                  '<a href="/">Home</a>')
        samples = careerpage.sample_links(markup, "https://acme.nl/werken-bij")
        self.assertIn("/werken-bij/functie/transportplanner", samples)
        self.assertIn("[other host] /go/1234", samples)
        self.assertNotIn("/", samples)

    def test_the_report_carries_them_when_nothing_matched(self):
        class Renderer:
            exhausted = False
            last_error = None

            def render(self, url):
                return '<a href="/werken-bij/functie/planner">Planner</a>'
        with mock.patch.object(run, "second_opinion_pages",
                               lambda *a, **k: ["https://acme.nl/werken-bij"]):
            report = run.rendered_second_opinion(Renderer(), {"id": "acme"}, {})["report"]
        self.assertIn("found 0 link(s)", report)
        self.assertIn("/werken-bij/functie/planner", report)


class RenderingFollowsTheRealCareersPage(unittest.TestCase):
    """Rendering found nothing because it was reading the wrong page.

    Of 43 companies, 20 rendered a homepage or a dead /careers guess that
    simply linked to the real careers page.
    """

    def attempt(self, pages, start="https://acme.nl/careers"):
        rendered = []

        class Renderer:
            exhausted = False
            last_error = None

            def render(self, url):
                rendered.append(url)
                if url not in pages:
                    raise RuntimeError("404")
                return pages[url]

        with mock.patch.object(run, "second_opinion_pages", lambda *a, **k: [start]):
            return run.rendered_second_opinion(Renderer(), {"id": "acme"}, {}), rendered

    def test_a_careers_link_on_the_rendered_page_is_followed(self):
        pages = {
            "https://acme.nl/careers": '<a href="/werken-bij">Werken bij ons</a>',
            "https://acme.nl/werken-bij":
                '<a href="/vacatures/transportplanner-venlo">Transportplanner Venlo</a>'
                '<a href="/vacatures/rail-coordinator">Rail Coordinator Oost</a>',
        }
        attempt, rendered = self.attempt(pages)
        self.assertEqual(sorted(j["title"] for j in attempt["jobs"]),
                         ["Rail Coordinator Oost", "Transportplanner Venlo"])
        self.assertEqual(rendered, ["https://acme.nl/careers", "https://acme.nl/werken-bij"])

    def test_following_stops_rather_than_wandering_the_site(self):
        pages = {"https://acme.nl/careers":
                 "".join(f'<a href="/careers-{i}">Careers {i}</a>' for i in range(6))}
        _, rendered = self.attempt(pages)
        self.assertLessEqual(len(rendered), 1 + run.RENDER_FOLLOWED_PAGES)

    def test_a_vacancy_site_on_another_host_is_followed(self):
        """Dutch employers keep theirs on werkenbij<brand>.nl, a different host."""
        pages = {"https://acme.nl/careers":
                 '<a href="https://werkenbijacme.nl/vacatures">Werken bij Acme</a>'}
        _, rendered = self.attempt(pages)
        self.assertIn("https://werkenbijacme.nl/vacatures", rendered)

    def test_a_job_board_is_not_followed(self):
        pages = {"https://acme.nl/careers":
                 '<a href="https://nl.indeed.com/jobs?q=acme">Vacatures op Indeed</a>'}
        _, rendered = self.attempt(pages)
        self.assertEqual(rendered, ["https://acme.nl/careers"])


class WwwIsTheSameSite(unittest.TestCase):
    """Several pages' own vacancy links were discarded as belonging elsewhere."""

    def test_a_link_to_the_www_form_is_kept(self):
        markup = '<a href="https://www.acme.nl/vacatures/transportplanner">Transportplanner Venlo</a>'
        found = careerpage.find_posting_links(markup, "https://acme.nl/werken-bij")
        self.assertEqual([t for _, t in found], ["Transportplanner Venlo"])

    def test_a_genuinely_different_site_is_still_rejected(self):
        markup = '<a href="https://indeed.com/vacatures/transportplanner">Transportplanner Venlo</a>'
        self.assertEqual(careerpage.find_posting_links(markup, "https://acme.nl/werken-bij"), [])


class VacanciesWrittenAsHeadings(unittest.TestCase):
    """Some companies put every vacancy on one page with no link to follow.

    Barsan publishes a Senior Transport Planner as a heading above an
    application form, so reading only links made the company look empty.
    """

    page_url = "https://www.barsan.nl/jobs"

    def titles(self, markup):
        return [t for _, t in careerpage.find_posting_headings(markup, self.page_url)]

    def test_a_role_heading_becomes_a_vacancy(self):
        markup = ("<h2>Vacature</h2><h3>Senior Transport Planner</h3>"
                  "<p>Solliciteer nu</p>")
        self.assertEqual(self.titles(markup), ["Senior Transport Planner"])

    def test_each_one_points_back_at_the_page_with_its_own_fragment(self):
        markup = "<h3>Senior Transport Planner</h3><h3>Logistiek Medewerker Venlo</h3>"
        urls = [u for u, _ in careerpage.find_posting_headings(markup, self.page_url)]
        self.assertEqual(urls, [f"{self.page_url}#senior-transport-planner",
                                f"{self.page_url}#logistiek-medewerker-venlo"])

    def test_ordinary_page_headings_are_not_vacancies(self):
        markup = ("<h1>Barsan Global Logistics</h1><h2>Over ons</h2>"
                  "<h2>Onze diensten</h2><h3>Contact opnemen</h3><h2>Vacature</h2>")
        self.assertEqual(self.titles(markup), [])

    def test_the_same_role_listed_twice_appears_once(self):
        markup = "<h3>Senior Transport Planner</h3><h4>senior transport planner</h4>"
        self.assertEqual(self.titles(markup), ["Senior Transport Planner"])

    def test_links_are_preferred_when_the_page_has_them(self):
        markup = ('<h3>Senior Transport Planner</h3>'
                  '<a href="/vacatures/transportplanner-venlo">Transportplanner Venlo</a>'
                  '<a href="/vacatures/rail-coordinator">Rail Coordinator Oost</a>')
        net = FakeNet(routes={self.page_url: markup})
        jobs = careerpage.fetch(net, {"url": self.page_url}, strict=False)
        self.assertEqual(sorted(j["title"] for j in jobs),
                         ["Rail Coordinator Oost", "Transportplanner Venlo"])


class ADutchCareersSiteCountsAsDutch(unittest.TestCase):
    """Barsan's group address is barsan.com while its careers site is barsan.nl.

    Judging by the group address alone treated every vacancy there as needing
    proof it was in the Netherlands, and discarded the open Senior Transport
    Planner for lack of it.
    """

    def test_a_dutch_careers_site_needs_no_proof(self):
        self.assertFalse(run.is_strict({"domain": "barsan.com",
                                        "careers_url": "https://www.barsan.nl/jobs"}))

    def test_the_page_actually_read_counts_too(self):
        self.assertFalse(run.is_strict({"domain": "acme.com"},
                                       "https://werkenbij.acme.nl/vacatures"))

    def test_a_company_with_no_dutch_address_anywhere_still_needs_proof(self):
        self.assertTrue(run.is_strict({"domain": "scania.com"},
                                      "https://jobs.scania.com/search"))

    def test_an_explicit_scope_overrides_the_address(self):
        self.assertTrue(run.is_strict({"domain": "acme.nl", "scope": "global"}))


class OneVacancyListedTwiceAppearsOnce(unittest.TestCase):
    """Grolleman's vacancies appeared twice, once per careers page read."""

    def test_the_same_title_on_two_pages_is_one_job(self):
        page = "<h3>Medewerker Overslag</h3><h3>Chauffeur Koeltransport</h3>"
        net = FakeNet(routes={"https://grolleman.nl/vacatures": page,
                              "https://grolleman.nl/werken-bij": page})
        jobs = careerpage.fetch(net, {"urls": ["https://grolleman.nl/vacatures",
                                               "https://grolleman.nl/werken-bij"]},
                                strict=False)
        self.assertEqual(sorted(j["title"] for j in jobs),
                         ["Chauffeur Koeltransport", "Medewerker Overslag"])


class AServicePageIsNotAVacancy(unittest.TestCase):
    """Cabooter keeps its service pages under /career/, where they read as jobs."""

    def test_a_service_page_is_rejected(self):
        self.assertFalse(_looks_like_a_posting(
            "https://www.cabootergroup.com/career/silk-road", "Silk Road"))
        self.assertFalse(_looks_like_a_posting(
            "https://www.cabootergroup.com/career/our-services/road-transport", "Road Transport"))

    def test_a_real_opening_still_passes(self):
        self.assertTrue(_looks_like_a_posting(
            "https://www.cabootergroup.com/career/career/jobs/intermodal-planner",
            "Intermodal Planner"))
        self.assertTrue(_looks_like_a_posting(
            "https://werkenbijneelevat.com/vacancies/container-planner/", "Container Planner"))


# ------------------------------------------------- finding the careers page

class TheCompanysOwnLinkIsTriedBeforeGuessedAddresses(unittest.TestCase):
    """Eleven guessed paths used to spend the page budget before the real link."""

    def test_the_real_link_is_read_even_though_guesses_exist(self):
        net = FakeNet({
            "https://acme.nl/": '<a href="/over-ons/werken-bij">Werken bij</a>',
            "https://www.acme.nl/": '<a href="/over-ons/werken-bij">Werken bij</a>',
            "https://acme.nl/over-ons/werken-bij":
                '<script src="https://boards.greenhouse.io/embed/job_board/js?for=acme"></script>',
        })
        found = discover(net, {"id": "acme", "domain": "acme.nl"})
        self.assertEqual(found["reason"], "found")
        self.assertEqual(found["source"]["type"], "greenhouse")


class TheSiteMapNamesVacanciesThePageHides(unittest.TestCase):
    def test_vacancy_addresses_are_taken_from_the_sitemap(self):
        net = FakeNet({
            "https://acme.nl/": "<html>nothing here</html>",
            "https://www.acme.nl/": "<html>nothing here</html>",
            "https://acme.nl/sitemap.xml":
                "<urlset><url><loc>https://acme.nl/vacatures/transportplanner</loc></url>"
                "<url><loc>https://acme.nl/vacatures/expediteur</loc></url>"
                "<url><loc>https://acme.nl/contact</loc></url></urlset>",
        })
        found = discover(net, {"id": "acme", "domain": "acme.nl"})
        self.assertEqual(found["reason"], "found")
        self.assertEqual(found["source"]["type"], "sitemap")
        self.assertEqual(len(found["source"]["urls"]), 2)

    def test_a_vacancy_page_is_read_as_one_posting(self):
        net = FakeNet({
            "https://acme.nl/vacatures/transportplanner":
                "<html><h1>Transportplanner Rotterdam</h1><p>Standplaats: Rotterdam. "
                "Je plant ritten voor onze klanten in Europa.</p></html>",
        })
        jobs = sitemap.fetch(net, {"urls": ["https://acme.nl/vacatures/transportplanner"]}, True)
        self.assertEqual(jobs[0]["title"], "Transportplanner Rotterdam")
        self.assertEqual(jobs[0]["location"], "Rotterdam")


class AnAddressIsLookedUpButOnlyKeptIfTheSiteNamesTheCompany(unittest.TestCase):
    SEARCH = ("https://html.duckduckgo.com/html/?q=Boreas+Logistics+B.V."
              "+Nederland+offici%C3%ABle+website")

    def results(self, *links):
        return "".join(f'<a class="result__a" href="{link}">x</a>' for link in links)

    def test_a_confirmed_site_is_accepted(self):
        net = FakeNet({
            self.SEARCH: self.results("https://www.linkedin.com/company/boreas",
                                      "https://boreaslogistics.nl/"),
            "https://boreaslogistics.nl/": "<html>Boreas Logistics verzorgt transport.</html>",
        })
        self.assertEqual(find_domain(net, "Boreas Logistics"), "boreaslogistics.nl")

    def test_a_site_that_does_not_name_the_company_is_refused(self):
        net = FakeNet({
            self.SEARCH: self.results("https://someoneelse.nl/"),
            "https://someoneelse.nl/": "<html>Wij verkopen fietsen.</html>",
        })
        self.assertIsNone(find_domain(net, "Boreas Logistics"))


class TheBrowserCanNameTheRecruitmentSystem(unittest.TestCase):
    """A page that loads its system in the browser hid it from the plain read."""

    def test_a_system_found_while_rendering_is_reported(self):
        pages = {"https://acme.nl/": '<div data-src="https://acme.recruitee.com/"></div>'}

        class Renderer:
            exhausted = False
            last_error = None

            def render(self, url):
                return pages[url]

        with mock.patch.object(run, "second_opinion_pages", lambda *a, **k: ["https://acme.nl/"]):
            attempt = run.rendered_second_opinion(Renderer(), {"id": "acme"}, {})
        self.assertEqual(attempt["source"], {"type": "recruitee", "subdomain": "acme"})


class TheBrowserOnlyOpensAddressesThatAnswered(unittest.TestCase):
    """Rendering guessed 404s spent the budget before most companies were tried."""

    def test_only_reached_pages_are_offered(self):
        cache = {"acme": {"reached_pages": ["https://acme.nl/", "https://acme.nl/over-ons"],
                          "checked_pages": ["https://acme.nl/", "https://acme.nl/careers",
                                            "https://acme.nl/over-ons"]}}
        order = run.rendering_order({"id": "acme", "domain": "acme.nl"}, cache)
        self.assertEqual(order, ["https://acme.nl/", "https://acme.nl/over-ons"])


class EveryCompanyGetsItsTurn(unittest.TestCase):
    """A run that ends early always skipped the same tail of the list."""

    def test_the_longest_waiting_company_goes_first(self):
        companies = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
        previous = {"a": {"checked_at": "2026-10-26T20:00+00:00"},
                    "b": {"checked_at": "2026-10-20T09:00+00:00"}}
        order = [c["id"] for c in run.least_recently_checked_first(companies, previous)]
        # "c" has never been checked at all, so it waited longest of the three.
        self.assertEqual(order, ["c", "b", "a"])
