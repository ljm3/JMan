"""PRN run: read sheet -> find PRNs + ZIPs -> UPS pickup status -> results tab + links both ways + columns."""
from __future__ import annotations

import logging
import threading
import time
from collections import Counter
from dataclasses import dataclass, field

from .. import models as M
from ..carriers.base import CarrierError, NotFound
from ..pipeline import Cancelled, _check, start_log
from ..report import OutCell, col_letter
from ..sheets import open_workbook
from . import ERROR, NO_ZIP, NOT_FOUND, NOT_SUPPORTED, STATUS_FILL, AUTO_ZIP, PickupResult
from .sheet import FoundPrns, analyse_prn_columns, find_prns
from .ups_web import PAGE_URL

log = logging.getLogger("tracking_checker")

# Columns added to the right of the data on the original tab (re-runs reuse them by header name).
BACK_STATUS, BACK_PIECES, BACK_TRACKING = "PRN Status", "PRN Pieces", "PRN Tracking #"
NO_TRACKING_NOTE = "UPS doesn't list tracking numbers for a pickup"


@dataclass
class PrnRequest:
    location: str
    sheet: str
    column: int                         # 1-based PRN column, or sheets.ALL_COLUMNS
    zip_column: int = AUTO_ZIP          # 1-based ZIP/address column, or AUTO_ZIP
    status_tab: str = "PRN Status"
    write_back: bool = True             # add PRN Status / Pieces / Tracking # columns to the original tab
    demo: bool = False


@dataclass
class PrnSummary:
    saved_to: str = ""
    status_tab: str = ""
    total: int = 0
    by_status: Counter = field(default_factory=Counter)
    columns_added: str = ""
    backup: str = ""
    log_file: str = ""
    notes: list[str] = field(default_factory=list)
    seconds: float = 0.0

    def text(self) -> str:
        lines = [f"Checked {self.total} pickup request number(s) in {self.seconds:.0f}s.",
                 "By status: " + ", ".join(f"{k} {v}" for k, v in self.by_status.most_common()),
                 f"Results tab: '{self.status_tab}' in {self.saved_to}"]
        if self.columns_added:
            lines.append(f"Added to your tab: {self.columns_added}")
        if self.backup:
            lines.append(f"Backup of the original workbook: {self.backup}")
        lines += [f"Note: {n}" for n in self.notes]
        lines.append(f"Log: {self.log_file}")
        return "\n".join(lines)


def run_prn(req: PrnRequest, settings, progress=None, cancel: threading.Event | None = None,
            workbook=None, client=None) -> PrnSummary:
    t0 = time.time()
    cancel = cancel or threading.Event()
    say = progress or (lambda msg, frac=None: None)
    log_path, handler = start_log()
    summary = PrnSummary(log_file=str(log_path), status_tab=req.status_tab)
    browser = None
    try:
        if client is None:
            if req.demo:
                from .demo import DemoPickups
                client = DemoPickups()
            else:
                from ..carriers.web import LazyBrowser
                from .ups_web import UPSPickupWeb
                browser = LazyBrowser(settings, attention=lambda m: (log.info(m), say(m, None)), cancel=cancel)
                client = UPSPickupWeb(browser, settings, capture_pod=False, cancel=cancel)
        _run(req, settings, say, cancel, workbook, summary, client)
    except Exception:
        log.exception("PRN run failed")
        raise
    finally:
        if browser is not None:
            browser.close()
        summary.seconds = time.time() - t0
        log.info("Summary:\n%s", summary.text())
        log.removeHandler(handler)
        handler.close()
    return summary


