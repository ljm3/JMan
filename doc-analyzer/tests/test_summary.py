"""The explicit-directive layer: numbered deliverables, the per-resource
breakouts, the single technician x round summary pivot, and the notes review."""
from __future__ import annotations

from doc_analyzer.analyze import directives as dmod
from doc_analyzer.analyze import execute_plan as ex
from doc_analyzer.analyze import facts as facts_mod
from doc_analyzer.analyze import resources as res_mod
from doc_analyzer.analyze.plan import _heuristic_plan

REAL_OBJECTIVE = (
    "Identify the resources and round of study from each file, the name can be identified using "
    "the information just before the file extension and after NonProd in the title.  Provide the "
    "following metrics for each resource and provide a table breaking out each and one summary "
    "table that includes all metrics by technician (technician name  Round 1 and technician name "
    "round 2 as the column headers and then all metrics lined up as appropriate in rows below.  "
    "Sum all rows and columns as appropriate (times separate from quantities).):  1.  List unique "
    "tasks and Quantity, 2.  List unique types and Quantity, 3.  Aggregate time for tasks, 4.  "
    "Aggregate time for types, 5  Calculate average time for each unique task and type,  6.  "
    "Provide those breakdowns separated by round and then with an aggregate total of all for that "
    "resource.  Based on these metrics, rank each resource comparative to all resources.  "
    "Determine productive time logged vs unproductive time logged - use judgement and consider "
    "unproductive Tasks to include Lunch, Break, and Types of Scheduled Break and Unscheduled "
    "Break.  Finally, review the notes on each file and identify improvement or degradation "
    "between rounds, issues, coaching opportunities, and potential for disciplinary action.  "
    "In the final result, provide a table for each of the items requested individually and then "
    "one summary table including all together.")


def test_directives_parse_real_objective():
    d = dmod.parse(REAL_OBJECTIVE)
    assert len(d.requested_items) == 6
    # item 6 is trimmed to its own sentence, not the trailing prose
    assert d.requested_items[0].lower().startswith("list unique tasks")
    assert "rank each resource" not in d.requested_items[5].lower()
    assert d.requested_items[5].lower().startswith("provide those breakdowns")
    assert d.want_summary_pivot and d.want_per_item_tables
    assert d.pivot_columns == "resource_round"
    assert [t.lower() for t in d.nonprod_tasks] == ["lunch", "break"]
    assert [t.lower() for t in d.nonprod_types] == ["scheduled break", "unscheduled break"]


def test_numbered_items_needs_a_real_run():
    assert dmod.parse("do the thing with 3 files and 2 rounds").requested_items == []
    got = dmod.parse("Steps: 1. alpha bravo 2. charlie delta 3. echo foxtrot").requested_items
    assert got == ["alpha bravo", "charlie delta", "echo foxtrot"]


def test_classify_honours_objective_rule():
    # default regex would call "Queue Review" productive and "Break" non-productive
    assert facts_mod.classify("Break", "Support Inbound") == "Non-Productive"
    # explicit rule: only these are non-productive, nothing else
    assert facts_mod.classify("Ticket Time", "Scheduled Break",
                              nonprod_tasks=["Lunch", "Break"],
                              nonprod_types=["Scheduled Break", "Unscheduled Break"]) == "Non-Productive"
    assert facts_mod.classify("Break", "Support Inbound",
                              nonprod_tasks=["Lunch"], nonprod_types=[]) == "Productive"


def _rec(rel, ctx, rows):
    return {"rel": rel, "ok": True, "meta": {
        "cell_context": ctx,
        "record_tables": [{
            "sheet": "Time Study", "header_row": 1,
            "columns": ["Line #", "Task", "Type", "Duration\n(Minutes)", "Notes"],
            "row_count": len(rows), "rows": rows, "context": ctx,
        }]}}


def _rows(spec):
    out = []
    for i, (task, typ, mins, note) in enumerate(spec, 1):
        out.append({"Line #": i, "Task": task, "Type": typ,
                    "Duration\n(Minutes)": mins, "Notes": note})
    return out


