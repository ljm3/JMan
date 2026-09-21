"""FedEx via www.fedex.com/fedextrack.

As of 2026-09 FedEx's bot protection rejects the tracking request made by an automated browser (the page
then says "We can't find that tracking number"). The app does not try to disguise itself, so this carrier
reports a clear "blocked" error and stops trying for the rest of the run; with FedEx API keys in Settings,
FedEx numbers use the official API instead (method "Automatic").
"""
from __future__ import annotations

from ... import models as M
from ..base import CarrierError, NotFound
from ..fedex import FedExCarrier, parse_fedex_result
from . import SOURCE, WebCarrier, snapshot

BLOCKED_HINT = ("FedEx's website blocks automated lookups. Add free FedEx API keys in Settings (FedEx tab) and "
                "FedEx numbers will use the official API automatically.")


class FedExWeb(WebCarrier):
    name = M.FEDEX
    tracking_url = staticmethod(FedExCarrier.tracking_url)

    def lookup(self, w, page, number):
        from .browser import Blocked
        url = f"https://www.fedex.com/fedextrack/?trknbr={number}"
        try:
            resp = self.load_and_capture(w, page, url, lambda u, m: "/track/v2/shipments" in u and m == "POST", "FedEx")
        except Blocked:
            raise Blocked(BLOCKED_HINT)
        if resp.status in (401, 403, 429):
            raise Blocked(BLOCKED_HINT)
        try:
            data = resp.json()
        except Exception:  # noqa: BLE001
            raise CarrierError("FedEx website returned unreadable tracking data")
        try:
            res = parse_fedex_web(number, data)
        except (NotFound, CarrierError):
            raise
        except Exception as e:  # noqa: BLE001
            snapshot(page, self.name, number, f"parse error {e}", data)
            raise CarrierError(f"Couldn't read the FedEx page ({e}); debug snapshot saved")
        if res.delivered and self.capture_pod:
            page.wait_for_timeout(1500)
            res.pod = res.pod or M.ProofOfDelivery()
            res.pod.page_capture = w.pdf(page)
        return res


def parse_fedex_web(number: str, data: dict) -> M.TrackingResult:
    out = data.get("output") or {}
    ctr = out.get("completeTrackResults") or []
    if ctr:
        trs = ctr[0].get("trackResults") or []
        if not trs:
            raise NotFound("FedEx returned no results")
        res = parse_fedex_result(number, trs[0], SOURCE)
        return res
    pkgs = out.get("packages") or []
    if pkgs and (pkgs[0].get("errorList") or [{}])[0].get("code") not in (None, "", "0"):
        raise NotFound(f"FedEx: {pkgs[0]['errorList'][0].get('message', 'not found')}")
    raise CarrierError("FedEx page returned data in an unrecognised format")
