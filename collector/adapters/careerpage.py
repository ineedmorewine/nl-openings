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


def sample_links(markup, page_url, limit=6):
    """A few of the links a page offers, for working out why none looked like a job."""
    seen = []
    for href, _ in _ANCHOR.findall(markup or ""):
        url = urljoin(page_url, href.strip()).split("#")[0]
        path = urlparse(url).path
        if not path or path == "/":
            continue
        mark = "" if same_site(url, page_url) else "[other host] "
        entry = mark + path[:70]
        if entry not in seen:
            seen.append(entry)
        if len(seen) >= limit:
            break
    return seen


def same_site(a, b):
    """www.acme.nl and acme.nl are one site."""
    strip = lambda host: urlparse(host).netloc.lower().removeprefix("www.")
    return strip(a) == strip(b)


def find_posting_links(markup, page_url):
    """Return [(url, title)] for links on this page that look like job postings."""
    found, seen = [], set()
    for href, label in _ANCHOR.findall(markup or ""):
        url = urljoin(page_url, href.strip())
        if not same_site(url, page_url):
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


# Some companies put every vacancy on one page as a heading with an application
# form under it, with no link to follow. Barsan publishes a Senior Transport
# Planner that way. Reading only links makes those companies look empty.
_HEADING = re.compile(r"<(h[1-4])\b[^>]*>(.*?)</\1>", re.I | re.S)
# A heading counts as a vacancy only if it names a role; otherwise "Over ons"
# and "Contact" would become jobs.
_ROLE_WORDS = re.compile(
    r"\b(planner|planning|forwarder|expediteur|expeditie|coordinator|"
    r"co[oö]rdinator|medewerker|specialist|manager|supervisor|teamleider|"
    r"teamlead|operator|chauffeur|monteur|engineer|analist|analyst|"
    r"administrateur|assistent|assistant|controller|adviseur|advisor|"
    r"disponent|dispatcher|agent|officer|trainee|stagiair)\b", re.I)
MAX_HEADING_POSTINGS = 25


def find_posting_headings(markup, page_url):
    """Vacancies written as headings on the careers page itself.

    The page is its own application page, so every one of these points back at
    it. Returns [(url, title)] with a fragment that keeps them apart.
    """
    found, seen = [], set()
    for _, raw in _HEADING.findall(markup or ""):
        title = _clean_title(raw)
        if not title or len(title) > 90 or len(title.split()) < 2:
            continue
        if not _ROLE_WORDS.search(title) or fold(title) in _NAVIGATION:
            continue
        key = fold(title)
        if key in seen:
            continue
        seen.add(key)
        slug = re.sub(r"[^a-z0-9]+", "-", key).strip("-")
        found.append((f"{page_url}#{slug}", title))
        if len(found) >= MAX_HEADING_POSTINGS:
            break
    return found


def _list_pages(params):
    if params.get("urls"):
        return list(params["urls"])
    if params.get("url"):
        return [params["url"]]
    base = params["base_url"].rstrip("/")
    return [base + path for path in LIST_PATHS]


def fetch(net, params, strict):
    links, titles = [], set()
    for page_url in _list_pages(params)[:MAX_LIST_PAGES]:
        try:
            markup = net.get(page_url).text
        except Exception:
            continue
        found = find_posting_links(markup, page_url)
        if not found:
            # No links: the vacancies may be headings on this page.
            found = find_posting_headings(markup, page_url)
        for url, title in found:
            # The same vacancy often appears on more than one careers page, and
            # a heading carries its page's address, so the title is what tells
            # two listings of one job apart.
            key = fold(title)
            if url in {u for u, _ in links} or key in titles:
                continue
            titles.add(key)
            links.append((url, title))
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
