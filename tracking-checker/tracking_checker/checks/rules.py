"""Offline interpreter for the "anything specific to check?" box.

Each recognised phrase becomes one extra column. Values are "Yes" / "No" / "Unknown" (or a
short text). A row "Matches Your Check" when any flag column is "Yes".
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Callable

from .. import models as M

_MON = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
DATE = (r"(\d{1,2}/\d{1,2}(?:/\d{2,4})?|\d{4}-\d{1,2}-\d{1,2}|" + _MON + r"\s+\d{1,2}(?:st|nd|rd|th)?(?:,?\s+\d{4})?|"
        r"\d{1,2}(?:st|nd|rd|th)?\s+" + _MON + r"(?:,?\s+\d{4})?)")

LEFT_WORDS = ["front door", "back door", "side door", "porch", "mailbox", "parcel locker", "locker", "garage",
              "reception", "front desk", "mail room", "mailroom", "loading dock", "dock", "neighbor", "office",
              "leasing office", "receptionist", "patio", "building"]

STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA", "colorado": "CO",
    "connecticut": "CT", "delaware": "DE", "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS", "kentucky": "KY", "louisiana": "LA",
    "maine": "ME", "maryland": "MD", "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR", "pennsylvania": "PA",
    "rhode island": "RI", "south carolina": "SC", "south dakota": "SD", "tennessee": "TN", "texas": "TX",
    "utah": "UT", "vermont": "VT", "virginia": "VA", "washington": "WA", "west virginia": "WV",
    "wisconsin": "WI", "wyoming": "WY", "district of columbia": "DC", "puerto rico": "PR",
}


@dataclass
class Rule:
    column: str
    fn: Callable[[M.TrackingResult], str]
    explain: str


def parse_date(s: str, today: date | None = None) -> date | None:
    from dateutil import parser as dparser
    today = today or date.today()
    s = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", s.strip(), flags=re.I)
    try:
        d = dparser.parse(s, default=datetime(today.year, 1, 1), dayfirst=False).date()
    except (ValueError, OverflowError):
        return None
    if not re.search(r"\d{4}|/\d{2}$", s) and d > today + timedelta(days=31):
        d = d.replace(year=d.year - 1)   # "Dec 20" typed in January means last December
    return d


def _yn(v: bool | None) -> str:
    return "Unknown" if v is None else ("Yes" if v else "No")


def _dday(dt) -> date | None:
    if isinstance(dt, datetime):
        return dt.date()
    return dt


def _est_date(r: M.TrackingResult) -> date | None:
    m = re.search(r"\d{4}-\d{2}-\d{2}", r.estimated_delivery or "")
    if not m:
        return None
    ds = re.findall(r"\d{4}-\d{2}-\d{2}", r.estimated_delivery)
    return datetime.strptime(ds[-1], "%Y-%m-%d").date()


def _num(s: str | None, default: float) -> float:
    try:
        return float(s) if s else default
    except ValueError:
        return default


def _weight_lbs(r: M.TrackingResult) -> float | None:
    m = re.match(r"\s*([\d.]+)\s*([a-z]*)", r.weight or "", re.I)
    if not m:
        return None
    v = float(m.group(1))
    return v * 2.20462 if m.group(2).lower().startswith("kg") else v


# ---------------------------------------------------------------- the matchers
def _matchers(today: date):
    """(regex, factory(match) -> Rule)"""
    out = []

    def add(pattern, factory):
        out.append((re.compile(pattern, re.I), factory))

    def delivered_cmp(label, cmp):
        def f(m):
            d = parse_date(m.group("d"), today)
            if not d:
                return None
            return Rule(f"Delivered {label} {d:%Y-%m-%d}",
                        lambda r: _yn(cmp(_dday(r.delivered_at), d)) if r.delivered and r.delivered_at else "No",
                        f"delivered {label.lower()} {d:%Y-%m-%d}")
        return f

    add(r"delivered\s+(?:between|from)\s+(?P<a>" + DATE + r")\s+(?:and|to|through|thru|-)\s+(?P<b>" + DATE + ")",
        lambda m: (lambda a, b: Rule(f"Delivered {a:%Y-%m-%d} to {b:%Y-%m-%d}",
                                      lambda r: _yn(r.delivered and r.delivered_at is not None
                                                    and a <= _dday(r.delivered_at) <= b),
                                      f"delivered between {a} and {b}") if a and b else None)(
            parse_date(m.group("a"), today), parse_date(m.group("b"), today)))
    add(r"delivered\s+(?:on\s+or\s+after|since)\s+(?P<d>" + DATE + ")", delivered_cmp("On/After", lambda x, d: x >= d))
    add(r"delivered\s+(?:after|later\s+than)\s+(?P<d>" + DATE + ")", delivered_cmp("After", lambda x, d: x > d))
    add(r"delivered\s+(?:on\s+or\s+before|by)\s+(?P<d>" + DATE + ")", delivered_cmp("By", lambda x, d: x <= d))
    add(r"delivered\s+(?:before|prior\s+to|earlier\s+than)\s+(?P<d>" + DATE + ")", delivered_cmp("Before", lambda x, d: x < d))
    add(r"delivered\s+on\s+(?P<d>" + DATE + ")", delivered_cmp("On", lambda x, d: x == d))

    def shipped_cmp(m):
        d = parse_date(m.group("d"), today)
        after = m.group("w").lower() in ("after", "since")
        if not d:
            return None
        return Rule(f"Shipped {'After' if after else 'Before'} {d:%Y-%m-%d}",
                    lambda r: "Unknown" if not r.ship_date else _yn(_dday(r.ship_date) > d if after else _dday(r.ship_date) < d),
                    f"shipped {'after' if after else 'before'} {d}")
    add(r"(?:shipped|ship\s+date|picked\s+up)\s+(?P<w>after|since|before|prior\s+to)\s+(?P<d>" + DATE + ")", shipped_cmp)

    add(r"delivered\s+(?:in\s+the\s+)?(?:last|past)\s+(?P<n>\d+)\s+days?",
        lambda m: (lambda n: Rule(f"Delivered in Last {n} Days",
                                  lambda r: _yn(r.delivered and r.delivered_at is not None
                                                and _dday(r.delivered_at) >= today - timedelta(days=n)),
                                  f"delivered in the last {n} days"))(int(m.group("n"))))
    add(r"delivered\s+(?P<w>today|yesterday)",
        lambda m: (lambda d, w: Rule(f"Delivered {w.title()}",
                                     lambda r: _yn(r.delivered and r.delivered_at is not None and _dday(r.delivered_at) == d),
                                     f"delivered {w}"))(today if m.group("w").lower() == "today" else today - timedelta(days=1),
                                                         m.group("w").lower()))

    add(r"\b(?:not|never|un)\s*(?:yet\s+)?delivered\b|\bundelivered\b|still\s+in\s+transit|\boutstanding\b|"
        r"\bopen\s+shipments?\b|\bnot\s+arrived\b|\bhasn'?t\s+(?:been\s+)?delivered",
        lambda m: Rule("Not Yet Delivered", lambda r: _yn(not r.delivered), "not yet delivered"))

    def stale(m):
        n = int(m.group("n") or 3)

        def f(r):
            if r.delivered:
                return "No"
            le = r.last_event
            if not le or not le.timestamp:
                return "Unknown"
            return _yn((datetime.now() - le.timestamp).days >= n)
        return Rule(f"No Update in {n}+ Days", f, f"no tracking update in {n}+ days")
    add(r"(?:no|without)\s+(?:movement|updates?|scans?|activity|tracking\s+updates?)(?:\s+(?:in|for)\s+"
        r"(?:the\s+)?(?:last\s+|past\s+)?(?P<n>\d+)\s*\+?\s*days?)?", stale)
    add(r"(?:stuck|stalled|stale|hasn'?t\s+moved|not\s+moving)(?:\D{0,30}?(?P<n>\d+)\s*\+?\s*days?)?", stale)

    def transit(m):
        n = int(m.group("n") or m.group("n2"))
        return Rule(f"Transit Over {n} Days",
                    lambda r: "Unknown" if r.days_in_transit() is None else _yn(r.days_in_transit() > n),
                    f"in transit longer than {n} days")
    add(r"(?:in\s+transit|transit\s+time|took|taking|taken)\s+(?:for\s+)?(?:more|longer|over)\s+(?:than\s+)?(?P<n>\d+)\s+days?"
        r"|(?:more|longer|over)\s+(?:than\s+)?(?P<n2>\d+)\s+days?\s+(?:in\s+transit|to\s+(?:deliver|arrive))", transit)

    add(r"\blate\b|\bmissed\b|past\s+due|overdue|after\s+(?:the\s+)?(?:estimated|expected|scheduled|promised)|on[\s-]time",
        lambda m: Rule("Late vs Estimate", _late, "delivered (or still undelivered) after the carrier's estimated date"))

    add(r"exception|delay|problem|issue|\balert|damage|weather|held\b",
        lambda m: Rule("Has Exception/Delay", _exception, "an exception, delay or alert appears in the history"))

    def signed_by(m):
        name = m.group("name").strip(" '\"").rstrip(".")
        if name.lower() in ("anyone", "someone", "whom", "who", "somebody"):
            return Rule("Has Signature Name", lambda r: _yn(bool(r.pod and r.pod.signed_by)) if r.delivered else "No",
                        "a signer name was recorded")
        return Rule(f"Signed By Contains '{name}'",
                    lambda r: _yn(bool(r.pod and name.lower() in (r.pod.signed_by or "").lower())) if r.delivered else "No",
                    f"signed for by someone matching '{name}'")
    add(r"signed\s+(?:for\s+)?by\s+(?P<name>[\"']?[A-Za-z][\w .'-]{0,40}?[\"']?)(?=[.,;!?]|$|\s+(?:and|or|but)\s)", signed_by)
    add(r"(?:no|missing|without)\s+signature|not\s+signed|unsigned",
        lambda m: Rule("No Signature Name", lambda r: _yn(not (r.pod and r.pod.signed_by)) if r.delivered else "No",
                       "delivered with no signer name recorded"))
    add(r"who\s+signed|signature\s+(?:required|present|captured|on\s+file)|has\s+(?:a\s+)?signature",
        lambda m: Rule("Has Signature Name", lambda r: _yn(bool(r.pod and r.pod.signed_by)) if r.delivered else "No",
                       "a signer name was recorded"))

    def left_at(m):
        what = m.group("what").strip().lower()
        return Rule(f"Left At Contains '{what}'",
                    lambda r: _yn(bool(r.pod and what in (r.pod.left_at or "").lower())) if r.delivered else "No",
                    f"left at '{what}'")
    add(r"left\s+(?:at|in|on|with)\s+(?:the\s+|a\s+)?(?P<what>[a-z][a-z ]{1,30}?)(?=[.,;!?]|$|\s+(?:and|or|but)\s)", left_at)

    def dest(m):
        raw = m.group("where").strip().rstrip(".")
        low = raw.lower()
        if low in LEFT_WORDS or any(low.startswith(w) for w in ("the front", "the back", "the ", "a ")) and \
                any(w in low for w in LEFT_WORDS):
            return left_at(re.match(r"(?P<what>.+)", re.sub(r"^(the|a)\s+", "", low)))
        if low in ("sender", "the sender", "shipper", "the shipper"):
            return None
        key = STATES.get(low, raw.upper() if re.fullmatch(r"[A-Za-z]{2}", raw) else None)

        def f(r):
            where = " ".join([r.destination or "", (r.pod.address if r.pod else "") or ""]).upper()
            if not where.strip():
                return "Unknown"
            if key:
                return _yn(bool(re.search(rf"(?:,|\s){key}(?:\s|$|,)", " " + where)))
            return _yn(raw.upper() in where)
        return Rule(f"Destination Is {key or raw.title()}", f, f"destination is {key or raw}")
    add(r"(?:delivered|delivering|shipped|shipping|going|sent|destined|headed|ship)\s+(?:to|in|into)\s+"
        r"(?P<where>[A-Za-z][A-Za-z .]{1,40}?)(?=[.,;!?]|$|\s+(?:and|or|but|after|before|between|on|since|by)\s)", dest)

    add(r"return(?:ed|ing|s)?\b(?:\s+to\s+sender)?|\brts\b|refused",
        lambda m: Rule("Returned to Sender", lambda r: _yn(r.status == M.RETURNED or any(
            re.search(r"return(ed|ing)? to (sender|shipper)|refused", e.description, re.I) for e in r.events)),
            "returned to sender or refused"))

    def attempts(m):
        n = int(m.group("n")) if m.group("n") else (1 if m.group("q") and m.group("q").lower() in ("multiple", "several") else 0)
        return Rule(f"Delivery Attempts > {n}", lambda r: "Unknown" if r.attempts is None else _yn(r.attempts > n),
                    f"more than {n} delivery attempt(s)")
    add(r"(?P<q>more\s+than|over|at\s+least|multiple|several)\s+(?P<n>\d+)?\s*(?:delivery\s+)?attempts?"
        r"|failed\s+(?:delivery\s+)?attempts?|(?<!\w)attempted", attempts)

    add(r"weekend|saturday|sunday",
        lambda m: Rule("Delivered on Weekend",
                       lambda r: _yn(r.delivered and r.delivered_at is not None and r.delivered_at.weekday() >= 5),
                       "delivered on a Saturday or Sunday"))
    add(r"out\s+for\s+delivery",
        lambda m: Rule("Out for Delivery Now", lambda r: _yn(r.status == M.OUT_FOR_DELIVERY), "currently out for delivery"))

    def weight(m):
        n = float(m.group("n"))
        lbs = n * 2.20462 if (m.group("u") or "").lower().startswith("k") else n
        over = m.group("w").lower() in ("more", "over", "greater", "heavier", "above")
        return Rule(f"Weight {'Over' if over else 'Under'} {m.group('n')} {m.group('u') or 'lbs'}",
                    lambda r: "Unknown" if _weight_lbs(r) is None else _yn(_weight_lbs(r) > lbs if over else _weight_lbs(r) < lbs),
                    f"weight {'over' if over else 'under'} {n}")
    add(r"weigh\w*\s+(?P<w>more|over|greater|heavier|above|less|under|lighter|below)\s+(?:than\s+)?"
        r"(?P<n>\d+(?:\.\d+)?)\s*(?P<u>lbs?|pounds?|kgs?|kilograms?)?", weight)

    add(r"(?:no|missing|without)\s+(?:pod|proof\s+of\s+delivery)|(?:pod|proof\s+of\s+delivery)\s+(?:is\s+)?(?:missing|not\s+available)",
        lambda m: Rule("POD Missing", lambda r: _yn(not (r.pod and r.pod.files)) if r.delivered else "No",
                       "delivered but no POD file could be produced"))
    add(r"not\s+found|invalid|no\s+record|bad\s+(?:tracking\s+)?numbers?|lookup\s+errors?|\berrors?\b",
        lambda m: Rule("Lookup Problem", lambda r: _yn(r.status in (M.NOT_FOUND, M.ERROR)), "the carrier lookup failed"))

    def carrier(m):
        c = {"ups": M.UPS, "fedex": M.FEDEX, "usps": M.USPS}[m.group("c").lower().replace(" ", "")]
        return Rule(f"Carrier Is {c}", lambda r: _yn(r.carrier == c), f"carrier is {c}")
    add(r"\b(?:only|just|all|which\s+are|that\s+are)\b.{0,20}?\b(?P<c>ups|fed\s?ex|usps)\b", carrier)

    def service(m):
        s = m.group("s").lower()
        return Rule(f"Service Contains '{s}'", lambda r: _yn(s.replace(" ", "") in (r.service or "").lower().replace(" ", "")),
                    f"service includes '{s}'")
    add(r"(?:service|shipped\s+via|sent\s+via|using|by)\s+(?:is\s+)?(?P<s>overnight|next\s+day|2\s?day|second\s+day|ground"
        r"(?:\s+advantage)?|express|priority(?:\s+mail)?|home\s+delivery|first[\s-]class)", service)
    return out


def _late(r: M.TrackingResult) -> str:
    est = _est_date(r)
    if est is None:
        return "Unknown"
    if r.delivered and r.delivered_at:
        return _yn(_dday(r.delivered_at) > est)
    if not r.delivered:
        return _yn(date.today() > est)
    return "Unknown"


def _exception(r: M.TrackingResult) -> str:
    if r.status in (M.EXCEPTION, M.RETURNED):
        return "Yes"
    pat = re.compile(r"exception|delay|unable|attempt|damag|refused|weather|incorrect address|alert|held", re.I)
    return _yn(any(pat.search(e.description) for e in r.events))


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.?!;])\s+|\n+|\s*•\s*|^\s*[-*]\s+", text.strip(), flags=re.M)
    return [p.strip(" -*\t") for p in parts if p and p.strip(" -*\t.")]


def interpret(text: str, today: date | None = None) -> tuple[list[Rule], list[str]]:
    """Return (rules, sentences that were not understood)."""
    today = today or date.today()
    rules: list[Rule] = []
    seen: set[str] = set()
    unrecognised: list[str] = []
    matchers = _matchers(today)
    for sentence in split_sentences(text or ""):
        hit = False
        taken: list[tuple[int, int]] = []
        for rx, factory in matchers:
            for m in rx.finditer(sentence):
                if any(m.start() < e and s < m.end() for s, e in taken):
                    continue
                rule = factory(m)
                if rule is None:
                    continue
                taken.append((m.start(), m.end()))
                hit = True
                if rule.column not in seen:
                    seen.add(rule.column)
                    rules.append(rule)
        if not hit:
            unrecognised.append(sentence)
    return rules, unrecognised
