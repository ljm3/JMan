"""Turn the free-text analysis objective into a structured AnalysisPlan using the
local model, and use the model again to write the answers from the computed
results.  Both steps degrade to deterministic fallbacks when the model is absent.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

_NARR_SYSTEM = (
    "You are a precise operations analyst. You are given an objective and the "
    "already-computed results tables. Write the findings. Use ONLY the numbers "
    "provided; never invent figures. Be concrete and brief. Plain text with short "
    "markdown headings is fine."
)

_COACH_SYSTEM = (
    "You review time-study observation notes. For each note give: category = "
    "'Corrective action' (a clear performance/process failure) or 'Coaching' (an "
    "improvement opportunity) or 'None'; bucket = 'disciplinary' (repeated or "
    "serious failure - missed calls, not logged in, walked away, abandoned work), "
    "'issue' (a technical or process problem hit during the work) or 'coaching' (a "
    "habit to correct); and a one-sentence actionable recommendation. Reply with "
    "ONE JSON object: {\"items\":[{\"i\":int,\"category\":str,\"bucket\":str,"
    "\"recommendation\":str}]}. Use only the note text."
)
_NOTES_NARR_SYSTEM = (
    "You are an operations supervisor writing a short, factual round-over-round "
    "read on one technician from time-study observation notes. 2-4 sentences: did "
    "performance improve or degrade between rounds, the main recurring issues, and "
    "whether any of it rises to disciplinary attention. Use only what the notes and "
    "figures say; no invented numbers."
)


@dataclass
class AnalysisPlan:
    restated_goal: str = ""
    resource_dimension: str = "the person/agent each file documents"
    expected_rounds_min: int = 2
    group_dims: list[str] = field(default_factory=lambda: ["task", "type", "round"])
    metrics: list[dict] = field(default_factory=list)
    ranking: dict = field(default_factory=lambda: {"by": "productive_pct", "direction": "desc",
                                                   "label": "productive time %"})
    coaching: dict = field(default_factory=lambda: {"notes_column": "Notes", "cues": [],
                                                    "categories": ["Corrective action", "Coaching"]})
    sections: list[str] = field(default_factory=lambda: [
        "resources_rounds", "tasks", "types", "class", "ranking", "crosstabs",
        "coaching", "answers"])
    questions: list[str] = field(default_factory=list)
    source: str = "heuristic"          # "local-model" | "heuristic"
    raw: dict = field(default_factory=dict)
    # explicit instructions lifted verbatim from the objective (see analyze.directives)
    requested_items: list[str] = field(default_factory=list)
    nonprod_tasks: list[str] = field(default_factory=list)
    nonprod_types: list[str] = field(default_factory=list)
    want_summary_pivot: bool = True
    want_per_item_tables: bool = True

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        return d


# --------------------------------------------------------------------- context
def build_context(objective: str, facts, resource_index, records: list[dict]) -> str:
    """A COMPACT view of the corpus for the planner - kept short so the local
    model's prefill stays fast."""
    return "\n".join([
        f"DATA: {len(records)} spreadsheet files, {len(resource_index.by_resource)} "
        f"unique resources, rounds {facts.rounds}, {len(facts.rows)} activity lines.",
        f"Each line has: task ({len(facts.tasks)} distinct, e.g. "
        f"{', '.join(facts.tasks[:8])}), activity type ({len(facts.types)} distinct, e.g. "
        f"{', '.join(facts.types[:8])}), duration in minutes, a status, and a free-text "
        f"Notes cell ({'present' if facts.notes_column_present else 'absent'}).",
        "Each file's resource and round come from its filename.",
    ])


_SECTION_CHOICES = ["resources_rounds", "files", "tasks", "types", "class",
                    "ranking", "crosstabs", "coaching", "answers"]
