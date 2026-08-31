"""Minutes, synopsis and action-item generation with the local LLM.

For long meetings the transcript is processed map-reduce style: each slice is
compressed to notes, then the notes are synthesised into the final minutes.
"""
from __future__ import annotations

from typing import Callable, List, Optional, Tuple

from ..config import Config
from ..documents.model import (ActionItem, DiscussionTopic, Minutes, Transcript)
from ..llm import prompts
from ..llm.qwen import get_llm
from ..logging_setup import get_logger

log = get_logger("summarize")
Progress = Callable[[float, str], None]


def _participants(transcript: Transcript) -> str:
    names = [s.display for s in transcript.speakers] or ["Speaker 1"]
    return ", ".join(names)


def _slice_transcript(transcript: Transcript, char_limit: int) -> List[str]:
    lines = []
    for seg in transcript.segments:
        who = transcript.display_for(seg.speaker)
        lines.append(f"[{_ts(seg.start)}] {who}: {seg.text.strip()}")
    full = "\n".join(lines)
    if len(full) <= char_limit:
        return [full]

    slices, cur, cur_len = [], [], 0
    for ln in lines:
        if cur_len + len(ln) > char_limit and cur:
            slices.append("\n".join(cur))
            cur, cur_len = [], 0
        cur.append(ln)
        cur_len += len(ln) + 1
    if cur:
        slices.append("\n".join(cur))
    return slices


def generate_minutes(transcript: Transcript, cfg: Config, *,
                     title_hint: str = "", date_hint: str = "",
                     with_synopsis: bool = True,
                     progress: Optional[Progress] = None) -> Minutes:
    p = progress or (lambda f, m: None)
    llm = get_llm(cfg)
    char_limit = int(cfg.get("llm", "map_reduce_char_limit", 40000))
    slices = _slice_transcript(transcript, char_limit)
    participants = _participants(transcript)

    if len(slices) == 1:
        p(0.2, "Drafting minutes with the local LLM ...")
        data = llm.generate_json(
            prompts.MINUTES_SYSTEM,
            prompts.MINUTES_USER.format(
                synopsis_instruction=(prompts.SYNOPSIS_WITH if with_synopsis
                                      else prompts.SYNOPSIS_WITHOUT),
                participants=participants,
                title_hint=title_hint or "not provided",
                date_hint=date_hint or "not provided",
                transcript=slices[0],
            ),
            max_new_tokens=2600,
        )
        p(0.9, "Minutes drafted.")
        return _minutes_from_json(data, transcript, title_hint, with_synopsis)

    # map-reduce
    log.info("Long transcript: %d slices -> map-reduce.", len(slices))
    partials = []
    for i, sl in enumerate(slices, 1):
        p(0.1 + 0.6 * (i - 1) / len(slices), f"Summarising slice {i}/{len(slices)} ...")
        partials.append(llm.generate_json(
            prompts.MAP_SYSTEM,
            prompts.MAP_USER.format(idx=i, total=len(slices), transcript=sl),
            max_new_tokens=1800,
        ))
    p(0.75, "Synthesising final minutes from slice notes ...")
    merged_notes = _merge_partials(partials)
    data = llm.generate_json(
        prompts.MINUTES_SYSTEM,
        prompts.MINUTES_USER.format(
            synopsis_instruction=(prompts.SYNOPSIS_WITH if with_synopsis
                                  else prompts.SYNOPSIS_WITHOUT),
            participants=participants,
            title_hint=title_hint or "not provided",
            date_hint=date_hint or "not provided",
            transcript="CONSOLIDATED SLICE NOTES:\n" + merged_notes,
        ),
        max_new_tokens=2600,
    )
    p(0.95, "Minutes drafted.")
    return _minutes_from_json(data, transcript, title_hint, with_synopsis)


def generate_action_items(transcript: Transcript, cfg: Config, *,
                          progress: Optional[Progress] = None) -> List[ActionItem]:
    p = progress or (lambda f, m: None)
    llm = get_llm(cfg)
    char_limit = int(cfg.get("llm", "map_reduce_char_limit", 40000))
    slices = _slice_transcript(transcript, char_limit)
    participants = _participants(transcript)

    seen: List[ActionItem] = []
    for i, sl in enumerate(slices, 1):
        p(0.1 + 0.8 * (i - 1) / len(slices),
          f"Extracting action items {i}/{len(slices)} ...")
        data = llm.generate_json(
            prompts.ACTION_ITEMS_SYSTEM,
            prompts.ACTION_ITEMS_USER.format(participants=participants, transcript=sl),
            max_new_tokens=1600,
        )
        for raw in data.get("action_items", []) or []:
            item = _action_from_json(raw, transcript)
            if item and not _is_duplicate(item, seen):
                seen.append(item)
    p(0.95, f"{len(seen)} action item(s) extracted.")
    return seen


