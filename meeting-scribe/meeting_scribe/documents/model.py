"""Core data model, shared by the pipeline, the GUI and the exporters.

Everything is a plain dataclass with ``to_dict`` / ``from_dict`` so a whole
project round-trips to a single JSON file in ``<workspace>/projects``.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional


# --------------------------------------------------------------------------- #
#  Wizard choices                                                             #
# --------------------------------------------------------------------------- #
class DocType(str, Enum):
    DIGITAL_TRANSCRIPT = "digital_transcript"       # interactive, highlight while playing
    TRANSCRIPT_NAMED = "transcript_named"           # printed, identified speakers
    TRANSCRIPT_GENERIC = "transcript_generic"       # printed, Speaker 1 / Speaker 2
    MINUTES_WITH_SYNOPSIS = "minutes_with_synopsis"
    MINUTES_NO_SYNOPSIS = "minutes_no_synopsis"
    ACTION_ITEMS = "action_items"

    @property
    def human(self) -> str:
        return {
            "digital_transcript": "Digital transcript (highlight as audio plays)",
            "transcript_named": "Printed transcript - identified speaker attribution",
            "transcript_generic": "Printed transcript - generic speaker attribution",
            "minutes_with_synopsis": "Minutes with synopsis",
            "minutes_no_synopsis": "Minutes without synopsis",
            "action_items": "Action items",
        }[self.value]


class AudioAccess(str, Enum):
    NONE = "none"
    ATTACH = "attach"     # copy the media next to the deliverables
    LINK = "link"         # reference the original path / URL


class OutputFormat(str, Enum):
    DOCX = "docx"
    ODT = "odt"
    PDF = "pdf"


@dataclass
class WizardAnswers:
    source: str = ""
    source_is_url: bool = False
    doc_types: List[DocType] = field(default_factory=list)
    audio_access: AudioAccess = AudioAccess.NONE
    formats: List[OutputFormat] = field(default_factory=lambda: [OutputFormat.DOCX])
    known_speaker_count: int = 0          # 0 = unknown
    meeting_title: str = ""
    meeting_date: str = ""                # free text; blank -> file mtime

    # -- derived helpers ------------------------------------------------------
    def wants_diarization(self) -> bool:
        return any(d in self.doc_types for d in (
            DocType.TRANSCRIPT_NAMED, DocType.TRANSCRIPT_GENERIC,
            DocType.DIGITAL_TRANSCRIPT, DocType.MINUTES_WITH_SYNOPSIS,
            DocType.MINUTES_NO_SYNOPSIS, DocType.ACTION_ITEMS,
        ))

    def wants_named_speakers(self) -> bool:
        return DocType.TRANSCRIPT_NAMED in self.doc_types

    def wants_minutes(self) -> bool:
        return any(d in self.doc_types for d in (
            DocType.MINUTES_WITH_SYNOPSIS, DocType.MINUTES_NO_SYNOPSIS))

    def wants_synopsis(self) -> bool:
        return DocType.MINUTES_WITH_SYNOPSIS in self.doc_types

    def wants_action_items(self) -> bool:
        return DocType.ACTION_ITEMS in self.doc_types

    def wants_llm(self) -> bool:
        return self.wants_minutes() or self.wants_action_items()

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["doc_types"] = [x.value for x in self.doc_types]
        d["audio_access"] = self.audio_access.value
        d["formats"] = [x.value for x in self.formats]
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "WizardAnswers":
        return cls(
            source=d.get("source", ""),
            source_is_url=d.get("source_is_url", False),
            doc_types=[DocType(x) for x in d.get("doc_types", [])],
            audio_access=AudioAccess(d.get("audio_access", "none")),
            formats=[OutputFormat(x) for x in d.get("formats", ["docx"])],
            known_speaker_count=d.get("known_speaker_count", 0),
            meeting_title=d.get("meeting_title", ""),
            meeting_date=d.get("meeting_date", ""),
        )

    def as_log_dict(self) -> Dict[str, Any]:
        return {
            "Audio source": self.source,
            "Source type": "online URL" if self.source_is_url else "local file",
            "Documents requested": [d.human for d in self.doc_types],
            "Source-audio access": self.audio_access.value,
            "Output formats": [f.value for f in self.formats],
            "Known speaker count": self.known_speaker_count or "unknown",
            "Meeting title": self.meeting_title or "(derive)",
            "Meeting date": self.meeting_date or "(derive from file)",
        }


# --------------------------------------------------------------------------- #
#  Transcription result                                                       #
# --------------------------------------------------------------------------- #
@dataclass
class Word:
    start: float
    end: float
    text: str
    score: Optional[float] = None
    speaker: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Word":
        return cls(**d)


@dataclass
class Segment:
    start: float
    end: float
    text: str
    speaker: Optional[str] = None
    words: List[Word] = field(default_factory=list)
    avg_logprob: Optional[float] = None
    compression_ratio: Optional[float] = None
    no_speech_prob: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["words"] = [w.to_dict() for w in self.words]
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Segment":
        d = dict(d)
        d["words"] = [Word.from_dict(w) for w in d.get("words", [])]
        return cls(**d)


@dataclass
class Speaker:
    id: str                     # diarizer id, e.g. "SPEAKER_00"
    label: str                  # generic label, e.g. "Speaker 1"
    name: str = ""              # user-confirmed real name (optional)
    confirmed: bool = False

    @property
    def display(self) -> str:
        return self.name.strip() if self.name.strip() else self.label

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Speaker":
        return cls(**d)


@dataclass
class Transcript:
    language: str = "en"
    duration: float = 0.0
    segments: List[Segment] = field(default_factory=list)
    speakers: List[Speaker] = field(default_factory=list)
    diarized: bool = False
    aligned: bool = False

    # -- helpers ------------------------------------------------------------
    def speaker_by_id(self, sid: Optional[str]) -> Optional[Speaker]:
        if sid is None:
            return None
        for s in self.speakers:
            if s.id == sid:
                return s
        return None

    def display_for(self, sid: Optional[str]) -> str:
        s = self.speaker_by_id(sid)
        return s.display if s else "Speaker"

    def full_text(self) -> str:
        return "\n".join(seg.text.strip() for seg in self.segments if seg.text.strip())

    def dialogue_text(self, named: bool = True) -> str:
        out = []
        last = None
        for seg in self.segments:
            who = self.speaker_by_id(seg.speaker)
            tag = (who.display if named else (who.label if who else "Speaker")) if who else "Speaker"
            if tag != last:
                out.append(f"\n{tag}:")
                last = tag
            out.append(seg.text.strip())
        return " ".join(out).strip()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "language": self.language,
            "duration": self.duration,
            "diarized": self.diarized,
            "aligned": self.aligned,
            "speakers": [s.to_dict() for s in self.speakers],
            "segments": [s.to_dict() for s in self.segments],
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Transcript":
        t = cls(
            language=d.get("language", "en"),
            duration=d.get("duration", 0.0),
            diarized=d.get("diarized", False),
            aligned=d.get("aligned", False),
        )
        t.speakers = [Speaker.from_dict(s) for s in d.get("speakers", [])]
        t.segments = [Segment.from_dict(s) for s in d.get("segments", [])]
        return t


# --------------------------------------------------------------------------- #
#  Analysis products                                                          #
# --------------------------------------------------------------------------- #
@dataclass
class DiscussionTopic:
    topic: str
    points: List[str] = field(default_factory=list)


@dataclass
class Minutes:
    title: str = "Meeting Minutes"
    meeting_date: str = ""
    location: str = ""
    attendees: List[str] = field(default_factory=list)
    agenda: List[str] = field(default_factory=list)
    discussion: List[DiscussionTopic] = field(default_factory=list)
    decisions: List[str] = field(default_factory=list)
    next_steps: List[str] = field(default_factory=list)
    synopsis: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["discussion"] = [asdict(t) for t in self.discussion]
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Minutes":
        d = dict(d)
        d["discussion"] = [DiscussionTopic(**t) for t in d.get("discussion", [])]
        return cls(**d)


@dataclass
class ActionItem:
    task: str
    owner: str = ""
    due: str = ""
    source_time: Optional[float] = None
    source_quote: str = ""
    confidence: float = 1.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ActionItem":
        return cls(**d)


# --------------------------------------------------------------------------- #
#  Quality control                                                            #
# --------------------------------------------------------------------------- #
class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass
class QCIssue:
    severity: Severity
    category: str
    detail: str
    suggestion: str = ""
    location: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["severity"] = self.severity.value
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "QCIssue":
        d = dict(d)
        d["severity"] = Severity(d.get("severity", "warning"))
        return cls(**d)


@dataclass
class QCReport:
    checks_run: List[str] = field(default_factory=list)
    issues: List[QCIssue] = field(default_factory=list)

    @property
    def errors(self) -> List[QCIssue]:
        return [i for i in self.issues if i.severity == Severity.ERROR]

    @property
    def warnings(self) -> List[QCIssue]:
        return [i for i in self.issues if i.severity == Severity.WARNING]

    @property
    def passed(self) -> bool:
        return not self.errors

    def add(self, severity: Severity, category: str, detail: str,
            suggestion: str = "", location: str = "") -> None:
        self.issues.append(QCIssue(severity, category, detail, suggestion, location))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "checks_run": self.checks_run,
            "issues": [i.to_dict() for i in self.issues],
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "QCReport":
        r = cls(checks_run=d.get("checks_run", []))
        r.issues = [QCIssue.from_dict(i) for i in d.get("issues", [])]
        return r


# --------------------------------------------------------------------------- #
#  Project (the whole thing, persisted)                                       #
# --------------------------------------------------------------------------- #
PROJECT_SCHEMA = 1


@dataclass
class Project:
    created: float = field(default_factory=time.time)
    session_label: str = ""
    answers: WizardAnswers = field(default_factory=WizardAnswers)

    origin: str = ""            # original path/URL
    media_path: str = ""        # local media actually analysed
    output_dir: str = ""        # where deliverables are written
    media_downloaded_copy: bool = False

    transcript: Transcript = field(default_factory=Transcript)
    minutes: Optional[Minutes] = None
    action_items: List[ActionItem] = field(default_factory=list)
    qc: QCReport = field(default_factory=QCReport)

    deliverables: List[str] = field(default_factory=list)   # produced file paths

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": PROJECT_SCHEMA,
            "created": self.created,
            "session_label": self.session_label,
            "answers": self.answers.to_dict(),
            "origin": self.origin,
            "media_path": self.media_path,
            "output_dir": self.output_dir,
            "media_downloaded_copy": self.media_downloaded_copy,
            "transcript": self.transcript.to_dict(),
            "minutes": self.minutes.to_dict() if self.minutes else None,
            "action_items": [a.to_dict() for a in self.action_items],
            "qc": self.qc.to_dict(),
            "deliverables": self.deliverables,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Project":
        p = cls(
            created=d.get("created", time.time()),
            session_label=d.get("session_label", ""),
            answers=WizardAnswers.from_dict(d.get("answers", {})),
            origin=d.get("origin", ""),
            media_path=d.get("media_path", ""),
            output_dir=d.get("output_dir", ""),
            media_downloaded_copy=d.get("media_downloaded_copy", False),
            transcript=Transcript.from_dict(d.get("transcript", {})),
            minutes=Minutes.from_dict(d["minutes"]) if d.get("minutes") else None,
            action_items=[ActionItem.from_dict(a) for a in d.get("action_items", [])],
            qc=QCReport.from_dict(d.get("qc", {})),
        )
        p.deliverables = d.get("deliverables", [])
        return p

    def save(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False),
                        encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: str | Path) -> "Project":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
