from __future__ import annotations

import argparse
import logging
import sys

from . import APP_NAME, __version__


def _parser():
    p = argparse.ArgumentParser(prog="tracking-check", description=f"{APP_NAME} {__version__}")
    p.add_argument("--cli", action="store_true", help="run headless (no window)")
    p.add_argument("--file", help="Excel workbook (.xlsx/.xlsm) to examine")
    p.add_argument("--gsheet", help="Google Sheets link to examine")
    p.add_argument("--sheet", help="tab holding the tracking numbers (default: auto-detect)")
    p.add_argument("--column", help="column letter or header text, or 'all' (default: auto-detect)")
    p.add_argument("--check", default="", help="anything specific to check, in plain English")
    p.add_argument("--tab-name", default=None, help="results tab name (default: Tracking Status)")
    p.add_argument("--demo", action="store_true", help="fake data, no carrier API calls")
    p.add_argument("--no-pod", action="store_true", help="don't save proof-of-delivery files")
    p.add_argument("--no-reuse", action="store_true", help="re-query packages delivered on earlier runs")
    p.add_argument("--detect", nargs="+", metavar="NUMBER", help="identify the carrier for tracking number(s)")
    p.add_argument("--doctor", action="store_true", help="show configuration and dependency status")
    p.add_argument("--selftest", action="store_true", help="run an end-to-end demo check on a generated workbook")
    p.add_argument("--make-sample", metavar="PATH", help="write a sample workbook with test tracking numbers")
    return p


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    if args.detect:
        from .detect import scan_cell
        for n in args.detect:
            s = scan_cell(n)
            if s.detections:
                for d in s.detections:
                    print(f"{n}: {' or '.join(d.carriers)}  [{d.confidence}]  {d.reason}")
            else:
                print(f"{n}: not recognised as a UPS/FedEx/USPS tracking number" + (f" - {s.problem}" if s.problem else ""))
        return 0
    if args.doctor:
        from .doctor import doctor
        return doctor()
    if args.selftest:
        from .selftest import selftest
        return selftest()
    if args.make_sample:
        from .selftest import make_sample
        print(make_sample(args.make_sample))
        return 0
    if args.cli or args.file or args.gsheet:
        return _cli(args)
    from .gui import main as gui_main
    gui_main()
    return 0


def _cli(args) -> int:
    from . import config
    from .pipeline import RunRequest, run
    from .sheets import ALL_COLUMNS, analyse_columns, open_workbook

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    loc = args.file or args.gsheet
    if not loc:
        print("Give --file PATH or --gsheet URL", file=sys.stderr)
        return 2
    settings = config.Settings.load()
    tab_name = args.tab_name or settings.status_tab_name
    wb = open_workbook(loc, settings)
    tabs = [t for t in wb.tab_names() if t != tab_name]
    sheet = args.sheet
    if not sheet:
        def matches(t):
            cols = analyse_columns(wb.read_tab(t))[1]
            return cols[0].matches if cols else 0
        sheet = max(tabs, key=matches)
    if sheet not in tabs:
        print(f"Tab '{sheet}' not found. Tabs: {', '.join(tabs)}", file=sys.stderr)
        return 2
    hdr, cols = analyse_columns(wb.read_tab(sheet))
    column = cols[0].index if cols else ALL_COLUMNS
    if args.column:
        c = args.column.strip()
        if c.lower() == "all":
            column = ALL_COLUMNS
        elif c.isalpha() and len(c) <= 3:
            from openpyxl.utils import column_index_from_string
            column = column_index_from_string(c.upper())
        else:
            grid = wb.read_tab(sheet)
            heads = [str(h or "").strip().lower() for h in (grid[hdr] if hdr >= 0 else [])]
            if c.lower() not in heads:
                print(f"No column headed '{c}'.", file=sys.stderr)
                return 2
            column = heads.index(c.lower()) + 1
    print(f"Tab '{sheet}', column {column or 'ALL'}")
    req = RunRequest(location=loc, sheet=sheet, column=column, extra_check=args.check, status_tab=tab_name,
                     download_pod=not args.no_pod, reuse_delivered=not args.no_reuse, demo=args.demo)
    summary = run(req, settings, lambda m, f=None: print(f"  {m}"), workbook=wb)
    print()
    print(summary.text())
    return 0


if __name__ == "__main__":
    sys.exit(main())