_RANK_MAP = {
    "productive percentage": "productive_pct", "productive %": "productive_pct",
    "productive pct": "productive_pct", "percentage": "productive_pct",
    "productive minutes": "productive_min", "productive time": "productive_min",
    "tasks logged": "tasks_logged", "number of tasks": "tasks_logged",
    "task count": "tasks_logged", "average task": "avg_task_min",
    "average task length": "avg_task_min", "avg task": "avg_task_min",
}
_MICRO = ("You answer with a single short line, no explanation, no markdown, no apology.")
# always emit these - they are the resource-identity core of any run
_CORE_SECTIONS = ["resources_rounds", "files", "answers"]
_OPTIONAL_SECTIONS = ["tasks", "types", "class", "ranking", "crosstabs", "coaching"]
_REFUSAL = ("i'm sorry", "i am sorry", "cannot", "can't help", "as an ai",
            "unclear", "not clear", "please provide", "please feel free",
            "the instructions", "i don't", "i do not", "unable to assist")


def _is_refusal(text: str) -> bool:
    t = (text or "").strip().lower()
    return not t or any(r in t for r in _REFUSAL)


def build_plan(llm, session, objective: str, context: str) -> AnalysisPlan:
    """Small, targeted asks (a big JSON schema is unreliable + slow on a local
    4B model); the heuristic plan is the base and every model answer is validated
    before it is allowed to change anything."""
    plan = _heuristic_plan(objective)          # base; the model only refines it
    if llm is None:
        session.detail(f"Plan (heuristic): sections {', '.join(plan.sections)}; "
                       f"rank by {plan.ranking.get('by')}; expected rounds >= "
                       f"{plan.expected_rounds_min}")
        return plan

    plan.source = "local-model"
    used = []
    ask = f"OBJECTIVE:\n{objective}\n\n{context}\n\n"
    try:
        # 1. restated goal (one sentence) - only accept a plain declarative line
        g = llm.complete(_MICRO, ask + "In ONE sentence, restate what this analysis "
                         "must produce. Start with a verb.", max_new_tokens=100).strip()
        g = g.split("\n")[0].strip(' "\'')
        if 15 < len(g) < 320 and not _is_refusal(g):
            plan.restated_goal = g
            used.append("goal")

        # 2. which optional report sections
        s = llm.complete(_MICRO, ask + "Reply with a comma-separated subset (nothing "
                         f"else) of: {', '.join(_OPTIONAL_SECTIONS)}", max_new_tokens=60)
        picked = [x for x in _split_list(s) if x in _OPTIONAL_SECTIONS]
        if picked and not _is_refusal(s):
            plan.sections = _CORE_SECTIONS[:2] + picked + ["answers"]
            used.append("sections")

        # 3. ranking basis
        r = llm.complete(_MICRO, ask + "Rank the resources by which one measure - "
                         "productive percentage, productive minutes, tasks logged, or "
                         "average task length?", max_new_tokens=30).strip().lower()
        for phrase, key in _RANK_MAP.items():
            if phrase in r:
                plan.ranking = {**plan.ranking, "by": key, "label": phrase}
                used.append("ranking")
                break

        # 4. coaching cue phrases - accept only short lexical tokens
        c = llm.complete(_MICRO, ask + "List up to 10 short lowercase words or "
                         "2-3 word phrases that, in an observation note, would signal "
                         "a coaching or corrective-action issue. Comma-separated only.",
                         max_new_tokens=90)
        if not _is_refusal(c):
            cues = []
            for x in _split_list(c, lower=True):
                if 3 <= len(x) <= 28 and len(x.split()) <= 3 and not _is_refusal(x) \
                        and all(ch.isalpha() or ch in " -'" for ch in x):
                    cues.append(x)
            if cues:
                plan.coaching = {**plan.coaching, "cues": cues[:10]}
                used.append("cues")

        plan.source = f"local-model ({', '.join(used)})" if used else "heuristic (model gave nothing usable)"
        session.detail(f"Plan ({plan.source}): {plan.restated_goal[:180]}")
        session.detail(f"Plan sections: {', '.join(plan.sections)}; rank by "
                       f"{plan.ranking.get('by')}; coaching cues +{len(plan.coaching.get('cues', []))}")
        plan.raw = {"used": used, "sections": plan.sections, "ranking": plan.ranking,
                    "coaching_cues": plan.coaching.get("cues")}
        return plan
    except Exception as exc:  # noqa: BLE001
        session.warn(f"Local-model planning failed ({exc}); using the heuristic plan.")
        plan.source = "heuristic (model error)"
        return plan


