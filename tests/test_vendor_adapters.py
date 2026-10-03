import json
import unittest

from collector.adapters import ADAPTERS
from collector.adapters import vendors
from collector.discovery import detect_source
from tests.fakes import FakeNet

PHENOM_HOST = "https://jobs.example.com"
PHENOM_SEARCH = PHENOM_HOST + "/search-results"


def phenom_page(jobs, total):
    ddo = {"eagerLoadRefineSearch": {"totalHits": total, "data": {"jobs": jobs}}}
    return ("<html><head><script>phApp.ddo = " + json.dumps(ddo) +
            "; phApp.sessionParams = {};</script></head><body></body></html>")


def phenom_job(job_id, title, city, country="Netherlands", posted="2026-09-28"):
    return {"jobId": job_id, "title": title, "cityState": city, "country": country,
            "postedDate": posted, "jobSeoUrl": f"/job/{job_id}/{title.lower().replace(' ', '-')}",
            "descriptionTeaser": "Plan road and rail shipments for European customers."}


class PhenomAdapter(unittest.TestCase):
    def query(self, offset):
        return {"from": offset, "s": "1", "location": "Netherlands", "country": "Netherlands"}

    def test_reads_jobs_paginates_and_drops_foreign_locations(self):
        page_one = phenom_page([phenom_job("1", "Transport Planner", "Venlo"),
                                phenom_job("2", "Rail Planner", "Bonn", country="Germany")], 12)
        page_two = phenom_page([phenom_job("3", "Freight Forwarder", "Rotterdam")], 12)
        net = FakeNet(routes={
            FakeNet.key(PHENOM_SEARCH, self.query(0)): page_one,
            FakeNet.key(PHENOM_SEARCH, self.query(10)): page_two,
        })
        jobs = vendors.phenom_fetch(net, {"base_url": PHENOM_HOST}, strict=True)
        self.assertEqual([j["ext_id"] for j in jobs], ["1", "3"])
        self.assertEqual(jobs[0]["url"], PHENOM_HOST + "/job/1/transport-planner")
        self.assertEqual(jobs[0]["posted_at"], "2026-09-28")
        self.assertIn("rail shipments", jobs[0]["description"])

    def test_falls_back_to_a_locale_search_path(self):
        localised = PHENOM_HOST + "/global/en/search-results"
        net = FakeNet(routes={
            FakeNet.key(localised, self.query(0)): phenom_page(
                [phenom_job("9", "Intermodal Planner", "Tilburg")], 1)})
        jobs = vendors.phenom_fetch(net, {"base_url": PHENOM_HOST}, strict=True)
        self.assertEqual(jobs[0]["title"], "Intermodal Planner")

    def test_refine_search_key_is_also_understood(self):
        ddo = {"refineSearch": {"data": {"jobs": [phenom_job("4", "Dispatcher", "Breda")],
                                         "totalHits": 1}}}
        self.assertEqual(vendors.phenom_jobs_from_ddo(ddo)[1], 1)

    def test_missing_data_raises_rather_than_reporting_zero_jobs(self):
        net = FakeNet(routes={FakeNet.key(PHENOM_SEARCH, self.query(0)): "<html>no data</html>"})
        with self.assertRaises(RuntimeError):
            vendors.phenom_fetch(net, {"base_url": PHENOM_HOST}, strict=True)


ORACLE_HOST = "acme.fa.em2.oraclecloud.com"


def oracle_payload(requisitions, total):
    return {"items": [{"TotalJobsCount": total, "requisitionList": requisitions}]}


