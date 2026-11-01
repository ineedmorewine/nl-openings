"""BambooHR careers sites.

A BambooHR account publishes its openings as JSON at /careers/list, which
carries the title, the city and the country for every posting, so no page has
to be opened to find out where a job is.
"""
from ..text import keep_location

LIST_PATH = "/careers/list"


def _place(job):
    where = job.get("location") or {}
    parts = [where.get("city"), where.get("state"), where.get("country")]
    return ", ".join(part for part in parts if part)


def fetch(net, params, strict):
    account = params["account"]
    base = f"https://{account}.bamboohr.com"
    data = net.get_json(base + LIST_PATH)
    jobs = []
    for job in (data or {}).get("result") or []:
        place = _place(job)
        if not keep_location(place, strict):
            continue
        jobs.append({
            "ext_id": str(job.get("id")),
            "title": (job.get("jobOpeningName") or "").strip(),
            "location": place,
            "url": f"{base}/careers/{job.get('id')}",
            "posted_at": None,
        })
    return [job for job in jobs if job["title"]]


def describe(net, params, job):
    """Open one posting for its text; the listing carries no description."""
    from ..text import strip_html
    return {"description": strip_html(net.get(job["url"]).text)[:6000]}
