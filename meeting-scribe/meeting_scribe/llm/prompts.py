"""Prompt templates for the local LLM.

All templates ask for STRICT JSON so the output can be parsed deterministically.
Kept deliberately explicit because smaller Qwen3 variants (4B/8B) need tight
instructions and leave more to the QC pass.
"""
from __future__ import annotations

MINUTES_SYSTEM = (
    "You are a meticulous corporate minute-taker. You transform raw meeting "
    "transcripts into accurate, neutral, well-structured minutes. You never "
    "invent facts, names, numbers or commitments that are not supported by the "
    "transcript. When information is missing you write \"not stated\". "
    "You reply with a single JSON object and nothing else."
)

MINUTES_USER = """\
Produce meeting minutes from the transcript below.

Return JSON with EXACTLY this shape:
{{
  "title": string,
  "meeting_date": string,            // copy if stated in transcript, else "not stated"
  "location": string,               // else "not stated"
  "attendees": [string],            // names or speaker labels actually present
  "agenda": [string],               // topics/agenda items; [] if none
  "discussion": [
    {{ "topic": string, "points": [string] }}   // 2-6 concise factual points each
  ],
  "decisions": [string],           // explicit decisions/agreements; [] if none
  "next_steps": [string],          // planned follow-ups that are not assigned tasks
  "synopsis": {synopsis_instruction}
}}

Rules:
- Base every statement on the transcript. Do not speculate.
- Keep points terse and factual; no filler.
- Preserve figures, dates and proper nouns exactly as spoken.
- Attendee list = the distinct speakers/participants named or labelled below.

KNOWN PARTICIPANTS: {participants}
MEETING TITLE HINT: {title_hint}
MEETING DATE HINT: {date_hint}

TRANSCRIPT:
{transcript}
"""

SYNOPSIS_WITH = ('string  // 4-8 sentence executive synopsis of purpose, '
                 'key discussion and outcomes')
SYNOPSIS_WITHOUT = 'string  // MUST be exactly "" (empty)'

ACTION_ITEMS_SYSTEM = (
    "You extract action items from meeting transcripts. An action item is a "
    "concrete task somebody committed to do. You do not invent owners or "
    "deadlines. If an owner or due date is not stated, you leave it empty. "
    "You reply with a single JSON object and nothing else."
)

ACTION_ITEMS_USER = """\
Extract every action item from the transcript.

Return JSON:
{{
  "action_items": [
    {{
      "task": string,        // imperative, specific, self-contained
      "owner": string,       // person responsible, exactly as named; "" if not stated
      "due": string,         // deadline as stated (e.g. "next Friday", "2026-09-01"); "" if not stated
      "source_quote": string,// short verbatim quote from the transcript that establishes this item
      "confidence": number   // 0.0-1.0, how clearly this is a real commitment
    }}
  ]
}}

Rules:
- Only include tasks that were actually committed to or assigned.
- Do NOT merge distinct tasks; do NOT split one task into several.
- owner/due empty string when the transcript does not state them - never guess.

KNOWN PARTICIPANTS: {participants}

TRANSCRIPT:
{transcript}
"""

# Map-reduce for long transcripts -----------------------------------------
MAP_SYSTEM = (
    "You are compressing one slice of a longer meeting transcript into dense "
    "factual notes for later synthesis. Keep names, numbers, decisions and "
    "commitments. Reply with a single JSON object and nothing else."
)

MAP_USER = """\
This is slice {idx} of {total} of a meeting transcript. Summarise ONLY what is in
this slice.

Return JSON:
{{
  "topics": [ {{ "topic": string, "points": [string] }} ],
  "decisions": [string],
  "action_items": [ {{ "task": string, "owner": string, "due": string, "source_quote": string }} ],
  "attendees_seen": [string]
}}

TRANSCRIPT SLICE:
{transcript}
"""

QC_SYSTEM = (
    "You are a quality-control reviewer for meeting documentation. You compare "
    "draft minutes and action items against the source transcript and flag "
    "problems: unsupported claims, missing action-item owners or due dates, "
    "missing sources, ambiguous wording, wrong or missing dates, and internal "
    "contradictions. You reply with a single JSON object and nothing else."
)

QC_USER = """\
Review the DRAFT against the TRANSCRIPT. List concrete problems only.

Return JSON:
{{
  "issues": [
    {{
      "severity": "error" | "warning" | "info",
      "category": string,     // e.g. "missing owner", "unsupported claim", "ambiguous", "date"
      "detail": string,       // what is wrong, referencing the specific item
      "suggestion": string    // how to fix it
    }}
  ]
}}

Guidance:
- "error": a factual claim not supported by the transcript, or a committed action item with no owner.
- "warning": missing due date, vague owner ("the team"), ambiguous phrasing, unverifiable figure.
- "info": minor style / completeness notes.
- If the draft is faithful and complete, return {{"issues": []}}.

DRAFT (JSON):
{draft}

TRANSCRIPT:
{transcript}
"""
