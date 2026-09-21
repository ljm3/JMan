"""Find the PRN column, the ZIP/address column and each pickup's ZIP code in a sheet."""
from __future__ import annotations

from dataclasses import dataclass, field

from .. import detect
from .. import models as M
from ..report import col_letter
from ..sheets import ALL_COLUMNS, header_row_index
from . import AUTO_ZIP, _OTHER_ID, header_is_address, header_is_prn, header_is_zip, ups_prns_in, zip_from_value


@dataclass
class PrnColumn:
    index: int                 # 1-based
    header: str
    matches: int
    others: int
    score: float

    @property
    def label(self) -> str:
        h = f" '{self.header}'" if self.header else ""
        extra = f" + {self.others} other" if self.others else ""
        return f"{col_letter(self.index)}{h} - {self.matches} UPS PRN(s){extra}"


@dataclass
class ZipColumn:
    index: int
    header: str
    kind: str                  # "zip" | "address"
    hits: int

    @property
    def label(self) -> str:
        h = f" '{self.header}'" if self.header else ""
        what = "ZIP codes" if self.kind == "zip" else "addresses with a ZIP"
        return f"{col_letter(self.index)}{h} - {self.hits} {what}"


@dataclass
class Pickup:
    prn: str
    carrier: str                                 # UPS, or the carrier named in the row / "Unknown"
    occurrences: list[tuple[int, int]]           # (row, col) 1-based
    zip_code: str = ""
    country: str = "US"
    zip_source: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def supported(self) -> bool:
        return self.carrier == M.UPS


@dataclass
class FoundPrns:
    pickups: dict[str, Pickup] = field(default_factory=dict)
    rows: list[int] = field(default_factory=list)         # 1-based rows holding a PRN

    def summary(self) -> str:
        if not self.pickups:
            return "No pickup request numbers found."
        ups = [p for p in self.pickups.values() if p.supported]
        other = len(self.pickups) - len(ups)
        s = f"{len(ups)} UPS pickup request number{'s' if len(ups) != 1 else ''}"
        if other:
            s += f" + {other} other (listed, not looked up)"
        no_zip = sum(1 for p in ups if not p.zip_code)
        s += f"  -  ZIP found for {len(ups) - no_zip}/{len(ups)}"
        return s


def _cell(row, c0):
    return row[c0] if c0 < len(row) else None


def analyse_prn_columns(grid: list[list]) -> tuple[int, list[PrnColumn], list[ZipColumn]]:
    hdr = header_row_index(grid)
    headers = grid[hdr] if hdr >= 0 else []
    width = max((len(r) for r in grid), default=0)
    prn_cols, zip_cols = [], []
    for c in range(width):
        h = str(headers[c]).strip() if c < len(headers) and headers[c] is not None else ""
        matches = others = non_empty = zips = addrs = 0
        for row in grid[hdr + 1:]:
            v = _cell(row, c)
            if v in (None, ""):
                continue
            non_empty += 1
            if ups_prns_in(v):
                matches += 1
            elif _other_pickup_id(v):
                others += 1
            if zip_from_value(v, zip_column=header_is_zip(h)):
                if isinstance(v, (int, float)) or len(str(v).strip()) <= 10:
                    zips += 1
                else:
                    addrs += 1
        if matches or (header_is_prn(h) and others and not header_is_zip(h)):
            score = matches / max(non_empty, 1) + (0.5 if header_is_prn(h) else 0) + min(matches, 500) / 5000
            prn_cols.append(PrnColumn(c + 1, h, matches, others if header_is_prn(h) else 0, round(score, 3)))
        if header_is_zip(h) and zips:
            zip_cols.append(ZipColumn(c + 1, h, "zip", zips))
        elif addrs and (header_is_address(h) or addrs >= max(1, non_empty // 2)):
            zip_cols.append(ZipColumn(c + 1, h, "address", addrs))
    prn_cols.sort(key=lambda s: s.score, reverse=True)
    zip_cols.sort(key=lambda z: (z.kind != "zip", -z.hits))
    return hdr, prn_cols, zip_cols


def find_prns(grid: list[list], column: int, header_row: int, zip_column: int = AUTO_ZIP,
              zip_candidates: list[ZipColumn] | None = None) -> FoundPrns:
    """column: 1-based PRN column (or ALL_COLUMNS); zip_column: 1-based, or AUTO_ZIP."""
    f = FoundPrns()
    headers = grid[header_row] if header_row >= 0 else []

    def head(c0):
        return str(headers[c0]).strip() if c0 < len(headers) and headers[c0] is not None else ""

    carrier_col = next((i for i, h in enumerate(headers) if detect.header_is_carrier(h)
                        and not detect.header_is_tracking(h) and not header_is_prn(h)), None)
    if zip_column != AUTO_ZIP:
        zip_order = [zip_column - 1]
    else:
        zip_order = [z.index - 1 for z in (zip_candidates or [])]
    prn_header = column != ALL_COLUMNS and header_is_prn(head(column - 1))

    for r0 in range(header_row + 1, len(grid)):
        row = grid[r0]
        cols = range(len(row)) if column == ALL_COLUMNS else [column - 1]
        hint = detect.carrier_from_text(_cell(row, carrier_col)) if carrier_col is not None else None
        found_here = []
        for c0 in cols:
            v = _cell(row, c0)
            if v in (None, ""):
                continue
            prns = ups_prns_in(v)
            carrier = M.UPS if prns and (hint in (None, M.UPS)) else (hint or "Unknown")
            if not prns and prn_header and _other_pickup_id(v):
                prns = [v.strip().upper()]
                carrier = hint or "Unknown"
            for p in prns:
                found_here.append((p, carrier, c0))
        if not found_here:
            continue
        f.rows.append(r0 + 1)
        z = _row_zip(row, zip_order, head, zip_column != AUTO_ZIP)
        for p, carrier, c0 in found_here:
            pk = f.pickups.get(p)
            if pk is None:
                pk = f.pickups[p] = Pickup(p, carrier, [])
            pk.occurrences.append((r0 + 1, c0 + 1))
            if z and not pk.zip_code:
                pk.zip_code, pk.country, pk.zip_source = z
            elif z and z[0] != pk.zip_code:
                pk.notes.append(f"Row {r0 + 1} has a different ZIP ({z[0]}); used {pk.zip_code}")
    return f


def _other_pickup_id(v) -> bool:
    """A non-UPS pickup confirmation (FedEx/USPS) in a PRN column - but not a tracking number."""
    if not isinstance(v, str):
        return False
    t = v.strip().upper()
    return bool(_OTHER_ID.match(t)) and any(ch.isdigit() for ch in t) and not detect.scan_cell(t).detections


def _row_zip(row, zip_order, head, chosen: bool) -> tuple[str, str, str] | None:
    for c0 in zip_order:
        v = _cell(row, c0)
        z = zip_from_value(v, zip_column=header_is_zip(head(c0)) or (chosen and len(str(v or "").strip()) <= 5))
        if z:
            where = f"'{head(c0)}'" if head(c0) else f"column {col_letter(c0 + 1)}"
            how = "ZIP column" if header_is_zip(head(c0)) or len(str(v).strip()) <= 10 else "parsed from address"
            return z[0], z[1], f"{how} {where}"
    if chosen:
        return None
    # No ZIP/address column recognised: look for an address anywhere in the row.
    for c0, v in enumerate(row):
        if isinstance(v, str) and len(v) > 10:
            z = zip_from_value(v)
            if z:
                where = f"'{head(c0)}'" if head(c0) else f"column {col_letter(c0 + 1)}"
                return z[0], z[1], f"parsed from address {where}"
    return None
