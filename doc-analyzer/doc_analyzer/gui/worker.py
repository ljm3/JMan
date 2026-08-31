"""Runs an analysis on a background thread and reports progress through a queue."""
from __future__ import annotations

import queue
import threading
import traceback


class AnalysisWorker:
    def __init__(self):
        self.q: "queue.Queue[tuple[str, object]]" = queue.Queue()
        self._thread: threading.Thread | None = None
        self.session = None
        self.result = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, source_spec: dict, claude: bool, run_opts=None, local_model=None) -> None:
        if self.running:
            return
        self._thread = threading.Thread(
            target=self._run, args=(source_spec, claude, run_opts, local_model), daemon=True)
        self._thread.start()

    def _run(self, source_spec: dict, claude: bool, run_opts=None, local_model=None) -> None:
        try:
            from ..config import Config
            from ..session import Session
            from ..analyze import analyze

            def sink(level: str, msg: str) -> None:
                self.q.put(("log", f"{level:<5} {msg}"))

            cfg = Config.load()
            if claude:
                cfg.set("claude.enabled", True)
            if local_model is not None:
                cfg.set("llm.enabled", bool(local_model))
                if local_model:
                    # the GUI checkbox drives both planning and the notes review
                    steps = list(cfg.llm_steps)
                    for s in ("plan", "coaching"):
                        if s not in steps:
                            steps.append(s)
                    cfg.set("llm.steps", steps)
            self.session = Session(sink=sink)
            self.q.put(("session", str(self.session.dir)))
            self.result = analyze(self.session, cfg, source_spec, run_opts)
            self.session.close()
            self.q.put(("done", self.result))
        except Exception as exc:  # noqa: BLE001
            tb = traceback.format_exc()
            if self.session:
                try:
                    self.session.warn(f"FAILED: {exc}")
                    self.session.close()
                except Exception:
                    pass
            self.q.put(("error", f"{type(exc).__name__}: {exc}\n\n{tb}"))
