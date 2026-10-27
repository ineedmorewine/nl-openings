"""Find which careers system a company uses, starting from its website.

Flow: homepage -> links that look like a careers page (plus a few common paths)
-> scan each page's HTML for known careers-system signatures. The result is cached
in docs/data/discovery.json so the next run goes straight to the adapter.
"""
import re
from urllib.parse import unquote, urljoin, urlparse

from .adapters.careerpage import find_posting_links
from .adapters.jsonld import extract_job_postings
from .text import fold

CAREER_WORDS = re.compile(
    r"(careers?|jobs|vacatures?|vacancies|vacancy|werken[\s\-]?bij|werkenbij|"
    r"join[\s\-](us|our[\s\-]team|the[\s\-]team)|work[\s\-](with|for|at)[\s\-]us|"
    r"karriere|job[\s\-]openings|recruit(ment)?|sollicit|"
    r"ons[\s\-]team|onze[\s\-]mensen|wie[\s\-]zijn[\s\-]wij)", re.I)
COMMON_PATHS = ["/careers", "/en/careers", "/careers/", "/nl/werken-bij", "/werken-bij",
                "/vacatures", "/nl/vacatures", "/jobs", "/en/jobs", "/career", "/en/career"]
# Careers sites very often live on their own host rather than a path.
# Careers sites very often live on their own host rather than a path. Dutch
# employers in particular put theirs on werkenbij<brand>.nl, a separate site
# from the company's own.
COMMON_HOSTS = ["jobs.{domain}", "careers.{domain}", "werkenbij.{domain}", "career.{domain}",
                "vacatures.{domain}", "karriere.{domain}", "werkenbij{brand}.nl",
                "werkenbij{brand}.com", "jobs.{brand}.nl"]
MAX_PAGES = 20

_ANCHOR = re.compile(r"<a\b[^>]*href=[\"']([^\"'#]+)[\"'][^>]*>(.*?)</a>", re.I | re.S)
_TAGS = re.compile(r"<[^>]+>")

IGNORED_SLUGS = {"api", "static", "assets", "cdn", "js", "css", "embed", "oneclick-ui", "ui",
                 "www", "app", "images", "fonts", "scripts", "j", "widget",
                 # placeholders copied from vendor documentation into page templates
                 "this_part", "your-company", "yourcompany", "company-name", "example",
                 "demo", "test", "token", "board-token", "your_token",
                 # shared vendor infrastructure, not a customer account
                 "careers-analytics", "analytics", "cdn-assets", "help", "support"}

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
    ("oracle",
     re.compile(r"https?://([a-z0-9\-]+\.fa\.[a-z0-9\-]+(?:\.ocs)?\.oraclecloud\.com)"
                r"/hcmUI/CandidateExperience/[^\"'\s]*?/sites/([A-Za-z0-9_\-]+)"),
     lambda m, page: {"host": m.group(1), "site": m.group(2)}),
    ("icims",
     re.compile(r"https?://(careers-[a-z0-9\-]+\.icims\.com)"),
     lambda m, page: {"host": m.group(1)}),
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


_BRAND_SEARCH = re.compile(r'href=["\']/([A-Za-z0-9_\-]{2,40})/(?:search|viewalljobs)/', re.I)


def _brand_path(page_url, markup):
    """Return "/Brand" when a SuccessFactors site is served under a brand folder."""
    first = [part for part in urlparse(page_url).path.split("/") if part]
    if first and first[0].lower() not in {"search", "viewalljobs", "job", "jobs", "go"}:
        return "/" + first[0]
    match = _BRAND_SEARCH.search(markup or "")
    return "/" + match.group(1) if match else ""


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
    if "phApp.ddo" in markup:
        # This page IS the Phenom career site, so its own origin is the right base.
        source = {"type": "phenom", "base_url": origin}
        if "search-results" in page_url:
            source["search_url"] = page_url.split("?")[0]
        return source, None
    if "phenompeople" in markup.lower():
        # Only a reference to Phenom; find the career host it points at.
        for match in re.finditer(r"https?://([a-z0-9.\-]+)/(?:[a-z\-]+/)*search-results", markup, re.I):
            return {"type": "phenom", "base_url": "https://" + match.group(1)}, None
        return None, "Phenom"
    if ("rmkcdn.successfactors.com" in markup or "jobTitle-link" in markup
            or "/viewalljobs/" in markup or "createNewAlert" in markup):
        # Several brands can share one host under different paths
        # (CEVA and CMA CGM both live on jobs.cmacgm-group.com), so keep the
        # first path segment when the page sits under one.
        return {"type": "rmk", "base_url": origin + _brand_path(page_url, markup)}, None
    if ("teamtailor.com" in markup.lower()
            or re.search(r'content="Teamtailor"', markup, re.I)) and "teamtailor.com" not in origin:
        return {"type": "teamtailor", "base_url": origin}, None
    if extract_job_postings(markup):
        return {"type": "jsonld", "url": page_url}, None
    if page_url != f"{origin}/" and len(find_posting_links(markup, page_url)) >= 2:
        # No recruitment system, but the page lists vacancies as ordinary links.
        return {"type": "careerpage", "url": page_url}, None
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


