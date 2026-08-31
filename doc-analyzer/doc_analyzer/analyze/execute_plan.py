"""Compute the metrics an AnalysisPlan asks for over the activity fact table."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_DEF_CUES = [
    "not logged in", "not log in", "still not logged", "without logging",
    "unable to", "does not check", "did not check", "not check",
    "no eyes on", "missed", "presumed break", "presumed a continued break",
    "inactivity", "autotimed to offline", "auto offline", "auto to offline",
    "still offline", "goes offline", "went offline", "lingering", "linger",
    "should have been trained", "should've been trained", "skipped",
    "stare at it", "very still", "remains very still", "no movement",
    "no further movement", "not started", "abandoned", "lack of aware",
    "not aware", "waiting on response", "never gets response", "idle", "idles",
    "idled", "sat idle", "distracted", "late", "no activity", "afk",
    "did not see", "left pending", "left open", "wraps up late", "closed late",
]


@dataclass
class PlanResults:
    tables: dict[str, dict] = field(default_factory=dict)   # name -> {columns, rows, note}
    grand_totals: dict = field(default_factory=dict)
    ranking: list[dict] = field(default_factory=list)
    coaching_candidates: list[dict] = field(default_factory=list)
    metrics: list[dict] = field(default_factory=list)       # results of plan.metrics interpreter
    notes_by_resource: dict = field(default_factory=dict)   # per-resource notes review
    requested_items: list[dict] = field(default_factory=list)  # user's numbered deliverables

    def add(self, name: str, columns: list[str], rows: list[list], note: str = "") -> None:
        self.tables[name] = {"columns": columns, "rows": rows, "note": note}

    def to_dict(self) -> dict:
        return {"tables": self.tables, "grand_totals": self.grand_totals,
                "ranking": self.ranking, "coaching_candidates": self.coaching_candidates,
                "metrics": self.metrics, "notes_by_resource": self.notes_by_resource,
                "requested_items": self.requested_items}


def _stats(rows, value="duration_min"):
    n = len(rows)
    vals = [r[value] for r in rows if isinstance(r.get(value), (int, float))]
    s = sum(vals)
    return {"count": n, "sum": round(s, 2), "avg": round(s / len(vals), 2) if vals else 0.0,
            "timed": len(vals)}


def _group(rows, keyfn):
    out: dict = {}
    for r in rows:
        out.setdefault(keyfn(r), []).append(r)
    return out


def _min_sum(rows) -> float:
    return round(sum(r["duration_min"] for r in rows
                     if isinstance(r.get("duration_min"), (int, float))), 2)


def _avg_min(rows) -> float:
    vals = [r["duration_min"] for r in rows if isinstance(r.get("duration_min"), (int, float))]
    return round(sum(vals) / len(vals), 2) if vals else 0.0


def _ordered_resources(resource_index, ranking) -> list[tuple[str, str, list[int]]]:
    """(resource_key, canonical, round_list) - in ranking order when we have one,
    else alphabetical by name.  Stable and deterministic either way."""
    nodes = resource_index.by_resource
    if ranking:
        order = [(r["resource_key"], r["resource"]) for r in ranking if r["resource_key"] in nodes]
    else:
        order = sorted(((k, n["canonical"]) for k, n in nodes.items()), key=lambda kv: kv[1].lower())
    out = []
    for key, canon in order:
        rounds = list(nodes[key].get("round_list") or [])
        out.append((key, canon, rounds))
    return out


def _per_resource_dim(rows, ordered, dim: str) -> list[list]:
    table: list[list] = []
    for key, canon, rounds in ordered:
        rres = [r for r in rows if r["resource_key"] == key]
        names = sorted({r[dim] for r in rres})
        for name in names:
            nrows = [r for r in rres if r[dim] == name]
            for rnd in rounds:
                g = [r for r in nrows if r["round"] == rnd]
                if g:
                    table.append([canon, name, rnd, len(g), _min_sum(g), _avg_min(g)])
            table.append([canon, name, "all rounds", len(nrows), _min_sum(nrows), _avg_min(nrows)])
        table.append([canon, "— ALL " + ("TASKS" if dim == "task" else "TYPES") + " —",
                      "all rounds", len(rres), _min_sum(rres), _avg_min(rres)])
    return table


def _summary_pivot(rows, ordered) -> tuple[list[str], list[list]]:
    tasks = sorted({r["task"] for r in rows})
    types = sorted({r["type"] for r in rows})

    columns = ["Metric"]
    col_specs: list[tuple] = []          # (subset_of_rows, is_grand_total)
    for key, canon, rounds in ordered:
        rres = [r for r in rows if r["resource_key"] == key]
        for rnd in rounds:
            columns.append(f"{canon} R{rnd}")
            col_specs.append([r for r in rres if r["round"] == rnd])
        columns.append(f"{canon} Total")
        col_specs.append(rres)
    columns.append("All resources")
    col_specs.append(list(rows))

    def qty_row(label, predicate):
        return [label] + [sum(1 for r in c if predicate(r)) for c in col_specs]

    def totmin_row(label, predicate):
        return [label] + [_min_sum([r for r in c if predicate(r)]) for c in col_specs]

    def avgmin_row(label, predicate):
        return [label] + [_avg_min([r for r in c if predicate(r)]) for c in col_specs]

    def pct_prod(c):
        p = _min_sum([r for r in c if r["activity_class"] == "Productive"])
        n = _min_sum([r for r in c if r["activity_class"] == "Non-Productive"])
        return round(100 * p / (p + n), 1) if (p + n) else 0.0

    body: list[list] = []
    body.append(["── TASKS  (Qty / Total min / Avg min) ──"] + [""] * len(col_specs))
    for t in tasks:
        pred = (lambda r, t=t: r["task"] == t)
        body.append(qty_row(f"{t} · Qty", pred))
        body.append(totmin_row(f"{t} · Total min", pred))
        body.append(avgmin_row(f"{t} · Avg min", pred))
    body.append(["── ACTIVITY TYPES  (Qty / Total min / Avg min) ──"] + [""] * len(col_specs))
    for ty in types:
        pred = (lambda r, ty=ty: r["type"] == ty)
        body.append(qty_row(f"{ty} · Qty", pred))
        body.append(totmin_row(f"{ty} · Total min", pred))
        body.append(avgmin_row(f"{ty} · Avg min", pred))
    body.append(["── PRODUCTIVITY ──"] + [""] * len(col_specs))
    body.append(totmin_row("Productive min", lambda r: r["activity_class"] == "Productive"))
    body.append(totmin_row("Non-Productive min", lambda r: r["activity_class"] == "Non-Productive"))
    body.append(["Productive %"] + [pct_prod(c) for c in col_specs])
    body.append(totmin_row("Total logged min", lambda r: True))
    body.append(qty_row("Activity lines", lambda r: True))
    return columns, body


# ------------------------------------------------------------------ notes summary
_ISSUE_CUES = ("issue", "problem", "error", "unable", "fail", "failed", "failure",
               "missed", "not working", "broken", "stuck", "escalat")
_DISC_CUES = ("not logged in", "not log in", "abandoned", "missed call", "missed this call",
              "should have been trained", "should've been trained", "skipped",
              "never gets response", "not started", "no eyes on", "walked away",
              "left early", "no call no show", "falsif")


def bucket_for(cand: dict) -> str:
    """issue | coaching | disciplinary - deterministic fallback used when the
    local model is not classifying notes."""
    blob = f"{cand.get('trigger', '')} {cand.get('note', '')}".lower()
    cat = str(cand.get("category", "")).lower()
    if cat == "corrective action" or any(h in blob for h in _DISC_CUES):
        return "disciplinary"
    if any(h in blob for h in _ISSUE_CUES):
        return "issue"
    return "coaching"


def build_notes_summary(results: PlanResults, facts, resource_index) -> None:
    """Per-resource: productive-% by round, an improvement / degradation verdict,
    and a count for each of issues / coaching opportunities / disciplinary flags.
    Runs after the notes have been categorised (model or keyword)."""
    rows = facts.rows
    ordered = _ordered_resources(resource_index, results.ranking)
    by_res_cand: dict = {}
    for c in results.coaching_candidates:
        c["bucket"] = c.get("bucket") or bucket_for(c)
        by_res_cand.setdefault(c["resource_key"], []).append(c)

    summary_rows: list[list] = []
    per_resource: dict[str, dict] = {}
    for key, canon, rounds in ordered:
        rres = [r for r in rows if r["resource_key"] == key]
        pct_by_round: dict[int, float] = {}
        for rnd in rounds:
            g = [r for r in rres if r["round"] == rnd]
            p = _min_sum([r for r in g if r["activity_class"] == "Productive"])
            n = _min_sum([r for r in g if r["activity_class"] == "Non-Productive"])
            pct_by_round[rnd] = round(100 * p / (p + n), 1) if (p + n) else 0.0
        cands = by_res_cand.get(key, [])
        flags_by_round: dict[int, int] = {}
        for c in cands:
            flags_by_round[c["round"]] = flags_by_round.get(c["round"], 0) + 1
        n_issue = sum(1 for c in cands if c.get("bucket") == "issue")
        n_coach = sum(1 for c in cands if c.get("bucket") == "coaching")
        n_disc = sum(1 for c in cands if c.get("bucket") == "disciplinary")

        verdict = "n/a"
        if len(rounds) >= 2:
            # compare the first round against the last round that carries a
            # meaningful amount of data (>= 5 activity lines), so a 1-line stub
            # round does not dominate the read
            def _lines(rnd):
                return sum(1 for r in rres if r["round"] == rnd)
            last_round = next((rnd for rnd in reversed(rounds) if _lines(rnd) >= 5), rounds[-1])
            first, last = pct_by_round.get(rounds[0], 0.0), pct_by_round.get(last_round, 0.0)
            d_pct = round(last - first, 1)
            f_first = flags_by_round.get(rounds[0], 0)
            f_last = flags_by_round.get(last_round, 0)
            note = ""
            if f_last > f_first:
                note = f"; flagged notes rose {f_first}->{f_last}"
            elif f_last < f_first:
                note = f"; flagged notes fell {f_first}->{f_last}"
            span = f" R{rounds[0]}->R{last_round}"
            if d_pct >= 3 and n_disc < 3:
                verdict = f"Improved ({d_pct:+} pts productive{span}{note})"
            elif d_pct <= -3:
                verdict = f"Degraded ({d_pct:+} pts productive{span}{note})"
            elif n_disc >= 3:
                verdict = (f"Mixed ({d_pct:+} pts productive{span}; "
                           f"{n_disc} possible disciplinary flags{note})")
            elif f_last > f_first + 2:
                verdict = f"Degraded (productive ~flat, flagged notes {f_first}->{f_last})"
            elif f_first > f_last + 2:
                verdict = f"Improved (productive ~flat, flagged notes {f_first}->{f_last})"
            else:
                verdict = f"Roughly flat ({d_pct:+} pts productive{note})"

        summary_rows.append([
            canon,
            ", ".join(f"R{r}:{pct_by_round.get(r, 0.0)}%" for r in rounds) or "-",
            verdict, n_issue, n_coach, n_disc, len(cands),
        ])
        per_resource[key] = {
            "resource": canon, "rounds": rounds, "productive_pct_by_round": pct_by_round,
            "flags_by_round": flags_by_round, "verdict": verdict,
            "issues": n_issue, "coaching": n_coach, "disciplinary": n_disc,
            "total_flagged": len(cands),
        }
    results.add("notes_summary",
                ["Resource", "Productive % by round", "Round-over-round",
                 "# issues", "# coaching", "# disciplinary", "# flagged total"],
                summary_rows,
                note="Review of the notes column, per resource: productivity trend between "
                     "rounds and a count of issues, coaching opportunities and disciplinary "
                     "flags.")
    results.notes_by_resource = per_resource


def run(plan, facts, resource_index, records: list[dict]) -> PlanResults:
    res = PlanResults()
    rows = facts.rows
    rounds = facts.rounds or sorted({r["round"] for r in rows if r["round"] is not None})
    expected_min = int(getattr(plan, "expected_rounds_min", 2) or 2)

    # -- 1. resources, rounds, where the name was found --------------------
    loc_label = {
        "filename_leading_label": "leading label in filename",
        "filename_trailing_token": "initials/surname token before .xlsx",
        "content_resource_cell": "RESOURCE cell inside the file",
        "folder": "containing folder name",
    }
    rrows = []
    for key, node in sorted(resource_index.by_resource.items(),
                            key=lambda kv: kv[1]["canonical"].lower()):
        locs = "; ".join(f"{loc_label.get(k, k)} (×{v})"
                         for k, v in node["name_locations"].most_common())
        rrows.append([
            node["canonical"],
            ", ".join(str(x) for x in node["round_list"]) or "-",
            node["round_count"],
            "yes" if node["round_count"] >= expected_min else f"NO (< {expected_min})",
            locs,
            "; ".join(sorted(node["variants"])),
            len(node["files"]),
        ])
    res.add("resources_rounds",
            ["Resource", "Rounds present", "# rounds",
             f"Meets >= {expected_min}?", "Name found in (naming scheme)",
             "Naming variant(s)", "# files"], rrows,
            note="One row per unique resource. 'Name found in' shows where each "
                 "resource's name appears in the file naming scheme.")

    # per-file: resource + round + where the name was found + how many lines logged
    lines_by_file: dict = {}
    for r in rows:
        lines_by_file[r["file"]] = lines_by_file.get(r["file"], 0) + 1
    frows = []
    thin = []
    for rel, fr in sorted(resource_index.by_file.items()):
        nlines = lines_by_file.get(rel, 0)
        frows.append([rel, fr.canonical, fr.round if fr.round is not None else "-",
                      "; ".join(fr.name_locations), fr.variant.split(" ")[0], nlines,
                      "; ".join(fr.anomalies) or "-"])
        if nlines < 5:
            thin.append(f"{rel} ({fr.canonical}, round {fr.round}): only {nlines} activity line(s) logged")
    res.add("files", ["File", "Resource", "Round", "Name found in", "Variant",
                      "# activity lines", "Notes"], frows,
            note="One row per file: the unique resource it belongs to, its study "
                 "round, and where in the filename the resource name was located.")
    for t in thin:
        resource_index.anomalies.append(t)

    # -- 2. tasks: count / aggregate / average, by round + total ----------
    def by_dim_round(dim: str):
        table = []
        g = _group(rows, lambda r: (r[dim], r["round"]))
        for (name, rnd), grp in sorted(g.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1]))):
            st = _stats(grp)
            table.append([name, rnd if rnd is not None else "-", st["count"],
                          st["sum"], st["avg"]])
        return table

    def totals_by_dim(dim: str):
        table = []
        g = _group(rows, lambda r: r[dim])
        for name, grp in sorted(g.items(), key=lambda kv: str(kv[0])):
            st = _stats(grp)
            table.append([name, st["count"], st["sum"], st["avg"]])
        return table

    res.add("task_by_round",
            ["Task", "Round", "# tasks", "Total minutes", "Avg minutes"],
            by_dim_round("task"),
            note="Quantity, aggregate time and computed average time per task, separated by round.")
    res.add("task_totals",
            ["Task", "# tasks (all rounds)", "Total minutes", "Avg minutes"],
            totals_by_dim("task"), note="All-rounds total per task.")
    res.add("type_by_round",
            ["Activity type", "Round", "# entries", "Total minutes", "Avg minutes"],
            by_dim_round("type"),
            note="Quantity, aggregate time and computed average time per activity type, by round.")
    res.add("type_totals",
            ["Activity type", "# entries (all rounds)", "Total minutes", "Avg minutes"],
            totals_by_dim("type"), note="All-rounds total per activity type.")

    # productive vs non-productive by round + total
    pr = []
    g = _group(rows, lambda r: (r["activity_class"], r["round"]))
    for (cls, rnd), grp in sorted(g.items(), key=lambda kv: (kv[0][0], str(kv[0][1]))):
        st = _stats(grp)
        pr.append([cls, rnd if rnd is not None else "-", st["count"], st["sum"], st["avg"]])
    res.add("class_by_round",
            ["Class", "Round", "# entries", "Total minutes", "Avg minutes"], pr)

    # -- 3. grand totals --------------------------------------------------
    all_st = _stats(rows)
    prod = [r for r in rows if r["activity_class"] == "Productive"]
    nonp = [r for r in rows if r["activity_class"] == "Non-Productive"]
    p_sum, n_sum = _stats(prod)["sum"], _stats(nonp)["sum"]
    denom = p_sum + n_sum
    res.grand_totals = {
        "activity_lines": all_st["count"],
        "total_minutes": all_st["sum"],
        "avg_minutes": all_st["avg"],
        "productive_lines": len(prod), "productive_minutes": p_sum,
        "nonproductive_lines": len(nonp), "nonproductive_minutes": n_sum,
        "productive_pct": round(100 * p_sum / denom, 1) if denom else 0.0,
        "rounds": rounds, "resources": len(resource_index.by_resource),
        "files": len(records),
    }

    # -- 4. rank resources against each other ----------------------------
    rank_by = str(getattr(plan, "ranking", {}).get("by", "productive_pct"))
    rres = []
    for key, node in resource_index.by_resource.items():
        rrows_ = [r for r in rows if r["resource_key"] == key]
        p = [r for r in rrows_ if r["activity_class"] == "Productive"]
        nq = [r for r in rrows_ if r["activity_class"] == "Non-Productive"]
        ps, ns = _stats(p)["sum"], _stats(nq)["sum"]
        d = ps + ns
        rres.append({
            "resource": node["canonical"],
            "resource_key": key,
            "rounds": node["round_count"],
            "tasks_logged": len(rrows_),
            "productive_min": ps,
            "nonproductive_min": ns,
            "productive_pct": round(100 * ps / d, 1) if d else 0.0,
            "avg_task_min": _stats(rrows_)["avg"],
        })
    keyfn = {
        "productive_pct": lambda x: (-x["productive_pct"], -x["productive_min"]),
        "productive_min": lambda x: (-x["productive_min"],),
        "tasks_logged": lambda x: (-x["tasks_logged"],),
        "avg_task_min": lambda x: (x["avg_task_min"],),
    }.get(rank_by, lambda x: (-x["productive_pct"], -x["productive_min"]))
    rres.sort(key=keyfn)
    for i, row in enumerate(rres, 1):
        row["rank"] = i
    res.ranking = rres
    res.add("resource_ranking",
            ["Rank", "Resource", "# rounds", "Tasks logged", "Productive min",
             "Non-productive min", "Productive %", "Avg task min"],
            [[r["rank"], r["resource"], r["rounds"], r["tasks_logged"], r["productive_min"],
              r["nonproductive_min"], r["productive_pct"], r["avg_task_min"]] for r in rres],
            note=f"Resources ranked against each other by {rank_by.replace('_', ' ')}.")

    # -- 5. cross-tabs --------------------------------------------------
    def crosstab(dim: str, title_cols):
        names = sorted({r[dim] for r in rows})
        keys = list(resource_index.by_resource.values())
        table = []
        for node in sorted(keys, key=lambda n: n["canonical"].lower()):
            k = next(kk for kk, vv in resource_index.by_resource.items() if vv is node)
            counts = _group([r for r in rows if r["resource_key"] == k], lambda r: r[dim])
            table.append([node["canonical"]] + [len(counts.get(n, [])) for n in names])
        return [title_cols[0]] + names, table

    hcols, trows = crosstab("type", ["Resource"])
    res.add("resource_x_type", hcols, trows, note="Count of entries per resource by activity type.")
    hcols, trows = crosstab("task", ["Resource"])
    res.add("resource_x_task", hcols, trows, note="Count of tasks per resource by task.")

    # -- 5b. per-resource breakouts: qty / total / avg by round + resource total
    ordered = _ordered_resources(resource_index, rres)
    res.add("per_resource_task",
            ["Resource", "Task", "Round", "Qty", "Total minutes", "Avg minutes"],
            _per_resource_dim(rows, ordered, "task"),
            note="For each resource: every task with quantity, aggregate time and average "
                 "time, separated by round, then an all-rounds total per task and a "
                 "resource total row.")
    res.add("per_resource_type",
            ["Resource", "Activity type", "Round", "Qty", "Total minutes", "Avg minutes"],
            _per_resource_dim(rows, ordered, "type"),
            note="For each resource: every activity type with quantity, aggregate time and "
                 "average time, separated by round, then an all-rounds total per type and a "
                 "resource total row.")

    # -- 5c. the single summary table: one column per resource+round (+ a resource
    #        total column and an all-resources column), one metric per row.
    cols, prows = _summary_pivot(rows, ordered)
    res.add("summary_pivot", cols, prows,
            note="One summary table, all metrics by technician: a column for each "
                 "technician x round, a per-technician total column and an all-resources "
                 "column. Quantity rows and time rows are summed separately; averages and "
                 "percentages are recomputed, not added.")

    # -- 6. coaching / corrective-action candidates from Notes -----------
    cues = list(_DEF_CUES)
    for extra in (getattr(plan, "coaching", {}) or {}).get("cues", []) or []:
        if extra and extra.lower() not in cues:
            cues.append(extra.lower())
    cue_res = [(c, re.compile(r"\b" + re.escape(c) + r"\b", re.I)) for c in cues]
    cand = []
    for r in rows:
        note = (r.get("notes") or "").strip()
        if len(note) < 8:
            continue
        hits = [c for c, rx in cue_res if rx.search(note)]
        if not hits:
            continue
        cand.append({
            "resource": r["resource"], "resource_key": r["resource_key"],
            "round": r["round"], "file": r["file"], "line": r["line"],
            "task": r["task"], "type": r["type"],
            "duration_min": r["duration_min"], "trigger": hits[0],
            "note": note[:400],
        })

    def _lineno(c):
        try:
            return int(float(c["line"]))
        except (TypeError, ValueError):
            return 10 ** 9
    cand.sort(key=lambda c: (str(c["resource"]), c["round"] if c["round"] is not None else 99,
                             _lineno(c)))
    res.coaching_candidates = cand

    # -- 7. generic plan.metrics interpreter (for other sessions) --------
    res.metrics = _run_metrics(getattr(plan, "metrics", []) or [], rows)

    # -- 8. echo the user's own numbered deliverables, each pointing at the
    #       table that answers it, so "a table for each item individually" is met
    res.requested_items = _map_requested_items(getattr(plan, "requested_items", []) or [])
    return res


_ITEM_KEYWORDS = [
    (("task", "quantity"), "per_resource_task"),
    (("task", "count"), "per_resource_task"),
    (("type", "quantity"), "per_resource_type"),
    (("type", "count"), "per_resource_type"),
    (("aggregate", "task"), "per_resource_task"),
    (("aggregate", "type"), "per_resource_type"),
    (("total time", "task"), "per_resource_task"),
    (("total time", "type"), "per_resource_type"),
    (("average", "task"), "per_resource_task"),
    (("average", "type"), "per_resource_type"),
    (("average",), "per_resource_task"),
    (("productive",), "class_by_round"),
    (("unproductive",), "class_by_round"),
    (("rank",), "resource_ranking"),
    (("round",), "per_resource_task"),
    (("coach", ), "notes_summary"),
    (("disciplin",), "notes_summary"),
    (("improvement",), "notes_summary"),
    (("degradation",), "notes_summary"),
    (("note",), "notes_summary"),
]


def _map_requested_items(items: list[str]) -> list[dict]:
    out = []
    for i, text in enumerate(items, 1):
        low = text.lower()
        table = ""
        for needles, key in _ITEM_KEYWORDS:
            if all(n in low for n in needles):
                table = key
                break
        out.append({"n": i, "text": text, "table": table})
    return out


# --------------------------------------------------------------------- generic
_FILTER_RE = re.compile(r"^\s*(\w+)\s*(=|!=|in)\s*(.+?)\s*$")


def _passes(row, flt: str) -> bool:
    if not flt:
        return True
    m = _FILTER_RE.match(flt)
    if not m:
        return True
    field_, op, val = m.groups()
    cur = str(row.get(field_, "")).lower()
    if op == "=":
        return cur == val.strip().strip("'\"").lower()
    if op == "!=":
        return cur != val.strip().strip("'\"").lower()
    if op == "in":
        opts = [x.strip().strip("'\"[]").lower() for x in val.split(",")]
        return cur in opts
    return True


def _run_metrics(specs: list[dict], rows: list[dict]) -> list[dict]:
    out = []
    for spec in specs:
        try:
            kind = str(spec.get("type", "count")).lower()
            gb = spec.get("group_by") or []
            if isinstance(gb, str):
                gb = [gb]
            val = spec.get("value", "duration_min")
            flt = spec.get("filter", "")
            sub = [r for r in rows if _passes(r, flt)]
            groups = _group(sub, lambda r: tuple(r.get(g) for g in gb)) if gb else {(): sub}
            result_rows = []
            for gkey, grp in groups.items():
                nums = [r[val] for r in grp if isinstance(r.get(val), (int, float))]
                if kind == "count":
                    v = len(grp)
                elif kind == "count_distinct":
                    v = len({r.get(val) for r in grp})
                elif kind == "sum":
                    v = round(sum(nums), 2)
                elif kind in ("mean", "avg"):
                    v = round(sum(nums) / len(nums), 2) if nums else 0.0
                elif kind == "min":
                    v = min(nums) if nums else None
                elif kind == "max":
                    v = max(nums) if nums else None
                else:
                    v = len(grp)
                result_rows.append(list(gkey) + [v])
            out.append({"id": spec.get("id") or spec.get("label") or kind,
                        "label": spec.get("label", ""), "type": kind,
                        "group_by": gb, "columns": gb + [kind],
                        "rows": sorted(result_rows, key=lambda r: [str(x) for x in r])})
        except Exception as exc:  # noqa: BLE001
            out.append({"id": spec.get("id", "?"), "error": f"{type(exc).__name__}: {exc}"})
    return out
