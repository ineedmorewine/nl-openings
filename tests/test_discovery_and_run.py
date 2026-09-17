import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from collector import discovery, run
from tests.fakes import FakeNet


class Detection(unittest.TestCase):
    def test_strong_signatures(self):
        cases = {
            '<a href="https://dsv.wd3.myworkdayjobs.com/en-US/DSV_Careers">Jobs</a>':
                {"type": "workday", "host": "dsv.wd3.myworkdayjobs.com", "tenant": "dsv", "site": "DSV_Careers"},
            '<iframe src="https://job-boards.eu.greenhouse.io/embed/job_board?for=acme"></iframe>':
                {"type": "greenhouse", "token": "acme", "region": "eu"},
            '<a href="https://jobs.lever.co/acme">Open roles</a>': {"type": "lever", "site": "acme", "region": "us"},
            '<a href="https://careers.smartrecruiters.com/AcmeGroup">Jobs</a>': {"type": "smartrecruiters", "company": "AcmeGroup"},
            '<script src="https://acme.recruitee.com/embed.js"></script>': {"type": "recruitee", "subdomain": "acme"},
            '<a href="https://acme.jobs.personio.com/">Jobs</a>': {"type": "personio", "base_url": "https://acme.jobs.personio.com"},
            '<a href="https://apply.workable.com/acme/">Jobs</a>': {"type": "workable", "account": "acme"},
        }
        for markup, expected in cases.items():
            with self.subTest(expected["type"]):
                self.assertEqual(discovery.detect_source(markup, "https://acme.com/careers")[0], expected)

    def test_rmk_and_custom_domain_teamtailor_use_page_origin(self):
        self.assertEqual(discovery.detect_source('<link href="https://rmkcdn.successfactors.com/x.css">',
                                                 "https://jobs.acme.com/search/")[0],
                         {"type": "rmk", "base_url": "https://jobs.acme.com"})
        self.assertEqual(discovery.detect_source('<meta name="generator" content="Teamtailor">',
                                                 "https://werkenbij.acme.nl/jobs")[0],
                         {"type": "teamtailor", "base_url": "https://werkenbij.acme.nl"})

    def test_weak_vendor_is_reported(self):
        self.assertEqual(discovery.detect_source('<script src="https://cdn.phenompeople.com/a.js">', "https://x.com"),
                         (None, "Phenom"))

    def test_asset_paths_are_not_mistaken_for_accounts(self):
        self.assertEqual(discovery.detect_source('<script src="https://apply.workable.com/api/v1/x">', "https://x.com"),
                         (None, None))

    def test_discover_walks_from_homepage_to_careers_page(self):
        net = FakeNet(routes={
            "https://acme.nl/": '<a href="/over-ons">Over ons</a><a href="/werken-bij">Werken bij</a>'
                                '<a href="https://www.linkedin.com/company/acme/jobs">LinkedIn</a>',
            "https://acme.nl/werken-bij": '<a href="https://acme.recruitee.com/">Bekijk vacatures</a>'})
        result = discovery.discover(net, {"id": "acme", "domain": "acme.nl"})
        self.assertEqual(result["source"], {"type": "recruitee", "subdomain": "acme"})
        self.assertFalse(any("linkedin" in str(c) for c in net.calls))

    def test_unreachable_site(self):
        result = discovery.discover(FakeNet(), {"id": "gone", "domain": "gone.example"})
        self.assertEqual(result["reason"], "site_unreachable")


class RunMerge(unittest.TestCase):
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
        companies = {"companies": [
            {"id": "acme", "name": "Acme", "group": "road_rail", "domain": "acme.nl",
             "source": {"type": "recruitee", "subdomain": "acme"}},
            {"id": "nowhere", "name": "Nowhere", "group": "check", "domain": None}]}
        self.paths["COMPANIES_FILE"].write_text(json.dumps(companies))

    def tearDown(self):
        self.tmp.cleanup()

    def run_with(self, offers, today):
        routes = {"https://acme.recruitee.com/api/offers/": {"offers": offers}} if offers is not None else {}
        fake_date = mock.Mock(wraps=run.date)
        fake_date.today.return_value = today
        with mock.patch.multiple(run, **self.paths), mock.patch.object(run, "Net", lambda: FakeNet(routes)), \
                mock.patch.object(run, "date", fake_date):
            run.main([])
        jobs = json.loads(self.paths["JOBS_FILE"].read_text())["jobs"]
        sources = {s["id"]: s for s in json.loads(self.paths["SOURCES_FILE"].read_text())["sources"]}
        return {j["id"]: j for j in jobs}, sources

    @staticmethod
    def offer(i, title="Transport Planner"):
        return {"id": i, "title": title, "city": "Venlo", "country_code": "NL",
                "careers_url": f"https://acme.recruitee.com/o/{i}"}

    def test_baseline_first_seen_and_bad_day_retention(self):
        from datetime import date
        jobs, sources = self.run_with([self.offer(1)], date(2026, 9, 14))
        self.assertTrue(jobs["acme:1"]["baseline"])
        self.assertEqual(sources["nowhere"]["status"], "no_address")
        self.assertEqual(sources["acme"]["last_success"], "2026-09-14")

        jobs, _ = self.run_with([self.offer(1), self.offer(2)], date(2026, 9, 15))
        self.assertEqual(jobs["acme:1"]["first_seen"], "2026-09-14")
        self.assertTrue(jobs["acme:1"]["baseline"])
        self.assertFalse(jobs["acme:2"]["baseline"])
        self.assertEqual(jobs["acme:2"]["first_seen"], "2026-09-15")

        jobs, sources = self.run_with(None, date(2026, 9, 16))
        self.assertEqual(sorted(jobs), ["acme:1", "acme:2"])
        self.assertEqual(sources["acme"]["status"], "read_failed")
        self.assertTrue(sources["acme"]["showing_old_data"])
        self.assertEqual(sources["acme"]["last_success"], "2026-09-15")

        jobs, _ = self.run_with([self.offer(2)], date(2026, 9, 17))
        self.assertEqual(sorted(jobs), ["acme:2"])


if __name__ == "__main__":
    unittest.main()
