"""Identify the unique *named resource* behind each file and where the name sits
in the naming scheme, plus the study round.  Deterministic; the LLM plan can
override the resource dimension but this is what actually parses the filenames.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

_ROUND_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
                "seven": 7, "eight": 8, "nine": 9, "ten": 10,
                "1st": 1, "2nd": 2, "3rd": 3, "first": 1, "second": 2, "third": 3}

# "Kyle J Time Study - Round 1 - Prod-NonProd.KJennings.xlsx"
_VARIANT_A = re.compile(
    r"^(?P<label>.+?)\s+time\s*study\s*-\s*round\s*(?P<round>\d+)\s*-\s*"
    r"(?P<cat>prod[-.\s]*non[-.\s]*prod)\.(?P<token>[A-Za-z][A-Za-z'\-]+)"
    r"(?:\s*\(\d+\))?\.(?:xlsx|xlsm|xls)$", re.I)
# "TimeStudyRoundOneProd.NonProd.DDomingo.xlsx"
_VARIANT_B = re.compile(
    r"^time\s*study\s*round\s*(?P<roundword>[A-Za-z0-9]+?)"
    r"(?P<cat>prod[-.\s]*non[-.\s]*prod)\.(?P<token>[A-Za-z][A-Za-z'\-]+)"
    r"(?:\s*\(\d+\))?\.(?:xlsx|xlsm|xls)$", re.I)
_ROUND_ANY = re.compile(r"round\s*[-_ ]?\s*([A-Za-z0-9]+)", re.I)
_COPY_SUFFIX = re.compile(r"\((\d+)\)\s*\.[^.]+$")


@dataclass
class FileRes:
    rel: str
    resource_key: str = ""
    canonical: str = ""
    round: int | None = None
    round_raw: str = ""
    category: str = ""
    variant: str = "unknown"
    name_locations: list[str] = field(default_factory=list)
    anomalies: list[str] = field(default_factory=list)
    label: str = ""
    token: str = ""
    content_name: str = ""


@dataclass
class ResourceIndex:
    by_file: dict[str, FileRes] = field(default_factory=dict)
    by_resource: dict[str, dict] = field(default_factory=dict)
    anomalies: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "by_file": {k: vars(v) for k, v in self.by_file.items()},
            "by_resource": {
                k: {**v, "aliases": sorted(v["aliases"]),
                    "variants": sorted(v["variants"]),
                    "name_locations": dict(v["name_locations"])}
                for k, v in self.by_resource.items()
            },
            "anomalies": self.anomalies,
            "resource_count": len(self.by_resource),
        }


def _round_from_raw(raw: str) -> int | None:
    raw = (raw or "").strip().lower()
    if raw.isdigit():
        return int(raw)
    if raw in _ROUND_WORDS:
        return _ROUND_WORDS[raw]
    m = re.match(r"(\d+)", raw)
    return int(m.group(1)) if m else None


def _humanize_token(token: str) -> str:
    """'KJennings' -> 'K. Jennings' ; 'TJGrunfelder' -> 'TJ Grunfelder'."""
    t = token.strip()
    m = re.match(r"^([A-Z]{1,3})([A-Z][a-z'\-]+)$", t)
    if m:
        initials, surname = m.group(1), m.group(2)
        return f"{'.'.join(initials)}. {surname}" if len(initials) == 1 else f"{initials} {surname}"
    return t


def _identity_key(token: str) -> str:
    """First initial + surname, lowercased - merges 'KJennings' with 'Kyle J'
    style labels when the surname is present."""
    m = re.match(r"^([A-Za-z])[A-Za-z]*?([A-Z][a-z'\-]+)$", token)
    if m:
        return (m.group(1) + m.group(2)).lower()
    return re.sub(r"[^a-z]", "", token.lower())


def parse_filename(name: str) -> FileRes:
    fr = FileRes(rel=name)
    base = name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    m = _VARIANT_A.match(base)
    if m:
        fr.variant = "A (spaced: '<label> Time Study - Round N - Prod-NonProd.<Token>')"
        fr.label = m.group("label").strip()
        fr.token = m.group("token")
        fr.round_raw = m.group("round")
        fr.category = m.group("cat")
        fr.name_locations = ["filename_leading_label", "filename_trailing_token"]
    else:
        m = _VARIANT_B.match(base)
        if m:
            fr.variant = "B (camel: 'TimeStudyRound<Word>Prod.NonProd.<Token>')"
            fr.token = m.group("token")
            fr.round_raw = m.group("roundword")
            fr.category = m.group("cat")
            fr.name_locations = ["filename_trailing_token"]
        else:
            # generic fallback
            fr.variant = "unrecognised"
            rm = _ROUND_ANY.search(base)
            if rm:
                fr.round_raw = rm.group(1)
            tm = re.search(r"\.([A-Za-z][A-Za-z'\-]+)(?:\s*\(\d+\))?\.[^.]+$", base)
            if tm:
                fr.token = tm.group(1)
                fr.name_locations = ["filename_trailing_token"]
            fr.anomalies.append("filename does not match either known Time-Study naming pattern")

    fr.round = _round_from_raw(fr.round_raw)
    if fr.round is None:
        fr.anomalies.append(f"could not read a round number from '{fr.round_raw or base}'")
    cm = _COPY_SUFFIX.search(base)
    if cm:
        fr.anomalies.append(f"filename carries a copy marker ({cm.group(0).strip()}) - possible duplicate")
    return fr


def build_index(records: list[dict]) -> ResourceIndex:
    idx = ResourceIndex()
    # first pass: parse filenames
    parsed: list[FileRes] = []
    for r in records:
        fr = parse_filename(r.get("rel", ""))
        # content RESOURCE cell, if the extractor captured it
        ctx = ((r.get("meta") or {}).get("cell_context") or {})
        cname = str(ctx.get("RESOURCE") or ctx.get("Resource") or "").strip()
        if cname:
            fr.content_name = cname
            fr.name_locations.append("content_resource_cell")
        parsed.append(fr)

    # resolve identities: prefer first-initial+surname key from the trailing token
    for fr in parsed:
        key = _identity_key(fr.token) if fr.token else _identity_key(re.sub(r"\s", "", fr.label))
        if not key:
            key = fr.rel.lower()
        fr.resource_key = key
        # canonical name preference: a spaced content name, else humanized token, else label
        if fr.content_name and " " in fr.content_name:
            fr.canonical = fr.content_name if fr.content_name[:1].isupper() else fr.content_name.title()
        elif fr.token:
            fr.canonical = _humanize_token(fr.token)
        else:
            fr.canonical = fr.label or key

        node = idx.by_resource.setdefault(key, {
            "canonical": fr.canonical, "token": fr.token, "label": fr.label,
            "files": [], "rounds": {}, "round_list": [], "round_count": 0,
            "aliases": set(), "variants": set(), "name_locations": Counter(),
            "anomalies": [],
        })
        node["files"].append(fr.rel)
        for a in (fr.token, fr.label, fr.content_name):
            if a:
                node["aliases"].add(a)
        node["variants"].add(fr.variant)
        for loc in fr.name_locations:
            node["name_locations"][loc] += 1
        if fr.round is not None:
            node["rounds"].setdefault(str(fr.round), []).append(fr.rel)
        if len(fr.content_name.split()) >= 2 and node["canonical"] != fr.content_name:
            node["canonical"] = fr.content_name
        idx.by_file[fr.rel] = fr

    # second pass: unify the display name per resource, round lists, anomalies
    for key, node in idx.by_resource.items():
        rounds = sorted(int(x) for x in node["rounds"])
        node["round_list"] = rounds
        node["round_count"] = len(rounds)
        node["aliases"] = set(node["aliases"])
        # one canonical name for the whole resource: a spaced real name if any file
        # carried one, else the humanised token
        spaced = [a for a in node["aliases"] if " " in a and a[:1].isalpha()]
        if spaced:
            node["canonical"] = max(spaced, key=len)
        for rel in node["files"]:
            idx.by_file[rel].canonical = node["canonical"]
            idx.by_file[rel].resource_key = key
        if node["round_count"] < 2:
            msg = (f"{node['canonical']}: only {node['round_count']} round(s) "
                   f"({rounds or 'none'}) - expected at least 2")
            node["anomalies"].append(msg)
            idx.anomalies.append(msg)
        # duplicate round files
        for rnum, files in node["rounds"].items():
            if len(files) > 1:
                msg = f"{node['canonical']}: round {rnum} has {len(files)} files ({', '.join(files)})"
                node["anomalies"].append(msg)
                idx.anomalies.append(msg)
        for fr_rel in node["files"]:
            for a in idx.by_file[fr_rel].anomalies:
                idx.anomalies.append(f"{fr_rel}: {a}")

    return idx
