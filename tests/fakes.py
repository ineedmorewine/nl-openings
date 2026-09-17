"""Offline stand-in for collector.net.Net."""
import json
from urllib.parse import urlencode


class FakeResponse:
    def __init__(self, body, url):
        self.url = url
        if isinstance(body, (dict, list)):
            self._json = body
            self.text = json.dumps(body)
        else:
            self._json = None
            self.text = body
        self.content = self.text.encode("utf-8")

    def json(self):
        return self._json if self._json is not None else json.loads(self.text)


class FakeNet:
    """Routes map 'URL' or 'URL?sorted-query' to a body. Missing routes raise like a 404."""

    def __init__(self, routes=None, posts=None):
        self.routes = routes or {}
        self.posts = posts or []   # list of (url, predicate(payload), body)
        self.calls = []

    @staticmethod
    def key(url, params=None):
        return url + ("?" + urlencode(sorted(params.items())) if params else "")

    def get(self, url, params=None):
        key = self.key(url, params)
        self.calls.append(key)
        for candidate in (key, url):
            if candidate in self.routes:
                return FakeResponse(self.routes[candidate], url)
        raise RuntimeError(f"404 for {key}")

    def post_json(self, url, payload):
        self.calls.append(("POST", url, json.dumps(payload, sort_keys=True)))
        for route_url, predicate, body in self.posts:
            if route_url == url and predicate(payload):
                return body
        raise RuntimeError(f"404 for POST {url}")
