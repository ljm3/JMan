"""Write the human + machine readable session artifacts."""
from __future__ import annotations

import json
from pathlib import Path


def _md_table(headers, rows):
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join("---" for _ in headers) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c).replace("|", "\\|").replace("\n", " ") for c in r) + " |")
    return "\n".join(out)


def write_all(session, corpus_summary, records, analyzed, cluster_rows,
              phrase_rows, category_rows, llm) -> dict[str, Path]:
    arts: dict[str, Path] = {}
    arts["synopsis_md"] = _synopsis_md(session, corpus_summary, records, analyzed, llm)
    arts["synopsis_json"] = _synopsis_json(session, corpus_summary, analyzed)
    arts["activity_md"] = _activity_md(session, corpus_summary, cluster_rows, phrase_rows, category_rows)
    arts["activity_json"] = _activity_json(session, cluster_rows, phrase_rows, category_rows)
    arts["manifest_json"] = _manifest(session, corpus_summary, records)
    if corpus_summary.get("plan_results"):
        arts["objective_analysis_md"] = _objective_analysis_md(session, corpus_summary)
        arts["objective_analysis_json"] = _objective_analysis_json(session, corpus_summary)
    arts["session_log"] = session._log_path  # noqa: SLF001 - intentional handle
    for k, v in arts.items():
        session.detail(f"artifact: {Path(v).name}")
    return arts


# --------------------------------------------------------------------- synopsis
def _synopsis_md(session, cs, records, analyzed, llm) -> Path:
    p = session.path("synopsis.md")
    L: list[str] = []
    L.append(f"# Document synopsis - session {session.id}")
    L.append("")
    L.append(f"- **Source:** {cs['source']}")
    L.append(f"- **Objective:** {cs.get('objective') or '_(not specified)_'}")
    L.append(f"- **Analyzed folder:** `{cs['root']}`")
    L.append(f"- **Generated (UTC):** {cs['generated_utc']}")
    L.append(f"- **Files on disk:** {cs['files_found']}  |  "
             f"**analyzed:** {cs['doc_count']}  |  **skipped:** {cs['files_skipped']}")
    L.append(f"- **Total words analyzed:** {cs['total_words']:,}  |  "
             f"**bytes catalogued:** {cs['total_bytes']:,}")
    if cs.get("date_range"):
        L.append(f"- **Date range:** {cs['date_range'][0]} -> {cs['date_range'][1]}  "
                 f"_({cs.get('date_basis', 'best guess')})_")
    L.append("")

    _objective_findings_md(L, cs)
    if cs.get("plan_results"):
        L.append("")
        _plan_sections_md(L, cs, heading_level="##")

    if llm and llm.get("narrative"):
        L.append("## Narrative synopsis (Claude)")
        L.append("")
        L.append(f"_model: {llm.get('model')}_")
        L.append("")
        L.append(llm["narrative"].strip())
        L.append("")

    L.append("## Corpus overview")
    L.append("")
    L.append("### Document types")
    L.append("")
    L.append(_md_table(["Type", "Documents"], cs["type_distribution"]))
    L.append("")
    L.append("### File extensions")
    L.append("")
    L.append(_md_table(["Extension", "Documents"], cs["ext_distribution"]))
    L.append("")
    L.append("### Top corpus keywords")
    L.append("")
    L.append(", ".join(f"`{k}`" for k in cs["keywords"]))
    L.append("")

    L.append("### Timeline (best-guess document date)")
    L.append("")
    L.append(_md_table(["Date", "Document", "Type"],
                       [(t["date"] or "-", t["doc"], t["kind"]) for t in cs["timeline"]]))
    L.append("")

    L.append("## Per-document synopsis")
    L.append("")
    by_rel = {r["rel"]: r for r in records}
    for rel in [r["rel"] for r in analyzed]:
        r = by_rel[rel]
        L.append(f"### {rel}")
        L.append("")
        L.append(f"- type: **{r['kind']}** via `{r['extractor']}`  |  size: {r['size']:,} B  "
                 f"|  words: {r['words']:,}  |  sha256: `{r['sha256'][:16]}…`")
        L.append(f"- modified: {r['mtime']}  |  best-guess date: {r['best_date'] or '-'}")
        if r.get("relevance_rank"):
            hits = ", ".join(r.get("objective_hits", [])[:8])
            L.append(f"- objective relevance: {r.get('relevance', 0):.3f} "
                     f"(rank {r['relevance_rank']})"
                     + (f"  |  hits: {hits}" if hits else ""))
        mh = _meta_highlights(r["meta"])
        if mh:
            L.append(f"- metadata: {mh}")
        if r["keywords"]:
            L.append(f"- keywords: {', '.join('`' + k + '`' for k in r['keywords'])}")
        ent = r.get("entities") or {}
        if ent:
            for name in ("dates", "money", "percent", "organizations", "people",
                         "email", "url", "phone", "ref_id", "acronyms"):
                if ent.get(name):
                    L.append(f"- {name}: {', '.join(str(x) for x in ent[name][:12])}")
        L.append("")
        if r["summary"]:
            L.append("> " + "\n> ".join(r["summary"]))
        else:
            L.append("> _(no extractive summary - document too short or non-prose)_")
        L.append("")

    skipped = [r for r in records if not r["ok"]]
    if skipped:
        L.append("## Skipped / errored files")
        L.append("")
        L.append(_md_table(["File", "Reason"],
                           [(r["rel"], r["error"] or "no extractable text") for r in skipped]))
        L.append("")

    p.write_text("\n".join(L), encoding="utf-8")
    return p


