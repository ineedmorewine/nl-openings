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


def _page_rows(net, base, query, start):
    markup = net.get(base + "/search/", params=dict(query, startrow=start)).text
    return parse_rows(markup)


def _collect(net, base, query):
    """Walk the result pages for one search, returning every row it lists."""
    seen, rows, start = set(), [], 0
    while start < MAX_ROWS:
        new = [r for r in _page_rows(net, base, query, start) if r["href"] not in seen]
        if not new:
            break
        for row in new:
            seen.add(row["href"])
            rows.append(row)
        start += ROWS_PER_PAGE
    return rows


def fetch(net, params, strict):
    base = params["base_url"].rstrip("/")
    # Searching by country works on global sites but returns nothing on sites whose
    # locations are plain city names, so fall back to the unfiltered list and sort
    # the Dutch rows out ourselves. Without this a company silently reports 0 jobs.
    rows = _collect(net, base, {"q": "", "locationsearch": "Netherlands"})
    dutch = [r for r in rows if keep_location(r["location"], strict=True)]
    if not dutch:
        rows = _collect(net, base, {"q": ""})
        dutch = [r for r in rows if keep_location(r["location"], strict=True)]

    return [{
        "ext_id": row["href"],
        "title": row["title"],
        "location": row["location"],
        "url": urljoin(base + "/", row["href"]),
        "posted_at": normalize_date(row["date"]),
    } for row in dutch]


def describe(net, params, job):
    markup = net.get(job["url"]).text
    postings = extract_job_postings(markup)
    if postings:
        return {"description": strip_html(postings[0].get("description") or "")}
    return {"description": strip_html(markup)}
