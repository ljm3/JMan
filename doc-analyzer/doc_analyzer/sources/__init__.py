"""Turn a user's source choice into a local folder to analyze.

A source_spec is a dict:  {"kind": "local"|"git"|"http"|"sharepoint"|"s3"|"azure_blob",
                           "location": "<path or url>", "options": {...}}
"""
from __future__ import annotations

from pathlib import Path

from . import local, git, http, sharepoint, s3, azure_blob

_KINDS = {
    "local": local.fetch,
    "git": git.fetch,
    "http": http.fetch,
    "sharepoint": sharepoint.fetch,
    "s3": s3.fetch,
    "azure_blob": azure_blob.fetch,
}

KIND_LABELS = {
    "local": "Local folder",
    "git": "Git repository",
    "http": "HTTP(S) URL / index",
    "sharepoint": "SharePoint / OneDrive (Microsoft Graph)",
    "s3": "Amazon S3",
    "azure_blob": "Azure Blob Storage",
}


def resolve_source(session, cfg, spec: dict) -> tuple[Path, str]:
    kind = (spec.get("kind") or "local").lower()
    fn = _KINDS.get(kind)
    if fn is None:
        raise ValueError(f"unknown source kind: {kind!r}")
    location = spec.get("location", "")
    options = spec.get("options", {}) or {}
    session.detail(f"Source kind: {KIND_LABELS.get(kind, kind)}  location: {location or '(none)'}")
    path = fn(session, cfg, location, options)
    path = Path(path)
    if not path.exists() or not path.is_dir():
        raise FileNotFoundError(f"resolved source path is not a directory: {path}")
    label = f"{KIND_LABELS.get(kind, kind)}: {location}" if location else KIND_LABELS.get(kind, kind)
    return path, label