def _objective_findings_md(L: list, cs: dict) -> None:
    prof = cs.get("objective_profile") or {}
    find = cs.get("objective_findings") or {}
    if not (prof or find):
        return
    L.append("## Findings for your objective")
    L.append("")
    L.append(f"> {cs.get('objective') or '(not specified)'}")
    L.append("")
    if prof.get("focus"):
        L.append(f"- **Focus areas detected:** {', '.join(prof['focus'])}")
    if prof.get("entity_types"):
        L.append(f"- **Information surfaced for this objective:** "
                 f"{', '.join(prof['entity_types'])}")
    rc = find.get("relevant_document_count")
    if rc is not None:
        L.append(f"- **Documents materially relevant:** {rc} of "
                 f"{len(find.get('ranked_documents', []))}")
    L.append("")

    rd = find.get("ranked_documents") or []
    if rd:
        L.append("### Documents ranked by relevance to the objective")
        L.append("")
        L.append(_md_table(
            ["Rank", "Relevance", "Document", "Objective hits", "Lead point"],
            [(d["rank"], f"{d['relevance']:.3f}", d["doc"],
              ", ".join(d.get("hits", [])[:6]) or "-", (d.get("why") or "-"))
             for d in rd]))
        L.append("")

    ks = find.get("key_sentences") or []
    if ks:
        L.append("### Passages that speak to the objective")
        L.append("")
        for s in ks:
            L.append(f"- *(“{s['doc']}”, score {s['score']:.3f})* {s['sentence']}")
        L.append("")

    eoi = find.get("entities_of_interest") or {}
    if eoi:
        L.append("### Objective-relevant details extracted across the corpus")
        L.append("")
        for et, vals in eoi.items():
            L.append(f"- **{et}:** {', '.join(str(v) for v in vals[:30])}")
        L.append("")