def _corpus():
    r1 = _rows([("Call", "Support Inbound", 10.0, "ok"),
                ("Call", "Support Inbound", 20.0, "sat idle after call"),
                ("Lunch", "Scheduled Break", 30.0, ""),
                ("Break", "Unscheduled Break", 5.0, "missed call while away")])
    r2 = _rows([("Call", "Support Inbound", 12.0, "ok"),
                ("Ticket Time", "Support Inbound", 8.0, "ok")])
    recs = [
        _rec("TimeStudyRoundOneProd.NonProd.WNazario.xlsx", {"RESOURCE": "Wil Nazario"}, r1),
        _rec("TimeStudyRoundTwoProd.NonProd.WNazario.xlsx", {"RESOURCE": "Wil Nazario"}, r2),
        _rec("Tek V Time Study - Round 1 - Prod-NonProd.TVillanueva.xlsx",
             {"RESOURCE": "Tek Villanueva"}, _rows([("Call", "Support Inbound", 15.0, "ok")])),
        _rec("Tek V Time Study - Round 2 - Prod-NonProd.TVillanueva.xlsx",
             {"RESOURCE": "Tek Villanueva"}, _rows([("Call", "Support Inbound", 9.0, "ok")])),
    ]
    idx = res_mod.build_index(recs)
    facts = facts_mod.build(recs, idx, nonprod_tasks=["Lunch", "Break"],
                            nonprod_types=["Scheduled Break", "Unscheduled Break"])
    plan = _heuristic_plan(REAL_OBJECTIVE)
    results = ex.run(plan, facts, idx, recs)
    return results, facts, idx


def test_per_resource_breakout_has_round_and_total_rows():
    results, _facts, _idx = _corpus()
    t = results.tables["per_resource_task"]
    assert t["columns"] == ["Resource", "Task", "Round", "Qty", "Total minutes", "Avg minutes"]
    rounds = {(r[0], r[1], r[2]) for r in t["rows"]}
    assert ("Wil Nazario", "Call", 1) in rounds
    assert ("Wil Nazario", "Call", "all rounds") in rounds
    assert any(r[1] == "— ALL TASKS —" for r in t["rows"] if r[0] == "Wil Nazario")
    # Wil's Call: R1 qty 2 total 30, R2 qty 1 total 12, all-rounds qty 3 total 42
    call_all = [r for r in t["rows"] if r[0] == "Wil Nazario" and r[1] == "Call"
                and r[2] == "all rounds"][0]
    assert call_all[3] == 3 and call_all[4] == 42.0


def test_summary_pivot_layout_and_totals():
    results, _facts, _idx = _corpus()
    p = results.tables["summary_pivot"]
    cols = p["columns"]
    assert cols[0] == "Metric"
    assert cols[-1] == "All resources"
    assert "Wil Nazario R1" in cols and "Wil Nazario R2" in cols and "Wil Nazario Total" in cols
    rows = {r[0]: r for r in p["rows"]}
    # Call Qty across everything = 2+1 (Wil) + 1+1 (Tek) = 5  -> the last cell
    assert rows["Call · Qty"][-1] == 5
    wil_total_idx = cols.index("Wil Nazario Total")
    assert rows["Call · Qty"][wil_total_idx] == 3
    # productive % row present and recomputed (not a naive sum)
    assert "Productive %" in rows
    r1_idx = cols.index("Wil Nazario R1")
    # Wil R1: productive = 10+20 = 30, non-productive = 30 (lunch) + 5 (break) = 35
    assert rows["Productive %"][r1_idx] == round(100 * 30 / 65, 1)


def test_notes_summary_and_buckets():
    results, facts, idx = _corpus()
    # keyword review assigns categories + buckets, then the summary is built
    from doc_analyzer.analyze import plan as plan_mod
    plan_mod.refine_coaching(None, _NullSession(), results.coaching_candidates)
    ex.build_notes_summary(results, facts, idx)
    ns = results.tables["notes_summary"]
    assert ns["columns"][:3] == ["Resource", "Productive % by round", "Round-over-round"]
    wil = [r for r in ns["rows"] if r[0] == "Wil Nazario"][0]
    # "missed call while away" -> disciplinary bucket
    assert wil[5] >= 1
    assert results.notes_by_resource  # per-resource dict populated
    assert all(c.get("bucket") in ("issue", "coaching", "disciplinary")
               for c in results.coaching_candidates)


class _NullSession:
    def detail(self, *_a, **_k):
        pass

    def warn(self, *_a, **_k):
        pass
