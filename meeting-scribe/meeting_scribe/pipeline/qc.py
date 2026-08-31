"""Accuracy checks run before any deliverable is written (requirement 6).

Two layers:

1. Deterministic application rules - transcription confidence, diarization
   status, unnamed speakers, action items missing owners / due dates / sources,
   empty minutes sections, speaker-count mismatch.
2. An optional Qwen3 reviewer pass that compares the draft minutes / action
   items against the transcript for unsupported claims and ambiguities.

Everything is reported as :class:`QCIssue`; nothing is silently mutated.
"""
from __future__ import annotations

import json
from typing import Callable, List, Optional

from ..config import Config
from ..documents.model import (ActionItem, Minutes, Project, QCReport, Severity,
                               Transcript, WizardAnswers)
from ..llm import prompts
from ..logging_setup import get_logger

log = get_logger("qc")
Progress = Callable[[float, str], None]

_VAGUE_OWNERS = {"team", "the team", "everyone", "all", "we", "us", "someone",
                 "somebody", "group", "staff", "they"}


def run_checks(project: Project, cfg: Config, *,
               progress: Optional[Progress] = None) -> QCReport:
    p = progress or (lambda f, m: None)
    r = QCReport()
    t = project.transcript
    a = project.answers

    p(0.05, "QC: checking transcription confidence ...")
    _check_transcript(r, t, cfg)

    p(0.25, "QC: checking speaker attribution ...")
    _check_speakers(r, t, a)

    if a.wants_minutes() and project.minutes is not None:
        p(0.4, "QC: checking minutes completeness ...")
        _check_minutes(r, project.minutes, a)

    if a.wants_action_items():
        p(0.55, "QC: checking action items ...")
        _check_action_items(r, project.action_items)

    if cfg.get("qc", "llm_reviewer", True) and (a.wants_minutes() or a.wants_action_items()):
        p(0.7, "QC: running LLM reviewer pass ...")
        try:
            _llm_review(r, project, cfg)
        except Exception as e:  # pragma: no cover
            log.warning("LLM reviewer pass failed: %s", e)
            r.add(Severity.INFO, "reviewer", f"LLM reviewer pass could not run: {e}")

    p(1.0, f"QC complete: {len(r.errors)} error(s), {len(r.warnings)} warning(s).")
    log.info("QC: %d errors, %d warnings, %d info.",
             len(r.errors), len(r.warnings),
             len([i for i in r.issues if i.severity == Severity.INFO]))
    return r