# ------------------------------------------------------ plan-driven objective analysis
_PLAN_TABLE_ORDER = [
    ("resources_rounds", "Resources & rounds (where the name sits in the naming scheme)"),
    ("files", "Per file - resource, round, and name location"),
    ("per_resource_task", "Per resource - tasks: quantity / total time / average by round"),
    ("per_resource_type", "Per resource - activity types: quantity / total time / average by round"),
    ("task_by_round", "All resources - tasks: count / total / average by round"),
    ("task_totals", "All resources - tasks: totals across all rounds"),
    ("type_by_round", "All resources - activity types: count / total / average by round"),
    ("type_totals", "All resources - activity types: totals across all rounds"),
    ("class_by_round", "Productive vs non-productive by round"),
    ("resource_ranking", "Resource ranking (each resource vs the others)"),
    ("summary_pivot", "SUMMARY - all metrics by technician x round"),
    ("notes_summary", "Notes review - trend + issue / coaching / disciplinary counts"),
    ("resource_x_type", "Resource x activity type (entry counts)"),
    ("resource_x_task", "Resource x task (task counts)"),
]


def _plan_sections_md(L: list, cs: dict, heading_level: str = "##") -> None:
    plan = cs.get("analysis_plan") or {}
    pr = cs.get("plan_results") or {}
    ridx = cs.get("resource_index") or {}
    ans = cs.get("objective_answers") or {}
    h, h2 = heading_level, heading_level + "#"

    L.append(f"{h} Objective-driven analysis")
    L.append("")
    L.append(f"> {cs.get('objective') or '(not specified)'}")
    L.append("")
    L.append(f"- **Planner:** {plan.get('source', 'heuristic')}  |  "
             f"**write-up:** {ans.get('source', 'template')}")
    if plan.get("restated_goal"):
        L.append(f"- **Understood as:** {plan['restated_goal']}")
    L.append(f"- **Unique resources:** {ridx.get('resource_count', 0)}  |  "
             f"**expected rounds each:** >= {plan.get('expected_rounds_min', 2)}")
    gt = pr.get("grand_totals") or {}
    if gt:
        L.append(f"- **Totals:** {gt.get('activity_lines')} activity lines, "
                 f"{gt.get('total_minutes')} min ({gt.get('productive_pct')}% productive), "
                 f"avg {gt.get('avg_minutes')} min/line")
    plan_d = cs.get("analysis_plan") or {}
    if plan_d.get("nonprod_tasks") or plan_d.get("nonprod_types"):
        L.append(f"- **Unproductive rule (from your objective):** tasks "
                 f"{plan_d.get('nonprod_tasks') or '(none)'}; types "
                 f"{plan_d.get('nonprod_types') or '(none)'}")

    items = pr.get("requested_items") or []
    if items:
        L.append("")
        L.append(f"{h2} Deliverables you asked for (one table each)")
        L.append("")
        L.append("_Each numbered item is answered by the table named below; the SUMMARY "
                 "table combines them all._")
        L.append("")
        _tmap = {
            "per_resource_task": "Per resource - tasks",
            "per_resource_type": "Per resource - activity types",
            "class_by_round": "Productive vs non-productive by round",
            "resource_ranking": "Resource ranking",
            "notes_summary": "Notes review + coaching instances",
        }
        L.append(_md_table(["#", "Requested item", "Answered by"],
                           [(it["n"], it["text"], _tmap.get(it.get("table"), "the SUMMARY table"))
                            for it in items]))

    if ridx.get("anomalies"):
        L.append("")
        L.append(f"{h2} Naming / round issues")
        L.append("")
        for a in ridx["anomalies"][:40]:
            L.append(f"- {a}")
    L.append("")

    tables = pr.get("tables") or {}
    for key, title in _PLAN_TABLE_ORDER:
        t = tables.get(key)
        if not t or not t.get("rows"):
            continue
        L.append(f"{h2} {title}")
        L.append("")
        if t.get("note"):
            L.append(f"_{t['note']}_")
            L.append("")
        L.append(_md_table(t["columns"], t["rows"]))
        L.append("")

    for m in pr.get("metrics") or []:
        if m.get("error") or not m.get("rows"):
            continue
        L.append(f"{h2} Metric: {m.get('label') or m.get('id')}")
        L.append("")
        L.append(_md_table(m["columns"], m["rows"]))
        L.append("")

    narr = cs.get("resource_notes_narrative") or {}
    if narr:
        L.append(f"{h2} Round-over-round read (per resource)")
        L.append("")
        for v in narr.values():
            if str(v).strip():
                L.append(f"- {v}")
        L.append("")

    coaching = pr.get("coaching_candidates") or []
    if coaching:
        L.append(f"{h2} Coaching / corrective-action instances (from the notes column)")
        L.append("")
        by_res: dict[str, list] = {}
        for c in coaching:
            by_res.setdefault(c.get("resource", "(unknown)"), []).append(c)
        for res_name, items in by_res.items():
            L.append(f"**{res_name}** - {len(items)} instance(s)")
            L.append("")
            L.append(_md_table(
                ["Round", "Line", "Category", "Bucket", "Task", "Min", "Note", "Recommendation"],
                [(c.get("round"), c.get("line"), c.get("category", c.get("trigger", "")),
                  c.get("bucket", ""), c.get("task"), c.get("duration_min"),
                  (c.get("note") or "")[:240], c.get("recommendation", "")) for c in items]))
            L.append("")

    if ans.get("markdown"):
        L.append(f"{h2} Answers to the objective")
        L.append("")
        L.append(ans["markdown"].strip())
        L.append("")


