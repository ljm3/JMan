"""Filesystem locations used by the app.

The workspace holds everything the app generates that is *not* a final
deliverable: session logs, downloaded audio, saved projects, and the local
model cache. It defaults to ``%USERPROFILE%\\MeetingScribe`` but can be
overridden in the config file.
"""
from __future__ import annotations

import os
from pathlib import Path

from . import APP_SLUG


def _appdata() -> Path:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return Path(base)


def config_dir() -> Path:
    d = _appdata() / APP_SLUG
    d.mkdir(parents=True, exist_ok=True)
    return d


def config_file() -> Path:
    return config_dir() / "config.toml"


def default_workspace() -> Path:
    return Path.home() / APP_SLUG


_workspace_override: Path | None = None


def set_workspace(path: str | os.PathLike | None) -> None:
    global _workspace_override
    _workspace_override = Path(path).expanduser() if path else None


def workspace() -> Path:
    d = _workspace_override or default_workspace()
    d.mkdir(parents=True, exist_ok=True)
    return d


def _sub(name: str) -> Path:
    d = workspace() / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def logs_dir() -> Path:
    return _sub("logs")


def downloads_dir() -> Path:
    return _sub("downloads")


def projects_dir() -> Path:
    return _sub("projects")


def models_dir() -> Path:
    return _sub("models")


def apply_model_cache_env() -> None:
    """Point Hugging Face / torch caches inside the workspace so the install
    stays self-contained and easy to relocate or delete."""
    mc = str(models_dir())
    os.environ.setdefault("HF_HOME", mc)
    os.environ.setdefault("HUGGINGFACE_HUB_CACHE", str(models_dir() / "hub"))
    os.environ.setdefault("TORCH_HOME", str(models_dir() / "torch"))
    # Keep tokenizers quiet about fork parallelism warnings.
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
