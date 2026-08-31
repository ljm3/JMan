"""Resolve the user's chosen audio source to a local file.

Supported (requirement 1 - "online repositories as well as local file paths"):

* a local file path (any audio/video container);
* a direct HTTP(S) URL to a media file;
* a share/stream URL handled by yt-dlp (YouTube, Google Drive, Dropbox,
  SharePoint, Vimeo, podcast pages, direct links, ...).

Returns a :class:`ResolvedSource` carrying the local media path and the
"output base folder" - requirement 7 says deliverables go in the same folder as
the audio file. For a local file that is its parent directory; for a download it
is the folder the file was downloaded into.
"""
from __future__ import annotations

import mimetypes
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlparse, unquote

from .logging_setup import get_logger
from . import paths

log = get_logger("sources")

ProgressFn = Callable[[str], None]

_MEDIA_EXT = {
    ".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma",
    ".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi", ".mpg", ".mpeg", ".3gp",
}


@dataclass
class ResolvedSource:
    media_path: Path          # local file to analyse
    output_dir: Path          # where deliverables must be written
    origin: str               # original path or URL, for provenance / "link to audio"
    kind: str                 # "local" | "download"
    downloaded_copy: bool     # True if media_path is a fetched copy (safe to keep/move)


def looks_like_url(text: str) -> bool:
    try:
        p = urlparse(text.strip())
        return p.scheme in ("http", "https") and bool(p.netloc)
    except Exception:
        return False


def _safe_name(name: str) -> str:
    name = unquote(name).strip().replace("\\", "_").replace("/", "_")
    name = re.sub(r'[<>:"|?*\x00-\x1f]', "_", name)
    return name[:180] or "audio"


def resolve(source: str, *, progress: Optional[ProgressFn] = None,
            download_dir: Optional[Path] = None) -> ResolvedSource:
    p = (progress or (lambda m: None))
    source = source.strip().strip('"')

    if looks_like_url(source):
        return _resolve_url(source, p, download_dir or paths.downloads_dir())

    path = Path(source).expanduser()
    if not path.exists():
        raise FileNotFoundError(f"No such file: {path}")
    if not path.is_file():
        raise ValueError(f"Not a file: {path}")
    log.info("Local source: %s", path)
    p(f"Using local file: {path}")
    return ResolvedSource(
        media_path=path,
        output_dir=path.parent,
        origin=str(path),
        kind="local",
        downloaded_copy=False,
    )


def _resolve_url(url: str, p: ProgressFn, dest_dir: Path) -> ResolvedSource:
    dest_dir.mkdir(parents=True, exist_ok=True)
    parsed = urlparse(url)
    tail = Path(unquote(parsed.path)).name
    ext = Path(tail).suffix.lower()

    # Fast path: a direct link to a media file.
    if ext in _MEDIA_EXT:
        out = dest_dir / _safe_name(tail)
        log.info("Direct download: %s -> %s", url, out)
        _http_download(url, out, p)
        return ResolvedSource(out, dest_dir, url, "download", True)

    # Otherwise let yt-dlp figure it out (extracts bestaudio).
    log.info("Handing URL to yt-dlp: %s", url)
    p("Resolving stream with yt-dlp ...")
    out = _ytdlp_download(url, dest_dir, p)
    return ResolvedSource(out, dest_dir, url, "download", True)


def _http_download(url: str, out: Path, p: ProgressFn) -> None:
    import requests

    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        step = max(total // 20, 1 << 20)
        nextmark = step
        with open(out, "wb") as fh:
            for chunk in r.iter_content(chunk_size=1 << 16):
                if not chunk:
                    continue
                fh.write(chunk)
                done += len(chunk)
                if done >= nextmark:
                    if total:
                        p(f"Downloading ... {done * 100 // total}%  ({done >> 20} MB)")
                    else:
                        p(f"Downloading ... {done >> 20} MB")
                    nextmark += step
    p(f"Downloaded {out.name} ({out.stat().st_size >> 20} MB)")


def _ytdlp_download(url: str, dest_dir: Path, p: ProgressFn) -> Path:
    try:
        import yt_dlp
    except Exception as e:  # pragma: no cover
        raise RuntimeError("yt-dlp is not installed; cannot fetch this URL") from e

    # Make sure yt-dlp can find an ffmpeg for any remux/extract step.
    _ensure_ffmpeg_on_path()

    captured: dict = {}

    def hook(d: dict) -> None:
        if d.get("status") == "downloading":
            pct = d.get("_percent_str", "").strip()
            spd = d.get("_speed_str", "").strip()
            p(f"yt-dlp: {pct} {spd}".strip())
        elif d.get("status") == "finished":
            captured["path"] = d.get("filename")
            p("yt-dlp: download finished, post-processing ...")

    outtmpl = str(dest_dir / "%(title).150B [%(id)s].%(ext)s")
    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": outtmpl,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "progress_hooks": [hook],
        "postprocessors": [
            {"key": "FFmpegExtractAudio", "preferredcodec": "m4a"},
        ],
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        final = ydl.prepare_filename(info)

    cand = Path(final)
    if not cand.exists():
        # post-processor changed the extension
        stem = cand.with_suffix("")
        for f in dest_dir.glob(stem.name + ".*"):
            cand = f
            break
    if not cand.exists() and captured.get("path"):
        cand = Path(captured["path"])
    if not cand.exists():
        raise RuntimeError("yt-dlp did not produce an output file")
    log.info("yt-dlp produced: %s", cand)
    return cand


def _ensure_ffmpeg_on_path() -> None:
    if shutil.which("ffmpeg"):
        return
    try:
        import imageio_ffmpeg
        exe = Path(imageio_ffmpeg.get_ffmpeg_exe())
        import os
        os.environ["PATH"] = str(exe.parent) + os.pathsep + os.environ.get("PATH", "")
    except Exception:
        log.warning("ffmpeg not found and imageio-ffmpeg unavailable; "
                    "some URL sources may fail to post-process.")
