"""Speech-to-text with faster-whisper (CTranslate2 backend).

Produces a :class:`~meeting_scribe.documents.model.Transcript` with segment- and
word-level timing. Word timing here is Whisper's own estimate; :mod:`.align`
can refine it with a forced-alignment model.
"""
from __future__ import annotations

from typing import Callable, Optional

import numpy as np

from ..audio import Waveform
from ..config import Config
from ..documents.model import Segment, Transcript, Word
from ..logging_setup import get_logger

log = get_logger("transcribe")
Progress = Callable[[float, str], None]


_MODEL_CACHE: dict = {}


def _get_model(name: str, compute_type: str):
    key = (name, compute_type)
    if key in _MODEL_CACHE:
        return _MODEL_CACHE[key]
    from faster_whisper import WhisperModel

    log.info("Loading faster-whisper model %r (compute_type=%s) ...", name, compute_type)
    model = WhisperModel(name, device="cpu", compute_type=compute_type)
    _MODEL_CACHE[key] = model
    log.info("Model loaded.")
    return model


def transcribe(wf: Waveform, cfg: Config, *, progress: Optional[Progress] = None) -> Transcript:
    p = progress or (lambda frac, msg: None)
    tc = cfg["transcription"]
    lang = cfg.get("general", "language", "auto")
    language = None if str(lang).lower() in ("", "auto") else lang

    model = _get_model(tc["model"], tc["compute_type"])
    p(0.05, f"Transcribing with Whisper '{tc['model']}' (this is the slow part on CPU) ...")

    seg_iter, info = model.transcribe(
        wf.samples,
        language=language,
        task="transcribe",
        beam_size=int(tc.get("beam_size", 1)),
        vad_filter=bool(tc.get("vad_filter", True)),
        word_timestamps=True,
        condition_on_previous_text=True,
    )

    detected = getattr(info, "language", None) or language or "en"
    total = wf.duration or getattr(info, "duration", 0.0) or 1.0
    log.info("Detected language: %s (p=%.2f)", detected,
             getattr(info, "language_probability", 0.0) or 0.0)

    transcript = Transcript(language=detected, duration=wf.duration or total)
    n = 0
    for s in seg_iter:
        words = []
        for w in (s.words or []):
            txt = (w.word or "").strip()
            if not txt:
                continue
            words.append(Word(start=float(w.start), end=float(w.end), text=txt,
                              score=float(getattr(w, "probability", 0.0) or 0.0)))
        seg = Segment(
            start=float(s.start), end=float(s.end), text=(s.text or "").strip(),
            words=words,
            avg_logprob=float(getattr(s, "avg_logprob", 0.0) or 0.0),
            compression_ratio=float(getattr(s, "compression_ratio", 0.0) or 0.0),
            no_speech_prob=float(getattr(s, "no_speech_prob", 0.0) or 0.0),
        )
        transcript.segments.append(seg)
        n += 1
        if n % 5 == 0:
            frac = 0.05 + 0.9 * min(s.end / total, 1.0)
            p(frac, f"Transcribed {_fmt(s.end)} / {_fmt(total)}  ({n} segments)")

    p(0.98, f"Transcription complete: {n} segments, {len(transcript.full_text())} chars")
    log.info("Transcription produced %d segments.", n)
    if n == 0:
        log.warning("No speech segments were produced.")
    return transcript


def _fmt(sec: float) -> str:
    sec = max(0, int(sec))
    return f"{sec // 60:02d}:{sec % 60:02d}"
