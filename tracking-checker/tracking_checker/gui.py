"""Tkinter desktop window."""
from __future__ import annotations

import logging
import os
import queue
import threading
import tkinter as tk
import traceback
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import APP_NAME, __version__, checks, config
from .models import SUPPORTED_CARRIERS
from .sheets import ALL_COLUMNS, analyse_columns, find_numbers, open_workbook

log = logging.getLogger("tracking_checker")
ROOT_DIR = Path(__file__).resolve().parent.parent
EXAMPLES = ("Examples:  flag anything not delivered yet  •  delivered after 9/1/2026  •  no update in 3 days  •  "
            "signed by Smith  •  delivered to Texas  •  late vs estimate  •  more than 1 delivery attempt")


class QueueHandler(logging.Handler):
    def __init__(self, q):
        super().__init__()
        self.q = q

    def emit(self, record):
        self.q.put(("log", self.format(record)))


def enable_clipboard(widget: tk.Text | tk.Entry):
    """Explicit Ctrl+C/X/V/A and a right-click menu (Tk's defaults fail on some keyboard layouts)."""
    is_text = isinstance(widget, tk.Text)

    def select_all(_=None):
        if is_text:
            widget.tag_add("sel", "1.0", "end-1c")
        else:
            widget.select_range(0, "end")
        return "break"

    def gen(ev):
        return lambda _=None: (widget.event_generate(ev), "break")[1]

    for seq, ev in (("<Control-c>", "<<Copy>>"), ("<Control-x>", "<<Cut>>"), ("<Control-v>", "<<Paste>>")):
        widget.bind(seq, gen(ev))
        widget.bind(seq.upper().replace("CONTROL", "Control"), gen(ev))
    widget.bind("<Control-a>", select_all)
    menu = tk.Menu(widget, tearoff=0)
    for label, ev in (("Cut", "<<Cut>>"), ("Copy", "<<Copy>>"), ("Paste", "<<Paste>>")):
        menu.add_command(label=label, command=lambda e=ev: widget.event_generate(e))
    menu.add_separator()
    menu.add_command(label="Select all", command=select_all)
    widget.bind("<Button-3>", lambda e: menu.tk_popup(e.x_root, e.y_root))


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} {__version__}")
        self.geometry("980x900")
        self.minsize(820, 740)
        self.settings = config.Settings.load()
        self.q: queue.Queue = queue.Queue()
        self.wb = None
        self.grid_cache: dict[str, list] = {}
        self.col_choices: list[tuple[str, int]] = []
        self.cancel = threading.Event()
        self.worker: threading.Thread | None = None
        self.last_saved = ""
        self.last_pod = ""
        h = QueueHandler(self.q)
        h.setFormatter(logging.Formatter("%(asctime)s  %(message)s", "%H:%M:%S"))
        log.addHandler(h)
        log.setLevel(logging.INFO)
        self._build()
        self.after(100, self._poll)
        self._refresh_engine_label()
        self._refresh_methods()

    # ------------------------------------------------------------ layout
    def _build(self):
        pad = {"padx": 10, "pady": 6}
        style = ttk.Style(self)
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        style.configure("Head.TLabelframe.Label", font=("Segoe UI", 10, "bold"))
        style.configure("TNotebook.Tab", font=("Segoe UI", 10, "bold"), padding=(14, 4))

        # One tab per job; the progress bar and log below are shared.
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="x", padx=6, pady=(6, 0))
        tt = ttk.Frame(self.nb)
        self.nb.add(tt, text="Tracking")

        # 1. file
        f1 = ttk.LabelFrame(tt, text="1. Spreadsheet to examine", style="Head.TLabelframe")
        f1.pack(fill="x", **pad)
        self.var_kind = tk.StringVar(value="xlsx" if not self.settings.last_gsheet_url or self.settings.last_file else "gsheet")
        ttk.Radiobutton(f1, text="Excel file (.xlsx / .xlsm)", variable=self.var_kind, value="xlsx",
                        command=self._kind_changed).grid(row=0, column=0, sticky="w", padx=8, pady=4)
        ttk.Radiobutton(f1, text="Google Sheet (paste the link)", variable=self.var_kind, value="gsheet",
                        command=self._kind_changed).grid(row=0, column=1, sticky="w", padx=8, pady=4)
        self.var_loc = tk.StringVar(value=self.settings.last_file if self.var_kind.get() == "xlsx" else self.settings.last_gsheet_url)
        self.ent_loc = ttk.Entry(f1, textvariable=self.var_loc)
        self.ent_loc.grid(row=1, column=0, columnspan=2, sticky="ew", padx=8, pady=(0, 8))
        enable_clipboard(self.ent_loc)
        self.btn_browse = ttk.Button(f1, text="Browse...", command=self._browse)
        self.btn_browse.grid(row=1, column=2, padx=4, pady=(0, 8))
        ttk.Button(f1, text="Load", command=self._load).grid(row=1, column=3, padx=(0, 8), pady=(0, 8))
        self.ent_loc.bind("<Return>", lambda e: self._load())
        f1.columnconfigure(0, weight=1)
        f1.columnconfigure(1, weight=1)

        # 2. where
        f2 = ttk.LabelFrame(tt, text="2. Where are the tracking numbers?", style="Head.TLabelframe")
        f2.pack(fill="x", **pad)
        ttk.Label(f2, text="Tab:").grid(row=0, column=0, sticky="w", padx=8, pady=4)
        self.cmb_tab = ttk.Combobox(f2, state="readonly", width=34)
        self.cmb_tab.grid(row=0, column=1, sticky="w", pady=4)
        self.cmb_tab.bind("<<ComboboxSelected>>", lambda e: self._tab_changed())
        ttk.Label(f2, text="Column:").grid(row=0, column=2, sticky="w", padx=(16, 4))
        self.cmb_col = ttk.Combobox(f2, state="readonly", width=46)
        self.cmb_col.grid(row=0, column=3, sticky="ew", pady=4, padx=(0, 8))
        self.cmb_col.bind("<<ComboboxSelected>>", lambda e: self._preview())
        self.lbl_found = ttk.Label(f2, text="Choose a spreadsheet above, then press Load.", foreground="#555")
        self.lbl_found.grid(row=1, column=0, columnspan=4, sticky="w", padx=8, pady=(0, 8))
        f2.columnconfigure(3, weight=1)

        # 3. extra check
        f3 = ttk.LabelFrame(tt, text="3. Is there anything specific to check in addition to the typical tracking?",
                            style="Head.TLabelframe")
        f3.pack(fill="x", **pad)
        self.txt_check = tk.Text(f3, height=4, wrap="word", undo=True, font=("Segoe UI", 10))
        self.txt_check.pack(fill="x", padx=8, pady=(6, 2))
        enable_clipboard(self.txt_check)
        ttk.Label(f3, text=EXAMPLES, foreground="#666", wraplength=900).pack(anchor="w", padx=8)
        self.lbl_engine = ttk.Label(f3, text="", foreground="#1f4e78")
        self.lbl_engine.pack(anchor="w", padx=8, pady=(2, 6))

        # 4. options
        f4 = ttk.LabelFrame(tt, text="4. Options", style="Head.TLabelframe")
        f4.pack(fill="x", **pad)
        ttk.Label(f4, text="Results tab name:").grid(row=0, column=0, sticky="w", padx=8, pady=4)
        self.var_tab = tk.StringVar(value=self.settings.status_tab_name)
        ttk.Entry(f4, textvariable=self.var_tab, width=28).grid(row=0, column=1, sticky="w")
        self.var_pod = tk.BooleanVar(value=self.settings.download_pod)
        ttk.Checkbutton(f4, text="Save proof of delivery for delivered packages", variable=self.var_pod
                        ).grid(row=0, column=2, sticky="w", padx=16)
        self.var_reuse = tk.BooleanVar(value=self.settings.reuse_delivered)
        ttk.Checkbutton(f4, text="Re-use results for packages already delivered on an earlier run (saves API quota)",
                        variable=self.var_reuse).grid(row=1, column=0, columnspan=3, sticky="w", padx=8)
        self.var_demo = tk.BooleanVar(value=False)
        self.chk_demo = ttk.Checkbutton(f4, text="Demo mode - fake data, no carrier lookups", variable=self.var_demo)
        self.chk_demo.grid(row=2, column=0, columnspan=2, sticky="w", padx=8, pady=(0, 6))
        self.btn_signin = ttk.Button(f4, text="Sign in to UPS / FedEx (for signatures)...", command=self._sign_in)
        self.btn_signin.grid(row=2, column=2, sticky="e", padx=8, pady=(0, 6))
        self.lbl_keys = ttk.Label(f4, text="", foreground="#666", wraplength=900, justify="left")
        self.lbl_keys.grid(row=3, column=0, columnspan=3, sticky="w", padx=8, pady=(0, 6))

        # 5. actions
        f5 = ttk.Frame(tt)
        f5.pack(fill="x", **pad)
        self.btn_run = ttk.Button(f5, text="Run check", command=self._run)
        self.btn_run.pack(side="left")
        self.btn_cancel = ttk.Button(f5, text="Cancel", command=self._cancel, state="disabled")
        self.btn_cancel.pack(side="left", padx=6)
        ttk.Button(f5, text="Settings / API keys...", command=self._settings).pack(side="left", padx=6)
        self.btn_open = ttk.Button(f5, text="Open results", command=self._open_results, state="disabled")
        self.btn_open.pack(side="left", padx=6)
        self.btn_pod = ttk.Button(f5, text="Open POD folder", command=self._open_pod, state="disabled")
        self.btn_pod.pack(side="left", padx=6)
        ttk.Button(f5, text="Help", command=self._help).pack(side="right")

        from .gui_prn import PrnTab
        self.prn_tab = PrnTab(self.nb, self)
        self.nb.add(self.prn_tab, text="PRN")

        self.pb = ttk.Progressbar(self, mode="determinate", maximum=1.0)
        self.pb.pack(fill="x", padx=10)
        self.lbl_status = ttk.Label(self, text="Ready.")
        self.lbl_status.pack(anchor="w", padx=10, pady=(2, 0))
        logf = ttk.Frame(self)
        logf.pack(fill="both", expand=True, padx=10, pady=(4, 10))
        self.txt_log = tk.Text(logf, height=12, wrap="word", state="disabled", font=("Consolas", 9), background="#fafafa")
        sb = ttk.Scrollbar(logf, command=self.txt_log.yview)
        self.txt_log.configure(yscrollcommand=sb.set)
        self.txt_log.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        enable_clipboard(self.txt_log)
        self._kind_changed(initial=True)

    # ------------------------------------------------------------ helpers
    def _log(self, msg: str):
        self.txt_log.configure(state="normal")
        self.txt_log.insert("end", msg + "\n")
        self.txt_log.see("end")
        self.txt_log.configure(state="disabled")

    def _poll(self):
        try:
            while True:
                kind, *payload = self.q.get_nowait()
                if kind == "log":
                    self._log(payload[0])
                elif kind == "progress":
                    msg, frac = payload
                    self.lbl_status.configure(text=msg)
                    if frac is not None:
                        self.pb["value"] = frac
                elif kind == "call":
                    payload[0]()
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _in_thread(self, fn, done=None):
        def body():
            try:
                res = fn()
                if done:
                    self.q.put(("call", lambda: done(res, None)))
            except Exception as e:  # noqa: BLE001
                log.debug(traceback.format_exc())
                if done:
                    self.q.put(("call", lambda e=e: done(None, e)))
        t = threading.Thread(target=body, daemon=True)
        t.start()
        return t

    def _refresh_engine_label(self):
        self.lbl_engine.configure(text="Your request will be interpreted by: " + checks.engine_label(self.settings))

    def _refresh_methods(self):
        parts = []
        for c in SUPPORTED_CARRIERS:
            m = config.lookup_method(c, self.settings)
            if m == config.WEBSITE:
                parts.append(f"{c}: website" + (" (FedEx blocks automated lookups - add FedEx API keys)"
                                                if c == "FedEx" else ""))
            elif config.carrier_configured(c):
                parts.append(f"{c}: API")
            else:
                parts.append(f"{c}: API - NO KEYS")
        self.lbl_keys.configure(text="Lookups   " + "   •   ".join(parts) + "      (change in Settings)")

    # ------------------------------------------------------------ step 1
    def _kind_changed(self, initial=False):
        xl = self.var_kind.get() == "xlsx"
        self.btn_browse.configure(state="normal" if xl else "disabled")
        if not initial:
            self.var_loc.set(self.settings.last_file if xl else self.settings.last_gsheet_url)

    def _browse(self):
        start = Path(self.var_loc.get()).parent if self.var_loc.get() else Path.home()
        p = filedialog.askopenfilename(title="Choose the spreadsheet to examine", initialdir=str(start),
                                       filetypes=[("Excel workbooks", "*.xlsx *.xlsm"), ("All files", "*.*")])
        if p:
            self.var_loc.set(os.path.normpath(p))
            self._load()

    def _load(self):
        loc = self.var_loc.get().strip().strip('"')
        if not loc:
            messagebox.showinfo(APP_NAME, "Choose an Excel file or paste a Google Sheets link first.")
            return
        self.lbl_found.configure(text="Opening ...")
        self.cmb_tab.set("")
        self.cmb_col.set("")
        self.grid_cache.clear()
        status_tab = self.var_tab.get()     # Tk variables must only be read on the UI thread

        def work():
            wb = open_workbook(loc, self.settings)
            tabs = wb.tab_names()
            best_tab, best_score, grids = None, -1.0, {}
            for t in tabs:
                if t == status_tab:
                    continue
                g = wb.read_tab(t)
                grids[t] = g
                _, cols = analyse_columns(g)
                score = cols[0].matches if cols else 0
                if score > best_score:
                    best_tab, best_score = t, score
            return wb, tabs, grids, best_tab

        def done(res, err):
            if err:
                self.lbl_found.configure(text=f"Couldn't open it: {err}")
                log.error("Couldn't open %s: %s", loc, err)
                return
            self.wb, tabs, self.grid_cache, best = res
            if self.var_kind.get() == "xlsx":
                self.settings.last_file = loc
            else:
                self.settings.last_gsheet_url = loc
            self.settings.save()
            choices = [t for t in tabs if t != self.var_tab.get()]
            self.cmb_tab["values"] = choices
            if best:
                self.cmb_tab.set(best)
            elif choices:
                self.cmb_tab.set(choices[0])
            log.info("Opened %s (%d tab(s))", self.wb.display_name, len(tabs))
            self._tab_changed()

        self._in_thread(work, done)

    # ------------------------------------------------------------ step 2
    def _tab_changed(self):
        tab = self.cmb_tab.get()
        g = self.grid_cache.get(tab)
        if g is None:
            return
        hdr, cols = analyse_columns(g)
        self.header_row = hdr
        self.col_choices = [(c.label, c.index) for c in cols] + [("(scan every column)", ALL_COLUMNS)]
        self.cmb_col["values"] = [c[0] for c in self.col_choices]
        self.cmb_col.current(0)
        if not cols:
            self.lbl_found.configure(text="No tracking-number column detected on this tab - try another tab, "
                                          "or choose '(scan every column)'.")
        self._preview()

    def _selected_column(self) -> int:
        label = self.cmb_col.get()
        return next((i for l, i in self.col_choices if l == label), ALL_COLUMNS)

    def _preview(self):
        g = self.grid_cache.get(self.cmb_tab.get())
        if g is None:
            return
        found = find_numbers(g, self._selected_column(), self.header_row)
        text = found.summary()
        if found.problems:
            text += "  (numbers stored as Excel numbers lost digits - see the log)"
            for r, c, m in found.problems[:5]:
                log.warning("Row %d: %s", r, m)
        self.lbl_found.configure(text=text, foreground="#1a7f37" if found.detections else "#b42318")

    # ------------------------------------------------------------ run
    def _run(self):
        if self.worker and self.worker.is_alive():
            return
        if not self.wb or not self.cmb_tab.get():
            messagebox.showinfo(APP_NAME, "Load a spreadsheet and pick the tab with the tracking numbers first.")
            return
        from .pipeline import Cancelled, RunRequest, run

        status_tab = self.var_tab.get().strip() or "Tracking Status"
        req = RunRequest(location=self.var_loc.get().strip().strip('"'), sheet=self.cmb_tab.get(),
                         column=self._selected_column(), extra_check=self.txt_check.get("1.0", "end").strip(),
                         status_tab=status_tab, download_pod=self.var_pod.get(),
                         reuse_delivered=self.var_reuse.get(), demo=self.var_demo.get())
        if not req.demo:
            missing = self._missing_keys()
            if missing and not messagebox.askyesno(
                    APP_NAME, "No API keys are set for: " + ", ".join(missing) + ".\n\nThose tracking numbers will be "
                    "reported as errors. Continue anyway?\n\n(Or cancel and add keys in Settings, or tick Demo mode.)"):
                return
        self.settings.status_tab_name, self.settings.download_pod = status_tab, req.download_pod
        self.settings.reuse_delivered = req.reuse_delivered
        self.settings.save()
        self.cancel.clear()
        self.btn_run.configure(state="disabled")
        self.btn_cancel.configure(state="normal")
        self.pb["value"] = 0
        log.info("---- Run: %s / tab '%s' ----", self.wb.display_name, req.sheet)
        wb = self.wb

        def work():
            return run(req, self.settings, lambda m, f=None: self.q.put(("progress", m, f)), self.cancel, workbook=wb)

        def done(summary, err):
            self.btn_run.configure(state="normal")
            self.btn_cancel.configure(state="disabled")
            self.wb = None     # a saved workbook must be re-opened before the next run
            if err:
                if isinstance(err, Cancelled):
                    self.lbl_status.configure(text="Cancelled - nothing was written.")
                    log.info("Cancelled - the spreadsheet was not changed.")
                else:
                    self.lbl_status.configure(text="Failed - see the log.")
                    log.error("Run failed: %s", err)
                    messagebox.showerror(APP_NAME, str(err))
                self._load()
                return
            self.last_saved, self.last_pod = summary.saved_to, summary.pod_folder
            self.btn_open.configure(state="normal")
            self.btn_pod.configure(state="normal" if summary.pod_folder else "disabled")
            for line in summary.text().splitlines():
                log.info(line)
            self.lbl_status.configure(text="Done - results written to the '%s' tab." % summary.status_tab)
            messagebox.showinfo(APP_NAME, summary.text())
            self._load()

        self.worker = self._in_thread(work, done)

    def _missing_keys(self) -> list[str]:
        g = self.grid_cache.get(self.cmb_tab.get()) or []
        found = find_numbers(g, self._selected_column(), getattr(self, "header_row", -1))
        needed = {d.carrier for d in found.detections.values() if d.carrier in SUPPORTED_CARRIERS}
        return sorted(c for c in needed
                      if config.lookup_method(c, self.settings) == config.API and not config.carrier_configured(c))

    def _cancel(self):
        self.cancel.set()
        self.lbl_status.configure(text="Cancelling ...")

    def _open_results(self):
        if self.last_saved.startswith("http"):
            webbrowser.open(self.last_saved)
        elif self.last_saved:
            os.startfile(self.last_saved)  # noqa: S606 - opening the user's own workbook

    def _open_pod(self):
        if self.last_pod:
            os.startfile(self.last_pod)  # noqa: S606

    def _help(self):
        guide = ROOT_DIR / "docs" / "CARRIER_API_SETUP.md"
        readme = ROOT_DIR / "README.md"
        target = guide if guide.exists() else readme
        if target.exists():
            os.startfile(str(target))  # noqa: S606
        else:
            messagebox.showinfo(APP_NAME, "See README.md in the app folder.")

    def _settings(self):
        from .settings_dialog import SettingsDialog
        SettingsDialog(self, self.settings, on_save=self._after_settings)

    def _after_settings(self):
        self._refresh_engine_label()
        self._refresh_methods()

    def _sign_in(self):
        if self.worker and self.worker.is_alive():
            messagebox.showinfo(APP_NAME, "Wait for the current run to finish first.")
            return
        sign_in(self, lambda m: self.q.put(("progress", m, None)))


SIGN_IN_URLS = ["https://www.ups.com/lasso/login?loc=en_US", "https://www.fedex.com/secure-login/en-us/#/login-credentials"]


def sign_in(parent, status):
    """Open the app's browser on the UPS and FedEx sign-in pages; the sessions are kept for website lookups."""
    from .carriers.web.browser import open_for_sign_in

    if not messagebox.askokcancel(
            APP_NAME, "A browser window will open with the UPS and FedEx sign-in pages (one tab each).\n\n"
                      "Sign in with the accounts you ship on, then CLOSE that browser window. The app remembers "
                      "the sign-ins, so the carriers' pages will show signatures and proof-of-delivery letters.",
            parent=parent):
        return

    def work():
        try:
            open_for_sign_in(SIGN_IN_URLS, status)
        except Exception as e:  # noqa: BLE001
            status(f"Sign-in window problem: {e}")
            log.error("Sign-in window problem: %s", e)
    threading.Thread(target=work, daemon=True).start()


def main():
    app = App()
    app.mainloop()
