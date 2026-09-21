"""Pickup request numbers (PRNs): find them in a sheet, work out each pickup's ZIP, describe the result."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from .. import detect
from .. import models as M

AUTO_ZIP = 0            # zip-column choice meaning "ZIP column if there is one, else parse an address"

# Pickup status categories written to the Status column (UPS's page shows the first three in capitals).
COMPLETED = "Completed"
PROCESSING = "Processing"
INCOMPLETE = "Incomplete"
CANCELLED = "Cancelled"
EN_ROUTE = "Driver En Route"
NOT_SUPPORTED = "Not Supported"
NO_ZIP = "No ZIP Code"
NOT_FOUND = M.NOT_FOUND
ERROR = M.ERROR
UNKNOWN = M.UNKNOWN

STATUS_FILL = {
    COMPLETED: "C6EFCE", EN_ROUTE: "DDEBF7", PROCESSING: "FFF2CC", INCOMPLETE: "FFC7CE", CANCELLED: "EDEDED",
    NOT_SUPPORTED: "EDEDED", NO_ZIP: "F4B084", NOT_FOUND: "F4B084", ERROR: "F4B084", UNKNOWN: "EDEDED",
}

# UPS orderStatusCode values seen on ups.com/ipr/pickup-status-check (2026-09-21).
_UPS_CODES = {"002": PROCESSING, "003": COMPLETED, "004": INCOMPLETE}


def status_from_ups(code: str | None, message: str | None) -> str:
    m = (message or "").lower()
    if "cancel" in m:
        return CANCELLED
    if code in _UPS_CODES:
        return _UPS_CODES[code]
    if "complete" in m and "incomplete" not in m:
        return COMPLETED
    if "unsuccessful" in m or "attempted" in m or "missed" in m or "incomplete" in m:
        return INCOMPLETE
    if "driver" in m and ("route" in m or "dispatch" in m):
        return EN_ROUTE
    if "received" in m or "scheduled" in m or "processing" in m:
        return PROCESSING
    return UNKNOWN


@dataclass
class PickupResult:
    prn: str
    carrier: str = M.UPS
    zip_code: str = ""
    country: str = "US"
    status: str = UNKNOWN
    status_code: str = ""
    status_detail: str = ""
    status_time: datetime | None = None
    pickup_date: datetime | None = None
    ready_time: datetime | None = None
    close_time: datetime | None = None
    pieces: list[tuple[str, int]] = field(default_factory=list)      # (service, quantity)
    weight: str = ""
    company: str = ""
    contact: str = ""
    phone: str = ""
    address: str = ""
    pickup_point: str = ""
    residential: bool | None = None
    notify_email: str = ""
    instructions: str = ""
    base_charge: float | None = None
    fuel_surcharge: float | None = None
    other_surcharges: float | None = None
    total_charge: float | None = None
    tracking_numbers: list[str] = field(default_factory=list)       # UPS doesn't list these for a pickup (yet)
    source: str = ""
    checked_at: datetime = field(default_factory=datetime.now)
    error: str = ""

    @property
    def piece_count(self) -> int | None:
        return sum(q for _, q in self.pieces) if self.pieces else None

    def services(self) -> str:
        return ", ".join(f"{s} x {q}" for s, q in self.pieces)

    def window(self) -> str:
        if not (self.ready_time and self.close_time):
            return ""
        return f"{self.ready_time:%I:%M %p} - {self.close_time:%I:%M %p}".replace(" 0", " ").lstrip("0")


# ---------------------------------------------------------------- PRN detection
# UPS PRNs are 11 characters, digits and capital letters, starting with a digit (e.g. 2900000AA11).
_UPS_PRN = re.compile(r"(?<![0-9A-Z])\d[0-9A-Z]{10}(?![0-9A-Z])")
_OTHER_ID = re.compile(r"^[A-Z0-9][A-Z0-9-]{3,24}$")
_PRN_HEADER = re.compile(r"\bprn\b|pick[\s-]*up|request\s*(no|num|#|number)|\bconf(irmation)?\b", re.I)


def is_ups_prn(token: str) -> bool:
    t = (token or "").strip().upper()
    if not _UPS_PRN.fullmatch(t):
        return False
    if t.isdigit() and not t.startswith("29"):
        return False            # an 11-digit number is more likely a phone/account number than a PRN
    return not any(d.at_least(detect.HIGH) for d in detect.scan_cell(t).detections)


def ups_prns_in(value) -> list[str]:
    if value is None or isinstance(value, (int, float, datetime)):
        return []
    return [t for t in _UPS_PRN.findall(str(value).upper()) if is_ups_prn(t)]


def header_is_prn(h) -> bool:
    return bool(h) and bool(_PRN_HEADER.search(str(h)))


# ---------------------------------------------------------------- ZIP codes
_STATES = set("AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY "
              "NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY PR VI GU AS MP AA AE AP".split())
_ZIP_HEADER = re.compile(r"\bzip|postal|post\s*code", re.I)
_ADDR_HEADER = re.compile(r"address|addr\b|street|location|ship\s*from|origin|pick[\s-]*up\s*(at|from|site)", re.I)
_ZIP_ONLY = re.compile(r"(\d{5})(?:-?\d{4})?")
_CA_POSTAL = re.compile(r"\b([ABCEGHJ-NPRSTVXY]\d[A-Z])\s?(\d[A-Z]\d)\b")
_ZIP_IN_TEXT = re.compile(r"(?<![\d-])(\d{5})(?:-\d{4})?(?![\d-])")


def zip_from_value(value, zip_column: bool = False) -> tuple[str, str] | None:
    """(zip, country) from a ZIP cell or a full address; None when there isn't one.

    zip_column=True accepts 3-4 digit numbers as ZIPs that lost their leading zeros in Excel (7030 -> 07030).
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, int):
        if 100_000_000 <= value <= 999_999_999 or (zip_column and 1_000_000 <= value < 100_000_000):
            return str(value).zfill(9)[:5], "US"            # ZIP+4 typed as one number
        if 10_000 <= value <= 99_999 or (zip_column and 100 <= value < 10_000):
            return str(value).zfill(5), "US"
        return None
    s = str(value).strip().upper()
    if not s:
        return None
    m = _ZIP_ONLY.fullmatch(s)
    if m:
        return m.group(1), "US"
    if zip_column and s.isdigit() and 3 <= len(s) <= 4:
        return s.zfill(5), "US"
    m = _CA_POSTAL.search(s)
    if m and (zip_column or "CANADA" in s or re.search(r"\b(ON|QC|BC|AB|MB|SK|NS|NB|NL|PE|YT|NT|NU)\b", s)):
        return f"{m.group(1)} {m.group(2)}", "CA"
    return _zip_from_address(s)


def _zip_from_address(s: str) -> tuple[str, str] | None:
    cands = list(_ZIP_IN_TEXT.finditer(s))
    if not cands:
        return None
    # Best: a ZIP right after a state code ("NEW YORK, NY 10016"); otherwise the last 5-digit group that isn't
    # the house number at the very start of the address.
    for m in reversed(cands):
        before = re.findall(r"[A-Z]+", s[max(0, m.start() - 6):m.start()])
        if before and before[-1] in _STATES:
            return m.group(1), "US"
    last = cands[-1]
    if last.start() == 0 or not s[:last.start()].strip(" ,#"):
        return None
    return last.group(1), "US"


def header_is_zip(h) -> bool:
    return bool(h) and bool(_ZIP_HEADER.search(str(h)))


def header_is_address(h) -> bool:
    return bool(h) and bool(_ADDR_HEADER.search(str(h))) and not header_is_zip(h)
