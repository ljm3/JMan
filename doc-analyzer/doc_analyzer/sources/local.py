from __future__ import annotations

from pathlib import Path


def fetch(session, cfg, location: str, options: dict) -> Path:
    if not location:
        raise ValueError("no local folder was provided")
    p = Path(location).expanduser().resolve()
    if not p.exists():
        raise FileNotFoundError(f"folder does not exist: {p}")
    if not p.is_dir():
        raise NotADirectoryError(f"not a folder: {p}")
    session.detail(f"Using local folder in place (no copy): {p}")
    session.ledger.record("source", "local filesystem", detail=str(p),
                          purpose="read documents to analyze")
    return p
