"""Turn the user's free-text analysis objective into something the engine can act
on: signal terms, quoted phrases, and detected *focus areas*.  The focus areas
decide which sentences get pulled into summaries, which entity types are surfaced,
and which activity categories are flagged as relevant - so the objective actually
steers the analysis and the reports, not just the wording of the narrative.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .tfidf import STOPWORDS, tokenize

# extra weight added to a sentence's score for each objective term it contains
SUMMARY_BOOST = 3.0
# a document at/above this cosine to the objective is treated as "relevant"
DEFAULT_MIN_RELEVANCE = 0.04

# focus area -> cue words (in the objective) / terms to also boost in the corpus /
# entity types to surface / activity categories that count as on-objective
FOCUS: dict[str, dict] = {
    "financial": {
        "cues": {"money", "cost", "costs", "price", "pricing", "amount", "amounts",
                 "payment", "payments", "pay", "paid", "owe", "owed", "payable",
                 "obligation", "obligations", "invoice", "invoices", "invoicing",
                 "budget", "budgets", "spend", "spending", "expense", "expenses",
                 "fee", "fees", "financial", "finance", "revenue", "liability",
                 "liabilities", "dollar", "dollars", "funding", "charge", "charges",
                 "balance", "due", "billing", "billable", "quote", "quotation"},
        "expand": {"invoice", "payment", "total", "due", "remit", "net", "balance",
                   "subtotal", "tax", "amount", "paid", "price", "cost", "fee"},
        "entities": ["money", "percent", "ref_id", "dates"],
        "categories": {"Finance & billing"},
    },
    "risk": {
        "cues": {"risk", "risks", "issue", "issues", "concern", "concerns", "problem",
                 "problems", "exposure", "threat", "threats", "blocker", "blockers",
                 "delay", "delays", "gap", "gaps", "weakness", "weaknesses",
                 "vulnerability", "vulnerabilities", "mitigation", "mitigate"},
        "expand": {"risk", "delay", "issue", "fail", "failure", "concern", "blocker",
                   "escalate", "mitigation", "depend", "dependency", "shortfall"},
        "entities": ["dates", "percent"],
        "categories": {"Project & planning", "IT & operations"},
    },
    "timeline": {
        "cues": {"deadline", "deadlines", "date", "dates", "schedule", "scheduled",
                 "schedules", "timeline", "timelines", "milestone", "milestones",
                 "when", "due", "overdue", "duration", "start", "finish",
                 "completion", "calendar", "timeframe"},
        "expand": {"date", "deadline", "due", "schedule", "milestone", "week",
                   "month", "complete", "start", "finish", "cutover"},
        "entities": ["dates"],
        "categories": {"Project & planning"},
    },
    "responsibility": {
        "cues": {"who", "responsible", "responsibility", "responsibilities", "owner",
                 "owners", "ownership", "accountable", "accountability", "party",
                 "parties", "assign", "assigned", "assignment", "role", "roles",
                 "contact", "contacts", "stakeholder", "stakeholders", "author",
                 "signatory", "signatories", "approver", "approvers", "counterparty"},
        "expand": {"manager", "director", "owner", "lead", "responsible", "assigned",
                   "signed", "approver", "contact", "prepared"},
        "entities": ["people", "organizations", "email", "phone"],
        "categories": {"People & HR", "Communications", "Contracts & compliance"},
    },
    "compliance": {
        "cues": {"comply", "compliance", "compliant", "regulation", "regulations",
                 "regulatory", "policy", "policies", "legal", "law", "laws",
                 "contract", "contracts", "contractual", "clause", "clauses", "term",
                 "terms", "obligation", "obligations", "governing", "audit", "audits",
                 "requirement", "requirements", "standard", "standards", "nda",
                 "confidential", "confidentiality", "warranty", "indemnity",
                 "liability", "breach", "penalty", "penalties"},
        "expand": {"agreement", "clause", "term", "shall", "governing", "confidential",
                   "comply", "obligation", "warranty", "liability", "party", "notice",
                   "terminate", "termination", "breach"},
        "entities": ["ref_id", "organizations", "people", "dates"],
        "categories": {"Contracts & compliance"},
    },
    "scope": {
        "cues": {"scope", "deliverable", "deliverables", "requirement", "requirements",
                 "feature", "features", "objective", "objectives", "goal", "goals",
                 "spec", "specification", "specifications", "phase", "phases"},
        "expand": {"scope", "deliverable", "requirement", "feature", "milestone",
                   "phase", "plan"},
        "entities": [],
        "categories": {"Project & planning", "Data & analysis"},
    },
    "decision": {
        "cues": {"decision", "decisions", "decide", "decided", "approve", "approved",
                 "approval", "approvals", "agreed", "choose", "chosen", "selected",
                 "selection", "rationale", "recommend", "recommendation",
                 "recommendations", "authorised", "authorized"},
        "expand": {"approve", "approved", "decision", "agreed", "selected",
                   "recommend", "sign", "authorize", "authorise"},
        "entities": ["people", "organizations", "dates"],
        "categories": {"Contracts & compliance", "Project & planning"},
    },
    "change": {
        "cues": {"change", "changes", "changed", "amend", "amendment", "amendments",
                 "revision", "revisions", "revise", "revised", "update", "updates",
                 "updated", "modify", "modification", "modifications", "version",
                 "versions", "replace", "replaced", "replacement"},
        "expand": {"change", "amend", "revision", "update", "version", "replace",
                   "new", "patch", "migrate"},
        "entities": ["dates"],
        "categories": {"IT & operations", "Contracts & compliance"},
    },
    "status": {
        "cues": {"status", "progress", "complete", "completed", "completion", "done",
                 "pending", "outstanding", "remaining", "ongoing", "state", "update"},
        "expand": {"status", "progress", "complete", "pending", "outstanding",
                   "remaining", "done", "next"},
        "entities": ["dates"],
        "categories": {"Project & planning"},
    },
}

_QUOTED = re.compile(r"[\"'“‘]([^\"'”’]{2,80})[\"'”’]")
_QUESTION_LEAD = {"what", "which", "who", "whom", "whose", "when", "where", "why",
                  "how", "is", "are", "do", "does", "did", "can", "could", "should",
                  "list", "identify", "find", "show", "summarize", "summarise",
                  "describe", "explain", "compare", "extract", "determine", "assess"}

_ENTITY_LABEL = {
    "money": "monetary amounts", "percent": "percentages", "ref_id": "reference IDs",
    "dates": "dates", "people": "people", "organizations": "organizations",
    "email": "email addresses", "phone": "phone numbers", "url": "URLs",
    "acronyms": "acronyms",
}


@dataclass
class Objective:
    raw: str = ""
    terms: list[str] = field(default_factory=list)          # signal tokens from the text
    phrases: list[str] = field(default_factory=list)        # quoted phrases
    focus: list[str] = field(default_factory=list)          # detected focus areas
    all_tokens: set[str] = field(default_factory=set)       # terms + phrase tokens + focus expansions
    is_question: bool = False

    def is_empty(self) -> bool:
        return not self.raw.strip()

    # entity types worth surfacing for this objective (focus-driven, else a default)
    def entity_types(self) -> list[str]:
        out: list[str] = []
        for area in self.focus:
            for e in FOCUS[area]["entities"]:
                if e not in out:
                    out.append(e)
        if not out:
            out = ["money", "dates", "people", "organizations", "ref_id"]
        return out

    def entity_type_labels(self) -> list[str]:
        return [_ENTITY_LABEL.get(e, e) for e in self.entity_types()]

    def relevant_categories(self) -> set[str]:
        cats: set[str] = set()
        for area in self.focus:
            cats |= FOCUS[area]["categories"]
        return cats

    def describe(self) -> str:
        if self.is_empty():
            return "(no objective given - full general analysis)"
        bits = []
        if self.focus:
            bits.append("focus: " + ", ".join(self.focus))
        if self.terms:
            bits.append("key terms: " + ", ".join(self.terms[:12]))
        if self.phrases:
            bits.append("phrases: " + "; ".join(self.phrases))
        return " | ".join(bits) or "(free-text objective)"


def parse(raw: str) -> Objective:
    raw = (raw or "").strip()
    if not raw:
        return Objective()
    low = raw.lower()
    phrases = [m.group(1).strip() for m in _QUOTED.finditer(raw)]
    without_quotes = _QUOTED.sub(" ", raw)

    terms: list[str] = []
    for t in tokenize(without_quotes):
        if t not in terms:
            terms.append(t)

    term_set = set(terms) | {t for ph in phrases for t in tokenize(ph)}
    focus: list[str] = []
    for area, spec in FOCUS.items():
        if term_set & spec["cues"] or any(" " + c + " " in " " + low + " " for c in spec["cues"] if " " in c):
            focus.append(area)

    all_tokens = set(term_set)
    for area in focus:
        all_tokens |= FOCUS[area]["expand"]
    all_tokens = {t for t in all_tokens if len(t) >= 3 and t not in STOPWORDS}

    first = tokenize(low)[:1]
    is_question = raw.rstrip().endswith("?") or (first and first[0] in _QUESTION_LEAD)

    return Objective(raw=raw, terms=terms, phrases=phrases, focus=focus,
                     all_tokens=all_tokens, is_question=is_question)


def key_sentences(doc_texts: dict[str, str], qvec: dict[str, float],
                  k: int = 12, per_doc_cap: int = 3) -> list[dict]:
    """Highest objective-relevance sentences across the whole corpus."""
    from .synopsis import _sentences  # local import to avoid a cycle at module load

    scored: list[tuple[float, str, str]] = []
    for did, text in doc_texts.items():
        seen = 0
        local: list[tuple[float, str]] = []
        for s in _sentences(text):
            toks = tokenize(s)
            if not toks:
                continue
            sc = sum(qvec.get(t, 0.0) for t in toks) / (len(toks) ** 0.5)
            if sc > 0:
                local.append((sc, s))
        local.sort(key=lambda x: -x[0])
        for sc, s in local[:per_doc_cap]:
            scored.append((sc, did, s))
            seen += 1
    scored.sort(key=lambda x: -x[0])
    out = []
    for sc, did, s in scored[:k]:
        out.append({"doc": did, "score": round(sc, 4), "sentence": s})
    return out


def activity_relevance(tokens: set[str], category: str, obj: Objective) -> bool:
    if obj.is_empty():
        return False
    if obj.all_tokens & tokens:
        return True
    return category in obj.relevant_categories()
