"""Orchestrates one full analysis run, emitting verbose numbered steps as it goes."""
from __future__ import annotations

import hashlib
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ..extract import extract_file, is_supported, SUPPORTED_EXTS
from ..report import formats as report_formats
from ..report import render as report_render
from ..sources import resolve_source
from . import activity as activity_mod
from . import entities as entities_mod
from . import execute_plan as execute_mod
from . import facts as facts_mod
from . import objective as objective_mod
from . import plan as plan_mod
from . import resources as resources_mod
from . import summarize_llm
from . import synopsis as syn
from .tfidf import Corpus, cluster, tokenize

_SKIP_DIRS = {".git", ".svn", ".hg", "__pycache__", "node_modules", ".venv", "venv",
              ".idea", ".vscode", "_fetched"}


@dataclass
class RunOptions:
    """User answers that shape one run (beyond the data source itself)."""
    objective: str = ""
    # "synopsis" | "activity" | "tools"  ->  "word" | "spreadsheet" | "presentation"
    formats: dict[str, str] = field(default_factory=dict)
    # also write the result documents into a subfolder of the analyzed folder
    write_to_source: bool = True


@dataclass
class AnalyzeResult:
    session_dir: Path
    source_label: str
    files_found: int = 0
    files_analyzed: int = 0
    files_skipped: int = 0
    clusters: int = 0
    activities: int = 0
    artifacts: dict[str, str] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    deliver_dir: Path | None = None
    deliverables: list[dict] = field(default_factory=list)


def _sha256(path: Path, limit: int) -> str:
    h = hashlib.sha256()
    read = 0
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 256), b""):
            h.update(chunk)
            read += len(chunk)
            if read >= limit:
                h.update(b"<truncated>")
                break
    return h.hexdigest()


def _iter_files(root: Path, recursive: bool, skip_prefixes: tuple[str, ...] = ()):
    def _skip(name: str) -> bool:
        return (name in _SKIP_DIRS or name.startswith(".")
                or any(name.startswith(p) for p in skip_prefixes if p))

    if recursive:
        for dpath, dnames, fnames in os.walk(root):
            dnames[:] = [d for d in dnames if not _skip(d)]
            for fn in fnames:
                yield Path(dpath) / fn
    else:
        for p in sorted(root.iterdir()):
            if p.is_file():
                yield p