def _split_list(text: str, lower: bool = False) -> list[str]:
    import re
    text = re.split(r"[\n:]", text.strip())[-1]
    parts = re.split(r"[,;/]|\band\b", text)
    out = []
    for p in parts:
        p = p.strip().strip(".\"'`-*[] ")
        if lower:
            p = p.lower()
        if p:
            out.append(p)
    return out


def _heuristic_plan(objective: str) -> AnalysisPlan:
    from . import directives as directives_mod

    o = (objective or "").lower()
    p = AnalysisPlan()
    dr = directives_mod.parse(objective)
    p.requested_items = dr.requested_items
    p.nonprod_tasks = dr.nonprod_tasks
    p.nonprod_types = dr.nonprod_types
    p.want_summary_pivot = dr.want_summary_pivot or True   # the pivot is always useful
    p.want_per_item_tables = dr.want_per_item_tables or bool(dr.requested_items)
    p.restated_goal = ("Break the folder down by unique resource and study round, aggregate "
                       "task/type counts, time and averages by round and overall, rank the "
                       "resources against each other, and flag coaching / corrective-action "
                       "instances from the notes.")
    m = None
    import re as _re
    mm = _re.search(r"at least\s+(\d+)|>=?\s*(\d+)\s*round|(\d+)\s*rounds?", o)
    if mm:
        for g in mm.groups():
            if g:
                m = int(g)
                break
    p.expected_rounds_min = m or 2
    secs = ["resources_rounds", "files"]
    if "task" in o:
        secs.append("tasks")
    if "type" in o or "activit" in o:
        secs.append("types")
    secs.append("class")
    if "rank" in o or "comparativ" in o or "compare" in o:
        secs.append("ranking")
    if "cross" in o or "matrix" in o:
        secs.append("crosstabs")
    if "note" in o or "coach" in o or "corrective" in o:
        secs.append("coaching")
    secs.append("answers")
    p.sections = secs
    if "average" in o and ("productive" in o or "rank" in o):
        p.ranking = {"by": "productive_pct", "direction": "desc", "label": "productive time %"}
    p.questions = [
        "Which resources have fewer than the expected number of rounds?",
        "What are the task and activity-type totals and averages per round and overall?",
        "How do the resources rank against each other?",
        "Which notes indicate a need for coaching or corrective action, and for whom?",
    ]
    return p


# --------------------------------------------------------------------- coaching
def _fallback_bucket(c: dict) -> str:
    from .execute_plan import bucket_for
    return bucket_for(c)


def _apply_keyword_review(candidates: list[dict]) -> None:
    for c in candidates:
        c["category"] = _guess_category(c["trigger"], c["note"])
        c["recommendation"] = _default_reco(c)
        c["bucket"] = _fallback_bucket(c)


