"""One Edge browser, driven from a single dedicated thread (Playwright's sync API is not thread-safe).

The browser is an ordinary Edge window using its own profile folder, so sign-ins to ups.com / fedex.com
persist between runs. Nothing here hides the automation from the websites: if a site asks the visitor
to prove they are human, the run pauses and the user completes the check in the window.
"""
from __future__ import annotations

import logging
import queue
import re
import threading
import time
from concurrent.futures import Future

from ... import config
from ..base import CarrierError

log = logging.getLogger("tracking_checker")

_HUMAN_CHECK = re.compile(r"verify (that )?you are (a )?human|are you a robot|press (&|and) hold|security check|"
                          r"unusual (traffic|activity)|access denied|request unsuccessful|pardon our interruption|"
                          r"complete the (security )?check|captcha", re.I)


# Chat assistants, cookie bars and other floating panels cover the tracking details in the saved PDF.
_HIDE_OVERLAYS = r"""() => {
  const hide = el => el.style.setProperty("display", "none", "important");
  document.querySelectorAll("#onetrust-consent-sdk, #onetrust-banner-sdk, iframe[title*='chat' i]").forEach(hide);
  for (const el of document.querySelectorAll("body *")) {
    const cs = getComputedStyle(el);
    if ((cs.position === "fixed" || cs.position === "sticky") && el.tagName !== "HEADER") hide(el);
  }
}"""


class Blocked(CarrierError):
    """The website refused an automated lookup."""


class BrowserUnavailable(CarrierError):
    pass


