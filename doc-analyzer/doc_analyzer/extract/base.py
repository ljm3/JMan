from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ExtractResult:
    text: str = ""
    kind: str = "unknown"          # coarse family: document, spreadsheet, presentation, pdf, email, image, structured, code, text
    extractor: str = "none"       # human name of the extractor that ran
    meta: dict[str, Any] = field(default_factory=dict)
    tools: list[tuple[str, str | None]] = field(default_factory=list)  # (module, purpose)
    ok: bool = True
    error: str = ""

    @property
    def words(self) -> int:
        return len(self.text.split())

    @property
    def chars(self) -> int:
        return len(self.text)