class PhenomWidgets(unittest.TestCase):
    """The /widgets endpoint is the primary path: it filters by country server-side."""

    def widgets(self, pages):
        """pages maps an offset to (jobs, totalHits)."""
        def respond(payload):
            return payload.get("from") in pages

        def body(payload):
            jobs, total = pages[payload["from"]]
            return {"refineSearch": {"totalHits": total, "data": {"jobs": jobs}}}
        return respond, body

    def test_widgets_results_are_used_and_paginated(self):
        pages = {0: ([phenom_job(str(i), "Transport Planner", "Venlo") for i in range(100)], 150),
                 100: ([phenom_job("x", "Rail Planner", "Tilburg")], 150)}
        calls = []

        class WidgetNet(FakeNet):
            def post_json(self, url, payload):
                calls.append((url, payload["from"], payload["selected_fields"]))
                jobs, total = pages[payload["from"]]
                return {"refineSearch": {"totalHits": total, "data": {"jobs": jobs}}}

        jobs = vendors.phenom_fetch(WidgetNet(), {"base_url": PHENOM_HOST}, strict=True)
        self.assertEqual(len(jobs), 101)
        self.assertEqual(calls[0][0], PHENOM_HOST + "/widgets")
        self.assertEqual(calls[0][2], {"country": ["Netherlands"]})
        self.assertEqual([c[1] for c in calls], [0, 100])

    def test_html_search_page_is_used_when_widgets_is_unavailable(self):
        query = {"from": 0, "s": "1", "location": "Netherlands", "country": "Netherlands"}
        net = FakeNet(routes={FakeNet.key(PHENOM_SEARCH, query): phenom_page(
            [phenom_job("5", "Freight Forwarder", "Breda")], 1)})
        jobs = vendors.phenom_fetch(net, {"base_url": PHENOM_HOST}, strict=True)
        self.assertEqual([j["ext_id"] for j in jobs], ["5"])

    def test_a_site_answering_with_no_dutch_jobs_is_not_an_error(self):
        class EmptyNet(FakeNet):
            def post_json(self, url, payload):
                return {"refineSearch": {"totalHits": 0, "data": {"jobs": []}}}

        net = EmptyNet(routes={FakeNet.key(PHENOM_SEARCH, {
            "from": 0, "s": "1", "location": "Netherlands", "country": "Netherlands"}):
            phenom_page([], 0)})
        self.assertEqual(vendors.phenom_fetch(net, {"base_url": PHENOM_HOST}, strict=True), [])

    def test_unreachable_site_raises_instead_of_reporting_zero(self):
        with self.assertRaises(RuntimeError):
            vendors.phenom_fetch(FakeNet(), {"base_url": PHENOM_HOST}, strict=True)


class OracleAdapter(unittest.TestCase):
    def test_reads_requisitions_and_builds_apply_links(self):
        url = vendors._oracle_api(ORACLE_HOST, "CX_1", 0)
        payload = oracle_payload([
            {"Id": "REQ_100", "Title": "Transport Coordinator", "PostedDate": "2026-09-25",
             "PrimaryLocation": "Rotterdam, Netherlands"},
            {"Id": "REQ_101", "Title": "Planner", "PrimaryLocation": "Dubai, UAE"},
            {"Id": "REQ_102", "Title": "Rail Planner", "PrimaryLocation": "Hamburg, Germany",
             "secondaryLocations": [{"Name": "Venlo, Netherlands"}]},
        ], 3)
        net = FakeNet(routes={url: payload})
        jobs = vendors.oracle_fetch(net, {"host": ORACLE_HOST, "site": "CX_1"}, strict=True)
        self.assertEqual([j["ext_id"] for j in jobs], ["REQ_100", "REQ_102"])
        self.assertEqual(
            jobs[0]["url"],
            f"https://{ORACLE_HOST}/hcmUI/CandidateExperience/en/sites/CX_1/job/REQ_100")
        self.assertEqual(jobs[0]["posted_at"], "2026-09-25")

    def test_finder_argument_keeps_its_punctuation(self):
        url = vendors._oracle_api(ORACLE_HOST, "CX_2", 200)
        self.assertIn("finder=findReqs;siteNumber=CX_2,limit=200,offset=200", url)


