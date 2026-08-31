from __future__ import annotations

import json
import tomllib
from pathlib import Path

from .base import ExtractResult
from .text import _read_text


def _flatten(obj, prefix="", out=None, depth=0):
    out = {} if out is None else out
    if depth > 6:
        return out
    if isinstance(obj, dict):
        for k, v in obj.items():
            _flatten(v, f"{prefix}.{k}" if prefix else str(k), out, depth + 1)
    elif isinstance(obj, list):
        out[prefix + "[]"] = len(obj)
        for i, v in enumerate(obj[:25]):
            _flatten(v, f"{prefix}[{i}]", out, depth + 1)
    else:
        out[prefix] = obj
    return out


def extract(path: Path, cfg) -> ExtractResult:
    ext = path.suffix.lower()
    raw = _read_text(path)
    meta: dict = {"format": ext.lstrip(".")}
    if ext == ".json":
        try:
            data = json.loads(raw)
            flat = _flatten(data)
            meta["keys"] = list(flat)[:200]
            meta["leaf_count"] = len(flat)
            body = "\n".join(f"{k}: {v}" for k, v in flat.items())
            return ExtractResult(text=body or raw, kind="structured",
                                 extractor="json (stdlib)", meta=meta)
        except Exception as exc:
            meta["parse_error"] = str(exc)
            return ExtractResult(text=raw, kind="structured", extractor="json (raw)", meta=meta)
    if ext in (".yaml", ".yml"):
        try:
            import yaml
            docs = list(yaml.safe_load_all(raw))
            data = docs[0] if len(docs) == 1 else docs
            flat = _flatten(data)
            meta["keys"] = list(flat)[:200]
            body = "\n".join(f"{k}: {v}" for k, v in flat.items())
            return ExtractResult(text=body or raw, kind="structured", extractor="PyYAML",
                                 meta=meta, tools=[("yaml", "parse .yaml files")])
        except Exception as exc:
            meta["parse_error"] = str(exc)
            return ExtractResult(text=raw, kind="structured", extractor="yaml (raw)", meta=meta)
    if ext == ".xml":
        try:
            import xml.etree.ElementTree as ET
            root = ET.fromstring(raw)
            tags: dict[str, int] = {}
            texts: list[str] = []
            for el in root.iter():
                tags[el.tag] = tags.get(el.tag, 0) + 1
                if el.text and el.text.strip():
                    texts.append(el.text.strip())
            meta["root_tag"] = root.tag
            meta["element_tags"] = dict(sorted(tags.items(), key=lambda x: -x[1])[:60])
            meta["element_count"] = sum(tags.values())
            return ExtractResult(text="\n".join(texts) or raw, kind="structured",
                                 extractor="xml.etree (stdlib)", meta=meta)
        except Exception as exc:
            meta["parse_error"] = str(exc)
            return ExtractResult(text=raw, kind="structured", extractor="xml (raw)", meta=meta)
    if ext == ".toml":
        try:
            data = tomllib.loads(raw)
            flat = _flatten(data)
            meta["keys"] = list(flat)[:200]
            body = "\n".join(f"{k}: {v}" for k, v in flat.items())
            return ExtractResult(text=body or raw, kind="structured",
                                 extractor="tomllib (stdlib)", meta=meta)
        except Exception as exc:
            meta["parse_error"] = str(exc)
    # .ini / .cfg / fallback
    return ExtractResult(text=raw, kind="structured", extractor="config text", meta=meta)
