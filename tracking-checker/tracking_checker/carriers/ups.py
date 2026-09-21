"""UPS Tracking API (OAuth 2 client-credentials).

Docs: https://developer.ups.com/api/reference?loc=en_US#tag/Tracking
Signature images / POD letters are only returned when the tracking number was shipped
on an account linked to your UPS developer app (put that shipper number in Settings).
"""
from __future__ import annotations

import base64
import uuid
from datetime import datetime

from .. import models as M
from .base import Carrier, CarrierError, NotFound, error_text, looks_not_found, normalize_status, place

HOSTS = {"production": "https://onlinetools.ups.com", "sandbox": "https://wwwcie.ups.com"}


class UPSCarrier(Carrier):
    name = M.UPS

    @property
    def host(self) -> str:
        return HOSTS["sandbox" if self.sandbox else "production"]

    def _fetch_token(self):
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        if self.settings.account_number:
            headers["x-merchant-id"] = self.settings.account_number
        r = self.session.post(f"{self.host}/security/v1/oauth/token", data={"grant_type": "client_credentials"},
                              auth=(self.client_id, self.client_secret), headers=headers, timeout=30)
        if r.status_code != 200:
            raise CarrierError(f"UPS sign-in failed - {error_text(r)}")
        j = r.json()
        return j["access_token"], float(j.get("expires_in", 3600))

    @staticmethod
    def tracking_url(number: str) -> str:
        return f"https://www.ups.com/track?tracknum={number}"

    def track(self, number: str) -> M.TrackingResult:
        r = self._request(
            "GET", f"{self.host}/api/track/v1/details/{number}",
            params={"locale": "en_US", "returnSignature": "true", "returnPOD": "true", "returnMilestones": "false"},
            headers={"transId": uuid.uuid4().hex[:32], "transactionSrc": "TrackingCheck"},
        )
        if r.status_code != 200:
            msg = error_text(r)
            if r.status_code == 404 or looks_not_found(msg):
                raise NotFound(f"UPS has no record of this number ({msg})")
            raise CarrierError(f"UPS error - {msg}")
        return parse_ups(number, r.json(), self.source_label)


def _d(date: str | None, time: str | None = None) -> datetime | None:
    if not date or len(date) != 8:
        return None
    t = (time or "000000").ljust(6, "0")[:6]
    try:
        return datetime.strptime(date + t, "%Y%m%d%H%M%S")
    except ValueError:
        return None


def _addr(a: dict | None) -> str:
    a = a or {}
    return place(a.get("city"), a.get("stateProvince"), a.get("postalCode"), a.get("countryCode") or a.get("country"))


def parse_ups(number: str, data: dict, source: str = "Live API") -> M.TrackingResult:
    try:
        shipment = data["trackResponse"]["shipment"][0]
    except (KeyError, IndexError, TypeError):
        raise CarrierError("UPS returned an unexpected response shape")
    warnings = shipment.get("warnings") or []
    packages = shipment.get("package") or []
    if not packages:
        msg = "; ".join(w.get("message", "") for w in warnings) or "no package data"
        raise NotFound(f"UPS: {msg}")
    pkg = packages[0]

    res = M.TrackingResult(tracking_number=number, carrier=M.UPS, source=source)
    res.service = (pkg.get("service") or {}).get("description", "")

    for a in pkg.get("activity") or []:
        st = a.get("status") or {}
        loc = _addr((a.get("location") or {}).get("address"))
        res.events.append(M.TrackingEvent(_d(a.get("date"), a.get("time")), st.get("description", "").strip(),
                                          loc, st.get("code") or st.get("type") or ""))

    cs = pkg.get("currentStatus") or {}
    latest = (pkg.get("activity") or [{}])[0].get("status") or {}
    res.status_detail = (cs.get("description") or latest.get("description") or "").strip()
    res.status = normalize_status(res.status_detail, latest.get("type") or "")

    for pa in pkg.get("packageAddress") or []:
        if pa.get("type") == "ORIGIN":
            res.origin = _addr(pa.get("address"))
        elif pa.get("type") == "DESTINATION":
            res.destination = _addr(pa.get("address"))

    dt_by_type = {d.get("type"): d.get("date") for d in pkg.get("deliveryDate") or []}
    dtime = pkg.get("deliveryTime") or {}
    if res.status == M.DELIVERED:
        res.delivered_at = _d(dt_by_type.get("DEL"), dtime.get("endTime") or dtime.get("startTime"))
        if not res.delivered_at and res.last_event:
            res.delivered_at = res.last_event.timestamp
    else:
        est = dt_by_type.get("SDD") or dt_by_type.get("RDD") or dt_by_type.get("EDD")
        if est:
            d = _d(est)
            window = ""
            if dtime.get("startTime") or dtime.get("endTime"):
                window = f" {_hhmm(dtime.get('startTime'))}-{_hhmm(dtime.get('endTime'))}".rstrip("-")
            res.estimated_delivery = (d.strftime("%Y-%m-%d") if d else est) + window

    pickup = pkg.get("pickupDate") or shipment.get("pickupDate")
    res.ship_date = _d(pickup) or (res.first_event.timestamp if res.first_event else None)

    w = pkg.get("weight") or {}
    if w.get("weight"):
        res.weight = f"{w['weight']} {(w.get('unitOfMeasurement') or '').lower()}".strip()

    attempts = [e for e in res.events if "attempt" in e.description.lower()]
    res.attempts = len(attempts)
    if res.status in (M.EXCEPTION, M.RETURNED):
        res.exception = res.status_detail

    di = pkg.get("deliveryInformation") or {}
    if res.status == M.DELIVERED or di:
        pod = M.ProofOfDelivery(signed_by=(di.get("receivedBy") or "").strip(), left_at=(di.get("location") or "").strip(),
                                address=res.destination)
        sig = (di.get("signature") or {}).get("image")
        if sig:
            pod.signature_image = _b64(sig)
        doc = (di.get("pod") or {}).get("content")
        if doc:
            pod.document = _b64(doc)
            pod.document_label = "UPS Proof of Delivery"
        photo = (di.get("deliveryPhoto") or {}).get("photo")
        if photo:
            pod.photo = _b64(photo)
        if any([pod.signed_by, pod.left_at, pod.signature_image, pod.document, pod.photo, res.status == M.DELIVERED]):
            res.pod = pod
    return res


def _hhmm(t: str | None) -> str:
    t = (t or "").strip()
    return f"{t[:2]}:{t[2:4]}" if len(t) >= 4 else t


def _b64(s: str) -> bytes | None:
    try:
        return base64.b64decode(s)
    except (ValueError, TypeError):
        return None
