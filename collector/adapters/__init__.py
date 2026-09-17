"""Careers-system adapters.

Each module exposes:
  fetch(net, params, strict) -> list of job dicts with keys
      ext_id, title, location, url, posted_at (YYYY-MM-DD or None), description (optional)
  describe(net, params, job) -> dict with description and optional url/location/posted_at
      (only for adapters whose listing does not include the ad text)
Only Dutch jobs are returned; 'strict' means unknown locations are dropped.
"""
from . import (amazon, greenhouse, jsonld, lever, personio, recruitee, rmk, smartrecruiters,
               teamtailor, workable, workday)

ADAPTERS = {
    "amazon": amazon,
    "greenhouse": greenhouse,
    "jsonld": jsonld,
    "lever": lever,
    "personio": personio,
    "recruitee": recruitee,
    "rmk": rmk,
    "smartrecruiters": smartrecruiters,
    "teamtailor": teamtailor,
    "workable": workable,
    "workday": workday,
}
