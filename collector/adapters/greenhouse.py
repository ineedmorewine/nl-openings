"""Greenhouse job boards API."""
from ..text import keep_location, normalize_date, strip_html


def _hosts(params):
    if params.get("region") == "eu":
        return ["https://boards-api.eu.greenhouse.io", "https://boards-api.greenhouse.io"]
    return ["https://boards-api.greenhouse.io"]


def fetch(net, params, strict):
    last_error = None
    for host in _hosts(params):
        try:
            data = net.get(f"{host}/v1/boards/{params['token']}/jobs", params={"content": "true"}).json()
            break
        except Exception as error:  # try the next regional host
            last_error = error
    else:
        raise last_error

    jobs = []
    for posting in data.get("jobs") or []:
        places = [(posting.get("location") or {}).get("name") or ""]
        places += [office.get("name") or "" for office in posting.get("offices") or []]
        known = [p for p in places if p]
        if known and not any(keep_location(p, strict) for p in known):
            continue
        if not known and strict:
            continue
        jobs.append({
            "ext_id": str(posting.get("id")),
            "title": posting.get("title") or "",
            "location": places[0],
            "url": posting.get("absolute_url") or "",
            "posted_at": normalize_date(posting.get("first_published") or posting.get("updated_at")),
            "description": strip_html(posting.get("content") or ""),
        })
    return jobs
