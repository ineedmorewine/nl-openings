"""Find which careers system a company uses, starting from its website.

Flow: homepage -> links that look like a careers page (plus a few common paths)
-> scan each page's HTML for known careers-system signatures. The result is cached
in docs/data/discovery.json so the next run goes straight to the adapter.
"""
import re
from urllib.parse import urljoin, urlparse

from .adapters.jsonld import extract_job_postings

CAREER_WORDS = re.compile(
    r"(careers?|jobs|vacatures?|vacancies|vacancy|werken[\s\-]?bij|werkenbij|join[\s\-]us|"
    r"work[\s\-]with[\s\-]us|karriere|job[\s\-]openings)", re.I)
COMMON_PATHS = ["/careers", "/en/careers", "/nl/werken-bij", "/werken-bij", "/vacatures", "/jobs"]
MAX_PAGES = 8

_ANCHOR = re.compile(r"<a\b[^>]*href=[\"']([^\"'#]+)[\"'][^>]*>(.*?)</a>", re.I | re.S)
_TAGS = re.compile(r"<[^>]+>")

IGNORED_SLUGS = {"api", "static", "assets", "cdn", "js", "css", "embed", "oneclick-ui", "ui",
                 "www", "app", "images", "fonts", "scripts", "j", "widget"}

# (adapter, regex, builder) in priority order; builders turn a match into adapter params.
STRONG_SIGNATURES = [
    ("workday",
     re.compile(r"https?://([a-z0-9\-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([A-Za-z0-9_\-]+)"),
     lambda m, page: {"host": f"{m.group(1)}.{m.group(2)}.myworkdayjobs.com",
                      "tenant": m.group(1), "site": m.group(3)}),
    ("smartrecruiters",
     re.compile(r"(?:jobs|careers)\.smartrecruiters\.com/([A-Za-z0-9_\-]+)|api\.smartrecruiters\.com/v1/companies/([A-Za-z0-9_\-]+)"),
     lambda m, page: {"company": m.group(1) or m.group(2)}),
    ("greenhouse",
     re.compile(r"(?:boards|job-boards)(\.eu)?\.greenhouse\.io/(?:embed/job_board(?:/js)?\?for=)?([A-Za-z0-9_\-]+)"),
     lambda m, page: {"token": m.group(2), "region": "eu" if m.group(1) else "us"}),
    ("lever",
     re.compile(r"jobs\.(eu\.)?lever\.co/([A-Za-z0-9_\-]+)"),
     lambda m, page: {"site": m.group(2), "region": "eu" if m.group(1) else "us"}),
    ("recruitee",
     re.compile(r"https?://([a-z0-9\-]+)\.recruitee\.com"),
     lambda m, page: {"subdomain": m.group(1)}),
    ("personio",
     re.compile(r"https?://([a-z0-9\-]+)\.jobs\.personio\.(de|com)"),
     lambda m, page: {"base_url": f"https://{m.group(1)}.jobs.personio.{m.group(2)}"}),
    ("teamtailor",
     re.compile(r"https?://([a-z0-9\-]+)\.teamtailor\.com"),
     lambda m, page: {"base_url": f"https://{m.group(1)}.teamtailor.com"}),
    ("workable",
     re.compile(r"apply\.workable\.com/([A-Za-z0-9_\-]+)"),
     lambda m, page: {"account": m.group(1)}),
]

WEAK_SIGNATURES = {
    "Phenom": re.compile(r"phenompeople", re.I),
    "iCIMS": re.compile(r"icims\.com", re.I),
    "Oracle Taleo": re.compile(r"taleo\.net", re.I),
    "Oracle Recruiting Cloud": re.compile(r"oraclecloud\.com/hcmUI/CandidateExperience", re.I),
    "SuccessFactors": re.compile(r"successfactors", re.I),
    "Avature": re.compile(r"avature\.net", re.I),
    "Jobvite": re.compile(r"jobvite\.com", re.I),
    "Homerun": re.compile(r"homerun\.co", re.I),
    "JOIN": re.compile(r"join\.com/companies", re.I),
    "Eightfold": re.compile(r"eightfold\.ai", re.I),
}


