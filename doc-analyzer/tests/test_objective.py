"""The analysis objective must actually steer the run: focus detection, document
ranking, objective-boosted summaries, and objective-relevant activity flags."""
from __future__ import annotations

import shutil
from pathlib import Path

from doc_analyzer.analyze import RunOptions, analyze
from doc_analyzer.analyze.objective import parse, key_sentences
from doc_analyzer.analyze.synopsis import extractive_summary
from doc_analyzer.analyze.tfidf import Corpus
from doc_analyzer.config import Config
from doc_analyzer.session import Session

SAMPLES = Path(__file__).resolve().parent.parent / "sample_docs"


def test_parse_detects_focus_terms_and_question():
    o = parse('What financial obligations and payment deadlines do these "master agreements" create?')
    assert "financial" in o.focus and "timeline" in o.focus and "compliance" in o.focus
    assert o.is_question
    assert "master agreements" in o.phrases
    # focus expansions feed the boost vocabulary
    assert {"invoice", "payment", "deadline"} <= o.all_tokens


def test_parse_empty_is_inert():
    o = parse("   ")
    assert o.is_empty() and not o.focus and not o.all_tokens


def test_query_vector_and_cosine_rank_docs():
    docs = [
        ("invoice", "Invoice total due. Payment terms net 30. Remit the balance to Acme."),
        ("recipe", "Chop the onions and simmer the tomatoes for twenty minutes."),
    ]
    c = Corpus(docs)
    qv = c.query_vector(parse("what payments are due").all_tokens)
    assert c.cosine_to("invoice", qv) > c.cosine_to("recipe", qv)


def test_objective_boost_changes_summary_selection():
    text = ("The quarterly facilities maintenance programme covered interior painting, "
            "carpet replacement, lighting upgrades and furniture refurbishment across "
            "every meeting room, kitchen and corridor in the north building. "
            "The supplier invoice payment is due on March 3. "
            "Recycling collection now happens weekly.")
    c = Corpus([("d", text)])
    plain = extractive_summary(text, c, "d", n=1)
    boosted = extractive_summary(text, c, "d", n=1,
                                 boost={t: 6.0 for t in parse("what payment is due and when").all_tokens})
    assert "facilities maintenance" in plain[0].lower()          # plain picks the dense line
    assert "due on march 3" in boosted[0].lower()                # objective boost flips it
    assert boosted != plain


def test_key_sentences_prefers_on_objective_lines():
    docs = {"a": "We hired two interns. The payment of $900 is due April 1. We ate cake."}
    c = Corpus(list(docs.items()))
    qv = c.query_vector(parse("what is due and when").all_tokens)
    ks = key_sentences(docs, qv, k=1)
    assert ks and "due" in ks[0]["sentence"].lower()


def test_pipeline_ranks_and_reports_for_objective(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCAN_SESSIONS_DIR", str(tmp_path / "s"))
    monkeypatch.setenv("DOCAN_LLM_ENABLED", "false")
    src = tmp_path / "docs"
    shutil.copytree(SAMPLES, src)
    sess = Session()
    res = analyze(sess, Config.load(),
                  {"kind": "local", "location": str(src), "options": {}},
                  RunOptions(objective="What financial obligations do these documents create, "
                                       "and by when are payments due?",
                             write_to_source=False))
    sess.close()

    syn = (sess.dir / "synopsis.md").read_text(encoding="utf-8")
    assert "## Findings for your objective" in syn
    assert "Documents ranked by relevance to the objective" in syn
    assert "Focus areas detected:" in syn

    data = __import__("json").loads((sess.dir / "synopsis.json").read_text(encoding="utf-8"))
    prof = data["corpus"]["objective_profile"]
    assert "financial" in prof["focus"]
    by_rel = {d["rel"]: d for d in data["documents"]}
    # the two invoices must outrank the python helper and the CSV
    assert by_rel["invoice_2026_014.txt"]["relevance"] > by_rel["import_stock.py"]["relevance"]
    assert by_rel["invoice_2026_021.txt"]["relevance"] > by_rel["inventory_counts.csv"]["relevance"]
    assert by_rel["invoice_2026_014.txt"]["relevance_rank"] <= 2

    act = (sess.dir / "activity_breakdown.md").read_text(encoding="utf-8")
    assert "Activities most relevant to your objective" in act
    assert "Finance & billing" in act

    log = (sess.dir / "session.log").read_text(encoding="utf-8")
    assert "Interpreting the analysis objective" in log
    assert "Ranking documents against the objective" in log


def test_pipeline_without_objective_has_no_findings_section(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCAN_SESSIONS_DIR", str(tmp_path / "s"))
    monkeypatch.setenv("DOCAN_LLM_ENABLED", "false")
    src = tmp_path / "docs"
    shutil.copytree(SAMPLES, src)
    sess = Session()
    analyze(sess, Config.load(), {"kind": "local", "location": str(src), "options": {}},
            RunOptions(write_to_source=False))
    sess.close()
    syn = (sess.dir / "synopsis.md").read_text(encoding="utf-8")
    assert "## Findings for your objective" not in syn
