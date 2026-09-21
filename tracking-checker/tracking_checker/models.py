from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

UPS, FEDEX, USPS = "UPS", "FedEx", "USPS"
SUPPORTED_CARRIERS = (UPS, FEDEX, USPS)

# Normalized status categories written to the Status column.
DELIVERED = "Delivered"
OUT_FOR_DELIVERY = "Out for Delivery"
IN_TRANSIT = "In Transit"
EXCEPTION = "Exception"
RETURNED = "Returned to Sender"
LABEL_CREATED = "Label Created"
PICKUP_READY = "Available for Pickup"
NOT_FOUND = "Not Found"
ERROR = "Error"
UNKNOWN = "Unknown"


@dataclass
class TrackingEvent:
    timestamp: datetime | None
    description: str
    location: str = ""
    code: str = ""

    def line(self) -> str:
        ts = self.timestamp.strftime("%Y-%m-%d %H:%M") if self.timestamp else "(no date)"
        parts = [ts, self.description]
        if self.location:
            parts.append(self.location)
        return " | ".join(parts)


@dataclass
class ProofOfDelivery:
    signed_by: str = ""
    left_at: str = ""
    address: str = ""
    signature_image: bytes | None = None
    document: bytes | None = None          # carrier-issued POD letter (FedEx SPOD PDF, UPS POD)
    document_label: str = ""
    photo: bytes | None = None
    page_capture: bytes | None = None                 # PDF of the carrier's own tracking page (website mode)
    files: list[str] = field(default_factory=list)   # saved file paths, best first
    link: str = ""                                    # what the POD cell links to
    kind: str = ""                                    # human description of the best POD


@dataclass
class TrackingResult:
    tracking_number: str
    carrier: str
    status: str = UNKNOWN
    status_detail: str = ""
    service: str = ""
    delivered_at: datetime | None = None
    ship_date: datetime | None = None
    estimated_delivery: str = ""
    origin: str = ""
    destination: str = ""
    weight: str = ""
    attempts: int | None = None
    exception: str = ""
    events: list[TrackingEvent] = field(default_factory=list)
    pod: ProofOfDelivery | None = None
    source: str = "Live API"
    error: str = ""
    identified_by: str = ""
    checked_at: datetime = field(default_factory=datetime.now)
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def delivered(self) -> bool:
        return self.status == DELIVERED

    @property
    def last_event(self) -> TrackingEvent | None:
        dated = [e for e in self.events if e.timestamp]
        if dated:
            return max(dated, key=lambda e: e.timestamp)
        return self.events[0] if self.events else None

    @property
    def first_event(self) -> TrackingEvent | None:
        dated = [e for e in self.events if e.timestamp]
        return min(dated, key=lambda e: e.timestamp) if dated else None

    def days_in_transit(self) -> float | None:
        start = self.ship_date or (self.first_event.timestamp if self.first_event else None)
        if not start:
            return None
        end = self.delivered_at or datetime.now()
        start = _naive(start)
        end = _naive(end)
        return round((end - start).total_seconds() / 86400, 1)

    def to_dict(self) -> dict:
        d = asdict(self)
        if self.pod:
            for k in ("signature_image", "document", "photo", "page_capture"):
                d["pod"][k] = None
        return _jsonable(d)

    @classmethod
    def from_dict(cls, d: dict) -> "TrackingResult":
        d = dict(d)
        d["events"] = [TrackingEvent(timestamp=_dt(e["timestamp"]), description=e["description"],
                                     location=e.get("location", ""), code=e.get("code", ""))
                       for e in d.get("events", [])]
        if d.get("pod"):
            d["pod"] = ProofOfDelivery(**d["pod"])
        for k in ("delivered_at", "ship_date", "checked_at"):
            d[k] = _dt(d.get(k))
        if d.get("checked_at") is None:
            d["checked_at"] = datetime.now()
        return cls(**d)


def _naive(dt: datetime) -> datetime:
    if dt.tzinfo is not None:
        return dt.astimezone().replace(tzinfo=None)
    return dt


def _dt(v):
    if v is None or isinstance(v, datetime):
        return v
    return datetime.fromisoformat(v)


def _jsonable(o):
    if isinstance(o, dict):
        return {k: _jsonable(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_jsonable(v) for v in o]
    if isinstance(o, datetime):
        return o.isoformat()
    return o
