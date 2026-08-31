"""The setup wizard (requirements 1-4).

Five steps:
  1. Where is the audio? (local path or online URL) + optional title/date/speakers
  2. Which documents to create? (multi-select, exact wording from the brief)
  3. Provide access to the source audio? (no / attach / link)
  4. Output formats (docx / odt / pdf)
  5. Review & start
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFormLayout,
                               QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QRadioButton, QSpinBox,
                               QStackedWidget, QVBoxLayout, QWidget, QButtonGroup,
                               QMessageBox, QPlainTextEdit)

from ..documents.model import (AudioAccess, DocType, OutputFormat, WizardAnswers)

_DOC_ORDER = [
    DocType.DIGITAL_TRANSCRIPT,
    DocType.TRANSCRIPT_NAMED,
    DocType.TRANSCRIPT_GENERIC,
    DocType.MINUTES_WITH_SYNOPSIS,
    DocType.MINUTES_NO_SYNOPSIS,
    DocType.ACTION_ITEMS,
]


def _h(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setStyleSheet("font-size: 17px; font-weight: 600; margin-bottom: 4px;")
    return lab


def _sub(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setWordWrap(True)
    lab.setStyleSheet("color: palette(mid); margin-bottom: 10px;")
    return lab


class WizardWidget(QWidget):
    submitted = Signal(object)   # WizardAnswers

    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.stack = QStackedWidget()
        self._build_pages()

        self.back_btn = QPushButton("\u2039  Back")
        self.next_btn = QPushButton("Next  \u203a")
        self.back_btn.clicked.connect(self._back)
        self.next_btn.clicked.connect(self._next)

        nav = QHBoxLayout()
        nav.addWidget(self.back_btn)
        nav.addStretch(1)
        self.step_lbl = QLabel()
        self.step_lbl.setStyleSheet("color: palette(mid);")
        nav.addWidget(self.step_lbl)
        nav.addStretch(1)
        nav.addWidget(self.next_btn)

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 20)
        root.addWidget(self.stack, 1)
        line = QFrame(); line.setFrameShape(QFrame.HLine)
        root.addWidget(line)
        root.addLayout(nav)
        self._sync_nav()

    # ------------------------------------------------------------------ pages
    def _build_pages(self):
        self.stack.addWidget(self._page_source())
        self.stack.addWidget(self._page_documents())
        self.stack.addWidget(self._page_audio_access())
        self.stack.addWidget(self._page_formats())
        self.stack.addWidget(self._page_review())

    def _page_source(self) -> QWidget:
        w = QWidget(); v = QVBoxLayout(w)
        v.addWidget(_h("1.  Where is the audio to analyse?"))
        v.addWidget(_sub("Choose a file on this computer, or paste a link "
                         "(direct media URL, YouTube, Google Drive, Dropbox, "
                         "SharePoint, podcast page, ...). Everything is processed "
                         "locally; the link is only used to download the audio."))

        self.src_local = QRadioButton("Local file")
        self.src_url = QRadioButton("Online link (URL)")
        self.src_local.setChecked(True)
        grp = QButtonGroup(w); grp.addButton(self.src_local); grp.addButton(self.src_url)

        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText(r"C:\Users\me\Recordings\board-meeting.m4a")
        browse = QPushButton("Browse\u2026")
        browse.clicked.connect(self._browse)
        row = QHBoxLayout(); row.addWidget(self.path_edit, 1); row.addWidget(browse)

        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("https://\u2026")
        self.url_edit.setEnabled(False)

        self.src_local.toggled.connect(lambda on: (self.path_edit.setEnabled(on),
                                                   browse.setEnabled(on),
                                                   self.url_edit.setEnabled(not on)))

        v.addWidget(self.src_local)
        v.addLayout(row)
        v.addSpacing(6)
        v.addWidget(self.src_url)
        v.addWidget(self.url_edit)
        v.addSpacing(16)

        form = QFormLayout()
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("(optional) e.g. Q3 Board Meeting")
        self.date_edit = QLineEdit()
        self.date_edit.setPlaceholderText("(optional) e.g. 2026-08-29")
        self.speaker_spin = QSpinBox(); self.speaker_spin.setRange(0, 40)
        self.speaker_spin.setSpecialValueText("Unknown / auto-detect")
        form.addRow("Meeting title:", self.title_edit)
        form.addRow("Meeting date:", self.date_edit)
        form.addRow("Number of speakers:", self.speaker_spin)
        v.addLayout(form)
        v.addStretch(1)
        return w

    def _page_documents(self) -> QWidget:
        w = QWidget(); v = QVBoxLayout(w)
        v.addWidget(_h("2.  Which documents should be produced?"))
        v.addWidget(_sub("Select one or more. Transcripts and minutes can be "
                         "generated together."))
        self.doc_checks = {}
        for dt in _DOC_ORDER:
            cb = QCheckBox(dt.human)
            self.doc_checks[dt] = cb
            v.addWidget(cb)
        self.doc_checks[DocType.MINUTES_WITH_SYNOPSIS].setChecked(True)
        self.doc_checks[DocType.ACTION_ITEMS].setChecked(True)

        note = _sub("Note: “identified speaker attribution” requires speaker "
                    "separation (pyannote) and that you confirm each speaker's "
                    "name after analysis. Without a Hugging Face token (Settings) "
                    "everything is attributed to a single speaker.")
        v.addSpacing(10); v.addWidget(note)
        v.addStretch(1)
        return w

    def _page_audio_access(self) -> QWidget:
        w = QWidget(); v = QVBoxLayout(w)
        v.addWidget(_h("3.  Provide access to the source recording?"))
        v.addWidget(_sub("Should the finished documents give the reader a way "
                         "back to the original audio?"))
        self.aa_none = QRadioButton("No \u2013 documents only")
        self.aa_attach = QRadioButton("Yes \u2013 attach the audio file "
                                      "(a copy is placed next to the documents)")
        self.aa_link = QRadioButton("Yes \u2013 link to the audio file "
                                    "(reference its path / URL, no copy)")
        self.aa_none.setChecked(True)
        g = QButtonGroup(w)
        for b in (self.aa_none, self.aa_attach, self.aa_link):
            g.addButton(b); v.addWidget(b)
        v.addSpacing(8)
        v.addWidget(_sub("For PDF output, “attach” also embeds the recording "
                         "inside the PDF as a file attachment. The interactive "
                         "transcript always keeps a copy of the audio beside it."))
        v.addStretch(1)
        return w

    def _page_formats(self) -> QWidget:
        w = QWidget(); v = QVBoxLayout(w)
        v.addWidget(_h("4.  File formats for the finished documents"))
        v.addWidget(_sub("Pick one or more. Each selected document is written in "
                         "each selected format."))
        self.fmt_checks = {}
        defaults = set(self.cfg.get("export", "default_formats", ["docx"]))
        for of in (OutputFormat.DOCX, OutputFormat.ODT, OutputFormat.PDF):
            cb = QCheckBox(of.value.upper())
            cb.setChecked(of.value in defaults)
            self.fmt_checks[of] = cb
            v.addWidget(cb)
        v.addSpacing(10)
        v.addWidget(_sub("The interactive (highlighting) transcript is always an "
                         "HTML file regardless of the choice above."))
        v.addStretch(1)
        return w

    def _page_review(self) -> QWidget:
        w = QWidget(); v = QVBoxLayout(w)
        v.addWidget(_h("5.  Review"))
        v.addWidget(_sub("These answers are written to the session log before "
                         "analysis begins."))
        self.review_text = QPlainTextEdit()
        self.review_text.setReadOnly(True)
        self.review_text.setStyleSheet("font-family: Consolas, monospace;")
        v.addWidget(self.review_text, 1)
        return w

    # ---------------------------------------------------------------- nav
    def _browse(self):
        start = str(Path.home())
        fn, _ = QFileDialog.getOpenFileName(
            self, "Choose an audio or video recording", start,
            "Media files (*.wav *.mp3 *.m4a *.aac *.flac *.ogg *.opus *.wma "
            "*.mp4 *.m4v *.mov *.mkv *.webm *.avi);;All files (*.*)")
        if fn:
            self.path_edit.setText(fn)
            self.src_local.setChecked(True)

    def _current(self) -> int:
        return self.stack.currentIndex()

    def _sync_nav(self):
        i = self._current()
        self.back_btn.setEnabled(i > 0)
        self.next_btn.setText("Start analysis  \u25b6" if i == self.stack.count() - 1
                              else "Next  \u203a")
        self.step_lbl.setText(f"Step {i + 1} of {self.stack.count()}")

    def _back(self):
        if self._current() > 0:
            self.stack.setCurrentIndex(self._current() - 1)
            self._sync_nav()

    def _next(self):
        i = self._current()
        if not self._validate(i):
            return
        if i == self.stack.count() - 1:
            self.submitted.emit(self._collect())
            return
        if i == self.stack.count() - 2:
            self.review_text.setPlainText(self._summary_text())
        self.stack.setCurrentIndex(i + 1)
        self._sync_nav()

    def _validate(self, i: int) -> bool:
        if i == 0:
            if self.src_local.isChecked():
                p = self.path_edit.text().strip().strip('"')
                if not p:
                    return self._warn("Please choose an audio file.")
                if not Path(p).expanduser().is_file():
                    return self._warn(f"File not found:\n{p}")
            else:
                u = self.url_edit.text().strip()
                if not (u.lower().startswith("http://") or u.lower().startswith("https://")):
                    return self._warn("Please enter a valid http(s) URL.")
        if i == 1 and not any(cb.isChecked() for cb in self.doc_checks.values()):
            return self._warn("Select at least one document to produce.")
        if i == 3 and not any(cb.isChecked() for cb in self.fmt_checks.values()):
            return self._warn("Select at least one output format.")
        return True

    def _warn(self, msg: str) -> bool:
        QMessageBox.warning(self, "Meeting Scribe", msg)
        return False

    # ------------------------------------------------------------- collect
    def _collect(self) -> WizardAnswers:
        is_url = self.src_url.isChecked()
        source = (self.url_edit.text().strip() if is_url
                  else self.path_edit.text().strip().strip('"'))
        docs = [dt for dt in _DOC_ORDER if self.doc_checks[dt].isChecked()]
        aa = (AudioAccess.ATTACH if self.aa_attach.isChecked()
              else AudioAccess.LINK if self.aa_link.isChecked()
              else AudioAccess.NONE)
        fmts = [of for of, cb in self.fmt_checks.items() if cb.isChecked()]
        return WizardAnswers(
            source=source,
            source_is_url=is_url,
            doc_types=docs,
            audio_access=aa,
            formats=fmts,
            known_speaker_count=self.speaker_spin.value(),
            meeting_title=self.title_edit.text().strip(),
            meeting_date=self.date_edit.text().strip(),
        )

    def _summary_text(self) -> str:
        a = self._collect()
        lines = ["AUDIO SOURCE",
                 f"  {'URL' if a.source_is_url else 'Local file'}: {a.source}",
                 "",
                 "DOCUMENTS TO CREATE"]
        lines += [f"  \u2022 {d.human}" for d in a.doc_types]
        lines += ["",
                  f"SOURCE-AUDIO ACCESS : {a.audio_access.value}",
                  f"OUTPUT FORMATS      : {', '.join(f.value.upper() for f in a.formats)}",
                  f"KNOWN SPEAKERS      : {a.known_speaker_count or 'auto-detect'}",
                  f"MEETING TITLE       : {a.meeting_title or '(derive)'}",
                  f"MEETING DATE        : {a.meeting_date or '(derive from file)'}",
                  "",
                  f"Whisper model       : {self.cfg.get('transcription', 'model')}",
                  f"Analysis LLM        : {self.cfg.get('llm', 'model')}",
                  f"Speaker separation  : "
                  + ("pyannote (token set)" if (self.cfg.get('diarization', 'hf_token') or '').strip()
                     else "single speaker (no HF token)")]
        return "\n".join(lines)
