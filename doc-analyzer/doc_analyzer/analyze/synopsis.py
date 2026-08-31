"""Extractive per-document summary + corpus-level roll-ups."""
from __future__ import annotations

import re
from collections import Counter

from .tfidf import Corpus, tokenize

_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])|\n{2,}")


def _sentences(text: str) -> list[str]:
    parts = []
    for chunk in text.split("\n"):
        chunk = chunk.strip()
        if not chunk:
            continue
        for s in _SENT.split(chunk):
            s = s.strip()
            if 20 <= len(s) <= 400 and sum(c.isalpha() for c in s) >= 12:
                parts.append(s)
    return parts


def extractive_summary(text: str, corpus: Corpus, doc_id: str, n: int = 4,
                       boost: dict[str, float] | None = None) -> list[str]:
    """Top-n sentences by TF-IDF mass.  When ``boost`` is given (objective terms ->
    weight), sentences carrying those terms are pulled up, so the summary answers
    the reader's objective instead of just describing the document."""
    sents = _sentences(text)
    if not sents:
        return []
    weights = dict(corpus.vectors.get(doc_id, {}))
    boost = boost or {}
    scored = []
    for pos, s in enumerate(sents):
        toks = tokenize(s)
        if not toks:
            continue
        score = sum(weights.get(t, 0.0) for t in toks) / (len(toks) ** 0.5)
        if boost:
            score += sum(boost.get(t, 0.0) for t in toks) / (len(toks) ** 0.5)
        if pos < 3:
            score *= 1.15  # lead bias
        scored.append((score, pos, s))
    scored.sort(key=lambda x: -x[0])
    picked = sorted(scored[: n], key=lambda x: x[1])
    return [s for _, _, s in picked]


def type_distribution(records) -> list[tuple[str, int]]:
    c = Counter(r["kind"] for r in records)
    return c.most_common()


def ext_distribution(records) -> list[tuple[str, int]]:
    c = Counter(r["ext"] for r in records)
    return c.most_common()


def corpus_keywords(corpus: Corpus, k: int = 25) -> list[tuple[str, float]]:
    return corpus.corpus_top_terms(k)


def timeline(records) -> list[dict]:
    """Best-guess a date for each doc: first content date, else file mtime."""
    rows = []
    for r in records:
        dt = r.get("best_date") or r.get("mtime_date")
        rows.append({"doc": r["rel"], "date": dt or "", "kind": r["kind"]})
    rows.sort(key=lambda x: (x["date"] == "", x["date"]))
    return rows
