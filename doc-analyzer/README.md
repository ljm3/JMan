# Document Corpus Analyzer

A self-contained Windows desktop app. Point it at **a local folder** or **an online
repository**, and it will inventory every supported document, produce a **detailed
synopsis**, break down **like activity across the documents**, show **verbose steps**
while it works, and write **one session folder per run** — including a secondary
**tools & versions** document listing everything the analysis used.

Maps to the original requirements:

| # | Requirement | Where it lives |
|---|---|---|
| 1 | Ask where the data is (local path **or** online repository) | GUI "Where is the data?" panel; `sources/` (local, git, http, sharepoint, s3, azure_blob) |
| 2 | Analyze every document in that folder | `analyze/pipeline.py` + `extract/` (docx, xlsx, pptx, pdf, txt/md/csv/rtf, eml/msg, images→OCR, json/yaml/xml, source code) |
| 3 | Detailed synopsis of all data | `sessions/<id>/synopsis.md` and `synopsis.json` |
| 4 | Breakdown of like activity across documents | `sessions/<id>/activity_breakdown.md` and `.json` (content clusters, recurring action phrases, activity categories) |
| 5 | Verbose steps as performed | Live log pane in the GUI; every step is numbered |
| 6 | Log all activity in per-session files | `sessions/<id>/session.log` (+ all artifacts for that run) |
| 7 | Tools and versions used, per session, in a secondary document | `sessions/<id>/tools_and_versions.md` and `.json` |
| 8 | Understand the analysis objective and analyze to it | GUI "Analysis objective" box; CLI `--objective`. A **local Qwen model** (torch/transformers in this app's `.venv` via `setup.ps1 -LocalModel`, or a meeting-scribe venv fallback) turns the free-text objective into a structured plan — named resources, naming-scheme decomposition, metrics, ranking, coaching cues — the engine computes every metric deterministically over an activity fact table, and the model writes the answers and reviews every flagged note (issue / coaching / disciplinary). **Explicit numbered deliverables and an explicit productive/unproductive rule in the objective are honoured verbatim** (`analyze/directives.py`). Falls back to a heuristic planner + deterministic notes review when the model is unavailable. Output: `objective_analysis.md/.json/.<docx\|xlsx\|pptx>` (per-resource metric tables, the single technician×round SUMMARY pivot, the notes review) + a section in the synopsis deliverable. A lighter keyword layer (`analyze/objective.py`) additionally ranks documents by relevance and adds a "Findings for your objective" section |
| 9 | Put the results next to the analyzed files | For a local folder: `<analyzed folder>/doc-analyzer-results_<id>/` (a full copy of the session artifacts). Online sources use the session folder only. |
| 10 | Choose the format of each result document | GUI "Output format" dropdowns / CLI `--format*`: **Word**, **spreadsheet**, or **presentation** per report; the `.md`/`.json` versions are still written |
| 11 | Record the run's answers, generated filenames, and tools used | Both `session.log` and `tools_and_versions.md/.json` (new "Analysis answers" + "Generated files" sections) |

## Install

```powershell
cd $HOME\.local\bin\doc-analyzer
.\setup.ps1             # base install + Start Menu / Desktop shortcut + self-test
.\setup.ps1 -LocalModel # also: the local Qwen objective planner (torch + transformers) in .venv
.\setup.ps1 -Full      # also: S3 / Azure / SharePoint / HTTP sources, image OCR, Claude, and -LocalModel
```

`setup.ps1` finds Python 3.11–3.13 (via the `py` launcher or PATH) or installs
Python 3.12 with `winget`, builds `.\.venv`, installs dependencies, registers the
`doc-analyzer` command, copies `config.toml`, creates the shortcuts, and runs a
self-test against `sample_docs\`.

For image OCR also install the engine: `winget install UB-Mannheim.TesseractOCR`.

## Run

- **GUI:** `.\run.ps1` or the **Document Corpus Analyzer** shortcut.
- **Headless:**
  ```powershell
  .\.venv\Scripts\python -m doc_analyzer --cli --source "C:\path\to\docs"
  .\.venv\Scripts\python -m doc_analyzer --cli --kind git --source https://github.com/org/repo.git

  # with an objective and per-report formats
  .\.venv\Scripts\python -m doc_analyzer --cli --source "C:\path\to\docs" `
      --objective "What recurring obligations and deadlines do these docs create?" `
      --format-synopsis word --format-activity spreadsheet --format-tools spreadsheet
  ```
  `--format word|spreadsheet|presentation` sets every report at once;
  `--format-synopsis` / `--format-activity` / `--format-objective` /
  `--format-tools` override one (`--format-objective` defaults to the synopsis
  format).
  `--no-source-copy` keeps results in the session folder only.
- **Diagnostics:** `.\.venv\Scripts\python -m doc_analyzer --doctor`
- **Self-test:** `.\.venv\Scripts\python -m doc_analyzer --selftest`

## How the objective steers the run

If you give an objective, the app doesn't just print it — it analyzes *to* it:

1. **Interprets it** — `analyze/objective.py` extracts signal terms + quoted
   phrases and classifies the request into focus areas (financial, timeline,
   responsibility, compliance, risk, scope, decision, change, status). The
   verbose log shows what it understood.
2. **Ranks documents** — every document gets a cosine relevance score against the
   objective; the log and the reports list them best-first, with the terms that
   matched.
3. **Focuses the summaries** — sentences carrying objective terms are pulled into
   each per-document summary, and a corpus-wide list of the most relevant
   passages is collected.
4. **Surfaces the right details** — the entity types the objective implies (money
   & refs for "obligations", dates for "deadlines", people/orgs for "who is
   responsible", …) are aggregated across the corpus into a findings table.
5. **Flags on-objective activity** — the activity categories and phrases that
   match the objective are listed first and marked ★.
6. **Briefs Claude** (if enabled) with the objective, the focus areas, the ranked
   documents and the key passages, and asks for a direct answer first.

With no objective the run is a full, unfocused analysis and these sections are
omitted.

### Local-model objective engine (plan → compute → write)

When `[llm].enabled` is on (default) the objective is also run through a **local
Qwen model**. Install its stack (torch + transformers) into this app's own `.venv`
with `.\setup.ps1 -LocalModel` (or `-Full`); the bridge then runs it
self-contained. If you'd rather not add the ML deps here, it also falls back to a
**meeting-scribe** checkout's `.venv` beside this app, or an explicit interpreter
in `[llm].python`. The Qwen3 weights (~8 GB) download on the first objective run
and are cached in the usual Hugging Face cache. The flow:

1. **Plan** — the model turns your free-text objective into a structured plan:
   what a *named resource* is, how the file **naming scheme** decomposes (and where
   each resource's name sits in it), which **metrics** to compute (counts, totals,
   averages, ratios — grouped by task / type / round / resource), how to **rank**
   the resources, and which words in the notes signal **coaching / corrective
   action**.
2. **Compute** — the engine builds one activity fact table from the spreadsheets
   (`analyze/facts.py`) and computes every metric deterministically
   (`analyze/execute_plan.py`). The model never does arithmetic.
3. **Write** — the model writes the answers from the computed tables, and reviews
   every flagged note: **category** (Corrective action / Coaching) + **bucket**
   (issue / coaching / disciplinary) + a per-resource round-over-round read.

**Explicit instructions are honoured literally** (`analyze/directives.py`): a
numbered list of deliverables in the objective is sliced out and each item is
answered by its own table; an explicit productive/unproductive rule
("consider unproductive Tasks to include Lunch, Break, and Types of Scheduled
Break and Unscheduled Break") overrides the built-in classifier.

Output lands in **`objective_analysis.md` / `.json` / `.<docx|xlsx|pptx>`** (and
inside the synopsis deliverable): the resource + round + name-location tables,
per-resource round counts, **per-resource** task/type quantity + aggregate +
average minutes by round with an all-rounds total, the corpus-wide task/type
tables, a resource ranking, a **single SUMMARY pivot** — one column per
technician × round plus a per-technician total and an all-resources column, one
metric per row (quantities and times summed separately, averages/percentages
recomputed) — a **notes review** table (productivity trend between rounds +
issue / coaching / disciplinary counts per resource), and the flagged-note list.
The objective analysis follows the synopsis format unless you set
`--format-objective` (GUI: the "Objective analysis" dropdown).

If no interpreter with torch/transformers is found, a **heuristic planner** runs
the same computation and a templated write-up is produced — the deterministic
metrics are identical. Force it per run with `--llm` / `--no-llm`; check which
interpreter the bridge resolved with `--doctor`.

## Online sources

Fill in `config.toml` (or the app's **Options…** dialog):

| Kind | Location you enter | Needs |
|---|---|---|
| Git | clone URL | `git` on PATH |
| HTTP(S) | a direct file URL or an index page | `requests` (`-Full`) |
| SharePoint / OneDrive | library folder path, e.g. `Shared Documents/Contracts` | `requests`; `[sources.sharepoint]` app registration with `Files.Read.All` |
| Amazon S3 | `s3://bucket/prefix` | `boto3` (`-Full`); AWS creds |
| Azure Blob | `container/prefix` or blob URL | `azure-storage-blob` (`-Full`); connection string or account URL + SAS |

Downloaded copies live under `sessions/<id>/_fetched/` so every run is reproducible.

## Session output

```
sessions/session_YYYYMMDD_HHMMSS_<pid>/
  session.log                  verbose step-by-step trace of the run (incl. the objective,
                               the format answers, and every generated filename)
  synopsis.md / .json          per-document + corpus synopsis
  synopsis.<docx|xlsx|pptx>    the synopsis in the format you chose
  activity_breakdown.md/.json  content clusters, action phrases, activity categories
  activity_breakdown.<...>     the activity breakdown in the format you chose
  objective_analysis.md/.json  resources/rounds, per-resource + corpus task/type metrics,
                               ranking, the SUMMARY technician x round pivot, notes review
  objective_analysis.<...>     the objective analysis in the format you chose
  manifest.json                every file: hash, size, type, extractor, status
  tools_and_versions.md/.json  secondary doc: analysis answers, generated files, tools + versions
  tools_and_versions.<...>     the tools & versions doc in the format you chose
  _fetched/                    local copy of anything pulled from an online source

<analyzed folder>/doc-analyzer-results_YYYYMMDD_HHMMSS_<pid>/
                               for a local-folder source: a copy of everything above
                               (except _fetched/), sitting next to the analyzed files
```

## Notes

- The base install is pure-Python (no compilers). The local analysis (TF-IDF
  keywords, entity extraction, extractive summaries, similarity clustering,
  activity mining) is deterministic and offline.
- Claude is optional. Set `[claude] enabled = true` and a key (or
  `ANTHROPIC_API_KEY`) to add a narrative synopsis; the local analysis stays the
  source of truth.
- Legacy binary Office files (`.doc`, `.xls`, `.ppt`) are catalogued but not
  parsed — re-save as `.docx` / `.xlsx` / `.pptx`.