def refine_coaching(llm, session, candidates: list[dict]) -> list[dict]:
    if not candidates:
        return []
    if llm is None:
        _apply_keyword_review(candidates)
        return candidates
    # batch (keep the prompt + the generation bounded - CPU is slow)
    batch = candidates[:24]
    payload = [{"i": i, "resource": c["resource"], "task": c["task"],
                "note": c["note"][:240]} for i, c in enumerate(batch)]
    user = ("Notes to review (JSON list). Categorise each.\n"
            + json.dumps(payload, ensure_ascii=False))
    _valid_buckets = {"disciplinary", "issue", "coaching"}
    try:
        raw = llm.complete_json(_COACH_SYSTEM, user, max_new_tokens=1400)
        items = {int(it["i"]): it for it in raw.get("items", []) if "i" in it}
        for i, c in enumerate(batch):
            it = items.get(i)
            if it and str(it.get("category", "")).lower() not in ("none", ""):
                c["category"] = it.get("category", "Coaching")
                c["recommendation"] = str(it.get("recommendation", "")).strip() or _default_reco(c)
            elif it and str(it.get("category", "")).lower() == "none":
                c["category"] = "Informational"
                c["recommendation"] = ""
            else:
                c["category"] = _guess_category(c["trigger"], c["note"])
                c["recommendation"] = _default_reco(c)
            b = str((it or {}).get("bucket", "")).strip().lower()
            c["bucket"] = b if b in _valid_buckets else _fallback_bucket(c)
        for c in candidates[24:]:
            c["category"] = _guess_category(c["trigger"], c["note"])
            c["recommendation"] = _default_reco(c)
            c["bucket"] = _fallback_bucket(c)
        session.detail(f"Local model reviewed {len(batch)} flagged note(s): "
                       f"buckets {_bucket_tally(candidates)}.")
    except Exception as exc:  # noqa: BLE001
        session.warn(f"Local-model coaching review failed ({exc}); using keyword categories.")
        _apply_keyword_review(candidates)
    return candidates


def _bucket_tally(cands: list[dict]) -> str:
    t: dict[str, int] = {}
    for c in cands:
        t[c.get("bucket", "?")] = t.get(c.get("bucket", "?"), 0) + 1
    return ", ".join(f"{k}={v}" for k, v in sorted(t.items())) or "none"


def write_resource_notes_narrative(llm, session, results, facts) -> dict:
    """One short round-over-round paragraph per resource.  Model-written when the
    notes step is on and the model answers; otherwise a compact deterministic
    sentence from the computed verdict + bucket counts."""
    per = getattr(results, "notes_by_resource", {}) or {}
    if not per:
        return {}
    cand_by_res: dict[str, list] = {}
    for c in results.coaching_candidates:
        cand_by_res.setdefault(c["resource_key"], []).append(c)

    out: dict[str, str] = {}
    for key, info in per.items():
        det = (f"{info['resource']}: {info['verdict']}. "
               f"{info['issues']} issue(s), {info['coaching']} coaching item(s), "
               f"{info['disciplinary']} possible disciplinary flag(s) across the notes.")
        if llm is None:
            out[key] = det
            continue
        notes = cand_by_res.get(key, [])
        if not notes:
            out[key] = det
            continue
        lines = "\n".join(f"- r{c['round']} [{c.get('bucket', '?')}] {c['task']}: {c['note'][:180]}"
                          for c in notes[:20])
        user = (f"TECHNICIAN: {info['resource']}\n"
                f"Productive % by round: {info['productive_pct_by_round']}\n"
                f"Flagged notes by round: {info['flags_by_round']}\n"
                f"NOTES:\n{lines}\n\nWrite the round-over-round read.")
        try:
            txt = llm.complete(_NOTES_NARR_SYSTEM, user, max_new_tokens=220).strip()
            out[key] = txt if txt and not _is_refusal(txt) else det
        except Exception:  # noqa: BLE001
            out[key] = det
    session.detail(f"Wrote round-over-round notes read for {len(out)} resource(s).")
    return out


def _guess_category(trigger: str, note: str) -> str:
    hard = ("not logged in", "not log in", "abandoned", "missed", "should have been trained",
            "should've been trained", "skipped", "never gets response", "not started")
    return "Corrective action" if any(h in (trigger + " " + note.lower()) for h in hard) else "Coaching"


def _default_reco(c: dict) -> str:
    return (f"Review the '{c['task']}' entry at line {c['line']} with {c['resource']}; "
            "confirm expected workflow and document the correction.")


