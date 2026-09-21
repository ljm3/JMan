"""FedEx Track API (OAuth 2 client-credentials).

Docs: https://developer.fedex.com/api/en-us/catalog/track/v1/docs.html
- Track by tracking number: up to 30 numbers per request.
- Signature Proof of Delivery (SPOD) PDF: /track/v1/trackingdocuments. FedEx only releases
  the signature image to the shipper/recipient account, so set your FedEx account number
  in Settings; without it you still get the delivery details, just no signature PDF.
"""
from __future__ import annotations

import base64

from .. import models as M
from .base import Carrier, CarrierError, NotFound, error_text, log, looks_not_found, normalize_status, parse_iso, place

HOSTS = {"production": "https://apis.fedex.com", "sandbox": "https://apis-sandbox.fedex.com"}


class FedExCarrier(Carrier):
    name = M.FEDEX
    batch_size = 30

    @property
    def host(self) -> str:
        return HOSTS["sandbox" if self.sandbox else "production"]

    def _fetch_token(self):
        r = self.session.post(f"{self.host}/oauth/token",
                              data={"grant_type": "client_credentials", "client_id": self.client_id,
                                    "client_secret": self.client_secret},
                              headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=30)
        if r.status_code != 200:
            raise CarrierError(f"FedEx sign-in failed - {error_text(r)}")
        j = r.json()
        return j["access_token"], float(j.get("expires_in", 3600))

    @staticmethod
    def tracking_url(number: str) -> str:
        return f"https://www.fedex.com/fedextrack/?trknbr={number}"

    def track(self, number: str) -> M.TrackingResult:
        res = self.track_many([number])[number]
        if isinstance(res, Exception):
            raise res
        return res

    def track_many(self, numbers):
        out: dict = {}
        for i in range(0, len(numbers), self.batch_size):
            chunk = numbers[i:i + self.batch_size]
            body = {"includeDetailedScans": True,
                    "trackingInfo": [{"trackingNumberInfo": {"trackingNumber": n}} for n in chunk]}
            try:
                r = self._request("POST", f"{self.host}/track/v1/trackingnumbers", json=body,
                                  headers={"Content-Type": "application/json", "X-locale": "en_US"})
            except Exception as e:  # noqa: BLE001
                for n in chunk:
                    out[n] = CarrierError(f"FedEx request failed - {e}")
                continue
            if r.status_code != 200:
                err = CarrierError(f"FedEx error - {error_text(r)}")
                for n in chunk:
                    out[n] = err
                continue
            out.update(parse_fedex_batch(r.json(), chunk, self.source_label))
        return out

    def fetch_pod(self, result: M.TrackingResult) -> None:
        if result.status != M.DELIVERED:
            return
        spec = {"trackingNumberInfo": {"trackingNumber": result.tracking_number}}
        if self.settings.account_number:
            spec["accountNumber"] = self.settings.account_number
        body = {"trackDocumentDetail": {"documentType": "SIGNATURE_PROOF_OF_DELIVERY", "documentFormat": "PDF"},
                "trackDocumentSpecification": [spec]}
        try:
            r = self._request("POST", f"{self.host}/track/v1/trackingdocuments", json=body,
                              headers={"Content-Type": "application/json", "X-locale": "en_US"})
        except Exception as e:  # noqa: BLE001
            log.info("FedEx SPOD request failed for %s: %s", result.tracking_number, e)
            return
        if r.status_code != 200:
            log.info("FedEx SPOD not available for %s: %s", result.tracking_number, error_text(r))
            return
        docs = (r.json().get("output") or {}).get("documents") or []
        for d in docs:
            raw = d if isinstance(d, str) else (d.get("document") or d.get("content") or "")
            try:
                pdf = base64.b64decode(raw)
            except (ValueError, TypeError):
                continue
            if pdf[:4] == b"%PDF":
                result.pod = result.pod or M.ProofOfDelivery()
                result.pod.document = pdf
                result.pod.document_label = "FedEx Signature Proof of Delivery"
                return


def _addr(a: dict | None) -> str:
    a = a or {}
    return place(a.get("city"), a.get("stateOrProvinceCode"), a.get("postalCode"), a.get("countryCode"))


