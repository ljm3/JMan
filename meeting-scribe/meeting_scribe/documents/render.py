"""Produce every requested deliverable into the output folder (requirement 7).

Deliverables land in ``project.output_dir`` - the same folder as the source
audio for a local file, or the download folder for a URL source.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Callable, List, Optional

from . import builders
from .builders import DocSpec
from .exporters import attach_file_to_pdf, export
from .export_html import build_interactive_html
from .model import AudioAccess, DocType, OutputFormat, Project
from ..logging_setup import get_logger

log = get_logger("render")
Progress = Callable[[float, str], None]


def _safe_stem(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip().rstrip(".")
    return name[:120] or "meeting"


def render_all(project: Project, *, progress: Optional[Progress] = None) -> List[Path]:
    p = progress or (lambda f, m: None)
    a = project.answers
    out_dir = Path(project.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    base = _safe_stem(Path(project.media_path).stem)
    formats = [f.value for f in a.formats] or ["docx"]
    produced: List[Path] = []

    # ---- source-audio handling ------------------------------------------
    copied_audio: Optional[Path] = None
    audio_link_target: Optional[str] = None
    if a.audio_access == AudioAccess.ATTACH:
        copied_audio = _ensure_local_copy(project, out_dir, base)
        audio_link_target = copied_audio.name
        produced.append(copied_audio)
    elif a.audio_access == AudioAccess.LINK:
        audio_link_target = project.origin or str(project.media_path)

    def finalize(spec: DocSpec) -> None:
        if audio_link_target:
            spec.add("h3", "Source recording")
            if a.audio_access == AudioAccess.ATTACH:
                spec.add("p", "The source recording is included alongside this document.")
                spec.add("link", (audio_link_target, audio_link_target))
            else:
                spec.add("p", "Source recording:")
                spec.add("link", (audio_link_target, audio_link_target))

    jobs: List[tuple] = []          # (DocSpec, filename_stem)
    both_minutes = (DocType.MINUTES_WITH_SYNOPSIS in a.doc_types
                    and DocType.MINUTES_NO_SYNOPSIS in a.doc_types)

    if DocType.TRANSCRIPT_NAMED in a.doc_types:
        jobs.append((builders.build_transcript(project, named=True),
                     f"{base} - Transcript (named speakers)"))
    if DocType.TRANSCRIPT_GENERIC in a.doc_types:
        jobs.append((builders.build_transcript(project, named=False),
                     f"{base} - Transcript"))
    if DocType.MINUTES_WITH_SYNOPSIS in a.doc_types:
        jobs.append((builders.build_minutes(project, with_synopsis=True),
                     f"{base} - Minutes (with synopsis)" if both_minutes
                     else f"{base} - Minutes"))
    if DocType.MINUTES_NO_SYNOPSIS in a.doc_types:
        jobs.append((builders.build_minutes(project, with_synopsis=False),
                     f"{base} - Minutes (no synopsis)" if both_minutes
                     else f"{base} - Minutes"))
    if DocType.ACTION_ITEMS in a.doc_types:
        jobs.append((builders.build_action_items(project),
                     f"{base} - Action Items"))

    # Accuracy report always accompanies the deliverables (requirement 6).
    jobs.append((builders.build_qc_report(project), f"{base} - Accuracy Report"))

    total = max(len(jobs) * len(formats) + 1, 1)
    step = 0
    for spec, stem in jobs:
        finalize(spec)
        for fmt in formats:
            step += 1
            p(step / total, f"Writing {stem}.{fmt}")
            path = out_dir / f"{stem}.{fmt}"
            try:
                export(spec, path, fmt)
                produced.append(path)
                if (fmt == "pdf" and a.audio_access == AudioAccess.ATTACH
                        and copied_audio and copied_audio.exists()
                        and "Accuracy Report" not in stem):
                    attach_file_to_pdf(path, copied_audio)
            except Exception as e:
                log.exception("Failed to write %s: %s", path, e)
                p(step / total, f"ERROR writing {path.name}: {e}")

    # ---- interactive transcript ---------------------------------------
    if DocType.DIGITAL_TRANSCRIPT in a.doc_types:
        step += 1
        p(step / total, "Building interactive transcript ...")
        media_dir = out_dir / f"{base}_media"
        media_dir.mkdir(exist_ok=True)
        dst = media_dir / Path(project.media_path).name
        if not dst.exists():
            shutil.copy2(project.media_path, dst)
        audio_rel = f"{media_dir.name}/{dst.name}"
        html_path = out_dir / f"{base} - Interactive Transcript.html"
        build_interactive_html(project, html_path, audio_rel,
                               named=DocType.TRANSCRIPT_NAMED in a.doc_types
                               or not DocType.TRANSCRIPT_GENERIC in a.doc_types)
        produced.append(html_path)
        produced.append(dst)

    project.deliverables = [str(x) for x in produced]
    log.info("Rendered %d deliverable file(s) into %s", len(produced), out_dir)
    p(1.0, f"All deliverables written to {out_dir}")
    return produced


def _ensure_local_copy(project: Project, out_dir: Path, base: str) -> Path:
    src = Path(project.media_path)
    dst = out_dir / src.name
    if dst.resolve() == src.resolve():
        return dst
    if not dst.exists():
        log.info("Copying source recording into deliverables folder: %s", dst)
        shutil.copy2(src, dst)
    return dst
