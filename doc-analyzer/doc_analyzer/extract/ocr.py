from __future__ import annotations

import shutil
from pathlib import Path

from .base import ExtractResult


def extract(path: Path, cfg) -> ExtractResult:
    if not cfg.ocr_enabled:
        return ExtractResult(kind="image", extractor="ocr (disabled)", ok=False,
                             error="OCR disabled in config (analysis.ocr_enabled=false)",
                             meta={"skipped": True})
    try:
        from PIL import Image
        import pytesseract
    except ImportError:
        return ExtractResult(kind="image", extractor="ocr (missing deps)", ok=False,
                             error="install Pillow + pytesseract (setup.ps1 -Full) for image OCR",
                             meta={"skipped": True})
    if not shutil.which("tesseract"):
        return ExtractResult(kind="image", extractor="ocr (no engine)", ok=False,
                             error="Tesseract binary not on PATH; install "
                                   "'UB-Mannheim.TesseractOCR' via winget",
                             meta={"skipped": True})
    img = Image.open(str(path))
    meta = {"width": img.width, "height": img.height, "mode": img.mode, "format": img.format}
    text = pytesseract.image_to_string(img)
    meta["ocr_chars"] = len(text.strip())
    return ExtractResult(text=text, kind="image", extractor="pytesseract + Tesseract",
                         meta=meta,
                         tools=[("PIL", "load images for OCR"),
                                ("pytesseract", "OCR text from images")])
