"""Background pipeline thread for the GUI."""
from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from ..config import Config
from ..documents.model import Project, WizardAnswers
from ..logging_setup import get_logger

log = get_logger("gui.worker")


class PipelineWorker(QThread):
    progress = Signal(float, str, str)     # overall fraction, stage, message
    finished_ok = Signal(object)           # Project
    failed = Signal(str)

    def __init__(self, answers: WizardAnswers, cfg: Config, parent=None):
        super().__init__(parent)
        self._answers = answers
        self._cfg = cfg
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True
        log.warning("Cancellation requested by user.")

    def run(self) -> None:  # QThread entry point
        from ..pipeline.runner import run, Cancelled
        try:
            project = run(
                self._answers, self._cfg,
                report=lambda f, s, m: self.progress.emit(float(f), s, m),
                should_cancel=lambda: self._cancel,
            )
            self.finished_ok.emit(project)
        except Cancelled:
            self.failed.emit("Cancelled by user.")
        except Exception as e:  # pragma: no cover
            log.exception("Pipeline crashed: %s", e)
            self.failed.emit(str(e))
