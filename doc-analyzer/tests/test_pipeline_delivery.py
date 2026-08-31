"""End-to-end: analyze() writes the choose-format deliverables and mirrors the
result set into a subfolder of the analyzed folder, and records the run's answers
+ generated filenames in the tools document."""
from __future__ import annotations

import shutil
from pathlib import Path

from doc_analyzer.analyze import RunOptions, analyze
from doc_analyzer.config import Config
from doc_analyzer.session import Session

SAMPLES = Path(__file__).resolve().parent.parent / "sample_docs"


def _run(tmp_path, monkeypatch, **ro_kwargs):
    monkeypatch.setenv("DOCAN_SESSIONS_DIR", str(tmp_path / "sessions"))
    monkeypatch.setenv("DOCAN_LLM_ENABLED", "false")
    src = tmp_path / "docs"
    shutil.copytree(SAMPLES, src)
    sess = Session()
    run_opts = RunOptions(
        objective="What financial obligations do these documents create?",
        formats={"synopsis": "word", "activity": "spreadsheet", "tools": "spreadsheet"},
        **ro_kwargs,
    )
    res = analyze(sess, Config.load(), {"kind": "local", "location": str(src), "options": {}},
                  run_opts)
    sess.close()
    return src, sess, res


def test_writes_deliverables_and_mirrors_to_source(tmp_path, monkeypatch):
    src, sess, res = _run(tmp_path, monkeypatch)

    # session folder has both the .md/.json and the chosen Office files
    for name in ("synopsis.md", "synopsis.docx", "activity_breakdown.xlsx",
                 "tools_and_versions.xlsx", "tools_and_versions.md", "manifest.json"):
        assert (sess.dir / name).is_file(), name

    # mirrored next to the analyzed files
    assert res.deliver_dir is not None
    mirror = res.deliver_dir
    assert mirror.parent == src
    assert mirror.name.startswith("doc-analyzer-results_")
    for name in ("synopsis.docx", "activity_breakdown.xlsx", "tools_and_versions.xlsx",
                 "session.log", "tools_and_versions.md"):
        assert (mirror / name).is_file(), name

    # the objective + format answers + generated filenames land in the tools doc
    tv = (sess.dir / "tools_and_versions.md").read_text(encoding="utf-8")
    assert "## Analysis answers" in tv
    assert "What financial obligations do these documents create?" in tv
    assert "## Generated files" in tv
    assert "synopsis.docx" in tv

    # ... and in the session log
    log = (sess.dir / "session.log").read_text(encoding="utf-8")
    assert "Recording the analysis objective and answers" in log
    assert "Generated result files:" in log

    # objective reaches the markdown synopsis too
    syn = (sess.dir / "synopsis.md").read_text(encoding="utf-8")
    assert "financial obligations" in syn


def test_no_source_copy_when_disabled(tmp_path, monkeypatch):
    src, sess, res = _run(tmp_path, monkeypatch, write_to_source=False)
    assert res.deliver_dir is None
    assert not any(p.name.startswith("doc-analyzer-results_") for p in src.iterdir())
    assert (sess.dir / "synopsis.docx").is_file()
