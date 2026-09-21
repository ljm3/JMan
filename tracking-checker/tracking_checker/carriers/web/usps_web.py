"""USPS via tools.usps.com - reads the tracking steps rendered on the page."""
from __future__ import annotations

import logging
import re
from datetime import datetime

from ... import models as M
from ..base import CarrierError, NotFound, normalize_status
from ..usps import USPSCarrier
from . import SOURCE, WebCarrier, snapshot

log = logging.getLogger("tracking_checker")

_EXTRACT = r"""() => {
  const tx = e => e ? (e.textContent || "").replace(/\s+/g, " ").trim() : "";
  const pick = (el, sels) => { for (const s of sels) { const x = el.querySelector(s); if (x && tx(x)) return tx(x); } return ""; };
  const steps = [...document.querySelectorAll(".tb-step")].map(s => ({
    status: pick(s, [".tb-status"]),
    detail: pick(s, [".tb-status-detail"]),
    location: pick(s, [".tb-location"]),
    date: pick(s, [".tb-date"]),
    lines: (s.innerText || s.textContent || "").split(/\n+/).map(x => x.trim()).filter(Boolean),
  }));
  return {
    steps,
    status: tx(document.querySelector(".current-tracking-status-wrapper")),
    banner: tx(document.querySelector(".latest-update-banner-wrapper, .red-banner, .banner-content")),
    body: (document.body.innerText || "").slice(0, 30000),
  };
}"""

_READY = ".tb-step, .tracking-number, .latest-update-banner-wrapper, .red-banner"
_MONTH = r"(?:January|February|March|April|May|June|July|August|September|October|November|December)"
_DATE_RE = re.compile(_MONTH + r"\s+\d{1,2},\s+\d{4}(?:,?\s+\d{1,2}:\d{2}\s*[ap]\.?m\.?)?", re.I)
_LOC_RE = re.compile(r"^[A-Z .'-]+,\s*[A-Z]{2}\s+\d{5}(?:-\d{4})?$|^[A-Z .'-]+,\s*[A-Z]{2}$")


class USPSWeb(WebCarrier):
    name = M.USPS
    tracking_url = staticmethod(USPSCarrier.tracking_url)

    def lookup(self, w, page, number):
        url = f"https://tools.usps.com/go/TrackConfirmAction?tLabels={number}"
        page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
        if not self._ready(page, 15000):
            # USPS's security check sometimes leaves a blank page; an ordinary reload usually clears it.
            page.reload(wait_until="domcontentloaded", timeout=self.timeout_ms)
            if not self._ready(page, 20000):
                if w.needs_human(page):
                    w.wait_for_human(page, "USPS", done=lambda: self._ready(page, 1000))
                else:
                    snapshot(page, self.name, number, "tracking section never appeared")
                    raise CarrierError("USPS page didn't show tracking details (debug snapshot saved)")
        data = page.evaluate(_EXTRACT)
        try:
            res = parse_usps_web(number, data)
        except NotFound:
            raise
        except CarrierError:
            snapshot(page, self.name, number, "layout not recognised", data)
            raise
        if res.delivered and self.capture_pod:
            try:
                page.get_by_text(re.compile(r"(see|view) all tracking history", re.I)).first.click(timeout=3000)
                page.wait_for_timeout(800)
            except Exception:  # noqa: BLE001
                pass
            res.pod.page_capture = w.pdf(page)
        return res

    @staticmethod
    def _ready(page, timeout=15000) -> bool:
        try:
            page.wait_for_selector(_READY, timeout=timeout, state="attached")
            return True
        except Exception:  # noqa: BLE001
            return False


