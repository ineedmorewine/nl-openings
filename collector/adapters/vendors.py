"""Adapters for the three recruitment vendors that our first version could not read.

phenom  - Phenom People career sites (Kuehne+Nagel, DHL, Lineage and others).
          The search page carries all its data in a `phApp.ddo = {...}` script block.
oracle  - Oracle Recruiting Cloud (DP World, Penske, Americold and others).
          Public REST endpoint, no sign-in.
icims   - iCIMS hosted portals. The listing pages are plain HTML.
"""
import json
import re
from urllib.parse import urljoin, urlparse

from ..text import keep_location, normalize_date, strip_html

# ------------------------------------------------------------------ phenom

PHENOM_PAGE_SIZE = 10
PHENOM_MAX_PAGES = 40
_DDO = re.compile(r"phApp\.ddo\s*=\s*(\{.*?\})\s*;\s*(?:phApp\.|</script>)", re.S)


def _json_prefix(text):
    """Read one JSON object from the start of `text`, ignoring whatever follows."""
    decoder = json.JSONDecoder()
    try:
        value, _ = decoder.raw_decode(text)
        return value
    except ValueError:
        return None


def extract_ddo(markup):
    """Return the page data object a Phenom career site embeds in its HTML."""
    start = markup.find("phApp.ddo")
    if start == -1:
        return None
    brace = markup.find("{", start)
    if brace == -1:
        return None
    return _json_prefix(markup[brace:])


def phenom_jobs_from_ddo(ddo):
    """Phenom moves the job list between two keys depending on site version."""
    for key in ("eagerLoadRefineSearch", "refineSearch"):
        section = (ddo or {}).get(key) or {}
        data = section.get("data") or {}
        jobs = data.get("jobs")
        if jobs:
            return jobs, section.get("totalHits") or data.get("totalHits") or 0
    return [], 0


def _phenom_location(job):
    parts = [job.get("cityStateCountry"), job.get("cityState"), job.get("location"),
             job.get("city"), job.get("state"), job.get("country")]
    seen, out = set(), []
    for part in parts:
        part = (part or "").strip()
        if part and part.lower() not in seen:
            seen.add(part.lower())
            out.append(part)
    return ", ".join(out[:3])


def _phenom_search_urls(params):
    if params.get("search_url"):
        return [params["search_url"]]
    base = params["base_url"].rstrip("/")
    return [base + path for path in ("/search-results", "/global/en/search-results",
                                     "/en/search-results", "/jobs")]


def phenom_fetch(net, params, strict):
    query = {"from": 0, "s": "1", "location": "Netherlands", "country": "Netherlands"}
    search_url, first_page = None, None
    for candidate in _phenom_search_urls(params):
        try:
            markup = net.get(candidate, params=query).text
        except Exception:
            continue
        jobs, total = phenom_jobs_from_ddo(extract_ddo(markup))
        if jobs:
            search_url, first_page = candidate, (jobs, total)
            break
    if not search_url:
        raise RuntimeError("no Phenom job data found on the search page")

    jobs, total = first_page
    collected, offset = list(jobs), PHENOM_PAGE_SIZE
    while offset < min(total or 0, PHENOM_PAGE_SIZE * PHENOM_MAX_PAGES):
        page_query = dict(query, **{"from": offset})
        try:
            markup = net.get(search_url, params=page_query).text
        except Exception:
            break
        page_jobs, _ = phenom_jobs_from_ddo(extract_ddo(markup))
        if not page_jobs:
            break
        collected.extend(page_jobs)
        offset += PHENOM_PAGE_SIZE

    results, seen = [], set()
    for job in collected:
        location = _phenom_location(job)
        if not keep_location(location, strict=True):
            continue
        url = job.get("applyUrl") or job.get("jobSeoUrl") or job.get("url") or ""
        if url and not url.startswith("http"):
            url = urljoin(search_url, url)
        ext_id = str(job.get("jobId") or job.get("id") or url)
        if ext_id in seen:
            continue
        seen.add(ext_id)
        results.append({
            "ext_id": ext_id,
            "title": (job.get("title") or "").strip(),
            "location": location,
            "url": url,
            "posted_at": normalize_date(job.get("postedDate") or job.get("dateCreated")),
            "description": strip_html(job.get("descriptionTeaser") or job.get("description") or ""),
        })
    return results


