"""Personio job pages via their public XML feed."""
import xml.etree.ElementTree as ET

from ..text import keep_location, normalize_date, strip_html


def fetch(net, params, strict):
    base = params["base_url"].rstrip("/")
    root = ET.fromstring(net.get(base + "/xml", params={"language": "en"}).content)
    jobs = []
    for position in root.iter("position"):
        offices = [position.findtext("office") or ""]
        offices += [(el.text or "") for el in position.iter("office")]
        offices = [o.strip() for o in dict.fromkeys(offices) if o and o.strip()]
        if offices and not any(keep_location(o, strict) for o in offices):
            continue
        if not offices and strict:
            continue
        description = " ".join(strip_html(el.findtext("value") or "")
                               for el in position.iter("jobDescription"))
        job_id = position.findtext("id") or ""
        jobs.append({
            "ext_id": job_id,
            "title": position.findtext("name") or "",
            "location": ", ".join(offices),
            "url": f"{base}/job/{job_id}",
            "posted_at": normalize_date(position.findtext("createdAt")),
            "description": description,
        })
    return jobs
