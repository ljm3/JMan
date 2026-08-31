"""Extractor registry: maps a file to text + metadata, and reports which library
it used so the session tool ledger stays accurate."""
from __future__ import annotations

from pathlib import Path

from .base import ExtractResult
from . import text as _text
from . import office as _office
from . import pdf as _pdf
from . import email_msg as _email
from . import ocr as _ocr
from . import structured as _structured
from . import code as _code

# extension (lower, with dot) -> handler(path) -> ExtractResult
_MAP: dict[str, object] = {}


def _reg(exts, fn):
    for e in exts:
        _MAP[e] = fn


_reg([".txt", ".text", ".log", ".rst", ".rtf", ".csv", ".tsv"], _text.extract)
_reg([".md", ".markdown"], _text.extract)
_reg([".docx"], _office.extract_docx)
_reg([".xlsx", ".xlsm"], _office.extract_xlsx)
_reg([".pptx"], _office.extract_pptx)
_reg([".doc", ".ppt", ".xls"], _office.extract_legacy)
_reg([".pdf"], _pdf.extract)
_reg([".eml"], _email.extract_eml)
_reg([".msg"], _email.extract_msg)
_reg([".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".webp"], _ocr.extract)
_reg([".json", ".yaml", ".yml", ".xml", ".toml", ".ini", ".cfg"], _structured.extract)
_reg([".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".cs", ".cpp", ".cc", ".c",
      ".h", ".hpp", ".go", ".rb", ".php", ".rs", ".swift", ".kt", ".scala",
      ".sh", ".ps1", ".psm1", ".bat", ".sql", ".r", ".m", ".pl", ".lua",
      ".html", ".htm", ".css", ".scss", ".vue"], _code.extract)

SUPPORTED_EXTS = frozenset(_MAP)


def is_supported(path: Path) -> bool:
    return path.suffix.lower() in _MAP


def extract_file(path: Path, cfg) -> ExtractResult:
    handler = _MAP.get(path.suffix.lower())
    if handler is None:
        return ExtractResult(kind="unknown", extractor="none", ok=False,
                             error=f"no extractor for '{path.suffix}'")
    try:
        return handler(path, cfg)  # type: ignore[operator]
    except Exception as exc:  # noqa: BLE001
        return ExtractResult(kind="unknown", extractor=handler.__name__, ok=False,
                             error=f"{type(exc).__name__}: {exc}")
