"""ToolLedger - records every tool/library/binary actually used during a session
and writes the per-session "secondary document" (requirement 7)."""
from __future__ import annotations

import importlib
import json
import platform
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from importlib import metadata as importlib_metadata
from typing import Any

from . import __version__


class ToolLedger:
    def __init__(self) -> None:
        self._entries: dict[tuple[str, str], dict[str, Any]] = {}
        self._answers: dict[str, str] = {}
        self._generated_files: list[dict[str, Any]] = []
        self.started = datetime.now(timezone.utc)
        self._seed_environment()

    # ---- run answers / outputs (requirement: record them in this document) ----
    def set_answers(self, mapping: dict[str, str]) -> None:
        self._answers = {str(k): str(v) for k, v in dict(mapping).items()}

    def set_generated_files(self, items: list[dict[str, Any]]) -> None:
        self._generated_files = [dict(it) for it in items]

    # ---- recording ------------------------------------------------------
    def record(self, category: str, name: str, version: str | None = None,
               detail: str | None = None, purpose: str | None = None) -> None:
        key = (category, name)
        cur = self._entries.get(key, {})
        entry = {
            "category": category,
            "name": name,
            "version": version or cur.get("version"),
            "detail": detail or cur.get("detail"),
            "purposes": sorted(set(cur.get("purposes", [])) | ({purpose} if purpose else set())),
        }
        self._entries[key] = entry

    def record_module(self, modname: str, purpose: str | None = None) -> str | None:
        """Import `modname`, remember its version, return the version string."""
        ver = None
        try:
            mod = importlib.import_module(modname)
            ver = getattr(mod, "__version__", None)
        except Exception as exc:  # noqa: BLE001 - we want the reason recorded
            self.record("python-package", modname, version=None,
                        detail=f"import failed: {exc}", purpose=purpose)
            return None
        if ver is None:
            for dist in (modname, modname.replace("_", "-")):
                try:
                    ver = importlib_metadata.version(dist)
                    break
                except importlib_metadata.PackageNotFoundError:
                    continue
        self.record("python-package", modname, version=ver, purpose=purpose)
        return ver

    def record_binary(self, name: str, version_args: list[str] | None = None,
                      purpose: str | None = None) -> str | None:
        path = shutil.which(name)
        if not path:
            self.record("external-binary", name, version=None,
                        detail="not found on PATH", purpose=purpose)
            return None
        ver = None
        try:
            out = subprocess.run([path, *(version_args or ["--version"])],
                                 capture_output=True, text=True, timeout=15)
            ver = (out.stdout or out.stderr).splitlines()[0].strip() if (out.stdout or out.stderr) else None
        except Exception as exc:  # noqa: BLE001
            self.record("external-binary", name, version=None,
                        detail=f"version probe failed: {exc}", purpose=purpose)
            return None
        self.record("external-binary", name, version=ver, detail=path, purpose=purpose)
        return ver

    # ---- environment seed --------------------------------------------------
    def _seed_environment(self) -> None:
        self.record("runtime", "doc-analyzer", version=__version__,
                    purpose="this application")
        self.record("runtime", "python", version=platform.python_version(),
                    detail=sys.executable, purpose="interpreter")
        self.record("runtime", "platform", version=platform.platform(),
                    detail=f"{platform.machine()} / {platform.node()}",
                    purpose="host workstation")

    # ---- output ----------------------------------------------------------
    def as_dict(self) -> dict[str, Any]:
        rows = sorted(self._entries.values(), key=lambda e: (e["category"], e["name"].lower()))
        return {
            "session_started_utc": self.started.isoformat(),
            "generated_utc": datetime.now(timezone.utc).isoformat(),
            "answers": dict(self._answers),
            "generated_files": list(self._generated_files),
            "tool_count": len(rows),
            "tools": rows,
        }

    def to_markdown(self, session_id: str) -> str:
        d = self.as_dict()
        out = [
            f"# Tools & versions used - session {session_id}",
            "",
            "This secondary document lists every runtime, library and external",
            "program the analyzer actually invoked while producing this session's",
            "synopsis and activity breakdown, together with the answers given for",
            "this run and the result files it produced.",
            "",
            f"- Session started (UTC): `{d['session_started_utc']}`",
            f"- Report generated (UTC): `{d['generated_utc']}`",
            f"- Distinct tools recorded: **{d['tool_count']}**",
            "",
        ]
        if d.get("answers"):
            out += ["## Analysis answers", "", "| Question | Answer |", "|---|---|"]
            for k, v in d["answers"].items():
                q = str(k).replace("|", "\\|")
                a = str(v).replace("|", "\\|")
                out.append(f"| {q} | {a} |")
            out.append("")
        if d.get("generated_files"):
            out += ["## Generated files", "", "| Deliverable | Format | File |", "|---|---|---|"]
            for g in d["generated_files"]:
                out.append(f"| {g.get('deliverable', '')} | {g.get('format', '')} | {g.get('file', '')} |")
            out.append("")
        out += [
            "## Tools & versions",
            "",
            "| Category | Tool | Version | Used for | Notes |",
            "|---|---|---|---|---|",
        ]
        for e in d["tools"]:
            purposes = "; ".join(e["purposes"]) or "-"
            notes = (e["detail"] or "-").replace("|", "\\|")
            if e["version"]:
                ver = e["version"]
            elif e["category"] in ("analysis-step",):
                ver = f"internal ({__version__})"
            else:
                ver = "_not detected_"
            out.append(f"| {e['category']} | `{e['name']}` | {ver} | {purposes} | {notes} |")
        out.append("")
        return "\n".join(out)

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), indent=2)
