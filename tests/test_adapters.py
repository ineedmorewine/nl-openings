import unittest

from collector.adapters import (amazon, greenhouse, jsonld, lever, personio, recruitee, rmk,
                                smartrecruiters, teamtailor, workable, workday)
from tests.fakes import FakeNet

LONG_TEXT = "Plan rail and road shipments for our customers across Europe. " * 5


class WorkdayAdapter(unittest.TestCase):
    params = {"host": "acme.wd3.myworkdayjobs.com", "tenant": "acme", "site": "Careers"}
    base = "https://acme.wd3.myworkdayjobs.com/wday/cxs/acme/Careers"

    def facets(self):
        return [{"facetParameter": "locationMainGroup", "values": [
            {"facetParameter": "locationCountry", "descriptor": "Country", "values": [
                {"descriptor": "Germany", "id": "de1", "count": 40},
                {"descriptor": "Netherlands", "id": "nl1", "count": 2}]}]}]

    def test_nested_facet_is_found(self):
        self.assertEqual(workday.find_netherlands_facet(self.facets()), ("locationCountry", "nl1"))

    def test_fetch_applies_country_facet_and_describe_reads_details(self):
        page = {"total": 2, "jobPostings": [
            {"title": "Intermodal Planner", "externalPath": "/job/Venlo/Intermodal-Planner_R1",
             "locationsText": "Venlo", "postedOn": "Posted Today"},
            {"title": "Transport Coordinator", "externalPath": "/job/Tilburg/TC_R2",
             "locationsText": "2 Locations", "postedOn": "Posted 30+ Days Ago"}]}
        net = FakeNet(
            routes={self.base + "/job/Venlo/Intermodal-Planner_R1": {"jobPostingInfo": {
                "jobDescription": "<p>" + LONG_TEXT + "</p>", "startDate": "2026-09-14",
                "externalUrl": "https://acme.wd3.myworkdayjobs.com/en-US/Careers/job/R1"}}},
            posts=[
                (self.base + "/jobs", lambda p: p["limit"] == 1, {"total": 42, "facets": self.facets()}),
                (self.base + "/jobs", lambda p: p["appliedFacets"] == {"locationCountry": ["nl1"]}, page),
            ])
        jobs = workday.fetch(net, self.params, strict=True)
        self.assertEqual([j["title"] for j in jobs], ["Intermodal Planner", "Transport Coordinator"])
        self.assertEqual(jobs[0]["url"], "https://acme.wd3.myworkdayjobs.com/Careers/job/Venlo/Intermodal-Planner_R1")
        details = workday.describe(net, self.params, jobs[0])
        self.assertIn("rail and road", details["description"])
        self.assertEqual(details["posted_at"], "2026-09-14")

    def test_without_facet_only_clear_dutch_locations_are_kept(self):
        page = {"total": 2, "jobPostings": [
            {"title": "Planner", "externalPath": "/a", "locationsText": "Rotterdam"},
            {"title": "Planner", "externalPath": "/b", "locationsText": "Hamburg"}]}
        net = FakeNet(posts=[
            (self.base + "/jobs", lambda p: p["limit"] == 1, {"total": 2, "facets": []}),
            (self.base + "/jobs", lambda p: p["limit"] == 20, page)])
        self.assertEqual([j["ext_id"] for j in workday.fetch(net, self.params, True)], ["/a"])


