"""Recruitee careers API."""
from ..text import keep_location, normalize_date, strip_html


def fetch(net, params, strict):
    base = params.get("base_url") or f"https://{params['subdomain']}.recruitee.com"
    data = net.get(base.rstrip("/") + "/api/offers/").json()
    jobs = []
    for offer in data.get("offers") or []:
        explicit_nl = (offer.get("country_code") or "").upper() == "NL"
        place = ", ".join(x for x in [offer.get("city"), offer.get("country")] if x) or offer.get("location") or ""
        if not explicit_nl and not keep_location(place, strict):
            continue
        jobs.append({
            "ext_id": str(offer.get("id")),
            "title": offer.get("title") or "",
            "location": offer.get("city") or offer.get("location") or "",
            "url": offer.get("careers_url") or "",
            "posted_at": normalize_date(offer.get("published_at") or offer.get("created_at")),
            "description": strip_html((offer.get("description") or "") + " " + (offer.get("requirements") or "")),
        })
    return jobs
