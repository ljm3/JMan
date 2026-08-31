"""Processing view (live progress + log) and results view."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
                               QPlainTextEdit, QProgressBar, QPushButton,
                               QVBoxLayout, QWidget, QGroupBox, QFrame)

from ..documents.model import Project, Severity
from ..logging_setup import attach_sink, detach_sink, current_session

_STAGE_SEQUENCE = ["Fetch source", "Load audio", "Transcribe", "Align words",
                   "Diarize speakers", "Draft minutes", "Extract action items",
                   "Accuracy checks", "Write deliverables"]

_LEVEL_COLOR = {"WARNING": "#b8860b", "ERROR": "#c0392b", "CRITICAL": "#c0392b"}


def open_path(path: str | Path) -> None:
    p = str(path)
    try:
        if sys.platform.startswith("win"):
            os.startfile(p)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", p])
        else:
            subprocess.Popen(["xdg-open", p])
    except Exception:
        pass


class ProcessingView(QWidget):
    cancel_requested = Signal()
    _log_line = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sink = self._log_line.emit
        self._seen_stages: set[str] = set()

        self.header = QLabel("Analysing recording…")
        self.header.setStyleSheet("font-size: 18px; font-weight: 600;")
        self.stage_lbl = QLabel("Starting…")
        self.stage_lbl.setStyleSheet("color: palette(mid);")

        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(True)
        self.bar.setFormat("%p%")

        self.stage_list = QListWidget()
        self.stage_list.setMaximumWidth(220)
        for s in _STAGE_SEQUENCE:
            QListWidgetItem("○  " + s, self.stage_list)

        self.console = QPlainTextEdit()
        self.console.setReadOnly(True)
        self.console.setMaximumBlockCount(6000)
        f = QFont("Consolas"); f.setStyleHint(QFont.Monospace); f.setPointSize(9)
        self.console.setFont(f)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self._on_cancel)

        mid = QHBoxLayout()
        mid.addWidget(self.stage_list)
        mid.addWidget(self.console, 1)

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        bottom.addWidget(self.cancel_btn)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 18)
        root.addWidget(self.header)
        root.addWidget(self.stage_lbl)
        root.addWidget(self.bar)
        root.addLayout(mid, 1)
        root.addLayout(bottom)

        self._log_line.connect(self._append)

    # -- lifecycle --------------------------------------------------------
    def start(self):
        self.bar.setValue(0)
        self.console.clear()
        self._seen_stages.clear()
        for i in range(self.stage_list.count()):
            self.stage_list.item(i).setText("○  " + _STAGE_SEQUENCE[i])
        attach_sink(self._sink)
        s = current_session()
        if s:
            self._append("INFO", f"Session log: {s.path}")

    def stop(self):
        detach_sink(self._sink)

    # -- slots ----------------------------------------------------------
    def on_progress(self, frac: float, stage: str, message: str):
        self.bar.setValue(int(max(0.0, min(1.0, frac)) * 1000))
        if stage and stage != "Done":
            self.stage_lbl.setText(f"{stage} — {message}" if message else stage)
            self._mark_stage(stage)

    def _mark_stage(self, stage: str):
        if stage in self._seen_stages or stage not in _STAGE_SEQUENCE:
            return
        self._seen_stages.add(stage)
        idx = _STAGE_SEQUENCE.index(stage)
        for i in range(self.stage_list.count()):
            if i < idx:
                self.stage_list.item(i).setText("✔  " + _STAGE_SEQUENCE[i])
            elif i == idx:
                self.stage_list.item(i).setText("▶  " + _STAGE_SEQUENCE[i])
        self.stage_list.setCurrentRow(idx)

    def finish_stages(self):
        for i in range(self.stage_list.count()):
            self.stage_list.item(i).setText("✔  " + _STAGE_SEQUENCE[i])

    def _append(self, level: str, msg: str):
        color = _LEVEL_COLOR.get(level.upper())
        if color:
            self.console.appendHtml(
                f'<span style="color:{color}">{_esc(msg)}</span>')
        else:
            self.console.appendPlainText(msg)
        sb = self.console.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _on_cancel(self):
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.setText("Cancelling…")
        self.cancel_requested.emit()


def _esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


class ResultsView(QWidget):
    new_run_requested = Signal()
    review_speakers_requested = Signal(object)   # Project

    def __init__(self, parent=None):
        super().__init__(parent)
        self._project: Project | None = None

        self.banner = QLabel()
        self.banner.setStyleSheet("font-size: 18px; font-weight: 600;")
        self.subtitle = QLabel()
        self.subtitle.setWordWrap(True)
        self.subtitle.setStyleSheet("color: palette(mid);")

        self.qc_box = QGroupBox("Accuracy checks")
        qc_l = QVBoxLayout(self.qc_box)
        self.qc_summary = QLabel()
        self.qc_detail = QPlainTextEdit()
        self.qc_detail.setReadOnly(True)
        self.qc_detail.setMaximumHeight(180)
        qc_l.addWidget(self.qc_summary)
        qc_l.addWidget(self.qc_detail)

        self.files_box = QGroupBox("Deliverables")
        fl = QVBoxLayout(self.files_box)
        self.files = QListWidget()
        self.files.itemDoubleClicked.connect(
            lambda it: open_path(it.data(Qt.UserRole)))
        fl.addWidget(QLabel("Double-click a file to open it."))
        fl.addWidget(self.files)

        self.open_folder_btn = QPushButton("Open output folder")
        self.open_html_btn = QPushButton("Open interactive transcript")
        self.open_log_btn = QPushButton("Open session log")
        self.review_btn = QPushButton("Review speakers & re-export…")
        self.new_btn = QPushButton("New run")
        self.open_folder_btn.clicked.connect(self._open_folder)
        self.open_html_btn.clicked.connect(self._open_html)
        self.open_log_btn.clicked.connect(self._open_log)
        self.review_btn.clicked.connect(
            lambda: self._project and self.review_speakers_requested.emit(self._project))
        self.new_btn.clicked.connect(self.new_run_requested.emit)

        btns = QHBoxLayout()
        for b in (self.open_folder_btn, self.open_html_btn, self.open_log_btn,
                  self.review_btn):
            btns.addWidget(b)
        btns.addStretch(1)
        btns.addWidget(self.new_btn)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 18)
        root.addWidget(self.banner)
        root.addWidget(self.subtitle)
        root.addWidget(self.qc_box)
        root.addWidget(self.files_box, 1)
        root.addLayout(btns)

    def show_project(self, project: Project):
        self._project = project
        a = project.answers
        r = project.qc
        n_files = len([d for d in project.deliverables
                       if not d.endswith((".json",))])
        if r.passed:
            self.banner.setText("✔  Analysis complete")
            self.banner.setStyleSheet("font-size:18px;font-weight:600;color:#2b8a3e;")
        else:
            self.banner.setText("⚠  Analysis complete — review needed")
            self.banner.setStyleSheet("font-size:18px;font-weight:600;color:#b8860b;")
        self.subtitle.setText(
            f"{n_files} file(s) written to  {project.output_dir}")

        self.qc_summary.setText(
            f"{len(r.errors)} error(s), {len(r.warnings)} warning(s), "
            f"{len([i for i in r.issues if i.severity == Severity.INFO])} note(s).   "
            f"Checks run: {', '.join(r.checks_run)}")
        lines = []
        for sev, tag in ((Severity.ERROR, "ERROR"), (Severity.WARNING, "WARN"),
                         (Severity.INFO, "note")):
            for i in [x for x in r.issues if x.severity == sev]:
                lines.append(f"[{tag}] ({i.category}) {i.detail}"
                             + (f"\n        fix: {i.suggestion}" if i.suggestion else ""))
        self.qc_detail.setPlainText("\n".join(lines) or "No issues raised.")

        self.files.clear()
        html_path = None
        for d in project.deliverables:
            p = Path(d)
            if p.suffix.lower() == ".json":
                continue
            it = QListWidgetItem(p.name)
            it.setData(Qt.UserRole, str(p))
            self.files.addItem(it)
            if p.suffix.lower() == ".html":
                html_path = str(p)
        self._html_path = html_path
        self.open_html_btn.setEnabled(bool(html_path))
        self.review_btn.setEnabled(a.wants_named_speakers())

    # -- button slots ---------------------------------------------------
    def _open_folder(self):
        if self._project:
            open_path(self._project.output_dir)

    def _open_html(self):
        if getattr(self, "_html_path", None):
            open_path(self._html_path)

    def _open_log(self):
        s = current_session()
        if s:
            open_path(s.path)