class JsonApiAdapters(unittest.TestCase):
    def test_smartrecruiters(self):
        api = "https://api.smartrecruiters.com/v1/companies/acme/postings"
        net = FakeNet(routes={
            FakeNet.key(api, {"limit": 100, "offset": 0, "country": "nl"}): {"totalFound": 1, "content": [
                {"id": "7", "name": "Road Planner", "releasedDate": "2026-09-10T09:00:00.000Z",
                 "location": {"city": "Breda", "country": "nl"}}]},
            api + "/7": {"jobAd": {"sections": {"jobDescription": {"text": "<p>" + LONG_TEXT + "</p>"}}},
                         "postingUrl": "https://jobs.smartrecruiters.com/acme/7-road-planner"}})
        jobs = smartrecruiters.fetch(net, {"company": "acme"}, True)
        self.assertEqual(jobs[0]["posted_at"], "2026-09-10")
        self.assertIn("customers", smartrecruiters.describe(net, {"company": "acme"}, jobs[0])["description"])

    def test_greenhouse_filters_location_and_unescapes_content(self):
        url = "https://boards-api.greenhouse.io/v1/boards/acme/jobs"
        net = FakeNet(routes={FakeNet.key(url, {"content": "true"}): {"jobs": [
            {"id": 1, "title": "Freight Ops", "location": {"name": "Amsterdam"}, "absolute_url": "https://x/1",
             "first_published": "2026-09-01T00:00:00Z", "content": "&lt;p&gt;Hello&lt;/p&gt;"},
            {"id": 2, "title": "Freight Ops", "location": {"name": "Chicago, IL"}, "absolute_url": "https://x/2"}]}})
        jobs = greenhouse.fetch(net, {"token": "acme"}, True)
        self.assertEqual([j["ext_id"] for j in jobs], ["1"])
        self.assertEqual(jobs[0]["description"], "Hello")

    def test_lever_eu_region(self):
        url = "https://api.eu.lever.co/v0/postings/acme"
        net = FakeNet(routes={FakeNet.key(url, {"mode": "json"}): [
            {"id": "a", "text": "Rail Ops", "hostedUrl": "https://jobs.eu.lever.co/acme/a", "createdAt": 1789200000000,
             "categories": {"location": "Utrecht"}, "descriptionPlain": "Text"},
            {"id": "b", "text": "Rail Ops", "hostedUrl": "https://jobs.eu.lever.co/acme/b",
             "categories": {"location": "Berlin"}}]})
        jobs = lever.fetch(net, {"site": "acme", "region": "eu"}, True)
        self.assertEqual([j["ext_id"] for j in jobs], ["a"])

    def test_recruitee_uses_country_code(self):
        net = FakeNet(routes={"https://acme.recruitee.com/api/offers/": {"offers": [
            {"id": 3, "title": "Planner", "city": "Hub", "country_code": "NL",
             "careers_url": "https://acme.recruitee.com/o/planner", "published_at": "2026-09-11 10:00:00 UTC"},
            {"id": 4, "title": "Planner", "city": "Gent", "country_code": "BE",
             "careers_url": "https://acme.recruitee.com/o/planner-2"}]}})
        jobs = recruitee.fetch(net, {"subdomain": "acme"}, True)
        self.assertEqual([j["ext_id"] for j in jobs], ["3"])

    def test_workable(self):
        url = "https://apply.workable.com/api/v1/widget/accounts/acme"
        net = FakeNet(routes={FakeNet.key(url, {"details": "true"}): {"jobs": [
            {"title": "Carrier Manager", "shortcode": "AB1", "city": "Rotterdam", "country": "Netherlands",
             "url": "https://apply.workable.com/j/AB1", "published_on": "2026-09-09"}]}})
        self.assertEqual(workable.fetch(net, {"account": "acme"}, True)[0]["posted_at"], "2026-09-09")

    def test_amazon_paginates_until_hits(self):
        api = "https://www.amazon.jobs/en/search.json"
        first = {"normalized_country_code[]": "NLD", "result_limit": 100, "offset": 0, "sort": "recent"}
        net = FakeNet(routes={FakeNet.key(api, first): {"hits": 1, "jobs": [
            {"id_icims": 99, "title": "Transportation Specialist", "job_path": "/en/jobs/99",
             "city": "Amsterdam", "posted_date": "September 12, 2026", "description": "Plan"}]}})
        jobs = amazon.fetch(net, {}, True)
        self.assertEqual(jobs[0]["url"], "https://www.amazon.jobs/en/jobs/99")
        self.assertEqual(jobs[0]["posted_at"], "2026-09-12")


