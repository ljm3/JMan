from __future__ import annotations

import csv
import io
from pathlib import Path

from .base import ExtractResult


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def extract(path: Path, cfg) -> ExtractResult:
    ext = path.suffix.lower()
    if ext == ".rtf":
        return _rtf(path)
    if ext in (".csv", ".tsv"):
        return _delimited(path, "\t" if ext == ".tsv" else ",")
    body = _read_text(path)
    kind = "text"
    meta = {"lines": body.count("\n") + 1}
    if ext in (".md", ".markdown"):
        kind = "text"
        headings = [ln.strip("# ").strip() for ln in body.splitlines() if ln.lstrip().startswith("#")]
        if headings:
            meta["headings"] = headings[:40]
    return ExtractResult(text=body, kind=kind, extractor="plain-text reader", meta=meta)


def _rtf(path: Path) -> ExtractResult:
    raw = _read_text(path)
    try:
        from striprtf.striprtf import rtf_to_text
        body = rtf_to_text(raw)
        return ExtractResult(text=body, kind="document", extractor="striprtf",
                             meta={"lines": body.count(chr(10)) + 1},
                             tools=[("striprtf", "read .rtf documents")])
    except Exception:
        # crude fallback: drop control words
        import re
        body = re.sub(r"\\[a-z]+-?\d* ?", " ", raw)
        body = re.sub(r"[{}]", " ", body)
        return ExtractResult(text=body, kind="document", extractor="rtf (naive strip)",
                             meta={"note": "striprtf unavailable; used naive control-word strip"})


def _delimited(path: Path, delim: str) -> ExtractResult:
    text = _read_text(path)
    rows = list(csv.reader(io.StringIO(text), delimiter=delim))
    header = rows[0] if rows else []
    ncols = max((len(r) for r in rows), default=0)
    # a readable projection for the language model / keyword pass
    flat_lines = [" | ".join(r) for r in rows[:2000]]
    meta = {
        "rows": len(rows),
        "columns": ncols,
        "header": header[:64],
        "delimiter": "tab" if delim == "\t" else "comma",
    }
    return ExtractResult(text="\n".join(flat_lines), kind="spreadsheet",
                         extractor="csv reader (stdlib)", meta=meta)