def _slug_ok(params):
    return not any(str(v).lower() in IGNORED_SLUGS for k, v in params.items()
                   if k in {"tenant", "site", "company", "token", "subdomain", "account"})


def detect_source(markup, page_url):
    """Return (source_or_None, weak_vendor_or_None) from one page's HTML."""
    markup = markup or ""
    for adapter, pattern, build in STRONG_SIGNATURES:
        for match in pattern.finditer(markup):
            params = build(match, page_url)
            if _slug_ok(params):
                return dict(type=adapter, **params), None
    origin = f"{urlparse(page_url).scheme}://{urlparse(page_url).netloc}"
    if "rmkcdn.successfactors.com" in markup or "jobTitle-link" in markup:
        return {"type": "rmk", "base_url": origin}, None
    if re.search(r"teamtailor", markup, re.I) and "teamtailor.com" not in origin:
        return {"type": "teamtailor", "base_url": origin}, None
    if extract_job_postings(markup):
        return {"type": "jsonld", "url": page_url}, None
    for vendor, pattern in WEAK_SIGNATURES.items():
        if pattern.search(markup):
            return None, vendor
    return None, None


SOCIAL_HOSTS = ("linkedin.", "facebook.", "instagram.", "twitter.", "x.com", "youtube.",
                "indeed.", "glassdoor.", "tiktok.com", "werkzoeken.", "nationalevacaturebank.")


def career_links(markup, page_url):
    """Links that look like careers pages; same-site links first, social networks skipped."""
    host = urlparse(page_url).netloc.replace("www.", "")
    same_site, elsewhere = [], []
    for href, label in _ANCHOR.findall(markup or ""):
        text = _TAGS.sub(" ", label)
        if not (CAREER_WORDS.search(href) or CAREER_WORDS.search(text)):
            continue
        absolute = urljoin(page_url, href.strip())
        if not absolute.startswith("http"):
            continue
        link_host = urlparse(absolute).netloc.replace("www.", "")
        if any(social in link_host for social in SOCIAL_HOSTS):
            continue
        bucket = same_site if (link_host == host or link_host.endswith("." + host)) else elsewhere
        if absolute not in same_site and absolute not in elsewhere:
            bucket.append(absolute)
    return same_site + elsewhere


def discover(net, company):
    """Return a dict: {source, vendor, checked_pages, reason}."""
    start_pages = []
    if company.get("careers_url"):
        start_pages.append(company["careers_url"])
    if company.get("domain"):
        start_pages.append(f"https://{company['domain']}/")

    queue, visited, weak_vendor = list(start_pages), [], None
    homepage = f"https://{company['domain']}/" if company.get("domain") else None
    reachable = False

    while queue and len(visited) < MAX_PAGES:
        url = queue.pop(0)
        if url in visited:
            continue
        visited.append(url)
        try:
            response = net.get(url)
        except Exception:
            continue
        reachable = True
        markup = response.text
        final_url = getattr(response, "url", None) or url
        source, vendor = detect_source(markup, final_url)
        if source and not (source["type"] == "jsonld" and url == homepage):
            return {"source": source, "vendor": None, "checked_pages": visited, "reason": "found"}
        weak_vendor = weak_vendor or vendor
        for link in career_links(markup, final_url):
            if link not in visited and link not in queue:
                queue.append(link)
        if url == homepage:
            queue.extend(f"https://{company['domain']}{path}" for path in COMMON_PATHS)

    if not reachable:
        reason = "site_unreachable"
    elif weak_vendor:
        reason = "unsupported_vendor"
    else:
        reason = "no_careers_system_found"
    return {"source": None, "vendor": weak_vendor, "checked_pages": visited, "reason": reason}
