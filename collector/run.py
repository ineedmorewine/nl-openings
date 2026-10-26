"""Daily collection run.

Usage:
  python -m collector.run                 # all companies
  python -m collector.run --only dsv      # one company, everything else carried over

Writes docs/data/jobs.json, docs/data/sources.json and docs/data/discovery.json.
A company whose site cannot be read today keeps its previous jobs, so a bad day
never wipes the list or makes old jobs look new tomorrow.
"""
import argparse
import re
import json
import time
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from .adapters import ADAPTERS, careerpage
from .discovery import career_links, detect_source, discover, find_domain, homepages
from .render import BrowserUnavailable, PageRenderer
from .filters import is_suitable
from .net import Net
from .text import dutch_required, international_labels, keep_location, written_in_dutch

ROOT = Path(__file__).resolve().parent.parent
COMPANIES_FILE = ROOT / "config" / "companies.json"
DATA_DIR = ROOT / "docs" / "data"
JOBS_FILE = DATA_DIR / "jobs.json"
SOURCES_FILE = DATA_DIR / "sources.json"
DISCOVERY_FILE = DATA_DIR / "discovery.json"
FILTERS_FILE = DATA_DIR / "filters.json"

MAX_DESCRIPTIONS_PER_SOURCE = 60
MAX_LINK_DESCRIPTIONS = 120
# Rendering is slow, so it is reserved for companies that would otherwise be
# reported as unreadable, and capped for the run as a whole.
RENDER_PAGE_BUDGET = 500
RENDER_PAGES_PER_COMPANY = 3
# Extra pages allowed when a rendered page points at the real careers page.
RENDER_FOLLOWED_PAGES = 2
TIME_BUDGET_SECONDS = 70 * 60


def load_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def is_strict(company, careers_page=None):
    """Whether a job must name a Dutch place before we believe it is in the Netherlands.

    A careers site on a .nl address lists Dutch vacancies, whatever the parent
    company's own address is. Barsan's site is barsan.nl while the group is
    barsan.com, and judging by the group address alone threw away its open
    Senior Transport Planner.
    """
    if company.get("scope"):
        return company["scope"] != "nl"
    for address in (careers_page, company.get("careers_url"), company.get("domain")):
        if address and urlparse(address if "//" in address else f"//{address}").netloc.endswith(".nl"):
            return False
    return True


def make_job_id(company_id, raw):
    return f"{company_id}:{raw.get('ext_id') or raw.get('url')}"


# Raised whenever the reading rules change, so postings already on the list are
# read again with the new rules instead of keeping an answer from the old ones.
LABEL_RULES = 2


def language_labels(text):
    if not text:
        return None
    return {"rules": LABEL_RULES,
            "dutch_required": dutch_required(text), "written_in_dutch": written_in_dutch(text),
            **(international_labels(text) or {})}


def labels_are_current(labels):
    """Whether a label set an earlier run saved was read with today's questions.

    When a new question is added, such as whether the ad mentions a work permit,
    the postings already in the list were never asked it. Rather than leave them
    blank for as long as they stay open, they are read once more.
    """
    return isinstance(labels, dict) and labels.get("rules") == LABEL_RULES


def careers_link(company, discovery_cache):
    """A page the person can open themselves when we cannot read the company.

    Prefers a careers page discovery actually reached, then anything configured,
    then the company website. Both spellings of the front page count as the
    front page, otherwise "https://www.x.com/" is mistaken for a careers page.
    """
    if company.get("careers_url"):
        return company["careers_url"]
    pages = (discovery_cache.get(company["id"]) or {}).get("checked_pages") or []
    fronts = set(homepages(company.get("domain")))
    for page in pages:
        if page not in fronts:
            return page
    return next(iter(sorted(fronts)), None)


# A recruitment system's own result page is where its jobs are listed; its brand
# landing page usually holds none. Try both when reading a site as plain links.
SEARCH_PATHS = ["/search/", "/search", "/search-results", "/jobs", "/go/"]


def second_opinion_pages(company, discovery_cache, source):
    """Pages worth reading as plain links, best first, without repeats."""
    candidates = []
    base = (source or {}).get("base_url")
    if base:
        candidates.extend(base.rstrip("/") + path for path in SEARCH_PATHS)
    candidates.append(careers_link(company, discovery_cache))
    for page in (discovery_cache.get(company["id"]) or {}).get("checked_pages") or []:
        candidates.append(page)
    seen, pages = set(), []
    for url in candidates:
        if url and url not in seen:
            seen.add(url)
            pages.append(url)
    def careers_first(url):
        return 0 if re.search(r"(vacature|vacancy|career|job|werken)", url, re.I) else 1

    return sorted(pages, key=careers_first)[:12]


