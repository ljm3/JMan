"""Tkinter desktop front-end for the Document Corpus Analyzer.

tkinter ships with the python.org / winget Python builds, so the base install
needs no GUI dependency.
"""
from __future__ import annotations

import os
import subprocess
import sys
import webbrowser
from pathlib import Path

from .. import APP_NAME, __version__
from ..analyze import RunOptions
from ..paths import sessions_dir
from ..report import formats as report_formats
from ..sources import KIND_LABELS
from .worker import AnalysisWorker

_FMT_VALUES = ["Word document", "Spreadsheet", "Presentation"]

# per-kind Options dialog fields:  key -> (label, is_secret)
_KIND_OPTIONS = {
    "git": [("branch", "Branch (optional)", False), ("depth", "Clone depth", False)],
    "http": [("token", "Bearer token (optional)", True)],
    "sharepoint": [("site", "Graph site id (contoso.sharepoint.com:/sites/X:)", False),
                   ("tenant_id", "Tenant id", False), ("client_id", "Client id", False),
                   ("client_secret", "Client secret", True)],
    "s3": [("region", "AWS region", False), ("access_key_id", "Access key id", False),
           ("secret_access_key", "Secret access key", True)],
    "azure_blob": [("connection_string", "Connection string", True),
                   ("account_url", "Account URL", False), ("sas_token", "SAS token", True)],
}
_ONLINE_KINDS = ["git", "http", "sharepoint", "s3", "azure_blob"]


def launch() -> int:
    try:
        import tkinter as tk  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        print(f"tkinter is not available: {exc}\n"
              "Use the headless mode instead:  python -m doc_analyzer --cli --source <folder>",
              file=sys.stderr)
        return 1
    App().run()
    return 0


