"""Config loading: config.toml (falling back to config.example.toml) + env overrides."""
from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

from .paths import CONFIG_EXAMPLE, CONFIG_PATH

# env var -> ("section.key", cast)
_ENV = {
    "DOCAN_MAX_FILE_MB": ("analysis.max_file_mb", float),
    "DOCAN_MAX_DOCS": ("analysis.max_docs", int),
    "DOCAN_SIMILARITY": ("analysis.similarity_threshold", float),
    "DOCAN_OCR": ("analysis.ocr_enabled", "bool"),
    "DOCAN_RECURSIVE": ("analysis.recursive", "bool"),
    "DOCAN_LLM_ENABLED": ("llm.enabled", "bool"),
    "DOCAN_LLM_MS_PATH": ("llm.meeting_scribe_path", str),
    "DOCAN_LLM_PYTHON": ("llm.python", str),
    "DOCAN_LLM_MODEL": ("llm.model", str),
    "DOCAN_LLM_MAX_TOKENS": ("llm.max_new_tokens", int),
    "DOCAN_LLM_TIMEOUT": ("llm.timeout_seconds", int),
    "DOCAN_LLM_STEPS": ("llm.steps", "csv"),
    "DOCAN_CLAUDE_ENABLED": ("claude.enabled", "bool"),
    "ANTHROPIC_API_KEY": ("claude.api_key", str),
    "DOCAN_CLAUDE_MODEL": ("claude.model", str),
    "DOCAN_OUTPUT_FORMAT": ("output.default_format", str),
    "DOCAN_WRITE_TO_SOURCE": ("output.write_to_source_folder", "bool"),
    "DOCAN_RESULTS_PREFIX": ("output.results_subfolder_prefix", str),
    "DOCAN_HTTP_TOKEN": ("sources.http.token", str),
    "DOCAN_SP_TENANT": ("sources.sharepoint.tenant_id", str),
    "DOCAN_SP_CLIENT_ID": ("sources.sharepoint.client_id", str),
    "DOCAN_SP_CLIENT_SECRET": ("sources.sharepoint.client_secret", str),
    "DOCAN_SP_SITE": ("sources.sharepoint.site", str),
    "AWS_DEFAULT_REGION": ("sources.s3.region", str),
    "AWS_ACCESS_KEY_ID": ("sources.s3.access_key_id", str),
    "AWS_SECRET_ACCESS_KEY": ("sources.s3.secret_access_key", str),
    "AZURE_STORAGE_CONNECTION_STRING": ("sources.azure_blob.connection_string", str),
    "DOCAN_AZ_ACCOUNT_URL": ("sources.azure_blob.account_url", str),
    "DOCAN_AZ_SAS": ("sources.azure_blob.sas_token", str),
}


def _as_bool(v: str) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes", "on")


class Config:
    def __init__(self, data: dict[str, Any], path: Path | None):
        self._d = data
        self.path = path

    @classmethod
    def load(cls) -> "Config":
        src = CONFIG_PATH if CONFIG_PATH.exists() else CONFIG_EXAMPLE
        data: dict[str, Any] = {}
        if src.exists():
            with open(src, "rb") as fh:
                data = tomllib.load(fh)
        cfg = cls(data, src if src.exists() else None)
        cfg._apply_env()
        return cfg

    def _apply_env(self) -> None:
        for env, (dotted, cast) in _ENV.items():
            if env not in os.environ or os.environ[env] == "":
                continue
            raw = os.environ[env]
            if cast == "bool":
                val: Any = _as_bool(raw)
            elif cast == "csv":
                val = [x.strip() for x in raw.split(",") if x.strip()]
            else:
                val = cast(raw)
            self.set(dotted, val)

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self._d
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def set(self, dotted: str, value: Any) -> None:
        parts = dotted.split(".")
        node = self._d
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value

    # convenience -----------------------------------------------------------
    @property
    def max_file_bytes(self) -> int:
        return int(float(self.get("analysis.max_file_mb", 25)) * 1024 * 1024)

    @property
    def max_docs(self) -> int:
        return int(self.get("analysis.max_docs", 5000))

    @property
    def similarity_threshold(self) -> float:
        return float(self.get("analysis.similarity_threshold", 0.18))

    @property
    def ocr_enabled(self) -> bool:
        return bool(self.get("analysis.ocr_enabled", True))

    @property
    def recursive(self) -> bool:
        return bool(self.get("analysis.recursive", True))

    @property
    def claude_enabled(self) -> bool:
        return bool(self.get("claude.enabled", False)) and bool(self.get("claude.api_key", ""))

    @property
    def llm_enabled(self) -> bool:
        return bool(self.get("llm.enabled", False))

    @property
    def llm_steps(self) -> list[str]:
        v = self.get("llm.steps", ["plan"])
        if isinstance(v, str):
            v = [x.strip() for x in v.split(",") if x.strip()]
        return [str(x).strip().lower() for x in (v or [])
                if str(x).strip().lower() in ("plan", "coaching", "narrative")]

    @property
    def default_output_format(self) -> str:
        v = str(self.get("output.default_format", "word")).strip().lower()
        return v if v in ("word", "spreadsheet", "presentation") else "word"

    @property
    def write_to_source_folder(self) -> bool:
        return bool(self.get("output.write_to_source_folder", True))

    @property
    def results_subfolder_prefix(self) -> str:
        return str(self.get("output.results_subfolder_prefix", "doc-analyzer-results")).strip() \
            or "doc-analyzer-results"
