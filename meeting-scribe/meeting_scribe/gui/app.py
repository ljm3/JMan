"""Application shell: wizard -> processing -> results, plus menus."""
from __future__ import annotations

import sys
from typing import List, Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QAction, QGuiApplication
from PySide6.QtWidgets import (QApplication, QMainWindow, QMessageBox,
                               QStackedWidget, QDialog, QVBoxLayout,
                               QPlainTextEdit, QPushButton)

from .. import __version__, APP_NAME, paths
from ..config import Config, autotune_for_hardware
from ..documents.model import Project, WizardAnswers
from ..logging_setup import init_session, get_logger, log_wizard_answers
from .flow_windows import ProcessingView, ResultsView, open_path
from .settings_dialog import SettingsDialog
from .transcript_viewer import SpeakerReviewDialog
from .wizard import WizardWidget
from .worker import PipelineWorker

log = get_logger("gui")


class _ReexportWorker(QThread):
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, project: Project, cfg: Config, parent=None):
        super().__init__(parent)
        self.project = project
        self.cfg = cfg

    def run(self):
        try:
            from ..documents.render import render_all
            from ..pipeline import qc as qc_mod
            import copy
            cfg2 = copy.deepcopy(self.cfg)
            cfg2.set("qc", "llm_reviewer", False)   # keep re-export fast
            self.project.qc = qc_mod.run_checks(self.project, cfg2)
            render_all(self.project)
            self.done.emit(self.project)
        except Exception as e:  # pragma: no cover
            log.exception("Re-export failed: %s", e)
            self.failed.emit(str(e))


