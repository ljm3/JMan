"""Fake, clearly-labelled pickup results for Demo mode and tests."""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta

from .. import models as M
from ..carriers.base import NotFound
from . import CANCELLED, COMPLETED, INCOMPLETE, PROCESSING, PickupResult

SOURCE = "DEMO - fake data"
_STATES = [(COMPLETED, "Your pickup request has been successfully completed."),
           (PROCESSING, "Your pickup request has been received and a UPS driver will be scheduled to pickup "
                        "your package(s)."),
           (INCOMPLETE, "UPS attempted to pickup your shipment(s) but was unsuccessful. Please schedule a new pickup."),
           (CANCELLED, "Your pickup request has been cancelled.")]


class DemoPickups:
    name = M.UPS

    def check(self, prn: str, zip_code: str, country: str = "US") -> PickupResult:
        h = int(hashlib.sha1(f"{prn}{zip_code}".encode()).hexdigest(), 16)
        if h % 11 == 0:
            raise NotFound(f"DEMO: UPS has no pickup {prn} for ZIP {zip_code}")
        status, msg = _STATES[h % len(_STATES)]
        day = datetime(2026, 9, 21) - timedelta(days=h % 5)
        r = PickupResult(prn, M.UPS, zip_code, country, status=status, status_detail=msg, source=SOURCE)
        r.status_code = {COMPLETED: "003", PROCESSING: "002", INCOMPLETE: "004"}.get(status, "")
        r.status_time = day.replace(hour=9 + h % 8, minute=h % 60)
        r.pickup_date, r.ready_time, r.close_time = day, day.replace(hour=9), day.replace(hour=16)
        r.pieces = [("UPS Ground", 1 + h % 6)]
        r.weight = f"{10 * (1 + h % 9)} lbs"
        r.company, r.contact, r.phone = "DEMO COMPANY", "Demo Contact", "(555) 010-0000"
        r.address = f"{100 + h % 900} Demo St, Sampletown, NY {zip_code}"
        r.pickup_point = "Front Door"
        r.residential = False
        r.base_charge, r.fuel_surcharge, r.other_surcharges, r.total_charge = 9.65, 2.85, 0.0, 12.5
        return r
