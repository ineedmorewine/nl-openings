"""Generic reader for career pages that embed schema.org JobPosting data (used by Google Jobs)."""
import json
import re
from urllib.parse import urljoin, urlparse

from ..text import keep_location, normalize_date, strip_html

MAX_FOLLOWED_LINKS = 40
_SCRIPT = re.compile(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.S | re.I)
_HREF = re.compile(r'href=["\']([^"\'#]+)["\']', re.I)
_JOB_LINK = re.compile(r"/(job|jobs|vacature|vacatures|vacancy|vacancies|career|careers|werken-bij)/[^/?]+", re.I)


def _walk(node):
    if isinstance(node, list):
        for item in node:
            yield from _walk(item)
    elif isinstance(node, dict):
        types = node.get("@type")
        types = types if isinstance(types, list) else [types]
        if "JobPosting" in types:
            yield node
        for key in ("@graph", "itemListElement", "item", "mainEntity"):
            if key in node:
                yield from _walk(node[key])


def extract_job_postings(markup):
    postings = []
    for raw in _SCRIPT.findall(markup or ""):
        try:
            postings.extend(_walk(json.loads(raw.strip())))
        except (json.JSONDecodeError, ValueError):
            continue
    return postings


def _country(address):
    country = address.get("addressCountry")
    if isinstance(country, dict):
        country = country.get("name") or country.get("identifier")
    return country or ""


def posting_location(posting):
    places = posting.get("jobLocation") or []
    places = places if isinstance(places, list) else [places]
    texts = []
    for place in places:
        address = (place or {}).get("address") or {}
        if isinstance(address, str):
            texts.append(address)
            continue
        parts = [address.get("addressLocality"), address.get("addressRegion"), _country(address)]
        texts.append(", ".join(p for p in parts if p))
    return "; ".join(t for t in texts if t)


def to_job(posting, page_url):
    url = posting.get("url") or page_url
    identifier = posting.get("identifier")
    if isinstance(identifier, dict):
        identifier = identifier.get("value")
    return {
        "ext_id": str(identifier or url),
        "title": strip_html(posting.get("title") or ""),
        "location": posting_location(posting),
        "url": url,
        "posted_at": normalize_date(posting.get("datePosted")),
        "description": strip_html(posting.get("description") or ""),
    }


def _nl_filter(jobs, strict):
    return [job for job in jobs if keep_location(job["location"], strict)
            or any(keep_location(part, True) for part in job["location"].split(";"))]


def fetch(net, params, strict):
    page_url = params["url"]
    markup = net.get(page_url).text
    postings = extract_job_postings(markup)
    if postings:
        return _nl_filter([to_job(p, page_url) for p in postings], strict)

    host = urlparse(page_url).netloc
    links = []
    for href in _HREF.findall(markup):
        absolute = urljoin(page_url, href)
        if urlparse(absolute).netloc == host and _JOB_LINK.search(absolute) and absolute not in links:
            if absolute.rstrip("/") != page_url.rstrip("/"):
                links.append(absolute)
    jobs = []
    for link in links[:MAX_FOLLOWED_LINKS]:
        try:
            found = extract_job_postings(net.get(link).text)
        except Exception:
            continue
        jobs.extend(to_job(p, link) for p in found[:1])
    return _nl_filter(jobs, strict)
