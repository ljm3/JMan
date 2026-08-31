"""Speaker diarization with pyannote.audio.

Splits the recording into speaker turns and assigns each transcript word/segment
to a speaker. Generic labels ("Speaker 1", "Speaker 2", ...) are always
produced; the user confirms real names afterwards in the review step.

pyannote's models are gated on Hugging Face: the user must accept the conditions
for ``pyannote/speaker-diarization-3.1`` and ``pyannote/segmentation-3.0`` and
supply a token (Settings dialog). Without a usable token this step degrades
gracefully to a single speaker and records a QC note.
"""
from __future__ import annotations

from typing import Callable, Dict, List, Optional

import numpy as np

from ..audio import Waveform
from ..config import Config
from ..documents.model import Segment, Speaker, Transcript
from ..logging_setup import get_logger

log = get_logger("diarize")
Progress = Callable[[float, str], None]

_PIPELINE_CACHE: dict = {}


class DiarizationUnavailable(RuntimeError):
    pass


def _load_pyannote(token: str):
    if "pipe" in _PIPELINE_CACHE:
        return _PIPELINE_CACHE["pipe"]
    import torch
    from pyannote.audio import Pipeline

    log.info("Loading pyannote speaker-diarization-3.1 ...")
    try:  # pyannote >= 3.3 renamed the kwarg
        pipe = Pipeline.from_pretrained(
            "pyannote/speaker-diarization-3.1", token=token or True)
    except TypeError:
        pipe = Pipeline.from_pretrained(
            "pyannote/speaker-diarization-3.1", use_auth_token=token or True)
    if pipe is None:
        raise DiarizationUnavailable(
            "pyannote returned no pipeline - the token may be missing or the "
            "model conditions have not been accepted on huggingface.co")
    pipe.to(torch.device("cpu"))
    _PIPELINE_CACHE["pipe"] = pipe
    log.info("pyannote pipeline ready.")
    return pipe


def diarize(transcript: Transcript, wf: Waveform, cfg: Config, *,
            known_speakers: int = 0, progress: Optional[Progress] = None) -> Transcript:
    p = progress or (lambda frac, msg: None)
    dcfg = cfg["diarization"]
    backend = dcfg.get("backend", "auto")
    token = (dcfg.get("hf_token") or "").strip()

    if backend == "off" or (backend == "auto" and not token):
        log.warning("Diarization not run (backend=%s, token present=%s).",
                    backend, bool(token))
        return _single_speaker(transcript,
                               reason="no Hugging Face token configured"
                               if not token else "diarization disabled")

    try:
        pipe = _load_pyannote(token)
    except Exception as e:
        log.warning("pyannote unavailable: %s", e)
        return _single_speaker(transcript, reason=f"pyannote unavailable ({e})")

    p(0.1, "Running speaker diarization (pyannote) ...")
    diar_kwargs: Dict[str, int] = {}
    n = int(known_speakers or dcfg.get("min_speakers", 0) or 0)
    if n > 0:
        diar_kwargs["num_speakers"] = n
    else:
        if dcfg.get("min_speakers", 0):
            diar_kwargs["min_speakers"] = int(dcfg["min_speakers"])
        if dcfg.get("max_speakers", 0):
            diar_kwargs["max_speakers"] = int(dcfg["max_speakers"])

    waveform_t = _as_tensor(wf)
    try:
        annotation = pipe({"waveform": waveform_t, "sample_rate": wf.sample_rate},
                          **diar_kwargs)
    except TypeError:
        annotation = pipe({"waveform": waveform_t, "sample_rate": wf.sample_rate})

    turns = [(float(seg.start), float(seg.end), str(spk))
             for seg, _, spk in annotation.itertracks(yield_label=True)]
    turns.sort()
    log.info("Diarization: %d turns, %d distinct speakers.",
             len(turns), len({t[2] for t in turns}))
    if not turns:
        return _single_speaker(transcript, reason="diarizer found no speech turns")

    _assign(transcript, turns)
    p(1.0, f"Diarization complete: {len(transcript.speakers)} speakers")
    return transcript


# --------------------------------------------------------------------------- #
def _as_tensor(wf: Waveform):
    import torch

    return torch.from_numpy(np.ascontiguousarray(wf.samples)).float().unsqueeze(0)


def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def _assign(transcript: Transcript, turns: List) -> None:
    raw_ids: List[str] = []
    for r in turns:
        if r[2] not in raw_ids:
            raw_ids.append(r[2])

    # stable generic labels in order of first appearance
    speakers = [Speaker(id=rid, label=f"Speaker {i + 1}")
                for i, rid in enumerate(raw_ids)]
    transcript.speakers = speakers
    transcript.diarized = True

    def best_for(a: float, b: float) -> Optional[str]:
        best, best_ov = None, 0.0
        for (t0, t1, sid) in turns:
            ov = _overlap(a, b, t0, t1)
            if ov > best_ov:
                best_ov, best = ov, sid
        if best is None:  # fall back to nearest turn midpoint
            mid = 0.5 * (a + b)
            best = min(turns, key=lambda t: abs(0.5 * (t[0] + t[1]) - mid))[2]
        return best

    for seg in transcript.segments:
        if seg.words:
            for w in seg.words:
                w.speaker = best_for(w.start, w.end)
            # segment speaker = majority vote of its words
            counts: Dict[str, float] = {}
            for w in seg.words:
                counts[w.speaker] = counts.get(w.speaker, 0.0) + (w.end - w.start)
            seg.speaker = max(counts, key=counts.get)
        else:
            seg.speaker = best_for(seg.start, seg.end)

    _split_mixed_segments(transcript)


def _split_mixed_segments(transcript: Transcript) -> None:
    """If a segment's words span more than one speaker, break it so each printed
    line has a single speaker."""
    new_segments: List[Segment] = []
    for seg in transcript.segments:
        if not seg.words:
            new_segments.append(seg)
            continue
        runs: List[List] = []
        cur: List = []
        cur_spk = None
        for w in seg.words:
            if cur and w.speaker != cur_spk:
                runs.append(cur)
                cur = []
            cur.append(w)
            cur_spk = w.speaker
        if cur:
            runs.append(cur)
        if len(runs) <= 1:
            new_segments.append(seg)
            continue
        for run in runs:
            new_segments.append(Segment(
                start=run[0].start, end=run[-1].end,
                text=" ".join(w.text for w in run).strip(),
                speaker=run[0].speaker, words=run,
                avg_logprob=seg.avg_logprob,
                compression_ratio=seg.compression_ratio,
                no_speech_prob=seg.no_speech_prob,
            ))
    transcript.segments = new_segments


def _single_speaker(transcript: Transcript, reason: str) -> Transcript:
    log.info("Falling back to a single speaker (%s).", reason)
    spk = Speaker(id="SPEAKER_00", label="Speaker 1")
    transcript.speakers = [spk]
    transcript.diarized = False
    for seg in transcript.segments:
        seg.speaker = spk.id
        for w in seg.words:
            w.speaker = spk.id
    transcript._diarization_skip_reason = reason  # type: ignore[attr-defined]
    return transcript