def _objective_analysis_md(session, cs) -> Path:
    p = session.path("objective_analysis.md")
    L = [f"# Objective analysis - session {session.id}", "",
         f"Source: {cs.get('source', '')}", ""]
    _plan_sections_md(L, cs, heading_level="##")
    p.write_text("\n".join(L), encoding="utf-8")
    return p


def _objective_analysis_json(session, cs) -> Path:
    p = session.path("objective_analysis.json")
    p.write_text(json.dumps({
        "session_id": session.id,
        "objective": cs.get("objective"),
        "objective_directives": cs.get("objective_directives"),
        "analysis_plan": cs.get("analysis_plan"),
        "resource_index": cs.get("resource_index"),
        "plan_results": cs.get("plan_results"),
        "resource_notes_narrative": cs.get("resource_notes_narrative"),
        "objective_answers": cs.get("objective_answers"),
    }, indent=2, default=str), encoding="utf-8")
    return p


def _meta_highlights(meta: dict) -> str:
    if not meta:
        return ""
    keys = ("title", "author", "creator", "last_modified_by", "pages", "slides",
            "sheets", "rows", "columns", "paragraphs", "language", "lines_total",
            "from", "to", "subject", "date", "attachments", "root_tag", "leaf_count")
    bits = []
    for k in keys:
        v = meta.get(k)
        if v in (None, "", [], {}):
            continue
        if isinstance(v, list):
            v = ", ".join(str(x) for x in v[:6])
        bits.append(f"{k}={v}")
    return "; ".join(bits)


def _synopsis_json(session, cs, analyzed) -> Path:
    p = session.path("synopsis.json")
    payload = {
        "session_id": session.id,
        "corpus": cs,
        "documents": [
            {k: r[k] for k in ("rel", "path", "ext", "kind", "extractor", "size",
                               "sha256", "mtime", "best_date", "words", "chars",
                               "keywords", "entities", "summary", "meta",
                               "relevance", "relevance_rank", "objective_hits")}
            for r in analyzed
        ],
    }
    p.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return p