def _run(req, settings, say, cancel, wb, summary, client):
    log.info("PRN run started: %s | tab=%s | column=%s | zip column=%s | demo=%s", req.location, req.sheet,
             req.column, req.zip_column or "auto", req.demo)
    if req.sheet.strip().lower() == req.status_tab.strip().lower():
        raise ValueError("Pick the tab that holds your pickup request numbers, not the results tab.")
    say("Opening spreadsheet ...", 0.02)
    wb = wb or open_workbook(req.location, settings)
    grid = wb.read_tab(req.sheet)
    hdr, _, zip_cols = analyse_prn_columns(grid)
    found = find_prns(grid, req.column, hdr, req.zip_column, zip_cols)
    if not found.pickups:
        raise ValueError("No pickup request numbers were found in that tab/column.")
    log.info("Found %s", found.summary())
    say(found.summary(), 0.05)

    pickups = sorted(found.pickups.values(), key=lambda p: p.occurrences[0])
    results: dict[str, PickupResult] = {}
    todo = []
    for p in pickups:
        if not p.supported:
            results[p.prn] = PickupResult(p.prn, p.carrier, status=NOT_SUPPORTED, source="-",
                                          error=f"{p.carrier} pickups aren't looked up - only UPS")
        elif not p.zip_code:
            results[p.prn] = PickupResult(p.prn, M.UPS, status=NO_ZIP, source="-",
                                          error="No ZIP code found on this row - UPS needs the pickup ZIP. "
                                                "Pick the ZIP/address column on the PRN tab.")
        else:
            todo.append(p)

    for i, p in enumerate(todo, 1):
        _check(cancel)
        say(f"UPS pickup {p.prn} (ZIP {p.zip_code}) ... {i}/{len(todo)}", 0.08 + 0.75 * (i - 1) / max(len(todo), 1))
        try:
            r = client.check(p.prn, p.zip_code, p.country)
        except NotFound as e:
            r = PickupResult(p.prn, M.UPS, p.zip_code, p.country, status=NOT_FOUND, source="-", error=str(e))
        except Exception as e:  # noqa: BLE001 - reported per PRN
            if cancel.is_set():
                raise Cancelled("Cancelled by user") from e
            r = PickupResult(p.prn, M.UPS, p.zip_code, p.country, status=ERROR, source="-",
                             error=str(e) if isinstance(e, CarrierError) else f"{type(e).__name__}: {e}")
        results[p.prn] = r
        log.info("%s: %s%s", p.prn, r.status, f" - {r.error}" if r.error else "")
    blocked = getattr(client, "blocked_reason", "")
    if blocked:
        summary.notes.append(f"UPS website lookups blocked - {blocked}")

    # ---- write
    _check(cancel)
    say("Writing the results tab ...", 0.88)
    headers, rows, widths = build(pickups, results, req.sheet)
    summary.notes += wb.begin_write()
    wb.write_status_tab(req.status_tab, headers, rows, widths, {"Tracking Numbers": _TRACKING_HELP})
    links = [(row, col, i + 2) for i, p in enumerate(pickups) for row, col in p.occurrences]
    wb.link_source_cells(req.sheet, links, req.status_tab, tooltip="Show pickup status")
    if req.write_back:
        cols = write_back_columns(grid, hdr, found, results)
        wb.write_columns(req.sheet, hdr + 1, cols, first_row=hdr + 2, last_row=len(grid))
        summary.columns_added = ", ".join(f"{h} ({col_letter(c)})" for c, h, _ in cols)
    say("Saving ...", 0.96)
    summary.saved_to = wb.save()
    summary.backup = str(getattr(wb, "backup_path", "") or "")

    summary.total = len(pickups)
    summary.by_status = Counter(results[p.prn].status for p in pickups)
    if not any(results[p.prn].tracking_numbers for p in pickups):
        summary.notes.append(NO_TRACKING_NOTE + " (only the package count) - the PRN Pieces column shows it.")
    if req.demo:
        summary.notes.append("DEMO MODE - every result is fake sample data, clearly labelled in the Data Source column.")
    say("Done.", 1.0)


_TRACKING_HELP = ("UPS's pickup record holds the package count and service, not the tracking numbers of the "
                  "packages collected. Tracking numbers appear here when a source for them is available.")

HEADERS = [
    ("Pickup Request Number", 22), ("Carrier", 9), ("Status", 15), ("Status Detail", 50), ("Status Updated", 17),
    ("Pickup Date", 12), ("Pickup Window", 18), ("Pieces", 8), ("Service", 22), ("Weight", 10),
    ("Tracking Numbers", 26), ("Company", 28), ("Contact", 18), ("Phone", 15), ("Pickup Address", 40),
    ("Pickup Point", 14), ("Residential?", 11), ("Notification Email", 28), ("Special Instructions", 30),
    ("Base Charge", 11), ("Fuel Surcharge", 11), ("Other Surcharges", 11), ("Total Charge", 11),
    ("ZIP Used", 9), ("ZIP Found In", 30), ("UPS Status Code", 10), ("Check on UPS", 16), ("Source Location", 18),
    ("Data Source", 22), ("Checked At", 17), ("Error / Notes", 50),
]


