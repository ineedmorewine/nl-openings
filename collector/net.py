"""Polite HTTP client shared by every adapter.

Waits at least one second between requests to the same host and identifies
itself honestly in the User-Agent header.
"""
import time
from urllib.parse import urlparse

import requests

USER_AGENT = "nl-openings/1.0 (personal job tracker, max one request per second per host)"
TIMEOUT_SECONDS = 25
MIN_INTERVAL_SECONDS = 1.0


class Net:
    def __init__(self, session=None, sleep=time.sleep, clock=time.monotonic):
        self.session = session or requests.Session()
        self.session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept-Language": "en,nl;q=0.8",
        })
        self._last_request_at = {}
        self._sleep = sleep
        self._clock = clock

    def _wait_for_host(self, url):
        host = urlparse(url).netloc
        last = self._last_request_at.get(host)
        if last is not None:
            elapsed = self._clock() - last
            if elapsed < MIN_INTERVAL_SECONDS:
                self._sleep(MIN_INTERVAL_SECONDS - elapsed)
        self._last_request_at[host] = self._clock()

    def get(self, url, params=None):
        self._wait_for_host(url)
        response = self.session.get(url, params=params, timeout=TIMEOUT_SECONDS)
        response.raise_for_status()
        return response

    def post_json(self, url, payload):
        self._wait_for_host(url)
        response = self.session.post(
            url,
            json=payload,
            timeout=TIMEOUT_SECONDS,
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()
        return response.json()
