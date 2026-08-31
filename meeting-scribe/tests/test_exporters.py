"""Smoke-test each exporter end to end. Requires python-docx / odfpy / reportlab
(all in requirements.txt)."""
from __future__ import annotations

import zipfile

import pytest

from meeting_scribe.documents.builders import (build_action_items, build_minutes,
                                               build_qc_report, build_transcript)
from meeting_scribe.documents.exporters import export
from tests.test_model_and_qc import _demo_project


@pytest.mark.parametrize("fmt", ["docx", "odt", "pdf"])
def test_export_every_doctype(tmp_path, fmt):
    p = _demo_project()
    specs = {
        "transcript": build_transcript(p, named=True),
        "transcript_generic": build_transcript(p, named=False),
        "minutes": build_minutes(p, with_synopsis=True),
        "minutes_plain": build_minutes(p, with_synopsis=False),
        "actions": build_action_items(p),
        "qc": build_qc_report(p),
    }
    for name, spec in specs.items():
        out = tmp_path / f"{name}.{fmt}"
        export(spec, out, fmt)
        assert out.exists() and out.stat().st_size > 200
        if fmt == "docx":
            assert zipfile.is_zipfile(out)
        if fmt == "pdf":
            assert out.read_bytes()[:4] == b"%PDF"


def test_interactive_html(tmp_path):
    from meeting_scribe.documents.export_html import build_interactive_html
    p = _demo_project()
    # give a word so highlight path is exercised
    p.transcript.segments[0].words = []
    out = build_interactive_html(p, tmp_path / "t.html", "media/audio.m4a", named=True)
    html = out.read_text(encoding="utf-8")
    assert "timeupdate" in html and "media/audio.m4a" in html
    assert "Budget Review" in html
