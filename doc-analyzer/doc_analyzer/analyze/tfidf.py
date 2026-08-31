"""Pure-Python TF-IDF, cosine similarity and threshold clustering - no numpy, so
the base install stays tiny and compiler-free."""
from __future__ import annotations

import math
import re
from collections import Counter

_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9_+\-/]{1,29}")

STOPWORDS = set("""
a about above after again against all am an and any are aren't as at be because been
before being below between both but by can't cannot could couldn't did didn't do does
doesn't doing don't down during each few for from further had hadn't has hasn't have
haven't having he he'd he'll he's her here here's hers herself him himself his how how's
i i'd i'll i'm i've if in into is isn't it it's its itself let's me more most mustn't my
myself no nor not of off on once only or other ought our ours ourselves out over own same
shan't she she'd she'll she's should shouldn't so some such than that that's the their
theirs them themselves then there there's these they they'd they'll they're they've this
those through to too under until up very was wasn't we we'd we'll we're we've were weren't
what what's when when's where where's which while who who's whom why why's with won't would
wouldn't you you'd you'll you're you've your yours yourself yourselves also may per via
etc within upon shall must many much every either neither able across among
""".split())


def tokenize(text: str) -> list[str]:
    out = []
    for m in _TOKEN.finditer(text.lower()):
        t = m.group(0).strip("-/_")
        if len(t) < 3 or t in STOPWORDS or t.isdigit():
            continue
        out.append(t)
    return out


class Corpus:
    """Holds per-document token counts and derived TF-IDF vectors."""

    def __init__(self, docs: list[tuple[str, str]]):
        # docs: list of (doc_id, text)
        self.ids = [d[0] for d in docs]
        self.counts: dict[str, Counter] = {}
        self.df: Counter = Counter()
        for did, text in docs:
            c = Counter(tokenize(text))
            self.counts[did] = c
            for term in c:
                self.df[term] += 1
        self.n = max(len(docs), 1)
        self.vectors: dict[str, dict[str, float]] = {}
        self._build_vectors()

    def _idf(self, term: str) -> float:
        return math.log((1 + self.n) / (1 + self.df[term])) + 1.0

    def _build_vectors(self) -> None:
        for did, c in self.counts.items():
            vec: dict[str, float] = {}
            for term, freq in c.items():
                vec[term] = (1 + math.log(freq)) * self._idf(term)
            norm = math.sqrt(sum(w * w for w in vec.values())) or 1.0
            self.vectors[did] = {t: w / norm for t, w in vec.items()}

    def top_terms(self, did: str, k: int = 12) -> list[tuple[str, float]]:
        return sorted(self.vectors.get(did, {}).items(), key=lambda x: -x[1])[:k]

    def corpus_top_terms(self, k: int = 25) -> list[tuple[str, float]]:
        agg: Counter = Counter()
        for vec in self.vectors.values():
            for t, w in vec.items():
                agg[t] += w
        return agg.most_common(k)

    def cosine(self, a: str, b: str) -> float:
        va, vb = self.vectors.get(a, {}), self.vectors.get(b, {})
        if len(va) > len(vb):
            va, vb = vb, va
        return sum(w * vb.get(t, 0.0) for t, w in va.items())

    def query_vector(self, tokens) -> dict[str, float]:
        """Build an L2-normalised TF-IDF vector from arbitrary tokens, using this
        corpus's IDF so a query/objective sits in the same space as the docs."""
        c = Counter(t for t in tokens if t)
        vec = {t: (1 + math.log(f)) * self._idf(t) for t, f in c.items()}
        norm = math.sqrt(sum(w * w for w in vec.values())) or 1.0
        return {t: w / norm for t, w in vec.items()}

    def cosine_to(self, doc_id: str, qvec: dict[str, float]) -> float:
        dv = self.vectors.get(doc_id, {})
        if len(qvec) <= len(dv):
            return sum(w * dv.get(t, 0.0) for t, w in qvec.items())
        return sum(w * qvec.get(t, 0.0) for t, w in dv.items())

    def similarity_pairs(self, threshold: float) -> list[tuple[str, str, float]]:
        out = []
        ids = self.ids
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                s = self.cosine(ids[i], ids[j])
                if s >= threshold:
                    out.append((ids[i], ids[j], s))
        return out


class _UF:
    def __init__(self, items):
        self.p = {i: i for i in items}

    def find(self, x):
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[ra] = rb


def cluster(corpus: Corpus, threshold: float) -> list[list[str]]:
    uf = _UF(corpus.ids)
    for a, b, _ in corpus.similarity_pairs(threshold):
        uf.union(a, b)
    groups: dict[str, list[str]] = {}
    for did in corpus.ids:
        groups.setdefault(uf.find(did), []).append(did)
    # largest clusters first, singletons last
    return sorted(groups.values(), key=lambda g: (-len(g), g[0]))