def homepages(domain):
    """Both spellings of a site's front page; small sites often answer on only one."""
    if not domain:
        return []
    bare = domain.replace("www.", "")
    return [f"https://{bare}/", f"https://www.{bare}/"]


# A careers page that builds its list in the browser looks empty to us, even
# when it runs on a platform we can read. These addresses answer directly, so
# they identify the platform where the page itself gives nothing away.
KNOWN_ENDPOINTS = [
    ("teamtailor", "/jobs.rss", lambda body: "<item" in body[:4000].lower()),
    ("recruitee", "/api/offers/", lambda body: '"offers"' in body[:4000]),
    ("personio", "/xml?language=en", lambda body: "<position" in body[:4000].lower()),
]


def probe_known_endpoints(net, origin):
    """Return a source when a careers host answers on a platform address we know."""
    for adapter, path, looks_right in KNOWN_ENDPOINTS:
        try:
            body = net.get(origin.rstrip("/") + path).text
        except Exception:
            continue
        if looks_right(body or ""):
            return {"type": adapter, "base_url": origin.rstrip("/")}
    return None


SEARCH_URL = "https://html.duckduckgo.com/html/"
# The result link, written with class and href in either order.
_RESULT_HREF = re.compile(
    r'<a[^>]*class="[^"]*result__a[^"]*"[^>]*href="([^"]+)"'
    r'|<a[^>]*href="([^"]+)"[^>]*class="[^"]*result__a', re.I)
# The search page often hands out its own redirect, with the real address inside.
_REDIRECTED = re.compile(r"[?&]uddg=([^&]+)")
_NOT_A_COMPANY_SITE = ("linkedin.", "facebook.", "indeed.", "glassdoor.", "kvk.nl",
                       "bedrijvenpagina", "companyinfo", "opencorporates", "youtube.",
                       "dnb.com", "trustpilot.", "wikipedia.", "duckduckgo.", "bing.",
                       "google.", "werkzoeken.", "nationalevacaturebank.", "jobbird.")


def find_domain(net, name):
    """Look up a company's own website, and only accept one that names it back.

    Addresses guessed from a company name were wrong often enough to be worse
    than nothing, so a candidate is kept only if its front page actually
    mentions the company. An address we cannot confirm is left empty.
    """
    try:
        markup = net.get(SEARCH_URL, params={"q": f"{name} B.V. Nederland officiële website"}).text
    except Exception:
        return None
    candidates = [first or second for first, second in _RESULT_HREF.findall(markup or "")]
    words = [word for word in re.findall(r"[a-z]+", fold(name)) if len(word) > 2]
    for link in candidates[:8]:
        redirected = _REDIRECTED.search(link)
        link = unquote(redirected.group(1)) if redirected else link
        host = urlparse(link).netloc.lower()
        if not host or any(skip in host for skip in _NOT_A_COMPANY_SITE):
            continue
        bare = host.replace("www.", "")
        try:
            body = fold(net.get(f"https://{bare}/").text)
        except Exception:
            continue
        if sum(word in body for word in words) >= max(1, len(words) - 1):
            return bare
    return None


_SITEMAP_URL = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.I)
_VACANCY_URL = re.compile(r"/(vacature|vacatures|vacancy|vacancies|job|jobs|werken-bij)[/-]", re.I)
SITEMAP_PATHS = ["/sitemap.xml", "/sitemap_index.xml", "/wp-sitemap.xml"]


