"""Flatten the structured spreadsheet tables into one activity fact table:
one row per logged task line, tagged with the file's resource + round.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_NONPROD_TYPE = re.compile(r"break|lunch|non[-\s]?prod|unproductive|unavailable|time off|pto", re.I)
_NONPROD_TASK = re.compile(r"^\s*(break|lunch|meal|rest|personal|afk)\s*$", re.I)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _find_col(columns: list[str], *needles: str) -> str | None:
    low = {c: c.lower().replace("\n", " ") for c in columns}
    for want in needles:
        for c, cl in low.items():
            if want == cl.strip():
                return c
    for want in needles:
        for c, cl in low.items():
            if want in cl:
                return c
    return None


@dataclass
class Facts:
    rows: list[dict] = field(default_factory=list)          # activity lines
    file_context: dict[str, dict] = field(default_factory=dict)  # rel -> summary cells
    tasks: list[str] = field(default_factory=list)
    types: list[str] = field(default_factory=list)
    rounds: list[int] = field(default_factory=list)
    resources: list[str] = field(default_factory=list)
    notes_column_present: bool = False
    nonprod_tasks: list[str] = field(default_factory=list)   # explicit rule, if the objective gave one
    nonprod_types: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"row_count": len(self.rows), "tasks": self.tasks, "types": self.types,
                "rounds": self.rounds, "resources": self.resources,
                "notes_column_present": self.notes_column_present,
                "nonprod_tasks": self.nonprod_tasks, "nonprod_types": self.nonprod_types,
                "file_context": self.file_context}


def classify(task: str, type_: str,
             nonprod_tasks: "list[str] | None" = None,
             nonprod_types: "list[str] | None" = None) -> str:
    """Non-Productive vs Productive.  When the objective spelled out its own list
    of unproductive tasks / activity types, that list wins (exact, case- and
    punctuation-insensitive); otherwise fall back to the built-in regexes."""
    if nonprod_tasks or nonprod_types:
        nt = {_norm(x) for x in (nonprod_tasks or [])}
        ny = {_norm(x) for x in (nonprod_types or [])}
        if _norm(task) in nt or _norm(type_) in ny:
            return "Non-Productive"
        return "Productive"
    if _NONPROD_TYPE.search(type_ or "") or _NONPROD_TASK.search(task or ""):
        return "Non-Productive"
    return "Productive"


def build(records: list[dict], resource_index,
          nonprod_tasks: "list[str] | None" = None,
          nonprod_types: "list[str] | None" = None) -> Facts:
    f = Facts(nonprod_tasks=list(nonprod_tasks or []), nonprod_types=list(nonprod_types or []))
    tset, tyset, rset, resset = set(), set(), set(), set()

    for rec in records:
        rel = rec.get("rel", "")
        fr = resource_index.by_file.get(rel)
        canon = (resource_index.by_resource.get(fr.resource_key, {}).get("canonical")
                 if fr else None) or (fr.canonical if fr else "(unknown)")
        meta = rec.get("meta") or {}
        tables = meta.get("record_tables") or []
        ctx = meta.get("cell_context") or {}
        if fr:
            f.file_context[rel] = {
                "resource": canon, "resource_key": fr.resource_key,
                "round": fr.round, "variant": fr.variant,
                **{k: ctx[k] for k in ctx},
            }

        if not tables:
            continue
        # choose the table that looks like an activity log
        def tbl_score(t):
            cl = " ".join(t["columns"]).lower()
            return (("task" in cl) + ("type" in cl) + ("duration" in cl)
                    + ("notes" in cl) + t["row_count"] / 10000.0)
        tbl = max(tables, key=tbl_score)
        cols = tbl["columns"]
        c_task = _find_col(cols, "task", "activity")
        c_type = _find_col(cols, "type", "category", "activity type")
        c_dur = _find_col(cols, "duration (minutes)", "duration minutes", "minutes", "duration min")
        c_notes = _find_col(cols, "notes", "comment", "comments", "observation")
        c_status = _find_col(cols, "status")
        c_line = _find_col(cols, "line #", "line", "#")
        c_ticket = _find_col(cols, "ticket number", "ticket", "case")
        if c_notes:
            f.notes_column_present = True

        for row in tbl["rows"]:
            task = str(row.get(c_task, "") or "").strip() if c_task else ""
            type_ = str(row.get(c_type, "") or "").strip() if c_type else ""
            note = str(row.get(c_notes, "") or "").strip() if c_notes else ""
            dur = row.get(c_dur) if c_dur else None
            dur = round(float(dur), 2) if isinstance(dur, (int, float)) else None
            status = str(row.get(c_status, "") or "").strip() if c_status else ""
            if not task and not type_ and dur is None and not note:
                continue
            if not task and not type_ and (dur is None or dur == 0):
                continue
            fact = {
                "file": rel,
                "resource": canon,
                "resource_key": fr.resource_key if fr else "unknown",
                "round": fr.round if fr else None,
                "line": row.get(c_line) if c_line else None,
                "task": task or "(blank)",
                "type": type_ or "(blank)",
                "activity_class": classify(task, type_, f.nonprod_tasks, f.nonprod_types),
                "duration_min": dur,
                "status": status,
                "ticket": str(row.get(c_ticket, "") or "").strip() if c_ticket else "",
                "notes": note,
            }
            f.rows.append(fact)
            if task:
                tset.add(task)
            if type_:
                tyset.add(type_)
            if fr and fr.round is not None:
                rset.add(fr.round)
            if fr:
                resset.add(canon)

    f.tasks = sorted(tset)
    f.types = sorted(tyset)
    f.rounds = sorted(rset)
    f.resources = sorted(resset)
    return f
