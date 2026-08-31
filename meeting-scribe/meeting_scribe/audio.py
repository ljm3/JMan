"""Audio loading via PyAV (no system ffmpeg required).

Decodes any container/codec PyAV supports to a mono float32 numpy array at a
target sample rate, and can produce a coarse waveform envelope for the UI.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Tuple

import numpy as np

from .logging_setup import get_logger

log = get_logger("audio")

TARGET_SR = 16000


@dataclass
class Waveform:
    samples: np.ndarray   # float32, mono, [-1, 1]
    sample_rate: int

    @property
    def duration(self) -> float:
        return len(self.samples) / float(self.sample_rate)


def load_audio(path: str | Path, sr: int = TARGET_SR) -> Waveform:
    import av

    path = str(path)
    log.info("Decoding audio: %s", path)
    container = av.open(path)
    try:
        if not container.streams.audio:
            raise ValueError(f"No audio stream in {path}")
        stream = container.streams.audio[0]
        stream.thread_type = "AUTO"
        resampler = av.AudioResampler(format="s16", layout="mono", rate=sr)
        blocks = []
        for frame in container.decode(stream):
            for rframe in resampler.resample(frame):
                blocks.append(rframe.to_ndarray().reshape(-1))
        for rframe in resampler.resample(None):  # flush
            blocks.append(rframe.to_ndarray().reshape(-1))
    finally:
        container.close()

    if not blocks:
        raise ValueError(f"Decoded no audio from {path}")
    pcm = np.concatenate(blocks).astype(np.float32) / 32768.0
    wf = Waveform(samples=pcm, sample_rate=sr)
    log.info("Decoded %.1f s of audio (%d samples @ %d Hz)", wf.duration, len(pcm), sr)
    return wf


def probe_duration(path: str | Path) -> float:
    import av

    with av.open(str(path)) as c:
        if c.duration:
            return float(c.duration) / av.time_base
        if c.streams.audio:
            s = c.streams.audio[0]
            if s.duration and s.time_base:
                return float(s.duration * s.time_base)
    return 0.0


def envelope(samples: np.ndarray, sample_rate: int, buckets: int = 1600) -> Tuple[np.ndarray, np.ndarray]:
    """Return (min, max) per bucket for a lightweight waveform plot."""
    n = len(samples)
    if n == 0:
        return np.zeros(buckets, np.float32), np.zeros(buckets, np.float32)
    buckets = max(1, min(buckets, n))
    edges = np.linspace(0, n, buckets + 1, dtype=np.int64)
    mn = np.empty(buckets, np.float32)
    mx = np.empty(buckets, np.float32)
    for i in range(buckets):
        seg = samples[edges[i]:edges[i + 1]]
        if seg.size:
            mn[i] = seg.min()
            mx[i] = seg.max()
        else:
            mn[i] = mx[i] = 0.0
    return mn, mx
