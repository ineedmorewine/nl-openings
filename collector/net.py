"""Polite HTTP client shared by every adapter.

Waits at least one second between requests to the same host. Sends the headers
an ordinary browser sends, because many careers sites reject requests without
them and that made whole companies look unreachable.
"""
import time
from urllib.parse import urlparse

import requests

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
BROWSER_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,nl;q=0.8",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
}
TIMEOUT_SECONDS = 30
MIN_INTERVAL_SECONDS = 1.0
RETRY_STATUS = {429, 500, 502, 503, 504}
RETRIES = 2


class Net:
    def __init__(self, session=None, sleep=time.sleep, clock=time.monotonic):
        self.session = session or requests.Session()
        self.session.headers.update(BROWSER_HEADERS)
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

    def get(self, url, params=None, headers=None):
        last_error = None
        for attempt in range(RETRIES + 1):
            self._wait_for_host(url)
            try:
                response = self.session.get(url, params=params, headers=headers,
                                            timeout=TIMEOUT_SECONDS)
            except requests.RequestException as error:
                last_error = error
                if attempt == RETRIES:
                    raise
                self._sleep(2 * (attempt + 1))
                continue
            if response.status_code in RETRY_STATUS and attempt < RETRIES:
                self._sleep(2 * (attempt + 1))
                continue
            response.raise_for_status()
            return response
        raise last_error

    def get_json(self, url, params=None):
        """GET a JSON endpoint, asking for JSON rather than HTML."""
        return self.get(url, params=params, headers={"Accept": "application/json"}).json()

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
