"""Deterministic objective-plan pipeline: naming-scheme parsing, the activity
fact table, the metric executor and the heuristic planner."""
from __future__ import annotations

from doc_analyzer.analyze import facts as facts_mod
from doc_analyzer.analyze import execute_plan as ex
from doc_analyzer.analyze import resources as res_mod
from doc_analyzer.analyze.plan import _heuristic_plan


def _rec(rel, ctx=None, rows=None, columns=None):
    meta = {"cell_context": ctx or {}}
    if rows is not None:
        meta["record_tables"] = [{
            "sheet": "Time Study", "header_row": 5,
            "columns": columns or ["Line #", "Task", "Type", "Duration\n(Minutes)", "Notes", "Status"],
            "row_count": len(rows), "rows": rows, "context": ctx or {},
        }]
    return {"rel": rel, "meta": meta, "ok": True}


def test_naming_scheme_both_variants_and_name_locations():
    recs = [
        _rec("Kyle J Time Study - Round 1 - Prod-NonProd.KJennings.xlsx",
             {"RESOURCE": "Kyle Jennings"}),
        _rec("Kyle J Time Study - Round 2 - Prod-NonProd.KJennings.xlsx",
             {"RESOURCE": "Kyle Jennings"}),
        _rec("TimeStudyRoundOneProd.NonProd.DDomingo.xlsx", {"RESOURCE": "Dallas Domingo"}),
        _rec("TimeStudyRoundTwoProd.NonProd.DDomingo.xlsx", {"RESOURCE": "Dallas Domingo"}),
        _rec("TimeStudyRoundTwoProd.NonProd.JLandino (2).xlsx", {"RESOURCE": "Jlandino"}),
    ]
    idx = res_mod.build_index(recs)
    # 3 unique resources (Kyle, Domingo, Landino), Kyle merged across 2 files
    assert idx.by_resource.keys() >= {"kjennings", "ddomingo", "jlandino"}
    kyle = idx.by_resource["kjennings"]
    assert kyle["round_list"] == [1, 2] and kyle["round_count"] == 2
    assert kyle["canonical"] == "Kyle Jennings"
    assert "filename_leading_label" in kyle["name_locations"]
    assert "filename_trailing_token" in kyle["name_locations"]
    # variant B has no leading label
    dom = idx.by_resource["ddomingo"]
    assert "filename_leading_label" not in dom["name_locations"]
    # round parsed from the camel word "One"
    assert 1 in dom["round_list"]
    # the (2) copy is flagged and Landino has <2 rounds
    joined = " ".join(idx.anomalies)
    assert "copy marker" in joined
    assert "expected at least 2" in joined


def test_round_words_and_humanize():
    fr = res_mod.parse_filename("TimeStudyRoundThreeProd.NonProd.WNazario.xlsx")
    assert fr.round == 3
    assert res_mod._humanize_token("KJennings") == "K. Jennings"
    assert res_mod._humanize_token("TJGrunfelder") == "TJ Grunfelder"


def _fact_rows():
    # 4 activity lines for one file
    return [
        {"Line #": 1, "Task": "Call", "Type": "Support Inbound", "Duration\n(Minutes)": 10.0,
         "Notes": "normal call", "Status": "Complete"},
        {"Line #": 2, "Task": "Ticket Time", "Type": "Support Inbound", "Duration\n(Minutes)": 20.0,
         "Notes": "Sat idle in ticket after call for 8 minutes", "Status": "Complete"},
        {"Line #": 3, "Task": "Break", "Type": "Scheduled Break", "Duration\n(Minutes)": 15.0,
         "Notes": "", "Status": "Complete"},
        {"Line #": 4, "Task": "Call", "Type": "Support Inbound", "Duration\n(Minutes)": 30.0,
         "Notes": "not logged in to Bomgar", "Status": "Complete"},
    ]


def test_facts_and_metrics_and_ranking_and_coaching():
    recs = [
        _rec("Kyle J Time Study - Round 1 - Prod-NonProd.KJennings.xlsx",
             {"RESOURCE": "Kyle Jennings"}, rows=_fact_rows()),
        _rec("Kyle J Time Study - Round 2 - Prod-NonProd.KJennings.xlsx",
             {"RESOURCE": "Kyle Jennings"}, rows=_fact_rows()[:2]),
        _rec("Tek V Time Study - Round 1 - Prod-NonProd.TVillanueva.xlsx",
             {"RESOURCE": "Tek Villanueva"}, rows=_fact_rows()[:1]),
    ]
    idx = res_mod.build_index(recs)
    facts = facts_mod.build(recs, idx)
    assert len(facts.rows) == 7
    assert facts.notes_column_present
    assert set(facts.rounds) == {1, 2}
    # Break -> non-productive
    br = [r for r in facts.rows if r["task"] == "Break"][0]
    assert br["activity_class"] == "Non-Productive"

    plan = _heuristic_plan("tasks by round and total, rank resources, notes for coaching, "
                           "each resource at least 2 rounds")
    assert "ranking" in plan.sections and "coaching" in plan.sections
    assert plan.expected_rounds_min == 2

    results = ex.run(plan, facts, idx, recs)
    gt = results.grand_totals
    assert gt["activity_lines"] == 7
    assert gt["productive_minutes"] == 10 + 20 + 30 + 10 + 20 + 10   # all non-break
    assert gt["nonproductive_minutes"] == 15

    # task totals table has a Call row with count 4 (2+1+1)
    tt = {row[0]: row for row in results.tables["task_totals"]["rows"]}
    assert tt["Call"][1] == 4

    # ranking: Kyle has a break (lower prod %), Tek is 100% productive -> Tek rank 1
    ranks = {r["resource"]: r["rank"] for r in results.ranking}
    assert ranks["Tek Villanueva"] == 1
    assert results.ranking[0]["productive_pct"] == 100.0

    # coaching picked up the idle + not-logged-in notes for Kyle
    kyle_flags = [c for c in results.coaching_candidates if c["resource"] == "Kyle Jennings"]
    assert len(kyle_flags) >= 2
    assert any("idle" in c["trigger"] for c in kyle_flags)

    # resources_rounds flags Tek as below the expected round count
    rr = {row[0]: row for row in results.tables["resources_rounds"]["rows"]}
    assert rr["Tek Villanueva"][3].startswith("NO")
    assert rr["Kyle Jennings"][3] == "yes"


def test_generic_metric_interpreter():
    rows = [
        {"task": "Call", "type": "In", "round": 1, "duration_min": 10, "activity_class": "Productive"},
        {"task": "Call", "type": "In", "round": 1, "duration_min": 30, "activity_class": "Productive"},
        {"task": "Break", "type": "Off", "round": 2, "duration_min": 15, "activity_class": "Non-Productive"},
    ]
    out = ex._run_metrics([
        {"id": "sum_prod", "type": "sum", "value": "duration_min",
         "group_by": ["round"], "filter": "activity_class=Productive"},
        {"id": "n_by_task", "type": "count", "group_by": ["task"], "filter": ""},
    ], rows)
    m = {o["id"]: o for o in out}
    assert m["sum_prod"]["rows"] == [[1, 40]]
    assert sorted(m["n_by_task"]["rows"]) == [["Break", 1], ["Call", 2]]