# ------------------------------------------------------------------ oracle

ORACLE_PAGE_SIZE = 200
ORACLE_MAX_PAGES = 10


def _oracle_api(host, site, offset):
    # The finder argument uses ';' and ',' literally, so the URL is built by hand.
    return (f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
            f"?onlyData=true&expand=requisitionList.secondaryLocations"
            f"&finder=findReqs;siteNumber={site},limit={ORACLE_PAGE_SIZE},"
            f"offset={offset},sortBy=POSTING_DATES_DESC")


def _oracle_requisitions(payload):
    items = (payload or {}).get("items") or []
    if not items:
        return [], 0
    first = items[0] or {}
    return first.get("requisitionList") or [], first.get("TotalJobsCount") or 0


def oracle_fetch(net, params, strict):
    host, site = params["host"], params.get("site", "CX_1")
    results, offset, total = [], 0, None
    while True:
        payload = net.get_json(_oracle_api(host, site, offset))
        requisitions, count = _oracle_requisitions(payload)
        if total is None:
            total = count
        for req in requisitions:
            places = [req.get("PrimaryLocation") or ""]
            places += [(p or {}).get("Name") or "" for p in req.get("secondaryLocations") or []]
            places = [p for p in places if p]
            if not any(keep_location(p, strict=True) for p in places):
                continue
            job_id = str(req.get("Id") or req.get("RequisitionId") or "")
            results.append({
                "ext_id": job_id,
                "title": (req.get("Title") or "").strip(),
                "location": places[0],
                "url": f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{job_id}",
                "posted_at": normalize_date(req.get("PostedDate")),
            })
        offset += ORACLE_PAGE_SIZE
        if not requisitions or offset >= min(total or 0, ORACLE_PAGE_SIZE * ORACLE_MAX_PAGES):
            break
    return results


# ------------------------------------------------------------------ icims

ICIMS_MAX_PAGES = 20
_ICIMS_ROW = re.compile(
    r'<a[^>]+class="[^"]*iCIMS_Anchor[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.S | re.I)
_ICIMS_LOCATION = re.compile(r'"(?:Job Locations|Locatie)"[^<]*</dt>\s*<dd[^>]*>(.*?)</dd>', re.S | re.I)
# A real posting link is /jobs/<number>/<slug>/job; anything else is navigation.
_ICIMS_JOB_URL = re.compile(r"/jobs/\d+/", re.I)


def icims_fetch(net, params, strict):
    host = params["host"]
    results, seen = [], set()
    for page in range(1, ICIMS_MAX_PAGES + 1):
        markup = net.get(f"https://{host}/jobs/search",
                         params={"ss": 1, "searchLocation": "Netherlands", "pr": page}).text
        rows = _ICIMS_ROW.findall(markup)
        new = 0
        for href, label in rows:
            url = urljoin(f"https://{host}/", href)
            if not _ICIMS_JOB_URL.search(url) or url in seen:
                continue
            title = strip_html(label)
            if not title:
                continue
            seen.add(url)
            new += 1
            results.append({
                "ext_id": url,
                "title": title,
                "location": "",
                "url": url,
                "posted_at": None,
            })
        if not new:
            break
    return results


def icims_describe(net, params, job):
    markup = net.get(job["url"]).text
    from .jsonld import extract_job_postings, posting_location, to_job
    postings = extract_job_postings(markup)
    if postings:
        found = to_job(postings[0], job["url"])
        return {"description": found["description"], "location": found["location"],
                "posted_at": found["posted_at"]}
    place = _ICIMS_LOCATION.search(markup)
    return {"description": strip_html(markup)[:4000],
            "location": strip_html(place.group(1)) if place else ""}
