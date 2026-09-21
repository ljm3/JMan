"""Website lookups: the carrier's public tracking page, loaded in a real Edge window."""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from datetime import datetime

from ... import config
from ... import models as M
from ..base import CarrierError, RateLimiter

log = logging.getLogger("tracking_checker")
SOURCE = "Carrier website"


class LazyBrowser:
    """Starts the shared BrowserWorker the first time a website lookup needs it."""

    def __init__(self, settings, attention=None, cancel: threading.Event | None = None):
        self.settings, self.attention, self.cancel = settings, attention, cancel
        self._worker = None
        self._lock = threading.Lock()
        self.limiter = RateLimiter(60.0 / max(0.5, float(settings.web_seconds_between)))

    def get(self):
        with self._lock:
            if self._worker is None:
                from .browser import BrowserWorker
                if self.attention:
                    self.attention("Starting the browser for website lookups ...")
                self._worker = BrowserWorker(self.settings, self.attention, self.cancel)
            return self._worker

    def close(self):
        with self._lock:
            if self._worker is not None:
                self._worker.close()
                self._worker = None


class WebCarrier:
    name = ""
    batch_size = 1

    def __init__(self, browser: LazyBrowser, settings, capture_pod: bool = True,
                 cancel: threading.Event | None = None):
        self.browser = browser
        self.settings = settings
        self.capture_pod = capture_pod
        self.cancel = cancel or threading.Event()
        self.blocked_reason = ""
        self.timeout_ms = int(settings.web_timeout_seconds) * 1000

    source_label = SOURCE

    def track_many(self, numbers):
        out = {}
        for n in numbers:
            try:
                out[n] = self.track(n)
            except Exception as e:  # noqa: BLE001 - reported per number
                out[n] = e
        return out

    def track(self, number: str) -> M.TrackingResult:
        from .browser import Blocked
        if self.blocked_reason:
            raise Blocked(self.blocked_reason)
        self.browser.limiter.wait(self.cancel)
        worker = self.browser.get()
        try:
            return worker.run(lambda w: self._with_page(w, number), timeout=self.timeout_ms / 1000 + 300)
        except Blocked as e:
            self.blocked_reason = str(e)
            raise

    def _with_page(self, w, number):
        page = w.new_page()
        try:
            return self.lookup(w, page, number)
        finally:
            try:
                page.close()
            except Exception:  # noqa: BLE001
                pass

    def lookup(self, w, page, number) -> M.TrackingResult:
        raise NotImplementedError

    def fetch_pod(self, result):
        """POD is captured while the tracking page is open (see lookup)."""

    # ------------------------------------------------------------ helpers
    def load_and_capture(self, w, page, url: str, match, site: str):
        """Open url and return the first response matching match(url, method).

        Raises Blocked when the site refuses the request outright, and gives the user a chance to clear
        a human-verification page (visible mode) before retrying once.
        """
        from .browser import Blocked
        for attempt in (1, 2):
            got, failed = [], []
            h1 = lambda r: got.append(r) if match(r.url, r.request.method) else None  # noqa: E731
            h2 = lambda r: failed.append(r.failure) if match(r.url, r.method) else None  # noqa: E731
            page.on("response", h1)
            page.on("requestfailed", h2)
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
                end = time.time() + self.timeout_ms / 1000
                while time.time() < end and not got and not failed:
                    if self.cancel.is_set():
                        raise CarrierError("Cancelled")
                    page.wait_for_timeout(250)
            finally:
                page.remove_listener("response", h1)
                page.remove_listener("requestfailed", h2)
            if got:
                return got[0]
            if attempt == 1 and w.needs_human(page):
                w.wait_for_human(page, site)
                continue
            if failed:
                raise Blocked(f"{site}'s website refused the automated lookup ({failed[0]}).")
            snapshot(page, self.name, url, "no tracking data")
            raise CarrierError(f"{site}'s website didn't return tracking data within "
                               f"{self.timeout_ms // 1000}s (debug snapshot saved in the logs folder)")
        raise CarrierError(f"{site}: no data")


def snapshot(page, carrier: str, number_or_url: str, why: str, data=None) -> None:
    """Save HTML + screenshot (+ captured data) so a changed page layout can be diagnosed."""
    try:
        d = config.workspace_dir() / "logs" / "web-debug"
        d.mkdir(parents=True, exist_ok=True)
        stem = f"{carrier}_{re.sub(r'[^A-Za-z0-9]', '', number_or_url)[-24:]}_{datetime.now():%Y%m%d-%H%M%S}"
        (d / f"{stem}.html").write_text(page.content(), encoding="utf-8")
        page.screenshot(path=str(d / f"{stem}.png"), full_page=True)
        if data is not None:
            (d / f"{stem}.json").write_text(json.dumps(data, indent=1, default=str)[:2_000_000], encoding="utf-8")
        log.warning("%s page: %s - debug snapshot saved: %s", carrier, why, d / stem)
    except Exception as e:  # noqa: BLE001
        log.info("Couldn't save debug snapshot: %s", e)
