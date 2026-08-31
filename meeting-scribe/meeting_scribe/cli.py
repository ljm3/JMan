"""Headless runner: same pipeline as the GUI, no Qt required.

    python -m meeting_scribe --cli --source PATH_OR_URL \
        --docs transcript_named,minutes_with_synopsis,action_items \
        --formats docx,pdf --audio-access link --speakers 3
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List

from .config import Config
from .documents.model import (AudioAccess, DocType, OutputFormat, WizardAnswers)
from .logging_setup import init_session, get_logger, log_wizard_answers
from . import paths

_DOC_ALIASES = {
    "digital": DocType.DIGITAL_TRANSCRIPT,
    "interactive": DocType.DIGITAL_TRANSCRIPT,
    "digital_transcript": DocType.DIGITAL_TRANSCRIPT,
    "transcript_named": DocType.TRANSCRIPT_NAMED,
    "named": DocType.TRANSCRIPT_NAMED,
    "transcript_generic": DocType.TRANSCRIPT_GENERIC,
    "generic": DocType.TRANSCRIPT_GENERIC,
    "transcript": DocType.TRANSCRIPT_GENERIC,
    "minutes": DocType.MINUTES_WITH_SYNOPSIS,
    "minutes_with_synopsis": DocType.MINUTES_WITH_SYNOPSIS,
    "minutes_synopsis": DocType.MINUTES_WITH_SYNOPSIS,
    "minutes_no_synopsis": DocType.MINUTES_NO_SYNOPSIS,
    "minutes_plain": DocType.MINUTES_NO_SYNOPSIS,
    "action_items": DocType.ACTION_ITEMS,
    "actions": DocType.ACTION_ITEMS,
}


def _parse_docs(text: str) -> List[DocType]:
    out: List[DocType] = []
    for tok in text.split(","):
        tok = tok.strip().lower()
        if not tok:
            continue
        if tok not in _DOC_ALIASES:
            raise SystemExit(f"Unknown document type: {tok!r}. "
                             f"Choices: {sorted(set(_DOC_ALIASES))}")
        dt = _DOC_ALIASES[tok]
        if dt not in out:
            out.append(dt)
    return out


def _parse_formats(text: str) -> List[OutputFormat]:
    out = []
    for tok in text.split(","):
        tok = tok.strip().lower()
        if not tok:
            continue
        out.append(OutputFormat(tok))
    return out or [OutputFormat.DOCX]


def _progress_printer():
    last = [-1.0]

    def rep(frac: float, stage: str, msg: str):
        pct = int(frac * 100)
        if pct != int(last[0] * 100) or msg:
            bar = "#" * (pct // 3) + "-" * (33 - pct // 3)
            line = f"\r[{bar}] {pct:3d}%  {stage:<20}"
            sys.stdout.write(line + (f"  {msg}" if msg else "") + " " * 6)
            sys.stdout.flush()
            if msg:
                sys.stdout.write("\n")
        last[0] = frac
    return rep


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="meeting-scribe --cli",
                                 description="Local meeting transcription & minutes.")
    ap.add_argument("--source", required=True, help="local path or URL to audio/video")
    ap.add_argument("--docs", required=True,
                    help="comma list: digital,transcript_named,transcript_generic,"
                         "minutes_with_synopsis,minutes_no_synopsis,action_items")
    ap.add_argument("--formats", default="docx", help="comma list: docx,odt,pdf")
    ap.add_argument("--audio-access", default="none",
                    choices=["none", "attach", "link"])
    ap.add_argument("--speakers", type=int, default=0,
                    help="known speaker count (0 = unknown)")
    ap.add_argument("--title", default="")
    ap.add_argument("--date", default="")
    ap.add_argument("--hf-token", default="", help="override Hugging Face token for pyannote")
    args = ap.parse_args(argv)

    cfg = Config.load()
    if args.hf_token:
        cfg.set("diarization", "hf_token", args.hf_token)
    paths.apply_model_cache_env()

    session = init_session()
    log = get_logger("cli")

    is_url = args.source.strip().lower().startswith(("http://", "https://"))
    answers = WizardAnswers(
        source=args.source.strip(),
        source_is_url=is_url,
        doc_types=_parse_docs(args.docs),
        audio_access=AudioAccess(args.audio_access),
        formats=_parse_formats(args.formats),
        known_speaker_count=max(0, args.speakers),
        meeting_title=args.title,
        meeting_date=args.date,
    )
    log_wizard_answers(answers.as_log_dict())

    from .pipeline.runner import run, Cancelled
    try:
        project = run(answers, cfg, report=_progress_printer())
    except Cancelled:
        log.warning("Cancelled.")
        return 130
    except Exception as e:
        log.exception("Pipeline failed: %s", e)
        print(f"\nERROR: {e}")
        return 1

    print("\n\nDeliverables written to:", project.output_dir)
    for d in project.deliverables:
        print("  -", Path(d).name)
    r = project.qc
    print(f"\nAccuracy checks: {len(r.errors)} error(s), {len(r.warnings)} warning(s).")
    if r.errors:
        for i in r.errors:
            print(f"  [ERROR] {i.detail}")
    print(f"\nSession log: {session.path}")
    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
