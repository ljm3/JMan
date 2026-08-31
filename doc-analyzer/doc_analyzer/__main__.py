"""Entry point.

  (no args)                 launch the desktop GUI
  --cli --source <loc>      run one analysis headless, verbose to stdout
       [--kind local|git|http|sharepoint|s3|azure_blob]
       [--branch B] [--claude]
       [--objective "what you want to ascertain"]
       [--format word|spreadsheet|presentation]
       [--format-synopsis/-activity/-tools ...] [--no-source-copy]
  --doctor                  print environment / dependency / binary report
  --selftest                analyze the bundled sample_docs and verify artifacts
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import APP_NAME, __version__


def _print_sink(level: str, msg: str) -> None:
    print(f"  {level:<5} {msg}", flush=True)


def _run_cli(args) -> int:
    from .config import Config
    from .session import Session
    from .analyze import analyze, RunOptions

    cfg = Config.load()
    if args.claude:
        cfg.set("claude.enabled", True)
    if args.llm is not None:
        cfg.set("llm.enabled", bool(args.llm))
        if args.llm and not cfg.llm_steps:
            cfg.set("llm.steps", ["plan"])
    if args.llm_steps is not None:
        cfg.set("llm.enabled", bool(args.llm_steps))
        cfg.set("llm.steps", [s.strip() for s in args.llm_steps.split(",") if s.strip()])

    formats = {}
    for name in ("synopsis", "activity", "tools", "objective"):
        chosen = getattr(args, f"fmt_{name}", None) or args.fmt_all
        if chosen:
            formats[name] = chosen
    run_opts = RunOptions(objective=args.objective or "", formats=formats,
                          write_to_source=args.write_to_source)

    sess = Session(sink=_print_sink)
    print(f"{APP_NAME} {__version__}  |  session {sess.id}")
    print(f"Session folder: {sess.dir}\n")
    try:
        spec = {"kind": args.kind, "location": args.source,
                "options": {"branch": args.branch} if args.branch else {}}
        res = analyze(sess, cfg, spec, run_opts)
    except Exception as exc:  # noqa: BLE001
        sess.warn(f"FAILED: {type(exc).__name__}: {exc}")
        sess.close()
        print(f"\nFAILED: {exc}", file=sys.stderr)
        return 2
    sess.close()
    print("\n--- artifacts ---")
    for k, v in res.artifacts.items():
        print(f"  {k:28} {v}")
    print(f"\n{res.files_analyzed} analyzed, {res.files_skipped} skipped, "
          f"{res.clusters} multi-doc clusters, {res.activities} activity categories.")
    print(f"Open: {sess.dir}")
    if res.deliver_dir:
        print(f"Results also written to: {res.deliver_dir}")
    return 0


def _doctor() -> int:
    import platform
    import shutil
    from .config import Config
    from .extract import SUPPORTED_EXTS

    print(f"{APP_NAME} {__version__}")
    print(f"Python      : {platform.python_version()}  ({sys.executable})")
    print(f"Platform    : {platform.platform()}")
    cfg = Config.load()
    print(f"Config      : {cfg.path or '(defaults only - no config.toml)'}")
    print(f"Sessions    : {__import__('doc_analyzer.paths', fromlist=['sessions_dir']).sessions_dir()}")
    print(f"Extensions  : {len(SUPPORTED_EXTS)} supported")
    print("\nOptional Python packages:")
    for mod in ("docx", "openpyxl", "pptx", "pypdf", "pymupdf", "striprtf", "yaml",
                "requests", "extract_msg", "PIL", "pytesseract", "pygments",
                "boto3", "azure.storage.blob", "anthropic", "tomli_w",
                "torch", "transformers"):
        try:
            m = __import__(mod)
            ver = getattr(m, "__version__", "?")
            print(f"  [ok]   {mod:22} {ver}")
        except Exception as exc:  # noqa: BLE001
            print(f"  [ --]  {mod:22} not installed ({exc.__class__.__name__})")
    print("\nExternal binaries:")
    for exe in ("git", "tesseract"):
        print(f"  {'[ok]  ' if shutil.which(exe) else '[ --] '} {exe:12} "
              f"{shutil.which(exe) or 'not on PATH'}")

    print("\nLocal model (objective planner):")
    try:
        from .llm import LocalLLM
        ok, reason = LocalLLM(cfg).preflight()
        print(f"  {'[ok]  ' if ok else '[ --] '} {reason}")
    except Exception as exc:  # noqa: BLE001
        print(f"  [ --]  bridge error: {exc}")
    return 0


def _selftest() -> int:
    from .config import Config
    from .session import Session
    from .analyze import analyze, RunOptions

    samples = Path(__file__).resolve().parent.parent / "sample_docs"
    if not samples.is_dir():
        print(f"selftest: sample_docs not found at {samples}", file=sys.stderr)
        return 1
    os.environ["DOCAN_LLM_ENABLED"] = "false"   # smoke test: never load the local model
    cfg = Config.load()
    sess = Session(sink=_print_sink)
    run_opts = RunOptions(
        objective="Identify the main business activities and financial obligations across the sample corpus.",
        formats={"synopsis": "word", "activity": "spreadsheet", "tools": "presentation",
                 "objective": "spreadsheet"},
        write_to_source=False,
    )
    res = analyze(sess, cfg, {"kind": "local", "location": str(samples), "options": {}}, run_opts)
    sess.close()
    required = ["synopsis.md", "synopsis.json", "activity_breakdown.md",
               "activity_breakdown.json", "manifest.json", "session.log",
               "tools_and_versions.md", "tools_and_versions.json",
               "synopsis.docx", "activity_breakdown.xlsx", "tools_and_versions.pptx",
               "objective_analysis.md", "objective_analysis.json", "objective_analysis.xlsx"]
    missing = [f for f in required if not (sess.dir / f).exists()]
    ok = not missing and res.files_analyzed > 0
    print(f"\nselftest: analyzed={res.files_analyzed} skipped={res.files_skipped} "
          f"clusters={res.clusters} activities={res.activities}")
    print(f"selftest: artifacts {'OK' if not missing else 'MISSING ' + ', '.join(missing)}")
    print(f"selftest: {'PASS' if ok else 'FAIL'}  ({sess.dir})")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="doc-analyzer", description=APP_NAME)
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    ap.add_argument("--cli", action="store_true", help="run one analysis without the GUI")
    ap.add_argument("--source", help="local folder path or online location")
    ap.add_argument("--kind", default="local",
                    choices=["local", "git", "http", "sharepoint", "s3", "azure_blob"])
    ap.add_argument("--branch", help="git branch (git kind only)")
    ap.add_argument("--claude", action="store_true", help="enable the optional Claude synopsis")
    ap.add_argument("--llm", dest="llm", action="store_true", default=None,
                    help="force the local Qwen model on for this run (needs the LLM stack in .venv)")
    ap.add_argument("--no-llm", dest="llm", action="store_false",
                    help="force the local model off (use the heuristic planner)")
    ap.add_argument("--llm-steps", default=None,
                    help="comma list of steps to run through the local model: plan,coaching,narrative")
    ap.add_argument("--objective", "--context", dest="objective", default="",
                    help="what you want to ascertain from this analysis")
    ap.add_argument("--format", dest="fmt_all", default=None,
                    choices=["word", "spreadsheet", "presentation"],
                    help="format for every result document "
                         "(default: config [output] default_format, else word)")
    for _name in ("synopsis", "activity", "tools", "objective"):
        ap.add_argument(f"--format-{_name}", dest=f"fmt_{_name}", default=None,
                        choices=["word", "spreadsheet", "presentation"],
                        help=f"override the output format for the {_name} document "
                             + ("(defaults to the synopsis format)" if _name == "objective" else ""))
    ap.add_argument("--no-source-copy", dest="write_to_source", action="store_false",
                    help="do not also write results into the analyzed folder")
    ap.set_defaults(write_to_source=True)
    ap.add_argument("--doctor", action="store_true", help="print an environment report")
    ap.add_argument("--selftest", action="store_true", help="analyze bundled sample_docs and verify")
    args = ap.parse_args(argv)

    if args.doctor:
        return _doctor()
    if args.selftest:
        return _selftest()
    if args.cli:
        if not args.source:
            ap.error("--cli requires --source")
        return _run_cli(args)

    from .gui.app import launch
    return launch()


if __name__ == "__main__":
    raise SystemExit(main())
