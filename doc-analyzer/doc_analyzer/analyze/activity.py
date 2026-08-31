"""Detect "activity" across the corpus: recurring action phrases and the
business-activity categories they roll up to (requirement 4)."""
from __future__ import annotations

import re
from collections import Counter, defaultdict

from .tfidf import STOPWORDS

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
_WORD = re.compile(r"[A-Za-z][A-Za-z\-']+")

# verb (lemma) -> business activity category
ACTIVITY_VERBS = {
    # finance
    "invoice": "Finance & billing", "bill": "Finance & billing", "pay": "Finance & billing",
    "charge": "Finance & billing", "refund": "Finance & billing", "reimburse": "Finance & billing",
    "budget": "Finance & billing", "fund": "Finance & billing", "expense": "Finance & billing",
    "quote": "Finance & billing", "price": "Finance & billing",
    # procurement / logistics
    "order": "Procurement & logistics", "purchase": "Procurement & logistics",
    "ship": "Procurement & logistics", "deliver": "Procurement & logistics",
    "receive": "Procurement & logistics", "dispatch": "Procurement & logistics",
    "procure": "Procurement & logistics", "stock": "Procurement & logistics",
    "restock": "Procurement & logistics", "supply": "Procurement & logistics",
    # contracts / legal / compliance
    "sign": "Contracts & compliance", "execute": "Contracts & compliance",
    "renew": "Contracts & compliance", "terminate": "Contracts & compliance",
    "amend": "Contracts & compliance", "approve": "Contracts & compliance",
    "reject": "Contracts & compliance", "authorize": "Contracts & compliance",
    "audit": "Contracts & compliance", "certify": "Contracts & compliance",
    "comply": "Contracts & compliance", "review": "Contracts & compliance",
    "file": "Contracts & compliance", "submit": "Contracts & compliance",
    # HR / people
    "hire": "People & HR", "onboard": "People & HR", "recruit": "People & HR",
    "train": "People & HR", "promote": "People & HR", "evaluate": "People & HR",
    "interview": "People & HR", "resign": "People & HR", "furlough": "People & HR",
    # IT / operations
    "install": "IT & operations", "configure": "IT & operations", "deploy": "IT & operations",
    "provision": "IT & operations", "patch": "IT & operations", "upgrade": "IT & operations",
    "migrate": "IT & operations", "backup": "IT & operations", "restore": "IT & operations",
    "monitor": "IT & operations", "decommission": "IT & operations",
    # project / planning
    "plan": "Project & planning", "schedule": "Project & planning", "assign": "Project & planning",
    "deliverable": "Project & planning", "milestone": "Project & planning",
    "complete": "Project & planning", "launch": "Project & planning",
    "kickoff": "Project & planning", "escalate": "Project & planning",
    "resolve": "Project & planning", "close": "Project & planning",
    # communications
    "notify": "Communications", "announce": "Communications", "report": "Communications",
    "request": "Communications", "confirm": "Communications", "respond": "Communications",
    "meet": "Communications", "discuss": "Communications", "present": "Communications",
    # sales / customer
    "sell": "Sales & customer", "propose": "Sales & customer", "negotiate": "Sales & customer",
    "onboarded": "Sales & customer", "renewal": "Sales & customer", "churn": "Sales & customer",
    "support": "Sales & customer", "ticket": "Sales & customer",
    # data / analysis
    "analyze": "Data & analysis", "forecast": "Data & analysis", "measure": "Data & analysis",
    "calculate": "Data & analysis", "estimate": "Data & analysis", "survey": "Data & analysis",
    "test": "Data & analysis", "validate": "Data & analysis", "sample": "Data & analysis",
}

_IRREGULAR = {
    "paid": "pay", "bought": "buy", "sold": "sell", "sent": "send", "built": "build",
    "signed": "sign", "shipped": "ship", "made": "make", "met": "meet", "ran": "run",
    "held": "hold", "gave": "give", "took": "take", "wrote": "write", "chose": "choose",
    "began": "begin", "drew": "draw", "hired": "hire",
}


def _lemma(word: str) -> str:
    w = word.lower()
    if w in _IRREGULAR:
        return _IRREGULAR[w]
    for suf, cut in (("ing", 3), ("ied", 3), ("ed", 2), ("es", 2), ("s", 1)):
        if w.endswith(suf) and len(w) - cut >= 3:
            stem = w[: len(w) - cut]
            if suf == "ing" and len(stem) >= 2 and stem[-1] == stem[-2]:
                stem = stem[:-1]
            if suf == "ied":
                stem += "y"
            return stem
    return w


def _phrase_after(tokens: list[str], idx: int, n: int = 3) -> str:
    out = []
    for tok in tokens[idx + 1: idx + 8]:
        low = tok.lower()
        if low in STOPWORDS:
            if out:
                continue
            else:
                continue
        out.append(low)
        if len(out) >= n:
            break
    return " ".join(out)


def analyze_activity(doc_texts: dict[str, str]):
    """Returns (phrase_rows, category_rows).

    phrase_rows: [{phrase, verb, category, occurrences, doc_count, docs}]
    category_rows: [{category, doc_count, mentions, verbs, docs}]
    """
    # group activity by  verb + first content token  (stable across wording),
    # but remember the most common fuller phrasing for display
    key_docs: dict[str, set] = defaultdict(set)
    key_hits: Counter = Counter()
    key_meta: dict[str, tuple[str, str]] = {}
    key_variants: dict[str, Counter] = defaultdict(Counter)
    cat_docs: dict[str, set] = defaultdict(set)
    cat_hits: Counter = Counter()
    cat_verbs: dict[str, Counter] = defaultdict(Counter)

    for did, text in doc_texts.items():
        for sent in _SENT_SPLIT.split(text):
            words = _WORD.findall(sent)
            if not words:
                continue
            for i, w in enumerate(words):
                lem = _lemma(w)
                cat = ACTIVITY_VERBS.get(lem)
                if not cat:
                    continue
                obj = _phrase_after(words, i, 1)
                display = (lem + " " + _phrase_after(words, i, 3)).strip()
                key = (lem + " " + obj).strip() if obj else lem
                key_hits[key] += 1
                key_docs[key].add(did)
                key_meta[key] = (lem, cat)
                key_variants[key][display] += 1
                cat_hits[cat] += 1
                cat_docs[cat].add(did)
                cat_verbs[cat][lem] += 1

    phrase_rows = []
    for key, occ in key_hits.most_common():
        lem, cat = key_meta[key]
        docs = sorted(key_docs[key])
        if occ < 2 and len(docs) < 2:
            continue  # keep only recurring activity
        phrase_rows.append({
            "phrase": key_variants[key].most_common(1)[0][0],
            "verb": lem, "category": cat,
            "occurrences": occ, "doc_count": len(docs), "docs": docs,
        })

    category_rows = []
    for cat, hits in cat_hits.most_common():
        category_rows.append({
            "category": cat,
            "doc_count": len(cat_docs[cat]),
            "mentions": hits,
            "verbs": [v for v, _ in cat_verbs[cat].most_common(8)],
            "docs": sorted(cat_docs[cat]),
        })
    return phrase_rows, category_rows
