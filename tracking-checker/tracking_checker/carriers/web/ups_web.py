"""UPS via www.ups.com/track - reads the JSON the tracking page itself loads (Track/GetStatus)."""
from __future__ import annotations

import base64
import html
import logging
import re
from datetime import datetime

from ... import models as M
from ..base import CarrierError, NotFound, normalize_status, place
from ..ups import UPSCarrier
from . import SOURCE, WebCarrier, snapshot

log = logging.getLogger("tracking_checker")


class UPSWeb(WebCarrier):
    name = M.UPS
    tracking_url = staticmethod(UPSCarrier.tracking_url)

    def lookup(self, w, page, number):
        url = f"https://www.ups.com/track?loc=en_US&tracknum={number}&requester=ST/trackdetails"
        resp = self.load_and_capture(w, page, url, lambda u, m: "/track/api/Track/GetStatus" in u and m == "POST", "UPS")
        if resp.status != 200:
            raise CarrierError(f"UPS website returned HTTP {resp.status}")
        try:
            data = resp.json()
        except Exception:  # noqa: BLE001
            snapshot(page, self.name, number, "unreadable GetStatus response")
            raise CarrierError("UPS website returned unreadable tracking data")
        try:
            res = parse_ups_web(number, data)
        except (NotFound, CarrierError):
            raise
        except Exception as e:  # noqa: BLE001
            snapshot(page, self.name, number, f"parse error {e}", data)
            raise CarrierError(f"Couldn't read the UPS page ({e}); debug snapshot saved")
        if res.delivered and self.capture_pod:
            self._pod(w, page, res, data)
        return res

    def _pod(self, w, page, res, data):
        td = (data.get("trackDetails") or [{}])[0]
        pod = res.pod
        pod_url = html.unescape(td.get("proofOfDeliveryUrl") or "")
        if data.get("isLoggedInUser") and pod_url:
            try:
                page.goto(pod_url, wait_until="domcontentloaded", timeout=self.timeout_ms)
                page.wait_for_timeout(2500)
                if "/track?" not in page.url and "proof of delivery" in page.inner_text("body")[:5000].lower():
                    pod.document = w.pdf(page)
                    pod.document_label = "UPS Proof of Delivery letter (ups.com)"
                    return
                log.info("UPS POD letter not offered for %s (page: %s)", res.tracking_number, page.url)
            except Exception as e:  # noqa: BLE001
                log.info("UPS POD letter capture failed for %s: %s", res.tracking_number, e)
        try:
            page.get_by_text(re.compile(r"^\s*Show Details", re.I)).first.click(timeout=3000)
            page.wait_for_timeout(1000)
        except Exception:  # noqa: BLE001
            pass
        pod.page_capture = w.pdf(page)


def _ts(date: str | None, time: str | None = None) -> datetime | None:
    date = (date or "").strip()
    if not date:
        return None
    t = (time or "").upper().replace("A.M.", "AM").replace("P.M.", "PM").replace(".", "").strip()
    for fmt, s in (("%m/%d/%Y %I:%M %p", f"{date} {t}"), ("%m/%d/%Y %H:%M", f"{date} {t}"), ("%m/%d/%Y", date)):
        try:
            return datetime.strptime(s.strip(), fmt)
        except ValueError:
            continue
    return None


def _loc(s: str | None) -> str:
    parts = [p.strip() for p in (s or "").split(",") if p.strip()]
    if len(parts) >= 2 and parts[-1].upper() in ("US", "USA", "UNITED STATES"):
        parts = parts[:-1]
    if not parts:
        return ""
    head = parts[0].title()
    rest = [p.upper() if len(p) <= 3 else p for p in parts[1:]]
    return ", ".join([head] + rest)


def _addr(a: dict | None) -> str:
    a = a or {}
    return place(a.get("city"), a.get("state") or a.get("province"), a.get("zipCode"), a.get("country"))


