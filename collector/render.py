"""Open a page in a real browser so its vacancies load before we read it.

Most careers pages that defeat the plain reader build their list after the page
arrives, so what we download is an empty shell. Rendering is the last thing
tried, for the companies that would otherwise be reported as unreadable, and it
is bounded: a page budget per run and a short timeout each.

Playwright is imported only when a page is actually rendered, so the rest of the
collector, and its tests, run without a browser installed.
"""
import os

DEFAULT_TIMEOUT_MS = 15000
# Waiting for the network to fall quiet is what lets a job list appear.
QUIET_NETWORK_MS = 2500
DISABLED = os.environ.get("NL_OPENINGS_NO_BROWSER") == "1"


class BrowserUnavailable(RuntimeError):
    """Raised when no browser can be started, so callers can carry on without one."""


class PageRenderer:
    """Renders pages one at a time, reusing a single browser for the whole run."""

    MAX_LAUNCH_ATTEMPTS = 3

    def __init__(self, budget, timeout_ms=DEFAULT_TIMEOUT_MS):
        self.budget = 0 if DISABLED else budget
        self.timeout_ms = timeout_ms
        self._browser = None
        self._playwright = None
        self._unavailable = DISABLED
        self._launch_failures = 0
        # Why rendering did or did not work, for the run's report.
        self.last_error = "disabled" if DISABLED else None
        self.pages_rendered = 0

    @property
    def exhausted(self):
        return self._unavailable or self.budget <= 0

    def _start(self):
        if self._browser is not None:
            return
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as error:
            self._unavailable = True
            self.last_error = "playwright is not installed"
            raise BrowserUnavailable(self.last_error) from error
        try:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(args=["--disable-dev-shm-usage"])
        except Exception as error:
            self._launch_failures += 1
            self.last_error = f"{type(error).__name__}: {str(error)[:140]}"
            self._playwright = self._browser = None
            if self._launch_failures >= self.MAX_LAUNCH_ATTEMPTS:
                self._unavailable = True
            raise BrowserUnavailable(self.last_error) from error

    def render(self, url):
        """Return the page's HTML once its scripts have run. Costs one page of budget."""
        if self.exhausted:
            raise BrowserUnavailable("no rendering budget left")
        self._start()
        self.budget -= 1
        self.pages_rendered += 1
        page = self._browser.new_page(
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
            locale="nl-NL",
        )
        try:
            page.goto(url, timeout=self.timeout_ms, wait_until="domcontentloaded")
            try:
                page.wait_for_load_state("networkidle", timeout=QUIET_NETWORK_MS)
            except Exception:
                pass  # A page that never goes quiet may still have its list by now.
            if accept_cookies(page):
                try:
                    page.wait_for_load_state("networkidle", timeout=QUIET_NETWORK_MS)
                except Exception:
                    pass
            return page.content()
        finally:
            page.close()

    def close(self):
        for part in (self._browser, self._playwright):
            try:
                if part is not None:
                    (part.close if hasattr(part, "close") else part.stop)()
            except Exception:
                pass
        self._browser = self._playwright = None


# A consent wall is not decoration: on some careers pages the vacancy list is
# not built until it is answered, so what we read is the cookie notice itself.
# ECT's vacancy page is all consent text and no vacancies until this is clicked.
CONSENT_LABELS = ["Alles accepteren", "Alle cookies accepteren", "Accepteren", "Akkoord",
                  "Ik ga akkoord", "Accept all", "Accept All Cookies", "Allow all",
                  "I agree", "Zustimmen", "Alle akzeptieren"]
CONSENT_CLICK_MS = 1200


def accept_cookies(page):
    """Answer a cookie notice if one is in the way. True when something was clicked.

    The notice is often inside a frame of its own, so every frame is tried.
    Failure is ordinary and silent: most pages have nothing to click.
    """
    for frame in [page] + list(getattr(page, "frames", []) or []):
        for label in CONSENT_LABELS:
            try:
                button = frame.get_by_role("button", name=label, exact=False)
                if button.count() == 0:
                    continue
                button.first.click(timeout=CONSENT_CLICK_MS)
                return True
            except Exception:
                continue
    return False
