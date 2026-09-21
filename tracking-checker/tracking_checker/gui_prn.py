"""The PRN tab: UPS pickup request numbers -> pickup status (same layout and flow as the Tracking tab)."""
from __future__ import annotations

import logging
import os
import threading
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
import tkinter as tk

from . import APP_NAME
from .pickup import AUTO_ZIP
from .pickup.sheet import analyse_prn_columns, find_prns
from .sheets import ALL_COLUMNS, open_workbook

log = logging.getLogger("tracking_checker")
ROOT_DIR = Path(__file__).resolve().parent.parent
LOOKUP_NOTE = ("Lookups   UPS: pickup-status page on ups.com (PRN + pickup ZIP), in the Edge window at a human pace.   "
               "FedEx / USPS / other pickups are listed on the results tab but not looked up.   UPS doesn't list the "
               "tracking numbers of a pickup - only the package count - so the PRN Tracking # columns stay empty.")


class PrnTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.settings = app.settings
        self.wb = None
        self.grid_cache: dict[str, list] = {}
        self.header_row = -1
        self.prn_choices: list[tuple[str, int]] = []
        self.zip_choices: list[tuple[str, int]] = []
        self.zip_candidates = []
        self.cancel = threading.Event()
        self.last_saved = ""
        self._build()

    # ------------------------------------------------------------ layout
    def _build(self):
        from .gui import enable_clipboard
        pad = {"padx": 10, "pady": 6}

        f1 = ttk.LabelFrame(self, text="1. Spreadsheet to examine", style="Head.TLabelframe")
        f1.pack(fill="x", **pad)
        self.var_kind = tk.StringVar(value="xlsx" if not self.settings.last_gsheet_url or self.settings.last_file
                                     else "gsheet")
        ttk.Radiobutton(f1, text="Excel file (.xlsx / .xlsm)", variable=self.var_kind, value="xlsx",
                        command=self._kind_changed).grid(row=0, column=0, sticky="w", padx=8, pady=4)
        ttk.Radiobutton(f1, text="Google Sheet (paste the link)", variable=self.var_kind, value="gsheet",
                        command=self._kind_changed).grid(row=0, column=1, sticky="w", padx=8, pady=4)
        self.var_loc = tk.StringVar(value=self.settings.last_file if self.var_kind.get() == "xlsx"
                                    else self.settings.last_gsheet_url)
        self.ent_loc = ttk.Entry(f1, textvariable=self.var_loc)
        self.ent_loc.grid(row=1, column=0, columnspan=2, sticky="ew", padx=8, pady=(0, 8))
        enable_clipboard(self.ent_loc)
        self.btn_browse = ttk.Button(f1, text="Browse...", command=self._browse)
        self.btn_browse.grid(row=1, column=2, padx=4, pady=(0, 8))
        ttk.Button(f1, text="Load", command=self._load).grid(row=1, column=3, padx=(0, 8), pady=(0, 8))
        self.ent_loc.bind("<Return>", lambda e: self._load())
        f1.columnconfigure(0, weight=1)
        f1.columnconfigure(1, weight=1)

        f2 = ttk.LabelFrame(self, text="2. Where are the pickup request numbers (PRNs) and their ZIP codes?",
                            style="Head.TLabelframe")
        f2.pack(fill="x", **pad)
        ttk.Label(f2, text="Tab:").grid(row=0, column=0, sticky="w", padx=8, pady=4)
        self.cmb_tab = ttk.Combobox(f2, state="readonly", width=34)
        self.cmb_tab.grid(row=0, column=1, sticky="w", pady=4)
        self.cmb_tab.bind("<<ComboboxSelected>>", lambda e: self._tab_changed())
        ttk.Label(f2, text="PRN column:").grid(row=0, column=2, sticky="w", padx=(16, 4))
        self.cmb_col = ttk.Combobox(f2, state="readonly", width=46)
        self.cmb_col.grid(row=0, column=3, sticky="ew", pady=4, padx=(0, 8))
        self.cmb_col.bind("<<ComboboxSelected>>", lambda e: self._preview())
        ttk.Label(f2, text="ZIP code from:").grid(row=1, column=2, sticky="w", padx=(16, 4))
        self.cmb_zip = ttk.Combobox(f2, state="readonly", width=46)
        self.cmb_zip.grid(row=1, column=3, sticky="ew", pady=4, padx=(0, 8))
        self.cmb_zip.bind("<<ComboboxSelected>>", lambda e: self._preview())
        ttk.Label(f2, text="A ZIP column is used as-is; from a full address the ZIP is parsed out "
                           "(e.g. '99 Park Ave, New York, NY 10016' -> 10016).", foreground="#666"
                  ).grid(row=2, column=0, columnspan=4, sticky="w", padx=8)
        self.lbl_found = ttk.Label(f2, text="Choose a spreadsheet above, then press Load.", foreground="#555")
        self.lbl_found.grid(row=3, column=0, columnspan=4, sticky="w", padx=8, pady=(2, 8))
        f2.columnconfigure(3, weight=1)

        f3 = ttk.LabelFrame(self, text="3. Options", style="Head.TLabelframe")
        f3.pack(fill="x", **pad)
        ttk.Label(f3, text="Results tab name:").grid(row=0, column=0, sticky="w", padx=8, pady=4)
        self.var_tab = tk.StringVar(value=self.settings.prn_tab_name)
        ttk.Entry(f3, textvariable=self.var_tab, width=28).grid(row=0, column=1, sticky="w")
        self.var_back = tk.BooleanVar(value=self.settings.prn_write_back)
        ttk.Checkbutton(f3, text="Add PRN Status / PRN Pieces / PRN Tracking # columns to the right of the data "
                                 "on the original tab", variable=self.var_back
                        ).grid(row=1, column=0, columnspan=3, sticky="w", padx=8)
        self.var_demo = tk.BooleanVar(value=False)
        ttk.Checkbutton(f3, text="Demo mode - fake data, no ups.com lookups", variable=self.var_demo
                        ).grid(row=2, column=0, columnspan=2, sticky="w", padx=8, pady=(0, 6))
        ttk.Label(f3, text=LOOKUP_NOTE, foreground="#666", wraplength=900, justify="left"
                  ).grid(row=3, column=0, columnspan=3, sticky="w", padx=8, pady=(0, 6))

        f4 = ttk.Frame(self)
        f4.pack(fill="x", **pad)
        self.btn_run = ttk.Button(f4, text="Run PRN check", command=self._run)
        self.btn_run.pack(side="left")
        self.btn_cancel = ttk.Button(f4, text="Cancel", command=self._cancel, state="disabled")
        self.btn_cancel.pack(side="left", padx=6)
        ttk.Button(f4, text="Settings...", command=self.app._settings).pack(side="left", padx=6)
        self.btn_open = ttk.Button(f4, text="Open results", command=self._open_results, state="disabled")
        self.btn_open.pack(side="left", padx=6)
        ttk.Button(f4, text="Help", command=self._help).pack(side="right")
        self._kind_changed(initial=True)

    # ------------------------------------------------------------ step 1
    def _kind_changed(self, initial=False):
        xl = self.var_kind.get() == "xlsx"
        self.btn_browse.configure(state="normal" if xl else "disabled")
        if not initial:
            self.var_loc.set(self.settings.last_file if xl else self.settings.last_gsheet_url)

    def _browse(self):
        start = Path(self.var_loc.get()).parent if self.var_loc.get() else Path.home()
        p = filedialog.askopenfilename(title="Choose the spreadsheet with the pickup request numbers",
                                       initialdir=str(start),
                                       filetypes=[("Excel workbooks", "*.xlsx *.xlsm"), ("All files", "*.*")])
        if p:
            self.var_loc.set(os.path.normpath(p))
            self._load()

    def _load(self):
        loc = self.var_loc.get().strip().strip('"')
        if not loc:
            messagebox.showinfo(APP_NAME, "Choose an Excel file or paste a Google Sheets link first.")
            return
        self.lbl_found.configure(text="Opening ...", foreground="#555")
        for c in (self.cmb_tab, self.cmb_col, self.cmb_zip):
            c.set("")
        self.grid_cache.clear()
        skip = {self.var_tab.get().strip(), self.settings.status_tab_name}

        def work():
            wb = open_workbook(loc, self.settings)
            tabs = [t for t in wb.tab_names() if t not in skip]
            best, best_score, grids = None, -1.0, {}
            for t in tabs:
                g = grids[t] = wb.read_tab(t)
                _, cols, _ = analyse_prn_columns(g)
                score = cols[0].matches if cols else 0
                if score > best_score:
                    best, best_score = t, score
            return wb, tabs, grids, best

        def done(res, err):
            if err:
                self.lbl_found.configure(text=f"Couldn't open it: {err}", foreground="#b42318")
                log.error("Couldn't open %s: %s", loc, err)
                return
            self.wb, tabs, self.grid_cache, best = res
            if self.var_kind.get() == "xlsx":
                self.settings.last_file = loc
            else:
                self.settings.last_gsheet_url = loc
            self.settings.save()
            self.cmb_tab["values"] = tabs
            if best or tabs:
                self.cmb_tab.set(best or tabs[0])
            log.info("Opened %s (%d tab(s))", self.wb.display_name, len(tabs))
            self._tab_changed()

        self.app._in_thread(work, done)

    # ------------------------------------------------------------ step 2
    def _tab_changed(self):
        g = self.grid_cache.get(self.cmb_tab.get())
        if g is None:
            return
        self.header_row, cols, self.zip_candidates = analyse_prn_columns(g)
        self.prn_choices = [(c.label, c.index) for c in cols] + [("(scan every column)", ALL_COLUMNS)]
        self.cmb_col["values"] = [c[0] for c in self.prn_choices]
        self.cmb_col.current(0)
        auto = ("Automatic - " + self.zip_candidates[0].label) if self.zip_candidates else \
            "Automatic - any address in the row"
        headers = g[self.header_row] if self.header_row >= 0 else []
        from .report import col_letter
        others = [(f"{col_letter(i + 1)} '{h}'" if h not in (None, "") else col_letter(i + 1), i + 1)
                  for i, h in enumerate(headers or (g[0] if g else []))]
        listed = {z.index for z in self.zip_candidates}
        self.zip_choices = [(auto, AUTO_ZIP)] + [(z.label, z.index) for z in self.zip_candidates] + \
                           [(l, i) for l, i in others if i not in listed]
        self.cmb_zip["values"] = [c[0] for c in self.zip_choices]
        self.cmb_zip.current(0)
        if not cols:
            self.lbl_found.configure(text="No pickup request number column detected on this tab - try another tab, "
                                          "or choose '(scan every column)'.", foreground="#b42318")
        self._preview()

    def _choice(self, cmb, choices, default):
        return next((i for l, i in choices if l == cmb.get()), default)

    def _found(self):
        g = self.grid_cache.get(self.cmb_tab.get())
        if g is None:
            return None
        return find_prns(g, self._choice(self.cmb_col, self.prn_choices, ALL_COLUMNS), self.header_row,
                         self._choice(self.cmb_zip, self.zip_choices, AUTO_ZIP), self.zip_candidates)

    def _preview(self):
        found = self._found()
        if found is None:
            return
        missing = [p for p in found.pickups.values() if p.supported and not p.zip_code]
        text = found.summary()
        if missing:
            text += f"  (no ZIP on row {missing[0].occurrences[0][0]}" + (" and others" if len(missing) > 1 else "") + ")"
        ok = found.pickups and not missing
        self.lbl_found.configure(text=text, foreground="#1a7f37" if ok else "#b42318" if not found.pickups
                                 else "#9a6700")

    # ------------------------------------------------------------ run
    def _run(self):
        if self.app.worker and self.app.worker.is_alive():
            messagebox.showinfo(APP_NAME, "Wait for the current run to finish first.")
            return
        if not self.wb or not self.cmb_tab.get():
            messagebox.showinfo(APP_NAME, "Load a spreadsheet and pick the tab with the pickup request numbers first.")
            return
        from .pickup.pipeline import PrnRequest, run_prn
        from .pipeline import Cancelled

        status_tab = self.var_tab.get().strip() or "PRN Status"
        req = PrnRequest(location=self.var_loc.get().strip().strip('"'), sheet=self.cmb_tab.get(),
                         column=self._choice(self.cmb_col, self.prn_choices, ALL_COLUMNS),
                         zip_column=self._choice(self.cmb_zip, self.zip_choices, AUTO_ZIP),
                         status_tab=status_tab, write_back=self.var_back.get(), demo=self.var_demo.get())
        self.settings.prn_tab_name, self.settings.prn_write_back = status_tab, req.write_back
        self.settings.save()
        self.cancel.clear()
        self.btn_run.configure(state="disabled")
        self.btn_cancel.configure(state="normal")
        self.app.pb["value"] = 0
        log.info("---- PRN run: %s / tab '%s' ----", self.wb.display_name, req.sheet)
        wb, q = self.wb, self.app.q

        def work():
            return run_prn(req, self.settings, lambda m, f=None: q.put(("progress", m, f)), self.cancel, workbook=wb)

        def done(summary, err):
            self.btn_run.configure(state="normal")
            self.btn_cancel.configure(state="disabled")
            self.wb = None     # a saved workbook must be re-opened before the next run
            if err:
                if isinstance(err, Cancelled):
                    self.app.lbl_status.configure(text="Cancelled - nothing was written.")
                    log.info("Cancelled - the spreadsheet was not changed.")
                else:
                    self.app.lbl_status.configure(text="Failed - see the log.")
                    log.error("PRN run failed: %s", err)
                    messagebox.showerror(APP_NAME, str(err))
                self._load()
                return
            self.last_saved = summary.saved_to
            self.btn_open.configure(state="normal")
            for line in summary.text().splitlines():
                log.info(line)
            self.app.lbl_status.configure(text="Done - results written to the '%s' tab." % summary.status_tab)
            messagebox.showinfo(APP_NAME, summary.text())
            self._load()

        self.app.worker = self.app._in_thread(work, done)

    def _cancel(self):
        self.cancel.set()
        self.app.lbl_status.configure(text="Cancelling ...")

    def _open_results(self):
        if self.last_saved.startswith("http"):
            webbrowser.open(self.last_saved)
        elif self.last_saved:
            os.startfile(self.last_saved)  # noqa: S606 - opening the user's own workbook

    def _help(self):
        readme = ROOT_DIR / "README.md"
        if readme.exists():
            os.startfile(str(readme))  # noqa: S606
        else:
            messagebox.showinfo(APP_NAME, "See README.md in the app folder.")