def _b64(v) -> bytes | None:
    if isinstance(v, dict):
        v = v.get("photo") or v.get("image") or v.get("content")
    if not v or not isinstance(v, str):
        return None
    v = v.split(",", 1)[1] if v.startswith("data:") else v
    try:
        return base64.b64decode(v)
    except (ValueError, TypeError):
        return None


_DATE_RE = re.compile(r"\d{1,2}/\d{1,2}/\d{4}")


def _find_estimate(td: dict) -> str:
    for k, v in td.items():
        kl = k.lower()
        if not any(x in kl for x in ("scheduleddelivery", "estimateddelivery", "rescheduleddelivery")):
            continue
        cand = v
        if isinstance(v, dict):
            cand = " ".join(str(x) for x in v.values() if isinstance(x, str))
        if isinstance(cand, str):
            m = _DATE_RE.search(cand)
            if m:
                d = _ts(m.group(0))
                return d.strftime("%Y-%m-%d") if d else m.group(0)
    return ""


def parse_ups_web(number: str, data: dict) -> M.TrackingResult:
    if str(data.get("statusCode", "200")) != "200":
        raise CarrierError(f"UPS website: {data.get('statusText') or data.get('statusCode')}")
    tds = data.get("trackDetails") or []
    if not tds:
        raise NotFound("UPS: no tracking details returned")
    td = tds[0]
    if td.get("errorCode") or td.get("errorText"):
        raise NotFound(f"UPS: {td.get('errorText') or td.get('errorCode')}")

    res = M.TrackingResult(tracking_number=number, carrier=M.UPS, source=SOURCE)
    info = td.get("additionalInformation") or {}
    res.service = ((info.get("serviceInformation") or {}).get("serviceName") or "").strip()
    if info.get("weight"):
        res.weight = f"{info['weight']} {(info.get('weightUnit') or '').lower()}".strip()

    for a in td.get("shipmentProgressActivities") or []:
        desc = (a.get("activityScan") or "").strip()
        if desc.isupper():
            desc = desc.capitalize()
        extra = (a.get("activityAdditionalDescription") or "").strip()
        if extra:
            desc = f"{desc} - {extra}"
        res.events.append(M.TrackingEvent(_ts(a.get("date"), a.get("time")), desc, _loc(a.get("location")),
                                          a.get("actCode") or ""))

    res.status_detail = (td.get("packageStatus") or "").strip()
    if td.get("simplifiedText"):
        res.status_detail = f"{res.status_detail} - {td['simplifiedText']}".strip(" -")
    res.status = normalize_status(td.get("packageStatus") or "", td.get("packageStatusType") or "")
    if td.get("isDelivered"):
        res.status = M.DELIVERED
    elif res.status == M.UNKNOWN and res.events:
        res.status = normalize_status(res.events[0].description)

    res.destination = _addr(td.get("shipToAddress"))
    res.origin = _addr(td.get("shipFromAddress"))
    if not res.origin and res.first_event:
        res.origin = res.first_event.location
    res.ship_date = res.first_event.timestamp if res.first_event else None
    res.attempts = sum(1 for e in res.events if "attempt" in e.description.lower())

    if res.delivered:
        dev = next((e for e in res.events if normalize_status(e.description) == M.DELIVERED and e.timestamp), None)
        cm = td.get("currentMilestone") or {}
        res.delivered_at = dev.timestamp if dev else _ts(cm.get("date"), cm.get("time"))
        left = (td.get("leftAt") or td.get("cdiLeaveAt") or td.get("leaveAt") or "")
        left = "" if str(left).strip().lower() in ("other", "none") else str(left).strip()
        res.pod = M.ProofOfDelivery(signed_by=(td.get("receivedBy") or "").strip(), left_at=left,
                                    address=res.destination or (dev.location if dev else ""))
        res.pod.signature_image = _b64(td.get("signatureImage"))
        res.pod.photo = _b64(td.get("deliveryPhoto"))
    else:
        res.estimated_delivery = _find_estimate(td)
    if res.status in (M.EXCEPTION, M.RETURNED):
        res.exception = res.status_detail
    return res
