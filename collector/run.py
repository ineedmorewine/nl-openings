"""Daily collection run.

Usage:
  python -m collector.run                 # all companies
  python -m collector.run --only dsv      # one company, everything else carried over

Writes docs/data/jobs.json, docs/data/sources.json and docs/data/discovery.json.
A company whose site cannot be read today keeps its previous jobs, so a bad day
never wipes the list or makes old jobs look new tomorrow.
"""
import argparse
import json
import time
from datetime import date, datetime, timezone
from pathlib import Path

from .adapters import ADAPTERS
from .discovery import discover
from .filters import is_suitable
from .net import Net
from .text import dutch_required, written_in_dutch

ROOT = Path(__file__).resolve().parent.parent
COMPANIES_FILE = ROOT / "config" / "companies.json"
DATA_DIR = ROOT / "docs" / "data"
JOBS_FILE = DATA_DIR / "jobs.json"
SOURCES_FILE = DATA_DIR / "sources.json"
DISCOVERY_FILE = DATA_DIR / "discovery.json"
FILTERS_FILE = DATA_DIR / "filters.json"

MAX_DESCRIPTIONS_PER_SOURCE = 60
TIME_BUDGET_SECONDS = 70 * 60


def load_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def is_strict(company):
    """Global careers sites keep only clearly Dutch locations; Dutch-only sites keep unknowns too."""
    if company.get("scope"):
        return company["scope"] != "nl"
    domain = company.get("domain") or ""
    return not domain.endswith(".nl")


def make_job_id(company_id, raw):
    return f"{company_id}:{raw.get('ext_id') or raw.get('url')}"


def language_labels(text):
    if not text:
        return None
    return {"dutch_required": dutch_required(text), "written_in_dutch": written_in_dutch(text)}


def resolve_source(net, company, discovery_cache, today):
    """Return (source, discovered, failure_status). failure_status is None when a source exists."""
    if company.get("source"):
        return company["source"], False, None
    cached = discovery_cache.get(company["id"])
    if cached and cached.get("source"):
        return cached["source"], True, None
    if not company.get("domain") and not company.get("careers_url"):
        return None, False, {"status": "no_address"}
    result = discover(net, company)
    discovery_cache[company["id"]] = dict(result, checked_at=today)
    if result["source"]:
        return result["source"], True, None
    return None, True, {"status": result["reason"], "vendor": result.get("vendor")}


def collect_company(net, company, discovery_cache, filters, previous_by_id, today):
    """Return (status_entry, jobs). jobs is None when the company could not be read today."""
    source, discovered, failure = resolve_source(net, company, discovery_cache, today)
    if failure:
        return failure, None

    adapter = ADAPTERS.get(source.get("type"))
    if adapter is None:
        return {"status": "read_failed", "detail": f"unknown source type {source.get('type')}"}, None
    params = {key: value for key, value in source.items() if key != "type"}

    try:
        raw_jobs = adapter.fetch(net, params, is_strict(company))
    except Exception as error:
        if discovered:
            # Forget the cached guess so tomorrow's run looks for the careers system again.
            discovery_cache.pop(company["id"], None)
        return {"status": "read_failed", "method": source["type"],
                "detail": f"{type(error).__name__}: {str(error)[:160]}"}, None

    jobs, described = {}, 0
    extra = company.get("extra_keywords") or []
    for raw in raw_jobs:
        url = (raw.get("url") or "").strip()
        title = (raw.get("title") or "").strip()
        if not title or not url.startswith("http"):
            continue
        job_id = make_job_id(company["id"], raw)
        previous = previous_by_id.get(job_id)

        if previous and previous.get("labels_checked"):
            labels, checked = previous.get("labels"), True
        else:
            text = raw.get("description") or ""
            describe = getattr(adapter, "describe", None)
            if (not text and describe and described < MAX_DESCRIPTIONS_PER_SOURCE
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
    return {"status": "ok", "method": source["type"]}, list(jobs.values())


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

        status, jobs = collect_company(net, company, discovery_cache, filters, previous_by_id, today)
        entry.update(status)

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
        sources.append(entry)
        print(f"{cid:<32} {entry['status']:<24} {entry.get('nl_jobs', 0):>4} jobs", flush=True)

    all_jobs.sort(key=lambda j: (j.get("posted_at") or j["first_seen"], j["first_seen"]), reverse=True)
    save_json(JOBS_FILE, {"generated_at": now, "jobs": all_jobs})
    save_json(SOURCES_FILE, {"generated_at": now, "sources": sources})
    save_json(DISCOVERY_FILE, discovery_cache)
    readable = sum(1 for s in sources if s.get("status") == "ok")
    print(f"Done: {readable} of {len(sources)} sources read, {len(all_jobs)} Dutch jobs stored.")


if __name__ == "__main__":
    main()