class BrowserWorker:
    def __init__(self, settings, attention=None, cancel: threading.Event | None = None):
        self.settings = settings
        self.attention = attention or (lambda msg: None)
        self.cancel = cancel or threading.Event()
        self._q: queue.Queue = queue.Queue()
        self._thread = threading.Thread(target=self._main, name="tracking-browser", daemon=True)
        self._started = threading.Event()
        self._start_error: Exception | None = None
        self.ctx = None
        self._thread.start()
        self._started.wait(120)
        if self._start_error:
            raise self._start_error

    @property
    def visible(self) -> bool:
        return bool(self.settings.web_show_browser)

    # ------------------------------------------------------------ thread
    def _main(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self._start_error = BrowserUnavailable("Website lookups need the 'playwright' package - re-run setup.ps1.")
            self._started.set()
            return
        try:
            with sync_playwright() as p:
                self.ctx = launch(p, self.visible)
                self._started.set()
                while True:
                    job = self._q.get()
                    if job is None:
                        break
                    fn, fut = job
                    if fut.set_running_or_notify_cancel():
                        try:
                            fut.set_result(fn(self))
                        except BaseException as e:  # noqa: BLE001 - hand every failure back to the caller
                            fut.set_exception(e)
                try:
                    self.ctx.close()
                except Exception:  # noqa: BLE001
                    pass
        except Exception as e:  # noqa: BLE001
            if not self._started.is_set():
                self._start_error = BrowserUnavailable(f"Couldn't start the browser: {e}")
                self._started.set()
            else:
                log.warning("Browser stopped: %s", e)

    # ------------------------------------------------------------ API used by carriers
    def run(self, fn, timeout: float = 600):
        """Run fn(worker) on the browser thread and return its result."""
        if self.cancel.is_set():
            raise CarrierError("Cancelled")
        fut: Future = Future()
        self._q.put((fn, fut))
        return fut.result(timeout=timeout)

    def close(self):
        self._q.put(None)
        self._thread.join(timeout=30)

    def new_page(self):
        return self.ctx.new_page()

    def needs_human(self, page) -> bool:
        try:
            if page.locator("iframe[src*='captcha'], iframe[title*='challenge' i], #px-captcha").count():
                return True
            txt = page.inner_text("body", timeout=5000)[:4000]
            if not txt.strip() and not page.title().strip():
                return True          # blank security-check page (Akamai interstitial)
        except Exception:  # noqa: BLE001
            return False
        return bool(_HUMAN_CHECK.search(txt)) and len(txt) < 3000

    def wait_for_human(self, page, site: str, done=None, limit: float = 180, reload_every: float = 30) -> bool:
        """Visible mode: ask the user to clear the site's check, then wait until the page is usable."""
        if not self.visible:
            raise Blocked(f"{site} asked for a human-verification check. Turn on 'Show the browser window' in "
                          "Settings -> Website lookups so you can complete it.")
        try:
            page.bring_to_front()
        except Exception:  # noqa: BLE001
            pass
        self.attention(f"{site} is showing a security check - complete it in the browser window, or press F5 there "
                       f"if the page stays blank (waiting up to {int(limit // 60)} min) ...")
        end = time.time() + limit
        next_reload = time.time() + reload_every
        while time.time() < end:
            if self.cancel.is_set():
                raise CarrierError("Cancelled")
            page.wait_for_timeout(2000)
            if (done and done()) or (done is None and not self.needs_human(page)):
                self.attention(f"{site} check cleared - continuing.")
                return True
            if reload_every and time.time() >= next_reload and self.needs_human(page):
                try:
                    page.reload(wait_until="domcontentloaded")
                except Exception:  # noqa: BLE001
                    pass
                next_reload = time.time() + reload_every
        raise Blocked(f"{site} verification wasn't completed in time.")

    def pdf(self, page) -> bytes | None:
        try:
            page.evaluate(_HIDE_OVERLAYS)
        except Exception:  # noqa: BLE001
            pass
        try:
            return page.pdf(print_background=True, format="Letter",
                            margin={"top": "0.4in", "bottom": "0.4in", "left": "0.4in", "right": "0.4in"})
        except Exception as e:  # noqa: BLE001
            log.info("PDF capture failed (%s); using a screenshot instead", e)
            try:
                return page.screenshot(full_page=True)
            except Exception:  # noqa: BLE001
                return None


def launch(p, visible: bool):
    """Start Edge (then Chrome, then Playwright's bundled Chromium) on the app's own profile."""
    profile = str(config.browser_profile_dir())
    opts = dict(headless=not visible, viewport={"width": 1280, "height": 900}, accept_downloads=True,
                args=["--hide-crash-restore-bubble"])
    errors = []
    for channel in ("msedge", "chrome", None):
        try:
            kw = {"channel": channel} if channel else {}
            ctx = p.chromium.launch_persistent_context(profile, **kw, **opts)
            _start_clean(ctx)
            log.info("Browser started (%s, %s)", channel or "bundled Chromium", "visible" if visible else "hidden")
            return ctx
        except Exception as e:  # noqa: BLE001
            msg = str(e).splitlines()[0] if str(e) else repr(e)
            if "already in use" in str(e).lower() or "ProcessSingleton" in str(e):
                raise BrowserUnavailable("The Tracking Check browser is already open (sign-in window?). "
                                         "Close it and try again.")
            errors.append(f"{channel or 'bundled'}: {msg}")
    raise BrowserUnavailable("No usable browser. Microsoft Edge normally works; details: " + " | ".join(errors))


def _start_clean(ctx) -> None:
    """Edge may restore tabs from a previous session that ended abruptly; start from one blank tab."""
    try:
        for pg in ctx.pages[1:]:
            pg.close()
        if ctx.pages and ctx.pages[0].url not in ("about:blank", ""):
            ctx.pages[0].goto("about:blank")
    except Exception:  # noqa: BLE001
        pass


def open_for_sign_in(urls: list[str], on_status=None) -> None:
    """Open the app's browser profile on the given sign-in pages and wait until the user closes the window."""
    from playwright.sync_api import sync_playwright

    say = on_status or (lambda m: None)
    with sync_playwright() as p:
        ctx = launch(p, visible=True)
        closed = threading.Event()
        ctx.on("close", lambda _: closed.set())
        say("Opening the sign-in pages ...")
        pages = list(ctx.pages)
        for i, u in enumerate(urls):
            page = pages[0] if i == 0 and pages else ctx.new_page()
            try:
                page.goto(u, wait_until="commit", timeout=30000)
            except Exception as e:  # noqa: BLE001 - the user can still navigate by hand
                log.info("Sign-in page %s didn't open: %s", u, e)
        say("Sign in on each tab, then close the browser window.")
        while not closed.is_set():
            try:
                if not ctx.pages:
                    break
                ctx.pages[0].wait_for_timeout(1000)
            except Exception:  # noqa: BLE001
                break
        try:
            ctx.close()
        except Exception:  # noqa: BLE001
            pass
        say("Sign-in window closed - your sign-ins are saved for website lookups.")
