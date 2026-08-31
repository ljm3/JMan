"""Application configuration: a small TOML file in the OS config dir.

Only settings that a user might reasonably want to change between runs live
here. The per-meeting choices (source, which documents, which formats) are
asked every session by the wizard and are *not* stored here.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict

from . import paths

try:  # py311+
    import tomllib as _toml_read
except ModuleNotFoundError:  # pragma: no cover - py310
    import tomli as _toml_read  # type: ignore

import tomli_w


DEFAULTS: Dict[str, Any] = {
    "general": {
        "workspace": "",          # "" -> paths.default_workspace()
        "language": "auto",       # ISO code or "auto"
    },
    "transcription": {
        "model": "medium",        # tiny|base|small|medium|large-v3|distil-large-v3
        "compute_type": "int8",   # int8 is the sweet spot on CPU
        "beam_size": 1,
        "vad_filter": True,
    },
    "alignment": {
        "enabled": True,
        "bundle": "WAV2VEC2_ASR_BASE_960H",   # torchaudio.pipelines bundle
    },
    "diarization": {
        "backend": "auto",        # auto -> pyannote if token present, else off
        "hf_token": "",
        "min_speakers": 0,        # 0 = unknown
        "max_speakers": 0,
    },
    "llm": {
        "model": "Qwen/Qwen3-4B",  # 1.7B / 4B / 8B / 14B / 32B - see README hardware notes
        "dtype": "int8",           # int8|bfloat16|float32|float16|auto (int8 fastest on CPU)
        "max_new_tokens": 1536,
        "map_reduce_char_limit": 40000,
        "enable_thinking": False,
    },
    "qc": {
        "llm_reviewer": True,
        "min_avg_logprob": -1.0,
        "max_compression_ratio": 2.4,
        "max_no_speech_prob": 0.6,
    },
    "export": {
        "default_formats": ["docx"],
    },
}


def _deep_merge(base: Dict[str, Any], over: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


@dataclass
class Config:
    data: Dict[str, Any] = field(default_factory=lambda: copy.deepcopy(DEFAULTS))
    path: Path = field(default_factory=paths.config_file)

    # -- section accessors -------------------------------------------------
    def __getitem__(self, section: str) -> Dict[str, Any]:
        return self.data[section]

    def get(self, section: str, key: str, default: Any = None) -> Any:
        return self.data.get(section, {}).get(key, default)

    def set(self, section: str, key: str, value: Any) -> None:
        self.data.setdefault(section, {})[key] = value

    # -- persistence ----------------------------------------------------------
    @classmethod
    def load(cls) -> "Config":
        p = paths.config_file()
        raw: Dict[str, Any] = {}
        if p.exists():
            try:
                raw = _toml_read.loads(p.read_text(encoding="utf-8"))
            except Exception:
                raw = {}
        cfg = cls(data=_deep_merge(DEFAULTS, raw), path=p)
        cfg._apply_env_overrides()
        ws = cfg.get("general", "workspace") or ""
        paths.set_workspace(ws or None)
        return cfg

    def _apply_env_overrides(self) -> None:
        """Per-run overrides via environment, without touching the config file.
        MS_WHISPER_MODEL, MS_WHISPER_COMPUTE, MS_LLM_MODEL, MS_LLM_DTYPE,
        MS_LLM_MAX_NEW_TOKENS, MS_QC_REVIEWER, MS_HF_TOKEN, MS_DIARIZATION."""
        import os as _os
        m = {
            "MS_WHISPER_MODEL": ("transcription", "model", str),
            "MS_WHISPER_COMPUTE": ("transcription", "compute_type", str),
            "MS_LLM_MODEL": ("llm", "model", str),
            "MS_LLM_DTYPE": ("llm", "dtype", str),
            "MS_LLM_MAX_NEW_TOKENS": ("llm", "max_new_tokens", int),
            "MS_QC_REVIEWER": ("qc", "llm_reviewer",
                               lambda v: v.lower() in ("1", "true", "yes", "on")),
            "MS_HF_TOKEN": ("diarization", "hf_token", str),
            "MS_DIARIZATION": ("diarization", "backend", str),
        }
        for env, (sec, key, cast) in m.items():
            val = _os.environ.get(env)
            if val is not None and val != "":
                try:
                    self.set(sec, key, cast(val))
                except Exception:
                    pass

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(tomli_w.dumps(self.data), encoding="utf-8")

    def as_dict(self) -> Dict[str, Any]:
        return copy.deepcopy(self.data)


def autotune_for_hardware(cfg: Config) -> Config:
    """Pick sane defaults for the detected machine. Called once by the
    installer (``--configure-defaults``) and safe to re-run."""
    try:
        import psutil  # optional
        total_gb = psutil.virtual_memory().total / (1024 ** 3)
    except Exception:
        try:
            import os
            total_gb = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / (1024 ** 3)  # type: ignore
        except Exception:
            total_gb = 16.0

    cuda = False
    try:
        import torch
        cuda = bool(torch.cuda.is_available())
    except Exception:
        cuda = False

    if cuda:
        cfg.set("transcription", "model", "large-v3")
        cfg.set("transcription", "compute_type", "float16")
        cfg.set("llm", "model", "Qwen/Qwen3-14B" if total_gb >= 24 else "Qwen/Qwen3-8B")
        cfg.set("llm", "dtype", "float16")
    else:
        # CPU-only: inference SPEED is the limiter, not RAM. Qwen3-8B runs at
        # ~5-7 min per generation on a typical desktop CPU; 4B is ~2x faster and
        # still solid for minutes/action items. Bump to 8B in Settings if you
        # have the patience or a fast many-core CPU.
        cfg.set("transcription", "model", "medium" if total_gb >= 12 else "small")
        cfg.set("llm", "model", "Qwen/Qwen3-4B")
        # int8 dynamic quantization of the Linear layers ~halves CPU generation
        # time vs bfloat16 (which itself ~halves float32). Still slow: budget a
        # few minutes per generation. Use Qwen3-1.7B for faster drafts, or an
        # 8B/14B model if you have a GPU.
        cfg.set("llm", "dtype", "int8")
        cfg.set("llm", "max_new_tokens", 1536)
        # the LLM reviewer pass is a whole extra generation; the deterministic
        # rules still cover requirement 6. Re-enable in Settings if wanted.
        cfg.set("qc", "llm_reviewer", False)
    return cfg
