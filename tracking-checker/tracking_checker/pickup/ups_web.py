"""UPS pickup status via www.ups.com/ipr/pickup-status-check.

The app fills the page's own form (Country, Pickup Request Number, ZIP Code), presses Check Status and reads
the JSON the page receives (POST /ipr/iprweb/api/PRNSecure/prn/getDetails): HTTP 200 with the pickup's details,
or HTTP 204 (empty) when UPS has no pickup for that PRN + ZIP.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime

from .. import models as M
from ..carriers.base import CarrierError, NotFound
from ..carriers.web import SOURCE, WebCarrier, snapshot
from . import PickupResult, status_from_ups

log = logging.getLogger("tracking_checker")

PAGE_URL = "https://www.ups.com/ipr/pickup-status-check?loc=en_US"
_API = "/ipr/iprweb/api/PRNSecure/prn/getDetails"


class UPSPickupWeb(WebCarrier):
    name = M.UPS

    def check(self, prn: str, zip_code: str, country: str = "US") -> PickupResult:
        return self.track((prn, zip_code, country))

    def lookup(self, w, page, job) -> PickupResult:
        from ..carriers.web.browser import Blocked
        prn, zip_code, country = job
        for attempt in (1, 2):
            try:
                page.goto(PAGE_URL, wait_until="domcontentloaded", timeout=self.timeout_ms)
                page.wait_for_selector("#requestNumber", state="visible", timeout=self.timeout_ms)
                break
            except Exception as e:  # noqa: BLE001
                if "Download is starting" in str(e):
                    raise Blocked("UPS's pickup page won't load in a hidden browser - turn on 'Show the browser "
                                  "window' in Settings -> Website lookups.") from e
                if attempt == 1 and w.needs_human(page):
                    w.wait_for_human(page, "UPS")
                    continue
                snapshot(page, "UPS-pickup", prn, f"form didn't load ({e})")
                raise CarrierError(f"UPS pickup page didn't load within {self.timeout_ms // 1000}s") from e
        page.wait_for_timeout(800)                   # let the page finish wiring up its form
        if country != "US":
            try:
                page.select_option("#inputCountry", country)
            except Exception as e:  # noqa: BLE001
                raise CarrierError(f"UPS pickup page has no country '{country}'") from e
        page.fill("#requestNumber", prn)
        page.fill("#zipCode", zip_code)
        try:
            with page.expect_response(lambda r: _API in r.url and r.request.method == "POST",
                                      timeout=self.timeout_ms) as info:
                page.get_by_role("button", name=re.compile(r"Check Status", re.I)).first.click()
            resp = info.value
        except Exception as e:  # noqa: BLE001
            problem = _form_error(page)
            if problem:
                raise NotFound(f"UPS: {problem}") from e
            if w.needs_human(page):
                w.wait_for_human(page, "UPS")
                raise CarrierError("UPS showed a security check - run again to look this one up") from e
            snapshot(page, "UPS-pickup", prn, f"no answer to Check Status ({e})")
            raise CarrierError("UPS pickup page didn't answer (debug snapshot saved in the logs folder)") from e
        if resp.status == 204:
            raise NotFound(f"UPS has no pickup {prn} for ZIP {zip_code} - check the PRN and the pickup ZIP code")
        if resp.status != 200:
            raise CarrierError(f"UPS pickup page returned HTTP {resp.status}")
        try:
            data = resp.json()
        except Exception as e:  # noqa: BLE001
            snapshot(page, "UPS-pickup", prn, "unreadable getDetails response")
            raise CarrierError("UPS returned unreadable pickup data") from e
        try:
            return parse_ups_pickup(prn, zip_code, country, data)
        except (NotFound, CarrierError):
            raise
        except Exception as e:  # noqa: BLE001
            snapshot(page, "UPS-pickup", prn, f"parse error {e}", data)
            raise CarrierError(f"Couldn't read the UPS pickup ({e}); debug snapshot saved") from e


def _form_error(page) -> str:
    try:
        txt = page.inner_text("body", timeout=3000)
    except Exception:  # noqa: BLE001
        return ""
    m = re.search(r"Please correct the following[^\n]*\n(.{0,200})", txt)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""


# ---------------------------------------------------------------- parsing
_TS = re.compile(r"(\d{4})-(\d{2})-(\d{2})(?:T(\d{2})[:.](\d{2})(?:[:.](\d{2}))?)?")


def _ts(s) -> datetime | None:
    """UPS writes '2026-09-21T13:51.47.000' (minutes and seconds separated by a dot)."""
    m = _TS.match(str(s or ""))
    if not m:
        return None
    y, mo, d, h, mi, sec = (int(x) if x else 0 for x in m.groups())
    try:
        return datetime(y, mo, d, h, mi, sec)
    except ValueError:
        return None


def _money(v) -> float | None:
    try:
        return round(float(v), 2) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def parse_ups_pickup(prn: str, zip_code: str, country: str, data: dict) -> PickupResult:
    if not isinstance(data, dict) or not data:
        raise NotFound(f"UPS has no pickup {prn} for ZIP {zip_code}")
    if data.get("errorCode") or data.get("errorMessage"):
        msg = data.get("errorMessage") or data.get("errorCode")
        raise NotFound(f"UPS: {msg}")
    if str(data.get("statusCode", "200")) != "200":
        raise CarrierError(f"UPS pickup lookup: status {data.get('statusCode')}")

    r = PickupResult(prn=(data.get("orderNumber") or prn).strip().upper(), carrier=M.UPS, zip_code=zip_code,
                     country=country, source=SOURCE)
    r.status_code = str(data.get("orderStatusCode") or "")
    r.status_detail = (data.get("orderStatusMessage") or "").strip()
    r.status = status_from_ups(r.status_code, r.status_detail)
    r.status_time = _ts(data.get("statusTime"))
    r.pickup_date = _ts(data.get("pickupDate"))
    r.ready_time = _ts(data.get("readyTime"))
    r.close_time = _ts(data.get("closeTime"))
    for p in data.get("pickupPieces") or []:
        r.pieces.append(((p.get("brandName") or "Package").strip(), int(p.get("quantity") or 0)))
    w = data.get("actualWeight") or data.get("weight")
    if w not in (None, "", "0"):
        r.weight = f"{w} {(data.get('unitOfMeasure') or data.get('weightMeasurementUnit') or '').lower()}".strip()
    a = data.get("pickupAddress") or {}
    r.company = (a.get("company") or "").strip()
    r.contact = (a.get("contactName") or "").strip()
    phone = re.sub(r"\D", "", a.get("phone") or "")
    r.phone = f"({phone[:3]}) {phone[3:6]}-{phone[6:]}" if len(phone) == 10 else (a.get("phone") or "")
    if a.get("phoneExtension"):
        r.phone += f" x{a['phoneExtension']}"
    city, state, postal = (a.get(k) or "" for k in ("politicalDivision2", "politicalDivision1", "postalCode"))
    state_zip = f"{state} {postal}".strip()
    city_line = ", ".join(x for x in (city.strip(), state_zip) if x)
    r.address = ", ".join(x for x in ((a.get("streetAddress") or "").strip(), city_line) if x)
    r.pickup_point = (a.get("pickupLocation") or "").strip()
    r.residential = a.get("residential") if isinstance(a.get("residential"), bool) else None
    r.notify_email = ((data.get("notification") or {}).get("emailsList") or "").strip()
    r.instructions = (data.get("specialInstructions") or "").strip()
    ch = data.get("pickupChargeInfo") or {}
    r.base_charge = _money(ch.get("baseCharge"))
    r.fuel_surcharge = _money(ch.get("fuelSurcharge"))
    other = [_money(ch.get(k)) for k in ("areaSurcharge", "residentialSurcharge", "saturdayStopSurcharge",
                                          "sundayStopSurcharge", "totalTax")]
    r.other_surcharges = round(sum(x for x in other if x), 2) if any(x is not None for x in other) else None
    r.total_charge = _money(ch.get("total"))
    return r
