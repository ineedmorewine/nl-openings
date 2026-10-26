"""Title filter. Mirrors docs/filters.js exactly; tests/filter_cases.json keeps them in sync.

Matching rule: a keyword of 6 or more characters matches anywhere in the title
(so 'planner' finds 'Transportplanner'). A shorter keyword must start a word
(so 'rail' finds 'Rail Planner' but not 'Retail').
"""
import re

from .text import fold

LONG_KEYWORD = 6


def keyword_matches(folded_title, keyword):
    kw = fold(keyword).strip()
    if not kw:
        return False
    if len(kw) >= LONG_KEYWORD:
        return kw in folded_title
    return re.search(r"(^|[^a-z0-9])" + re.escape(kw), folded_title) is not None


def any_match(title, keywords):
    folded = fold(title)
    return any(keyword_matches(folded, k) for k in keywords)


def is_suitable(title, filters, extra_include=()):
    include = list(filters.get("include", [])) + list(extra_include)
    return any_match(title, include) and not any_match(title, filters.get("exclude", []))


def is_air_or_ocean_only(title, filters):
    return any_match(title, filters.get("air_ocean", [])) and not any_match(title, filters.get("land", []))


def is_worth_reading(title, filters, extra_include=()):
    """Whether a posting should be opened and read, which is a wider net than the list.

    The excluded words are a matter of taste and are edited in the browser, so a
    job this run would leave out may be one she has chosen to see. Opening it
    costs one page and is what gives the posting its language and location
    labels, so inclusion alone decides.
    """
    include = list(filters.get("include", [])) + list(extra_include)
    return any_match(title, include)
