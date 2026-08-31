"""Unit tests that do not need the ML stack or Qt."""
from __future__ import annotations

from meeting_scribe.config import Config
from meeting_scribe.documents.model import (ActionItem, AudioAccess, DocType,
                                            Minutes, OutputFormat, Project,
                                            Segment, Speaker, Transcript,
                                            WizardAnswers, Severity)
from meeting_scribe.pipeline import qc as qc_mod


def _demo_project() -> Project:
    t = Transcript(language="en", duration=120.0, diarized=True, aligned=True)
    t.speakers = [Speaker(id="SPEAKER_00", label="Speaker 1", name="Alice",
                          confirmed=True),
                  Speaker(id="SPEAKER_01", label="Speaker 2")]
    t.segments = [
        Segment(0.0, 5.0, "Let's start the budget review.", speaker="SPEAKER_00",
                avg_logprob=-0.2, compression_ratio=1.4, no_speech_prob=0.01),
        Segment(5.0, 9.0, "asdkjh qweqwe kjh", speaker="SPEAKER_01",
                avg_logprob=-2.5, compression_ratio=3.1, no_speech_prob=0.8),
    ]
    a = WizardAnswers(
        source="x.m4a", doc_types=[DocType.TRANSCRIPT_NAMED,
                                   DocType.MINUTES_WITH_SYNOPSIS,
                                   DocType.ACTION_ITEMS],
        audio_access=AudioAccess.LINK, formats=[OutputFormat.DOCX],
        known_speaker_count=3)
    p = Project(answers=a, transcript=t, origin="x.m4a", media_path="x.m4a")
    p.minutes = Minutes(title="Budget Review", attendees=["Alice", "Speaker 2"],
                        synopsis="", discussion=[])
    p.action_items = [
        ActionItem(task="Send the revised forecast", owner="", due="",
                   source_quote="", confidence=0.9),
        ActionItem(task="Book the room", owner="Bob", due="Friday",
                   source_quote="I'll book the room", source_time=42.0,
                   confidence=0.4),
    ]
    return p


def test_project_json_roundtrip(tmp_path):
    p = _demo_project()
    path = p.save(tmp_path / "proj.mscribe.json")
    q = Project.load(path)
    assert q.minutes.title == "Budget Review"
    assert len(q.transcript.segments) == 2
    assert q.transcript.speakers[0].name == "Alice"
    assert [d for d in q.answers.doc_types] == p.answers.doc_types
    assert q.action_items[0].owner == ""


def test_qc_flags_expected_issues():
    cfg = Config()
    cfg.set("qc", "llm_reviewer", False)
    p = _demo_project()
    report = qc_mod.run_checks(p, cfg)

    cats = [i.category for i in report.issues]
    # missing owner on a committed action item -> error
    assert any(i.severity == Severity.ERROR and i.category == "action-items"
               for i in report.issues)
    # low confidence transcript segment
    assert any(i.category == "transcription" for i in report.issues)
    # speaker 2 has no confirmed name but named transcript requested
    assert any(i.category == "speaker-names" for i in report.issues)
    # empty synopsis requested
    assert any("synopsis" in i.detail.lower() for i in report.issues)
    # known speaker count (3) != detected (2)
    assert any("expected" in i.detail for i in report.issues)
    assert "transcription-confidence" in report.checks_run


def test_wizard_answer_derivations():
    a = WizardAnswers(doc_types=[DocType.TRANSCRIPT_GENERIC])
    assert a.wants_diarization() and not a.wants_named_speakers()
    assert not a.wants_llm()
    a.doc_types.append(DocType.ACTION_ITEMS)
    assert a.wants_llm() and a.wants_action_items()
