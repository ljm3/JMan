from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def fetch(session, cfg, location: str, options: dict) -> Path:
    if not location:
        raise ValueError("no Git URL was provided")
    git_exe = shutil.which("git")
    if not git_exe:
        raise RuntimeError("git is not installed / not on PATH. Install "
                           "'Git.Git' via winget and retry.")
    ver = session.ledger.record_binary("git", ["--version"], purpose="clone the source repository")
    session.detail(f"git: {ver or 'version unknown'}")

    dest = session.fetch_dir / "git"
    dest.mkdir(parents=True, exist_ok=True)
    branch = (options.get("branch") or "").strip()
    depth = str(options.get("depth") or 1)

    cmd = [git_exe, "clone", "--depth", depth]
    if branch:
        cmd += ["--branch", branch, "--single-branch"]
    cmd += [location, str(dest / "repo")]
    session.detail("Running: " + " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"git clone failed:\n{proc.stderr.strip()}")
    session.detail(proc.stderr.strip() or "clone complete.")
    session.ledger.record("source", "git repository", detail=location,
                          purpose="fetch documents to analyze")
    return dest / "repo"
