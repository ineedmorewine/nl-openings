"""Vacancies from the Welcome to NL job board.

A board run for people moving to the Netherlands, so every employer on it is
already looking outside the country. It is kept apart from the companies on the
IND sponsor list: a different source, listed on its own.

The board builds its list in the browser, so it is read with one, and each card
carries the employer, the job title and the place. The cards hold no address of
their own, so each job points back at the board with its title as the search.
"""
import re
from urllib.parse import quote

from .filters import is_suitable
from .render import search_board
from .text import keep_location

BOARD_URL = "https://www.welcome-to-nl.nl/jobs/"
# The board holds thousands of jobs and shows eighteen at a time, so reading
# its front page finds whatever is newest, which is rarely logistics. Her own
# words are typed into its search box instead, one at a time.
SEARCHES = ["transport planner", "forwarder", "expediteur", "logistics coordinator",
            "transport coordinator", "supply chain", "freight"]
PAGES = len(SEARCHES)
MAX_JOBS = 120

_CARD = re.compile(r'<div class="job">(.*?)(?=<div class="job">|$)', re.S)
_COMPANY = re.compile(r'class="job-company"[^>]*>\s*<span>(.*?)</span>', re.S)
_TITLE = re.compile(r'class="h6 job-title"[^>]*>\s*<div>(.*?)</div>', re.S)
_PLACE = re.compile(r'<span class="tag">(.*?)</span>', re.S)
_TAGS = re.compile(r"<[^>]+>")


def _text(match):
    if not match:
        return ""
    return re.sub(r"\s+", " ", _TAGS.sub("", match.group(1))).strip()


def read_cards(markup):
    """The jobs on one page of the board, as (company, title, place)."""
    found = []
    for card in _CARD.findall(markup or ""):
        company, title, place = _text(_COMPANY.search(card)), _text(_TITLE.search(card)), _text(_PLACE.search(card))
        if company and title:
            found.append((company, title, place))
    return found


def collect(renderer, filters, today):
    """Search the board for her work and keep the Dutch jobs that suit her.

    Returns [] rather than raising when no browser is available: the board is a
    second source, and the day's collection must not fail over it.
    """
    jobs, seen = [], set()
    for query in SEARCHES:
        try:
            markup = search_board(renderer, BOARD_URL, query)
        except Exception:
            continue
        for company, title, place in read_cards(markup):
            key = (company.lower(), title.lower())
            if key in seen:
                continue
            seen.add(key)
            if not is_suitable(title, filters):
                continue
            if place and not keep_location(place, True):
                continue
            jobs.append({
                "id": f"welcome-to-nl:{company}:{title}".lower(),
                "company": company,
                "title": title,
                "location": place,
                "url": f"{BOARD_URL}?search={quote(title)}",
                "first_seen": today,
            })
    return jobs[:MAX_JOBS]


def merge(previous, found, today):
    """Keep the day a job was first seen, so the board's list can be ordered."""
    was = {job["id"]: job.get("first_seen", today) for job in previous or []}
    for job in found:
        job["first_seen"] = was.get(job["id"], today)
    return sorted(found, key=lambda job: job["first_seen"], reverse=True)
