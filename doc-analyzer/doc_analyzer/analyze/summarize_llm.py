"""Optional Claude pass: a short narrative synopsis + friendly activity names.
Never required - the local analysis is always the source of truth."""
from __future__ import annotations

from typing import Any


def enhance(session, cfg, corpus_summary: dict[str, Any], per_doc: list[dict],
            objective: str = "", obj: Any = None) -> dict | None:
    if not cfg.claude_enabled:
        session.detail("Claude enhancement disabled - skipping (local analysis only).")
        return None
    try:
        import anthropic
    except ImportError:
        session.warn("claude.enabled is set but the 'anthropic' package is not installed "
                     "(setup.ps1 -Full). Skipping remote synopsis.")
        return None

    model = cfg.get("claude.model", "claude-sonnet-5")
    cap = int(cfg.get("claude.max_docs_sent", 40))
    session.ledger.record_module("anthropic", "Claude narrative synopsis")
    session.ledger.record("service", "Anthropic Messages API", version=model,
                          purpose="narrative synopsis + activity naming")

    findings = corpus_summary.get("objective_findings") or {}
    has_obj = bool(objective.strip())

    lines: list[str] = []
    if has_obj:
        lines += [
            f"The reader's objective for this analysis: {objective.strip()}",
            "",
            "Produce, in this order:",
            "(A) A direct answer to that objective in 2-4 sentences, grounded ONLY in",
            "    the evidence below. If the evidence is insufficient, say so plainly.",
            "(B) A ~120-word executive synopsis of the collection, oriented to the objective.",
            "(C) A bullet list of the distinct kinds of activity evidenced, most",
            "    objective-relevant first.",
        ]
        focus = getattr(obj, "focus", None) or []
        if focus:
            lines.append(f"Detected focus areas: {', '.join(focus)}.")
        lines.append("")
        if findings.get("key_sentences"):
            lines.append("Objective-relevant sentences pulled from the corpus:")
            for ks in findings["key_sentences"][:14]:
                lines.append(f"  - ({ks['doc']}) {ks['sentence'][:280]}")
            lines.append("")
        if findings.get("entities_of_interest"):
            lines.append("Entities of interest already extracted:")
            for et, vals in findings["entities_of_interest"].items():
                lines.append(f"  - {et}: {', '.join(str(v) for v in vals[:20])}")
            lines.append("")
        ranked = findings.get("ranked_documents") or []
        if ranked:
            lines.append("Documents ranked by relevance to the objective:")
            for d in ranked[:cap]:
                lines.append(f"  - {d['doc']} (relevance {d['relevance']:.3f}) {d['why'][:160]}")
            lines.append("")
    else:
        lines += [
            "You are analysing a document corpus. Produce: (1) a 150-word executive",
            "synopsis of what this collection contains and its apparent purpose; (2) a",
            "bullet list naming the distinct kinds of activity evidenced across the",
            "documents. Be concrete and neutral. Base it ONLY on the data below.",
            "",
        ]

    lines += [
        "Do not invent facts.",
        "",
        f"Corpus: {corpus_summary.get('doc_count')} documents, types: "
        f"{corpus_summary.get('type_distribution')}",
        f"Top corpus keywords: {corpus_summary.get('keywords')}",
        f"Detected activity categories: {corpus_summary.get('activity_categories')}",
        "",
        "Per-document digests:",
    ]
    for d in per_doc[:cap]:
        lines.append(f"- {d['rel']} [{d['kind']}] rel={d.get('relevance', 0)} "
                     f"kw={d.get('keywords', [])[:8]} "
                     f"summary={' '.join(d.get('summary', []))[:400]}")
    prompt = "\n".join(lines)

    try:
        client = anthropic.Anthropic(api_key=cfg.get("claude.api_key"))
        msg = client.messages.create(
            model=model, max_tokens=1200,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        session.detail(f"Claude synopsis received ({len(text)} chars).")
        return {"model": model, "narrative": text}
    except Exception as exc:  # noqa: BLE001
        session.warn(f"Claude request failed: {exc}. Continuing with local analysis only.")
        return None
