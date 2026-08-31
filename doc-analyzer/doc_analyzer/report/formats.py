"""Render the three human-facing reports (synopsis, activity breakdown, tools &
versions) as real Office files - Word / spreadsheet / presentation - chosen per
report by the user.  The existing Markdown/JSON artifacts are still written by
``doc_analyzer.report.render``; these are produced *in addition*.

python-docx / openpyxl / python-pptx are base dependencies (requirements.txt).  If
a renderer is somehow unavailable or fails, a plain-text fallback is written so a
result file always exists and the reason is logged + recorded on the tool ledger.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

FMT_EXT = {"word": ".docx", "spreadsheet": ".xlsx", "presentation": ".pptx"}
FMT_LABEL = {"word": "Word document", "spreadsheet": "Spreadsheet",
             "presentation": "Presentation"}
LABEL_TO_FMT = {v: k for k, v in FMT_LABEL.items()}

DELIVERABLE_BASENAME = {"synopsis": "synopsis", "activity": "activity_breakdown",
                        "tools": "tools_and_versions", "objective": "objective_analysis"}
DELIVERABLE_TITLE = {"synopsis": "Document synopsis",
                     "activity": "Like-activity breakdown",
                     "tools": "Tools & versions used",
                     "objective": "Objective analysis"}


@dataclass
class Section:
    heading: str
    level: int = 1
    paragraphs: list[str] = field(default_factory=list)
    bullets: list[str] = field(default_factory=list)
    # (headers, rows)
    table: tuple[list[str], list[list]] | None = None


# --------------------------------------------------------------------- helpers
def resolve_fmt(run_opts, cfg, kind: str) -> str:
    """kind in {'synopsis','activity','tools','objective'} ->
    'word'|'spreadsheet'|'presentation'.  'objective' has no dedicated picker in
    the GUI - it follows the synopsis choice unless set explicitly."""
    chosen = None
    if run_opts is not None and getattr(run_opts, "formats", None):
        chosen = run_opts.formats.get(kind)
        if not chosen and kind == "objective":
            chosen = run_opts.formats.get("synopsis")
    if not chosen:
        chosen = getattr(cfg, "default_output_format", "word")
    chosen = str(chosen).strip().lower()
    return chosen if chosen in FMT_EXT else "word"


def deliverable_filename(run_opts, cfg, kind: str) -> str:
    return f"{DELIVERABLE_BASENAME[kind]}{FMT_EXT[resolve_fmt(run_opts, cfg, kind)]}"


def _cell(v) -> str:
    if isinstance(v, (list, tuple)):
        return ", ".join(str(x) for x in list(v)[:20])
    return str(v).replace("\r", " ").replace("\n", " ")


_BAD_SHEET_CHARS = set('[]:*?/\\')


def _sheet_name(name: str, used: set[str]) -> str:
    clean = "".join(c for c in str(name) if c not in _BAD_SHEET_CHARS).strip() or "Sheet"
    clean = clean[:31]
    base, i = clean, 2
    lower_used = {u.lower() for u in used}
    while clean.lower() in lower_used:
        suffix = f" {i}"
        clean = base[:31 - len(suffix)] + suffix
        i += 1
    used.add(clean)
    return clean


# --------------------------------------------------------------- section builders
def build_synopsis_sections(cs: dict, records: list[dict], analyzed: list[dict],
                            llm: dict | None, objective: str, *, detailed: bool) -> list[Section]:
    S: list[Section] = [Section("Objective", 1, paragraphs=[objective or "(not specified)"])]

    prof = cs.get("objective_profile") or {}
    find = cs.get("objective_findings") or {}
    if prof or find:
        paras: list[str] = []
        if prof.get("focus"):
            paras.append("Focus areas detected: " + ", ".join(prof["focus"]))
        if prof.get("entity_types"):
            paras.append("Information surfaced for this objective: "
                         + ", ".join(prof["entity_types"]))
        if find.get("relevant_document_count") is not None:
            paras.append(f"{find['relevant_document_count']} of "
                         f"{len(find.get('ranked_documents', []))} documents are "
                         "materially relevant to the objective.")
        for ks in (find.get("key_sentences") or [])[:10]:
            paras.append(f"[{ks['doc']}] {ks['sentence']}")
        S.append(Section("Findings for your objective", 1,
                         paragraphs=paras or ["(no objective-specific findings)"]))
        rd = find.get("ranked_documents") or []
        if rd:
            S.append(Section("Documents ranked by objective relevance", 1,
                             table=(["Rank", "Relevance", "Document", "Objective hits", "Lead point"],
                                    [[d["rank"], f"{d['relevance']:.3f}", d["doc"],
                                      ", ".join(d.get("hits", [])[:6]), d.get("why", "")]
                                     for d in rd])))
        eoi = find.get("entities_of_interest") or {}
        if eoi:
            S.append(Section("Objective-relevant details extracted", 1,
                             table=(["Type", "Values"],
                                    [[et, ", ".join(str(v) for v in vals[:30])]
                                     for et, vals in eoi.items()])))

    S += build_objective_plan_sections(cs)

    overview = [
        ["Source", cs.get("source", "")],
        ["Analyzed folder", cs.get("root", "")],
        ["Generated (UTC)", cs.get("generated_utc", "")],
        ["Files on disk", cs.get("files_found", "")],
        ["Documents analyzed", cs.get("doc_count", "")],
        ["Files skipped", cs.get("files_skipped", "")],
        ["Total words analyzed", f"{cs.get('total_words', 0):,}"],
        ["Bytes catalogued", f"{cs.get('total_bytes', 0):,}"],
    ]
    if cs.get("date_range"):
        overview.append(["Date range",
                         f"{cs['date_range'][0]} to {cs['date_range'][1]} "
                         f"({cs.get('date_basis', 'best guess')})"])
    S.append(Section("Overview", 1, table=(["Field", "Value"], overview)))

    if llm and llm.get("narrative"):
        S.append(Section("Narrative synopsis (Claude)", 1,
                         paragraphs=[f"model: {llm.get('model')}", llm["narrative"].strip()]))

    if cs.get("type_distribution"):
        S.append(Section("Document types", 1,
                         table=(["Type", "Documents"], [list(t) for t in cs["type_distribution"]])))
    if cs.get("ext_distribution"):
        S.append(Section("File extensions", 1,
                         table=(["Extension", "Documents"], [list(t) for t in cs["ext_distribution"]])))
    if cs.get("keywords"):
        S.append(Section("Top corpus keywords", 1, paragraphs=[", ".join(cs["keywords"])]))
    if cs.get("timeline"):
        S.append(Section("Timeline (best-guess document date)", 1,
                         table=(["Date", "Document", "Type"],
                                [[t.get("date") or "-", t.get("doc"), t.get("kind")]
                                 for t in cs["timeline"]])))

    by_rel = {r["rel"]: r for r in records}
    doc_rows = []
    for r in analyzed:
        rr = by_rel.get(r["rel"], r)
        doc_rows.append([rr["rel"], rr["kind"], rr["extractor"], f"{rr['size']:,}",
                         f"{rr['words']:,}", rr.get("best_date") or "-",
                         ", ".join(rr.get("keywords", [])[:8])])
    if doc_rows:
        S.append(Section("Per-document overview", 1,
                         table=(["Document", "Type", "Extractor", "Bytes", "Words",
                                 "Best date", "Keywords"], doc_rows)))

    if detailed:
        for r in analyzed:
            rr = by_rel.get(r["rel"], r)
            paras: list[str] = []
            ent = rr.get("entities") or {}
            for name in ("dates", "money", "percent", "organizations", "people",
                         "email", "url", "phone", "ref_id", "acronyms"):
                if ent.get(name):
                    paras.append(f"{name}: " + ", ".join(str(x) for x in ent[name][:12]))
            if rr.get("summary"):
                paras.append(" ".join(rr["summary"]))
            S.append(Section(rr["rel"], 2, paragraphs=paras or ["(no extractive summary)"]))

    skipped = [r for r in records if not r["ok"]]
    if skipped:
        S.append(Section("Skipped / errored files", 1,
                         table=(["File", "Reason"],
                                [[r["rel"], r["error"] or "no extractable text"] for r in skipped])))
    return S


_PLAN_TABLE_ORDER = [
    ("resources_rounds", "Resources & rounds (name location in the naming scheme)"),
    ("files", "Per file - resource, round, name location"),
    ("per_resource_task", "Per resource - tasks: qty / total / average by round"),
    ("per_resource_type", "Per resource - activity types: qty / total / average by round"),
    ("task_by_round", "All resources - tasks by round: count / total / average"),
    ("task_totals", "All resources - tasks: totals across all rounds"),
    ("type_by_round", "All resources - activity types by round: count / total / average"),
    ("type_totals", "All resources - activity types: totals across all rounds"),
    ("class_by_round", "Productive vs non-productive by round"),
    ("resource_ranking", "Resource ranking (each resource vs the others)"),
    ("summary_pivot", "SUMMARY - all metrics by technician x round"),
    ("notes_summary", "Notes review - trend + issue / coaching / disciplinary counts"),
    ("resource_x_type", "Resource x activity type (entry counts)"),
    ("resource_x_task", "Resource x task (task counts)"),
]


def _requested_items_section(pr: dict) -> "Section | None":
    items = pr.get("requested_items") or []
    if not items:
        return None
    tmap = {
        "per_resource_task": "see 'Per resource - tasks' table",
        "per_resource_type": "see 'Per resource - activity types' table",
        "class_by_round": "see 'Productive vs non-productive by round' table",
        "resource_ranking": "see 'Resource ranking' table",
        "notes_summary": "see 'Notes review' table + coaching instances",
    }
    rows = [[it["n"], it["text"], tmap.get(it.get("table"), "see the summary table")]
            for it in items]
    return Section("Deliverables you asked for (one table each)", 1,
                   table=(["#", "Requested item", "Answered by"], rows),
                   paragraphs=["Each numbered item below is answered by the table named "
                               "in the third column; the SUMMARY table combines them all."])


def build_objective_plan_sections(cs: dict) -> list[Section]:
    pr = cs.get("plan_results") or {}
    if not pr:
        return []
    plan = cs.get("analysis_plan") or {}
    ridx = cs.get("resource_index") or {}
    ans = cs.get("objective_answers") or {}
    narr = cs.get("resource_notes_narrative") or {}
    gt = pr.get("grand_totals") or {}
    S: list[Section] = []

    head = [f"Planner: {plan.get('source', 'heuristic')}; write-up: {ans.get('source', 'template')}"]
    if plan.get("restated_goal"):
        head.append("Understood as: " + plan["restated_goal"])
    head.append(f"Unique resources: {ridx.get('resource_count', 0)}; "
                f"expected rounds each >= {plan.get('expected_rounds_min', 2)}")
    if plan.get("nonprod_tasks") or plan.get("nonprod_types"):
        head.append("Unproductive rule (from your objective): tasks "
                    f"{plan.get('nonprod_tasks') or '(none)'}; types "
                    f"{plan.get('nonprod_types') or '(none)'}")
    if gt:
        head.append(f"Totals: {gt.get('activity_lines')} activity lines, "
                    f"{gt.get('total_minutes')} min, {gt.get('productive_pct')}% productive, "
                    f"avg {gt.get('avg_minutes')} min/line")
    S.append(Section("Objective-driven analysis", 1, paragraphs=head))

    ri = _requested_items_section(pr)
    if ri:
        S.append(ri)

    if ridx.get("anomalies"):
        S.append(Section("Naming / round issues", 1,
                         bullets=[str(a) for a in ridx["anomalies"][:40]]))

    tables = pr.get("tables") or {}
    for key, title in _PLAN_TABLE_ORDER:
        t = tables.get(key)
        if t and t.get("rows"):
            S.append(Section(title, 1, table=(t["columns"], t["rows"]),
                             paragraphs=[t["note"]] if t.get("note") else []))

    for m in pr.get("metrics") or []:
        if not m.get("error") and m.get("rows"):
            S.append(Section(f"Metric: {m.get('label') or m.get('id')}", 1,
                             table=(m["columns"], m["rows"])))

    if narr:
        S.append(Section("Round-over-round read (per resource)", 1,
                         bullets=[str(v) for v in narr.values() if str(v).strip()]))

    coaching = pr.get("coaching_candidates") or []
    if coaching:
        rows = [[c.get("resource"), c.get("round"), c.get("line"),
                 c.get("category", c.get("trigger", "")), c.get("bucket", ""), c.get("task"),
                 c.get("duration_min"), (c.get("note") or "")[:200],
                 c.get("recommendation", "")] for c in coaching]
        S.append(Section("Coaching / corrective-action instances (from notes)", 1,
                         table=(["Resource", "Round", "Line", "Category", "Bucket", "Task",
                                 "Min", "Note", "Recommendation"], rows)))

    if ans.get("markdown"):
        S.append(Section("Answers to the objective", 1,
                         paragraphs=[ln for ln in ans["markdown"].splitlines() if ln.strip()]))
    return S


def build_objective_analysis_sections(cs: dict, objective: str) -> list[Section]:
    """Standalone Objective analysis deliverable - the objective, then every
    plan-driven table (per-resource breakouts, the summary pivot, the notes
    review)."""
    S: list[Section] = [Section("Objective", 1, paragraphs=[objective or "(not specified)"])]
    S += build_objective_plan_sections(cs)
    if len(S) == 1:
        S.append(Section("No objective analysis", 1,
                         paragraphs=["This run produced no structured objective analysis "
                                     "(no resource/round naming scheme was detected in the "
                                     "source files)."]))
    return S


def build_activity_sections(cs: dict, cluster_rows: list[dict], phrase_rows: list[dict],
                            category_rows: list[dict], objective: str) -> list[Section]:
    S: list[Section] = [Section("Objective", 1, paragraphs=[objective or "(not specified)"])]
    S.append(Section("Summary", 1, paragraphs=[
        f"Source: {cs.get('source', '')}",
        f"Documents analyzed: {cs.get('doc_count', 0)}",
        "Documents are grouped three ways: by content similarity (near-duplicate / "
        "same-template families), by recurring action phrases, and by business-activity "
        "category.",
    ]))

    rel_cats = cs.get("objective_activities") or []
    if objective and rel_cats:
        S.append(Section("Activities most relevant to your objective", 1,
                         table=(["Category", "Documents", "Mentions", "Verbs seen", "In documents"],
                                [[c["category"], c["doc_count"], c["mentions"],
                                  ", ".join(c["verbs"][:6]), ", ".join(c["docs"][:6])]
                                 for c in category_rows if c.get("objective_relevant")])))

    multi = [c for c in cluster_rows if c["size"] > 1]
    if multi:
        S.append(Section("Content clusters (like documents)", 1,
                         table=(["#", "Docs", "Shared terms", "Types", "Representative", "Members"],
                                [[c["id"], c["size"], ", ".join(c["shared_terms"][:5]),
                                  "/".join(c["kinds"]), c["representative"],
                                  ", ".join(c["docs"][:8])] for c in multi])))
    else:
        S.append(Section("Content clusters (like documents)", 1,
                         paragraphs=["No multi-document clusters above the similarity threshold."]))

    if phrase_rows:
        S.append(Section("Recurring activities (action phrases)", 1,
                         table=(["Activity phrase", "Category", "Occurrences", "Docs", "In documents"],
                                [[r["phrase"], r["category"], r["occurrences"], r["doc_count"],
                                  ", ".join(r["docs"][:6])] for r in phrase_rows[:60]])))
    else:
        S.append(Section("Recurring activities (action phrases)", 1,
                         paragraphs=["No action phrase recurred across the corpus."]))

    if category_rows:
        S.append(Section("Activity categories", 1,
                         table=(["Category", "Documents", "Mentions", "Verbs seen", "In documents"],
                                [[c["category"], c["doc_count"], c["mentions"],
                                  ", ".join(c["verbs"][:6]), ", ".join(c["docs"][:6])]
                                 for c in category_rows])))
    else:
        S.append(Section("Activity categories", 1, paragraphs=["No categorised activity detected."]))
    return S


def build_tools_sections(ld: dict, objective: str) -> list[Section]:
    S: list[Section] = [Section("Objective", 1, paragraphs=[objective or "(not specified)"])]

    answers = ld.get("answers") or {}
    if answers:
        S.append(Section("Analysis answers", 1,
                         table=(["Question", "Answer"], [[k, v] for k, v in answers.items()])))

    gen = ld.get("generated_files") or []
    if gen:
        S.append(Section("Generated files", 1,
                         table=(["Deliverable", "Format", "File"],
                                [[g.get("deliverable", ""), g.get("format", ""), g.get("file", "")]
                                 for g in gen])))

    S.append(Section("Run", 1, paragraphs=[
        f"Session started (UTC): {ld.get('session_started_utc', '')}",
        f"Report generated (UTC): {ld.get('generated_utc', '')}",
        f"Distinct tools recorded: {ld.get('tool_count', 0)}",
    ]))

    rows = []
    for e in ld.get("tools", []):
        rows.append([e.get("category"), e.get("name"), e.get("version") or "not detected",
                     "; ".join(e.get("purposes", [])) or "-", e.get("detail") or "-"])
    S.append(Section("Tools & versions", 1,
                     table=(["Category", "Tool", "Version", "Used for", "Notes"], rows)))
    return S


# ------------------------------------------------------------------- renderers
def render_word(title: str, subtitle: str, sections: list[Section], path: Path) -> Path:
    from docx import Document

    doc = Document()
    doc.add_heading(title, 0)
    if subtitle:
        p = doc.add_paragraph(subtitle)
        if p.runs:
            p.runs[0].italic = True

    for s in sections:
        doc.add_heading(s.heading, min(max(s.level, 1), 4))
        for para in s.paragraphs:
            doc.add_paragraph(str(para))
        for b in s.bullets:
            try:
                doc.add_paragraph(str(b), style="List Bullet")
            except KeyError:
                doc.add_paragraph("- " + str(b))
        if s.table:
            headers, rows = s.table
            headers = [str(h) for h in headers]
            t = doc.add_table(rows=1, cols=max(len(headers), 1))
            try:
                t.style = "Table Grid"
            except KeyError:
                pass
            for i, h in enumerate(headers):
                run = t.rows[0].cells[i].paragraphs[0].add_run(h)
                run.bold = True
            for row in rows:
                cells = t.add_row().cells
                for i in range(len(headers)):
                    cells[i].text = _cell(row[i]) if i < len(row) else ""
    doc.save(str(path))
    return path


def render_spreadsheet(title: str, subtitle: str, sections: list[Section], path: Path) -> Path:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font

    wb = Workbook()
    ov = wb.active
    ov.title = "Overview"
    ov["A1"] = title
    ov["A1"].font = Font(bold=True, size=14)
    r = 2
    if subtitle:
        ov[f"A{r}"] = subtitle
        r += 1
    r += 1

    used = {"Overview"}
    for s in sections:
        if s.table:
            headers, rows = s.table
            headers = [str(h) for h in headers] or ["Value"]
            name = _sheet_name(s.heading, used)
            ws = wb.create_sheet(title=name)
            ws.append(headers)
            for c in ws[1]:
                c.font = Font(bold=True)
            ws.freeze_panes = "A2"
            for row in rows:
                ws.append([_cell(v) for v in list(row)[:len(headers)]])
            for col_cells in ws.columns:
                longest = max((len(str(c.value)) for c in col_cells if c.value is not None), default=10)
                ws.column_dimensions[col_cells[0].column_letter].width = min(60, max(10, longest + 2))
            ov[f"A{r}"] = s.heading
            ov[f"A{r}"].font = Font(bold=True)
            ov[f"B{r}"] = f"see sheet '{name}' ({len(rows)} rows)"
            r += 1
        else:
            ov[f"A{r}"] = s.heading
            ov[f"A{r}"].font = Font(bold=True)
            r += 1
            for para in list(s.paragraphs) + list(s.bullets):
                cell = ov[f"A{r}"]
                cell.value = str(para)
                cell.alignment = Alignment(wrap_text=True, vertical="top")
                r += 1
            r += 1
    ov.column_dimensions["A"].width = 32
    ov.column_dimensions["B"].width = 60
    wb.save(str(path))
    return path


def _text_slides(prs, sec: Section) -> None:
    lines = [str(x) for x in list(sec.paragraphs)] + [f"• {b}" for b in sec.bullets]
    lines = [x for x in lines if x.strip()] or ["(none)"]
    per = 10
    for ci in range(0, len(lines), per):
        chunk = lines[ci:ci + per]
        slide = prs.slides.add_slide(prs.slide_layouts[1])
        slide.shapes.title.text = sec.heading + ("" if ci == 0 else " (cont.)")
        body = slide.placeholders[1].text_frame
        body.word_wrap = True
        body.text = chunk[0][:600]
        for ln in chunk[1:]:
            body.add_paragraph().text = ln[:600]


def _table_slide(prs, sec: Section) -> None:
    from pptx.util import Inches, Pt

    headers, rows = sec.table
    headers = [str(h) for h in headers][:8] or ["Value"]
    ncol = len(headers)
    show = list(rows)[:14]
    nrow = len(show) + 1

    slide = prs.slides.add_slide(prs.slide_layouts[5])
    slide.shapes.title.text = sec.heading
    tbl = slide.shapes.add_table(nrow, ncol, Inches(0.4), Inches(1.5),
                                 Inches(9.2), Inches(min(5.0, 0.35 * nrow))).table
    for c, h in enumerate(headers):
        tbl.cell(0, c).text = h
    for ri, row in enumerate(show, start=1):
        vals = list(row)
        for c in range(ncol):
            tbl.cell(ri, c).text = _cell(vals[c]) if c < len(vals) else ""
    for trow in tbl.rows:
        for cell in trow.cells:
            for para in cell.text_frame.paragraphs:
                for run in para.runs:
                    run.font.size = Pt(9)
    if len(rows) > len(show):
        tb = slide.shapes.add_textbox(Inches(0.4), Inches(1.6 + min(5.0, 0.35 * nrow)),
                                      Inches(9.2), Inches(0.4))
        tb.text_frame.text = f"({len(rows) - len(show)} more rows — see the full report)"


def render_presentation(title: str, subtitle: str, sections: list[Section], path: Path) -> Path:
    from pptx import Presentation

    prs = Presentation()
    s0 = prs.slides.add_slide(prs.slide_layouts[0])
    s0.shapes.title.text = title
    if len(s0.placeholders) > 1:
        s0.placeholders[1].text = subtitle or ""
    for sec in sections:
        if sec.table:
            _table_slide(prs, sec)
        else:
            _text_slides(prs, sec)
    prs.save(str(path))
    return path


def _write_txt_fallback(title: str, subtitle: str, sections: list[Section], path: Path) -> Path:
    L = [title, subtitle or "", ""]
    for s in sections:
        L.append("#" * min(max(s.level, 1), 4) + " " + s.heading)
        L.extend(str(p) for p in s.paragraphs)
        L.extend("- " + str(b) for b in s.bullets)
        if s.table:
            headers, rows = s.table
            L.append(" | ".join(str(h) for h in headers))
            for row in rows:
                L.append(" | ".join(_cell(c) for c in row))
        L.append("")
    path.write_text("\n".join(L), encoding="utf-8")
    return path


_RENDERERS = {"word": ("docx", render_word),
              "spreadsheet": ("openpyxl", render_spreadsheet),
              "presentation": ("pptx", render_presentation)}


def _render(session, kind: str, fmt: str, subtitle: str, sections: list[Section]) -> dict:
    base = DELIVERABLE_BASENAME[kind]
    title = DELIVERABLE_TITLE[kind]
    path = session.path(f"{base}{FMT_EXT[fmt]}")
    modname, renderer = _RENDERERS[fmt]
    try:
        session.ledger.record_module(modname, f"render the {FMT_LABEL[fmt].lower()} deliverable")
        renderer(title, subtitle, sections, path)
        session.detail(f"deliverable: {path.name}  ({FMT_LABEL[fmt]})")
        return {"deliverable": kind, "format": fmt, "file": path.name, "ok": True}
    except Exception as exc:  # noqa: BLE001 - always leave a usable file behind
        fallback = session.path(f"{base}.txt")
        _write_txt_fallback(title, subtitle, sections, fallback)
        session.warn(f"Could not render {base} as {FMT_LABEL[fmt]} "
                     f"({type(exc).__name__}: {exc}); wrote {fallback.name} instead.")
        session.ledger.record("analysis-step", "deliverable rendering",
                              detail=f"{base} -> {fmt} failed: {exc}",
                              purpose="format-specific result documents")
        return {"deliverable": kind, "format": fmt, "file": fallback.name, "ok": False}


def write_report_deliverables(session, run_opts, cfg, *, corpus_summary, records, analyzed,
                              cluster_rows, phrase_rows, category_rows, llm) -> list[dict]:
    """Render the synopsis + activity-breakdown deliverables.  The tools & versions
    deliverable is rendered separately (see ``write_tools_deliverable``) because it
    needs the finalized tool ledger."""
    session.ledger.record("analysis-step", "deliverable rendering",
                          detail="doc_analyzer.report.formats",
                          purpose="format-specific result documents (Word / spreadsheet / presentation)")
    subtitle = f"session {session.id}  ·  {corpus_summary.get('source', '')}"
    objective = corpus_summary.get("objective") or ""
    out: list[dict] = []

    fmt = resolve_fmt(run_opts, cfg, "synopsis")
    out.append(_render(session, "synopsis", fmt, subtitle,
                       build_synopsis_sections(corpus_summary, records, analyzed, llm,
                                               objective, detailed=(fmt == "word"))))

    fmt = resolve_fmt(run_opts, cfg, "activity")
    out.append(_render(session, "activity", fmt, subtitle,
                       build_activity_sections(corpus_summary, cluster_rows, phrase_rows,
                                               category_rows, objective)))

    if corpus_summary.get("plan_results"):
        fmt = resolve_fmt(run_opts, cfg, "objective")
        out.append(_render(session, "objective", fmt, subtitle,
                           build_objective_analysis_sections(corpus_summary, objective)))
    return out


def write_tools_deliverable(session, run_opts, cfg, *, ledger, objective: str,
                            subtitle: str) -> dict:
    fmt = resolve_fmt(run_opts, cfg, "tools")
    sections = build_tools_sections(ledger.as_dict(), objective)
    return _render(session, "tools", fmt, subtitle, sections)
