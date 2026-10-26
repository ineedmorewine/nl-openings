"""Read vacancies from the addresses a site lists in its own sitemap.

Some careers pages build their list in the browser, so what we download is an
empty shell and no link to a vacancy is ever visible. The sitemap is the same
site describing itself to machines, and it names those vacancy pages outright.
Each address is opened and read as a single posting.
"""
from ..text import strip_html
from .careerpage import _guess_location, _title_from_url
from .jsonld import extract_job_postings, to_job

MAX_POSTINGS = 40


def fetch(net, params, strict):
    jobs = []
    for url in list(params.get("urls") or [])[:MAX_POSTINGS]:
        try:
            markup = net.get(url).text
        except Exception:
            continue
        postings = extract_job_postings(markup)
        if postings:
            found = to_job(postings[0], url)
            if found and found.get("title"):
                found["ext_id"] = url
                jobs.append(found)
                continue
        title = _page_title(markup) or _title_from_url(url)
        if not title:
            continue
        text = strip_html(markup)
        jobs.append({"ext_id": url, "title": title, "url": url,
                     "location": _guess_location(text), "posted_at": None,
                     "description": text[:6000]})
    if not jobs:
        raise RuntimeError("no vacancies read from the sitemap addresses")
    return jobs


def _page_title(markup):
    """The posting's own heading, preferred over the slug in its address."""
    import re
    for pattern in (r"<h1[^>]*>(.*?)</h1>", r"<title[^>]*>(.*?)</title>"):
        match = re.search(pattern, markup or "", re.S | re.I)
        if not match:
            continue
        title = " ".join(strip_html(match.group(1)).split())
        # A page title usually carries the company name after a separator.
        title = re.split(r"\s+[|–—]\s+", title)[0].strip()
        if 6 <= len(title) <= 120 and len(title.split()) >= 2:
            return title
    return ""
