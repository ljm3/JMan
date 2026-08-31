"""Entry point.

    python -m meeting_scribe                     # launch the desktop app
    python -m meeting_scribe --cli ...           # headless run (see cli.py)
    python -m meeting_scribe --doctor            # environment diagnostics
    python -m meeting_scribe --configure-defaults# hardware auto-tune the config
"""
from __future__ import annotations

import sys
from typing import List


def _doctor() -> int:
    from .config import Config
    from . import paths

    cfg = Config.load()
    paths.apply_model_cache_env()
    print("Meeting Scribe - environment check\n" + "=" * 40)
    print(f"Python           : {sys.version.split()[0]}")
    print(f"Workspace        : {paths.workspace()}")
    print(f"Config file      : {paths.config_file()}")
    print(f"Model cache (HF)  : {paths.models_dir()}")

    import importlib
    import importlib.metadata as md

    def check(label: str, module: str, dist: str | None = None):
        try:
            importlib.import_module(module)
        except Exception as e:
            print(f"{label:<17}: MISSING / ERROR - {e}")
            return
        ver = "ok"
        try:
            ver = md.version(dist or module)
        except Exception:
            pass
        print(f"{label:<17}: {ver}")

    check("PySide6", "PySide6")
    check("PyAV (av)", "av")
    check("numpy", "numpy")
    check("faster-whisper", "faster_whisper", "faster-whisper")
    check("torch", "torch")
    check("torchaudio", "torchaudio")
    check("transformers", "transformers")
    check("pyannote.audio", "pyannote.audio", "pyannote.audio")
    check("python-docx", "docx", "python-docx")
    check("odfpy", "odf", "odfpy")
    check("reportlab", "reportlab")
    check("yt-dlp", "yt_dlp", "yt-dlp")
    check("pkg_resources", "pkg_resources", "setuptools")

    try:
        import torch
        print(f"CUDA available    : {torch.cuda.is_available()}")
    except Exception:
        print("CUDA available    : (torch not importable)")

    tok = (cfg.get("diarization", "hf_token") or "").strip()
    print(f"HF token set      : {'yes' if tok else 'NO  (speaker separation will be disabled)'}")
    print(f"LLM model         : {cfg.get('llm', 'model')}  (dtype={cfg.get('llm', 'dtype')})")
    print(f"Whisper model     : {cfg.get('transcription', 'model')}")
    print("\nIf any heavy component says MISSING, run setup.ps1 again.")
    return 0


def _configure_defaults() -> int:
    from .config import Config, autotune_for_hardware
    cfg = Config.load()
    autotune_for_hardware(cfg)
    cfg.save()
    print("Wrote hardware-tuned defaults to", cfg.path)
    print(f"  Whisper model : {cfg.get('transcription', 'model')}")
    print(f"  LLM model     : {cfg.get('llm', 'model')} (dtype={cfg.get('llm', 'dtype')})")
    return 0


def main(argv: List[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    if "--doctor" in argv:
        return _doctor()
    if "--configure-defaults" in argv:
        return _configure_defaults()
    if "--cli" in argv:
        argv.remove("--cli")
        from .cli import main as cli_main
        return cli_main(argv)

    from .gui.app import launch
    return launch(argv)


if __name__ == "__main__":
    raise SystemExit(main())
