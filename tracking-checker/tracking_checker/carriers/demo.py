"""Deterministic fake tracking data so the app can be tried before any API keys exist."""
from __future__ import annotations

import hashlib
import random
from datetime import datetime, timedelta

from .. import models as M
from .base import NotFound

DEMO_SOURCE = "DEMO - not real tracking data"

_CITIES = [("Atlanta", "GA", "30301"), ("Memphis", "TN", "38118"), ("Louisville", "KY", "40209"),
           ("Chicago", "IL", "60666"), ("Dallas", "TX", "75261"), ("Newark", "NJ", "07114"),
           ("Denver", "CO", "80249"), ("Phoenix", "AZ", "85034"), ("Orlando", "FL", "32824"),
           ("Columbus", "OH", "43217"), ("Hartford", "CT", "06101"), ("Seattle", "WA", "98108")]
_SIGNERS = ["J SMITH", "MARIA LOPEZ", "FRONT DESK", "K PATEL", "", "D WILLIAMS", "RECEPTION", ""]
_LEFT = ["Front Door", "Front Desk", "Mailbox", "Garage", "Reception", "Loading Dock", "Parcel Locker"]
_SERVICES = {M.UPS: ["UPS Ground", "UPS 2nd Day Air", "UPS Next Day Air"],
             M.FEDEX: ["FedEx Ground", "FedEx Home Delivery", "FedEx 2Day", "FedEx Priority Overnight"],
             M.USPS: ["Priority Mail", "Ground Advantage", "Priority Mail Express", "First-Class Package"]}


class DemoCarrier:
    batch_size = 30

    def __init__(self, carrier: str):
        self.name = carrier

    @staticmethod
    def tracking_url(number: str) -> str:
        return ""

    def track_many(self, numbers):
        out = {}
        for n in numbers:
            try:
                out[n] = self.track(n)
            except NotFound as e:
                out[n] = e
        return out

    def track(self, number: str) -> M.TrackingResult:
        rnd = random.Random(int(hashlib.sha256(f"{self.name}:{number}".encode()).hexdigest()[:12], 16))
        roll = rnd.random()
        if roll < 0.04:
            raise NotFound(f"[DEMO] {self.name} has no record of this number")
        status = (M.DELIVERED if roll < 0.62 else M.IN_TRANSIT if roll < 0.78 else M.OUT_FOR_DELIVERY
                  if roll < 0.86 else M.EXCEPTION if roll < 0.94 else M.LABEL_CREATED if roll < 0.97 else M.RETURNED)
        now = datetime.now().replace(second=0, microsecond=0)
        ship = now - timedelta(days=rnd.randint(2, 20), hours=rnd.randint(0, 12))
        o, d = rnd.sample(_CITIES, 2)
        mids = rnd.sample(_CITIES, 3)
        res = M.TrackingResult(tracking_number=number, carrier=self.name, source=DEMO_SOURCE,
                               service=rnd.choice(_SERVICES.get(self.name, ["Standard"])),
                               origin=f"{o[0]}, {o[1]} {o[2]}", destination=f"{d[0]}, {d[1]} {d[2]}",
                               weight=f"{rnd.randint(1, 60)}.{rnd.randint(0, 9)} lbs", ship_date=ship)
        t = ship
        steps = [("Shipment information sent to carrier", o), ("Picked up", o), ("Departed facility", o)]
        steps += [("Arrived at facility", c) for c in mids[: rnd.randint(1, 3)]]
        steps += [("Arrived at destination facility", d)]
        if status == M.LABEL_CREATED:
            steps = steps[:1]
        elif status == M.IN_TRANSIT:
            steps = steps[: rnd.randint(3, len(steps))]
        elif status in (M.OUT_FOR_DELIVERY, M.DELIVERED, M.EXCEPTION, M.RETURNED):
            steps.append(("Out for delivery", d))
        if status == M.EXCEPTION:
            steps.append((rnd.choice(["Delivery attempted - no access to delivery location",
                                      "Weather delay", "Incorrect address - delivery rescheduled"]), d))
            res.attempts = 1
        if status == M.RETURNED:
            steps.append(("Returned to sender - refused by recipient", d))
        if status == M.DELIVERED:
            steps.append(("Delivered", d))
        for desc, c in steps:
            t += timedelta(hours=rnd.randint(3, 30))
            if t > now:
                t = now - timedelta(minutes=rnd.randint(5, 90))
            res.events.append(M.TrackingEvent(t, desc, f"{c[0]}, {c[1]} {c[2]}"))
        res.events.sort(key=lambda e: e.timestamp, reverse=True)
        res.status = status
        res.status_detail = f"[DEMO] {res.events[0].description}"
        res.attempts = res.attempts or 0
        if status in (M.EXCEPTION, M.RETURNED):
            res.exception = res.events[0].description
        if status == M.DELIVERED:
            res.delivered_at = res.events[0].timestamp
            res.pod = M.ProofOfDelivery(signed_by=rnd.choice(_SIGNERS), left_at=rnd.choice(_LEFT),
                                        address=res.destination)
        else:
            res.estimated_delivery = (now + timedelta(days=rnd.randint(0, 4))).strftime("%Y-%m-%d")
        return res

    def fetch_pod(self, result):
        pass
