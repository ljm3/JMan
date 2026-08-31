"""Optional local-LLM layer (Qwen3 via transformers, in this app's .venv or a
meeting-scribe checkout - see doc_analyzer.llm.bridge)."""
from .bridge import LLMUnavailable, LocalLLM

__all__ = ["LocalLLM", "LLMUnavailable"]