# --------------------------------------------------------------------- activity
def _activity_md(session, cs, cluster_rows, phrase_rows, category_rows) -> Path:
    p = session.path("activity_breakdown.md")
    L: list[str] = []
    L.append(f"# Like-activity breakdown - session {session.id}")
    L.append("")
    L.append(f"Source: {cs['source']}  |  documents analyzed: {cs['doc_count']}")
    L.append(f"Objective: {cs.get('objective') or '(not specified)'}")
    L.append("")
    L.append("This report groups documents three ways: by content similarity "
             "(near-duplicate / same-template families), by recurring action "
             "phrases, and by business-activity category.")
    L.append("")

    rel_cats = cs.get("objective_activities") or []
    rel_phrases = [r for r in phrase_rows if r.get("objective_relevant")]
    if cs.get("objective") and (rel_cats or rel_phrases):
        L.append("## Activities most relevant to your objective")
        L.append("")
        L.append(f"> {cs.get('objective')}")
        L.append("")
        if rel_cats:
            L.append(_md_table(
                ["Category", "Documents", "Mentions", "Verbs seen", "In documents"],
                [(c["category"], c["doc_count"], c["mentions"], ", ".join(c["verbs"][:6]),
                  ", ".join(c["docs"][:6]) + (" ..." if c["doc_count"] > 6 else ""))
                 for c in category_rows if c.get("objective_relevant")]))
            L.append("")
        if rel_phrases:
            L.append(_md_table(
                ["Activity phrase", "Category", "Occurrences", "Docs", "In documents"],
                [(r["phrase"], r["category"], r["occurrences"], r["doc_count"],
                  ", ".join(r["docs"][:6])) for r in rel_phrases[:30]]))
            L.append("")
        L.append("_The full breakdown below is ordered with these first (marked ★)._")
        L.append("")

    L.append("## 1. Content clusters (like documents)")
    L.append("")
    multi = [c for c in cluster_rows if c["size"] > 1]
    if multi:
        L.append(_md_table(
            ["#", "Docs", "Shared terms", "Types", "Representative", "Members"],
            [(c["id"], c["size"], ", ".join(c["shared_terms"][:5]),
              "/".join(c["kinds"]), c["representative"],
              ", ".join(c["docs"][:8]) + (" ..." if c["size"] > 8 else ""))
             for c in multi]))
    else:
        L.append("_No multi-document clusters above the similarity threshold._")
    singles = [c for c in cluster_rows if c["size"] == 1]
    if singles:
        L.append("")
        L.append(f"_Plus {len(singles)} document(s) with no close match: "
                 + ", ".join(c["docs"][0] for c in singles[:20])
                 + (" ..." if len(singles) > 20 else "") + "_")
    L.append("")

    L.append("## 2. Recurring activities (action phrases)")
    L.append("")
    if phrase_rows:
        L.append(_md_table(
            ["", "Activity phrase", "Category", "Occurrences", "Docs", "In documents"],
            [("★" if r.get("objective_relevant") else "",
              r["phrase"], r["category"], r["occurrences"], r["doc_count"],
              ", ".join(r["docs"][:6]) + (" ..." if r["doc_count"] > 6 else ""))
             for r in phrase_rows[:60]]))
    else:
        L.append("_No action phrase recurred across the corpus._")
    L.append("")

    L.append("## 3. Activity categories")
    L.append("")
    if category_rows:
        L.append(_md_table(
            ["", "Category", "Documents", "Mentions", "Verbs seen", "In documents"],
            [("★" if c.get("objective_relevant") else "",
              c["category"], c["doc_count"], c["mentions"], ", ".join(c["verbs"][:6]),
              ", ".join(c["docs"][:6]) + (" ..." if c["doc_count"] > 6 else ""))
             for c in category_rows]))
    else:
        L.append("_No categorised activity detected._")
    L.append("")

    p.write_text("\n".join(L), encoding="utf-8")
    return p


def _activity_json(session, cluster_rows, phrase_rows, category_rows) -> Path:
    p = session.path("activity_breakdown.json")
    p.write_text(json.dumps({
        "session_id": session.id,
        "content_clusters": cluster_rows,
        "action_phrases": phrase_rows,
        "activity_categories": category_rows,
    }, indent=2), encoding="utf-8")
    return p


# --------------------------------------------------------------------- manifest
def _manifest(session, cs, records) -> Path:
    p = session.path("manifest.json")
    p.write_text(json.dumps({
        "session_id": session.id,
        "source": cs["source"],
        "root": cs["root"],
        "generated_utc": cs["generated_utc"],
        "files": [
            {k: r[k] for k in ("rel", "path", "ext", "kind", "size", "sha256",
                               "mtime", "extractor", "words", "chars", "ok", "error")}
            for r in records
        ],
    }, indent=2), encoding="utf-8")
    return p
