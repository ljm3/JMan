"""A Session owns one run: its own directory, its own verbose log file, and the
tool ledger.  Every run of the analyzer creates a fresh session folder
(requirement 6)."""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Callable

from .paths import sessions_dir
from .toolinfo import ToolLedger

# a sink receives (level, message) for live display (GUI / stdout)
Sink = Callable[[str, str], None]


class Session:
    def __init__(self, sink: Sink | None = None):
        ts = datetime.now()
        self.id = ts.strftime("%Y%m%d_%H%M%S") + f"_{os.getpid()}"
        self.started = ts
        self.dir = sessions_dir() / f"session_{self.id}"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.fetch_dir = self.dir / "_fetched"
        self.ledger = ToolLedger()
        self._sink = sink
        self._log_path = self.dir / "session.log"
        self._fh = open(self._log_path, "a", encoding="utf-8")
        self._step = 0
        self.log("INFO", f"Session {self.id} started; folder: {self.dir}")

    # ---- paths ----------------------------------------------------------
    def path(self, name: str) -> Path:
        return self.dir / name

    # ---- logging ------------------------------------------------------------
    def log(self, level: str, message: str) -> None:
        line = f"{datetime.now().isoformat(timespec='seconds')}  {level:<5}  {message}"
        try:
            self._fh.write(line + "\n")
            self._fh.flush()
        except Exception:
            pass
        if self._sink:
            try:
                self._sink(level, message)
            except Exception:
                pass

    def step(self, message: str) -> None:
        """A verbose, numbered pipeline step (requirement 5)."""
        self._step += 1
        self.log("STEP", f"[{self._step:02d}] {message}")

    def detail(self, message: str) -> None:
        self.log("INFO", f"     {message}")

    def warn(self, message: str) -> None:
        self.log("WARN", message)

    # ---- lifecycle -------------------------------------------------------
    def finalize_tool_report(self) -> tuple[Path, Path]:
        md = self.path("tools_and_versions.md")
        js = self.path("tools_and_versions.json")
        md.write_text(self.ledger.to_markdown(self.id), encoding="utf-8")
        js.write_text(self.ledger.to_json(), encoding="utf-8")
        self.log("INFO", f"Wrote tool report: {md.name} / {js.name}")
        return md, js

    def close(self) -> None:
        self.log("INFO", f"Session {self.id} closed.")
        try:
            self._fh.close()
        except Exception:
            pass