class IcimsAdapter(unittest.TestCase):
    host = "careers-acme.icims.com"

    def search(self, query, page):
        return FakeNet.key(f"https://{self.host}/jobs/search", dict(query, pr=page))

    def test_collects_job_links_from_the_portal(self):
        query = {"ss": 1, "searchCountry": "Netherlands"}
        row = ('<a class="iCIMS_Anchor" href="/jobs/4821/transport-planner/job">Transport Planner</a>'
               '<a class="iCIMS_Anchor" href="/jobs/search?pr=2">Next</a>')
        net = FakeNet(routes={self.search(query, 1): row, self.search(query, 2): "<html></html>"})
        jobs = vendors.icims_fetch(net, {"host": self.host}, strict=True)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["url"], f"https://{self.host}/jobs/4821/transport-planner/job")

    def test_falls_back_when_a_portal_refuses_the_first_search_url(self):
        """Some portals answer 405 to searchCountry; the next shape must be tried."""
        query = {"ss": 1, "searchLocation": "Netherlands"}
        row = '<a class="iCIMS_Anchor" href="/jobs/77/planner/job">Planner</a>'
        net = FakeNet(routes={self.search(query, 1): row, self.search(query, 2): "<html></html>"})
        jobs = vendors.icims_fetch(net, {"host": self.host}, strict=True)
        self.assertEqual([j["title"] for j in jobs], ["Planner"])

    def test_portal_refusing_every_url_raises(self):
        with self.assertRaises(RuntimeError):
            vendors.icims_fetch(FakeNet(), {"host": self.host}, strict=True)


class VendorDetection(unittest.TestCase):
    def test_oracle_tenant_and_site_are_read_from_the_link(self):
        markup = ('<a href="https://acme.fa.em2.oraclecloud.com/hcmUI/CandidateExperience/'
                  'en/sites/CX_1/requisitions">Vacancies</a>')
        self.assertEqual(detect_source(markup, "https://acme.com/careers")[0],
                         {"type": "oracle", "host": "acme.fa.em2.oraclecloud.com", "site": "CX_1"})

    def test_phenom_page_is_detected_by_its_data_block(self):
        source, _ = detect_source("<script>phApp.ddo = {};</script>",
                                  "https://jobs.acme.com/global/en/search-results?from=0")
        self.assertEqual(source, {"type": "phenom", "base_url": "https://jobs.acme.com",
                                  "search_url": "https://jobs.acme.com/global/en/search-results"})

    def test_phenom_reference_points_at_the_career_host(self):
        markup = '<script src="https://cdn.phenompeople.com/x.js"></script>' \
                 '<a href="https://jobs.acme.com/global/en/search-results">All jobs</a>'
        self.assertEqual(detect_source(markup, "https://acme.com/careers")[0],
                         {"type": "phenom", "base_url": "https://jobs.acme.com"})

    def test_successfactors_is_detected_without_the_cdn_asset(self):
        self.assertEqual(detect_source('<a href="/viewalljobs/?locale=en_US">View all</a>',
                                       "https://jobs.dsv.com/")[0],
                         {"type": "rmk", "base_url": "https://jobs.dsv.com"})

    def test_placeholder_and_shared_slugs_are_rejected(self):
        for markup in ('<iframe src="https://boards.greenhouse.io/embed/job_board?for=THIS_PART">',
                       '<script src="https://careers-analytics.recruitee.com/t.js">'):
            self.assertIsNone(detect_source(markup, "https://acme.com/careers")[0], markup)

    def test_a_passing_mention_of_teamtailor_is_not_a_careers_system(self):
        self.assertEqual(detect_source("<p>We moved away from Teamtailor last year.</p>",
                                       "https://acme.com/careers"), (None, None))


class Registry(unittest.TestCase):
    def test_every_new_vendor_is_registered(self):
        for name in ("phenom", "oracle", "icims"):
            self.assertTrue(hasattr(ADAPTERS[name], "fetch"), name)


if __name__ == "__main__":
    unittest.main()
