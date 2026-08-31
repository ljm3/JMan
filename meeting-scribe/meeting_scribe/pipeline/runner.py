"""End-to-end orchestration: source -> transcript -> align -> diarize ->
minutes / action items -> QC -> deliverables.

Used by both the GUI worker thread and the headless CLI. Progress is reported
through a single callback ``report(overall_fraction, stage, message)`` and every
step is logged (requirement 9: "work should be shown").
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .. import sources
from ..audio import load_audio, probe_duration
from ..config import Config
from ..documents.model import Project, WizardAnswers
from ..documents.render import render_all
from ..logging_setup import get_logger, current_session
from .. import paths
from . import align as align_mod
from . import diarize as diarize_mod
from . import qc as qc_mod
from . import summarize as summarize_mod
from . import transcribe as transcribe_mod

log = get_logger("runner")

Report = Callable[[float, str, str], None]
CancelFn = Callable[[], bool]


class Cancelled(Exception):
    pass


@dataclass
class _Stage:
    name: str
    weight: float


def run(answers: WizardAnswers, cfg: Config, *,
        report: Optional[Report] = None,
        should_cancel: Optional[CancelFn] = None) -> Project:
    rep = report or (lambda f, s, m: None)
    cancelled = should_cancel or (lambda: False)

    def guard():
        if cancelled():
            raise Cancelled()

    # ---- plan the stages & weights ------------------------------------
    stages = [_Stage("Fetch source", 2), _Stage("Load audio", 3),
              _Stage("Transcribe", 34)]
    do_align = cfg.get("alignment", "enabled", True)
    if do_align:
        stages.append(_Stage("Align words", 12))
    do_diar = answers.wants_diarization()
    if do_diar:
        stages.append(_Stage("Diarize speakers", 18))
    if answers.wants_minutes():
        stages.append(_Stage("Draft minutes", 12))
    if answers.wants_action_items():
        stages.append(_Stage("Extract action items", 8))
    stages.append(_Stage("Accuracy checks", 6))
    stages.append(_Stage("Write deliverables", 8))

    total_w = sum(s.weight for s in stages)
    bases = {}
    acc = 0.0
    for s in stages:
        bases[s.name] = acc / total_w
        acc += s.weight
    span = {s.name: s.weight / total_w for s in stages}

    def stage_report(name: str):
        b, w = bases[name], span[name]
        def _p(frac: float, msg: str):
            rep(min(1.0, b + w * max(0.0, min(1.0, frac))), name, msg)
            if msg:
                log.info("[%s] %s", name, msg)
        return _p

    t0 = time.time()
    sess = current_session()
    project = Project(
        session_label=sess.label if sess else "",
        answers=answers,
    )

    log.info("Pipeline start. Stages: %s", ", ".join(s.name for s in stages))

    # ---- 1. source --------------------------------------------------------
    guard()
    sp = stage_report("Fetch source")
    sp(0.1, f"Resolving source: {answers.source}")
    resolved = sources.resolve(answers.source, progress=lambda m: sp(0.5, m))
    project.origin = resolved.origin
    project.media_path = str(resolved.media_path)
    project.output_dir = str(resolved.output_dir)
    project.media_downloaded_copy = resolved.downloaded_copy
    sp(1.0, f"Media ready: {resolved.media_path}  (output folder: {resolved.output_dir})")

    # ---- 2. load audio -------------------------------------------------
    guard()
    lp = stage_report("Load audio")
    lp(0.2, "Decoding audio to 16 kHz mono ...")
    wf = load_audio(resolved.media_path)
    if wf.duration < 0.5:
        raise RuntimeError("The audio is shorter than half a second - nothing to do.")
    lp(1.0, f"Loaded {wf.duration:.1f}s of audio.")

    # ---- 3. transcribe --------------------------------------------------
    guard()
    tp = stage_report("Transcribe")
    project.transcript = transcribe_mod.transcribe(wf, cfg, progress=tp)
    project.transcript.duration = wf.duration
    _checkpoint(project)

    # ---- 4. align -----------------------------------------------------
    if do_align:
        guard()
        ap = stage_report("Align words")
        project.transcript = align_mod.align(project.transcript, wf, cfg, progress=ap)
        _checkpoint(project)

    # ---- 5. diarize ---------------------------------------------------
    if do_diar:
        guard()
        dp = stage_report("Diarize speakers")
        project.transcript = diarize_mod.diarize(
            project.transcript, wf, cfg,
            known_speakers=answers.known_speaker_count, progress=dp)
    else:
        # still need a speaker object for rendering
        diarize_mod._single_speaker(project.transcript, reason="not requested")
    _checkpoint(project)

    has_speech = bool(project.transcript.full_text().strip())
    if not has_speech and answers.wants_llm():
        log.warning("Transcript is empty - skipping minutes / action-item "
                    "generation; the accuracy report will flag this.")

    # ---- 6. minutes -------------------------------------------------
    if answers.wants_minutes() and has_speech:
        guard()
        mp = stage_report("Draft minutes")
        project.minutes = summarize_mod.generate_minutes(
            project.transcript, cfg,
            title_hint=answers.meeting_title,
            date_hint=answers.meeting_date,
            with_synopsis=answers.wants_synopsis(),
            progress=mp)
        _checkpoint(project)

    # ---- 7. action items ---------------------------------------------
    if answers.wants_action_items() and has_speech:
        guard()
        aip = stage_report("Extract action items")
        project.action_items = summarize_mod.generate_action_items(
            project.transcript, cfg, progress=aip)
        _checkpoint(project)

    # ---- 8. QC ------------------------------------------------------
    guard()
    qp = stage_report("Accuracy checks")
    project.qc = qc_mod.run_checks(project, cfg, progress=qp)
    _checkpoint(project)

    # ---- 9. render ------------------------------------------------
    guard()
    rp = stage_report("Write deliverables")
    render_all(project, progress=rp)
    _checkpoint(project)

    dt = time.time() - t0
    log.info("Pipeline finished in %.1fs. %d deliverable(s).",
             dt, len(project.deliverables))
    rep(1.0, "Done", f"Completed in {dt:.0f}s")
    return project


def _checkpoint(project: Project) -> None:
    try:
        base = Path(project.media_path).stem or "project"
        name = f"{project.session_label or 'session'}_{base}.mscribe.json"
        project.save(paths.projects_dir() / name)
    except Exception as e:  # pragma: no cover
        log.debug("checkpoint save failed: %s", e)
