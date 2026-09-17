"""Lever postings API (global and EU instances)."""
from ..text import keep_location, normalize_date


def fetch(net, params, strict):
    host = "https://api.eu.lever.co" if params.get("region") == "eu" else "https://api.lever.co"
    data = net.get(f"{host}/v0/postings/{params['site']}", params={"mode": "json"}).json()
    jobs = []
    for posting in data or []:
        categories = posting.get("categories") or {}
        places = [categories.get("location") or ""] + list(categories.get("allLocations") or [])
        explicit_nl = (posting.get("country") or "").upper() == "NL"
        if not explicit_nl and not any(keep_location(p, strict) for p in places if p):
            continue
        parts = [posting.get("descriptionPlain") or ""]
        for block in posting.get("lists") or []:
            parts.append(block.get("text") or "")
            parts.append(block.get("content") or "")
        parts.append(posting.get("additionalPlain") or "")
        jobs.append({
            "ext_id": posting.get("id") or posting.get("hostedUrl"),
            "title": posting.get("text") or "",
            "location": categories.get("location") or "",
            "url": posting.get("hostedUrl") or "",
            "posted_at": normalize_date(posting.get("createdAt")),
            "description": " ".join(parts),
        })
    return jobs
