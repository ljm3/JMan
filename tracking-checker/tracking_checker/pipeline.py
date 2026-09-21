"""End-to-end run: read sheet -> identify -> track -> POD -> extra checks -> write tab + links."""
from __future__ import annotations

import json
import logging
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import carriers, checks, config, report
from . import models as M
from .carriers import CarrierError, NotConfigured, NotFound
from .pod import save_pod
from .sheets import find_numbers, header_row_index, open_workbook

log = logging.getLogger("tracking_checker")


@dataclass
class RunRequest:
    location: str
    sheet: str
    column: int                       # 1-based, or sheets.ALL_COLUMNS (0)
    extra_check: str = ""
    status_tab: str = "Tracking Status"
    download_pod: bool = True
    reuse_delivered: bool = True
    demo: bool = False


@dataclass
class RunSummary:
    saved_to: str = ""
    status_tab: str = ""
    total: int = 0
    by_status: Counter = field(default_factory=Counter)
    by_carrier: Counter = field(default_factory=Counter)
    pod_files: int = 0
    pod_folder: str = ""
    backup: str = ""
    log_file: str = ""
    check_engine: str = ""
    check_interpretation: str = ""
    notes: list[str] = field(default_factory=list)
    seconds: float = 0.0

    def text(self) -> str:
        lines = [f"Checked {self.total} tracking number(s) in {self.seconds:.0f}s.",
                 "By status: " + ", ".join(f"{k} {v}" for k, v in self.by_status.most_common()),
                 "By carrier: " + ", ".join(f"{k} {v}" for k, v in self.by_carrier.most_common()),
                 f"Results tab: '{self.status_tab}' in {self.saved_to}"]
        if self.pod_files:
            lines.append(f"Proof-of-delivery files: {self.pod_files} in {self.pod_folder}")
        if self.check_engine:
            lines.append(f"Extra check ({self.check_engine}): {self.check_interpretation}")
        if self.backup:
            lines.append(f"Backup of the original workbook: {self.backup}")
        lines += [f"Note: {n}" for n in self.notes]
        lines.append(f"Log: {self.log_file}")
        return "\n".join(lines)


class Cancelled(Exception):
    pass