def analyze(session, cfg, source_spec: dict, run_opts: "RunOptions | None" = None) -> AnalyzeResult:
    t0 = datetime.now(timezone.utc)
    run_opts = run_opts or RunOptions()
    objective = (run_opts.objective or "").strip()
    obj = objective_mod.parse(objective)
    kind = (source_spec.get("kind") or "local").lower()

    session.step(f"Resolving data source ({kind}) ...")
    root, source_label = resolve_source(session, cfg, source_spec)
    session.detail(f"Analyzing folder: {root}")
    result = AnalyzeResult(session_dir=session.dir, source_label=source_label)

    # -- record the run's answers (requirement: objective + format choices) ----
    deliver_dir = _prepare_deliver_dir(session, cfg, root, kind, run_opts)
    answers = {
        "Where is the data?": source_label,
        "What do you want to ascertain from this analysis?": objective or "(not specified)",
        "Synopsis format": report_formats.FMT_LABEL[report_formats.resolve_fmt(run_opts, cfg, "synopsis")],
        "Activity breakdown format": report_formats.FMT_LABEL[report_formats.resolve_fmt(run_opts, cfg, "activity")],
        "Objective analysis format": report_formats.FMT_LABEL[report_formats.resolve_fmt(run_opts, cfg, "objective")],
        "Tools & versions format": report_formats.FMT_LABEL[report_formats.resolve_fmt(run_opts, cfg, "tools")],
    }
    session.step("Recording the analysis objective and answers ...")
    for q, a in answers.items():
        session.detail(f"{q}  ->  {a}")
    session.ledger.set_answers(answers)

    session.step("Interpreting the analysis objective ...")
    if obj.is_empty():
        session.detail("No objective given - running a full, unfocused analysis.")
    else:
        session.detail(f"Understood objective -> {obj.describe()}")
        if obj.focus:
            session.detail("Focus areas: " + ", ".join(obj.focus)
                           + "  ->  will surface " + ", ".join(obj.entity_type_labels()))
        if obj.is_question:
            session.detail("Objective reads as a question - reports will lead with a direct answer.")
        session.ledger.record("analysis-step", "objective interpretation",
                              detail=f"focus={obj.focus or 'none'}; "
                                     f"terms={obj.terms[:12]}; phrases={obj.phrases}",
                              purpose="steer summaries, document ranking and activity relevance "
                                      "toward the stated objective")

    # -- enumerate ------------------------------------------------------------
    session.step("Scanning for documents ...")
    # never re-ingest our own mirrored result folders from a previous run
    skip_prefixes = (cfg.results_subfolder_prefix, "doc-analyzer-results")
    all_files = list(_iter_files(root, cfg.recursive, skip_prefixes))
    candidates = [p for p in all_files if is_supported(p)]
    others = len(all_files) - len(candidates)
    session.detail(f"{len(all_files)} files on disk; {len(candidates)} have a supported "
                   f"extension ({others} skipped as unsupported/binary).")
    session.detail(f"Supported extensions: {', '.join(sorted(SUPPORTED_EXTS))}")
    if len(candidates) > cfg.max_docs:
        session.warn(f"Capping at analysis.max_docs={cfg.max_docs} (found {len(candidates)}).")
        candidates = candidates[: cfg.max_docs]
    result.files_found = len(all_files)

    # -- extract ------------------------------------------------------------
    session.step(f"Extracting text + metadata from {len(candidates)} documents ...")
    records: list[dict] = []
    doc_texts: dict[str, str] = {}
    used_extractors: set[str] = set()
    for i, path in enumerate(candidates, 1):
        rel = str(path.relative_to(root))
        try:
            size = path.stat().st_size
            mtime = datetime.fromtimestamp(path.stat().st_mtime)
        except OSError as exc:
            session.warn(f"[{i}/{len(candidates)}] cannot stat {rel}: {exc}")
            result.files_skipped += 1
            continue

        if size > cfg.max_file_bytes:
            session.detail(f"[{i}/{len(candidates)}] {rel}: {size/1e6:.1f} MB exceeds "
                           f"max_file_mb; catalogued but body not parsed.")
            res = None
        else:
            res = extract_file(path, cfg)

        rec = {
            "rel": rel, "path": str(path), "ext": path.suffix.lower(),
            "size": size, "sha256": _sha256(path, cfg.max_file_bytes),
            "mtime": mtime.isoformat(timespec="seconds"),
            "mtime_date": mtime.date().isoformat(),
            "kind": "oversized" if res is None else res.kind,
            "extractor": "skipped (too large)" if res is None else res.extractor,
            "words": 0 if res is None else res.words,
            "chars": 0 if res is None else res.chars,
            "ok": False if res is None else res.ok,
            "error": "" if res is None else res.error,
            "meta": {} if res is None else res.meta,
            "keywords": [], "entities": {}, "summary": [], "best_date": "",
            "iso_dates": [], "relevance": 0.0, "relevance_rank": 0, "objective_hits": [],
        }

        if res is not None and res.ok and res.text.strip():
            doc_texts[rel] = res.text
            used_extractors.add(res.extractor)
            for mod, purpose in res.tools:
                session.ledger.record_module(mod, purpose)
            iso = entities_mod.iso_dates(res.text)
            rec["iso_dates"] = iso
            rec["best_date"] = (iso[0] if iso else "") or rec["mtime_date"]
            result.files_analyzed += 1
            session.detail(f"[{i}/{len(candidates)}] {rel}: {res.words} words, "
                           f"{res.chars} chars via {res.extractor}")
        else:
            result.files_skipped += 1
            reason = (res.error if res is not None else "file too large") or "no extractable text"
            session.detail(f"[{i}/{len(candidates)}] {rel}: SKIPPED - {reason}")
            if res is not None and res.error:
                result.errors.append(f"{rel}: {res.error}")
        records.append(rec)

    session.ledger.record("analysis-step", "text extraction",
                          detail=f"extractors used: {', '.join(sorted(used_extractors)) or 'none'}",
                          purpose="convert documents to analyzable text")

    if not doc_texts:
        session.warn("No documents yielded analyzable text. Writing an empty report.")

    # -- corpus / tf-idf --------------------------------------------------
    session.step("Building TF-IDF model of the corpus ...")
    corpus = Corpus(list(doc_texts.items()))
    session.detail(f"Vocabulary: {len(corpus.df)} distinct terms across {corpus.n} documents.")
    session.ledger.record("analysis-step", "TF-IDF vectorization",
                          detail="pure-Python (doc_analyzer.analyze.tfidf)",
                          purpose="keyword ranking + document similarity")

    obj_vec = corpus.query_vector(obj.all_tokens) if not obj.is_empty() else {}
    summary_boost = {t: objective_mod.SUMMARY_BOOST for t in obj.all_tokens}
    if obj_vec:
        session.detail(f"Objective vector spans {len(obj_vec)} corpus terms; "
                       "per-document summaries and ranking are now objective-weighted.")

    # -- per-document synopsis -----------------------------------------------
    session.step("Writing per-document synopses (keywords, entities, summary) ...")
    for rec in records:
        rel = rec["rel"]
        if rel not in doc_texts:
            continue
        text = doc_texts[rel]
        rec["keywords"] = [t for t, _ in corpus.top_terms(rel, 12)]
        rec["entities"] = entities_mod.extract_entities(text)
        rec["summary"] = syn.extractive_summary(text, corpus, rel, 4, boost=summary_boost)
        if obj_vec:
            rec["relevance"] = round(corpus.cosine_to(rel, obj_vec), 4)
            rec["objective_hits"] = sorted(set(rec["keywords"]) & obj.all_tokens)
            session.detail(f"{rel}: top terms -> {', '.join(rec['keywords'][:6])}"
                           f"  |  objective relevance {rec['relevance']:.3f}"
                           + (f"  (hits: {', '.join(rec['objective_hits'][:6])})"
                              if rec["objective_hits"] else ""))
        else:
            session.detail(f"{rel}: top terms -> {', '.join(rec['keywords'][:6])}")

    if obj_vec:
        session.step("Ranking documents against the objective ...")
        ranked = sorted((r for r in records if r["rel"] in doc_texts),
                        key=lambda r: -r.get("relevance", 0.0))
        for i, r in enumerate(ranked, 1):
            r["relevance_rank"] = i
        n_rel = sum(1 for r in ranked
                    if r.get("relevance", 0.0) >= objective_mod.DEFAULT_MIN_RELEVANCE)
        session.detail(f"{n_rel} of {len(ranked)} documents are materially relevant to the objective.")
        for r in ranked[:8]:
            session.detail(f"  #{r['relevance_rank']:>2}  {r['rel']}  ({r.get('relevance', 0.0):.3f})")

    session.ledger.record("analysis-step", "entity extraction",
                          detail="regex/heuristic (doc_analyzer.analyze.entities)",
                          purpose="dates, money, orgs, people, refs, contacts")
    session.ledger.record("analysis-step", "extractive summarization",
                          detail="TF-IDF sentence scoring (doc_analyzer.analyze.synopsis)",
                          purpose="per-document summary paragraphs")

    # -- corpus synopsis --------------------------------------------------
    session.step("Aggregating corpus-level synopsis ...")
    analyzed_records = [r for r in records if r["rel"] in doc_texts]
    corpus_summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "source": source_label,
        "objective": objective,
        "root": str(root),
        "doc_count": len(analyzed_records),
        "files_found": result.files_found,
        "files_skipped": result.files_skipped,
        "total_words": sum(r["words"] for r in analyzed_records),
        "total_bytes": sum(r["size"] for r in records),
        "type_distribution": syn.type_distribution(analyzed_records),
        "ext_distribution": syn.ext_distribution(analyzed_records),
        "keywords": [t for t, _ in syn.corpus_keywords(corpus, 25)],
        "timeline": syn.timeline(analyzed_records),
    }
    content_dates = sorted({d for r in analyzed_records for d in r.get("iso_dates", [])})
    if content_dates:
        corpus_summary["date_range"] = [content_dates[0], content_dates[-1]]
        corpus_summary["date_basis"] = "dates found in document content"
    else:
        mdates = [r["best_date"] for r in analyzed_records if r["best_date"]]
        if mdates:
            corpus_summary["date_range"] = [min(mdates), max(mdates)]
            corpus_summary["date_basis"] = "file modification times (no dates in content)"
    session.detail(f"Types: {corpus_summary['type_distribution']}")
    session.detail(f"Top keywords: {', '.join(corpus_summary['keywords'][:12])}")

    # -- objective-focused findings (the direct answer, computed locally) ---
    if not obj.is_empty():
        session.step("Assembling findings for the stated objective ...")
        analyzed_sorted = sorted(analyzed_records, key=lambda r: -r.get("relevance", 0.0))
        ranked_docs = [{
            "doc": r["rel"], "relevance": r.get("relevance", 0.0),
            "rank": r.get("relevance_rank", 0), "hits": r.get("objective_hits", []),
            "why": (r.get("summary") or [""])[0][:240],
        } for r in analyzed_sorted]
        etypes = obj.entity_types()
        ent_of_interest: dict[str, list] = {}
        for et in etypes:
            seen: list = []
            for r in analyzed_sorted:
                for v in (r.get("entities") or {}).get(et, []) or []:
                    if v not in seen:
                        seen.append(v)
            if seen:
                ent_of_interest[et] = seen[:40]
        key_sents = objective_mod.key_sentences(doc_texts, obj_vec, 12)
        corpus_summary["objective_profile"] = {
            "raw": obj.raw, "terms": obj.terms, "phrases": obj.phrases,
            "focus": obj.focus, "is_question": obj.is_question, "entity_types": etypes,
        }
        corpus_summary["objective_findings"] = {
            "ranked_documents": ranked_docs,
            "relevant_document_count": sum(
                1 for d in ranked_docs
                if d["relevance"] >= objective_mod.DEFAULT_MIN_RELEVANCE),
            "key_sentences": key_sents,
            "entities_of_interest": ent_of_interest,
        }
        session.detail(f"{len(key_sents)} objective-relevant sentences pulled; "
                       f"entities surfaced: {', '.join(ent_of_interest) or 'none'}")

    # -- like-activity breakdown --------------------------------------------
    session.step("Grouping 'like' documents by content similarity ...")
    groups = cluster(corpus, cfg.similarity_threshold)
    cluster_rows = _describe_clusters(groups, corpus, records)
    multi = [c for c in cluster_rows if c["size"] > 1]
    session.detail(f"{len(cluster_rows)} groups ({len(multi)} with 2+ documents) at "
                   f"similarity>={cfg.similarity_threshold}.")
    for c in multi:
        session.detail(f"  group '{c['label']}': {c['size']} docs -> {', '.join(c['docs'][:4])}"
                       + (" ..." if c["size"] > 4 else ""))

    session.step("Detecting recurring activities across documents ...")
    phrase_rows, category_rows = activity_mod.analyze_activity(doc_texts)
    session.detail(f"{len(phrase_rows)} recurring action phrases; "
                   f"{len(category_rows)} activity categories.")
    for cr in category_rows:
        session.detail(f"  {cr['category']}: {cr['mentions']} mentions in {cr['doc_count']} docs "
                       f"(verbs: {', '.join(cr['verbs'][:5])})")
    session.ledger.record("analysis-step", "activity mining",
                          detail="verb-lemma + category map (doc_analyzer.analyze.activity)",
                          purpose="'like activity' breakdown across documents")

    if not obj.is_empty():
        for c in category_rows:
            toks = set(tokenize(c["category"])) | set(c.get("verbs", []))
            c["objective_relevant"] = objective_mod.activity_relevance(toks, c["category"], obj)
        for pr in phrase_rows:
            toks = set(tokenize(pr["phrase"])) | {pr.get("verb", "")}
            pr["objective_relevant"] = objective_mod.activity_relevance(toks, pr["category"], obj)
        category_rows.sort(key=lambda c: (not c.get("objective_relevant"), -c["mentions"]))
        phrase_rows.sort(key=lambda p: (not p.get("objective_relevant"),
                                        -p["occurrences"], -p["doc_count"]))
        rel_cats = [c["category"] for c in category_rows if c.get("objective_relevant")]
        corpus_summary["objective_activities"] = rel_cats
        session.detail("Activity categories on-objective: " + (", ".join(rel_cats) or "none flagged"))

    corpus_summary["activity_categories"] = [(c["category"], c["doc_count"]) for c in category_rows]

    # -- objective-driven analysis: local model plans, engine computes, model writes
    if not obj.is_empty():
        _run_objective_plan(session, cfg, objective, records, analyzed_records, corpus_summary)

    # -- optional Claude --------------------------------------------------
    session.step("Optional Claude narrative synopsis ...")
    llm = summarize_llm.enhance(session, cfg, corpus_summary,
                                [r for r in records if r["rel"] in doc_texts],
                                objective=objective, obj=obj)

    # -- render (Markdown / JSON artifacts) ---------------------------------
    session.step("Rendering session reports ...")
    artifacts = report_render.write_all(
        session=session, corpus_summary=corpus_summary, records=records,
        analyzed=analyzed_records, cluster_rows=cluster_rows,
        phrase_rows=phrase_rows, category_rows=category_rows, llm=llm,
    )
    result.artifacts = {k: str(v) for k, v in artifacts.items()}
    result.clusters = len(multi)
    result.activities = len(category_rows)

    # -- choose-format deliverables (synopsis + activity breakdown) --------
    session.step("Rendering the choose-format deliverables (synopsis, activity breakdown) ...")
    deliverables = report_formats.write_report_deliverables(
        session, run_opts, cfg, corpus_summary=corpus_summary, records=records,
        analyzed=analyzed_records, cluster_rows=cluster_rows,
        phrase_rows=phrase_rows, category_rows=category_rows, llm=llm,
    )
    for d in deliverables:
        result.artifacts[f"{d['deliverable']}_deliverable"] = str(session.path(d["file"]))

    # -- assemble the list of every result file (recorded in both the tool
    #    document and, via the steps above, session.log) --------------------
    tools_fmt = report_formats.resolve_fmt(run_opts, cfg, "tools")
    generated = list(deliverables)
    generated.append({"deliverable": "tools", "format": tools_fmt,
                      "file": f"tools_and_versions{report_formats.FMT_EXT[tools_fmt]}", "ok": True})
    for fname, dv, ff in (
        ("synopsis.md", "synopsis", "markdown"), ("synopsis.json", "synopsis", "json"),
        ("activity_breakdown.md", "activity", "markdown"),
        ("activity_breakdown.json", "activity", "json"),
        ("manifest.json", "manifest", "json"),
        ("tools_and_versions.md", "tools", "markdown"),
        ("tools_and_versions.json", "tools", "json"),
        ("session.log", "session log", "text"),
    ):
        generated.append({"deliverable": dv, "format": ff, "file": fname, "ok": True})
    if corpus_summary.get("plan_results"):
        for fname in ("objective_analysis.md", "objective_analysis.json"):
            generated.append({"deliverable": "objective analysis",
                              "format": fname.rsplit(".", 1)[-1], "file": fname, "ok": True})
    session.ledger.set_generated_files(generated)
    session.step("Generated result files:")
    for g in generated:
        session.detail(f"  {g['file']}  ({g['format']})")

    # -- tool report (secondary document, requirement 7) -------------------
    session.step("Writing the tools & versions secondary document ...")
    session.ledger.record("analysis-step", "report rendering",
                          detail="doc_analyzer.report.render",
                          purpose="synopsis + activity breakdown + manifest")
    md, js = session.finalize_tool_report()
    result.artifacts["tools_and_versions_md"] = str(md)
    result.artifacts["tools_and_versions_json"] = str(js)

    session.step("Rendering the tools & versions deliverable ...")
    td = report_formats.write_tools_deliverable(
        session, run_opts, cfg, ledger=session.ledger, objective=objective,
        subtitle=f"session {session.id}  ·  {source_label}",
    )
    result.artifacts["tools_deliverable"] = str(session.path(td["file"]))
    result.deliverables = generated

    # -- mirror the results next to the analyzed files (local sources) -----
    if deliver_dir is not None:
        session.step(f"Mirroring results into {deliver_dir} ...")
        _mirror_results(session, deliver_dir)
        result.deliver_dir = deliver_dir

    dt = (datetime.now(timezone.utc) - t0).total_seconds()
    where = f"{session.dir}" + (f"  and  {deliver_dir}" if deliver_dir is not None else "")
    session.step(f"Done in {dt:.1f}s. {result.files_analyzed} analyzed, "
                 f"{result.files_skipped} skipped. Reports in {where}")
    return result


