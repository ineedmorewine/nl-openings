"""Workday career sites (*.myworkdayjobs.com). Uses the public JSON endpoints behind the site."""
from ..text import keep_location, normalize_date, parse_relative_posted, strip_html

NL_NAMES = {"netherlands", "the netherlands", "nederland"}
PAGE_SIZE = 20
MAX_POSTINGS = 3000


def _base(params):
    return f"https://{params['host']}/wday/cxs/{params['tenant']}/{params['site']}"


def find_netherlands_facet(facets, parent_parameter=None):
    """Walk Workday's nested facet tree and return (facetParameter, id) for the Netherlands."""
    for facet in facets or []:
        parameter = facet.get("facetParameter") or parent_parameter
        for value in facet.get("values") or []:
            if "values" in value:
                found = find_netherlands_facet([value], parameter)
                if found:
                    return found
            elif (value.get("descriptor") or "").strip().lower() in NL_NAMES and value.get("id"):
                return parameter, value["id"]
    return None


def fetch(net, params, strict):
    url = _base(params) + "/jobs"
    probe = net.post_json(url, {"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""})
    facet = find_netherlands_facet(probe.get("facets"))
    applied = {facet[0]: [facet[1]]} if facet else {}

    jobs, offset, total = [], 0, None
    while True:
        data = net.post_json(url, {"appliedFacets": applied, "limit": PAGE_SIZE,
                                   "offset": offset, "searchText": ""})
        postings = data.get("jobPostings") or []
        if total is None:
            total = data.get("total") or 0
        for posting in postings:
            location = posting.get("locationsText") or ""
            # Without a country facet, keep only postings whose location text is clearly Dutch.
            if not facet and not keep_location(location, strict=True):
                continue
            path = posting.get("externalPath") or ""
            jobs.append({
                "ext_id": path,
                "title": posting.get("title") or "",
                "location": location,
                "url": f"https://{params['host']}/{params['site']}{path}",
                "posted_at": parse_relative_posted(posting.get("postedOn") or ""),
            })
        offset += PAGE_SIZE
        if not postings or offset >= min(total, MAX_POSTINGS):
            break
    return jobs


def describe(net, params, job):
    info = net.get(_base(params) + job["ext_id"]).json().get("jobPostingInfo") or {}
    result = {"description": strip_html(info.get("jobDescription") or "")}
    if info.get("externalUrl"):
        result["url"] = info["externalUrl"]
    if info.get("location"):
        result["location"] = info["location"]
    if normalize_date(info.get("startDate")):
        result["posted_at"] = normalize_date(info.get("startDate"))
    return result
