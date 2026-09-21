from __future__ import annotations

import logging
import re
import threading
import time
from datetime import datetime

from .. import models as M

log = logging.getLogger("tracking_checker")


class CarrierError(Exception):
    """A request failed in a way that should be shown in the Error column."""


class NotFound(CarrierError):
    """The carrier has no record of this number."""


class NotConfigured(CarrierError):
    pass


class RateLimiter:
    def __init__(self, per_minute: float):
        self.interval = 60.0 / per_minute if per_minute and per_minute > 0 else 0.0
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self, cancel: threading.Event | None = None) -> None:
        if not self.interval:
            return
        with self._lock:
            now = time.monotonic()
            slot = max(now, self._next)
            self._next = slot + self.interval
        delay = slot - time.monotonic()
        while delay > 0:
            if cancel is not None and cancel.is_set():
                raise CarrierError("Cancelled")
            time.sleep(min(delay, 0.5))
            delay = slot - time.monotonic()


def make_session():
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry

    s = requests.Session()
    retry = Retry(total=4, backoff_factor=1.5, status_forcelist=[429, 500, 502, 503, 504],
                  allowed_methods=frozenset({"GET", "POST"}), respect_retry_after_header=True,
                  raise_on_status=False)
    s.mount("https://", HTTPAdapter(max_retries=retry, pool_maxsize=8))
    s.headers["User-Agent"] = "TrackingCheck/1.0"
    return s


class Carrier:
    name = ""
    batch_size = 1

    def __init__(self, client_id: str, client_secret: str, settings, cancel: threading.Event | None = None):
        if not (client_id and client_secret):
            raise NotConfigured(f"No {self.name} API credentials - add them in Settings.")
        self.client_id = client_id
        self.client_secret = client_secret
        self.settings = settings          # CarrierSettings
        self.cancel = cancel
        self.session = make_session()
        self.limiter = RateLimiter(settings.requests_per_minute)
        self._token = ""
        self._token_exp = 0.0
        self._token_lock = threading.Lock()

    @property
    def sandbox(self) -> bool:
        return self.settings.environment == "sandbox"

    @property
    def source_label(self) -> str:
        return "Carrier sandbox (test data)" if self.sandbox else "Live API"

    # ------------------------------------------------------------ auth
    def token(self, force: bool = False) -> str:
        with self._token_lock:
            if force or not self._token or time.time() > self._token_exp - 60:
                self._token, ttl = self._fetch_token()
                self._token_exp = time.time() + ttl
            return self._token

    def _fetch_token(self) -> tuple[str, float]:
        raise NotImplementedError

    def test_connection(self) -> str:
        self.token(force=True)
        return f"{self.name}: credentials accepted ({self.settings.environment})."

    # ------------------------------------------------------------ tracking
    def track_many(self, numbers: list[str]) -> dict[str, M.TrackingResult | Exception]:
        out: dict[str, M.TrackingResult | Exception] = {}
        for n in numbers:
            try:
                out[n] = self.track(n)
            except Exception as e:  # noqa: BLE001 - per-number errors are reported in the sheet
                out[n] = e
        return out

    def track(self, number: str) -> M.TrackingResult:
        raise NotImplementedError

    def fetch_pod(self, result: M.TrackingResult) -> None:
        """Populate result.pod with carrier-issued documents where the API offers them."""

    def _request(self, method: str, url: str, **kw):
        self.limiter.wait(self.cancel)
        kw.setdefault("timeout", 45)
        headers = kw.pop("headers", {})
        for attempt in (1, 2):
            h = dict(headers)
            h["Authorization"] = f"Bearer {self.token(force=attempt == 2)}"
            r = self.session.request(method, url, headers=h, **kw)
            if r.status_code == 401 and attempt == 1:
                continue
            return r
        return r

    @staticmethod
    def tracking_url(number: str) -> str:
        return ""


# ---------------------------------------------------------------- helpers
def normalize_status(text: str, code: str = "") -> str:
    """Map carrier status wording onto one of the models.* status categories."""
    t = (text or "").lower()
    c = (code or "").upper()
    if c in {"DL", "D", "DELIVERED"}:
        return M.DELIVERED
    if c in {"OD", "OUT_FOR_DELIVERY"}:
        return M.OUT_FOR_DELIVERY
    if c in {"RS", "RT", "RETURN_TO_SENDER"}:
        return M.RETURNED
    if c in {"DE", "SE", "CA", "X", "EXCEPTION"}:
        return M.EXCEPTION
    if c in {"OC", "M", "MV", "PRE-SHIPMENT", "LABEL_CREATED"}:
        return M.LABEL_CREATED
    if not t:
        return M.IN_TRANSIT if c in {"IT", "I", "PU", "P", "AR", "DP"} else M.UNKNOWN
    if re.search(r"return(ed|ing)? to (sender|shipper)|being returned|returned", t):
        return M.RETURNED
    if "out for delivery" in t or "on vehicle for delivery" in t or "on fedex vehicle" in t:
        return M.OUT_FOR_DELIVERY
    if re.search(r"available for pickup|ready for pickup|held at|hold at location|at (a|the) (ups access point|post office)", t) \
            and "delivered" not in t:
        return M.PICKUP_READY
    if re.search(r"\bdelivered\b", t) and not re.search(r"not delivered|undeliver|delivery attempt|attempted", t):
        return M.DELIVERED
    if re.search(r"exception|delay|unable|attempt|damag|refused|insufficient address|incorrect address|missort|"
                 r"alert|clearance|cancel|lost|no access|business closed", t):
        return M.EXCEPTION
    if re.search(r"label created|shipment information sent|pre-shipment|pre-transit|shipping label|order processed|"
                 r"awaiting item|billing information received|shipment ready", t):
        return M.LABEL_CREATED
    if re.search(r"transit|arriv|depart|picked up|pickup|accept|process|origin scan|destination|facility|"
                 r"on the way|moving|left|in possession|tendered|dropped off|scan", t):
        return M.IN_TRANSIT
    return M.UNKNOWN


def place(city="", state="", zip_="", country="") -> str:
    city = (city or "").strip().title()
    state = (state or "").strip().upper()
    parts = [p for p in [city, " ".join(x for x in [state, (zip_ or "").strip()] if x)] if p]
    s = ", ".join(parts)
    country = (country or "").strip().upper()
    if country and country != "US":
        s = f"{s}, {country}" if s else country
    return s


def parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    s = str(s).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%m/%d/%Y %H:%M", "%m/%d/%Y"):
            try:
                dt = datetime.strptime(s[: len(fmt) + 4], fmt)
                break
            except ValueError:
                continue
        else:
            return None
    # Carriers report the local time where the scan happened; keep that wall-clock time.
    return dt.replace(tzinfo=None)


def error_text(resp) -> str:
    try:
        j = resp.json()
    except ValueError:
        return f"HTTP {resp.status_code}: {resp.text[:200]}"
    msgs = []

    def walk(o):
        if isinstance(o, dict):
            for k in ("message", "description", "detail", "error_description"):
                if isinstance(o.get(k), str):
                    msgs.append(o[k])
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(j)
    seen = []
    for m in msgs:
        if m not in seen:
            seen.append(m)
    return f"HTTP {resp.status_code}: " + ("; ".join(seen[:3]) if seen else str(j)[:200])


_NOT_FOUND_RE = re.compile(r"not ?found|no (tracking )?(information|record|data)|invalid tracking|could not locate|"
                           r"unable to locate|no results|not available at this time", re.I)


def looks_not_found(msg: str) -> bool:
    return bool(_NOT_FOUND_RE.search(msg or ""))
