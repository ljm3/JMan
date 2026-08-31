from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from meeting_scribe import paths, sources
from meeting_scribe import logging_setup


def test_session_log_numbering(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "_workspace_override", tmp_path, raising=False)
    monkeypatch.setattr(paths, "default_workspace", lambda: tmp_path)
    logs = paths.logs_dir()
    today = dt.date.today().isoformat()
    (logs / f"session-0003_{today}.log").write_text("old", encoding="utf-8")

    # reset the module-level singleton
    logging_setup._SESSION = None
    s = logging_setup.init_session()
    assert s.number == 4
    assert s.path.name == f"session-0004_{today}.log"
    assert s.path.exists()
    assert (logs / "history.log").exists()

    logging_setup.get_logger("t").info("hello world")
    assert "hello world" in s.path.read_text(encoding="utf-8")
    logging_setup._SESSION = None


def test_wizard_answers_written_to_log(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "default_workspace", lambda: tmp_path)
    monkeypatch.setattr(paths, "_workspace_override", None, raising=False)
    logging_setup._SESSION = None
    s = logging_setup.init_session()
    logging_setup.log_wizard_answers({"Audio source": "meeting.m4a",
                                      "Output formats": ["docx", "pdf"]})
    text = s.path.read_text(encoding="utf-8")
    assert "USER SELECTIONS FOR THIS RUN" in text
    assert "meeting.m4a" in text
    assert "docx, pdf" in text
    logging_setup._SESSION = None


def test_looks_like_url():
    assert sources.looks_like_url("https://example.com/a.mp3")
    assert sources.looks_like_url("http://host/x")
    assert not sources.looks_like_url(r"C:\Users\me\a.mp3")
    assert not sources.looks_like_url("just text")


def test_resolve_local_file(tmp_path):
    f = tmp_path / "rec.wav"
    f.write_bytes(b"RIFF....WAVEfmt ")
    r = sources.resolve(str(f))
    assert r.kind == "local"
    assert r.media_path == f
    assert r.output_dir == tmp_path
    assert not r.downloaded_copy


def test_resolve_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        sources.resolve(str(tmp_path / "nope.wav"))
