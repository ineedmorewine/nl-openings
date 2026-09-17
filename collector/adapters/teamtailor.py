"""Teamtailor career sites via their public RSS feed (works on custom domains too)."""
import xml.etree.ElementTree as ET

from ..text import keep_location, normalize_date, strip_html


def _local(tag):
    return tag.rsplit("}", 1)[-1].lower()


def fetch(net, params, strict):
    feed = net.get(params["base_url"].rstrip("/") + "/jobs.rss").content
    root = ET.fromstring(feed)
    jobs = []
    for item in root.iter("item"):
        fields = {_local(child.tag): (child.text or "") for child in item}
        places = [(el.text or "").strip() for el in item.iter()
                  if _local(el.tag) in {"city", "country", "location"} and (el.text or "").strip()]
        place = ", ".join(dict.fromkeys(places))
        if place and not keep_location(place, strict):
            continue
        if not place and strict:
            continue
        link = fields.get("link", "").strip()
        jobs.append({
            "ext_id": fields.get("guid", "").strip() or link,
            "title": fields.get("title", "").strip(),
            "location": place,
            "url": link,
            "posted_at": normalize_date(fields.get("pubdate")),
            "description": strip_html(fields.get("description", "")),
        })
    return jobs
