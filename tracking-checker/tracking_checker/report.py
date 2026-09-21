"""Turn tracking results into the header row + data rows of the status tab."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from . import models as M
from .detect import Detection

MAX_CELL = 32000  # Excel's hard limit is 32,767 characters per cell


@dataclass
class OutCell:
    value: Any = ""
    url: str = ""                                   # web page or file link
    goto: tuple[str, int, int] | None = None         # (tab, row, col) link inside the workbook, 1-based
    fill: str = ""                                   # background colour hex, e.g. "C6EFCE"


@dataclass
class Entry:
    number: str
    detection: Detection | None
    occurrences: list[tuple[int, int]]              # (row, col) 1-based in the source tab
    result: M.TrackingResult
    pod_link: str = ""
    other_pod_links: list[str] = field(default_factory=list)


STATUS_FILL = {
    M.DELIVERED: "C6EFCE", M.OUT_FOR_DELIVERY: "DDEBF7", M.IN_TRANSIT: "FFF2CC", M.PICKUP_READY: "FFF2CC",
    M.LABEL_CREATED: "EDEDED", M.EXCEPTION: "FFC7CE", M.RETURNED: "FFC7CE", M.NOT_FOUND: "F4B084",
    M.ERROR: "F4B084", M.UNKNOWN: "EDEDED",
}

BASE_HEADERS = [
    ("Tracking Number", 26), ("Carrier", 9), ("Identified By", 34), ("Service", 24), ("Status", 17),
    ("Status Detail", 44), ("Delivered?", 10), ("Delivered Date/Time", 18), ("Signed By", 20),
    ("Left At / Delivery Location", 24), ("Delivery Address", 28), ("Ship Date", 12), ("Origin", 26),
    ("Destination", 26), ("Estimated Delivery", 20), ("Last Event", 40), ("Last Event Date/Time", 18),
    ("Last Event Location", 26), ("Days in Transit", 9), ("Delivery Attempts", 9), ("Exception / Alert", 36),
    ("Weight", 10), ("Event Count", 8), ("Full Tracking History", 90), ("Proof of Delivery", 22),
    ("POD Type", 36), ("Other POD Files", 30), ("Carrier Tracking Page", 18), ("Source Location", 18),
    ("Data Source", 22), ("Checked At", 17), ("Error / Notes", 50),
]


def col_letter(n: int) -> str:
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def build(entries: list[Entry], source_tab: str, extra_columns: list[str], tracking_url) -> tuple[list[str], list[list[OutCell]], dict[str, int]]:
    headers = [h for h, _ in BASE_HEADERS] + list(extra_columns)
    widths = dict(BASE_HEADERS)
    for c in extra_columns:
        widths[c] = max(14, min(40, len(c) + 4))
    rows = []
    for e in entries:
        r = e.result
        pod = r.pod or M.ProofOfDelivery()
        last = r.last_event
        first_occ = e.occurrences[0] if e.occurrences else None
        history = "\n".join(ev.line() for ev in sorted(r.events, key=lambda x: x.timestamp or datetime.min, reverse=True))
        if len(history) > MAX_CELL:
            history = history[:MAX_CELL] + "\n... (truncated - see the POD summary / carrier page for the rest)"
        dit = r.days_in_transit()
        url = tracking_url(r.carrier, r.tracking_number)
        where = "; ".join(f"{col_letter(c)}{rw}" for rw, c in e.occurrences)
        notes = [x for x in [r.error] + (e.detection.warnings if e.detection else []) if x]
        cells = [
            OutCell(r.tracking_number, goto=(source_tab, first_occ[0], first_occ[1]) if first_occ else None),
            OutCell(r.carrier),
            OutCell(r.identified_by or (e.detection.reason if e.detection else "")),
            OutCell(r.service),
            OutCell(r.status, fill=STATUS_FILL.get(r.status, "")),
            OutCell(r.status_detail),
            OutCell("Yes" if r.delivered else "No"),
            OutCell(r.delivered_at),
            OutCell(pod.signed_by),
            OutCell(pod.left_at),
            OutCell(pod.address if r.delivered else ""),
            OutCell(r.ship_date.date() if isinstance(r.ship_date, datetime) else r.ship_date),
            OutCell(r.origin),
            OutCell(r.destination),
            OutCell(r.estimated_delivery),
            OutCell(last.description if last else ""),
            OutCell(last.timestamp if last else None),
            OutCell(last.location if last else ""),
            OutCell(dit),
            OutCell(r.attempts),
            OutCell(r.exception),
            OutCell(r.weight),
            OutCell(len(r.events) if r.events else None),
            OutCell(history),
            OutCell("View POD" if e.pod_link else "Not delivered" if not r.delivered
                    else "Saved locally - see Other POD Files" if pod.files else "Not available",
                    url=e.pod_link),
            OutCell(pod.kind if r.delivered else ""),
            OutCell("\n".join(e.other_pod_links)),
            OutCell("Open carrier page" if url else "", url=url),
            OutCell(f"'{source_tab}'!{where}" if where else ""),
            OutCell(r.source),
            OutCell(r.checked_at),
            OutCell("; ".join(notes)),
        ]
        for c in extra_columns:
            v = r.extra.get(c, "")
            fill = "FFEB9C" if c == "Matches Your Check" and v == "Yes" else ""
            cells.append(OutCell(v, fill=fill))
        rows.append(cells)
    return headers, rows, widths


def file_link(path: Path, base: Path) -> str:
    """Relative link (works when the workbook and POD folder are moved together)."""
    try:
        return Path(path).resolve().relative_to(base.resolve()).as_posix()
    except ValueError:
        return Path(path).resolve().as_uri()
