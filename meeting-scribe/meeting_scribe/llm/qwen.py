"""Thin wrapper around a local Qwen3 Instruct model (Hugging Face transformers).

Loaded lazily and cached for the process. Greedy decoding for reproducibility.
``generate_json`` extracts and repairs the first JSON object in the reply.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional

from ..config import Config
from ..logging_setup import get_logger

log = get_logger("llm")

_LLM: "LocalLLM | None" = None


def get_llm(cfg: Config) -> "LocalLLM":
    global _LLM
    mcfg = cfg["llm"]
    if _LLM is None or _LLM.model_name != mcfg["model"]:
        _LLM = LocalLLM(
            model_name=mcfg["model"],
            dtype=mcfg.get("dtype", "float32"),
            max_new_tokens=int(mcfg.get("max_new_tokens", 2048)),
            enable_thinking=bool(mcfg.get("enable_thinking", False)),
        )
    return _LLM


class LocalLLM:
    def __init__(self, model_name: str, dtype: str = "float32",
                 max_new_tokens: int = 2048, enable_thinking: bool = False):
        self.model_name = model_name
        self.dtype = dtype
        self.max_new_tokens = max_new_tokens
        self.enable_thinking = enable_thinking
        self._model = None
        self._tok = None

    # -- loading ---------------------------------------------------------------
    def load(self) -> None:
        if self._model is not None:
            return
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        dtype_map = {
            "float32": torch.float32, "float16": torch.float16,
            "bfloat16": torch.bfloat16, "auto": "auto",
        }
        has_cuda = torch.cuda.is_available()
        want_int8 = (self.dtype == "int8")
        # int8 = dynamic-quantise Linear layers after loading (CPU speed-up).
        # The quantized linear kernels require float32 activations, so the base
        # model must be loaded in float32 before quantization.
        load_dtype = torch.float32 if (want_int8 and not has_cuda) else \
            dtype_map.get(self.dtype, torch.float32)
        log.info("Loading LLM %s (dtype=%s, cuda=%s) - first run downloads several GB.",
                 self.model_name, self.dtype, has_cuda)

        self._tok = AutoTokenizer.from_pretrained(self.model_name)
        common = dict(
            device_map="auto" if has_cuda else None,
            low_cpu_mem_usage=True,
        )
        try:  # transformers >= 5 renamed torch_dtype -> dtype
            self._model = AutoModelForCausalLM.from_pretrained(
                self.model_name, dtype=load_dtype, **common)
        except TypeError:
            self._model = AutoModelForCausalLM.from_pretrained(
                self.model_name, torch_dtype=load_dtype, **common)
        if not has_cuda:
            self._model.to("cpu")
        self._model.eval()

        if want_int8 and not has_cuda:
            try:
                log.info("Applying int8 dynamic quantization to Linear layers "
                         "(this takes ~1 min but roughly halves generation time) ...")
                self._model = torch.ao.quantization.quantize_dynamic(
                    self._model, {torch.nn.Linear}, dtype=torch.qint8)
                log.info("int8 quantization done.")
            except Exception as e:  # pragma: no cover
                log.warning("int8 quantization failed (%s); using bfloat16 weights.", e)

        log.info("LLM ready.")

    def unload(self) -> None:
        self._model = None
        self._tok = None
        try:
            import gc
            import torch
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass

    # -- generation ---------------------------------------------------------
    def generate(self, system: str, user: str,
                 max_new_tokens: Optional[int] = None) -> str:
        self.load()
        import torch

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        try:
            prompt = self._tok.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True,
                enable_thinking=self.enable_thinking)
        except TypeError:  # tokenizer without the Qwen3 kwarg
            prompt = self._tok.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True)

        inputs = self._tok(prompt, return_tensors="pt").to(self._model.device)
        gen_kwargs = dict(
            max_new_tokens=max_new_tokens or self.max_new_tokens,
            do_sample=False,
            repetition_penalty=1.05,
            pad_token_id=self._tok.pad_token_id or self._tok.eos_token_id,
        )
        with torch.inference_mode():
            out = self._model.generate(**inputs, **gen_kwargs)
        text = self._tok.decode(out[0][inputs["input_ids"].shape[1]:],
                                skip_special_tokens=True)
        return _strip_think(text).strip()

    def generate_json(self, system: str, user: str,
                      max_new_tokens: Optional[int] = None,
                      retries: int = 1) -> Dict[str, Any]:
        raw = self.generate(system, user, max_new_tokens)
        obj = _parse_json(raw)
        attempt = 0
        while obj is None and attempt < retries:
            attempt += 1
            log.warning("LLM JSON parse failed (attempt %d); asking for a clean retry.",
                        attempt)
            fix = (user + "\n\nYour previous answer was not valid JSON. "
                   "Reply with ONE valid JSON object only, no prose, no markdown fences.")
            raw = self.generate(system, fix, max_new_tokens)
            obj = _parse_json(raw)
        if obj is None:
            log.error("LLM did not return parseable JSON. First 400 chars: %s",
                      raw[:400])
            return {}
        return obj


# --------------------------------------------------------------------------- #
def _strip_think(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)


def _parse_json(text: str) -> Optional[Dict[str, Any]]:
    if not text:
        return None
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
    candidates = []
    if m:
        candidates.append(m.group(1))
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start:end + 1])
    for cand in candidates:
        for attempt in (cand, _loose_fix(cand)):
            try:
                val = json.loads(attempt)
                if isinstance(val, dict):
                    return val
            except Exception:
                continue
    return None


def _loose_fix(s: str) -> str:
    s = re.sub(r",\s*([}\]])", r"\1", s)          # trailing commas
    s = s.replace("“", '"').replace("”", '"').replace("’", "'")
    s = re.sub(r"//[^\n\"]*", "", s)              # line comments
    return s
