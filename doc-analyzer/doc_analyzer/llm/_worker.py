"""Persistent local-LLM worker.

Runs under whichever Python the bridge resolved to (this app's own .venv when it
was set up with ``setup.ps1 -LocalModel``, otherwise a meeting-scribe checkout) -
that interpreter must have torch + transformers; the Qwen3 weights are pulled
from / cached in the usual Hugging Face cache.
It speaks newline-delimited JSON on stdin/stdout:

  <- {"model": "...", "dtype": "int8", "max_new_tokens": 2048}   (first line = config)
  -> {"ready": true}                          or {"ready": false, "error": "..."}
  <- {"id": 1, "system": "...", "user": "...", "max_new_tokens": 1024}
  -> {"id": 1, "ok": true, "text": "..."}     or {"id": 1, "ok": false, "error": "..."}

Generation mirrors meeting-scribe's llm/qwen.py: chat template, greedy decoding,
repetition_penalty 1.05, <think> stripped.
"""
from __future__ import annotations

import json
import re
import sys
import traceback

_STATE: dict = {}


def _log(msg: str) -> None:
    print(f"[llm-worker] {msg}", file=sys.stderr, flush=True)


def _load(cfg: dict) -> None:
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    model_name = cfg.get("model", "Qwen/Qwen3-4B")
    dtype = cfg.get("dtype", "int8")
    _log(f"loading {model_name} (dtype={dtype}); first run downloads several GB ...")

    dtype_map = {"float32": torch.float32, "float16": torch.float16,
                 "bfloat16": torch.bfloat16, "auto": "auto"}
    has_cuda = torch.cuda.is_available()
    want_int8 = dtype == "int8"
    load_dtype = torch.float32 if (want_int8 and not has_cuda) else dtype_map.get(dtype, torch.float32)

    tok = AutoTokenizer.from_pretrained(model_name)
    common = dict(device_map="auto" if has_cuda else None, low_cpu_mem_usage=True)
    try:
        model = AutoModelForCausalLM.from_pretrained(model_name, dtype=load_dtype, **common)
    except TypeError:  # older transformers: torch_dtype
        model = AutoModelForCausalLM.from_pretrained(model_name, torch_dtype=load_dtype, **common)
    if not has_cuda:
        model.to("cpu")
    model.eval()

    if want_int8 and not has_cuda:
        try:
            _log("applying int8 dynamic quantization (~1 min, ~2x faster generation) ...")
            model = torch.ao.quantization.quantize_dynamic(model, {torch.nn.Linear}, dtype=torch.qint8)
        except Exception as exc:  # pragma: no cover
            _log(f"int8 quantization failed ({exc}); continuing with full-precision weights.")

    _STATE.update(torch=torch, tok=tok, model=model,
                  max_new_tokens=int(cfg.get("max_new_tokens", 2048)),
                  enable_thinking=bool(cfg.get("enable_thinking", False)))
    _log("model ready.")


def _generate(system: str, user: str, max_new_tokens: int | None) -> str:
    torch = _STATE["torch"]
    tok = _STATE["tok"]
    model = _STATE["model"]
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    try:
        prompt = tok.apply_chat_template(messages, tokenize=False, add_generation_prompt=True,
                                         enable_thinking=_STATE["enable_thinking"])
    except TypeError:
        prompt = tok.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)

    inputs = tok(prompt, return_tensors="pt").to(model.device)
    gen_kwargs = dict(
        max_new_tokens=max_new_tokens or _STATE["max_new_tokens"],
        do_sample=False, repetition_penalty=1.05,
        pad_token_id=tok.pad_token_id or tok.eos_token_id,
    )
    with torch.inference_mode():
        out = model.generate(**inputs, **gen_kwargs)
    text = tok.decode(out[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True)
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def main() -> int:
    first = sys.stdin.readline()
    if not first:
        return 0
    try:
        _load(json.loads(first))
        print(json.dumps({"ready": True}), flush=True)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ready": False, "error": f"{type(exc).__name__}: {exc}",
                          "trace": traceback.format_exc()[-1500:]}), flush=True)
        return 1

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            continue
        if req.get("cmd") == "shutdown":
            break
        rid = req.get("id")
        try:
            text = _generate(req.get("system", ""), req.get("user", ""),
                             req.get("max_new_tokens"))
            print(json.dumps({"id": rid, "ok": True, "text": text}), flush=True)
        except Exception as exc:  # noqa: BLE001
            print(json.dumps({"id": rid, "ok": False,
                              "error": f"{type(exc).__name__}: {exc}"}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
