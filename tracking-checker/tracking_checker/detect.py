"""Identify tracking numbers inside cell text and work out which carrier issued them.

Detection is pattern + check digit. A check-digit pass gives "high" confidence; a
format-only match gives "medium". When a number's format is shared between carriers,
``carriers`` lists every candidate in order of likelihood and the pipeline tries the
next one if the first carrier reports "not found".
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .models import FEDEX, UPS, USPS

HIGH, MEDIUM, LOW = "high", "medium", "low"
_RANK = {HIGH: 3, MEDIUM: 2, LOW: 1}


@dataclass
class Detection:
    number: str
    carriers: list[str]
    confidence: str
    reason: str
    warnings: list[str] = field(default_factory=list)

    @property
    def carrier(self) -> str:
        return self.carriers[0] if self.carriers else "Unknown"

    def at_least(self, level: str) -> bool:
        return _RANK[self.confidence] >= _RANK[level]


def normalize(text: str) -> str:
    return re.sub(r"[\s\-]", "", str(text)).upper()


# ---------------------------------------------------------------- check digits
def ups_check_ok(n: str) -> bool:
    body, check = n[2:-1], n[-1]
    if not check.isdigit():
        return False
    total = 0
    for i, ch in enumerate(body):
        v = int(ch) if ch.isdigit() else (ord(ch) - ord("A") + 2) % 10
        total += v * (2 if i % 2 else 1)
    return (10 - total % 10) % 10 == int(check)


def fedex12_check_ok(n: str) -> bool:
    digits = [int(c) for c in n[:11]]
    weights = [1, 3, 7]
    total = sum(d * weights[i % 3] for i, d in enumerate(reversed(digits)))
    return (total % 11) % 10 == int(n[11])


def mod10_31_check_ok(n: str) -> bool:
    """GS1-style mod 10 (weights 3,1 from the right). Used by USPS IMpb and FedEx Ground."""
    body, check = n[:-1], int(n[-1])
    total = sum(int(d) * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(body)))
    return (10 - total % 10) % 10 == check


def s10_check_ok(n: str) -> bool:
    """UPU S10 international format, e.g. EC123456789US."""
    digits = [int(c) for c in n[2:10]]
    check = int(n[10])
    total = sum(d * w for d, w in zip(digits, [8, 6, 4, 2, 3, 5, 9, 7]))
    c = 11 - total % 11
    c = 0 if c == 10 else 5 if c == 11 else c
    return c == check


# ---------------------------------------------------------------- classification
_USPS_420 = re.compile(r"^420\d{5}(?:\d{4})?(9\d{19,21})$")


def classify(raw: str) -> Detection | None:
    """Classify one already-isolated token. Returns None if it is not a tracking number."""
    n = normalize(raw)
    if not n:
        return None

    m = _USPS_420.match(n)
    if m:  # USPS barcode with the 420+ZIP routing prefix - strip it
        inner = classify(m.group(1))
        if inner:
            inner.reason += " (420+ZIP routing prefix removed)"
            return inner

    if re.fullmatch(r"1Z[0-9A-Z]{16}", n):
        if ups_check_ok(n):
            return Detection(n, [UPS], HIGH, "UPS 1Z format, check digit valid")
        return Detection(n, [UPS], MEDIUM, "UPS 1Z format (check digit did not verify)")

    if re.fullmatch(r"T\d{10}", n):
        return Detection(n, [UPS], MEDIUM, "UPS T-number format")

    if re.fullmatch(r"[A-Z]{2}\d{9}[A-Z]{2}", n):
        ok = s10_check_ok(n)
        conf = HIGH if ok else MEDIUM
        if n.endswith("US"):
            return Detection(n, [USPS], conf, "USPS international (S10) format" + (", check digit valid" if ok else ""))
        return Detection(n, [USPS], LOW, f"International postal (S10) number from {n[-2:]}; USPS may track the US leg",
                         warnings=["Foreign postal number - USPS may only have the US portion."])

    if not n.isdigit():
        return None

    L = len(n)
    if L == 12:
        if fedex12_check_ok(n):
            return Detection(n, [FEDEX], HIGH, "FedEx 12-digit (Express/Ground), check digit valid")
        return Detection(n, [FEDEX], MEDIUM, "FedEx 12-digit format")
    if L == 15:
        conf = HIGH if mod10_31_check_ok(n) else MEDIUM
        return Detection(n, [FEDEX], conf, "FedEx 15-digit Ground format")
    if L == 22 and n.startswith("96"):
        return Detection(n, [FEDEX], MEDIUM, "FedEx Ground '96' barcode format")
    if L == 34:
        return Detection(n, [FEDEX], MEDIUM, "FedEx 34-digit barcode format")
    if L in (20, 22, 26) and n[0] == "9" and n[1] in "12345":
        ok = mod10_31_check_ok(n)
        conf = HIGH if ok else MEDIUM
        # 92/93 numbers are also issued for FedEx Ground Economy (ex-SmartPost) parcels that USPS delivers.
        carriers = [USPS, FEDEX] if n[:2] in ("92", "93") else [USPS]
        return Detection(n, carriers, conf, f"USPS {L}-digit IMpb format" + (", check digit valid" if ok else ""))
    if L == 20 and (n[0] == "7" or n[:2] in ("03", "23")):
        conf = HIGH if mod10_31_check_ok(n) else MEDIUM
        return Detection(n, [USPS], conf, "USPS 20-digit Certified/Registered format")
    if L == 10:
        return Detection(n, ["DHL"], LOW, "10-digit number - possibly DHL Express (not supported)",
                         warnings=["DHL is not a configured carrier."])
    return None


# ---------------------------------------------------------------- extraction
_TOKEN_PATTERNS = [
    r"1Z[0-9A-Z]{16}",
    r"T\d{10}",
    r"[A-Z]{2}\d{9}[A-Z]{2}",
    r"(?:\d[ \-]?){10,34}",
]
_TOKEN_RE = re.compile(r"(?<![0-9A-Z])(?:" + "|".join(_TOKEN_PATTERNS) + r")(?![0-9A-Z])", re.I)
_SCI_RE = re.compile(r"^\d(?:\.\d+)?E\+\d+$", re.I)


@dataclass
class CellScan:
    detections: list[Detection]
    problem: str = ""


def scan_cell(value, min_confidence: str = MEDIUM) -> CellScan:
    """Find tracking numbers in a spreadsheet cell value (str, int, float or None)."""
    if value is None:
        return CellScan([])
    if isinstance(value, bool):
        return CellScan([])
    if isinstance(value, float):
        if value >= 1e15:
            return CellScan([], _precision_problem(value))
        if value.is_integer():
            value = str(int(value))
        else:
            return CellScan([])
    elif isinstance(value, int):
        value = str(value)
    text = str(value).strip()
    if not text:
        return CellScan([])
    if _SCI_RE.match(text):
        return CellScan([], _precision_problem(text))

    found: list[Detection] = []
    whole = classify(text) if len(text) <= 45 else None
    if whole:
        found.append(whole)
    else:
        for m in _TOKEN_RE.finditer(text.upper()):
            d = classify(m.group(0))
            if d:
                found.append(d)
                continue
            for part in re.split(r"[\s\-]+", m.group(0)):
                d = classify(part)
                if d:
                    found.append(d)
    seen, out = set(), []
    for d in found:
        if d.number not in seen and d.at_least(min_confidence):
            seen.add(d.number)
            out.append(d)
    return CellScan(out)


def _precision_problem(v) -> str:
    return (f"Cell holds {v!r} as a number, so digits past the 15th were lost. "
            "Format the column as Text and re-enter the tracking number.")


_HEADER_HINT = re.compile(r"track|awb|waybill|shipment\s*(id|no|#)|pro\s*#|pkg|package", re.I)
_CARRIER_HINT = re.compile(r"carrier|ship\s*via|shipper|courier|service", re.I)


def header_is_tracking(h) -> bool:
    return bool(h) and bool(_HEADER_HINT.search(str(h)))


def header_is_carrier(h) -> bool:
    return bool(h) and bool(_CARRIER_HINT.search(str(h)))


def carrier_from_text(text) -> str | None:
    t = str(text or "").lower()
    if "fedex" in t or "fed ex" in t or "federal express" in t:
        return FEDEX
    if re.search(r"\bups\b|united parcel", t):
        return UPS
    if "usps" in t or "postal" in t or "post office" in t:
        return USPS
    return None
