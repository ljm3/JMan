from __future__ import annotations

from pathlib import Path

from .base import ExtractResult
from .text import _read_text

_LANG = {
    ".py": "Python", ".js": "JavaScript", ".jsx": "JavaScript (JSX)", ".ts": "TypeScript",
    ".tsx": "TypeScript (TSX)", ".java": "Java", ".cs": "C#", ".cpp": "C++", ".cc": "C++",
    ".c": "C", ".h": "C/C++ header", ".hpp": "C++ header", ".go": "Go", ".rb": "Ruby",
    ".php": "PHP", ".rs": "Rust", ".swift": "Swift", ".kt": "Kotlin", ".scala": "Scala",
    ".sh": "Shell", ".ps1": "PowerShell", ".psm1": "PowerShell module", ".bat": "Batch",
    ".sql": "SQL", ".r": "R", ".m": "MATLAB/Objective-C", ".pl": "Perl", ".lua": "Lua",
    ".html": "HTML", ".htm": "HTML", ".css": "CSS", ".scss": "SCSS", ".vue": "Vue",
}


def extract(path: Path, cfg) -> ExtractResult:
    body = _read_text(path)
    lines = body.splitlines()
    ext = path.suffix.lower()
    blank = sum(1 for ln in lines if not ln.strip())
    comment = sum(1 for ln in lines if ln.lstrip()[:2] in ("//", "# ", "--", "/*", "* ")
                  or ln.lstrip().startswith("#"))
    lang = _LANG.get(ext, ext.lstrip("."))
    tools: list[tuple[str, str]] = []
    try:
        from pygments.lexers import guess_lexer_for_filename
        lang = guess_lexer_for_filename(path.name, body).name
        tools.append(("pygments", "identify source-code language"))
    except Exception:
        pass
    meta = {
        "language": lang,
        "lines_total": len(lines),
        "lines_blank": blank,
        "lines_comment": comment,
        "lines_code": len(lines) - blank - comment,
    }
    return ExtractResult(text=body, kind="code", extractor="source reader", meta=meta, tools=tools)
