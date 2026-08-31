"""Session logging.

Requirement 9: during all operations work must be shown and logs kept for each
session, numbered and dated, including the answers the user gave. A new log file
is started at the start of each session; a running ``history.log`` is appended to
across sessions.

Layout (inside ``<workspace>/logs``)::

    session-0001_2026-08-29.log      <- one file per app launch, numbered + dated
    session-0002_2026-08-29.log
    history.log                      <- every line from every session, appended

Use :func:`init_session` once at startup, then ``logging.getLogger("meeting_scribe")``
anywhere. :func:`attach_sink` lets the GUI mirror the same records into a live
console pane.
"""
from __future__ import annotations

import datetime as _dt
import logging
import re
from pathlib import Path
from typing import Callable, List, Optional

from . import paths

LOGGER_NAME = "meeting_scribe"
_SESSION: "Session | None" = None

_FMT = "%(asctime)s  %(levelname)-7s  %(name)s: %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"


class _CallbackHandler(logging.Handler):
    """Bridge log records to arbitrary callables (e.g. a Qt signal)."""

    def __init__(self) -> None:
        super().__init__()
        self._sinks: List[Callable[[str, str], None]] = []
        self.setFormatter(logging.Formatter(_FMT, _DATEFMT))

    def add_sink(self, fn: Callable[[str, str], None]) -> None:
        self._sinks.append(fn)

    def remove_sink(self, fn: Callable[[str, str], None]) -> None:
        if fn in self._sinks:
            self._sinks.remove(fn)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
        except Exception:
            return
        for fn in list(self._sinks):
            try:
                fn(record.levelname, msg)
            except Exception:
                pass


class Session:
    def __init__(self, number: int, date: _dt.date, path: Path):
        self.number = number
        self.date = date
        self.path = path
        self.started_at = _dt.datetime.now()

    @property
    def label(self) -> str:
        return f"session-{self.number:04d}_{self.date.isoformat()}"


def _next_session_number(logs: Path) -> int:
    n = 0
    pat = re.compile(r"session-(\d+)_")
    for f in logs.glob("session-*.log"):
        m = pat.match(f.name)
        if m:
            n = max(n, int(m.group(1)))
    return n + 1


def init_session() -> Session:
    """Create this session's numbered log file and wire up all handlers."""
    global _SESSION
    if _SESSION is not None:
        return _SESSION

    logs = paths.logs_dir()
    today = _dt.date.today()
    number = _next_session_number(logs)
    session_path = logs / f"session-{number:04d}_{today.isoformat()}.log"
    history_path = logs / "history.log"

    root = logging.getLogger(LOGGER_NAME)
    root.setLevel(logging.DEBUG)
    root.handlers.clear()
    root.propagate = False

    fmt = logging.Formatter(_FMT, _DATEFMT)

    fh = logging.FileHandler(session_path, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    root.addHandler(fh)

    hh = logging.FileHandler(history_path, encoding="utf-8")
    hh.setLevel(logging.INFO)
    hh.setFormatter(fmt)
    root.addHandler(hh)

    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    root.addHandler(ch)

    cb = _CallbackHandler()
    cb.setLevel(logging.INFO)
    cb.name = "callback"
    root.addHandler(cb)

    _SESSION = Session(number, today, session_path)

    log = get_logger()
    log.info("=" * 78)
    log.info("%s started  (log: %s)", _SESSION.label, session_path.name)
    log.info("workspace: %s", paths.workspace())
    log.info("=" * 78)
    return _SESSION


def current_session() -> Optional[Session]:
    return _SESSION


def get_logger(child: str | None = None) -> logging.Logger:
    name = LOGGER_NAME if not child else f"{LOGGER_NAME}.{child}"
    return logging.getLogger(name)


def _callback_handler() -> Optional[_CallbackHandler]:
    for h in logging.getLogger(LOGGER_NAME).handlers:
        if isinstance(h, _CallbackHandler):
            return h
    return None


def attach_sink(fn: Callable[[str, str], None]) -> None:
    h = _callback_handler()
    if h:
        h.add_sink(fn)


def detach_sink(fn: Callable[[str, str], None]) -> None:
    h = _callback_handler()
    if h:
        h.remove_sink(fn)


def log_wizard_answers(answers: dict) -> None:
    """Write the user's answers into the session log verbatim (requirement 9)."""
    log = get_logger("wizard")
    log.info("-" * 78)
    log.info("USER SELECTIONS FOR THIS RUN")
    for key, value in answers.items():
        if isinstance(value, (list, tuple)):
            value = ", ".join(str(v) for v in value) or "(none)"
        log.info("  %-22s : %s", key, value)
    log.info("-" * 78)
