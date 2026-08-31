from __future__ import annotations

from pathlib import Path

from .base import ExtractResult


def extract(path: Path, cfg) -> ExtractResult:
    # Prefer PyMuPDF when installed (better layout + speed), else pypdf.
    try:
        try:
            import pymupdf as fitz  # PyMuPDF >= 1.24 preferred import name
        except ImportError:
            import fitz  # older PyMuPDF
        doc = fitz.open(str(path))
        pages = [p.get_text("text") for p in doc]
        md = doc.metadata or {}
        meta = {
            "pages": doc.page_count,
            "title": md.get("title", ""),
            "author": md.get("author", ""),
            "subject": md.get("subject", ""),
            "creator": md.get("creator", ""),
            "producer": md.get("producer", ""),
            "created": md.get("creationDate", ""),
            "encrypted": bool(doc.is_encrypted),
        }
        body = "\n".join(pages)
        doc.close()
        empty = sum(1 for p in pages if not p.strip())
        if empty:
            meta["pages_without_text"] = empty
        return ExtractResult(text=body, kind="pdf", extractor="PyMuPDF", meta=meta,
                             tools=[("pymupdf", "read .pdf documents")])
    except ImportError:
        pass

    from pypdf import PdfReader
    reader = PdfReader(str(path))
    pages = []
    for pg in reader.pages:
        try:
            pages.append(pg.extract_text() or "")
        except Exception:
            pages.append("")
    di = reader.metadata or {}
    meta = {
        "pages": len(reader.pages),
        "title": str(di.get("/Title", "")),
        "author": str(di.get("/Author", "")),
        "subject": str(di.get("/Subject", "")),
        "creator": str(di.get("/Creator", "")),
        "producer": str(di.get("/Producer", "")),
        "created": str(di.get("/CreationDate", "")),
        "encrypted": bool(getattr(reader, "is_encrypted", False)),
    }
    empty = sum(1 for p in pages if not p.strip())
    if empty:
        meta["pages_without_text"] = empty
        meta["note"] = "pages without extractable text may be scanned images; enable OCR"
    return ExtractResult(text="\n".join(pages), kind="pdf", extractor="pypdf", meta=meta,
                         tools=[("pypdf", "read .pdf documents")])