def build(pickups, results: dict[str, PickupResult], source_tab: str):
    headers = [h for h, _ in HEADERS]
    widths = dict(HEADERS)
    rows = []
    for p in pickups:
        r = results[p.prn]
        first = p.occurrences[0]
        looked_up = r.source not in ("", "-")
        where = "; ".join(f"{col_letter(c)}{rw}" for rw, c in p.occurrences)
        tracking = "\n".join(r.tracking_numbers) or (f"None listed by UPS ({r.piece_count} package(s))"
                                                     if looked_up and r.piece_count else "")
        money = lambda v: v if v is not None else ""  # noqa: E731
        rows.append([
            OutCell(r.prn, goto=(source_tab, first[0], first[1])),
            OutCell(r.carrier),
            OutCell(r.status, fill=STATUS_FILL.get(r.status, "")),
            OutCell(r.status_detail),
            OutCell(r.status_time),
            OutCell(r.pickup_date.date() if r.pickup_date else None),
            OutCell(r.window()),
            OutCell(r.piece_count),
            OutCell(r.services()),
            OutCell(r.weight),
            OutCell(tracking),
            OutCell(r.company),
            OutCell(r.contact),
            OutCell(r.phone),
            OutCell(r.address),
            OutCell(r.pickup_point),
            OutCell("" if r.residential is None else "Yes" if r.residential else "No"),
            OutCell(r.notify_email),
            OutCell(r.instructions),
            OutCell(money(r.base_charge)),
            OutCell(money(r.fuel_surcharge)),
            OutCell(money(r.other_surcharges)),
            OutCell(money(r.total_charge)),
            OutCell(p.zip_code),
            OutCell(p.zip_source),
            OutCell(r.status_code),
            OutCell("Open UPS pickup status" if p.supported else "", url=PAGE_URL if p.supported else ""),
            OutCell(f"'{source_tab}'!{where}"),
            OutCell(r.source),
            OutCell(r.checked_at),
            OutCell("; ".join(x for x in [r.error] + p.notes if x)),
        ])
    return headers, rows, widths


def write_back_columns(grid, hdr: int, found: FoundPrns, results: dict[str, PickupResult]):
    """[(col, header, {row: OutCell})] for the columns added to the right of the data on the original tab."""
    per_row: dict[int, list[PickupResult]] = {}
    for p in found.pickups.values():
        for row, _ in p.occurrences:
            per_row.setdefault(row, [])
            if results[p.prn] not in per_row[row]:
                per_row[row].append(results[p.prn])
    n_track = max((len(r.tracking_numbers) for rs in per_row.values() for r in rs), default=0)
    wanted = [BACK_STATUS, BACK_PIECES] + [f"{BACK_TRACKING} {i}" for i in range(1, n_track + 1)]
    cols = plan_columns(grid, hdr, wanted)
    status_vals, piece_vals, track_vals = {}, {}, [dict() for _ in range(n_track)]
    for row, rs in per_row.items():
        status_vals[row] = OutCell(" / ".join(r.status for r in rs), fill=STATUS_FILL.get(rs[0].status, "")
                                   if len(rs) == 1 else "")
        pieces = [r.piece_count for r in rs if r.piece_count is not None]
        piece_vals[row] = OutCell(sum(pieces) if pieces else "")
        nums = [n for r in rs for n in r.tracking_numbers]
        for i, n in enumerate(nums[:n_track]):
            track_vals[i][row] = OutCell(n)
    out = [(cols[BACK_STATUS], BACK_STATUS, status_vals), (cols[BACK_PIECES], BACK_PIECES, piece_vals)]
    out += [(cols[f"{BACK_TRACKING} {i + 1}"], f"{BACK_TRACKING} {i + 1}", track_vals[i]) for i in range(n_track)]
    return out


def plan_columns(grid, hdr: int, wanted: list[str]) -> dict[str, int]:
    """Reuse columns already headed with our names (a re-run); otherwise the first empty columns to the right."""
    headers = grid[hdr] if hdr >= 0 else []
    existing = {str(h).strip().lower(): i + 1 for i, h in enumerate(headers) if h not in (None, "")}
    used = 0
    for row in grid:
        for i in range(len(row) - 1, -1, -1):
            if row[i] not in (None, ""):
                used = max(used, i + 1)
                break
    out, nxt = {}, used + 1
    for h in wanted:
        if h.lower() in existing:
            out[h] = existing[h.lower()]
        else:
            out[h] = nxt
            nxt += 1
    return out
