"""USPS Tracking API v3 (apis.usps.com, OAuth 2 client-credentials).

Docs: https://developers.usps.com/trackingv3
- One number per request; new developer apps are capped at ~60 requests/hour until USPS
  approves a quota increase (Settings -> USPS -> requests per minute).
- The API returns the delivery scan with the recipient/agent name where one was captured,
  but NOT the signature image (USPS only issues that via its Return Receipt / Proof of
  Delivery letter programs). The app therefore builds a POD summary page for USPS.
"""
from __future__ import annotations

from .. import models as M
from .base import Carrier, CarrierError, NotFound, error_text, looks_not_found, normalize_status, parse_iso, place

HOSTS = {"production": "https://apis.usps.com", "sandbox": "https://apis-tem.usps.com"}


class USPSCarrier(Carrier):
    name = M.USPS

    @property
    def host(self) -> str:
        return HOSTS["sandbox" if self.sandbox else "production"]

    def _fetch_token(self):
        r = self.session.post(f"{self.host}/oauth2/v3/token",
                              json={"grant_type": "client_credentials", "client_id": self.client_id,
                                    "client_secret": self.client_secret}, timeout=30)
        if r.status_code != 200:
            raise CarrierError(f"USPS sign-in failed - {error_text(r)}")
        j = r.json()
        return j["access_token"], float(j.get("expires_in", 3600))

    @staticmethod
    def tracking_url(number: str) -> str:
        return f"https://tools.usps.com/go/TrackConfirmAction?tLabels={number}"

    def track(self, number: str) -> M.TrackingResult:
        r = self._request("GET", f"{self.host}/tracking/v3/tracking/{number}", params={"expand": "DETAIL"})
        if r.status_code != 200:
            msg = error_text(r)
            if r.status_code == 404 or looks_not_found(msg):
                raise NotFound(f"USPS has no record of this number ({msg})")
            if r.status_code == 429:
                raise CarrierError("USPS hourly request quota reached - lower USPS requests/minute in Settings "
                                   "or ask USPS for a quota increase")
            raise CarrierError(f"USPS error - {msg}")
        return parse_usps(number, r.json(), self.source_label)


def parse_usps(number: str, j: dict, source: str = "Live API") -> M.TrackingResult:
    if j.get("error"):
        msg = str(j["error"].get("message") if isinstance(j["error"], dict) else j["error"])
        if looks_not_found(msg):
            raise NotFound(f"USPS: {msg}")
        raise CarrierError(f"USPS: {msg}")

    res = M.TrackingResult(tracking_number=number, carrier=M.USPS, source=source)
    res.service = j.get("mailClass") or j.get("mailType") or ""
    res.status_detail = j.get("statusSummary") or j.get("status") or ""
    res.status = normalize_status(j.get("statusCategory") or j.get("status") or res.status_detail)
    if res.status == M.UNKNOWN:
        res.status = normalize_status(res.status_detail)
    res.origin = place(j.get("originCity"), j.get("originState"), j.get("originZIP"))
    res.destination = place(j.get("destinationCity"), j.get("destinationState"), j.get("destinationZIP"),
                            j.get("destinationCountryCode"))
    exp = parse_iso(j.get("expectedDeliveryTimeStamp") or j.get("expectedDeliveryDate"))
    if exp and res.status != M.DELIVERED:
        res.estimated_delivery = exp.strftime("%Y-%m-%d") if exp.hour == 0 and exp.minute == 0 else exp.strftime("%Y-%m-%d %H:%M")

    delivered_event = None
    for ev in j.get("trackingEvents") or []:
        e = M.TrackingEvent(parse_iso(ev.get("eventTimestamp")), (ev.get("eventType") or "").strip(),
                            place(ev.get("eventCity"), ev.get("eventState"), ev.get("eventZIP"), ev.get("eventCountry")),
                            ev.get("eventCode") or "")
        res.events.append(e)
        if delivered_event is None and normalize_status(e.description, e.code) == M.DELIVERED:
            delivered_event = (e, ev)

    res.ship_date = res.first_event.timestamp if res.first_event else None
    res.attempts = sum(1 for e in res.events if "attempt" in e.description.lower() or "notice left" in e.description.lower())
    if res.status in (M.EXCEPTION, M.RETURNED):
        res.exception = res.status_detail

    if res.status == M.DELIVERED:
        e, raw = delivered_event if delivered_event else (res.last_event, {})
        res.delivered_at = e.timestamp if e else None
        signer = raw.get("name") or " ".join(x for x in [raw.get("firstName"), raw.get("lastName")] if x)
        desc = e.description if e else ""
        left_at = ""
        low = desc.lower()
        for phrase in ("front door", "porch", "mailbox", "parcel locker", "front desk", "reception", "mail room",
                       "garage", "back door", "individual picked up at post office", "left with individual"):
            if phrase in low:
                left_at = phrase.title()
                break
        res.pod = M.ProofOfDelivery(signed_by=(signer or "").strip(), left_at=left_at,
                                    address=(e.location if e else "") or res.destination)
    return res
