"""Fast unit checks for the offline analysis primitives."""
from __future__ import annotations

from doc_analyzer.analyze.activity import analyze_activity
from doc_analyzer.analyze.entities import extract_entities, iso_dates, normalize_date
from doc_analyzer.analyze.synopsis import extractive_summary
from doc_analyzer.analyze.tfidf import Corpus, cluster, tokenize


def test_tokenize_drops_stopwords_and_short():
    toks = tokenize("The quick brown fox, a FOX! 42 io")
    assert "quick" in toks and "brown" in toks and "fox" in toks
    assert "the" not in toks and "42" not in toks and "io" not in toks


def test_corpus_clusters_like_documents():
    docs = [
        ("inv1", "invoice total due payment acme supply net 30 remit"),
        ("inv2", "invoice total due payment acme supply net 30 balance"),
        ("hr1", "employee onboarding benefits enrollment handbook policy"),
    ]
    c = Corpus(docs)
    groups = cluster(c, threshold=0.18)
    sizes = sorted(len(g) for g in groups)
    assert sizes == [1, 2]
    pair = next(g for g in groups if len(g) == 2)
    assert set(pair) == {"inv1", "inv2"}


def test_entities_money_dates_refs():
    text = ("Invoice INV-2026-014 dated 2026-03-04. Total due $1,091.88 by "
            "April 3, 2026. Contact billing@acme.example or +1 503 555 0142.")
    ent = extract_entities(text)
    assert any(m.startswith("$1,091") for m in ent.get("money", []))
    assert "billing@acme.example" in ent.get("email", [])
    assert "INV-2026-014" in ent.get("ref_id", [])
    assert "+1 503 555 0142" in ent.get("phone", [])


def test_normalize_date_variants():
    assert normalize_date("2026-03-04") == "2026-03-04"
    assert normalize_date("March 1, 2026") == "2026-03-01"
    assert normalize_date("not a date") is None
    assert iso_dates("seen 2026-03-04 and April 3, 2026") == ["2026-03-04", "2026-04-03"]


def test_activity_detects_recurring_phrases_and_categories():
    docs = {
        "a": "We ship the order after payment. The team must approve the budget.",
        "b": "We ship the order today. Finance will approve the budget next week.",
    }
    phrases, cats = analyze_activity(docs)
    # "ship order" and "approve budget" each recur across both documents
    verbs = {p["verb"] for p in phrases}
    assert {"ship", "approve"} <= verbs
    assert all(p["doc_count"] >= 2 for p in phrases)
    labels = {c["category"] for c in cats}
    assert "Procurement & logistics" in labels
    assert "Finance & billing" in labels


def test_extractive_summary_picks_sentences():
    text = ("The project completed racking installation on aisles one through six. "
            "Unrelated filler sentence about weather and lunch. "
            "The team configured scanners and deployed the inventory application.")
    c = Corpus([("d", text)])
    summ = extractive_summary(text, c, "d", n=2)
    assert 1 <= len(summ) <= 2
    assert any("racking" in s or "scanners" in s for s in summ)