def careers_page_from_sitemap(net, origin):
    """Find the vacancy section from the site's own map of itself.

    A page that builds its list in the browser hides its vacancies from us, but
    the sitemap still lists them, and it is a file written for machines to read.
    """
    for path in SITEMAP_PATHS:
        try:
            body = net.get(origin.rstrip("/") + path).text
        except Exception:
            continue
        locations = _SITEMAP_URL.findall(body or "")
        # A sitemap index points at further sitemaps; follow one that sounds right.
        nested = [loc for loc in locations if loc.endswith(".xml") and _VACANCY_URL.search(loc)]
        for loc in nested[:2]:
            try:
                locations += _SITEMAP_URL.findall(net.get(loc).text or "")
            except Exception:
                continue
        vacancies = [loc for loc in locations if _VACANCY_URL.search(loc)]
        if len(vacancies) >= 2:
            return vacancies
    return []


def discover(net, company):
    """Return a dict: {source, vendor, checked_pages, reason, notes}."""
    start_pages = []
    if company.get("careers_url"):
        start_pages.append(company["careers_url"])
    start_pages.extend(homepages(company.get("domain")))

    # Two queues, and the guessed addresses are always read last. A company's
    # own "Werken bij" link is worth more than eleven guessed paths, and when
    # the guesses went first they used up the page budget on 404s before the
    # real careers page was ever opened.
    queue, guesses, visited, weak_vendor = list(start_pages), [], [], None
    # Pages that actually answered. Opening a page in a browser is slow, so the
    # browser is only ever sent to an address we know exists.
    reached = []
    fronts = set(homepages(company.get("domain")))
    reachable = False
    notes = []

    while (queue or guesses) and len(visited) < MAX_PAGES:
        url = queue.pop(0) if queue else guesses.pop(0)
        if url in visited:
            continue
        visited.append(url)
        try:
            response = net.get(url)
        except Exception as error:
            # Keep why a page failed: a refusal and a bad address need different fixes.
            notes.append(f"{url} -> {type(error).__name__}: {str(error)[:90]}")
            continue
        reachable = True
        reached.append(url)
        markup = response.text
        final_url = getattr(response, "url", None) or url
        source, vendor = detect_source(markup, final_url)
        if source and not (source["type"] == "jsonld" and url in fronts):
            return {"source": source, "vendor": None, "checked_pages": visited,
                    "reached_pages": reached, "reason": "found", "notes": notes}
        weak_vendor = weak_vendor or vendor
        for link in career_links(markup, final_url):
            if link not in visited and link not in queue:
                queue.append(link)
        if url in fronts and company.get("domain"):
            bare = company["domain"].replace("www.", "")
            brand = bare.split(".")[0]
            for prefix in (f"https://www.{bare}", f"https://{bare}"):
                guesses.extend(prefix + path for path in COMMON_PATHS)
            guesses.extend("https://" + host.format(domain=bare, brand=brand) + "/"
                           for host in COMMON_HOSTS)

    # Nothing recognised on any page. Before giving up, ask the careers hosts
    # themselves whether they run on a platform we can read.
    for page in visited:
        parsed = urlparse(page)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin in {f"{urlparse(f).scheme}://{urlparse(f).netloc}" for f in fronts}:
            continue
        source = probe_known_endpoints(net, origin)
        if source:
            return {"source": source, "vendor": None, "checked_pages": visited,
                    "reached_pages": reached, "reason": "found", "notes": notes}

    # Still nothing. The site's own map of itself names its vacancy pages even
    # when no link to them survives in the markup we can download.
    for page in list(fronts) + visited[:3]:
        parsed = urlparse(page)
        vacancies = careers_page_from_sitemap(net, f"{parsed.scheme}://{parsed.netloc}")
        if vacancies:
            return {"source": {"type": "sitemap", "urls": vacancies[:40]}, "vendor": None,
                    "checked_pages": visited, "reached_pages": reached,
                    "reason": "found", "notes": notes}

    if not reachable:
        reason = "site_unreachable"
    elif weak_vendor:
        reason = "unsupported_vendor"
    else:
        reason = "no_careers_system_found"
    return {"source": None, "vendor": weak_vendor, "checked_pages": visited,
            "reached_pages": reached, "reason": reason, "notes": notes[:6]}