def _prepare_deliver_dir(session, cfg, root, kind: str, run_opts: RunOptions):
    """Where to also write the result documents.  Only local-folder sources get a
    copy next to the analyzed files; everything else stays in the session folder."""
    if kind != "local":
        session.detail("Online source: results will be written to the session folder only.")
        return None
    if not run_opts.write_to_source or not cfg.write_to_source_folder:
        session.detail("Source-folder copy disabled: results go to the session folder only.")
        return None
    target = Path(root) / f"{cfg.results_subfolder_prefix}_{session.id}"
    try:
        target.mkdir(parents=True, exist_ok=True)
        probe = target / ".write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        session.warn(f"Cannot write results into {root} ({exc}); using the session folder only.")
        return None
    session.detail(f"Results will also be written to {target}")
    return target


def _mirror_results(session, deliver_dir: Path) -> None:
    """Copy every session artifact (except the _fetched/ tree) into deliver_dir;
    session.log is copied last so the mirrored copy is as complete as possible."""
    try:
        deliver_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        session.warn(f"Cannot create {deliver_dir} ({exc}); skipping the source-folder copy.")
        return
    log_name = session._log_path.name  # noqa: SLF001 - intentional handle
    for p in sorted(session.dir.iterdir()):
        if not p.is_file() or p.name == log_name:
            continue
        try:
            shutil.copy2(p, deliver_dir / p.name)
        except OSError as exc:
            session.warn(f"could not mirror {p.name}: {exc}")
    try:
        shutil.copy2(session._log_path, deliver_dir / log_name)  # noqa: SLF001
    except OSError:
        pass