def second_opinion(net, company, discovery_cache, source=None):
    """Read the company's careers pages as plain links. None if nothing is found."""
    pages = second_opinion_pages(company, discovery_cache, source)
    if not pages:
        return None
    params = {"urls": pages}
    try:
        jobs = careerpage.fetch(net, params, is_strict(company, pages[0]))
    except Exception:
        return None
    return {"jobs": jobs, "params": params} if jobs else None


def rendering_order(company, discovery_cache, source=None):
    """Pages worth opening in a browser, best first.

    Different from reading a page as plain text. The addresses that look most
    like careers pages are usually the guessed ones that returned 404, and
    rendering a 404 teaches nothing. The front page is the one page we know
    answers, and when it is built in the browser it is also the page holding
    the careers link we could not see.
    """
    pages, seen = [], set()
    failed = {note.split(" -> ")[0] for note in
              (discovery_cache.get(company["id"]) or {}).get("notes") or []}
    for url in (homepages(company.get("domain"))[:1]
                + second_opinion_pages(company, discovery_cache, source)):
        if url and url not in seen and url not in failed:
            seen.add(url)
            pages.append(url)
    return pages


def rendered_second_opinion(renderer, company, discovery_cache, source=None):
    """Last resort: open the careers pages in a browser and read what appears.

    A page that builds its vacancy list after loading is empty when downloaded,
    so nothing above can see it. Returns None when a browser is unavailable,
    the budget is spent, or the rendered pages still list nothing.
    """
    if renderer is None:
        return None
    if renderer.exhausted:
        return {"jobs": [], "params": {}, "report": f"skipped: {renderer.last_error or 'budget spent'}"}
    jobs, seen, pages, trouble, samples = [], set(), 0, None, []
    queue = rendering_order(company, discovery_cache, source)[:RENDER_PAGES_PER_COMPANY]
    followed = 0
    for url in queue:
        try:
            markup = renderer.render(url)
            pages += 1
        except BrowserUnavailable as error:
            return {"jobs": [], "params": {}, "report": f"no browser: {error}"}
        except Exception as error:
            trouble = f"{type(error).__name__}: {str(error)[:90]}"
            continue
        # The page we could not read may simply have loaded its recruitment
        # system in the browser. If it did, say which one: the proper reader
        # gives locations and dates that scraped links never do.
        rendered_source, _ = detect_source(markup, url)
        if rendered_source and rendered_source["type"] not in ("jsonld", "careerpage"):
            return {"jobs": [], "params": {}, "source": rendered_source,
                    "report": f"rendered {pages} page(s), found a {rendered_source['type']} site"}

        found = careerpage.find_posting_links(markup, url)
        if not found:
            found = careerpage.find_posting_headings(markup, url)
        if not found:
            if not samples:
                # Keep a few of the links the page did offer, so a page that
                # lists no vacancy can be told from one we misread.
                samples = careerpage.sample_links(markup, url)
            # Often this is a homepage or a dead guess that simply points at the
            # real careers page. Follow that rather than give up on the company.
            if followed < RENDER_FOLLOWED_PAGES:
                # Not restricted to the same site: Dutch employers routinely
                # keep their vacancies on werkenbij<brand>.nl, a different host
                # from the company's own, and that link was being discarded.
                for link in career_links(markup, url):
                    if link not in queue:
                        queue.append(link)
                        followed += 1
                        if followed >= RENDER_FOLLOWED_PAGES:
                            break
        for link, title in found:
            if link in seen:
                continue
            seen.add(link)
            jobs.append({"ext_id": link, "title": title, "location": "",
                         "url": link, "posted_at": None})
        if jobs:
            break
    report = (f"rendered {pages} page(s), found {len(jobs)} link(s)"
              + (f"; {trouble}" if trouble else "")
              + (f"; saw {samples}" if samples and not jobs else ""))
    return {"jobs": jobs, "params": {"urls": [j["url"] for j in jobs]}, "report": report}


def resolve_source(net, company, discovery_cache, today):
    """Return (source, discovered, failure_status). failure_status is None when a source exists."""
    if company.get("source"):
        return company["source"], False, None
    cached = discovery_cache.get(company["id"])
    if cached and cached.get("source"):
        return cached["source"], True, None
    if not company.get("domain") and not company.get("careers_url"):
        # No website on file. Look one up, and keep it only if the page names
        # the company back, so a wrong address is never written down as fact.
        looked_up = (cached or {}).get("looked_up_domain")
        if not looked_up:
            # A failed look-up is not remembered, so a search that was merely
            # unavailable today is tried again tomorrow.
            looked_up = find_domain(net, company["name"])
            if looked_up:
                discovery_cache[company["id"]] = dict(cached or {}, looked_up_domain=looked_up,
                                                      checked_at=today)
        if not looked_up:
            return None, False, {"status": "no_address"}
        company = dict(company, domain=looked_up)
    result = discover(net, company)
    discovery_cache[company["id"]] = dict(result, checked_at=today)
    if result["source"]:
        return result["source"], True, None
    return None, True, {"status": result["reason"], "vendor": result.get("vendor"),
                        "notes": result.get("notes") or []}