# ---------------------------------------------------------------- logging
def start_log() -> tuple[Path, logging.Handler]:
    path = config.workspace_dir() / "logs" / f"run-{datetime.now():%Y%m%d-%H%M%S}.log"
    h = logging.FileHandler(path, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s"))
    log.addHandler(h)
    log.setLevel(logging.INFO)
    return path, h


# ---------------------------------------------------------------- cache
def _cache_path() -> Path:
    return config.workspace_dir() / "cache" / "delivered.json"


def load_cache() -> dict:
    p = _cache_path()
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
    return {}


def save_cache(cache: dict) -> None:
    _cache_path().write_text(json.dumps(cache, indent=1), encoding="utf-8")


# ---------------------------------------------------------------- run
def run(req: RunRequest, settings: config.Settings, progress=None, cancel: threading.Event | None = None,
        workbook=None) -> RunSummary:
    t0 = time.time()
    cancel = cancel or threading.Event()
    say = progress or (lambda msg, frac=None: None)
    log_path, handler = start_log()
    summary = RunSummary(log_file=str(log_path), status_tab=req.status_tab)
    from .carriers.web import LazyBrowser
    browser = LazyBrowser(settings, attention=lambda m: (log.info(m), say(m, None)), cancel=cancel)
    try:
        _run(req, settings, say, cancel, workbook, summary, browser)
    except Exception:
        log.exception("Run failed")
        raise
    finally:
        browser.close()
        summary.seconds = time.time() - t0
        log.info("Summary:\n%s", summary.text())
        log.removeHandler(handler)
        handler.close()
    return summary


def _check(cancel):
    if cancel.is_set():
        raise Cancelled("Cancelled by user")


def _run(req, settings, say, cancel, wb, summary, browser):
    log.info("Run started: %s | tab=%s | column=%s | demo=%s", req.location, req.sheet, req.column, req.demo)
    if not req.demo:
        log.info("Lookup methods: %s", ", ".join(f"{c} {config.lookup_method(c, settings)}" for c in M.SUPPORTED_CARRIERS))
    if req.sheet.strip().lower() == req.status_tab.strip().lower():
        raise ValueError("Pick the tab that holds your tracking numbers, not the results tab.")
    say("Opening spreadsheet ...", 0.02)
    wb = wb or open_workbook(req.location, settings)
    grid = wb.read_tab(req.sheet)
    hdr = header_row_index(grid)
    found = find_numbers(grid, req.column, hdr)
    if not found.detections and not found.problems:
        raise ValueError("No tracking numbers were found in that tab/column.")
    log.info("Found %s", found.summary())
    for r, c, msg in found.problems:
        log.warning("Row %s col %s: %s", r, c, msg)
    say(found.summary(), 0.05)

    numbers = sorted(found.detections, key=lambda n: found.occurrences[n][0])
    results: dict[str, M.TrackingResult] = {}
    candidates: dict[str, list[str]] = {}
    for n in numbers:
        d = found.detections[n]
        cands = [c for c in d.carriers]
        hint = found.carrier_hints.get(n)
        if hint in M.SUPPORTED_CARRIERS:
            if hint in cands:
                cands.remove(hint)
                cands.insert(0, hint)
            else:
                cands.append(hint)
        candidates[n] = [c for c in cands if c in M.SUPPORTED_CARRIERS]
        if not candidates[n]:
            results[n] = M.TrackingResult(n, d.carrier, status=M.ERROR, source="-",
                                          error=f"{d.carrier} is not a supported carrier", identified_by=d.reason)

    # ---- reuse delivered results from earlier runs
    cache = load_cache()
    if req.reuse_delivered and not req.demo:
        for n in numbers:
            if n in results or not candidates.get(n):
                continue
            for c in candidates[n]:
                hit = cache.get(f"{c}:{n}")
                if hit:
                    r = M.TrackingResult.from_dict(hit)
                    if r.pod and r.pod.files and not all(Path(p).exists() for p in r.pod.files):
                        continue
                    r.source = f"Cached - delivered; last checked {r.checked_at:%Y-%m-%d %H:%M}"
                    r.extra = {}
                    results[n] = r
                    break
        reused = sum(1 for r in results.values() if r.source.startswith("Cached"))
        if reused:
            say(f"Re-using {reused} package(s) already delivered on an earlier run.", 0.07)

    # ---- query carriers, falling back to the next candidate on "not found"
    clients: dict[str, object] = {}
    client_errors: dict[str, str] = {}

    def client_for(carrier):
        if carrier in clients or carrier in client_errors:
            return clients.get(carrier)
        try:
            clients[carrier] = carriers.build(carrier, settings, demo=req.demo, cancel=cancel, browser=browser,
                                              capture_pod=req.download_pod)
        except NotConfigured as e:
            client_errors[carrier] = str(e)
        return clients.get(carrier)

    pending = {n: list(candidates[n]) for n in numbers if n not in results}
    last_error: dict[str, Exception] = {}
    total = max(len(pending), 1)
    done = 0
    while pending:
        _check(cancel)
        for n in [n for n, c in pending.items() if not c]:
            err = last_error.get(n, CarrierError("No carrier could track this number"))
            d = found.detections[n]
            status = M.NOT_FOUND if isinstance(err, NotFound) else M.ERROR
            results[n] = M.TrackingResult(n, candidates[n][0] if candidates[n] else d.carrier, status=status,
                                          source="-", error=str(err), identified_by=d.reason)
            del pending[n]
        by_carrier: dict[str, list[str]] = {}
        for n, cands in list(pending.items()):
            if not cands:
                continue
            by_carrier.setdefault(cands[0], []).append(n)
        if not by_carrier:
            break
        jobs = []
        with ThreadPoolExecutor(max_workers=6) as pool:
            for carrier, nums in by_carrier.items():
                cl = client_for(carrier)
                if cl is None:
                    for n in nums:
                        last_error[n] = NotConfigured(client_errors[carrier])
                        pending[n].pop(0)
                    continue
                step = getattr(cl, "batch_size", 1) or 1
                for i in range(0, len(nums), step):
                    jobs.append(pool.submit(_track_chunk, cl, carrier, nums[i:i + step]))
            for fut in as_completed(jobs):
                carrier, got = fut.result()
                for n, res in got.items():
                    if n not in pending:
                        continue
                    pending[n].pop(0)
                    if isinstance(res, M.TrackingResult):
                        res.identified_by = _identified(found.detections[n], carrier)
                        results[n] = res
                        del pending[n]
                        done += 1
                    else:
                        last_error[n] = res
                        if not pending[n] or not isinstance(res, NotFound):
                            pending[n] = []
                            done += 1
                if cancel.is_set():
                    pool.shutdown(cancel_futures=True)
                    raise Cancelled("Cancelled by user")
                say(f"Tracking ... {done}/{total} checked", 0.08 + 0.6 * done / total)

    # ---- proof of delivery
    pod_dir = wb.pod_folder()
    entries: list[report.Entry] = []
    delivered = [results[n] for n in numbers if results[n].delivered]
    for i, r in enumerate(delivered, 1):
        _check(cancel)
        if req.download_pod and not r.source.startswith("Cached"):
            cl = clients.get(r.carrier)
            if cl is not None:
                try:
                    cl.fetch_pod(r)
                except Exception as e:  # noqa: BLE001
                    log.info("POD fetch failed for %s: %s", r.tracking_number, e)
            try:
                save_pod(r, pod_dir)
            except OSError as e:
                r.error = f"Couldn't save POD files: {e}"
        say(f"Proof of delivery {i}/{len(delivered)} ...", 0.7 + 0.1 * i / max(len(delivered), 1))

    for n in numbers:
        r = results[n]
        e = report.Entry(n, found.detections[n], found.occurrences[n], r)
        files = r.pod.files if (r.pod and r.delivered) else []
        if files:
            e.pod_link = wb.pod_link(Path(files[0]))
            if not e.pod_link:
                e.other_pod_links = files
            else:
                e.other_pod_links = [wb.pod_link(Path(p)) if wb.kind == "xlsx" else Path(p).name for p in files[1:]]
            summary.pod_files += len(files)
        entries.append(e)
    for row, col, msg in found.problems:
        label = str(grid[row - 1][col - 1])
        r = M.TrackingResult(label, "Unknown", status=M.ERROR, source="-", error=msg)
        entries.append(report.Entry(label, None, [(row, col)], r))
    entries.sort(key=lambda e: e.occurrences[0])

    # ---- extra checks
    _check(cancel)
    outcome = checks.run(req.extra_check, [e.result for e in entries], settings, lambda m: say(m, 0.82))
    summary.check_engine, summary.check_interpretation = outcome.engine, outcome.interpretation
    summary.notes += outcome.notes

    # ---- write
    _check(cancel)
    say("Writing the results tab ...", 0.9)
    headers, rows, widths = report.build(entries, req.sheet, outcome.columns, carriers.tracking_url)
    header_notes = {}
    if outcome.columns:
        header_notes[checks.MATCH_COL] = (f"Your request: {req.extra_check.strip()}\n\nInterpreted by "
                                          f"{outcome.engine} as: {outcome.interpretation}")
    summary.notes += wb.begin_write()
    wb.write_status_tab(req.status_tab, headers, rows, widths, header_notes)
    links = [(row, col, i + 2) for i, e in enumerate(entries) for row, col in e.occurrences]
    wb.link_source_cells(req.sheet, links, req.status_tab)
    say("Saving ...", 0.96)
    summary.saved_to = wb.save()
    summary.backup = str(getattr(wb, "backup_path", "") or "")
    summary.pod_folder = str(pod_dir) if summary.pod_files else ""

    # ---- cache delivered live results
    for e in entries:
        r = e.result
        if r.delivered and r.source in ("Live API", "Carrier website"):
            cache[f"{r.carrier}:{r.tracking_number}"] = r.to_dict()
    if not req.demo:
        save_cache(cache)

    summary.total = len(entries)
    summary.by_status = Counter(e.result.status for e in entries)
    summary.by_carrier = Counter(e.result.carrier for e in entries)
    for carrier, msg in client_errors.items():
        summary.notes.append(msg)
    for carrier, cl in clients.items():
        if getattr(cl, "blocked_reason", ""):
            n_blocked = sum(1 for e in entries if e.result.carrier == carrier and cl.blocked_reason in e.result.error)
            summary.notes.append(f"{carrier}: {n_blocked} website lookup(s) blocked - {cl.blocked_reason}")
    if req.demo:
        summary.notes.append("DEMO MODE - every result is fake sample data, clearly labelled in the Data Source column.")
    say("Done.", 1.0)


def _track_chunk(client, carrier: str, nums: list[str]):
    try:
        got = client.track_many(nums)
    except Exception as e:  # noqa: BLE001
        got = {n: e for n in nums}
    return carrier, got


def _identified(d, found_by: str) -> str:
    if d and d.carrier == found_by:
        return f"{d.reason}; confirmed by {found_by}"
    return f"{d.reason if d else 'Unrecognised format'}; found by {found_by}"
