"""SAP SuccessFactors career sites (Recruiting Marketing layout with jobTitle-link rows)."""
from html.parser import HTMLParser
from urllib.parse import urljoin

from ..text import keep_location, normalize_date, strip_html
from .jsonld import extract_job_postings

ROWS_PER_PAGE = 25
MAX_ROWS = 600


class RowParser(HTMLParser):
    """Collect (href, title, location, date) from search result rows."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows = []
        self._row = None
        self._capture = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        classes = (attributes.get("class") or "").split()
        if tag == "tr" and "data-row" in classes:
            self._row = {"href": "", "title": "", "location": "", "date": ""}
            self.rows.append(self._row)
        if tag == "a" and "jobTitle-link" in classes:
            if self._row is None:
                self._row = {"href": "", "title": "", "location": "", "date": ""}
                self.rows.append(self._row)
            if not self._row["href"]:
                self._row["href"] = attributes.get("href") or ""
                self._capture = "title"
        elif tag == "span" and self._row is not None:
            if "jobLocation" in classes and not self._row["location"]:
                self._capture = "location"
            elif "jobDate" in classes and not self._row["date"]:
                self._capture = "date"

    def handle_endtag(self, tag):
        if tag in {"a", "span"}:
            self._capture = None
        if tag == "tr":
            self._row = None

    def handle_data(self, data):
        if self._capture and self._row is not None:
            self._row[self._capture] += data


def parse_rows(markup):
    parser = RowParser()
    parser.feed(markup)
    rows = []
    for row in parser.rows:
        row = {key: " ".join(value.split()) for key, value in row.items()}
        if row["href"]:
            rows.append(row)
    return rows


def fetch(net, params, strict):
    base = params["base_url"].rstrip("/")
    seen, jobs, start = set(), [], 0
    while start < MAX_ROWS:
        markup = net.get(base + "/search/", params={"q": "", "locationsearch": "Netherlands",
                                                     "startrow": start}).text
        new_rows = [row for row in parse_rows(markup) if row["href"] not in seen]
        if not new_rows:
            break
        for row in new_rows:
            seen.add(row["href"])
            if not keep_location(row["location"], strict=True):
                continue
            jobs.append({
                "ext_id": row["href"],
                "title": row["title"],
                "location": row["location"],
                "url": urljoin(base + "/", row["href"]),
                "posted_at": normalize_date(row["date"]),
            })
        start += ROWS_PER_PAGE
    return jobs


def describe(net, params, job):
    markup = net.get(job["url"]).text
    postings = extract_job_postings(markup)
    if postings:
        return {"description": strip_html(postings[0].get("description") or "")}
    return {"description": strip_html(markup)}