def collect_company(net, company, discovery_cache, filters, previous_by_id, today,
                    renderer=None):
    """Return (status_entry, jobs). jobs is None when the company could not be read today."""
    source, discovered, failure = resolve_source(net, company, discovery_cache, today)
    if failure:
        # A site that refuses our download often answers a real browser, so an
        # unreachable site is worth one rendered attempt before it is written
        # off. Only an address we do not have is hopeless here.
        if failure["status"] not in ("no_careers_system_found", "site_unreachable"):
            return failure, None
        # No recruitment system recognised, but the careers pages we reached may
        # still list vacancies as ordinary links. Giving up here is what kept
        # most of the list unread: a company without a known system was never
        # read at all, however plainly its vacancies were published.
        blocked = failure["status"] == "site_unreachable"
        fallback = None if blocked else second_opinion(net, company, discovery_cache)
        rendered = False
        if not fallback:
            attempt = rendered_second_opinion(renderer, company, discovery_cache)
            failure["rendering"] = attempt["report"] if attempt else "not attempted"
            fallback = attempt
            rendered = bool(attempt and attempt["jobs"])
            # The browser recognised a recruitment system the plain page hid.
            # Remember it, so from the next run on this company is read
            # properly, by its own system, with no browser needed.
            if attempt and attempt.get("source"):
                discovery_cache[company["id"]] = dict(discovery_cache.get(company["id"]) or {},
                                                      source=attempt["source"], checked_at=today)
                source, failure, fallback = attempt["source"], None, None
        if failure and (not fallback or not fallback["jobs"]):
            return failure, None
        if fallback and fallback["jobs"]:
            source, adapter = {"type": "careerpage"}, careerpage
            params = fallback["params"]
            method = "careerpage (rendered)" if rendered else "careerpage"
            discovered = True
            raw_jobs = fallback["jobs"]
        else:
            # The browser named the recruitment system; read it properly below.
            adapter = ADAPTERS.get(source.get("type"))
            if adapter is None:
                return {"status": "read_failed",
                        "detail": f"unknown source type {source.get('type')}"}, None
            params = {key: value for key, value in source.items() if key != "type"}
            method = f"{source['type']} (found by browser)"
            discovered = True
            raw_jobs = None
    else:
        adapter = ADAPTERS.get(source.get("type"))
        if adapter is None:
            return {"status": "read_failed",
                    "detail": f"unknown source type {source.get('type')}"}, None
        params = {key: value for key, value in source.items() if key != "type"}
        method = source["type"]
        raw_jobs = None

    if raw_jobs is None:
        try:
            raw_jobs = adapter.fetch(net, params, is_strict(company))
        except Exception as error:
            if discovered:
                # Forget the guess so the next run looks for the careers system again.
                discovery_cache.pop(company["id"], None)
            return {"status": "read_failed", "method": method,
                    "detail": f"{type(error).__name__}: {str(error)[:160]}"}, None

    if not raw_jobs:
        # The recruitment system answered but listed nothing. That is sometimes
        # true and sometimes means its page layout is one we cannot parse, which
        # looks identical from here. Read the page as plain links before believing
        # the zero: this is what kept CEVA and Kuehne+Nagel showing no vacancies.
        fallback = second_opinion(net, company, discovery_cache, source)
        if fallback:
            raw_jobs, adapter, params = fallback["jobs"], careerpage, fallback["params"]
            method = f"{method}+careerpage"

    jobs, described = {}, 0
    extra = company.get("extra_keywords") or []
    for raw in raw_jobs:
        url = (raw.get("url") or "").strip()
        title = (raw.get("title") or "").strip()
        if not title or not url.startswith("http"):
            continue
        job_id = make_job_id(company["id"], raw)
        previous = previous_by_id.get(job_id)

        # Carry forward what an earlier run learned by opening this posting.
        # Without this the location is lost and the job is then discarded as one
        # we cannot place.
        if previous and previous.get("location") and not raw.get("location"):
            raw["location"] = previous["location"]

        if previous and previous.get("labels_checked") and labels_are_current(previous.get("labels")):
            labels, checked = previous.get("labels"), True
        else:
            text = raw.get("description") or ""
            describe = getattr(adapter, "describe", None)
            # Opening a posting is the only way to learn where it is, so a
            # link-read source is given more of them: without a location its
            # jobs are discarded above.
            budget = (MAX_LINK_DESCRIPTIONS if "careerpage" in method
                      else MAX_DESCRIPTIONS_PER_SOURCE)
            if (not text and describe and described < budget
                    and is_suitable(title, filters, extra)):
                described += 1
                try:
                    details = describe(net, params, raw)
                    text = details.get("description") or ""
                    for key in ("url", "location", "posted_at"):
                        if details.get(key) and not raw.get(key):
                            raw[key] = details[key]
                    url = raw.get("url") or url
                except Exception:
                    text = ""
            labels = language_labels(text)
            checked = labels is not None

        # A reader that lists links cannot see where a job is until the posting
        # itself is opened. Once the location is known, a foreign one must be
        # dropped, or a Bangkok vacancy sits in a list of Dutch openings.
        place = raw.get("location") or ""
        if place and not keep_location(place, is_strict(company)):
            continue
        # A company that hires across the world lists its foreign vacancies on
        # the same page as its Dutch ones. Reading that page as links tells us
        # nothing about where a job is, so an unconfirmed one must not be
        # presented as a Dutch opening. A Dutch-only site needs no such proof.
        if not place and "careerpage" in method and is_strict(company):
            continue

        jobs[job_id] = {
            "id": job_id,
            "company_id": company["id"],
            "title": title,
            "location": (raw.get("location") or "").strip(),
            "url": url,
            "posted_at": raw.get("posted_at"),
            "labels": labels,
            "labels_checked": checked,
        }
    return {"status": "ok", "method": method}, list(jobs.values())


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", help="company id to collect; others are carried over")
    args = parser.parse_args(argv)

    started = time.monotonic()
    today = date.today().isoformat()
    now = datetime.now(timezone.utc).isoformat(timespec="minutes")

    companies = load_json(COMPANIES_FILE, {"companies": []})["companies"]
    filters = load_json(FILTERS_FILE, {})
    previous_jobs = load_json(JOBS_FILE, {"jobs": []}).get("jobs", [])
    previous_sources = {s["id"]: s for s in load_json(SOURCES_FILE, {"sources": []}).get("sources", [])}
    discovery_cache = load_json(DISCOVERY_FILE, {})

    previous_by_id = {job["id"]: job for job in previous_jobs}
    previous_by_company = {}
    for job in previous_jobs:
        previous_by_company.setdefault(job["company_id"], []).append(job)

    net = Net()
    renderer = PageRenderer(RENDER_PAGE_BUDGET)
    all_jobs, sources = [], []

    for company in companies:
        cid = company["id"]
        before = previous_sources.get(cid, {})
        entry = {
            "id": cid,
            "name": company["name"],
            "entities": company.get("entities", []),
            "group": company["group"],
            "extra_keywords": company.get("extra_keywords", []),
            "last_success": before.get("last_success"),
            "checked_at": now,
        }

        over_budget = time.monotonic() - started > TIME_BUDGET_SECONDS
        if (args.only and cid != args.only) or over_budget:
            carried = dict(before) if before else dict(entry, status="not_checked_yet")
            carried.update({k: entry[k] for k in ("name", "entities", "group", "extra_keywords")})
            sources.append(carried)
            all_jobs.extend(previous_by_company.get(cid, []))
            continue

        status, jobs = collect_company(net, company, discovery_cache, filters,
                                       previous_by_id, today, renderer)
        entry.update(status)
        entry["careers_link"] = careers_link(company, discovery_cache)

        if jobs is None:
            kept = previous_by_company.get(cid, [])
            all_jobs.extend(kept)
            entry["nl_jobs"] = len(kept)
            entry["showing_old_data"] = bool(kept)
        else:
            first_success = not before.get("last_success")
            for job in jobs:
                previous = previous_by_id.get(job["id"])
                job["first_seen"] = previous["first_seen"] if previous else today
                job["baseline"] = previous["baseline"] if previous else first_success
            all_jobs.extend(jobs)
            entry["nl_jobs"] = len(jobs)
            entry["last_success"] = today
            if not jobs and not company.get("source"):
                # A guessed careers system that finds nothing may simply be the
                # wrong one, and a cached guess would never be reconsidered.
                # Forget it so the next run looks the company up again.
                discovery_cache.pop(cid, None)
                entry["retry_discovery"] = True
        sources.append(entry)
        print(f"{cid:<32} {entry['status']:<24} {entry.get('nl_jobs', 0):>4} jobs", flush=True)

    renderer.close()
    all_jobs.sort(key=lambda j: (j.get("posted_at") or j["first_seen"], j["first_seen"]), reverse=True)
    save_json(JOBS_FILE, {"generated_at": now, "jobs": all_jobs})
    save_json(SOURCES_FILE, {"generated_at": now, "sources": sources})
    save_json(DISCOVERY_FILE, discovery_cache)
    readable = sum(1 for s in sources if s.get("status") == "ok")
    print(f"Done: {readable} of {len(sources)} sources read, {len(all_jobs)} Dutch jobs stored.")


if __name__ == "__main__":
    main()