def parse_fedex_batch(data: dict, requested: list[str], source: str = "Live API") -> dict:
    out: dict = {}
    for ctr in (data.get("output") or {}).get("completeTrackResults") or []:
        num = ctr.get("trackingNumber", "")
        trs = ctr.get("trackResults") or []
        if not trs:
            out[num] = NotFound("FedEx returned no results")
            continue
        try:
            out[num] = parse_fedex_result(num, trs[0], source)
        except CarrierError as e:
            out[num] = e
    for n in requested:
        out.setdefault(n, NotFound("FedEx did not return this number"))
    return out


def parse_fedex_result(number: str, r: dict, source: str = "Live API") -> M.TrackingResult:
    err = r.get("error")
    if err:
        msg = err.get("message") or err.get("code") or "error"
        if "NOTFOUND" in (err.get("code") or "").upper() or looks_not_found(msg):
            raise NotFound(f"FedEx: {msg}")
        raise CarrierError(f"FedEx: {msg}")

    res = M.TrackingResult(tracking_number=number, carrier=M.FEDEX, source=source)
    lsd = r.get("latestStatusDetail") or {}
    res.status_detail = lsd.get("statusByLocale") or lsd.get("description") or ""
    anc = lsd.get("ancillaryDetails") or []
    if anc:
        extra = "; ".join(a.get("reasonDescription", "") for a in anc if a.get("reasonDescription"))
        if extra:
            res.status_detail = f"{res.status_detail} - {extra}"
    res.status = normalize_status(lsd.get("description") or res.status_detail,
                                  lsd.get("derivedCode") or lsd.get("code") or "")
    res.service = (r.get("serviceDetail") or {}).get("description", "")

    for ev in r.get("scanEvents") or []:
        desc = ev.get("eventDescription") or ev.get("derivedStatus") or ""
        if ev.get("exceptionDescription"):
            desc = f"{desc} - {ev['exceptionDescription']}"
        res.events.append(M.TrackingEvent(parse_iso(ev.get("date")), desc.strip(), _addr(ev.get("scanLocation")),
                                          ev.get("derivedStatusCode") or ev.get("eventType") or ""))

    dates = {d.get("type"): d.get("dateTime") for d in r.get("dateAndTimes") or []}
    res.ship_date = parse_iso(dates.get("SHIP") or dates.get("ACTUAL_PICKUP") or dates.get("ACTUAL_TENDER"))
    if res.status == M.DELIVERED:
        res.delivered_at = parse_iso(dates.get("ACTUAL_DELIVERY")) or (res.last_event.timestamp if res.last_event else None)
    est = dates.get("ESTIMATED_DELIVERY") or dates.get("APPOINTMENT_DELIVERY") or dates.get("COMMITMENT")
    win = (r.get("estimatedDeliveryTimeWindow") or {}).get("window") or {}
    if win.get("begins") or win.get("ends"):
        b, e = parse_iso(win.get("begins")), parse_iso(win.get("ends"))
        res.estimated_delivery = " - ".join(x.strftime("%Y-%m-%d %H:%M") for x in (b, e) if x)
    elif est:
        d = parse_iso(est)
        res.estimated_delivery = d.strftime("%Y-%m-%d %H:%M") if d else est

    si = (r.get("shipperInformation") or {}).get("address") or \
        ((r.get("originLocation") or {}).get("locationContactAndAddress") or {}).get("address")
    ri = (r.get("recipientInformation") or {}).get("address") or \
        ((r.get("destinationLocation") or {}).get("locationContactAndAddress") or {}).get("address") or \
        r.get("lastUpdatedDestinationAddress")
    res.origin, res.destination = _addr(si), _addr(ri)

    wts = ((r.get("packageDetails") or {}).get("weightAndDimensions") or {}).get("weight") or []
    if wts:
        w = next((x for x in wts if (x.get("unit") or "").upper() == "LB"), wts[0])
        res.weight = f"{w.get('value')} {(w.get('unit') or '').lower()}".strip()

    dd = r.get("deliveryDetails") or {}
    try:
        res.attempts = int(dd.get("deliveryAttempts")) if dd.get("deliveryAttempts") not in (None, "") else None
    except (TypeError, ValueError):
        res.attempts = None
    if res.status in (M.EXCEPTION, M.RETURNED):
        res.exception = res.status_detail
    if res.status == M.DELIVERED:
        res.pod = M.ProofOfDelivery(
            signed_by=(dd.get("receivedByName") or dd.get("signedByName") or "").strip(),
            left_at=(dd.get("locationDescription") or "").replace("_", " ").title(),
            address=_addr(dd.get("actualDeliveryAddress")) or res.destination,
        )
    return res
