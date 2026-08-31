"""Pull the *explicit* instructions out of a free-text analysis objective.

``objective.py`` turns the objective into signal terms / focus areas so the run
can be *steered*.  This module is stricter: when the user spells out a numbered
list of deliverables, an exact output shape ("one summary table with the
technician + round as column headers"), or their own productive/unproductive
rules, those must be honoured literally rather than approximated.  Nothing here is
time-study specific - it only recognises structure that any objective might carry
and leaves everything else untouched.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_SPLIT = re.compile(r"\s*(?:,|;|/|\band\b|\bor\b|&|\bplus\b)\s*", re.I)


def _split_list(text: str) -> list[str]:
    out: list[str] = []
    for piece in _SPLIT.split(text or ""):
        piece = piece.strip().strip(".:;- \t\"'()")
        # keep short, name-like fragments only
        if piece and len(piece) <= 40 and not piece.lower().startswith(("include", "type", "task")):
            out.append(piece)
    # de-dupe, preserve order, keep casing
    seen: set[str] = set()
    uniq: list[str] = []
    for x in out:
        k = x.lower()
        if k not in seen:
            seen.add(k)
            uniq.append(x)
    return uniq


@dataclass
class Directives:
    requested_items: list[str] = field(default_factory=list)   # the user's numbered deliverables
    nonprod_tasks: list[str] = field(default_factory=list)      # explicit unproductive task names
    nonprod_types: list[str] = field(default_factory=list)      # explicit unproductive activity types
    want_summary_pivot: bool = False        # "one summary table with all metrics"
    want_per_item_tables: bool = False      # "a table for each item individually"
    pivot_columns: str = ""                 # "resource_round" when that layout is spelled out
    sum_rows_and_cols: bool = False         # "sum all rows and columns"
    rank_resources: bool = False            # "rank each resource against the others"

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def parse(objective: str) -> Directives:
    d = Directives()
    text = (objective or "").strip()
    if not text:
        return d
    low = text.lower()

    d.requested_items = _numbered_items(text)
    d.want_per_item_tables = bool(
        re.search(r"table\s+(?:for|breaking out)\s+each", low)
        or "each of the items requested individually" in low
        or "table for each" in low
    )
    d.want_summary_pivot = bool(
        re.search(r"(one\s+)?summary\s+table", low)
        or "all metrics" in low and "table" in low
        or "one table that includes all" in low
    )
    d.sum_rows_and_cols = bool(re.search(r"sum(?:ming)?\s+(?:all\s+)?(?:the\s+)?rows?\s+and\s+columns?", low)
                              or "total all rows and columns" in low)
    d.rank_resources = bool(re.search(r"\brank(?:ing)?\b", low) and
                            re.search(r"resource|technician|agent|person|each\b", low))

    # column-header layout, e.g. "technician name Round 1 and technician name round 2
    # as the column headers"
    if re.search(r"column\s+headers?", low) and re.search(r"round\s*\d", low) \
            and re.search(r"technician|resource|agent|name|person", low):
        d.pivot_columns = "resource_round"
        d.want_summary_pivot = True

    _parse_nonprod(low, d)
    return d


def _numbered_items(text: str) -> list[str]:
    """Slice a "1. ...  2. ...  3 ..." enumeration into its parts.  Accepts
    ``1.`` / ``1)`` / ``1:`` and a bare ``5`` followed by 2+ spaces (a common
    typo).  Requires a run of at least three markers that starts at 1 or 2 so we
    don't mistake "24/7" or a year for a list."""
    marks: list[tuple[int, int, int]] = []
    for m in re.finditer(r"(?:(?<=[\s(\[])|^)(\d{1,2})(?:[.)\]:]|\s{2,})\s*(?=[A-Za-z\"'])", text):
        marks.append((m.start(), m.end(), int(m.group(1))))
    if len(marks) < 3:
        return []

    best: list[tuple[int, int, int]] = []
    run: list[tuple[int, int, int]] = []
    expect = None
    for mk in marks:
        n = mk[2]
        if expect is None:
            if n in (1, 2):
                run = [mk]
                expect = n + 1
            continue
        if n == expect or n == expect + 1:      # allow a single skipped number
            run.append(mk)
            expect = n + 1
        else:
            if len(run) > len(best):
                best = run
            run = [mk] if n in (1, 2) else []
            expect = (n + 1) if run else None
    if len(run) > len(best):
        best = run
    if len(best) < 3:
        return []

    items: list[str] = []
    for i, (_s, e, _n) in enumerate(best):
        end = best[i + 1][0] if i + 1 < len(best) else len(text)
        frag = re.sub(r"\s+", " ", text[e:end].strip().strip(".,;: \t"))
        if not frag:
            continue
        # a numbered item is a short phrase; the trailing prose that follows the
        # last number ("Based on these metrics, ... Finally, review ...") is not
        # part of it - cut at the first sentence boundary.
        head = re.split(r"(?<=[.;])\s+(?=[A-Z])", frag)[0].strip(".;, ")
        items.append(head if len(head) >= 10 else frag)
    return items


def _parse_nonprod(low: str, d: Directives) -> None:
    """"...consider unproductive Tasks to include Lunch, Break, and Types of
    Scheduled Break and Unscheduled Break." -> tasks=[Lunch, Break],
    types=[Scheduled Break, Unscheduled Break]."""
    m = re.search(
        r"unproductive\s+(?:tasks?|activit\w*)\s+(?:to\s+)?(?:include|are|:)\s*(.+?)"
        r"(?:\.|$|\bfinally\b|\bthen\b|\breview\b)", low, re.S)
    if not m:
        m = re.search(r"unproductive[^.]*?\binclude\s+(.+?)(?:\.|$)", low, re.S)
    if not m:
        return
    blob = m.group(1)
    types_part = ""
    tm = re.search(r"\btypes?\s+of\s+(.+)$", blob, re.S)
    if tm:
        types_part = tm.group(1)
        blob = blob[:tm.start()]
    d.nonprod_tasks = _split_list(blob)
    d.nonprod_types = _split_list(types_part)
