"""Talk to a local Qwen model via a persistent worker subprocess.

The worker (:mod:`doc_analyzer.llm._worker`) runs under whichever Python has
torch + transformers, tried in this order:

  1. ``[llm].python`` in config.toml, if set                    [DOCAN_LLM_PYTHON]
  2. this app's own .venv  (``setup.ps1 -LocalModel`` installs the Qwen stack here)
  3. a meeting-scribe checkout beside this app  (legacy shared-venv setup)

If none of them has the ML stack the bridge reports unavailable and the pipeline
falls back to its deterministic analysis - the LLM layer is always optional.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from ..paths import INSTALL_ROOT


class LLMUnavailable(RuntimeError):
    pass


def _candidate_ms_paths(cfg) -> list[Path]:
    out = []
    p = cfg.get("llm.meeting_scribe_path", "") if cfg else ""
    if p:
        out.append(Path(p))
    out += [INSTALL_ROOT.parent / "meeting-scribe", INSTALL_ROOT.parent / "meeting_scribe"]
    return out


def _venv_pythons(base: Path):
    for rel in ("Scripts/python.exe", "bin/python", "bin/python3"):
        cand = base / ".venv" / rel
        if cand.exists():
            yield cand


def _candidate_pythons(cfg) -> list[Path]:
    """Interpreters to try for the worker, most-preferred first."""
    out: list[Path] = []
    p = cfg.get("llm.python", "") if cfg else ""
    if p:
        out.append(Path(p))
    out.append(Path(sys.executable))                 # this app's own .venv
    for base in _candidate_ms_paths(cfg):            # legacy: meeting-scribe's .venv
        out.extend(_venv_pythons(base))
    seen: set[str] = set()
    uniq: list[Path] = []
    for c in out:
        try:
            key = str(c.resolve()).lower()
        except Exception:
            key = str(c).lower()
        if key in seen or not c.exists():
            continue
        seen.add(key)
        uniq.append(c)
    return uniq


def _describe_python(py: Path) -> str:
    try:
        if py.resolve() == Path(sys.executable).resolve():
            return "this app's .venv"
    except Exception:
        pass
    # .../<checkout>/.venv/Scripts/python.exe  ->  <checkout>/.venv
    parts = py.parts
    if ".venv" in parts:
        i = parts.index(".venv")
        return "/".join(parts[max(0, i - 1):i + 1])
    return str(py)


class LocalLLM:
    def __init__(self, cfg, session=None):
        self.cfg = cfg
        self.session = session
        self.model = cfg.get("llm.model", "Qwen/Qwen3-4B")
        self.dtype = cfg.get("llm.dtype", "int8")
        self.max_new_tokens = int(cfg.get("llm.max_new_tokens", 2048))
        self.timeout = int(cfg.get("llm.timeout_seconds", 900))
        self._proc: subprocess.Popen | None = None
        self._pythons = _candidate_pythons(cfg)
        self._python: Path | None = self._pythons[0] if self._pythons else None
        self._rid = 0
        self._lock = threading.Lock()
        self._unavailable_reason = ""

    @property
    def interpreter(self) -> str:
        return _describe_python(self._python) if self._python else "(none)"

    # ---- availability --------------------------------------------------
    def preflight(self) -> tuple[bool, str]:
        if not self.cfg.get("llm.enabled", False):
            return False, "llm.enabled is false in config"
        if not self._pythons:
            return False, ("no Python with the ML stack found: install it into this app's "
                           ".venv with  setup.ps1 -LocalModel , or set [llm].python / "
                           "[llm].meeting_scribe_path in config.toml")
        errs = []
        for py in self._pythons:
            probe = subprocess.run([str(py), "-c", "import torch, transformers"],
                                   capture_output=True, text=True)
            if probe.returncode == 0:
                self._python = py
                return True, f"local model {self.model} via {_describe_python(py)}"
            tail = (probe.stderr.strip().splitlines() or ["import failed"])[-1][:120]
            errs.append(f"{_describe_python(py)}: {tail}")
        return False, "torch/transformers not importable in any candidate env -> " + " ; ".join(errs)

    # ---- lifecycle ----------------------------------------------------
    def start(self) -> None:
        ok, reason = self.preflight()
        if not ok:
            raise LLMUnavailable(reason)
        self._log(f"starting local model worker: {self.model} ({self.dtype}) in "
                  f"{_describe_python(self._python)}")
        env = dict(os.environ)
        env["PYTHONPATH"] = str(INSTALL_ROOT)          # so `-m doc_analyzer.llm._worker` resolves
        env.setdefault("HF_HUB_OFFLINE", "0")
        env["TOKENIZERS_PARALLELISM"] = "false"
        self._proc = subprocess.Popen(
            [str(self._python), "-m", "doc_analyzer.llm._worker"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1, env=env, cwd=str(INSTALL_ROOT),
        )
        cfg_line = json.dumps({"model": self.model, "dtype": self.dtype,
                               "max_new_tokens": self.max_new_tokens})
        self._proc.stdin.write(cfg_line + "\n")
        self._proc.stdin.flush()

        t0 = time.time()
        load_budget = max(self.timeout, 600)
        while True:
            line = self._readline(load_budget)
            if line is None:
                self._drain_stderr_into_log()
                self.stop()
                raise LLMUnavailable(f"worker did not become ready within {load_budget}s")
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if "ready" in msg:
                if msg["ready"]:
                    self._log(f"local model ready in {time.time() - t0:.0f}s")
                    return
                self.stop()
                raise LLMUnavailable(msg.get("error", "worker failed to load model"))

    def stop(self) -> None:
        if self._proc is None:
            return
        try:
            if self._proc.poll() is None:
                self._proc.stdin.write(json.dumps({"cmd": "shutdown"}) + "\n")
                self._proc.stdin.flush()
                self._proc.wait(timeout=10)
        except Exception:
            pass
        try:
            if self._proc.poll() is None:
                self._proc.kill()
        except Exception:
            pass
        self._proc = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()

    # ---- generation --------------------------------------------------
    def complete(self, system: str, user: str, max_new_tokens: int | None = None) -> str:
        if self._proc is None or self._proc.poll() is not None:
            raise LLMUnavailable("worker is not running")
        with self._lock:
            self._rid += 1
            rid = self._rid
            req = {"id": rid, "system": system, "user": user,
                   "max_new_tokens": max_new_tokens or self.max_new_tokens}
            self._proc.stdin.write(json.dumps(req) + "\n")
            self._proc.stdin.flush()
            while True:
                line = self._readline(self.timeout)
                if line is None:
                    self._drain_stderr_into_log()
                    raise LLMUnavailable(f"generation timed out after {self.timeout}s")
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if msg.get("id") != rid:
                    continue
                if msg.get("ok"):
                    return msg.get("text", "")
                raise LLMUnavailable(msg.get("error", "generation failed"))

    def complete_json(self, system: str, user: str, max_new_tokens: int | None = None,
                      retries: int = 1) -> dict:
        raw = self.complete(system, user, max_new_tokens)
        obj = _parse_json(raw)
        tries = 0
        while obj is None and tries < retries:
            tries += 1
            self._log(f"local model JSON parse failed (try {tries}); asking for clean JSON.")
            raw = self.complete(system,
                                user + "\n\nYour previous reply was not valid JSON. "
                                "Reply with ONE JSON object only - no prose, no code fences.",
                                max_new_tokens)
            obj = _parse_json(raw)
        if obj is None:
            raise LLMUnavailable(f"local model did not return parseable JSON: {raw[:200]}")
        return obj

    # ---- plumbing ---------------------------------------------------------
    def _readline(self, timeout: float) -> str | None:
        result: list[str] = []

        def _rd():
            try:
                result.append(self._proc.stdout.readline())
            except Exception:
                result.append("")

        th = threading.Thread(target=_rd, daemon=True)
        th.start()
        th.join(timeout)
        if th.is_alive() or not result:
            return None
        return (result[0] or "").strip() or None

    def _drain_stderr_into_log(self) -> None:
        try:
            self._proc.stdout.close()
        except Exception:
            pass
        try:
            err = self._proc.stderr.read() if self._proc and self._proc.stderr else ""
        except Exception:
            err = ""
        for ln in (err or "").splitlines()[-12:]:
            self._log(f"worker: {ln}")

    def _log(self, msg: str) -> None:
        if self.session is not None:
            self.session.detail(msg)
        else:
            print(f"[llm] {msg}", file=sys.stderr, flush=True)


# --------------------------------------------------------------------- helpers
def _parse_json(text: str):
    if not text:
        return None
    import re
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
    cands = []
    if fence:
        cands.append(fence.group(1))
    s, e = text.find("{"), text.rfind("}")
    if s != -1 and e > s:
        cands.append(text[s:e + 1])
    for c in cands:
        for attempt in (c, _loose_fix(c)):
            try:
                v = json.loads(attempt)
                if isinstance(v, dict):
                    return v
            except Exception:
                continue
    return None


def _loose_fix(s: str) -> str:
    import re
    s = re.sub(r",\s*([}\]])", r"\1", s)
    s = s.replace("“", '"').replace("”", '"').replace("’", "'")
    s = re.sub(r"//[^\n\"]*", "", s)
    return s