class App:
    def __init__(self):
        import tkinter as tk
        from tkinter import ttk

        self.tk = tk
        self.ttk = ttk
        self.root = tk.Tk()
        self.root.title(f"{APP_NAME}  v{__version__}")
        self.root.geometry("980x720")
        self.root.minsize(820, 560)

        self.worker = AnalysisWorker()
        self.session_dir: Path | None = None
        self.online_options: dict[str, str] = {}

        self.var_mode = tk.StringVar(value="local")
        self.var_local = tk.StringVar()
        self.var_kind = tk.StringVar(value="git")
        self.var_online_loc = tk.StringVar()
        self.var_claude = tk.BooleanVar(value=False)
        self.var_localmodel = tk.BooleanVar(value=False)
        self.var_fmt_syn = tk.StringVar(value="Word document")
        self.var_fmt_act = tk.StringVar(value="Spreadsheet")
        self.var_fmt_tools = tk.StringVar(value="Spreadsheet")
        self.var_fmt_obj = tk.StringVar(value="Spreadsheet")

        self._build_menu()
        self._build_body()
        self._sync_mode()

    # ---- layout -------------------------------------------------------------
    def _build_menu(self):
        m = self.tk.Menu(self.root)
        filem = self.tk.Menu(m, tearoff=0)
        filem.add_command(label="Open sessions folder", command=self._open_sessions)
        filem.add_separator()
        filem.add_command(label="Exit", command=self.root.destroy)
        m.add_cascade(label="File", menu=filem)
        helpm = self.tk.Menu(m, tearoff=0)
        helpm.add_command(label="Environment report (doctor)", command=self._doctor_popup)
        helpm.add_command(label="About", command=self._about)
        m.add_cascade(label="Help", menu=helpm)
        self.root.config(menu=m)

    def _build_body(self):
        tk, ttk = self.tk, self.ttk
        pad = dict(padx=8, pady=4)

        src = ttk.LabelFrame(self.root, text="1. Where is the data to be analyzed?")
        src.pack(fill="x", **pad)

        ttk.Radiobutton(src, text="Local folder", value="local", variable=self.var_mode,
                        command=self._sync_mode).grid(row=0, column=0, sticky="w", padx=6, pady=4)
        self.e_local = ttk.Entry(src, textvariable=self.var_local)
        self.e_local.grid(row=0, column=1, sticky="ew", padx=6)
        self.b_browse = ttk.Button(src, text="Browse…", command=self._browse)
        self.b_browse.grid(row=0, column=2, padx=6)

        ttk.Radiobutton(src, text="Online repository", value="online", variable=self.var_mode,
                        command=self._sync_mode).grid(row=1, column=0, sticky="w", padx=6, pady=4)
        self.cmb_kind = ttk.Combobox(src, textvariable=self.var_kind, state="readonly",
                                     values=[f"{k}  —  {KIND_LABELS[k]}" for k in _ONLINE_KINDS],
                                     width=42)
        self.cmb_kind.grid(row=1, column=1, sticky="w", padx=6)
        self.cmb_kind.bind("<<ComboboxSelected>>", lambda _e: self.online_options.clear())
        self.b_opts = ttk.Button(src, text="Options…", command=self._edit_options)
        self.b_opts.grid(row=1, column=2, padx=6)

        ttk.Label(src, text="Location / URL").grid(row=2, column=0, sticky="e", padx=6)
        self.e_online = ttk.Entry(src, textvariable=self.var_online_loc)
        self.e_online.grid(row=2, column=1, columnspan=2, sticky="ew", padx=6, pady=(0, 6))
        src.columnconfigure(1, weight=1)

        opt = ttk.LabelFrame(self.root, text="2. Analysis objective")
        opt.pack(fill="x", **pad)
        ttk.Label(opt, text="What do you want to ascertain from this analysis?  (optional)"
                  ).pack(anchor="w", padx=6, pady=(4, 0))
        ttk.Label(opt, foreground="#666",
                  text="The app reads this to focus the run: it ranks documents by relevance, "
                       "pulls matching passages into the summaries, surfaces the entity types "
                       "you're asking about, and flags the on-objective activities. "
                       "e.g. “What financial obligations do these contracts create, and by when?”"
                  ).pack(anchor="w", padx=6, pady=(0, 2))
        from tkinter.scrolledtext import ScrolledText
        self.txt_objective = ScrolledText(opt, wrap="word", height=3, font=("Segoe UI", 9),
                                          undo=True, maxundo=-1)
        self.txt_objective.pack(fill="x", padx=6, pady=(0, 4))
        self._enable_clipboard(self.txt_objective)
        ttk.Checkbutton(opt, text="Use the local model (Qwen) to plan the analysis AND review "
                        "every notes entry (category + issue/coaching/disciplinary bucket + "
                        "round-over-round read)  -  adds several minutes on CPU; the metric "
                        "tables are computed the same either way",
                        variable=self.var_localmodel).pack(anchor="w", padx=6, pady=(4, 0))
        ttk.Checkbutton(opt, text="Also generate a narrative synopsis with Claude "
                        "(needs ANTHROPIC_API_KEY or config.toml [claude])",
                        variable=self.var_claude).pack(anchor="w", padx=6, pady=4)

        fmts = ttk.LabelFrame(self.root, text="3. Output format for each result document")
        fmts.pack(fill="x", **pad)
        for col, (lbl, var) in enumerate((
            ("Synopsis", self.var_fmt_syn),
            ("Activity breakdown", self.var_fmt_act),
            ("Objective analysis", self.var_fmt_obj),
            ("Tools & versions", self.var_fmt_tools),
        )):
            cell = ttk.Frame(fmts)
            cell.grid(row=0, column=col, sticky="w", padx=10, pady=6)
            ttk.Label(cell, text=lbl).pack(anchor="w")
            ttk.Combobox(cell, textvariable=var, state="readonly",
                         values=_FMT_VALUES, width=18).pack(anchor="w")

        act = ttk.Frame(self.root)
        act.pack(fill="x", **pad)
        self.b_run = ttk.Button(act, text="▶  Analyze", command=self._start)
        self.b_run.pack(side="left", padx=6)
        self.b_open = ttk.Button(act, text="Open session folder", command=self._open_session,
                                 state="disabled")
        self.b_open.pack(side="left", padx=6)
        self.pb = ttk.Progressbar(act, mode="indeterminate", length=220)
        self.pb.pack(side="right", padx=6)
        self.lbl_status = ttk.Label(act, text="Idle.")
        self.lbl_status.pack(side="right", padx=10)

        logf = ttk.LabelFrame(self.root, text="4-6. Verbose steps (also written to session.log)")
        logf.pack(fill="both", expand=True, **pad)
        from tkinter.scrolledtext import ScrolledText
        self.txt = ScrolledText(logf, wrap="word", height=18, font=("Consolas", 9))
        self.txt.pack(fill="both", expand=True, padx=4, pady=4)
        self.txt.configure(state="disabled")

    def _enable_clipboard(self, widget):
        """Give a Text/Entry widget reliable cut/copy/paste/select-all + a
        right-click menu.  Tk's built-in class bindings for these break on
        non-US keyboard layouts, so bind them explicitly and stop the event."""
        tk = self.tk

        def _fire(event_name):
            def handler(_e):
                try:
                    widget.event_generate(event_name)
                except Exception:
                    pass
                return "break"
            return handler

        def _select_all(_e=None):
            try:
                if isinstance(widget, tk.Text) or widget.winfo_class() == "Text":
                    widget.tag_add("sel", "1.0", "end-1c")
                    widget.mark_set("insert", "1.0")
                else:  # Entry
                    widget.select_range(0, "end")
                    widget.icursor("end")
            except Exception:
                pass
            return "break"

        for seq in ("<Control-c>", "<Control-C>"):
            widget.bind(seq, _fire("<<Copy>>"))
        for seq in ("<Control-x>", "<Control-X>"):
            widget.bind(seq, _fire("<<Cut>>"))
        for seq in ("<Control-v>", "<Control-V>"):
            widget.bind(seq, _fire("<<Paste>>"))
        for seq in ("<Control-a>", "<Control-A>"):
            widget.bind(seq, _select_all)

        menu = tk.Menu(widget, tearoff=0)
        menu.add_command(label="Cut", command=lambda: widget.event_generate("<<Cut>>"))
        menu.add_command(label="Copy", command=lambda: widget.event_generate("<<Copy>>"))
        menu.add_command(label="Paste", command=lambda: widget.event_generate("<<Paste>>"))
        menu.add_separator()
        menu.add_command(label="Select all", command=_select_all)

        def _popup(e):
            try:
                widget.focus_set()
                menu.tk_popup(e.x_root, e.y_root)
            finally:
                menu.grab_release()
            return "break"

        widget.bind("<Button-3>", _popup)
        widget.bind("<Control-Button-1>", _popup)  # mac trackpad / no right button

    # ---- behaviour --------------------------------------------------------
    def _sync_mode(self):
        online = self.var_mode.get() == "online"
        for w in (self.e_local, self.b_browse):
            w.configure(state="disabled" if online else "normal")
        for w in (self.cmb_kind, self.b_opts, self.e_online):
            w.configure(state=("readonly" if w is self.cmb_kind else "normal") if online else "disabled")

    def _current_kind(self) -> str:
        return self.var_kind.get().split("  —  ")[0].strip() or "git"

    def _browse(self):
        from tkinter import filedialog
        d = filedialog.askdirectory(title="Choose the folder with documents to analyze")
        if d:
            self.var_local.set(d)

    def _edit_options(self):
        kind = self._current_kind()
        fields = _KIND_OPTIONS.get(kind, [])
        if not fields:
            self._info(f"No extra options for '{kind}'.")
            return
        tk, ttk = self.tk, self.ttk
        dlg = tk.Toplevel(self.root)
        dlg.title(f"{KIND_LABELS[kind]} options")
        dlg.transient(self.root)
        dlg.grab_set()
        entries = {}
        for i, (key, label, secret) in enumerate(fields):
            ttk.Label(dlg, text=label).grid(row=i, column=0, sticky="e", padx=8, pady=4)
            var = tk.StringVar(value=self.online_options.get(key, ""))
            e = ttk.Entry(dlg, textvariable=var, width=52, show="•" if secret else "")
            e.grid(row=i, column=1, sticky="ew", padx=8, pady=4)
            entries[key] = var
        dlg.columnconfigure(1, weight=1)

        def save():
            self.online_options = {k: v.get().strip() for k, v in entries.items() if v.get().strip()}
            dlg.destroy()

        ttk.Button(dlg, text="Save", command=save).grid(row=len(fields), column=1, sticky="e",
                                                        padx=8, pady=8)

    def _source_spec(self):
        if self.var_mode.get() == "local":
            loc = self.var_local.get().strip()
            if not loc:
                raise ValueError("choose a local folder first")
            return {"kind": "local", "location": loc, "options": {}}
        loc = self.var_online_loc.get().strip()
        if not loc:
            raise ValueError("enter the online location / URL first")
        return {"kind": self._current_kind(), "location": loc, "options": dict(self.online_options)}

    def _start(self):
        if self.worker.running:
            return
        try:
            spec = self._source_spec()
        except ValueError as exc:
            self._info(str(exc))
            return
        objective = self.txt_objective.get("1.0", "end").strip()
        l2f = report_formats.LABEL_TO_FMT
        run_opts = RunOptions(
            objective=objective,
            formats={
                "synopsis": l2f.get(self.var_fmt_syn.get(), "word"),
                "activity": l2f.get(self.var_fmt_act.get(), "spreadsheet"),
                "objective": l2f.get(self.var_fmt_obj.get(), "spreadsheet"),
                "tools": l2f.get(self.var_fmt_tools.get(), "spreadsheet"),
            },
        )
        self._clear_log()
        self._log(f"{APP_NAME} v{__version__}")
        self._log(f"Source: {spec['kind']} -> {spec['location']}")
        self._log(f"Objective: {objective or '(not specified)'}")
        self.b_run.configure(state="disabled")
        self.b_open.configure(state="disabled")
        self.lbl_status.configure(text="Working…")
        self.pb.start(12)
        self.worker = AnalysisWorker()
        self.worker.start(spec, self.var_claude.get(), run_opts,
                          local_model=self.var_localmodel.get())
        self.root.after(120, self._poll)

    def _poll(self):
        drained = 0
        try:
            while True:
                kind, payload = self.worker.q.get_nowait()
                drained += 1
                if kind == "log":
                    self._log(str(payload))
                elif kind == "session":
                    self.session_dir = Path(str(payload))
                    self._log(f"Session folder: {payload}")
                elif kind == "done":
                    self._finish_ok(payload)
                    return
                elif kind == "error":
                    self._finish_err(str(payload))
                    return
        except Exception:
            pass
        if self.worker.running or drained:
            self.root.after(120, self._poll)
        else:
            self.root.after(250, self._poll)

    def _finish_ok(self, res):
        self.pb.stop()
        self.b_run.configure(state="normal")
        self.b_open.configure(state="normal")
        self.lbl_status.configure(text="Done.")
        self._log("")
        self._log("=== ARTIFACTS ===")
        for k, v in res.artifacts.items():
            self._log(f"  {k:26} {v}")
        self._log("")
        self._log(f"{res.files_analyzed} analyzed · {res.files_skipped} skipped · "
                  f"{res.clusters} multi-doc clusters · {res.activities} activity categories")
        deliver_dir = getattr(res, "deliver_dir", None)
        if deliver_dir:
            self._log(f"Results also written next to the analyzed files: {deliver_dir}")
        extra = f"\n\nAlso copied to:\n{deliver_dir}" if deliver_dir else ""
        self._info(f"Analysis complete.\n\n{res.files_analyzed} documents analyzed, "
                   f"{res.files_skipped} skipped.\n\nReports written to:\n{self.session_dir}{extra}")

    def _finish_err(self, msg):
        self.pb.stop()
        self.b_run.configure(state="normal")
        self.lbl_status.configure(text="Failed.")
        self._log("")
        self._log("!!! FAILED")
        self._log(msg)
        if self.session_dir:
            self.b_open.configure(state="normal")
        from tkinter import messagebox
        messagebox.showerror(APP_NAME, msg.splitlines()[0] if msg else "Analysis failed.")

    # ---- helpers --------------------------------------------------------
    def _open_session(self):
        self._open_path(self.session_dir or sessions_dir())

    def _open_sessions(self):
        self._open_path(sessions_dir())

    def _open_path(self, p):
        p = str(p)
        try:
            if sys.platform.startswith("win"):
                os.startfile(p)  # noqa: S606
            elif sys.platform == "darwin":
                subprocess.run(["open", p])
            else:
                subprocess.run(["xdg-open", p])
        except Exception:
            webbrowser.open(Path(p).as_uri())

    def _doctor_popup(self):
        import io
        import contextlib
        from ..__main__ import _doctor
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            _doctor()
        self._scroll_popup("Environment report", buf.getvalue())

    def _about(self):
        self._info(f"{APP_NAME}  v{__version__}\n\n"
                   "Point it at a folder or an online repository; it extracts every "
                   "supported document, writes a detailed synopsis, breaks down like "
                   "activity across the corpus, shows verbose steps, and logs each run "
                   "in its own session folder together with a tools & versions report.")

    def _scroll_popup(self, title, text):
        tk = self.tk
        from tkinter.scrolledtext import ScrolledText
        dlg = tk.Toplevel(self.root)
        dlg.title(title)
        dlg.geometry("760x520")
        st = ScrolledText(dlg, wrap="word", font=("Consolas", 9))
        st.pack(fill="both", expand=True)
        st.insert("1.0", text)
        st.configure(state="disabled")

    def _info(self, msg):
        from tkinter import messagebox
        messagebox.showinfo(APP_NAME, msg)

    def _clear_log(self):
        self.txt.configure(state="normal")
        self.txt.delete("1.0", "end")
        self.txt.configure(state="disabled")

    def _log(self, line):
        self.txt.configure(state="normal")
        self.txt.insert("end", line + "\n")
        self.txt.see("end")
        self.txt.configure(state="disabled")

    def run(self):
        self.root.mainloop()