# --------------------------------------------------------------------------- #
def _minutes_from_json(data: dict, transcript: Transcript, title_hint: str,
                       with_synopsis: bool) -> Minutes:
    def _s(v) -> str:
        return "" if v is None else str(v).strip()

    def _list(v) -> List[str]:
        if not v:
            return []
        if isinstance(v, str):
            return [v.strip()] if v.strip() else []
        return [str(x).strip() for x in v if str(x).strip()]

    disc = []
    for t in data.get("discussion", []) or []:
        if isinstance(t, dict):
            disc.append(DiscussionTopic(topic=_s(t.get("topic")) or "Discussion",
                                        points=_list(t.get("points"))))
        elif isinstance(t, str) and t.strip():
            disc.append(DiscussionTopic(topic="Discussion", points=[t.strip()]))

    attendees = _list(data.get("attendees")) or [s.display for s in transcript.speakers]
    m = Minutes(
        title=_s(data.get("title")) or title_hint or "Meeting Minutes",
        meeting_date=_s(data.get("meeting_date")),
        location=_s(data.get("location")),
        attendees=attendees,
        agenda=_list(data.get("agenda")),
        discussion=disc,
        decisions=_list(data.get("decisions")),
        next_steps=_list(data.get("next_steps")),
        synopsis=_s(data.get("synopsis")) if with_synopsis else "",
    )
    for junk in ("not stated", "not provided", "n/a", "none"):
        if m.meeting_date.lower() == junk:
            m.meeting_date = ""
        if m.location.lower() == junk:
            m.location = ""
    return m


def _action_from_json(raw: dict, transcript: Transcript) -> Optional[ActionItem]:
    if not isinstance(raw, dict):
        return None
    task = str(raw.get("task", "")).strip()
    if not task:
        return None
    owner = str(raw.get("owner", "")).strip()
    if owner.lower() in ("not stated", "n/a", "none", "unknown", "tbd"):
        owner = ""
    due = str(raw.get("due", "")).strip()
    if due.lower() in ("not stated", "n/a", "none", "tbd"):
        due = ""
    quote = str(raw.get("source_quote", "")).strip()
    try:
        conf = float(raw.get("confidence", 0.8))
    except Exception:
        conf = 0.8
    st = _find_quote_time(quote, transcript)
    return ActionItem(task=task, owner=owner, due=due, source_time=st,
                      source_quote=quote, confidence=max(0.0, min(1.0, conf)))


def _is_duplicate(item: ActionItem, seen: List[ActionItem]) -> bool:
    import difflib
    for s in seen:
        r = difflib.SequenceMatcher(None, s.task.lower(), item.task.lower()).ratio()
        if r > 0.82:
            return True
    return False


def _find_quote_time(quote: str, transcript: Transcript) -> Optional[float]:
    if not quote:
        return None
    q = quote.lower()[:60]
    for seg in transcript.segments:
        if q and q in seg.text.lower():
            return round(seg.start, 2)
    # looser: any 4-word shingle
    words = [w for w in q.split() if w]
    if len(words) >= 4:
        shingle = " ".join(words[:4])
        for seg in transcript.segments:
            if shingle in seg.text.lower():
                return round(seg.start, 2)
    return None


def _merge_partials(partials: List[dict]) -> str:
    topics, decisions, actions, attendees = [], [], [], []
    for p in partials:
        for t in p.get("topics", []) or []:
            if isinstance(t, dict):
                topics.append(f"- {t.get('topic', 'Topic')}: "
                              + "; ".join(str(x) for x in (t.get("points") or [])))
        for d in p.get("decisions", []) or []:
            decisions.append(f"- {d}")
        for a in p.get("action_items", []) or []:
            if isinstance(a, dict):
                actions.append(f"- {a.get('task', '')} (owner: {a.get('owner', '') or 'n/a'}, "
                               f"due: {a.get('due', '') or 'n/a'})")
        for at in p.get("attendees_seen", []) or []:
            if at not in attendees:
                attendees.append(at)
    parts = []
    if attendees:
        parts.append("ATTENDEES SEEN: " + ", ".join(attendees))
    if topics:
        parts.append("TOPICS:\n" + "\n".join(topics))
    if decisions:
        parts.append("DECISIONS:\n" + "\n".join(decisions))
    if actions:
        parts.append("ACTION ITEMS:\n" + "\n".join(actions))
    return "\n\n".join(parts)


def _ts(sec: float) -> str:
    sec = max(0, int(sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
