"""Run the user's "anything specific to check?" request against every result."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

from .. import config
from .. import models as M
from . import rules

log = logging.getLogger("tracking_checker")

MATCH_COL, NOTES_COL = "Matches Your Check", "Check Notes"


@dataclass
class CheckOutcome:
    engine: str = ""
    columns: list[str] = field(default_factory=list)
    interpretation: str = ""
    notes: list[str] = field(default_factory=list)


def claude_available(settings) -> bool:
    return bool(settings.claude_enabled and config.get_secret("anthropic_api_key"))


def engine_label(settings) -> str:
    if claude_available(settings):
        return f"Claude ({settings.claude_model})"
    return "Built-in rules (add an Anthropic API key in Settings for free-form checks)"


def shipment_facts(r: M.TrackingResult) -> dict:
    pod = r.pod or M.ProofOfDelivery()
    events = sorted(r.events, key=lambda e: e.timestamp or datetime.min, reverse=True)
    return {
        "tracking_number": r.tracking_number, "carrier": r.carrier, "service": r.service,
        "status": r.status, "status_detail": r.status_detail, "delivered": r.delivered,
        "delivered_at": r.delivered_at, "signed_by": pod.signed_by, "left_at": pod.left_at,
        "delivery_address": pod.address, "ship_date": r.ship_date, "origin": r.origin,
        "destination": r.destination, "estimated_delivery": r.estimated_delivery,
        "days_in_transit": r.days_in_transit(), "delivery_attempts": r.attempts, "exception": r.exception,
        "weight": r.weight, "pod_available": bool(pod.files), "lookup_error": r.error,
        "events": [e.line() for e in events[:40]],
    }


def run(request: str, results: list[M.TrackingResult], settings, progress=None) -> CheckOutcome:
    request = (request or "").strip()
    if not request:
        return CheckOutcome()
    if claude_available(settings):
        try:
            return _run_claude(request, results, settings, progress)
        except Exception as e:  # noqa: BLE001 - any Claude failure falls back to the offline rules
            log.warning("Claude check failed (%s) - falling back to the built-in rules.", e)
            out = _run_rules(request, results)
            out.notes.insert(0, f"Claude was unavailable ({e}); the built-in rules were used instead.")
            return out
    return _run_rules(request, results)


def _run_rules(request: str, results: list[M.TrackingResult]) -> CheckOutcome:
    rs, unknown = rules.interpret(request)
    out = CheckOutcome(engine="Built-in rules")
    out.columns = [r.column for r in rs] + [MATCH_COL, NOTES_COL]
    out.interpretation = "; ".join(r.explain for r in rs) or "nothing recognised"
    for s in unknown:
        out.notes.append(f"Not understood by the built-in rules: \"{s}\" - add an Anthropic API key in "
                         "Settings to have Claude interpret free-form requests.")
    for res in results:
        hits = []
        for rule in rs:
            try:
                v = rule.fn(res)
            except Exception:  # noqa: BLE001
                v = "Unknown"
            res.extra[rule.column] = v
            if v == "Yes":
                hits.append(rule.explain)
        res.extra[MATCH_COL] = "Yes" if hits else "No"
        res.extra[NOTES_COL] = ("Matches: " + "; ".join(hits)) if hits else ""
    return out


def _run_claude(request: str, results: list[M.TrackingResult], settings, progress) -> CheckOutcome:
    from .claude_check import ClaudeChecker

    checker = ClaudeChecker(config.get_secret("anthropic_api_key"), settings.claude_model)
    if progress:
        progress("Claude is interpreting your request ...")
    plan = checker.plan(request)
    out = CheckOutcome(engine=f"Claude ({settings.claude_model})", interpretation=plan.get("interpretation", ""))
    out.notes += [f"Claude could not check: {x}" for x in plan.get("cannot_do", [])]
    names = [c["name"] for c in plan["columns"]]
    out.columns = names + [MATCH_COL, NOTES_COL]
    if not results:
        return out
    verdicts = checker.judge(request, plan, [shipment_facts(r) for r in results], progress)
    missing = 0
    for res in results:
        v = verdicts.get(res.tracking_number)
        if v is None:
            missing += 1
            for n in names:
                res.extra[n] = "Unknown"
            res.extra[MATCH_COL] = "Unknown"
            res.extra[NOTES_COL] = "Claude did not return a result for this number"
            continue
        for n in names:
            res.extra[n] = v["values"].get(n, "Unknown")
        res.extra[MATCH_COL] = "Yes" if v["matches"] else "No"
        res.extra[NOTES_COL] = v["note"]
    if missing:
        out.notes.append(f"Claude skipped {missing} shipment(s); they are marked Unknown.")
    return out