# --------------------------------------------------------------------- narrative
def write_narrative(llm, session, objective: str, plan: AnalysisPlan, results) -> dict:
    compact = _compact_results(results)
    if llm is None:
        return {"source": "template", "markdown": _template_narrative(objective, plan, results)}
    user = (f"OBJECTIVE:\n{objective}\n\nYOUR PLAN:\n{plan.restated_goal}\n\n"
            f"COMPUTED RESULTS (authoritative - use these numbers):\n{compact}\n\n"
            "Write: (1) a short restatement of what was asked; (2) answers to each "
            "question in the plan; (3) the resource ranking with one line of "
            "commentary; (4) a coaching / corrective-action summary naming each "
            "resource and the specific instances. Do not invent numbers.")
    try:
        text = llm.complete(_NARR_SYSTEM,
                            user + f"\n\nPLAN QUESTIONS:\n- " + "\n- ".join(plan.questions),
                            max_new_tokens=900)
        if _is_refusal(text) or len(text.strip()) < 120:
            session.warn("Local-model narrative was empty/refused; using a templated summary.")
            return {"source": "template (model refused)",
                    "markdown": _template_narrative(objective, plan, results)}
        session.detail(f"Local model wrote the objective answers ({len(text)} chars).")
        return {"source": "local-model", "markdown": text.strip()}
    except Exception as exc:  # noqa: BLE001
        session.warn(f"Local-model narrative failed ({exc}); using a templated summary.")
        return {"source": "template", "markdown": _template_narrative(objective, plan, results)}


def _compact_results(results) -> str:
    out = [f"Grand totals: {json.dumps(results.grand_totals)}"]
    for name in ("resources_rounds", "task_totals", "type_totals", "class_by_round",
                 "resource_ranking"):
        t = results.tables.get(name)
        if not t:
            continue
        out.append(f"\n[{name}] columns={t['columns']}")
        for row in t["rows"][:40]:
            out.append("  " + " | ".join(str(x) for x in row))
    if results.coaching_candidates:
        out.append(f"\n[coaching_candidates] {len(results.coaching_candidates)} flagged notes:")
        for c in results.coaching_candidates[:60]:
            out.append(f"  {c['resource']} r{c['round']} L{c['line']} [{c.get('category', c['trigger'])}] "
                       f"{c['task']}: {c['note'][:160]}")
    return "\n".join(out)


def _template_narrative(objective: str, plan: AnalysisPlan, results) -> str:
    g = results.grand_totals
    L = ["## What was asked", "", objective.strip(), "",
         "## Understanding", "", plan.restated_goal, "",
         "## Key figures", "",
         f"- Files: {g.get('files')}  |  unique resources: {g.get('resources')}  |  rounds: {g.get('rounds')}",
         f"- Activity lines: {g.get('activity_lines')}  |  total minutes: {g.get('total_minutes')}  "
         f"|  avg minutes/line: {g.get('avg_minutes')}",
         f"- Productive: {g.get('productive_minutes')} min ({g.get('productive_pct')}%)  |  "
         f"non-productive: {g.get('nonproductive_minutes')} min", ""]
    under = [r for r in results.tables.get("resources_rounds", {}).get("rows", [])
             if str(r[3]).startswith("NO")]
    if under:
        L += ["## Resources below the expected round count", ""]
        L += [f"- {r[0]}: rounds {r[1]} ({r[2]})" for r in under] + [""]
    L += ["## Resource ranking", ""]
    for r in results.ranking:
        L.append(f"{r['rank']}. {r['resource']} - {r['productive_pct']}% productive, "
                 f"{r['productive_min']} prod min, {r['tasks_logged']} tasks")
    L.append("")
    if results.coaching_candidates:
        L += ["## Coaching / corrective action", ""]
        by_res: dict[str, list] = {}
        for c in results.coaching_candidates:
            by_res.setdefault(c["resource"], []).append(c)
        for res_name, items in by_res.items():
            L.append(f"### {res_name} ({len(items)})")
            for c in items[:25]:
                L.append(f"- r{c['round']} line {c['line']} [{c.get('category', 'Coaching')}] "
                         f"{c['task']}: {c['note'][:200]}")
            L.append("")
    return "\n".join(L)
