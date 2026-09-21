"""Workbook adapters + tracking-column detection."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from .. import detect
from ..detect import Detection

ALL_COLUMNS = 0   # column index meaning "scan every column"


def open_workbook(location: str, settings):
    loc = (location or "").strip().strip('"')
    if loc.lower().startswith(("http://", "https://")) or "docs.google.com" in loc:
        from .gsheets import GoogleWorkbook
        return GoogleWorkbook(loc, settings)
    from .xlsx import XlsxWorkbook
    return XlsxWorkbook(loc)


@dataclass
class ColumnInfo:
    index: int                 # 1-based
    header: str
    matches: int
    non_empty: int
    score: float

    @property
    def label(self) -> str:
        from ..report import col_letter
        h = f" '{self.header}'" if self.header else ""
        return f"{col_letter(self.index)}{h} - {self.matches} cell(s) with tracking numbers"


@dataclass
class Found:
    occurrences: dict[str, list[tuple[int, int]]] = field(default_factory=dict)   # number -> [(row, col)]
    detections: dict[str, Detection] = field(default_factory=dict)
    problems: list[tuple[int, int, str]] = field(default_factory=list)              # (row, col, message)
    carrier_hints: dict[str, str] = field(default_factory=dict)                     # number -> carrier from a Carrier column

    def by_carrier(self) -> Counter:
        return Counter(d.carrier for d in self.detections.values())

    def summary(self) -> str:
        n = len(self.detections)
        if not n:
            return "No tracking numbers found."
        parts = " / ".join(f"{c} {k}" for c, k in self.by_carrier().most_common())
        dup = sum(len(v) for v in self.occurrences.values()) - n
        s = f"{n} unique tracking number{'s' if n != 1 else ''}: {parts}"
        if dup:
            s += f"  ({dup} repeated)"
        if self.problems:
            s += f"  - {len(self.problems)} cell(s) need fixing"
        return s


def header_row_index(grid: list[list]) -> int:
    """0-based index of the header row: the first non-empty row that holds no tracking numbers."""
    for i, row in enumerate(grid[:30]):
        vals = [v for v in row if v not in (None, "")]
        if not vals:
            continue
        if any(detect.scan_cell(v).detections for v in vals):
            return -1
        if sum(isinstance(v, str) for v in vals) >= max(1, len(vals) // 2):
            return i
        return -1
    return -1


def analyse_columns(grid: list[list]) -> tuple[int, list[ColumnInfo]]:
    hdr = header_row_index(grid)
    headers = grid[hdr] if hdr >= 0 else []
    width = max((len(r) for r in grid), default=0)
    stats = []
    for c in range(width):
        matches = non_empty = 0
        for row in grid[hdr + 1:]:
            v = row[c] if c < len(row) else None
            if v in (None, ""):
                continue
            non_empty += 1
            scan = detect.scan_cell(v)
            if scan.detections or scan.problem:
                matches += 1
        h = str(headers[c]).strip() if c < len(headers) and headers[c] is not None else ""
        if not matches:
            continue
        score = matches / max(non_empty, 1) + (0.5 if detect.header_is_tracking(h) else 0) + min(matches, 500) / 5000
        stats.append(ColumnInfo(c + 1, h, matches, non_empty, round(score, 3)))
    stats.sort(key=lambda s: s.score, reverse=True)
    return hdr, stats


def find_numbers(grid: list[list], column: int, header_row: int) -> Found:
    """column: 1-based index, or ALL_COLUMNS. header_row: 0-based, -1 if none."""
    f = Found()
    # Scanning every column risks picking up order/invoice numbers, so demand a verified check digit there.
    min_conf = detect.HIGH if column == ALL_COLUMNS else detect.MEDIUM
    headers = grid[header_row] if header_row >= 0 else []
    carrier_col = next((i for i, h in enumerate(headers) if detect.header_is_carrier(h)
                        and not detect.header_is_tracking(h)), None)
    for r0 in range(header_row + 1, len(grid)):
        row = grid[r0]
        cols = range(len(row)) if column == ALL_COLUMNS else [column - 1]
        hint = detect.carrier_from_text(row[carrier_col]) if carrier_col is not None and carrier_col < len(row) else None
        for c0 in cols:
            if c0 >= len(row):
                continue
            scan = detect.scan_cell(row[c0], min_conf)
            if scan.problem:
                f.problems.append((r0 + 1, c0 + 1, scan.problem))
            for d in scan.detections:
                f.occurrences.setdefault(d.number, []).append((r0 + 1, c0 + 1))
                f.detections.setdefault(d.number, d)
                if hint and d.number not in f.carrier_hints:
                    f.carrier_hints[d.number] = hint
    return f