def _ts(s: str) -> datetime | None:
    s = (s or "").replace(".", "").replace(" at ", ", ").strip()
    s = re.sub(r"\s+", " ", s)
    for fmt in ("%B %d, %Y, %I:%M %p", "%B %d, %Y %I:%M %p", "%B %d, %Y"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _norm_loc(s: str) -> str:
    s = (s or "").strip()
    m = re.match(r"^(.*?),\s*([A-Z]{2})(?:\s+(\d{5}(?:-\d{4})?))?$", s)
    if not m:
        return s.title() if s.isupper() else s
    city, st, z = m.groups()
    return f"{city.title()}, {st}" + (f" {z}" if z else "")


def _step(s: dict) -> M.TrackingEvent:
    detail, status, loc, date = s.get("detail", ""), s.get("status", ""), s.get("location", ""), s.get("date", "")
    lines = s.get("lines") or []
    if not date:
        date = next((x for x in lines if _DATE_RE.search(x)), "")
        m = _DATE_RE.search(date)
        date = m.group(0) if m else ""
    if not loc:
        loc = next((x for x in lines if _LOC_RE.match(x.strip())), "")
    if not detail:
        detail = next((x for x in lines if x not in (date, loc, status) and not _DATE_RE.search(x)
                       and len(x) > 3 and "see less" not in x.lower() and "see all" not in x.lower()), "")
    desc = detail or status
    return M.TrackingEvent(_ts(date), desc, _norm_loc(loc), status)


def parse_usps_web(number: str, d: dict) -> M.TrackingResult:
    body = d.get("body") or ""
    steps = [s for s in d.get("steps") or [] if any(s.get(k) for k in ("status", "detail", "date", "lines"))]
    if not steps:
        if re.search(r"Tracking Not Available|Status Not Available|not available for this item|"
                     r"could not locate the tracking information", body, re.I):
            raise NotFound("USPS: Tracking Not Available")
        raise CarrierError("USPS page had no tracking steps (the page layout may have changed)")

    res = M.TrackingResult(tracking_number=number, carrier=M.USPS, source=SOURCE)
    res.events = [e for e in (_step(s) for s in steps) if e.description or e.timestamp]
    top = steps[0]
    headline = " ".join(x for x in (top.get("status"), top.get("detail"), d.get("status")) if x)
    res.status_detail = (top.get("detail") or top.get("status") or d.get("status") or "").strip()
    res.status = normalize_status(headline)
    if res.status == M.UNKNOWN and res.events:
        res.status = normalize_status(res.events[0].description)

    m = re.search(r"(?:Postal Product|Product)\s*:?\s*\n?\s*([^\n]{3,80})", body)
    if m and not re.search(r"information|features", m.group(1), re.I):
        res.service = m.group(1).strip()
    elif (m2 := re.search(r"(Priority Mail Express|Priority Mail|USPS Ground Advantage|First-Class[^\n]{0,30}|"
                          r"Media Mail|Parcel Select[^\n]{0,20})", body)):
        res.service = m2.group(1).strip()

    res.ship_date = res.first_event.timestamp if res.first_event else None
    res.origin = res.first_event.location if res.first_event else ""
    res.attempts = sum(1 for e in res.events if re.search(r"attempt|notice left", e.description, re.I))
    if res.status in (M.EXCEPTION, M.RETURNED):
        res.exception = res.status_detail

    if res.delivered:
        dev = next((e for e in res.events if normalize_status(e.description, "") == M.DELIVERED
                    or e.code.lower().startswith("delivered")), res.events[0] if res.events else None)
        res.delivered_at = dev.timestamp if dev else None
        res.destination = dev.location if dev else ""
        left = ""
        if dev and "," in dev.description:
            left = dev.description.split(",", 1)[1].strip()
        res.pod = M.ProofOfDelivery(left_at=left, address=res.destination)
    else:
        em = re.search(r"Expected Delivery(?: on| by)?\s*:?\s*\n?\s*(?:[A-Za-z]+,?\s+)?(" + _MONTH + r"\s+\d{1,2}(?:,\s*\d{4})?)",
                       body, re.I)
        if em:
            raw = em.group(1)
            if not re.search(r"\d{4}", raw):
                raw = f"{raw}, {datetime.now().year}"
            dt = _ts(raw)
            res.estimated_delivery = dt.strftime("%Y-%m-%d") if dt else em.group(1)
        latest = res.last_event
        res.destination = ""
        if latest and res.status == M.OUT_FOR_DELIVERY:
            res.destination = latest.location
    return res