def _describe_clusters(groups, corpus, records):
    by_rel = {r["rel"]: r for r in records}
    rows = []
    for gi, members in enumerate(groups, 1):
        agg = {}
        for m in members:
            for t, w in corpus.vectors.get(m, {}).items():
                agg[t] = agg.get(t, 0.0) + w
        shared = [t for t, _ in sorted(agg.items(), key=lambda x: -x[1])[:6]]
        # representative = member most similar to the rest
        rep = members[0]
        if len(members) > 1:
            rep = max(members, key=lambda a: sum(corpus.cosine(a, b) for b in members if b != a))
        kinds = sorted({by_rel[m]["kind"] for m in members if m in by_rel})
        rows.append({
            "id": gi,
            "size": len(members),
            "label": ", ".join(shared[:4]) if shared else "(no shared terms)",
            "shared_terms": shared,
            "kinds": kinds,
            "representative": rep,
            "docs": sorted(members),
        })
    return rows


def _run_objective_plan(session, cfg, objective, records, analyzed_records, corpus_summary):
    """Local-model-planned, engine-computed, local-model-written analysis that
    answers the free-text objective directly (resources, rounds, per-task/type
    metrics, ranking, coaching from notes)."""
    from ..llm import LocalLLM, LLMUnavailable

    session.step("Identifying named resources and study rounds from the naming scheme ...")
    resource_index = resources_mod.build_index(analyzed_records)
    for key, node in resource_index.by_resource.items():
        session.detail(f"  {node['canonical']}: rounds {node['round_list'] or 'none'} "
                       f"(name in {', '.join(k for k in node['name_locations'])})")
    for a in resource_index.anomalies[:20]:
        session.warn(f"  naming/round issue - {a}")
    session.ledger.record("analysis-step", "resource + naming-scheme resolution",
                          detail="doc_analyzer.analyze.resources",
                          purpose="unique resource per file + where the name sits in the naming scheme + round")

    from . import directives as directives_mod
    dr = directives_mod.parse(objective)
    if dr.requested_items:
        session.detail(f"Objective spells out {len(dr.requested_items)} numbered deliverable(s); "
                       "each gets its own table.")
    if dr.nonprod_tasks or dr.nonprod_types:
        session.detail("Productive/unproductive rule from the objective -> unproductive tasks: "
                       f"{dr.nonprod_tasks or '(none)'}; unproductive types: "
                       f"{dr.nonprod_types or '(none)'}.")

    session.step("Building the activity fact table from the spreadsheets ...")
    facts = facts_mod.build(analyzed_records, resource_index,
                            nonprod_tasks=dr.nonprod_tasks, nonprod_types=dr.nonprod_types)
    session.detail(f"{len(facts.rows)} activity lines; {len(facts.tasks)} tasks, "
                   f"{len(facts.types)} types, rounds {facts.rounds}, "
                   f"notes column {'found' if facts.notes_column_present else 'not found'}.")
    session.ledger.record("analysis-step", "structured spreadsheet fact table",
                          detail="doc_analyzer.analyze.facts",
                          purpose="one row per logged task line, tagged with resource + round")

    steps = set(cfg.llm_steps) if cfg.llm_enabled else set()
    llm = None
    if steps:
        session.step(f"Starting the local model for: {', '.join(sorted(steps))} "
                     "(CPU generation is slow - other steps stay deterministic) ...")
        try:
            llm = LocalLLM(cfg, session)
            llm.start()
            session.ledger.record("service", "local LLM (Qwen via transformers)",
                                  version=cfg.get("llm.model", "Qwen/Qwen3-4B"),
                                  detail=f"interpreter: {llm.interpreter}; "
                                         f"used for: {', '.join(sorted(steps))}",
                                  purpose="interpret the objective / write the answers")
        except LLMUnavailable as exc:
            session.warn(f"Local model unavailable ({exc}). Using the heuristic planner "
                         "and a templated write-up.")
            llm = None
    elif cfg.llm_enabled:
        session.detail("llm.steps is empty - using the heuristic planner (no model load).")
    else:
        session.detail("llm.enabled is false - using the heuristic planner.")

    resource_notes: dict = {}
    try:
        session.step("Planning the analysis from the objective ...")
        context = plan_mod.build_context(objective, facts, resource_index, analyzed_records)
        plan = plan_mod.build_plan(llm if "plan" in steps else None, session, objective, context)

        session.step("Computing the requested metrics ...")
        results = execute_mod.run(plan, facts, resource_index, analyzed_records)
        gt = results.grand_totals
        session.detail(f"totals: {gt.get('activity_lines')} lines, {gt.get('total_minutes')} min, "
                       f"{gt.get('productive_pct')}% productive; "
                       f"{len(results.ranking)} resources ranked; "
                       f"{len(results.coaching_candidates)} note(s) flagged for review.")

        session.step("Reviewing the notes column for coaching / corrective-action instances ...")
        results.coaching_candidates = plan_mod.refine_coaching(
            llm if "coaching" in steps else None, session, results.coaching_candidates)

        session.step("Summarising the notes per resource (trend, issue / coaching / disciplinary counts) ...")
        execute_mod.build_notes_summary(results, facts, resource_index)
        resource_notes = plan_mod.write_resource_notes_narrative(
            llm if "coaching" in steps else None, session, results, facts)

        session.step("Writing the objective answers ...")
        answers = plan_mod.write_narrative(
            llm if "narrative" in steps else None, session, objective, plan, results)
    finally:
        if llm is not None:
            llm.stop()

    corpus_summary["analysis_plan"] = plan.to_dict()
    corpus_summary["plan_results"] = results.to_dict()
    corpus_summary["resource_index"] = resource_index.to_dict()
    corpus_summary["objective_answers"] = answers
    corpus_summary["resource_notes_narrative"] = resource_notes
    corpus_summary["objective_directives"] = dr.to_dict()
    session.ledger.record("analysis-step", "objective answering",
                          detail=f"planner={plan.source}; writer={answers.get('source')}",
                          purpose="turn the free-text objective into computed metrics + written answers")
