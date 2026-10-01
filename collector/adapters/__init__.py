"""Careers-system adapters.

Each entry in ADAPTERS exposes:
  fetch(net, params, strict) -> list of job dicts with keys
      ext_id, title, location, url, posted_at (YYYY-MM-DD or None), description (optional)
  describe(net, params, job) -> dict with description and optional url/location/posted_at
      (only for adapters whose listing does not include the ad text)
Only Dutch jobs are returned; 'strict' means unknown locations are dropped.
"""
from types import SimpleNamespace

from . import (amazon, greenhouse, jsonld, lever, personio, recruitee, rmk, smartrecruiters,
               teamtailor, vendors, workable, workday)

ADAPTERS = {
    "amazon": amazon,
    "greenhouse": greenhouse,
    "icims": SimpleNamespace(fetch=vendors.icims_fetch, describe=vendors.icims_describe),
    "jsonld": jsonld,
    "lever": lever,
    "oracle": SimpleNamespace(fetch=vendors.oracle_fetch),
    "personio": personio,
    "phenom": SimpleNamespace(fetch=vendors.phenom_fetch),
    "recruitee": recruitee,
    "rmk": rmk,
    "smartrecruiters": smartrecruiters,
    "teamtailor": teamtailor,
    "workable": workable,
    "workday": workday,
}
