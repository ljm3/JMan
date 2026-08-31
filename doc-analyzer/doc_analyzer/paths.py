"""Filesystem locations used by the app."""
from __future__ import annotations

import os
from pathlib import Path

# The install root is the folder that contains the `doc_analyzer` package
# (i.e. the repo / install directory), so sessions land next to the code the
# same way meeting-scribe keeps its .venv beside itself.
INSTALL_ROOT = Path(__file__).resolve().parent.parent

CONFIG_PATH = INSTALL_ROOT / "config.toml"
CONFIG_EXAMPLE = INSTALL_ROOT / "config.example.toml"
SESSIONS_DIR = INSTALL_ROOT / "sessions"


def sessions_dir() -> Path:
    d = Path(os.environ.get("DOCAN_SESSIONS_DIR", SESSIONS_DIR))
    d.mkdir(parents=True, exist_ok=True)
    return d
