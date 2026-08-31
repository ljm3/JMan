"""Word-level forced alignment.

Refines Whisper's word timestamps with a CTC forced-alignment model from
``torchaudio.pipelines`` (bundled weights, no Hugging Face token needed). This
gives the tight word timing needed to highlight the transcript in sync with the
audio.

Robust by design: alignment runs per ~24 s chunk; any chunk that fails keeps
Whisper's original word timing. If the model can't be loaded at all the whole
step is skipped and ``transcript.aligned`` stays ``False``.
"""
from __future__ import annotations

import re
from typing import Callable, List, Optional, Tuple

import numpy as np

from ..audio import Waveform
from ..config import Config
from ..documents.model import Segment, Transcript, Word
from ..logging_setup import get_logger

log = get_logger("align")
Progress = Callable[[float, str], None]

CHUNK_SECONDS = 24.0
_BUNDLE_CACHE: dict = {}


def _load_bundle(name: str):
    if name in _BUNDLE_CACHE:
        return _BUNDLE_CACHE[name]
    import torch
    import torchaudio

    bundle = getattr(torchaudio.pipelines, name, None)
    if bundle is None:
        raise RuntimeError(f"Unknown torchaudio bundle: {name}")
    model = bundle.get_model()
    model.eval()
    labels = bundle.get_labels()
    dictionary = {c: i for i, c in enumerate(labels)}
    _BUNDLE_CACHE[name] = (model, labels, dictionary, bundle.sample_rate)
    log.info("Loaded alignment bundle %s (sr=%d, %d labels)", name,
             bundle.sample_rate, len(labels))
    return _BUNDLE_CACHE[name]


def align(transcript: Transcript, wf: Waveform, cfg: Config, *,
          progress: Optional[Progress] = None) -> Transcript:
    p = progress or (lambda frac, msg: None)
    if not cfg.get("alignment", "enabled", True):
        log.info("Alignment disabled in config; keeping Whisper word timing.")
        return transcript
    if not transcript.segments:
        return transcript

    try:
        import torch
        model, labels, dictionary, sr = _load_bundle(
            cfg.get("alignment", "bundle", "WAV2VEC2_ASR_BASE_960H"))
    except Exception as e:  # pragma: no cover - environment dependent
        log.warning("Could not load alignment model (%s); keeping Whisper timing.", e)
        return transcript

    star = dictionary.get("*", None)
    sep = dictionary.get("|", None)
    audio = wf.samples
    n_chunks = 0
    ok_chunks = 0

    chunks = _chunk_segments(transcript.segments, CHUNK_SECONDS)
    for ci, group in enumerate(chunks):
        n_chunks += 1
        c_start = group[0].start
        c_end = group[-1].end
        a0 = max(0, int(c_start * sr))
        a1 = min(len(audio), int(c_end * sr) + 1)
        if a1 - a0 < sr // 4:
            continue
        clip = torch.from_numpy(audio[a0:a1]).float().unsqueeze(0)
        try:
            with torch.inference_mode():
                emissions, _ = model(clip)
                emissions = torch.log_softmax(emissions, dim=-1)
            emission = emissions[0].cpu()
            frames = emission.size(0)
            ratio = (a1 - a0) / frames / sr  # seconds per emission frame

            words = [w for s in group for w in s.words]
            tokens_per_word, flat_tokens = _tokenize_words(
                [w.text for w in words], dictionary)
            if not flat_tokens:
                continue

            targets = torch.tensor([flat_tokens], dtype=torch.int32)
            token_spans = _forced_align(emission, targets)
            spans = _group_tokens_into_words(token_spans, tokens_per_word)
            for w, span in zip(words, spans):
                if span is None:
                    continue
                s_frame, e_frame, sc = span
                w.start = round(c_start + s_frame * ratio, 3)
                w.end = round(c_start + max(e_frame, s_frame + 1) * ratio, 3)
                w.score = round(float(sc), 4)
            ok_chunks += 1
        except Exception as e:
            log.debug("chunk %d alignment failed: %s", ci, e)
        p(0.1 + 0.85 * (ci + 1) / max(len(chunks), 1),
          f"Aligned words {ci + 1}/{len(chunks)} chunks")

    # Keep segment text/order; make segment bounds follow refined words.
    for seg in transcript.segments:
        if seg.words:
            seg.start = min(seg.start, seg.words[0].start)
            seg.end = max(seg.end, seg.words[-1].end)

    transcript.aligned = ok_chunks > 0
    log.info("Alignment done: %d/%d chunks refined.", ok_chunks, n_chunks)
    p(1.0, f"Word alignment complete ({ok_chunks}/{n_chunks} chunks)")
    return transcript


# --------------------------------------------------------------------------- #
def _chunk_segments(segments: List[Segment], seconds: float) -> List[List[Segment]]:
    groups: List[List[Segment]] = []
    cur: List[Segment] = []
    base = None
    for s in segments:
        if not s.words:
            continue
        if base is None:
            base = s.start
        if cur and (s.end - base) > seconds:
            groups.append(cur)
            cur = []
            base = s.start
        cur.append(s)
    if cur:
        groups.append(cur)
    return groups


def _tokenize_words(texts: List[str], dictionary: dict) -> Tuple[List[int], List[int]]:
    """Map each word to CTC label ids. Returns (token_count_per_word, flat_tokens)."""
    per_word: List[int] = []
    flat: List[int] = []
    for t in texts:
        norm = re.sub(r"[^A-Za-z']", "", t.upper())
        ids = [dictionary[c] for c in norm if c in dictionary]
        if not ids:
            # fall back to a single blank-ish placeholder so indexing stays aligned
            ids = [dictionary.get("|", 0)]
        per_word.append(len(ids))
        flat.extend(ids)
    return per_word, flat


def _forced_align(emission, targets):
    """Return a list of (start_frame, end_frame, score) - one per target token."""
    import torchaudio.functional as AF

    aligned, scores = AF.forced_align(emission.unsqueeze(0), targets, blank=0)
    aligned = aligned[0]
    scores = scores[0].exp()

    try:  # official helper collapses the path to one span per token
        token_spans = AF.merge_tokens(aligned, scores)
        return [(int(ts.start), int(ts.end), float(ts.score)) for ts in token_spans]
    except Exception:
        pass

    # Manual fallback: forced_align keeps target order, so non-blank runs map 1:1
    out = []
    i, T = 0, aligned.size(0)
    while i < T:
        tok = int(aligned[i])
        j = i
        while j < T and int(aligned[j]) == tok:
            j += 1
        if tok != 0:
            out.append((i, j, float(scores[i:j].mean()) if j > i else 0.0))
        i = j
    return out


def _group_tokens_into_words(token_spans, tokens_per_word: List[int]):
    out = []
    idx = 0
    for count in tokens_per_word:
        grp = token_spans[idx: idx + count]
        idx += count
        if not grp:
            out.append(None)
            continue
        out.append((grp[0][0], grp[-1][1], sum(g[2] for g in grp) / len(grp)))
    return out
