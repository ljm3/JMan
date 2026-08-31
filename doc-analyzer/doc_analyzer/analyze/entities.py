"""Regex + heuristic entity extraction - deterministic, dependency-free."""
from __future__ import annotations

import re
from collections import Counter

_MONTHS_FULL = ["january", "february", "march", "april", "may", "june", "july",
                "august", "september", "october", "november", "december"]
_MONTHS_ABBR = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep",
                "sept", "oct", "nov", "dec"]
_MONTH_NUM = {m: i + 1 for i, m in enumerate(_MONTHS_FULL)}
_MONTH_NUM.update({m: (i + 1 if m != "sept" else 9) for i, m in enumerate(_MONTHS_ABBR)})

_MONTHS_RE = "|".join(_MONTHS_FULL + _MONTHS_ABBR)

RE = {
    "email": re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),
    "url": re.compile(r"\bhttps?://[^\s<>\"')]+", re.I),
    # require internal separators so "INV-2026-014" style refs are not phones
    "phone": re.compile(r"(?<![\w-])(?:\+\d{1,3}[ .]?)?(?:\(\d{2,4}\)[ .]?)?\d{3}[ .]\d{3,4}(?:[ .]\d{2,4})?(?![\w-])"),
    "money": re.compile(r"(?:USD|EUR|GBP|\$|£|€)\s?\d[\d,]*(?:\.\d{2})?|\b\d[\d,]*\.\d{2}\s?(?:USD|EUR|GBP|dollars|euros)\b", re.I),
    "percent": re.compile(r"\b\d{1,3}(?:\.\d+)?\s?%"),
    "date_iso": re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"),
    "date_num": re.compile(r"\b(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})\b"),
    "date_word": re.compile(rf"\b({_MONTHS_RE})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b", re.I),
    "ref_id": re.compile(r"\b(?:INV|PO|REF|ORD|TCK|CASE|DOC|CR|SOW|MSA|NDA|AGR)[-#]\d{2,}(?:-\d{2,})*\b", re.I),
    "acronym": re.compile(r"\b[A-Z][A-Z0-9]{1,5}\b"),
}

_PROPER = re.compile(r"\b(?:[A-Z]{2,}[ \t]|[A-Z][a-z]{1,}\.?[ \t])?[A-Z][a-z]{1,}[ \t][A-Z][a-z]{1,}\b")
_ORG_SUFFIX = re.compile(r"\b(Inc|LLC|LLP|Ltd|Limited|Corp|Corporation|Company|GmbH|"
                         r"PLC|Group|Holdings|Partners|Associates|Foundation|University|"
                         r"Institute|Department|Ministry|Agency|Bureau|Bank|Trust|"
                         r"Systems|Technologies|Solutions|Industries|Supply|Trading)\b\.?")
_ROLE_HINT = re.compile(r"\b(Mr|Mrs|Ms|Dr|Prof|CEO|CFO|CTO|COO|VP|Director|Manager|"
                        r"President|Chair|Chairman|Secretary|Treasurer|Officer|Lead|"
                        r"Head|Owner|Partner|Analyst|Engineer|Administrator)\b\.?", re.I)

# capitalised words that are never names / orgs on their own
_STOP_CAP = set("""
The This That These Those There Then Than They Them Their When Where While With
Please Note Dear From Sent Subject Date Time Total Amount Qty Quantity Unit Price
Bill Ship Sold Buy Pay Paid Description Item Items Line Lines Page Notes Summary
Purpose Term Obligations Governing Signed Agreement Party Parties Section Article
Monday Tuesday Wednesday Thursday Friday Saturday Sunday Today Tomorrow Yesterday
January February March April June July August September October November December
Week Weekly Status Progress Risks Next Prepared Steel Freight Tax Subtotal Remit
Questions Payment Harbor Street Project Manager Confidential Information Disclosure
Disclosing Receiving Mutual Non Governing Law State Effect Notice Employees Access
And But For Not All Any One Two Three Our Your His Her Its
""".split())

_COMMON_ACRO_STOP = {"AND", "THE", "FOR", "ALL", "ANY", "NON", "PDF", "USD", "EUR",
                     "GBP", "NDA", "INV", "USA", "USB", "FYI", "TBD", "ETA", "AKA"}


def _clean(seq):
    return [s.strip(" .,:;–-\n\t") for s in seq if s and len(s.strip()) > 1]


def _looks_like_name(span: str) -> bool:
    parts = span.split()
    if not (2 <= len(parts) <= 3):
        return False
    return all(p[:1].isupper() and p[1:].islower() and p not in _STOP_CAP for p in parts)


def normalize_date(raw: str) -> str | None:
    """Return yyyy-mm-dd for a recognised date string, else None."""
    s = raw.strip().strip(",")
    m = RE["date_iso"].search(s)
    if m:
        y, mo, d = m.groups()
        return f"{y}-{mo}-{d}"
    m = RE["date_word"].search(s)
    if m:
        mon, d, y = m.groups()
        mn = _MONTH_NUM.get(mon.lower())
        if mn:
            return f"{int(y):04d}-{mn:02d}-{int(d):02d}"
    m = RE["date_num"].search(s)
    if m:
        a, b, y = (int(x) for x in m.groups())
        if y < 100:
            y += 2000
        # assume day/month/year unless first field can only be a month
        d, mo = (a, b) if a > 12 else (b, a) if b > 12 else (a, b)
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return f"{y:04d}-{mo:02d}-{d:02d}"
    return None


def all_dates(text: str) -> list[str]:
    out = []
    for key in ("date_iso", "date_word", "date_num"):
        for m in RE[key].finditer(text):
            out.append(m.group(0))
    return _clean(out)


def iso_dates(text: str) -> list[str]:
    out = {normalize_date(d) for d in all_dates(text)}
    return sorted(x for x in out if x)


def extract_entities(text: str, top: int = 15) -> dict[str, list]:
    res: dict[str, list] = {}
    for name in ("email", "url", "phone", "money", "percent", "ref_id"):
        vals = Counter(_clean(RE[name].findall(text)))
        if vals:
            res[name] = [v for v, _ in vals.most_common(top)]

    dates = all_dates(text)
    if dates:
        res["dates"] = [v for v, _ in Counter(dates).most_common(top)]

    acr = Counter(a for a in RE["acronym"].findall(text) if a not in _COMMON_ACRO_STOP)
    if acr:
        res["acronyms"] = [a for a, _ in acr.most_common(top)]

    orgs: Counter = Counter()
    people: Counter = Counter()
    for m in _PROPER.finditer(text.replace("\n", " ")):
        span = m.group(0).strip()
        if "\n" in span or any(p in _STOP_CAP for p in span.split()):
            continue
        window = text.replace("\n", " ")[max(0, m.start() - 14): m.end() + 22]
        if _ORG_SUFFIX.search(span) or _ORG_SUFFIX.search(window):
            orgs[span] += 1
        elif _looks_like_name(span):
            people[span] += 1
    if orgs:
        res["organizations"] = [o for o, _ in orgs.most_common(top)]
    if people:
        res["people"] = [p for p, _ in people.most_common(top)]
    return res
