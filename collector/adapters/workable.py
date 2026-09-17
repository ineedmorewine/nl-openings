"""Workable accounts via the public widget API."""
from ..text import keep_location, normalize_date, strip_html


def fetch(net, params, strict):
    url = f"https://apply.workable.com/api/v1/widget/accounts/{params['account']}"
    data = net.get(url, params={"details": "true"}).json()
    jobs = []
    for posting in data.get("jobs") or []:
        place = ", ".join(x for x in [posting.get("city"), posting.get("country")] if x)
        if not keep_location(place, strict):
            continue
        jobs.append({
            "ext_id": posting.get("shortcode") or posting.get("url"),
            "title": posting.get("title") or "",
            "location": posting.get("city") or posting.get("country") or "",
            "url": posting.get("url") or posting.get("shortlink") or "",
            "posted_at": normalize_date(posting.get("published_on") or posting.get("created_at")),
            "description": strip_html(posting.get("description") or ""),
        })
    return jobs
