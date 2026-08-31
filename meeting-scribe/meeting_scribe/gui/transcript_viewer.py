"""Speaker review dialog.

Requirement 3 / named attribution: diarization tells voices apart, but a person
must confirm which name goes with each voice. This dialog shows sample
utterances per speaker (and can play them if Qt multimedia is available), lets
the user type real names, then optionally re-exports the deliverables.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List

from PySide6.QtCore import Qt, QUrl
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QGroupBox, QHBoxLayout,
                               QLabel, QLineEdit, QPlainTextEdit, QPushButton,
                               QScrollArea, QVBoxLayout, QWidget, QMessageBox)

from ..documents.model import Project

try:
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
    _HAVE_MEDIA = True
except Exception:  # pragma: no cover
    _HAVE_MEDIA = False


def _samples_for(project: Project, speaker_id: str, n: int = 3) -> List:
    segs = [s for s in project.transcript.segments if s.speaker == speaker_id]
    segs.sort(key=lambda s: len(s.text), reverse=True)
    return segs[:n]


class SpeakerReviewDialog(QDialog):
    def __init__(self, project: Project, parent=None):
        super().__init__(parent)
        self.project = project
        self.reexport = False
        self.setWindowTitle("Meeting Scribe — Confirm speaker names")
        self.setMinimumSize(680, 560)

        self._edits: Dict[str, QLineEdit] = {}
        self._player = None
        self._audio_out = None
        media = project.media_path
        if _HAVE_MEDIA and media and Path(media).exists():
            try:
                self._player = QMediaPlayer(self)
                self._audio_out = QAudioOutput(self)
                self._player.setAudioOutput(self._audio_out)
                self._player.setSource(QUrl.fromLocalFile(str(Path(media).resolve())))
            except Exception:
                self._player = None

        intro = QLabel(
            "Diarization separated the voices below. Listen to / read the samples "
            "and enter each speaker's real name. Leave a field blank to keep the "
            "generic label.")
        intro.setWordWrap(True)

        inner = QWidget()
        vbox = QVBoxLayout(inner)
        for spk in project.transcript.speakers:
            vbox.addWidget(self._speaker_box(spk))
        vbox.addStretch(1)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(inner)

        self.reexport_btn = QPushButton("Save names && re-export documents")
        self.save_btn = QPushButton("Save names only")
        self.cancel_btn = QPushButton("Cancel")
        self.reexport_btn.clicked.connect(lambda: self._finish(True))
        self.save_btn.clicked.connect(lambda: self._finish(False))
        self.cancel_btn.clicked.connect(self.reject)

        btns = QHBoxLayout()
        btns.addWidget(self.cancel_btn)
        btns.addStretch(1)
        btns.addWidget(self.save_btn)
        btns.addWidget(self.reexport_btn)

        root = QVBoxLayout(self)
        root.addWidget(intro)
        root.addWidget(scroll, 1)
        if not self._player:
            note = QLabel("(Audio preview unavailable — showing text samples only.)")
            note.setStyleSheet("color: palette(mid);")
            root.addWidget(note)
        root.addLayout(btns)

    def _speaker_box(self, spk) -> QGroupBox:
        box = QGroupBox(spk.label)
        lay = QVBoxLayout(box)

        row = QHBoxLayout()
        row.addWidget(QLabel("Name:"))
        edit = QLineEdit(spk.name)
        edit.setPlaceholderText(f"e.g. real name for {spk.label}")
        self._edits[spk.id] = edit
        row.addWidget(edit, 1)
        lay.addLayout(row)

        for seg in _samples_for(self.project, spk.id):
            r = QHBoxLayout()
            if self._player:
                b = QPushButton("▶")
                b.setFixedWidth(34)
                start = seg.start
                b.clicked.connect(lambda _=False, s=start: self._play(s))
                r.addWidget(b)
            t = QLabel(f"[{_ts(seg.start)}]  “{seg.text.strip()[:200]}”")
            t.setWordWrap(True)
            r.addWidget(t, 1)
            lay.addLayout(r)
        return box

    def _play(self, start: float):
        if not self._player:
            return
        try:
            self._player.setPosition(int(start * 1000))
            self._player.play()
        except Exception:
            pass

    def _finish(self, reexport: bool):
        for sid, edit in self._edits.items():
            spk = self.project.transcript.speaker_by_id(sid)
            if spk is None:
                continue
            name = edit.text().strip()
            spk.name = name
            spk.confirmed = bool(name)
        if self._player:
            try:
                self._player.stop()
            except Exception:
                pass
        self.reexport = reexport
        self.accept()


def _ts(sec: float) -> str:
    sec = max(0, int(sec))
    m, s = divmod(sec, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