class MainWindow(QMainWindow):
    def __init__(self, cfg: Config):
        super().__init__()
        self.cfg = cfg
        self.worker: Optional[PipelineWorker] = None
        self._reworker: Optional[_ReexportWorker] = None

        self.setWindowTitle(f"{APP_NAME}  {__version__}")
        self.resize(940, 720)

        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)

        self.wizard = WizardWidget(cfg)
        self.processing = ProcessingView()
        self.results = ResultsView()
        for w in (self.wizard, self.processing, self.results):
            self.stack.addWidget(w)

        self.wizard.submitted.connect(self._start_run)
        self.processing.cancel_requested.connect(self._cancel_run)
        self.results.new_run_requested.connect(self._new_run)
        self.results.review_speakers_requested.connect(self._review_speakers)

        self._build_menu()
        self.stack.setCurrentWidget(self.wizard)

    # ------------------------------------------------------------------ menu
    def _build_menu(self):
        m = self.menuBar()
        filem = m.addMenu("&File")
        act_new = QAction("New run", self); act_new.triggered.connect(self._new_run)
        act_ws = QAction("Open workspace folder", self)
        act_ws.triggered.connect(lambda: open_path(paths.workspace()))
        act_logs = QAction("Open logs folder", self)
        act_logs.triggered.connect(lambda: open_path(paths.logs_dir()))
        act_set = QAction("Settings…", self); act_set.triggered.connect(self._settings)
        act_quit = QAction("Quit", self); act_quit.triggered.connect(self.close)
        for a in (act_new, act_ws, act_logs, act_set):
            filem.addAction(a)
        filem.addSeparator(); filem.addAction(act_quit)

        helpm = m.addMenu("&Help")
        act_diag = QAction("Run diagnostics", self)
        act_diag.triggered.connect(self._diagnostics)
        act_about = QAction("About", self); act_about.triggered.connect(self._about)
        helpm.addAction(act_diag); helpm.addAction(act_about)

    def _settings(self):
        dlg = SettingsDialog(self.cfg, self)
        if dlg.exec() == QDialog.Accepted:
            self.wizard.cfg = self.cfg
            QMessageBox.information(self, APP_NAME,
                                   "Settings saved. They apply to the next run.")

    def _diagnostics(self):
        import io
        import contextlib
        from ..__main__ import _doctor
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            try:
                _doctor()
            except Exception as e:
                buf.write(f"\ndiagnostics error: {e}")
        dlg = QDialog(self); dlg.setWindowTitle("Diagnostics")
        dlg.resize(620, 480)
        lay = QVBoxLayout(dlg)
        te = QPlainTextEdit(buf.getvalue()); te.setReadOnly(True)
        te.setStyleSheet("font-family: Consolas, monospace;")
        lay.addWidget(te)
        b = QPushButton("Close"); b.clicked.connect(dlg.accept)
        lay.addWidget(b)
        dlg.exec()

    def _about(self):
        QMessageBox.about(
            self, f"About {APP_NAME}",
            f"<b>{APP_NAME}</b> {__version__}<br><br>"
            "Local meeting transcription, speaker attribution, minutes, "
            "synopsis, action items and accuracy checks.<br><br>"
            "All processing runs on this computer. Models: faster-whisper, "
            "torchaudio forced alignment, pyannote.audio, Qwen3 (transformers)."
            f"<br><br>Workspace: {paths.workspace()}")

    # --------------------------------------------------------------- run flow
    def _start_run(self, answers: WizardAnswers):
        log_wizard_answers(answers.as_log_dict())
        self._warn_if_needed(answers)
        self.processing.start()
        self.stack.setCurrentWidget(self.processing)
        self.worker = PipelineWorker(answers, self.cfg)
        self.worker.progress.connect(self.processing.on_progress)
        self.worker.finished_ok.connect(self._run_finished)
        self.worker.failed.connect(self._run_failed)
        self.worker.start()

    def _warn_if_needed(self, answers: WizardAnswers):
        tok = (self.cfg.get("diarization", "hf_token") or "").strip()
        if answers.wants_named_speakers() and not tok:
            QMessageBox.warning(
                self, APP_NAME,
                "You asked for a transcript with identified speakers, but no "
                "Hugging Face token is set, so speaker separation will be "
                "skipped and everything attributed to one speaker.\n\n"
                "Add a token in File ▸ Settings and accept the pyannote model "
                "conditions to enable this.")

    def _cancel_run(self):
        if self.worker:
            self.worker.cancel()

    def _run_failed(self, msg: str):
        self.processing.stop()
        QMessageBox.critical(self, APP_NAME, f"Analysis did not finish:\n\n{msg}")
        self.stack.setCurrentWidget(self.wizard)

    def _run_finished(self, project: Project):
        self.processing.stop()
        self.processing.finish_stages()
        self._project = project
        if (project.answers.wants_named_speakers() and project.transcript.diarized
                and len(project.transcript.speakers) >= 1):
            self._review_speakers(project, first_time=True)
        else:
            self.results.show_project(project)
            self.stack.setCurrentWidget(self.results)

    # ---------------------------------------------------------- speaker review
    def _review_speakers(self, project: Project, first_time: bool = False):
        dlg = SpeakerReviewDialog(project, self)
        if dlg.exec() != QDialog.Accepted:
            self.results.show_project(project)
            self.stack.setCurrentWidget(self.results)
            return
        if dlg.reexport:
            self.processing.start()
            self.processing.header.setText("Re-exporting with confirmed names…")
            self.stack.setCurrentWidget(self.processing)
            self._reworker = _ReexportWorker(project, self.cfg)
            self._reworker.done.connect(self._reexport_done)
            self._reworker.failed.connect(self._run_failed)
            self._reworker.start()
        else:
            self.results.show_project(project)
            self.stack.setCurrentWidget(self.results)

    def _reexport_done(self, project: Project):
        self.processing.stop()
        self.results.show_project(project)
        self.stack.setCurrentWidget(self.results)

    def _new_run(self):
        if self.worker and self.worker.isRunning():
            QMessageBox.information(self, APP_NAME, "A run is in progress.")
            return
        old = self.wizard
        self.wizard = WizardWidget(self.cfg)
        self.wizard.submitted.connect(self._start_run)
        self.stack.insertWidget(0, self.wizard)
        self.stack.setCurrentWidget(self.wizard)
        self.stack.removeWidget(old)
        old.deleteLater()

    def closeEvent(self, ev):
        if self.worker and self.worker.isRunning():
            if QMessageBox.question(self, APP_NAME,
                                    "A run is in progress. Quit anyway?") != QMessageBox.Yes:
                ev.ignore()
                return
            self.worker.cancel()
            self.worker.wait(3000)
        ev.accept()


def launch(argv: List[str] | None = None) -> int:
    app = QApplication(argv or sys.argv)
    app.setApplicationName(APP_NAME)

    cfg = Config.load()
    first_run = not paths.config_file().exists()
    if first_run:
        try:
            autotune_for_hardware(cfg)
            cfg.save()
        except Exception:
            pass
    paths.apply_model_cache_env()
    session = init_session()
    log.info("GUI launched (%s). First run: %s", session.label, first_run)

    win = MainWindow(cfg)
    win.show()

    if first_run:
        QMessageBox.information(
            win, APP_NAME,
            "Welcome to Meeting Scribe.\n\n"
            f"Defaults were tuned for this machine:\n"
            f"  • Whisper model: {cfg.get('transcription', 'model')}\n"
            f"  • Analysis LLM: {cfg.get('llm', 'model')}\n\n"
            "The first analysis downloads several GB of models into\n"
            f"{paths.models_dir()}\n\n"
            "For speaker separation, add a Hugging Face token in File ▸ Settings.")

    return app.exec()
