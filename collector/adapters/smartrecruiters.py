"""SmartRecruiters public postings API."""
from ..text import keep_location, normalize_date, strip_html

API = "https://api.smartrecruiters.com/v1/companies/{company}/postings"


def fetch(net, params, strict):
    company = params["company"]
    jobs, offset = [], 0
    while True:
        data = net.get(API.format(company=company),
                       params={"limit": 100, "offset": offset, "country": "nl"}).json()
        content = data.get("content") or []
        for posting in content:
            location = posting.get("location") or {}
            country = (location.get("country") or "").lower()
            text = ", ".join(x for x in [location.get("city"), location.get("country")] if x)
            if country != "nl" and not keep_location(text, strict):
                continue
            jobs.append({
                "ext_id": posting["id"],
                "title": posting.get("name") or "",
                "location": location.get("city") or "Netherlands",
                "url": f"https://jobs.smartrecruiters.com/{company}/{posting['id']}",
                "posted_at": normalize_date(posting.get("releasedDate")),
            })
        offset += len(content)
        if not content or offset >= (data.get("totalFound") or 0):
            break
    return jobs


def describe(net, params, job):
    data = net.get(API.format(company=params["company"]) + "/" + job["ext_id"]).json()
    sections = (data.get("jobAd") or {}).get("sections") or {}
    text = " ".join(strip_html((section or {}).get("text") or "") for section in sections.values())
    result = {"description": text}
    if data.get("postingUrl"):
        result["url"] = data["postingUrl"]
    return result
