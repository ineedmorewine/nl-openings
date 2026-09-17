"""amazon.jobs search endpoint, filtered to the Netherlands."""
from ..text import normalize_date, strip_html

API = "https://www.amazon.jobs/en/search.json"
PAGE_SIZE = 100
MAX_POSTINGS = 2000


def fetch(net, params, strict):
    jobs, offset = [], 0
    while True:
        data = net.get(API, params={"normalized_country_code[]": "NLD", "result_limit": PAGE_SIZE,
                                    "offset": offset, "sort": "recent"}).json()
        batch = data.get("jobs") or []
        for posting in batch:
            path = posting.get("job_path") or ""
            jobs.append({
                "ext_id": str(posting.get("id_icims") or path),
                "title": posting.get("title") or "",
                "location": posting.get("city") or posting.get("normalized_location") or "",
                "url": "https://www.amazon.jobs" + path if path.startswith("/") else path,
                "posted_at": normalize_date(posting.get("posted_date")),
                "description": strip_html(" ".join(posting.get(k) or "" for k in
                                                   ("description", "basic_qualifications",
                                                    "preferred_qualifications"))),
            })
        offset += PAGE_SIZE
        if not batch or offset >= min(data.get("hits") or 0, MAX_POSTINGS):
            break
    return jobs