# --------------------------------------------------------------------------- #
def _check_transcript(r: QCReport, t: Transcript, cfg: Config) -> None:
    r.checks_run.append("transcription-coverage")
    r.checks_run.append("transcription-confidence")

    text = t.full_text()
    if not text.strip():
        r.add(Severity.ERROR, "transcription",
              "The transcript is empty - no speech was recognised.",
              "Check the audio file has audible speech and try a larger Whisper model.")
        return
    if t.duration > 30 and len(text) < 0.5 * t.duration:
        r.add(Severity.WARNING, "transcription",
              f"Transcript looks sparse ({len(text)} chars for {t.duration:.0f}s of audio).",
              "Audio may be noisy or partly silent; spot-check the recording.")

    min_lp = float(cfg.get("qc", "min_avg_logprob", -1.0))
    max_cr = float(cfg.get("qc", "max_compression_ratio", 2.4))
    max_ns = float(cfg.get("qc", "max_no_speech_prob", 0.6))
    weak = []
    for s in t.segments:
        bad = []
        if s.avg_logprob is not None and s.avg_logprob < min_lp:
            bad.append(f"avg_logprob={s.avg_logprob:.2f}")
        if s.compression_ratio is not None and s.compression_ratio > max_cr:
            bad.append(f"compression={s.compression_ratio:.2f}")
        if s.no_speech_prob is not None and s.no_speech_prob > max_ns:
            bad.append(f"no_speech={s.no_speech_prob:.2f}")
        if bad:
            weak.append((s.start, s.text[:60], ", ".join(bad)))
    if weak:
        sample = "; ".join(f"[{_ts(st)}] “{txt}...” ({why})"
                           for st, txt, why in weak[:5])
        sev = Severity.WARNING if len(weak) <= max(3, len(t.segments) // 10) else Severity.ERROR
        r.add(sev, "transcription",
              f"{len(weak)} low-confidence segment(s) detected. Examples: {sample}",
              "Review these passages against the audio before distributing.")


def _check_speakers(r: QCReport, t: Transcript, a: WizardAnswers) -> None:
    r.checks_run.append("diarization-status")
    if a.wants_diarization() and not t.diarized:
        reason = getattr(t, "_diarization_skip_reason", "diarization unavailable")
        r.add(Severity.WARNING, "diarization",
              f"Speakers were not separated ({reason}); everything is attributed "
              f"to a single speaker.",
              "Add a Hugging Face token in Settings and accept the pyannote model "
              "conditions to enable speaker separation.")
        return

    if t.diarized:
        r.checks_run.append("speaker-count")
        n = len(t.speakers)
        if a.known_speaker_count and n != a.known_speaker_count:
            r.add(Severity.WARNING, "diarization",
                  f"Diarization found {n} speaker(s) but you expected "
                  f"{a.known_speaker_count}.",
                  "Merge or rename speakers in the review step, or set the "
                  "speaker count and re-run.")

    if a.wants_named_speakers():
        r.checks_run.append("speaker-names")
        for s in t.speakers:
            if not s.name.strip():
                r.add(Severity.WARNING, "speaker-names",
                      f"{s.label} has no confirmed name but a named-attribution "
                      f"transcript was requested.",
                      "Assign a name to every speaker in the review step.")
            elif not s.confirmed:
                r.add(Severity.INFO, "speaker-names",
                      f"{s.label} -> “{s.name}” is not user-confirmed.",
                      "Confirm speaker names in the review step.")


def _check_minutes(r: QCReport, m: Minutes, a: WizardAnswers) -> None:
    r.checks_run.append("minutes-completeness")
    if not m.attendees:
        r.add(Severity.WARNING, "minutes", "Minutes list no attendees.",
              "Confirm participants in the review step.")
    if not m.discussion or all(not d.points for d in m.discussion):
        r.add(Severity.WARNING, "minutes", "Minutes contain no discussion points.",
              "The transcript may be too short or the model under-produced; review.")
    if not m.meeting_date:
        r.add(Severity.INFO, "minutes", "No meeting date is recorded in the minutes.",
              "Set the meeting date in the wizard or review step.")
    if a.wants_synopsis() and not m.synopsis.strip():
        r.add(Severity.WARNING, "minutes",
              "A synopsis was requested but the model produced none.",
              "Re-run minutes generation or write the synopsis manually.")
    if not a.wants_synopsis() and m.synopsis.strip():
        r.add(Severity.INFO, "minutes",
              "A synopsis was produced although 'minutes without synopsis' was chosen; "
              "it will be omitted from the export.")


def _check_action_items(r: QCReport, items: List[ActionItem]) -> None:
    r.checks_run.append("action-item-owners")
    r.checks_run.append("action-item-due-dates")
    r.checks_run.append("action-item-sources")
    if not items:
        r.add(Severity.INFO, "action-items",
              "No action items were extracted.",
              "Confirm the meeting genuinely produced no assigned tasks.")
        return
    for idx, it in enumerate(items, 1):
        loc = f"action item {idx}: “{it.task[:70]}”"
        if not it.owner:
            r.add(Severity.ERROR, "action-items",
                  f"{loc} has no owner.", "Assign a responsible person.", loc)
        elif it.owner.strip().lower() in _VAGUE_OWNERS:
            r.add(Severity.WARNING, "action-items",
                  f"{loc} has a vague owner (“{it.owner}”).",
                  "Name a specific person.", loc)
        if not it.due:
            r.add(Severity.WARNING, "action-items",
                  f"{loc} has no due date.", "Add a deadline if one was agreed.", loc)
        if not it.source_quote and it.source_time is None:
            r.add(Severity.WARNING, "action-items",
                  f"{loc} has no supporting quote or timestamp.",
                  "Verify this item against the recording.", loc)
        if it.confidence < 0.5:
            r.add(Severity.WARNING, "action-items",
                  f"{loc} was extracted with low confidence ({it.confidence:.2f}).",
                  "Confirm this was a real commitment.", loc)


def _llm_review(r: QCReport, project: Project, cfg: Config) -> None:
    from ..llm.qwen import get_llm

    r.checks_run.append("llm-reviewer-pass")
    draft = {
        "minutes": project.minutes.to_dict() if project.minutes else None,
        "action_items": [i.to_dict() for i in project.action_items],
    }
    transcript = project.transcript.dialogue_text(named=True)
    limit = int(cfg.get("llm", "map_reduce_char_limit", 40000))
    if len(transcript) > limit:
        transcript = transcript[:limit] + "\n...[transcript truncated for review]..."

    llm = get_llm(cfg)
    data = llm.generate_json(
        prompts.QC_SYSTEM,
        prompts.QC_USER.format(draft=json.dumps(draft, ensure_ascii=False, indent=2),
                               transcript=transcript),
        max_new_tokens=1800,
    )
    issues = data.get("issues", []) if isinstance(data, dict) else []
    if not issues:
        r.add(Severity.INFO, "llm-reviewer",
              "LLM reviewer found no additional issues.")
        return
    sev_map = {"error": Severity.ERROR, "warning": Severity.WARNING,
               "info": Severity.INFO}
    for iss in issues[:40]:
        if not isinstance(iss, dict):
            continue
        r.add(sev_map.get(str(iss.get("severity", "warning")).lower(), Severity.WARNING),
              f"reviewer:{iss.get('category', 'note')}",
              str(iss.get("detail", "")).strip(),
              str(iss.get("suggestion", "")).strip())


def _ts(sec: float) -> str:
    sec = max(0, int(sec))
    m, s = divmod(sec, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
