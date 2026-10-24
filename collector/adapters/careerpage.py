"""Last-resort reader for careers pages that run no recognised recruitment system.

Most small Dutch logistics firms publish vacancies as ordinary links on a
"/vacatures" page. There is no API and no structured data, so this reader looks
for links that are shaped like a job posting, takes the link text as the title,
and opens each one to find the location and the ad text.

It is deliberately the last thing tried: a real recruitment system always gives
better data. Its job is to stop a company from being invisible.
"""
import re
from urllib.parse import urljoin, urlparse

from ..text import fold, keep_location, location_status, normalize_date, strip_html
from .jsonld import extract_job_postings, to_job

MAX_POSTINGS = 60
MAX_LIST_PAGES = 8

# A posting link usually says so in the path.
# Either "/vacatures/<slug>" or the hyphen-joined "/vacature-<slug>" both appear
# on Dutch company sites, so accept a slash or a hyphen after the keyword.
_JOB_PATH = re.compile(
    r"/(?:vacature|vacatures|vacancy|vacancies|job|jobs|job-openings|werken-bij|"
    r"career|careers|stelle|offre)[s]?[/-][^/?#]{3,}", re.I)
# Paths that match the shape above but are never a single posting.
_NOT_A_POSTING = re.compile(
    r"/(?:vacatures|vacancies|jobs|careers)/?$|"
    r"(?:category|categorie|filter|page|pagina|tag|search|zoeken|overzicht|"
    r"sollicitatie|open-sollicitatie|privacy|cookie)", re.I)
_ANCHOR = re.compile(r"<a\b[^>]*href=[\"']([^\"'>]+)[\"'][^>]*>(.*?)</a>", re.I | re.S)
_TAGS = re.compile(r"<[^>]+>")

LIST_PATHS = ["", "/vacatures", "/nl/vacatures", "/vacatures/overzicht", "/werken-bij",
              "/jobs", "/en/jobs", "/careers", "/en/careers", "/vacancies"]

# Words that mean the link is navigation, not a vacancy.
_NAVIGATION = {"vacatures", "vacancies", "jobs", "careers", "werken bij", "alle vacatures",
               "bekijk vacatures", "view all jobs", "open sollicitatie", "meer", "more",
               "lees meer", "read more", "solliciteer", "apply", "apply now", "contact"}

# Many Dutch careers pages put the job title in a heading and give every link the
# same label ("View the vacancy"). Those labels are not titles, so the title is
# taken from the address instead.
_GENERIC_LABEL = re.compile(
    r"^(view|bekijk|lees|see|read|open|naar|go to|meer over)\b|"
    r"\b(vacature|vacancy|job|functie|details?|here|hier)$", re.I)


def _title_from_url(url):
    """Turn ".../vacancies/freight-forwarder-road-uk/" into a readable title."""
    slug = [part for part in urlparse(url).path.split("/") if part]
    if not slug:
        return ""
    words = re.sub(r"[-_]+", " ", slug[-1]).strip()
    words = re.sub(r"\s+\d+$", "", words)          # drop the "-2" on repeated slugs
    words = re.sub(r"\.(html?|php|aspx)$", "", words, flags=re.I)
    return " ".join(w if w.isupper() else w.capitalize() for w in words.split())


def _is_generic(title):
    return bool(_GENERIC_LABEL.search(title)) or len(title.split()) < 2


def _clean_title(label):
    title = strip_html(label)
    title = re.sub(r"\s+", " ", title).strip(" -–|·•\t\n")
    return title


def _looks_like_a_posting(url, title):
    if not _JOB_PATH.search(url) or _NOT_A_POSTING.search(url):
        return False
    if fold(title) in _NAVIGATION:
        return False
    return 6 <= len(title) <= 120 and len(title.split()) >= 2


def find_posting_links(markup, page_url):
    """Return [(url, title)] for links on this page that look like job postings."""
    host = urlparse(page_url).netloc
    found, seen = [], set()
    for href, label in _ANCHOR.findall(markup or ""):
        url = urljoin(page_url, href.strip())
        if urlparse(url).netloc != host:
            continue
        url = url.split("#")[0]
        title = _clean_title(label)
        if _is_generic(title):
            title = _title_from_url(url)
        if url in seen or not _looks_like_a_posting(url, title):
            continue
        seen.add(url)
        found.append((url, title))
    return found


def _list_pages(params):
    if params.get("urls"):
        return list(params["urls"])
    if params.get("url"):
        return [params["url"]]
    base = params["base_url"].rstrip("/")
    return [base + path for path in LIST_PATHS]


def fetch(net, params, strict):
    links = []
    for page_url in _list_pages(params)[:MAX_LIST_PAGES]:
        try:
            markup = net.get(page_url).text
        except Exception:
            continue
        for item in find_posting_links(markup, page_url):
            if item[0] not in {u for u, _ in links}:
                links.append(item)
        if len(links) >= MAX_POSTINGS:
            break
    if not links:
        raise RuntimeError("no job links found on the careers page")

    jobs = []
    for url, title in links[:MAX_POSTINGS]:
        jobs.append({"ext_id": url, "title": title, "location": "", "url": url,
                     "posted_at": None})
    return jobs


def describe(net, params, job):
    """Open one posting to read its location, date and text."""
    markup = net.get(job["url"]).text
    postings = extract_job_postings(markup)
    if postings:
        found = to_job(postings[0], job["url"])
        return {"description": found["description"], "location": found["location"],
                "posted_at": found["posted_at"]}
    text = strip_html(markup)
    return {"description": text[:6000], "location": _guess_location(text)}


# "Standplaats: Tilburg", "Locatie Venlo", "Location: Rotterdam".
# The label is matched case-insensitively, the place name is not: place names are
# capitalised, and ignoring case here would swallow the sentence after them.
_DUTCH_PLACE = re.compile(
    r"(?i:\b(?:standplaats|werklocatie|locatie|location|vestiging)\b)\s*[:\-]?\s*"
    # Dutch place names carry lowercase particles: Bergen op Zoom, Alphen aan den Rijn.
    r"([A-ZÀ-Ü][a-zà-ÿ'\u2019.-]+"
    r"(?:[ -](?:op|aan|den|der|de|van|bij|ter|te)\b|[ -][A-ZÀ-Ü][a-zà-ÿ'\u2019.-]+){0,4})")


def _guess_location(text):
    """Return the place a posting names, whether or not we recognise it.

    Reporting only Dutch places would leave a foreign vacancy looking as though
    its location were unknown, and an unknown location is kept. A stated place
    we do not recognise is better reported as it stands and filtered later.

    The capture can run on into the next sentence ("Tilburg Je plant..."), so
    prefer the shortest leading words that already name a known place, which
    also keeps multi-word names such as "Bergen op Zoom" whole. Failing that,
    fall back to the first word.
    """
    for match in _DUTCH_PLACE.finditer(text or ""):
        words = match.group(1).strip(" .,-").split()
        if not words:
            continue
        for length in range(1, len(words) + 1):
            candidate = " ".join(words[:length])
            if location_status(candidate) in ("nl", "foreign"):
                return candidate
        return words[0]
    return ""