class FeedAdapters(unittest.TestCase):
    def test_teamtailor_rss(self):
        rss = """<?xml version="1.0"?><rss xmlns:tt="https://teamtailor.com/locations"><channel>
          <item><title>Transport Planner</title><link>https://careers.acme.nl/jobs/1</link>
          <guid>1</guid><pubDate>Sat, 12 Sep 2026 08:00:00 +0000</pubDate><description>&lt;p&gt;Hi&lt;/p&gt;</description>
          <tt:locations><tt:location><tt:city>Venlo</tt:city><tt:country>Netherlands</tt:country></tt:location></tt:locations></item>
          <item><title>Planner</title><link>https://careers.acme.nl/jobs/2</link><guid>2</guid>
          <tt:locations><tt:location><tt:city>Hamburg</tt:city></tt:location></tt:locations></item>
        </channel></rss>"""
        net = FakeNet(routes={"https://careers.acme.nl/jobs.rss": rss})
        jobs = teamtailor.fetch(net, {"base_url": "https://careers.acme.nl"}, True)
        self.assertEqual([j["ext_id"] for j in jobs], ["1"])
        self.assertEqual(jobs[0]["location"], "Venlo, Netherlands")

    def test_personio_xml(self):
        xml = """<workzag-jobs><position><id>55</id><office>Amsterdam</office><name>Ops Coordinator</name>
          <createdAt>2026-09-08T10:00:00+00:00</createdAt>
          <jobDescriptions><jobDescription><name>Tasks</name><value>&lt;p&gt;Coordinate&lt;/p&gt;</value></jobDescription></jobDescriptions>
          </position></workzag-jobs>"""
        base = "https://acme.jobs.personio.de"
        net = FakeNet(routes={FakeNet.key(base + "/xml", {"language": "en"}): xml})
        jobs = personio.fetch(net, {"base_url": base}, True)
        self.assertEqual(jobs[0]["url"], base + "/job/55")
        self.assertEqual(jobs[0]["description"], "Coordinate")


class HtmlAdapters(unittest.TestCase):
    def test_jsonld_graph_and_country_object(self):
        page = """<html><script type="application/ld+json">{"@graph":[{"@type":"JobPosting","title":"Rail Planner",
          "datePosted":"2026-09-13","url":"https://acme.com/jobs/1","identifier":{"value":"J1"},
          "jobLocation":{"address":{"addressLocality":"Venlo","addressCountry":{"name":"NL"}}}},
          {"@type":"JobPosting","title":"Rail Planner","url":"https://acme.com/jobs/2",
          "jobLocation":[{"address":{"addressLocality":"Lyon","addressCountry":"FR"}}]}]}</script></html>"""
        net = FakeNet(routes={"https://acme.com/careers": page})
        jobs = jsonld.fetch(net, {"url": "https://acme.com/careers"}, True)
        self.assertEqual([j["ext_id"] for j in jobs], ["J1"])

    def test_jsonld_follows_job_links_when_listing_has_no_data(self):
        listing = '<a href="/vacatures/planner-1">Planner</a><a href="https://other.com/jobs/x">x</a>'
        detail = ('<script type="application/ld+json">{"@type":"JobPosting","title":"Planner",'
                  '"jobLocation":{"address":{"addressLocality":"Tilburg"}}}</script>')
        net = FakeNet(routes={"https://acme.nl/vacatures": listing, "https://acme.nl/vacatures/planner-1": detail})
        jobs = jsonld.fetch(net, {"url": "https://acme.nl/vacatures"}, False)
        self.assertEqual(jobs[0]["url"], "https://acme.nl/vacatures/planner-1")

    def test_rmk_rows(self):
        row = """<table><tr class="data-row"><td><span class="jobTitle hidden-phone">
          <a href="/job/Venlo-Transport-Planner/123/" class="jobTitle-link">Transport Planner</a></span>
          <span class="jobTitle visible-phone"><a class="jobTitle-link" href="/job/Venlo-Transport-Planner/123/">Transport Planner</a></span></td>
          <td><span class="jobLocation">Venlo, NL</span></td><td><span class="jobDate">Sep 11, 2026</span></td></tr></table>"""
        base = "https://jobs.acme.com"
        net = FakeNet(routes={
            FakeNet.key(base + "/search/", {"q": "", "locationsearch": "Netherlands", "startrow": 0}): row,
            FakeNet.key(base + "/search/", {"q": "", "locationsearch": "Netherlands", "startrow": 25}): "<table></table>"})
        jobs = rmk.fetch(net, {"base_url": base}, True)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["url"], "https://jobs.acme.com/job/Venlo-Transport-Planner/123/")
        self.assertEqual(jobs[0]["posted_at"], "2026-09-11")


if __name__ == "__main__":
    unittest.main()
