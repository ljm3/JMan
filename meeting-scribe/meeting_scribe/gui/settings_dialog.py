"""Settings dialog - edits the persistent config file."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QFileDialog, QFormLayout, QHBoxLayout, QLabel,
                               QLineEdit, QPushButton, QSpinBox, QVBoxLayout,
                               QWidget)

from ..config import Config

_WHISPER = ["tiny", "base", "small", "medium", "large-v3", "distil-large-v3"]
_LLM = ["Qwen/Qwen3-1.7B", "Qwen/Qwen3-4B", "Qwen/Qwen3-8B",
        "Qwen/Qwen3-14B", "Qwen/Qwen3-32B"]
_DTYPE = ["int8", "bfloat16", "float32", "float16", "auto"]
_DIAR = ["auto", "pyannote", "off"]


class SettingsDialog(QDialog):
    def __init__(self, cfg: Config, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.setWindowTitle("Meeting Scribe — Settings")
        self.setMinimumWidth(560)

        form = QFormLayout()

        self.workspace = QLineEdit(cfg.get("general", "workspace", ""))
        self.workspace.setPlaceholderText("(default: ~/MeetingScribe)")
        ws_btn = QPushButton("Browse…")
        ws_btn.clicked.connect(self._pick_workspace)
        ws_row = QHBoxLayout(); ws_row.addWidget(self.workspace, 1); ws_row.addWidget(ws_btn)
        ws_wrap = QWidget(); ws_wrap.setLayout(ws_row)
        form.addRow("Workspace folder:", ws_wrap)

        self.language = QLineEdit(cfg.get("general", "language", "auto"))
        form.addRow("Language (ISO or 'auto'):", self.language)

        self.whisper = QComboBox(); self.whisper.addItems(_WHISPER)
        self.whisper.setCurrentText(cfg.get("transcription", "model", "medium"))
        form.addRow("Whisper model:", self.whisper)

        self.compute = QComboBox(); self.compute.addItems(["int8", "int8_float16", "float16", "float32"])
        self.compute.setCurrentText(cfg.get("transcription", "compute_type", "int8"))
        form.addRow("Whisper compute type:", self.compute)

        self.align = QCheckBox("Enable word-level forced alignment (needed for highlight sync)")
        self.align.setChecked(bool(cfg.get("alignment", "enabled", True)))
        form.addRow("Alignment:", self.align)

        self.diar = QComboBox(); self.diar.addItems(_DIAR)
        self.diar.setCurrentText(cfg.get("diarization", "backend", "auto"))
        form.addRow("Diarization backend:", self.diar)

        self.hf = QLineEdit(cfg.get("diarization", "hf_token", ""))
        self.hf.setEchoMode(QLineEdit.Password)
        self.hf.setPlaceholderText("hf_… (accept pyannote/speaker-diarization-3.1 conditions first)")
        form.addRow("Hugging Face token:", self.hf)

        self.min_spk = QSpinBox(); self.min_spk.setRange(0, 40)
        self.min_spk.setValue(int(cfg.get("diarization", "min_speakers", 0)))
        self.max_spk = QSpinBox(); self.max_spk.setRange(0, 40)
        self.max_spk.setValue(int(cfg.get("diarization", "max_speakers", 0)))
        sp = QHBoxLayout(); sp.addWidget(QLabel("min")); sp.addWidget(self.min_spk)
        sp.addWidget(QLabel("max")); sp.addWidget(self.max_spk); sp.addStretch(1)
        sp_wrap = QWidget(); sp_wrap.setLayout(sp)
        form.addRow("Speaker bounds (0=auto):", sp_wrap)

        self.llm = QComboBox(); self.llm.setEditable(True); self.llm.addItems(_LLM)
        self.llm.setCurrentText(cfg.get("llm", "model", "Qwen/Qwen3-8B"))
        form.addRow("Analysis LLM (HF id):", self.llm)

        self.dtype = QComboBox(); self.dtype.addItems(_DTYPE)
        self.dtype.setCurrentText(cfg.get("llm", "dtype", "bfloat16"))
        form.addRow("LLM dtype:", self.dtype)

        self.max_new = QSpinBox(); self.max_new.setRange(256, 8192)
        self.max_new.setSingleStep(128)
        self.max_new.setValue(int(cfg.get("llm", "max_new_tokens", 2048)))
        form.addRow("LLM max new tokens:", self.max_new)

        self.reviewer = QCheckBox("Run the LLM reviewer pass during accuracy checks")
        self.reviewer.setChecked(bool(cfg.get("qc", "llm_reviewer", True)))
        form.addRow("QC:", self.reviewer)

        hint = QLabel(
            "CPU-only note: LLM generation is slow. dtype 'int8' (dynamic "
            "quantization) is the fastest CPU option, then 'bfloat16'. Qwen3-4B "
            "is the balanced pick; Qwen3-1.7B is ~2x faster for quick drafts; "
            "8B/14B really want a GPU. Whisper 'medium' int8 is a good CPU "
            "default; 'large-v3' is much slower.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(mid); font-size: 11px;")

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)

        root = QVBoxLayout(self)
        root.addLayout(form)
        root.addWidget(hint)
        root.addWidget(buttons)

    def _pick_workspace(self):
        d = QFileDialog.getExistingDirectory(self, "Choose workspace folder",
                                             self.workspace.text() or str(Path.home()))
        if d:
            self.workspace.setText(d)

    def _save(self):
        c = self.cfg
        c.set("general", "workspace", self.workspace.text().strip())
        c.set("general", "language", self.language.text().strip() or "auto")
        c.set("transcription", "model", self.whisper.currentText())
        c.set("transcription", "compute_type", self.compute.currentText())
        c.set("alignment", "enabled", self.align.isChecked())
        c.set("diarization", "backend", self.diar.currentText())
        c.set("diarization", "hf_token", self.hf.text().strip())
        c.set("diarization", "min_speakers", self.min_spk.value())
        c.set("diarization", "max_speakers", self.max_spk.value())
        c.set("llm", "model", self.llm.currentText().strip())
        c.set("llm", "dtype", self.dtype.currentText())
        c.set("llm", "max_new_tokens", self.max_new.value())
        c.set("qc", "llm_reviewer", self.reviewer.isChecked())
        try:
            c.save()
        except Exception as e:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(self, "Settings", f"Could not save config:\n{e}")
            return
        # Re-apply workspace immediately.
        from .. import paths
        paths.set_workspace(self.workspace.text().strip() or None)
        self.accept()
